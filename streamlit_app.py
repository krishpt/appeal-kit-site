"""Free hosting front end (Streamlit Community Cloud). Same engine and PDF as web/app.py; no letter, no recommendations,
nothing logged. Run locally: .venv/bin/streamlit run web/streamlit_app.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the deploy repo keeps appealkit/ next to this file
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # ...and the project keeps it one level up

import streamlit as st  # noqa: E402

from appealkit import packet  # noqa: E402
from appealkit.service import NOT_SINGLE_FAMILY, Engine, listing  # noqa: E402

SITE_LIMIT = 200  # lookups per minute across the whole site: bounds bulk scraping


@st.cache_resource
def engine() -> Engine:
    here = Path(__file__).resolve().parent
    root = os.environ.get("APPEALKIT_DATA") or str(here / "data/raw" if (here / "data/raw").exists() else here.parent / "data/raw")
    return Engine.from_files(root)


@st.cache_resource
def site_hits() -> deque:
    return deque()


def over_limit() -> bool:
    q, now = site_hits(), time.monotonic()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= SITE_LIMIT:
        return True
    q.append(now)
    return False


def pdf_bytes(e: dict) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        packet.render_pdf(e, f"{d}/p.pdf")
        return Path(f"{d}/p.pdf").read_bytes()


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap');
:root{--ink:#0F172A;--muted:#475569;--line:#CBD5E1;--accent:#0369A1;--above:#B91C1C;--within:#15803D;--below:#0369A1}
html,body,[class*="st-"],button,input{font-family:'Atkinson Hyperlegible',system-ui,sans-serif}
.block-container{max-width:860px;padding-top:2.5rem}
h1{font-size:2.4rem!important;letter-spacing:-.02em;margin-bottom:.2rem}
.lede{font-size:1.15rem;color:var(--muted);max-width:60ch;margin:0 0 1.5rem}
.answer{font-size:1.5rem;line-height:1.35;max-width:46ch;margin:.6rem 0 1rem;text-wrap:balance}
.answer b{font-weight:700}
.pos{display:inline-block;padding:.2rem .75rem;border:2px solid currentColor;border-radius:6px;font-weight:700;font-size:1rem}
.pos.above_range{color:var(--above)}.pos.within_range{color:var(--within)}.pos.below_range{color:var(--below)}.pos.not_enough_data{color:var(--muted)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:1rem 2rem;margin:1.2rem 0;padding:1rem 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.facts dt{font-size:.9rem;color:var(--muted)}.facts dd{margin:0;font-size:1.1rem;font-weight:700}
.facts dd,table,[data-testid="stDataFrame"]{font-variant-numeric:tabular-nums}
.gauge{position:relative;height:12px;border-radius:6px;background:#E2E8F0;margin:2.4rem 0 .5rem}
.gauge .band{position:absolute;top:0;bottom:0;border-radius:6px;background:#7DB6D6}
.gauge .mark{position:absolute;top:-8px;width:4px;height:28px;background:var(--above);border-radius:2px}
.gauge .lbl{position:absolute;top:-34px;transform:translateX(-50%);font-size:.95rem;font-weight:700;color:var(--above);white-space:nowrap}
.note{font-size:.95rem;color:var(--muted);max-width:65ch}
::selection{background:#BAE0F5;color:var(--ink)}
:focus-visible{outline:3px solid var(--accent)!important;outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
"""
NEXT = [s.format(attach="") for s in packet.FILING if not s.startswith("Send the exhibit")]
HOW = """
1. **Find similar sales.** Qualified sales from the last 24 months in your neighborhood. With too few, widen to 1 mile, then your township.
2. **Adjust each sale to your home** for land value, size, age, and the county's quality grade and condition.
3. **Compare** your county value with the range of adjusted prices and show where it falls.
4. **Say how strong the evidence is.** Few or loosely matched sales are labeled WEAK, not hidden.

**Limits.** In our backtest the typical error was about 7% (about 5% where evidence is strong), similar to Zillow's. Homes on 5+ acres or
older than 45 years are the weakest. We make no adjustment for market movement. Appeal Kit makes no recommendation and never represents anyone.
"""
ADJ = [("time", "Market time"), ("land", "Land"), ("size", "Size"), ("age", "Age"), ("quality", "Quality")]


