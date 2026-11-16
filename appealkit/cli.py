"""appealkit check "1506 Wake Forest Rd, Raleigh" [--as-of 2027-01-01]
appealkit packet "1506 Wake Forest Rd, Raleigh" --out packet.pdf [--as-of 2027-01-01]

Prints (or renders) the evidence: the county's assessed value, comparable sales with every adjustment itemized, the
estimated range and where the assessment sits in it. No recommendation while verdicts are off (BET.md gates 1c/1d).
"""

from __future__ import annotations

import argparse

from appealkit import packet
from appealkit.service import NOT_SINGLE_FAMILY, Engine, latest, listing  # noqa: F401  (latest: kept importable from cli)


def report(e: dict) -> str:
    m = packet.money
    lines = [f"{e['address']}  (REID {e['reid']})",
             f"County assessed value: {m(e['assessed'])}   Valuation date used: {e['as_of']}",
             f"{e['sqft']} sq ft heated, built {e['built']}, neighborhood {e['neighborhood']}"]
    if e["preview"]:
        lines.insert(2, packet.PREVIEW_NOTE)
    if e["estimate"] is None:
        return "\n".join(lines + ["Not enough comparable sales to compare.", "", packet.DISCLAIMER])
    parts = [p for p in packet.PARTS if p != "time" or e["time_adjusted"]]
    lines += [packet.headline(e), f"Your assessed value is {packet.POSITION[e['position']]}.",
              f"Evidence strength: {e['strength']}", "",
              f"{'Comparable sale':30s} {'REID':>7s} {'Sold':10s} {'Price':>10s} {'SqFt':>5s} {'Built':>5s} {'Miles':>5s} "
              + " ".join(f"{p.title():>8s}" for p in parts) + f" {'Adjusted':>10s} {'Gross':>6s}"]
    for c in e["comps"]:
        miles = f"{c['miles']:5.2f}" if c["miles"] is not None else "     "
        lines.append(f"{c['address'][:30]:30s} {c['reid']:7d} {c['sold']:10s} {m(c['price']):>10s} {c['sqft']:5d} {c['built']:5d} "
                     f"{miles} " + " ".join(f"{c[p]:+8,.0f}" for p in parts) + f" {m(c['adjusted']):>10s} {c['gross_pct']:6.0%}")
    if e["others"]:
        top = max(e["others"], key=lambda o: o["price"])
        lines += ["", f"{len(e['others'])} other qualified sale(s) in your neighborhood were not used (see the PDF exhibit); "
                      f"highest: {top['address']}, {m(top['price'])}, {top['sqft']:,} sq ft ({top['why']})."]
    if not e["time_adjusted"]:
        lines += ["", packet.TIME_NOTE]
    if e["position"] == "above_range":
        lines += ["", packet.MISS_RATE_NOTE]
    return "\n".join(lines + ["", packet.DISCLAIMER])


def load_evidence(query: str, as_of: str) -> dict:
    kind, got = Engine.from_files().lookup(query, as_of)
    if kind == "several":
        raise SystemExit("Several matches, add the city or ZIP:\n" + "\n".join("  " + x for x in listing(got)))
    if kind == "none":
        raise SystemExit("No matching Wake County parcel." + (" Did you mean:\n" + "\n".join("  " + x for x in listing(got)) if len(got) else ""))
    if kind == "not_single_family":
        raise SystemExit(NOT_SINGLE_FAMILY.format(address=got.ADDRESS, city=got.CITY))
    return got


def main() -> None:
    p = argparse.ArgumentParser(prog="appealkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("check", "print the evidence for a Wake County home"), ("packet", "write the evidence packet PDF")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("query", help='address ("1506 Wake Forest Rd, Raleigh"), REID or 10-digit PIN')
        c.add_argument("--as-of", default="2027-01-01", help="valuation date (default 2027-01-01, the 2027 revaluation date)")
        if name == "packet":
            c.add_argument("--out", required=True, help="output PDF path")
            c.add_argument("--letter", action="store_true",
                           help="INTERNAL ONLY until attorney review (docs/LEGAL.md): add a fact-checked draft letter")
    a = p.parse_args()
    e = load_evidence(a.query, a.as_of)
    if a.cmd == "check":
        print(report(e))
    else:
        text = None
        if a.letter:
            print("Note: the draft letter is internal-only until an NC attorney reviews it (docs/LEGAL.md, G.S. 84-2.2).")
            from appealkit.letter import LetterError, draft
            try:
                text = draft(e)
            except LetterError as err:
                print(f"No letter: {err}")
            except (RuntimeError, OSError) as err:  # claude -p missing, timed out or empty: still write the packet
                print(f"No letter (drafting failed: {err})")
        packet.render_pdf(e, a.out, text)
        print(f"wrote {a.out}" + (" (with draft letter)" if text else ""))


if __name__ == "__main__":
    main()
