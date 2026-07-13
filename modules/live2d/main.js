// Live2D Desktop Pet - Main Process
// Architecture aligned with CucumberVPet v1.1.4

const { app, BrowserWindow, screen, ipcMain, Tray, Menu, nativeImage, globalShortcut, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const https = require('https');
const url = require('url');
const koffi = require('koffi');

// ─── Native Windows Drag (matches WPF DragMove for frameless windows) ────────
const _user32 = koffi.load('user32.dll');
const _ReleaseCapture = _user32.func('bool ReleaseCapture()');
const _SendMessageW = _user32.func('intptr_t SendMessageW(intptr_t, uint32_t, intptr_t, intptr_t)');
const WM_SYSCOMMAND = 0x0112;
const SC_MOVE = 0xF010;
const HTCAPTION = 2;

// ─── Constants ──────────────────────────────────────────────────────────────
let mainWindow = null, settingsWindow = null, downloadWindow = null, httpServer = null, tray = null, hermesWs = null;
const HTTP_PORT = 19919;
const ASSETS_DIR = path.join(__dirname, 'assets');
const FIGURE_DIR = path.join(ASSETS_DIR, 'figure');
const MODELS_DIR = path.join(ASSETS_DIR, 'models');
const STATE_FILE = path.join(__dirname, 'window_state.json');
const BASE_W = 460, BASE_H = 680;

// ─── App State ──────────────────────────────────────────────────────────────
let appState = {
  clickThrough: false,
  alwaysOnTop: true,
  mouseFollow: true,
  maxFPS: 0,
  sizePercent: 100,
  followFps: 30,
  rememberPosition: true,
  streamingMode: false,
};

let _rendererReady = false;
let _hitTestCache = { x: -9999, y: -9999, hit: false };
let _hitTestPending = false;

// ─── Config & State Persistence ─────────────────────────────────────────────
function loadConfig() {
  try { return JSON.parse(fs.readFileSync(path.join(__dirname, 'conf.json'), 'utf8')); }
  catch { return {}; }
}

function loadWindowState() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); }
  catch { return {}; }
}

function saveWindowState(state) {
  try {
    const cur = loadWindowState();
    fs.writeFileSync(STATE_FILE, JSON.stringify({ ...cur, ...state }, null, 2));
  } catch {}
}

// ─── Model Discovery ────────────────────────────────────────────────────────
function scanModels() {
  const models = {};
  if (!fs.existsSync(MODELS_DIR)) return models;
  for (const char of fs.readdirSync(MODELS_DIR)) {
    const charDir = path.join(MODELS_DIR, char);
    if (!fs.statSync(charDir).isDirectory()) continue;
    const costumes = [];
    for (const entry of fs.readdirSync(charDir)) {
      const entryPath = path.join(charDir, entry);
      if (!fs.statSync(entryPath).isDirectory()) continue;
      if (fs.readdirSync(entryPath).some(f => f.endsWith('.model3.json'))) costumes.push(entry);
    }
    if (costumes.length > 0) models[char] = costumes;
  }
  return models;
}

function scanModelsCategorized() {
  const config = loadConfig();
  const all = scanModels();
  const subChars = new Set(config.subCharacters || []);
  const main = {}, sub = {};
  for (const [c, costumes] of Object.entries(all)) {
    if (subChars.has(c)) sub[c] = costumes; else main[c] = costumes;
  }
  return { main, sub };
}

