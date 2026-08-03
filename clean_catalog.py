"""
Session 8 — clean anchor-corpus rebuild (ledger #30 proved the session-6
catalog contaminated: 6,151 impossible 1-52-bit inter-anchor spacings, a
frame with 105 anchored "MBs" > 99, mostly length-1 anchor runs).

Validation design — MEASURED on this stream before being adopted (see
session-8 log). Purity metric = rate of "impossible" 1-52-bit spacings
between consecutive kept anchors (the detectable-contamination signal;
standard-syntax MB minimum is 53 bits). Baseline (no validation): ~4.0%.

  - run length >= 2 MBs  ->  0.79% pre-V3   (the workhorse validator: a
    unique tiling over >=106 bits inside a matched segment is a far
    stronger constraint than one 53-67-bit MB)
  - full-run bitstring corroborated in >= 2 of frames i-2,i-1,i+1,i+2
    -> 2.29%  (content recurrence does NOT certify boundary correctness:
    a boundary-off-by-slack anchor is still static content and recurs in
    neighbours just as well; requiring it ON TOP of len>=2 shrinks the
    corpus ~3.5x with no measured purity gain)

So the corpus is TIERED rather than gated on corroboration alone:

  GOLD    runs of >= 2 MBs (corroboration recorded as metadata);
  SILVER  single-MB anchors with corroboration >= 2 (the only source of
          small inter-anchor gaps — the step-2 DC attack needs 1-MB gaps —
          kept with a measured ~2-3% boundary-error rate, tagged so
          downstream inference can weight or exclude them).

Then, over ALL kept runs:
  V3  impossible-spacing rejection — consecutive runs separated by 1..52
      bits contradict the 53-bit MB minimum: gold+gold or silver+silver
      pairs drop BOTH; gold+silver pairs drop the silver only (gold's
      independent error rate is ~10x lower);
  V4  frame sanity — overlapping tilings or > 99 total anchored MBs
      reject the whole frame's output.

Every gap is flanked by validated runs by construction and is tagged
gold (both flanks gold) or silver (any silver flank).

Usage:
  python clean_catalog.py inspect 70
  python clean_catalog.py batch 0 9367 -j 10   # -> clean_catalog.json
"""
import json
import sys
from collections import Counter

from mb_catalog import (PSCS, get_frame, frame_bitstr, match_segments,
                        anchors_in_segment, parse_one_mb, SLACK)
from i263_decoder import DecodeError

CORROB_NEIGHBOURS = (-2, -1, 1, 2)
SILVER_CORROB_MIN = 2
GOLD_MIN_MBS = 2
MIN_MB_BITS = 53        # 1 MCBPC + 2 CBPY + 48 INTRADC + 2 = standard min
MAX_FRAME_MBS = 99      # QCIF 11x9


def candidate_runs(i, frame):
    """mb_catalog's segment -> unique-tiling stage, unchanged."""
    segs = []
    if i > 0:
        segs += match_segments(i, i - 1)
    if i + 1 < len(PSCS):
        segs += match_segments(i, i + 1)
    segs.sort()
    merged = []
    for s, ln in segs:
        if merged and s <= merged[-1][0] + merged[-1][1]:
            merged[-1] = (merged[-1][0],
                          max(merged[-1][1], s + ln - merged[-1][0]))
        else:
            merged.append((s, ln))
    runs = []
    stats = Counter()
    for s, ln in merged:
        run = anchors_in_segment(frame, s, ln)
        if not run:
            stats["seg_no_unique_tiling"] += 1
            continue
        cov = run[-1][1] - run[0][0]
        if cov < ln - 2 * SLACK:
            stats["seg_poor_coverage"] += 1
            continue
        runs.append(run)
    return runs, stats


