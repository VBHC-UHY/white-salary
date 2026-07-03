/**
 * White Salary - Electron 主进程
 *
 * 这个文件是桌面应用的入口，负责：
 *   1. 创建透明无边框窗口（桌面宠物模式）
 *   2. 加载 Live2D 渲染页面
 *   3. 处理窗口置顶、鼠标穿透等桌面宠物行为
 *   4. 管理IPC通信（主进程 ↔ 渲染进程）
 *
 * 架构参考：morettt/my-neuro，但改成适合 White Salary 的科幻风格
 */

const { app, BrowserWindow, ipcMain, screen, globalShortcut, desktopCapturer } = require('electron');
const path = require('path');
const fs = require('fs');

// 修复Windows控制台中文显示（设置UTF-8编码）
if (process.platform === 'win32') {
    try { require('child_process').execSync('chcp 65001', { stdio: 'ignore' }); } catch {}
}

// ============================================================
// 配置
// ============================================================

/** 后端WebSocket地址 */
const BACKEND_WS_URL = 'ws://localhost:12400/ws/chat';

/** 后端HTTP地址 */
const BACKEND_HTTP_URL = 'http://localhost:12400';

// ============================================================
// 窗口管理
// ============================================================

/** 主窗口引用 */
let mainWindow = null;

/** 设置窗口引用 */
let settingsWindow = null;

/**
 * 确保窗口始终在最顶层（桌面宠物必须的）。
 * 有时候其他程序会抢占置顶，所以需要定时检查。
 */
function ensureTopMost(win) {
    if (win && !win.isDestroyed() && !win.isAlwaysOnTop()) {
        win.setAlwaysOnTop(true, 'screen-saver');
    }
}

/**
 * 创建主窗口。
 *
 * 关键设置：
 *   - transparent: true  → 背景完全透明，只看到Live2D角色
 *   - frame: false       → 没有标题栏和边框
 *   - alwaysOnTop: true  → 始终在最顶层
 *   - skipTaskbar: true  → 不显示在任务栏（像桌面宠物一样安静待着）
 */
function createWindow() {
    const primaryDisplay = screen.getPrimaryDisplay();
    const { width: screenWidth, height: screenHeight } = primaryDisplay.workAreaSize;

    mainWindow = new BrowserWindow({
        width: screenWidth,
        height: screenHeight,
        transparent: true,
        frame: false,
        alwaysOnTop: true,
        backgroundColor: '#00000000',
        hasShadow: false,
        focusable: true,
        resizable: false,
        movable: true,
        skipTaskbar: true,
        maximizable: false,
        webPreferences: {
            nodeIntegration: true,
            contextIsolation: false,
            zoomFactor: 1.0,
        },
    });

    // 置顶级别设为最高（screen-saver级别，几乎不会被其他窗口遮挡）
    mainWindow.setAlwaysOnTop(true, 'screen-saver');

    // 默认开启鼠标穿透（鼠标能穿过窗口点到后面的程序）
    // 渲染进程会根据鼠标位置动态切换
    mainWindow.setIgnoreMouseEvents(true, { forward: true });

    // 不显示菜单栏
    mainWindow.setMenu(null);

    // 窗口放在屏幕左上角（全屏大小，但透明）
    mainWindow.setPosition(0, 0);

    // 加载前端页面
    mainWindow.loadFile('index.html');

    // 防止最小化（桌面宠物不应该被最小化）
    mainWindow.on('minimize', (event) => {
        event.preventDefault();
        mainWindow.restore();
    });

    // 失去焦点时重新置顶
    mainWindow.on('blur', () => {
        ensureTopMost(mainWindow);
    });

    // 定时检查置顶状态（每秒一次）
    setInterval(() => {
        ensureTopMost(mainWindow);
    }, 1000);

    return mainWindow;
}

// ============================================================
// Settings Window
// ============================================================

/**
 * Open the settings/control panel window.
 * Creates a new window if not already open.
 */
