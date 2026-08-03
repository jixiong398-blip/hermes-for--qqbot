#!/usr/bin/env python3
"""FunASR speech-to-text: SenseVoiceSmall — 50+ languages, fast CPU inference.

Singleton model loaded once, reused across calls.
Usage:
    from tools.stt_funasr import transcribe
    result = transcribe("/path/to/audio.ogg")
    # {"text": "今天天气真好", "language": "zh", "emotion": "neutral"}
"""

import logging
import threading

logger = logging.getLogger("stt.funasr")

_model = None
_lock = threading.Lock()


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        from funasr import AutoModel
        _model = AutoModel(
            model="iic/SenseVoiceSmall",
            device="cpu",
            disable_update=True,
        )
        logger.info("FunASR SenseVoiceSmall loaded")
        return _model


def transcribe(audio_path: str) -> dict:
    """Transcribe audio file. Returns {text, language, emotion} or {text: "语音"} on failure."""
    try:
        model = _load_model()
        result = model.generate(
            input=audio_path,
            language="auto",       # Auto-detect language
            use_itn=True,           # Inverse Text Normalization (numbers, dates, etc.)
            batch_size_s=60,        # Long audio: 60s chunks
        )
        if result and len(result) > 0:
            r = result[0]
            text = r.get("text", "").strip()
            lang = r.get("key", "")
            emotion = ""
            # SenseVoice format: "<|zh|><|NEUTRAL|><|Speech|><|withitn|>实际转写文本"
            if text.startswith("<|"):
                import re as _re
                for tag in _re.findall(r'<\|([^|>]+)\|>', text):
                    tag_upper = tag.upper()
                    if tag_upper in ("ZH", "EN", "JA", "KO", "YUE", "CANT", "WUU", "CN"):
                        lang = tag.lower()
                    elif tag_upper in ("NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED", "FEARFUL", "DISGUSTED"):
                        emotion = tag.lower()
                # Strip tags: remove everything before the last |>
                text = text.rsplit("|>", 1)[-1].strip() if "|>" in text else text
            return {
                "text": (text or "").strip(),
                "language": lang,
                "emotion": emotion,
            }
        return {"text": "语音", "language": "", "emotion": ""}
    except Exception as e:
        logger.warning("FunASR transcription failed: %s", e)
        return {"text": "语音", "language": "", "emotion": ""}
