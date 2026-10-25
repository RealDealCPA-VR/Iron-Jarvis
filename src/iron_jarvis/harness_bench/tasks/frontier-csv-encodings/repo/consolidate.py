"""Consolidate the branch offices' order exports (README.md says how).

    python consolidate.py                # exports/ -> orders.csv + summary.json here
    python consolidate.py EXPORTS OUT
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def consolidate(exports_dir=os.path.join(HERE, "exports"), out_dir=HERE):
    """Read every export in ``exports_dir``; write orders.csv and summary.json
    into ``out_dir``; return the summary."""
    orders = {}
    skipped = 0
    duplicates = 0
    for name in sorted(os.listdir(exports_dir)):
        if not name.endswith(".csv"):
            continue
        office = name.split("_")[0]
        with open(os.path.join(exports_dir, name), encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        if not lines:
            continue
        header = lines[0].split(",")
        for line in lines[1:]:
            if not line.strip():
                continue
            row = dict(zip(header, line.split(",")))
            order_id = row.get("order_id", "").strip()
            try:
                amount = float(row.get("amount", ""))
            except ValueError:
                skipped += 1
                continue
            if not order_id:
                skipped += 1
                continue
            if order_id in orders:
                duplicates += 1
                continue
            orders[order_id] = (office, row.get("customer", "").strip(), amount)

    os.makedirs(out_dir, exist_ok=True)
    totals = {}
    with open(os.path.join(out_dir, "orders.csv"), "w", encoding="utf-8") as fh:
        fh.write("order_id,office,customer,amount\n")
        for order_id in sorted(orders):
            office, customer, amount = orders[order_id]
            fh.write(f"{order_id},{office},{customer},{amount:.2f}\n")
            count, total = totals.get(office, (0, 0.0))
            totals[office] = (count + 1, total + amount)
    summary = {
        "offices": {office: {"orders": count, "total": f"{total:.2f}"} for office, (count, total) in totals.items()},
        "skipped_rows": skipped,
        "duplicates": duplicates,
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    return summary


def main(argv):
    if len(argv) not in (1, 3):
        print("usage: python consolidate.py [EXPORTS OUT]", file=sys.stderr)
        return 2
    if len(argv) == 3:
        summary = consolidate(argv[1], argv[2])
    else:
        summary = consolidate()
    orders = sum(office["orders"] for office in summary["offices"].values())
    print(f"{orders} orders, {summary['skipped_rows']} bad rows skipped, {summary['duplicates']} duplicates")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
