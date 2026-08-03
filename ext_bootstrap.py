"""
Session 6 — iterative cross-gap bootstrap for the proprietary TCOEF extension.

Background (read VLC_REVERSE_ENGINEERING.md sessions 4-5 first):
  The proprietary table = standard TABLE 13 + a small set of EXTENSION codes
  that live in the deep-zero prefix space (>=5 leading zeros). A stock parse
  silently mis-consumes these, drifting and failing. The MB catalog
  (mb_catalog.json) gives us GAPS = spans of complete MBs with EXACT start/end
  bits (pinned by cross-frame-alignment anchors). A correct extension table
  must let every gap parse and land EXACTLY on its end bit.

Why iterate instead of a single big DFS (ext_solver.py was single-override):
  Most failing gaps contain k>=2 extension events, so no single override fits.
  But an extension code is a SHARED table entry: the same deep-zero bit-prefix
  must map to the same (skip, last) in every gap it appears in. So:

    1. With the current COMMIT table applied everywhere, find gaps that now
       need exactly ONE more override to land exactly. Each such gap casts a
       vote: prefix(window, skip) -> (skip, last).
    2. Commit the highest cross-gap-support assignment (clear margin required).
    3. Re-parse: gaps that needed {committed_code, X} now need only X -> new
       single-override votes. Repeat.

  k>=2 is thus handled by discovering codes one at a time, globally. run is
  irrelevant to landing (deferred to M4 pixel domain); overrides use run=0
  (permissive: never triggers a false 64-coef overflow).

Validation each round: (a) #gaps that fit under COMMIT alone must rise;
  (b) the 15-sample baseline (i263_decoder.decode_frame) must not regress.

Usage:
  python ext_bootstrap.py [max_gap_bits] [catalog.json]
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

SKIP_RANGE = range(8, 21)        # candidate extension code lengths
MIN_LEADING_ZEROS = 5           # only deep-zero windows are override candidates
MIN_CFIT = 2                    # a real SHARED code lands >1 gap; reject +1 ghosts


def window_at(frame, pos, n=24):
    total = len(frame) * 8
    return "".join(str((frame[b // 8] >> (7 - b % 8)) & 1)
                   if b < total else "0" for b in range(pos, pos + n))


def leading_zeros(w):
    i = 0
    while i < len(w) and w[i] == "0":
        i += 1
    return i


def parse_gap(frame, start, end, commit, override=None, events_out=None):
    """Parse complete MBs in [start, end). commit: prefix-string -> (skip,last).
    override = (bit, skip, last) applied once at that event position.
    Returns n_mbs if it lands EXACTLY on end with complete MBs, else raises.
    If events_out is given, appends the bit position of every *deep-zero*
    TCOEF event that was parsed with stock tables (override candidates)."""
    br = BitReader(frame, start)
    g = 16
    n_mbs = 0
    while br.pos < end:
        # --- MCBPC (with stuffing loop) ---
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
                w = window_at(frame, ev_pos)
                # committed extension code?
                hit = None
                for skip in SKIP_RANGE:
                    pref = w[:skip]
                    ent = commit.get(pref)
                    if ent is not None:
                        hit = ent
                        break
                if override is not None and ev_pos == override[0]:
                    _, o_skip, o_last = override
                    if end - br.pos < o_skip:
                        raise DecodeError("override past end")
                    br.skip(o_skip)
                    last = bool(o_last)
                    run = 0
                elif hit is not None:
                    c_skip, c_last = hit
                    if end - br.pos < c_skip:
                        raise DecodeError("commit past end")
                    br.skip(c_skip)
                    last = bool(c_last)
                    run = 0
                else:
                    vlc = peek_safe(br, 13)
                    sym2 = VLC_TAB5[vlc >> 5]
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
                            sym2 = VLC_TAB6[vlc]
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
                        if events_out is not None and leading_zeros(w) >= MIN_LEADING_ZEROS:
                            events_out.append(ev_pos)
                if coef_num + run > 63:
                    raise DecodeError("overflow")
                coef_num += run + 1
        n_mbs += 1
    if br.pos != end:
        raise DecodeError("misland")
    return n_mbs


def load_gaps(path, max_gap):
    cat = json.load(open(path))
    return [g for g in cat["gaps"]
            if not g["stock_ok"] and 53 <= g["end"] - g["start"] <= max_gap]


def frame_loader():
    cache = {}

    def frame_of(i):
        if i not in cache:
            off = PSCS[i]
            end = PSCS[i + 1] if i + 1 < len(PSCS) else len(RAW)
            cache[i] = RAW[off:end]
            if len(cache) > 6:
                cache.pop(next(iter(cache)))
        return cache[i]
    return frame_of


def gap_fits(g, frame_of, commit, extra=None):
    """Does this gap parse-and-land exactly under commit (+ one extra code)?"""
    c = commit if extra is None else {**commit, extra[0]: extra[1]}
    try:
        parse_gap(frame_of(g["frame"]), g["start"], g["end"], c)
        return True
    except DecodeError:
        return False


def round_pass(gaps, frame_of, commit):
    """One pass over all gaps under the current commit table.
    Returns (n_fit, votes, failing) where votes: (prefix,last) -> support and
    failing = list of gaps that did NOT fit (candidate-trial corpus)."""
    n_fit = 0
    votes = Counter()
    failing = []
    for g in gaps:
        frame = frame_of(g["frame"])
        start, end = g["start"], g["end"]
        events = []
        try:
            parse_gap(frame, start, end, commit, events_out=events)
            n_fit += 1
            continue
        except DecodeError:
            failing.append(g)
        # single additional override search over deep-zero candidates
        sols = []
        for p in events:
            w = window_at(frame, p)
            for skip in SKIP_RANGE:
                pref = w[:skip]
                if pref in commit:          # already explained by commit
                    continue
                for lst in (0, 1):
                    try:
                        parse_gap(frame, start, end, commit,
                                  override=(p, skip, lst))
                    except DecodeError:
                        continue
                    sols.append((pref, lst))
        # vote: if all solutions agree on a single (prefix,last), strong vote;
        # otherwise vote the intersection (assignments common to *every* sol)
        if not sols:
            continue
        uniq = set(sols)
        if len(uniq) == 1:
            votes[next(iter(uniq))] += 2        # unanimous, weight 2
        else:
            # weak votes: each distinct candidate gets fractional credit
            for cand in uniq:
                votes[cand] += 1.0 / len(uniq)
    return n_fit, votes, failing


def baseline_check(commit):
    """Decode the 15 sample frames with the committed extension applied.
    Returns dict file -> MBs decoded before failure (99 = full)."""
    from pathlib import Path
    from bitreader import decode_picture_header
    samples = Path("samples")
    manifest = json.loads((samples / "manifest.json").read_text())
    out = {}
    for entry in manifest["frames"]:
        data = (samples / entry["file"]).read_bytes()
        hdr = decode_picture_header(data, 0)
        # parse whole frame as one big "gap" [header_end, total) but we only
        # care how many MBs decode; reuse parse_gap with end=total and catch.
        total = len(data) * 8
        try:
            n = parse_gap_count(data, hdr["header_end_bit"], total, commit)
        except DecodeError:
            n = -1
        out[entry["file"]] = n
    return out


def parse_gap_count(frame, start, end, commit):
    """Like parse_gap but for a whole frame: decode MBs until failure or
    99 MBs; tolerant of trailing stuffing. Returns #MBs decoded."""
    br = BitReader(frame, start)
    g = 16
    n_mbs = 0
    while n_mbs < 99:
        try:
            while True:
                vlc = peek_safe(br, 6)
                sym = MCBPC_INTRA[vlc]
                br.skip(sym & 0xFF)
                if vlc == 0:
                    if br.bits_remaining() <= 0:
                        return n_mbs
                    continue
                break
            mb_type = (sym >> 10) & 7
            cbpc = (sym >> 8) & 3
            if mb_type not in (MB_INTRA, MB_INTRA_Q):
                return n_mbs
            sym = CBPY_TAB[peek_safe(br, 6)]
            if sym == 0:
                return n_mbs
            br.skip(sym & 0xFF)
            cbpy = (sym >> 12) & 0xF
            if mb_type == MB_INTRA_Q:
                g += DQUANT_DIFF[br.read(2)]
                if not (1 <= g <= 31):
                    return n_mbs
            cbp = (cbpy << 2) | cbpc
            for b in range(6):
                cbp += cbp
                if br.bits_remaining() < 8:
                    return n_mbs
                v = br.read(8)
                if v in (0x00, 0x80):
                    return n_mbs
                if not (cbp & 64):
                    continue
                coef_num = 1
                last = False
                while coef_num < 64 and not last:
                    ev_pos = br.pos
                    w = window_at(frame, ev_pos)
                    hit = None
                    for skip in SKIP_RANGE:
                        ent = commit.get(w[:skip])
                        if ent is not None:
                            hit = ent
                            break
                    if hit is not None:
                        c_skip, c_last = hit
                        br.skip(c_skip)
                        last = bool(c_last)
                        run = 0
                    else:
                        vlc = peek_safe(br, 13)
                        sym2 = VLC_TAB5[vlc >> 5]
                        if sym2 == 1:
                            if br.bits_remaining() < 22:
                                return n_mbs
                            br.skip(7)
                            last = bool(br.read(1))
                            run = br.read(6)
                            level = br.read(8)
                            if level in (0x00, 0x80):
                                return n_mbs
                        else:
                            if (sym2 & 1) and (sym2 >> 1):
                                sym2 = VLC_TAB6[vlc]
                            else:
                                sym2 >>= 1
                            skip = (sym2 >> 17) & 0x1F
                            if sym2 == 0 or skip == 0:
                                return n_mbs
                            run = ((sym2 >> 8) & 0xFF) - 1
                            last = bool((sym2 >> 16) & 1)
                            br.skip(skip)
                    if coef_num + run > 63:
                        return n_mbs
                    coef_num += run + 1
            n_mbs += 1
        except (IndexError, DecodeError):
            return n_mbs
    return n_mbs


