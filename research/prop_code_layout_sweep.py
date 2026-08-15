"""
Sweep the assumed payload layout of the two proprietary TCOEF codes
(`000000000101` LAST=0, `000000000100` LAST=1).

Hypothesis: the codes are ESCAPE-like — the 12-bit code is followed by N
payload bits (RUN/LEVEL/sign fields of unknown width).  Our current
placeholder assumes N=1 (a bare sign bit).  If N is wrong, every MB that
contains one of these codes desyncs right after it, which is what forces the
~40-50-bit drift corrections in reconstruct_frame0.py.

Test: for each N in 0..24, decode all 99 MBs of frame_00000 with the
drift-correcting walker, consuming 12+N bits per proprietary code.  Score
each N by (a) how many drift corrections were needed, (b) total absolute
drift, (c) spatial smoothness of the resulting Y1 INTRADC grid (mean abs
diff between horizontal neighbours — real images are locally smooth, noise
averages ~85).

The true N should minimize corrections and produce a smoother grid.
"""
import json
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
_PROP0 = "PROP_LAST0"   # 000000000101
_PROP1 = "PROP_LAST1"   # 000000000100
LOOKUP = {**TCOEF_VLC, ESCAPE_CODE: _ESC,
          "000000000101": _PROP0, "000000000100": _PROP1}
MAX_LEN = max(len(c) for c in LOOKUP)

SCAN_WINDOW = 50
QCIF_MB = 99


def _mvlc(br, table, max_len):
    if br.bits_remaining() < 1:
        return None
    peek_len = min(max_len, br.bits_remaining())
    bits = format(br.peek(peek_len), f"0{peek_len}b")
    for length in range(1, peek_len + 1):
        key = bits[:length]
        if key in table:
            br.skip(length)
            return table[key]
    return None


def try_decode_mb(data, pos, prop_n):
    """Decode one MB; proprietary TCOEF codes consume 12 + prop_n bits."""
    br = BitReader(data, pos)
    if br.bits_remaining() < 20:
        return False, pos, None

    mcv = _mvlc(br, MCBPC, ML_MCBPC)
    if mcv is None or mcv[0] == "S":
        return False, pos, None
    mbt, cbpc = mcv

    cbpyv = _mvlc(br, CBPY, ML_CBPY)
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

    for c in coded:
        if br.bits_remaining() < 8:
            return False, pos, None
        idc = br.read(8)
        if idc in FORBIDDEN_INTRADC:
            return False, pos, None
        idc_list.append(idc)

        if c:
            tc = 0
            while tc < 64:
                v = _mvlc(br, LOOKUP, MAX_LEN)
                if v is None:
                    return False, pos, None
                if v is _ESC:
                    if br.bits_remaining() < 15:
                        return False, pos, None
                    last = br.read(1)
                    br.read(6)
                    lb = br.read(8)
                    if lb in FORBIDDEN_LEVEL_BYTES:
                        return False, pos, None
                elif v is _PROP0 or v is _PROP1:
                    if br.bits_remaining() < prop_n:
                        return False, pos, None
                    if prop_n:
                        br.read(prop_n)
                    last = 1 if v is _PROP1 else 0
                else:
                    last, run, level = v
                    if br.bits_remaining() < 1:
                        return False, pos, None
                    br.read(1)   # sign
                tc += 1
                if last:
                    break
            else:
                return False, pos, None

    return True, br.pos, idc_list


def score_start(data, pos, prop_n, ahead=6):
    score = 0
    cur = pos
    for _ in range(ahead):
        ok, nxt, _ = try_decode_mb(data, cur, prop_n)
        if not ok:
            break
        score += 1
        cur = nxt
    return score


def decode_frame(data, start_bit, prop_n):
    mb_idc = []
    cur = start_bit
    corrections = []   # (mb_idx, drift)
    n_failed = 0

    for mb_idx in range(QCIF_MB):
        ok, nxt, idc_list = try_decode_mb(data, cur, prop_n)
        if not ok:
            best_score, best_pos = 0, cur
            for delta in range(-SCAN_WINDOW, SCAN_WINDOW + 1):
                cand = cur + delta
                if cand < start_bit:
                    continue
                s = score_start(data, cand, prop_n)
                if s > best_score:
                    best_score, best_pos = s, cand
            if best_score > 0:
                ok, nxt, idc_list = try_decode_mb(data, best_pos, prop_n)
                corrections.append((mb_idx, best_pos - cur))
                cur = best_pos
            else:
                mb_idc.append(None)
                n_failed += 1
                continue
        mb_idc.append(idc_list)
        cur = nxt
    return mb_idc, corrections, n_failed


def grid_smoothness(mb_idc):
    """Mean |diff| between horizontally adjacent Y1 values (lower = smoother)."""
    diffs = []
    for row in range(9):
        for col in range(10):
            a = mb_idc[row * 11 + col]
            b = mb_idc[row * 11 + col + 1]
            if a and b:
                diffs.append(abs(a[0] - b[0]))
    return sum(diffs) / len(diffs) if diffs else float("nan")


def main():
    samples = Path("samples")
    manifest = json.loads((samples / "manifest.json").read_text())
    entry = next(e for e in manifest["frames"] if e["file"] == "frame_00000.bin")
    data = (samples / entry["file"]).read_bytes()
    start_bit = entry["mb_data_bit_offset_within_frame"]

    print("frame_00000: sweeping proprietary-code payload size N (bits after the 12-bit code)")
    print(f"{'N':>3} {'corrections':>11} {'failed':>6} {'tot|drift|':>10} {'Y1 smooth':>9}")
    results = []
    for n in range(25):
        mb_idc, corrections, n_failed = decode_frame(data, start_bit, n)
        tot_drift = sum(abs(d) for _, d in corrections)
        smooth = grid_smoothness(mb_idc)
        results.append((n, len(corrections), n_failed, tot_drift, smooth))
        print(f"{n:>3} {len(corrections):>11} {n_failed:>6} {tot_drift:>10} {smooth:>9.1f}")

    best = min(results, key=lambda r: (r[1] + 10 * r[2], r[3]))
    print(f"\nBest by corrections: N={best[0]} "
          f"({best[1]} corrections, {best[2]} failed, smoothness {best[4]:.1f})")


if __name__ == "__main__":
    main()
