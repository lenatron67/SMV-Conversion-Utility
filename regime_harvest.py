"""
Session 10 -- known-plaintext harvest at scale (session-10 action #1).

Ledger #35 confirmed the two-syntax re-encode model: an MB that changed vs
the previous frame is proprietary-coded; an unchanged MB is re-encoded in
STANDARD syntax; identical content produces identical bits. Therefore, when
a fresh region finally holds still, its first standard re-encoding decodes
exactly the coefficients its LAST proprietary payload encodes -- a known-
plaintext pair. regime_tension.py harvested 4 pairs looking only 1-2 frames
ahead with fixed frame-i flanks; this script scales that up:

Per failing catalog gap (frame i, span [s,e)):
  - Track the region forward frame-by-frame (up to MAX_DEPTH frames):
    locate the current flanking bitstrings in the next frame (exact
    substring match; flank lengths tried longest-first, one-sided fallback
    with an expected-span-length proximity prior when only one flank is
    unique), giving the same grid MB's next encoding.
  - If that span stock-parses AND lands exactly: HARVEST the pair
    (last fresh payload = previous frame's span; standard = this span),
    with a corroboration flag (standard bits recur in the frame after --
    content stayed static) and the standard decode's DC values. Stop.
  - Else the region is still fresh: adopt the new span as payload and
    RE-TAKE the flanks from the current frame (adaptive tracking -- the
    surroundings may drift), continue.

Boundary safety: flanks are exact >=53-bit substrings (chance collision
2^-53); ambiguous flanks (multiple occurrences, e.g. repeating flat
texture) stop the track unless the other flank + length prior resolves a
UNIQUE candidate. A mislocated span would almost surely fail the exact-
landing stock parse, so harvested pairs are self-validating.

Model check en route: span bits identical to the previous frame's fresh
payload at ADJACENT frames would violate #35 (static => standard); counted
separately if ever seen.

Usage: python regime_harvest.py [max_gap_bits]   (default 300)
Writes regime_harvest.json (pairs incl. payload/std bitstrings).
"""
import json
import sys
from collections import Counter

from bitreader import BitReader
from i263_decoder import DecodeError, decode_mb
from mb_catalog import PSCS, get_frame, frame_bitstr, parse_one_mb

FLANK = 160
FLANK_STEPS = (160, 120, 100, 80, 64, 53)
MAX_DEPTH = 30
MAX_SPAN = 400
MAX_SPAN_MBS = 6
PROX_TOL = 60


def parse_span(frame_bytes, start, end):
    g, n, pos = 16, 0, start
    while pos < end:
        pos, g, _ = parse_one_mb(frame_bytes, pos, g)
        n += 1
        if n > MAX_SPAN_MBS:
            raise DecodeError("too many MBs")
    if pos != end:
        raise DecodeError("misland")
    return n


def span_dcs(frame_bytes, start, end):
    br = BitReader(frame_bytes, start)
    out, g = [], 16
    while br.pos < end:
        idc, g = decode_mb(br, g)
        out.append(idc)
    return out


def occurrences(body, sub, cap=16):
    out, st = [], 0
    while len(out) < cap:
        p = body.find(sub, st)
        if p < 0:
            break
        out.append(p)
        st = p + 1
    return out


def find_unique(body, s, kind):
    """Unique occurrence of a suffix (left flank) / prefix (right flank)
    of s. Returns (pos, used_len, status); pos = match start in body.
    Longer-first; count>=2 is terminal (shorter can only be more common)."""
    for L in FLANK_STEPS:
        if L > len(s):
            continue
        sub = s[-L:] if kind == "suffix" else s[:L]
        c = body.count(sub)
        if c == 1:
            return body.index(sub), L, "ok"
        if c >= 2:
            return -1, L, "ambiguous"
    return -1, 0, "absent"


def flank_candidates(body, s, kind):
    """Longest flank sub-string that occurs at all, with its occurrences."""
    for L in FLANK_STEPS:
        if L > len(s):
            continue
        sub = s[-L:] if kind == "suffix" else s[:L]
        occ = occurrences(body, sub)
        if occ:
            return occ, L
    return [], 0


