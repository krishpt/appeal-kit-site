"""Look up one Wake County home and build its evidence. Data is loaded once (Engine), then each lookup is fast.
Used by the CLI (one lookup per process) and the web app (many lookups per process)."""

from __future__ import annotations

import glob
import re

import pandas as pd

from appealkit import packet, value
from appealkit.data import COLS, attach_quality, attach_xy, clean, find_parcels, load_parcels, near_misses

# attach_quality reads only these columns of the full parcel file
RAW_COLS = {"REAL_ESTATE_ID", "CARD_NUMBER", "GRADE", "BATH", "Deeded_Acreage", "Accrued_Assessed_Condition_Pct", "Effective_year"}
NOT_SINGLE_FAMILY = ("{address}, {city}: Wake's records show no single-family house here (vacant land, or "
                     "another property type). Appeal Kit only compares single-family homes.")


def latest(pattern: str) -> str:
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit(f"missing data file {pattern}: see docs/DATA.md")
    return files[-1]


def listing(df: pd.DataFrame) -> list[str]:
    return [f"{r.ADDRESS}, {r.CITY} {r.ZIP} (REID {int(r.REID)})" for r in df.itertuples()]


def build_subject(row: pd.Series, points: pd.DataFrame, parcels_raw: pd.DataFrame) -> dict:
    return attach_quality(attach_xy(row.to_frame().T, points), parcels_raw).iloc[0].to_dict()


class Engine:
    def __init__(self, parcels: pd.DataFrame, points: pd.DataFrame, sales: pd.DataFrame, raw: pd.DataFrame, preview: bool):
        """parcels: load_parcels(); points: PIN_NUM, X, Y; sales: load_sales() output (with X/Y and quality);
        raw: the full parcel file (only the RAW_COLS are used)."""
        self.parcels, self.sales, self.preview = parcels, sales, preview
        self.points = points.groupby(pd.to_numeric(points.PIN_NUM, errors="coerce"))[["X", "Y"]].mean().reset_index()
        self.raw = raw[raw.CARD_NUMBER.astype(str).str.lstrip("0") == "1"]  # attach_quality only uses the main card

    @classmethod
    def from_files(cls, root: str = "data/raw") -> "Engine":
        parcels_path = latest(f"{root}/parcels_*.csv")
        points = pd.read_csv(latest(f"{root}/parcel_address_points_*.csv"), usecols=["PIN_NUM", "X", "Y"])
        raw = pd.read_csv(parcels_path, usecols=lambda c: c in RAW_COLS, dtype={"REAL_ESTATE_ID": str, "CARD_NUMBER": str, "GRADE": str})
        sales = attach_quality(attach_xy(clean(pd.read_excel(latest(f"{root}/Qualified_Sales_Past_24Months_*.xlsx"), usecols=COLS)),
                                         points), raw)
        vintage = re.search(r"(\d{8})", parcels_path)
        return cls(load_parcels(parcels_path), points, sales, raw,
                   bool(vintage) and vintage[1] < "20270119")  # 2027 values are presented Jan 19, 2027

    def lookup(self, query: str, as_of: str = "2027-01-01") -> tuple[str, object]:
        """('found', evidence dict) | ('several', parcels) | ('none', near-miss parcels) | ('not_single_family', parcel row)."""
        hits = find_parcels(self.parcels, query)
        if len(hits) > 1:
            return "several", hits
        if not len(hits):
            return "none", near_misses(self.parcels, query)
        home = hits.iloc[0]
        if not (home.TOTAL_HEATED_AREA > 0 and home.YEAR_BUILT > 0) or pd.to_numeric(home.TYPE_AND_USE, errors="coerce") != 1:
            return "not_single_family", home
        pin = pd.to_numeric(home.PIN_NUM, errors="coerce")
        subject = build_subject(home, self.points[self.points.PIN_NUM == pin], self.raw)
        when = pd.Timestamp(as_of)
        comps = value.select_comps(subject, self.sales, when)
        hood = self.sales[(self.sales.VCS == subject["VCS"]) & (self.sales.SALE_DATE < when) & (self.sales.REID != subject["REID"])
                          & (self.sales.SALE_DATE >= when - pd.Timedelta(days=value.LOOKBACK_DAYS))]
        return "found", packet.evidence(subject, comps, value.estimate(subject, comps), when, self.preview, hood)
