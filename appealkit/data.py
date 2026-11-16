"""Load Wake County's qualified sales file (see docs/DATA.md)."""

from __future__ import annotations

import re

import pandas as pd

COLS = ["REID", "PIN_NUM", "SALE_PRICE", "SALE_DATE", "SALE_TYPE", "BLDG_USE_DESC", "VCS", "TOWNSHIP",
        "PLANNING_JURISDICTION", "YEAR_BUILT", "TOTAL_HEATED_AREA", "TOTAL_HEATED_AREA_ATSALE",
        "LAND_VALUE_ASSD", "PARCEL_ASSD_VALUE_ATSALE", "TOTAL_PROP_VALUE_ASSD",
        "STREET_NUM", "STREET_NAME", "STREET_TYPE", "ZIP"]


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Single-family house sales (not land-only), one row per sale, usable for comps."""
    df = df.copy()
    # a comp is evidence as it was when sold: ~3.5% of houses changed size after the sale.
    # Land stays on the CURRENT value so subject and comps share one assessment basis (matters after the 2027 reval).
    at_sale = df.TOTAL_HEATED_AREA_ATSALE > 0
    df.loc[at_sale, "TOTAL_HEATED_AREA"] = df.loc[at_sale, "TOTAL_HEATED_AREA_ATSALE"]
    df = df[(df.BLDG_USE_DESC == "SINGLFAM") & (df.SALE_TYPE == "Package")
            & (df.TOTAL_HEATED_AREA > 0) & (df.SALE_PRICE > 0) & (df.YEAR_BUILT > 0)]
    return df.drop_duplicates(["REID", "SALE_DATE"]).reset_index(drop=True)


def attach_xy(sales: pd.DataFrame, points: pd.DataFrame) -> pd.DataFrame:
    """Add X/Y (NC State Plane feet) per parcel from Wake's Parcel Address Points; a parcel with several addresses gets their mean."""
    xy = points.groupby(pd.to_numeric(points.PIN_NUM, errors="coerce"))[["X", "Y"]].mean()
    return sales.assign(**xy.reindex(pd.to_numeric(sales.PIN_NUM, errors="coerce")).set_index(sales.index))


_LETTERS = {"E": 0, "D": 1, "C": 2, "B": 3, "A": 4, "AA": 5}
# Wake BATH codes, read from Wake's CodeDescriptions.pdf (2020-10): A-F are bath counts; G-J are not counts.
BATHS = {"A": "1", "B": "1.5", "C": "2", "D": "2.5", "E": "3", "F": "3.5", "G": "limited", "H": "none", "I": "adequate",
         "J": "by fixtures"}


def grade_score(g) -> float:
    """Wake construction grade ("B+05", " C", "AA-10") as a number: one letter = 1.0, +/-25% = one letter."""
    m = re.fullmatch(r"\s*(AA|A|B|C|D|E)([+-]\d+)?\s*", str(g))
    return _LETTERS[m[1]] + (int(m[2]) / 25 if m[2] else 0.0) if m else float("nan")


