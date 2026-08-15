"""
Session 9 -- regime-tension test (session-10 action #3, ledger #31/#34).

The regime-split model (ledger #31) says: static MBs are bit-copies of
previously-encoded MBs and parse standard; freshly-encoded (changed) MBs use
proprietary coding. Tension: every static MB was fresh ONCE. If its fresh
encoding was proprietary and static frames merely copy those bits, then
anchored (standard-parsing) static MBs could not exist -- unless either
(a) the encoder RE-ENCODES the MB with standard syntax once it goes static
    (two-syntax model), or
(b) "static => standard" was selection bias: the anchor machinery only ever
    certified matched segments that ALSO stock-parse, so bit-identical
    static copies of PROPRIETARY payloads would be invisible to it.

Test, per clean failing gap (frame i, span [s,e), the fresh MB payload):

  1. PERSISTENCE: does the exact payload bitstring recur in frames i+1..i+D
     (and, sanity, i-1..i-B -- ledger #31's census says fresh payloads are
     absent from neighbours)? An exact >=53-bit recurrence cannot be chance
     (2^-53): forward recurrence = static-but-proprietary copies exist (b).

  2. NEXT-FRAME RE-ENCODING: locate the gap's flanking anchor bitstrings in
     frame i+1 (and i+2); the span between them is the SAME grid MB's next
     encoding. Classify: identical to payload (copied -> (b)); stock-parses
     and lands (re-encoded standard -> (a) CONFIRMED, and the pair
     (proprietary payload, standard re-encoding of the same now-static
     content) is a KNOWN-PLAINTEXT candidate -- the standard decode yields
     the coefficients the proprietary payload encodes); parse-fails
     (content still changing OR a different proprietary encoding --
     uninformative here).

Either decisive outcome restructures the model; outcome (a) additionally
opens a known-plaintext attack on the proprietary table.

Usage: python regime_tension.py [max_gap_bits]   (default 200)
Writes regime_tension_results.json (incl. the plaintext-pair harvest).
"""
import json
import sys
from collections import Counter

from bitreader import BitReader
from i263_decoder import DecodeError, decode_mb
from mb_catalog import PSCS, get_frame, frame_bitstr, parse_one_mb

FWD_D = 10        # persistence look-ahead frames
BWD_D = 5         # backward sanity frames
FLANK = 160       # max flank bits used as locator string
MAX_SPAN = 260    # ignore next-frame spans longer than this
MAX_SPAN_MBS = 4


def parse_span(frame_bytes, start_bit, end_bit):
    """Stock-parse [start,end) as complete MBs; return count or raise."""
    g, n, pos = 16, 0, start_bit
    while pos < end_bit:
        pos, g, _ = parse_one_mb(frame_bytes, pos, g)
        n += 1
        if n > MAX_SPAN_MBS:
            raise DecodeError("too many MBs")
    if pos != end_bit:
        raise DecodeError("misland")
    return n


def span_dcs(frame_bytes, start_bit, end_bit):
    """Decode the span's MBs fully; return list of 6-DC lists."""
    br = BitReader(frame_bytes, start_bit)
    out = []
    g = 16
    while br.pos < end_bit:
        idc, g = decode_mb(br, g)
        out.append(idc)
    return out


