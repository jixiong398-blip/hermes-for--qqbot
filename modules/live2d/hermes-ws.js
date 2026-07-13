// Hermes WebSocket Bridge - Control port for Hermes QQ Bot
// Exposes Live2D controls via WebSocket for remote operation.
//
// Attaches to the existing HTTP server on path /hermes (no extra port needed).
// Uses raw Node.js WebSocket protocol (RFC 6455) — zero dependencies.
//
// Usage in main.js:
//   const { startHermesWsServer } = require('./hermes-ws');
//   const hermesWs = startHermesWsServer(mainWindow, httpServer, loadConfig(), {
//     switchModel, setWindowSize, setFPS, setFollowFps,
//     saveWindowState, startMouseFollow, stopMouseFollow,
//     getAppState: () => appState,
//     setAppState: (s) => { Object.assign(appState, s); },
//   });
//
// Then in switchModel / applyAppState:
//   if (hermesWs) hermesWs.broadcast({ type: 'modelLoaded', character, costume });
//   if (hermesWs) hermesWs.broadcast({ type: 'state', model: {...}, settings: {...} });

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

// ─── Constants ──────────────────────────────────────────────────────────────
const WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';
const OPCODE_TEXT = 0x1;
const OPCODE_CLOSE = 0x8;
const OPCODE_PING = 0x9;
const OPCODE_PONG = 0xA;
const HEARTBEAT_INTERVAL = 30000;  // 30s
const HEARTBEAT_TIMEOUT = 60000;   // 60s
const MAX_FRAME_SIZE = 1024 * 1024; // 1 MB safety limit

// ─── Model Discovery (mirrors main.js scanModels) ───────────────────────────
function scanModels(modelsDir) {
  const models = {};
  if (!fs.existsSync(modelsDir)) return models;
  try {
    for (const char of fs.readdirSync(modelsDir)) {
      const charDir = path.join(modelsDir, char);
      if (!fs.statSync(charDir).isDirectory()) continue;
      const costumes = [];
      for (const entry of fs.readdirSync(charDir)) {
        const entryPath = path.join(charDir, entry);
        if (!fs.statSync(entryPath).isDirectory()) continue;
        if (fs.readdirSync(entryPath).some(f => f.endsWith('.model3.json'))) {
          costumes.push(entry);
        }
      }
      if (costumes.length > 0) models[char] = costumes;
    }
  } catch (err) {
    console.error('[HermesWS] scanModels error:', err.message);
  }
  return models;
}

// ─── HermesWsBridge ─────────────────────────────────────────────────────────
class HermesWsBridge {
  constructor(mainWindow, httpServer, config, callbacks = {}) {
    this._mainWindow = mainWindow;
    this._httpServer = httpServer;
    this._callbacks = callbacks;
    this._config = config || {};
    this._clients = new Set();
    this._heartbeatTimer = null;
    this._modelsDir = path.join(__dirname, 'assets', 'models');

    this._currentModel = {
      character: this._config.character || '',
      costume: this._config.costume || '',
    };

    this._appState = {};

    this._setupUpgradeHandler();
    this._startHeartbeat();

    console.log('[HermesWS] Bridge started on /hermes (attached to HTTP server)');
  }

  // ─── Upgrade Handler ────────────────────────────────────────────────────
  _setupUpgradeHandler() {
    if (!this._httpServer) { console.warn('[HermesWS] No HTTP server provided'); return; }

    this._httpServer.on('upgrade', (req, socket, head) => {
      if (req.url !== '/hermes') return;

      const upgrade = (req.headers.upgrade || '').toLowerCase();
      if (upgrade !== 'websocket') return;

      const key = req.headers['sec-websocket-key'];
      if (!key) { socket.destroy(); return; }

      const acceptKey = crypto.createHash('sha1').update(key + WS_GUID).digest('base64');

      socket.write(
        'HTTP/1.1 101 Switching Protocols\r\n' +
        'Upgrade: websocket\r\n' +
        'Connection: Upgrade\r\n' +
        'Sec-WebSocket-Accept: ' + acceptKey + '\r\n' +
        '\r\n'
      );

      const client = { socket, alive: true, buffer: Buffer.alloc(0) };
      this._clients.add(client);

      console.log('[HermesWS] Client connected (' + this._clients.size + ' total)');

      // Send welcome
      this._send(client, {
        type: 'welcome',
        version: '1.1.4',
        currentModel: this._currentModel,
        settings: this._appState,
      });

      socket.on('data', (chunk) => { this._onData(client, chunk); });
      socket.on('close', () => { this._clients.delete(client); console.log('[HermesWS] Client disconnected (' + this._clients.size + ' total)'); });
      socket.on('error', (err) => { console.error('[HermesWS] Socket error:', err.message); this._clients.delete(client); try { socket.destroy(); } catch {} });
    });
  }

