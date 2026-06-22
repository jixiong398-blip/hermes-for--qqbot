"""Relative date resolution for memory distillation.

Resolves Chinese relative date expressions (明天/后天/下周三/昨晚/大后天...)
into ISO dates (YYYY-MM-DD) using a reference timestamp.

Zero external dependencies beyond stdlib — no dateparser required.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple


_RELATIVE_PATTERNS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"大前天"), -3),
    (re.compile(r"大后天|大後天"), 3),
    (re.compile(r"前天|前日"), -2),
    (re.compile(r"后天|後天"), 2),
    (re.compile(r"昨天|昨日|昨兒|昨晚"), -1),
    (re.compile(r"明天|明日|明兒"), 1),
    (re.compile(r"今天|今日|今兒"), 0),
]

_WEEKDAY_MAP = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}

_WEEKDAY_PATTERN = re.compile(r"下周(一|二|三|四|五|六|日|天)|下週(一|二|三|四|五|六|日|天)")
_THIS_WEEK_PATTERN = re.compile(r"本周(一|二|三|四|五|六|日|天)|本週(一|二|三|四|五|六|日|天)")
_AFTER_DAYS_PATTERN = re.compile(r"(\d+)天后|(\d+)天後|(\d+)日后|(\d+)日後")
_BEFORE_DAYS_PATTERN = re.compile(r"(\d+)天前|(\d+)日前")


def _resolve_weekday(ref: datetime, target_dow: int, weeks_ahead: int = 1) -> datetime:
    current_dow = ref.weekday()
    days_until = target_dow - current_dow
    if weeks_ahead == 1:
        if days_until <= 0:
            days_until += 7
    elif weeks_ahead == 0:
        if days_until < 0:
            days_until += 7
    return ref + timedelta(days=days_until)


def resolve_relative_dates(text: str, reference_ts: float,
                           lang: str = "zh") -> Tuple[str, list[str]]:
    """Replace relative date expressions in text with ISO dates.

    Args:
        text: Input text that may contain relative dates.
        reference_ts: Unix timestamp to resolve relative dates against.
        lang: Language hint ("zh" or "en"). Currently only "zh" implemented.

    Returns:
        (resolved_text, list_of_iso_dates_found)
    """
    if not text or not reference_ts:
        return text, []

    ref = datetime.fromtimestamp(reference_ts)
    ref_date = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    found_dates: list[str] = []

    spans: list[tuple[int, int, str]] = []

    for pattern, days_offset in _RELATIVE_PATTERNS:
        for m in pattern.finditer(text):
            target = ref_date + timedelta(days=days_offset)
            iso = target.strftime("%Y-%m-%d")
            found_dates.append(iso)
            spans.append((m.start(), m.end(), f"{iso}({m.group(0)})"))

    for m in _WEEKDAY_PATTERN.finditer(text):
        dow_char = m.group(1)
        target_dow = _WEEKDAY_MAP.get(dow_char, 0)
        target = _resolve_weekday(ref_date, target_dow, weeks_ahead=1)
        iso = target.strftime("%Y-%m-%d")
        found_dates.append(iso)
        spans.append((m.start(), m.end(), f"{iso}({m.group(0)})"))

    for m in _THIS_WEEK_PATTERN.finditer(text):
        dow_char = m.group(1)
        target_dow = _WEEKDAY_MAP.get(dow_char, 0)
        target = _resolve_weekday(ref_date, target_dow, weeks_ahead=0)
        iso = target.strftime("%Y-%m-%d")
        found_dates.append(iso)
        spans.append((m.start(), m.end(), f"{iso}({m.group(0)})"))

    for m in _AFTER_DAYS_PATTERN.finditer(text):
        for g in m.groups():
            if g:
                n = int(g)
                target = ref_date + timedelta(days=n)
                iso = target.strftime("%Y-%m-%d")
                found_dates.append(iso)
                spans.append((m.start(), m.end(), f"{iso}({m.group(0)})"))
                break

    for m in _BEFORE_DAYS_PATTERN.finditer(text):
        n = int(m.group(1))
        target = ref_date - timedelta(days=n)
        iso = target.strftime("%Y-%m-%d")
        found_dates.append(iso)
        spans.append((m.start(), m.end(), f"{iso}({m.group(0)})"))

    spans.sort(key=lambda s: s[0])
    filtered: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, replacement in spans:
        if start >= last_end:
            filtered.append((start, end, replacement))
            last_end = end

    result = text
    for start, end, replacement in reversed(filtered):
        result = result[:start] + replacement + result[end:]

    return result, found_dates
