"""UI translations (constitution P10, ADR-009). Only UI strings are translated; user data never is.

`t("key", **fmt)` looks the key up in the active locale, falls back to English, then to the key itself.
Locale files: `ui/locales/{en,es}.json`. A test checks every key used in the code exists in both.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

LANGUAGES = {"en": "English", "es": "Español"}
_current = {"lang": "en"}


@cache
def _locale(lang: str) -> dict[str, str]:
    path = resources.files("aidriven") / "ui" / "locales" / f"{lang}.json"
    data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    return data


def set_language(lang: str) -> None:
    _current["lang"] = lang if lang in LANGUAGES else "en"


def get_language() -> str:
    return _current["lang"]


def t(key_: str, /, **fmt: Any) -> str:
    text = _locale(_current["lang"]).get(key_) or _locale("en").get(key_) or key_
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text