// ─── HTTP Server ────────────────────────────────────────────────────────────
function startHttpServer() {
  const mimeMap = {
    '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg',
    '.moc3': 'application/octet-stream', '.js': 'application/javascript',
    '.css': 'text/css', '.html': 'text/html',
  };

  httpServer = http.createServer((req, res) => {
    const parsed = url.parse(req.url, true);
    const headers = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' };

    if (req.method === 'OPTIONS') { res.writeHead(204, headers); res.end(); return; }

    // POST /cmd
    if (parsed.pathname === '/cmd' && req.method === 'POST') {
      let body = '';
      req.on('data', c => body += c);
      req.on('end', () => {
        try {
          const data = JSON.parse(body);
          if (mainWindow && !mainWindow.isDestroyed()) {
            if (data.type === 'screenshot_request') {
              mainWindow.webContents.executeJavaScript('document.getElementById(\"stage\").toDataURL(\"image/png\")').then(d => {
                ipcMain.emit('screenshot-data', null, d);
              }).catch(() => {});
            } else if (data.type === 'switch_model') {
              // Convert switch_model → loadModel with correct URL
              const { character, costume } = data;
              if (character && costume) {
                const modelDir = path.join(MODELS_DIR, character, costume);
                if (fs.existsSync(modelDir)) {
                  const modelFile = fs.readdirSync(modelDir).find(f => f.endsWith('.model3.json')) || '';
                  if (modelFile) {
                    const modelUrl = 'http://127.0.0.1:' + HTTP_PORT + '/assets/models/' + character + '/' + costume + '/' + modelFile;
                    console.log('[Live2D] Switch via /cmd: ' + modelUrl);
                    mainWindow.webContents.send('webview-message', { type: 'loadModel', url: modelUrl });
                  }
                }
              }
            } else {
              mainWindow.webContents.send('webview-message', data);
            }
          }
          res.writeHead(200, { ...headers, 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: true }));
        } catch (e) { res.writeHead(400, headers); res.end(JSON.stringify({ error: e.message })); }
      });
      return;
    }

    // GET /screenshot
    if (parsed.pathname === '/screenshot' && req.method === 'GET') {
      let resolved = false;
      const timer = setTimeout(() => { if (!resolved) { resolved = true; res.writeHead(500); res.end(); } }, 5000);
      ipcMain.once('screenshot-data', (_e, dataUrl) => {
        if (resolved) return; resolved = true; clearTimeout(timer);
        try {
          const buf = Buffer.from(dataUrl.replace(/^data:image\/png;base64,/, ''), 'base64');
          res.writeHead(200, { 'Content-Type': 'image/png', 'Content-Length': buf.length });
          res.end(buf);
        } catch { res.writeHead(500); res.end(); }
      });
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.executeJavaScript('document.getElementById(\"stage\").toDataURL(\"image/png\")').then(dataUrl => {
          if (resolved) return; resolved = true; clearTimeout(timer);
          try {
            const buf = Buffer.from(dataUrl.replace(/^data:image\/png;base64,/, ''), 'base64');
            res.writeHead(200, { 'Content-Type': 'image/png', 'Content-Length': buf.length });
            res.end(buf);
          } catch { res.writeHead(500); res.end(); }
        }).catch(() => { if (!resolved) { resolved = true; res.writeHead(500); res.end(); } });
      }
      return;
    }

    // GET /api/models
    if (parsed.pathname === '/api/models') {
      res.writeHead(200, { ...headers, 'Content-Type': 'application/json' });
      res.end(JSON.stringify(scanModels()));
      return;
    }

    // GET /api/model?character=X&costume=Y -> returns model3.json URL
    if (parsed.pathname === '/api/model') {
      const char = parsed.query.character;
      const costume = parsed.query.costume;
      const modelDir = path.join(MODELS_DIR, char, costume);
      let modelFile = '';
      if (fs.existsSync(modelDir) && fs.statSync(modelDir).isDirectory()) {
        modelFile = fs.readdirSync(modelDir).find(f => f.endsWith('.model3.json')) || '';
      }
      res.writeHead(200, { ...headers, 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ url: modelFile ? `http://127.0.0.1:${HTTP_PORT}/assets/models/${char}/${costume}/${modelFile}` : '' }));
      return;
    }

    // Static files
    let filePath = null;
    if (parsed.pathname.startsWith('/assets/models/')) filePath = path.join(MODELS_DIR, parsed.pathname.replace('/assets/models/', ''));
    else if (parsed.pathname.startsWith('/assets/figure/')) filePath = path.join(FIGURE_DIR, parsed.pathname.replace('/assets/figure/', ''));
    else if (parsed.pathname.startsWith('/assets/')) filePath = path.join(ASSETS_DIR, parsed.pathname.replace(/^\/assets\//, ''));

    if (!filePath || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      if (filePath && filePath.endsWith('model3.json')) {
        const dir = path.dirname(filePath);
        if (fs.existsSync(dir) && fs.statSync(dir).isDirectory()) {
          const alt = fs.readdirSync(dir).find(f => f.endsWith('.model3.json'));
          if (alt) filePath = path.join(dir, alt);
        }
      }
      if (!filePath || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
        res.writeHead(404, headers); res.end('Not Found'); return;
      }
    }

    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, { ...headers, 'Content-Type': mimeMap[ext] || 'application/octet-stream' });
    fs.createReadStream(filePath).pipe(res);
  });

  httpServer.listen(HTTP_PORT, '127.0.0.1', () => {
    console.log(`[Live2D] HTTP server on http://127.0.0.1:${HTTP_PORT}/`);
    console.log(`[Live2D] Models: ${JSON.stringify(Object.keys(scanModels()))}`);
  });
}

// ─── Apply State ────────────────────────────────────────────────────────────
function applyAppState() {
  if (!mainWindow || mainWindow.isDestroyed()) return;

  // Click-through: ON = all events pass through, OFF = window captures events
  mainWindow.setIgnoreMouseEvents(appState.clickThrough, { forward: true });

  // Streaming mode: show in taskbar so OBS can detect the window
  if (appState.streamingMode) {
    mainWindow.setSkipTaskbar(false);
    mainWindow.setTitle('Hermes Live2D (Stream)');
  } else {
    mainWindow.setSkipTaskbar(true);
    mainWindow.setTitle('Live2D Desktop Pet');
  }

  mainWindow.setAlwaysOnTop(appState.alwaysOnTop, 'screen-saver');

  if (mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
    mainWindow.webContents.send('app-state', appState);
  }
}

