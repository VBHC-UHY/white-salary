const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function loadController() {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'js', 'chat-controller.js'),
    'utf8',
  );
  const sandbox = {
    Audio: class {},
    Blob,
    Buffer,
    clearTimeout,
    console,
    document: {
      addEventListener() {},
      createElement() { return { textContent: '', innerHTML: '' }; },
    },
    navigator: {},
    setTimeout,
    URL: { createObjectURL() { return 'blob:test'; } },
    window: {},
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'chat-controller.js' });
  return sandbox.window.ChatController;
}

const ChatController = loadController();

function bareController() {
  const controller = Object.create(ChatController.prototype);
  controller._isPlaying = false;
  controller._sentences = {};
  controller._nextPlayIndex = 0;
  controller._voiceBuffer = [];
  controller._voiceMergeTimer = null;
  controller._voiceMergeTimeout = 25;
  controller._pendingVoiceRequests = new Set();
  controller._activeVoiceRequestId = null;
  controller._recentBackendErrors = new Map();
  controller._fullReply = '';
  controller._replyDone = false;
  controller.micBtn = null;
  controller._updateSubtitle = () => {};
  controller._updateBubble = () => {};
  return controller;
}

test('audio-skipped sentences advance the ordered playback cursor', () => {
  const controller = bareController();
  const shown = [];
  controller._updateSubtitle = (text) => shown.push(text);
  controller._sentences = {
    0: { text: '第一句。', audio: null, audioSkipped: true, played: false },
    1: { text: '第二句。', audio: null, audioSkipped: true, played: false },
  };

  controller._tryPlayNext();

  assert.equal(controller._nextPlayIndex, 2);
  assert.deepEqual(shown, ['第一句。', '第二句。']);
  assert.equal(controller._sentences[0].played, true);
  assert.equal(controller._sentences[1].played, true);
});

test('an early skipped frame waits for its sentence text', () => {
  const controller = bareController();
  controller._sentences = {
    0: { text: '', audio: null, audioSkipped: true, played: false },
  };

  controller._tryPlayNext();
  assert.equal(controller._nextPlayIndex, 0);

  controller._handleSentence('后来到达的文本。', 0);
  assert.equal(controller._nextPlayIndex, 1);
});

test('continuous voice merge timer starts only after all ASR requests finish', () => {
  const controller = bareController();
  controller._pendingVoiceRequests.add('voice-1');
  controller._pendingVoiceRequests.add('voice-2');

  controller._handleTranscription('第一段', 'continuous', 'voice-1');
  assert.equal(controller._voiceMergeTimer, null);

  controller._handleVoiceStatus('done', 'voice-1');
  assert.equal(controller._voiceMergeTimer, null);

  controller._handleTranscription('第二段', 'continuous', 'voice-2');
  controller._handleVoiceStatus('done', 'voice-2');
  assert.notEqual(controller._voiceMergeTimer, null);
  clearTimeout(controller._voiceMergeTimer);
});

test('a stale push-to-talk transcript cannot replace the latest request', () => {
  const controller = bareController();
  controller._activeVoiceRequestId = 'voice-new';

  controller._handleTranscription('旧结果', 'push_to_talk', 'voice-old');

  assert.deepEqual(controller._voiceBuffer, []);
});

test('a silent tool completion does not add an empty assistant message', () => {
  const controller = bareController();
  const messages = [];
  controller._addChatMessage = (role, text) => messages.push({ role, text });
  controller._scheduleHideAfterAudio = () => {};

  controller._handleMessage({ type: 'done', content: '', silent: true });

  assert.deepEqual(messages, []);
  assert.equal(controller._replyDone, true);
});

test('identical automatic backend errors are shown only once', () => {
  const controller = bareController();
  const messages = [];
  controller._addChatMessage = (role, text) => messages.push({ role, text });

  controller._handleMessage({ type: 'error', content: 'Connection error' });
  controller._handleMessage({ type: 'error', content: 'Connection error' });

  assert.deepEqual(messages, [
    { role: 'system', text: '[Error] Connection error' },
  ]);
});

