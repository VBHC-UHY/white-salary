/**
 * chat-controller.js - Chat Controller
 *
 * Handles:
 *   1. WebSocket connection to Python backend
 *   2. User input (send messages)
 *   3. Receive AI replies (sentence-level streaming with synced audio)
 *   4. Update subtitle and speech bubble
 *   5. Manage chat history display
 *
 * Protocol (WebSocket JSON):
 *   Send: { "type": "chat", "content": "user text" }
 *   Recv: { "type": "reply_start", "source": "user"|"auto" }   ← 2026-07-02 审计修复（批3）：每轮回复开始的重置信号
 *         { "type": "sentence", "content": "text", "index": 0 }
 *         { "type": "sentence_audio", "content": "base64", "format": "wav", "index": 0 }
 *         { "type": "done", "content": "full reply" }
 *         { "type": "emotion", "content": "happy" }
 *         { "type": "error", "content": "error message" }
 */

class ChatController {
    constructor(options = {}) {
        this.wsUrl = options.wsUrl || 'ws://localhost:12400/ws/chat';
        this.live2dManager = options.live2dManager || null;
        this.ws = null;
        this.isConnected = false;

        // Sentence-level streaming state
        this._sentences = {};        // index -> { text, audio, played }
        this._audioQueue = [];       // ordered audio blobs waiting to play
        this._nextPlayIndex = 0;     // next sentence index to play
        this._isPlaying = false;     // currently playing audio
        this._fullReply = '';        // accumulated reply text

        // Timers
        this._bubbleHideTimer = null;
        this._subtitleHideTimer = null;
        this._reconnectTimer = null;
        this._reconnectInterval = 3000;

        this._bindElements();
        this._bindEvents();
    }

    _bindElements() {
        this.chatInput = document.getElementById('chat-input');
        this.sendBtn = document.getElementById('chat-send-btn');
        this.micBtn = document.getElementById('chat-mic-btn');
        this.chatMessages = document.getElementById('chat-messages');
        this.subtitleContainer = document.getElementById('subtitle-container');
        this.subtitleText = document.getElementById('subtitle-text');
        this.bubbleContainer = document.getElementById('bubble-container');
        this.bubbleText = document.getElementById('bubble-text');
        this.statusDot = document.getElementById('status-dot');
        this.statusText = document.getElementById('status-text');

        // Voice recording state (push-to-talk)
        this._mediaRecorder = null;
        this._audioChunks = [];
        this._isRecording = false;
        this._recordingRequested = false;
        this._voiceRequestSeq = 0;
        this._activeVoiceRequestId = null;
        this._pendingVoiceRequests = new Set();

        // Voice message merging (accumulate multiple speech segments)
        this._voiceBuffer = [];          // Accumulated transcription texts
        this._voiceMergeTimer = null;    // Timer for merge timeout
        this._voiceMergeTimeout = 5000;  // 5 seconds to wait before sending merged text

        // Interrupt control
        this._interruptEnabled = true;   // Can be toggled by UI button

        // Continuous listening state
        this._continuousListening = false;
        this._listenStream = null;
        this._listenAnalyser = null;
        this._listenAudioCtx = null;
        // 2026-07-02 审计修复（批3）：持续监听改用 Web Audio 原始 PCM 采集，
        // 替代原 MediaRecorder 方案（_listenRecorder/_listenChunks 已废弃删除）。
        // 旧方案的预录环形缓冲会丢掉含 WebM/EBML 头的首个 chunk，还把两个独立
        // MediaRecorder 的编码流拼成一个 Blob，产物不可解码，是 ASR 失败的独立成因。
        this._pcmSource = null;           // MediaStreamAudioSourceNode（停止时需断开）
        this._pcmProcessor = null;        // ScriptProcessorNode（PCM 采集节点）
        this._pcmPreBuffer = [];          // PCM 环形预缓冲（Float32Array 片段数组）
        this._pcmPreBufferSamples = 0;    // 预缓冲当前累计采样数
        this._pcmPreBufferMaxSamples = 0; // 预缓冲采样数上限（启动时按实际采样率算约1秒）
        this._speechPcmChunks = [];       // 说话期间收集的 PCM 片段
        this._isCapturingSpeech = false;  // 是否正在收集说话 PCM
        this._noiseFloor = 0.005;      // Dynamic noise baseline
        this._listenThreshold = 2.0;   // Multiplier over noise floor (lower = more sensitive to quiet speech)
        this._isSpeaking = false;
        this._silenceStart = 0;
        this._speechStart = 0;
        this._silenceTimeout = 2000;   // ms of silence to end speech (2秒静默才算说完，减少长话被截断)
        this._minSpeechDuration = 300; // ms minimum speech to be valid
    }

