import * as PIXI from 'pixi.js';
window.PIXI = PIXI;

import 'pixi-live2d-display/cubism2';
import { Live2DModel, Cubism2ModelSettings } from 'pixi-live2d-display';

window.Live2DModel = Live2DModel;
window.Cubism2ModelSettings = Cubism2ModelSettings;

console.log('[Bundle] PIXI v' + PIXI.VERSION + ' + pixi-live2d-display cubism2');
