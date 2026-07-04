/**
 * settings.js - White Salary Control Panel
 *
 * Handles tab switching, loading/saving settings via backend API,
 * and service status monitoring.
 */

const API_BASE = new URLSearchParams(window.location.search).get('api') || 'http://localhost:12400';
let currentSettings = {};
let _cachedProviders = null;

// ============================================================
// Tab Navigation
// ============================================================

document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        // Deactivate all tabs and buttons
        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));

        // Activate clicked tab
        btn.classList.add('active');
        const tabId = 'tab-' + btn.dataset.tab;
        const panel = document.getElementById(tabId);
        if (panel) panel.classList.add('active');

        // 调试面板：懒加载
        if (btn.dataset.tab === 'debug') {
            if (!window._debugLoaded) {
                window._debugLoaded = true;
                initDebugPanel();
            }
        }

        // 插件市场标签页：懒加载
        if (btn.dataset.tab === 'plugins') {
            if (!window._pluginsLoaded) {
                window._pluginsLoaded = true;
                loadMarketPlugins();
            }
        }

        // 知识图谱标签页：懒加载iframe
        if (btn.dataset.tab === 'knowledge') {
            const iframe = document.getElementById('knowledge-iframe');
            if (iframe && !iframe.src.includes('knowledge.html')) {
                iframe.src = `knowledge.html?api=${API_BASE}`;
            }
        }
    });
});

// ============================================================
// Titlebar Buttons (Electron IPC)
// ============================================================

// Window controls via Electron IPC
const { ipcRenderer } = (() => {
    try { return require('electron'); }
    catch { return { ipcRenderer: null }; }
})();

document.getElementById('btn-minimize').addEventListener('click', () => {
    if (ipcRenderer) ipcRenderer.send('settings-control', 'minimize');
});
document.getElementById('btn-maximize').addEventListener('click', () => {
    if (ipcRenderer) ipcRenderer.send('settings-control', 'maximize');
});
document.getElementById('btn-close').addEventListener('click', () => {
    if (ipcRenderer) ipcRenderer.send('settings-control', 'close');
    else window.close();
});

// ============================================================
// Load Settings from Backend
// ============================================================

async function loadSettings() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/full`);
        const data = await resp.json();
        currentSettings = data.settings || {};
        populateForm(currentSettings);
        console.log('[Settings] Loaded config');
    } catch (err) {
        console.error('[Settings] Failed to load:', err);
        showHint('无法连接后端服务，请确认后端是否在运行', true);
    }
}

function populateForm(s) {
    // Main LLM
    setVal('llm-provider', s.llm?.provider);
    setVal('llm-api-key', s.llm?.api_key);
    setVal('llm-base-url', s.llm?.base_url);
    setVal('llm-model', s.llm?.model);
    setVal('llm-temperature', s.llm?.temperature);
    setVal('llm-max-tokens', s.llm?.max_tokens);
    const tempVal = document.getElementById('llm-temp-value');
    if (tempVal) tempVal.textContent = s.llm?.temperature || '0.7';

    // Sub LLM roles — each completely independent,不继承主模型
    for (const role of ['tool', 'memory', 'emotion', 'vision', 'postprocess', 'detect', 'background']) {
        const cfg = s[`llm_${role}`] || {};
        setVal(`llm-${role}-provider`, cfg.provider || '');
        setVal(`llm-${role}-api-key`, cfg.api_key || '');
        setVal(`llm-${role}-model`, cfg.model || '');
        // 2026-07-03 面板升级（批6）：子通道补 base_url 回填（此前只能用预设供应商地址，
        // 自定义代理地址无处可填，见 panel-llm.json"7个子通道表单"审计项）
        setVal(`llm-${role}-base-url`, cfg.base_url || '');
    }

    // TTS
    // 2026-07-03 面板升级（批6）：原 provider/voice/rate 是批5删除的死字段（Pydantic
    // 直接丢弃），改绑批5真实生效的字段（见 panel-voice.json 审计项）；
    // tts-provider 下拉已改只读展示当前引擎（由 checkStatus 按 /status 的 tts_local 更新）
    setVal('tts-local-api-url', s.tts?.local_api_url);
    setVal('tts-ref-audio', s.tts?.ref_audio);
    setVal('tts-ref-text', s.tts?.ref_text);
    setVal('tts-fallback-api-key', s.tts?.fallback_api_key);
    setVal('tts-fallback-model', s.tts?.fallback_model);
    setVal('tts-fallback-voice', s.tts?.fallback_voice);
    setVal('tts-speed', s.tts?.speed);
    const speedVal = document.getElementById('tts-speed-value');
    if (speedVal) speedVal.textContent = String(s.tts?.speed ?? '1.0');
    // 声音克隆页的参考音频输入框与语音页同字段（双向镜像，见 _bindMirror）
    setVal('vc-ref-audio', s.tts?.ref_audio);
    setVal('vc-ref-text', s.tts?.ref_text);

    // ASR（2026-07-03 面板升级（批6）：批5配置化后一直无UI，见 panel-voice.json"ASR无UI"审计项）
    setVal('asr-api-key', s.asr?.api_key);
    setVal('asr-model', s.asr?.model);

    // Character
    setVal('char-name', s.personality?.character_name);

    // Memory
    setVal('mem-max-turns', s.memory?.short_term_max_turns);
    setVal('mem-provider', s.memory?.long_term_provider);

    // UI
    setVal('avatar-model-path', s.avatar?.model_path);
    setVal('emotion-enabled', String(s.emotion?.enabled));

    // QQ
    setVal('qq-enabled', String(s.qq?.enabled || false));
    setVal('qq-ws-url', s.qq?.ws_url || 'ws://127.0.0.1:3001');
    setVal('qq-bot-name', s.qq?.bot_name || '白');
    setVal('qq-wake-words', (s.qq?.wake_words || ['白']).join('\n'));
    setVal('qq-token', s.qq?.token || '');
    // 家人QQ号列表
    window._familyQQList = (s.qq?.family_qq || []).map(String);
    renderFamilyQQList();

    // 主动聊天
    const ac = s.auto_chat || {};
    setVal('auto-chat-enabled', String(ac.enabled !== false));
    setVal('auto-chat-morning', String(ac.morning_greeting !== false));
    setVal('auto-chat-night', String(ac.night_greeting !== false));
    setVal('auto-chat-care', String(ac.care_reminder !== false));
    setVal('auto-chat-random', String(ac.random_chat !== false));
    setVal('auto-chat-limit', ac.daily_limit || 3);

    // 智能行为
    const feat = s.features || {};
    setVal('topic-tracker-enabled', String(feat.topic_tracker !== false));
    setVal('rest-system-enabled', String(feat.rest_system !== false));
    setVal('user-learning-enabled', String(feat.user_learning !== false));
    setVal('mem-consolidation-enabled', String(feat.memory_consolidation !== false));
    setVal('content-filter-enabled', String(feat.content_filter !== false));
}

function setVal(id, value) {
    const el = document.getElementById(id);
    if (el && value !== undefined && value !== null) {
        el.value = value;
    }
}

// ============================================================
// Load Providers List
// ============================================================

async function loadProviders() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/providers`);
        const data = await resp.json();
        const select = document.getElementById('llm-provider');
        select.innerHTML = '';

        for (const [key, info] of Object.entries(data.providers)) {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = info.name;
            select.appendChild(opt);
        }

        _cachedProviders = data.providers;

        // Re-set selected value after populating options
        if (currentSettings.llm?.provider) {
            select.value = currentSettings.llm.provider;
        }

        // 切换供应商时，Key + URL + 模型全部跟着切换
        select.addEventListener('change', () => {
            const provider = data.providers[select.value];
            if (provider) {
                // 2026-07-02 审计修复（批2）：仅当 Key 输入框为空时才填预设值——预设 api_key 全是空串，
                // 原逻辑一切换下拉就把已填密钥清空，误碰后点保存密钥即丢
                const keyInput = document.getElementById('llm-api-key');
                if (!keyInput.value) keyInput.value = provider.api_key || '';
                document.getElementById('llm-base-url').value = provider.base_url || '';
                document.getElementById('llm-model').value = provider.default_model || '';
            }
        });

        // Populate sub-role provider dropdowns + auto-fill key on change
        for (const role of ['tool', 'memory', 'emotion', 'vision', 'postprocess', 'detect', 'background']) {
            const subSelect = document.getElementById(`llm-${role}-provider`);
            if (!subSelect) continue;
            subSelect.innerHTML = '<option value="">请选择供应商</option>';
            for (const [key, info] of Object.entries(data.providers)) {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = info.name;
                subSelect.appendChild(opt);
            }
            const savedVal = currentSettings[`llm_${role}`]?.provider;
            if (savedVal) subSelect.value = savedVal;

            // When switching provider, auto-fill key + model
            subSelect.addEventListener('change', ((r) => () => {
                const prov = data.providers[document.getElementById(`llm-${r}-provider`).value];
                if (prov) {
                    // 2026-07-02 审计修复（批2）：仅当 Key 为空时才填预设值，避免切换下拉清空已填密钥
                    // （保存后 run_server 要求 api_key 与 model 同时非空，该子模型通道会静默消失）
                    const keyInput = document.getElementById(`llm-${r}-api-key`);
                    if (!keyInput.value) keyInput.value = prov.api_key || '';
                    document.getElementById(`llm-${r}-model`).value = prov.default_model || '';
                    // 2026-07-03 面板升级（批6）：切供应商同步填预设 base_url（与主通道行为
                    // 一致，settings.js 主通道 change 也是无条件覆盖 base_url）
                    const urlInput = document.getElementById(`llm-${r}-base-url`);
                    if (urlInput) urlInput.value = prov.base_url || '';
                }
            })(role));
        }
    } catch (err) {
        console.error('[Settings] Failed to load providers:', err);
    }
}

// ============================================================
// Load System Prompt
// ============================================================

async function loadPrompt() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/prompt`);
        const data = await resp.json();
        document.getElementById('char-prompt').value = data.prompt || '';
    } catch (err) {
        console.error('[Settings] Failed to load prompt:', err);
    }
}

async function savePrompt() {
    const text = document.getElementById('char-prompt').value;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/prompt`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: text }),
        });
        const data = await resp.json();
        // 2026-07-03 面板升级（批6）：按后端响应展示生效方式——hot_reloaded=true 为
        // "已热更新"，否则"重启生效"（依据 panel-persona.json"完整编辑+保存"审计项）
        if (resp.ok) {
            showHint(data.message || '人设提示词已保存！');
        } else {
            showHint('保存失败: ' + (data.detail || '未知错误'), true);
        }
    } catch (err) {
        showHint('保存失败', true);
    }
}

// ============================================================
// Save All Settings
// ============================================================

async function saveAllSettings() {
    const mainProvider = getVal('llm-provider');
    const mainProviderInfo = _cachedProviders ? _cachedProviders[mainProvider] : null;
    const mainBaseUrl = getVal('llm-base-url') || (mainProviderInfo ? mainProviderInfo.base_url : '');

    const settings = {
        llm: {
            provider: mainProvider,
            api_key: getVal('llm-api-key'),
            base_url: mainBaseUrl,
            model: getVal('llm-model'),
            temperature: parseFloat(getVal('llm-temperature')) || 0.7,
            max_tokens: parseInt(getVal('llm-max-tokens')) || 2048,
        },
        // 2026-07-03 面板升级（批6）：tts 节改存批5真实生效的字段，不再写 provider/voice/rate
        // 死键（TTSConfig 无这些字段会被 Pydantic 丢弃，见 panel-voice.json 审计项）；
        // fallback_provider 只读不提交（当前仅支持 siliconflow，保持 conf 现值）
        tts: {
            local_api_url: getVal('tts-local-api-url'),
            ref_audio: getVal('tts-ref-audio'),
            ref_text: getVal('tts-ref-text'),
            fallback_api_key: getVal('tts-fallback-api-key'),
            fallback_model: getVal('tts-fallback-model'),
            fallback_voice: getVal('tts-fallback-voice'),
            speed: parseFloat(getVal('tts-speed')) || 1.0,
        },
        // 2026-07-03 面板升级（批6）：ASR 节（批5配置化字段，此前无UI）
        asr: {
            api_key: getVal('asr-api-key'),
            model: getVal('asr-model'),
        },
        personality: {
            character_name: getVal('char-name'),
        },
        memory: {
            short_term_max_turns: parseInt(getVal('mem-max-turns')) || 20,
            long_term_provider: getVal('mem-provider'),
        },
        avatar: {
            model_path: getVal('avatar-model-path'),
        },
        emotion: {
            enabled: getVal('emotion-enabled') === 'true',
        },
        qq: {
            enabled: getVal('qq-enabled') === 'true',
            ws_url: getVal('qq-ws-url'),
            bot_name: getVal('qq-bot-name'),
            wake_words: getVal('qq-wake-words')
                .split(/[\n,，]+/)
                .map(s => s.trim())
                .filter(Boolean),
            token: getVal('qq-token'),
            family_qq: (window._familyQQList || []).map(Number),
        },
        auto_chat: {
            enabled: getVal('auto-chat-enabled') === 'true',
            morning_greeting: getVal('auto-chat-morning') === 'true',
            night_greeting: getVal('auto-chat-night') === 'true',
            care_reminder: getVal('auto-chat-care') === 'true',
            random_chat: getVal('auto-chat-random') === 'true',
            daily_limit: parseInt(getVal('auto-chat-limit')) || 3,
        },
        features: {
            topic_tracker: getVal('topic-tracker-enabled') === 'true',
            rest_system: getVal('rest-system-enabled') === 'true',
            user_learning: getVal('user-learning-enabled') === 'true',
            memory_consolidation: getVal('mem-consolidation-enabled') === 'true',
            content_filter: getVal('content-filter-enabled') === 'true',
        },
    };

    // Save all 5 sub-role LLM configs — each completely independent
    for (const role of ['tool', 'memory', 'emotion', 'vision', 'postprocess', 'detect', 'background']) {
        const provider = getVal(`llm-${role}-provider`);
        const apiKey = getVal(`llm-${role}-api-key`);
        const model = getVal(`llm-${role}-model`);
        const providerInfo = _cachedProviders ? _cachedProviders[provider] : null;
        // 2026-07-03 面板升级（批6）：优先用子通道 base_url 输入框的值（支持自定义代理
        // 地址），留空才回退预设供应商地址（= 原行为，见 panel-llm.json"7个子通道表单"）
        const baseUrl = getVal(`llm-${role}-base-url`)
            || (providerInfo ? providerInfo.base_url : '');
        settings[`llm_${role}`] = {
            provider: provider,
            api_key: apiKey,
            model: model,
            base_url: baseUrl,
        };
    }

    // 显示保存中状态
    const saveBtn = document.querySelector('.save-bar .btn-primary');
    const originalText = saveBtn ? saveBtn.textContent : '';
    if (saveBtn) { saveBtn.textContent = '保存中...'; saveBtn.disabled = true; }

    try {
        const resp = await fetch(`${API_BASE}/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings }),
        });
        const data = await resp.json();
        showHint(data.message || '设置已保存！重启后端生效');
    } catch (err) {
        showHint('保存失败: ' + err.message, true);
    } finally {
        if (saveBtn) { saveBtn.textContent = originalText; saveBtn.disabled = false; }
    }
}

function getVal(id) {
    const el = document.getElementById(id);
    return el ? el.value : '';
}

// ============================================================
// Service Status Check
// ============================================================

// 视觉系统控制（桌面截屏/摄像头）
function toggleDesktopVision(value) {
    const enabled = value === "true";
    // 通过Electron IPC通知主窗口
    if (window.electronAPI && window.electronAPI.send) {
        window.electronAPI.send("toggle-desktop-vision", enabled);
        showHint(enabled ? "桌面截屏已开启" : "桌面截屏已关闭");
    } else {
        // 非Electron环境（浏览器直接打开），保存到设置
        showHint(enabled ? "桌面截屏已开启（重启后生效）" : "桌面截屏已关闭");
    }
}

function toggleCameraVision(value) {
    const enabled = value === "true";
    if (window.electronAPI && window.electronAPI.send) {
        window.electronAPI.send("toggle-camera-vision", enabled);
        showHint(enabled ? "摄像头已开启" : "摄像头已关闭");
    } else {
        showHint(enabled ? "摄像头已开启（重启后生效）" : "摄像头已关闭");
    }
}

// 2026-07-03 面板升级（批6）：加 force 参数——手动点"刷新状态"传 true 带 ?force=1
// 跳过后端10秒TTL缓存，真正即时探测（依据 panel-main.json"刷新状态按钮"审计项）
async function checkStatus(force = false) {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/status${force ? '?force=1' : ''}`);
        const data = await resp.json();

        setStatus('status-backend', data.backend);
        setStatus('status-tts', data.tts_local);
        setStatus('status-qq', data.qq_connected || false);

        // 2026-07-03 面板升级（批6）：语音页只读下拉展示当前实际生效引擎
        // （本地9880在线走 GPT-SoVITS，否则 SiliconFlow 云端兜底，见 panel-voice.json）
        const ttsProviderSel = document.getElementById('tts-provider');
        if (ttsProviderSel) {
            ttsProviderSel.value = data.tts_local ? 'gpt_sovits' : 'siliconflow';
        }
        setStatus('term-status-backend', data.backend);
        setStatus('term-status-tts', data.tts_local);
        setStatus('term-status-qq', data.qq_connected || false);

        const qqTermDetail = document.getElementById('term-detail-qq');
        if (qqTermDetail) {
            qqTermDetail.textContent = data.qq_connected ? '已连接' : '未连接';
        }

        // QQ状态详情
        const qqDetail = document.getElementById('detail-qq');
        if (qqDetail) {
            qqDetail.textContent = data.qq_connected ? '已连接' : '未连接';
        }

        // 记忆统计
        const memCount = document.getElementById('memory-count-home');
        const memDetail = document.getElementById('memory-detail-home');
        if (memCount && data.memory_count !== undefined) {
            memCount.textContent = `${data.memory_count || 0}条`;
        }
        if (memDetail && data.conversation_count !== undefined) {
            memDetail.textContent = `对话${data.conversation_count || 0}条`;
        }

        // Update terminal detail text with PID
        const backendDetail = document.getElementById('term-detail-backend');
        if (backendDetail) {
            backendDetail.textContent = data.backend
                ? `端口 ${data.backend_port} | PID: ${data.backend_pid || '?'}`
                : '未运行';
        }
        const ttsDetail = document.getElementById('term-detail-tts');
        if (ttsDetail) {
            ttsDetail.textContent = data.tts_local
                ? `端口 ${data.tts_port} | PID: ${data.tts_pid || '?'}`
                : '未运行';
        }
    } catch {
        setStatus('status-backend', false);
        setStatus('status-tts', false);
        setStatus('term-status-backend', false);
        setStatus('term-status-tts', false);
    }
}

