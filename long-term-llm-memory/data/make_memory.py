#!/usr/bin/env python3
"""Regenerates memory.json: nine months of an assistant's memory about one user (fictional).

The facts are ordered by when the assistant learned them; the demo stores the first 20 (week 1),
then the first 100 (month 3), then all 400 (month 9).  The seed is fixed, so the file is reproducible:

    python3 data/make_memory.py
"""
import json
import random
from pathlib import Path

random.seed(7)

FIRST = ("Ada Ben Cleo Dmitri Esme Farid Greta Hugo Ines Jonah Kaia Leon Mira Nikhil Odile Pavel Quinn Rosa Sami Tove "
         "Ugo Vera Wes Ximena Yusuf Zora Arlo Bea Cyrus Della Emil Fern Gideon Hana Ivo Juno Kofi Lena Marek Nell").split()
LAST = ("Achterberg Baptiste Castellan Dovey Ekwueme Ferreira Galloway Haas Iyer Jansen Kowal Lund Moreau Nakata Okafor "
        "Pereira Quist Rinaldi Sorensen Takeda").split()
REL = ["college friend", "former colleague", "client", "neighbour", "cousin", "climbing partner", "accountant", "dentist",
       "book-club friend", "structural engineer she works with", "landlord", "yoga teacher", "old flatmate",
       "project manager at a client", "godchild's parent"]
MONTHS = "January February March April May June July August September October November December".split()

contacts, names = [], set()
while len(contacts) < 60:
    n = f"{random.choice(FIRST)} {random.choice(LAST)}"
    if n in names:
        continue
    names.add(n)
    m = MONTHS[len(contacts) % 12] if len(contacts) < 48 else random.choice(MONTHS)
    contacts.append(dict(name=n, relation=random.choice(REL), month=m, day=random.randint(1, 28)))

POINT = [
    "The door code for Maya's studio on Kestrel Lane is 4471, then the pound key.",
    "Maya takes 10 mg of cetirizine for hay fever, only from April to June.",
    "On flights longer than four hours Maya wants an aisle seat, never the exit row.",
    "Maya's bike was serviced at Spoke & Chain on 2 September; the next service is due after 1,500 km.",
    "Maya's passport expires on 14 March 2028.",
    "Maya's accountant needs receipts by the 5th of each month, as PDFs, named by date.",
]
TOPICS = {
    "project": ["The {x} project for {c} is due {m} {d}; Maya is on the {p} drawings.",
                "Client note: {c} prefers updates by email on Fridays for the {x} project."],
    "food": ["Maya liked the {f} at {r} and wants to go back.",
             "Maya avoids {a} at restaurants since a reaction last {m}."],
    "home": ["Maya's {h} was fixed by {c} in {m}.",
             "The {h} filter at Maya's flat needs replacing every {k} months."],
    "reading": ["Maya finished '{b}' in {m} and rated it {s} out of 5.",
                "Maya wants to read '{b}' next, recommended by {c}."],
    "travel": ["Maya stayed at {t} in {city} in {m} and would book it again.",
               "Maya's trip to {city} is planned for {m}; she wants a quiet room."],
    "work": ["Maya bills {c} at {k}0 euros an hour for {p} work.",
             "Maya uses {tool} for {p} drawings since {m}."],
}
X = ["Harbour Library", "Annex", "Mill Lofts", "Orchard School", "Quay Pavilion", "Northgate Clinic", "Elm Court",
     "Lantern Hall", "Saltworks Studios", "Ferry Terminal", "Granary Flats", "Willow Creche"]
F = ["mushroom risotto", "lamb shoulder", "miso aubergine", "fish stew", "pistachio tart", "dal", "ramen", "gnocchi"]
R = ["Hearth", "Little Fig", "Tidewater", "Osteria Nove", "Bramble", "The Larder", "Kiln", "Seabright"]
A = ["sesame", "shellfish", "raw onion", "peanut oil"]
H = ["boiler", "dishwasher", "balcony door", "extractor fan", "water heater", "window latch"]
B = ["The Sea Wall", "Quiet Rooms", "A Field Guide to Bridges", "Salt and Stone", "The Long Draft", "Paper Cities",
     "North Light", "The Timber Year"]
T = ["Hotel Lark", "Casa Ombra", "The Pier House", "Villa Sora", "Hostel Kanto"]
CITY = ["Porto", "Kyoto", "Ghent", "Trieste", "Bergen", "Seville", "Ljubljana"]
P = ["structural", "facade", "interior", "landscape", "lighting"]
TOOL = ["Rhino", "Revit", "Vectorworks", "SketchUp", "Blender"]

facts = list(POINT) + [f"Contact: {c['name']} is Maya's {c['relation']}; birthday {c['month']} {c['day']}." for c in contacts]
seen = set(facts)
while len(facts) < 400:
    k = random.choice(list(TOPICS))
    s = random.choice(TOPICS[k]).format(
        x=random.choice(X), c=random.choice(contacts)["name"], m=random.choice(MONTHS), d=random.randint(1, 28),
        p=random.choice(P), f=random.choice(F), r=random.choice(R), a=random.choice(A), h=random.choice(H),
        k=random.randint(2, 12), b=random.choice(B), s=random.randint(2, 5), t=random.choice(T),
        city=random.choice(CITY), tool=random.choice(TOOL))
    if s not in seen:
        seen.add(s)
        facts.append(s)

out = dict(
    user="Maya Lindqvist, a freelance architect (fictional)",
    checkpoints=[dict(key="week1", label="Week 1", facts=20),
                 dict(key="month3", label="Month 3", facts=100),
                 dict(key="month9", label="Month 9", facts=400)],
    contacts=[c["name"] for c in contacts],
    facts=facts,
)
Path(__file__).with_name("memory.json").write_text(json.dumps(out, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
print(f"{len(facts)} facts, {len(contacts)} contacts")
