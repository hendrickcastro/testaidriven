"""Reference solution for the `semver-ranges` seed task: SemVer 2.0.0 parsing/precedence + npm-style ranges."""

from __future__ import annotations

import re

_NUM = r"(?:0|[1-9][0-9]*)"
_PRE = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_BUILD = r"[0-9A-Za-z-]+"
_SEMVER = re.compile(rf"({_NUM})\.({_NUM})\.({_NUM})(?:-({_PRE}(?:\.{_PRE})*))?(?:\+({_BUILD}(?:\.{_BUILD})*))?")
_XR = r"(?:[xX*]|0|[1-9][0-9]*)"
_PARTIAL = re.compile(rf"({_XR})(?:\.({_XR})(?:\.({_XR})(?:-({_PRE}(?:\.{_PRE})*))?(?:\+({_BUILD}(?:\.{_BUILD})*))?)?)?")

Version = tuple  # (major, minor, patch, prerelease, build)


def parse(s: str) -> Version:
    if not isinstance(s, str):
        raise ValueError("version must be a string")
    m = _SEMVER.fullmatch(s)
    if not m:
        raise ValueError(f"invalid version: {s!r}")
    pre = tuple(int(x) if x.isdigit() else x for x in m.group(4).split(".")) if m.group(4) else ()
    build = tuple(m.group(5).split(".")) if m.group(5) else ()
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre, build)


def _cmp(a, b) -> int:
    return (a > b) - (a < b)


def _cmp_pre(a: tuple, b: tuple) -> int:
    if a == b:
        return 0
    if not a:
        return 1
    if not b:
        return -1
    for x, y in zip(a, b):
        if x == y:
            continue
        xi, yi = isinstance(x, int), isinstance(y, int)
        if xi and yi:
            return _cmp(x, y)
        if xi:
            return -1
        if yi:
            return 1
        return _cmp(x, y)
    return _cmp(len(a), len(b))


def _cmpv(a: Version, b: Version) -> int:
    return _cmp(a[:3], b[:3]) or _cmp_pre(a[3], b[3])


def compare(a: str, b: str) -> int:
    return _cmpv(parse(a), parse(b))


# ------------------------------------------------------------------------------------------------
# Ranges: a range set is a list of comparator sets; a comparator set is a list of (op, version)
# with op in {"<", "<=", ">", ">=", "="}; ANY = no comparator; NONE = ("none", None).
# ------------------------------------------------------------------------------------------------

_NONE = ("none", None)


def _v(M: int, m: int, p: int, pre: tuple = ()) -> Version:
    return (M, m, p, pre, ())


def _partial(text: str):
    mt = _PARTIAL.fullmatch(text)
    if not mt:
        raise ValueError(f"invalid partial version: {text!r}")
    parts = [mt.group(1), mt.group(2), mt.group(3)]
    nums: list[int | None] = []
    seen_x = False
    for part in parts:
        if part is None or part in ("x", "X", "*"):
            seen_x = True
            nums.append(None)
        else:
            if seen_x:
                raise ValueError(f"number after wildcard in {text!r}")
            nums.append(int(part))
    if (mt.group(4) or mt.group(5)) and None in nums:  # e.g. "1.2.x-beta"
        raise ValueError("qualifier on a partial version")
    pre = parse(text)[3] if None not in nums else ()
    return nums, pre


def _primitive(op: str, text: str) -> list:
    (M, m, p), pre = _partial(text)
    if p is not None:
        return [(op or "=", _v(M, m, p, pre))]
    if M is None:
        return [_NONE] if op in (">", "<") else []
    if op in ("", "="):
        if m is None:
            return [(">=", _v(M, 0, 0)), ("<", _v(M + 1, 0, 0, (0,)))]
        return [(">=", _v(M, m, 0)), ("<", _v(M, m + 1, 0, (0,)))]
    if op == ">":
        return [(">=", _v(M + 1, 0, 0) if m is None else _v(M, m + 1, 0))]
    if op == ">=":
        return [(">=", _v(M, m or 0, 0))]
    if op == "<":
        return [("<", _v(M, m or 0, 0, (0,)))]
    if op == "<=":
        return [("<", _v(M + 1, 0, 0, (0,)) if m is None else _v(M, m + 1, 0, (0,)))]
    raise ValueError(op)


