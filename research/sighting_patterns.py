"""
Session 5 — direct pattern analysis of the 1,493 in-sync proprietary-code
sightings (32-bit windows from full_scan_results.json).

If the extension codes are code+FLC-payload (like ESCAPE), the bits after
the code prefix are payload = near-uniform, while any further structural
bits (e.g. a terminator, or the next event's code) are biased. Per window
value, print the per-position frequency of '1' across all sightings, plus
the most common full prefixes. Bias profile vs position reveals where the
code/payload boundary sits — no decoding, no self-sync pollution.
"""
import json
from collections import Counter

results = json.load(open("full_scan_results.json"))
sight = [r for r in results if r["kind"] == "invalid_tcoef"]
print(f"{len(sight)} sightings\n")

# global: per-position P(bit=1) for bits 13..31 (after the window value)
all_wins = [r["win32"] for r in sight]


def bias_row(wins, lo, hi):
    out = []
    for p in range(lo, hi):
        ones = sum(1 for w in wins if w[p] == "1")
        out.append(ones / len(wins))
    return out


print("ALL sightings, P(bit=1) at positions 9..31 (positions 0-8 are the 9 zeros):")
row = bias_row(all_wins, 9, 32)
print("  pos: " + " ".join(f"{p:4d}" for p in range(9, 32)))
print("  P1 : " + " ".join(f"{v:4.2f}" for v in row))

for wv in range(16):
    wins = [r["win32"] for r in sight if int(r["win32"][:13], 2) == wv]
    if len(wins) < 10:
        print(f"\nwindow {wv:2d} ({wv:013b}): n={len(wins)} (too few, listing)")
        for w in wins:
            print(f"    {w}")
        continue
    print(f"\nwindow {wv:2d} ({wv:013b}): n={len(wins)}")
    row = bias_row(wins, 13, 32)
    print("  pos: " + " ".join(f"{p:4d}" for p in range(13, 32)))
    print("  P1 : " + " ".join(f"{v:4.2f}" for v in row))
    c = Counter(w[13:22] for w in wins)
    print("  next-9-bits top5: " +
          ", ".join(f"{k}({n})" for k, n in c.most_common(5)))
