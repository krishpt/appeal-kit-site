"""The evidence packet. `evidence()` builds one dict holding every number we print (text report, PDF, letter facts);
`render_pdf()` only lays it out. Page 1 is for the homeowner; the exhibit pages are facts only, for the county.
Every summary figure is derived from rows printed in the exhibit, so an appraiser can trace each one.
"""

from __future__ import annotations

import io
import textwrap
from datetime import date
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.graphics.shapes import Circle, Drawing, Line, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from appealkit import value

DISCLAIMER = ("Self-help evidence from Wake County public records (Tax Administration; GIS data CC-BY-4.0, Wake County GIS). "
              "Not legal, tax or appraisal advice. Appeal Kit does not represent you. You decide whether to appeal and you file it yourself.")
# Gate 1c read, 2026-11-14 (BET.md): 5,791 test sales; 372 had strong evidence of an assessment >= 5% and $10,000 above
# the comparable middle and above the range; 83 of those still sold for at least their assessed value.
GATE1C = {"sales": 5791, "flagged": 372, "sold_at_or_above": 83}
MISS_RATE_NOTE = (f"How sure is this? We tested the method on {GATE1C['sales']:,} Wake home sales from 2025-26. Of "
                  f"{GATE1C['flagged']} homes whose assessment sat clearly above strong comparable evidence, "
                  f"{GATE1C['sold_at_or_above']} ({GATE1C['sold_at_or_above'] / GATE1C['flagged']:.0%}) still sold for at least "
                  "their assessed value. Comparable sales are evidence, not proof.")
PREVIEW_NOTE = ("PREVIEW, DO NOT FILE: Wake has not published 2027 values yet, so this uses the 2024 assessed value. "
                "Re-run after your 2027 notice arrives (by Jan 22, 2027).")
TIME_NOTE = ("No adjustment for market change over time was applied: each sale is used at its own price. During 2026 Wake "
             "sale prices ran 1.014 to 1.027 times their 2024 assessments by quarter (our calculation from Wake's qualified "
             "sales), so the market was roughly flat.")
HEARING = [
    "You have five minutes to state your opinion of value (BOER Rule 20c). A simple order: the county's assessed value; "
    "the three closest sales in the exhibit (address, date, price); your opinion of value and why.",
    "Only the owner or a certified appraiser may give an opinion of value (Rule 13). Fill in and sign the line at the top of the exhibit.",
    "Do not contact board members before the hearing (Rule 5). You may reschedule once, at least 48 hours ahead (Rule 6).",
    "The county sends the docket and its evidence at least 3 days before the hearing (Rule 17).",
    "If the county recommends an increase, the item is still heard even if you withdraw (Rule 22).",
    "Home owned by an LLC or other entity? Owner-representative paperwork is required (Rules 9 and 25a).",
]
GROSS_FLAG = 0.25  # rule of thumb: a comparable needing > 25% total adjustment is a weak comparable
POSITION = {"above_range": "higher than most adjusted comparable prices",
            "within_range": "in line with the adjusted comparable prices",
            "below_range": "lower than most adjusted comparable prices",
            "not_enough_data": "not comparable: too few similar sales"}