def _tilde(text: str) -> list:
    (M, m, p), pre = _partial(text)
    if M is None:
        return []
    if m is None:
        return [(">=", _v(M, 0, 0)), ("<", _v(M + 1, 0, 0, (0,)))]
    return [(">=", _v(M, m, p or 0, pre)), ("<", _v(M, m + 1, 0, (0,)))]


def _caret(text: str) -> list:
    (M, m, p), pre = _partial(text)
    if M is None:
        return []
    lo = _v(M, m or 0, p or 0, pre)
    if m is None:
        hi = _v(M + 1, 0, 0, (0,))
    elif p is None:
        hi = _v(M + 1, 0, 0, (0,)) if M > 0 else _v(0, m + 1, 0, (0,))
    elif M > 0:
        hi = _v(M + 1, 0, 0, (0,))
    elif m > 0:
        hi = _v(0, m + 1, 0, (0,))
    else:
        hi = _v(0, 0, p + 1, (0,))
    return [(">=", lo), ("<", hi)]


def _hyphen(a: str, b: str) -> list:
    (M1, m1, p1), pre1 = _partial(a)
    (M2, m2, p2), pre2 = _partial(b)
    out = []
    if M1 is not None:
        out.append((">=", _v(M1, m1 or 0, p1 or 0, pre1)))
    if M2 is not None:
        if p2 is not None:
            out.append(("<=", _v(M2, m2, p2, pre2)))
        elif m2 is not None:
            out.append(("<", _v(M2, m2 + 1, 0, (0,))))
        else:
            out.append(("<", _v(M2 + 1, 0, 0, (0,))))
    return out


_SIMPLE = re.compile(r"(>=|<=|>|<|=|~|\^)?(.*)", re.S)


def _parse_set(text: str) -> list:
    text = text.strip()
    if not text:
        return []
    hy = re.fullmatch(r"(\S+)\s+-\s+(\S+)", text)
    if hy:
        return _hyphen(hy.group(1), hy.group(2))
    text = re.sub(r"(>=|<=|>|<|=|~|\^)\s+", r"\1", text)
    comps: list = []
    for tok in text.split():
        op, rest = _SIMPLE.fullmatch(tok).groups()
        if op == "~":
            comps += _tilde(rest)
        elif op == "^":
            comps += _caret(rest)
        else:
            comps += _primitive(op or "", rest)
    return comps


def parse_range(rng: str) -> list:
    if not isinstance(rng, str):
        raise ValueError("range must be a string")
    return [_parse_set(part) for part in rng.split("||")]


def _test(op: str, v: Version, c: Version) -> bool:
    r = _cmpv(v, c)
    return {"<": r < 0, "<=": r <= 0, ">": r > 0, ">=": r >= 0, "=": r == 0}[op]


def _set_ok(comps: list, v: Version) -> bool:
    for op, c in comps:
        if op == "none" or not _test(op, v, c):
            return False
    if v[3]:
        return any(op != "none" and c[3] and c[:3] == v[:3] for op, c in comps)
    return True


def satisfies(version: str, rng: str) -> bool:
    v = parse(version)
    return any(_set_ok(s, v) for s in parse_range(rng))


def max_satisfying(versions: list[str], rng: str) -> str | None:
    sets = parse_range(rng)
    best = None
    best_v = None
    for s in versions:
        try:
            v = parse(s)
        except ValueError:
            continue
        if any(_set_ok(cs, v) for cs in sets) and (best_v is None or _cmpv(v, best_v) > 0):
            best, best_v = s, v
    return best
