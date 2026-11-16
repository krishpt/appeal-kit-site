"""Make sure a letter never states a number we can't back up. Copied from ../counteroffer/counteroffer/factcheck.py
(money and percentages), plus plain numbers (square feet, years, counts), because a wrong square footage in a letter
to the county is as damaging as a wrong dollar figure.
"""

from __future__ import annotations

import re

_MONEY = re.compile(r"\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:\s?([kK])\b)?")  # no trailing comma
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
_MONTHS = "January February March April May June July August September October November December".split()
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_LONG = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b")
_PLAIN = re.compile(r"(?<![\$\d.,])\b(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\b(?!\s?%)")


def numbers_in(text: str) -> list[tuple[str, float]]:
    out = []
    for m in _MONEY.finditer(text):
        v = float(m.group(1).replace(",", ""))
        out.append((m.group(0), v * 1000 if m.group(2) else v))
    out += [(m.group(0), float(m.group(1))) for m in _PCT.finditer(text)]
    return out


def unsupported(text: str, facts: dict[str, float], tolerance: float = 0.01) -> list[str]:
    """Money/percent figures in `text` that don't match any fact (1% relative tolerance or $1)."""
    allowed = [abs(v) for v in facts.values()]
    return [raw for raw, v in numbers_in(text) if not any(abs(v - a) <= max(1.0, tolerance * a) for a in allowed)]


_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}
_BIG_WORDS = re.compile(r"\b(thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
                        r"seventy|eighty|ninety|hundred|thousand|million|billion|dozen)\b", re.I)
_LOOSE_DATE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b(?:" + "|".join(_MONTHS) + "|" +
                         "|".join(m[:3] for m in _MONTHS) + r")\b\.?")


def unsupported_plain(text: str, allowed: set[float]) -> list[str]:
    """Plain numbers (not $, %, or whole dates) must match exactly, digits or words. 'one' is ignored (ordinary prose)."""
    stripped = _PCT.sub(" ", _MONEY.sub(" ", text))
    bad = [m.group(0) for m in _PLAIN.finditer(stripped) if float(m.group(1).replace(",", "")) not in allowed]
    bad += [m.group(0) for m in re.finditer(r"\b(" + "|".join(list(_WORDS)[2:]) + r")\b", stripped, re.I)
            if _WORDS[m.group(1).lower()] not in allowed]
    return bad + [m.group(0) for m in _BIG_WORDS.finditer(stripped)]


def loose_dates(text: str) -> list[str]:
    """Month names or m/d dates left after whole dates are removed: a date we can't verify ('Feb. 27', '2/27/2026')."""
    return [m.group(0) for m in _LOOSE_DATE.finditer(without_dates(text))]


def dates_in(text: str) -> list[tuple[str, str]]:
    """(as written, ISO yyyy-mm-dd) for '2026-02-27' and 'February 27, 2026' style dates."""
    out = [(m.group(0), f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}") for m in _ISO.finditer(text)]
    out += [(m.group(0), f"{int(m[3]):04d}-{_MONTHS.index(m[1]) + 1:02d}-{int(m[2]):02d}") for m in _LONG.finditer(text)]
    return out


def unsupported_dates(text: str, allowed: set[str]) -> list[str]:
    """Whole dates must match a fact; checking year and day separately let '2027-02-27' pass for 2026-02-27."""
    return [raw for raw, iso in dates_in(text) if iso not in allowed]


def without_dates(text: str) -> str:
    return _LONG.sub(" ", _ISO.sub(" ", text))
