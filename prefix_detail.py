"""Per-segment (L, LAST) detail for the recurring culprit prefixes found by
analyze_localize_hits.py — singleton L-sets are the most constrained
evidence for the true code length."""
import re
import sys
from collections import defaultdict

PATH = "desync_localize_all.txt"
PREFIXES = ["0000011000", "0000001100", "0001100000", "0000010000"]

seg_re = re.compile(r"^(SOLVED|UNSOLVED)\s+(\S+) seg(\d+)")
hit_re = re.compile(
    r"MB\s+(\d+) (\w+)\s+bit\s+(\d+)\s+parsed-as\s+(\S+) \((\w+)\)\s+"
    r"true_len=\s*(\d+) LAST=(\d)\s+down=\s*(-?\d+)\s+(STRONG|weak)\s+bits=([01]+)")

# segkey -> list of all strong-hit positions (to know if a position was the
# segment's ONLY strong candidate)
seg_positions = defaultdict(set)
hits = []
segkey = None
with open(PATH, encoding="utf-8", errors="replace") as f:
    for line in f:
        m = seg_re.match(line)
        if m:
            segkey = (m.group(2), int(m.group(3)))
            continue
        m = hit_re.search(line)
        if m and m.group(9) == "STRONG":
            pos = int(m.group(3))
            seg_positions[segkey].add(pos)
            hits.append((segkey, pos, m.group(10), int(m.group(6)),
                         int(m.group(7)), int(m.group(8))))

for pfx in PREFIXES:
    print(f"=== prefix {pfx} ===")
    by_seg = defaultdict(list)
    for segkey, pos, window, L, last, down in hits:
        if window.startswith(pfx):
            by_seg[segkey].append((pos, window, L, last, down))
    for segkey, rows in sorted(by_seg.items()):
        only = "ONLY-STRONG-POS" if len(seg_positions[segkey]) == 1 else \
               f"{len(seg_positions[segkey])} strong positions in seg"
        print(f"  {segkey[0]} seg{segkey[1]}  ({only})")
        for pos, window, L, last, down in sorted(rows):
            print(f"    bit {pos:6d}  L={L:2d} LAST={last} down={down:5d}  {window}")
    print()