  // ─── Frame Parsing (RFC 6455) ───────────────────────────────────────────
  _onData(client, chunk) {
    // Append chunk to client buffer
    client.buffer = Buffer.concat([client.buffer, chunk]);

    // Safety: drop if buffer grows too large
    if (client.buffer.length > MAX_FRAME_SIZE) {
      console.warn('[HermesWS] Client buffer overflow, disconnecting');
      this._sendClose(client, 1009, 'Message too large');
      return;
    }

    while (client.buffer.length >= 2) {
      const buf = client.buffer;
      const byte0 = buf[0];
      const byte1 = buf[1];
      const fin = (byte0 & 0x80) !== 0;
      const opcode = byte0 & 0x0F;
      const masked = (byte1 & 0x80) !== 0;
      let payloadLen = byte1 & 0x7F;
      let headerLen = 2;

      // Extended payload length
      if (payloadLen === 126) {
        if (buf.length < 4) return; // Not enough data
        payloadLen = buf.readUInt16BE(2);
        headerLen = 4;
      } else if (payloadLen === 127) {
        if (buf.length < 10) return; // Not enough data
        payloadLen = Number(buf.readBigUInt64BE(2));
        if (!Number.isSafeInteger(payloadLen) || payloadLen > MAX_FRAME_SIZE) {
          console.warn('[HermesWS] Frame too large, disconnecting');
          this._sendClose(client, 1009, 'Frame too large');
          return;
        }
        headerLen = 10;
      }

      // Masking key (present for client→server frames)
      let maskLen = 0;
      let maskKey = null;
      if (masked) {
        maskLen = 4;
        maskKey = buf.slice(headerLen, headerLen + 4);
        headerLen += 4;
      }

      // Check if complete frame is available
      if (buf.length < headerLen + payloadLen) return; // Wait for more data

      // Extract payload
      let payload = buf.slice(headerLen, headerLen + payloadLen);

      // Unmask
      if (masked && maskKey) {
        for (let i = 0; i < payload.length; i++) {
          payload[i] ^= maskKey[i % 4];
        }
      }

      // Consume frame from buffer
      client.buffer = buf.slice(headerLen + payloadLen);

      // Process frame
      try {
        this._onFrame(client, opcode, payload, fin);
      } catch (err) {
        console.error('[HermesWS] Frame processing error:', err.message);
      }

      // Stop on close
      if (opcode === OPCODE_CLOSE) return;

      // Only process one frame per tick for non-trivial frames
      if (!fin) break; // Continuation frames — not supported, break
    }
  }

  _onFrame(client, opcode, payload, _fin) {
    switch (opcode) {
      case OPCODE_TEXT: {
        const text = payload.toString('utf8');
        try {
          const data = JSON.parse(text);
          console.log('[HermesWS] ←', data.type, data.character ? '(' + data.character + '/' + data.costume + ')' : '');
          this._processCommand(client, data);
        } catch (e) {
          this._send(client, { type: 'error', message: 'Invalid JSON: ' + e.message });
        }
        break;
      }
      case OPCODE_CLOSE: {
        // Echo close frame
        let code = 1000;
        let reason = '';
        if (payload.length >= 2) {
          code = payload.readUInt16BE(0);
          reason = payload.slice(2).toString('utf8');
        }
        this._sendClose(client, code, reason);
        this._clients.delete(client);
        try { client.socket.end(); } catch {}
        console.log('[HermesWS] Client disconnected (' + this._clients.size + ' total)');
        break;
      }
      case OPCODE_PING: {
        this._sendRaw(client, OPCODE_PONG, payload);
        break;
      }
      case OPCODE_PONG: {
        client.alive = true;
        break;
      }
      default:
        // Unknown opcode — ignore
        break;
    }
  }

