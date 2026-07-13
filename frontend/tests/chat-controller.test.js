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
