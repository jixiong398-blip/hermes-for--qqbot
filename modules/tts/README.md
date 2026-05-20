# TTS 语音模块 — 接入说明

本模块只包含调用代码（ts_adapter.py + tts_infer.yaml），不包含 GPT-SoVITS 模型文件。

## 安装 GPT-SoVITS

1. 下载 GPT-SoVITS v2ProPlus:
   https://github.com/XuMingyue-xmy/GPT-SoVITS

2. 放置音色权重到 `GPT-SoVITS-v2pro-20250604/`:
   `MyGO_soyo_v2pp.pth`  → 放入 reference/ 目录

3. 放置参考音频到 `tts_refs/`:
   10 个情绪 .wav 文件（default/happy/laugh/sad/angry/surprise/question/apologize/agree/flirty）

## 启动

```bash
# 1. 启动 GPT-SoVITS API（端口 9880）
python api_v2.py

# 2. 启动 TTS 适配器（端口 5000）
python ts_adapter.py
```

## 调用方式

Hermes Gateway 通过 HTTP 调用:
POST http://127.0.0.1:5000/v1/audio/speech
Body: {"text": "要说的内容", "emotion": "happy"}

TTS Adapter → GPT-SoVITS → 生成 WAV → ffmpeg → MP3 → QQ 语音