function setStatus(id, online) {
    const el = document.getElementById(id);
    if (el) {
        el.className = 'status-indicator ' + (online ? 'online' : 'offline');
    }
}

async function loadLogs() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/logs`);
        const data = await resp.json();
        const viewer = document.getElementById('terminal-log-viewer');
        if (viewer && data.logs) {
            viewer.innerHTML = data.logs
                .map(line => `<div class="log-line">${escapeHtml(line)}</div>`)
                .join('');
            viewer.scrollTop = viewer.scrollHeight;
        }
    } catch (err) {
        console.error('[Settings] Failed to load logs:', err);
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 2026-07-03 面板升级（批6）：把任意值安全编码成可嵌入内联 onclick 单引号字符串
 * 的参数（配合 decodeURIComponent 还原）。注意 encodeURIComponent 本身不转义
 * 单引号，必须补一刀 %27，否则内容含 ' 时会逃逸出 JS 字符串（注入点）。
 */
function encodeJsArg(value) {
    return encodeURIComponent(String(value)).replace(/'/g, '%27');
}

// ============================================================
// Memory System
// ============================================================

async function loadMemory() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/memory`);
        const data = await resp.json();

        // 好感度
        if (data.affinity) {
            const a = data.affinity;
            const emojiEl = document.getElementById('affinity-emoji');
            const levelEl = document.getElementById('affinity-level');
            const ptsEl = document.getElementById('affinity-points');
            if (emojiEl) emojiEl.textContent = a.emoji || '👤';
            if (levelEl) levelEl.textContent = a.level_name || '陌生人';
            if (ptsEl) ptsEl.textContent = `${a.points || 0}分 | 连续${a.consecutive_days || 0}天`;
        }

        // Home dashboard
        if (data.affinity) {
            const a = data.affinity;
            const el1 = document.getElementById('affinity-emoji-home');
            const el2 = document.getElementById('affinity-level-home');
            const el3 = document.getElementById('affinity-pts-home');
            if (el1) el1.textContent = a.emoji || '👤';
            if (el2) el2.textContent = a.level_name || '--';
            if (el3) el3.textContent = `${a.points || 0}分`;
        }
        if (data.emotion) {
            const e = data.emotion;
            const el1 = document.getElementById('mood-emoji-home');
            const el2 = document.getElementById('mood-desc-home');
            const el3 = document.getElementById('mood-pts-home');
            if (el1) el1.textContent = e.mood_emoji || '😊';
            if (el2) el2.textContent = e.mood_description || '--';
            if (el3) el3.textContent = `${e.mood_score || 80}分`;
        }

        // 好感度历史
        if (data.affinity && data.affinity.history) {
            const histEl = document.getElementById('affinity-history');
            if (histEl) {
                const hist = data.affinity.history;
                if (hist.length === 0) {
                    histEl.innerHTML = '<div class="log-line" style="color:#6b7b8d;">暂无变化记录</div>';
                } else {
                    histEl.innerHTML = hist.map(h =>
                        `<div class="log-line">[${h.time || ''}] ${h.reason || ''}: ${h.delta > 0 ? '+' : ''}${h.delta} (${h.old}→${h.new})</div>`
                    ).join('');
                    histEl.scrollTop = histEl.scrollHeight;
                }
            }
        }

        // 情绪历史
        if (data.emotion_history) {
            const emoHistEl = document.getElementById('emotion-history');
            if (emoHistEl) {
                if (data.emotion_history.length === 0) {
                    emoHistEl.innerHTML = '<div class="log-line" style="color:#6b7b8d;">暂无情绪记录</div>';
                } else {
                    emoHistEl.innerHTML = data.emotion_history.map(e =>
                        `<div class="log-line">[${e.time || ''}] ${e.emotion} (强度${e.intensity}) → 心情${e.mood_score}分 ${e.change > 0 ? '+' : ''}${e.change} | ${e.trigger || ''}</div>`
                    ).join('');
                    emoHistEl.scrollTop = emoHistEl.scrollHeight;
                }
            }
        }

        // 情绪
        if (data.emotion) {
            const e = data.emotion;
            const emojiEl = document.getElementById('mood-emoji');
            const descEl = document.getElementById('mood-desc');
            const scoreEl = document.getElementById('mood-score');
            if (emojiEl) emojiEl.textContent = e.mood_emoji || '😊';
            if (descEl) descEl.textContent = e.mood_description || '正常';
            if (scoreEl) scoreEl.textContent = `${e.mood_score || 80}分`;
        }

        // 核心记忆列表
        const coreList = document.getElementById('core-memory-list');
        if (coreList && data.core_memories) {
            if (data.core_memories.length === 0) {
                coreList.innerHTML = '<div class="log-line" style="color:#6b7b8d;">暂无核心记忆（和白聊天时会自动记住你的信息）</div>';
            } else {
                // 2026-07-03 面板升级（批6）：每行加"编辑"按钮——回填 key/value 到下方输入框
                // 复用 addCoreMemory 提交（core_store.set 同键即覆盖，见 panel-memory.json
                // "核心记忆编辑（改）"审计项）；onclick 参数用 encodeURIComponent 防引号逃逸
                coreList.innerHTML = data.core_memories.map(m =>
                    `<div class="about-row">
                        <span>${escapeHtml(m.key)}</span>
                        <span>${escapeHtml(m.value)}
                            <button class="btn-small" style="margin-left:8px;padding:2px 8px;font-size:10px;" onclick="editCoreMemory('${encodeJsArg(m.key)}','${encodeJsArg(m.value)}')">编辑</button>
                            <button class="btn-small" style="margin-left:4px;padding:2px 8px;font-size:10px;" onclick="deleteCoreMemory(decodeURIComponent('${encodeJsArg(m.key)}'))">删除</button>
                        </span>
                    </div>`
                ).join('');
            }
        }

        // 长期记忆统计
        const ltStats = document.getElementById('long-term-stats');
        if (ltStats && data.long_term_stats) {
            const s = data.long_term_stats;
            let html = `<div class="about-row"><span>总计</span><span>${s.total || 0} 条</span></div>`;
            if (s.by_layer) {
                const layerNames = {fact:'永久事实',event:'事件(365天)',emotion:'情感(30天)',temp:'临时(1天)'};
                for (const [layer, count] of Object.entries(s.by_layer)) {
                    html += `<div class="about-row"><span>${layerNames[layer]||layer}</span><span>${count} 条</span></div>`;
                }
            }
            html += `<div class="about-row"><span>精华记忆</span><span>${s.highlights || 0} 条</span></div>`;
            ltStats.innerHTML = html;
        }

        // 最近记忆
        const recentEl = document.getElementById('recent-memories');
        if (recentEl && data.long_term_recent) {
            if (data.long_term_recent.length === 0) {
                recentEl.innerHTML = '<div class="log-line" style="color:#6b7b8d;">暂无长期记忆</div>';
            } else {
                recentEl.innerHTML = data.long_term_recent.map(m => {
                    const hl = m.is_highlight ? ' ★' : '';
                    return `<div class="log-line">[${m.layer}]${hl} ${escapeHtml(m.content)}</div>`;
                }).join('');
                recentEl.scrollTop = recentEl.scrollHeight;
            }
        }
    } catch (err) {
        console.error('[Settings] Failed to load memory:', err);
    }
}

async function addCoreMemory() {
    const key = document.getElementById('new-mem-key').value.trim();
    const value = document.getElementById('new-mem-value').value.trim();
    if (!key || !value) { showHint('请填写键名和内容', true); return; }

    try {
        await fetch(`${API_BASE}/api/settings/memory/core`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ key, value, category: 'other', importance: 5 }),
        });
        document.getElementById('new-mem-key').value = '';
        document.getElementById('new-mem-value').value = '';
        showHint('核心记忆已添加');
        await loadMemory();
    } catch (err) {
        showHint('添加失败', true);
    }
}

// ============================================================
// Expression Drag-Drop Mapping
// ============================================================

// 2026-07-03 面板升级（批6）：默认映射与后端 EmotionTracker.EXPRESSION_MAP 逐键对齐
// （16种情绪，字段 expression/motion_group/mouth_form）；此表仅作后端不可达时的兜底，
// 正常流程从批6新端点 GET /expression-map 拉取（依据 panel-expressions.json 审计项）
const DEFAULT_EXPRESSION_MAP = {
    happy:      { expression: 'happy',     motion_group: 'idle', mouth_form: 0.3 },
    excited:    { expression: 'happy',     motion_group: 'tap',  mouth_form: 0.5 },
    grateful:   { expression: 'happy',     motion_group: 'idle', mouth_form: 0.2 },
    touched:    { expression: 'shy',       motion_group: 'idle', mouth_form: 0.1 },
    playful:    { expression: 'happy',     motion_group: 'tap',  mouth_form: 0.4 },
    shy:        { expression: 'shy',       motion_group: 'idle', mouth_form: 0.1 },
    calm:       { expression: 'default',   motion_group: 'idle', mouth_form: 0.0 },
    neutral:    { expression: 'default',   motion_group: 'idle', mouth_form: 0.0 },
    bored:      { expression: 'default',   motion_group: 'idle', mouth_form: 0.0 },
    confused:   { expression: 'surprised', motion_group: 'idle', mouth_form: 0.2 },
    sad:        { expression: 'sad',       motion_group: 'idle', mouth_form: 0.0 },
    angry:      { expression: 'angry',     motion_group: 'tap',  mouth_form: 0.3 },
    frustrated: { expression: 'angry',     motion_group: 'idle', mouth_form: 0.2 },
    hurt:       { expression: 'sad',       motion_group: 'idle', mouth_form: 0.0 },
    scared:     { expression: 'surprised', motion_group: 'idle', mouth_form: 0.1 },
    surprised:  { expression: 'surprised', motion_group: 'tap',  mouth_form: 0.4 },
};

// 2026-07-03 面板升级（批6）：情绪槽从8种补齐到16种（原来只覆盖后端一半情绪）
const EMOTION_LABELS = {
    happy: '😄 开心', excited: '🤩 兴奋', grateful: '🥰 感激', touched: '😊 感动',
    playful: '😜 调皮', shy: '😳 害羞', calm: '😌 平和', neutral: '😐 平静',
    bored: '😑 无聊', confused: '🤔 困惑', sad: '😢 难过', angry: '😠 生气',
    frustrated: '😤 沮丧', hurt: '💔 受伤', scared: '😨 害怕', surprised: '😲 惊讶',
};

// 2026-07-03 面板升级（批6）：表情池改动态（GET /live2d/expressions 枚举模型全部38个
// 表情），此处硬编码仅作后端不可达兜底；动作池保留硬编码（当前模型 Motions 为空，
// 池子仅展示，HTML 已加"仅表情生效"说明，不删UI）
let AVAILABLE_EXPRESSIONS = ['default', 'happy', 'sad', 'angry', 'surprised', 'shy'];
const AVAILABLE_MOTIONS = ['idle', 'tap', 'flick', 'pinch'];

let currentMapping = { ...DEFAULT_EXPRESSION_MAP };

/**
 * 2026-07-03 面板升级（批6）：把任意来源（后端/localStorage旧缓存）的映射
 * 规整成 {expression, motion_group, mouth_form} 形状——旧8情绪缓存用的是
 * motion 键，这里兼容迁移，避免脏字段写回后端。
 */
function normalizeExpressionMap(raw) {
    const result = {};
    for (const [key, def] of Object.entries(DEFAULT_EXPRESSION_MAP)) {
        const v = (raw && typeof raw[key] === 'object' && raw[key]) || {};
        result[key] = {
            expression: v.expression || def.expression,
            motion_group: v.motion_group || v.motion || def.motion_group,
            mouth_form: (typeof v.mouth_form === 'number') ? v.mouth_form : def.mouth_form,
        };
    }
    return result;
}

