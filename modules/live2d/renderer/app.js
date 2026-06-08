const params = new URLSearchParams(window.location.search);
let CHARACTER = params.get('character') || 'soyo';
let COSTUME = params.get('costume') || 'casual-2023';
const WS_PORT = params.get('wsPort') || '9190';
const ASSETS_BASE = 'http://127.0.0.1:19919/assets/';

let pixiApp = null;
let model = null;
let currentState = 'idle';
let ws = null;
let wsTimer = null;
let idleTimer = null;
let manualPauseUntil = 0;
const MANUAL_PAUSE_MS = 10000;

const STATE_REPERTOIRE = {
  idle: { expressions: ['default', 'idle01', 'nf01', 'nf02', 'nf03'], motions: ['idle01', 'nf01', 'nf02'], interval: 8000 },
  thinking: { expressions: ['thinking01', 'thinking02_01', 'thinking02_02', 'serious01'], motions: ['thinking01', 'thinking02_01'], interval: 3000 },
  tool_call: { expressions: ['kime01', 'serious01', 'serious02'], motions: ['kime01'], interval: 5000 },
  replying: { expressions: ['smile01', 'smile02', 'smile03'], motions: ['smile01'], interval: 6000 },
  speaking: { expressions: ['default', 'smile01'], motions: ['idle01', 'nf01'], interval: 4000 },
};

const EMOTION_MAP = {
  smile:     { expressions: ['smile01','smile02','smile03','smile04','smile05','smile06','wink01'], motions: ['smile01','smile02','smile03','smile04','smile05','smile06','smile01_ingameV2'] },
  angry:     { expressions: ['angry01','angry02','angry03','angry04'], motions: ['angry01','angry02','angry03','angry04','angry05','angry06'] },
  sad:       { expressions: ['sad01','sad02','sad03'], motions: ['sad01','sad02','sad03'] },
  cry:       { expressions: ['cry01','cry02'], motions: ['cry01','cry02'] },
  surprised: { expressions: ['surprised01'], motions: ['surprised01'] },
  serious:   { expressions: ['serious01','serious02','serious03','serious04','kime01'], motions: ['serious01','serious02','serious03','serious04','kime01'] },
  shame:     { expressions: ['shame01','shame02'], motions: ['shame01','shame02'] },
  wink:      { expressions: ['wink01'], motions: ['wink01'] },
  thinking:  { expressions: ['thinking01','thinking02'], motions: ['thinking01','thinking02_01','thinking02_02','thinking02_ingameV2'] },
  goodbye:   { expressions: ['bye01','bye02'], motions: ['bye01','bye02'] },
  nervous:   { expressions: ['odoodo01'], motions: ['odoodo01'] },
  relieved:  { expressions: ['ando01'], motions: ['ando01'] },
  excited:   { expressions: ['kandou01'], motions: ['kandou01'] },
  scared:    { expressions: ['default'], motions: ['scared01'] },
  default:   { expressions: ['default','idle01'], motions: ['idle01','nf01','nf02','nf03','nf04','nf05'] },
};

function modelUrl() {
  return `${ASSETS_BASE}figure/${CHARACTER}/${COSTUME}/model.json`;
}

function setIPCStatus(ok, detail) {
  const el = document.getElementById('ipc-status');
  if (el) {
    el.textContent = ok ? 'IPC ✓' : 'IPC ✗';
    el.className = ok ? 'connected' : 'disconnected';
    el.title = detail || '';
  }
}

function setStatus(ok) {
  const el = document.getElementById('ws-status');
  el.textContent = ok ? '● 接続済' : '● 未接続';
  el.className = ok ? 'connected' : 'disconnected';
}

function setLabel(s) {
  const m = { idle: '待機', thinking: '思考中', tool_call: 'ツール実行', replying: '返信中', speaking: '通話中' };
  document.getElementById('state-label').textContent = m[s] || s;
}

async function initPixi() {
  const canvas = document.getElementById('live2d-canvas');
  pixiApp = new PIXI.Application({
    view: canvas, width: window.innerWidth, height: window.innerHeight,
    backgroundAlpha: 0, antialias: true,
    resolution: window.devicePixelRatio || 1, autoDensity: true,
    preserveDrawingBuffer: true,
  });
  pixiApp.stage.sortableChildren = true;
  pixiApp.stage.eventMode = 'none';
  pixiApp.stage.interactiveChildren = false;
}

async function loadModel() {
  const url = modelUrl();
  model = await window.Live2DModel.from(url, { autoInteract: false, autoUpdate: true });
  if (!model) throw new Error('Model creation returned null');
  model.visible = true;
  model.eventMode = 'none';
  model.interactiveChildren = false;
  pixiApp.stage.addChild(model);
  fitModel();
  window.addEventListener('resize', fitModel);
}

function fitModel() {
  if (!model || !model.width) return;
  const cw = window.innerWidth, ch = window.innerHeight;
  const mw = model.width, mh = model.height;
  const s = Math.min((cw * 0.85) / mw, (ch * 0.9) / mh);
  model.scale.set(s);
  model.anchor.set(0.5, 0.5);
  model.x = cw / 2;
  model.y = ch / 2;
}