def run_corroboration(i, sa, run):
    """In how many of the 4 neighbour frames does the run's exact
    bitstring occur?"""
    rs = sa[run[0][0]:run[-1][1]]
    c = 0
    for d in CORROB_NEIGHBOURS:
        j = i + d
        if 0 <= j < len(PSCS):
            sb, _ = frame_bitstr(j)
            if rs in sb:
                c += 1
    return c


def clean_frame(i, verbose=False):
    """Returns (runs, gaps, stats); runs is a list of (run, tier, corrob).
    runs=None means the frame was rejected at V4."""
    frame, hdr = get_frame(i)
    cands, stats = candidate_runs(i, frame)
    sa, _ = frame_bitstr(i)

    kept = []
    for run in cands:
        c = run_corroboration(i, sa, run)
        if len(run) >= GOLD_MIN_MBS:
            kept.append((run, "gold", c))
        elif c >= SILVER_CORROB_MIN:
            kept.append((run, "silver", c))
        else:
            stats["dropped_single_uncorroborated"] += 1
    kept.sort(key=lambda rtc: rtc[0][0][0])

    # V4a — overlapping validated runs = conflicting tilings
    for (r1, _, _), (r2, _, _) in zip(kept, kept[1:]):
        if r2[0][0] < r1[-1][1]:
            stats["v4_frame_overlapping_runs"] += 1
            return None, None, stats

    # merge abutting runs (independent tilings agreeing on a boundary)
    merged = []
    for run, tier, c in kept:
        if merged and run[0][0] == merged[-1][0][-1][1]:
            prun, ptier, pc = merged[-1]
            merged[-1] = (prun + run,
                          "gold" if "gold" in (ptier, tier) else "silver",
                          min(pc, c))
            stats["runs_merged_abutting"] += 1
        else:
            merged.append((run, tier, c))

    # V3 — impossible inter-run spacing
    bad = set()
    for k in range(len(merged) - 1):
        spacing = merged[k + 1][0][0][0] - merged[k][0][-1][1]
        if 0 < spacing < MIN_MB_BITS:
            stats["v3_impossible_spacings"] += 1
            ta, tb = merged[k][1], merged[k + 1][1]
            if ta == "gold" and tb == "silver":
                bad.add(k + 1)
            elif ta == "silver" and tb == "gold":
                bad.add(k)
            else:
                bad.add(k)
                bad.add(k + 1)
    if bad:
        stats["v3_runs_dropped"] += len(bad)
        merged = [rtc for k, rtc in enumerate(merged) if k not in bad]

    # V4b — more anchored MBs than the frame has
    n_mbs = sum(len(r) for r, _, _ in merged)
    if n_mbs > MAX_FRAME_MBS:
        stats["v4_frame_too_many_mbs"] += 1
        return None, None, stats

    if verbose:
        for run, tier, c in merged:
            print(f"  {tier:6s} run [{run[0][0]}..{run[-1][1]}) "
                  f"{len(run)} MBs, corrob {c}/{len(CORROB_NEIGHBOURS)}")

    # gaps between surviving consecutive runs
    gaps = []
    for (a, ta, _), (b, tb, _) in zip(merged, merged[1:]):
        gs, ge = a[-1][1], b[0][0]
        if ge - gs == 0:
            continue
        if ge - gs < MIN_MB_BITS:
            stats["gap_sub53_residual"] += 1      # only possible post-V3 drop
            continue
        s, g, n_gap_mbs, ok = gs, 16, 0, True
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
            n_gap_mbs += 1
        landed = ok and s == ge
        gaps.append({"frame": i, "start": gs, "end": ge,
                     "tier": "gold" if ta == tb == "gold" else "silver",
                     "stock_ok": landed,
                     "stock_mbs": n_gap_mbs if landed else -1})
        if verbose:
            r = gaps[-1]
            print(f"  gap [{gs}..{ge}) len {ge - gs} tier={r['tier']}: "
                  f"stock_ok={r['stock_ok']} mbs={r['stock_mbs']}")

    stats["frames_ok"] += 1
    return merged, gaps, stats


