"""JSON a model wrote, read the way it meant it, without guessing.

The compliance analyst answers in one JSON object. Now and then the model writes a slip that a
strict reader refuses: a trailing comma before `]` or `}`, a key with no value, a raw line break
inside a string. One slip used to cost the whole analysis, and the turn fell back to a summary
written without it ("Expecting value: line 1 column 8944", 27 Oct 2026).

`loads` repairs only those slips, only outside string literals, and only in ways that change no
value the model wrote: a dropped trailing comma, `null` for a value that is not there. A text cut
off before its end is not completed: guessing the rest would invent an answer, so it still fails.
The repaired object goes through the same validation as any other.
"""
from __future__ import annotations

import json
from typing import Any


def _repair(text: str) -> tuple[str, list[str]]:
    out: list[str] = []
    fixes: list[str] = []
    in_string = False
    escaped = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "]}":
                fixes.append("trailing comma")
                i += 1
                continue
        if ch == ":":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in ",}":
                fixes.append("missing value")
                out.append(": null")
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out), fixes


def loads(text: str) -> tuple[Any, list[str]]:
    """(the parsed value, the slips repaired). Raises json.JSONDecodeError as json.loads does when
    the text cannot be read without guessing."""
    try:
        return json.loads(text), []
    except json.JSONDecodeError as first:
        repaired, fixes = _repair(text)
        try:
            value = json.loads(repaired, strict=False)
        except json.JSONDecodeError:
            raise first from None
        return value, (fixes or ["control character in a string"])