  // ─── Command Processing ─────────────────────────────────────────────────
  _processCommand(client, data) {
    if (!data || !data.type) {
      this._send(client, { type: 'error', message: 'Missing "type" field' });
      return;
    }

    switch (data.type) {
      // ── Renderer-bound commands (forward via webview-message) ──
      case 'expression':
      case 'motion':
      case 'look':
      case 'audioVolume':
        this._forwardToRenderer(data);
        this._send(client, { type: 'ok', command: data.type });
        break;

      // ── Model loading ──
      case 'loadModel':
        this._handleLoadModel(client, data);
        break;

      // ── Window size ──
      case 'size':
        this._handleSize(client, data);
        break;

      // ── App state toggles ──
      case 'setClickThrough':
        this._handleSetClickThrough(client, data);
        break;
      case 'setAlwaysOnTop':
        this._handleSetAlwaysOnTop(client, data);
        break;
      case 'setStreamingMode':
        this._handleSetStreamingMode(client, data);
        break;
      case 'setMouseFollow':
        this._handleSetMouseFollow(client, data);
        break;

      // ── Queries ──
      case 'getScreenshot':
        this._handleScreenshot(client);
        break;
      case 'getState':
        this._send(client, this._buildStateMessage());
        break;
      case 'listModels':
        this._handleListModels(client);
        break;

      default:
        this._send(client, { type: 'error', message: 'Unknown command: ' + data.type });
    }
  }

  // ── Command Handlers ────────────────────────────────────────────────────

  _forwardToRenderer(data) {
    const win = this._mainWindow;
    if (!win || win.isDestroyed()) return;
    try {
      win.webContents.send('webview-message', data);
    } catch (err) {
      console.error('[HermesWS] forwardToRenderer error:', err.message);
    }
  }

  _handleLoadModel(client, data) {
    const { character, costume } = data;
    if (!character || !costume) {
      this._send(client, { type: 'error', message: 'loadModel requires character and costume' });
      return;
    }

    // Use callback if provided (main.js switchModel is the authority)
    if (this._callbacks.switchModel) {
      try {
        this._callbacks.switchModel(character, costume);
        this._currentModel = { character, costume };
        this._send(client, { type: 'modelLoaded', character, costume });
      } catch (err) {
        this._send(client, { type: 'error', message: 'Failed to switch model: ' + err.message });
      }
      return;
    }

    // Fallback: send loadModel directly to renderer
    const md = scanModels(this._modelsDir);
    if (!md[character] || !md[character].includes(costume)) {
      this._send(client, { type: 'error', message: 'Model not found: ' + character + '/' + costume });
      return;
    }

    const modelDir = path.join(this._modelsDir, character, costume);
    let modelFile = '';
    try {
      modelFile = fs.readdirSync(modelDir).find(f => f.endsWith('.model3.json')) || '';
    } catch {}
    if (!modelFile) {
      this._send(client, { type: 'error', message: 'No model3.json found for ' + character + '/' + costume });
      return;
    }

    const modelUrl = 'http://127.0.0.1:19919/assets/models/' + character + '/' + costume + '/' + modelFile;
    this._forwardToRenderer({ type: 'loadModel', url: modelUrl, character, costume });
    this._currentModel = { character, costume };
    this._send(client, { type: 'modelLoaded', character, costume });
  }

  _handleSize(client, data) {
    const percent = Math.max(30, Math.min(500, Number(data.percent) || 100));

    // Use callback if provided
    if (this._callbacks.setWindowSize) {
      try {
        this._callbacks.setWindowSize(percent);
      } catch (err) {
        this._send(client, { type: 'error', message: 'Failed to set size: ' + err.message });
        return;
      }
    } else {
      // Direct window manipulation
      const win = this._mainWindow;
      if (!win || win.isDestroyed()) {
        this._send(client, { type: 'error', message: 'Window not available' });
        return;
      }
      const BASE_W = 460, BASE_H = 680;
      const w = Math.round(BASE_W * percent / 100);
      const h = Math.round(BASE_H * percent / 100);
      try {
        const [x, y] = win.getPosition();
        win.setBounds({ x, y, width: w, height: h });
      } catch (err) {
        this._send(client, { type: 'error', message: 'Failed to set window size: ' + err.message });
        return;
      }
    }

    // Also forward to renderer for model scaling
    this._forwardToRenderer({ type: 'size', percent });
    this._send(client, { type: 'ok', command: 'size', percent });
  }

