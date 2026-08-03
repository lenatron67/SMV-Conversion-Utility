"""
Session 5 — exact-fit extension-code solver over the MB catalog gaps.

Each catalog gap is a span of complete MBs with EXACT start and end bits
(pinned by validated stock-parse anchors on both sides). For each gap:
  1. stock parse from gap start, recording every TCOEF event position;
  2. for each recorded event position p (incl. the failure position), each
     skip in 8..20 and LAST in {0,1}: replay with that single override and
     require the parse to land EXACTLY on the gap end with complete MBs and
     no budget violation;
  3. record all (p, window, skip, last, n_mbs) solutions.

Gaps with exactly one solution are high-confidence observations of a
proprietary code occurrence. Aggregate (13-bit window value -> skip, last)
votes across gaps; the true table assignment should dominate per window,
while self-sync ghosts scatter.

Usage: python ext_solver.py [max_gap_bits] [min_gap_bits]
"""
import json
import sys
from collections import Counter, defaultdict

from bitreader import BitReader, find_psc_offsets
from i263_decoder import (DecodeError, peek_safe, VLC_TAB5, VLC_TAB6,
                          MCBPC_INTRA, CBPY_TAB, DQUANT_DIFF,
                          MB_INTRA, MB_INTRA_Q)

RAW = open("raw_h263.bin", "rb").read()
PSCS = find_psc_offsets(RAW)


COMMIT = {}     # prefix -> (skip, last, run): applied wherever it matches


def parse_gap(frame, start, end, override=None, events_out=None):
    """Parse complete MBs in [start, end). override = (bit, skip, last).
    Returns n_mbs if the parse lands exactly on `end`, else raises."""
    br = BitReader(frame, start)
    g = 16
    n_mbs = 0
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
            coef_num = 1
            last = False
            while coef_num < 64 and not last:
                ev_pos = br.pos
                committed = None
                if COMMIT:
                    w = window_at(frame, ev_pos, 16)
                    for pref, ent in COMMIT.items():
                        if w.startswith(pref):
                            committed = ent
                            break
                if override is not None and ev_pos == override[0]:
                    _, o_skip, o_last = override
                    if end - br.pos < o_skip:
                        raise DecodeError("override past end")
                    br.skip(o_skip)
                    last = bool(o_last)
                    run = 0
                elif committed is not None:
                    c_skip, c_last, c_run = committed
                    if end - br.pos < c_skip:
                        raise DecodeError("commit past end")
                    br.skip(c_skip)
                    last = bool(c_last)
                    run = c_run
                else:
                    vlc = peek_safe(br, 13)
                    sym2 = VLC_TAB5[vlc >> 5]
                    if sym2 == 1:
                        if end - br.pos < 22:
                            raise DecodeError("EOF esc")
                        if events_out is not None:
                            events_out.append(ev_pos)
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
                            if events_out is not None:
                                events_out.append(ev_pos)
                            raise DecodeError("invalid tcoef")
                        if end - br.pos < skip:
                            raise DecodeError("EOF tcoef")
                        if events_out is not None:
                            events_out.append(ev_pos)
                        run = ((sym2 >> 8) & 0xFF) - 1
                        last = bool((sym2 >> 16) & 1)
                        br.skip(skip)
                if coef_num + run > 63:
                    raise DecodeError("overflow")
                coef_num += run + 1
        n_mbs += 1
    if br.pos != end:
        raise DecodeError("misland")
    return n_mbs


def window_at(frame, pos, n=20):
    total = len(frame) * 8
    return "".join(str((frame[b // 8] >> (7 - b % 8)) & 1)
                   if b < total else "0" for b in range(pos, pos + n))


VARIANTS = {
    "none": {},
    # 14-bit code `0000011?000000`+1 trailing bit, LAST=1 (solver votes)
    "last1": {"0000011000000": (14, 1, 0)},
    # ESC-like: 8th bit is the LAST flag, RUN field zero, no level field
    "escL": {"00000110000000": (14, 0, 0), "00000110000001": (14, 0, 0),
             "00000111000000": (14, 1, 0), "00000111000001": (14, 1, 0)},
}


def main():
    global COMMIT
    if len(sys.argv) > 3:
        COMMIT = VARIANTS[sys.argv[3]]
        print(f"committed variant: {sys.argv[3]} -> {COMMIT}")
    max_gap = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    min_gap = int(sys.argv[2]) if len(sys.argv) > 2 else 53
    cat = json.load(open("mb_catalog.json"))
    gaps = [g for g in cat["gaps"]
            if not g["stock_ok"] and min_gap <= g["end"] - g["start"] <= max_gap]
    print(f"{len(gaps)} failing gaps of size [{min_gap}, {max_gap}]")

    frames_cache = {}

    def frame_of(i):
        if i not in frames_cache:
            off = PSCS[i]
            end = PSCS[i + 1] if i + 1 < len(PSCS) else len(RAW)
            frames_cache[i] = RAW[off:end]
            if len(frames_cache) > 4:
                frames_cache.pop(next(iter(frames_cache)))
        return frames_cache[i]

    n_unique = n_multi = n_none = n_fit = 0
    votes = defaultdict(Counter)        # window13 -> (skip,last) -> count
    unique_records = []
    for g in gaps:
        frame = frame_of(g["frame"])
        start, end = g["start"], g["end"]
        events = []
        try:
            parse_gap(frame, start, end, events_out=events)
            n_fit += 1      # exact fit under COMMIT alone
            continue
        except DecodeError:
            pass
        sols = []
        for p in events:
            for skip in range(8, 21):
                for lst in (0, 1):
                    try:
                        n_mbs = parse_gap(frame, start, end,
                                          override=(p, skip, lst))
                    except DecodeError:
                        continue
                    sols.append((p, skip, lst, n_mbs))
        if not sols:
            n_none += 1
            continue
        positions = {s[0] for s in sols}
        if len(sols) == 1:
            n_unique += 1
            p, skip, lst, n_mbs = sols[0]
            w = window_at(frame, p)
            votes[w[:13]][(skip, lst)] += 1
            unique_records.append({"frame": g["frame"], "bit": p,
                                   "win": w, "skip": skip, "last": lst,
                                   "mbs": n_mbs,
                                   "gap": [start, end]})
        else:
            n_multi += 1
    print(f"exact-fit under COMMIT alone: {n_fit}; "
          f"unique-solution gaps: {n_unique}; multi: {n_multi}; "
          f"unsolved (k>=2 or boundary error): {n_none}")

    with open("ext_solver_unique.json", "w") as f:
        json.dump(unique_records, f, indent=1)

    print("\nvotes per 13-bit window (windows with >=3 votes):")
    for w, c in sorted(votes.items()):
        tot = sum(c.values())
        if tot < 3:
            continue
        top = c.most_common(3)
        print(f"  {w} (n={tot}): " +
              ", ".join(f"skip={s} last={l} x{n}" for (s, l), n in top))

    # deep-zero focus: aggregate by window value < 16
    print("\ndeep-zero (9+ leading zeros) votes:")
    agg = defaultdict(Counter)
    for r in unique_records:
        if r["win"][:9] == "0" * 9:
            wv = int(r["win"][:13], 2)
            agg[wv][(r["skip"], r["last"])] += 1
    for wv in sorted(agg):
        c = agg[wv]
        tot = sum(c.values())
        top = ", ".join(f"skip={s} last={l} x{n}"
                        for (s, l), n in c.most_common(4))
        print(f"  window {wv:2d} ({wv:013b}, n={tot}): {top}")


if __name__ == "__main__":
    main()
