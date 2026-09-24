"""Deterministic generator for the seed task assets (standard library only).

Writes into src/aidriven/seed/assets/:
  * shapes_grid.png          - vision task (09x07 grid of coloured shapes, pixel-drawn, PNG via zlib+struct)
  * norrbeck_staff.txt       - long-context task, staff directory + transfers
  * norrbeck_ledger_h1.txt   - long-context task, expense ledger (Jan-Jun 2025)
  * norrbeck_finance_policy.txt - long-context task, charging rules + FX table + correction

Run:  python tools/gen_seed_assets.py            (regenerate)
      python tools/gen_seed_assets.py --answers  (also print the computed ground-truth answers)

The output is committed; re-running must reproduce it byte for byte.
"""

from __future__ import annotations

import random
import struct
import sys
import zlib
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "aidriven" / "seed" / "assets"

# ------------------------------------------------------------------------------------------------
# PNG writer
# ------------------------------------------------------------------------------------------------


def write_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    """Write an 8-bit RGB PNG. `pixels` is row-major RGB, len = width*height*3."""
    stride = width * 3
    raw = b"".join(b"\x00" + bytes(pixels[y * stride : (y + 1) * stride]) for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


class Canvas:
    def __init__(self, width: int, height: int, bg: tuple[int, int, int]) -> None:
        self.w, self.h = width, height
        self.px = bytearray(bytes(bg) * (width * height))

    def set(self, x: int, y: int, c: tuple[int, int, int]) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i : i + 3] = bytes(c)

    def rect(self, x0: int, y0: int, x1: int, y1: int, c: tuple[int, int, int]) -> None:
        for y in range(y0, y1):
            for x in range(x0, x1):
                self.set(x, y, c)

    def circle(self, cx: int, cy: int, r: int, c: tuple[int, int, int]) -> None:
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self.set(x, y, c)

    def triangle(self, cx: int, cy: int, r: int, c: tuple[int, int, int]) -> None:
        """Upward isosceles triangle inscribed in the box [cx-r, cx+r] x [cy-r, cy+r]."""
        top, bottom = cy - r, cy + r
        for y in range(top, bottom + 1):
            half = (y - top) * r / (2 * r)
            for x in range(round(cx - half), round(cx + half) + 1):
                self.set(x, y, c)


# 5x7 bitmap digits
DIGITS = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
}


def draw_text(cv: Canvas, x: int, y: int, text: str, scale: int, c: tuple[int, int, int]) -> None:
    for ch in text:
        for ry, row in enumerate(DIGITS[ch]):
            for rx, bit in enumerate(row):
                if bit == "1":
                    cv.rect(x + rx * scale, y + ry * scale, x + (rx + 1) * scale, y + (ry + 1) * scale, c)
        x += 6 * scale


COLORS = {
    "red": (214, 39, 40),
    "green": (44, 160, 44),
    "blue": (31, 90, 220),
    "yellow": (240, 196, 25),
}
SHAPES = ("circle", "square", "triangle")
COLS, ROWS, CELL, MARGIN = 9, 7, 96, 56


def vision_layout() -> dict[tuple[int, int], tuple[str, str, int, int, int]]:
    """(row, col) 1-based -> (shape, colour, radius, dx, dy). Exactly one green triangle."""
    rng = random.Random(20260924)
    layout: dict[tuple[int, int], tuple[str, str, int, int, int]] = {}
    for r in range(1, ROWS + 1):
        for c in range(1, COLS + 1):
            if rng.randrange(100) < 64:
                shape = rng.choice(SHAPES)
                colour = rng.choice(tuple(COLORS))
                if shape == "triangle" and colour == "green":
                    colour = "yellow"
                radius = rng.randrange(18, 37)
                dx = rng.randrange(-8, 9)
                dy = rng.randrange(-8, 9)
                layout[(r, c)] = (shape, colour, radius, dx, dy)
    # The single green triangle, placed in an otherwise empty cell.
    layout[(5, 7)] = ("triangle", "green", 27, 4, -3)
    return layout


