"""
Localize the silent proprietary TCOEF codes using PINNED SEGMENT BOUNDARIES.

Key idea (sharper than desync_fingerprint.py's frequency stats): every drift
correction gives us the TRUE bit position of an MB start.  So each segment
between corrections has a known start, a known MB count, and a known exact
end position.  Replay the segment; at each TCOEF event hypothesize "THIS is
the (first) proprietary code here, with true consumption L bits and LAST=l",
skip L bits, resume standard decoding — and accept the hypothesis only if the
remaining MBs all parse AND the segment ends EXACTLY on the pinned boundary.

The exact-landing requirement is what tcoef_codelength_brute.py lacked: VLC
self-synchronisation makes many (position, L) re-align to a valid code
boundary, but landing on the exact cumulative bit position is a far stronger
filter.  Events BEFORE the true culprit can't pass (downstream still hits the
real misparse); events AFTER it can't pass (they're already desynced).  So
hits should cluster at the true culprit position, with a small set of viable
L values.

Caveat: segments with MULTIPLE silent codes have no single-perturbation
solution — they are reported as unsolved (expected for some segments).

Usage:  python desync_localize.py [frame_substr ...]
        (no args = all 15 sample frames)
"""
import json
import sys
from collections import Counter
from pathlib import Path

from bitreader import BitReader
from tcoef_tables import (
    TCOEF_VLC, ESCAPE_CODE,
    FORBIDDEN_LEVEL_BYTES, decode_run_flc, decode_level_flc,
)

MCBPC = {
    "1":         (3, 0b00),
    "001":       (3, 0b01),
    "010":       (3, 0b10),
    "011":       (3, 0b11),
    "0001":      (4, 0b00),
    "000001":    (4, 0b01),
    "000010":    (4, 0b10),
    "000011":    (4, 0b11),
    "000000001": ("S", None),
}
ML_MCBPC = max(len(c) for c in MCBPC)

CBPY = {
    "0011":   0b0000, "00101":  0b0001, "00100":  0b0010, "1001":   0b0011,
    "00011":  0b0100, "0111":   0b0101, "000010": 0b0110, "1011":   0b0111,
    "00010":  0b1000, "000011": 0b1001, "0101":   0b1010, "1010":   0b1011,
    "0100":   0b1100, "1000":   0b1101, "0110":   0b1110, "11":     0b1111,
}
ML_CBPY = max(len(c) for c in CBPY)

FORBIDDEN_INTRADC = {0x00, 0x80}

_ESC = "ESC"
LOOKUP = {**TCOEF_VLC, ESCAPE_CODE: _ESC}
LOOKUP["000000000101"] = (0, 0, 1)
LOOKUP["000000000100"] = (1, 0, 1)
PROP_CODES = {"000000000101", "000000000100"}
MAX_LEN = max(len(c) for c in LOOKUP)

SCAN_WINDOW = 50
QCIF_MB = 99
BLOCK_NAMES = ["Y1", "Y2", "Y3", "Y4", "Cb", "Cr"]
L_MIN, L_MAX = 1, 32
STRONG_DOWNSTREAM = 40   # bits of valid standard decode required after the
                         # perturbation for a hit to count as informative


def _mvlc(br, table, max_len):
    if br.bits_remaining() < 1:
        return None, None
    peek_len = min(max_len, br.bits_remaining())
    bits = format(br.peek(peek_len), f"0{peek_len}b")
    for length in range(1, peek_len + 1):
        key = bits[:length]
        if key in table:
            br.skip(length)
            return table[key], key
    return None, None


