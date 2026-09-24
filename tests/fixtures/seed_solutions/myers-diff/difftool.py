"""Reference solution for the `myers-diff` seed task: minimal unified diff (Myers O(ND))."""

from __future__ import annotations


def _edit_script(a: list[str], b: list[str]) -> list[tuple[str, int, int]]:
    """Minimal edit script as a list of ('=', i, j) / ('-', i, j) / ('+', i, j) steps (Myers greedy + trace)."""
    n, m = len(a), len(b)
    # trim common prefix / suffix
    pre = 0
    while pre < n and pre < m and a[pre] == b[pre]:
        pre += 1
    suf = 0
    while suf < n - pre and suf < m - pre and a[n - 1 - suf] == b[m - 1 - suf]:
        suf += 1
    A, B = a[pre : n - suf], b[pre : m - suf]
    N, M = len(A), len(B)
    ops: list[tuple[str, int, int]] = [("=", i, i) for i in range(pre)]
    core: list[tuple[str, int, int]] = []
    if N == 0:
        core = [("+", pre, pre + j) for j in range(M)]
    elif M == 0:
        core = [("-", pre + i, pre) for i in range(N)]
    else:
        # intern lines to ints for fast comparison
        ids: dict[str, int] = {}
        A2 = [ids.setdefault(x, len(ids)) for x in A]
        B2 = [ids.setdefault(x, len(ids)) for x in B]
        mx = N + M
        v = {1: 0}
        trace: list[dict[int, int]] = []
        done = False
        for d in range(mx + 1):
            trace.append(dict(v))
            for k in range(-d, d + 1, 2):
                if k == -d or (k != d and v[k - 1] < v[k + 1]):
                    x = v[k + 1]  # down (insertion)
                else:
                    x = v[k - 1] + 1  # right (deletion)
                y = x - k
                while x < N and y < M and A2[x] == B2[y]:
                    x += 1
                    y += 1
                v[k] = x
                if x >= N and y >= M:
                    done = True
                    break
            if done:
                break
        # backtrack
        x, y = N, M
        rev: list[tuple[str, int, int]] = []
        for d in range(len(trace) - 1, -1, -1):
            vv = trace[d]
            k = x - y
            if d == 0:
                while x > 0 and y > 0:
                    x -= 1
                    y -= 1
                    rev.append(("=", x, y))
                break
            if k == -d or (k != d and vv[k - 1] < vv[k + 1]):
                pk = k + 1
            else:
                pk = k - 1
            px = vv[pk]
            py = px - pk
            while x > px and y > py:
                x -= 1
                y -= 1
                rev.append(("=", x, y))
            if pk == k + 1:
                y -= 1
                rev.append(("+", x, y))
            else:
                x -= 1
                rev.append(("-", x, y))
        core = [(op, i + pre, j + pre) for op, i, j in reversed(rev)]
    ops += core
    ops += [("=", n - suf + i, m - suf + i) for i in range(suf)]
    return ops


def _blocks(ops: list[tuple[str, int, int]]) -> list[tuple[int, int, int, int]]:
    """Maximal change blocks as (i0, i1, j0, j1) half-open ranges in a and b."""
    out = []
    i = j = 0
    k = 0
    while k < len(ops):
        if ops[k][0] == "=":
            i += 1
            j += 1
            k += 1
            continue
        i0, j0 = i, j
        while k < len(ops) and ops[k][0] != "=":
            if ops[k][0] == "-":
                i += 1
            else:
                j += 1
            k += 1
        out.append((i0, i, j0, j))
    return out


def _range(start: int, length: int) -> str:
    if length == 1:
        return f"{start + 1}"
    return f"{start + 1 if length else start},{length}"


def unified_diff(a: list[str], b: list[str], context: int = 3) -> list[str]:
    blocks = _blocks(_edit_script(a, b))
    if not blocks:
        return []
    # group blocks into hunks
    groups: list[list[tuple[int, int, int, int]]] = [[blocks[0]]]
    for blk in blocks[1:]:
        if blk[0] - groups[-1][-1][1] <= 2 * context:
            groups[-1].append(blk)
        else:
            groups.append([blk])
    out: list[str] = []
    n = len(a)
    for g in groups:
        first, last = g[0], g[-1]
        lead = min(context, first[0])
        trail = min(context, n - last[1])
        a0, b0 = first[0] - lead, first[2] - lead
        a1, b1 = last[1] + trail, last[3] + trail
        out.append(f"@@ -{_range(a0, a1 - a0)} +{_range(b0, b1 - b0)} @@")
        i = a0
        for i0, i1, j0, j1 in g:
            out.extend(" " + a[x] for x in range(i, i0))
            out.extend("-" + a[x] for x in range(i0, i1))
            out.extend("+" + b[y] for y in range(j0, j1))
            i = i1
        out.extend(" " + a[x] for x in range(i, a1))
    return out