def gauge(e: dict) -> str:
    s = e["summary"]
    lo, hi = min(s["lowest"], e["assessed"]), max(s["highest"], e["assessed"])
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    pos = lambda v: 100 * (v - lo) / (hi - lo)
    return (f'<div role="img" aria-label="County value {packet.money(e["assessed"])} against adjusted comparable sales from '
            f'{packet.money(s["lowest"])} to {packet.money(s["highest"])}"><div class="gauge">'
            f'<div class="band" style="left:{pos(s["lowest"]):.1f}%;width:{pos(s["highest"]) - pos(s["lowest"]):.1f}%"></div>'
            f'<div class="mark" style="left:{pos(e["assessed"]):.1f}%"></div>'
            f'<div class="lbl" style="left:{min(max(pos(e["assessed"]), 12), 88):.1f}%">County {packet.money(e["assessed"])}</div></div></div>'
            f'<p class="note">Blue band: lowest to highest adjusted comparable sale, {packet.money(s["lowest"])} to {packet.money(s["highest"])}.</p>')


def charts(e: dict) -> None:
    import altair as alt
    import pandas as pd
    df = pd.DataFrame([{"Home": c["address"], "Adjusted": c["adjusted"], "Sold price": c["price"], "Miles": c["miles"] or 0,
                        "dx": c["dx_mi"], "dy": c["dy_mi"]} for c in e["comps"]])
    base = alt.Chart(df).encode(y=alt.Y("Home:N", sort="-x", title=None))
    bars = base.mark_bar(color="#0369A1").encode(x=alt.X("Adjusted:Q", title="Adjusted comparable price ($)", scale=alt.Scale(zero=False)),
                                                  tooltip=["Home", "Sold price", "Adjusted"])
    rule = alt.Chart(pd.DataFrame({"v": [e["assessed"]]})).mark_rule(color="#B91C1C", strokeWidth=3).encode(x="v:Q")
    st.altair_chart((bars + rule).properties(height=max(120, 34 * len(df))), width="stretch")
    st.caption("Red line: the county's assessed value for your home. The Comparable sales tab has the same numbers as a table.")
    if df.dx.notna().any():
        pts = df.dropna(subset=["dx", "dy"])
        you = pd.DataFrame({"dx": [0.0], "dy": [0.0], "Home": ["Your home"]})
        m = alt.Chart(pts).mark_circle(size=140, color="#0369A1").encode(
            x=alt.X("dx:Q", title="miles east of your home"), y=alt.Y("dy:Q", title="miles north"), tooltip=["Home", "Miles"])
        y = alt.Chart(you).mark_point(shape="diamond", size=260, color="#B91C1C", filled=True).encode(x="dx:Q", y="dy:Q", tooltip=["Home"])
        st.altair_chart((m + y).properties(height=320), width="stretch")
        st.caption("Comparable sales (blue) around your home (red diamond). Distances are straight-line.")


def answer(e: dict) -> str:
    s, a = e["summary"], e["assessed"]
    diff = (s["median"] - a) / a
    side = "lower" if diff < 0 else "higher"
    return (f'The county values your home at <b>{packet.money(a)}</b>. {s["n"]} comparable sales, adjusted to your home, have a median of '
            f'<b>{packet.money(s["median"])}</b>, {abs(diff):.1%} {side}.')


