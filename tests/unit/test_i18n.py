"""Every UI string key used in the code must exist in both locales (constitution P10)."""

import json
import re
from pathlib import Path

from aidriven.domain.models import AgentTool, ReasoningMode, ResultStatus, RunStatus

UI = Path(__file__).resolve().parents[2] / "src" / "aidriven" / "ui"
KEY_RE = re.compile(r"""\bt\(\s*["']([a-z_]+\.[A-Za-z0-9_]+)["']""")
TUPLE_KEY_RE = re.compile(r"""["']((?:nav|score|dim|runs|compare|common|metric|results)\.[a-z0-9_]+)["']""")


def _locales() -> dict[str, dict[str, str]]:
    return {lang: json.loads((UI / "locales" / f"{lang}.json").read_text(encoding="utf-8")) for lang in ("en", "es")}


def _used_keys() -> set[str]:
    keys: set[str] = set()
    for f in UI.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        keys |= set(KEY_RE.findall(text))
        keys |= set(TUPLE_KEY_RE.findall(text))
    # dynamic families
    keys |= {f"status.{s.value}" for s in ResultStatus} | {f"status.{s.value}" for s in RunStatus}
    keys |= {f"reasoning.{m.value}" for m in ReasoningMode} | {f"tool.{x.value}" for x in AgentTool}
    keys |= {f"dim.{d}" for d in ("reliability", "performance", "conformity", "intelligence")}
    keys |= {f"score.{s}" for s in ("auto", "judge", "user", "final")}
    keys |= {f"difficulty.{d}" for d in ("medium", "hard", "extreme")}
    keys |= {f"source.{s}" for s in ("seed", "user", "import")}
    keys |= {
        f"cap.{k}"
        for k in (
            "vision",
            "tools",
            "json_mode",
            "streaming",
            "temperature",
            "top_p",
            "top_k",
            "seed",
            "stop_sequences",
            "verbosity",
        )
    }
    keys |= {f"rerun.scope_{s}" for s in ("full", "tasks", "results")} | {"rerun.mode_original", "rerun.mode_current"}
    keys |= {f"providers.keysrc_{s}" for s in ("saved", "env", "none")}
    return keys


def test_all_used_keys_exist_in_both_locales() -> None:
    locales = _locales()
    used = _used_keys()
    for lang, data in locales.items():
        missing = sorted(k for k in used if k not in data)
        assert not missing, f"{lang}: missing {missing}"


def test_locales_have_same_keys_and_placeholders() -> None:
    en, es = _locales()["en"], _locales()["es"]
    assert set(en) == set(es)
    ph = re.compile(r"\{(\w+)\}")
    for k in en:
        assert set(ph.findall(en[k])) == set(ph.findall(es[k])), k
