"""
Session 10 -- pair-corpus first-look analysis, saved from the inline runs
that produced the ledger #36 findings. Session 11's mining starts here.

Given `stasis_harvest.json` (the 45 known-plaintext pairs), this script:
  1. tests each +8-lendiff pair for the exact single-8-bit-insertion
     property (fresh[:p] + fresh[p+8:] == std for some p), reporting the
     feasible insertion window and the inserted field value(s);
  2. traces the standard MB's event boundaries (MCBPC/CBPY/DQUANT/DC/
     TCOEF/ESC per block) and maps each insertion window onto them;
  3. summarizes: insertion-slot histogram, inserted-value histogram,
     and the non-+8 pairs' lendiff spread (the mining backlog).

Known result (2026-08-02): 26/45 pairs have lendiff +8; 25/26 are exact
single insertions; values cluster on `01010110`(86) with 43/202/101 as
1-bit shifts (window ambiguity); slots are content-dependent block-level
positions (CBPY..DC6, sometimes inside a block's AC events).

Usage: python pair_insertions.py
"""
import json
from collections import Counter

from bitreader import BitReader
from mb_catalog import get_frame
from i263_decoder import (peek_safe, VLC_TAB5, VLC_TAB6, MCBPC_INTRA,
                          CBPY_TAB, MB_INTRA_Q)


def trace_mb(frame, start):
    """Event list [(kind, abs_start, abs_end)] for one standard MB."""
    br = BitReader(frame, start)
    ev = []
    p0 = br.pos
    while True:
        vlc = peek_safe(br, 6)
        sym = MCBPC_INTRA[vlc]
        br.skip(sym & 0xFF)
        if vlc == 0:
            continue
        break
    ev.append(("MCBPC", p0, br.pos))
    mbt = (sym >> 10) & 7
    cbpc = (sym >> 8) & 3
    p0 = br.pos
    sym = CBPY_TAB[peek_safe(br, 6)]
    br.skip(sym & 0xFF)
    cbpy = (sym >> 12) & 0xF
    ev.append(("CBPY", p0, br.pos))
    if mbt == MB_INTRA_Q:
        p0 = br.pos
        br.read(2)
        ev.append(("DQUANT", p0, br.pos))
    cbp = (cbpy << 2) | cbpc
    for b in range(6):
        cbp += cbp
        p0 = br.pos
        br.read(8)
        ev.append((f"DC{b + 1}", p0, br.pos))
        if cbp & 64:
            cn, last = 1, False
            while cn < 64 and not last:
                p0 = br.pos
                w13 = peek_safe(br, 13)
                sym2 = VLC_TAB5[w13 >> 5]
                if sym2 == 1:
                    br.skip(7)
                    last = bool(br.read(1))
                    run = br.read(6)
                    br.read(8)
                    ev.append((f"ESC.b{b + 1}", p0, br.pos))
                else:
                    sym2 = (VLC_TAB6[w13] if (sym2 & 1) and (sym2 >> 1)
                            else sym2 >> 1)
                    skip = (sym2 >> 17) & 0x1F
                    run = ((sym2 >> 8) & 0xFF) - 1
                    last = bool((sym2 >> 16) & 1)
                    br.skip(skip)
                    ev.append((f"T.b{b + 1}", p0, br.pos))
                cn += run + 1
    return ev, cbpc, cbpy


def main():
    h = json.load(open("stasis_harvest.json"))
    pairs = h["pairs"]
    print(f"{len(pairs)} pairs")

    slot_hist = Counter()
    field_hist = Counter()
    n8 = exact = 0
    other = Counter()
    for p in pairs:
        f, s = p["fresh_bits"], p["std_bits"]
        if len(f) - len(s) != 8:
            other[p["lendiff"]] += 1
            continue
        n8 += 1
        hits = [i for i in range(len(f) - 7) if f[:i] + f[i + 8:] == s]
        if not hits:
            print(f"  fr{p['std_frame']:5d}: +8 but NO single-insertion fit")
            continue
        exact += 1
        lo, hi = hits[0], hits[-1]
        for i in hits:
            field_hist[f[i:i + 8]] += 1 / len(hits)
        frame, _ = get_frame(p["std_frame"])
        ev, cbpc, cbpy = trace_mb(frame, p["std_start"])
        rel = [(k, a - p["std_start"], b - p["std_start"]) for k, a, b in ev]
        where = [k for k, a, b in rel
                 if a <= lo < b or a <= hi < b or (lo <= a and b <= hi + 1)]
        slot_hist["+".join(where)] += 1
        evstr = " ".join(f"{k}[{a}:{b}]" for k, a, b in rel)
        print(f"fr{p['std_frame']:5d} cbpc={cbpc} cbpy={cbpy:2d} "
              f"ins[{lo}..{hi}] field {f[lo:lo + 8]} in {where}: {evstr}")

    print(f"\n+8 pairs: {n8}, exact single 8-bit insertions: {exact}")
    print(f"insertion slot histogram: {dict(slot_hist.most_common())}")
    print("inserted field values (weighted by window ambiguity):")
    for v, c in field_hist.most_common():
        print(f"  {v} ({int(v, 2):3d}): {c:.2f}")
    print(f"\nnon-+8 lendiffs (mining backlog): {dict(sorted(other.items()))}")


if __name__ == "__main__":
    main()