// ─── Settings Window ─────────────────────────────────────────────────────────
function createSettingsWindow() {
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.focus();
    return;
  }
  settingsWindow = new BrowserWindow({
    width: 500, height: 620, resizable: false, frame: false,
    transparent: true, backgroundColor: '#00000000',
    parent: mainWindow, modal: false,
    webPreferences: { nodeIntegration: true, contextIsolation: false, sandbox: false },
  });
  settingsWindow.loadFile(path.join(__dirname, 'settings.html'));
  settingsWindow.on('closed', () => { settingsWindow = null; });
  settingsWindow.setMenu(null);
}

// ─── Download Window ─────────────────────────────────────────────────────────
function createDownloadWindow() {
  if (downloadWindow && !downloadWindow.isDestroyed()) {
    downloadWindow.focus();
    return;
  }
  downloadWindow = new BrowserWindow({
    width: 560, height: 700, resizable: true, minWidth: 480, minHeight: 500,
    frame: false, transparent: true, backgroundColor: '#00000000',
    parent: mainWindow, modal: false,
    webPreferences: { nodeIntegration: true, contextIsolation: false, sandbox: false },
  });
  downloadWindow.loadFile(path.join(__dirname, 'download.html'));
  downloadWindow.on('closed', () => { downloadWindow = null; });
  downloadWindow.setMenu(null);
}

// ─── Settings IPC (invoke → handle for async support) ────────────────────────
ipcMain.handle('settings-load', async () => {
  const config = loadConfig();
  return {
    ...appState,
    character: config.character || 'rana',
    costume: config.costume || 'live_01',
    followFps: appState.followFps,
    hermesPort: config.hermesPort || 9190,
    startupSound: config.startupSound !== false,
    audioVolume: config.audioVolume ?? 60,
    version: config.version || '1.1.4',
  };
});

ipcMain.handle('settings-set', async (_e, key, value) => {
  const config = loadConfig();
  switch (key) {
    case 'alwaysOnTop': appState.alwaysOnTop = value; break;
    case 'clickThrough': appState.clickThrough = value; break;
    case 'streamingMode': appState.streamingMode = value; break;
    case 'mouseFollow': appState.mouseFollow = value; if (!value) stopMouseFollow(); else if (appState.followFps > 0) startMouseFollow(); break;
    case 'rememberPosition': appState.rememberPosition = value; break;
    case 'startupSound': config.startupSound = value; fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2)); break;
    case 'audioVolume': config.audioVolume = value; fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2)); break;
    case 'maxFPS': appState.maxFPS = value; break;
    case 'followFps': appState.followFps = value; config.followFps = value; fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2)); break;
    case 'sizePercent': appState.sizePercent = value; break;
    case 'hermesPort': config.hermesPort = value; fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2)); break;
  }
  applyAppState();
  saveWindowState(appState);
  if (tray) tray.setContextMenu(buildTrayMenu());
  if (hermesWs) hermesWs.broadcast({ type: 'state', settings: appState });
  return { ok: true };
});

ipcMain.handle('settings-reset', async () => {
  appState.clickThrough = false; appState.alwaysOnTop = true; appState.mouseFollow = true;
  appState.maxFPS = 0; appState.sizePercent = 100; appState.followFps = 30;
  appState.rememberPosition = true; appState.streamingMode = false;
  applyAppState(); saveWindowState(appState);
  const config = loadConfig();
  config.startupSound = true; config.audioVolume = 60; config.followFps = 30;
  fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2));
  if (tray) tray.setContextMenu(buildTrayMenu());
  return { ok: true };
});

ipcMain.handle('ws-restart', async () => {
  if (hermesWs) { hermesWs.destroy(); hermesWs = null; }
  try {
    const { startHermesWsServer } = require('./hermes-ws');
    hermesWs = startHermesWsServer(mainWindow, httpServer, loadConfig());
    return { ok: true };
  } catch (e) { return { error: e.message }; }
});

// ─── Legacy Settings IPC (on/sendSync for backward compat) ────────────────────
ipcMain.on('get-settings', (e) => {
  const config = loadConfig();
  e.returnValue = {
    ...appState,
    character: config.character || 'mutsumi',
    costume: config.costume || 'casual_spring_01',
    followFps: appState.followFps,
    hermesPort: config.hermesPort || 9190,
    startupSound: config.startupSound !== false,
    audioVolume: config.audioVolume ?? 60,
    version: config.version || '1.1.4',
  };
});

ipcMain.on('set-setting', (_e, key, value) => {
  const config = loadConfig();
  switch (key) {
    case 'alwaysOnTop': appState.alwaysOnTop = value; break;
    case 'clickThrough': appState.clickThrough = value; break;
    case 'streamingMode': appState.streamingMode = value; break;
    case 'mouseFollow': appState.mouseFollow = value; if (!value) stopMouseFollow(); else if (appState.followFps > 0) startMouseFollow(); break;
    case 'rememberPosition': appState.rememberPosition = value; break;
    case 'startupSound': config.startupSound = value; fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2)); break;
    case 'audioVolume': config.audioVolume = value; fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2)); break;
    case 'maxFPS': appState.maxFPS = value; break;
    case 'followFps': appState.followFps = value; config.followFps = value; fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2)); break;
    case 'sizePercent': appState.sizePercent = value; break;
    case 'hermesPort': config.hermesPort = value; fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2)); break;
  }
  applyAppState();
  saveWindowState(appState);
  if (tray) tray.setContextMenu(buildTrayMenu());
  // Sync to Hermes
  if (hermesWs) hermesWs.broadcast({ type: 'state', settings: appState });
});

