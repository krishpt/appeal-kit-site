"""Comparable-sales valuation. Plain math, no AI, every step explainable in a packet.

A comp's sale price is adjusted toward the subject house:
- land: swap the comp's county land value for the subject's (uses Wake's own land values)
- size: half the comp's building $/sq ft for each sq ft of difference (marginal sq ft is worth less than average)
- age: AGE_RATE of the comp's building value per year newer/older, capped at AGE_CAP years
- quality: county grade, condition and effective year, as a % of the adjusted price (when both houses have them)
- time: the comp's price moved by the countywide market level between its sale and the valuation date
The estimate is the median adjusted price; the range is the 25th-75th percentile.
"""

from __future__ import annotations

import math

import pandas as pd

SIZE_BAND = 0.25      # comps within +/-25% heated area
YEAR_BAND = 15        # and +/-15 years of build year
LOOKBACK_DAYS = 365   # sales in the year before the valuation date
MAX_COMPS = 6
MIN_COMPS = 3
MARGINAL_SQFT = 0.5   # ASSUMPTION (confidence: low): rule of thumb, tuned by backtest
AGE_RATE = 0.005      # ASSUMPTION (confidence: low): rule of thumb, tuned by backtest
AGE_CAP = 15
# Quality, as log-price per unit of (subject - comp). Fit on one 2,000-sale backtest sample, scored on another (2026-11-14).
GRADE_RATE = 0.068    # per construction-grade letter
COND_RATE = 0.0032    # per county condition % point
EFF_RATE = 0.0017     # per effective-year (renovation-adjusted age)
RADIUS_FT = 5280      # ASSUMPTION (confidence: med): 1 mile when the neighborhood is thin (X/Y are NC State Plane feet)

# Confidence rule (BET.md gate 1b, frozen 2026-11-14, round 2): only these homes get an appeal/fair verdict; others get a range.
MAX_SPREAD = 0.15     # comp IQR / estimate. Round 1 used 0.068 (too strict: 24.8% got a verdict); 0.15 chosen on that used sample
MAX_AGE = 45          # years at the valuation date

# Gate 1c FAILED 2026-11-14 (22.3% false appeals vs <= 15%). Users see position-only until gate 1d passes (BET.md).
VERDICTS_ENABLED = False
INDEX_WINDOW_DAYS = 90  # frozen for gate 1d: market level at d uses sales in [d - 90 days, d) only
INDEX_MIN_SALES = 50    # frozen for gate 1d

# Verdict thresholds: only call it "too high" when the gap is clear in % AND dollars.
APPEAL_PCT = 0.05
APPEAL_DOLLARS = 10_000


def market_index(sales: pd.DataFrame) -> pd.Series:
    """Daily market level: median of sale price / county assessed value over the prior INDEX_WINDOW_DAYS (today excluded).
    Every assessment shares one valuation date, so the ratio tracks constant quality; raw $/sq ft drifts with which homes sell
    (it showed a false 10% drop in 2024-26)."""
    ratio = (sales.SALE_PRICE / sales.PARCEL_ASSD_VALUE_ATSALE).groupby(sales.SALE_DATE).apply(list).sort_index()
    days = pd.date_range(sales.SALE_DATE.min(), sales.SALE_DATE.max() + pd.Timedelta(days=1))
    out = {}
    for d in days:
        window = ratio[(ratio.index >= d - pd.Timedelta(days=INDEX_WINDOW_DAYS)) & (ratio.index < d)]
        vals = [x for xs in window for x in xs]
        out[d] = pd.Series(vals).median() if len(vals) >= INDEX_MIN_SALES else float("nan")  # too thin -> no adjustment
    return pd.Series(out)


def time_factor(index: pd.Series | None, comp_date: pd.Timestamp, as_of: pd.Timestamp) -> float:
    """How much the market moved from the comp's sale to the valuation date; 1.0 when unknown."""
    if index is None:
        return 1.0
    a, c = index.get(as_of.normalize()), index.get(comp_date.normalize())
    return float(a / c) if pd.notna(a) and pd.notna(c) and c > 0 else 1.0


def shown_verdict(raw: str, assessed: float, est: dict | None) -> str:
    """What the user sees. Until gate 1d passes there are no recommendations, only where the assessment sits."""
    if VERDICTS_ENABLED or est is None:
        return raw
    return "above_range" if assessed > est["high"] else "below_range" if assessed < est["low"] else "within_range"