    _bindEvents() {
        if (this.sendBtn) {
            this.sendBtn.addEventListener('click', () => this._handleSend());
        }
        if (this.chatInput) {
            this.chatInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this._handleSend();
                }
            });
        }
        // Image paste support (Ctrl+V paste image)
        if (this.chatInput) {
            this.chatInput.addEventListener('paste', (e) => this._handlePaste(e));
        }

        // Mic button: press and hold to record
        if (this.micBtn) {
            this.micBtn.addEventListener('pointerdown', (event) => {
                if (event.button !== undefined && event.button !== 0) return;
                event.preventDefault();
                try { this.micBtn.setPointerCapture(event.pointerId); } catch (e) { /* ignore */ }
                this._startRecording();
            });
            this.micBtn.addEventListener('pointerup', (event) => {
                event.preventDefault();
                this._stopRecording();
            });
            this.micBtn.addEventListener('pointercancel', () => this._stopRecording());
            this.micBtn.addEventListener('lostpointercapture', () => {
                if (this._recordingRequested || this._isRecording) this._stopRecording();
            });
        }

        // Listen toggle button
        this.listenToggle = document.getElementById('chat-listen-toggle');
        if (this.listenToggle) {
            this.listenToggle.addEventListener('click', () => this._toggleContinuousListening());
        }
    }

    // ================================================================
    // WebSocket Connection
    // ================================================================

    connect() {
        this._updateStatus('connecting', 'CONNECTING...');

        try {
            this.ws = new WebSocket(this.wsUrl);

            this.ws.onopen = () => {
                this.isConnected = true;
                this._resetReconnectInterval();
                this._updateStatus('connected', 'ONLINE');
                console.log('[Chat] WebSocket connected:', this.wsUrl);
                if (this._reconnectTimer) {
                    clearTimeout(this._reconnectTimer);
                    this._reconnectTimer = null;
                }
            };

            this.ws.onmessage = (event) => {
                this._handleMessage(JSON.parse(event.data));
            };

            this.ws.onclose = () => {
                this.isConnected = false;
                this._updateStatus('disconnected', 'OFFLINE');
                console.log('[Chat] WebSocket disconnected');
                this._scheduleReconnect();
            };

            this.ws.onerror = (error) => {
                console.error('[Chat] WebSocket error:', error);
                this._updateStatus('disconnected', 'ERROR');
            };
        } catch (error) {
            console.error('[Chat] Connection failed:', error);
            this._updateStatus('disconnected', 'FAILED');
            this._scheduleReconnect();
        }
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.isConnected = false;
        this._updateStatus('disconnected', 'OFFLINE');
    }

    _scheduleReconnect() {
        if (this._reconnectTimer) return;
        // 指数退避：3s → 6s → 12s → 最多30s
        const delay = Math.min(this._reconnectInterval, 30000);
        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            console.log(`[Chat] Reconnecting (${delay/1000}s)...`);
            this.connect();
        }, delay);
        this._reconnectInterval = Math.min(this._reconnectInterval * 2, 30000);
    }

    _resetReconnectInterval() {
        this._reconnectInterval = 3000;
    }

    // ================================================================
    // Send & Receive Messages
    // ================================================================

    _handleSend() {
        if (!this.chatInput) return;
        const text = this.chatInput.innerText.trim();
        if (!text) return;
        this.chatInput.innerText = '';
        this.sendMessage(text);
    }

    sendMessage(text) {
        if (!this.isConnected || !this.ws) {
            console.warn('[Chat] Not connected, cannot send');
            return;
        }

        this._addChatMessage('user', text);

        // Clear any pending voice buffer
        if (this._voiceMergeTimer) {
            clearTimeout(this._voiceMergeTimer);
            this._voiceMergeTimer = null;
        }
        this._voiceBuffer = [];

        // Reset streaming state for new reply
        this._sentences = {};
        this._audioQueue = [];
        this._nextPlayIndex = 0;
        this._isPlaying = false;
        this._fullReply = '';
        this._replyDone = false;

        // Stop any playing audio and hide subtitle/bubble
        if (this._currentAudio) {
            this._currentAudio.pause();
            this._currentAudio = null;
        }
        if (this.live2dManager) this.live2dManager.setLipSync(0);
        if (this.subtitleContainer) this.subtitleContainer.style.display = 'none';
        if (this.bubbleContainer) this.bubbleContainer.style.display = 'none';

        this.ws.send(JSON.stringify({ type: 'chat', content: text }));
        console.log('[Chat] Sent:', text);
    }

    _handleMessage(data) {
        switch (data.type) {
            // 2026-07-02 审计修复（批3）：新增 reply_start——后端在每轮回复开始时、
            // 第一条 sentence 之前下发（source: "user"=用户消息触发 / "auto"=auto_chat、
            // 跨平台桥、图片流程触发）。统一在此重置流式播放状态，修复主动语音因
            // 播放游标 _nextPlayIndex 残留上一轮句数而永不出声的问题。
            case 'reply_start':
                this._handleReplyStart(data.source || 'user');
                break;

            case 'sentence':
                // A complete sentence of text arrived
                this._handleSentence(data.content, data.index);
                break;

            case 'sentence_audio':
                // Audio for a specific sentence arrived
                this._handleSentenceAudio(data.content, data.format || 'wav', data.index);
                break;

            case 'sentence_audio_skipped':
                this._handleSentenceAudioSkipped(data.index, data.reason || 'unavailable');
                break;

            case 'done':
                // Full reply complete — always trust backend's text over local reconstruction
                const finalText = data.content || this._fullReply;
                this._fullReply = finalText;  // Sync with backend
                this._addChatMessage('assistant', finalText);
                this._replyDone = true;
                // Schedule hide after audio finishes (or immediately if no audio)
                this._scheduleHideAfterAudio();
                break;

            // 2026-07-02 审计修复（批3）：删除 case 'audio' 死分支——grep 后端
            // websocket_handler.py 确认从不发送该类型（协议漂移死代码）。
            case 'transcription':
                // Voice input recognized text — buffer it, wait for more
                this._handleTranscription(
                    data.content,
                    data.mode || 'continuous',
                    data.request_id || null,
                );
                break;

            case 'voice_status':
                this._handleVoiceStatus(data.state || 'idle', data.request_id || null);
                break;

            case 'emotion':
                console.log('[Chat] Emotion:', data.content);
                break;

            case 'expression':
                // Live2D expression command from emotion system
                this._applyExpression(data.content);
                break;

            case 'info':
                console.log('[Chat] Info:', data.content);
                this._addChatMessage('system', data.content);
                break;

            // 2026-07-02 审计修复（批3）：删除 case 'auto_chat'/'chunk' 死分支——
            // 后端主动聊天走 _handle_chat_message 下发 sentence/done，从不发送这两种
            // 类型；其"新一轮回复重置"职责由上方 reply_start 分支接管。
            case 'error':
                console.error('[Chat] Backend error:', data.content);
                this._addChatMessage('system', `[Error] ${data.content}`);
                break;

            default:
                console.warn('[Chat] Unknown message type:', data.type);
        }
    }

    // ================================================================
    // Sentence-Level Sync Playback
    // ================================================================

    /**
     * 2026-07-02 审计修复（批3）：处理后端 reply_start（每轮回复开始的重置信号）。
     * 后端每轮回复的 sentence index 都从 0 重新计数，旧实现只在前端自己发消息的
     * 三条路径（sendMessage/_sendImage/_sendVoice）重置播放游标，auto_chat 定时
     * 触发与 QQ 跨平台桥推送的主动回复到达时游标仍停在上一轮句数 N，
     * _tryPlayNext 永远等不到 index==N 的旧句，主动语音整轮不播。
     * 现在无论哪端触发新回复，统一在这里：停止当前句播放、清空句子队列、
     * 播放游标归 0、重置流式字幕/_fullReply 状态。
     */
    _handleReplyStart(source) {
        console.log(`[Chat] Reply start (source=${source})`);

        // 停止当前句音频播放
        if (this._currentAudio) {
            this._currentAudio.pause();
            this._currentAudio = null;
        }
        if (this.live2dManager) this.live2dManager.setLipSync(0);

        // 释放上一轮未播放句子的 objectURL，避免长期挂机内存泄漏
        for (const key of Object.keys(this._sentences)) {
            const entry = this._sentences[key];
            if (entry && entry.audio && !entry.played) {
                try { URL.revokeObjectURL(entry.audio); } catch (e) { /* 忽略 */ }
            }
        }

        // 清空句子队列、播放游标归 0、重置流式字幕/回复累积状态
        this._sentences = {};
        this._audioQueue = [];
        this._nextPlayIndex = 0;
        this._isPlaying = false;
        this._fullReply = '';
        this._replyDone = false;

        // 隐藏上一轮残留的字幕/气泡（新一轮首句播放时会重新显示）
        if (this.subtitleContainer) this.subtitleContainer.style.display = 'none';
        if (this.bubbleContainer) this.bubbleContainer.style.display = 'none';
    }

    _handleSentence(text, index) {
        // Store sentence text, do NOT show subtitle yet (wait for audio sync)
        if (!this._sentences[index]) {
            this._sentences[index] = { text, audio: null, audioSkipped: false, played: false };
        } else {
            this._sentences[index].text = text;
        }

        this._fullReply += text;
        console.log(`[Chat] Sentence ${index}: ${text.substring(0, 30)}...`);
        this._tryPlayNext();
    }

    _handleSentenceAudio(base64Audio, format, index) {
        // Decode audio
        const binaryStr = atob(base64Audio);
        const bytes = new Uint8Array(binaryStr.length);
        for (let i = 0; i < binaryStr.length; i++) {
            bytes[i] = binaryStr.charCodeAt(i);
        }

        const mimeType = format === 'wav' ? 'audio/wav' : 'audio/mpeg';
        const blob = new Blob([bytes], { type: mimeType });
        const audioUrl = URL.createObjectURL(blob);

        // Store audio for this sentence
        if (!this._sentences[index]) {
            this._sentences[index] = { text: '', audio: audioUrl, audioSkipped: false, played: false };
        } else {
            this._sentences[index].audio = audioUrl;
            this._sentences[index].audioSkipped = false;
        }

        console.log(`[Chat] Audio ${index}: ${(bytes.length / 1024).toFixed(1)}KB`);

        // Try to play next in queue
        this._tryPlayNext();
    }

    _handleSentenceAudioSkipped(index, reason) {
        if (!this._sentences[index]) {
            this._sentences[index] = {
                text: '', audio: null, audioSkipped: true, played: false,
            };
        } else {
            this._sentences[index].audioSkipped = true;
        }
        console.log(`[Chat] Audio ${index} skipped: ${reason}`);
        this._tryPlayNext();
    }

    _tryPlayNext() {
        // Already playing something, wait
        if (this._isPlaying) return;

        // Explicitly skipped audio is a terminal state too. Advance the index
        // so one failed TTS sentence cannot block every later sentence.
        let entry = this._sentences[this._nextPlayIndex];
        while (entry && entry.audioSkipped && !entry.played) {
            if (!entry.text) return;
            entry.played = true;
            this._updateSubtitle(entry.text);
            this._updateBubble(entry.text);
            this._nextPlayIndex++;
            entry = this._sentences[this._nextPlayIndex];
        }
        if (!entry || !entry.audio || entry.played) return;

        // Play this sentence
        entry.played = true;
        this._isPlaying = true;

        const audio = new Audio(entry.audio);
        this._currentAudio = audio;

        // Update subtitle to show current sentence being spoken
        if (entry.text) {
            this._updateSubtitle(entry.text);
            this._updateBubble(entry.text);
        }

        // Set up lip sync
        this._setupLipSync(audio);

        audio.play().catch(err => {
            console.warn('[Chat] Audio playback failed:', err);
            this._isPlaying = false;
            this._nextPlayIndex++;
            this._tryPlayNext();
        });

        audio.onended = () => {
            URL.revokeObjectURL(entry.audio);
            this._currentAudio = null;
            this._isPlaying = false;

            if (this.live2dManager) {
                this.live2dManager.setLipSync(0);
            }

            this._nextPlayIndex++;
            // Try to play the next sentence immediately
            this._tryPlayNext();

            // If no more audio to play and reply is done, hide subtitle/bubble
            if (!this._isPlaying && this._replyDone) {
                this._scheduleHideAfterAudio();
            }
        };
    }

    _scheduleHideAfterAudio() {
        if (this._isPlaying) {
            // Audio still playing — will be called again when audio ends
            return;
        }
        // Hide subtitle and bubble after 5 seconds
        this._autoHideSubtitle(5000);
        this._autoHideBubble(5000);
    }

    // 2026-07-02 审计修复（批3）：删除 _playAudioData 遗留函数——唯一调用点
    // case 'audio' 死分支已随协议清理移除，避免留下新的无引用死代码。

    // ================================================================
    // UI Updates
    // ================================================================

    _addChatMessage(role, text) {
        if (!this.chatMessages) return;
        this.chatMessages.style.display = 'block';

        const msgDiv = document.createElement('div');
        msgDiv.className = `chat-message ${role}`;

        const senderName = role === 'user' ? 'YOU' : role === 'assistant' ? 'WHITE SALARY' : 'SYSTEM';
        msgDiv.innerHTML = `
            <div class="sender">${senderName}</div>
            <div class="content">${this._escapeHtml(text)}</div>
        `;

        this.chatMessages.appendChild(msgDiv);
        this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
    }

    _updateSubtitle(text) {
        if (!this.subtitleContainer || !this.subtitleText) return;
        this.subtitleText.textContent = text;
        this.subtitleContainer.style.display = 'block';
        if (this._subtitleHideTimer) {
            clearTimeout(this._subtitleHideTimer);
            this._subtitleHideTimer = null;
        }
    }

    _autoHideSubtitle(delay) {
        if (this._subtitleHideTimer) clearTimeout(this._subtitleHideTimer);
        this._subtitleHideTimer = setTimeout(() => {
            if (this.subtitleContainer) {
                this.subtitleContainer.style.display = 'none';
            }
        }, delay);
    }

    _updateBubble(text) {
        if (!this.bubbleContainer || !this.bubbleText) return;

        let pos = { x: window.innerWidth * 0.5, y: window.innerHeight * 0.2 };
        if (this.live2dManager) {
            pos = this.live2dManager.getBubblePosition();
        }

        this.bubbleContainer.style.left = `${pos.x}px`;
        this.bubbleContainer.style.top = `${pos.y}px`;
        this.bubbleText.textContent = text.length > 80 ? text.slice(-80) + '...' : text;
        this.bubbleContainer.style.display = 'block';

        if (this._bubbleHideTimer) {
            clearTimeout(this._bubbleHideTimer);
            this._bubbleHideTimer = null;
        }
    }

    _autoHideBubble(delay) {
        if (this._bubbleHideTimer) clearTimeout(this._bubbleHideTimer);
        this._bubbleHideTimer = setTimeout(() => {
            if (this.bubbleContainer) {
                this.bubbleContainer.style.display = 'none';
            }
        }, delay);
    }

    _updateStatus(status, text) {
        if (this.statusDot) this.statusDot.className = status;
        if (this.statusText) this.statusText.textContent = text;
    }

    _showSystemNotice(text) {
        this._addChatMessage('system', text);
    }

    _describeAudioInputError(err) {
        const name = err && err.name ? err.name : '';
        const message = err && err.message ? err.message : String(err || '');
        if (name === 'NotAllowedError' || name === 'SecurityError') {
            return '麦克风权限被拒绝，请检查系统隐私设置';
        }
        if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
            return '没有找到可用麦克风，请检查录音设备';
        }
        if (name === 'NotReadableError' || name === 'TrackStartError') {
            return '麦克风被其他软件占用或设备无法启动';
        }
        if (name === 'OverconstrainedError' || name === 'ConstraintNotSatisfiedError') {
            return '当前麦克风不支持请求的录音参数';
        }
        if (name === 'TypeError') {
            return '当前环境不支持麦克风录音接口';
        }
        return `${name} ${message}`.trim() || '麦克风打开失败';
    }

    // ================================================================
    // Lip Sync
    // ================================================================

    _setupLipSync(audioElement) {
        if (!this.live2dManager) return;

        try {
            if (!this._audioContext) {
                this._audioContext = new (window.AudioContext || window.webkitAudioContext)();
            }

            const source = this._audioContext.createMediaElementSource(audioElement);
            const analyser = this._audioContext.createAnalyser();
            analyser.fftSize = 256;

            source.connect(analyser);
            analyser.connect(this._audioContext.destination);

            const dataArray = new Uint8Array(analyser.frequencyBinCount);

            const updateLipSync = () => {
                if (!this._currentAudio || this._currentAudio.paused) {
                    this.live2dManager.setLipSync(0);
                    return;
                }

                analyser.getByteFrequencyData(dataArray);
                let sum = 0;
                for (let i = 0; i < dataArray.length; i++) {
                    sum += dataArray[i];
                }
                const avg = sum / dataArray.length;
                const lipValue = Math.min(1.0, avg / 128);
                this.live2dManager.setLipSync(lipValue);
                requestAnimationFrame(updateLipSync);
            };

            requestAnimationFrame(updateLipSync);
        } catch (error) {
            console.warn('[Chat] Lip sync setup failed:', error);
        }
    }

    // ================================================================
    // Image Handling (paste/upload)
    // ================================================================

    _handlePaste(e) {
        const items = e.clipboardData?.items;
        if (!items) return;

        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                const file = item.getAsFile();
                if (file) this._sendImage(file);
                return;
            }
        }
    }

    async _sendImage(file) {
        if (!this.isConnected || !this.ws) return;

        try {
            // Convert file to base64 (handle large files properly)
            const base64 = await new Promise((resolve) => {
                const reader = new FileReader();
                reader.onload = () => {
                    const dataUrl = reader.result;
                    // Remove data:image/xxx;base64, prefix
                    resolve(dataUrl.split(',')[1]);
                };
                reader.readAsDataURL(file);
            });

            // Get prompt from input or use default
            const prompt = this.chatInput?.innerText?.trim() || '描述这张图片的内容';
            if (this.chatInput) this.chatInput.innerText = '';

            this._addChatMessage('user', `[发送了图片] ${prompt}`);

            // Reset state
            this._sentences = {};
            this._nextPlayIndex = 0;
            this._isPlaying = false;
            this._fullReply = '';
            this._replyDone = false;

            this.ws.send(JSON.stringify({
                type: 'image',
                content: base64,
                prompt: prompt,
            }));

            console.log(`[Chat] Sent image (${(file.size / 1024).toFixed(1)}KB)`);
        } catch (err) {
            console.error('[Chat] Image send failed:', err);
        }
    }

    // ================================================================
    // Live2D Expression Control
    // ================================================================

    _applyExpression(command) {
        if (!this.live2dManager || !command || typeof command !== 'object') return;

        try {
            const expression = command.expression || 'default';
            const motion_group = command.motion_group || 'idle';

            // Apply expression if Live2D model supports it
            if (expression && expression !== 'default') {
                this.live2dManager.setExpression(expression);
                console.log(`[Expression] Set: ${expression}`);
            }

            // Trigger motion if specified
            if (motion_group && motion_group !== 'idle') {
                this.live2dManager.playMotion(motion_group);
                console.log(`[Expression] Motion: ${motion_group}`);
            }
        } catch (err) {
            console.warn('[Expression] Apply failed:', err);
        }
    }

    // ================================================================
    // Voice Message Buffering & Merging
    // ================================================================

    _handleTranscription(text, mode = 'continuous', requestId = null) {
        if (
            requestId
            && this._activeVoiceRequestId
            && requestId !== this._activeVoiceRequestId
            && mode === 'push_to_talk'
        ) {
            console.debug(`[Voice] Ignoring stale push-to-talk result: ${requestId}`);
            return;
        }
        if (!text || !text.trim()) return;

        // Add to buffer
        this._voiceBuffer.push(text.trim());
        console.log(`[Voice] Buffered: "${text}" (${this._voiceBuffer.length} segments)`);

        // Show accumulated text as preview subtitle
        const accumulated = this._voiceBuffer.join(' ');
        this._updateSubtitle('🎤 ' + accumulated);

        if (mode === 'push_to_talk') {
            this._flushVoiceBuffer();
            return;
        }

        this._scheduleVoiceBufferFlush();
    }

    _scheduleVoiceBufferFlush() {
        if (this._voiceBuffer.length === 0 || this._pendingVoiceRequests.size > 0) return;

        // Wait for a natural pause only after every queued ASR request has
        // completed; otherwise a slow recognition call could lose later audio.
        if (this._voiceMergeTimer) {
            clearTimeout(this._voiceMergeTimer);
        }

        this._voiceMergeTimer = setTimeout(() => {
            this._flushVoiceBuffer();
        }, this._voiceMergeTimeout);
    }

    _flushVoiceBuffer() {
        if (this._voiceBuffer.length === 0) return;

        // Merge all buffered texts into one
        const segmentCount = this._voiceBuffer.length;
        const merged = this._voiceBuffer.join(' ');
        this._voiceBuffer = [];
        this._voiceMergeTimer = null;

        console.log(`[Voice] Sending merged (${segmentCount} segments): "${merged}"`);

        // Hide preview subtitle
        if (this.subtitleContainer) {
            this.subtitleContainer.style.display = 'none';
        }

        // 先interrupt取消旧的处理（在真正发消息时才interrupt，不在录音开始时）
        if (this._interruptEnabled && this.isConnected && this.ws) {
            this.ws.send(JSON.stringify({ type: 'interrupt' }));
        }

        // Stop any playing audio (discard old reply)
        if (this._currentAudio) {
            this._currentAudio.pause();
            this._currentAudio = null;
        }
        this._isPlaying = false;
        if (this.live2dManager) this.live2dManager.setLipSync(0);

        // Send merged voice message
        this.sendMessage(merged);
    }

    // ================================================================
    // Continuous Listening Mode
    // ================================================================

    async _toggleContinuousListening() {
        if (this._continuousListening) {
            this._stopContinuousListening();
        } else {
            await this._startContinuousListening();
        }
    }

    async _startContinuousListening() {
        if (this._continuousListening) return;
        this._showSystemNotice('[持续监听] 正在请求麦克风权限...');

        try {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                throw new TypeError('getUserMedia is not available');
            }

            this._listenStream = await navigator.mediaDevices.getUserMedia({
                audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
            });

            this._listenAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const source = this._listenAudioCtx.createMediaStreamSource(this._listenStream);
            this._listenAnalyser = this._listenAudioCtx.createAnalyser();
            this._listenAnalyser.fftSize = 512;
            source.connect(this._listenAnalyser);

            this._continuousListening = true;
            this._isSpeaking = false;

            // 2026-07-02 审计修复（批3）：预录缓冲改用 Web Audio 原始 PCM 环形缓冲
            // （约1秒），说话时预缓冲+后续 PCM 统一在前端编码成 16kHz 单声道 WAV
            // 再发送，彻底修掉「预缓冲丢 WebM 头 + 两段编码流拼接不可解码」问题，
            // 且后端收到 RIFF 头直接免转码。
            this._pcmSource = source;
            this._pcmPreBuffer = [];
            this._pcmPreBufferSamples = 0;
            this._pcmPreBufferMaxSamples = Math.round(this._listenAudioCtx.sampleRate * 1.0);
            this._speechPcmChunks = [];
            this._isCapturingSpeech = false;
            this._startPcmCapture();

            if (this.listenToggle) {
                this.listenToggle.classList.add('active');
                this.listenToggle.title = '持续监听中（点击关闭）';
                const off = this.listenToggle.querySelector('.icon-off');
                const on = this.listenToggle.querySelector('.icon-on');
                if (off) off.style.display = 'none';
                if (on) on.style.display = '';
            }

            this._showSystemNotice('[持续监听] 麦克风已打开，正在校准环境噪声...');
            console.log('[Listen] Calibrating noise floor (2s)...');
            await this._calibrateNoiseFloor();
            console.log(`[Listen] Noise floor: ${this._noiseFloor.toFixed(4)}, threshold: ${(this._noiseFloor * this._listenThreshold).toFixed(4)}`);

            this._showSystemNotice('[持续监听] 已开启，可以直接说话');
            this._listenLoop();

        } catch (err) {
            console.error('[Listen] Mic access failed:', err);
            this._stopContinuousListening();
            this._showSystemNotice(`[持续监听] 开启失败：${this._describeAudioInputError(err)}`);
        }
    }

    _stopContinuousListening() {
        this._continuousListening = false;
        this._isSpeaking = false;

        if (this._listenStream) {
            this._listenStream.getTracks().forEach(t => t.stop());
            this._listenStream = null;
        }
        // 2026-07-02 审计修复（批3）：释放 PCM 采集资源——断开节点、清空缓冲
        //（原 MediaRecorder 停止逻辑随预录方案废弃一并移除）
        this._isCapturingSpeech = false;
        if (this._pcmProcessor) {
            try { this._pcmProcessor.disconnect(); } catch (e) { /* 忽略 */ }
            this._pcmProcessor.onaudioprocess = null;
            this._pcmProcessor = null;
        }
        if (this._pcmSource) {
            try { this._pcmSource.disconnect(); } catch (e) { /* 忽略 */ }
            this._pcmSource = null;
        }
        this._pcmPreBuffer = [];
        this._pcmPreBufferSamples = 0;
        this._speechPcmChunks = [];

        if (this._listenAudioCtx) {
            this._listenAudioCtx.close();
            this._listenAudioCtx = null;
        }

        if (this.listenToggle) {
            this.listenToggle.classList.remove('active');
            this.listenToggle.title = '开启持续监听';
            this.listenToggle.style.boxShadow = '';
            const off = this.listenToggle.querySelector('.icon-off');
            const on = this.listenToggle.querySelector('.icon-on');
            if (off) off.style.display = '';
            if (on) on.style.display = 'none';
        }

        console.log('[Listen] Stopped');
    }

    async _calibrateNoiseFloor() {
        // Sample noise for 2 seconds to establish baseline
        const dataArray = new Uint8Array(this._listenAnalyser.frequencyBinCount);
        let samples = 0;
        let totalEnergy = 0;

        await new Promise(resolve => {
            const interval = setInterval(() => {
                this._listenAnalyser.getByteTimeDomainData(dataArray);
                let sum = 0;
                for (let i = 0; i < dataArray.length; i++) {
                    const v = (dataArray[i] - 128) / 128;
                    sum += v * v;
                }
                totalEnergy += Math.sqrt(sum / dataArray.length);
                samples++;
                if (samples >= 40) { // ~2 seconds at 50ms intervals
                    clearInterval(interval);
                    resolve();
                }
            }, 50);
        });

        this._noiseFloor = Math.max(0.003, totalEnergy / samples);
    }

    _listenLoop() {
        if (!this._continuousListening) return;

        const dataArray = new Uint8Array(this._listenAnalyser.frequencyBinCount);
        this._listenAnalyser.getByteTimeDomainData(dataArray);

        // Calculate RMS energy
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
            const v = (dataArray[i] - 128) / 128;
            sum += v * v;
        }
        const rms = Math.sqrt(sum / dataArray.length);
        const threshold = this._noiseFloor * this._listenThreshold;
        const now = Date.now();

        if (rms > threshold) {
            // Voice detected
            if (!this._isSpeaking) {
                // Speech just started
                this._isSpeaking = true;
                this._speechStart = now;
                this._startListenRecording();
                console.log('[Listen] Speech detected');
            }
            this._silenceStart = 0;
        } else {
            // Silence
            if (this._isSpeaking) {
                if (this._silenceStart === 0) {
                    this._silenceStart = now;
                } else if (now - this._silenceStart >= this._silenceTimeout) {
                    // Silence confirmed — end speech
                    const duration = now - this._speechStart;
                    if (duration >= this._minSpeechDuration) {
                        console.log(`[Listen] Speech ended (${duration}ms)`);
                        this._stopListenRecording();
                    } else {
                        console.log(`[Listen] Too short (${duration}ms), ignoring`);
                        this._cancelListenRecording();
                    }
                    this._isSpeaking = false;
                    this._silenceStart = 0;
                }
            }
        }

        // Update volume indicator
        this._updateVolumeIndicator(rms, threshold);

        // Next frame
        setTimeout(() => this._listenLoop(), 50);
    }

    /**
     * 2026-07-02 审计修复（批3）：启动 PCM 采集节点，替代原 MediaRecorder 预录方案。
     * 未说话时维护约1秒的 PCM 环形预缓冲；说话时把 PCM 片段收进 _speechPcmChunks。
     * 路线选择：ScriptProcessor 而非 AudioWorklet——无需额外加载 worklet 模块文件，
     * 在 Electron 内置 Chromium 上行为稳定；4096 采样/回调（48kHz 下约85ms）对
     * 预缓冲场景足够，已废弃警告不影响功能。
     */
    _startPcmCapture() {
        if (!this._listenAudioCtx || !this._pcmSource || this._pcmProcessor) return;

        this._pcmProcessor = this._listenAudioCtx.createScriptProcessor(4096, 1, 1);
        this._pcmProcessor.onaudioprocess = (e) => {
            if (!this._continuousListening) return;
            // 回调复用底层 buffer，必须拷贝一份再存
            const input = e.inputBuffer.getChannelData(0);
            const chunk = new Float32Array(input.length);
            chunk.set(input);

            if (this._isCapturingSpeech) {
                // 说话中：全部收集
                this._speechPcmChunks.push(chunk);
            } else {
                // 未说话：进环形预缓冲，超出约1秒即丢最旧片段
                this._pcmPreBuffer.push(chunk);
                this._pcmPreBufferSamples += chunk.length;
                while (this._pcmPreBufferSamples > this._pcmPreBufferMaxSamples
                       && this._pcmPreBuffer.length > 1) {
                    this._pcmPreBufferSamples -= this._pcmPreBuffer.shift().length;
                }
            }
        };
        // ScriptProcessor 必须接到 destination 才会持续触发回调；
        // 输出 buffer 保持默认全零（从不写 outputBuffer），不会把麦克风回放出来。
        this._pcmSource.connect(this._pcmProcessor);
        this._pcmProcessor.connect(this._listenAudioCtx.destination);
    }

    _startListenRecording() {
        // 2026-07-02 审计修复（批3）：说话开始——把约1秒 PCM 预缓冲接到说话片段
        // 开头（不再有 WebM 头丢失、不再拼接两个编码流），后续 PCM 由采集回调
        // 直接收进 _speechPcmChunks。
        if (!this._listenAudioCtx || !this._pcmProcessor) return;

        this._speechPcmChunks = this._pcmPreBuffer;
        this._pcmPreBuffer = [];
        this._pcmPreBufferSamples = 0;
        this._isCapturingSpeech = true;
        console.log(`[Listen] Recording started with ${this._speechPcmChunks.length} pre-buffered PCM chunks`);

        // 用户开始说话时：停止播放音频，但不发interrupt
        if (this._interruptEnabled) {
            if (this._currentAudio) {
                this._currentAudio.pause();
                this._currentAudio = null;
                this._isPlaying = false;
                if (this.live2dManager) this.live2dManager.setLipSync(0);
            }
        }
    }

    _stopListenRecording() {
        // 2026-07-02 审计修复（批3）：说话结束（2秒静默）——把收集到的 PCM 重采样
        // 为 16kHz 单声道并编码成标准 RIFF WAV，走现有 base64 通道发送；
        // 采集回调检测到 _isCapturingSpeech=false 后自动恢复填充预缓冲。
        if (!this._isCapturingSpeech) return;
        this._isCapturingSpeech = false;

        const chunks = this._speechPcmChunks;
        this._speechPcmChunks = [];

        try {
            const srcRate = this._listenAudioCtx ? this._listenAudioCtx.sampleRate : 48000;
            const blob = this._encodePcmChunksToWav(chunks, srcRate);
            if (blob && blob.size > 44) {
                this._sendVoice(blob, 'continuous');
            }
        } catch (err) {
            console.error('[Listen] WAV encode failed:', err);
            this._addChatMessage('system', '[语音] 音频编码失败，请重试');
        }
    }

    _cancelListenRecording() {
        // 2026-07-02 审计修复（批3）：说话太短判定无效——丢弃已收集 PCM，
        // 预缓冲由采集回调自动继续填充。
        this._isCapturingSpeech = false;
        this._speechPcmChunks = [];
    }

    /**
     * 2026-07-02 审计修复（批3）：把 PCM 片段合并、线性插值重采样为 16kHz 单声道，
     * 编码为标准 RIFF WAV（16bit PCM）Blob。后端按文件头识别为 wav 后免转码直传 ASR。
     */
    _encodePcmChunksToWav(chunks, srcRate) {
        // 合并所有 Float32Array 片段
        let total = 0;
        for (const c of chunks) total += c.length;
        if (total === 0) return null;
        const merged = new Float32Array(total);
        let offset = 0;
        for (const c of chunks) {
            merged.set(c, offset);
            offset += c.length;
        }

        // 重采样到 16kHz
        const resampled = this._resamplePcm(merged, srcRate, 16000);

        // Float32 [-1,1] → Int16 小端，前置 44 字节 RIFF 头
        const dataLen = resampled.length * 2;
        const buffer = new ArrayBuffer(44 + dataLen);
        const view = new DataView(buffer);
        this._writeWavHeader(view, dataLen, 16000);
        let pos = 44;
        for (let i = 0; i < resampled.length; i++) {
            const s = Math.max(-1, Math.min(1, resampled[i]));
            view.setInt16(pos, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
            pos += 2;
        }
        return new Blob([buffer], { type: 'audio/wav' });
    }

    /** 2026-07-02 审计修复（批3）：单声道 PCM 线性插值重采样 */
    _resamplePcm(samples, srcRate, dstRate) {
        if (srcRate === dstRate) return samples;
        const ratio = srcRate / dstRate;
        const dstLen = Math.floor(samples.length / ratio);
        const out = new Float32Array(dstLen);
        for (let i = 0; i < dstLen; i++) {
            const srcPos = i * ratio;
            const i0 = Math.floor(srcPos);
            const i1 = Math.min(i0 + 1, samples.length - 1);
            const frac = srcPos - i0;
            out[i] = samples[i0] * (1 - frac) + samples[i1] * frac;
        }
        return out;
    }

    /** 2026-07-02 审计修复（批3）：写标准 RIFF/WAVE 头（PCM 16bit 单声道，小端） */
    _writeWavHeader(view, dataLen, sampleRate) {
        const writeStr = (offset, str) => {
            for (let i = 0; i < str.length; i++) {
                view.setUint8(offset + i, str.charCodeAt(i));
            }
        };
        writeStr(0, 'RIFF');
        view.setUint32(4, 36 + dataLen, true);     // RIFF chunk 大小 = 36 + data 长度
        writeStr(8, 'WAVE');
        writeStr(12, 'fmt ');
        view.setUint32(16, 16, true);              // fmt 子块大小
        view.setUint16(20, 1, true);               // 音频格式：1 = PCM
        view.setUint16(22, 1, true);               // 声道数：1（单声道）
        view.setUint32(24, sampleRate, true);      // 采样率
        view.setUint32(28, sampleRate * 2, true);  // 字节率 = 采样率 × 块对齐
        view.setUint16(32, 2, true);               // 块对齐 = 声道数 × 位深/8
        view.setUint16(34, 16, true);              // 位深：16bit
        writeStr(36, 'data');
        view.setUint32(40, dataLen, true);         // data 子块大小
    }

    _updateVolumeIndicator(rms, threshold) {
        // Update the listen toggle button to show volume
        if (!this.listenToggle || !this._continuousListening) return;
        const level = Math.min(1, rms / (threshold * 2));
        if (this._isSpeaking) {
            this.listenToggle.style.boxShadow = `inset 0 0 ${10 + level * 20}px rgba(0, 212, 255, ${0.3 + level * 0.5})`;
        } else {
            this.listenToggle.style.boxShadow = '';
        }
    }

    // ================================================================
    // Voice Recording (Push-to-talk)
    // ================================================================

    async _startRecording() {
        if (this._isRecording || this._recordingRequested) return;
        this._recordingRequested = true;

        let stream = null;
        let mimeType = '';
        try {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                this._recordingRequested = false;
                this._showSystemNotice('[语音] 当前环境不支持麦克风录音');
                return;
            }
            if (!window.MediaRecorder) {
                this._recordingRequested = false;
                this._showSystemNotice('[语音] 当前环境不支持 MediaRecorder 录音');
                return;
            }

            stream = await navigator.mediaDevices.getUserMedia({
                audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
            });

            if (!this._recordingRequested) {
                stream.getTracks().forEach(t => t.stop());
                return;
            }

            this._audioChunks = [];
            if (typeof MediaRecorder.isTypeSupported === 'function' && MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
                mimeType = 'audio/webm;codecs=opus';
            } else if (typeof MediaRecorder.isTypeSupported === 'function' && MediaRecorder.isTypeSupported('audio/webm')) {
                mimeType = 'audio/webm';
            }
            const recorderOptions = mimeType ? { mimeType } : {};
            this._mediaRecorder = new MediaRecorder(stream, recorderOptions);

            this._mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) this._audioChunks.push(e.data);
            };

            this._mediaRecorder.onstop = async () => {
                // Stop all tracks
                stream.getTracks().forEach(t => t.stop());
                this._mediaRecorder = null;

                const totalSize = this._audioChunks.reduce((sum, chunk) => sum + chunk.size, 0);
                if (this._audioChunks.length === 0 || totalSize < 256) {
                    this._showSystemNotice('[语音] 没有录到声音，请按住说完再松开');
                    return;
                }

                // Convert to wav and send
                const blob = new Blob(this._audioChunks, { type: mimeType || 'audio/webm' });
                await this._sendVoice(blob, 'push_to_talk');
            };

            this._mediaRecorder.start(100); // Collect data every 100ms
            this._isRecording = true;

            if (this.micBtn) this.micBtn.classList.add('recording');
            console.log('[Voice] Recording started');

            // Interrupt any playing audio (only if enabled)
            if (this._interruptEnabled) {
                if (this._currentAudio) {
                    this._currentAudio.pause();
                    this._currentAudio = null;
                    this._isPlaying = false;
                    if (this.live2dManager) this.live2dManager.setLipSync(0);
                }
                if (this.isConnected && this.ws) {
                    this.ws.send(JSON.stringify({ type: 'interrupt' }));
                }
            }

        } catch (err) {
            console.error('[Voice] Mic access failed:', err);
            if (stream) {
                try { stream.getTracks().forEach(t => t.stop()); } catch (e) { /* ignore */ }
            }
            this._isRecording = false;
            this._recordingRequested = false;
            this._mediaRecorder = null;
            if (this.micBtn) this.micBtn.classList.remove('recording');
            this._showSystemNotice(`[语音] 麦克风打开失败：${this._describeAudioInputError(err)}`);
        }
    }

    _stopRecording() {
        this._recordingRequested = false;
        if (!this._isRecording || !this._mediaRecorder) return;

        if (this._mediaRecorder.state !== 'inactive') {
            this._mediaRecorder.stop();
        }
        this._isRecording = false;

        if (this.micBtn) this.micBtn.classList.remove('recording');
        console.log('[Voice] Recording stopped');
    }

    async _sendVoice(blob, mode = 'push_to_talk') {
        if (!this.isConnected || !this.ws) {
            this._showSystemNotice('[语音] 后端未连接，语音没有发送');
            return;
        }

        let requestId = null;
        try {
            // 2026-07-02 审计修复（批3）：原实现 btoa(String.fromCharCode(...bytes))
            // 用 spread 把每个字节当函数实参传入，长语音（持续监听2秒静默才收尾，
            // 几十秒音频可达数百KB）超出 V8 实参数量上限抛 RangeError，消息无声丢失。
            // 改为每 8192 字节一段 String.fromCharCode.apply 累积后一次 btoa。
            const arrayBuffer = await blob.arrayBuffer();
            const bytes = new Uint8Array(arrayBuffer);
            const CHUNK_SIZE = 8192;
            let binary = '';
            for (let i = 0; i < bytes.length; i += CHUNK_SIZE) {
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK_SIZE));
            }
            const base64 = btoa(binary);

            // Reset streaming state
            this._sentences = {};
            this._nextPlayIndex = 0;
            this._isPlaying = false;
            this._fullReply = '';
            this._replyDone = false;

            requestId = `voice-${Date.now()}-${++this._voiceRequestSeq}`;
            this._activeVoiceRequestId = requestId;
            this._pendingVoiceRequests.add(requestId);
            this.ws.send(JSON.stringify({
                type: 'voice',
                content: base64,
                mode,
                request_id: requestId,
            }));

            console.log(`[Voice] Sent ${(blob.size / 1024).toFixed(1)}KB audio`);
        } catch (err) {
            // 失败不再只 console.error：只发聊天系统消息，不碰说话字幕。
            console.error('[Voice] Send failed:', err);
            if (requestId) {
                this._pendingVoiceRequests.delete(requestId);
                if (this._activeVoiceRequestId === requestId) {
                    this._activeVoiceRequestId = null;
                }
            }
            this._handleVoiceStatus('error', requestId);
            this._showSystemNotice('[语音] 发送失败，请重试');
        }
    }

    _handleVoiceStatus(state, requestId = null) {
        const isBusyState = state === 'queued' || state === 'processing';
        if (requestId) {
            if (isBusyState) {
                this._pendingVoiceRequests.add(requestId);
                if (this._voiceMergeTimer) {
                    clearTimeout(this._voiceMergeTimer);
                    this._voiceMergeTimer = null;
                }
            } else {
                this._pendingVoiceRequests.delete(requestId);
            }
        }
        const isBusy = isBusyState || this._pendingVoiceRequests.size > 0;
        if (this.micBtn) {
            this.micBtn.classList.toggle('processing', isBusy);
            this.micBtn.title = isBusy ? '正在识别' : '按住说话';
        }
        if (!isBusyState) {
            if (!requestId || requestId === this._activeVoiceRequestId) {
                this._activeVoiceRequestId = null;
            }
        }
        if (!isBusy) {
            this._scheduleVoiceBufferFlush();
        }
    }

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

window.ChatController = ChatController;