ipcMain.on('reset-settings', () => {
  appState.clickThrough = false;
  appState.alwaysOnTop = true;
  appState.mouseFollow = true;
  appState.maxFPS = 0;
  appState.sizePercent = 100;
  appState.followFps = 30;
  appState.rememberPosition = true;
  appState.streamingMode = false;
  applyAppState();
  saveWindowState(appState);
  const config = loadConfig();
  config.startupSound = true;
  config.audioVolume = 60;
  config.followFps = 30;
  fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2));
  if (tray) tray.setContextMenu(buildTrayMenu());
});

ipcMain.on('restart-ws', () => {
  if (hermesWs) { hermesWs.close(); hermesWs = null; }
  const config = loadConfig();
  try {
    const { startHermesWsServer } = require('./hermes-ws');
    hermesWs = startHermesWsServer(mainWindow, httpServer, config);
    console.log('[Live2D] Hermes WS restarted');
  } catch (e) { console.error('[Live2D] Failed to restart WS:', e.message); }
});

// ─── Download IPC ────────────────────────────────────────────────────────────
ipcMain.on('download-get-roles', async (e) => {
  try {
    const https_mod = require('https');
    const manifestUrl = 'https://cucumbervpet.sevenvoxel.com/manifest.json';
    const data = await new Promise((resolve, reject) => {
      https_mod.get(manifestUrl, (res) => {
        let body = '';
        res.on('data', c => body += c);
        res.on('end', () => resolve(body));
      }).on('error', reject);
    });
    const manifest = JSON.parse(data.startsWith('\uFEFF') ? data.slice(1) : data);
    const config = loadConfig();
    const mainChars = new Set(config.mainCharacters || []);
    const subChars = new Set(config.subCharacters || []);
    const modelsDir = path.join(__dirname, 'assets', 'models');

    const roles = (manifest.roles || []).map(r => {
      const parts = r.id.split('_');
      const char = parts[0];
      const costume = parts.slice(1).join('_');
      const category = mainChars.has(char) ? 'main' : subChars.has(char) ? 'sub' : 'other';
      const installed = fs.existsSync(path.join(modelsDir, char, costume)) &&
        fs.existsSync(path.join(modelsDir, char, costume)) &&
        fs.readdirSync(path.join(modelsDir, char, costume)).some(f => f.endsWith('.model3.json'));
      return {
        id: r.id, character: char, costume, displayName: r.displayName,
        url: r.url, sha256: r.sha256, size: r.size, category, installed,
      };
    });

    e.returnValue = roles;
  } catch (err) {
    console.error('[Live2D] download-get-roles error:', err.message);
    e.returnValue = { error: err.message };
  }
});

ipcMain.on('download-start', async (e, roleId, url) => {
  try {
    const downloadsDir = path.join(__dirname, 'downloads');
    if (!fs.existsSync(downloadsDir)) fs.mkdirSync(downloadsDir, { recursive: true });
    const filePath = path.join(downloadsDir, roleId + '.cvpkg');

    const https_mod = require('https');
    const file = fs.createWriteStream(filePath);
    let downloaded = 0;

    await new Promise((resolve, reject) => {
      https_mod.get(url, (res) => {
        const total = parseInt(res.headers['content-length'] || '0');
        res.on('data', (chunk) => {
          downloaded += chunk.length;
          if (e.sender && !e.sender.isDestroyed()) {
            e.sender.send('download-progress', { roleId, percent: total > 0 ? Math.round(downloaded / total * 100) : 0, downloaded, total, status: 'downloading' });
          }
        });
        res.pipe(file);
        file.on('finish', () => { file.close(); resolve(); });
      }).on('error', reject);
    });

    if (e.sender && !e.sender.isDestroyed()) {
      e.sender.send('download-progress', { roleId, percent: 100, status: 'done', filePath });
    }
  } catch (err) {
    console.error('[Live2D] download-start error:', err.message);
    if (e.sender && !e.sender.isDestroyed()) {
      e.sender.send('download-progress', { roleId, percent: 0, status: 'error', error: err.message });
    }
  }
});

