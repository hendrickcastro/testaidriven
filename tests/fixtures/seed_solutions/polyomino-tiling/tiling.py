"""Reference solution for the `polyomino-tiling` seed task.

Exact cover by backtracking on the first empty cell (row-major), with placements precomputed as bitmasks
and a connected-component area pruning (every empty component must have an area that can still be
assembled from the remaining piece sizes; for single-size piece sets, a multiple of that size).
"""

from __future__ import annotations

from functools import lru_cache


def _cells(shape: list[str]) -> list[tuple[int, int]]:
    return [(r, c) for r, row in enumerate(shape) for c, ch in enumerate(row) if ch not in ". "]


def _orientations(cells: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    seen = set()
    out = []
    pts = cells
    for flip in range(2):
        for _rot in range(4):
            pts = [(c, -r) for r, c in pts]  # rotate 90 degrees
            mr = min(r for r, _ in pts)
            mc = min(c for _, c in pts)
            norm = tuple(sorted((r - mr, c - mc) for r, c in pts))
            if norm not in seen:
                seen.add(norm)
                first = norm[0]  # smallest (row, col): the anchor cell
                out.append([(r - first[0], c - first[1]) for r, c in norm])
        pts = [(r, -c) for r, c in pts]  # mirror
        del flip
    return out


def count_tilings(region: list[str], pieces: list[list[str]]) -> int:
    width = max((len(row) for row in region), default=0)
    if width > len(region):
        # Scan along the short side: transpose (pieces are free, so the count is unchanged).
        region = ["".join(row[c] if c < len(row) else "#" for row in region) for c in range(width)]
    cells = [(r, c) for r, row in enumerate(region) for c, ch in enumerate(row) if ch == "."]
    index = {rc: i for i, rc in enumerate(cells)}
    n = len(cells)
    shapes = [_cells(p) for p in pieces]
    if sum(len(s) for s in shapes) != n:
        return 0
    if n == 0:
        return 1
    # placements[cell][piece] = list of bitmasks of placements whose anchor is that cell
    placements: list[list[list[int]]] = [[[] for _ in shapes] for _ in range(n)]
    for pi, shape in enumerate(shapes):
        for orient in _orientations(shape):
            for (r, c), i in index.items():
                bits = 0
                for dr, dc in orient:
                    j = index.get((r + dr, c + dc))
                    if j is None:
                        break
                    bits |= 1 << j
                else:
                    placements[i][pi].append(bits)
    neighbours = []
    for r, c in cells:
        nb = 0
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            j = index.get((r + dr, c + dc))
            if j is not None:
                nb |= 1 << j
        neighbours.append(nb)
    full = (1 << n) - 1
    sizes = [len(s) for s in shapes]

    @lru_cache(maxsize=None)
    def achievable(remaining: int) -> frozenset[int]:
        """All subset sums of the sizes of the pieces in bitmask `remaining`."""
        sums = {0}
        for pi in range(len(sizes)):
            if remaining >> pi & 1:
                sums |= {s + sizes[pi] for s in sums}
        return frozenset(sums)

    def components_ok(filled: int, remaining: int) -> bool:
        empty = full & ~filled
        ok_sums = achievable(remaining)
        while empty:
            seed = empty & -empty
            comp = seed
            frontier = seed
            while frontier:
                b = frontier & -frontier
                frontier ^= b
                nb = neighbours[b.bit_length() - 1] & empty & ~comp
                comp |= nb
                frontier |= nb
            if comp.bit_count() not in ok_sums:
                return False
            empty &= ~comp
        return True

    def solve(filled: int, remaining: int, depth: int) -> int:
        if filled == full:
            return 1
        free = ~filled & full
        i = (free & -free).bit_length() - 1
        total = 0
        options = placements[i]
        r = remaining
        while r:
            pb = r & -r
            r ^= pb
            pi = pb.bit_length() - 1
            for bits in options[pi]:
                if bits & filled:
                    continue
                nf = filled | bits
                nr = remaining ^ pb
                if depth % 2 == 0 and not components_ok(nf, nr):
                    continue
                total += solve(nf, nr, depth + 1)
        return total

    return solve(0, (1 << len(shapes)) - 1, 0)