test('a new user reply attempt clears the previous error suppression', () => {
  const controller = bareController();
  const messages = [];
  controller._addChatMessage = (role, text) => messages.push({ role, text });
  controller._handleReplyStart = () => {};

  controller._handleMessage({ type: 'error', content: 'Connection error' });
  controller._handleMessage({ type: 'reply_start', source: 'user' });
  controller._handleMessage({ type: 'error', content: 'Connection error' });

  assert.deepEqual(messages, [
    { role: 'system', text: '[Error] Connection error' },
    { role: 'system', text: '[Error] Connection error' },
  ]);
});

// ---------------------------------------------------------------------------
// 断线清理在途语音请求（v0.1.12 修复）
//
// 缺陷：_pendingVoiceRequests 只在收到终态 voice_status 帧或发送失败时才移除
// requestId。连接在“已排队”之后、终态帧之前断开时（识别较慢时窗口很宽），
// requestId 永久滞留，而 _scheduleVoiceBufferFlush 的第一行守卫就是
// “size > 0 则直接 return”——此后持续监听识别出的文字只会一直堆进缓冲永不
// 发送，麦克风永久停在“正在识别”，只能重启客户端。
// ---------------------------------------------------------------------------

function controllerWithPendingVoice() {
  const controller = bareController();
  controller._pendingVoiceRequests = new Set(['voice-1', 'voice-2']);
  controller._activeVoiceRequestId = 'voice-2';
  controller._voiceMergeTimer = setTimeout(() => {}, 60000);
  controller.micBtn = {
    classList: {
      _set: new Set(['processing']),
      add(name) { this._set.add(name); },
      remove(name) { this._set.delete(name); },
      contains(name) { return this._set.has(name); },
    },
    title: '正在识别',
  };
  controller._notices = [];
  controller._showSystemNotice = (text) => controller._notices.push(text);
  return controller;
}

test('disconnecting clears in-flight voice requests so continuous listening recovers', () => {
  const controller = controllerWithPendingVoice();

  controller._abortPendingVoiceRequests('连接断开');

  assert.equal(controller._pendingVoiceRequests.size, 0);
  assert.equal(controller._activeVoiceRequestId, null);
  assert.equal(controller._voiceMergeTimer, null);
});

test('aborting in-flight voice requests resets the mic button out of the processing state', () => {
  const controller = controllerWithPendingVoice();

  controller._abortPendingVoiceRequests('连接断开');

  assert.equal(controller.micBtn.classList.contains('processing'), false);
  assert.equal(controller.micBtn.title, '按住说话');
});

test('aborting in-flight voice requests tells the user the recognition was dropped', () => {
  const controller = controllerWithPendingVoice();

  controller._abortPendingVoiceRequests('连接断开');

  assert.equal(controller._notices.length, 1);
  assert.match(controller._notices[0], /语音/);
});

test('aborting with nothing in flight stays silent', () => {
  // 正常关闭不该刷提示，否则每次退出都会多一条系统消息
  const controller = controllerWithPendingVoice();
  controller._pendingVoiceRequests.clear();
  controller._activeVoiceRequestId = null;

  controller._abortPendingVoiceRequests('主动断开');

  assert.deepEqual(controller._notices, []);
});

test('the voice buffer flush guard is what the cleanup unblocks', () => {
  // 固化因果关系：清空 _pendingVoiceRequests 之所以能解除卡死，
  // 是因为 flush 的前置守卫看的正是它。若哪天守卫改了条件，
  // 这条断言会提醒重新确认清理动作是否仍然有效。
  const source = ChatController.prototype._scheduleVoiceBufferFlush.toString();
  assert.ok(
    source.includes('_pendingVoiceRequests'),
    'flush 守卫不再依赖 _pendingVoiceRequests，需重新确认断线清理是否还有意义',
  );
});

test('a queued voice request is still tracked before the terminal frame arrives', () => {
  // 反向确认：修复没有把正常的“在途”跟踪一起去掉，
  // 否则并发语音请求的排队保护就没了。
  const controller = bareController();

  controller._handleVoiceStatus('queued', 'voice-9');

  assert.equal(controller._pendingVoiceRequests.has('voice-9'), true);
});
