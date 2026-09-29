"""Generate data/sample_listings.csv: 240 realistic, messy seller listings
(200 distinct + 40 duplicates).

Deterministic (fixed random seed), so the file is reproducible:
    python scripts/generate_sample_data.py

Messiness on purpose, like real marketplace data:
  - random UPPER/lower case and extra spaces
  - units written many ways: 500G, 500 gm, 500g, 1KG, 1 ltr
  - "pck of 2" / "pack of 3" / "combo" in title or description
  - some rows with no description, some with commas and quotes (CSV quoting)
Duplicates on purpose (for the cache): 40 rows repeat an earlier listing's
content under a NEW SKU, with different case and spacing, so only the
content key shows they are the same.
"""

import csv
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "sample_listings.csv"
rng = random.Random(1425)

# (brand, product, sizes, description ideas)
CATALOGUE = [
    # Groceries
    ("Amul", "butter", ["100G", "500G", "500 gm"], ["pasteurised table butter", "salted, pck of 2"]),
    ("Amul", "cheese slices", ["200g", "750 G"], ["processed cheese, 10 slices", ""]),
    ("Aashirvaad", "whole wheat atta", ["5KG", "10 kg"], ["100% whole wheat, 0% maida", "chakki fresh"]),
    ("Fortune", "sunflower oil", ["1 ltr", "5L"], ["refined, light and healthy", "pouch"]),
    ("Tata", "salt", ["1kg"], ["vacuum evaporated iodised salt", ""]),
    ("Maggi", "2-minute noodles masala", ["70g", "pack of 12"], ["instant noodles", "family pack, pck of 4"]),
    ("Britannia", "good day cashew biscuits", ["200 g", "600G"], ["cookies with cashew", ""]),
    ("Parle", "g glucose biscuits", ["800g"], ["value pack", "pack of 3"]),
    ("Haldiram", "aloo bhujia", ["400 gm", "1 kg"], ["namkeen snack", "crispy, spicy"]),
    ("MDH", "garam masala", ["100g"], ["blended spices", ""]),
    ("Everest", "turmeric powder", ["200 G"], ["haldi, pure", ""]),
    ("India Gate", "basmati rice", ["1KG", "5 kg"], ["long grain, aged", "classic"]),
    ("Tata Sampann", "toor dal", ["1 kg"], ["unpolished arhar dal", ""]),
    # Beverages
    ("Tata", "tea gold", ["250g", "1KG"], ["premium assam tea", "leaf tea"]),
    ("Red Label", "tea", ["500 G"], ["natural care, 5 herbs", ""]),
    ("Bru", "instant coffee", ["100g", "200 gm"], ["coffee and chicory", "jar"]),
    ("Nescafe", "classic coffee", ["50g", "100 G"], ["100% pure instant coffee", ""]),
    ("Real", "fruit juice mixed fruit", ["1 ltr"], ["no added preservatives", "pack of 2"]),
    ("Coca Cola", "soft drink", ["750 ml", "2.25 L"], ["cold drink bottle", "pack of 6"]),
    ("Red Bull", "energy drink", ["250 ml"], ["can", "pack of 4"]),
    ("Bisleri", "mineral water", ["1 ltr", "5L"], ["packaged drinking water", ""]),
    # Personal Care
    ("Colgate", "strong teeth toothpaste", ["200g", "100 G"], ["cavity protection", "pack of 2"]),
    ("Dove", "cream beauty bathing bar soap", ["125g"], ["1/4 moisturising cream", "pack of 3"]),
    ("Head & Shoulders", "anti dandruff shampoo", ["340 ml", "650ml"], ["smooth and silky", ""]),
    ("Nivea", "soft light moisturising cream", ["100 ml", "200ML"], ["with vitamin E and jojoba oil", ""]),
    ("Himalaya", "neem face wash", ["150 ml"], ["purifying, for oily skin", ""]),
    ("Gillette", "mach3 razor", ["1 pc"], ["3 blades", "with 2 cartridges"]),
    ("Dettol", "original liquid hand wash", ["200ml", "1.5 L refill"], ["germ protection", ""]),
    # Household
    ("Surf Excel", "matic liquid detergent", ["1 ltr", "2L"], ["top load", "front load"]),
    ("Vim", "dishwash gel lemon", ["750 ml"], ["removes tough grease", ""]),
    ("Harpic", "toilet cleaner", ["500ml", "1 L"], ["original, kills 99.9% germs", "pack of 2"]),
    ("Lizol", "floor cleaner citrus", ["975 ml", "2 ltr"], ["disinfectant surface cleaner", ""]),
    ("Odonil", "air freshener blocks", ["50g"], ["jasmine, pack of 4", ""]),
    ("Presto", "garbage bags medium", ["30 bags"], ["black, 19 x 21 inches", ""]),
    # Electronics
    ("boAt", "airdopes 141 bluetooth earbuds", [""], ["42H playtime, IPX4", "black"]),
    ("boAt", "rockerz 450 wireless headphones", [""], ["on ear, 15H battery", ""]),
    ("Noise", "colorfit pro smart watch", [""], ["1.85\" display, bluetooth calling", ""]),
    ("Samsung", "25W usb c fast charger", [""], ["travel adapter, cable not included", ""]),
    ("Mi", "10000mAh power bank", [""], ["dual output, 18W fast charging", ""]),
    ("Logitech", "m331 silent wireless mouse", [""], ["90% less click noise", ""]),
    ("Duracell", "AA alkaline battery", ["pack of 8"], ["long lasting", ""]),
    # Fashion
    ("Levis", "mens slim fit jeans", ["32W"], ["dark blue, stretch denim", ""]),
    ("Puma", "men running shoes", ["UK 9"], ["lightweight, grey", ""]),
    ("Allen Solly", "men polo t-shirt", ["M", "L"], ["100% cotton, navy", ""]),
    ("Biba", "women cotton kurta", ["S", "XL"], ["printed, straight fit", ""]),
    ("Jockey", "men cotton socks", ["pack of 3"], ["ankle length", ""]),
    # Home & Kitchen
    ("Prestige", "pressure cooker aluminium", ["3 ltr", "5 L"], ["outer lid, induction base", ""]),
    ("Milton", "thermosteel water bottle", ["1000 ml", "750ml"], ["hot and cold 24 hrs", ""]),
    ("Pigeon", "non stick tawa", ["28 cm"], ["induction compatible", ""]),
    ("Cello", "plastic storage container set", ["pack of 6"], ["airtight, BPA free", ""]),
    ("Philips", "LED bulb 9W", ["pack of 4"], ["cool day light, B22 base", ""]),
    ("Bombay Dyeing", "cotton double bedsheet", ["with 2 pillow covers"], ["floral, 144 TC", ""]),
    # No known brand / odd listings -> brand null, often category Other
    ("", "handmade clay diya", ["set of 12"], ["for diwali decoration", ""]),
    ("", "kids puzzle toy wooden", [""], ["educational, 3+ years", ""]),
    ("", "unbranded mobile stand", [""], ["adjustable, foldable", ""]),
    ("", "gift wrapping paper", ["10 sheets"], ["assorted designs", ""]),
]