def main():
    max_bits = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    cat = json.load(open("clean_catalog.json"))
    run_by_end = {(r["frame"], r["end"]): r for r in cat["runs"]}
    run_by_start = {(r["frame"], r["start"]): r for r in cat["runs"]}
    gaps = [g for g in cat["gaps"]
            if not g["stock_ok"] and 53 <= g["end"] - g["start"] <= max_bits]
    print(f"{len(gaps)} failing gaps of 53..{max_bits} bits "
          f"(tiers: {dict(Counter(g['tier'] for g in gaps))})")

    persist_hist = Counter()          # consecutive forward recurrence depth
    fwd_any = bwd_any = 0
    cls_by_dj = {1: Counter(), 2: Counter()}
    pairs = []
    lendiff = Counter()
    for gi, g in enumerate(gaps):
        i, s, e = g["frame"], g["start"], g["end"]
        sa, ha = frame_bitstr(i)
        payload = sa[s:e]

        # ---- 1. exact-payload persistence ----
        depth = 0
        anyf = False
        for d in range(1, FWD_D + 1):
            j = i + d
            if j >= len(PSCS):
                break
            sb, hb = frame_bitstr(j)
            if payload in sb[hb:]:
                anyf = True
                if depth == d - 1:
                    depth = d
        persist_hist[depth] += 1
        fwd_any += anyf
        for d in range(1, BWD_D + 1):
            j = i - d
            if j < 0:
                break
            sb, hb = frame_bitstr(j)
            if payload in sb[hb:]:
                bwd_any += 1
                break

        # ---- 2. locate the same grid MB's encoding in frames i+1, i+2 ----
        r1 = run_by_end.get((i, s))
        r2 = run_by_start.get((i, e))
        if not r1 or not r2:
            continue
        f1 = sa[max(r1["start"], s - FLANK):s]
        f2 = sa[e:min(r2["end"], e + FLANK)]
        for dj in (1, 2):
            j = i + dj
            if j >= len(PSCS):
                continue
            sb, hb = frame_bitstr(j)
            body = sb[hb:]
            if body.count(f1) != 1 or body.count(f2) != 1:
                cls_by_dj[dj]["flanks_not_unique"] += 1
                continue
            p1 = hb + body.index(f1) + len(f1)
            p2 = hb + body.index(f2)
            if p2 < p1:
                cls_by_dj[dj]["flanks_reordered"] += 1
                continue
            span = sb[p1:p2]
            L = len(span)
            if L == 0:
                cls_by_dj[dj]["vanished"] += 1
                continue
            if span == payload:
                cls_by_dj[dj]["copied_identical"] += 1
                continue
            if L < 53 or L > MAX_SPAN:
                cls_by_dj[dj]["span_size_out"] += 1
                continue
            frame_j, _ = get_frame(j)
            try:
                n_mbs = parse_span(frame_j, p1, p2)
            except DecodeError:
                cls_by_dj[dj]["parse_fail"] += 1
                continue
            cls_by_dj[dj]["restd_parses"] += 1
            if dj == 1:
                lendiff[(e - s) - L] += 1
            try:
                dcs = span_dcs(frame_j, p1, p2)
            except DecodeError:
                dcs = None
            pairs.append({"gap_frame": i, "gap_start": s, "gap_end": e,
                          "gap_bits": e - s, "tier": g["tier"], "dj": dj,
                          "std_frame": j, "std_start": p1, "std_end": p2,
                          "std_bits": L, "std_mbs": n_mbs, "std_dcs": dcs})
        if gi and gi % 200 == 0:
            print(f"  ...{gi}/{len(gaps)}", flush=True)

    n = len(gaps)
    print(f"\n1. payload persistence (exact bitstring recurrence):")
    print(f"   forward (any of next {FWD_D} frames): {fwd_any}/{n} "
          f"({fwd_any / n:.1%})")
    print(f"   consecutive-depth histogram: {dict(sorted(persist_hist.items()))}")
    print(f"   backward (any of prev {BWD_D} frames): {bwd_any}/{n} "
          f"({bwd_any / n:.1%})  [#31 sanity: should be ~0]")

    for dj in (1, 2):
        c = cls_by_dj[dj]
        print(f"\n2. same-grid-MB span in frame i+{dj}: "
              f"{dict(sorted(c.items()))}")

    n_pairs = len(pairs)
    n1 = sum(1 for p in pairs if p["dj"] == 1)
    print(f"\nknown-plaintext candidate pairs harvested: {n_pairs} "
          f"({n1} at i+1, {n_pairs - n1} at i+2)")
    if lendiff:
        print(f"payload-minus-standard length diff (i+1 pairs): "
              f"{dict(sorted(lendiff.items()))}")

    json.dump({"max_gap_bits": max_bits, "n_gaps": n,
               "fwd_any": fwd_any, "bwd_any": bwd_any,
               "persist_hist": {str(k): v
                                for k, v in sorted(persist_hist.items())},
               "classify_dj1": dict(cls_by_dj[1]),
               "classify_dj2": dict(cls_by_dj[2]),
               "lendiff_dj1": {str(k): v for k, v in sorted(lendiff.items())},
               "pairs": pairs},
              open("regime_tension_results.json", "w"), indent=1)
    print("\nwritten regime_tension_results.json")


if __name__ == "__main__":
    main()
