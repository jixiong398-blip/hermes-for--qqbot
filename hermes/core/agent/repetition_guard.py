"""Detect repetition-dominated fragments before truncated responses continue.

The length-continuation path normally asks a model to continue a response
after ``finish_reason=length``.  A model caught in a repetition loop can
spend its entire output budget echoing one fragment, so continuing would
only stitch more repeated text into the response.  These helpers provide a
conservative, provider-independent check for that failure mode.

Only long verbatim repeats whose occurrences cover a majority of the
fragment are blocked.  Short truncations, ordinary repeated headings, and
similar-looking code fail open and keep the existing continuation behavior.
"""

from __future__ import annotations

import math


# Short truncations can contain repeated tokens naturally and should still be
# eligible for continuation.
MIN_FRAGMENT_LENGTH = 400

# A 60-character verbatim repeat is far beyond ordinary phrasing reuse.
_REPEAT_WINDOW = 60
_MIN_REPEAT_COUNT = 5
_DOMINANCE_RATIO = 0.5


def is_repetition_dominated(text: str) -> bool:
    """Return whether ``text`` is dominated by a repeated verbatim fragment.

    Non-string, empty, and short values return ``False`` so this guard cannot
    block a continuation when it cannot confidently classify the response.
    """
    if not isinstance(text, str):
        return False
    length = len(text)
    if length < MIN_FRAGMENT_LENGTH:
        return False

    # The line path catches the common repeated-paragraph shape without
    # allocating a sliding-window entry for every character.
    if _line_repetition_dominated(text, length):
        return True

    # The general path catches repeats that do not align with line boundaries.
    needed = max(
        _MIN_REPEAT_COUNT,
        math.ceil(length * _DOMINANCE_RATIO / _REPEAT_WINDOW),
    )
    counts: dict[str, int] = {}
    for index in range(length - _REPEAT_WINDOW + 1):
        window = text[index : index + _REPEAT_WINDOW]
        count = counts.get(window, 0) + 1
        if count >= needed:
            return True
        counts[window] = count
    return False


def _line_repetition_dominated(text: str, length: int) -> bool:
    """Return whether one normalized line accounts for half the fragment."""
    counts: dict[str, int] = {}
    for line in text.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        counts[normalized] = counts.get(normalized, 0) + 1

    return any(
        count >= _MIN_REPEAT_COUNT
        and count * len(line) >= length * _DOMINANCE_RATIO
        for line, count in counts.items()
    )
