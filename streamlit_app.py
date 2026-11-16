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


def show(e: dict) -> None:
    st.subheader(f"{e['address']} (Wake REID {e['reid']})")
    if e["preview"]:
        st.warning(packet.PREVIEW_NOTE)
    st.write(f"County assessed value: **{packet.money(e['assessed'])}**. {e['sqft']:,} sq ft, built {e['built']}, "
             f"neighborhood {e['neighborhood']}. Valuation date {e['as_of']}.")
    if e["estimate"] is None:
        st.info("Not enough comparable sales to compare this home.")
        return
    if e["fileable"]:
        st.write(packet.headline(e))
        st.write(f"Your assessed value is **{packet.POSITION[e['position']]}**.")
        st.write(f"Evidence strength: {e['strength']}")
        if e["position"] == "above_range":
            st.caption(packet.MISS_RATE_NOTE)
    else:
        st.info(f"There are too few close comparable sales for an exhibit. {e['strength']} If you appeal anyway, the board "
                "can lower, keep, or raise the value (N.C. G.S. 105-322(g)).")
    st.dataframe([{"Address": c["address"], "Sold": c["sold"], "Price": packet.money(c["price"]), "Sq ft": c["sqft"],
                   "Built": c["built"], "Miles": round(c["miles"], 2) if c["miles"] is not None else None,
                   "Adjusted": packet.money(c["adjusted"]), "Gross adj.": f"{c['gross_pct']:.0%}"} for c in e["comps"]],
                 hide_index=True)
    st.download_button("Download the PDF evidence packet", pdf_bytes(e), file_name=f"appeal-kit-{e['reid']}.pdf",
                       mime="application/pdf")


st.set_page_config(page_title="Appeal Kit (preview)", page_icon="🏠")
st.title("Appeal Kit (preview)")
st.write("Compare a Wake County, NC home's assessed value with recent qualified sales of similar homes nearby, and "
         "download the evidence as a PDF.")
query = st.text_input("Wake County address (e.g. 617 Brier Crossings Loop, Durham) or Real Estate ID", max_chars=200)
if query.strip():
    if over_limit():
        st.error("The site is busy. Please try again in a minute.")
    else:
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