def messy_case(text: str) -> str:
    style = rng.random()
    if style < 0.25:
        return text.upper()
    if style < 0.45:
        return text.lower()
    if style < 0.65:
        return text.title()
    return " ".join(w.upper() if rng.random() < 0.2 else w for w in text.split())


def messy_spaces(text: str) -> str:
    words = text.split()
    out = " ".join(w + (" " * rng.choice([0, 0, 0, 1, 2])) for w in words)
    return (" " * rng.choice([0, 0, 1, 2])) + out + (" " * rng.choice([0, 0, 1]))


UNIQUE_LISTINGS = 200
DUPLICATES = 40


def main() -> None:
    # Every distinct (brand, product, size, description, word order) combination,
    # shuffled; keep the first 200 whose content is really different.
    combos = [
        (brand, product, size, desc, brand_first)
        for brand, product, sizes, descriptions in CATALOGUE
        for size in sizes
        for desc in descriptions
        for brand_first in (True, False)
        if brand or brand_first
    ]
    rng.shuffle(combos)
    unique, seen = [], set()
    for brand, product, size, desc, brand_first in combos:
        parts = [brand, product, size] if brand_first else [product, brand, size]
        title = messy_spaces(messy_case(" ".join(p for p in parts if p)))
        if desc and rng.random() < 0.15:
            desc = f'{desc}, "best seller"'   # quotes + comma: needs CSV quoting
        key = " ".join(f"{title} {desc}".lower().split())
        if key not in seen:
            seen.add(key)
            unique.append((title, desc))
        if len(unique) == UNIQUE_LISTINGS:
            break

    # Duplicates: an earlier listing's content under a NEW SKU, re-messed
    # case and spacing, so only the content key shows they are the same.
    listings = list(unique)
    for _ in range(DUPLICATES):
        title, desc = rng.choice(unique)
        title = messy_spaces(rng.choice([title.upper(), title.lower(), title]))
        desc = rng.choice([desc.upper(), desc.lower(), desc])
        listings.insert(rng.randrange(len(listings) + 1), (title, desc))

    rows = []
    sku_number = 1000
    for title, desc in listings:
        sku_number += rng.randint(1, 7)
        rows.append((f"SKU-{sku_number}", title, desc))

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "raw_title", "raw_description"])
        writer.writerows(rows)
    print(f"Wrote {len(rows)} listings to {OUT}")


if __name__ == "__main__":
    main()
