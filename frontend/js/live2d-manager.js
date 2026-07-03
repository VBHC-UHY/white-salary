/**
 * live2d-manager.js - Live2D model manager
 *
 * Handles:
 *   1. PixiJS app initialization
 *   2. Live2D model loading
 *   3. Model position, scale, drag, zoom
 *   4. Lip sync (mouth movement driven by audio amplitude)
 *   5. Expression switching (emotion-based)
 *   6. Mouse passthrough for desktop pet mode
 */

class Live2DManager {
    constructor() {
        /** @type {PIXI.Application} */
        this.pixiApp = null;

        /** @type {PIXI.live2d.Live2DModel} */
        this.model = null;

        /** Model loaded flag */
        this.isLoaded = false;

        /** Current model scale */
        this.scale = 0.25;

        /** Lip sync current value (0=closed, 1=open) */
        this._lipSyncValue = 0;

        /** Drag state */
        this._isDragging = false;
        this._dragOffsetX = 0;
        this._dragOffsetY = 0;

        // 2026-07-02 审计修复（批3）：鼠标穿透监听是否已注册（防止 app.js 与 initialize 重复注册）
        this._passthroughRegistered = false;
    }

    /**
     * Initialize PixiJS and load a Live2D model.
     *
     * @param {string} modelPath - path to .model3.json file
     * @param {object} options - optional config
     */
    async initialize(modelPath, options = {}) {
        this.scale = options.scale || 0.25;

        // 1. Create PixiJS app
        this.pixiApp = new PIXI.Application({
            view: document.getElementById('canvas'),
            autoStart: true,
            transparent: true,
            width: window.innerWidth,
            height: window.innerHeight,
        });

        // 2. Load Live2D model
        try {
            this.model = await PIXI.live2d.Live2DModel.from(modelPath);
            this.pixiApp.stage.addChild(this.model);
            this.isLoaded = true;

            // 3. Setup model properties
            this._setupModel(options);

            // 4. Setup mouse interactions (drag, zoom, passthrough)
            // 2026-07-02 审计修复（批3）：穿透注册已解耦到 setupMousePassthrough（app.js 在 DOM
            // 就绪时先行调用），这里保留一次带防重入保护的调用作为兜底。
            this._setupDragAndZoom();
            this.setupMousePassthrough();

            // 5. Handle window resize
            window.addEventListener('resize', () => this._onResize());

            console.log('[Live2D] Model loaded:', modelPath);
        } catch (error) {
            console.error('[Live2D] Failed to load model:', error);
            throw error;
        }
    }

    /**
     * Setup initial model properties (position, scale).
     * Default: small model in the bottom-right area like a desktop pet.
     * @private
     */
    _setupModel(options) {
        if (!this.model) return;

        const model = this.model;

        // Scale - much smaller than fullscreen
        model.scale.set(this.scale);

        // Anchor at center bottom (feet)
        model.anchor.set(0.5, 1.0);

        // Position: bottom-right area of screen
        const posX = options.posX || window.innerWidth * 0.75;
        const posY = options.posY || window.innerHeight * 0.95;
        model.position.set(posX, posY);

        // Enable internal animations (breathing, blinking)
        model.autoUpdate = true;

        // Make model interactive (needed for drag and hit detection)
        model.interactive = true;
        model.buttonMode = true;
    }

    /**
     * Setup drag (left-click hold) and zoom (scroll wheel).
     * @private
     */
    _setupDragAndZoom() {
        if (!this.model) return;

        const model = this.model;

        // --- Drag: hold left mouse button to move model ---
        model.on('pointerdown', (e) => {
            this._isDragging = true;
            const pos = e.data.global;
            this._dragOffsetX = pos.x - model.position.x;
            this._dragOffsetY = pos.y - model.position.y;
        });

        document.addEventListener('pointermove', (e) => {
            if (!this._isDragging) return;
            model.position.set(
                e.clientX - this._dragOffsetX,
                e.clientY - this._dragOffsetY
            );
        });

        document.addEventListener('pointerup', () => {
            this._isDragging = false;
        });

        // --- Zoom: scroll wheel to resize model ---
        document.addEventListener('wheel', (e) => {
            if (!this.model || !this.isLoaded) return;

            // Check if mouse is roughly near the model
            const modelBounds = model.getBounds();
            const mouseX = e.clientX;
            const mouseY = e.clientY;

            // Expand hit area a bit for easier targeting
            const padding = 100;
            const isNearModel = (
                mouseX >= modelBounds.x - padding &&
                mouseX <= modelBounds.x + modelBounds.width + padding &&
                mouseY >= modelBounds.y - padding &&
                mouseY <= modelBounds.y + modelBounds.height + padding
            );

            if (!isNearModel) return;

            // Zoom in/out
            const zoomSpeed = 0.02;
            if (e.deltaY < 0) {
                // Scroll up = zoom in
                this.scale = Math.min(1.0, this.scale + zoomSpeed);
            } else {
                // Scroll down = zoom out
                this.scale = Math.max(0.05, this.scale - zoomSpeed);
            }

            model.scale.set(this.scale);
        });
    }