function openSettingsWindow() {
    if (settingsWindow && !settingsWindow.isDestroyed()) {
        settingsWindow.focus();
        return;
    }

    settingsWindow = new BrowserWindow({
        width: 900,
        height: 650,
        minWidth: 750,
        minHeight: 500,
        frame: false,
        backgroundColor: '#0a0e1a',
        resizable: true,
        webPreferences: {
            nodeIntegration: true,
            contextIsolation: false,
        },
    });

    settingsWindow.loadFile('settings.html');
    settingsWindow.setMenu(null);

    settingsWindow.on('closed', () => {
        settingsWindow = null;
    });
}

// ============================================================
// IPC 通信处理（主进程 ↔ 渲染进程）
// ============================================================

/**
 * 处理渲染进程发来的鼠标穿透设置请求。
 *
 * 原理：当鼠标移到Live2D模型上时，关闭穿透（可以点击交互）；
 * 当鼠标移开时，开启穿透（鼠标能点到后面的程序）。
 */
ipcMain.on('set-ignore-mouse-events', (event, { ignore, options }) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.setIgnoreMouseEvents(ignore, options || { forward: true });
    }
});

/**
 * 处理渲染进程发来的窗口控制请求。
 */
ipcMain.on('window-control', (event, action) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;

    switch (action) {
        case 'minimize':
            mainWindow.minimize();
            break;
        case 'close':
            mainWindow.close();
            break;
        case 'toggle-dev-tools':
            mainWindow.webContents.toggleDevTools();
            break;
        case 'open-settings':
            openSettingsWindow();
            break;
    }
});

/** Settings window controls */
ipcMain.on('settings-control', (event, action) => {
    if (!settingsWindow || settingsWindow.isDestroyed()) return;

    switch (action) {
        case 'minimize':
            settingsWindow.minimize();
            break;
        case 'maximize':
            if (settingsWindow.isMaximized()) {
                settingsWindow.unmaximize();
            } else {
                settingsWindow.maximize();
            }
            break;
        case 'close':
            settingsWindow.close();
            break;
    }
});

// ============================================================
// NapCat QQ start
// ============================================================

ipcMain.on('start-napcat', () => {
    const { exec } = require('child_process');
    const napcatPath = path.join(__dirname, '..', 'NapCat');
    const launcher = path.join(napcatPath, 'launcher.bat');
    const launcherWin10 = path.join(napcatPath, 'launcher-win10.bat');
    const bat = fs.existsSync(launcher) ? launcher : launcherWin10;

    if (fs.existsSync(bat)) {
        exec(`start cmd /k "${bat}"`, { cwd: napcatPath });
        console.log('[NapCat] Started:', bat);
    } else {
        console.error('[NapCat] Launcher not found');
    }
});

ipcMain.on('start-local-tts', () => {
    const { exec } = require('child_process');
    const ttsCmd = 'start "WhiteSalary-TTS" cmd /k "cd /d D:\\AI_Tools\\GPT-SoVITS && call venv_new\\Scripts\\activate.bat && python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml"';
    exec(ttsCmd, (err) => {
        if (err) console.error('[TTS] Start failed:', err);
        else console.log('[TTS] Local GPT-SoVITS start requested');
    });
});

// ============================================================
// Screenshot capture (for vision system)
// ============================================================

/**
 * Capture screenshot of primary screen and return as base64 JPEG.
 * Uses Electron's desktopCapturer (same approach as my-neuro).
 */
ipcMain.handle('take-screenshot', async () => {
    try {
        await new Promise(resolve => setTimeout(resolve, 100));

        const sources = await desktopCapturer.getSources({
            types: ['screen'],
            thumbnailSize: screen.getPrimaryDisplay().workAreaSize,
            fetchWindowIcons: false,
        });

        if (sources.length === 0) return null;

        const screenshot = sources[0].thumbnail;
        const jpegBuffer = screenshot.toJPEG(75);
        const base64 = jpegBuffer.toString('base64');

        console.log(`[Screenshot] Captured ${(base64.length / 1024).toFixed(0)}KB`);
        return base64;
    } catch (error) {
        console.error('[Screenshot] Error:', error);
        return null;
    }
});

