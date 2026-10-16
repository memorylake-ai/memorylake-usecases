#!/usr/bin/env python3
"""Regenerate the board-pack chart pages in data/ from data/board.json (needs matplotlib; the demo itself does not).

Each KPI is one page, exported three ways (vector PDF: the lines are paths, every label is text on the page):

  board-<x>.pdf                 the page as the finance team exports it: actual and plan as two lines, every point
                                labelled with its number (actual below its point, plan above)
  board-<x>-month-labels.pdf    the same page with the month written into every label ("Mar 1.97")
  board-<x>-with-table.pdf      the same chart with the numbers printed as a table under it, on the same page; each
                                row is labelled with its line, metric, year and unit ("Actual operating cash flow 2026 ($M)")
  board-<x>*.png                a picture of each page for the web page (never uploaded; the demo stores the PDFs)

Corran Ridge Systems and its numbers are fictional.

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
WHEN = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def fmt(f: dict, v: float) -> str:
    return f"{v:.{f['decimals']}f}"


def draw(ax, d: dict, f: dict, labels: str) -> None:
    m = d["months"]
    ax.plot(m, f["Actual"], marker="o", color="#2f6db5", label="Actual")
    ax.plot(m, f["Plan"], marker="s", color="#e07b28", label="Plan")
    for s, dy in (("Actual", -14), ("Plan", 7)):
        for k, v in enumerate(f[s]):
            text = fmt(f, v) if labels == "value" else f"{m[k]} {fmt(f, v)}"
            ax.annotate(text, (m[k], v), textcoords="offset points", xytext=(0, dy), ha="center",
                        fontsize=8 if labels == "value" else 7)
    ax.set_yticks(f["ticks"])
    ax.set_ylim(f["ticks"][0], f["ticks"][-1])
    ax.set_ylabel(f["unit"])
    ax.set_title(f["title"])
    ax.legend(loc="upper left")
    ax.grid(axis="y", alpha=0.25)


def save(fig, path: Path, title: str) -> None:
    fig.savefig(path.with_suffix(".pdf"), metadata={"Title": title, "Author": "Corran Ridge Systems FP&A",
                                                     "Creator": None, "Producer": None, "CreationDate": WHEN})
    fig.savefig(path.with_suffix(".png"), dpi=110, metadata={"Software": None})
    plt.close(fig)


def main() -> None:
    d = json.loads((HERE / "board.json").read_text(encoding="utf-8"))
    for f in d["figures"]:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        draw(ax, d, f, "value")
        fig.tight_layout()
        save(fig, HERE / f["file"], f["title"])

        fig, ax = plt.subplots(figsize=(8, 4.5))
        draw(ax, d, f, "month")
        fig.tight_layout()
        save(fig, HERE / (f["file"] + "-month-labels"), f["title"])

        # The table's rows say what they are — line, metric, year, unit — so a row read on its own still answers.
        fig = plt.figure(figsize=(9, 6.4))
        ax = fig.add_axes([0.08, 0.36, 0.89, 0.57])
        draw(ax, d, f, "value")
        tx = fig.add_axes([0.02, 0.03, 0.96, 0.22])
        tx.axis("off")
        rows = [[f"{s} {f['metric']} {d['year']} ({f['unit']})"] + [fmt(f, v) for v in f[s]] for s in d["series"]]
        t = tx.table(cellText=rows, colLabels=[""] + d["months"], colWidths=[0.36] + [0.08] * 8, loc="center",
                     cellLoc="center")
        t.auto_set_font_size(False)
        t.set_fontsize(8.5)
        save(fig, HERE / (f["file"] + "-with-table"), f["title"])
        print("wrote", f["file"] + "{,-month-labels,-with-table}.{pdf,png}")


if __name__ == "__main__":
    main()