ipcMain.on('download-install', async (e, roleId) => {
  try {
    const parts = roleId.split('_');
    const character = parts[0];
    const costume = parts.slice(1).join('_');
    const cvpkgPath = path.join(__dirname, 'downloads', roleId + '.cvpkg');
    const outputDir = path.join(__dirname, 'assets', 'models', character, costume);
    const decryptScript = path.join(process.env.USERPROFILE, '.config', 'opencode', 'skills', 'cvpkg-decrypt', 'decrypt_cvpkg.py');

    if (!fs.existsSync(cvpkgPath)) {
      if (e.sender) e.sender.send('download-progress', { roleId, status: 'error', error: 'Download file not found' });
      return;
    }

    const { execFile } = require('child_process');
    if (e.sender) e.sender.send('download-progress', { roleId, status: 'installing' });

    await new Promise((resolve, reject) => {
      execFile('python', [decryptScript, '-o', outputDir, cvpkgPath], (err, stdout, stderr) => {
        if (err) reject(new Error(stderr || err.message));
        else resolve(stdout);
      });
    });

    if (e.sender) e.sender.send('download-progress', { roleId, status: 'installed', character, costume });
    if (tray) tray.setContextMenu(buildTrayMenu());
    // Notify Hermes
    if (hermesWs) hermesWs.broadcast({ type: 'modelInstalled', character, costume, roleId });
  } catch (err) {
    console.error('[Live2D] download-install error:', err.message);
    if (e.sender) e.sender.send('download-progress', { roleId, status: 'error', error: err.message });
  }
});

// ─── Update Check ────────────────────────────────────────────────────────────
async function checkForUpdates(silent = true) {
  try {
    const https_mod = require('https');
    const manifestUrl = 'https://cucumbervpet.sevenvoxel.com/manifest.json';
    // In production, this would check a version endpoint
    // For now, check if the manifest has changed since last fetch
    console.log('[Live2D] Update check (placeholder)');
    if (!silent) {
      dialog.showMessageBox(mainWindow, {
        type: 'info', title: '检查更新',
        message: '当前版本 v1.1.4 已是最新',
        detail: 'CucumberVPet v1.1.4 (port from WPF to Electron)',
      });
    }
  } catch (e) { console.error('[Live2D] Update check error:', e.message); }
}

// ─── Mouse Follow (cursor tracking + look + hit-test cache) ─────────────────
let mouseFollowTimer = null;

function startMouseFollow() {
  if (mouseFollowTimer || appState.followFps <= 0) return;
  const interval = Math.round(1000 / appState.followFps);
  console.log(`[Live2D] Mouse follow @ ${appState.followFps}fps`);

  mouseFollowTimer = setInterval(() => {
    if (!mainWindow || mainWindow.isDestroyed() || !appState.mouseFollow) return;

    const mousePos = screen.getCursorScreenPoint();
    const bounds = mainWindow.getBounds();
    const cx = bounds.x + bounds.width / 2;
    const cy = bounds.y + bounds.height / 2;
    const maxDist = Math.max(bounds.width, bounds.height) / 2;
    const lookX = Math.max(-1, Math.min(1, (mousePos.x - cx) / maxDist));
    const lookY = Math.max(-1, Math.min(1, (mousePos.y - cy) / maxDist));

    if (mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
      mainWindow.webContents.send('webview-message', { type: 'look', x: lookX, y: lookY, enabled: true });
    }

    // Cache hit-test result for WM_NCHITTEST
    if (_rendererReady && !_hitTestPending) {
      const lx = mousePos.x - bounds.x;
      const ly = mousePos.y - bounds.y;
      if (lx >= 0 && ly >= 0 && lx < bounds.width && ly < bounds.height) {
        _hitTestPending = true;
        mainWindow.webContents.executeJavaScript(
          `window.hermesHitTest(${Math.round(lx)}, ${Math.round(bounds.height - ly)})`
        ).then(hit => {
          _hitTestPending = false;
          _hitTestCache = { x: lx, y: ly, hit };
        }).catch(() => { _hitTestPending = false; _hitTestCache = { x: -9999, y: -9999, hit: false }; });
      } else {
        _hitTestCache = { x: -9999, y: -9999, hit: false };
      }
    }
  }, interval);
}

function stopMouseFollow() {
  if (mouseFollowTimer) { clearInterval(mouseFollowTimer); mouseFollowTimer = null; }
  _hitTestPending = false;
  _hitTestCache = { x: -9999, y: -9999, hit: false };
}