  _handleSetClickThrough(client, data) {
    const enabled = !!data.enabled;
    const win = this._mainWindow;
    if (!win || win.isDestroyed()) {
      this._send(client, { type: 'error', message: 'Window not available' });
      return;
    }
    try {
      win.setIgnoreMouseEvents(enabled, { forward: true });
    } catch (err) {
      this._send(client, { type: 'error', message: 'Failed: ' + err.message });
      return;
    }
    this._appState.clickThrough = enabled;
    if (this._callbacks.saveWindowState) {
      try { this._callbacks.saveWindowState({ clickThrough: enabled }); } catch {}
    }
    this._send(client, { type: 'ok', command: 'setClickThrough', enabled });
  }

  _handleSetAlwaysOnTop(client, data) {
    const enabled = !!data.enabled;
    const win = this._mainWindow;
    if (!win || win.isDestroyed()) {
      this._send(client, { type: 'error', message: 'Window not available' });
      return;
    }
    try {
      win.setAlwaysOnTop(enabled, 'screen-saver');
    } catch (err) {
      this._send(client, { type: 'error', message: 'Failed: ' + err.message });
      return;
    }
    this._appState.alwaysOnTop = enabled;
    if (this._callbacks.saveWindowState) {
      try { this._callbacks.saveWindowState({ alwaysOnTop: enabled }); } catch {}
    }
    this._send(client, { type: 'ok', command: 'setAlwaysOnTop', enabled });
  }

  _handleSetStreamingMode(client, data) {
    const enabled = !!data.enabled;
    const win = this._mainWindow;
    if (!win || win.isDestroyed()) {
      this._send(client, { type: 'error', message: 'Window not available' });
      return;
    }
    try {
      if (enabled) {
        win.setSkipTaskbar(false);
        win.setTitle('Hermes Live2D (Stream)');
      } else {
        win.setSkipTaskbar(true);
        win.setTitle('Live2D Desktop Pet');
      }
    } catch (err) {
      this._send(client, { type: 'error', message: 'Failed: ' + err.message });
      return;
    }
    this._appState.streamingMode = enabled;
    if (this._callbacks.saveWindowState) {
      try { this._callbacks.saveWindowState({ streamingMode: enabled }); } catch {}
    }
    this._send(client, { type: 'ok', command: 'setStreamingMode', enabled });
  }

  _handleSetMouseFollow(client, data) {
    const enabled = !!data.enabled;
    this._appState.mouseFollow = enabled;
    if (this._callbacks.setMouseFollow) {
      try {
        this._callbacks.setMouseFollow(enabled);
      } catch (err) {
        this._send(client, { type: 'error', message: 'Failed: ' + err.message });
        return;
      }
    }
    this._send(client, { type: 'ok', command: 'setMouseFollow', enabled });
  }

  async _handleScreenshot(client) {
    const win = this._mainWindow;
    if (!win || win.isDestroyed()) {
      this._send(client, { type: 'error', message: 'Window not available' });
      return;
    }

    // Two attempts: canvas.toDataURL then nativeImage
    try {
      const dataUrl = await win.webContents.executeJavaScript(
        '(function(){var c=document.getElementById("stage");return c?c.toDataURL("image/png"):null;})()'
      );
      if (dataUrl && typeof dataUrl === 'string') {
        this._send(client, { type: 'screenshot', data: dataUrl });
        return;
      }
    } catch (err) {
      console.error('[HermesWS] Screenshot canvas error:', err.message);
    }

    // Fallback: nativeImage capture
    try {
      const { nativeImage } = require('electron');
      const img = await win.capturePage();
      const png = img.toPNG();
      const b64 = 'data:image/png;base64,' + png.toString('base64');
      this._send(client, { type: 'screenshot', data: b64 });
    } catch (err) {
      this._send(client, { type: 'error', message: 'Screenshot failed: ' + err.message });
    }
  }

  _handleListModels(client) {
    const models = scanModels(this._modelsDir);
    const entries = Object.entries(models).map(([character, costumes]) => ({
      character,
      costumes,
    }));
    this._send(client, { type: 'modelList', models: entries });
  }

  // ─── State Message Builder ──────────────────────────────────────────────
  _buildStateMessage() {
    // Merge with live appState from main.js if callback available
    let settings = { ...this._appState };
    if (this._callbacks.getAppState) {
      try { settings = { ...settings, ...this._callbacks.getAppState() }; } catch {}
    }
    return {
      type: 'state',
      model: this._currentModel,
      settings,
    };
  }

