const canvas = document.getElementById("stage");
const statusEl = document.getElementById("status");
const renderQualityScale = 2;
const maxRendererResolution = 4;

PIXI.settings.ROUND_PIXELS = true;

function getRendererResolution() {
    const deviceRatio = Math.max(1, window.devicePixelRatio || 1);
    return Math.min(maxRendererResolution, deviceRatio * renderQualityScale);
}

const app = new PIXI.Application({
    view: canvas,
    autoStart: true,
    autoDensity: true,
    backgroundAlpha: 0,
    antialias: true,
    preserveDrawingBuffer: true,
    resolution: getRendererResolution(),
    resizeTo: window,
});

let currentModel = null;
let lookEnabled = false;
let targetLookX = 0;
let targetLookY = 0;
let currentLookX = 0;
let currentLookY = 0;
let pendingFitFrames = 0;
let desiredSizePercent = 100;
let loadRequestId = 0;
let startupAudio = null;
let startupVolume = 0.6;
let lastHitTest = { x: -9999, y: -9999, hit: false, time: 0 };

const baseWindowWidth = 460;
const baseWindowHeight = 680;
const fitMarginLeft = 0.14;
const fitMarginRight = 0.14;
const fitMarginTop = 0.003;
const fitMarginBottom = 0.16;

function post(type, payload = {}) {
    if (window.chrome?.webview) {
        window.chrome.webview.postMessage({ type, ...payload });
    }
}

function setStatus(text, hidden = false) {
    statusEl.textContent = text;
    statusEl.classList.toggle("hidden", hidden);
}

function resizeModel() {
    if (!currentModel) {
        return;
    }

    currentModel.scale.set(1);
    currentModel.position.set(0, 0);
    currentModel.updateTransform();

    const bounds = currentModel.getBounds();
    if (!Number.isFinite(bounds.width) || !Number.isFinite(bounds.height) || bounds.width <= 0 || bounds.height <= 0) {
        return;
    }

    const screenWidth = app.screen.width;
    const screenHeight = app.screen.height;
    const targetWidth = screenWidth * (1 - fitMarginLeft - fitMarginRight);
    const targetHeight = screenHeight * (1 - fitMarginTop - fitMarginBottom);
    const fitScale = Math.min(targetWidth / bounds.width, targetHeight / bounds.height);
    const actualWindowPercent = Math.min(
        screenWidth / baseWindowWidth,
        screenHeight / baseWindowHeight,
    ) * 100;
    const visualBoost = actualWindowPercent > 0
        ? Math.max(1, Math.min(1.22, desiredSizePercent / actualWindowPercent))
        : 1;
    const scale = fitScale * visualBoost;

    currentModel.scale.set(scale);
    currentModel.x = Math.round(screenWidth * fitMarginLeft + (targetWidth - bounds.width * scale) / 2 - bounds.x * scale);
    currentModel.y = Math.round(screenHeight * fitMarginTop - bounds.y * scale);
    currentModel.roundPixels = true;
}

function syncRendererResolution() {
    const ratio = getRendererResolution();
    if (Math.abs(app.renderer.resolution - ratio) > 0.001) {
        app.renderer.resolution = ratio;
        app.renderer.resize(window.innerWidth, window.innerHeight);
        scheduleFit(12);
    }
}

function setSizePercent(percent) {
    desiredSizePercent = Math.max(50, Math.min(200, Number(percent) || 100));
    resizeModel();
    scheduleFit(12);
}

function scheduleFit(frames = 24) {
    pendingFitFrames = Math.max(pendingFitFrames, frames);
}

function destroyModel(model) {
    if (!model) {
        return;
    }

    try {
        if (model.parent) {
            model.parent.removeChild(model);
        }
    } catch {
        // Ignore stale Pixi parent state from interrupted model switches.
    }

    try {
        if (typeof model.destroy === "function" && !model.destroyed) {
            model.destroy({ children: true, texture: false, baseTexture: false });
        }
    } catch {
        // A broken or interrupted model load can leave Pixi internals half-initialized.
    }
}

