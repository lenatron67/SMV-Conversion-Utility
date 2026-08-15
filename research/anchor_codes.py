"""
Session 6 — trusted-codeword census from anchored MBs.

The MB catalog's ANCHORED MBs parse cleanly with stock tables BY CONSTRUCTION
(their boundaries are validated by cross-frame identity). Therefore any TCOEF
codeword that appears INSIDE an anchored MB's block as a mid-block LAST=0 event
is DEFINITIVELY a genuine standard LAST=0 code in this stream — it cannot be a
code whose true LAST is 1 (or the anchored MB would have mis-parsed and become
a gap instead).

This gives a rigorous discriminator for the LAST-flip-remap hypothesis
(ledger #26) that does NOT depend on any heuristic:
  - A codeword that appears as mid-block LAST=0 in trusted MBs is CONFIRMED
    standard -> it is NOT a LAST-flip remap (kills 101/100 etc. cleanly).
  - A LAST-flip-remap candidate must be a codeword that drives over-run in
    failing gaps but essentially NEVER appears mid-block LAST=0 in anchored MBs.

Output: per-codeword counts {mid_block_last0, terminal_last0_neverhappens,
last1} over a sample of anchored MBs.

Usage: python anchor_codes.py [sample_n]
"""
import json
import sys
from collections import Counter

import ext_bootstrap as eb
from bitreader import BitReader
from i263_decoder import (DecodeError, peek_safe, VLC_TAB5, VLC_TAB6,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)


def census_mb(frame, start, end, midblock_last0, any_last0, total_seen):
    """Parse one anchored MB [start,end); tally codewords. For each VLC event,
    record its skip-length prefix in total_seen; if stock LAST=0 record in
    any_last0; if it is NOT the final coefficient of its block (i.e. another
    coefficient follows in the same block) record in midblock_last0 — that is
    the rigorous 'confirmed standard LAST=0' signal."""
    br = BitReader(frame, start)
    g = 16
    while br.pos < end:
        while True:
            vlc = peek_safe(br, 6)
            sym = MCBPC_INTRA[vlc]
            br.skip(sym & 0xFF)
            if vlc == 0:
                if br.pos >= end:
                    return
                continue
            break
        mbt = (sym >> 10) & 7
        cbpc = (sym >> 8) & 3
        if mbt not in (MB_INTRA, MB_INTRA_Q):
            return
        sym = CBPY_TAB[peek_safe(br, 6)]
        if sym == 0:
            return
        br.skip(sym & 0xFF)
        cbpy = (sym >> 12) & 0xF
        if mbt == MB_INTRA_Q:
            if end - br.pos < 2:
                return
            g += DQUANT_DIFF[br.read(2)]
        cbp = (cbpy << 2) | cbpc
        for b in range(6):
            cbp += cbp
            if end - br.pos < 8:
                return
            v = br.read(8)
            if v in (0x00, 0x80):
                return
            if not (cbp & 64):
                continue
            cn = 1
            last = False
            block_events = []          # (key_or_None, last) per event
            while cn < 64 and not last:
                pos = br.pos
                w13 = peek_safe(br, 13)
                sym2 = VLC_TAB5[w13 >> 5]
                if sym2 == 1:
                    if end - br.pos < 22:
                        return
                    br.skip(7)
                    last = bool(br.read(1))
                    run = br.read(6)
                    level = br.read(8)
                    if level in (0x00, 0x80):
                        return
                    block_events.append((None, last))
                else:
                    if (sym2 & 1) and (sym2 >> 1):
                        sym2 = VLC_TAB6[w13]
                    else:
                        sym2 >>= 1
                    skip = (sym2 >> 17) & 0x1F
                    if sym2 == 0 or skip == 0:
                        return
                    if end - br.pos < skip:
                        return
                    run = ((sym2 >> 8) & 0xFF) - 1
                    last = bool((sym2 >> 16) & 1)
                    key = eb.window_at(frame, pos)[:skip]
                    br.skip(skip)
                    block_events.append((key, last))
                if cn + run > 63:
                    return
                cn += run + 1
            # tally: a VLC event is 'mid-block' if another event follows it
            for i, (key, lst) in enumerate(block_events):
                if key is None:
                    continue
                total_seen[key] += 1
                if not lst:
                    any_last0[key] += 1
                    if i < len(block_events) - 1:
                        midblock_last0[key] += 1


def main():
    sample_n = int(sys.argv[1]) if len(sys.argv) > 1 else 30000
    cat = json.load(open("mb_catalog.json"))
    anchors = cat["anchors"]
    # spread the sample across the file
    step = max(1, len(anchors) // sample_n)
    sel = anchors[::step][:sample_n]
    print(f"sampling {len(sel)} of {len(anchors)} anchored MBs")
    fo = eb.frame_loader()

    midblock_last0 = Counter()
    any_last0 = Counter()
    total_seen = Counter()
    for a in sel:
        try:
            census_mb(fo(a["frame"]), a["start"], a["end"],
                      midblock_last0, any_last0, total_seen)
        except (DecodeError, IndexError):
            pass

    print(f"\ndistinct codewords seen in trusted MBs: {len(total_seen)}")
    print("CONFIRMED-STANDARD LAST=0 codes (appear mid-block last=0 in "
          "trusted MBs -> NOT a last-flip remap):")
    confirmed = {k for k, c in midblock_last0.items() if c >= 3}
    for k in sorted(confirmed, key=lambda k: -midblock_last0[k])[:25]:
        print(f"   {k:<14} midblock_last0={midblock_last0[k]:6d} "
              f"any_last0={any_last0[k]:6d} total={total_seen[k]:6d}")

    # cross-reference the remap_search single-flip winners
    try:
        flips = json.load(open("remap_last_flips.json"))
        print("\nremap_search LAST-flip winners vs trusted census:")
        for r in flips[:15]:
            k = r["code"]
            mb = midblock_last0.get(k, 0)
            verdict = "CONFIRMED STD (reject flip)" if mb >= 3 else \
                      "not seen mid-block last0 (REMAP CANDIDATE)"
            print(f"   {k:<14} dgaps={r['delta']:+5d}  "
                  f"midblock_last0={mb:6d}  -> {verdict}")
    except FileNotFoundError:
        print("\n(run remap_search.py first for the cross-reference)")

    json.dump({"confirmed_std_last0": sorted(confirmed),
               "midblock_last0": dict(midblock_last0),
               "any_last0": dict(any_last0),
               "total_seen": dict(total_seen)},
              open("anchor_codes.json", "w"), indent=1)
    print("\nwritten anchor_codes.json")


if __name__ == "__main__":
    main()
