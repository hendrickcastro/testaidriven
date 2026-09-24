"""Reference solution for the `lisp-interpreter` seed task: a small Scheme-like interpreter.

Proper tail calls are implemented with a `while True` loop in `_eval`: every expression in tail position
replaces `x`/`env` and loops instead of recursing, so tail recursion runs in constant Python stack.
"""

from __future__ import annotations

import re


class LispError(Exception):
    """Any error raised while reading or evaluating a program."""


class Symbol(str):
    __slots__ = ()


_SYMBOLS: dict[str, Symbol] = {}


def sym(name: str) -> Symbol:
    s = _SYMBOLS.get(name)
    if s is None:
        s = _SYMBOLS[name] = Symbol(name)
    return s


class _Nil:
    __slots__ = ()


NIL = _Nil()


class _Void:
    __slots__ = ()


VOID = _Void()


class Pair:
    __slots__ = ("car", "cdr")

    def __init__(self, car: object, cdr: object) -> None:
        self.car = car
        self.cdr = cdr


class Builtin:
    __slots__ = ("fn", "name")

    def __init__(self, name: str, fn) -> None:
        self.name, self.fn = name, fn


class Closure:
    __slots__ = ("body", "env", "params", "rest")

    def __init__(self, params: list[Symbol], rest: Symbol | None, body: list, env: Env) -> None:
        self.params, self.rest, self.body, self.env = params, rest, body, env


class Env:
    __slots__ = ("outer", "vars")

    def __init__(self, vars: dict, outer: Env | None = None) -> None:
        self.vars, self.outer = vars, outer

    def lookup(self, name: Symbol) -> object:
        e: Env | None = self
        while e is not None:
            v = e.vars
            if name in v:
                return v[name]
            e = e.outer
        raise LispError(f"unbound variable: {name}")

    def assign(self, name: Symbol, value: object) -> None:
        e: Env | None = self
        while e is not None:
            if name in e.vars:
                e.vars[name] = value
                return
            e = e.outer
        raise LispError(f"set! of unbound variable: {name}")


# --------------------------------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------------------------------

_TOKEN = re.compile(r"\s+|;[^\n]*|(,@|[()'`,]|[^\s()'`,;]+)")
_INT = re.compile(r"[+-]?\d+\Z")
QUOTE, QUASI, UNQUOTE, SPLICE = sym("quote"), sym("quasiquote"), sym("unquote"), sym("unquote-splicing")
_SHORT = {"'": QUOTE, "`": QUASI, ",": UNQUOTE, ",@": SPLICE}


def tokenize(src: str) -> list[str]:
    out = []
    pos = 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if m is None:  # pragma: no cover - the regex matches any char
            raise LispError(f"bad input at {pos}")
        if m.group(1):
            out.append(m.group(1))
        pos = m.end()
    return out


def from_list(items: list, tail: object = NIL) -> object:
    r = tail
    for x in reversed(items):
        r = Pair(x, r)
    return r


def to_list(x: object) -> list:
    out = []
    while isinstance(x, Pair):
        out.append(x.car)
        x = x.cdr
    if x is not NIL:
        raise LispError("expected a proper list")
    return out


def read_all(src: str) -> list:
    toks = tokenize(src)
    pos = 0

    def read() -> object:
        nonlocal pos
        if pos >= len(toks):
            raise LispError("unexpected end of input")
        tok = toks[pos]
        pos += 1
        if tok == "(":
            items = []
            tail: object = NIL
            while True:
                if pos >= len(toks):
                    raise LispError("missing )")
                if toks[pos] == ")":
                    pos += 1
                    break
                if toks[pos] == ".":
                    if not items:
                        raise LispError("bad dotted list")
                    pos += 1
                    tail = read()
                    if pos >= len(toks) or toks[pos] != ")":
                        raise LispError("bad dotted list")
                    pos += 1
                    break
                items.append(read())
            return from_list(items, tail)
        if tok == ")":
            raise LispError("unexpected )")
        if tok in _SHORT:
            return Pair(_SHORT[tok], Pair(read(), NIL))
        if _INT.match(tok):
            return int(tok)
        if tok == "#t":
            return True
        if tok == "#f":
            return False
        if tok == ".":
            raise LispError("unexpected .")
        return sym(tok)

    forms = []
    while pos < len(toks):
        forms.append(read())
    return forms


# --------------------------------------------------------------------------------------------------
# Printer
# --------------------------------------------------------------------------------------------------


def to_str(x: object) -> str:
    if x is True:
        return "#t"
    if x is False:
        return "#f"
    if x is NIL:
        return "()"
    if x is VOID:
        return ""
    if isinstance(x, (Builtin, Closure)):
        return "#<procedure>"
    if isinstance(x, Pair):
        parts = []
        while isinstance(x, Pair):
            parts.append(to_str(x.car))
            x = x.cdr
        if x is not NIL:
            parts.append(".")
            parts.append(to_str(x))
        return "(" + " ".join(parts) + ")"
    return str(x)


# --------------------------------------------------------------------------------------------------
# Evaluator
# --------------------------------------------------------------------------------------------------

