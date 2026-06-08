"""
Reads price_log.csv and prints a full-day summary per item:
highest price, lowest price, and the time each occurred.
Run: python analyze.py [optional: price_log.csv]
"""

import csv
import sys
from collections import defaultdict

LOG_FILE = sys.argv[1] if len(sys.argv) > 1 else "price_log.csv"

stats = defaultdict(lambda: {"min": None, "max": None, "rows": 0})

try:
    with open(LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            item = row["item"].lower()
            try:
                price = float(row["unit_price"])
            except ValueError:
                continue

            s = stats[item]
            s["rows"] += 1

            if s["min"] is None or price < s["min"]["price"]:
                s["min"] = {"price": price, "time": row["timestamp"], "seller": row["seller"]}
            if s["max"] is None or price > s["max"]["price"]:
                s["max"] = {"price": price, "time": row["timestamp"], "seller": row["seller"]}

except FileNotFoundError:
    print(f"Log file '{LOG_FILE}' not found. Run bot.py first.")
    sys.exit(1)

if not stats:
    print("No data found in log.")
    sys.exit(0)

print(f"\n{'='*72}")
print(f"  DONUT SMP PRICE ANALYSIS  —  {LOG_FILE}")
print(f"{'='*72}")

for item, s in sorted(stats.items()):
    print(f"\n  {item.upper()}  ({s['rows']} data points)")
    if s["min"]:
        print(f"    🟢 Lowest : {s['min']['price']:>12,.2f} coins   @ {s['min']['time']}   seller: {s['min']['seller']}")
    if s["max"]:
        print(f"    🔴 Highest: {s['max']['price']:>12,.2f} coins   @ {s['max']['time']}   seller: {s['max']['seller']}")

print(f"\n{'='*72}\n")