    /**
     * Setup mouse passthrough for desktop pet mode.
     * When mouse is on the model or chat area: disable passthrough (can interact).
     * When mouse is elsewhere: enable passthrough (clicks go through to desktop).
     *
     * 2026-07-02 审计修复（批3）：与模型加载解耦——原来只在模型加载成功后注册，
     * 模型一旦加载失败窗口永久 ignoreMouseEvents(true)，聊天输入/按钮全部点不到。
     * 现在改为公开方法，DOM/canvas 就绪即可由 app.js 调用注册；模型为 null 时
     * 按 UI 元素（聊天框等）判定命中，保证无模型时 UI 仍可交互。
     */
    setupMousePassthrough() {
        // 防重入：app.js 与 initialize() 都可能调用，只注册一次
        if (this._passthroughRegistered) return;
        this._passthroughRegistered = true;

        const { ipcRenderer } = require('electron');

        document.addEventListener('mousemove', () => {
            // If dragging, always disable passthrough
            if (this._isDragging) {
                ipcRenderer.send('set-ignore-mouse-events', {
                    ignore: false,
                    options: { forward: false },
                });
                return;
            }

            // Check if mouse is on the model
            // 2026-07-02 审计修复（批3）：仅在模型真正加载后才做模型命中检测，
            // 模型为 null 时跳过（不再整个 return，让 UI 命中检测继续生效）
            let isOnModel = false;
            if (this.model && this.isLoaded && this.pixiApp) {
                try {
                    isOnModel = this.model.containsPoint(
                        this.pixiApp.renderer.plugins.interaction.mouse.global
                    );
                } catch (error) {
                    isOnModel = false;
                }
            }

            // Check if mouse is on the chat area
            const chatContainer = document.getElementById('chat-container');
            const isOnChat = chatContainer && chatContainer.matches(':hover');

            if (isOnModel || isOnChat) {
                ipcRenderer.send('set-ignore-mouse-events', {
                    ignore: false,
                    options: { forward: false },
                });
            } else {
                ipcRenderer.send('set-ignore-mouse-events', {
                    ignore: true,
                    options: { forward: true },
                });
            }
        });

        console.log('[Live2D] Mouse passthrough registered (model-independent)');
    }

    /**
     * Handle window resize.
     * @private
     */
    _onResize() {
        if (!this.pixiApp) return;
        this.pixiApp.renderer.resize(window.innerWidth, window.innerHeight);
    }

    /**
     * Play a motion animation.
     * @param {string} motionGroup - e.g. "Idle", "TapBody"
     * @param {number} index - motion index within the group
     */
    playMotion(motionGroup, index = 0) {
        if (!this.model || !this.isLoaded) return;
        // 2026-07-02 审计修复（批3）：本模型 model3.json 未声明任何 Motions，
        // 先查已注册动作组，缺失时安静降级（只打一条 debug，不再刷 warn 错误）
        try {
            const settings = this.model.internalModel && this.model.internalModel.settings;
            const motions = (settings && settings.motions) || {};
            const groupDefs = motions[motionGroup];
            if (!groupDefs || groupDefs.length === 0) {
                console.debug(`[Live2D] Motion group "${motionGroup}" not defined in model, skipped`);
                return;
            }
            this.model.motion(motionGroup, index);
        } catch (error) {
            console.debug(`[Live2D] Motion failed: ${motionGroup}[${index}]`, error);
        }
    }

