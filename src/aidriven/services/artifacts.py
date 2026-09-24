"""Artifact extraction from model responses.

Convention (the runner tells the model): every file comes in a fenced block whose opening line is
```<lang> file=<name>. Fallbacks, in order: `file:`/`filename=`/`title=` in the info string, a heading or
bold/backticked file name on the line right before the block, a `# file: name` first line, and finally
(if the task asks for exactly one file) the last block whose language matches its extension.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from aidriven.adapters.artifact_stores import safe_name
from aidriven.domain.models import ArtifactKind

_FENCE_RE = re.compile(r"^(?P<fence>`{3,}|~{3,})(?P<info>[^\n`]*)\n(?P<body>.*?)^(?P=fence)[ \t]*$", re.M | re.S)
_INFO_FILE_RE = re.compile(r"(?:file(?:name)?|title|path)\s*[=:]\s*[\"']?([^\s\"']+)", re.I)
_BARE_NAME_RE = re.compile(r"^[\w./-]+\.[A-Za-z0-9]{1,6}$")
_PRE_LINE_RE = re.compile(r"[`*_#\s]*(?:file(?:name)?\s*[:=]?\s*)?[`*_]*([\w./-]+\.[A-Za-z0-9]{1,6})[`*_:\s]*$", re.I)
_FIRST_LINE_RE = re.compile(r"^\s*(?:#|//|<!--|--)\s*(?:file(?:name)?)\s*[:=]\s*([\w./-]+\.[A-Za-z0-9]{1,6})", re.I)

EXT_KIND = {
    ".py": ArtifactKind.PYTHON,
    ".html": ArtifactKind.HTML,
    ".htm": ArtifactKind.HTML,
    ".js": ArtifactKind.JAVASCRIPT,
    ".mjs": ArtifactKind.JAVASCRIPT,
    ".json": ArtifactKind.JSON,
    ".sql": ArtifactKind.SQL,
    ".md": ArtifactKind.MARKDOWN,
}
LANG_EXT = {
    "python": ".py",
    "py": ".py",
    "html": ".html",
    "javascript": ".js",
    "js": ".js",
    "json": ".json",
    "sql": ".sql",
    "markdown": ".md",
    "md": ".md",
}
KIND_MIME = {
    ArtifactKind.PYTHON: "text/x-python",
    ArtifactKind.HTML: "text/html",
    ArtifactKind.JAVASCRIPT: "text/javascript",
    ArtifactKind.JSON: "application/json",
    ArtifactKind.SQL: "application/sql",
    ArtifactKind.MARKDOWN: "text/markdown",
    ArtifactKind.TEXT: "text/plain",
}


@dataclass
class ExtractedFile:
    name: str
    content: str
    kind: ArtifactKind
    explicit: bool  # named with the `file=` convention (conformity signal)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def mime(self) -> str:
        return KIND_MIME.get(self.kind, "text/plain")


def kind_for(name: str) -> ArtifactKind:
    return EXT_KIND.get(PurePosixPath(name).suffix.lower(), ArtifactKind.TEXT)


def _normalize_name(name: str) -> str:
    name = name.strip().strip("`*'\"")
    name = re.sub(r"^\./", "", name)
    return PurePosixPath(name).name if "/" in name else name


def extract_files(text: str, expected_names: list[str] | None = None) -> list[ExtractedFile]:
    expected_names = expected_names or []
    found: dict[str, ExtractedFile] = {}
    unnamed: list[tuple[str, str]] = []  # (lang, body)
    for m in _FENCE_RE.finditer(text):
        info = m.group("info").strip()
        body = m.group("body")
        if body.endswith("\n"):
            body = body[:-1]
        lang = info.split()[0].lower() if info else ""
        name, explicit = None, False
        im = _INFO_FILE_RE.search(info)
        if im:
            name, explicit = im.group(1), True
        else:
            tokens = info.split()
            if len(tokens) >= 2 and _BARE_NAME_RE.match(tokens[1]):
                name = tokens[1]
            elif len(tokens) == 1 and _BARE_NAME_RE.match(tokens[0]) and "." in tokens[0]:
                name, lang = tokens[0], ""
        if name is None:
            before = text[: m.start()].rstrip("\n").rsplit("\n", 1)[-1]
            pm = _PRE_LINE_RE.fullmatch(before.strip()) if before.strip() else None
            if pm and len(before) < 160:
                name = pm.group(1)
        if name is None:
            fm = _FIRST_LINE_RE.match(body)
            if fm:
                name = fm.group(1)
        if name:
            n = _normalize_name(name)
            found[n] = ExtractedFile(n, body, kind_for(n), explicit)  # last one wins (models often revise)
        else:
            unnamed.append((lang, body))
    # Single expected file and no named block: take the last block of a matching language.
    missing = [n for n in expected_names if n not in found]
    if len(expected_names) == 1 and missing and unnamed:
        target = expected_names[0]
        ext = PurePosixPath(target).suffix.lower()
        candidates = [b for lang, b in unnamed if LANG_EXT.get(lang) == ext] or [b for _, b in unnamed]
        found[target] = ExtractedFile(target, candidates[-1], kind_for(target), False)
    return list(found.values())


def artifact_storage_name(name: str) -> str:
    return safe_name(name)
