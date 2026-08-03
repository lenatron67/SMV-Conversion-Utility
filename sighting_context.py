"""
Session 5 — where do the in-sync proprietary-code sightings occur within
the block/MB structure?

Key discriminator:
 - If sightings cluster at event 0 (first TCOEF event right after INTRADC),
   the divergence is likely STRUCTURAL (wrong coded-block flags / an extra
   field after INTRADC / CBPY extension) rather than a TCOEF code.
 - If they spread across event indices/coef positions like ordinary TCOEF
   statistics, they are genuine TCOEF extension codes.
Also tabulates the same for run_overflow / bad_intradc / forbidden_esc_level
failures, and the INTRADC value of the failing block.
"""
import json
from collections import Counter

results = json.load(open("full_scan_results.json"))

for kind in ("invalid_tcoef", "run_overflow", "forbidden_esc_level"):
    recs = [r for r in results if r["kind"] == kind]
    print(f"\n=== {kind} (n={len(recs)}) ===")
    print("  event-index histogram:",
          dict(sorted(Counter(r["ev"] for r in recs).most_common(10))))
    print("  block histogram      :",
          dict(sorted(Counter(r["block"] for r in recs).items())))
    print("  coef_num histogram   :",
          dict(sorted(Counter(r["coef"] for r in recs).most_common(12))))
    print("  MB histogram         :",
          dict(sorted(Counter(r["mb"] for r in recs).most_common(8))))

# for invalid_tcoef: cross event-index with window value
recs = [r for r in results if r["kind"] == "invalid_tcoef"]
print("\ninvalid_tcoef: window value vs event index")
print(f"{'wv':>3} {'n':>5} {'ev0':>5} {'ev1':>5} {'ev2':>5} {'ev3+':>5} "
      f"{'med_coef':>8}")
for wv in range(16):
    sub = [r for r in recs if int(r["win32"][:13], 2) == wv]
    if not sub:
        continue
    evs = Counter(min(r["ev"], 3) for r in sub)
    coefs = sorted(r["coef"] for r in sub)
    print(f"{wv:>3} {len(sub):>5} {evs.get(0, 0):>5} {evs.get(1, 0):>5} "
          f"{evs.get(2, 0):>5} {evs.get(3, 0):>5} "
          f"{coefs[len(coefs) // 2]:>8}")

# INTRADC of failing block for invalid_tcoef at ev 0 vs later
ev0 = [r["intradc"] for r in recs if r["ev"] == 0]
evN = [r["intradc"] for r in recs if r["ev"] > 0]
print(f"\nINTRADC at ev0 sightings: n={len(ev0)} "
      f"min={min(ev0) if ev0 else '-'} max={max(ev0) if ev0 else '-'}")
print("  most common:", Counter(ev0).most_common(10))
print(f"INTRADC at ev>0 sightings: n={len(evN)}")
print("  most common:", Counter(evN).most_common(10))
