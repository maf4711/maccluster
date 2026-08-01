"""Sanitize untrusted strings for terminal output."""

from __future__ import annotations

import re

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def sanitize(value: str | None, *, max_len: int = 200) -> str:
    if value is None:
        return ""
    text = _ANSI.sub("", value)
    text = _CTRL.sub("", text)
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text