def try_decode_mb(data, pos, trace=None, perturb=None):
    """Decode one MB.  perturb = (bitpos, skip_len, last_flag): when a TCOEF
    event starts exactly at `bitpos`, consume skip_len bits and treat the
    event as having LAST=last_flag (no sign/run/level read)."""
    br = BitReader(data, pos)
    if br.bits_remaining() < 20:
        return False, pos, None

    mcv, _ = _mvlc(br, MCBPC, ML_MCBPC)
    if mcv is None or mcv[0] == "S":
        return False, pos, None
    mbt, cbpc = mcv

    cbpyv, _ = _mvlc(br, CBPY, ML_CBPY)
    if cbpyv is None:
        return False, pos, None

    if mbt == 4:
        if br.bits_remaining() < 2:
            return False, pos, None
        br.read(2)

    coded = [
        (cbpyv >> 3) & 1, (cbpyv >> 2) & 1,
        (cbpyv >> 1) & 1, (cbpyv >> 0) & 1,
        (cbpc  >> 1) & 1, (cbpc  >> 0) & 1,
    ]
    idc_list = []

    for bi, c in enumerate(coded):
        if br.bits_remaining() < 8:
            return False, pos, None
        idc = br.read(8)
        if idc in FORBIDDEN_INTRADC:
            return False, pos, None
        idc_list.append(idc)

        if c:
            tc = 0
            while tc < 64:
                ev_pos = br.pos
                if perturb is not None and ev_pos == perturb[0]:
                    skip_len, last = perturb[1], perturb[2]
                    if br.bits_remaining() < skip_len:
                        return False, pos, None
                    br.skip(skip_len)
                    if trace is not None:
                        trace.append({"block": BLOCK_NAMES[bi],
                                      "bitpos": ev_pos, "code": "<PERTURB>",
                                      "kind": "PERT", "last": last,
                                      "run": 0, "level": 0})
                    tc += 1
                    if last:
                        break
                    continue
                v, code = _mvlc(br, LOOKUP, MAX_LEN)
                if v is None:
                    return False, pos, None
                if v is _ESC:
                    if br.bits_remaining() < 15:
                        return False, pos, None
                    last = br.read(1)
                    run = decode_run_flc(br.read(6))
                    lb = br.read(8)
                    if lb in FORBIDDEN_LEVEL_BYTES:
                        return False, pos, None
                    level = decode_level_flc(lb)
                    kind = "ESC"
                else:
                    last, run, level = v
                    if br.bits_remaining() < 1:
                        return False, pos, None
                    if br.read(1):
                        level = -level
                    kind = "PROP" if code in PROP_CODES else "VLC"
                if trace is not None:
                    trace.append({"block": BLOCK_NAMES[bi], "bitpos": ev_pos,
                                  "code": code, "kind": kind,
                                  "last": last, "run": run, "level": level})
                tc += 1
                if last:
                    break
            else:
                return False, pos, None

    return True, br.pos, idc_list


def score_start(data, pos, ahead=6):
    score = 0
    cur = pos
    for _ in range(ahead):
        ok, nxt, _ = try_decode_mb(data, cur)
        if not ok:
            break
        score += 1
        cur = nxt
    return score


def decode_frame(data, start_bit):
    records = []
    corrections = []   # (mb_idx, drift)
    cur = start_bit
    for mb_idx in range(QCIF_MB):
        ev = []
        ok, nxt, idc = try_decode_mb(data, cur, ev)
        if not ok:
            best_score, best_pos = 0, cur
            for delta in range(-SCAN_WINDOW, SCAN_WINDOW + 1):
                cand = cur + delta
                if cand < start_bit:
                    continue
                s = score_start(data, cand)
                if s > best_score:
                    best_score, best_pos = s, cand
            if best_score > 0:
                corrections.append((mb_idx, best_pos - cur))
                cur = best_pos
                ev = []
                ok, nxt, idc = try_decode_mb(data, cur, ev)
            else:
                records.append(None)
                continue
        records.append({"start": cur, "end": nxt, "events": ev})
        cur = nxt
    return records, corrections


def segment_lands(data, seg_start, n_mbs, target_end, perturb):
    """True iff decoding n_mbs MBs from seg_start with `perturb` applied ends
    exactly at target_end."""
    cur = seg_start
    for _ in range(n_mbs):
        ok, nxt, _ = try_decode_mb(data, cur, perturb=perturb)
        if not ok or nxt > target_end:
            return False
        cur = nxt
    return cur == target_end


def bits_at(data, bitpos, n=24):
    br = BitReader(data, bitpos)
    n = min(n, br.bits_remaining())
    return format(br.read(n), f"0{n}b") if n else ""