// ─── Tray Menu ──────────────────────────────────────────────────────────────
function buildTrayMenu() {
  const config = loadConfig();
  const curChar = config.character || 'mutsumi';
  const curCostume = config.costume || 'casual_spring_01';
  const cat = scanModelsCategorized();

  const charSubmenu = [];
  for (const [c, costumes] of Object.entries(cat.main)) {
    charSubmenu.push({ label: c, submenu: costumes.map(co => ({
      label: co, type: 'radio', checked: c === curChar && co === curCostume, click: () => switchModel(c, co)
    }))});
  }
  if (Object.keys(cat.sub).length > 0) {
    charSubmenu.push({ type: 'separator' });
    const subItems = [];
    for (const [c, costumes] of Object.entries(cat.sub)) {
      subItems.push({ label: c, submenu: costumes.map(co => ({
        label: co, type: 'radio', checked: c === curChar && co === curCostume, click: () => switchModel(c, co)
      }))});
    }
    charSubmenu.push({ label: '配角', submenu: subItems });
  }

  return Menu.buildFromTemplate([
    { label: 'Live2D 桌面宠物', enabled: false },
    { type: 'separator' },
{ label: `${appState.clickThrough ? '✓ ' : '   '}点击透过`, click: () => { appState.clickThrough = !appState.clickThrough; applyAppState(); saveWindowState({ clickThrough: appState.clickThrough }); if (tray) tray.setContextMenu(buildTrayMenu()); } },
    { label: `${appState.alwaysOnTop ? '✓ ' : '   '}窗口置顶`, click: () => { appState.alwaysOnTop = !appState.alwaysOnTop; applyAppState(); saveWindowState({ alwaysOnTop: appState.alwaysOnTop }); if (tray) tray.setContextMenu(buildTrayMenu()); } },
    { label: `${appState.mouseFollow ? '✓ ' : '   '}鼠标跟随`, click: () => { appState.mouseFollow = !appState.mouseFollow; applyAppState(); saveWindowState({ mouseFollow: appState.mouseFollow }); if (tray) tray.setContextMenu(buildTrayMenu()); } },
    { type: 'separator' },
    { label: '渲染帧率', submenu: [
      { label: '无限制', type: 'radio', checked: appState.maxFPS === 0, click: () => setFPS(0) },
      { label: '30', type: 'radio', checked: appState.maxFPS === 30, click: () => setFPS(30) },
      { label: '60', type: 'radio', checked: appState.maxFPS === 60, click: () => setFPS(60) },
      { label: '120', type: 'radio', checked: appState.maxFPS === 120, click: () => setFPS(120) },
      { label: '144', type: 'radio', checked: appState.maxFPS === 144, click: () => setFPS(144) },
    ]},
    { label: '跟随帧率', submenu: [
      { label: '关闭', type: 'radio', checked: appState.followFps === 0, click: () => setFollowFps(0) },
      { label: '15', type: 'radio', checked: appState.followFps === 15, click: () => setFollowFps(15) },
      { label: '30', type: 'radio', checked: appState.followFps === 30, click: () => setFollowFps(30) },
      { label: '60', type: 'radio', checked: appState.followFps === 60, click: () => setFollowFps(60) },
    ]},
    { label: '窗口大小', submenu: [
      { label: '50%', type: 'radio', checked: appState.sizePercent === 50, click: () => setWindowSize(50) },
      { label: '75%', type: 'radio', checked: appState.sizePercent === 75, click: () => setWindowSize(75) },
      { label: '100%', type: 'radio', checked: appState.sizePercent === 100, click: () => setWindowSize(100) },
      { label: '150%', type: 'radio', checked: appState.sizePercent === 150, click: () => setWindowSize(150) },
      { label: '200%', type: 'radio', checked: appState.sizePercent === 200, click: () => setWindowSize(200) },
      { label: '300%', type: 'radio', checked: appState.sizePercent === 300, click: () => setWindowSize(300) },
    ]},
    { type: 'separator' },
    { label: `${appState.streamingMode ? '✓ ' : '   '}直播模式`, click: () => { appState.streamingMode = !appState.streamingMode; applyAppState(); saveWindowState({ streamingMode: appState.streamingMode }); if (tray) tray.setContextMenu(buildTrayMenu()); } },
    { type: 'separator' },
    { label: '设置', click: () => createSettingsWindow() },
    { label: '下载模型', click: () => createDownloadWindow() },
    { label: '检查更新', click: () => checkForUpdates(false) },
    { type: 'separator' },
    { type: 'separator' },
    { label: '显示/隐藏', click: () => { if (mainWindow && !mainWindow.isDestroyed()) { mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show(); } } },
    { label: '重置位置', click: () => { if (mainWindow && !mainWindow.isDestroyed()) { const d = screen.getPrimaryDisplay(); const w = mainWindow.getBounds().width, h = mainWindow.getBounds().height; mainWindow.setPosition(d.workAreaSize.width - w - 10, d.workAreaSize.height - h - 10); } } },
    { type: 'separator' },
    { label: '退出', click: () => { if (httpServer) httpServer.close(); app.quit(); } },
  ]);
}

function switchModel(character, costume) {
  const config = loadConfig();
  config.character = character; config.costume = costume;
  fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2));
  if (mainWindow && !mainWindow.isDestroyed()) {
    // Find model file and send loadModel message (same as WPF host)
    const modelDir = path.join(MODELS_DIR, character, costume);
    let modelFile = '';
    if (fs.existsSync(modelDir) && fs.statSync(modelDir).isDirectory()) {
      modelFile = fs.readdirSync(modelDir).find(f => f.endsWith('.model3.json')) || '';
    }
    if (modelFile) {
      const modelUrl = 'http://127.0.0.1:' + HTTP_PORT + '/assets/models/' + character + '/' + costume + '/' + modelFile;
      mainWindow.webContents.send('webview-message', { type: 'loadModel', url: modelUrl });
      // Notify Hermes
      if (hermesWs) hermesWs.broadcast({ type: 'modelLoaded', character, costume });
    }
  }
  if (tray) tray.setContextMenu(buildTrayMenu());
}

