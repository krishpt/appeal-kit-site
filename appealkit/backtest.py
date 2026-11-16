"""$0 accuracy test: predict real sales from earlier comps only, and report the error.

python -m appealkit.backtest [path_to_sales.xlsx] [--n 2000]
Pass line (IDEA.md): >= 90% coverage, median abs error <= 7%, >= 60% within 10%.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from appealkit import value
from appealkit.data import load_sales


def eligible(sales: pd.DataFrame) -> pd.DataFrame:
    # subjects need a full lookback year of earlier sales
    pool = sales[sales.SALE_DATE >= sales.SALE_DATE.min() + pd.Timedelta(days=value.LOOKBACK_DAYS)]
    # existing homes only: new construction sells before the county has a stable record
    return pool[pool.YEAR_BUILT < pool.SALE_DATE.dt.year - 1]


def keys(df: pd.DataFrame) -> pd.Series:
    return df.REID.astype(str) + "@" + pd.to_datetime(df.SALE_DATE).dt.strftime("%Y-%m-%d")


def run(sales: pd.DataFrame, n: int, seed: int = 0, exclude: set[str] = frozenset(), used_out: str | None = None,
        time_adjust: bool = False) -> dict:
    """Gate 1 metrics on all subjects, gate 1b metrics on the ones the confidence rule would give a verdict.
    `exclude` holds REID@date keys of subjects already used for fitting, tuning or earlier reads."""
    pool = eligible(sales)
    pool = pool[~keys(pool).isin(exclude)]
    subjects = pool.sample(min(n, len(pool)), random_state=seed)
    if used_out:
        keys(subjects).to_frame("key").to_csv(used_out, index=False)
    index = value.market_index(sales) if time_adjust else None
    errs, conf, misses, appeals, false_appeals = [], [], 0, 0, 0
    fairs = fair_but_over = confident_n = confident_over = 0  # gate 1d (c)
    for _, s in subjects.iterrows():
        est = value.estimate(s, value.select_comps(s, sales, s.SALE_DATE, index))
        if est is None:
            misses += 1
            continue
        errs.append((est["value"] - s.SALE_PRICE) / s.SALE_PRICE)
        conf.append(value.confident(est, s, s.SALE_DATE))
        v_ = value.verdict(s.PARCEL_ASSD_VALUE_ATSALE, est, is_confident=conf[-1])
        gap = s.PARCEL_ASSD_VALUE_ATSALE - s.SALE_PRICE
        over = gap >= 0.05 * s.SALE_PRICE and gap >= 10_000  # truly over-assessed: sold >=5% and >=$10k below assessment
        if v_ == "appeal":
            appeals += 1
            false_appeals += s.SALE_PRICE >= s.PARCEL_ASSD_VALUE_ATSALE  # it sold for at least its assessment
        if conf[-1]:
            confident_n += 1
            confident_over += over
            if v_ == "fair":
                fairs += 1
                fair_but_over += over
    e = pd.Series(errs, dtype=float)
    c = e[pd.Series(conf, dtype=bool)] if len(e) else e
    return {
        "gate1c_appeals": appeals,
        "gate1c_false_appeal_rate": round(false_appeals / appeals, 3) if appeals else None,
        "gate1d_fair_over_share": round(fair_but_over / fairs, 3) if fairs else None,
        "gate1d_confident_over_share": round(confident_over / confident_n, 3) if confident_n else None,
        "gate1b_verdict_share": round(len(c) / len(subjects), 3) if len(subjects) else 0.0,
        "gate1b_median_abs_err": round(c.abs().median(), 4),
        "gate1b_within_10pct": round((c.abs() <= 0.10).mean(), 3),
        "coverage": round(len(e) / len(subjects), 3) if len(subjects) else 0.0,
        "subjects": len(subjects),
        "no_estimate": misses,
        "median_abs_err": round(e.abs().median(), 4),
        "within_5pct": round((e.abs() <= 0.05).mean(), 3),
        "within_10pct": round((e.abs() <= 0.10).mean(), 3),
        "median_bias": round(e.median(), 4),  # >0 means we over-estimate
    }


def pool_snapshots(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """S1: several snapshots of the rolling sales file -> one table. Later files win; a sale whose assessment-at-sale
    differs between snapshots means the county changed the record, so stop rather than guess."""
    allx = pd.concat(frames, ignore_index=True)
    k = keys(allx)
    clash = allx.assign(k=k).groupby("k").PARCEL_ASSD_VALUE_ATSALE.nunique()
    if (clash > 1).any():
        raise SystemExit(f"{int((clash > 1).sum())} sale(s) changed assessment-at-sale between snapshots: inspect before reading")
    return allx[~k.duplicated(keep="last")].reset_index(drop=True)


def used_nearby(sales: pd.DataFrame, used: set[str], days: int = 30) -> set[str]:
    """S1: keys of sales whose REID was already used within `days` of that date (a corrected sale date can't sneak back)."""
    by_reid: dict[str, list[pd.Timestamp]] = {}
    for k in used:
        reid, d = k.split("@")
        by_reid.setdefault(reid, []).append(pd.Timestamp(d))
    return {key for reid, when, key in zip(sales.REID.astype(str), sales.SALE_DATE, keys(sales))
            if any(abs((when - u).days) <= days for u in by_reid.get(reid, ()))}


def gate1d(plain: dict, adjusted: dict) -> str:
    """S4, frozen 2026-11-15 (BET.md gate 1d): pass is judged on the default (unadjusted) engine; time adjustment switches on
    only if it also passes and is no worse, so picking the better of two arms can't flatter the read."""
    if min(plain["gate1c_appeals"], adjusted["gate1c_appeals"]) < 60:
        return f"GATE 1d NO READ (appeal verdicts: plain {plain['gate1c_appeals']}, adjusted {adjusted['gate1c_appeals']}; need >= 60 each)"
    a = plain["gate1c_false_appeal_rate"] <= 0.15
    b = adjusted["gate1c_false_appeal_rate"] <= min(0.15, plain["gate1c_false_appeal_rate"])
    c = (plain["gate1d_fair_over_share"] is not None and plain["gate1d_confident_over_share"] is not None
         and plain["gate1d_fair_over_share"] <= 0.5 * plain["gate1d_confident_over_share"])
    return (f"GATE 1d (a) {'PASS' if a else 'FAIL'}: false appeals {plain['gate1c_false_appeal_rate']:.1%} of "
            f"{plain['gate1c_appeals']} (<= 15%)\n"
            f"GATE 1d (b) time adjustment {'ON' if a and b else 'OFF'}: adjusted {adjusted['gate1c_false_appeal_rate']:.1%} of "
            f"{adjusted['gate1c_appeals']}\n"
            f"GATE 1d (c) {'PASS' if c else 'FAIL'}: 'fair' but over-assessed {plain['gate1d_fair_over_share']} vs "
            f"{plain['gate1d_confident_over_share']} among confident (needs <= half)\n"
            f"VERDICTS {'ON' if a else 'OFF'} for 'appeal'; 'fair' {'ON' if a and c else 'OFF'} (provisional until the March re-read)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("path", nargs="*", default=["data/raw/Qualified_Sales_Past_24Months.xlsx"],
                   help="one or more snapshots of the sales file (pooled, later files win)")
    p.add_argument("--n", type=int, default=2000)
    p.add_argument("--points", default=None, help="Wake Parcel Address Points CSV (enables the distance fallback)")
    p.add_argument("--features", default=None, help="parcels CSV from appealkit.extract (grade/condition/effective year)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--time-adjust", action="store_true", help="adjust comps by the sale/assessment market index")
    p.add_argument("--both-arms", action="store_true", help="gate 1d: same subjects with and without time adjustment")
    p.add_argument("--count-only", action="store_true", help="gate 1d: print only appeal-verdict counts; write nothing")
    p.add_argument("--used-dir", default=None, help="exclude subjects listed in every CSV here, then save this run's subjects as <label>.csv")
    p.add_argument("--label", default=None, help="name for this run's used-subjects file (required with --used-dir)")
    a = p.parse_args()
    sales = pool_snapshots([load_sales(f, a.points, a.features) for f in a.path])
    exclude, out = set(), None
    if a.used_dir:
        d = Path(a.used_dir)
        d.mkdir(parents=True, exist_ok=True)
        used = {k for f in d.glob("*.csv") for k in pd.read_csv(f).key}
        exclude = used | used_nearby(sales, used)
        if not a.count_only:
            out = d / f"{a.label}.csv"
            if out.exists():
                raise SystemExit(f"{out} exists: a read is run once. Pick a new label.")
    if a.count_only or a.both_arms:
        plain = run(sales, a.n, a.seed, exclude, None if a.count_only else str(out) if out else None, False)
        adjusted = run(sales, a.n, a.seed, exclude, None, True)  # same pool, seed and n -> the same subjects
        if a.count_only:
            print(f"appeal verdicts: plain {plain['gate1c_appeals']}, adjusted {adjusted['gate1c_appeals']} (no rates shown)")
        else:
            print(gate1d(plain, adjusted))
        return
    r = run(sales, a.n, a.seed, exclude, str(out) if out else None, a.time_adjust)
    print(r)
    ok_b = r["gate1b_verdict_share"] >= 0.40 and r["gate1b_median_abs_err"] <= 0.055 and r["gate1b_within_10pct"] >= 0.70
    print("GATE 1b", "PASS" if ok_b else "FAIL", "(>= 40% get a verdict, their median err <= 5.5%, >= 70% within 10%)")
    if r["gate1c_appeals"] < 30:
        print(f"GATE 1c NO READ (only {r['gate1c_appeals']} appeal verdicts; need >= 30)")
    else:
        print("GATE 1c", "PASS" if r["gate1c_false_appeal_rate"] <= 0.15 else "FAIL", "(false-appeal rate <= 15%)")
    ok = r["coverage"] >= 0.90 and r["median_abs_err"] <= 0.07 and r["within_10pct"] >= 0.60  # NaN compares False -> FAIL
    print("GATE 1", "PASS" if ok else "FAIL", "(pass = >= 90% get an estimate, median abs err <= 7%, >= 60% within 10%)")


if __name__ == "__main__":
    main()