function clearStageExcept(keptModel) {
    for (const child of [...app.stage.children]) {
        if (child !== keptModel) {
            destroyModel(child);
        }
    }
}

function stopStartupAudio() {
    if (!startupAudio) {
        return;
    }

    try {
        startupAudio.pause();
        startupAudio.currentTime = 0;
    } catch {
        // Audio cleanup should not affect model switching.
    }

    startupAudio = null;
}

function playStartupSound(url) {
    stopStartupAudio();
    if (!url) {
        return;
    }

    try {
        startupAudio = new Audio(url);
        startupAudio.volume = startupVolume;
        const playResult = startupAudio.play();
        if (playResult && typeof playResult.catch === "function") {
            playResult.catch((error) => {
                post("audioError", { message: error?.message ?? String(error) });
                stopStartupAudio();
            });
        }
    } catch (error) {
        post("audioError", { message: error?.message ?? String(error) });
        stopStartupAudio();
    }
}

function setAudioVolume(percent) {
    const normalized = Math.max(0, Math.min(1, (Number(percent) || 0) / 100));
    startupVolume = normalized;
    if (startupAudio) {
        startupAudio.volume = startupVolume;
    }
}

async function loadModel(url, startupSoundUrl = null) {
    const requestId = ++loadRequestId;
    setStatus("Loading Live2D...");
    stopStartupAudio();

    let nextModel = null;
    try {
        nextModel = await PIXI.live2d.Live2DModel.from(url, {
            autoInteract: false,
        });
    } catch (error) {
        if (requestId === loadRequestId) {
            throw error;
        }

        return;
    }

    if (requestId !== loadRequestId) {
        destroyModel(nextModel);
        return;
    }

    currentModel = nextModel;
    clearStageExcept(currentModel);
    if (!currentModel.parent) {
        app.stage.addChild(currentModel);
    }

    resizeModel();
    scheduleFit(36);
    playStartupSound(startupSoundUrl);
    currentLookX = 0;
    currentLookY = 0;
    targetLookX = 0;
    targetLookY = 0;

    setStatus("", true);
}

function playExpression(name) {
    if (!currentModel || !name) {
        return;
    }

    try {
        currentModel.expression(name);
    } catch {
        const expressions = currentModel.internalModel?.settings?.expressions ?? [];
        const index = expressions.findIndex((item) => item.Name === name || item.name === name);
        if (index >= 0) {
            currentModel.expression(index);
        }
    }

    scheduleFit(18);
}

function playMotion(group, index) {
    if (!currentModel || !group) {
        return;
    }

    currentModel.motion(group, index ?? 0);
    scheduleFit(60);
}

function setModelParameter(id, value, weight = 1) {
    const coreModel = currentModel?.internalModel?.coreModel;
    if (!coreModel) {
        return;
    }

    if (typeof coreModel.setParameterValueById === "function") {
        coreModel.setParameterValueById(id, value, weight);
    } else if (typeof coreModel.addParameterValueById === "function") {
        coreModel.addParameterValueById(id, value, weight);
    }
}

function updateLookTarget(x, y, enabled) {
    lookEnabled = enabled !== false;
    targetLookX = lookEnabled ? Math.max(-1, Math.min(1, Number(x) || 0)) : 0;
    targetLookY = lookEnabled ? Math.max(-1, Math.min(1, Number(y) || 0)) : 0;
}

function applyLook() {
    if (!currentModel) {
        return;
    }

    const ease = 0.24;
    currentLookX += (targetLookX - currentLookX) * ease;
    currentLookY += (targetLookY - currentLookY) * ease;

    const angleX = currentLookX * 26;
    const angleY = -currentLookY * 18;
    const eyeX = currentLookX;
    const eyeY = -currentLookY * 0.65;
    const bodyX = currentLookX * 7;

    setModelParameter("ParamAngleX", angleX, 1);
    setModelParameter("ParamAngleY", angleY, 1);
    setModelParameter("ParamEyeBallX", eyeX, 1);
    setModelParameter("ParamEyeBallY", eyeY, 1);
    setModelParameter("ParamBodyAngleX", bodyX, 0.55);
}