    /**
     * Set expression on the Live2D model.
     * @param {string} expressionName - Expression name (e.g., "happy", "sad", "angry")
     */
    setExpression(expressionName) {
        if (!this.model || !this.isLoaded) return;
        // 2026-07-02 审计修复（批3）：不再依赖库抛异常触发回退——pixi-live2d-display 在
        // 表情未注册时 model.expression() 只会静默 no-op（返回 false/undefined，不抛错），
        // 原 catch 回退永远走不到。现改为先查 model3.json 已注册表情名单
        // （internalModel.settings.expressions，Cubism4 用 Name 字段、Cubism2 用 name），
        // 命中才走表情文件路径，否则直接走参数回退路径。
        try {
            const settings = this.model.internalModel && this.model.internalModel.settings;
            const defs = (settings && settings.expressions) || [];
            const isRegistered = defs.some(
                (def) => def && (def.Name === expressionName || def.name === expressionName)
            );
            if (isRegistered) {
                this.model.expression(expressionName);
                console.log(`[Live2D] Expression set: ${expressionName}`);
                return;
            }
            console.debug(`[Live2D] Expression "${expressionName}" not registered, using parameter fallback`);
        } catch (error) {
            console.debug('[Live2D] Expression lookup failed, using parameter fallback:', error);
        }
        this._applyExpressionParams(expressionName);
    }

    /**
     * 2026-07-02 审计修复（批3）：参数回退路径独立成私有方法——
     * 模型没有对应表情文件时，直接写核心参数模拟表情（原 catch 里那套逻辑）。
     * @param {string} expressionName - Expression name (e.g., "happy", "sad", "angry")
     * @private
     */
    _applyExpressionParams(expressionName) {
        try {
            const coreModel = this.model.internalModel.coreModel;
            // Map expression names to parameter values
            const paramMap = {
                'happy':     { 'ParamEyeLSmile': 1, 'ParamEyeRSmile': 1, 'ParamMouthForm': 0.5 },
                'sad':       { 'ParamEyeLSmile': 0, 'ParamEyeRSmile': 0, 'ParamMouthForm': -0.3, 'ParamBrowLY': -0.5, 'ParamBrowRY': -0.5 },
                'angry':     { 'ParamEyeLSmile': 0, 'ParamEyeRSmile': 0, 'ParamBrowLAngle': -1, 'ParamBrowRAngle': -1, 'ParamMouthForm': -0.3 },
                'surprised': { 'ParamEyeLOpen': 1.2, 'ParamEyeROpen': 1.2, 'ParamMouthOpenY': 0.5, 'ParamBrowLY': 0.5, 'ParamBrowRY': 0.5 },
                'shy':       { 'ParamEyeLSmile': 0.5, 'ParamEyeRSmile': 0.5, 'ParamMouthForm': 0.2, 'ParamCheek': 1 },
                'default':   { 'ParamEyeLSmile': 0, 'ParamEyeRSmile': 0, 'ParamMouthForm': 0, 'ParamBrowLY': 0, 'ParamBrowRY': 0 },
            };
            const params = paramMap[expressionName] || paramMap['default'];
            for (const [param, value] of Object.entries(params)) {
                try { coreModel.setParameterValueById(param, value); } catch {}
            }
        } catch (e2) {
            console.warn(`[Live2D] Expression fallback also failed:`, e2);
        }
    }

    /**
     * Set lip sync value (mouth open amount).
     * @param {number} value - 0.0 (closed) to 1.0 (fully open)
     */
    setLipSync(value) {
        if (!this.model || !this.isLoaded) return;
        this._lipSyncValue = Math.max(0, Math.min(1, value));
        try {
            const coreModel = this.model.internalModel.coreModel;
            coreModel.setParameterValueById('ParamMouthOpenY', this._lipSyncValue);
        } catch (error) {
            // Some models may not have this parameter
        }
    }

    /**
     * Get model center position on screen (for bubble positioning).
     * @returns {{ x: number, y: number }}
     */
    getModelPosition() {
        if (!this.model || !this.isLoaded) {
            return { x: window.innerWidth / 2, y: window.innerHeight / 2 };
        }
        return {
            x: this.model.position.x,
            y: this.model.position.y,
        };
    }

    /**
     * Get position for speech bubble (above model's head).
     * @returns {{ x: number, y: number }}
     */
    getBubblePosition() {
        if (!this.model || !this.isLoaded) {
            return { x: window.innerWidth * 0.5, y: window.innerHeight * 0.2 };
        }
        const bounds = this.model.getBounds();
        return {
            x: bounds.x + bounds.width * 0.3,
            y: bounds.y - 20,
        };
    }
}

// Expose globally
window.Live2DManager = Live2DManager;
