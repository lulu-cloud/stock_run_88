"""Small deterministic helpers for extracting model-produced JSON."""

from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)\s*```", re.I)


def _strip_json_noise(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("\ufeff"):
        value = value[1:].lstrip()
    value = re.sub(r"^\s*(?:json|JSON)\s*[:：]\s*", "", value)
    value = value.replace("\u201c", '"').replace("\u201d", '"')
    value = value.replace("\u2018", "'").replace("\u2019", "'")
    value = re.sub(r",\s*([}\]])", r"\1", value)
    return value.strip()


def _balanced_json_slices(text: str) -> list[str]:
    slices: list[str] = []
    raw = text or ""
    for open_char, close_char in (("{", "}"), ("[", "]")):
        stack = 0
        start = -1
        in_string = False
        escape = False
        for idx, char in enumerate(raw):
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == open_char:
                if stack == 0:
                    start = idx
                stack += 1
            elif char == close_char and stack:
                stack -= 1
                if stack == 0 and start >= 0:
                    slices.append(raw[start:idx + 1])
                    start = -1
    return slices


def json_candidates(text: str) -> list[str]:
    """Return likely JSON snippets from noisy LLM output, most specific first."""
    raw = text or ""
    candidates: list[str] = []
    candidates.extend(match.group(1) for match in _FENCE_RE.finditer(raw))
    candidates.extend(_balanced_json_slices(raw))
    candidates.append(raw)

    seen: set[str] = set()
    cleaned: list[str] = []
    for item in candidates:
        value = _strip_json_noise(item)
        if value and value not in seen:
            seen.add(value)
            cleaned.append(value)
    return cleaned


def extract_json_value(text: str, expected_type: type | tuple[type, ...] = dict) -> Any:
    for item in json_candidates(text):
        try:
            data = json.loads(item)
        except json.JSONDecodeError:
            continue
        if expected_type is None or isinstance(data, expected_type):
            return data
    return None


def extract_json_object(text: str) -> dict:
    data = extract_json_value(text, dict)
    return data if isinstance(data, dict) else {}
