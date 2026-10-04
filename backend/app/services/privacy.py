"""What leaves the machine.

Embeddings and search run locally (Ollama). Only text placed in a prompt for a
cloud model leaves — and before it does, it passes through the profile's
redactions, so explicit words are replaced by the person's own aliases.
"""

from __future__ import annotations

import re

from app.models import Profile


def redact(text: str, profile: Profile | None) -> str:
    if not text or profile is None:
        return text
    for rule in profile.redactions or []:
        pattern, replacement = rule.get("pattern"), rule.get("replace", "[private]")
        if not pattern:
            continue
        try:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        except re.error:
            continue
    return text


def glossary_lines(profile: Profile, for_cloud: bool) -> list[str]:
    lines = []
    for g in profile.glossary or []:
        term, meaning = g.get("term"), g.get("meaning")
        if not term:
            continue
        if g.get("private") and for_cloud:
            lines.append(f"- {term}: private — refer to it only by this alias, never by name")
        else:
            lines.append(f"- {term}: {meaning}")
    return lines
