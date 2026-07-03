/**
 * app.js - Frontend app entry point
 *
 * Initializes all modules and starts the app:
 *   1. Particle background
 *   2. Live2D model
 *   3. Chat controller (WebSocket)
 */

// ============================================================
// Config
// ============================================================

/** Backend WebSocket URL (configurable via query param: ?ws=ws://host:port/ws/chat) */
const WS_URL = new URLSearchParams(window.location.search).get('ws') || 'ws://localhost:12400/ws/chat';

/** Live2D model path - Silver-haired girl (ulvm2) */
const LIVE2D_MODEL_PATH = '../live2d_models/default/ulvm2_0001.model3.json';

/** Model scale (0.25 = 25% of original size, scroll wheel to adjust) */
const MODEL_SCALE = 0.25;

// ============================================================
// Global instances
// ============================================================

let particleSystem = null;
let live2dManager = null;
let chatController = null;

// ============================================================
// Initialization
// ============================================================

/**
 * Main init function.
 * Starts all modules in order. One module failing won't break others.
 */
async function initializeApp() {
    console.log('='.repeat(50));
    console.log('  White Salary frontend initializing...');
    console.log('='.repeat(50));

    // 1. Particle background
    try {
        particleSystem = new ParticleSystem('particles-canvas', {
            count: 40,
            minSize: 1,
            maxSize: 2.5,
            speed: 0.2,
            color: '0, 212, 255',
            minOpacity: 0.05,
            maxOpacity: 0.4,
        });
        console.log('[App] Particles started');
    } catch (error) {
        console.warn('[App] Particles failed (non-critical):', error);
    }

    // 2. Live2D model
    // 2026-07-02 审计修复（批3）：鼠标穿透注册与模型加载解耦——
    // 原来穿透监听只在模型加载成功后注册，模型一旦失败窗口永久全屏穿透，
    // 聊天输入/按钮全部点不到。现在 DOM 就绪（此处）即先注册穿透监听，
    // 再加载模型；穿透命中检测在模型为 null 时按 UI 元素（聊天框）判定。
    live2dManager = new Live2DManager();
    try {
        live2dManager.setupMousePassthrough();
        console.log('[App] Mouse passthrough registered');
    } catch (error) {
        console.warn('[App] Mouse passthrough setup failed:', error);
    }
    try {
        await live2dManager.initialize(LIVE2D_MODEL_PATH, {
            scale: MODEL_SCALE,
        });
        console.log('[App] Live2D model loaded');
    } catch (error) {
        console.warn('[App] Live2D model failed:', error);
        console.warn('[App] Running without model (chat still works)');
        // 2026-07-02 审计修复（批3）：不再把 live2dManager 置 null——
        // 穿透监听持有该实例，且其全部公开方法在 model 为 null 时都会安全降级
        // （setExpression/playMotion/setLipSync 直接返回，getBubblePosition 返回默认位置）。
    }

    // 3. Chat controller
    try {
        chatController = new ChatController({
            wsUrl: WS_URL,
            live2dManager: live2dManager,
        });
        chatController.connect();
        window._chatController = chatController;  // Expose for UI buttons
        console.log('[App] Chat controller started');
    } catch (error) {
        console.error('[App] Chat controller failed:', error);
    }

    console.log('');
    console.log('[App] White Salary frontend ready!');
    console.log('[App] Hotkeys: F12=DevTools, Ctrl+Q=Quit');
}

// Init after DOM is loaded
window.addEventListener('DOMContentLoaded', () => {
    initializeApp().catch((error) => {
        console.error('[App] Init failed:', error);
    });
});
