"""
Session 5 — MB-boundary catalog via cross-frame alignment (the static-scene
trick; see frame_align.py for the premise and VLC_REVERSE_ENGINEERING.md
session-5 notes for the derivation).

Pipeline per frame i:
 1. matched segments against neighbours (i-1 and i+1), in frame-i bit coords;
 2. within each segment, find "anchor parses": maximal runs of complete MBs
    that parse with STOCK tables (validated port) and tile the segment
    (start/end within SLACK bits of segment edges) — their boundaries are
    treated as TRUE MB boundaries;
 3. catalog the gaps between consecutive anchors inside a frame: each gap is
    an integer number of complete MBs with EXACT start/end bits; gaps whose
    stock parse fails contain proprietary extension codes -> the inference
    corpus.

Output: mb_catalog.json with anchors and gap records.

Usage:
  python mb_catalog.py inspect 70        # verbose single-frame quality check
  python mb_catalog.py batch 0 500      # catalog frames [0, 500)
"""
import json
import sys
from collections import defaultdict

from bitreader import BitReader, decode_picture_header, find_psc_offsets
from i263_decoder import (DecodeError, peek_safe, VLC_TAB5, VLC_TAB6,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)

K = 64          # rolling window for alignment
SLACK = 24      # max coincidental match extension at segment edges

RAW = open("raw_h263.bin", "rb").read()
PSCS = find_psc_offsets(RAW)


def get_frame(i):
    off = PSCS[i]
    end = PSCS[i + 1] if i + 1 < len(PSCS) else len(RAW)
    frame = RAW[off:end]
    hdr = decode_picture_header(frame, 0)
    return frame, hdr


_bitcache = {}


def frame_bitstr(i):
    if i not in _bitcache:
        frame, hdr = get_frame(i)
        s = "".join(f"{b:08b}" for b in frame)
        _bitcache[i] = (s, hdr["header_end_bit"])
        if len(_bitcache) > 8:
            _bitcache.pop(next(iter(_bitcache)))
    return _bitcache[i]


def match_segments(i, j):
    """Maximal common substrings between MB data of frames i and j,
    returned in FRAME-i bit coordinates (absolute, incl. header offset)."""
    sa, ha = frame_bitstr(i)
    sb, hb = frame_bitstr(j)
    a, b = sa[ha:], sb[hb:]
    idx = defaultdict(list)
    for p in range(0, len(b) - K + 1, 1):
        idx[b[p:p + K]].append(p)
    segs = []
    pa = 0
    while pa + K <= len(a):
        cands = idx.get(a[pa:pa + K])
        if not cands:
            pa += 1
            continue
        prev_off = segs[-1][1] - segs[-1][0] if segs else 0
        pb = min(cands, key=lambda q: abs((q - pa) - prev_off))
        la, lb = pa, pb
        while la > 0 and lb > 0 and a[la - 1] == b[lb - 1]:
            la -= 1
            lb -= 1
        if segs and la < segs[-1][0] + segs[-1][2]:
            d = segs[-1][0] + segs[-1][2] - la
            la += d
            lb += d
        ra, rb = pa + K, pb + K
        while ra < len(a) and rb < len(b) and a[ra] == b[rb]:
            ra += 1
            rb += 1
        if ra - la >= K:
            segs.append((la, lb, ra - la))
        pa = ra
    return [(ha + s, ln) for s, _, ln in segs]