async function initExpressionMapping() {
    // 2026-07-03 面板升级（批6）：优先从后端拉真实映射（config/expression_map.json，
    // EmotionTracker 实时消费）；后端不可达回退 localStorage 离线缓存，再回退默认表
    let loaded = false;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/expression-map`);
        const data = await resp.json();
        if (data.map && typeof data.map === 'object') {
            currentMapping = normalizeExpressionMap(data.map);
            loaded = true;
            // 同步一份到 localStorage 作离线缓存
            try { localStorage.setItem('ws_expression_map', JSON.stringify(currentMapping)); } catch {}
        }
    } catch {}
    if (!loaded) {
        try {
            const saved = localStorage.getItem('ws_expression_map');
            if (saved) currentMapping = normalizeExpressionMap(JSON.parse(saved));
        } catch {}
    }

    // 表情池动态枚举（批6新端点；失败保留硬编码6个兜底）
    try {
        const resp = await fetch(`${API_BASE}/api/settings/live2d/expressions`);
        const data = await resp.json();
        if (Array.isArray(data.expressions) && data.expressions.length > 0) {
            AVAILABLE_EXPRESSIONS = data.expressions;
        }
    } catch {}

    renderEmotionSlots();
    renderPools();
}

function renderEmotionSlots() {
    const container = document.getElementById('emotion-slots');
    if (!container) return;

    container.innerHTML = Object.entries(EMOTION_LABELS).map(([key, label]) => {
        const map = currentMapping[key] || { expression: 'default', motion_group: 'idle' };
        return `<div class="emotion-slot" data-emotion="${key}"
                     ondragover="event.preventDefault();this.style.borderColor='#00d4ff'"
                     ondragleave="this.style.borderColor='rgba(0,212,255,0.15)'"
                     ondrop="handleExprDrop(event,'${key}')">
            <span style="min-width:90px;">${label}</span>
            <span style="color:#00d4ff;font-size:12px;">表情: ${escapeHtml(map.expression || 'default')}</span>
            <span style="color:#7a8fa0;font-size:12px;">动作: ${escapeHtml(map.motion_group || 'idle')}</span>
        </div>`;
    }).join('');
}

function renderPools() {
    const exprPool = document.getElementById('expression-pool');
    const motionPool = document.getElementById('motion-pool');
    if (!exprPool || !motionPool) return;

    exprPool.innerHTML = AVAILABLE_EXPRESSIONS.map(e =>
        `<div class="drag-chip" draggable="true" ondragstart="event.dataTransfer.setData('text','expr:${escapeHtml(e)}')">${escapeHtml(e)}</div>`
    ).join('');

    motionPool.innerHTML = AVAILABLE_MOTIONS.map(m =>
        `<div class="drag-chip" draggable="true" ondragstart="event.dataTransfer.setData('text','motion:${m}')">${m}</div>`
    ).join('');
}

function handleExprDrop(event, emotion) {
    event.preventDefault();
    event.target.closest('.emotion-slot').style.borderColor = 'rgba(0,212,255,0.15)';

    const data = event.dataTransfer.getData('text');
    if (!data) return;

    // 表情名可能含冒号，只按第一个冒号切
    const sep = data.indexOf(':');
    if (sep < 0) return;
    const type = data.slice(0, sep);
    const value = data.slice(sep + 1);
    if (!currentMapping[emotion]) {
        currentMapping[emotion] = { expression: 'default', motion_group: 'idle', mouth_form: 0.0 };
    }

    if (type === 'expr') {
        currentMapping[emotion].expression = value;
    } else if (type === 'motion') {
        // 2026-07-03 面板升级（批6）：键名对齐后端 motion_group
        currentMapping[emotion].motion_group = value;
    }

    renderEmotionSlots();
    showHint(`已绑定: ${EMOTION_LABELS[emotion]} → ${type === 'expr' ? '表情' : '动作'}: ${value}`);
}

async function saveExpressionMapping() {
    // 2026-07-03 面板升级（批6）：改调批6新端点 PUT /expression-map 落盘
    // config/expression_map.json（EmotionTracker 每次取表情时实时读取，改完即生效）；
    // localStorage 保留作离线缓存（依据 panel-expressions.json"映射编辑器"审计项）
    try { localStorage.setItem('ws_expression_map', JSON.stringify(currentMapping)); } catch {}
    try {
        const resp = await fetch(`${API_BASE}/api/settings/expression-map`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ map: currentMapping }),
        });
        const data = await resp.json();
        if (data.status === 'ok') {
            showHint(data.message || '表情映射已保存（即时生效）');
        } else {
            showHint('保存失败: ' + (data.detail || '未知错误'), true);
        }
    } catch (err) {
        showHint('后端不可达，映射已暂存本地缓存，恢复后请重新保存', true);
    }
}

async function restartBackend() {
    if (!confirm('确定重启后端？当前对话会断开，设置修改会立即生效。')) return;
    try {
        await fetch(`${API_BASE}/api/settings/restart`, { method: 'POST' });
        showHint('后端正在重启...请等待几秒');
        // Wait and reconnect
        setTimeout(() => { location.reload(); }, 5000);
    } catch (err) {
        showHint('重启请求已发送，等待重新连接...', false);
        setTimeout(() => { location.reload(); }, 5000);
    }
}

function startNapCat() {
    try {
        const { ipcRenderer } = require('electron');
        // Ask main process to start NapCat
        ipcRenderer.send('start-napcat');
        showHint('NapCat 启动中...');
    } catch {
        // Fallback: try via API
        fetch(`${API_BASE}/api/settings/start-napcat`, { method: 'POST' }).catch(() => {});
        showHint('NapCat 启动请求已发送');
    }
}

function openNapCatWebUI() {
    try {
        require('electron').shell.openExternal('http://127.0.0.1:6099');
    } catch {
        window.open('http://127.0.0.1:6099', '_blank');
    }
}

async function setFamily(isFamily) {
    try {
        await fetch(`${API_BASE}/api/settings/affinity/set_family`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ is_family: isFamily }),
        });
        showHint(isFamily ? '已设为家人' : '已取消家人');
        await loadMemory();
    } catch (err) { showHint('设置失败', true); }
}

async function setPoints() {
    const pts = document.getElementById('manual-points')?.value;
    if (!pts) { showHint('请输入分数', true); return; }
    try {
        await fetch(`${API_BASE}/api/settings/affinity/set_points`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ points: parseFloat(pts) }),
        });
        showHint(`好感度已设为 ${pts}`);
        await loadMemory();
    } catch (err) { showHint('设置失败', true); }
}

async function loadTemplates() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/prompt/templates`);
        const data = await resp.json();
        const list = document.getElementById('template-list');
        if (!list || !data.templates) return;

        if (data.templates.length === 0) {
            list.innerHTML = '<div class="log-line" style="color:#6b7b8d;">暂无模板</div>';
            return;
        }

        list.innerHTML = data.templates.map(t =>
            `<div class="about-row">
                <span>${escapeHtml(t.name)}</span>
                <span>
                    <span style="color:#6b7b8d;font-size:11px;">${escapeHtml(t.preview.substring(0, 40))}...</span>
                    <button class="btn-small" style="margin-left:8px;padding:2px 8px;font-size:10px;"
                            onclick="applyTemplate('${escapeHtml(t.file)}')">应用</button>
                </span>
            </div>`
        ).join('');
    } catch (err) {
        console.error('[Settings] Failed to load templates:', err);
    }
}

async function applyTemplate(file) {
    if (!confirm('确定要应用此模板吗？将覆盖当前的系统提示词！（原人设会自动备份到 prompts/backups/）')) return;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/prompt/apply_template`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ file }),
        });
        const data = await resp.json();
        // 2026-07-03 面板升级（批6）：提示注明已自动备份及找回位置（后端批6已在覆盖前
        // 备份到 prompts/backups/，依据 panel-persona.json"人设模板库"审计项）
        if (resp.ok) {
            showHint(`${data.message || '模板已应用'}，重启后生效；原人设已自动备份，可在 prompts/backups/ 找回`);
        } else {
            showHint('应用失败: ' + (data.detail || '未知错误'), true);
        }
        await loadPrompt();
    } catch (err) {
        showHint('应用失败', true);
    }
}

async function deleteCoreMemory(key) {
    try {
        await fetch(`${API_BASE}/api/settings/memory/core/${encodeURIComponent(key)}`, {
            method: 'DELETE',
        });
        showHint('记忆已删除');
        await loadMemory();
    } catch (err) {
        showHint('删除失败', true);
    }
}

async function loadLaunchLogs() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/logs`);
        const data = await resp.json();
        const viewer = document.getElementById('launch-log-viewer');
        if (viewer && data.logs) {
            // Show last 30 lines on launch tab (shorter)
            const recent = data.logs.slice(-30);
            viewer.innerHTML = recent
                .map(line => `<div class="log-line">${escapeHtml(line)}</div>`)
                .join('');
            viewer.scrollTop = viewer.scrollHeight;
        }
    } catch (err) {
        console.error('[Settings] Failed to load launch logs:', err);
    }
}

// ============================================================
// Utilities
// ============================================================

function togglePassword(id) {
    const input = document.getElementById(id);
    if (input) {
        input.type = input.type === 'password' ? 'text' : 'password';
    }
}

function showHint(msg, isError = false) {
    const hint = document.getElementById('save-hint');
    if (hint) {
        hint.textContent = msg;
        hint.style.color = isError ? '#e74c3c' : '#2ecc71';
        hint.style.opacity = '1';
        hint.style.transition = 'opacity 0.3s';
        // 3秒后渐出
        clearTimeout(hint._fadeTimer);
        hint._fadeTimer = setTimeout(() => {
            hint.style.opacity = '0';
            setTimeout(() => { hint.textContent = ''; hint.style.opacity = '1'; }, 300);
        }, 4000);
    }
}

// ============================================================
// Launch Buttons & Quick Actions
// ============================================================

function startLocalTTS() {
    try {
        const { ipcRenderer } = require('electron');
        ipcRenderer.send('start-local-tts');
        showHint('本地TTS启动中...模型加载需要约45秒');
    } catch {
        // 非Electron环境（设置窗口），走API
        fetch(`${API_BASE}/api/settings/start-tts`, { method: 'POST' })
            .then(() => showHint('TTS启动请求已发送'))
            .catch(() => showHint('TTS启动失败', true));
    }
}

function openProjectDir() {
    // 2026-07-03 面板升级（批6）：去掉硬编码绝对路径——settings.html 位于 frontend/，
    // 上一级即项目根，换机不失效（依据 panel-main.json"打开项目目录按钮"审计项）
    try {
        const path = require('path');
        require('electron').shell.openPath(path.join(__dirname, '..'));
    } catch {
        showHint('请手动打开项目根目录');
    }
}

function openNapCatLogs() {
    // 2026-07-03 面板升级（批6）：按钮叫"NapCat日志目录"，补 logs 子目录名实相符
    // （依据 panel-main.json"NapCat日志目录按钮"审计项）
    try {
        const path = require('path');
        const napLogDir = path.join(__dirname, '..', 'NapCat', 'logs');
        require('electron').shell.openPath(napLogDir);
    } catch {
        showHint('请手动打开 NapCat/logs 目录');
    }
}

async function triggerMemoryConsolidation() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/memory/consolidate`, { method: 'POST' });
        const data = await resp.json();
        showHint(`记忆整理完成: 去重${data.duplicates_removed || 0}条, 过期清理${data.expired_removed || 0}条`);
        await loadMemory();
    } catch (err) {
        showHint('记忆整理失败', true);
    }
}

async function triggerUserLearning() {
    try {
        showHint('正在分析用户画像...请稍候');
        const resp = await fetch(`${API_BASE}/api/settings/user-learning/trigger`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            showHint('用户画像分析完成！');
        } else {
            showHint(data.message || '分析未触发（消息不足）', true);
        }
    } catch (err) {
        showHint('分析失败', true);
    }
}

async function clearDesktopChat() {
    if (!confirm('确定清空桌面端对话历史？')) return;
    try {
        await fetch(`${API_BASE}/api/settings/chat/reset`, { method: 'POST' });
        showHint('桌面对话已清空');
    } catch { showHint('清空失败', true); }
}

async function clearQQContext() {
    if (!confirm('确定清空QQ对话上下文？')) return;
    try {
        await fetch(`${API_BASE}/api/settings/qq/clear-context`, { method: 'POST' });
        showHint('QQ对话上下文已清空');
    } catch { showHint('清空失败', true); }
}

// ============================================================
// Debug Tools
// ============================================================

let _debugTools = [];
let _debugLogInterval = null;

async function loadDebugTools() {
    // 加载所有可用工具列表
    try {
        const resp = await fetch(`${API_BASE}/api/settings/knowledge/stats`);
        // 从工具注册表获取工具列表（通过一个特殊的API）
    } catch {}

    // 直接从页面构建工具选项（通过API获取工具列表）
    try {
        const resp = await fetch(`${API_BASE}/api/settings/status`);
        const data = await resp.json();
        // 工具列表目前没有专门的API，先用预设列表
    } catch {}

    const select = document.getElementById('debug-tool-select');
    if (!select) return;
    // 常用工具列表
    const tools = [
        { name: 'get_current_time', desc: '获取当前时间', params: '{}' },
        { name: 'calculator', desc: '数学计算', params: '{"expression": "2+3*4"}' },
        { name: 'web_search', desc: '搜索互联网', params: '{"query": "Python教程"}' },
        { name: 'bilibili_search', desc: 'B站搜索', params: '{"query": "编程"}' },
        { name: 'memory_search', desc: '搜索记忆', params: '{"keyword": "小白"}' },
        { name: 'recall_conversation', desc: '回忆对话', params: '{"keyword": "你好"}' },
        { name: 'query_knowledge_graph', desc: '查询知识图谱', params: '{"question": "华月姐姐是谁"}' },
        { name: 'path_query', desc: '关系路径', params: '{"start_name": "白", "max_depth": 2}' },
        { name: 'evaluate_person', desc: '评价人物', params: '{"person_name": "华月姐姐"}' },
        { name: 'affinity_check', desc: '查询好感度', params: '{"user_id": "default"}' },
        { name: 'affinity_ranking', desc: '好感度排行', params: '{}' },
        { name: 'learning_stats', desc: '学习统计', params: '{}' },
        { name: 'pc_control', desc: 'PC控制', params: '{"action": "screenshot", "target": ""}' },
        { name: 'dice_roller', desc: '掷骰子', params: '{"expression": "2d6+3"}' },
        { name: 'random_number', desc: '随机数', params: '{"min": 1, "max": 100}' },
    ];
    _debugTools = tools;
    select.innerHTML = '<option value="">选择工具...</option>' +
        tools.map(t => `<option value="${t.name}">${t.name} — ${t.desc}</option>`).join('');
}

function onDebugToolSelect() {
    const name = document.getElementById('debug-tool-select').value;
    const tool = _debugTools.find(t => t.name === name);
    document.getElementById('debug-tool-desc').textContent = tool ? tool.desc : '';
    document.getElementById('debug-tool-params').value = tool ? tool.params : '';
}

async function debugTestTool() {
    const name = document.getElementById('debug-tool-select').value;
    if (!name) { showHint('请选择工具', true); return; }

    let params = {};
    try { params = JSON.parse(document.getElementById('debug-tool-params').value || '{}'); } catch (e) {
        showHint('参数JSON格式错误', true); return;
    }

    const btn = document.getElementById('debug-tool-btn');
    btn.textContent = '执行中...'; btn.disabled = true;
    const t0 = Date.now();

    try {
        // 调用工具执行API
        const resp = await fetch(`${API_BASE}/api/settings/knowledge/query`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ question: `调用工具${name}: ${JSON.stringify(params)}` }),
        });
        const data = await resp.json();
        const elapsed = Date.now() - t0;

        const resultDiv = document.getElementById('debug-tool-result');
        resultDiv.style.display = 'block';
        resultDiv.innerHTML = `<div class="log-line" style="color:#22c55e;">[${name}] 执行完成 (${elapsed}ms)</div>` +
            `<div class="log-line">${escapeHtml(JSON.stringify(data, null, 2))}</div>`;
        document.getElementById('debug-tool-time').textContent = `${elapsed}ms`;
    } catch (err) {
        const resultDiv = document.getElementById('debug-tool-result');
        resultDiv.style.display = 'block';
        resultDiv.innerHTML = `<div class="log-line" style="color:#e74c3c;">执行失败: ${err.message}</div>`;
    } finally {
        btn.textContent = '执行'; btn.disabled = false;
    }
}

async function debugTestApi() {
    const method = document.getElementById('debug-api-method').value;
    const url = document.getElementById('debug-api-url').value.trim();
    if (!url) { showHint('请输入API路径', true); return; }

    const fullUrl = url.startsWith('http') ? url : `${API_BASE}${url}`;
    const btn = document.getElementById('debug-api-btn');
    btn.textContent = '发送中...'; btn.disabled = true;

    try {
        const options = { method, headers: {'Content-Type':'application/json'} };
        if (method !== 'GET') {
            const body = document.getElementById('debug-api-body').value.trim();
            if (body) options.body = body;
        }

        const t0 = Date.now();
        const resp = await fetch(fullUrl, options);
        const elapsed = Date.now() - t0;
        const data = await resp.json().catch(() => resp.text());

        const statusDiv = document.getElementById('debug-api-status');
        statusDiv.style.display = 'block';
        statusDiv.innerHTML = `<span style="color:${resp.ok?'#22c55e':'#e74c3c'}">HTTP ${resp.status}</span> | ${elapsed}ms`;

        const resultDiv = document.getElementById('debug-api-result');
        resultDiv.style.display = 'block';
        resultDiv.innerHTML = `<div class="log-line">${escapeHtml(typeof data === 'string' ? data : JSON.stringify(data, null, 2))}</div>`;
    } catch (err) {
        document.getElementById('debug-api-status').style.display = 'block';
        document.getElementById('debug-api-status').innerHTML = `<span style="color:#e74c3c">请求失败: ${err.message}</span>`;
    } finally {
        btn.textContent = '发送'; btn.disabled = false;
    }
}

function debugApiQuick(path) {
    document.getElementById('debug-api-url').value = path;
    document.getElementById('debug-api-method').value = 'GET';
    debugTestApi();
}

async function debugAction(action) {
    const resultDiv = document.getElementById('debug-action-result');
    resultDiv.style.display = 'block';
    resultDiv.innerHTML = '<div class="log-line">执行中...</div>';

    const actions = {
        test_llm: async () => {
            const resp = await fetch(`${API_BASE}/api/settings/status`);
            return `后端状态: ${(await resp.json()).backend ? '正常' : '异常'}`;
        },
        check_memory: async () => {
            const resp = await fetch(`${API_BASE}/api/settings/memory`);
            const d = await resp.json();
            return `核心记忆: ${d.core_memories?.length||0}条\n好感度: ${d.affinity?.level_name||'?'}\n心情: ${d.emotion?.mood_score||'?'}分`;
        },
        check_adapters: async () => {
            const resp = await fetch(`${API_BASE}/api/settings/status`);
            const d = await resp.json();
            return `后端: ${d.backend?'✓':'✗'}\nTTS: ${d.tts_local?'✓':'✗'}\nQQ: ${d.qq_connected?'✓':'✗'}\n视觉: ${d.vision_enabled?'✓':'✗'}`;
        },
        system_info: async () => {
            const resp = await fetch(`${API_BASE}/api/settings/status`);
            const d = await resp.json();
            return `端口: ${d.backend_port}\nPID: ${d.backend_pid}\n记忆: ${d.memory_count}条\n对话: ${d.conversation_count}条`;
        },
        reload_tools: () => '工具重载需要重启后端',
        reload_plugins: () => '插件重载需要重启后端',
        clear_cache: () => { localStorage.clear(); return '本地缓存已清除'; },
        export_logs: async () => {
            const resp = await fetch(`${API_BASE}/api/settings/logs`);
            const d = await resp.json();
            const blob = new Blob([d.logs.join('\n')], {type:'text/plain'});
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = `logs_${new Date().toISOString().slice(0,10)}.txt`;
            a.click();
            return `已导出 ${d.logs.length} 行日志`;
        },
    };

    try {
        const fn = actions[action];
        const result = fn ? (typeof fn === 'function' ? await fn() : fn) : '未知操作';
        resultDiv.innerHTML = `<div class="log-line" style="white-space:pre-wrap;">${escapeHtml(result)}</div>`;
    } catch (err) {
        resultDiv.innerHTML = `<div class="log-line" style="color:#e74c3c;">失败: ${err.message}</div>`;
    }
}

async function loadDebugMemory() {
    const type = document.getElementById('debug-mem-type').value;
    const listDiv = document.getElementById('debug-mem-list');
    const statsDiv = document.getElementById('debug-mem-stats');

    try {
        if (type === 'core') {
            const resp = await fetch(`${API_BASE}/api/settings/memory`);
            const data = await resp.json();
            const mems = data.core_memories || [];
            statsDiv.textContent = `${mems.length} 条核心记忆`;
            listDiv.innerHTML = mems.map(m =>
                `<div class="log-line"><b>${m.key}</b>: ${m.value}</div>`
            ).join('') || '<div class="log-line" style="color:#64748b;">无数据</div>';
        } else if (type === 'longterm') {
            const resp = await fetch(`${API_BASE}/api/settings/memory`);
            const data = await resp.json();
            const recent = data.long_term_recent || [];
            statsDiv.textContent = `${data.long_term_stats?.total||0} 条长期记忆`;
            listDiv.innerHTML = recent.map(m =>
                `<div class="log-line">[${m.layer}] ${m.content}</div>`
            ).join('') || '<div class="log-line" style="color:#64748b;">无数据</div>';
        } else if (type === 'conversation') {
            const resp = await fetch(`${API_BASE}/api/settings/status`);
            const data = await resp.json();
            statsDiv.textContent = `${data.conversation_count||0} 条对话记录`;
            listDiv.innerHTML = '<div class="log-line" style="color:#64748b;">使用recall_conversation工具查询对话日志</div>';
        } else if (type === 'knowledge') {
            const resp = await fetch(`${API_BASE}/api/settings/knowledge/stats`);
            const data = await resp.json();
            statsDiv.textContent = `${data.total_entities||0} 实体, ${data.total_relations||0} 关系`;
            const byType = data.by_type || {};
            listDiv.innerHTML = Object.entries(byType).map(([t, c]) =>
                `<div class="log-line">${t}: ${c}个</div>`
            ).join('') + (data.most_mentioned || []).map(m =>
                `<div class="log-line">⭐ ${m.name} (提及${m.count}次, ${m.type})</div>`
            ).join('');
        }
    } catch (err) {
        listDiv.innerHTML = `<div class="log-line" style="color:#e74c3c;">加载失败: ${err.message}</div>`;
    }
}

function filterDebugMemory() {
    const query = document.getElementById('debug-mem-search').value.toLowerCase();
    const lines = document.querySelectorAll('#debug-mem-list .log-line');
    lines.forEach(el => {
        el.style.display = el.textContent.toLowerCase().includes(query) ? '' : 'none';
    });
}

async function loadDebugLogs() {
    try {
        const level = document.getElementById('debug-log-level').value;
        const resp = await fetch(`${API_BASE}/api/settings/logs`);
        const data = await resp.json();
        let logs = data.logs || [];

        // 过滤级别
        if (level !== 'all') {
            logs = logs.filter(l => l.includes(level));
        }

        const viewer = document.getElementById('debug-log-viewer');
        viewer.innerHTML = logs.map(line => {
            let color = '#c8d6e5';
            if (line.includes('ERROR')) color = '#e74c3c';
            else if (line.includes('WARNING')) color = '#f59e0b';
            else if (line.includes('DEBUG')) color = '#64748b';
            else if (line.includes('INFO')) color = '#22c55e';
            return `<div class="log-line" style="color:${color}">${escapeHtml(line)}</div>`;
        }).join('');
        viewer.scrollTop = viewer.scrollHeight;
        document.getElementById('debug-log-count').textContent = logs.length;
    } catch (err) {
        document.getElementById('debug-log-viewer').innerHTML =
            `<div class="log-line" style="color:#e74c3c;">加载失败: ${err.message}</div>`;
    }
}

function filterDebugLogs() {
    const query = document.getElementById('debug-log-search').value.toLowerCase();
    const lines = document.querySelectorAll('#debug-log-viewer .log-line');
    lines.forEach(el => {
        el.style.display = el.textContent.toLowerCase().includes(query) ? '' : 'none';
    });
}

function initDebugPanel() {
    loadDebugTools();
    loadDebugMemory();
    loadDebugLogs();
    // 自动刷新日志
    if (_debugLogInterval) clearInterval(_debugLogInterval);
    _debugLogInterval = setInterval(loadDebugLogs, 10000);
}

// ============================================================
// Plugin Market
// ============================================================

let _marketPlugins = [];
let _marketFilter = '';
let _marketCategory = '';
let _marketTag = '';

function pluginRoleLabel(role) {
    const labels = {
        interceptor: '抢答',
        rewriter: '改写',
        tool_provider: '工具',
        observer: '观察'
    };
    return labels[role] || role;
}

function pluginMetaLine(plugin) {
    const bits = [];
    const roles = Array.isArray(plugin.roles) ? plugin.roles : [];
    const platforms = Array.isArray(plugin.platforms) ? plugin.platforms : [];
    const permissions = Array.isArray(plugin.permissions) ? plugin.permissions : [];
    const services = Array.isArray(plugin.requires_service) ? plugin.requires_service : [];
    if (roles.length) bits.push('类型: ' + roles.map(pluginRoleLabel).join('/'));
    if (platforms.length && !platforms.includes('all')) bits.push('平台: ' + platforms.join('/'));
    if (permissions.length) bits.push('权限: ' + permissions.join('/'));
    if (services.length) bits.push('服务: ' + services.join('/'));
    return bits.join(' · ');
}

async function loadMarketPlugins() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/plugins/market/list`);
        const data = await resp.json();
        _marketPlugins = data.plugins || [];
        document.getElementById('pm-available').textContent = _marketPlugins.length;
        document.getElementById('pm-installed').textContent = _marketPlugins.filter(p => p.installed).length;
        renderMarketPlugins();
    } catch (err) {
        document.getElementById('pm-plugins').innerHTML =
            '<div style="color:#e74c3c;text-align:center;padding:40px;">加载失败：' + err.message + '</div>';
    }
}

