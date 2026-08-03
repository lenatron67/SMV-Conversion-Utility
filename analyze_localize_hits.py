"""
Mine desync_localize_all.txt for recurring proprietary-code candidates.

Within ONE segment, the viable L values at a culprit position are
{true L} + {self-sync aliases}; the aliases depend on that segment's
downstream content, the true L does not.  So: group STRONG hits by the
leading k bits of their bit-window (the candidate code prefix), and
intersect the (L, LAST) sets across DIFFERENT segments.  Prefixes that
recur in >=3 segments with a small, stable (L, LAST) intersection are
real proprietary-code candidates.
"""
import re
import sys
from collections import defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else "desync_localize_all.txt"
MIN_SEGMENTS = 3

seg_re = re.compile(r"^(SOLVED|UNSOLVED)\s+(\S+) seg(\d+)")
hit_re = re.compile(
    r"MB\s+(\d+) (\w+)\s+bit\s+(\d+)\s+parsed-as\s+(\S+) \((\w+)\)\s+"
    r"true_len=\s*(\d+) LAST=(\d)\s+down=\s*(-?\d+)\s+(STRONG|weak)\s+bits=([01]+)")

hits = []   # (segkey, window, L, last, parsed_as, kind)
segkey = None
with open(PATH, encoding="utf-8", errors="replace") as f:
    for line in f:
        m = seg_re.match(line)
        if m:
            segkey = (m.group(2), int(m.group(3)))
            continue
        m = hit_re.search(line)
        if m and m.group(9) == "STRONG":
            hits.append((segkey, m.group(10), int(m.group(6)),
                         int(m.group(7)), m.group(4), m.group(5)))

print(f"Parsed {len(hits)} STRONG hits from {PATH}\n")

for k in (10, 12, 14):
    # prefix -> segkey -> set of (L, LAST)
    by_prefix = defaultdict(lambda: defaultdict(set))
    parsed_as = defaultdict(set)
    for segkey, window, L, last, pa, kind in hits:
        pfx = window[:k]
        by_prefix[pfx][segkey].add((L, last))
        parsed_as[pfx].add(f"{pa}({kind})")

    rows = []
    for pfx, segs in by_prefix.items():
        if len(segs) < MIN_SEGMENTS:
            continue
        lsets = list(segs.values())
        inter = set.intersection(*lsets)
        rows.append((len(segs), pfx, inter, lsets))

    rows.sort(key=lambda r: (-r[0], r[1]))
    print(f"=== prefix length k={k}: prefixes seen in >= {MIN_SEGMENTS} segments ===")
    print(f"{'prefix':<{k+2}} {'#segs':>5}  intersection of (L,LAST) across segments")
    for nsegs, pfx, inter, lsets in rows:
        if inter:
            inter_s = ", ".join(f"L={L}/last={l}" for L, l in sorted(inter))
        else:
            inter_s = "(empty)"
        sizes = "+".join(str(len(s)) for s in lsets)
        print(f"{pfx:<{k+2}} {nsegs:>5}  {inter_s}   [set sizes: {sizes}]"
              f"   parsed-as: {', '.join(sorted(parsed_as[pfx]))}")
    print()
