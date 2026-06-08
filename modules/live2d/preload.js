const { contextBridge, ipcRenderer } = require('electron');

let _onCmd = null;

ipcRenderer.on('live2d-cmd', (_event, data) => {
  if (typeof _onCmd === 'function') {
    try { _onCmd(data); } catch (_) {}
  }
});

contextBridge.exposeInMainWorld('electronAPI', {
  onLive2dCmd: (callback) => { _onCmd = callback; },
  sendScreenshot: (dataUrl) => { ipcRenderer.send('screenshot-data', dataUrl); },
});