// ============================================================
// B站Cookie自动获取（三模式）
// ============================================================

/**
 * 模式A（全自动）：直接读Chrome/Edge的Cookie文件 → SQLite解析 → DPAPI+AES解密
 * 模式B（半自动）：Chrome调试端口CDP协议读cookie
 * 模式C（手动）：Electron开B站登录窗口 → 用户登录后读cookie
 */
ipcMain.handle('bili-login', async () => {
    console.log('[BiliLogin] 开始获取B站Cookie...');

    // 模式A：直接读文件（全自动，不需要Chrome配合）
    try {
        const fileResult = await tryDirectCookieRead();
        if (fileResult && fileResult.SESSDATA) {
            console.log('[BiliLogin] 模式A成功（直接读取Cookie文件）');
            return { success: true, method: 'direct_file', cookies: fileResult };
        }
    } catch (e) {
        console.log('[BiliLogin] 模式A失败:', e.message);
    }

    // 模式B：Chrome调试端口（需要Chrome已开启调试端口）
    try {
        const cdpResult = await tryChromeCDP();
        if (cdpResult && cdpResult.SESSDATA) {
            console.log('[BiliLogin] 模式B成功（Chrome调试端口）');
            return { success: true, method: 'chrome_cdp', cookies: cdpResult };
        }
    } catch (e) {
        console.log('[BiliLogin] 模式B失败:', e.message);
    }

    // 模式C：Electron窗口登录（需要用户手动登录）
    console.log('[BiliLogin] 自动方式均失败，切换模式C（Electron窗口手动登录）');
    const electronResult = await openBiliLoginWindow();
    return electronResult;
});

// ------- 模式A：直接读Cookie文件 -------

/** 需要提取的B站Cookie名 */
const BILI_COOKIE_NAMES = ['SESSDATA', 'bili_jct', 'buvid3', 'DedeUserID'];

