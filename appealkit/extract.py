"""One-time extract of the columns we use from Wake's 174 MB full parcel file (RealEstDataMMDDYYYY.xlsx) into a CSV.

python -m appealkit.extract data/raw/RealEstData11132026.xlsx data/raw/parcels_20261113.csv   (~70 s)
Keeps every building card; subject lookup and the quality join pick card 01 / sum areas themselves.
"""

from __future__ import annotations

import csv
import sys

import openpyxl

COLS = ["REAL_ESTATE_ID", "CARD_NUMBER", "Street_Number", "Street_Prefix", "Street_Name", "Street_Type", "Street_Suffix",
        "PHYSICAL_CITY", "PHYSICAL_ZIP_CODE", "Parcel_Identification", "VCS", "Township", "Planning_Jurisdiction",
        "Year_Built", "Effective_year", "HEATED_AREA", "Assessed_Building_Value", "Assessed_Land_Value", "Deeded_Acreage",
        "TYPE_AND_USE", "GRADE", "Accrued_Assessed_Condition_Pct", "BATH", "Land_classification"]


def main(src: str, dst: str) -> None:
    rows = openpyxl.load_workbook(src, read_only=True)["REALINFO"].iter_rows(values_only=True)
    header = next(rows)
    idx = [header.index(c) for c in COLS]  # fails loudly if Wake renames a column
    with open(dst, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        n = 0
        for r in rows:
            w.writerow([r[i] for i in idx])
            n += 1
    print(f"{n} rows -> {dst}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