def frame_records(i):
    """Worker entry: plain-serializable per-frame results."""
    runs, gaps, stats = clean_frame(i)
    if runs is None:
        return i, [], [], dict(stats)
    run_recs = []
    for run, tier, c in runs:
        run_recs.append({"frame": i, "start": run[0][0], "end": run[-1][1],
                         "n_mbs": len(run), "tier": tier, "corrob": c,
                         "mbs": [{"start": ms, "end": me, "cbpc": cbpc,
                                  "cbpy": cbpy, "events": nev}
                                 for ms, me, (cbpc, cbpy, nev) in run]})
    return i, run_recs, gaps, dict(stats)


def main():
    mode = sys.argv[1]
    if mode == "inspect":
        i = int(sys.argv[2])
        runs, gaps, stats = clean_frame(i, verbose=True)
        if runs is None:
            print(f"frame {i}: REJECTED — {dict(stats)}")
            return
        na = sum(len(r) for r, _, _ in runs)
        print(f"\nframe {i}: {na} anchored MBs in {len(runs)} runs, "
              f"{len(gaps)} gaps "
              f"({sum(1 for g in gaps if not g['stock_ok'])} need extension)")
        print(f"stats: {dict(stats)}")
        return

    lo, hi = int(sys.argv[2]), int(sys.argv[3])
    jobs = 1
    if "-j" in sys.argv:
        jobs = int(sys.argv[sys.argv.index("-j") + 1])
    all_runs, all_gaps = [], []
    totals = Counter()
    if jobs > 1:
        import multiprocessing as mp
        with mp.Pool(jobs) as pool:
            done = 0
            for i, rr, gg, st in pool.imap_unordered(
                    frame_records, range(lo, hi), chunksize=16):
                all_runs.extend(rr)
                all_gaps.extend(gg)
                totals.update(st)
                done += 1
                if done % 500 == 0:
                    print(f"  {done}/{hi - lo} frames", flush=True)
    else:
        for i in range(lo, hi):
            _, rr, gg, st = frame_records(i)
            all_runs.extend(rr)
            all_gaps.extend(gg)
            totals.update(st)
            if (i + 1 - lo) % 100 == 0:
                print(f"  {i + 1 - lo}/{hi - lo} frames", flush=True)

    all_runs.sort(key=lambda r: (r["frame"], r["start"]))
    all_gaps.sort(key=lambda g: (g["frame"], g["start"]))
    with open("clean_catalog.json", "w") as f:
        json.dump({"params": {"corrob_neighbours": CORROB_NEIGHBOURS,
                              "silver_corrob_min": SILVER_CORROB_MIN,
                              "gold_min_mbs": GOLD_MIN_MBS,
                              "frames": [lo, hi]},
                   "runs": all_runs, "gaps": all_gaps,
                   "stats": dict(totals)}, f)

    print(f"\nframes [{lo},{hi}):")
    for tier in ("gold", "silver"):
        rs = [r for r in all_runs if r["tier"] == tier]
        na = sum(r["n_mbs"] for r in rs)
        print(f"  {tier}: {na} anchored MBs in {len(rs)} runs")
    print(f"validation stats: {dict(sorted(totals.items()))}")
    for tier in ("gold", "silver"):
        bad = [g for g in all_gaps
               if g["tier"] == tier and not g["stock_ok"]]
        okc = sum(1 for g in all_gaps
                  if g["tier"] == tier and g["stock_ok"])
        sizes = Counter()
        for g in bad:
            n = g["end"] - g["start"]
            sizes["<=100" if n <= 100 else "<=200" if n <= 200 else
                  "<=400" if n <= 400 else ">400"] += 1
        print(f"  {tier} gaps: {okc} stock-ok, {len(bad)} need extension; "
              f"sizes {dict(sorted(sizes.items()))}")


if __name__ == "__main__":
    main()