function renderMarketPlugins() {
    const container = document.getElementById('pm-plugins');
    let plugins = [..._marketPlugins];

    // 搜索
    const search = (document.getElementById('pm-search')?.value || '').toLowerCase();
    if (search) {
        plugins = plugins.filter(p =>
            (p.name || '').toLowerCase().includes(search) ||
            (p.cn_name || '').toLowerCase().includes(search) ||
            (p.description || '').toLowerCase().includes(search) ||
            (p.author || '').toLowerCase().includes(search)
        );
    }

    // 分类
    const cat = document.getElementById('pm-category')?.value || '';
    if (cat) {
        plugins = plugins.filter(p => (p.category || '') === cat);
    }

    // 标签
    if (_marketTag === 'featured') plugins = plugins.filter(p => p.featured);
    if (_marketTag === 'installed') plugins = plugins.filter(p => p.installed);
    if (_marketTag === 'not-installed') plugins = plugins.filter(p => !p.installed);

    // 排序
    const sort = document.getElementById('pm-sort')?.value || 'default';
    if (sort === 'downloads') plugins.sort((a, b) => (b.downloads || 0) - (a.downloads || 0));
    if (sort === 'rating') plugins.sort((a, b) => (b.rating || 0) - (a.rating || 0));
    if (sort === 'name') plugins.sort((a, b) => (a.name || '').localeCompare(b.name || ''));

    if (!plugins.length) {
        container.innerHTML = '<div style="color:#6b7b8d;text-align:center;padding:40px;">没有找到插件</div>';
        return;
    }

    container.innerHTML = plugins.map(p => {
        const name = p.cn_name || p.name || p.id || '未命名';
        const desc = (p.description || '无描述').substring(0, 60);
        const author = p.author || '匿名';
        const version = p.version || '1.0';
        const downloads = p.downloads || 0;
        const rating = p.rating || 0;
        const metaLine = pluginMetaLine(p);
        const metaHtml = metaLine
            ? `<div style="font-size:10px;color:#38bdf8;margin-bottom:8px;">${escapeHtml(metaLine)}</div>`
            : '';
        const featured = p.featured ? '<span style="background:#f59e0b;color:#000;padding:1px 6px;border-radius:4px;font-size:9px;margin-left:4px;">精选</span>' : '';
        const installed = p.installed;
        const btnColor = installed ? 'background:rgba(34,197,94,0.2);color:#22c55e;border-color:rgba(34,197,94,0.3);' : '';
        const btnText = installed ? '✅ 已安装' : '📥 安装';
        const btnAction = installed
            ? `uninstallPlugin('${p.id}','${name}')`
            : `installPlugin('${p.id}','${name}')`;

        return `<div class="section" style="padding:16px;cursor:default;">
            <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:8px;">
                <div>
                    <span style="font-size:14px;font-weight:600;">${escapeHtml(name)}</span>${featured}
                    <span style="font-size:10px;color:#64748b;margin-left:6px;">v${version}</span>
                </div>
                <span style="font-size:10px;color:#64748b;">${p.category || ''}</span>
            </div>
            <div style="font-size:12px;color:#94a3b8;margin-bottom:10px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">${escapeHtml(desc)}</div>
            ${metaHtml}
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div style="font-size:10px;color:#64748b;">
                    👤 ${escapeHtml(author)}
                    ${downloads ? ' · ⬇️ ' + downloads : ''}
                    ${rating ? ' · ⭐ ' + rating.toFixed(1) : ''}
                </div>
                <button class="btn-small" style="${btnColor}" onclick="${btnAction}">${btnText}</button>
            </div>
        </div>`;
    }).join('');
}

function filterMarketPlugins() { renderMarketPlugins(); }
function sortMarketPlugins() { renderMarketPlugins(); }
function filterByTag(tag) { _marketTag = tag; renderMarketPlugins(); }

async function installPlugin(id, name) {
    showHint(`正在安装 ${name}...`);
    try {
        const resp = await fetch(`${API_BASE}/api/settings/plugins/install-from-market`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ plugin_id: id }),
        });
        const data = await resp.json();
        showHint(data.message || (data.success ? '安装成功' : '安装失败'), !data.success);
        if (data.success) await loadMarketPlugins();
    } catch (err) { showHint('安装失败: ' + err.message, true); }
}

async function uninstallPlugin(id, name) {
    if (!confirm(`确定卸载 "${name}"？`)) return;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/plugins/uninstall`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ plugin_id: id }),
        });
        const data = await resp.json();
        showHint(data.message, !data.success);
        if (data.success) await loadMarketPlugins();
    } catch (err) { showHint('卸载失败', true); }
}

async function syncToGithub() {
    if (!confirm('同步本地插件到GitHub仓库？')) return;
    showHint('同步中...');
    try {
        const resp = await fetch(`${API_BASE}/api/settings/plugins/sync-to-github`, { method: 'POST' });
        const data = await resp.json();
        showHint(data.message);
    } catch (err) { showHint('同步失败', true); }
}

// ================================================================
// 开发者平台
// ================================================================

let _devToken = "";
let _devRole = "";

/**
 * 2026-07-03 面板升级（批6）：登录成功后的公共UI更新（devLogin 与
 * restoreDevSession 共用）。
 */
function _applyDevLoginState(username, role) {
    _devRole = role;
    const statusEl = document.getElementById("dev-login-status");
    if (statusEl) {
        statusEl.innerHTML = `✅ 已登录: <b>${escapeHtml(username)}</b> (${escapeHtml(role)})`;
    }
    const panel = document.getElementById("dev-admin-panel");
    if (panel) panel.style.display = "block";
    if (role === "admin" || role === "super_admin") {
        loadDevelopers();
    }
}

async function devLogin() {
    const username = document.getElementById("dev-username").value.trim();
    const password = document.getElementById("dev-password").value.trim();
    if (!username || !password) { showHint("请输入用户名和密码", true); return; }
    try {
        const resp = await fetch(`${API_BASE}/api/settings/developers/login`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({username, password}),
        });
        const data = await resp.json();
        if (data.success) {
            _devToken = data.token;
            // 2026-07-03 面板升级（批6）：token 存 localStorage，关窗/刷新后由
            // restoreDevSession 静默恢复（原来只存内存变量，登录态半小时命，
            // 见 panel-developer.json"登出/会话保持"审计项）
            try { localStorage.setItem("ws_dev_token", data.token); } catch {}
            _applyDevLoginState(data.username, data.role);
            // 2026-07-03 面板升级（批6）：命中默认密码时后端返回 must_change_password，
            // 弹提示要求尽快改密（默认密码写在源码里等于公开）
            if (data.must_change_password) {
                alert(data.message || "当前仍在使用默认密码，请尽快在 conf.yaml 的 admin.password 中设置新密码并重启");
                showHint("登录成功，但仍在使用默认密码，请尽快修改！", true);
            } else {
                showHint(`登录成功: ${data.username}`);
            }
        } else {
            showHint(data.message, true);
        }
    } catch (e) {
        showHint("登录失败: " + e.message, true);
    }
}

/**
 * 2026-07-03 面板升级（批6）：登出——调已有 POST /developers/logout 真删token，
 * 并清空本地登录态与 localStorage 缓存。
 */
async function devLogout() {
    if (!_devToken) { showHint("当前未登录", true); return; }
    try {
        await fetch(`${API_BASE}/api/settings/developers/logout`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({token: _devToken}),
        });
    } catch (e) {
        // 后端不可达也照常清本地态（token 24小时自然过期）
        console.warn("登出请求失败（本地登录态已清除）:", e);
    }
    _devToken = "";
    _devRole = "";
    try { localStorage.removeItem("ws_dev_token"); } catch {}
    const statusEl = document.getElementById("dev-login-status");
    if (statusEl) statusEl.textContent = "已登出";
    const panel = document.getElementById("dev-admin-panel");
    if (panel) panel.style.display = "none";
    showHint("已登出");
}

/**
 * 2026-07-03 面板升级（批6）：页面加载时用 localStorage 里的 token 调
 * POST /developers/verify 静默恢复登录态；token 无效/过期则清除缓存不打扰用户。
 */
async function restoreDevSession() {
    let saved = "";
    try { saved = localStorage.getItem("ws_dev_token") || ""; } catch {}
    if (!saved) return;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/developers/verify`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({token: saved}),
        });
        const data = await resp.json();
        if (data.success) {
            _devToken = saved;
            _applyDevLoginState(data.username, data.role);
        } else {
            try { localStorage.removeItem("ws_dev_token"); } catch {}
        }
    } catch {
        // 后端未起时静默跳过，保留缓存下次再试
    }
}

async function devRegister() {
    const username = document.getElementById("dev-username").value.trim();
    const password = document.getElementById("dev-password").value.trim();
    if (!username || !password) { showHint("请输入用户名和密码", true); return; }
    try {
        const resp = await fetch(`${API_BASE}/api/settings/developers/register`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({username, password}),
        });
        const data = await resp.json();
        showHint(data.message, !data.success);
    } catch (e) {
        showHint("注册失败: " + e.message, true);
    }
}