S = {
    n: sym(n) for n in ("if", "define", "set!", "lambda", "begin", "let", "let*", "letrec", "cond", "else", "and", "or")
}
SPECIAL = frozenset(S.values()) | {QUOTE, QUASI}


def _params(spec: object) -> tuple[list[Symbol], Symbol | None]:
    params = []
    while isinstance(spec, Pair):
        if not isinstance(spec.car, Symbol):
            raise LispError("parameter must be a symbol")
        params.append(spec.car)
        spec = spec.cdr
    if spec is NIL:
        return params, None
    if isinstance(spec, Symbol):
        return params, spec
    raise LispError("bad parameter list")


def _bind(f: Closure, args: list) -> Env:
    n = len(f.params)
    if len(args) < n or (f.rest is None and len(args) != n):
        raise LispError("wrong number of arguments")
    d = dict(zip(f.params, args))
    if f.rest is not None:
        d[f.rest] = from_list(args[n:])
    return Env(d, f.env)


def apply_proc(f: object, args: list) -> object:
    if isinstance(f, Builtin):
        return f.fn(*args)
    if isinstance(f, Closure):
        env = _bind(f, args)
        for e in f.body[:-1]:
            _eval(e, env)
        return _eval(f.body[-1], env)
    raise LispError(f"not a procedure: {to_str(f)}")


def _qq(t: object, env: Env) -> object:
    if not isinstance(t, Pair):
        return t
    if t.car is UNQUOTE:
        return _eval(t.cdr.car, env)
    items: list = []
    while isinstance(t, Pair):
        if t.car is UNQUOTE:  # dotted tail: `(a . ,b)
            return from_list(items, _eval(t.cdr.car, env))
        e = t.car
        if isinstance(e, Pair) and e.car is SPLICE:
            items.extend(to_list(_eval(e.cdr.car, env)))
        else:
            items.append(_qq(e, env))
        t = t.cdr
    return from_list(items, t)


def _eval(x: object, env: Env) -> object:
    while True:
        if isinstance(x, Symbol):
            return env.lookup(x)
        if not isinstance(x, Pair):
            if x is NIL:
                raise LispError("cannot evaluate ()")
            return x
        op = x.car
        if op in SPECIAL and isinstance(op, Symbol):
            args = to_list(x.cdr)
            if op is QUOTE:
                return args[0]
            if op is QUASI:
                return _qq(args[0], env)
            if op is S["if"]:
                if len(args) not in (2, 3):
                    raise LispError("bad if")
                if _eval(args[0], env) is not False:
                    x = args[1]
                elif len(args) == 3:
                    x = args[2]
                else:
                    return VOID
                continue
            if op is S["define"]:
                target = args[0]
                if isinstance(target, Pair):
                    ps, rest = _params(target.cdr)
                    env.vars[target.car] = Closure(ps, rest, args[1:], env)
                else:
                    env.vars[target] = _eval(args[1], env)
                return VOID
            if op is S["set!"]:
                env.assign(args[0], _eval(args[1], env))
                return VOID
            if op is S["lambda"]:
                ps, rest = _params(args[0])
                if len(args) < 2:
                    raise LispError("empty lambda body")
                return Closure(ps, rest, args[1:], env)
            if op is S["begin"]:
                if not args:
                    return VOID
                for e in args[:-1]:
                    _eval(e, env)
                x = args[-1]
                continue
            if op is S["let"]:
                if isinstance(args[0], Symbol):  # named let
                    name, bindings, body = args[0], to_list(args[1]), args[2:]
                    names = [to_list(b)[0] for b in bindings]
                    vals = [_eval(to_list(b)[1], env) for b in bindings]
                    loop_env = Env({}, env)
                    proc = Closure(names, None, body, loop_env)
                    loop_env.vars[name] = proc
                    env = _bind(proc, vals)
                else:
                    bindings, body = to_list(args[0]), args[1:]
                    pairs = [to_list(b) for b in bindings]
                    env = Env({p[0]: _eval(p[1], env) for p in pairs}, env)
                for e in body[:-1]:
                    _eval(e, env)
                x = body[-1]
                continue
            if op is S["let*"] or op is S["letrec"]:
                new = Env({}, env)
                if op is S["letrec"]:
                    for b in to_list(args[0]):
                        new.vars[to_list(b)[0]] = VOID
                for b in to_list(args[0]):
                    name, init = to_list(b)
                    new.vars[name] = _eval(init, new)
                env = new
                body = args[1:]
                for e in body[:-1]:
                    _eval(e, env)
                x = body[-1]
                continue
            if op is S["cond"]:
                chosen = None
                for clause in args:
                    parts = to_list(clause)
                    if parts[0] is S["else"]:
                        chosen = parts[1:]
                        break
                    v = _eval(parts[0], env)
                    if v is not False:
                        if len(parts) == 1:
                            return v
                        chosen = parts[1:]
                        break
                if chosen is None:
                    return VOID
                for e in chosen[:-1]:
                    _eval(e, env)
                x = chosen[-1]
                continue
            if op is S["and"]:
                if not args:
                    return True
                for e in args[:-1]:
                    if _eval(e, env) is False:
                        return False
                x = args[-1]
                continue
            if op is S["or"]:
                if not args:
                    return False
                done = False
                for e in args[:-1]:
                    v = _eval(e, env)
                    if v is not False:
                        done = True
                        break
                if done:
                    return v
                x = args[-1]
                continue
        f = _eval(op, env)
        argv = []
        a = x.cdr
        while isinstance(a, Pair):
            argv.append(_eval(a.car, env))
            a = a.cdr
        if isinstance(f, Closure):
            env = _bind(f, argv)
            for e in f.body[:-1]:
                _eval(e, env)
            x = f.body[-1]
            continue
        if isinstance(f, Builtin):
            try:
                return f.fn(*argv)
            except LispError:
                raise
            except (TypeError, ZeroDivisionError, AttributeError) as exc:
                raise LispError(f"{f.name}: {exc}") from None
        raise LispError(f"not a procedure: {to_str(f)}")


