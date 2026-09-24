"""Reference solution for the `regex-engine` seed task: a backtracking regex engine with backreferences.

Parser -> AST of tuples; matcher in continuation-passing style so that backtracking order (greedy/lazy,
alternation priority) and capture restoration follow Perl/Python semantics.
"""

from __future__ import annotations

import sys

sys.setrecursionlimit(max(sys.getrecursionlimit(), 200000))

_DIGIT = frozenset("0123456789")
_WORD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_SPACE = frozenset(" \t\n\r\f\v")
_CLASS_ESC = {
    "d": (_DIGIT, False),
    "D": (_DIGIT, True),
    "w": (_WORD, False),
    "W": (_WORD, True),
    "s": (_SPACE, False),
    "S": (_SPACE, True),
}
_LIT_ESC = {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v"}


class _Parser:
    def __init__(self, p: str) -> None:
        self.p, self.i, self.ngroups = p, 0, 0

    def peek(self) -> str | None:
        return self.p[self.i] if self.i < len(self.p) else None

    def take(self) -> str:
        c = self.p[self.i]
        self.i += 1
        return c

    def parse(self):
        node = self.alt()
        if self.i != len(self.p):
            raise ValueError(f"unexpected {self.p[self.i]!r} at {self.i}")
        return node

    def alt(self):
        branches = [self.seq()]
        while self.peek() == "|":
            self.take()
            branches.append(self.seq())
        return branches[0] if len(branches) == 1 else ("alt", branches)

    def seq(self):
        items = []
        while self.peek() is not None and self.peek() not in "|)":
            items.append(self.quantified())
        return ("seq", items)

    def quantified(self):
        atom = self.atom()
        c = self.peek()
        lo = hi = None
        if c == "*":
            self.take()
            lo, hi = 0, None
        elif c == "+":
            self.take()
            lo, hi = 1, None
        elif c == "?":
            self.take()
            lo, hi = 0, 1
        elif c == "{":
            save = self.i
            q = self._brace()
            if q is None:
                self.i = save
                return atom
            lo, hi = q
        else:
            return atom
        lazy = False
        if self.peek() == "?":
            self.take()
            lazy = True
        return ("rep", atom, lo, hi, lazy)

    def _brace(self):
        self.take()  # {
        j = self.p.find("}", self.i)
        if j < 0:
            return None
        body = self.p[self.i : j]
        if "," in body:
            a, b = body.split(",", 1)
            if not a.isdigit() or (b and not b.isdigit()):
                return None
            lo, hi = int(a), (int(b) if b else None)
        else:
            if not body.isdigit():
                return None
            lo = hi = int(body)
        self.i = j + 1
        return lo, hi

    def atom(self):
        c = self.take()
        if c == "(":
            if self.p.startswith("?:", self.i):
                self.i += 2
                inner = self.alt()
                idx = None
            else:
                self.ngroups += 1
                idx = self.ngroups
                inner = self.alt()
            if self.peek() != ")":
                raise ValueError("missing )")
            self.take()
            return ("group", idx, inner)
        if c == "[":
            return self._class()
        if c == ".":
            return ("any",)
        if c == "^":
            return ("bol",)
        if c == "$":
            return ("eol",)
        if c == "\\":
            e = self.take()
            if e in _CLASS_ESC:
                s, neg = _CLASS_ESC[e]
                return ("set", s, (), neg)
            if e == "b":
                return ("wordb", False)
            if e == "B":
                return ("wordb", True)
            if e.isdigit() and e != "0":
                return ("backref", int(e))
            return ("char", _LIT_ESC.get(e, e))
        if c in "*+?":
            raise ValueError("nothing to repeat")
        return ("char", c)

    def _class(self):
        neg = False
        if self.peek() == "^":
            self.take()
            neg = True
        chars: set[str] = set()
        ranges: list[tuple[str, str]] = []
        first = True
        while True:
            c = self.peek()
            if c is None:
                raise ValueError("unterminated class")
            if c == "]" and not first:
                self.take()
                break
            first = False
            self.take()
            if c == "\\":
                e = self.take()
                if e in _CLASS_ESC:
                    s, eneg = _CLASS_ESC[e]
                    if eneg:
                        ranges.append(("neg", "".join(sorted(s))))
                    else:
                        chars |= s
                    continue
                c = _LIT_ESC.get(e, e)
            if self.peek() == "-" and self.i + 1 < len(self.p) and self.p[self.i + 1] != "]":
                self.take()
                d = self.take()
                if d == "\\":
                    d = self.take()
                    d = _LIT_ESC.get(d, d)
                ranges.append((c, d))
            else:
                chars.add(c)
        return ("set", frozenset(chars), tuple(ranges), neg)


def _in_set(node, ch: str) -> bool:
    _, chars, ranges, neg = node
    hit = ch in chars
    if not hit:
        for a, b in ranges:
            if a == "neg":
                if ch not in b:
                    hit = True
                    break
            elif a <= ch <= b:
                hit = True
                break
    return hit != neg


def _match(node, s: str, i: int, caps: tuple, k):
    t = node[0]
    if t == "char":
        if i < len(s) and s[i] == node[1]:
            return k(i + 1, caps)
        return None
    if t == "any":
        if i < len(s) and s[i] != "\n":
            return k(i + 1, caps)
        return None
    if t == "set":
        if i < len(s) and _in_set(node, s[i]):
            return k(i + 1, caps)
        return None
    if t == "seq":
        items = node[1]
        n = len(items)

        def go(idx: int, j: int, c: tuple):
            if idx == n:
                return k(j, c)
            return _match(items[idx], s, j, c, lambda j2, c2: go(idx + 1, j2, c2))

        return go(0, i, caps)
    if t == "alt":
        for b in node[1]:
            r = _match(b, s, i, caps, k)
            if r is not None:
                return r
        return None
    if t == "group":
        idx = node[1]
        if idx is None:
            return _match(node[2], s, i, caps, k)
        return _match(node[2], s, i, caps, lambda j, c: k(j, c[:idx] + ((i, j),) + c[idx + 1 :]))
    if t == "rep":
        _, sub, lo, hi, lazy = node

        def rep(j: int, c: tuple, count: int):
            can_more = hi is None or count < hi

            def more():
                if not can_more:
                    return None
                return _match(sub, s, j, c, lambda j2, c2: rep(j2, c2, count + 1) if (j2 != j or count < lo) else None)

            if count < lo:
                return more()
            if lazy:
                r = k(j, c)
                return r if r is not None else more()
            r = more()
            return r if r is not None else k(j, c)

        return rep(i, caps, 0)
    if t == "bol":
        return k(i, caps) if i == 0 else None
    if t == "eol":
        if i == len(s) or (i == len(s) - 1 and s[i] == "\n"):
            return k(i, caps)
        return None
    if t == "wordb":
        a = i > 0 and s[i - 1] in _WORD
        b = i < len(s) and s[i] in _WORD
        return k(i, caps) if (a != b) != node[1] else None
    if t == "backref":
        span = caps[node[1]] if node[1] < len(caps) else None
        if span is None:
            return None
        sub = s[span[0] : span[1]]
        if s.startswith(sub, i):
            return k(i + len(sub), caps)
        return None
    raise ValueError(t)


def search(pattern: str, text: str) -> list[tuple[int, int] | None] | None:
    parser = _Parser(pattern)
    root = parser.parse()
    empty = (None,) * (parser.ngroups + 1)
    for start in range(len(text) + 1):
        r = _match(root, text, start, empty, lambda j, c: (j, c))
        if r is not None:
            end, caps = r
            return [(start, end)] + list(caps[1:])
    return None
