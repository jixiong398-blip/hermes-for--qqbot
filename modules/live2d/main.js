const { app, BrowserWindow, screen, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const url = require('url');

let mainWindow = null;
let httpServer = null;
const HTTP_PORT = 19919;
const ASSETS_DIR = path.join(__dirname, 'assets');

function loadConfig() {
  const confPath = path.join(__dirname, 'conf.json');
  try {
    if (fs.existsSync(confPath)) return JSON.parse(fs.readFileSync(confPath, 'utf8'));
  } catch (_) {}
  return {};
}

function startHttpServer() {
  httpServer = http.createServer((req, res) => {
    const parsed = url.parse(req.url);

    // POST /cmd — receive Live2D commands and forward to renderer
    if (parsed.pathname === '/cmd' && req.method === 'POST') {
      let body = '';
      req.on('data', c => body += c);
      req.on('end', () => {
        try {
          const data = JSON.parse(body);
          console.log('[main] HTTP /cmd received:', JSON.stringify(data).slice(0, 80));
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('live2d-cmd', data);
            console.log('[main] IPC sent to renderer');
          }
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: true }));
        } catch (e) {
          res.writeHead(400);
          res.end(JSON.stringify({ error: e.message }));
        }
      });
      return;
    }

    // GET /screenshot — renderer captures canvas PNG
    if (parsed.pathname === '/screenshot' && req.method === 'GET') {
      let resolved = false;
      const timer = setTimeout(() => { if (!resolved) { resolved = true; res.writeHead(500); res.end(); } }, 5000);
      const onShot = (_e, dataUrl) => {
        if (resolved) return; resolved = true; clearTimeout(timer);
        try {
          const base64 = dataUrl.replace(/^data:image\/png;base64,/, '');
          const buf = Buffer.from(base64, 'base64');
          res.writeHead(200, { 'Content-Type': 'image/png', 'Content-Length': buf.length });
          res.end(buf);
        } catch (_) { res.writeHead(500); res.end(); }
      };
      ipcMain.once('screenshot-data', onShot);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('live2d-cmd', { type: 'screenshot_request' });
      }
      return;
    }

    const filePath = path.join(ASSETS_DIR, parsed.pathname.replace(/^\/assets\//, ''));
    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      res.writeHead(404); res.end(); return;
    }
    const mimeMap = { '.json': 'application/json', '.png': 'image/png', '.moc': 'application/octet-stream', '.mtn': 'application/octet-stream', '.js': 'application/javascript', '.exp.json': 'application/json' };
    res.writeHead(200, { 'Content-Type': mimeMap[path.extname(filePath).toLowerCase()] || 'application/octet-stream', 'Access-Control-Allow-Origin': '*' });
    fs.createReadStream(filePath).pipe(res);
  });
  httpServer.listen(HTTP_PORT, '127.0.0.1', () => console.log(`Assets: http://127.0.0.1:${HTTP_PORT}/assets/`));
}

function createWindow() {
  const config = loadConfig();
  const costume = config.costume || 'live_event_297_ur';
  const wsPort = config.wsPort || 9190;
  const W = config.width || 600;
  const H = config.height || 800;

  mainWindow = new BrowserWindow({
    width: W, height: H,
    transparent: true, frame: false, resizable: true,
    hasShadow: false, backgroundColor: '#00000000',
    title: '長崎素世 - Live2D',
    webPreferences: {
      nodeIntegration: false, contextIsolation: true,
      sandbox: false, preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.setAlwaysOnTop(true, 'screen-saver');
  mainWindow.setVisibleOnAllWorkspaces(true);
  mainWindow.setIgnoreMouseEvents(true, { forward: true });

  const display = screen.getPrimaryDisplay();
  mainWindow.setPosition(display.workAreaSize.width - W - 10, display.workAreaSize.height - H - 10);

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'), {
    query: { character: 'soyo', costume, wsPort: String(wsPort) },
  });

  mainWindow.webContents.on('console-message', (event, level, message) => {
    console.log(`[renderer] ${message}`);
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(() => { startHttpServer(); createWindow(); });
app.on('window-all-closed', () => { if (httpServer) httpServer.close(); app.quit(); });
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