  // ─── Frame Building (RFC 6455 server→client, no masking) ───────────────
  _sendRaw(client, opcode, payload) {
    const len = payload.length;
    let frame;

    if (len < 126) {
      frame = Buffer.allocUnsafe(2 + len);
      frame[0] = 0x80 | opcode;
      frame[1] = len;
      if (len > 0) payload.copy(frame, 2);
    } else if (len < 65536) {
      frame = Buffer.allocUnsafe(4 + len);
      frame[0] = 0x80 | opcode;
      frame[1] = 126;
      frame.writeUInt16BE(len, 2);
      payload.copy(frame, 4);
    } else {
      frame = Buffer.allocUnsafe(10 + len);
      frame[0] = 0x80 | opcode;
      frame[1] = 127;
      frame.writeBigUInt64BE(BigInt(len), 2);
      payload.copy(frame, 10);
    }

    try { client.socket.write(frame); } catch {}
  }

  _send(client, message) {
    const json = JSON.stringify(message);
    const payload = Buffer.from(json, 'utf8');
    this._sendRaw(client, OPCODE_TEXT, payload);
  }

  _sendClose(client, code, reason) {
    const reasonBuf = Buffer.from(reason || '', 'utf8');
    const payload = Buffer.allocUnsafe(2 + reasonBuf.length);
    payload.writeUInt16BE(code, 0);
    if (reasonBuf.length > 0) reasonBuf.copy(payload, 2);
    this._sendRaw(client, OPCODE_CLOSE, payload);
  }

  // ─── Broadcast (for main.js to push state updates) ──────────────────────
  broadcast(message) {
    if (this._clients.size === 0) return;
    const json = JSON.stringify(message);
    const payload = Buffer.from(json, 'utf8');
    const len = payload.length;

    // Pre-build frame template
    let frame;
    if (len < 126) {
      frame = Buffer.allocUnsafe(2 + len);
      frame[0] = 0x80 | OPCODE_TEXT;
      frame[1] = len;
      payload.copy(frame, 2);
    } else if (len < 65536) {
      frame = Buffer.allocUnsafe(4 + len);
      frame[0] = 0x80 | OPCODE_TEXT;
      frame[1] = 126;
      frame.writeUInt16BE(len, 2);
      payload.copy(frame, 4);
    } else {
      frame = Buffer.allocUnsafe(10 + len);
      frame[0] = 0x80 | OPCODE_TEXT;
      frame[1] = 127;
      frame.writeBigUInt64BE(BigInt(len), 2);
      payload.copy(frame, 10);
    }

    for (const client of this._clients) {
      try { client.socket.write(frame); } catch { this._clients.delete(client); }
    }
  }

  // ─── Heartbeat ─────────────────────────────────────────────────────────
  _startHeartbeat() {
    this._heartbeatTimer = setInterval(() => {
      const now = Date.now();
      for (const client of this._clients) {
        if (!client.alive) {
          console.log('[HermesWS] Heartbeat timeout, disconnecting client');
          this._sendClose(client, 1001, 'Heartbeat timeout');
          this._clients.delete(client);
          try { client.socket.destroy(); } catch {}
          continue;
        }
        client.alive = false;
        this._sendRaw(client, OPCODE_PING, Buffer.alloc(0));
      }
    }, HEARTBEAT_INTERVAL);

    if (this._heartbeatTimer && typeof this._heartbeatTimer.unref === 'function') {
      this._heartbeatTimer.unref(); // Don't keep process alive for heartbeat
    }
  }

  // ─── Cleanup ───────────────────────────────────────────────────────────
  destroy() {
    if (this._heartbeatTimer) { clearInterval(this._heartbeatTimer); this._heartbeatTimer = null; }
    for (const client of this._clients) {
      this._sendClose(client, 1001, 'Server shutting down');
      try { client.socket.destroy(); } catch {}
    }
    this._clients.clear();
    this._callbacks = {};
    console.log('[HermesWS] Bridge destroyed');
  }
}

// ─── Public API ────────────────────────────────────────────────────────────
function startHermesWsServer(mainWindow, httpServer, config, callbacks) {
  return new HermesWsBridge(mainWindow, httpServer, config, callbacks);
}

module.exports = { startHermesWsServer };