function setFPS(fps) { appState.maxFPS = fps; applyAppState(); saveWindowState({ maxFPS: fps }); if (tray) tray.setContextMenu(buildTrayMenu()); }
function setFollowFps(fps) { appState.followFps = fps; const c = loadConfig(); c.followFps = fps; fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(c, null, 2)); stopMouseFollow(); if (fps > 0) startMouseFollow(); if (tray) tray.setContextMenu(buildTrayMenu()); }
function setWindowSize(percent) { appState.sizePercent = percent; const w = Math.round(BASE_W * percent / 100), h = Math.round(BASE_H * percent / 100); if (mainWindow && !mainWindow.isDestroyed()) { const [x, y] = mainWindow.getPosition(); mainWindow.setBounds({ x, y, width: w, height: h }); } saveWindowState({ sizePercent: percent }); if (tray) tray.setContextMenu(buildTrayMenu()); }

// ─── Window Creation ────────────────────────────────────────────────────────
function createWindow() {
  const config = loadConfig();
  let character = config.character || 'mutsumi';
  let costume = config.costume || 'casual_spring_01';
  const wsPort = config.wsPort || 9190;
  const W = Math.round(BASE_W * appState.sizePercent / 100);
  const H = Math.round(BASE_H * appState.sizePercent / 100);

  // Validate model
  const models = scanModels();
  if (!models[character] || !models[character].includes(costume)) {
    const first = Object.keys(models)[0];
    if (first) { character = first; costume = models[first][0]; config.character = character; config.costume = costume; fs.writeFileSync(path.join(__dirname, 'conf.json'), JSON.stringify(config, null, 2)); }
  }

  // Load saved state
  const saved = loadWindowState();
  appState.clickThrough = saved.clickThrough ?? false;
  appState.alwaysOnTop = saved.alwaysOnTop ?? true;
  appState.mouseFollow = saved.mouseFollow ?? true;
  appState.maxFPS = saved.maxFPS ?? 0;
  appState.sizePercent = saved.sizePercent ?? 100;
  appState.followFps = saved.followFps ?? (config.followFps || 30);
  appState.streamingMode = saved.streamingMode ?? false;

  mainWindow = new BrowserWindow({
    width: W, height: H,
    x: saved.x, y: saved.y,
    transparent: true, frame: false, resizable: true,
    hasShadow: false, backgroundColor: '#00000000',
    title: 'Live2D Desktop Pet', skipTaskbar: true,
    webPreferences: { nodeIntegration: false, contextIsolation: false, sandbox: false, preload: path.join(__dirname, 'preload.js') },
  });

  applyAppState();

  if (saved.x === undefined || saved.y === undefined) {
    const d = screen.getPrimaryDisplay();
    mainWindow.setPosition(d.workAreaSize.width - W - 10, d.workAreaSize.height - H - 10);
  }

  // Find actual model3.json filename
  const modelDir = path.join(MODELS_DIR, character, costume);
  let modelFile = '';
  if (fs.existsSync(modelDir)) {
    const found = fs.readdirSync(modelDir).find(f => f.endsWith('.model3.json'));
    if (found) modelFile = found;
  }
  const modelUrl = modelFile ? `${ASSETS_DIR.replace(/\\/g,'/')}/models/${character}/${costume}/${modelFile}` : '';
  const httpModelUrl = modelFile ? `http://127.0.0.1:${HTTP_PORT}/assets/models/${character}/${costume}/${modelFile}` : '';

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'), { query: { character, costume, wsPort: String(wsPort), modelUrl: httpModelUrl } });

  mainWindow.webContents.on('console-message', (_e, _l, msg) => {
    if (!msg || msg.includes('%c') || msg.includes('Security') || msg.includes('CSM') || msg.includes('pixi') || msg.includes('WebGL') || msg.trim() === '') return;
    console.log(`[renderer] ${msg}`);
  });

  // Position memory
  let moveTimer = null;
  mainWindow.on('move', () => { if (!appState.rememberPosition) return; if (moveTimer) clearTimeout(moveTimer); moveTimer = setTimeout(() => { const [x, y] = mainWindow.getPosition(); saveWindowState({ x, y }); }, 500); });
  mainWindow.on('resize', () => { if (!appState.rememberPosition) return; const [x, y] = mainWindow.getPosition(); const [w, h] = mainWindow.getSize(); saveWindowState({ x, y, width: w, height: h }); });

  mainWindow.on('close', (e) => { if (!app.isQuitting) { e.preventDefault(); mainWindow.hide(); } });
  mainWindow.on('closed', () => { mainWindow = null; });

  // Send model URL to renderer (same as WPF host sending loadModel message)
  mainWindow.webContents.on('did-finish-load', () => {
    console.log('[Live2D] Renderer page loaded');
    const modelDir = path.join(MODELS_DIR, character, costume);
    let modelFile = '';
    if (fs.existsSync(modelDir) && fs.statSync(modelDir).isDirectory()) {
      modelFile = fs.readdirSync(modelDir).find(f => f.endsWith('.model3.json')) || '';
    }
    if (modelFile) {
      const modelUrl = 'http://127.0.0.1:' + HTTP_PORT + '/assets/models/' + character + '/' + costume + '/' + modelFile;
      console.log('[Live2D] Sending loadModel: ' + modelUrl);
      mainWindow.webContents.send('webview-message', { type: 'loadModel', url: modelUrl });
      if (hermesWs) hermesWs.broadcast({ type: 'modelLoaded', character, costume });
    } else {
      console.log('[Live2D] No model3.json found in ' + modelDir);
    }
  });
  mainWindow.webContents.on('context-menu', (e) => e.preventDefault());

  startMouseFollow();
}