async function loadDevelopers() {
    try {
        // 2026-07-03 面板升级（批6）：名单接口已加token鉴权（P1批6改造），带 ?token=
        const resp = await fetch(`${API_BASE}/api/settings/developers/list?token=${encodeURIComponent(_devToken)}`);
        const data = await resp.json();
        const container = document.getElementById("dev-list");
        const statsEl = document.getElementById("dev-stats");
        if (resp.status === 401) {
            container.innerHTML = '<div class="log-line">登录已过期，请重新登录</div>';
            return;
        }

        if (data.stats) {
            statsEl.textContent = `(${data.stats.approved}已审批 / ${data.stats.pending}待审批 / ${data.stats.admins}管理员)`;
        }

        if (!data.developers || data.developers.length === 0) {
            container.innerHTML = '<div class="log-line">暂无开发者</div>';
            return;
        }

        // 2026-07-03 面板升级（批6）：用户名来自无鉴权的注册接口（外部可控），
        // 渲染套 escapeHtml、onclick 参数用 encodeURIComponent 防注入/逃逸
        let html = "";
        for (const dev of data.developers) {
            const roleIcon = dev.role === "super_admin" ? "👑" : dev.role === "admin" ? "🛡️" : "👨‍💻";
            const statusColor = dev.status === "approved" ? "#22c55e" : dev.status === "pending" ? "#f59e0b" : "#ff4444";
            const unameEnc = encodeJsArg(dev.username);
            let actions = "";
            if (_devRole === "super_admin" || _devRole === "admin") {
                if (dev.status === "pending") {
                    actions += ` <span style="color:#22c55e;cursor:pointer;" onclick="devAction('approve',decodeURIComponent('${unameEnc}'))">✅审批</span>`;
                    actions += ` <span style="color:#ff4444;cursor:pointer;" onclick="devAction('reject',decodeURIComponent('${unameEnc}'))">❌拒绝</span>`;
                }
            }
            if (_devRole === "super_admin") {
                if (dev.role === "developer" && dev.status === "approved") {
                    actions += ` <span style="color:#3b82f6;cursor:pointer;" onclick="devAction('set-admin',decodeURIComponent('${unameEnc}'))">⬆️升管理</span>`;
                }
                if (dev.role === "admin") {
                    actions += ` <span style="color:#f59e0b;cursor:pointer;" onclick="devAction('remove-admin',decodeURIComponent('${unameEnc}'))">⬇️降级</span>`;
                }
                if (dev.role !== "super_admin") {
                    actions += ` <span style="color:#ff4444;cursor:pointer;" onclick="devAction('delete',decodeURIComponent('${unameEnc}'))">🗑️</span>`;
                }
            }
            html += `<div class="log-line">${roleIcon} <b>${escapeHtml(dev.username)}</b> <span style="color:${statusColor};">[${escapeHtml(dev.status)}]</span> ${escapeHtml(dev.role)} · ${dev.plugins_count}个插件${actions}</div>`;
        }
        container.innerHTML = html;
    } catch (e) {
        console.error("加载开发者失败:", e);
    }
}

async function devAction(action, username) {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/developers/${action}`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({username, token: _devToken}),
        });
        const data = await resp.json();
        showHint(data.message || (data.success ? "操作成功" : "操作失败"), !data.success);
        loadDevelopers();
    } catch (e) {
        showHint("操作失败: " + e.message, true);
    }
}

// ================================================================
// B站设置
// ================================================================

async function checkBiliLogin() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/bilibili/check-login`);
        const data = await resp.json();
        const el = document.getElementById("bili-login-status");
        if (data.logged_in) {
            el.innerHTML = `✅ 已登录: <b>${data.username}</b> (UID: ${data.uid})`;
            el.style.color = "#22c55e";
        } else {
            el.innerHTML = `❌ ${data.message}`;
            el.style.color = "#ff4444";
        }
    } catch (e) {
        document.getElementById("bili-login-status").innerHTML = "检查失败: " + e.message;
    }
}

async function biliAutoCookie() {
    const el = document.getElementById("bili-auto-result");
    el.innerHTML = "🔄 自动获取中...";

    // 优先用Electron IPC（双模式：Chrome调试端口 → Electron窗口）
    try {
        if (typeof require !== 'undefined') {
            const { ipcRenderer } = require('electron');
            const result = await ipcRenderer.invoke('bili-login');
            if (result.success) {
                // 拿到cookie了，发给后端保存
                el.innerHTML = `✅ 获取成功（${result.method === 'chrome_cdp' ? 'Chrome自动读取' : 'Electron窗口登录'}）`;
                await fetch(`${API_BASE}/api/settings/bilibili/manual-cookie`, {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        sessdata: result.cookies.SESSDATA || "",
                        bili_jct: result.cookies.bili_jct || "",
                        buvid3: result.cookies.buvid3 || "",
                        dedeuserid: result.cookies.DedeUserID || "",
                    }),
                });
                checkBiliLogin();
                return;
            } else {
                el.innerHTML = `⚠️ ${result.message || '获取失败'}，尝试备用方案...`;
            }
        }
    } catch (e) {
        console.log('Electron IPC不可用:', e);
    }

    // 备用：后端直接读浏览器cookie文件
    try {
        const resp = await fetch(`${API_BASE}/api/settings/bilibili/auto-cookie`, {method: "POST"});
        const data = await resp.json();
        el.innerHTML = data.success ? `✅ ${data.message}` : `❌ ${data.message}`;
        if (data.success) checkBiliLogin();
    } catch (e) {
        el.innerHTML = "❌ " + e.message;
    }
}

let _biliQrPollTimer = null;

async function biliQrLogin() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/bilibili/qr-generate`, {method: "POST"});
        const data = await resp.json();
        if (!data.success) { showHint(data.message, true); return; }

        const container = document.getElementById("bili-qr-container");
        container.style.display = "block";

        const img = document.getElementById("bili-qr-image");
        if (data.qr_image) {
            img.src = "data:image/png;base64," + data.qr_image;
        } else {
            // 没有qrcode库，用在线API生成
            img.src = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(data.qr_url)}`;
        }

        document.getElementById("bili-qr-status").innerHTML = "请用B站APP扫描二维码...";

        // 轮询扫码状态
        if (_biliQrPollTimer) clearInterval(_biliQrPollTimer);
        let pollCount = 0;
        _biliQrPollTimer = setInterval(async () => {
            pollCount++;
            if (pollCount > 90) { // 3分钟
                clearInterval(_biliQrPollTimer);
                document.getElementById("bili-qr-status").innerHTML = "⏰ 超时，请重新生成";
                return;
            }
            try {
                const pr = await fetch(`${API_BASE}/api/settings/bilibili/qr-poll`, {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({qr_key: data.qr_key}),
                });
                const pd = await pr.json();
                document.getElementById("bili-qr-status").innerHTML = pd.message;
                if (pd.status === "confirmed" && pd.success) {
                    clearInterval(_biliQrPollTimer);
                    showHint("B站登录成功！");
                    container.style.display = "none";
                    checkBiliLogin();
                } else if (pd.status === "expired") {
                    clearInterval(_biliQrPollTimer);
                }
            } catch (e) {}
        }, 2000);

    } catch (e) {
        showHint("生成二维码失败: " + e.message, true);
    }
}

async function biliManualSave() {
    const sessdata = document.getElementById("bili-sessdata").value.trim();
    const jct = document.getElementById("bili-jct").value.trim();
    if (!sessdata) { showHint("请填写SESSDATA", true); return; }
    try {
        const resp = await fetch(`${API_BASE}/api/settings/bilibili/manual-cookie`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({sessdata, bili_jct: jct}),
        });
        const data = await resp.json();
        showHint(data.message, !data.success);
        if (data.success) checkBiliLogin();
    } catch (e) {
        showHint("保存失败: " + e.message, true);
    }
}

async function loadBiliStats() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/bilibili/status`);
        const data = await resp.json();
        const el = document.getElementById("bili-learning-stats");
        const l = data.learning || {};
        el.innerHTML = `
            <div class="log-line">Cookie已配置: ${data.cookie_configured ? "✅" : "❌"}</div>
            <div class="log-line">点赞记录: ${l.likes || 0}条</div>
            <div class="log-line">评论记录: ${l.comments || 0}条</div>
            <div class="log-line">UP主追踪: ${l.ups_tracked || 0}个</div>
            <div class="log-line">视频缓存: ${l.videos_cached || 0}条</div>
        `;
    } catch (e) {}
}

// 标签页切换加载
document.addEventListener("DOMContentLoaded", function() {
    const origHandler = document.querySelector(".nav-btn[data-tab='bilibili']");
    if (origHandler) {
        origHandler.addEventListener("click", function() {
            checkBiliLogin();
            loadBiliStats();
        });
    }
});

function openExternal(url) {
    try {
        require('electron').shell.openExternal(url);
    } catch {
        window.open(url, '_blank');
    }
}

// ============================================================
// Initialize
// ============================================================

window.addEventListener('DOMContentLoaded', async () => {
    // 并行加载所有数据（比串行快3-5倍）
    try {
        const [settingsOk] = await Promise.allSettled([
            loadSettings().then(() => loadProviders()),
            loadPrompt(),
            checkStatus(),
            loadLogs(),
            loadLaunchLogs(),
            loadMemory(),
            loadTemplates(),
        ]);
    } catch (err) {
        console.error('[Settings] Init error:', err);
    }
    initExpressionMapping();
    // 2026-07-03 面板升级（批6）：静默恢复开发者登录态（localStorage token +
    // POST /developers/verify）；绑定语音页/声音克隆页参考音频输入框双向镜像
    restoreDevSession();
    bindRefAudioMirrors();

    // 定时刷新（状态15s，日志30s——不需要太频繁）
    setInterval(checkStatus, 15000);
    setInterval(loadLogs, 30000);
    setInterval(loadMemory, 60000);
});

// ================================================================
// 人设分区编辑器
// ================================================================

const SECTION_LABELS = {
    format_rules: "输出格式规则",
    basic_info: "基本资料",
    appearance: "外貌特征",
    living: "居住环境",
    personality: "性格特点",
    hobbies: "兴趣爱好",
    self_story: "白的自述（经历故事）",
    character_detail: "角色设定详解（说话风格/互动规则）",
    absolute_rules: "绝对执行规则",
    autonomy_rules: "自主意识与家人互动",
    search_rules: "主动搜索规则",
    memory_rules: "禁止编造记忆",
    image_rules: "表情包/图片反应",
    attack_rules: "遭受攻击时的反击",
};

async function loadPromptSections() {
    const container = document.getElementById("prompt-sections-container");
    try {
        if (container) {
            container.innerHTML = '<div class="log-line">正在加载人设分区...</div>';
        }
        const resp = await fetch(`${API_BASE}/api/settings/prompt/sections`);
        let data = {};
        try {
            data = await resp.json();
        } catch (jsonErr) {
            data = {};
        }
        if (!resp.ok) {
            const msg = data.detail || data.message || `HTTP ${resp.status}`;
            container.innerHTML = `<div class="log-line">加载人设分区失败：${escapeHtml(msg)}</div>`;
            return;
        }
        if (!data.sections || Object.keys(data.sections).length === 0) {
            const msg = data.message || "未识别到可分区编辑的人设标题，可使用下方完整编辑。";
            container.innerHTML = `<div class="log-line">${escapeHtml(msg)}</div>`;
            return;
        }
        let html = "";
        for (const [key, content] of Object.entries(data.sections)) {
            const label = SECTION_LABELS[key] || key;
            const preview = content.substring(0, 80).replace(/\n/g, " ") + "...";
            html += `
                <details style="margin-bottom:8px;border:1px solid #1a2332;border-radius:6px;padding:8px;">
                    <summary style="cursor:pointer;color:#00d4ff;font-weight:bold;">${label}</summary>
                    <textarea id="section-${key}" rows="10" style="width:100%;margin-top:8px;background:#0a0e14;color:#e0e0e0;border:1px solid #1a2332;border-radius:4px;padding:8px;font-size:12px;font-family:monospace;">${escapeHtml(content)}</textarea>
                    <button class="btn-primary" onclick="saveSection('${key}')" style="margin-top:6px;">保存「${label}」</button>
                </details>
            `;
        }
        container.innerHTML = html;
    } catch (e) {
        console.error("加载人设分区失败:", e);
        if (container) {
            container.innerHTML = `<div class="log-line">加载人设分区失败：${escapeHtml(e.message || String(e))}</div>`;
        }
    }
}

async function saveSection(sectionName) {
    const textarea = document.getElementById(`section-${sectionName}`);
    if (!textarea) return;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/prompt/sections/${sectionName}`, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({content: textarea.value}),
        });
        const data = await resp.json();
        if (resp.ok && data.status === "ok") {
            // 2026-07-03 面板升级（批6）：按后端响应展示"已热更新/重启生效"
            // （依据 panel-persona.json"分区编辑器"审计项：原提示未说明需重启）
            showHint(`「${SECTION_LABELS[sectionName] || sectionName}」${data.message || "已保存"}`);
            // 同步更新完整编辑框
            loadPrompt();
        } else {
            showHint("保存失败: " + (data.detail || data.message || "未知错误"), true);
        }
    } catch (e) {
        showHint("保存失败: " + e.message, true);
    }
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// ================================================================
// 模块管理
// ================================================================

async function loadModules() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/modules`);
        const data = await resp.json();
        const container = document.getElementById("modules-list");
        if (!data.modules || data.modules.length === 0) {
            container.innerHTML = '<div class="log-line">没有加载任何模块</div>';
            return;
        }
        // 2026-07-03 面板升级（批6）：每行加启/禁开关（调批6新端点 POST /modules/toggle，
        // 重启后生效）+ title 悬浮显示模块 docstring 描述
        // （依据 panel-modules.json"模块开关"/"模块详细信息"审计项）
        let html = `<div class="log-line" style="color:#00d4ff;">共 ${data.total} 个模块（开关改动重启后端后生效）</div>`;
        for (const m of data.modules) {
            const status = m.enabled ? "✅ 运行中" : "❌ 已关闭";
            const stemEnc = encodeJsArg(m.stem || "");
            const toggle = m.stem !== undefined
                ? `<label style="cursor:pointer;margin-right:6px;" title="启用/禁用该模块（重启后生效）">
                       <input type="checkbox" ${m.enabled ? "checked" : ""}
                              onchange="toggleModule(decodeURIComponent('${stemEnc}'), this.checked)">
                   </label>`
                : "";
            html += `<div class="log-line" title="${escapeHtml(m.description || "暂无描述")}">${toggle}${status} <b>${escapeHtml(m.name)}</b> <span style="color:#6b7b8d;">(${escapeHtml(m.file)})</span>${m.description ? ` <span style="color:#4a5568;">— ${escapeHtml(m.description)}</span>` : ""}</div>`;
        }
        container.innerHTML = html;
    } catch (e) {
        console.error("加载模块失败:", e);
    }
}

/**
 * 2026-07-03 面板升级（批6）：启用/禁用记忆模块。
 * 调 POST /modules/toggle {stem, enabled}，写 config/memory_settings.json 的
 * modules.disabled；运行时只在启动时扫描加载，改动需重启后端生效。
 */
async function toggleModule(stem, enabled) {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/modules/toggle`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ stem, enabled }),
        });
        const data = await resp.json();
        if (resp.ok && data.status === "ok") {
            showHint(data.message || `模块 ${stem} 已${enabled ? "启用" : "禁用"}，重启后端后生效`);
        } else {
            showHint("切换失败: " + (data.detail || "未知错误"), true);
        }
    } catch (e) {
        showHint("切换失败: " + e.message, true);
    }
    // 无论成败都重新拉一次列表，让复选框回到真实落盘状态
    loadModules();
}

// ================================================================
// 用户管理
// ================================================================