function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function setExpression(name) {
  if (!model || !name) return;
  const key = `${CHARACTER}/${name}`;
  try { model.expression(key); } catch (_) {}
}

function playMotion(name) {
  if (!model || !name) return;
  try { model.motion(`${CHARACTER}/${name}`, 0, 3); } catch (_) {}
}

function startIdleCycle(repertoire) {
  stopIdleCycle();
  let idx = 0;
  idleTimer = setInterval(() => {
    if (Date.now() < manualPauseUntil) return;
    if (!repertoire || !repertoire.expressions) return;
    const exp = repertoire.expressions[idx % repertoire.expressions.length];
    setExpression(exp);
    if (repertoire.motions && Math.random() < 0.5) {
      playMotion(pickRandom(repertoire.motions));
    }
    idx++;
  }, repertoire.interval || 8000);
}

function stopIdleCycle() {
  if (idleTimer) { clearInterval(idleTimer); idleTimer = null; }
}

function applyEmotion(emotionName) {
  const r = EMOTION_MAP[emotionName];
  if (!r) return;
  setExpression(pickRandom(r.expressions));
  playMotion(pickRandom(r.motions));
  manualPauseUntil = Date.now() + MANUAL_PAUSE_MS;
}

function changeState(state) {
  if (state === currentState) return;
  currentState = state;
  setLabel(state);

  const r = STATE_REPERTOIRE[state];
  if (!r) return;

  if (Date.now() < manualPauseUntil) {
    startIdleCycle(r);
    return;
  }

  setExpression(pickRandom(r.expressions));
  playMotion(pickRandom(r.motions));
  startIdleCycle(r);
}

function handleMsg(raw) {
  try {
    const m = typeof raw === 'string' ? JSON.parse(raw) : raw;
    switch (m.type) {
      case 'state': changeState(m.state); break;
      case 'expression':
        setExpression(m.name.replace(`${CHARACTER}/`, ''));
        manualPauseUntil = Date.now() + MANUAL_PAUSE_MS;
        break;
      case 'motion':
        playMotion(m.name.replace(`${CHARACTER}/`, ''));
        manualPauseUntil = Date.now() + MANUAL_PAUSE_MS;
        break;
      case 'speaking':
        if (m.state) { changeState('speaking'); }
        else { manualPauseUntil = 0; changeState('idle'); }
        break;
      case 'emotion':
        applyEmotion(m.emotion);
        break;
      case 'screenshot_request':
        try {
          if (window.electronAPI && window.electronAPI.sendScreenshot) {
            const canvas = document.getElementById('live2d-canvas');
            if (canvas) {
              const dataUrl = canvas.toDataURL('image/png');
              window.electronAPI.sendScreenshot(dataUrl);
            } else if (pixiApp && pixiApp.renderer) {
              pixiApp.renderer.render(pixiApp.stage);
              const c = pixiApp.renderer.extract.canvas ? 
                pixiApp.renderer.extract.canvas(pixiApp.stage) :
                pixiApp.renderer.view;
              const dataUrl = c.toDataURL('image/png');
              window.electronAPI.sendScreenshot(dataUrl);
            }
          }
        } catch (_) {}
        break;
    }
  } catch (_) {}
}

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  clearTimeout(wsTimer); wsTimer = null;
  try {
    ws = new WebSocket(`ws://127.0.0.1:${WS_PORT}`);
    ws.onopen = () => setStatus(true);
    ws.onmessage = e => handleMsg(e.data);
    ws.onclose = () => { setStatus(false); wsTimer = setTimeout(connectWS, 3000); };
    ws.onerror = () => { wsTimer = setTimeout(connectWS, 3000); };
  } catch (_) { wsTimer = setTimeout(connectWS, 3000); }
}

async function init() {
  try {
    await initPixi();
    await loadModel();
    changeState('idle');
    console.log('[Live2D] Ready');
  } catch (e) {
    console.error('[Live2D] Error:', e.message);
    document.body.innerHTML = `<div style="color:white;padding:20px;font-family:sans-serif;">エラー: ${e.message}</div>`;
    return;
  }
  connectWS();
}

document.addEventListener('DOMContentLoaded', init);

// IPC: switch model without page reload
window._switchModel = async (newChar, newOutfit) => {
  if (!model || !pixiApp) return;
  pixiApp.stage.removeChild(model);
  model = null;
  stopIdleCycle();

  CHARACTER = newChar;
  COSTUME = newOutfit;
  const url = new URL(window.location.href);
  url.searchParams.set('character', newChar);
  url.searchParams.set('costume', newOutfit);
  window.history.replaceState({}, '', url.toString());

  await loadModel();
  changeState('idle');
  document.getElementById('state-label').textContent = newChar + '/' + newOutfit;
  setTimeout(() => setLabel(currentState), 3000);
};

if (window.electronAPI && window.electronAPI.onLive2dCmd) {
  window.electronAPI.onLive2dCmd((data) => {
    if (data.type === 'switch_model') {
      window._switchModel(data.character, data.costume);
      return;
    }
    handleMsg(data);
  });
}
