"""
Session 11 -- plaintext-pair mining, step 1: classify the +8 insertion rule.

Builds on pair_insertions.py's finding (25/26 +8 pairs are exact single
8-bit insertions; values cluster on 01010110=86 and its bit-shifts).

Working hypothesis H1: the inserted field is the CONSTANT bit string
01010110; the other observed values are insertion-window ambiguity
artifacts. This script tests H1, canonicalizes each pair's insertion
position on it, maps canonical positions onto the standard MB's event
grammar, and correlates the landing slot with MB content (std DCs).

Usage: python pair_mining.py
"""
import json
from collections import Counter

from mb_catalog import get_frame
from pair_insertions import trace_mb

MARKER = "01010110"


def insertion_hits(fresh, std):
    """All positions p with fresh[:p]+fresh[p+8:] == std."""
    return [p for p in range(len(fresh) - 7) if fresh[:p] + fresh[p + 8:] == std]


def trace_pair_events(p):
    """Concatenated event list across all MBs of the middle, rel to std_start."""
    frame, _ = get_frame(p["std_frame"])
    ev = []
    for mi, (s, e) in enumerate(p["std_mb_bounds"]):
        mbev, cbpc, cbpy = trace_mb(frame, s)
        tag = f"m{mi}." if len(p["std_mb_bounds"]) > 1 else ""
        ev += [(tag + k, a - p["std_start"], b - p["std_start"]) for k, a, b in mbev]
    return ev


def slot_of(ev, pos):
    """(event_name, offset_within) for a bit position; boundary => start of next."""
    for k, a, b in ev:
        if a <= pos < b:
            return k, pos - a
    if ev and pos == ev[-1][2]:
        return "END", 0
    return "?", -1


def main():
    h = json.load(open("stasis_harvest.json"))
    pairs = [p for p in h["pairs"] if p["lendiff"] == 8]
    print(f"{len(pairs)} +8 pairs\n")

    slot_hist = Counter()
    off_hist = Counter()
    n_h1 = 0
    rows = []
    for p in pairs:
        f, s = p["fresh_bits"], p["std_bits"]
        hits = insertion_hits(f, s)
        if not hits:
            print(f"fr{p['std_frame']:5d}: NO single-insertion fit "
                  f"(pfx={common_pfx(f,s)}, sfx={common_sfx(f,s)}) -- backlog")
            continue
        vals = {q: f[q:q + 8] for q in hits}
        canon = [q for q in hits if vals[q] == MARKER]
        ev = trace_pair_events(p)
        if not canon:
            print(f"fr{p['std_frame']:5d}: H1 FAILS -- feasible values "
                  f"{sorted(set(vals.values()))} window {hits[0]}..{hits[-1]}")
            continue
        n_h1 += 1
        # canonical position: if several give the marker, keep all
        slots = [slot_of(ev, q) for q in canon]
        for q, (k, off) in zip(canon, slots):
            base = k.split(".")[-1] if k.startswith("m") else k
            slot_hist[base] += 1 / len(canon)
            off_hist[(base, off)] += 1 / len(canon)
        dcs = p["std_dcs"]
        rows.append((p, canon, slots, dcs))
        sl = " | ".join(f"p={q} {k}+{off}" for q, (k, off) in zip(canon, slots))
        print(f"fr{p['std_frame']:5d} n_mbs={p['std_mbs']} canon: {sl}"
              f"   dcs={dcs}")

    print(f"\nH1 (constant marker {MARKER}) holds on {n_h1}/{len(pairs)-1} "
          f"single-insertion pairs")
    print(f"slot histogram (canonical): {dict(slot_hist.most_common())}")
    print(f"(slot, offset) histogram: "
          f"{ {k: round(v,2) for k, v in off_hist.most_common()} }")

    # --- content correlation for the unambiguous DC-slot pairs -------------
    print("\n--- DC-slot content probes (unique canonical position only) ---")
    print("block = DC index containing the marker; probe: is that block's DC")
    print("an outlier vs the MB's other lumas / its chroma partner?")
    for p, canon, slots, dcs in rows:
        if len(canon) != 1:
            continue
        k, off = slots[0]
        base = k.split(".")[-1]
        if not base.startswith("DC"):
            continue
        b = int(base[2:]) - 1
        mi = int(k.split(".")[0][1:]) if k.startswith("m") and "." in k else 0
        d = dcs[mi]
        luma, chroma = d[:4], d[4:]
        marks = []
        if b < 4:
            others = [x for i, x in enumerate(luma) if i != b]
            marks.append(f"luma dev {d[b]-sum(others)/3:+.0f}")
            if d[b] in (max(luma), min(luma)):
                marks.append("EXTREME-luma")
        else:
            marks.append(f"chroma pair {chroma}")
        print(f"fr{p['std_frame']:5d} marker in {base}+{off}: dcs={d}"
              f"  [{', '.join(marks)}]")


def common_pfx(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


def common_sfx(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[-1 - n] == b[-1 - n]:
        n += 1
    return n


if __name__ == "__main__":
    main()