async function loadAllAffinity() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/users/affinity/all`);
        const data = await resp.json();
        const container = document.getElementById("affinity-all-list");
        if (!data.users || data.users.length === 0) {
            container.innerHTML = '<div class="log-line">暂无用户数据</div>';
            return;
        }
        // 2026-07-03 面板升级（批6）：每行加"改分/设家人"按钮，调批6新端点
        // POST /users/affinity/{user_id}/set（批5共享实例，改分即时生效，
        // 见 panel-users.json"单个用户好感度修改"审计项）；onclick 参数
        // encodeURIComponent 防逃逸
        let html = "";
        for (const u of data.users) {
            const icon = u.is_family ? "❤️" : u.points >= 80 ? "😊" : u.points >= 0 ? "👤" : "😠";
            const uidEnc = encodeJsArg(u.user_id);
            html += `<div class="log-line">${icon} <b>${escapeHtml(String(u.user_id))}</b> — ${u.points}分 ${u.is_family ? "(家人)" : ""} 连续${u.consecutive_days}天
                <span style="color:#3b82f6;cursor:pointer;margin-left:6px;" onclick="editUserAffinity(decodeURIComponent('${uidEnc}'), ${u.points})">[改分]</span>
                <span style="color:${u.is_family ? "#f59e0b" : "#22c55e"};cursor:pointer;margin-left:4px;" onclick="toggleUserFamily(decodeURIComponent('${uidEnc}'), ${u.is_family ? "false" : "true"})">[${u.is_family ? "取消家人" : "设家人"}]</span>
            </div>`;
        }
        container.innerHTML = html;
    } catch (e) {
        console.error("加载好感度失败:", e);
    }
}

/**
 * 2026-07-03 面板升级（批6）：修改指定QQ用户的好感度积分。
 * 调 POST /users/affinity/{user_id}/set {points}（批6新端点，即时生效）。
 */
async function editUserAffinity(userId, currentPoints) {
    const input = prompt(`修改用户 ${userId} 的好感度积分（当前 ${currentPoints} 分）：`, String(currentPoints));
    if (input === null) return;
    const pts = parseFloat(input);
    if (isNaN(pts)) { showHint("请输入数字", true); return; }
    try {
        const resp = await fetch(`${API_BASE}/api/settings/users/affinity/${encodeURIComponent(userId)}/set`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ points: pts }),
        });
        const data = await resp.json();
        if (resp.ok && data.status === "ok") {
            showHint(`用户 ${userId} 好感度已设为 ${data.points}（${data.level_name || ""}）`);
        } else {
            showHint("修改失败: " + (data.detail || "未知错误"), true);
        }
    } catch (e) {
        showHint("修改失败: " + e.message, true);
    }
    loadAllAffinity();
}

/**
 * 2026-07-03 面板升级（批6）：设置/取消指定QQ用户的家人标记。
 * 调 POST /users/affinity/{user_id}/set {is_family}（批6新端点，即时生效）。
 */
async function toggleUserFamily(userId, isFamily) {
    if (!confirm(`确定把用户 ${userId} ${isFamily ? "设为家人" : "取消家人"}？`)) return;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/users/affinity/${encodeURIComponent(userId)}/set`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ is_family: isFamily }),
        });
        const data = await resp.json();
        if (resp.ok && data.status === "ok") {
            showHint(`用户 ${userId} 已${isFamily ? "设为家人" : "取消家人"}`);
        } else {
            showHint("设置失败: " + (data.detail || "未知错误"), true);
        }
    } catch (e) {
        showHint("设置失败: " + e.message, true);
    }
    loadAllAffinity();
}

/**
 * 2026-07-03 面板升级（批6）：用户过滤状态区渲染——原 filter-status 永远停在
 * "加载中..."（无任何代码调 GET /users/filter，见 panel-users.json"用户过滤状态区"
 * 审计项）。现渲染模式+黑名单计数+名单明细，每行配"解除"按钮调已有 DELETE 端点。
 */
async function loadFilterStatus() {
    const container = document.getElementById("filter-status");
    if (!container) return;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/users/filter`);
        const data = await resp.json();
        if (data.status !== "ok" || !data.stats) {
            container.innerHTML = `<span style="color:#e74c3c;">过滤状态加载失败: ${escapeHtml(data.error || "未知错误")}</span>`;
            return;
        }
        const s = data.stats;
        const modeNames = { blacklist: "黑名单模式", whitelist: "白名单模式", off: "关闭过滤" };
        const modeSelect = document.getElementById("filter-mode-select");
        if (modeSelect) modeSelect.value = s.mode || "blacklist";
        let html = `<div style="margin-bottom:6px;">模式: <b>${escapeHtml(modeNames[s.mode] || String(s.mode))}</b>
            · 白名单 <b>${s.whitelist_count || 0}</b> 人
            · 永久拉黑 <b>${s.hard_blacklist || 0}</b> 人 · 限时拉黑 <b>${s.soft_blacklist || 0}</b> 人
            · 已验证 <b>${s.verified || 0}</b> 人
            ${data.runtime ? '<span style="color:#22c55e;">（运行实例，即时生效）</span>' : '<span style="color:#f59e0b;">（文件实例，重启后生效）</span>'}</div>`;
        const whitelist = data.whitelist || [];
        if (whitelist.length === 0) {
            html += '<div style="color:#6b7b8d;margin-bottom:6px;">白名单为空</div>';
        } else {
            html += '<div style="margin-bottom:4px;color:#4a5568;">白名单：</div>';
            for (const uid of whitelist) {
                const uidEnc = encodeJsArg(uid);
                html += `<div class="log-line">✅ <b>${escapeHtml(String(uid))}</b>
                    <span style="color:#22c55e;cursor:pointer;margin-left:6px;" onclick="removeWhitelist(decodeURIComponent('${uidEnc}'))">[移除]</span>
                </div>`;
            }
        }
        const blacklist = data.blacklist || [];
        if (blacklist.length === 0) {
            html += '<div style="color:#6b7b8d;">黑名单为空</div>';
        } else {
            html += '<div style="margin:6px 0 4px;color:#4a5568;">黑名单：</div>';
            for (const b of blacklist) {
                const uidEnc = encodeJsArg(b.user_id);
                const typeLabel = b.type === "hard" ? "永久" : "限时";
                html += `<div class="log-line">🚫 <b>${escapeHtml(String(b.user_id))}</b>
                    ${b.nickname ? escapeHtml(String(b.nickname)) : ""}
                    <span style="color:#6b7b8d;">[${typeLabel}] ${escapeHtml(String(b.reason || ""))}</span>
                    <span style="color:#22c55e;cursor:pointer;margin-left:6px;" onclick="removeBlacklist(decodeURIComponent('${uidEnc}'))">[解除]</span>
                </div>`;
            }
        }
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = '<span style="color:#e74c3c;">无法连接后端</span>';
    }
}

async function setUserFilterMode() {
    const mode = document.getElementById("filter-mode-select")?.value || "blacklist";
    try {
        const resp = await fetch(`${API_BASE}/api/settings/users/filter/mode`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({mode}),
        });
        const data = await resp.json();
        if (resp.ok && data.status === "ok") {
            showHint(data.message || "过滤模式已切换");
        } else {
            showHint("切换失败: " + (data.detail || data.error || "未知错误"), true);
        }
    } catch (e) {
        showHint("切换失败: " + e.message, true);
    }
    loadFilterStatus();
}

/**
 * 2026-07-03 面板升级（批6）：解除拉黑，调已有 DELETE /users/filter/blacklist/{user_id}。
 */
async function removeBlacklist(userId) {
    if (!confirm(`确定解除对 ${userId} 的拉黑？`)) return;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/users/filter/blacklist/${encodeURIComponent(userId)}`, {
            method: "DELETE",
        });
        const data = await resp.json();
        if (resp.ok && data.status === "ok") {
            showHint(data.removed ? `已解除拉黑 ${userId}` : `${userId} 不在黑名单中`);
        } else {
            showHint("解除失败: " + (data.detail || "未知错误"), true);
        }
    } catch (e) {
        showHint("解除失败: " + e.message, true);
    }
    loadFilterStatus();
}

async function addBlacklist() {
    const userId = document.getElementById("blacklist-user-id").value.trim();
    const reason = document.getElementById("blacklist-reason").value.trim();
    const permanent = document.getElementById("blacklist-permanent")?.checked !== false;
    if (!userId) { showHint("请输入QQ号", true); return; }
    try {
        const resp = await fetch(`${API_BASE}/api/settings/users/filter/blacklist`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                user_id: userId,
                reason: reason || "手动拉黑",
                permanent,
            }),
        });
        const data = await resp.json();
        if (data.status === "ok") {
            // 2026-07-03 面板升级（批6）：显示后端的生效方式说明，并刷新过滤状态区
            showHint(data.message || `已拉黑 ${userId}`);
            document.getElementById("blacklist-user-id").value = "";
            document.getElementById("blacklist-reason").value = "";
            loadFilterStatus();
        }
    } catch (e) {
        showHint("拉黑失败: " + e.message, true);
    }
}

async function addWhitelist() {
    const userId = document.getElementById("whitelist-user-id").value.trim();
    if (!userId) { showHint("请输入白名单QQ号", true); return; }
    try {
        const resp = await fetch(`${API_BASE}/api/settings/users/filter/whitelist`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({user_id: userId}),
        });
        const data = await resp.json();
        if (resp.ok && data.status === "ok") {
            showHint(data.message || `已加入白名单 ${userId}`);
            document.getElementById("whitelist-user-id").value = "";
            loadFilterStatus();
        } else {
            showHint("加入白名单失败: " + (data.detail || data.error || "未知错误"), true);
        }
    } catch (e) {
        showHint("加入白名单失败: " + e.message, true);
    }
}

async function removeWhitelist(userId) {
    if (!confirm(`确定从白名单移除 ${userId}？`)) return;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/users/filter/whitelist/${encodeURIComponent(userId)}`, {
            method: "DELETE",
        });
        const data = await resp.json();
        if (resp.ok && data.status === "ok") {
            showHint(data.removed ? `已移除白名单 ${userId}` : `${userId} 不在白名单中`);
        } else {
            showHint("移除白名单失败: " + (data.detail || data.error || "未知错误"), true);
        }
    } catch (e) {
        showHint("移除白名单失败: " + e.message, true);
    }
    loadFilterStatus();
}

async function loadSecrets() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/users/secrets`);
        const data = await resp.json();
        const container = document.getElementById("secrets-list");
        if (!data.secrets || data.secrets.length === 0) {
            container.innerHTML = '<div class="log-line" style="color:#6b7b8d;">暂无秘密</div>';
            return;
        }
        // 2026-07-03 面板升级（批6）：秘密内容/讲述人来自用户对话（外部可控），
        // 全部套 escapeHtml 防HTML注入（设置窗口 nodeIntegration:true，注入可执行
        // 任意代码，见 panel-users.json"秘密列表"审计项）；onclick 的 id 用
        // encodeURIComponent 防单引号逃逸
        let html = "";
        for (const s of data.secrets) {
            html += `<div class="log-line">🤫 ${escapeHtml(s.told_by || "用户")}: ${escapeHtml(s.content || "")} <span style="color:#ff4444;cursor:pointer;" onclick="deleteSecret(decodeURIComponent('${encodeJsArg(s.id)}'))">[删除]</span></div>`;
        }
        container.innerHTML = html;
    } catch (e) {
        console.error("加载秘密失败:", e);
    }
}

async function deleteSecret(secretId) {
    try {
        // 2026-07-03 面板升级（批6）：路径段 encodeURIComponent，防特殊字符破坏URL
        await fetch(`${API_BASE}/api/settings/users/secrets/${encodeURIComponent(secretId)}`, {method: "DELETE"});
        showHint("已删除");
        loadSecrets();
    } catch (e) {
        showHint("删除失败", true);
    }
}

async function loadImpressions() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/users/impressions`);
        const data = await resp.json();
        const container = document.getElementById("impressions-list");
        if (!data.impressions || Object.keys(data.impressions).length === 0) {
            container.innerHTML = '<div class="log-line" style="color:#6b7b8d;">暂无印象数据</div>';
            return;
        }
        // 2026-07-03 面板升级（批6）：imp.name 来源是QQ昵称（任意外部用户可控），
        // 套 escapeHtml 防HTML注入（见 panel-users.json"对每个人的印象"审计项）
        let html = "";
        for (const [uid, imp] of Object.entries(data.impressions)) {
            const warmthIcon = imp.warmth > 2 ? "😊" : imp.warmth < -2 ? "😠" : "😐";
            html += `<div class="log-line">${warmthIcon} <b>${escapeHtml(imp.name || uid)}</b> — 温暖:${imp.warmth} 信任:${imp.trust} 有趣:${imp.fun} (互动${imp.interactions}次)</div>`;
        }
        container.innerHTML = html;
    } catch (e) {
        console.error("加载印象失败:", e);
    }
}

// ================================================================
// 表情包管理
// ================================================================

async function loadStickers() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/stickers`);
        const data = await resp.json();
        const grid = document.getElementById("sticker-grid");
        const countEl = document.getElementById("sticker-count");

        if (!data.stickers || data.stickers.length === 0) {
            grid.innerHTML = '<div class="log-line" style="color:#6b7b8d;">暂无表情包，请上传</div>';
            countEl.textContent = "(0)";
            return;
        }

        countEl.textContent = `(${data.total})`;
        let html = "";
        // 2026-07-02 审计修复（批2）：预览图加 API_BASE 前缀——设置页以 file:// 协议加载，
        // 根相对路径 /sticker/ 会解析成 file:///sticker/... 导致缩略图全部裂图
        for (const s of data.stickers) {
            html += `
                <div style="position:relative;border:1px solid #1a2332;border-radius:6px;overflow:hidden;background:#0a0e14;cursor:pointer;" title="${s.desc || s.path}" onclick="confirmDeleteSticker('${s.id}','${s.path}')">
                    <img src="${API_BASE}/sticker/${s.path}" style="width:100%;height:80px;object-fit:cover;" onerror="this.style.display='none'">
                    <div style="font-size:9px;color:#6b7b8d;padding:2px 4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${s.desc || s.path.substring(0,8)}</div>
                </div>
            `;
        }
        grid.innerHTML = html;
    } catch (e) {
        console.error("加载表情包失败:", e);
    }
}

async function uploadSticker() {
    const fileInput = document.getElementById("sticker-upload-file");
    const descInput = document.getElementById("sticker-upload-desc");
    if (!fileInput.files || fileInput.files.length === 0) {
        showHint("请选择图片文件", true);
        return;
    }
    // 2026-07-02 审计修复（批2）：后端 upload_sticker 只接收 JSON base64（{data,desc,filename}），
    // 原来发 multipart FormData 必然 422（且 python-multipart 未安装）；
    // 改为 FileReader 读取 base64 后 POST JSON
    const file = fileInput.files[0];
    let base64Data;
    try {
        base64Data = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                // reader.result 形如 "data:image/png;base64,xxxx"，只取逗号后的纯 base64 部分
                const result = String(reader.result || '');
                const idx = result.indexOf(',');
                resolve(idx >= 0 ? result.slice(idx + 1) : result);
            };
            reader.onerror = () => reject(reader.error || new Error('读取文件失败'));
            reader.readAsDataURL(file);
        });
    } catch (e) {
        showHint("读取文件失败: " + e.message, true);
        return;
    }
    try {
        const resp = await fetch(`${API_BASE}/api/settings/stickers/upload`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                data: base64Data,
                desc: descInput.value || "",
                filename: file.name || "sticker.jpg",
            }),
        });
        const data = await resp.json();
        if (data.status === "ok") {
            showHint(`上传成功 (ID: ${data.id})`);
            fileInput.value = "";
            descInput.value = "";
            loadStickers();
        } else {
            // 2026-07-02 审计修复（批2）：后端错误格式是 {status:"error",message:...}，兼容读取
            showHint("上传失败: " + (data.message || data.detail || "未知错误"), true);
        }
    } catch (e) {
        showHint("上传失败: " + e.message, true);
    }
}

function confirmDeleteSticker(id, path) {
    if (confirm(`删除表情包 ${path.substring(0,12)}...？`)) {
        deleteSticker(id);
    }
}

async function deleteSticker(id) {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/stickers/${id}`, {method: "DELETE"});
        const data = await resp.json();
        if (data.status === "ok") {
            showHint("已删除");
            loadStickers();
        }
    } catch (e) {
        showHint("删除失败", true);
    }
}

// ================================================================
// 对话提示模板编辑
// ================================================================

async function loadPromptTemplates() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/prompt-templates`);
        const data = await resp.json();

        // 语气模式
        const persona = data.affinity_persona || {};
        const fields = ["family", "intimate", "friendly", "polite", "cold"];
        for (const f of fields) {
            const el = document.getElementById(`tpl-persona-${f}`);
            if (el) el.value = persona[f] || "";
        }

        // 禁用词
        const filter = data.human_like_filter || {};
        const bannedWords = document.getElementById("tpl-banned-words");
        if (bannedWords) bannedWords.value = (filter.banned_words || []).join("\n");
        const bannedLecture = document.getElementById("tpl-banned-lecture");
        if (bannedLecture) bannedLecture.value = (filter.banned_lecture || []).join("\n");
    } catch (e) {
        console.error("加载提示模板失败:", e);
    }
}