def show(e: dict) -> None:
    st.subheader(e["address"])
    if e["preview"]:
        st.warning(packet.PREVIEW_NOTE)
    if e["estimate"] is None:
        st.info("Not enough comparable sales to compare this home.")
        return
    st.markdown(f'<span class="pos {e["position"]}">Your assessed value is {packet.POSITION[e["position"]]}</span>'
                f'<p class="answer">{answer(e)}</p>', unsafe_allow_html=True)
    st.markdown(gauge(e), unsafe_allow_html=True)
    st.markdown(f'<dl class="facts"><div><dt>Real Estate ID</dt><dd>{e["reid"]}</dd></div>'
                f'<div><dt>Size and age</dt><dd>{e["sqft"]:,} sq ft, built {e["built"]}</dd></div>'
                f'<div><dt>Neighborhood</dt><dd>{e["neighborhood"]}</dd></div>'
                f'<div><dt>Valuation date</dt><dd>{e["as_of"]}</dd></div></dl>', unsafe_allow_html=True)
    tab_sum, tab_comps, tab_chart, tab_adj, tab_next, tab_how = st.tabs(
        ["Evidence", "Comparable sales", "Charts", "Adjustments", "Next steps", "How this works"])
    with tab_sum:
        st.write(f"Evidence strength: {e['strength']}")
        if e["fileable"]:
            st.write(packet.headline(e))
            if e["position"] == "above_range":
                st.caption(packet.MISS_RATE_NOTE)
        else:
            st.info("There are too few close comparable sales for an exhibit. If you appeal anyway, the board "
                    "can lower, keep, or raise the value (N.C. G.S. 105-322(g)).")
        a = e["all_similar"]
        st.caption(f"Across all {a['n']} similar sales nearby, {a['below_assessed']} adjust to below your assessed value (median {packet.money(a['median'])}).")
    with tab_comps:
        st.dataframe([{"Address": c["address"], "Sold": c["sold"], "Price": packet.money(c["price"]), "Sq ft": c["sqft"],
                       "Built": c["built"], "Miles": round(c["miles"], 2) if c["miles"] is not None else None,
                       "County value": packet.money(c["county_value"]) if c["county_value"] else None,
                       "Adjusted": packet.money(c["adjusted"]), "Gross adj.": f"{c['gross_pct']:.0%}"} for c in e["comps"]],
                     hide_index=True, width="stretch")
    with tab_chart:
        charts(e)
    with tab_adj:
        st.write("How each sale price is adjusted to match your home (rounded to $100):")
        st.dataframe([{"Address": c["address"], "Sold price": packet.money(c["price"]),
                       **{name: f"{c[k]:+,.0f}" for k, name in ADJ if k in c}, "Adjusted": packet.money(c["adjusted"])}
                      for c in e["comps"]], hide_index=True, width="stretch")
    with tab_next:
        st.markdown("\n".join(f"{i}. {t}" for i, t in enumerate(NEXT, 1)))
        st.caption("Dates are planned for 2027 and must be confirmed on your Notice of Assessed Value.")
    with tab_how:
        st.markdown(HOW)
    st.download_button("Download the PDF evidence packet", pdf_bytes(e), file_name=f"appeal-kit-{e['reid']}.pdf",
                       mime="application/pdf", type="primary")


st.set_page_config(page_title="Appeal Kit (preview)", page_icon=":material/home:", layout="centered")
st.markdown(CSS, unsafe_allow_html=True)
st.title("Appeal Kit")
st.markdown('<p class="lede">Is your Wake County home assessed fairly? See how the county\'s value compares with recent '
            'qualified sales of similar nearby homes, and download the evidence as a PDF.</p>', unsafe_allow_html=True)
query = st.text_input("Wake County address or Real Estate ID", placeholder="617 Brier Crossings Loop, Durham", max_chars=200)
if query.strip():
    if over_limit():
        st.error("The site is busy. Please try again in a minute.")
    else:
        with st.spinner("Comparing with recent sales..."):
            kind, got = engine().lookup(query.strip())
        if kind == "found":
            show(got)
        elif kind == "not_single_family":
            st.info(NOT_SINGLE_FAMILY.format(address=got.ADDRESS, city=got.CITY))
        elif kind == "several":
            st.write("Several matches. Type one of these (or its Real Estate ID):")
            st.write("\n".join(f"- {t}" for t in listing(got)))
        else:
            st.write("No matching Wake County parcel." + (" Did you mean:\n" + "\n".join(f"- {t}" for t in listing(got))
                                                          if len(got) else ""))
st.divider()
st.caption(packet.DISCLAIMER)
st.caption("About: Wake County Tax Administration public data files; Parcel Address Points from Wake County Open Data "
           "(CC-BY-4.0, credit: Wake County GIS). Appeal Kit never represents anyone and makes no recommendations.")