def track(gap, counters):
    """Follow one gap region forward; return (pair_record or None, outcome,
    depth). Outcome in harvest/flank_lost/reordered/vanished/size_out/
    depth_exhausted/eof."""
    i, s, e = gap["frame"], gap["start"], gap["end"]
    sa, ha = frame_bitstr(i)
    payload = sa[s:e]
    pf, ps, pe = i, s, e                     # previous span (frame, start, end)
    f1 = sa[max(ha, s - FLANK):s]
    f2 = sa[e:e + FLANK]
    for depth in range(1, MAX_DEPTH + 1):
        j = pf + 1
        if j >= len(PSCS):
            return None, "eof", depth
        sb, hb = frame_bitstr(j)
        body = sb[hb:]
        p1, L1, st1 = find_unique(body, f1, "suffix")
        p2, L2, st2 = find_unique(body, f2, "prefix")
        Lprev = len(payload)
        if st1 == "ok" and st2 == "ok":
            bs, be = p1 + L1, p2
        elif st1 == "ok":
            occ, L2c = flank_candidates(body, f2, "prefix")
            exp = p1 + L1 + Lprev
            good = [q for q in occ if q >= p1 + L1 and abs(q - exp) <= PROX_TOL]
            if len(good) != 1:
                return None, "flank_lost", depth
            bs, be = p1 + L1, good[0]
        elif st2 == "ok":
            occ, L1c = flank_candidates(body, f1, "suffix")
            exp = p2 - Lprev
            good = [q for q in occ if q + L1c <= p2
                    and abs(q + L1c - exp) <= PROX_TOL]
            if len(good) != 1:
                return None, "flank_lost", depth
            bs, be = good[0] + L1c, p2
        else:
            return None, "flank_lost", depth
        if be < bs:
            return None, "reordered", depth
        L = be - bs
        if L == 0:
            return None, "vanished", depth
        if L < 53 or L > MAX_SPAN:
            return None, "size_out", depth
        ss, se = hb + bs, hb + be
        span_bits = sb[ss:se]
        frame_j, _ = get_frame(j)
        try:
            n_mbs = parse_span(frame_j, ss, se)
        except DecodeError:
            n_mbs = None
        if n_mbs is not None:                # HARVEST
            corrob = False
            if j + 1 < len(PSCS):
                s2, h2 = frame_bitstr(j + 1)
                corrob = span_bits in s2[h2:]
            try:
                dcs = span_dcs(frame_j, ss, se)
            except DecodeError:
                dcs = None
            return ({"gap_frame": i, "gap_start": s, "gap_end": e,
                     "tier": gap["tier"], "depth": depth,
                     "fresh_frame": pf, "fresh_start": ps, "fresh_end": pe,
                     "fresh_bits": payload,
                     "std_frame": j, "std_start": ss, "std_end": se,
                     "std_bits": span_bits, "std_mbs": n_mbs,
                     "std_dcs": dcs, "corroborated": corrob,
                     "lendiff": len(payload) - L},
                    "harvest", depth)
        if span_bits == payload:
            counters["adjacent_identical_MODEL_VIOLATION"] += 1
        payload = span_bits
        pf, ps, pe = j, ss, se
        f1 = sb[max(hb, ss - FLANK):ss]
        f2 = sb[se:se + FLANK]
    return None, "depth_exhausted", MAX_DEPTH


def main():
    max_bits = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    cat = json.load(open("clean_catalog.json"))
    gaps = [g for g in cat["gaps"]
            if not g["stock_ok"] and 53 <= g["end"] - g["start"] <= max_bits]
    print(f"{len(gaps)} failing gaps of 53..{max_bits} bits "
          f"(tiers: {dict(Counter(g['tier'] for g in gaps))})")

    outcomes = Counter()
    counters = Counter()
    depth_hist = Counter()
    pairs = []
    for gi, g in enumerate(gaps):
        rec, outcome, depth = track(g, counters)
        outcomes[outcome] += 1
        if rec:
            depth_hist[depth] += 1
            pairs.append(rec)
        if gi and gi % 200 == 0:
            print(f"  ...{gi}/{len(gaps)}, {len(pairs)} pairs", flush=True)

    # dedupe pairs that stabilized onto the same standard span (adjacent
    # per-frame gaps of the same moving region converge)
    seen = {}
    for p in pairs:
        key = (p["std_frame"], p["std_start"], p["std_end"])
        if key not in seen or p["depth"] < seen[key]["depth"]:
            seen[key] = p
    uniq = sorted(seen.values(),
                  key=lambda p: (p["std_frame"], p["std_start"]))

    print(f"\noutcomes: {dict(sorted(outcomes.items()))}")
    if counters:
        print(f"counters: {dict(counters)}")
    print(f"pairs harvested: {len(pairs)} raw, {len(uniq)} unique "
          f"({sum(1 for p in uniq if p['corroborated'])} corroborated)")
    print(f"harvest depth histogram: {dict(sorted(depth_hist.items()))}")
    print(f"std_mbs histogram: "
          f"{dict(sorted(Counter(p['std_mbs'] for p in uniq).items()))}")
    print(f"lendiff (fresh - std) histogram: "
          f"{dict(sorted(Counter(p['lendiff'] for p in uniq).items()))}")

    json.dump({"max_gap_bits": max_bits, "n_gaps": len(gaps),
               "outcomes": dict(outcomes), "counters": dict(counters),
               "pairs": uniq},
              open("regime_harvest.json", "w"), indent=1)
    print("\nwritten regime_harvest.json")


if __name__ == "__main__":
    main()
