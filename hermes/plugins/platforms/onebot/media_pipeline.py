import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MEDIA_TASK_TIMEOUT = 30.0


class MediaPipeline:
    """Owns all media (image/sticker) download+describe tasks.

    Tasks are registered by message seq and tracked until completion
    or the pipeline is shut down.  Completion updates the
    BufferedMessage object directly (by object reference, not by
    buffer[-1] assumption).

    The persist callback is an async callable(group_id, seq, hint, descs)
    that sends an UPDATE_MEDIA operation into the adapter's persist
    queue.
    """

    def __init__(self, adapter):
        self._adapter = adapter
        self._tasks: Dict[int, asyncio.Task] = {}

    # ── public ──────────────────────────────────────────────

    def start(self, buffered, raw_msg: dict) -> asyncio.Task:
        """Launch a fire-and-forget media task, registered by seq.

        The returned task is also stored in self._tasks.
        Callers are NOT required to await it; the task finishes
        independently and updates buffered in-place.
        """
        task = asyncio.create_task(self._process(buffered, raw_msg))
        self._tasks[buffered.seq] = task
        task.add_done_callback(lambda _t: self._cleanup(buffered.seq))
        return task

    async def await_completion(self, seq: int, timeout: float = MEDIA_TASK_TIMEOUT) -> Tuple[str, List[str]]:
        """Wait for a specific media task to finish.

        Returns (image_hint, image_descs).  If the task is not
        found or times out, returns ("", []).
        """
        task = self._tasks.get(seq)
        if task is None:
            return "", []
        try:
            return await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("[MediaPipeline] Task seq=%d timed out after %.0fs", seq, timeout)
            return " [image:timeout]", []
        except Exception as e:
            logger.warning("[MediaPipeline] Task seq=%d failed: %s", seq, e)
            return "", []

    def cancel_all(self):
        """Cancel every in-flight task.  Used on disconnect."""
        for seq, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
        self._tasks.clear()

    # ── internal ────────────────────────────────────────────

    def _cleanup(self, seq: int):
        self._tasks.pop(seq, None)

    async def _process(self, buffered, raw_msg: dict) -> Tuple[str, List[str]]:
        """Download images and describe them.  Returns (image_hint, image_descs).

        On success the buffered message fields are updated in-place.
        """
        try:
            img_paths = await self._adapter._get_image_files(raw_msg)
        except Exception:
            img_paths = []

        if not img_paths:
            buffered.text = buffered.text.replace(" [image:pending]", "")
            return "", []

        image_hint = " [image:" + ",".join(img_paths) + "]"
        is_sticker = self._adapter._has_sticker_message(raw_msg)

        descs = []
        for ip in img_paths[:5]:
            for p in ip.split(","):
                p = p.strip()
                if not p or p == "download_failed":
                    continue
                try:
                    d = await self._adapter._describe_image(p, is_sticker=is_sticker)
                    tag = "表情包" if is_sticker else "图片"
                    descs.append(f"[{tag}: {d}]")
                except Exception:
                    pass

        # Update the BufferedMessage object in-place (by reference)
        base = buffered.text.replace(" [image:pending]", "")
        buffered.text = base + image_hint
        if descs:
            buffered.text += " " + " ".join(descs)
            buffered.descriptions = descs
        else:
            buffered.descriptions = []

        return image_hint, descs