async function savePromptTemplateSection(section) {
    try {
        let content;
        if (section === "affinity_persona") {
            content = {};
            for (const f of ["family", "intimate", "friendly", "polite", "cold"]) {
                const el = document.getElementById(`tpl-persona-${f}`);
                if (el) content[f] = el.value;
            }
        } else if (section === "human_like_filter") {
            const bannedWords = document.getElementById("tpl-banned-words");
            const bannedLecture = document.getElementById("tpl-banned-lecture");
            content = {
                banned_words: (bannedWords.value || "").split("\n").filter(x => x.trim()),
                banned_lecture: (bannedLecture.value || "").split("\n").filter(x => x.trim()),
            };
        }

        const resp = await fetch(`${API_BASE}/api/settings/prompt-templates/${section}`, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({content: content}),
        });
        const data = await resp.json();
        if (data.status === "ok") {
            showHint("提示模板已保存（下次对话生效）");
        }
    } catch (e) {
        showHint("保存失败: " + e.message, true);
    }
}

// ================================================================
// AI生成（ComfyUI）
// ================================================================

async function checkComfyUIStatus() {
    const el = document.getElementById("comfyui-status");
    const modelsEl = document.getElementById("comfyui-models");
    el.innerHTML = "检测中...";
    try {
        const resp = await fetch(`${API_BASE}/api/settings/comfyui/status`);
        const data = await resp.json();
        if (data.online) {
            el.innerHTML = '<span style="color:#22c55e;">✅ ComfyUI 在线</span>';
            if (data.models && data.models.length > 0) {
                modelsEl.innerHTML = "可用模型: " + data.models.map(m =>
                    '<span style="color:#94a3b8;">' + m + '</span>'
                ).join(", ");
            }
        } else {
            el.innerHTML = '<span style="color:#ff4444;">❌ ComfyUI 离线</span>';
            modelsEl.innerHTML = "请启动ComfyUI或双击 run_nvidia_gpu.bat";
        }
    } catch (e) {
        el.innerHTML = '<span style="color:#ff4444;">检测失败</span>';
    }
}

async function startComfyUI() {
    const el = document.getElementById("comfyui-status");
    el.innerHTML = '<span style="color:#f59e0b;">🔄 正在启动ComfyUI（加载模型中，请等待...）</span>';
    try {
        const resp = await fetch(`${API_BASE}/api/settings/comfyui/start`, { method: "POST" });
        const data = await resp.json();
        if (data.success) {
            el.innerHTML = '<span style="color:#22c55e;">✅ ' + data.message + '</span>';
            checkComfyUIStatus();
            showHint("ComfyUI启动成功");
        } else {
            el.innerHTML = '<span style="color:#ff4444;">❌ ' + data.message + '</span>';
            showHint(data.message, true);
        }
    } catch (e) {
        el.innerHTML = '<span style="color:#ff4444;">启动失败</span>';
        showHint("启动失败: " + e.message, true);
    }
}

async function loadAIGenConfig() {
    try {
        // 先加载配置
        const resp = await fetch(`${API_BASE}/api/settings/comfyui/config`);
        const data = await resp.json();
        const qualityEl = document.getElementById("aigen-quality");
        const sizeEl = document.getElementById("aigen-size");
        if (qualityEl) qualityEl.value = data.default_quality || "hires";
        if (sizeEl) sizeEl.value = data.default_size || "1024x1024";

        // 从ComfyUI动态获取模型列表
        const statusResp = await fetch(`${API_BASE}/api/settings/comfyui/status`);
        const statusData = await statusResp.json();
        const models = statusData.models || [];

        const modelEl = document.getElementById("aigen-model");
        const portraitEl = document.getElementById("aigen-portrait-model");

        if (models.length > 0) {
            // 用ComfyUI实际可用模型填充下拉列表
            const opts = models.filter(m => !m.includes('svd')).map(m =>
                `<option value="${m}">${m.replace('.safetensors','')}</option>`
            ).join('');
            if (modelEl) modelEl.innerHTML = opts;
            if (portraitEl) portraitEl.innerHTML = opts;
            // 测试区模型选择
            const testModelEl = document.getElementById("aigen-test-model");
            if (testModelEl) {
                testModelEl.innerHTML = '<option value="">使用默认模型</option>' + opts;
            }
        }

        // 设置当前选中值
        const comfyuiCfg = (data.providers && data.providers.comfyui) || {};
        if (modelEl) modelEl.value = comfyuiCfg.model || "";
        if (portraitEl) portraitEl.value = comfyuiCfg.self_portrait_model || "";

        // 白的外观描述
        const setTextarea = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ""; };
        setTextarea("aigen-appearance", data.base_appearance);
        setTextarea("aigen-outfit", data.outfit_default);
        setTextarea("aigen-quality-tags", data.quality_tags);
        setTextarea("aigen-negative", data.negative_prompt);
        const triggerEl = document.getElementById("aigen-trigger-keywords");
        if (triggerEl) triggerEl.value = (data.trigger_keywords || []).join(",");
    } catch (e) {
        console.error("加载AI生成配置失败:", e);
    }
}

async function saveAIGenConfig() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/comfyui/config`);
        const config = await resp.json();

        config.providers = config.providers || {};
        config.providers.comfyui = config.providers.comfyui || {};
        config.providers.comfyui.model = document.getElementById("aigen-model").value;
        config.providers.comfyui.self_portrait_model = document.getElementById("aigen-portrait-model").value;
        config.default_quality = document.getElementById("aigen-quality").value;
        config.default_size = document.getElementById("aigen-size").value;

        // 白的外观描述
        const getTextarea = (id) => { const el = document.getElementById(id); return el ? el.value : ""; };
        config.base_appearance = getTextarea("aigen-appearance");
        config.outfit_default = getTextarea("aigen-outfit");
        config.quality_tags = getTextarea("aigen-quality-tags");
        config.negative_prompt = getTextarea("aigen-negative");
        const kwStr = (document.getElementById("aigen-trigger-keywords") || {}).value || "";
        config.trigger_keywords = kwStr.split(",").map(s => s.trim()).filter(Boolean);

        const saveResp = await fetch(`${API_BASE}/api/settings/comfyui/config`, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(config),
        });
        const result = await saveResp.json();
        showHint(result.message || "配置已保存");
    } catch (e) {
        showHint("保存失败: " + e.message, true);
    }
}

async function testGenerate() {
    const prompt = document.getElementById("aigen-test-prompt").value.trim()
        || "1girl, silver hair, blue eyes, smile, upper body, simple background, high quality, masterpiece";
    const statusEl = document.getElementById("aigen-test-status");
    const resultEl = document.getElementById("aigen-test-result");
    statusEl.textContent = "生成中，请稍候...";
    resultEl.innerHTML = "";
    try {
        const provider = document.getElementById("aigen-provider").value;
        const quality = document.getElementById("aigen-quality").value;
        const testModel = (document.getElementById("aigen-test-model") || {}).value || "";
        const resp = await fetch(`${API_BASE}/api/settings/comfyui/test`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ prompt, quality, provider, model: testModel }),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.textContent = "生成成功！";
            if (data.path) {
                resultEl.innerHTML = '<div style="color:#22c55e;margin-bottom:8px;">保存位置: ' + data.path + '</div>';
            }
            showHint("图片生成成功");
        } else {
            statusEl.textContent = "生成失败";
            resultEl.innerHTML = '<div style="color:#ff4444;">' + (data.message || '未知错误') + '</div>';
        }
    } catch (e) {
        statusEl.textContent = "请求失败";
        resultEl.innerHTML = '<div style="color:#ff4444;">' + e.message + '</div>';
    }
}

async function testImg2Img() {
    const fileInput = document.getElementById("img2img-file");
    const prompt = document.getElementById("img2img-prompt").value.trim();
    const denoise = document.getElementById("img2img-strength").value;
    const statusEl = document.getElementById("img2img-status");
    const resultEl = document.getElementById("img2img-result");

    if (!fileInput.files.length || !prompt) {
        showHint("请选择图片并输入修改描述");
        return;
    }

    statusEl.textContent = "修改中，请稍候...";
    resultEl.innerHTML = "";

    try {
        // 先上传图片到服务器
        const formData = new FormData();
        formData.append("file", fileInput.files[0]);
        const uploadResp = await fetch(`${API_BASE}/api/settings/upload-temp`, {
            method: "POST",
            body: formData,
        });
        let imagePath = "";
        let uploadErrMsg = "";
        if (uploadResp.ok) {
            const uploadData = await uploadResp.json();
            imagePath = uploadData.path || "";
            uploadErrMsg = uploadData.message || "";
        }
        if (!imagePath) {
            // 2026-07-03 审计修复（批5）：上传失败时直接报错终止。
            // 原逻辑回退用裸文件名当 image_path 传给后端——后端按服务端 cwd 解析
            // 该路径必不存在，图生图必失败且报错信息误导（见 settings-align.json）
            statusEl.textContent = "上传失败";
            resultEl.innerHTML = '<div style="color:#ff4444;">图片上传到后端失败'
                + (uploadErrMsg ? ("：" + uploadErrMsg) : "")
                + '（请确认后端在运行且已安装 python-multipart）</div>';
            showHint("图片上传失败，已取消图生图");
            return;
        }

        const resp = await fetch(`${API_BASE}/api/settings/comfyui/img2img`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ image_path: imagePath, prompt, denoise: parseFloat(denoise) }),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.textContent = "修改成功！";
            if (data.path) {
                resultEl.innerHTML = '<div style="color:#22c55e;margin-bottom:8px;">保存位置: ' + data.path + '</div>';
            }
            showHint("图片修改成功");
        } else {
            statusEl.textContent = "修改失败";
            resultEl.innerHTML = '<div style="color:#ff4444;">' + (data.message || '未知错误') + '</div>';
        }
    } catch (e) {
        statusEl.textContent = "请求失败";
        resultEl.innerHTML = '<div style="color:#ff4444;">' + e.message + '</div>';
    }
}

async function testVideoGenerate() {
    const prompt = document.getElementById("vgen-prompt").value.trim()
        || "1girl, silver white long hair, bright eyes, smile, gentle wind, anime style, masterpiece";
    const mode = document.getElementById("vgen-mode").value;
    const size = document.getElementById("vgen-size").value;
    const statusEl = document.getElementById("vgen-status");
    const resultEl = document.getElementById("vgen-result");
    const timeHints = {"auto":"生成中...","cloud":"云端生成中（约60秒）...","local_wan22":"本地Wan2.2生成中（约20分钟）...","local_svd":"本地SVD生成中（约2分钟）..."};
    statusEl.textContent = timeHints[mode] || "生成中...";
    resultEl.innerHTML = "";
    try {
        const resp = await fetch(`${API_BASE}/api/settings/comfyui/test-video`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ prompt, mode, size }),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.textContent = "生成成功！";
            resultEl.innerHTML = '<div style="color:#22c55e;">保存位置: ' + data.path + '</div>';
            showHint("视频生成成功");
        } else {
            statusEl.textContent = "生成失败";
            resultEl.innerHTML = '<div style="color:#ff4444;">' + (data.message || "未知错误") + '</div>';
        }
    } catch (e) {
        statusEl.textContent = "请求失败";
        resultEl.innerHTML = '<div style="color:#ff4444;">' + e.message + '</div>';
    }
}

async function testSVDGenerate() {
    const statusEl = document.getElementById("vgen-svd-status");
    statusEl.textContent = "SVD图生视频功能需要先启动ComfyUI并上传图片到input目录";
    showHint("请先在ComfyUI的input目录放入图片", true);
}

// ================================================================
// QQ空间
// ================================================================

async function qzoneAutoLogin() {
    try {
        const { ipcRenderer } = require('electron');
        showHint("QQ空间登录窗口已打开，请登录...");
        const result = await ipcRenderer.invoke('qzone-login');
        if (result.success) {
            await fetch(`${API_BASE}/api/settings/qzone/cookie`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(result.cookies),
            });
            showHint("QQ空间登录成功！Cookie已自动保存");
            checkQZoneStatus();
        } else {
            showHint(result.message || "登录失败", true);
        }
    } catch (e) {
        showHint("登录失败: " + e.message, true);
    }
}

async function checkQZoneStatus() {
    const el = document.getElementById("qzone-status");
    try {
        const resp = await fetch(`${API_BASE}/api/settings/qzone/status`);
        const data = await resp.json();
        if (data.configured) {
            el.innerHTML = '<span style="color:#27ae60;">✅ 已配置 (QQ: ' + data.uin + ')</span>';
        } else {
            el.innerHTML = '<span style="color:#e74c3c;">❌ 未配置Cookie</span>';
        }
    } catch (e) {
        el.innerHTML = '<span style="color:#e74c3c;">检测失败</span>';
    }
}

async function saveQZoneCookie() {
    const uin = document.getElementById("qzone-uin").value.trim();
    const skey = document.getElementById("qzone-skey").value.trim();
    const pskey = document.getElementById("qzone-pskey").value.trim();
    if (!uin || !skey || !pskey) { showHint("三个字段都不能为空", true); return; }
    try {
        const resp = await fetch(`${API_BASE}/api/settings/qzone/cookie`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({uin, skey, p_skey: pskey}),
        });
        const data = await resp.json();
        showHint(data.message, !data.success);
        if (data.success) checkQZoneStatus();
    } catch (e) {
        showHint("保存失败: " + e.message, true);
    }
}

async function testQZone() {
    const el = document.getElementById("qzone-test-result");
    el.textContent = "测试中...";
    try {
        const resp = await fetch(`${API_BASE}/api/settings/qzone/test`, {method: "POST"});
        const data = await resp.json();
        el.innerHTML = data.success
            ? '<span style="color:#27ae60;">' + data.message + '</span>'
            : '<span style="color:#e74c3c;">' + data.message + '</span>';
    } catch (e) {
        el.innerHTML = '<span style="color:#e74c3c;">测试失败</span>';
    }
}

// 页面加载时自动加载新面板数据
document.addEventListener("DOMContentLoaded", function() {
    // 当切换到对应标签页时加载数据
    document.querySelectorAll(".nav-btn").forEach(btn => {
        btn.addEventListener("click", function() {
            const tab = this.dataset.tab;
            if (tab === "character") { loadPromptSections(); loadPromptTemplates(); }
            if (tab === "modules") loadModules();
            if (tab === "stickers") loadStickers();
            if (tab === "plugins") loadMarketPlugins();
            if (tab === "devplatform" && _devToken) loadDevelopers();
            if (tab === "aigen") { checkComfyUIStatus(); loadAIGenConfig(); }
            if (tab === "qzone") checkQZoneStatus();
            if (tab === "users") {
                loadAllAffinity();
                loadSecrets();
                loadImpressions();
                // 2026-07-03 面板升级（批6）：切tab时渲染用户过滤状态区
                // （原 filter-status 永远停在"加载中..."）
                loadFilterStatus();
            }
            // 2026-07-03 面板升级（批6）：新增切tab动态加载——声音克隆状态卡、
            // 关于页动态信息、表情动作页映射/表情池（均为批6新端点）
            if (tab === "voice_clone") { loadVoiceCloneStatus(); refreshVoiceTrainStatus(); }
            if (tab === "about") loadAbout();
            if (tab === "expressions") initExpressionMapping();
        });
    });
});

// ============================================================
// 家人QQ号管理
// ============================================================

/** 渲染家人QQ号列表 */
function renderFamilyQQList() {
    const container = document.getElementById('qq-family-list');
    if (!container) return;
    const list = window._familyQQList || [];
    if (list.length === 0) {
        container.innerHTML = '<span style="color:#718096;font-size:12px;">暂未添加家人</span>';
        return;
    }
    container.innerHTML = list.map(qq =>
        `<div style="display:flex;align-items:center;gap:6px;background:#1a202c;border:1px solid #2d3748;border-radius:6px;padding:4px 10px;">
            <span style="color:#e2e8f0;font-size:13px;">${qq}</span>
            <button onclick="removeFamilyQQ('${qq}')" style="background:none;border:none;color:#e53e3e;cursor:pointer;font-size:14px;padding:0 2px;" title="删除">×</button>
        </div>`
    ).join('');
}

/** 添加家人QQ号 */
function addFamilyQQ() {
    const input = document.getElementById('qq-family-input');
    const qq = (input.value || '').trim();
    if (!qq) return;
    if (!/^\d+$/.test(qq)) {
        alert('QQ号必须是纯数字');
        return;
    }
    if (!window._familyQQList) window._familyQQList = [];
    if (window._familyQQList.includes(qq)) {
        alert('这个QQ号已经添加过了');
        return;
    }
    window._familyQQList.push(qq);
    input.value = '';
    renderFamilyQQList();
}

/** 删除家人QQ号 */
function removeFamilyQQ(qq) {
    if (!window._familyQQList) return;
    window._familyQQList = window._familyQQList.filter(q => q !== qq);
    renderFamilyQQList();
}

// ============================================================
// 2026-07-03 面板升级（批6）：以下为本批新增函数
// （LLM连通测试 / 记忆搜索与编辑 / 声音克隆状态 / QZone发说说测试 / 关于页动态化）
// ============================================================

/**
 * LLM 通道连通性测试——调批6新端点 POST /llm/test（1-token探活）。
 * 直接带表单当前值（未保存的也能测），空值由后端回退已保存配置/预设供应商。
 *
 * @param {string} role LLM_TEST_ROLES 之一：'llm' 或 'llm_tool' 等
 */
async function testLLMChannel(role) {
    // 表单元素 id 前缀：主通道 'llm'，子通道 'llm_tool' → 'llm-tool'
    const prefix = role === 'llm' ? 'llm' : 'llm-' + role.slice(4);
    const resultEl = document.getElementById(`llm-test-result-${role}`);
    if (resultEl) {
        resultEl.style.color = '#f59e0b';
        resultEl.textContent = '测试中...（最长30秒）';
    }
    try {
        const resp = await fetch(`${API_BASE}/api/settings/llm/test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                role: role,
                provider: getVal(`${prefix}-provider`),
                api_key: getVal(`${prefix}-api-key`),
                base_url: getVal(`${prefix}-base-url`),
                model: getVal(`${prefix}-model`),
            }),
        });
        const data = await resp.json();
        if (!resultEl) return;
        if (resp.ok && data.ok) {
            resultEl.style.color = '#22c55e';
            resultEl.textContent = `✅ 连通正常（${data.elapsed_ms}ms · ${data.model || ''}）`;
        } else {
            resultEl.style.color = '#e74c3c';
            resultEl.textContent = `❌ ${data.error || data.detail || '测试失败'}`;
        }
    } catch (e) {
        if (resultEl) {
            resultEl.style.color = '#e74c3c';
            resultEl.textContent = '❌ 无法连接后端: ' + e.message;
        }
    }
}

