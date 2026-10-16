#!/usr/bin/env python3
"""Regenerate the chart pages in data/ from data/pack.json (needs matplotlib; the demo itself does not).

Each page is one chart, the way the competitive-intelligence team exports it from its slides: a rival's head-to-head
win rate against Lumen Fieldworks, January to August of one year, one line, every point labelled with its number,
the rival and the year in the chart title (vector PDF: the line is a path, every label is text on the page).

  <rival>-win-rate-<year>.pdf   the page the demo stores (under two file names, one per project)
  <rival>-win-rate-<year>.png   a picture of the page for the web page (never uploaded)

Lumen Fieldworks, its rivals and their numbers are fictional.

    pip install matplotlib && python3 data/make_pages.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
WHEN = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


def main() -> None:
    d = json.loads((HERE / "pack.json").read_text(encoding="utf-8"))
    m = d["months"]
    for p in d["pages"]:
        title = f"{p['rival']} win rate in head-to-head deals, {p['year']} (%)"
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.plot(m, p["values"], marker="o", color="#2f6db5")
        for k, v in enumerate(p["values"]):
            ax.annotate(f"{v:.1f}", (m[k], v), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=8)
        ax.set_yticks(d["ticks"])
        ax.set_ylim(d["ticks"][0], d["ticks"][-1])
        ax.set_ylabel(d["unit"])
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        path = HERE / p["file"]
        fig.savefig(path.with_suffix(".pdf"), metadata={"Author": "Lumen Fieldworks competitive intelligence", "Creator": None,
                                                         "Producer": None, "CreationDate": WHEN})
        fig.savefig(path.with_suffix(".png"), dpi=110, metadata={"Software": None})
        plt.close(fig)
        print("wrote", p["file"] + ".{pdf,png}")


if __name__ == "__main__":
    main()
