#!/usr/bin/env python3
"""Regenerates data/accounts.json: the renewal book of a fictional SaaS customer-success team.

160 accounts, each with one renewal record; 40 of them renew in the review window
(2026-10-19 to 2026-12-31), split 11 / 9 / 12 / 8 across the four account managers, so
any one manager's list is short and the whole team's list is twice what MemoryRouter
attaches to one request. 60 call notes add the everyday noise of a real account book.
Every name is invented. Run it once; the demo reads the JSON, not this script.
"""
import datetime as dt
import json
import random
from pathlib import Path

random.seed(20261019)
OUT = Path(__file__).resolve().parent / "accounts.json"

FIRST = ("Alder Birch Cedar Dune Ember Fjord Garnet Harbor Iris Juniper Kestrel Lumen Marlow Nimbus Onyx "
         "Pike Quarry Rowan Sable Tamar Umber Vale Willow Yarrow Zephyr Basalt Coral Delta Flint Glade").split()
LAST = ("Freight Labs Health Foods Logistics Studio Bank Systems Media Robotics Analytics Retail Energy "
        "Clinic Works Dental Brewing Insurance Legal Mobility").split()
MANAGERS = ["Priya Nair", "Tomas Reyes", "Hana Okafor", "Lev Brandt"]
IN_WINDOW = {"Priya Nair": 11, "Tomas Reyes": 9, "Hana Okafor": 12, "Lev Brandt": 8}  # 40 in all
WINDOW = (dt.date(2026, 10, 19), dt.date(2026, 12, 31))
TOPICS = ["asked for copies of last year's invoices", "wants SSO set up before the renewal call",
          "hit the API rate limit twice this month", "asked for a training session for new hires",
          "is adding 15 seats in the spring", "asked how to export all their data",
          "pushed back on the 7 percent price increase", "reported the mobile app logging users out",
          "named a new admin after their old one left", "wants a monthly usage report",
          "asked about a multi-year discount", "is evaluating a cheaper competitor"]

names: list[str] = []
while len(names) < 160:
    n = f"{random.choice(FIRST)} {random.choice(LAST)}"
    if n not in names:
        names.append(n)

owners = [m for m in MANAGERS for _ in range(40)]
random.shuffle(owners)
accounts = []
left = dict(IN_WINDOW)
for name, am in zip(names, owners):
    if left[am] > 0:
        left[am] -= 1
        d = WINDOW[0] + dt.timedelta(days=random.randint(0, (WINDOW[1] - WINDOW[0]).days))
    else:
        d = dt.date(2027, 1, 4) + dt.timedelta(days=random.randint(0, 330))
    accounts.append(dict(account=name, manager=am, renews_on=d.isoformat(), arr=random.randint(12, 180) * 1000))
assert not any(left.values()), left
random.shuffle(accounts)  # the in-window accounts were drawn first; store them in no particular order

notes = []
for i in range(60):
    a = random.choice(accounts)
    day = dt.date(2026, 7, 1) + dt.timedelta(days=random.randint(0, 105))
    notes.append(f"Call note ({day.isoformat()}, {a['manager']}): {a['account']} {random.choice(TOPICS)}.")

OUT.write_text(json.dumps(dict(
    company="Tidewater Software (fictional)",
    today="2026-10-19",
    window=[WINDOW[0].isoformat(), WINDOW[1].isoformat()],
    managers=MANAGERS,
    accounts=accounts,
    notes=notes,
), indent=1) + "\n", encoding="utf-8")
print(f"wrote {OUT.name}: {len(accounts)} accounts, "
      f"{sum(WINDOW[0].isoformat() <= a['renews_on'] <= WINDOW[1].isoformat() for a in accounts)} in the window, "
      f"{len(notes)} call notes")