def vision_answers(layout: dict[tuple[int, int], tuple[str, str, int, int, int]]) -> dict[str, object]:
    circles = sum(1 for s in layout.values() if s[0] == "circle")
    red = sum(1 for s in layout.values() if s[1] == "red")
    empty = ROWS * COLS - len(layout)

    def red_neighbour(r: int, c: int) -> bool:
        return any(layout.get((r + dr, c + dc), ("", ""))[1] == "red" for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)))

    yellow_next_to_red = sum(1 for (r, c), s in layout.items() if s[1] == "yellow" and red_neighbour(r, c))
    tri_per_col = {
        c: sum(1 for (_r, cc), s in layout.items() if cc == c and s[0] == "triangle") for c in range(1, COLS + 1)
    }
    best = max(tri_per_col.values())
    best_cols = [c for c, v in tri_per_col.items() if v == best]
    return {
        "circles": circles,
        "red": red,
        "empty": empty,
        "yellow_next_to_red": yellow_next_to_red,
        "most_triangles_columns": best_cols,
        "triangles_per_column": tri_per_col,
    }


def gen_vision() -> dict[str, object]:
    layout = vision_layout()
    w, h = MARGIN + COLS * CELL + 16, MARGIN + ROWS * CELL + 16
    cv = Canvas(w, h, (255, 255, 255))
    grey, black = (190, 190, 190), (20, 20, 20)
    for c in range(COLS + 1):
        x = MARGIN + c * CELL
        cv.rect(x - 1, MARGIN, x + 1, MARGIN + ROWS * CELL, grey)
    for r in range(ROWS + 1):
        y = MARGIN + r * CELL
        cv.rect(MARGIN, y - 1, MARGIN + COLS * CELL, y + 1, grey)
    for c in range(1, COLS + 1):
        draw_text(cv, MARGIN + (c - 1) * CELL + CELL // 2 - 7, 18, str(c), 3, black)
    for r in range(1, ROWS + 1):
        draw_text(cv, 18, MARGIN + (r - 1) * CELL + CELL // 2 - 10, str(r), 3, black)
    for (r, c), (shape, colour, radius, dx, dy) in sorted(layout.items()):
        cx = MARGIN + (c - 1) * CELL + CELL // 2 + dx
        cy = MARGIN + (r - 1) * CELL + CELL // 2 + dy
        col = COLORS[colour]
        if shape == "circle":
            cv.circle(cx, cy, radius, col)
        elif shape == "square":
            cv.rect(cx - radius, cy - radius, cx + radius + 1, cy + radius + 1, col)
        else:
            cv.triangle(cx, cy, radius, col)
    write_png(ASSETS / "shapes_grid.png", w, h, cv.px)
    return vision_answers(layout)


# ------------------------------------------------------------------------------------------------
# Long-context documents (fictional company "Norrbeck Instruments")
# ------------------------------------------------------------------------------------------------

DEPTS = [
    "Research & Development",
    "Logistics",
    "Sales",
    "Finance",
    "Human Resources",
    "Customer Support",
    "Manufacturing",
]
FIRST = [
    "Alba",
    "Brisk",
    "Corin",
    "Dalia",
    "Emrys",
    "Fenna",
    "Galen",
    "Hesper",
    "Ilro",
    "Jorun",
    "Kaelen",
    "Liv",
    "Maren",
    "Niko",
    "Oriel",
    "Pell",
    "Quilla",
    "Rasmus",
    "Sefa",
    "Tamsin",
    "Ulric",
    "Vesna",
    "Wren",
    "Yara",
    "Zev",
    "Ansel",
    "Brin",
    "Cato",
    "Delphine",
    "Eskil",
]
LAST = [
    "Adler",
    "Brenn",
    "Corvan",
    "Dunmore",
    "Elsted",
    "Farrow",
    "Grell",
    "Holt",
    "Ibsen",
    "Jarvik",
    "Kessel",
    "Lund",
    "Morrow",
    "Norgaard",
    "Ostrin",
    "Pardo",
    "Quist",
    "Ravel",
    "Sorby",
    "Tallis",
    "Umber",
    "Voss",
    "Wexley",
    "Yorke",
    "Zeller",
]
CATEGORIES = ["TRAVEL", "SOFTWARE", "EQUIPMENT", "CONF", "MEALS", "TRAINING", "SUPPLIES"]
CURRENCIES = ["EUR", "USD", "GBP", "CHF"]
MONTHS = ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"]
# EUR value of one unit of currency, per month (as published in the policy table).
FX_PUBLISHED = {
    "USD": ["0.9612", "0.9580", "0.9244", "0.8871", "0.8853", "0.8702"],
    "GBP": ["1.1904", "1.2011", "1.1936", "1.1702", "1.1843", "1.1790"],
    "CHF": ["1.0633", "1.0655", "1.0480", "1.0712", "1.0768", "1.0694"],
}
# The correction notice at the end of the policy replaces the June GBP rate.
FX_CORRECTED_JUNE_GBP = "1.1682"
TARGET_DEPT = "Research & Development"
TARGET_MONTH = "2025-06"
CAPEX_LIMIT = Decimal("5000.00")


def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_amount(x: Decimal) -> str:
    return f"{x:,.2f}"


def build_company(rng: random.Random) -> tuple[list[dict], list[dict], list[dict]]:
    names: set[str] = set()
    staff = []
    for i in range(96):
        while True:
            n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if n not in names:
                names.add(n)
                break
        dept = DEPTS[0] if i % 5 == 0 else rng.choice(DEPTS)
        staff.append(
            {
                "id": f"E-{1001 + i}",
                "name": n,
                "dept": dept,
                "since": f"20{rng.randrange(12, 25):02d}-{rng.randrange(1, 13):02d}-01",
            }
        )
    # Transfers: (employee, from, to, effective date, cancelled?)
    transfers = []
    movers = rng.sample(staff, 18)
    for k, emp in enumerate(movers):
        if k < 6:
            to = TARGET_DEPT if emp["dept"] != TARGET_DEPT else "Logistics"
        else:
            to = rng.choice([d for d in DEPTS if d != emp["dept"]])
        month = rng.choice([3, 4, 5, 6, 6, 6])
        day = rng.randrange(2, 28)
        transfers.append(
            {
                "id": emp["id"],
                "from": emp["dept"],
                "to": to,
                "date": f"2025-{month:02d}-{day:02d}",
                "cancelled": k in (2, 11),
            }
        )
    return staff, transfers, movers


def dept_on(emp: dict, date: str, transfers: list[dict]) -> str:
    dept = emp["dept"]
    for t in sorted(transfers, key=lambda t: t["date"]):
        if t["id"] == emp["id"] and not t["cancelled"] and date >= t["date"]:
            dept = t["to"]
    return dept


def gen_ledger(rng: random.Random, staff: list[dict], transfers: list[dict]) -> list[dict]:
    txns = []
    n = 0
    for m_index, month in enumerate(MONTHS):
        count = 190 if month != TARGET_MONTH else 230
        for _ in range(count):
            n += 1
            emp = rng.choice(staff) if rng.randrange(3) else rng.choice(staff[::5])
            cat = rng.choice(CATEGORIES)
            cur = rng.choice([*CURRENCIES, "EUR", "EUR"])
            base = {
                "TRAVEL": 900,
                "SOFTWARE": 1400,
                "EQUIPMENT": 3800,
                "CONF": 1100,
                "MEALS": 160,
                "TRAINING": 1200,
                "SUPPLIES": 240,
            }[cat]
            cents = rng.randrange(base * 20, base * 180)
            if cat == "EQUIPMENT" and rng.randrange(4) == 0:
                cents = rng.randrange(560000, 990000)
            amount = Decimal(cents) / 100
            status = "APPROVED" if rng.randrange(100) < 82 else rng.choice(["REJECTED", "PENDING"])
            day = rng.randrange(1, 31 if month not in ("2025-02",) else 29)
            if month in ("2025-04", "2025-06") and day == 31:
                day = 30
            txns.append(
                {
                    "no": n,
                    "date": f"{month}-{day:02d}",
                    "emp": emp["id"],
                    "amount": amount,
                    "cur": cur,
                    "cat": cat,
                    "status": status,
                    "ref": None,
                    "m": m_index,
                }
            )
    # Claims by employees whose transfer out of R&D was withdrawn (they stay in R&D in June).
    for t in transfers:
        if t["cancelled"]:
            for day in (int(t["date"][8:]) + 1, 28):
                n += 1
                txns.append(
                    {
                        "no": n,
                        "date": f"{t['date'][:8]}{day:02d}",
                        "emp": t["id"],
                        "amount": Decimal(rng.randrange(40000, 190000)) / 100,
                        "cur": rng.choice(["USD", "GBP", "EUR"]),
                        "cat": rng.choice(["TRAVEL", "SOFTWARE", "TRAINING"]),
                        "status": "APPROVED",
                        "ref": None,
                        "m": 5,
                    }
                )
    # Reversals: each cancels an earlier APPROVED transaction entirely.
    approved = [t for t in txns if t["status"] == "APPROVED"]
    by_id = {e["id"]: e for e in staff}
    june_rd = [
        t
        for t in approved
        if t["date"].startswith(TARGET_MONTH)
        and t["cat"] not in ("CONF", "EQUIPMENT")
        and dept_on(by_id[t["emp"]], t["date"], transfers) == TARGET_DEPT
        and t["date"] < "2025-06-20"
    ]
    may_rd = [
        t
        for t in approved
        if t["date"].startswith("2025-05-2") and dept_on(by_id[t["emp"]], t["date"], transfers) == TARGET_DEPT
    ]
    targets = rng.sample(june_rd, 5) + rng.sample(may_rd, 3)
    targets += rng.sample([t for t in approved if t not in targets], 32)
    for t in targets:
        n += 1
        _year, mth, d = t["date"].split("-")
        rd = min(int(d) + rng.randrange(1, 12), 28)
        rm = int(mth) if rd > int(d) else min(int(mth) + 1, 6)
        if t["date"].startswith("2025-05-2"):
            rd, rm = rng.randrange(2, 9), 6
        rdate = f"2025-{rm:02d}-{rd:02d}"
        rdate = max(rdate, t["date"])
        txns.append(
            {
                "no": n,
                "date": rdate,
                "emp": t["emp"],
                "amount": -t["amount"],
                "cur": t["cur"],
                "cat": t["cat"],
                "status": "APPROVED",
                "ref": t["no"],
                "m": int(rdate[5:7]) - 1,
            }
        )
    txns.sort(key=lambda t: (t["date"], t["no"]))
    return txns


def rate(cur: str, month_index: int, corrected: bool = True) -> Decimal:
    if cur == "EUR":
        return Decimal(1)
    if corrected and cur == "GBP" and month_index == 5:
        return Decimal(FX_CORRECTED_JUNE_GBP)
    return Decimal(FX_PUBLISHED[cur][month_index])


def long_context_answer(
    staff: list[dict], transfers: list[dict], txns: list[dict], variant: str = ""
) -> tuple[Decimal, str, dict]:
    by_id = {e["id"]: e for e in staff}
    reversed_nos = {t["ref"] for t in txns if t["ref"] is not None}
    per_emp: dict[str, Decimal] = {}
    for t in txns:
        if t["ref"] is not None and variant != "no_reversal":
            continue  # the reversal entry itself
        if not t["date"].startswith(TARGET_MONTH):
            continue
        if t["status"] != "APPROVED":
            continue
        if t["no"] in reversed_nos and variant != "no_reversal":
            continue
        if t["cat"] == "CONF" and variant != "no_conf":
            continue
        tr = transfers if variant != "no_transfers" else []
        if variant == "ignore_cancel":
            tr = [dict(x, cancelled=False) for x in transfers]
        if dept_on(by_id[t["emp"]], t["date"], tr) != TARGET_DEPT:
            continue
        eur = q2(t["amount"] * rate(t["cur"], t["m"], corrected=variant != "no_correction"))
        if t["cat"] == "EQUIPMENT" and eur > CAPEX_LIMIT and variant != "no_capex":
            continue
        per_emp[t["emp"]] = per_emp.get(t["emp"], Decimal(0)) + eur
    total = sum(per_emp.values(), Decimal(0))
    top = max(per_emp, key=lambda k: per_emp[k])
    return total, top, per_emp


def gen_long_context() -> dict[str, object]:
    rng = random.Random(4242)
    staff, transfers, _ = build_company(rng)
    txns = gen_ledger(rng, staff, transfers)

    lines = [
        "NORRBECK INSTRUMENTS - STAFF DIRECTORY (snapshot 2025-01-01)",
        "Fictional company. Department shown is the home department on 2025-01-01.",
        "",
        "ID      | Name                 | Department               | With company since",
        "--------+----------------------+--------------------------+-------------------",
    ]
    for e in staff:
        lines.append(f"{e['id']:<7} | {e['name']:<20} | {e['dept']:<24} | {e['since']}")
    lines += [
        "",
        "INTERNAL TRANSFERS ANNOUNCED IN H1 2025",
        "",
        "An employee belongs to the new department from the effective date (inclusive).",
        "",
    ]
    notices = []
    for k, t in enumerate(sorted(transfers, key=lambda t: (t["date"], t["id"]))):
        notices.append(f"Notice T-{k + 1:02d}: {t['id']} moves from {t['from']} to {t['to']}, effective {t['date']}.")
    lines += notices
    lines += ["", "CANCELLATIONS", ""]
    for t in transfers:
        if t["cancelled"]:
            k = sorted(transfers, key=lambda x: (x["date"], x["id"])).index(t) + 1
            lines.append(
                f"Notice T-{k:02d} ({t['id']}) was withdrawn before its effective date; "
                f"the employee stays in {t['from']}."
            )
    (ASSETS / "norrbeck_staff.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    led = [
        "NORRBECK INSTRUMENTS - EXPENSE LEDGER, JANUARY-JUNE 2025",
        "Amounts are in the original currency of the claim. Negative amounts are reversal entries.",
        "",
        "TXN        | Date       | Employee | Amount       | Cur | Category  | Status   | Note",
        "-----------+------------+----------+--------------+-----+-----------+----------+-----------------------",
    ]
    for t in txns:
        note = f"reversal of TXN-{t['ref']:05d}" if t["ref"] is not None else ""
        led.append(
            (
                f"TXN-{t['no']:05d} | {t['date']} | {t['emp']:<8} | {fmt_amount(t['amount']):>12} | "
                f"{t['cur']} | {t['cat']:<9} | {t['status']:<8} | {note}"
            ).rstrip()
        )
    (ASSETS / "norrbeck_ledger_h1.txt").write_text("\n".join(led) + "\n", encoding="utf-8", newline="\n")

    pol = [
        "NORRBECK INSTRUMENTS - FINANCE POLICY FN-7: CHARGING EXPENSES TO DEPARTMENTS (applies from 2025-01-01)",
        "",
        "1. Only ledger entries with status APPROVED are charged. REJECTED and PENDING entries are ignored.",
        "2. A reversal entry (note 'reversal of TXN-n') cancels transaction TXN-n completely: neither the",
        "   original transaction nor the reversal entry is charged to anyone, whatever their dates.",
        "3. An expense is charged to the department the claiming employee belongs to on the transaction date,",
        "   taking into account internal transfers (and their withdrawals) from the staff directory.",
        "4. Category CONF (conference fees) is never charged to departments: it is paid from the central",
        "   Training Pool.",
        "5. Amounts are converted to EUR with the monthly rate of the transaction month (table below). Each",
        "   transaction is converted and rounded to cents (half away from zero) before any summing.",
        "6. EQUIPMENT transactions whose converted amount exceeds 5,000.00 EUR are capitalised as assets and",
        "   are not charged to departmental spend. EQUIPMENT at or below that threshold is charged normally.",
        "7. All other categories are charged in full.",
        "",
        "MONTHLY CONVERSION RATES (EUR per 1 unit of currency)",
        "",
        "Month   | USD    | GBP    | CHF",
        "--------+--------+--------+-------",
    ]
    for i, m in enumerate(MONTHS):
        pol.append(f"{m} | {FX_PUBLISHED['USD'][i]} | {FX_PUBLISHED['GBP'][i]} | {FX_PUBLISHED['CHF'][i]}")
    pol += [
        "",
        "EUR amounts need no conversion.",
        "",
        "Appendix A - worked example: a 1,000.00 USD claim dated 2025-02-10 is charged as 958.00 EUR.",
        "",
        "Appendix B - distribution: Finance, department heads, internal audit.",
        "",
        "CORRECTION NOTICE (issued 2025-07-02): the GBP rate published above for 2025-06 was a typing error.",
        f"The correct June 2025 GBP rate is {FX_CORRECTED_JUNE_GBP}. All June figures must use the corrected rate.",
    ]
    (ASSETS / "norrbeck_finance_policy.txt").write_text("\n".join(pol) + "\n", encoding="utf-8", newline="\n")

    total, top, per_emp = long_context_answer(staff, transfers, txns)
    ranking = sorted(per_emp.items(), key=lambda kv: -kv[1])
    variants = {
        v: str(long_context_answer(staff, transfers, txns, v)[0])
        for v in ("no_reversal", "no_conf", "no_transfers", "ignore_cancel", "no_correction", "no_capex")
    }
    return {
        "total": str(total),
        "top": top,
        "top3": [(k, str(v)) for k, v in ranking[:3]],
        "n_contributors": len(per_emp),
        "trap_variants": variants,
    }


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    vision = gen_vision()
    lc = gen_long_context()
    if "--answers" in sys.argv:
        print("vision:", vision)
        print("long-context:", lc)


if __name__ == "__main__":
    main()
