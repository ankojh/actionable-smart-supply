"""
NorthStar Robotics — reference calculation engine + data-integrity test (Rev. 2)

Run from the data-pack root:   python tests/verify_scenario.py
Exit code 0 = every expected result in SPEC.md §3 reproduces from the CSVs + YAML.

The functions here are also a working baseline for the MVP's deterministic layer:
SQL/Python computes every number; Claude only interprets evidence and explains.
"""
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


# ---------- loading ----------
def load(name):
    with open(DATA / name, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k, v in r.items():
            if v in ("NULL", ""):
                r[k] = None
    return rows


def d(s):
    return date.fromisoformat(s) if s else None


POL = yaml.safe_load(open(ROOT / "context" / "company_policies.yaml"))
EVAL = d(POL["evaluation_date"])
PP = POL["production_policy"]
H_START, H_END = d(PP["horizon_start"]), d(PP["horizon_end"])
CAP = PP["daily_capacity_units"]
LEAD = PP["material_to_ship_business_days"]

bom = load("bom.csv")
inv = {r["part_number"]: r for r in load("inventory.csv")}
pos = load("purchase_orders.csv")
plans = load("production_plan.csv")
orders = load("customer_orders.csv")
recovery = load("recovery_inventory.csv")

PART = "HDR-200"
QTY_PER = int(next(r for r in bom if r["part_number"] == PART)["qty_per_unit"])


# ---------- calendar ----------
def is_bday(x):
    return x.weekday() < 5


def add_bdays(x, n):
    while n > 0:
        x += timedelta(days=1)
        if is_bday(x):
            n -= 1
    return x


def bdays_between(a, b):
    """business days after a, up to and including b"""
    n, x = 0, a
    while x < b:
        x += timedelta(days=1)
        if is_bday(x):
            n += 1
    return n


def next_bday_on_or_after(x):
    while not is_bday(x):
        x += timedelta(days=1)
    return x


# ---------- demand in priority order ----------
def priority_orders():
    contracted = [o for o in orders if o["contracted"] == "Y"]
    forecast = [o for o in orders if o["contracted"] == "N"]
    # contracted -> earliest promised date -> tie-break ascending customer_order_id
    contracted.sort(key=lambda o: (d(o["promised_ship_date"]), o["customer_order_id"]))
    forecast.sort(key=lambda o: o["customer_order_id"])
    return contracted + forecast


# ---------- scheduler ----------
def simulate(supply_lots, end=None):
    """
    supply_lots: list of (usable_date, drives).
    Day by day over business days: start robots in priority order, limited by
    shared capacity (robot starts/day) and drives on hand. Each order is filled
    completely before the next (sequential_whole_order); the last one reached may be partial.
    Returns per-order list of (start_date, units), schedule rows, leftover drives.
    """
    end = end or add_bdays(H_END, 10)  # run past horizon to see late shipments
    remaining = {o["customer_order_id"]: int(o["units"]) for o in orders}
    starts = {o["customer_order_id"]: [] for o in orders}
    drives = 0
    sched = []
    x = H_START
    while x <= end:
        drives += sum(q for (u, q) in supply_lots if u == x)
        if is_bday(x):
            cap = CAP
            for o in priority_orders():
                oid = o["customer_order_id"]
                n = min(remaining[oid], cap, drives // QTY_PER)
                if n > 0:
                    remaining[oid] -= n
                    cap -= n
                    drives -= n * QTY_PER
                    starts[oid].append((x, n))
                    sched.append((x, oid, n, add_bdays(x, LEAD)))
        x += timedelta(days=1)
    return starts, sched, drives


def assess(starts):
    """Contracted at-risk units per order, judged on promised_ship_date."""
    at_risk = {}
    for o in orders:
        if o["contracted"] != "Y":
            continue
        promised = d(o["promised_ship_date"])
        on_time = sum(n for (s, n) in starts[o["customer_order_id"]] if add_bdays(s, LEAD) <= promised)
        short = int(o["units"]) - on_time
        if short:
            at_risk[o["customer_order_id"]] = short
    return at_risk


def exposure(at_risk):
    price = {o["customer_order_id"]: int(o["unit_revenue_usd"]) for o in orders}
    return sum(n * price[k] for k, n in at_risk.items())


def ship_dates(starts, oid):
    return [add_bdays(s, LEAD) for (s, n) in starts[oid]]


# ---------- checks ----------
fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got}" + ("" if ok else f"  (expected {want})"))
    if not ok:
        fails.append(label)


print("== Data integrity ==")
check("BOM rows", len(bom), 14)
check("BOM all NS-AMR-200", {r["model"] for r in bom}, {"NS-AMR-200"})
check("inventory rows", len(inv), 14)
check("customer_orders rows", len(orders), 4)
check("production_plan rows", len(plans), 2)
check("purchase_orders rows in 10-20", 10 <= len(pos) <= 20, True)

total_robots = sum(int(p["planned_build_qty"]) for p in plans)
check("planned robots", total_robots, 250)
check("contracted units", sum(int(o["units"]) for o in orders if o["contracted"] == "Y"), 120)
check("forecast units", sum(int(o["units"]) for o in orders if o["contracted"] == "N"), 130)
check("contract value", sum(int(o["units"]) * int(o["unit_revenue_usd"]) for o in orders if o["contracted"] == "Y"), 2_160_000)
check("contracted orders -> PLAN-001", {o["plan_id"] for o in orders if o["contracted"] == "Y"}, {"PLAN-001"})

# other components cover 250 robots and stay at/above safety stock
short_parts = []
for b in bom:
    p = b["part_number"]
    if p == PART:
        continue
    i = inv[p]
    avail = int(i["on_hand_qty"]) - int(i["allocated_qty"])
    need = int(b["qty_per_unit"]) * total_robots
    if avail - need < int(i["safety_stock_qty"]):
        short_parts.append(p)
check("non-HDR parts cover plan without breaching safety stock", short_parts, [])

hdr_avail = int(inv[PART]["on_hand_qty"]) - int(inv[PART]["allocated_qty"])
check("HDR-200 usable warehouse stock", hdr_avail, 120)

po = next(r for r in pos if r["po_number"] == "PO-2026-1042")
check("PO-1042 qty/price/ETA/status", (po["qty"], po["unit_price_usd"], po["latest_eta"], po["status"], po["open_qty"]),
      ("400", "412", "2026-10-20", "Open", "400"))
other_inbound_hdr = [r["po_number"] for r in pos if r["part_number"] == PART and r["po_number"] != "PO-2026-1042" and int(r["open_qty"]) > 0]
check("no other open HDR-200 PO", other_inbound_hdr, [])

past_due = [r["po_number"] for r in pos if int(r["open_qty"]) > 0 and d(r["latest_eta"]) < EVAL]
check("past-due ERP POs (only 1042)", past_due, ["PO-2026-1042"])
check("PO-1042 days past due", (EVAL - d(po["latest_eta"])).days, 6)

print("\n== Agent 1: baseline impact ==")
required = QTY_PER * total_robots
check("drives required", required, 500)
check("uncovered requirement", required - hdr_avail, 380)

PACIFIC_REVISED = date(2026, 11, 10)          # from DOC-SUP-001 (source precedence)
check("Pacific revised ETA after horizon", PACIFIC_REVISED > H_END, True)

base_starts, _, base_left = simulate([(H_START, hdr_avail)], end=H_END)
base_risk = assess(base_starts)
check("baseline at-risk contracted units", base_risk, {"SO-002": 40, "SO-003": 20})
check("baseline exposure", exposure(base_risk), 1_080_000)

print("\n== Option A: Apex emergency ==")
APEX_QTY, APEX_PRICE, PAC_PRICE = 400, 486, int(po["unit_price_usd"])
APEX_RECEIPT = date(2026, 10, 29)             # DOC-APEX-002
check("Apex standard lead time arrival (accept Oct 27 + 10 bd)", add_bdays(date(2026, 10, 27), 10), date(2026, 11, 10))
check("purchase cost", APEX_QTY * APEX_PRICE, 194_400)
check("unit-price premium", APEX_QTY * (APEX_PRICE - PAC_PRICE), 29_600)
check("premium pct (rounded)", round((APEX_PRICE - PAC_PRICE) / PAC_PRICE * 100, 1), 18.0)
check("within approval limit", APEX_QTY * APEX_PRICE <= POL["approval_policy"]["approval_limit_usd"], True)
check("Pacific PO value still due if unchanged", int(po["qty"]) * PAC_PRICE, 164_800)

a_starts, a_sched, a_left = simulate([(H_START, hdr_avail), (APEX_RECEIPT, APEX_QTY)], end=H_END)
a_risk = assess(a_starts)
check("A at-risk contracted units", a_risk, {})
check("A exposure", exposure(a_risk), 0)
check("A BayShip/NovaFab ship date", (ship_dates(a_starts, "SO-002"), ship_dates(a_starts, "SO-003")),
      ([date(2026, 11, 2)], [date(2026, 11, 2)]))
check("A forecast fully started in horizon", sum(n for _, n in a_starts["FC-001"]), 130)
check("A HDR-200 left at horizon end", a_left, 20)
print("      schedule:", [(s.isoformat(), o, n, sh.isoformat()) for s, o, n, sh in a_sched])

# slip tolerance: latest Apex receipt that still protects every contract
latest_ok = None
x = APEX_RECEIPT
while x <= H_END:
    if is_bday(x) and not assess(simulate([(H_START, hdr_avail), (x, APEX_QTY)], end=H_END)[0]):
        latest_ok = x
    x += timedelta(days=1)
check("A slip tolerance (latest Apex receipt)", latest_ok, date(2026, 11, 4))

print("\n== Option B: pilot line ==")
usable_rec = [r for r in recovery if r["approved_for_use"] == "Y" and r["condition_status"] == "Usable" and r["part_number"] == PART]
check("usable recovery lots", [r["recovery_id"] for r in usable_rec], ["REC-001"])
rec = usable_rec[0]
check("REC-001 cost stays null", rec["incremental_cost_usd"], None)
b_lots = [(H_START, hdr_avail), (d(rec["available_date"]), int(rec["recoverable_qty"]))]
b_starts, _, b_left = simulate(b_lots, end=H_END)
b_risk = assess(b_starts)
check("B robots buildable", (hdr_avail + int(rec["recoverable_qty"])) // QTY_PER, 100)
check("B at-risk (tie-break -> NovaFab)", b_risk, {"SO-003": 20})
check("B exposure", exposure(b_risk), 360_000)
check("B BayShip ship date", ship_dates(b_starts, "SO-002"), [date(2026, 10, 30)])

# NovaFab under B waits for Pacific Nov 10
b_full, _, _ = simulate(b_lots + [(PACIFIC_REVISED, int(po["open_qty"]))])
nova_ship = max(ship_dates(b_full, "SO-003"))
late_bd = bdays_between(d("2026-11-06"), nova_ship)
check("B NovaFab actual ship date", nova_ship, date(2026, 11, 12))
check("B NovaFab late business days", late_bd, 4)

print("\n== Decision policy: economic check ==")
premium = APEX_QTY * (APEX_PRICE - PAC_PRICE)
protected = exposure(b_risk) - exposure(a_risk)
check("break-even per order", premium, 29_600)
check("break-even per unit", premium // 20, 1_480)
check("break-even per late business day", premium // late_bd, 7_400)
check("break-even % of NovaFab order (rounded)", round(premium / 360_000 * 100, 1), 8.2)
check("review flag (ratio > 0.25)", premium / protected > POL["decision_policy"]["economic_check"]["review_flag_ratio"], False)

print("\n== Safety-stock warnings (warn only) ==")
ss = int(inv[PART]["safety_stock_qty"])
check("HDR-200 below safety stock: baseline/A/B", (base_left < ss, a_left < ss, b_left < ss), (True, True, True))

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} CHECK(S) FAILED: {fails}"))
sys.exit(1 if fails else 0)