// ─── IPC Handlers ───────────────────────────────────────────────────────────
ipcMain.on('renderer-event', (_e, data) => {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (data.type === 'doubleClick') { appState.clickThrough = !appState.clickThrough; applyAppState(); saveWindowState({ clickThrough: appState.clickThrough }); }
});

ipcMain.on('renderer-ready', () => { _rendererReady = true; console.log('[Live2D] Renderer ready'); });

ipcMain.on('right-click', (_e, data) => {
  if (!mainWindow || mainWindow.isDestroyed() || !data.hit) return;
  buildTrayMenu().popup({ window: mainWindow, x: Math.round(data.x), y: Math.round(data.y) });
});

// ─── IPC: Native drag (sendSync, koffi WM_SYSCOMMAND SC_MOVE) ──────────────
// Pixel-level drag: renderer.js gates with isCharacterHit() before post("drag").
// WM_SYSCOMMAND SC_MOVE works for frameless windows (no WS_CAPTION needed).
// Coordinates DPI-scaled from CSS→physical pixels.
ipcMain.on('native-drag', (e, data) => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    try {
      const hwnd = mainWindow.getNativeWindowHandle().readInt32LE(0);
      const scale = screen.getPrimaryDisplay().scaleFactor || 1;
      const sx = Math.round(((data && data.screenX) ? data.screenX : screen.getCursorScreenPoint().x) * scale);
      const sy = Math.round(((data && data.screenY) ? data.screenY : screen.getCursorScreenPoint().y) * scale);
      const lParam = ((sy & 0xFFFF) << 16) | (sx & 0xFFFF);
      _ReleaseCapture();
      _SendMessageW(hwnd, WM_SYSCOMMAND, SC_MOVE | HTCAPTION, lParam);
    } catch (err) { console.error('[Live2D] drag error:', err.message); }
  }
  e.returnValue = true;
});

// ─── IPC: WebView2 message (renderer.js post() -> main process) ─────────────
ipcMain.on('webview-message', (e, data) => {
  if (!data || !data.type) { e.returnValue = true; return; }
  switch (data.type) {
    case 'ready':
      _rendererReady = true;
      console.log('[Live2D] Renderer ready');
      break;
    case 'contextMenu':
      if (mainWindow && !mainWindow.isDestroyed()) {
        // popup({window, x, y}) uses WINDOW-RELATIVE coordinates, not screen coordinates!
        const bounds = mainWindow.getBounds();
        const pos = screen.getCursorScreenPoint();
        const mx = pos.x - bounds.x;
        const my = pos.y - bounds.y;
        if (tray) tray.setContextMenu(buildTrayMenu());
        buildTrayMenu().popup({ window: mainWindow, x: mx, y: my });
      }
      break;
    case 'error':
      console.error('[Live2D] Renderer error:', data.message);
      break;
  }
  e.returnValue = true;
});

// ─── App Lifecycle ──────────────────────────────────────────────────────────
app.whenReady().then(() => {
  startHttpServer();
  createWindow();

  // Start Hermes WebSocket bridge (if hermes-ws.js exists)
  try {
    const { startHermesWsServer } = require('./hermes-ws');
    hermesWs = startHermesWsServer(mainWindow, httpServer, loadConfig());
  } catch (e) { console.log('[Live2D] Hermes WS not loaded:', e.message); }

  const iconPath = path.join(__dirname, 'icon.png');
  const trayIcon = fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
  tray = new Tray(trayIcon);
  tray.setToolTip('Live2D Desktop Pet');
  tray.setContextMenu(buildTrayMenu());
  tray.on('click', () => { if (mainWindow && !mainWindow.isDestroyed()) { mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show(); } });

  globalShortcut.register('CommandOrControl+Shift+L', () => { appState.clickThrough = !appState.clickThrough; applyAppState(); saveWindowState({ clickThrough: appState.clickThrough }); });
});

app.on('window-all-closed', (e) => e.preventDefault());
app.on('before-quit', () => { app.isQuitting = true; if (hermesWs) hermesWs.destroy(); if (httpServer) httpServer.close(); stopMouseFollow(); globalShortcut.unregisterAll(); });
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); else if (mainWindow) mainWindow.show(); });