def main():
    max_gap = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    cat_path = sys.argv[2] if len(sys.argv) > 2 else "mb_catalog.json"
    gaps = load_gaps(cat_path, max_gap)
    frame_of = frame_loader()
    print(f"{len(gaps)} failing gaps of size [53,{max_gap}] from {cat_path}")

    commit = {}
    base0 = baseline_check(commit)
    print("baseline (stock) 15-sample MBs:",
          " ".join(str(v) for v in base0.values()),
          f"sum={sum(base0.values())}")

    TOPK = 12          # vote candidates to trial against the oracles
    for rnd in range(1, 40):
        n_fit, votes, failing = round_pass(gaps, frame_of, commit)
        print(f"\n=== round {rnd}: {n_fit}/{len(gaps)} gaps fit under "
              f"COMMIT ({len(commit)} codes); baseline sum "
              f"{sum(base0.values())} ===")
        if not votes:
            print("no votes; stopping")
            break
        # Votes only PROPOSE. Two independent oracles ARBITRATE:
        #   (1) corpus-fit gain  = #currently-failing gaps that newly land
        #       EXACTLY when this one code is added (broad; the real signal of
        #       a *shared* table entry);
        #   (2) 15-sample no-regression guard (full frames, catalog-independent).
        # Commit the candidate with the largest corpus-fit gain that does not
        # regress the sample baseline.
        best = None             # (cfit, pref, lst, base)
        for (pref, lst), s in votes.most_common(TOPK):
            extra = (pref, (len(pref), lst))
            cfit = sum(1 for g in failing
                       if gap_fits(g, frame_of, commit, extra))
            cand = {**commit, pref: (len(pref), lst)}
            base = baseline_check(cand)
            regressed = any(base[f] < base0[f] for f in base)
            bgain = sum(base.values()) - sum(base0.values())
            tag = "REGRESS" if regressed else f"corpus+{cfit} base{bgain:+d}"
            print(f"   try {pref:<22} last={lst} support={s:4.1f} -> {tag}")
            if regressed or cfit < MIN_CFIT:
                continue
            if best is None or cfit > best[0]:
                best = (cfit, pref, lst, base)
        if best is None:
            print(f"   no candidate lands >={MIN_CFIT} gaps without "
                  f"regressing; stopping")
            break
        cfit, pref, lst, base = best
        commit[pref] = (len(pref), lst)
        base0 = base
        print(f"   COMMIT {pref} (skip={len(pref)}) last={lst}  "
              f"corpus+{cfit}  baseline -> {sum(base.values())}")

    print(f"\nFinal COMMIT table ({len(commit)} codes):")
    for pref, (skip, lst) in commit.items():
        print(f"   {pref}  skip={skip} last={lst}")
    json.dump({k: list(v) for k, v in commit.items()},
              open("ext_commit.json", "w"), indent=1)
    print("written ext_commit.json")


if __name__ == "__main__":
    main()
