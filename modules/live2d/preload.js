const { ipcRenderer } = require('electron');

const _msgListeners = [];

// Capture accurate screen coords from pointerdown/pointerup (before renderer.js processes it)
document.addEventListener('pointerdown', (e) => {
  window.__dragScreenX = e.screenX;
  window.__dragScreenY = e.screenY;
}, true);
document.addEventListener('pointerup', (e) => {
  window.__clickScreenX = e.screenX;
  window.__clickScreenY = e.screenY;
}, true);

window.chrome = window.chrome || {};
window.chrome.webview = {
  postMessage(data) {
    if (data && data.type === 'drag') {
      // Pass screen coords from pointerdown + let renderer.js gating via isCharacterHit
      ipcRenderer.sendSync('native-drag', {
        screenX: window.__dragScreenX || 0,
        screenY: window.__dragScreenY || 0,
      });
    } else if (data && data.type === 'contextMenu') {
      // Pass screen coords from pointerup (accurate, no IPC delay)
      ipcRenderer.send('webview-message', {
        ...data,
        screenX: window.__clickScreenX || 0,
        screenY: window.__clickScreenY || 0,
      });
    } else {
      ipcRenderer.send('webview-message', data);
    }
  },
  addEventListener(event, cb) { if (event === 'message') _msgListeners.push(cb); },
  removeEventListener(event, cb) {
    if (event === 'message') {
      const i = _msgListeners.indexOf(cb);
      if (i >= 0) _msgListeners.splice(i, 1);
    }
  },
};

ipcRenderer.on('webview-message', (_e, data) => {
  const e = { data };
  for (const cb of _msgListeners) { try { cb(e); } catch {} }
});

Object.defineProperty(window, 'hermesHitTest', {
  get: () => window.cucumberVPetHitTest,
  configurable: true,
});