def select_comps(subject: dict, sales: pd.DataFrame, as_of: pd.Timestamp, index: pd.Series | None = None) -> pd.DataFrame:
    """Most similar qualified sales before `as_of`: same neighborhood, else within RADIUS_FT, else same township."""
    s = sales[
        (sales.SALE_DATE < as_of)
        & (sales.SALE_DATE >= as_of - pd.Timedelta(days=LOOKBACK_DAYS))
        & (sales.REID != subject["REID"])
        & ((sales.TOTAL_HEATED_AREA - subject["TOTAL_HEATED_AREA"]).abs() <= SIZE_BAND * subject["TOTAL_HEATED_AREA"])
        & ((sales.YEAR_BUILT - subject["YEAR_BUILT"]).abs() <= YEAR_BAND)
    ]
    same_vcs = s[s.VCS == subject["VCS"]]
    sx, sy = (float("nan") if subject.get(k) is None else subject[k] for k in ("X", "Y"))  # None (hand-built subject) acts as unknown
    dist = ((s.X - sx) ** 2 + (s.Y - sy) ** 2) ** 0.5 if "X" in s else None
    if len(same_vcs) >= MIN_COMPS:
        pool, extra, how = same_vcs, 0.0, "neighborhood"
    elif dist is not None and (dist <= RADIUS_FT).sum() >= MIN_COMPS:  # unknown coordinates give NaN distances, never "within"
        pool, how = s[dist <= RADIUS_FT], "radius"
        extra = dist[pool.index] / RADIUS_FT
    else:  # no coordinates, or too few sales within the radius
        pool, extra, how = s[(s.TOWNSHIP == subject["TOWNSHIP"]) & (s.PLANNING_JURISDICTION == subject["PLANNING_JURISDICTION"])], 0.0, "township"
    score = (
        (pool.VCS != subject["VCS"]) * 1.0
        + extra
        + (pool.TOTAL_HEATED_AREA - subject["TOTAL_HEATED_AREA"]).abs() / subject["TOTAL_HEATED_AREA"] / SIZE_BAND
        + (pool.YEAR_BUILT - subject["YEAR_BUILT"]).abs() / YEAR_BAND
        + (as_of - pool.SALE_DATE).dt.days / LOOKBACK_DAYS
    )
    best = pool.assign(similarity=score).nsmallest(MAX_COMPS, "similarity")
    return best.assign(TIME_FACTOR=[time_factor(index, d, as_of) for d in best.SALE_DATE], POOL=how)  # which search ran


def adjust(comp: pd.Series | dict, subject: dict) -> dict:
    """Adjusted price of one comp, with each adjustment itemized for the packet."""
    time = comp["SALE_PRICE"] * (comp.get("TIME_FACTOR", 1.0) - 1)  # market movement since the comp sold
    bldg_value = max(comp["SALE_PRICE"] + time - comp["LAND_VALUE_ASSD"], 0)
    land = subject["LAND_VALUE_ASSD"] - comp["LAND_VALUE_ASSD"]
    size = (subject["TOTAL_HEATED_AREA"] - comp["TOTAL_HEATED_AREA"]) * MARGINAL_SQFT * bldg_value / comp["TOTAL_HEATED_AREA"]
    years = max(-AGE_CAP, min(AGE_CAP, subject["YEAR_BUILT"] - comp["YEAR_BUILT"]))
    age = years * AGE_RATE * bldg_value
    base = comp["SALE_PRICE"] + time + land + size + age
    log_q = sum(rate * (subject[k] - comp[k]) for k, rate in (("GRADE", GRADE_RATE), ("COND", COND_RATE), ("EFF", EFF_RATE))
                if pd.notna(subject.get(k)) and pd.notna(comp.get(k)))  # skipped when either side lacks the field
    quality = base * (math.exp(log_q) - 1)
    return {"time": time, "land": land, "size": size, "age": age, "quality": quality, "adjusted": base + quality}


def estimate(subject: dict, comps: pd.DataFrame) -> dict | None:
    """Market value estimate and range, or None if there aren't enough comps to say anything."""
    adj = pd.Series([adjust(c, subject)["adjusted"] for _, c in comps.iterrows()], dtype=float).dropna()
    if len(adj) < MIN_COMPS:  # count only comps that produced a number, so the packet's comp count is true
        return None
    value, low, high = float(adj.median()), float(adj.quantile(0.25)), float(adj.quantile(0.75))
    return {"value": value, "low": low, "high": high, "n": len(adj), "spread": (high - low) / value,
            "same_vcs": bool((comps.VCS == subject["VCS"]).all())}


def confident(est: dict | None, subject: dict, as_of: pd.Timestamp) -> bool:
    """Gate 1b rule: neighborhood comps that agree, on a home under MAX_AGE years."""
    return (est is not None and est["same_vcs"] and est["spread"] <= MAX_SPREAD
            and as_of.year - subject["YEAR_BUILT"] < MAX_AGE)


def verdict(assessed: float, est: dict | None, *, is_confident: bool) -> str:
    """'appeal' only when confident AND the assessment is clearly above the estimate; never push a weak case."""
    if est is None:
        return "not_enough_data"
    if not is_confident:
        return "range_only"
    gap = assessed - est["value"]
    if gap >= APPEAL_DOLLARS and gap >= APPEAL_PCT * est["value"] and assessed > est["high"]:
        return "appeal"
    return "fair"
