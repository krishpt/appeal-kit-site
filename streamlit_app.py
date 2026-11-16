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
.block-container{max-width:980px;padding-top:2rem}
.hero{padding:1.4rem 1.6rem;border-radius:16px;background:linear-gradient(135deg,#1e3a5f,#2c6e9b);color:#fff;margin-bottom:1.2rem}
.hero h1{margin:0 0 .3rem 0;font-size:2rem;color:#fff}.hero p{margin:0;opacity:.9}
.pill{display:inline-block;padding:.25rem .8rem;border-radius:999px;font-weight:600;font-size:.9rem;color:#fff}
.pill.above{background:#c0392b}.pill.within{background:#2e8b57}.pill.below{background:#2c6e9b}.pill.none{background:#7f8c8d}
.gauge{position:relative;height:14px;border-radius:7px;background:#d6dde5;margin:1.6rem 0 .4rem}
.gauge .band{position:absolute;top:0;bottom:0;border-radius:7px;background:#7fb7d9}
.gauge .mark{position:absolute;top:-6px;width:4px;height:26px;background:#c0392b;border-radius:2px}
.gauge .lbl{position:absolute;top:-28px;transform:translateX(-50%);font-size:.75rem;font-weight:600;white-space:nowrap}
.small{font-size:.85rem;opacity:.75}
</style>
"""
PILL = {"above_range": ("above", "Above comparable range"), "within_range": ("within", "Within comparable range"),
        "below_range": ("below", "Below comparable range"), "not_enough_data": ("none", "Not enough data")}
ADJ = [("time", "Market time"), ("land", "Land"), ("size", "Size"), ("age", "Age"), ("quality", "Quality")]


def gauge(e: dict) -> str:
    s = e["summary"]
    lo, hi = min(s["lowest"], e["assessed"]), max(s["highest"], e["assessed"])
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    pos = lambda v: 100 * (v - lo) / (hi - lo)
    return (f'<div class="gauge"><div class="band" style="left:{pos(s["lowest"]):.1f}%;width:{pos(s["highest"]) - pos(s["lowest"]):.1f}%"></div>'
            f'<div class="mark" style="left:{pos(e["assessed"]):.1f}%"></div>'
            f'<div class="lbl" style="left:{pos(e["assessed"]):.1f}%;color:#c0392b">County: {packet.money(e["assessed"])}</div></div>'
            f'<div class="small">Blue band = lowest to highest adjusted comparable sale ({packet.money(s["lowest"])} to {packet.money(s["highest"])}).</div>')


def charts(e: dict) -> None:
    import altair as alt
    import pandas as pd
    df = pd.DataFrame([{"Home": c["address"], "Adjusted": c["adjusted"], "Sold price": c["price"], "Miles": c["miles"] or 0,
                        "dx": c["dx_mi"], "dy": c["dy_mi"]} for c in e["comps"]])
    base = alt.Chart(df).encode(y=alt.Y("Home:N", sort="-x", title=None))
    bars = base.mark_bar(color="#7fb7d9").encode(x=alt.X("Adjusted:Q", title="Adjusted comparable price ($)", scale=alt.Scale(zero=False)),
                                                  tooltip=["Home", "Sold price", "Adjusted"])
    rule = alt.Chart(pd.DataFrame({"v": [e["assessed"]]})).mark_rule(color="#c0392b", strokeWidth=3).encode(x="v:Q")
    st.altair_chart((bars + rule).properties(height=max(120, 34 * len(df))), width="stretch")
    st.caption("Red line = the county's assessed value for your home.")
    if df.dx.notna().any():
        pts = df.dropna(subset=["dx", "dy"])
        you = pd.DataFrame({"dx": [0.0], "dy": [0.0], "Home": ["Your home"]})
        m = alt.Chart(pts).mark_circle(size=140, color="#2c6e9b").encode(
            x=alt.X("dx:Q", title="miles east of your home"), y=alt.Y("dy:Q", title="miles north"), tooltip=["Home", "Miles"])
        y = alt.Chart(you).mark_point(shape="diamond", size=260, color="#c0392b", filled=True).encode(x="dx:Q", y="dy:Q", tooltip=["Home"])
        st.altair_chart((m + y).properties(height=320), width="stretch")
        st.caption("Where the comparable sales are relative to your home (red diamond).")


def show(e: dict) -> None:
    st.subheader(f"{e['address']}")
    st.caption(f"Wake REID {e['reid']} | neighborhood {e['neighborhood']} | valuation date {e['as_of']}")
    if e["preview"]:
        st.warning(packet.PREVIEW_NOTE)
    if e["estimate"] is None:
        st.info("Not enough comparable sales to compare this home.")
        return
    cls, label = PILL[e["position"]]
    s = e["summary"]
    st.markdown(f'<span class="pill {cls}">{label}</span>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("County assessed value", packet.money(e["assessed"]))
    c2.metric("Median adjusted comp", packet.money(s["median"]), f"{(s['median'] - e['assessed']) / e['assessed']:+.1%} vs county")
    c3.metric("Comparable sales used", s["n"])
    c4.metric("Home", f"{e['sqft']:,} sq ft", f"built {e['built']}", delta_color="off")
    st.markdown(gauge(e), unsafe_allow_html=True)
    tab_sum, tab_chart, tab_comps, tab_adj, tab_how = st.tabs(["Summary", "Charts", "Comparable sales", "Adjustments", "How this works"])
    with tab_sum:
        if e["fileable"]:
            st.write(packet.headline(e))
            st.write(f"Your assessed value is **{packet.POSITION[e['position']]}**.")
            st.write(f"Evidence strength: {e['strength']}")
            if e["position"] == "above_range":
                st.caption(packet.MISS_RATE_NOTE)
        else:
            st.info(f"There are too few close comparable sales for an exhibit. {e['strength']} If you appeal anyway, the board "
                    "can lower, keep, or raise the value (N.C. G.S. 105-322(g)).")
        a = e["all_similar"]
        st.caption(f"Across all {a['n']} similar sales nearby, {a['below_assessed']} adjust to below your assessed value (median {packet.money(a['median'])}).")
    with tab_chart:
        charts(e)
    with tab_comps:
        st.dataframe([{"Address": c["address"], "Sold": c["sold"], "Price": packet.money(c["price"]), "Sq ft": c["sqft"],
                       "Built": c["built"], "Miles": round(c["miles"], 2) if c["miles"] is not None else None,
                       "County value": packet.money(c["county_value"]) if c["county_value"] else None,
                       "Adjusted": packet.money(c["adjusted"]), "Gross adj.": f"{c['gross_pct']:.0%}"} for c in e["comps"]],
                     hide_index=True, width="stretch")
    with tab_adj:
        st.write("How each sale price is adjusted to match your home (rounded to $100):")
        st.dataframe([{"Address": c["address"], "Sold price": packet.money(c["price"]),
                       **{name: f"{c[k]:+,.0f}" for k, name in ADJ if k in c}, "Adjusted": packet.money(c["adjusted"])}
                      for c in e["comps"]], hide_index=True, width="stretch")
    with tab_how:
        st.markdown(HOW)
    st.download_button("Download the PDF evidence packet", pdf_bytes(e), file_name=f"appeal-kit-{e['reid']}.pdf",
                       mime="application/pdf", type="primary")


HOW = """
1. **Find similar sales.** Qualified sales from the last 24 months in your neighborhood; if there are too few, within 1 mile, then your township.
2. **Adjust each sale to your home** for land value, size, age and county quality grade and condition.
3. **Compare** your county value with the range of adjusted prices and show where it falls.
4. **Say how strong the evidence is.** Few or loosely matched sales are labeled WEAK instead of hidden.

**Limits.** In our backtest the typical error was about 7% (5% where evidence is strong), similar to Zillow's. Homes on 5+ acres or
older than 45 years are the weakest. No adjustment is made for market movement. Appeal Kit makes no recommendation and never represents anyone.
"""

st.set_page_config(page_title="Appeal Kit (preview)", page_icon="🏠", layout="centered")
st.markdown(CSS, unsafe_allow_html=True)
st.markdown('<div class="hero"><h1>Appeal Kit</h1><p>Is your Wake County home assessed fairly? Compare it with recent qualified '
            'sales of similar nearby homes, then download the evidence as a PDF.</p></div>', unsafe_allow_html=True)
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
