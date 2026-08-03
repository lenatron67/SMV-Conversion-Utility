"""
Session 10 -- catalog-based known-plaintext harvest ("first stasis" events).

Supersedes regime_harvest.py's substring tracking (94% flank_lost, 9 pairs,
none corroborated). Instead of following fresh regions forward, this walks
the validated catalog directly:

A validated anchored run R at frame j whose exact bits do NOT occur in
frame j-1 is a FIRST-STASIS event: R anchored, so it matched an adjacent
frame; not j-1, hence j+1 -- i.e. its standard bits provably persist to
j+1 (static >=2 frames, corroborated BY CONSTRUCTION), and at j-1 the same
grid MBs' content was already present but freshly (proprietary-)encoded
(content changed at j-1, held still at j: the first standard frame).

v1 required R to be flanked by persistent runs CONTIGUOUSLY and harvested
ZERO pairs: a region that stabilizes next to already-static content is
ABSORBED into one longer catalog run, and stand-alone stasis runs are
flanked by still-fresh gaps. v2 therefore splits first-stasis runs
INTERNALLY: using the run's own per-MB boundaries, find the longest
prefix and suffix of WHOLE MBs whose bits occur (uniquely) in frame j-1 --
the persistent head/tail -- leaving middle MBs that are the newly
stabilized content. The span between head-end and tail-start at their j-1
positions is exactly the fresh (proprietary) encoding of those middle MBs:

  frame j-1: [ head bits ][ FRESH payload (proprietary) ][ tail bits ]
  frame j:   [ head bits ][ MIDDLE = standard re-encode ][ tail bits ]

Under the #35 two-syntax model (re-encode cached coefficients on stasis),
(fresh payload, middle MBs) is exact known plaintext: the standard decode
of the middle MBs yields the coefficients the fresh payload encodes.

Sanity per pair: the fresh payload should FAIL stock parse (it was
proprietary); pairs whose fresh side parses-and-lands are counted
separately and excluded.

Usage: python stasis_harvest.py
Writes stasis_harvest.json.
"""
import json
from collections import Counter, defaultdict

from bitreader import BitReader
from i263_decoder import DecodeError, decode_mb
from mb_catalog import PSCS, get_frame, frame_bitstr, parse_one_mb

MAX_FRESH = 500
MIN_FRESH = 40


def parse_span(frame_bytes, start, end, max_mbs=8):
    g, n, pos = 16, 0, start
    while pos < end:
        pos, g, _ = parse_one_mb(frame_bytes, pos, g)
        n += 1
        if n > max_mbs:
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


def main():
    cat = json.load(open("clean_catalog.json"))
    runs_by_frame = defaultdict(list)
    for r in cat["runs"]:
        runs_by_frame[r["frame"]].append(r)
    for f in runs_by_frame:
        runs_by_frame[f].sort(key=lambda r: r["start"])

    stats = Counter()
    pairs = []
    frames = sorted(runs_by_frame)
    for fi, j in enumerate(frames):
        if j == 0:
            continue
        runs_j = runs_by_frame[j]
        sa, ha = frame_bitstr(j)
        sbm, hbm = frame_bitstr(j - 1)
        bodym = sbm[hbm:]

        for R in runs_j:
            B = sa[R["start"]:R["end"]]
            c = bodym.count(B)
            if c == 1:
                continue                       # fully persistent
            if c >= 2:
                stats["run_ambiguous_in_prev"] += 1
                continue
            stats["first_stasis_runs"] += 1
            mbs = R["mbs"]
            n = len(mbs)
            if n < 3:
                stats["run_too_short_to_split"] += 1
                continue
            # longest whole-MB prefix present uniquely in j-1
            k_head, p_head = 0, None
            for k in range(n - 2, 0, -1):      # leave >=1 middle + tail MB
                sub = sa[R["start"]:mbs[k - 1]["end"]]
                cc = bodym.count(sub)
                if cc == 1:
                    k_head, p_head = k, hbm + bodym.index(sub)
                    break
                if cc >= 2:
                    break                      # shorter is never more unique
            # longest whole-MB suffix present uniquely in j-1
            k_tail, p_tail = 0, None
            for k in range(n - 2, 0, -1):
                sub = sa[mbs[n - k]["start"]:R["end"]]
                cc = bodym.count(sub)
                if cc == 1:
                    k_tail, p_tail = k, hbm + bodym.index(sub)
                    break
                if cc >= 2:
                    break
            if not k_head or not k_tail:
                stats["no_persistent_head_or_tail"] += 1
                continue
            if k_head + k_tail >= n:
                stats["head_tail_overlap"] += 1
                continue
            m = n - k_head - k_tail            # newly stabilized middle MBs
            head_len = mbs[k_head - 1]["end"] - R["start"]
            fs = p_head + head_len             # fresh span start in j-1
            fe = p_tail                        # fresh span end in j-1
            if fe < fs:
                stats["reordered"] += 1
                continue
            L = fe - fs
            if not (MIN_FRESH <= L <= min(MAX_FRESH * m, 2000)):
                stats["fresh_size_out"] += 1
                continue
            frame_jm1, _ = get_frame(j - 1)
            fresh_bits = sbm[fs:fe]
            try:
                parse_span(frame_jm1, fs, fe, max_mbs=m + 4)
                stats["fresh_parses_standard_EXCLUDED"] += 1
                continue
            except DecodeError:
                pass
            ms, me = mbs[k_head]["start"], mbs[n - k_tail - 1]["end"]
            frame_j, _ = get_frame(j)
            try:
                dcs = span_dcs(frame_j, ms, me)
            except DecodeError:
                dcs = None
            pairs.append({"std_frame": j, "std_start": ms, "std_end": me,
                          "std_bits": sa[ms:me], "std_mbs": m,
                          "std_mb_bounds": [[mb["start"], mb["end"]]
                                            for mb in mbs[k_head:n - k_tail]],
                          "tier": R["tier"], "corrob": R["corrob"],
                          "fresh_frame": j - 1, "fresh_start": fs,
                          "fresh_end": fe, "fresh_bits": fresh_bits,
                          "lendiff": L - (me - ms),
                          "std_dcs": dcs})
            stats["pairs"] += 1
        if fi and fi % 1000 == 0:
            print(f"  ...{fi}/{len(frames)} frames, "
                  f"{len(pairs)} pairs", flush=True)

    print(f"\nstats: {dict(sorted(stats.items()))}")
    print(f"pairs harvested: {len(pairs)}")
    if pairs:
        print(f"by tier: {dict(Counter(p['tier'] for p in pairs))}")
        print(f"std_mbs histogram: "
              f"{dict(sorted(Counter(p['std_mbs'] for p in pairs).items()))}")
        ld = Counter(p["lendiff"] for p in pairs)
        print(f"lendiff (fresh - std) histogram (top 20): "
              f"{dict(ld.most_common(20))}")

    json.dump({"stats": dict(stats), "pairs": pairs},
              open("stasis_harvest.json", "w"), indent=1)
    print("\nwritten stasis_harvest.json")


if __name__ == "__main__":
    main()
