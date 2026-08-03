"""
Session 9 -- DC-field pixel-prior attack (session-8 next-action #2).

Premise (ledger #31/#33): fresh MBs use proprietary coding with >=2 deviant
events per MB, so no grammar-level single-event search can work. But IF fresh
MBs still store INTRADC as 8-bit FLC fields (as the static regime provably
does), those six bytes are PREDICTABLE from pixel priors: webcam content is
spatially smooth, and each clean 1-MB gap sits between two anchored MBs that
are its raster neighbours (prev run's last MB = left neighbour, next run's
first MB = right neighbour, modulo the ~1/11 row-wrap cases we accept as
noise). Locating the six DC fields would segment the fresh-MB structure
WITHOUT assuming any VLC table: header bits before DC1, AC spans between DCs.

Method:
  1. CALIBRATION -- over gold runs (>=2 MBs), decode consecutive anchored MB
     pairs and measure |DC diff| under the edge-continuity pairing
     (prev.Y2->next.Y1, prev.Y4->next.Y3, Cb->Cb, Cr->Cr) vs a shuffled-pair
     baseline. This measures how informative the spatial prior can be at all.
  2. ATTACK -- per failing 53..100-bit gap: predict the six DC bytes from the
     flanking MBs (Y1<-prev.Y2, Y2<-next.Y1, Y3<-prev.Y4, Y4<-next.Y3,
     chroma <- mean), then DP-search the payload for the best increasing
     6-tuple of 8-bit windows: p1 >= 3 (min MCBPC+CBPY), p[k+1] >= p[k]+8,
     p6 <= G-8. Cost = sum of |window - pred| capped at CAP (forbidden
     0x00/0x80 windows cost CAP).
  3. CONTROL -- same payload + DP, predictions borrowed from NCTRL random
     OTHER gaps. rank = #controls beating the real predictions. If DC fields
     exist and the prior is informative, real predictions win (rank 0) far
     more often than the 1/(NCTRL+1) chance rate; self-sync-style degeneracy
     cannot fake this because controls share every structural freedom.

Aggregates: win counts (gold/silver), real-vs-control score distributions,
and -- over winning gaps only -- histograms of first-DC offset (implied
fresh-MB header size), inter-DC AC spans, and tail slack.

Usage: python dc_prior_attack.py
Writes dc_prior_results.json.
"""
import json
import random
from collections import Counter
from statistics import mean, median

import ext_bootstrap as eb
from bitreader import BitReader
from i263_decoder import DecodeError, decode_mb

CAP = 128         # per-block cost cap; must exceed the wrong-prediction
                  # error band (~56-75, see calibration) or the real-vs-
                  # control contrast is clipped away (CAP=64 run was ~null)
NCTRL = 20        # borrowed-prediction controls per gap
MIN_HDR = 3       # min bits before DC1 (MCBPC >= 1 + CBPY >= 2)
random.seed(9)


def mb_dcs(frame, start, end):
    """Decode one anchored MB with stock tables; return its 6 DC bytes."""
    br = BitReader(frame, start)
    idc, _ = decode_mb(br, 16)
    if br.pos != end:
        raise DecodeError("misland")
    if any(v is None for v in idc):
        raise DecodeError("missing DC")
    return idc