def attach_quality(sales: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """Add GRADE (score), COND (county condition %) and EFF (effective year) from the full parcel file, main building card."""
    f = features[features.CARD_NUMBER.astype(str).str.lstrip("0") == "1"]
    q = pd.DataFrame({
        "GRADE": f.GRADE.map(grade_score).values,
        "GRADE_CODE": f.GRADE.astype(str).str.strip().values,
        "BATHS": f.BATH.map(BATHS).values if "BATH" in f else None,
        "ACRES": pd.to_numeric(f.Deeded_Acreage, errors="coerce").values if "Deeded_Acreage" in f else None,
        "COND": f.Accrued_Assessed_Condition_Pct.where(f.Accrued_Assessed_Condition_Pct > 0).values,
        "EFF": f.Effective_year.where(f.Effective_year.between(1800, 2100)).values,  # file has 0 and 2999 placeholders
    }, index=pd.to_numeric(f.REAL_ESTATE_ID, errors="coerce").values)
    return sales.assign(**q[~q.index.duplicated()].reindex(pd.to_numeric(sales.REID, errors="coerce")).set_index(sales.index))


def load_sales(path: str, points_path: str | None = None, features_path: str | None = None) -> pd.DataFrame:
    sales = clean(pd.read_excel(path, usecols=COLS))
    if points_path:
        sales = attach_xy(sales, pd.read_csv(points_path, usecols=["PIN_NUM", "X", "Y"]))
    if features_path:
        sales = attach_quality(sales, pd.read_csv(features_path, dtype={"REAL_ESTATE_ID": str, "CARD_NUMBER": str, "GRADE": str}))
    return sales


def pin_from_parcel_id(pid) -> float:
    """'1714.09 05 9993 000' -> 1714059993 (map, then the two blocks after the scale; matches Wake's PIN_NUM)."""
    m = re.fullmatch(r"\s*(\d{4})\.\d+\s+(\d{2})\s+(\d{4}).*", str(pid))
    return float(m[1] + m[2] + m[3]) if m else float("nan")


def load_parcels(path: str) -> pd.DataFrame:
    """One row per parcel (REID) from the extract: card-01 facts, heated area and building value summed over cards."""
    f = pd.read_csv(path, dtype={"REAL_ESTATE_ID": str, "CARD_NUMBER": str, "GRADE": str, "Parcel_Identification": str,
                                 "Street_Prefix": str, "Street_Type": str, "Street_Suffix": str, "VCS": str,
                                 "Planning_Jurisdiction": str, "PHYSICAL_CITY": str, "PHYSICAL_ZIP_CODE": str},
                    keep_default_na=False, na_values=[""])
    f["REID"] = pd.to_numeric(f.REAL_ESTATE_ID, errors="coerce")
    sums = f.groupby("REID")[["HEATED_AREA", "Assessed_Building_Value"]].sum()
    main = f[f.CARD_NUMBER.str.lstrip("0") == "1"].drop_duplicates("REID").set_index("REID")
    parts = main[["Street_Number", "Street_Prefix", "Street_Name", "Street_Type", "Street_Suffix"]].fillna("").astype(str)
    address = (parts.Street_Number + " " + parts.Street_Prefix + " " + parts.Street_Name + " " + parts.Street_Type
               + " " + parts.Street_Suffix).str.replace(r"\s+", " ", regex=True).str.strip()
    return pd.DataFrame({
        "REID": main.index, "PIN_NUM": main.Parcel_Identification.map(pin_from_parcel_id).values,
        "ADDRESS": address.values, "CITY": main.PHYSICAL_CITY.values, "ZIP": main.PHYSICAL_ZIP_CODE.values,
        "STREET_NUM": main.Street_Number.values, "STREET_NAME": main.Street_Name.fillna("").astype(str).values,
        "STREET_PREFIX": parts.Street_Prefix.values, "STREET_TYPE": parts.Street_Type.values, "STREET_SUFFIX": parts.Street_Suffix.values,
        "VCS": main.VCS.values, "TOWNSHIP": pd.to_numeric(main.Township, errors="coerce").values,
        "PLANNING_JURISDICTION": main.Planning_Jurisdiction.values,
        "YEAR_BUILT": main.Year_Built.values, "TOTAL_HEATED_AREA": sums.HEATED_AREA.reindex(main.index).values,
        "LAND_VALUE_ASSD": main.Assessed_Land_Value.values,
        "ASSESSED": main.Assessed_Land_Value.values + sums.Assessed_Building_Value.reindex(main.index).values,
        "TYPE_AND_USE": main.TYPE_AND_USE.values,
    }).reset_index(drop=True)


_TYPES = {"ROAD": "RD", "DRIVE": "DR", "STREET": "ST", "LANE": "LN", "COURT": "CT", "AVENUE": "AVE", "CIRCLE": "CIR",
          "PLACE": "PL", "TRAIL": "TRL", "BOULEVARD": "BLVD", "PARKWAY": "PKWY", "TERRACE": "TER", "HIGHWAY": "HWY",
          "LOOP": "LOOP", "WAY": "WAY", "RUN": "RUN", "COVE": "CV", "POINT": "PT", "CROSSING": "XING", "SQUARE": "SQ"}
_DIRS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}
_TYPE_ABBR = set(_TYPES.values())