/**
 * 长期记忆搜索——调批6新端点 GET /memory/search?q=（有ChromaDB走语义检索，
 * 否则关键词匹配降级）。结果渲染到独立结果区，不干扰60秒轮询的"最近记忆"。
 */
async function searchMemories() {
    const input = document.getElementById('memory-search-input');
    const resultsEl = document.getElementById('memory-search-results');
    if (!input || !resultsEl) return;
    const query = input.value.trim();
    if (!query) {
        resultsEl.style.display = 'none';
        resultsEl.innerHTML = '';
        return;
    }
    resultsEl.style.display = 'block';
    resultsEl.innerHTML = '<div class="log-line" style="color:#6b7b8d;">搜索中...</div>';
    try {
        const resp = await fetch(`${API_BASE}/api/settings/memory/search?q=${encodeURIComponent(query)}&limit=20`);
        const data = await resp.json();
        if (data.error) {
            resultsEl.innerHTML = `<div class="log-line" style="color:#e74c3c;">搜索失败: ${escapeHtml(data.error)}</div>`;
            return;
        }
        if (!data.results || data.results.length === 0) {
            resultsEl.innerHTML = '<div class="log-line" style="color:#6b7b8d;">没有找到相关记忆</div>';
            return;
        }
        resultsEl.innerHTML = data.results.map(m => {
            const hl = m.is_highlight ? ' ★' : '';
            return `<div class="log-line">[${escapeHtml(m.layer || '')}]${hl} ${escapeHtml(m.content || '')} <span style="color:#4a5568;">(重要度${m.importance})</span></div>`;
        }).join('');
    } catch (e) {
        resultsEl.innerHTML = '<div class="log-line" style="color:#e74c3c;">无法连接后端</div>';
    }
}

/**
 * 核心记忆行内"编辑"——把 key/value 回填到下方添加输入框，复用 addCoreMemory
 * 提交（后端 core_store.set 同键即覆盖）。参数是 encodeURIComponent 过的。
 *
 * @param {string} keyEnc 编码后的键名
 * @param {string} valueEnc 编码后的内容
 */
function editCoreMemory(keyEnc, valueEnc) {
    const keyInput = document.getElementById('new-mem-key');
    const valueInput = document.getElementById('new-mem-value');
    if (!keyInput || !valueInput) return;
    keyInput.value = decodeURIComponent(keyEnc);
    valueInput.value = decodeURIComponent(valueEnc);
    valueInput.focus();
    showHint('已回填到下方输入框，改完点「添加」即覆盖保存');
}

/**
 * 声音克隆状态卡——调批6新端点 GET /voice-clone/status 动态渲染当前
 * GPT/SoVITS 权重与版本（train_voice.py 每次训练完会更新 tts_infer.yaml）。
 */
async function loadVoiceCloneStatus() {
    const container = document.getElementById('voiceclone-model-info');
    if (!container) return;
    try {
        const resp = await fetch(`${API_BASE}/api/settings/voice-clone/status`);
        const data = await resp.json();
        if (!data.available) {
            container.innerHTML = `<div class="about-row"><span>状态</span><span style="color:#e74c3c;">${escapeHtml(data.error || '不可用')}</span></div>`;
            return;
        }
        // 路径太长只显示文件名，完整路径放 title 悬浮
        const baseName = (p) => String(p || '').split(/[\\/]/).pop() || '--';
        container.innerHTML = `
            <div class="about-row"><span>GPT 权重</span><span title="${escapeHtml(data.gpt_weights)}">${escapeHtml(baseName(data.gpt_weights))}</span></div>
            <div class="about-row"><span>SoVITS 权重</span><span title="${escapeHtml(data.sovits_weights)}">${escapeHtml(baseName(data.sovits_weights))}</span></div>
            <div class="about-row"><span>版本</span><span>GPT-SoVITS ${escapeHtml(data.version || '--')}</span></div>
            <div class="about-row"><span>推理设备</span><span>${escapeHtml(data.device || '--')}</span></div>
            <div class="about-row"><span>配置文件</span><span title="${escapeHtml(data.config_path)}">${escapeHtml(baseName(data.config_path))}</span></div>
        `;
    } catch (e) {
        container.innerHTML = '<div class="about-row"><span>状态</span><span style="color:#e74c3c;">无法连接后端</span></div>';
    }
}

// ============================================================
// 2026-07-03 功能大项（批11二波）：声音一键训练
// 面板"声音克隆"页触发 GPT-SoVITS 训练 + 每3秒轮询看进度。
// 端点：POST /voice-clone/train、GET /voice-clone/train-status、
//       POST /voice-clone/train-stop。
// ============================================================

// 训练进度轮询定时器句柄（切页/训练结束时清掉，避免重复轮询）
let _voiceTrainPollTimer = null;

/**
 * 上传训练音频到临时目录，成功后把返回路径回填到路径输入框。
 * 复用通用 POST /upload-temp（multipart）。
 * @param {HTMLInputElement} input 文件选择框
 */
async function uploadTrainAudio(input) {
    const hint = document.getElementById('vc-train-upload-hint');
    const file = input.files && input.files[0];
    if (!file) return;
    if (hint) hint.textContent = '上传中...';
    try {
        const fd = new FormData();
        fd.append('file', file);
        const resp = await fetch(`${API_BASE}/api/settings/upload-temp`, {
            method: 'POST',
            body: fd,
        });
        const data = await resp.json();
        if (data.success && data.path) {
            const pathInput = document.getElementById('vc-train-audio');
            if (pathInput) pathInput.value = data.path;
            if (hint) hint.textContent = '✅ 已上传，可直接开始训练';
        } else {
            if (hint) hint.textContent = '❌ ' + (data.message || '上传失败');
        }
    } catch (e) {
        if (hint) hint.textContent = '❌ 无法连接后端';
    } finally {
        input.value = '';  // 清空以便同名文件可再次触发 change
    }
}

/**
 * 点「开始训练」：调 POST /voice-clone/train 起训练进程。
 * 成功后开始轮询进度；GPT-SoVITS 未装/防重复等情形按后端返回的中文提示展示。
 */
async function startVoiceTrain() {
    const audioPath = (document.getElementById('vc-train-audio') || {}).value || '';
    const resume = !!(document.getElementById('vc-train-resume') || {}).checked;
    const statusEl = document.getElementById('vc-train-status');
    const startBtn = document.getElementById('vc-train-start-btn');
    if (!confirm('开始训练白的专属声音？\n这会占用显卡、耗时约几十分钟，期间白可以照常聊天。')) return;
    if (startBtn) { startBtn.disabled = true; startBtn.textContent = '启动中...'; }
    if (statusEl) statusEl.innerHTML = '<span style="color:#f59e0b;">正在启动训练...</span>';
    try {
        const resp = await fetch(`${API_BASE}/api/settings/voice-clone/train`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ audio_path: audioPath.trim(), resume }),
        });
        const data = await resp.json();
        if (data.ok && data.started) {
            if (statusEl) statusEl.innerHTML = `<span style="color:#27ae60;">✅ ${escapeHtml(data.message || '训练已开始')}</span>`;
            _setVoiceTrainRunningUI(true);
            _startVoiceTrainPolling();
        } else {
            // 未装 GPT-SoVITS / 正在训练中 / 音频不存在等：展示后端中文指引
            if (statusEl) statusEl.innerHTML = `<span style="color:#e74c3c;">${escapeHtml(data.message || '启动失败')}</span>`;
            // 若因"已在训练中"被拒，仍然进入运行态并轮询（接管已在跑的训练）
            if (data.running) { _setVoiceTrainRunningUI(true); _startVoiceTrainPolling(); }
            else { _setVoiceTrainRunningUI(false); }
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = '<span style="color:#e74c3c;">❌ 无法连接后端</span>';
        _setVoiceTrainRunningUI(false);
    }
}

/**
 * 点「停止训练」：调 POST /voice-clone/train-stop 终止训练进程。
 */
async function stopVoiceTrain() {
    if (!confirm('确定停止训练？已产出的中间结果会保留，下次可勾选「断点续跑」继续。')) return;
    const statusEl = document.getElementById('vc-train-status');
    try {
        const resp = await fetch(`${API_BASE}/api/settings/voice-clone/train-stop`, { method: 'POST' });
        const data = await resp.json();
        if (statusEl) statusEl.innerHTML = `<span style="color:#7a8fa0;">${escapeHtml(data.message || '已请求停止')}</span>`;
    } catch (e) {
        if (statusEl) statusEl.innerHTML = '<span style="color:#e74c3c;">❌ 无法连接后端</span>';
    }
    // 停止后再拉一次状态刷新按钮态与日志
    refreshVoiceTrainStatus();
}

/** 开始每3秒轮询训练状态（幂等：已在轮询则不重复起定时器）。 */
function _startVoiceTrainPolling() {
    if (_voiceTrainPollTimer) return;
    _voiceTrainPollTimer = setInterval(refreshVoiceTrainStatus, 3000);
}

/** 停止轮询。 */
function _stopVoiceTrainPolling() {
    if (_voiceTrainPollTimer) {
        clearInterval(_voiceTrainPollTimer);
        _voiceTrainPollTimer = null;
    }
}

/**
 * 按运行态切换按钮/日志区可见性与禁用态。
 * @param {boolean} running 是否训练进行中
 */
function _setVoiceTrainRunningUI(running) {
    const startBtn = document.getElementById('vc-train-start-btn');
    const stopBtn = document.getElementById('vc-train-stop-btn');
    const logEl = document.getElementById('vc-train-log');
    if (startBtn) {
        startBtn.disabled = running;
        startBtn.textContent = running ? '训练中…' : '开始训练';
    }
    if (stopBtn) stopBtn.style.display = running ? 'inline-block' : 'none';
    if (logEl && running) logEl.style.display = 'block';
}

/**
 * 拉一次训练状态：更新日志区、运行态按钮；训练结束时停轮询、提示完成并刷新音色状态卡。
 */
async function refreshVoiceTrainStatus() {
    const logEl = document.getElementById('vc-train-log');
    const statusEl = document.getElementById('vc-train-status');
    try {
        const resp = await fetch(`${API_BASE}/api/settings/voice-clone/train-status`);
        const data = await resp.json();
        const running = !!data.running;
        _setVoiceTrainRunningUI(running);

        // 渲染最近日志
        const lines = Array.isArray(data.last_lines) ? data.last_lines : [];
        if (logEl && lines.length) {
            logEl.style.display = 'block';
            logEl.innerHTML = lines
                .map((l) => `<div class="log-line">${escapeHtml(l)}</div>`)
                .join('');
            logEl.scrollTop = logEl.scrollHeight;
        }

        if (running) {
            _startVoiceTrainPolling();
            if (statusEl) statusEl.innerHTML = '<span style="color:#f59e0b;">训练进行中…（期间白可正常聊天）</span>';
        } else {
            _stopVoiceTrainPolling();
            if (data.done) {
                // 训练结束：done=true（有日志文件且进程已结束）。刷新音色状态卡。
                if (statusEl) statusEl.innerHTML = '<span style="color:#27ae60;">✅ 训练进程已结束。请查看日志末尾确认是否成功；若成功，白已用上专属声音（可重启本地TTS生效）。</span>';
                loadVoiceCloneStatus();
            }
        }
    } catch (e) {
        // 后端不可达时静默（轮询下次再试），不打断用户
    }
}

/**
 * 语音页与声音克隆页的参考音频输入框双向镜像（同一配置 tts.ref_audio/ref_text，
 * saveAllSettings 只读语音页的值，镜像保证两处始终一致）。
 */
function bindRefAudioMirrors() {
    const pairs = [
        ['tts-ref-audio', 'vc-ref-audio'],
        ['tts-ref-text', 'vc-ref-text'],
    ];
    for (const [a, b] of pairs) {
        const elA = document.getElementById(a);
        const elB = document.getElementById(b);
        if (!elA || !elB) continue;
        elA.addEventListener('input', () => { elB.value = elA.value; });
        elB.addEventListener('input', () => { elA.value = elB.value; });
    }
}

/**
 * 发说说测试——调批6新端点 POST /qzone/test-post（真实发布到QQ空间，
 * 用于验证Cookie发布权限）。
 */
async function qzoneTestPost() {
    const input = document.getElementById('qzone-test-post-content');
    const resultEl = document.getElementById('qzone-post-result');
    if (!input || !resultEl) return;
    const content = input.value.trim();
    if (!content) { showHint('请输入说说内容', true); return; }
    if (!confirm('确定发布这条说说？会真实出现在你的QQ空间！')) return;
    resultEl.innerHTML = '<span style="color:#f59e0b;">发布中...</span>';
    try {
        const resp = await fetch(`${API_BASE}/api/settings/qzone/test-post`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        const data = await resp.json();
        if (data.success) {
            resultEl.innerHTML = `<span style="color:#27ae60;">✅ ${escapeHtml(data.message || '发布成功')}${data.tid ? '（tid: ' + escapeHtml(String(data.tid)) + '）' : ''}</span>`;
            input.value = '';
        } else {
            resultEl.innerHTML = `<span style="color:#e74c3c;">❌ ${escapeHtml(data.message || '发布失败')}</span>`;
        }
    } catch (e) {
        resultEl.innerHTML = '<span style="color:#e74c3c;">❌ 无法连接后端</span>';
    }
}

/**
 * 关于页动态信息——调批6新端点 GET /about 填充版本号/工具数/记忆模块统计，
 * 替代硬编码快照。
 */
async function loadAbout() {
    try {
        const resp = await fetch(`${API_BASE}/api/settings/about`);
        const data = await resp.json();
        const setText = (id, text) => {
            const el = document.getElementById(id);
            if (el) el.textContent = text;
        };
        setText('about-version', data.version || '--');
        setText('about-python', data.python_version || '--');
        setText('about-tool-count', String(data.tool_count ?? '--'));
        setText('about-module-stats',
            `${data.module_total ?? 0}个记忆模块，启用${data.module_enabled ?? 0} / 禁用${data.module_disabled ?? 0}`);
    } catch (e) {
        console.error('[About] 加载关于页信息失败:', e);
    }
}
