#!/home/{{USERNAME}}/.hermes/.venv/bin/python3
"""Video/image understanding via SmolVLM2 (transformers, 256M params, ~1GB).

Usage:
    video_understand.py <image_or_video_path> [--prompt "custom prompt"]

For videos: extracts key frames via ffmpeg, describes each frame, combines.
For images: direct inference.
"""

import os, sys, subprocess, tempfile, time, base64, logging

logger = logging.getLogger("video_understand")

MODEL_ID = "HuggingFaceTB/SmolVLM2-256M-Video-Instruct"
CACHE_DIR = os.path.expanduser("~/.hermes/models/SmolVLM2")

# Prevent network access — model already cached locally
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import torch
torch.set_num_threads(4)  # Leave headroom for gateway

_processor = None
_model = None


def _load_model():
    global _processor, _model
    if _model is not None:
        return _processor, _model

    from transformers import AutoProcessor, AutoModelForImageTextToText
    import torch
    from PIL import Image

    _processor = AutoProcessor.from_pretrained(MODEL_ID, cache_dir=CACHE_DIR, local_files_only=True)
    _model = AutoModelForImageTextToText.from_pretrained(
        MODEL_ID, cache_dir=CACHE_DIR, dtype=torch.float32, device_map="cpu",
        local_files_only=True,
    )
    logger.info("SmolVLM2 loaded (256M params, transformers)")
    return _processor, _model


def describe_image(image_path: str, prompt: str = None) -> str:
    """Describe a single image."""
    from PIL import Image
    import torch

    processor, model = _load_model()
    img = Image.open(image_path).convert("RGB")

    user_prompt = prompt or "Describe this image briefly in English."
    messages = [{"role": "user", "content": [
        {"type": "image", "url": "http://x"},
        {"type": "text", "text": user_prompt},
    ]}]
    prompt_text = processor.apply_chat_template(messages, add_generation_prompt=True)
    inputs = processor(text=prompt_text, images=[img], return_tensors="pt")

    t0 = time.time()
    with torch.no_grad():
        outputs = model.generate(**inputs, max_new_tokens=80)
    text = processor.decode(outputs[0], skip_special_tokens=True)
    if "Assistant:" in text:
        text = text.split("Assistant:")[-1]
    logger.debug("describe_image: %.1fs → %s", time.time() - t0, text[:60])
    return text.strip()


def describe_video(video_path: str, num_frames: int = 4) -> str:
    """Extract key frames and describe video content."""
    import re as _re

    if not os.path.exists(video_path):
        return "[视频: 文件不存在]"

    # Extract frames
    temp_dir = tempfile.mkdtemp(prefix="vlm_frames_")
    try:
        dur = float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            timeout=10).strip())
    except Exception:
        dur = 30.0

    interval = max(1.0, dur / (num_frames + 1))
    frames = []
    for i in range(num_frames):
        t = interval * (i + 1)
        out = os.path.join(temp_dir, f"f{i:02d}.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", video_path,
             "-vframes", "1", "-q:v", "3", out],
            capture_output=True, timeout=15,
        )
        if os.path.exists(out) and os.path.getsize(out) > 100:
            frames.append(out)

    if not frames:
        return "[视频: 无法提取帧]"

    # Describe each frame
    descriptions = []
    for fp in frames:
        desc = describe_image(fp, "Describe this video frame briefly in English.")
        descriptions.append(desc)

    # Cleanup
    for fp in frames:
        try: os.remove(fp)
        except: pass
    try: os.rmdir(temp_dir)
    except: pass

    combined = " | ".join(d for d in descriptions if d)
    return combined if combined else "[视频: 无法描述内容]"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: video_understand.py <path> [--frames N]")
        sys.exit(1)

    path = sys.argv[1]
    frames = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--frames" else 4

    ext = os.path.splitext(path)[1].lower()
    if ext in (".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv"):
        result = describe_video(path, frames)
    else:
        result = describe_image(path)

    print(result)