# --------------------------------------------------------------------------------------------------
# Builtins
# --------------------------------------------------------------------------------------------------


def _num(x: object) -> int:
    if isinstance(x, bool) or not isinstance(x, int):
        raise LispError(f"not a number: {to_str(x)}")
    return x


def _chain(op):
    def f(*xs):
        if not xs:
            raise LispError("comparison needs arguments")
        ns = [_num(v) for v in xs]
        return all(op(a, b) for a, b in zip(ns, ns[1:]))

    return f


def _minus(*xs):
    if not xs:
        raise LispError("- needs arguments")
    ns = [_num(v) for v in xs]
    return -ns[0] if len(ns) == 1 else ns[0] - sum(ns[1:])


def _mul(*xs):
    r = 1
    for v in xs:
        r *= _num(v)
    return r


def _quotient(a, b):
    a, b = _num(a), _num(b)
    if b == 0:
        raise LispError("division by zero")
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _remainder(a, b):
    return _num(a) - _num(b) * _quotient(a, b)


def _modulo(a, b):
    a, b = _num(a), _num(b)
    if b == 0:
        raise LispError("division by zero")
    return a % b


def _eqv(a, b):
    if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) and not isinstance(b, bool):
        return a == b
    if isinstance(a, Symbol) and isinstance(b, Symbol):
        return a == b
    return a is b


def _equal(a, b):
    while isinstance(a, Pair) and isinstance(b, Pair):
        if not _equal(a.car, b.car):
            return False
        a, b = a.cdr, b.cdr
    return _eqv(a, b)


def _car(p):
    if not isinstance(p, Pair):
        raise LispError("car: not a pair")
    return p.car


def _cdr(p):
    if not isinstance(p, Pair):
        raise LispError("cdr: not a pair")
    return p.cdr


def _append(*xs):
    if not xs:
        return NIL
    items: list = []
    for lst in xs[:-1]:
        items.extend(to_list(lst))
    return from_list(items, xs[-1])


def _apply(f, *args):
    if not args:
        raise LispError("apply needs a list")
    return apply_proc(f, list(args[:-1]) + to_list(args[-1]))


def _map(f, lst):
    return from_list([apply_proc(f, [v]) for v in to_list(lst)])


def _global_env() -> Env:
    table = {
        "+": lambda *xs: sum(_num(v) for v in xs),
        "-": _minus,
        "*": _mul,
        "quotient": _quotient,
        "remainder": _remainder,
        "modulo": _modulo,
        "=": _chain(lambda a, b: a == b),
        "<": _chain(lambda a, b: a < b),
        ">": _chain(lambda a, b: a > b),
        "<=": _chain(lambda a, b: a <= b),
        ">=": _chain(lambda a, b: a >= b),
        "abs": lambda a: abs(_num(a)),
        "min": lambda a, *r: min(_num(v) for v in (a, *r)),
        "max": lambda a, *r: max(_num(v) for v in (a, *r)),
        "not": lambda a: a is False,
        "eq?": _eqv,
        "eqv?": _eqv,
        "equal?": _equal,
        "cons": lambda a, b: Pair(a, b),
        "car": _car,
        "cdr": _cdr,
        "list": lambda *xs: from_list(list(xs)),
        "length": lambda lst: len(to_list(lst)),
        "append": _append,
        "reverse": lambda lst: from_list(to_list(lst)[::-1]),
        "null?": lambda a: a is NIL,
        "pair?": lambda a: isinstance(a, Pair),
        "number?": lambda a: isinstance(a, int) and not isinstance(a, bool),
        "symbol?": lambda a: isinstance(a, Symbol),
        "boolean?": lambda a: isinstance(a, bool),
        "procedure?": lambda a: isinstance(a, (Builtin, Closure)),
        "zero?": lambda a: _num(a) == 0,
        "apply": _apply,
        "map": _map,
    }
    return Env({sym(k): Builtin(k, v) for k, v in table.items()})


def run(source: str) -> str:
    """Evaluate every top-level form in a fresh global environment; return the printed last value."""
    env = _global_env()
    result: object = VOID
    for form in read_all(source):
        result = _eval(form, env)
    return to_str(result)
