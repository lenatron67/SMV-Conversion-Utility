"""
Sweep the assumed ESCAPE payload width.

Standard H.263 TABLE 13 ESCAPE = `0000011` + 15 payload bits (LAST 1, RUN 6,
LEVEL 8).  Hypothesis: the proprietary codec uses a different payload width W,
making every ESCAPE event silently mis-consume |W-15| bits — a pervasive
desync source that would explain culprit positions clustering at/just after
ESC-parsed bits (see desync_localize results).

Counter-evidence to beat: tcoef_stats.py's LAST-count check (204=204) passed
WITH W=15 on the 49 cleanly-decoded MBs — so W=15 works at least for the
escapes in those MBs.  If the sweep is flat here too, that hypothesis dies
and W=15 stands confirmed.

Test: decode frames with the drift-correcting walker for W = 12..22
(forbidden-byte check only applied when W==15, since field layout is unknown
otherwise), score by corrections / total |drift| / Y1-grid smoothness.
"""
import json
import sys
from pathlib import Path

from bitreader import BitReader
from tcoef_tables import TCOEF_VLC, ESCAPE_CODE, FORBIDDEN_LEVEL_BYTES

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


def try_decode_mb(data, pos, esc_w):
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
                    if br.bits_remaining() < esc_w:
                        return False, pos, None
                    last = br.read(1)            # LAST assumed first either way
                    rest = br.read(esc_w - 1)
                    if esc_w == 15:
                        lb = rest & 0xFF
                        if lb in FORBIDDEN_LEVEL_BYTES:
                            return False, pos, None
                else:
                    last, run, level = v
                    if br.bits_remaining() < 1:
                        return False, pos, None
                    br.read(1)
                tc += 1
                if last:
                    break
            else:
                return False, pos, None

    return True, br.pos, idc_list


def score_start(data, pos, esc_w, ahead=6):
    score, cur = 0, pos
    for _ in range(ahead):
        ok, nxt, _ = try_decode_mb(data, cur, esc_w)
        if not ok:
            break
        score += 1
        cur = nxt
    return score


def decode_frame(data, start_bit, esc_w):
    mb_idc = []
    corrections = []
    n_failed = 0
    cur = start_bit
    for mb_idx in range(QCIF_MB):
        ok, nxt, idc = try_decode_mb(data, cur, esc_w)
        if not ok:
            best_score, best_pos = 0, cur
            for delta in range(-SCAN_WINDOW, SCAN_WINDOW + 1):
                cand = cur + delta
                if cand < start_bit:
                    continue
                s = score_start(data, cand, esc_w)
                if s > best_score:
                    best_score, best_pos = s, cand
            if best_score > 0:
                corrections.append((mb_idx, best_pos - cur))
                cur = best_pos
                ok, nxt, idc = try_decode_mb(data, cur, esc_w)
            else:
                mb_idc.append(None)
                n_failed += 1
                continue
        mb_idc.append(idc)
        cur = nxt
    return mb_idc, corrections, n_failed


def grid_smoothness(mb_idc):
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
    names = sys.argv[1:] or ["frame_00000", "frame_00001", "frame_09365"]
    frames = [e for e in manifest["frames"]
              if any(n in e["file"] for n in names)]

    for entry in frames:
        data = (samples / entry["file"]).read_bytes()
        start_bit = entry["mb_data_bit_offset_within_frame"]
        print(f"\n{entry['file']}: ESC payload width sweep "
              f"(standard = 15 bits after the 7-bit prefix)")
        print(f"{'W':>3} {'corrections':>11} {'failed':>6} {'tot|drift|':>10} {'Y1 smooth':>9}")
        for w in range(12, 23):
            mb_idc, corrections, n_failed = decode_frame(data, start_bit, w)
            tot = sum(abs(d) for _, d in corrections)
            sm = grid_smoothness(mb_idc)
            star = "  <- standard" if w == 15 else ""
            print(f"{w:>3} {len(corrections):>11} {n_failed:>6} {tot:>10} {sm:>9.1f}{star}")


if __name__ == "__main__":
    main()
