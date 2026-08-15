"""
Fingerprint the SILENT proprietary TCOEF codes.

Background (see VLC_REVERSE_ENGINEERING.md, session 4): frame_00000 needs ~16
drift corrections but the two KNOWN proprietary codes appear only ~1x/frame.
So ~15 proprietary codes per frame are being silently mis-read: they
prefix-match a valid TABLE 13 / ESCAPE entry, consume the wrong number of
bits, and the parser desyncs without raising VLC-no-match.  The desync then
surfaces a few MBs later as an MCBPC/CBPY structural failure -> drift
correction.

Method: decode all 15 sample frames with the drift-correcting walker, tracing
every TCOEF event (code string, bit position, run/level).  For each decoded
MB compute its distance-to-next-correction (1 = the MB right before a drift
correction).  A code that CAUSES desyncs must be over-represented at small
distances vs the baseline (far from any correction).  Also compare ESCAPE
payload plausibility (run/level distributions) near vs far from desyncs:
mis-consumed proprietary bits decoded "as ESCAPE" should produce junk
run/level values.

Output: enrichment table per code + ESCAPE payload comparison + raw trace of
the last MB before each of frame_00000's first corrections (for eyeballing).
"""
import json
from collections import Counter, defaultdict
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
LOOKUP["000000000101"] = (0, 0, 1)   # known proprietary, LAST=0 (placeholder)
LOOKUP["000000000100"] = (1, 0, 1)   # known proprietary, LAST=1 (placeholder)
PROP_CODES = {"000000000101", "000000000100"}
MAX_LEN = max(len(c) for c in LOOKUP)

SCAN_WINDOW = 50
QCIF_MB = 99
BLOCK_NAMES = ["Y1", "Y2", "Y3", "Y4", "Cb", "Cr"]


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


def try_decode_mb(data, pos, trace=None):
    """Decode one MB at bit `pos`.  If `trace` is a list, append TCOEF event
    dicts: {block, bitpos, code, kind, last, run, level}."""
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
                    trace.append({
                        "block": BLOCK_NAMES[bi], "bitpos": ev_pos,
                        "code": code, "kind": kind,
                        "last": last, "run": run, "level": level,
                    })
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
    """Returns (mb_records, correction_mbs).  mb_records[i] is None or
    {start, end, events, corrected, drift}."""
    records = []
    correction_mbs = []
    cur = start_bit

    for mb_idx in range(QCIF_MB):
        ev = []
        ok, nxt, idc = try_decode_mb(data, cur, ev)
        corrected, drift = False, 0
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
                corrected, drift = True, best_pos - cur
                correction_mbs.append(mb_idx)
                ev = []
                ok, nxt, idc = try_decode_mb(data, best_pos, ev)
                cur = best_pos
            else:
                records.append(None)
                continue
        records.append({"start": cur, "end": nxt, "events": ev,
                        "corrected": corrected, "drift": drift})
        cur = nxt
    return records, correction_mbs


def main():
    samples = Path("samples")
    manifest = json.loads((samples / "manifest.json").read_text())

    # code -> Counter keyed by distance bucket ("1","2","3","4-5","base")
    code_by_dist = defaultdict(Counter)
    total_by_dist = Counter()
    esc_payloads = {"near": [], "base": []}   # (run, level) for ESC events
    eyeball_dumps = []
    total_corrections = 0

    for entry in manifest["frames"]:
        data = (samples / entry["file"]).read_bytes()
        start_bit = entry["mb_data_bit_offset_within_frame"]
        records, corrections = decode_frame(data, start_bit)
        total_corrections += len(corrections)

        for mb_idx, rec in enumerate(records):
            if rec is None:
                continue
            nexts = [c for c in corrections if c > mb_idx]
            dist = (nexts[0] - mb_idx) if nexts else 99
            if dist == 1:
                bucket = "1"
            elif dist == 2:
                bucket = "2"
            elif dist == 3:
                bucket = "3"
            elif dist <= 5:
                bucket = "4-5"
            else:
                bucket = "base"

            for ev in rec["events"]:
                key = "ESC" if ev["kind"] == "ESC" else ev["code"]
                code_by_dist[key][bucket] += 1
                total_by_dist[bucket] += 1
                if ev["kind"] == "ESC":
                    side = "near" if dist <= 2 else ("base" if bucket == "base" else None)
                    if side:
                        esc_payloads[side].append((ev["run"], ev["level"]))

        # eyeball dump: last MB before each of the first 3 corrections (frame 0 only)
        if entry["file"] == "frame_00000.bin":
            for c in corrections[:3]:
                rec = records[c - 1]
                if rec:
                    eyeball_dumps.append((c, rec))

    # ── report ────────────────────────────────────────────────────────────────
    print(f"Frames: {len(manifest['frames'])}, total drift corrections: {total_corrections}")
    print(f"TCOEF events by distance-to-next-correction: "
          f"{dict(total_by_dist)}\n")

    print("=== Code enrichment at distance 1 (the MB right before a desync) ===")
    print("freq(d=1) / freq(base); codes with <5 occurrences at d=1 suppressed")
    print(f"{'code':>14} {'kind':>5} {'n(d1)':>6} {'n(d2)':>6} {'n(d3)':>6} "
          f"{'n(4-5)':>6} {'n(base)':>8} {'enrich':>7}")
    rows = []
    for code, cnts in code_by_dist.items():
        n1 = cnts["1"]
        if n1 < 5:
            continue
        f1 = n1 / max(total_by_dist["1"], 1)
        fb = cnts["base"] / max(total_by_dist["base"], 1)
        enrich = f1 / fb if fb > 0 else float("inf")
        kind = "ESC" if code == "ESC" else ("PROP" if code in PROP_CODES else "VLC")
        rows.append((enrich, code, kind, n1, cnts["2"], cnts["3"],
                     cnts["4-5"], cnts["base"]))
    rows.sort(reverse=True)
    for enrich, code, kind, n1, n2, n3, n45, nb in rows[:25]:
        e = f"{enrich:7.2f}" if enrich != float("inf") else "    inf"
        print(f"{code:>14} {kind:>5} {n1:>6} {n2:>6} {n3:>6} {n45:>6} {nb:>8} {e}")

    print("\n=== ESCAPE payload plausibility: near desync (d<=2) vs baseline ===")
    for side in ("near", "base"):
        pl = esc_payloads[side]
        if not pl:
            print(f"{side:>5}: no ESC events")
            continue
        runs = [r for r, _ in pl]
        lvls = [abs(l) for _, l in pl]
        big_lvl = sum(1 for l in lvls if l > 32) / len(lvls)
        big_run = sum(1 for r in runs if r > 20) / len(runs)
        print(f"{side:>5}: n={len(pl):4d}  mean|level|={sum(lvls)/len(lvls):6.1f}  "
              f"P(|level|>32)={big_lvl:.2f}  mean run={sum(runs)/len(runs):5.1f}  "
              f"P(run>20)={big_run:.2f}")

    print("\n=== Eyeball: frame_00000, last MB before first corrections ===")
    for c, rec in eyeball_dumps:
        print(f"\n-- MB {c-1} (correction follows at MB {c}, "
              f"MB spans bits {rec['start']}..{rec['end']}) --")
        for ev in rec["events"]:
            code_disp = ev["code"] if ev["kind"] != "ESC" else f"ESC({ev['code']})"
            print(f"  {ev['block']}  bit {ev['bitpos']:6d}  {code_disp:>20}  "
                  f"{ev['kind']:>4}  last={ev['last']} run={ev['run']:2d} "
                  f"level={ev['level']:4d}")


if __name__ == "__main__":
    main()
