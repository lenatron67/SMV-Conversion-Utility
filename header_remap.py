"""
Session 7 -- MB-header-layer override search (next-action #2, clause 1).

length_remap.py (runs 1+2) showed single TCOEF/ESC/INTRADC length overrides
land almost none of the 1-MB failing gaps -- the TCOEF layer is exhausted as
a single-divergence explanation. This script exhausts the remaining
single-event layer: the MB header. For each failing gap, at each recorded
MCBPC or CBPY event, override the event with EVERY (consumed-length, forced
symbol) combination and keep overrides that make the gap land exactly:

  MCBPC: L' in 1..9, forced (mbt in {INTRA, INTRA_Q}) x (cbpc in 0..3)
  CBPY:  L' in 1..8, forced cbpy in 0..15

Rationale this layer is NOT census-closed like TCOEF: anchored MBs confirm
the standard header codes are in use, but the header code spaces have UNUSED
prefixes (CBPY_TAB has sym==0 holes -- 'bad cbpy' is ~7% of small-gap
failures), so proprietary header codes could live there without displacing
any confirmed-standard code. A real header remap recurs as a consistent
(window-bits -> length, symbol) vote across gaps; self-sync scatters.

Usage: python header_remap.py [min_bits max_bits [catalog.json]]
Writes header_remap_results.json (or *_clean.json when run on
clean_catalog.json). Session-9 re-run: pass clean_catalog.json -- gaps carry
a tier tag (gold/silver, ledger #32) and votes are reported per-tier.
"""
import json
import sys
from collections import Counter, defaultdict

import ext_bootstrap as eb
from bitreader import BitReader
from i263_decoder import (DecodeError, peek_safe, VLC_TAB5, VLC_TAB6,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)


def parse(frame, start, end, ov=None, events=None):
    """Stock-parse complete MBs in [start, end); land EXACTLY on end or raise.

    ov overrides ONE header event, identified by its start bit:
      {"pos", "len", "kind": "mcbpc", "mbt", "cbpc"} -- consume len bits in
        place of the whole MCBPC read (incl. any stuffing), force mbt/cbpc;
      {"pos", "len", "kind": "cbpy", "cbpy"} -- consume len bits, force cbpy.

    events, if a list, collects {"pos", "kind": "mcbpc"|"cbpy", "len"} for
    each header event (stock consumed length, stuffing included for mcbpc).
    """
    br = BitReader(frame, start)
    g = 16
    n = 0
    ov_pos = ov["pos"] if ov else -1
    while br.pos < end:
        pos = br.pos
        if pos == ov_pos and ov["kind"] == "mcbpc":
            if end - pos < ov["len"]:
                raise DecodeError("EOF override")
            br.skip(ov["len"])
            mbt, cbpc = ov["mbt"], ov["cbpc"]
            ov_pos = -1
        else:
            while True:                          # MCBPC (+ stuffing)
                vlc = peek_safe(br, 6)
                sym = MCBPC_INTRA[vlc]
                br.skip(sym & 0xFF)
                if vlc == 0:
                    if br.pos >= end:
                        raise DecodeError("stuffing past end")
                    continue
                break
            if events is not None:
                events.append({"pos": pos, "kind": "mcbpc",
                               "len": br.pos - pos})
            mbt = (sym >> 10) & 7
            cbpc = (sym >> 8) & 3
            if mbt not in (MB_INTRA, MB_INTRA_Q):
                raise DecodeError("non-intra")
        pos = br.pos
        if pos == ov_pos and ov["kind"] == "cbpy":
            if end - pos < ov["len"]:
                raise DecodeError("EOF override")
            br.skip(ov["len"])
            cbpy = ov["cbpy"]
            ov_pos = -1
        else:
            sym = CBPY_TAB[peek_safe(br, 6)]
            if sym == 0:
                if events is not None:
                    events.append({"pos": pos, "kind": "cbpy", "len": None})
                raise DecodeError("bad cbpy")
            br.skip(sym & 0xFF)
            if events is not None:
                events.append({"pos": pos, "kind": "cbpy",
                               "len": br.pos - pos})
            cbpy = (sym >> 12) & 0xF
        if mbt == MB_INTRA_Q:
            if end - br.pos < 2:
                raise DecodeError("EOF dquant")
            g += DQUANT_DIFF[br.read(2)]
            if not (1 <= g <= 31):
                raise DecodeError("gquant")
        cbp = (cbpy << 2) | cbpc
        for b in range(6):
            cbp += cbp
            if end - br.pos < 8:
                raise DecodeError("EOF intradc")
            v = br.read(8)
            if v in (0x00, 0x80):
                raise DecodeError("bad intradc")
            if not (cbp & 64):
                continue
            cn = 1
            last = False
            while cn < 64 and not last:
                w13 = peek_safe(br, 13)
                sym2 = VLC_TAB5[w13 >> 5]
                if sym2 == 1:                    # ESCAPE
                    if end - br.pos < 22:
                        raise DecodeError("EOF esc")
                    br.skip(7)
                    last = bool(br.read(1))
                    run = br.read(6)
                    level = br.read(8)
                    if level in (0x00, 0x80):
                        raise DecodeError("esc level")
                else:
                    if (sym2 & 1) and (sym2 >> 1):
                        sym2 = VLC_TAB6[w13]
                    else:
                        sym2 >>= 1
                    skip = (sym2 >> 17) & 0x1F
                    if sym2 == 0 or skip == 0:
                        raise DecodeError("invalid tcoef")
                    if end - br.pos < skip:
                        raise DecodeError("EOF tcoef")
                    run = ((sym2 >> 8) & 0xFF) - 1
                    last = bool((sym2 >> 16) & 1)
                    br.skip(skip)
                if cn + run > 63:
                    raise DecodeError("overflow")
                cn += run + 1
        n += 1
    if br.pos != end:
        raise DecodeError("misland")
    return n


