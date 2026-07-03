/**
 * particles.js - 科幻粒子背景效果
 *
 * 在画面上飘浮微小的冰蓝色光点，营造科幻氛围。
 * 粒子会缓慢移动、闪烁，像太空中的星尘。
 *
 * 注意：这个效果是纯装饰性的，不影响Live2D渲染。
 * 使用独立的canvas，性能开销很小。
 */

class ParticleSystem {
    /**
     * 初始化粒子系统。
     *
     * @param {string} canvasId - canvas元素的ID
     * @param {object} options - 可选配置
     */
    constructor(canvasId, options = {}) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;

        this.ctx = this.canvas.getContext('2d');
        this.particles = [];

        // 配置参数（可以在外部调整）
        this.config = {
            count: options.count || 50,           // 粒子数量
            minSize: options.minSize || 1,         // 最小粒子半径（像素）
            maxSize: options.maxSize || 3,         // 最大粒子半径
            speed: options.speed || 0.3,           // 移动速度
            color: options.color || '0, 212, 255', // 粒子颜色（RGB格式）
            minOpacity: options.minOpacity || 0.1, // 最小透明度
            maxOpacity: options.maxOpacity || 0.6, // 最大透明度
        };

        // 适配窗口大小
        this._resize();
        window.addEventListener('resize', () => this._resize());

        // 生成初始粒子
        this._createParticles();

        // 启动动画循环
        this._animate();
    }

    /** 适配窗口大小 */
    _resize() {
        this.canvas.width = window.innerWidth;
        this.canvas.height = window.innerHeight;
    }

    /** 生成所有粒子 */
    _createParticles() {
        this.particles = [];
        for (let i = 0; i < this.config.count; i++) {
            this.particles.push(this._createSingleParticle());
        }
    }

    /** 生成一个粒子 */
    _createSingleParticle() {
        const { minSize, maxSize, speed, minOpacity, maxOpacity } = this.config;
        return {
            x: Math.random() * this.canvas.width,
            y: Math.random() * this.canvas.height,
            size: minSize + Math.random() * (maxSize - minSize),
            speedX: (Math.random() - 0.5) * speed,
            speedY: (Math.random() - 0.5) * speed,
            opacity: minOpacity + Math.random() * (maxOpacity - minOpacity),
            // 闪烁：每个粒子有不同的闪烁频率
            twinkleSpeed: 0.005 + Math.random() * 0.015,
            twinklePhase: Math.random() * Math.PI * 2,
        };
    }

    /** 动画循环 */
    _animate() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

        for (const p of this.particles) {
            // 更新位置
            p.x += p.speedX;
            p.y += p.speedY;

            // 边界循环（从一边出去，从另一边进来）
            if (p.x < -10) p.x = this.canvas.width + 10;
            if (p.x > this.canvas.width + 10) p.x = -10;
            if (p.y < -10) p.y = this.canvas.height + 10;
            if (p.y > this.canvas.height + 10) p.y = -10;

            // 计算闪烁透明度
            p.twinklePhase += p.twinkleSpeed;
            const twinkle = 0.5 + 0.5 * Math.sin(p.twinklePhase);
            const currentOpacity = p.opacity * twinkle;

            // 绘制粒子（带发光效果）
            this.ctx.beginPath();
            this.ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
            this.ctx.fillStyle = `rgba(${this.config.color}, ${currentOpacity})`;
            this.ctx.fill();

            // 外层光晕
            this.ctx.beginPath();
            this.ctx.arc(p.x, p.y, p.size * 2.5, 0, Math.PI * 2);
            this.ctx.fillStyle = `rgba(${this.config.color}, ${currentOpacity * 0.15})`;
            this.ctx.fill();
        }

        requestAnimationFrame(() => this._animate());
    }
}

// 全局暴露（供其他模块使用）
window.ParticleSystem = ParticleSystem;