/** 模式A：直接从Chrome/Edge读Cookie文件（Windows专属，全自动） */
async function tryDirectCookieRead() {
    if (process.platform !== 'win32') {
        console.log('[BiliLogin] 非Windows平台，跳过直接读取');
        return null;
    }

    const pathMod = require('path');
    const os = require('os');
    const localAppData = process.env.LOCALAPPDATA || '';

    console.log('[BiliLogin] 模式A：扫描浏览器Cookie文件...');

    // 支持的浏览器配置（便携Chrome + 标准Chrome + Edge）
    const browsers = [
        {
            name: '便携Chrome',
            cookiePaths: [
                'D:\\谷歌浏览器\\Chrome\\Data\\Default\\Network\\Cookies',
                'D:\\谷歌浏览器\\Chrome\\Data\\Default\\Cookies',
            ],
            localState: 'D:\\谷歌浏览器\\Chrome\\Data\\Local State',
        },
        {
            name: 'Chrome',
            cookiePaths: [
                pathMod.join(localAppData, 'Google', 'Chrome', 'User Data', 'Default', 'Network', 'Cookies'),
                pathMod.join(localAppData, 'Google', 'Chrome', 'User Data', 'Default', 'Cookies'),
            ],
            localState: pathMod.join(localAppData, 'Google', 'Chrome', 'User Data', 'Local State'),
        },
        {
            name: 'Edge',
            cookiePaths: [
                pathMod.join(localAppData, 'Microsoft', 'Edge', 'User Data', 'Default', 'Network', 'Cookies'),
                pathMod.join(localAppData, 'Microsoft', 'Edge', 'User Data', 'Default', 'Cookies'),
            ],
            localState: pathMod.join(localAppData, 'Microsoft', 'Edge', 'User Data', 'Local State'),
        },
    ];

    for (const browser of browsers) {
        // 找Cookie数据库文件
        let cookiePath = null;
        for (const p of browser.cookiePaths) {
            try { if (fs.existsSync(p)) { cookiePath = p; break; } } catch {}
        }
        if (!cookiePath) continue;

        console.log(`[BiliLogin] 找到${browser.name}的Cookie: ${cookiePath}`);

        // 复制Cookie文件（Chrome运行时会锁定原文件）
        const tmpPath = pathMod.join(os.tmpdir(), `ws_bili_cookies_${Date.now()}`);
        let copied = false;

        // 尝试多种复制方式（全部异步，不阻塞主进程）
        const { exec } = require('child_process');
        const _execAsync = (cmd) => new Promise((resolve) => {
            exec(cmd, { shell: 'cmd.exe', windowsHide: true, timeout: 5000 }, (err) => resolve(!err));
        });

        // 方式1：cmd copy
        if (!copied) {
            await _execAsync(`copy /Y "${cookiePath}" "${tmpPath}" >nul 2>&1`);
            try { if (fs.existsSync(tmpPath) && fs.statSync(tmpPath).size > 0) copied = true; } catch {}
        }
        // 方式2：Node.js原生（同步但很快，不会卡）
        if (!copied) {
            try { fs.copyFileSync(cookiePath, tmpPath); copied = true; } catch {}
        }

        if (!copied) {
            console.log(`[BiliLogin] ${browser.name}的Cookie文件复制失败（可能被浏览器锁定）`);
            continue;
        }

        try {
            // 用sql.js读取SQLite
            const initSqlJs = require('sql.js');
            const SQL = await initSqlJs();
            const buffer = fs.readFileSync(tmpPath);
            const db = new SQL.Database(buffer);

            const results = db.exec(
                "SELECT name, value, encrypted_value FROM cookies " +
                "WHERE host_key LIKE '%bilibili.com' " +
                "AND name IN ('SESSDATA', 'bili_jct', 'buvid3', 'DedeUserID')"
            );

            const cookies = {};

            if (results.length > 0 && results[0].values.length > 0) {
                // 预获取AES解密key（Chrome 80+需要）
                let aesKey = null;

                for (const row of results[0].values) {
                    const name = row[0];             // string
                    const value = row[1];             // string or null
                    const encryptedRaw = row[2];      // Uint8Array or null

                    if (value && typeof value === 'string' && value.length > 0) {
                        // 未加密的cookie值（旧版Chrome）
                        cookies[name] = value;
                    } else if (encryptedRaw && encryptedRaw.length > 3) {
                        // 加密的cookie值（Chrome 80+）
                        const encrypted = Buffer.from(encryptedRaw);
                        const prefix = encrypted.slice(0, 3).toString('ascii');

                        if (prefix === 'v10' || prefix === 'v20') {
                            // AES-256-GCM加密，需要从Local State获取key
                            if (!aesKey) {
                                aesKey = await _getAESKeyFromLocalState(browser.localState);
                            }
                            if (aesKey) {
                                const decrypted = _decryptAESGCM(encrypted, aesKey);
                                if (decrypted) cookies[name] = decrypted;
                            }
                        } else {
                            // 旧版DPAPI直接加密
                            const decrypted = await _decryptDPAPI(encrypted);
                            if (decrypted) cookies[name] = decrypted;
                        }
                    }
                }
            }

            db.close();

            if (cookies.SESSDATA) {
                console.log(`[BiliLogin] 从${browser.name}成功读取${Object.keys(cookies).length}个B站Cookie`);
                return cookies;
            } else {
                console.log(`[BiliLogin] ${browser.name}中没有B站SESSDATA`);
            }
        } catch (e) {
            console.log(`[BiliLogin] ${browser.name}的SQLite读取失败:`, e.message);
        } finally {
            try { fs.unlinkSync(tmpPath); } catch {}
        }
    }

    return null;
}

