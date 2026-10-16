#!/usr/bin/env python3
"""Writes the comps workbook from data/comps.json the way the senior agent keeps it in Excel:

  sheet "Comps"    closed sales; the reason a sale is not a usable comp is a cell note (comment) on its price,
                   and a sale that fell through is a hidden row
  sheet "Pending"  contracts that have not closed yet — a hidden sheet

Standard library only (the writer lives in demo.py, which the demo also uses for the two fixed copies). Zip entries
carry a fixed timestamp, so the bytes are reproducible.

    python3 data/make_workbooks.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from demo import saved_workbook  # noqa: E402


def main() -> None:
    d = json.loads((HERE / "comps.json").read_text(encoding="utf-8"))
    path = HERE / (d["file"] + ".xlsx")
    path.write_bytes(saved_workbook(d))
    notes = sum(1 for c in d["comps"] if c.get("note"))
    hidden = sum(1 for c in d["comps"] if c.get("hidden"))
    print(f"wrote data/{path.name}: {len(d['comps'])} comps ({notes} with a cell note, {hidden} hidden row), "
          f"hidden sheet “{d['pending_sheet']}” with {len(d['pending'])} contracts")


if __name__ == "__main__":
    main()
