"""MiniCPM-V 4.6 本地识图模块 — CPU 推理，常驻加载。

Singleton 模型，gateway 启动后首次调用时加载到内存，
之后复用。替换云端 MiMo v2.5，零 API 成本。

用法:
    from vision_local import describe_image
    desc = describe_image("/path/to/image.jpg")
    # "闭眼微笑女孩喝杯茶，显得十分惬意。"
"""

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("vision.local")

_model = None
_processor = None
_tokenizer = None
_lock = threading.Lock()

_HERMES_HOME = os.getenv("HERMES_HOME", str(os.path.expanduser("~/.hermes")))
_MODEL_PATH = os.getenv(
    "VISION_LOCAL_MODEL_PATH",
    os.path.join(_HERMES_HOME, "models", "models--openbmb--MiniCPM-V-4_6", "snapshots"),
)


def _resolve_model_path() -> str:
    """If _MODEL_PATH ends at snapshots/, pick the first snapshot subdir.
    If it points directly at a model dir, use as-is."""
    if os.path.exists(os.path.join(_MODEL_PATH, "config.json")):
        return _MODEL_PATH
    if os.path.isdir(_MODEL_PATH):
        for name in os.listdir(_MODEL_PATH):
            sub = os.path.join(_MODEL_PATH, name)
            if os.path.exists(os.path.join(sub, "config.json")):
                return sub
    raise FileNotFoundError(
        f"MiniCPM-V 4.6 model not found under {_MODEL_PATH}. "
        f"Set VISION_LOCAL_MODEL_PATH env to the model dir, "
        f"or run snapshot_download to fetch it."
    )


def _ensure_loaded():
    global _model, _processor, _tokenizer
    if _model is not None:
        return
    with _lock:
        if _model is not None:
            return
        import torch
        from transformers import (
            AutoModelForImageTextToText,
            AutoProcessor,
            AutoTokenizer,
        )

        actual_path = _resolve_model_path()

        t0 = time.time()
        _model = AutoModelForImageTextToText.from_pretrained(
            actual_path,
            dtype=torch.float32,
            device_map="cpu",
        )
        _processor = AutoProcessor.from_pretrained(actual_path)
        _tokenizer = AutoTokenizer.from_pretrained(actual_path)
        logger.info(
            "MiniCPM-V 4.6 loaded in %.1fs (CPU, float32) from %s",
            time.time() - t0, actual_path,
        )


def describe_image(
    image_path: str,
    prompt: str = "简洁描述这张图片内容，包括文字、表情、动作。中文，不超过40字。",
    max_new_tokens: int = 80,
) -> str:
    """Describe an image locally with MiniCPM-V 4.6.

    Returns description text, or "图片" if file missing / inference fails.
    Model is loaded on first call and stays resident.
    """
    if not os.path.exists(image_path):
        return "图片"

    try:
        import torch
        from PIL import Image

        _ensure_loaded()

        img = Image.open(image_path).convert("RGB")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": img},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        text = _tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = _processor(text=text, images=[img], return_tensors="pt")

        with torch.no_grad():
            out = _model.generate(
                **inputs, max_new_tokens=max_new_tokens, do_sample=False
            )
        result = _processor.batch_decode(
            out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True
        )[0]
        return result.strip() or "图片"
    except Exception as e:
        logger.warning("MiniCPM-V 4.6 describe_image failed for %s: %s", image_path, e)
        return "图片"


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "/home/{{USERNAME}}/Pictures/soyo_chibi_tea.jpg"
    t = time.time()
    desc = describe_image(path)
    print(f"[{time.time()-t:.1f}s] {desc}")