def parse_one_mb(frame, start_bit, gquant=16):
    """Stock-table parse of exactly one MB. Returns (end_bit, gquant, info)
    or raises DecodeError. info = (cbpc, cbpy, n_events)."""
    br = BitReader(frame, start_bit)
    while True:
        vlc = peek_safe(br, 6)
        sym = MCBPC_INTRA[vlc]
        br.skip(sym & 0xFF)
        if vlc == 0:
            if br.bits_remaining() <= 0:
                raise DecodeError("EOF stuffing")
            continue
        break
    mb_type = (sym >> 10) & 7
    cbpc = (sym >> 8) & 3
    if mb_type not in (MB_INTRA, MB_INTRA_Q):
        raise DecodeError("non-intra")
    sym = CBPY_TAB[peek_safe(br, 6)]
    if sym == 0:
        raise DecodeError("bad cbpy")
    br.skip(sym & 0xFF)
    cbpy = (sym >> 12) & 0xF
    if mb_type == MB_INTRA_Q:
        if br.bits_remaining() < 2:
            raise DecodeError("EOF dquant")
        gquant += DQUANT_DIFF[br.read(2)]
        if not (1 <= gquant <= 31):
            raise DecodeError("gquant range")
    cbp = (cbpy << 2) | cbpc
    n_ev = 0
    for b in range(6):
        cbp += cbp
        if br.bits_remaining() < 8:
            raise DecodeError("EOF intradc")
        v = br.read(8)
        if v in (0x00, 0x80):
            raise DecodeError("bad intradc")
        if not (cbp & 64):
            continue
        coef_num = 1
        last = False
        while coef_num < 64 and not last:
            vlc = peek_safe(br, 13)
            sym2 = VLC_TAB5[vlc >> 5]
            if sym2 == 1:
                if br.bits_remaining() < 22:
                    raise DecodeError("EOF esc")
                br.skip(7)
                last = bool(br.read(1))
                run = br.read(6)
                level = br.read(8)
                if level in (0x00, 0x80):
                    raise DecodeError("esc level")
            else:
                if (sym2 & 1) and (sym2 >> 1):
                    sym2 = VLC_TAB6[vlc]
                else:
                    sym2 >>= 1
                skip = (sym2 >> 17) & 0x1F
                if sym2 == 0 or skip == 0:
                    raise DecodeError("invalid tcoef")
                if br.bits_remaining() < skip:
                    raise DecodeError("EOF tcoef")
                run = ((sym2 >> 8) & 0xFF) - 1
                last = bool((sym2 >> 16) & 1)
                br.skip(skip)
            if coef_num + run > 63:
                raise DecodeError("run overflow")
            coef_num += run + 1
            n_ev += 1
    return br.pos, gquant, (cbpc, cbpy, n_ev)


def anchors_in_segment(frame, seg_start, seg_len):
    """Find the stock-parse tiling of a matched segment.
    A valid tiling starts within SLACK of the segment start, parses complete
    MBs, and its last MB ends within SLACK of the segment end (the unchanged
    MB run should fill the matched region up to coincidental edge bits).
    Require the tiling to be UNIQUE across start offsets — competing
    self-synced tilings with different boundaries disqualify the segment."""
    seg_end = seg_start + seg_len
    valid = []
    for d in range(min(SLACK, seg_len)):
        s = seg_start + d
        run = []
        g = 16
        while True:
            try:
                e, g, info = parse_one_mb(frame, s, g)
            except (DecodeError, IndexError):
                break
            if e > seg_end:
                break
            run.append((s, e, info))
            s = e
        if run and run[-1][1] >= seg_end - SLACK:
            valid.append(run)
    if not valid:
        return []
    # all valid tilings must agree on MB boundaries (suffix-compatible:
    # a tiling starting a few bits later may simply skip edge bits, but if
    # boundaries disagree anywhere, self-sync ambiguity -> reject)
    bounds = [tuple(e for _, e, _ in run) for run in valid]
    ref = bounds[0]
    for bs in bounds[1:]:
        if not (set(bs) <= set(ref) or set(ref) <= set(bs)):
            return []
    # return the longest (earliest-starting) tiling
    return max(valid, key=lambda r: r[-1][1] - r[0][0])