/**
 * 从Local State文件获取AES-256解密key（异步，不阻塞主进程）。
 * Chrome 80+用DPAPI加密master key → 再用master key做AES-GCM加密cookie。
 */
async function _getAESKeyFromLocalState(localStatePath) {
    const { exec } = require('child_process');

    try {
        if (!fs.existsSync(localStatePath)) {
            console.log('[BiliLogin] Local State文件不存在:', localStatePath);
            return null;
        }

        const localState = JSON.parse(fs.readFileSync(localStatePath, 'utf8'));
        const encryptedKeyB64 = localState.os_crypt && localState.os_crypt.encrypted_key;
        if (!encryptedKeyB64) {
            console.log('[BiliLogin] Local State中没有encrypted_key');
            return null;
        }

        // Base64解码后去掉前5字节"DPAPI"前缀
        const encryptedKey = Buffer.from(encryptedKeyB64, 'base64').slice(5);

        // 用PowerShell调Windows DPAPI解密master key（异步执行）
        const b64Input = encryptedKey.toString('base64');
        const psScript = [
            'Add-Type -AssemblyName System.Security',
            '$enc = [Convert]::FromBase64String("' + b64Input + '")',
            '$dec = [System.Security.Cryptography.ProtectedData]::Unprotect($enc, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
            '[Convert]::ToBase64String($dec)',
        ].join('; ');

        const result = await new Promise((resolve, reject) => {
            exec(
                'powershell.exe -NoProfile -NonInteractive -Command "' + psScript + '"',
                { encoding: 'utf8', timeout: 10000, windowsHide: true },
                (err, stdout) => err ? reject(err) : resolve(stdout.trim())
            );
        });

        const key = Buffer.from(result, 'base64');
        console.log('[BiliLogin] AES解密key获取成功 (' + key.length + '字节)');
        return key;
    } catch (e) {
        console.log('[BiliLogin] AES解密key获取失败:', e.message);
        return null;
    }
}

/** AES-256-GCM解密Cookie值（Chrome 80+） */
function _decryptAESGCM(encrypted, key) {
    const crypto = require('crypto');

    try {
        // 结构：v10/v20(3字节) + nonce(12字节) + 密文 + authTag(16字节)
        const nonce = encrypted.slice(3, 15);
        const ciphertext = encrypted.slice(15, encrypted.length - 16);
        const authTag = encrypted.slice(encrypted.length - 16);

        const decipher = crypto.createDecipheriv('aes-256-gcm', key, nonce);
        decipher.setAuthTag(authTag);

        return Buffer.concat([
            decipher.update(ciphertext),
            decipher.final(),
        ]).toString('utf8');
    } catch (e) {
        return null;
    }
}

/** DPAPI直接解密（旧版Chrome <80，异步） */
async function _decryptDPAPI(encrypted) {
    const { exec } = require('child_process');

    try {
        const b64 = encrypted.toString('base64');
        const psScript = [
            'Add-Type -AssemblyName System.Security',
            '$enc = [Convert]::FromBase64String("' + b64 + '")',
            '$dec = [System.Security.Cryptography.ProtectedData]::Unprotect($enc, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
            '[System.Text.Encoding]::UTF8.GetString($dec)',
        ].join('; ');

        return await new Promise((resolve) => {
            exec(
                'powershell.exe -NoProfile -NonInteractive -Command "' + psScript + '"',
                { encoding: 'utf8', timeout: 10000, windowsHide: true },
                (err, stdout) => resolve(err ? null : stdout.trim())
            );
        });
    } catch {
        return null;
    }
}

// ------- 模式B：Chrome调试端口CDP -------

/** 模式B：通过CDP协议从已开调试端口的Chrome读cookie */
async function tryChromeCDP() {
    const http = require('http');

    // 检查常用调试端口
    for (const port of [9222, 9229]) {
        try {
            const cookies = await cdpGetCookies(port);
            if (cookies && cookies.SESSDATA) return cookies;
        } catch {}
    }
    return null;
}

