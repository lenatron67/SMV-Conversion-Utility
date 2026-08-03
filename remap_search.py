"""
Session 6 — LAST-flag remap search.

Diagnostic finding (this session): 66% of small (<=100 bit) failing catalog
gaps contain NO deep-zero TCOEF event, yet still fail stock parsing. Their
failure kind is overwhelmingly "ran past the gap end" (EOF tcoef/intradc/esc):
stock parsing UNDER-terminates blocks -- it keeps reading coefficients past
where the true MB ended. That is the signature of a LAST-flag remap: a
standard codeword that truth marks LAST=1 (block ends) is decoded by the
stock table as LAST=0 (keep reading). cf. the code 00000011000, which stock
TABLE 13 defines as run=5,LAST=0 but the SMV stream uses as LAST=1.

This script tests that hypothesis directly and decisively: for each standard
TCOEF codeword that actually appears (identified by its exact skip-length bit
prefix), flip its LAST flag globally and count how many anchor-pinned gaps
then parse-and-land EXACTLY. A genuine remap should land many gaps; a spurious
one lands few or regresses. The codeword set is tiny (<=102), so this is a
fast, complete scan -- no self-sync brute force.

Usage: python remap_search.py [max_gap_bits]
"""
import json
import sys
from collections import Counter

import ext_bootstrap as eb
from bitreader import BitReader
from i263_decoder import (DecodeError, peek_safe, VLC_TAB5, VLC_TAB6,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)


def parse_flip(frame, start, end, flips, collect=None):
    """Stock-parse complete MBs in [start,end). flips: codeword-prefix ->
    forced_last (bool). At each VLC TCOEF event, if the stock codeword's
    skip-length bit prefix is in flips, force its LAST to flips[prefix]
    (skip and run unchanged). Raises unless it lands EXACTLY on end.
    If collect is a Counter, tallies every stock LAST=0 VLC codeword prefix."""
    br = BitReader(frame, start)
    g = 16
    n = 0
    while br.pos < end:
        while True:
            vlc = peek_safe(br, 6)
            sym = MCBPC_INTRA[vlc]
            br.skip(sym & 0xFF)
            if vlc == 0:
                if br.pos >= end:
                    raise DecodeError("stuffing past end")
                continue
            break
        mbt = (sym >> 10) & 7
        cbpc = (sym >> 8) & 3
        if mbt not in (MB_INTRA, MB_INTRA_Q):
            raise DecodeError("non-intra")
        sym = CBPY_TAB[peek_safe(br, 6)]
        if sym == 0:
            raise DecodeError("bad cbpy")
        br.skip(sym & 0xFF)
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
                pos = br.pos
                w13 = peek_safe(br, 13)
                sym2 = VLC_TAB5[w13 >> 5]
                if sym2 == 1:                       # ESCAPE
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
                    key = eb.window_at(frame, pos)[:skip]
                    br.skip(skip)
                    if collect is not None and not last:
                        collect[key] += 1
                    if key in flips:
                        last = flips[key]
                if cn + run > 63:
                    raise DecodeError("overflow")
                cn += run + 1
        n += 1
    if br.pos != end:
        raise DecodeError("misland")
    return n


def lands(frame, start, end, flips):
    try:
        parse_flip(frame, start, end, flips)
        return True
    except DecodeError:
        return False


def frame_mbs(frame, start, total, flips):
    """Full-frame decode under flips; tolerant of trailing stuffing.
    Returns #MBs decoded before any failure (99 = whole QCIF frame)."""
    n = 0
    pos = start
    while n < 99:
        # find a safe end: decode one MB by parse_flip on [pos, total) is not
        # exact; instead reuse parse_flip's loop body via a 1-MB window probe.
        try:
            # parse exactly one MB by letting parse_flip run on a generous
            # window and catching where it lands is awkward; decode inline.
            pos = _decode_one_mb(frame, pos, total, flips)
        except DecodeError:
            break
        if pos is None or pos > total:
            break
        n += 1
    return n


