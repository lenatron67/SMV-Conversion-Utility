"""
Reconstruct a DC-only grayscale image of frame_00000 using the drift-correcting
MB decoder.

The decoder walks all 99 MBs (11×9 QCIF grid).  When a MB fails to parse
cleanly (because a proprietary TCOEF code causes bit-count drift), it scans
±SCAN_WINDOW bits to find the true MB start (best downstream parse score).

INTRADC values are used directly as pixel brightness (1 INTRADC unit ≈ 1 luma
level for an 8×8 constant block).  Each block becomes an 8×8 constant-color
tile in the 176×144 output image.

Outputs:
  frame0_dc_gray.png  — grayscale luma image (M4 visual check)

Dependencies: PIL (already installed), bitreader.py, tcoef_tables.py
"""
import json
from pathlib import Path

from PIL import Image

from bitreader import BitReader
from tcoef_tables import (
    TCOEF_VLC, ESCAPE_CODE, MAX_TCOEF_VLC_LEN,
    FORBIDDEN_LEVEL_BYTES, decode_run_flc, decode_level_flc,
)

# ── VLC tables ────────────────────────────────────────────────────────────────
MCBPC = {
    "1":         (3, 0b00),
    "001":       (3, 0b01),
    "010":       (3, 0b10),
    "011":       (3, 0b11),
    "0001":      (4, 0b00),
    "000001":    (4, 0b01),
    "000010":    (4, 0b10),
    "000011":    (4, 0b11),
    "000000001": ("S", None),   # stuffing
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
# Two confirmed proprietary codes in the `000000000x` prefix space
# (12-bit codes, RUN/LEVEL are structural placeholders — only length matters)
LOOKUP["000000000101"] = (0, 0, 1)   # LAST=0
LOOKUP["000000000100"] = (1, 0, 1)   # LAST=1
MAX_LEN = max(len(c) for c in LOOKUP)

SCAN_WINDOW = 50
QCIF_MB = 99  # 11 × 9


# ── core helpers ──────────────────────────────────────────────────────────────
def _mvlc(br: BitReader, table: dict, max_len: int):
    """Longest-prefix VLC match; returns (value, code_str) or (None, None)."""
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


def try_decode_mb(data: bytes, pos: int):
    """Attempt to decode one complete MB starting at bit position `pos`.

    Returns (ok, next_pos, mbt, cbpc, cbpyv, idc_list, tcoeff_list).
    On failure returns (False, pos, None, None, None, None, None).
    """
    br = BitReader(data, pos)
    if br.bits_remaining() < 20:
        return False, pos, None, None, None, None, None

    mcv, _ = _mvlc(br, MCBPC, ML_MCBPC)
    if mcv is None or mcv[0] == "S":
        return False, pos, None, None, None, None, None
    mbt, cbpc = mcv

    cbpyv, _ = _mvlc(br, CBPY, ML_CBPY)
    if cbpyv is None:
        return False, pos, None, None, None, None, None

    if mbt == 4:
        if br.bits_remaining() < 2:
            return False, pos, None, None, None, None, None
        br.read(2)   # DQUANT

    coded = [
        (cbpyv >> 3) & 1, (cbpyv >> 2) & 1,
        (cbpyv >> 1) & 1, (cbpyv >> 0) & 1,
        (cbpc  >> 1) & 1, (cbpc  >> 0) & 1,
    ]
    idc_list = []
    tcoeff_list = []

    for c in coded:
        if br.bits_remaining() < 8:
            return False, pos, None, None, None, None, None
        idc = br.read(8)
        if idc in FORBIDDEN_INTRADC:
            return False, pos, None, None, None, None, None
        idc_list.append(idc)

        evts = []
        if c:
            tc = 0
            while tc < 64:
                v, _ = _mvlc(br, LOOKUP, MAX_LEN)
                if v is None:
                    return False, pos, None, None, None, None, None
                if v is _ESC:
                    if br.bits_remaining() < 15:
                        return False, pos, None, None, None, None, None
                    last = br.read(1)
                    run = decode_run_flc(br.read(6))
                    lb = br.read(8)
                    if lb in FORBIDDEN_LEVEL_BYTES:
                        return False, pos, None, None, None, None, None
                    level = decode_level_flc(lb)
                else:
                    last, run, level = v
                    if br.bits_remaining() < 1:
                        return False, pos, None, None, None, None, None
                    sign = br.read(1)
                    if sign:
                        level = -level
                evts.append((last, run, level))
                tc += 1
                if last:
                    break
            else:
                return False, pos, None, None, None, None, None
        tcoeff_list.append(evts)

    return True, br.pos, mbt, cbpc, cbpyv, idc_list, tcoeff_list


def score_start(data: bytes, pos: int, ahead: int = 6, prev_idc=None) -> int:
    """Score a candidate MB-start position.

    H2 fix: structural parse alone is not enough — random positions inside
    TCOEF data pass the forbidden-byte check ~95% of the time, so many wrong
    candidates parse 6 MBs deep.  Add two plausibility terms:
      - range: INTRADC values of every look-ahead MB inside [40, 220]
        (natural indoor video) earn points; out-of-range values don't.
      - continuity: the first MB's mean luma INTRADC should be close to the
        previously decoded MB's (natural images are locally smooth; garbage
        positions give large jumps).
    Structural depth still dominates (1000/MB) so a position that parses
    further always beats a shallower one; plausibility breaks the ties.
    """
    score = 0
    cur = pos
    first_idc = None
    for i in range(ahead):
        ok, nxt, _mbt, _cbpc, _cbpyv, idc_list, _ = try_decode_mb(data, cur)
        if not ok:
            break
        score += 1000
        score += sum(10 for v in idc_list if 40 <= v <= 220)
        if i == 0:
            first_idc = idc_list
        cur = nxt
    if first_idc and prev_idc:
        mean_new = sum(first_idc[:4]) / 4
        mean_prev = sum(prev_idc[:4]) / 4
        score += max(0, 60 - abs(mean_new - mean_prev))
    return score


# ── load frame_00000 ─────────────────────────────────────────────────────────
SAMPLES = Path("samples")
manifest = json.loads((SAMPLES / "manifest.json").read_text())
entry = next(e for e in manifest["frames"] if e["file"] == "frame_00000.bin")
data = (SAMPLES / entry["file"]).read_bytes()
start_bit = entry["mb_data_bit_offset_within_frame"]

print(f"frame_00000.bin: {entry['byte_length']} bytes, MB data starts at bit {start_bit}")
print(f"Decoding {QCIF_MB} MBs with ±{SCAN_WINDOW}-bit drift correction ...\n")

# ── main decode loop ──────────────────────────────────────────────────────────
mb_idc = []   # list of (idc_list or None) per MB, in MB order
cur = start_bit
n_drift = 0
n_failed = 0

for mb_idx in range(QCIF_MB):
    ok, nxt, mbt, cbpc, cbpyv, idc_list, _ = try_decode_mb(data, cur)

    if not ok:
        # Scan ±SCAN_WINDOW for a better start position
        prev_idc = next((idc for idc in reversed(mb_idc) if idc), None)
        best_score = 0
        best_pos = cur
        for delta in range(-SCAN_WINDOW, SCAN_WINDOW + 1):
            candidate = cur + delta
            if candidate < start_bit:
                continue
            s = score_start(data, candidate, prev_idc=prev_idc)
            if s > best_score:
                best_score = s
                best_pos = candidate

        drift = best_pos - cur
        if best_score > 0:
            ok, nxt, mbt, cbpc, cbpyv, idc_list, _ = try_decode_mb(data, best_pos)
            print(f"  MB {mb_idx:2d}: drift correction {drift:+d} bits  (score={best_score})")
            n_drift += 1
            cur = best_pos
        else:
            print(f"  MB {mb_idx:2d}: TOTAL FAILURE — no valid start in ±{SCAN_WINDOW} bits")
            mb_idc.append(None)
            n_failed += 1
            continue

    mb_idc.append(idc_list)
    cur = nxt

print(f"\nDecoded {QCIF_MB - n_failed}/{QCIF_MB} MBs  ({n_drift} drift corrections, {n_failed} failures)")

# ── Step 1 diagnostic (H1 vs H2): Y1 INTRADC grid ────────────────────────────
# MBs 0-2 decode before the first drift correction, so their INTRADC values are
# trustworthy regardless of which hypothesis is right.  If they are mutually
# close and plausible while the rest of the grid is noisy -> H2 (drift
# correction picks wrong restart positions).  If MBs 0-2 also swing wildly ->
# H1 (CBPY inversion missing, INTRADC read from inside TCOEF data).
print("\nY1 INTRADC grid (11 cols × 9 rows):")
for row in range(9):
    vals = []
    for col in range(11):
        mb_idx = row * 11 + col
        idc = mb_idc[mb_idx]
        vals.append(f"{idc[0]:3d}" if idc else "  ?")
    print(" ".join(vals))

print("\nDiagnostic — MBs 0-2 (decoded with NO drift correction):")
for mb_idx in range(3):
    idc = mb_idc[mb_idx]
    if idc:
        print(f"  MB {mb_idx}: Y1-Y4 = {idc[:4]}  Cb,Cr = {idc[4:]}")
    else:
        print(f"  MB {mb_idx}: FAILED")

# ── INTRADC statistics ────────────────────────────────────────────────────────
all_idc = [v for idc in mb_idc if idc for v in idc[:4]]   # luma only
if all_idc:
    mean_idc = sum(all_idc) / len(all_idc)
    print(f"Luma INTRADC: min={min(all_idc)} max={max(all_idc)} mean={mean_idc:.1f}  "
          f"(expect ~100-180 for a typical indoor webcam scene)")

# ── render 176×144 grayscale image ────────────────────────────────────────────
img = Image.new("L", (176, 144), 128)   # default mid-gray for missing blocks

BLOCK_OFFSETS = [(0, 0), (8, 0), (0, 8), (8, 8)]   # Y1 Y2 Y3 Y4 within 16×16 MB

for mb_idx, idc_list in enumerate(mb_idc):
    if idc_list is None:
        continue
    row_mb = mb_idx // 11
    col_mb = mb_idx % 11
    x0 = col_mb * 16
    y0 = row_mb * 16

    for bi in range(4):   # luma blocks Y1-Y4
        px = min(255, max(0, idc_list[bi]))
        dx, dy = BLOCK_OFFSETS[bi]
        for row in range(8):
            for col in range(8):
                img.putpixel((x0 + dx + col, y0 + dy + row), px)

out_path = Path("frame0_dc_gray.png")
img.save(out_path)
print(f"\nSaved: {out_path}  ({img.width}×{img.height} px)")
print("Open frame0_dc_gray.png and check: does it look like a person at a computer?")
print("  Yes -> M4 milestone reached")
print("  No  -> drift-correction is picking wrong restart positions; stronger criterion needed")