# Filing facts, verified 2026-11-14 (docs/APPEAL_PROCESS.md). Re-verify when 2027 notices arrive.
FILING = [
    "Find your Real Estate ID and access code on your Notice of Assessed Value (owners start receiving them by Jan 22, 2027).",
    "Informal review: ask a Wake Tax Administration appraiser to review your value, online in Wake's Tax Portal, by mail "
    "(Wake County Tax Administration, P.O. Box 2331, Raleigh NC 27602) or in person.{attach} The appraiser decides.",
    "Formal appeal: file with the Board of Equalization and Review (BOER), with or without an informal review first. "
    "The board decides at a hearing; you do not have to attend in person. Planned 2027 deadline: April 30, 2027 (confirm on your notice).",
    "Send the exhibit pages when you file. The county must receive all evidence by 5:00 PM Eastern, 10 days before your "
    "hearing; later evidence may not be considered (BOER Rule 7). Send it by the appeal portal, county email, mail, courier "
    "or in person. No copy count or page limit is set.",
    "There is no fee. Appeals are about the value only; tax rates cannot be appealed. Market changes after Jan 1, 2027 cannot be considered.",
    "The board can lower, keep, or raise the value it reviews (N.C. G.S. 105-322(g)).",
    "Questions: Wake revaluation line 919-857-3800, revaluation@wake.gov.",
]
SOURCES = [
    "Sales, values, property records: Wake County Tax Administration data files (services.wake.gov/realdata_extracts).",
    "Locations: Wake County Open Data, Parcel Address Points (CC-BY-4.0). Distances are straight-line.",
    "Process and dates: wake.gov Revaluation 2027; wake.gov/tax/appeals; BOER Rules of Procedure (effective Jan 1, 2027).",
]
METHOD = [
    ("Selection", f"sales Wake marks 'qualified' (reviewed by a Wake appraiser as arm's-length, per Wake's data page) with "
                  f"Wake's single-family building code, in the {value.LOOKBACK_DAYS} days before "
                  f"the valuation date, within {value.SIZE_BAND:.0%} of your heated size and {value.YEAR_BAND} years of your "
                  f"build year; your Wake neighborhood code first, then within 1 mile; the {value.MAX_COMPS} most similar."),
    ("Land", "the county's own land value for your lot minus the comparable's."),
    ("Size", f"{value.MARGINAL_SQFT:.0%} of the comparable's building price per square foot, times the difference in heated square feet."),
    ("Age", f"{value.AGE_RATE:.1%} of the comparable's building price per year newer or older, capped at {value.AGE_CAP} years."),
    ("Quality", "the county's construction grade, condition % and effective (renovation-adjusted) year, at rates measured on Wake sales."),
    ("Rounding", "each adjustment is rounded to the nearest $100; the adjusted price is the sale price plus the rounded adjustments."),
]
PARTS = ("time", "land", "size", "age", "quality")


def money(x: float) -> str:
    return f"${x:,.0f}"


def _street(c) -> str:
    return f"{int(c.STREET_NUM)} {c.STREET_NAME} {c.STREET_TYPE if pd.notna(c.STREET_TYPE) else ''}".strip()


def _miles(a, b) -> float | None:
    if pd.isna(a.get("X")) or pd.isna(b.get("X")):
        return None
    return float(((a["X"] - b["X"]) ** 2 + (a["Y"] - b["Y"]) ** 2) ** 0.5 / 5280)


def _record(r) -> dict:
    """The county's record for a house, as printed in the exhibit (missing fields print blank)."""
    g = lambda k: r.get(k) if pd.notna(r.get(k)) else None  # noqa: E731
    return {"eff": int(g("EFF")) if g("EFF") else None, "grade": g("GRADE_CODE"), "cond": int(g("COND")) if g("COND") else None,
            "baths": g("BATHS"), "acres": float(g("ACRES")) if g("ACRES") is not None else None,
            "land": float(g("LAND_VALUE_ASSD")) if g("LAND_VALUE_ASSD") is not None else None}