def _decode_one_mb(frame, start, limit, flips):
    """Decode a single MB starting at `start`; return end bit or raise."""
    br = BitReader(frame, start)
    g = 16
    while True:
        vlc = peek_safe(br, 6)
        sym = MCBPC_INTRA[vlc]
        br.skip(sym & 0xFF)
        if vlc == 0:
            if br.pos >= limit:
                raise DecodeError("stuffing end")
            continue
        break
    mbt = (sym >> 10) & 7
    cbpc = (sym >> 8) & 3
    if mbt not in (MB_INTRA, MB_INTRA_Q):
        raise DecodeError("non-intra")
    sym = CBPY_TAB[peek_safe(br, 6)]
    if sym == 0:
        raise DecodeError("bad cbpy")
    br.skip(sym & 0xFF)
    cbpy = (sym >> 12) & 0xF
    if mbt == MB_INTRA_Q:
        if limit - br.pos < 2:
            raise DecodeError("EOF dquant")
        g += DQUANT_DIFF[br.read(2)]
    cbp = (cbpy << 2) | cbpc
    for b in range(6):
        cbp += cbp
        if limit - br.pos < 8:
            raise DecodeError("EOF intradc")
        v = br.read(8)
        if v in (0x00, 0x80):
            raise DecodeError("bad intradc")
        if not (cbp & 64):
            continue
        cn = 1
        last = False
        while cn < 64 and not last:
            pos = br.pos
            w13 = peek_safe(br, 13)
            sym2 = VLC_TAB5[w13 >> 5]
            if sym2 == 1:
                if limit - br.pos < 22:
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
                if limit - br.pos < skip:
                    raise DecodeError("EOF tcoef")
                run = ((sym2 >> 8) & 0xFF) - 1
                last = bool((sym2 >> 16) & 1)
                key = eb.window_at(frame, pos)[:skip]
                br.skip(skip)
                if key in flips:
                    last = flips[key]
            if cn + run > 63:
                raise DecodeError("overflow")
            cn += run + 1
    return br.pos


def baseline_mbs(flips):
    """Sum of MBs decoded across the 15 sample frames under flips."""
    from pathlib import Path
    from bitreader import decode_picture_header
    samples = Path("samples")
    manifest = json.loads((samples / "manifest.json").read_text())
    tot = 0
    for entry in manifest["frames"]:
        data = (samples / entry["file"]).read_bytes()
        hdr = decode_picture_header(data, 0)
        tot += frame_mbs(data, hdr["header_end_bit"], len(data) * 8, flips)
    return tot


def greedy(gaps, fo, cands_sorted):
    """Greedily build a flip set: each step add the codeword whose marginal
    flip lands the most additional gaps WITHOUT regressing the 15-sample
    full-frame baseline. Reports the trajectory."""
    flips = {}
    base_b = baseline_mbs(flips)
    base_land = sum(1 for g in gaps
                    if lands(fo(g["frame"]), g["start"], g["end"], flips))
    print(f"\ngreedy start: corpus landed {base_land}/{len(gaps)}, "
          f"15-frame baseline {base_b}")
    pool = [c for c, _ in cands_sorted]
    for step in range(1, 16):
        best = None
        for key in pool:
            if key in flips:
                continue
            cand = {**flips, key: True}
            b = baseline_mbs(cand)
            if b < base_b:
                continue                # regression guard
            land = sum(1 for g in gaps
                       if lands(fo(g["frame"]), g["start"], g["end"], cand))
            if best is None or land > best[1] or (land == best[1] and b > best[2]):
                best = (key, land, b)
        if best is None or best[1] <= base_land:
            print("  no non-regressing flip adds gaps; stop")
            break
        key, land, b = best
        flips[key] = True
        print(f"  step {step}: +flip {key:<14} -> corpus {land}/{len(gaps)} "
              f"(+{land - base_land}), baseline {b} (was {base_b})")
        base_land, base_b = land, b
    print(f"\nfinal flip set ({len(flips)}): {list(flips)}")
    return flips


def main():
    max_gap = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    cat = json.load(open("mb_catalog.json"))
    fo = eb.frame_loader()
    gaps = [g for g in cat["gaps"]
            if not g["stock_ok"] and 53 <= g["end"] - g["start"] <= max_gap]
    print(f"{len(gaps)} failing gaps of size [53,{max_gap}]")

    base = sum(1 for g in gaps
               if lands(fo(g["frame"]), g["start"], g["end"], {}))
    print(f"gaps landing under stock (sanity, should be ~0): {base}")

    # tally every stock LAST=0 VLC codeword that appears
    cands = Counter()
    for g in gaps:
        try:
            parse_flip(fo(g["frame"]), g["start"], g["end"], {}, collect=cands)
        except DecodeError:
            pass
    print(f"distinct stock LAST=0 codewords appearing: {len(cands)}")

    # flip each (most frequent first) to LAST=1 globally; count landed gaps
    res = []
    for key, freq in cands.most_common():
        flips = {key: True}
        land = sum(1 for g in gaps
                   if lands(fo(g["frame"]), g["start"], g["end"], flips))
        res.append((land - base, key, freq))
    res.sort(reverse=True)
    print("\ntop single LAST=1 flips (delta gaps landed, codeword, stock_freq):")
    for d, k, f in res[:20]:
        # decode what this codeword is in stock terms
        w = int(k + "0" * (13 - len(k)), 2) if len(k) <= 13 else int(k[:13], 2)
        print(f"  {d:+6d}  {k:<14} stock_freq={f}")

    json.dump([{"delta": d, "code": k, "freq": f} for d, k, f in res[:40]],
              open("remap_last_flips.json", "w"), indent=1)
    print("\nwritten remap_last_flips.json")

    # decisive test: greedily combine flips, gated by the full-frame baseline
    greedy(gaps, fo, [(k, f) for _, k, f in res])


if __name__ == "__main__":
    main()
