"""A pure-Python stand-in for `jiter`, used only when its compiled module cannot
load — Windows' Smart App Control can start refusing it overnight ("an
application control policy has blocked this file"), and the OpenAI client
imports it on the first model call, so every AI feature would fail.

The OpenAI client uses one function, `from_json`, and only in its own streaming
helpers (LangChain does not call them): the standard json module does the work,
and a cut-off document is closed before parsing when partial_mode allows it.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["from_json"]


def _close(text: str) -> str:
    """Closes the strings, arrays and objects a truncated document left open."""
    stack: list[str] = []
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
        elif ch in "]}" and stack:
            stack.pop()
    out = text + ('"' if in_string else "")
    out = out.rstrip().rstrip(",")
    if out.endswith(":"):
        out += " null"
    return out + "".join(reversed(stack))


def from_json(json_data: bytes | str, *, allow_inf_nan: bool = True, cache_mode: Any = True,
              partial_mode: bool | str = False, catch_duplicate_keys: bool = False,
              float_mode: str = "float") -> Any:
    text = json_data.decode() if isinstance(json_data, (bytes, bytearray)) else json_data
    try:
        return json.loads(text)
    except ValueError:
        if not partial_mode or not text.strip():
            raise
        return json.loads(_close(text))