function isCharacterHit(event) {
    const rect = canvas.getBoundingClientRect();
    return hitTestAtCssPoint(event.clientX - rect.left, event.clientY - rect.top);
}

function readAlphaAtCssPoint(x, y) {
    if (!currentModel || !app.renderer?.gl) {
        return 0;
    }

    const resolution = app.renderer.resolution || window.devicePixelRatio || 1;
    const pixelX = Math.floor(x * resolution);
    const pixelY = Math.floor((canvas.clientHeight - y) * resolution);
    if (pixelX < 0 || pixelY < 0 || pixelX >= canvas.width || pixelY >= canvas.height) {
        return 0;
    }

    const gl = app.renderer.gl;
    const pixel = new Uint8Array(4);
    try {
        gl.readPixels(pixelX, pixelY, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
        return pixel[3] || 0;
    } catch {
        return 0;
    }
}

function hitTestAtCssPoint(x, y) {
    if (!currentModel) {
        return false;
    }

    const now = performance.now();
    if (now - lastHitTest.time < 16
        && Math.abs(lastHitTest.x - x) < 2
        && Math.abs(lastHitTest.y - y) < 2) {
        return lastHitTest.hit;
    }

    const bounds = currentModel.getBounds();
    const coarsePadding = 4;
    if (x < bounds.x - coarsePadding
        || x > bounds.x + bounds.width + coarsePadding
        || y < bounds.y - coarsePadding
        || y > bounds.y + bounds.height + coarsePadding) {
        lastHitTest = { x, y, hit: false, time: now };
        return false;
    }

    const threshold = 8;
    const offsets = [
        [0, 0],
        [2, 0],
        [-2, 0],
        [0, 2],
        [0, -2],
        [3, 3],
        [-3, 3],
        [3, -3],
        [-3, -3],
    ];
    const hit = offsets.some(([dx, dy]) => readAlphaAtCssPoint(x + dx, y + dy) >= threshold);
    lastHitTest = { x, y, hit, time: now };
    return hit;
}

window.cucumberVPetHitTest = (x, y) => hitTestAtCssPoint(Number(x) || 0, Number(y) || 0);

window.chrome?.webview?.addEventListener("message", async (event) => {
    const message = event.data ?? {};

    try {
        if (message.type === "loadModel") {
            if (message.sizePercent) {
                desiredSizePercent = Math.max(50, Math.min(200, Number(message.sizePercent) || 100));
            }
            if (message.audioVolumePercent !== undefined) {
                setAudioVolume(message.audioVolumePercent);
            }
            await loadModel(message.url, message.startupSoundUrl);
        } else if (message.type === "expression") {
            playExpression(message.name);
        } else if (message.type === "motion") {
            playMotion(message.group, message.index);
        } else if (message.type === "look") {
            updateLookTarget(message.x, message.y, message.enabled);
        } else if (message.type === "size") {
            setSizePercent(message.percent);
        } else if (message.type === "audioVolume") {
            setAudioVolume(message.percent);
        }
    } catch (error) {
        setStatus(error?.message ?? "Live2D load failed");
        post("error", { message: error?.message ?? String(error) });
    }
});

app.ticker.add(applyLook, undefined, PIXI.UPDATE_PRIORITY.LOW);
app.ticker.add(() => {
    if (pendingFitFrames > 0) {
        resizeModel();
        pendingFitFrames -= 1;
    }
}, undefined, PIXI.UPDATE_PRIORITY.LOW);
window.addEventListener("resize", resizeModel);
window.addEventListener("resize", syncRendererResolution);

canvas.addEventListener("contextmenu", (event) => {
    event.preventDefault();
});

document.addEventListener("pointerdown", (event) => {
    if (event.button === 0 && isCharacterHit(event)) {
        post("drag");
        event.preventDefault();
    } else if (event.button === 2) {
        event.preventDefault();
    }
});

document.addEventListener("pointerup", (event) => {
    if (event.button === 2 && isCharacterHit(event)) {
        post("contextMenu");
        event.preventDefault();
    } else if (event.button === 2) {
        event.preventDefault();
    }
});

post("ready");