/** 通过CDP协议读B站cookie */
function cdpGetCookies(port) {
    return new Promise((resolve) => {
        const http = require('http');
        const req = http.get(`http://127.0.0.1:${port}/json/version`, (res) => {
            let data = '';
            res.on('data', c => data += c);
            res.on('end', () => {
                try {
                    const info = JSON.parse(data);
                    const wsUrl = info.webSocketDebuggerUrl;
                    if (!wsUrl) { resolve(null); return; }

                    let WebSocketClass;
                    try { WebSocketClass = require('ws'); } catch { resolve(null); return; }

                    const ws = new WebSocketClass(wsUrl);
                    ws.on('open', () => {
                        ws.send(JSON.stringify({
                            id: 1, method: 'Network.getCookies',
                            params: { urls: ['https://www.bilibili.com'] }
                        }));
                    });
                    ws.on('message', (msg) => {
                        try {
                            const result = JSON.parse(msg);
                            if (result.id === 1 && result.result) {
                                const cookies = {};
                                for (const c of result.result.cookies || []) {
                                    if (BILI_COOKIE_NAMES.includes(c.name)) {
                                        cookies[c.name] = c.value;
                                    }
                                }
                                ws.close();
                                resolve(cookies.SESSDATA ? cookies : null);
                            }
                        } catch { resolve(null); }
                    });
                    ws.on('error', () => resolve(null));
                    setTimeout(() => { try { ws.close(); } catch {} resolve(null); }, 5000);
                } catch { resolve(null); }
            });
        });
        req.on('error', () => resolve(null));
        req.setTimeout(3000, () => { req.destroy(); resolve(null); });
    });
}

// ------- 模式C：Electron窗口手动登录 -------

/** 模式C：Electron窗口登录B站（需要用户手动操作） */
function openBiliLoginWindow() {
    return new Promise((resolve) => {
        const loginWin = new BrowserWindow({
            width: 1000,
            height: 700,
            title: 'B站登录 - White Salary',
            webPreferences: {
                nodeIntegration: false,
                contextIsolation: true,
            },
        });

        loginWin.loadURL('https://passport.bilibili.com/login');

        // 监听URL变化——登录成功后bilibili会跳转到首页
        loginWin.webContents.on('did-navigate', async (event, url) => {
            if (url.includes('bilibili.com') && !url.includes('passport.bilibili.com/login')) {
                try {
                    const cookies = await loginWin.webContents.session.cookies.get({
                        domain: '.bilibili.com'
                    });
                    const result = {};
                    for (const c of cookies) {
                        if (BILI_COOKIE_NAMES.includes(c.name)) {
                            result[c.name] = c.value;
                        }
                    }
                    if (result.SESSDATA) {
                        console.log('[BiliLogin] 模式C成功！用户已登录');
                        loginWin.close();
                        resolve({ success: true, method: 'electron_window', cookies: result });
                        return;
                    }
                } catch (e) {
                    console.log('[BiliLogin] 读取cookie失败:', e.message);
                }
            }
        });

        loginWin.on('closed', () => {
            resolve({ success: false, message: '登录窗口已关闭' });
        });

        setTimeout(() => {
            if (!loginWin.isDestroyed()) {
                loginWin.close();
                resolve({ success: false, message: '登录超时(5分钟)' });
            }
        }, 300000);
    });
}

// ============================================================
// QQ空间Cookie自动获取（Electron窗口登录）
// ============================================================