def evidence(subject: dict, comps: pd.DataFrame, est: dict | None, as_of: pd.Timestamp, preview: bool = False,
             neighborhood: pd.DataFrame | None = None) -> dict:
    """Everything the packet says, as data."""
    e = {"address": f"{subject['ADDRESS']}, {subject['CITY']}", "reid": int(subject["REID"]), "as_of": as_of.date().isoformat(),
         "assessed": float(subject["ASSESSED"]), "sqft": int(subject["TOTAL_HEATED_AREA"]), "built": int(subject["YEAR_BUILT"]),
         "neighborhood": subject["VCS"], "record": _record(subject), "estimate": est, "comps": [], "others": [],
         "preview": preview, "time_adjusted": False}
    if est is None:
        e.update(position="not_enough_data", strength=None, summary=None, fileable=False)
        return e
    raw = value.verdict(e["assessed"], est, is_confident=value.confident(est, subject, as_of))
    e.update(position=value.shown_verdict(raw, e["assessed"], est), strength=strength(est, subject, as_of))
    dropped: set = set()
    search = comps.POOL.iloc[0] if "POOL" in comps and len(comps) else "neighborhood"
    for _, c in comps.iterrows():
        raw_adj = value.adjust(c, subject)
        if pd.isna(raw_adj["adjusted"]):  # a missing county field: estimate() drops this comp, so the exhibit does too
            dropped.add((int(c.REID), c.SALE_DATE.date().isoformat()))
            continue
        a = {k: round(v, -2) for k, v in raw_adj.items() if k != "adjusted"}
        adjusted = round(c.SALE_PRICE) + sum(a.values())
        miles = _miles(c, subject)
        e["comps"].append({
            "reid": int(c.REID), "address": _street(c), "sold": c.SALE_DATE.date().isoformat(), "price": float(c.SALE_PRICE),
            "sqft": int(c.TOTAL_HEATED_AREA), "built": int(c.YEAR_BUILT), "same_neighborhood": bool(c.VCS == subject["VCS"]),
            "county_value": float(c.TOTAL_PROP_VALUE_ASSD) if pd.notna(c.get("TOTAL_PROP_VALUE_ASSD")) else None,
            "record": _record(c), "miles": miles,
            "dx_mi": float((c.X - subject["X"]) / 5280) if miles is not None else None,
            "dy_mi": float((c.Y - subject["Y"]) / 5280) if miles is not None else None,
            **{k: float(v) for k, v in a.items()}, "adjusted": float(adjusted),
            "gross_pct": sum(abs(v) for v in a.values()) / float(c.SALE_PRICE)})
    adj = sorted(c["adjusted"] for c in e["comps"])
    n = len(adj)
    median = adj[n // 2] if n % 2 else (adj[n // 2 - 1] + adj[n // 2]) / 2
    e["summary"] = {"n": n, "lowest": adj[0], "median": median, "median_is_average": n % 2 == 0, "highest": adj[-1],
                    "below_assessed": sum(x < e["assessed"] for x in adj)}
    e["time_adjusted"] = any(c["time"] for c in e["comps"])
    heavy = sum(c["gross_pct"] > GROSS_FLAG for c in e["comps"])
    if heavy:  # the cover must not sound surer than the exhibit
        reason = f"{heavy} of {n} sales needed large adjustments (over {GROSS_FLAG:.0%} of the price)"
        e["strength"] = (f"WEAK, because {reason}." if e["strength"].startswith("GOOD")
                         else e["strength"][:-1] + f"; {reason}.")
    used = {(c["reid"], c["sold"]) for c in e["comps"]}
    similar = [c["adjusted"] for c in e["comps"]]
    if neighborhood is not None and len(neighborhood):
        for _, s in neighborhood.iterrows():
            if (int(s.REID), s.SALE_DATE.date().isoformat()) in used:
                continue
            if (int(s.REID), s.SALE_DATE.date().isoformat()) in dropped:
                why = "missing a county record field needed to adjust it"
            elif abs(s.TOTAL_HEATED_AREA - e["sqft"]) > value.SIZE_BAND * e["sqft"]:
                why = f"size more than {value.SIZE_BAND:.0%} different"
            elif abs(s.YEAR_BUILT - e["built"]) > value.YEAR_BAND:
                why = f"built more than {value.YEAR_BAND} years apart"
            elif search == "radius" and (_miles(s, subject) or 0) > value.RADIUS_FT / 5280:
                why = "over 1 mile away (search widened: too few similar sales in your neighborhood)"
            else:
                why = "similar, but ranked below the sales used"
                a = value.adjust(s, subject)
                if pd.notna(a["adjusted"]):
                    similar.append(round(s.SALE_PRICE) + sum(round(v, -2) for k, v in a.items() if k != "adjusted"))
            e["others"].append({"reid": int(s.REID), "address": _street(s), "sold": s.SALE_DATE.date().isoformat(),
                                "price": float(s.SALE_PRICE), "sqft": int(s.TOTAL_HEATED_AREA), "built": int(s.YEAR_BUILT),
                                "miles": _miles(s, subject), "why": why})
        e["others"].sort(key=lambda o: (o["miles"] is None, o["miles"] or 0))
    similar.sort()
    k = len(similar)
    e["all_similar"] = {"n": k, "median": similar[k // 2] if k % 2 else (similar[k // 2 - 1] + similar[k // 2]) / 2,
                        "below_assessed": sum(x < e["assessed"] for x in similar)}
    e["fileable"] = e["strength"].startswith("GOOD")  # an exhibit is printed only for strong evidence (red-team 2026-11-15)
    return e


def strength(est: dict, subject: dict, as_of: pd.Timestamp) -> str:
    """Plain-language confidence, from the gate-1b rule: say why the evidence is weak instead of hiding it."""
    if value.confident(est, subject, as_of):
        return "GOOD: all sales are from your Wake neighborhood and their adjusted prices are tightly grouped."
    why = []
    if not est["same_vcs"]:
        why.append("some sales are from nearby neighborhoods, not yours")
    if est["spread"] > value.MAX_SPREAD:
        why.append("the adjusted prices are spread widely")
    if as_of.year - subject["YEAR_BUILT"] >= value.MAX_AGE:
        why.append(f"older homes ({value.MAX_AGE}+ years) vary more than the data can show")
    return "WEAK, because " + "; ".join(why) + "."


def headline(e: dict) -> str:
    """One traceable sentence: counts and the lowest / median / highest adjusted prices from the exhibit."""
    s = e["summary"]
    mid = "average of the two middle sales" if s["median_is_average"] else "middle sale"
    return (f"{s['below_assessed']} of {s['n']} comparable sales, adjusted to your home, are below your assessed value of "
            f"{money(e['assessed'])}. Adjusted prices run from {money(s['lowest'])} to {money(s['highest'])} "
            f"(median {money(s['median'])}, the {mid}).")


def comp_map(e: dict, size: float = 3.0 * inch) -> Drawing:
    """Your home at the center, comparables at their true offsets (NC State Plane), with a scale bar. No map tiles."""
    d = Drawing(size, size + 14)
    pts = [(c["dx_mi"], c["dy_mi"]) for c in e["comps"] if c["dx_mi"] is not None]
    reach = max([max(abs(x), abs(y)) for x, y in pts] + [0.1]) * 1.15
    scale, mid = (size / 2 - 14) / reach, size / 2
    d.add(Line(mid, 8, mid, size - 8, strokeColor=colors.lightgrey))
    d.add(Line(8, mid, size - 8, mid, strokeColor=colors.lightgrey))
    d.add(String(mid + 3, size - 12, "N", fontSize=8, fillColor=colors.grey))
    placed = [(mid, mid)]
    for i, (x, y) in enumerate(pts, 1):
        tx, ty = mid + x * scale, mid + y * scale
        cx, cy = tx, ty
        while any((cx - px) ** 2 + (cy - py) ** 2 < 13 ** 2 for px, py in placed):  # nudge overlapping markers aside
            cx += 13
        if (cx, cy) != (tx, ty):
            d.add(Line(tx, ty, cx, cy, strokeColor=colors.grey, strokeWidth=0.5))
            d.add(Circle(tx, ty, 1.5, fillColor=colors.grey, strokeColor=colors.grey))
        placed.append((cx, cy))
        d.add(Circle(cx, cy, 6, fillColor=colors.HexColor("#3b6ea8"), strokeColor=colors.white))
        d.add(String(cx - 2.5, cy - 3, str(i), fontSize=7, fillColor=colors.white))
    d.add(Circle(mid, mid, 7, fillColor=colors.HexColor("#c0392b"), strokeColor=colors.white))
    d.add(Circle(12, size + 6, 5, fillColor=colors.HexColor("#c0392b"), strokeColor=colors.white))  # legend, not a label
    d.add(String(20, size + 3, "subject property     numbered dots = sales used (table 1 rows)", fontSize=7))
    bar_mi = 0.1 if reach < 0.5 else 0.25 if reach < 1.5 else 1.0
    d.add(Line(10, 10, 10 + bar_mi * scale, 10, strokeWidth=2))
    d.add(String(10, 14, f"{bar_mi:g} mile", fontSize=7))
    return d


def _table(rows, right_from=None, highlight_last=False, font=7.5):
    t = Table(rows, repeatRows=1)
    style = [("FONTSIZE", (0, 0), (-1, -1), font), ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
             ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
             ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    if right_from is not None:
        style.append(("ALIGN", (right_from, 1), (-1, -1), "RIGHT"))
    if rows and rows[0][-1] == "Why not used":  # text column stays left-aligned
        style.append(("ALIGN", (-1, 1), (-1, -1), "LEFT"))
    if highlight_last:
        style.append(("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor("#c0392b")))
    t.setStyle(TableStyle(style))
    return t


def render_pdf(e: dict, path: str, letter_text: str | None = None) -> None:
    ss = getSampleStyleSheet()
    body = ss["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9.5, textColor=colors.grey)
    red = ParagraphStyle("red", parent=body, textColor=colors.HexColor("#c0392b"))
    h1, h2 = ss["Title"], ss["Heading2"]
    exhibit = e["estimate"] is not None and e.get("fileable", False)
    fmt = lambda v, f="{}": "" if v is None else f.format(v)  # noqa: E731

    def make() -> list:  # a fresh story per build: reportlab flowables keep layout state
        # Page 1: for the homeowner (not for filing)
        if exhibit:
            intro = "This page is for you. " + ("When your 2027 values arrive, re-run this check, then file its exhibit pages."
                                                if e["preview"] else "File the exhibit pages that follow.")
        else:
            intro = ""
        story = [Paragraph("Your assessment check", h1),
                 Paragraph(f"<b>{escape(e['address'])}</b> (Wake REID {e['reid']}). Valuation date: {e['as_of']}. {intro}", body),
                 Spacer(1, 6)]
        if e["preview"]:
            story += [Paragraph(f"<b>{PREVIEW_NOTE}</b>", red), Spacer(1, 6)]
        if e["estimate"] is None:
            story += [Paragraph("Wake's records do not have enough similar sales near this home to compare it.", body)]
        elif not exhibit:
            story += [Paragraph(f"<b>There are too few close comparable sales for an exhibit.</b> {escape(e['strength'])}", body),
                      Spacer(1, 4),
                      Paragraph("So this packet has no exhibit. If you appeal anyway, the board weighs your evidence against the "
                                "county appraiser's, and it can lower, keep, or raise the value (N.C. G.S. 105-322(g)).", body)]
        else:
            story += [Paragraph(f"<b>What the sales show.</b> {escape(headline(e))} Your assessed value is "
                                f"<b>{POSITION[e['position']]}</b>.", body), Spacer(1, 4),
                      Paragraph(f"<b>Evidence strength:</b> {escape(e['strength'])}", body), Spacer(1, 4),
                      Paragraph(MISS_RATE_NOTE, body), Spacer(1, 4),
                      Paragraph("Your lot's value comes from the same county assessment, so if the county over-valued the lot "
                                "itself, these comparisons inherit that.", small)]
        story += [Spacer(1, 6), Paragraph("How to file (Wake County, 2027)", h2)]
        attach = " Attach the exhibit pages." if exhibit and not e["preview"] else ""
        steps = [s for s in FILING if exhibit or not s.startswith("Send the exhibit")]
        story += [Paragraph(f"{i}. {escape(s.format(attach=attach))}", body) for i, s in enumerate(steps, 1)]
        if exhibit:
            story += [KeepTogether([Paragraph("At the hearing", h2)] + [Paragraph(f"- {escape(s)}", body) for s in HEARING])]

        if exhibit:
            # Exhibit: facts only, written for the county appraiser and the board
            story += [PageBreak(), Paragraph("Exhibit: comparable sales evidence", h1),
                      Paragraph(f"Owner: ______________________ &nbsp; Wake REID {e['reid']} &nbsp; Appeal no.: __________ "
                                f"&nbsp; Prepared: {date.today().isoformat()}", body),
                      Paragraph(f"<b>Subject property:</b> {escape(e['address'])}, neighborhood {e['neighborhood']}. County assessed "
                                f"value {money(e['assessed'])}. Valuation date {e['as_of']}.", body)]
            if e["preview"]:
                story += [Paragraph(f"<b>{PREVIEW_NOTE}</b>", red)]
            a = e["all_similar"]
            story += [Spacer(1, 4),
                      Paragraph("Owner's opinion of value as of January 1, 2027: $______________ &nbsp; "
                                "Owner signature ______________________ &nbsp; Date __________", body),
                      Paragraph("(Only the owner or a certified appraiser may give an opinion of value: BOER Rule 13.)", small),
                      Spacer(1, 4), Paragraph(escape(headline(e).replace("your home", "the subject property")
                                                     .replace("your assessed value", "the assessed value")), body)]
            if a["n"] > e["summary"]["n"]:
                story += [Paragraph(f"Using all {a['n']} similar qualified sales in the neighborhood (the {e['summary']['n']} used "
                                    f"plus {a['n'] - e['summary']['n']} similar ones listed in section 3), the median adjusted price "
                                    f"is {money(a['median'])}, and {a['below_assessed']} of {a['n']} are below the assessed value.", body)]
            story += [Spacer(1, 6), Paragraph("1. Sales used, as recorded by Wake County", h2)]
            rec = [["#", "Wake REID", "Address", "Sold", "Price", "County value", "Sq ft", "Built", "Eff. yr", "Grade", "Cond %",
                    "Baths", "Acres", "Miles"]]
            for i, c in enumerate(e["comps"], 1):
                r = c["record"]
                rec.append([i, c["reid"], c["address"][:24], c["sold"], money(c["price"]), fmt(c["county_value"], "${:,.0f}"),
                            f"{c['sqft']:,}", c["built"], fmt(r["eff"]), fmt(r["grade"]), fmt(r["cond"]), fmt(r["baths"]),
                            fmt(r["acres"], "{:.2f}"), fmt(c["miles"], "{:.2f}")])
            r = e["record"]
            rec.append(["", e["reid"], "Subject property", "", "", money(e["assessed"]), f"{e['sqft']:,}", e["built"], fmt(r["eff"]),
                        fmt(r["grade"]), fmt(r["cond"]), fmt(r["baths"]), fmt(r["acres"], "{:.2f}"), ""])
            story += [_table(rec, right_from=4, highlight_last=True, font=6.6), Spacer(1, 6),
                      Paragraph("2. Adjustments to the subject property (rounded to $100)", h2)]
            parts = [p for p in PARTS if p != "time" or e["time_adjusted"]]
            adj = [["#", "Sale price"] + [p.title() for p in parts] + ["Adjusted price", "Gross adj."]]
            for i, c in enumerate(e["comps"], 1):
                flag = " (weak)" if c["gross_pct"] > GROSS_FLAG else ""
                adj.append([i, money(c["price"])] + [f"{c[p]:+,.0f}" for p in parts] + [money(c["adjusted"]), f"{c['gross_pct']:.0%}{flag}"])
            notes = [Paragraph(f"<b>{k}:</b> {escape(v)}", small) for k, v in METHOD]
            notes += [Paragraph(f"<b>Gross adj.</b> = total size of all adjustments / sale price; over {GROSS_FLAG:.0%} is marked weak.", small)]
            if not e["time_adjusted"]:
                notes.append(Paragraph(escape(TIME_NOTE), small))
            story += [_table(adj, right_from=1), Spacer(1, 4)] + notes
            if e["others"]:
                oth = [["Wake REID", "Address", "Sold", "Price", "Sq ft", "Built", "Miles", "Why not used"]]
                for o in e["others"][:15]:
                    oth.append([o["reid"], o["address"][:24], o["sold"], money(o["price"]), f"{o['sqft']:,}", o["built"],
                                fmt(o["miles"], "{:.2f}"), o["why"]])
                top = max(e["others"], key=lambda o: o["price"])
                story += [Spacer(1, 6), Paragraph("3. Other qualified sales in the neighborhood that were not used", h2),
                          _table(oth, right_from=3, font=6.6), Spacer(1, 2),
                          Paragraph(f"Shown so nothing is hidden: {len(e['others'])} other qualified sale(s) in the subject's Wake "
                                    f"neighborhood in the {value.LOOKBACK_DAYS} days before the valuation date"
                                    f"{', nearest 15 listed' if len(e['others']) > 15 else ''}. Highest price among them: "
                                    f"{escape(top['address'])}, {money(top['price'])} ({top['sqft']:,} sq ft).", small)]
            if any(c["dx_mi"] is not None for c in e["comps"]):
                story += [KeepTogether([Paragraph("4. Where the sales used are", h2), comp_map(e)])]
            story += [Spacer(1, 6), Paragraph("Sources", h2)] + [Paragraph(s, small) for s in SOURCES]

        if letter_text:
            story += [PageBreak(), Paragraph("Draft letter (edit it, add your details, then send it with the exhibit)", h2),
                      Paragraph("Every number in this draft was checked against the exhibit.", small), Spacer(1, 8)]
            story += [Paragraph(escape(par).replace("\n", "<br/>"), body) for par in letter_text.split("\n\n")]

        return story

    def build(target, total):
        def footer(canvas, doc):
            canvas.saveState()
            canvas.setFont("Helvetica", 6.5)
            canvas.setFillColor(colors.grey)
            for k, line in enumerate(textwrap.wrap(DISCLAIMER, 150)[:2]):
                canvas.drawString(0.6 * inch, (0.45 - 0.12 * k) * inch, line)
            canvas.drawRightString(7.9 * inch, 0.33 * inch, f"page {doc.page} of {total}" if total else f"page {doc.page}")
            pages[0] = max(pages[0], doc.page)
            if e["preview"]:
                canvas.setFont("Helvetica-Bold", 9)
                canvas.setFillColor(colors.HexColor("#c0392b"))
                canvas.drawRightString(7.9 * inch, 10.6 * inch, "PREVIEW - DO NOT FILE")
            canvas.restoreState()
        SimpleDocTemplate(target, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.6 * inch,
                          bottomMargin=0.75 * inch, title=f"Assessment evidence: {e['address']}").build(
            make(), onFirstPage=footer, onLaterPages=footer)

    pages = [0]
    build(io.BytesIO(), None)  # first pass counts pages so the footer can say "page x of y"
    build(path, pages[0])