def predict(prev, nxt):
    """Predict a gap MB's 6 DC bytes from its raster neighbours."""
    if prev and nxt:
        return [prev[1], nxt[0], prev[3], nxt[2],
                (prev[4] + nxt[4]) // 2, (prev[5] + nxt[5]) // 2]
    src = prev or nxt
    return list(src)


def dp_best(wins, G, preds):
    """Best increasing 6-tuple of DC positions. Returns (cost, positions).

    wins[p] = 8-bit window value at payload bit p (0 <= p <= G-8).
    Position k (0-based) allowed in [MIN_HDR + 8k, G - 8(6-k)].
    """
    def cost(k, p):
        w = wins[p]
        if w in (0x00, 0x80):
            return CAP
        return min(abs(w - preds[k]), CAP)

    INF = 10 ** 9
    prev_f = None
    back = []
    for k in range(6):
        lo = MIN_HDR + 8 * k
        hi = G - 8 * (6 - k)
        f = [INF] * (hi + 1)
        bk = [-1] * (hi + 1)
        if k == 0:
            for p in range(lo, hi + 1):
                f[p] = cost(0, p)
        else:
            best, arg = INF, -1
            for p in range(lo, hi + 1):
                q = p - 8                       # newly eligible predecessor
                if q < len(prev_f) and prev_f[q] < best:
                    best, arg = prev_f[q], q
                if best < INF:
                    f[p] = best + cost(k, p)
                    bk[p] = arg
        prev_f = f
        back.append(bk)
    tail = min(range(len(prev_f)), key=lambda p: prev_f[p])
    total = prev_f[tail]
    pos = [tail]
    for k in range(5, 0, -1):
        pos.append(back[k][pos[-1]])
    pos.reverse()
    return total, pos


def main():
    cat = json.load(open("clean_catalog.json"))
    fo = eb.frame_loader()

    # index runs by (frame, boundary-bit) for flank lookup
    run_by_end = {}
    run_by_start = {}
    for r in cat["runs"]:
        run_by_end[(r["frame"], r["end"])] = r
        run_by_start[(r["frame"], r["start"])] = r

    # ---- 1. calibration: spatial DC predictability inside gold runs ----
    edge_diffs = {"Y_h": [], "Cb": [], "Cr": []}
    all_pairs = []                    # (prevdcs, nextdcs) for shuffled baseline
    n_parse_fail = 0
    for r in cat["runs"]:
        if r["tier"] != "gold" or r["n_mbs"] < 2:
            continue
        frame = fo(r["frame"])
        dcs = []
        for mb in r["mbs"]:
            try:
                dcs.append(mb_dcs(frame, mb["start"], mb["end"]))
            except DecodeError:
                dcs.append(None)
                n_parse_fail += 1
        for a, b in zip(dcs, dcs[1:]):
            if a is None or b is None:
                continue
            edge_diffs["Y_h"] += [abs(a[1] - b[0]), abs(a[3] - b[2])]
            edge_diffs["Cb"].append(abs(a[4] - b[4]))
            edge_diffs["Cr"].append(abs(a[5] - b[5]))
            all_pairs.append((a, b))
    print(f"calibration: {len(all_pairs)} consecutive gold MB pairs "
          f"({n_parse_fail} anchored-MB parse failures)")
    shuf = all_pairs[:]
    random.shuffle(shuf)
    base = {"Y_h": [], "Cb": [], "Cr": []}
    for (a, _), (_, b) in zip(all_pairs, shuf):
        base["Y_h"] += [abs(a[1] - b[0]), abs(a[3] - b[2])]
        base["Cb"].append(abs(a[4] - b[4]))
        base["Cr"].append(abs(a[5] - b[5]))
    calib = {}
    for k in edge_diffs:
        d, bl = edge_diffs[k], base[k]
        calib[k] = {"n": len(d), "median": median(d), "mean": mean(d),
                    "baseline_median": median(bl), "baseline_mean": mean(bl)}
        print(f"  {k:4s} |dDC| adjacent: median {median(d):5.1f} mean "
              f"{mean(d):5.1f}   shuffled baseline: median "
              f"{median(bl):5.1f} mean {mean(bl):5.1f}")

    # ---- 2+3. attack + controls over the clean 1-MB gaps ----
    gaps = [g for g in cat["gaps"]
            if not g["stock_ok"] and 53 <= g["end"] - g["start"] <= 100]
    print(f"\n{len(gaps)} failing 1-MB gaps "
          f"(tiers: {dict(Counter(g['tier'] for g in gaps))})")

    # per-gap predictions from flanking anchored MBs
    recs = []
    n_noflank = 0
    for g in gaps:
        frame = fo(g["frame"])
        prev = nxt = None
        r = run_by_end.get((g["frame"], g["start"]))
        if r:
            mb = r["mbs"][-1]
            try:
                prev = mb_dcs(frame, mb["start"], mb["end"])
            except DecodeError:
                pass
        r = run_by_start.get((g["frame"], g["end"]))
        if r:
            mb = r["mbs"][0]
            try:
                nxt = mb_dcs(frame, mb["start"], mb["end"])
            except DecodeError:
                pass
        if not prev and not nxt:
            n_noflank += 1
            continue
        G = g["end"] - g["start"]
        wins = [int(eb.window_at(frame, g["start"] + p, 8), 2)
                for p in range(G - 7)]
        # flank agreement: how consistent the two neighbours are about the
        # gap MB (small = locally smooth = trustworthy prediction)
        conf = (abs(prev[1] - nxt[0]) + abs(prev[3] - nxt[2])) / 2 \
            if prev and nxt else 255
        recs.append({"gap": g, "G": G, "wins": wins,
                     "preds": predict(prev, nxt), "conf": conf,
                     "both_flanks": bool(prev and nxt)})
    print(f"{len(recs)} gaps with usable flanking DCs "
          f"({n_noflank} without; "
          f"{sum(1 for r in recs if r['both_flanks'])} with both flanks)")

    all_preds = [r["preds"] for r in recs]
    wins_hist = Counter()
    ranks = []
    advantages = []                   # mean(ctrl) - real, per gap
    real_scores, ctrl_scores = [], []
    win_p1, win_spans, win_tail = Counter(), Counter(), Counter()
    win_rows = []
    ctrl_sets = []
    for i, r in enumerate(recs):
        sc, pos = dp_best(r["wins"], r["G"], r["preds"])
        others = [j for j in range(len(recs)) if j != i]
        sample = random.sample(others, min(NCTRL, len(others)))
        ctrl_sets.append(sample)
        ctrl = []
        for j in sample:
            c, _ = dp_best(r["wins"], r["G"], all_preds[j])
            ctrl.append(c)
        rank = sum(1 for c in ctrl if c < sc)
        ranks.append(rank)
        advantages.append(mean(ctrl) - sc)
        real_scores.append(sc)
        ctrl_scores += ctrl
        if rank == 0:
            wins_hist[r["gap"]["tier"]] += 1
            win_p1[pos[0]] += 1
            for a, b in zip(pos, pos[1:]):
                win_spans[b - a - 8] += 1
            win_tail[r["G"] - (pos[-1] + 8)] += 1
            win_rows.append({"frame": r["gap"]["frame"],
                             "start": r["gap"]["start"], "G": r["G"],
                             "tier": r["gap"]["tier"], "score": sc,
                             "pos": pos, "preds": r["preds"],
                             "bytes": [r["wins"][p] for p in pos]})

    n = len(recs)
    n_win = sum(wins_hist.values())
    exp = n / (NCTRL + 1)
    rank_hist = Counter(min(r, 10) for r in ranks)
    # fraction of (gap, control) comparisons the real predictions win/tie
    pairs_won = sum(NCTRL - rk for rk in ranks)
    print(f"\nreal-prediction DP score: mean {mean(real_scores):6.1f} "
          f"median {median(real_scores):5.1f}")
    print(f"control (borrowed preds):  mean {mean(ctrl_scores):6.1f} "
          f"median {median(ctrl_scores):5.1f}")
    print(f"gaps where real preds beat ALL {NCTRL} controls (rank 0): "
          f"{n_win}/{n}  (chance expectation {exp:.1f})  "
          f"by tier: {dict(wins_hist)}")
    print(f"rank histogram (10 = >=10): {dict(sorted(rank_hist.items()))}")
    print(f"(gap,control) comparisons won-or-tied by real preds: "
          f"{pairs_won}/{n * NCTRL} ({pairs_won / (n * NCTRL):.1%}, "
          f"chance ~50%)")

    # ---- flank-agreement stratification: signal should concentrate in
    # gaps whose two neighbours agree (locally smooth => tight prior) ----
    order = sorted(range(n), key=lambda i: recs[i]["conf"])
    print("\nflank-agreement quartiles (Q1 = most agreement/smoothest):")
    strat = []
    for q in range(4):
        idx = order[q * n // 4:(q + 1) * n // 4]
        r0 = sum(1 for i in idx if ranks[i] == 0)
        adv = mean(advantages[i] for i in idx)
        pw = sum(NCTRL - ranks[i] for i in idx) / (len(idx) * NCTRL)
        confs = [recs[i]["conf"] for i in idx]
        strat.append({"q": q + 1, "n": len(idx),
                      "conf_range": [min(confs), max(confs)],
                      "rank0": r0, "rank0_expected": len(idx) / (NCTRL + 1),
                      "mean_advantage": adv, "pairs_won_frac": pw})
        print(f"  Q{q + 1} conf {min(confs):5.1f}-{max(confs):5.1f}  "
              f"rank0 {r0:3d}/{len(idx)} (chance {len(idx) / (NCTRL + 1):.1f})"
              f"  mean advantage {adv:+6.1f}  pairs won {pw:.1%}")

    # ---- gap-size stratification: smallest gaps have the fewest layouts,
    # so noise absorption by the DP min is weakest there ----
    order_g = sorted(range(n), key=lambda i: recs[i]["G"])
    print("\ngap-size quartiles (S1 = smallest):")
    strat_g = []
    for q in range(4):
        idx = order_g[q * n // 4:(q + 1) * n // 4]
        r0 = sum(1 for i in idx if ranks[i] == 0)
        adv = mean(advantages[i] for i in idx)
        pw = sum(NCTRL - ranks[i] for i in idx) / (len(idx) * NCTRL)
        gs = [recs[i]["G"] for i in idx]
        strat_g.append({"q": q + 1, "n": len(idx),
                        "G_range": [min(gs), max(gs)], "rank0": r0,
                        "mean_advantage": adv, "pairs_won_frac": pw})
        print(f"  S{q + 1} G {min(gs):3d}-{max(gs):3d}  "
              f"rank0 {r0:3d}/{len(idx)} (chance {len(idx) / (NCTRL + 1):.1f})"
              f"  mean advantage {adv:+6.1f}  pairs won {pw:.1%}")

    # ---- ensemble positional profiles: if fresh MBs put DC1 at a roughly
    # consistent offset (header length) and the last DC at a consistent
    # slack from the MB end, the per-position advantage peaks there ----
    def profile(block, positions, from_end=False):
        out = []
        for p in positions:
            vals = []
            for i, r in enumerate(recs):
                pos = (r["G"] - 8 - p) if from_end else p
                if pos < MIN_HDR or pos + 8 > r["G"]:
                    continue
                if from_end and pos < MIN_HDR + 40:
                    continue                     # room for 5 earlier DCs
                if not from_end and pos > r["G"] - 48:
                    continue                     # room for 5 later DCs
                w = r["wins"][pos]
                if w in (0x00, 0x80):
                    continue
                cr = abs(w - r["preds"][block])
                cc = mean(abs(w - all_preds[j][block])
                          for j in ctrl_sets[i])
                vals.append(cc - cr)
            out.append((p, len(vals), mean(vals) if vals else 0.0))
        return out

    print("\nensemble first-DC (Y1) advantage by offset p1 "
          "(positive = real preds fit better):")
    prof1 = profile(0, range(MIN_HDR, 21))
    for p, m, a in prof1:
        print(f"  p1={p:2d}  n={m:3d}  adv {a:+6.2f}")
    print("ensemble last-DC (Cr) advantage by tail slack s (p6 = G-8-s):")
    prof6 = profile(5, range(0, 16), from_end=True)
    for s, m, a in prof6:
        print(f"  s ={s:2d}  n={m:3d}  adv {a:+6.2f}")

    if n_win:
        print(f"\namong rank-0 gaps ({n_win}):")
        print(f"  first-DC offset p1 (implied header bits): "
              f"{dict(sorted(win_p1.items()))}")
        print(f"  inter-DC AC-span bits (p[k+1]-p[k]-8): "
              f"{dict(sorted(win_spans.items()))}")
        print(f"  tail slack after last DC: {dict(sorted(win_tail.items()))}")

    json.dump({"calibration": calib, "n_gaps": len(gaps), "n_usable": n,
               "ncrtl": NCTRL, "cap": CAP,
               "real_score_mean": mean(real_scores),
               "ctrl_score_mean": mean(ctrl_scores),
               "rank_hist": {str(k): v for k, v in sorted(rank_hist.items())},
               "n_rank0": n_win, "rank0_expected_by_chance": exp,
               "rank0_by_tier": dict(wins_hist),
               "rank0_p1_hist": {str(k): v for k, v in sorted(win_p1.items())},
               "rank0_span_hist": {str(k): v
                                   for k, v in sorted(win_spans.items())},
               "rank0_tail_hist": {str(k): v
                                   for k, v in sorted(win_tail.items())},
               "conf_strata": strat, "size_strata": strat_g,
               "profile_first_dc": prof1, "profile_last_dc": prof6,
               "rank0_gaps": win_rows},
              open("dc_prior_results.json", "w"), indent=1)
    print("\nwritten dc_prior_results.json")


if __name__ == "__main__":
    main()