def _norm(text: str) -> list[str]:
    toks = re.sub(r"[^A-Z0-9 ]", " ", str(text).upper()).split()
    return [_TYPES.get(x, _DIRS.get(x, x)) for x in toks]


def _street_rest(q: list[str], prefix: str, name: str, typ: str, suffix: str) -> list[str] | None:
    """If query tokens q (after the house number) name this street, return the leftover tokens; else None.
    Prefix, type and suffix may be omitted but not contradicted; any other leftover word is judged by the caller."""
    pre, n, ty, suf = _norm(prefix), _norm(name), _norm(typ), _norm(suffix)
    i = 1 if pre and q[:1] == pre else 0  # "N" is a prefix only if this street has one ("NORTH HILLS DR" is a name)
    if not n or q[i:i + len(n)] != n:
        return None
    i += len(n)
    if ty and q[i:i + len(ty)] == ty:
        i += len(ty)
    elif q[i:i + 1] and q[i] in _TYPE_ABBR:
        return None  # contradicting street type
    if suf and q[i:i + len(suf)] == suf:
        i += len(suf)
    return q[i:]


def _place_only(rest: list[str], city: str, zip_: str) -> bool:
    """True if the leftover words are nothing but this parcel's town and/or ZIP (plus NC / a unit number)."""
    words = list(rest)
    for j, w in enumerate(words):  # skip "APT 12B" style units, but never a town or a 5-digit ZIP after "Apt"
        if w in ("APT", "UNIT", "STE") and j + 1 < len(words) and re.fullmatch(r"\d{1,4}[A-Z]?|[A-Z]\d{0,3}", words[j + 1]):
            words[j:j + 2] = []
            break
    words = [w for w in words if w not in ("NC", "USA")]
    c, z = _norm(city), str(zip_).split(".")[0]
    if words[-1:] == [z]:
        words = words[:-1]
    return words in ([], c)


def find_parcels(parcels: pd.DataFrame, query: str) -> pd.DataFrame:
    """Parcels matching an address ('1506 Wake Forest Rd, Raleigh 27604'), a REID (<= 7 digits) or a 10-digit PIN.
    Returns every plausible match; the caller must refuse to guess when there is more than one."""
    q = _norm(query)
    if len(q) == 1 and q[0].isdigit():
        col = "PIN_NUM" if len(q[0]) == 10 else "REID"
        return parcels[parcels[col] == float(q[0])]
    if not q or not q[0].isdigit():
        return parcels.iloc[0:0]
    same_num = parcels[parcels.STREET_NUM == int(q[0])]
    keep = []
    for r in same_num.itertuples():
        rest = _street_rest(q[1:], r.STREET_PREFIX, r.STREET_NAME, r.STREET_TYPE, r.STREET_SUFFIX)
        if rest is not None and _place_only(rest, r.CITY, r.ZIP):
            keep.append(r.Index)
    # every leftover word must be explained (town/ZIP), so "838 Athens Path" never matches 838 Athens Dr
    return same_num.loc[keep]


def near_misses(parcels: pd.DataFrame, query: str, limit: int = 5) -> pd.DataFrame:
    """Same house number, street name appears in the query: shown as 'did you mean' when nothing matched exactly."""
    q = _norm(query)
    if not q or not q[0].isdigit():
        return parcels.iloc[0:0]
    text = " " + " ".join(q[1:]) + " "
    same = parcels[parcels.STREET_NUM == int(q[0])]
    return same[[f" {' '.join(_norm(n))} " in text for n in same.STREET_NAME]].head(limit)