def catalog_frame(i, verbose=False):
    frame, hdr = get_frame(i)
    total_bits = len(frame) * 8
    segs = []
    if i > 0:
        segs += match_segments(i, i - 1)
    if i + 1 < len(PSCS):
        segs += match_segments(i, i + 1)
    # merge overlapping segments
    segs.sort()
    merged = []
    for s, ln in segs:
        if merged and s <= merged[-1][0] + merged[-1][1]:
            merged[-1] = (merged[-1][0],
                          max(merged[-1][1], s + ln - merged[-1][0]))
        else:
            merged.append((s, ln))

    anchors = []
    for s, ln in merged:
        run = anchors_in_segment(frame, s, ln)
        if not run:
            continue
        cov = run[-1][1] - run[0][0]
        if cov < ln - 2 * SLACK:
            continue        # poor tiling: do not trust as anchor
        anchors.append(run)
        if verbose:
            print(f"  segment @{s} len {ln}: anchored {len(run)} MBs "
                  f"[{run[0][0]}..{run[-1][1]}) cov {cov}/{ln}")
            for ms, me, (cbpc, cbpy, nev) in run:
                print(f"      MB [{ms:6d}..{me:6d}) len {me - ms:4d} "
                      f"cbpc={cbpc:02b} cbpy={cbpy:04b} events={nev}")

    # gaps between consecutive anchor runs = exact-bounded unknown spans
    gaps = []
    for a, b in zip(anchors, anchors[1:]):
        gs, ge = a[-1][1], b[0][0]
        if ge - gs < 53:
            # smaller than the minimum MB (1+4+48 bits): at least one of the
            # bounding anchors is wrong -> not a usable corpus sample
            continue
        # does the gap parse with stock tables, landing exactly?
        s = gs
        g = 16
        n_mbs = 0
        ok = True
        while s < ge:
            try:
                e, g, _ = parse_one_mb(frame, s, g)
            except (DecodeError, IndexError):
                ok = False
                break
            if e > ge:
                ok = False
                break
            s = e
            n_mbs += 1
        gaps.append({"frame": i, "start": gs, "end": ge,
                     "stock_ok": ok and s == ge,
                     "stock_mbs": n_mbs if ok and s == ge else -1})
        if verbose:
            r = gaps[-1]
            print(f"  gap [{gs}..{ge}) len {ge - gs}: "
                  f"stock_ok={r['stock_ok']} mbs={r['stock_mbs']}")
    return anchors, gaps


def main():
    mode = sys.argv[1]
    if mode == "inspect":
        i = int(sys.argv[2])
        anchors, gaps = catalog_frame(i, verbose=True)
        na = sum(len(r) for r in anchors)
        print(f"\nframe {i}: {na} anchored MBs in {len(anchors)} runs, "
              f"{len(gaps)} gaps "
              f"({sum(1 for g in gaps if not g['stock_ok'])} need extension)")
    elif mode == "batch":
        lo, hi = int(sys.argv[2]), int(sys.argv[3])
        all_anchors = []
        all_gaps = []
        for i in range(lo, hi):
            anchors, gaps = catalog_frame(i)
            for run in anchors:
                for ms, me, (cbpc, cbpy, nev) in run:
                    all_anchors.append({"frame": i, "start": ms, "end": me,
                                        "cbpc": cbpc, "cbpy": cbpy,
                                        "events": nev})
            all_gaps.extend(gaps)
            if (i + 1) % 50 == 0:
                print(f"  {i + 1} frames", flush=True)
        with open("mb_catalog.json", "w") as f:
            json.dump({"anchors": all_anchors, "gaps": all_gaps}, f)
        bad = [g for g in all_gaps if not g["stock_ok"]]
        print(f"\nframes [{lo},{hi}): {len(all_anchors)} anchored MBs, "
              f"{len(all_gaps)} exact-bounded gaps, "
              f"{len(bad)} gaps need extension codes")
        from collections import Counter
        sizes = Counter(min((g['end'] - g['start']) // 100, 20)
                        for g in bad)
        print("extension-gap size histogram (x100 bits):",
              dict(sorted(sizes.items())))


if __name__ == "__main__":
    main()