def solve_gap(frame, start, end):
    events = []
    try:
        parse(frame, start, end, events=events)
        return events, None
    except DecodeError:
        pass
    sols = []
    for ev in events:
        if ev["kind"] == "mcbpc":
            for L in range(1, 10):
                for mbt in (MB_INTRA, MB_INTRA_Q):
                    for cbpc in range(4):
                        o = {"pos": ev["pos"], "len": L, "kind": "mcbpc",
                             "mbt": mbt, "cbpc": cbpc}
                        try:
                            parse(frame, start, end, ov=o)
                            sols.append(o)
                        except DecodeError:
                            pass
        else:
            for L in range(1, 9):
                for cbpy in range(16):
                    o = {"pos": ev["pos"], "len": L, "kind": "cbpy",
                         "cbpy": cbpy}
                    try:
                        parse(frame, start, end, ov=o)
                        sols.append(o)
                    except DecodeError:
                        pass
    return events, sols


def main():
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 53
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    catfile = sys.argv[3] if len(sys.argv) > 3 else "mb_catalog.json"
    cat = json.load(open(catfile))
    fo = eb.frame_loader()
    gaps = [g for g in cat["gaps"]
            if not g["stock_ok"] and lo <= g["end"] - g["start"] <= hi]
    tiers = Counter(g.get("tier", "-") for g in gaps)
    print(f"{len(gaps)} failing gaps of size [{lo},{hi}] bits "
          f"from {catfile} (tiers: {dict(tiers)})")

    votes = Counter()          # (kind, window-bits, sym-tuple) -> gap votes
    gold_votes = Counter()
    uniq = Counter()
    landed = 0
    landed_tier = Counter()
    nsol_hist = Counter()
    for i, gp in enumerate(gaps):
        if i and i % 500 == 0:
            print(f"  ...{i}/{len(gaps)} gaps, {landed} landed", flush=True)
        tier = gp.get("tier", "-")
        frame = fo(gp["frame"])
        events, sols = solve_gap(frame, gp["start"], gp["end"])
        if sols is None:
            continue
        keys = set()
        for o in sols:
            bits = eb.window_at(frame, o["pos"])[:o["len"]]
            sym = ((o["mbt"], o["cbpc"]) if o["kind"] == "mcbpc"
                   else (o["cbpy"],))
            keys.add((o["kind"], bits, sym))
        nsol_hist[min(len(keys), 10)] += 1
        if keys:
            landed += 1
            landed_tier[tier] += 1
        for k in keys:
            votes[k] += 1
            if tier == "gold":
                gold_votes[k] += 1
            if len(keys) == 1:
                uniq[k] += 1

    print(f"\ngaps landed by >=1 single header override: "
          f"{landed}/{len(gaps)} (by tier: {dict(landed_tier)})")
    print("solutions-per-gap histogram (10 = >=10):",
          dict(sorted(nsol_hist.items())))
    print("\ntop candidates (votes / gold / unique / kind / bits / "
          "forced-symbol):")
    rows = []
    for (kind, bits, sym), v in votes.most_common():
        rows.append({"kind": kind, "bits": bits, "sym": list(sym),
                     "votes": v, "gold": gold_votes[(kind, bits, sym)],
                     "unique": uniq[(kind, bits, sym)]})
    for r in rows[:40]:
        print(f"  {r['votes']:5d}  gold {r['gold']:3d}  "
              f"uniq {r['unique']:4d}  {r['kind']:<6} "
              f"{r['bits']:<10} -> {tuple(r['sym'])}")

    # structural aggregate: same (kind, consumed length, forced symbol)
    # recurring across DIFFERENT bit patterns = fixed-length-field signature
    agg = Counter()
    agg_uniq = Counter()
    for (kind, bits, sym), v in votes.items():
        agg[(kind, len(bits), sym)] += v
        agg_uniq[(kind, len(bits), sym)] += uniq[(kind, bits, sym)]
    print("\naggregate by (kind, len, forced-symbol):")
    for (kind, L, sym), v in agg.most_common(20):
        print(f"  {v:5d}  uniq {agg_uniq[(kind, L, sym)]:4d}  "
              f"{kind:<6} len {L}  -> {sym}")

    # raw-bitmap consistency for cbpy solutions: does the forced cbpy equal
    # the consumed bits under a simple mapping?
    maps = {"identity": lambda b: int(b, 2),
            "complement": lambda b: int(b, 2) ^ ((1 << len(b)) - 1),
            "bit-reversed": lambda b: int(b[::-1], 2),
            "rev-complement": lambda b: int(b[::-1], 2) ^ ((1 << len(b)) - 1)}
    print("\ncbpy raw-field consistency (votes where forced == f(bits)):")
    tot = sum(v for (k, _, _), v in votes.items() if k == "cbpy")
    for name, f in maps.items():
        n = sum(v for (k, bits, sym), v in votes.items()
                if k == "cbpy" and f(bits) == sym[0])
        nu = sum(uniq[(k, bits, sym)] for (k, bits, sym) in votes
                 if k == "cbpy" and f(bits) == sym[0])
        print(f"  {name:<14} {n}/{tot} votes ({nu} unique)")

    out = ("header_remap_results_clean.json" if catfile != "mb_catalog.json"
           else "header_remap_results.json")
    json.dump({"band": [lo, hi], "catalog": catfile, "n_gaps": len(gaps),
               "tiers": dict(tiers), "landed": landed,
               "landed_tier": dict(landed_tier),
               "nsol_hist": dict(nsol_hist), "candidates": rows},
              open(out, "w"), indent=1)
    print(f"\nwritten {out}")


if __name__ == "__main__":
    main()