ipcMain.handle('qzone-login', async () => {
    console.log('[QZoneLogin] Opening QZone login window...');

    return new Promise((resolve) => {
        const loginWin = new BrowserWindow({
            width: 1000,
            height: 700,
            title: 'QQ Space Login - White Salary',
            webPreferences: {
                nodeIntegration: false,
                contextIsolation: true,
            },
        });

        loginWin.loadURL('https://user.qzone.qq.com');

        // Monitor navigation - after login, QZone redirects to user page
        loginWin.webContents.on('did-navigate', async (event, url) => {
            console.log('[QZoneLogin] Navigated to:', url);

            // QZone login success: URL contains user.qzone.qq.com/{qqnumber}
            if (url.includes('user.qzone.qq.com') && !url.includes('login') && !url.includes('connect')) {
                try {
                    // Get ALL cookies (no domain filter)
                    const allCookies = await loginWin.webContents.session.cookies.get({});

                    const result = {};
                    for (const c of allCookies) {
                        // Debug: log relevant cookies
                        if (['uin', 'skey', 'p_skey', 'p_uin', 'pt_login_sig'].includes(c.name)) {
                            console.log(`[QZoneLogin] Cookie: ${c.name}=${c.value.substring(0,15)}... domain=${c.domain}`);
                        }

                        if (c.name === 'uin' && !result.uin) {
                            result.uin = c.value.replace(/^o/, '');
                        }
                        if (c.name === 'skey' && !result.skey) {
                            result.skey = c.value;
                        }
                        // p_skey: prefer qzone domain, fallback to any
                        if (c.name === 'p_skey') {
                            if (c.domain.includes('qzone')) {
                                result.p_skey = c.value;  // qzone domain takes priority
                            } else if (!result.p_skey) {
                                result.p_skey = c.value;  // fallback
                            }
                        }
                    }

                    console.log('[QZoneLogin] Extracted:', JSON.stringify({
                        uin: result.uin || '',
                        skey: result.skey ? result.skey.substring(0,8) + '...' : '',
                        p_skey: result.p_skey ? result.p_skey.substring(0,8) + '...' : '',
                    }));

                    if (result.uin && result.skey && result.p_skey) {
                        console.log('[QZoneLogin] Success! QQ:', result.uin);
                        loginWin.close();
                        resolve({ success: true, cookies: result });
                        return;
                    } else {
                        console.log('[QZoneLogin] Cookies incomplete, waiting...');
                    }
                } catch (e) {
                    console.log('[QZoneLogin] Read cookie failed:', e.message);
                }
            }
        });

        // Also check on page load finish
        loginWin.webContents.on('did-finish-load', async () => {
            const url = loginWin.webContents.getURL();
            if (url.includes('user.qzone.qq.com') && !url.includes('login')) {
                // Trigger the same check
                loginWin.webContents.emit('did-navigate', null, url);
            }
        });

        loginWin.on('closed', () => {
            resolve({ success: false, message: 'Login window closed' });
        });

        // 5 min timeout
        setTimeout(() => {
            if (!loginWin.isDestroyed()) {
                loginWin.close();
                resolve({ success: false, message: 'Login timeout' });
            }
        }, 300000);
    });
});

// ============================================================
// 应用生命周期
// ============================================================

app.whenReady().then(() => {
    createWindow();

    // 注册全局快捷键
    // F12: 打开开发者工具（调试用）
    globalShortcut.register('F12', () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.toggleDevTools();
        }
    });

    // Ctrl+Shift+R: 重新加载页面
    globalShortcut.register('CommandOrControl+Shift+R', () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.reload();
        }
    });

    // Ctrl+Q: 退出应用
    globalShortcut.register('CommandOrControl+Q', () => {
        app.quit();
    });

    // Ctrl+,: 打开设置面板
    globalShortcut.register('CommandOrControl+,', () => {
        openSettingsWindow();
    });

    console.log('White Salary desktop app started');
    console.log(`Backend: ${BACKEND_HTTP_URL}`);
    console.log('Hotkeys: F12=DevTools, Ctrl+Shift+R=Reload, Ctrl+Q=Quit, Ctrl+,=Settings');
});

// 所有窗口关闭时退出（Windows/Linux行为）
app.on('window-all-closed', () => {
    globalShortcut.unregisterAll();
    app.quit();
});

// macOS: 点击dock图标时重新创建窗口
app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
    }
});
