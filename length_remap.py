"""
Session 7 -- length-remap solver (VLC_REVERSE_ENGINEERING.md next-action #1).

Premise (from session 6's trusted-codeword census, ledger #26/#27): the ~66%
of small failing gaps that over-run with entirely-valid standard codes are
best explained by proprietary codewords in SHALLOW prefix space that
prefix-collide with standard TABLE 13 codes and are mis-consumed at the
WRONG LENGTH -- not a new symbol (deep-zero refuted) and not a flipped LAST
flag (census refuted).

Method: for each 1-MB-scale (default 53..100 bit) failing catalog gap,
stock-parse it once recording every TCOEF-level event (VLC, ESCAPE, or the
invalid-code failure point). Then for each event, re-parse the gap with a
single override at that event's bit position: consume L' = L + delta bits
(delta in +-1..4; absolute 2..16 for invalid-code events) with forced
LAST in {0,1} and run=0 (run is permissive -- it only gates the 64-coeff
overflow check, and we are solving for bit ALIGNMENT, not pixels). If the
gap then parses and lands EXACTLY on its end anchor, that (window-bits[:L'],
LAST) pair gets one vote from this gap.

Discrimination (the lesson of ledger #27): a REAL length-remap recurs -- the
same (bit-pattern -> length) vote lands many gaps, concentrated, and
especially lands gaps that have NO OTHER solution. Self-sync scatters votes
thinly across many candidates. We report, per candidate: total gap votes,
UNIQUE-solution gap votes (gaps where it is the only landing override), the
stock codeword(s) it shadows with their trusted-census counts
(anchor_codes.json midblock_last0 -- a high-trust shadow makes a candidate
suspect per the census argument), and prefix conflicts against the 204
confirmed-standard codewords (total_seen).

Run 1 (delta +-4, TCOEF events only) REFUTED the concentrated-remap form:
554/4681 gaps landed, top vote 36 (0.8%), all candidates truncations of
top-frequency standard codes with LAST=1 (self-sync signature) and all
prefix-conflicting with high-trust census codes. Run 2 (this version) closes
the coverage holes for an airtight negative: ABSOLUTE lengths 1..16 at every
TCOEF event (2..26 at ESC), single-event INTRADC overrides, and two GLOBAL
structural sweeps -- INTRADC size L=2..16 applied to every DC read, and
INTRADC with the 0x00/0x80 validity check dropped.

Usage: python length_remap.py [min_bits max_bits [catalog.json]]
Writes length_remap_results.json (or *_clean.json when run on
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

DELTAS = (-4, -3, -2, -1, 1, 2, 3, 4)
MAX_VLC_LEN = 16          # candidate true-code length cap for VLC events
MAX_ESC_LEN = 26          # ESCAPE is 22 bits stock; allow +-4


def parse(frame, start, end, ov=None, events=None, dc_len=8, dc_check=True):
    """Stock-parse complete MBs in [start, end); land EXACTLY on end or raise.

    ov = (pos, new_len, last): at the TCOEF-level or INTRADC event starting
    at bit `pos`, consume new_len bits and (for TCOEF) treat the event as
    (last, run=0) instead of stock-decoding it. Fires at most once (parse is
    bit-identical to stock until pos, so it is reached exactly once).

    dc_len / dc_check: GLOBAL structural variants -- every INTRADC read
    consumes dc_len bits (validity check only applies at 8), and dc_check
    False drops the 0x00/0x80 forbidden-value check.

    events, if a list, collects every overridable event:
    {"pos", "kind": "vlc"|"esc"|"bad"|"dc", "len", "key"} (key = stock
    codeword bits; None for esc/bad/dc). Recording stops at the first
    DecodeError, which is fine -- the true divergence is at or before the
    failure point.
    """
    br = BitReader(frame, start)
    g = 16
    n = 0
    ov_pos = ov[0] if ov else -1
    while br.pos < end:
        while True:                              # MCBPC (+ stuffing)
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
            pos = br.pos
            if pos == ov_pos:                    # single-event DC override
                _, new_len, _ = ov
                if end - pos < new_len:
                    raise DecodeError("EOF override")
                br.skip(new_len)
                ov_pos = -1
            else:
                if events is not None:
                    events.append({"pos": pos, "kind": "dc",
                                   "len": dc_len, "key": None})
                if end - br.pos < dc_len:
                    raise DecodeError("EOF intradc")
                v = br.read(dc_len)
                if dc_len == 8 and dc_check and v in (0x00, 0x80):
                    raise DecodeError("bad intradc")
            if not (cbp & 64):
                continue
            cn = 1
            last = False
            while cn < 64 and not last:
                pos = br.pos
                if pos == ov_pos:                # apply the length override
                    _, new_len, last = ov
                    if end - pos < new_len:
                        raise DecodeError("EOF override")
                    br.skip(new_len)
                    run = 0
                    ov_pos = -1
                else:
                    w13 = peek_safe(br, 13)
                    sym2 = VLC_TAB5[w13 >> 5]
                    if sym2 == 1:                # ESCAPE
                        if events is not None:
                            events.append({"pos": pos, "kind": "esc",
                                           "len": 22, "key": None})
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
                            if events is not None:
                                events.append({"pos": pos, "kind": "bad",
                                               "len": None, "key": None})
                            raise DecodeError("invalid tcoef")
                        if end - br.pos < skip:
                            raise DecodeError("EOF tcoef")
                        run = ((sym2 >> 8) & 0xFF) - 1
                        last = bool((sym2 >> 16) & 1)
                        if events is not None:
                            events.append(
                                {"pos": pos, "kind": "vlc", "len": skip,
                                 "key": eb.window_at(frame, pos)[:skip]})
                        br.skip(skip)
                if cn + run > 63:
                    raise DecodeError("overflow")
                cn += run + 1
        n += 1
    if br.pos != end:
        raise DecodeError("misland")
    return n


def cand_lengths(ev):
    """Candidate true-event lengths to try (ABSOLUTE, run 2)."""
    if ev["kind"] == "esc":
        return [L for L in range(2, MAX_ESC_LEN + 1) if L != 22]
    if ev["kind"] == "dc":
        return [L for L in range(2, MAX_VLC_LEN + 1) if L != ev["len"]]
    return range(1, MAX_VLC_LEN + 1)             # vlc (incl. stock L) / bad


def solve_gap(frame, start, end):
    """Record events, then try every single-event length override.
    Returns (events, solutions): solutions = list of (ev, new_len, last)."""
    events = []
    try:
        parse(frame, start, end, events=events)
        return events, None                      # landed stock -- not a gap
    except DecodeError:
        pass
    sols = []
    for ev in events:
        lasts = (False,) if ev["kind"] == "dc" else (False, True)
        for L in cand_lengths(ev):
            for lf in lasts:
                try:
                    parse(frame, start, end, ov=(ev["pos"], L, lf))
                    sols.append((ev, L, lf))
                except DecodeError:
                    pass
    return events, sols


def prefix_conflicts(code, alphabet):
    """Standard codewords that `code` collides with (equal / either-prefix)."""
    return [s for s in alphabet
            if s == code or s.startswith(code) or code.startswith(s)]


def main():
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 53
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    catfile = sys.argv[3] if len(sys.argv) > 3 else "mb_catalog.json"
    cat = json.load(open(catfile))
    trust = json.load(open("anchor_codes.json"))
    midblock = trust["midblock_last0"]           # stock code -> trusted count
    confirmed = list(trust["total_seen"])        # the 204-codeword alphabet
    fo = eb.frame_loader()

    gaps = [g for g in cat["gaps"]
            if not g["stock_ok"] and lo <= g["end"] - g["start"] <= hi]
    tiers = Counter(g.get("tier", "-") for g in gaps)
    print(f"{len(gaps)} failing gaps of size [{lo},{hi}] bits "
          f"from {catfile} (tiers: {dict(tiers)})")

    # ---- global structural sweeps (all-DC size; DC validity check) ----
    def count_global(**kw):
        n = 0
        for gp in gaps:
            try:
                parse(fo(gp["frame"]), gp["start"], gp["end"], **kw)
                n += 1
            except DecodeError:
                pass
        return n

    print("\nglobal INTRADC-size sweep (every DC read = L bits):")
    global_dc = {}
    for L in range(2, MAX_VLC_LEN + 1):
        n = count_global(dc_len=L) if L != 8 else count_global(dc_check=False)
        tag = "8 (check dropped)" if L == 8 else str(L)
        global_dc[tag] = n
        print(f"  L={tag:<17} lands {n}/{len(gaps)}")

    # ---- per-gap single-event override search ----
    votes = Counter()                            # (code,last) -> gap votes
    gold_votes = Counter()                       # ... -> gold-tier gap votes
    uniq = Counter()                             # ... -> unique-solution votes
    shadows = defaultdict(Counter)               # (code,last) -> stock keys
    dc_votes = Counter()                         # single-DC-event len -> gaps
    landed = 0
    landed_tier = Counter()
    nsol_hist = Counter()
    for i, gp in enumerate(gaps):
        if i and i % 500 == 0:
            print(f"  ...{i}/{len(gaps)} gaps, {landed} landed by >=1 override")
        tier = gp.get("tier", "-")
        frame = fo(gp["frame"])
        events, sols = solve_gap(frame, gp["start"], gp["end"])
        if sols is None:
            continue                             # stock-landed (unexpected)
        keys = set()
        key_shadow = {}
        dc_keys = set()
        for ev, L, lf in sols:
            if ev["kind"] == "dc":
                dc_keys.add(("DC", L))
                continue
            code = eb.window_at(frame, ev["pos"], 28)[:L]
            keys.add((code, lf))
            key_shadow[(code, lf)] = ev["key"] or ev["kind"]
        nsol = len(keys) + len(dc_keys)
        nsol_hist[min(nsol, 10)] += 1
        if nsol:
            landed += 1
            landed_tier[tier] += 1
        for k in dc_keys:
            dc_votes[k[1]] += 1
        for k in keys:
            votes[k] += 1
            if tier == "gold":
                gold_votes[k] += 1
            shadows[k][key_shadow[k]] += 1
            if nsol == 1:
                uniq[k] += 1

    print(f"\ngaps landed by at least one single length-override: "
          f"{landed}/{len(gaps)} (by tier: {dict(landed_tier)})")
    print("solutions-per-gap histogram (10 = >=10):",
          dict(sorted(nsol_hist.items())))
    print("single-DC-event override votes by length:",
          dict(sorted(dc_votes.items())))

    print(f"\ntop candidates by gap votes "
          f"(votes / gold / unique-solution votes / len / LAST / "
          f"shadows(trust)):")
    rows = []
    for (code, lf), v in votes.most_common(40):
        shd = ", ".join(f"{s}({midblock.get(s, 0)})" if s not in
                        ("esc", "bad") else s
                        for s, _ in shadows[(code, lf)].most_common(3))
        confl = prefix_conflicts(code, confirmed)
        confl_trust = max((trust["total_seen"][c] for c in confl), default=0)
        rows.append({"code": code, "len": len(code), "last": lf,
                     "votes": v, "gold": gold_votes[(code, lf)],
                     "unique": uniq[(code, lf)],
                     "shadows": dict(shadows[(code, lf)]),
                     "conflicts": confl, "max_conflict_trust": confl_trust})
        print(f"  {v:5d}  gold {gold_votes[(code, lf)]:3d}  "
              f"uniq {uniq[(code, lf)]:4d}  len {len(code):2d}  "
              f"LAST={int(lf)}  {code:<18} shadows: {shd}  "
              f"conflicts: {len(confl)} (max trust {confl_trust})")

    out = ("length_remap_results_clean.json" if catfile != "mb_catalog.json"
           else "length_remap_results.json")
    json.dump({"band": [lo, hi], "catalog": catfile, "n_gaps": len(gaps),
               "tiers": dict(tiers), "landed": landed,
               "landed_tier": dict(landed_tier),
               "nsol_hist": dict(nsol_hist), "global_dc": global_dc,
               "dc_event_votes": dict(dc_votes), "candidates": rows},
              open(out, "w"), indent=1)
    print(f"\nwritten {out}")


if __name__ == "__main__":
    main()