def main():
    samples = Path("samples")
    manifest = json.loads((samples / "manifest.json").read_text())
    args = sys.argv[1:]
    frames = [e for e in manifest["frames"]
              if not args or any(a in e["file"] for a in args)]

    all_hits = []          # (frame, seg_id, mb, block, bitpos, code, L, last, window)
    solved = unsolved = skipped = 0

    for entry in frames:
        data = (samples / entry["file"]).read_bytes()
        start_bit = entry["mb_data_bit_offset_within_frame"]
        records, corrections = decode_frame(data, start_bit)

        prev_mb = 0
        for seg_id, (corr_mb, drift) in enumerate(corrections):
            first_mb = prev_mb
            prev_mb = corr_mb
            n_mbs = corr_mb - first_mb
            if n_mbs <= 0:
                continue
            seg_recs = records[first_mb:corr_mb]
            if any(r is None for r in seg_recs) or records[corr_mb] is None:
                skipped += 1
                continue
            seg_start = seg_recs[0]["start"]
            target_end = records[corr_mb]["start"]

            # candidate culprit positions: every TCOEF event in the segment
            events = [(first_mb + i, ev)
                      for i, r in enumerate(seg_recs) for ev in r["events"]]

            seg_hits = []
            for mb_idx, ev in events:
                for last in (0, 1):
                    for L in range(L_MIN, L_MAX + 1):
                        if segment_lands(data, seg_start, n_mbs, target_end,
                                         (ev["bitpos"], L, last)):
                            # downstream = bits decoded by STANDARD parsing
                            # after the perturbed event, all of which must be
                            # structurally valid AND land exactly on the pin.
                            # Near-zero downstream = degenerate "absorb the
                            # tail of the last block" solution (uninformative,
                            # any L that reaches the boundary works).
                            downstream = target_end - (ev["bitpos"] + L)
                            seg_hits.append((mb_idx, ev, L, last, downstream))

            positions = {h[1]["bitpos"] for h in seg_hits}
            strong = [h for h in seg_hits if h[4] >= STRONG_DOWNSTREAM]
            tag = (f"{entry['file']} seg{seg_id} "
                   f"(MBs {first_mb}-{corr_mb - 1}, drift {drift:+d})")
            if not seg_hits:
                unsolved += 1
                print(f"UNSOLVED  {tag} — no single-perturbation solution "
                      f"(multi-culprit segment)")
                continue
            solved += 1
            print(f"SOLVED    {tag} — {len(seg_hits)} hits "
                  f"at {len(positions)} position(s), {len(strong)} strong")
            for mb_idx, ev, L, last, downstream in seg_hits:
                window = bits_at(data, ev["bitpos"])
                mark = "STRONG" if downstream >= STRONG_DOWNSTREAM else "weak"
                print(f"    MB {mb_idx:2d} {ev['block']}  bit {ev['bitpos']:6d}  "
                      f"parsed-as {ev['code']:>14} ({ev['kind']})  "
                      f"true_len={L:2d} LAST={last}  down={downstream:5d} "
                      f"{mark:>6}  bits={window}")
                all_hits.append((entry["file"], seg_id, mb_idx, ev["block"],
                                 ev["bitpos"], ev["code"], L, last, window,
                                 downstream))

    print(f"\n=== Summary: {solved} solved, {unsolved} unsolved "
          f"(multi-culprit), {skipped} skipped ===")

    strong_hits = [h for h in all_hits if h[9] >= STRONG_DOWNSTREAM]
    print(f"Total hits: {len(all_hits)}, strong (downstream >= "
          f"{STRONG_DOWNSTREAM} bits): {len(strong_hits)}")

    if strong_hits:
        print("\n=== STRONG culprit bit-windows (sorted — look for shared prefixes) ===")
        for w in sorted(set(h[8] for h in strong_hits)):
            hits_w = [h for h in strong_hits if h[8] == w]
            lls = ", ".join(f"L={h[6]}/LAST={h[7]}" for h in hits_w)
            print(f"  {w}  (x{len(hits_w)}: {lls})")
        print("\n=== STRONG parsed-as code frequency ===")
        for code, n in Counter(h[5] for h in strong_hits).most_common():
            print(f"  {code:>14}  x{n}")
        print("\n=== STRONG (true_len, LAST) frequency ===")
        for (L, last), n in Counter((h[6], h[7]) for h in strong_hits).most_common():
            print(f"  L={L:2d} LAST={last}  x{n}")


if __name__ == "__main__":
    main()
