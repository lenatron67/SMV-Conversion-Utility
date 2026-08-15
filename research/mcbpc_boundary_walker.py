"""
Extended MB chain walker: MCBPC -> CBPY -> [DQUANT] -> per-block (INTRADC + [TCOEFF]).

The original version could only chain through "DC-only" MBs (CBPY=0000, CBPC=00),
stopping at the first block with AC coefficients because TCOEFF table was unknown.
This version wires in TABLE 13/H.263 from tcoef_tables.py and handles ALL MBs.

Per-block structure (H.263 section 5.4, Figure 10, INTRA MB):
  For each block in order (Y1, Y2, Y3, Y4, Cb, Cr):
    INTRADC  [8 bits, always present for INTRA]
    TCOEFF*  [variable-length VLC events, present only if CBPY/CBPC coded flag set]
             Each TCOEFF event: VLC prefix -> (LAST, RUN, LEVEL) + 1 sign bit,
             OR 7-bit ESCAPE marker + 1-bit LAST + 6-bit RUN FLC + 8-bit LEVEL FLC.
             Read events until an event with LAST=1.

Key diagnostic question: does standard TABLE 13/H.263 let us chain through all
99 MBs of a QCIF frame? FFmpeg's known failure points for reference:
  frame_00000: fails at MB 2  ("illegal ac vlc code")
  frame_00001: fails at MB 8  ("run overflow")
  frame_00002: fails at MB 3  ("illegal dc")
  frame_00003: fails at MB 3  ("cbpy damaged")
If TABLE 13 is right -> expect success through all 99 MBs on some/all frames.
If TABLE 13 is wrong  -> expect failures clustered at consistent early MB indices.
"""
import json
from pathlib import Path

from bitreader import BitReader
from tcoef_tables import (
    TCOEF_VLC, ESCAPE_CODE, MAX_TCOEF_VLC_LEN,
    FORBIDDEN_LEVEL_BYTES, decode_run_flc, decode_level_flc,
)

SAMPLES = Path("samples")
manifest = json.loads((SAMPLES / "manifest.json").read_text())

QCIF_MB_COUNT = 11 * 9  # 99 macroblocks per QCIF frame

# ---------------------------------------------------------------------------
# TABLE 4/H.263 -- MCBPC VLC for I-pictures.
# code -> (mb_type, cbpc) ; mb_type 3=INTRA, 4=INTRA+Q ; cbpc is 2 bits
# ---------------------------------------------------------------------------
MCBPC_INTRA = {
    "1":         (3, 0b00),
    "001":       (3, 0b01),
    "010":       (3, 0b10),
    "011":       (3, 0b11),
    "0001":      (4, 0b00),
    "000001":    (4, 0b01),
    "000010":    (4, 0b10),
    "000011":    (4, 0b11),
    "000000001": ("stuffing", None),
}

# ---------------------------------------------------------------------------
# TABLE 10/H.263 -- CBPY VLC, INTRA column.
# code -> 4-bit luma coded-block pattern (bits 3..0 = Y1..Y4; 1 = AC-coded)
# ---------------------------------------------------------------------------
CBPY_INTRA = {
    "0011":   0b0000,
    "00101":  0b0001,
    "00100":  0b0010,
    "1001":   0b0011,
    "00011":  0b0100,
    "0111":   0b0101,
    "000010": 0b0110,
    "1011":   0b0111,
    "00010":  0b1000,
    "000011": 0b1001,
    "0101":   0b1010,
    "1010":   0b1011,
    "0100":   0b1100,
    "1000":   0b1101,
    "0110":   0b1110,
    "11":     0b1111,
}

FORBIDDEN_INTRADC = {0x00, 0x80}

# Combined lookup for TCOEFF: standard VLC entries + ESCAPE sentinel.
# Greedy prefix search returns "ESCAPE" for the 7-bit ESCAPE code, or a
# (last, run, level) tuple for any standard VLC entry.
_ESCAPE_SENTINEL = "ESCAPE"
_TCOEF_LOOKUP = {**TCOEF_VLC, ESCAPE_CODE: _ESCAPE_SENTINEL}
_TCOEF_MAX_LEN = MAX_TCOEF_VLC_LEN  # 12 bits (longest VLC prefix)

# Sanity cap: an 8x8 block has 64 coefficients; bail if we see more events
# than that without a LAST=1 -- it means the stream is desynced.
_MAX_TCOEF_EVENTS_PER_BLOCK = 64


def match_vlc(br: BitReader, table: dict, max_len: int):
    """Greedy prefix match of the next bits against `table`.
    Returns (value, code_string) or (None, None) on failure. Does not consume on failure."""
    if br.bits_remaining() < 1:
        return None, None
    peek_len = min(max_len, br.bits_remaining())
    peek = br.peek(peek_len)
    bits = format(peek, f"0{peek_len}b")
    for length in range(1, peek_len + 1):
        candidate = bits[:length]
        if candidate in table:
            br.skip(length)
            return table[candidate], candidate
    return None, None


def read_tcoeff_block(br: BitReader):
    """Read TCOEFF events for one coded block until LAST=1.
    Returns (events_list, None) on success, or (None, error_string) on failure.
    events_list entries: dicts with keys type/last/run/level (and code for VLC type)."""
    events = []
    while True:
        if len(events) >= _MAX_TCOEF_EVENTS_PER_BLOCK:
            return None, f"exceeded {_MAX_TCOEF_EVENTS_PER_BLOCK} events without LAST=1 (stream desynced)"

        val, code = match_vlc(br, _TCOEF_LOOKUP, _TCOEF_MAX_LEN)
        if val is None:
            return None, f"no TCOEFF VLC match (bits remaining: {br.bits_remaining()})"

        if val is _ESCAPE_SENTINEL:
            # 22-bit escape: 7-bit marker already consumed; read 1+6+8 = 15 more bits
            if br.bits_remaining() < 15:
                return None, "ran out of bits reading ESCAPE payload"
            last = br.read(1)
            run_bits = br.read(6)
            level_byte = br.read(8)
            if level_byte in FORBIDDEN_LEVEL_BYTES:
                return None, f"ESCAPE LEVEL byte forbidden: {hex(level_byte)}"
            run = decode_run_flc(run_bits)
            level = decode_level_flc(level_byte)
            events.append({"type": "escape", "last": last, "run": run, "level": level})
        else:
            last, run, level = val
            if br.bits_remaining() < 1:
                return None, "ran out of bits reading TCOEFF sign bit"
            sign = br.read(1)
            if sign:
                level = -level
            events.append({"type": "vlc", "last": last, "run": run, "level": level, "code": code})

        if last:
            break

    return events, None


def walk_frame(data: bytes, start_bit: int, frame_byte_len: int):
    """Decode the full MB layer of one frame using standard H.263 tables.
    Returns a dict with decode results and diagnostics."""
    br = BitReader(data, start_bit)
    total_bits = frame_byte_len * 8
    mb_log = []

    for mb_index in range(QCIF_MB_COUNT):
        mb_start_bit = br.pos

        # --- MCBPC ---
        mcbpc_val, mcbpc_code = match_vlc(br, MCBPC_INTRA, 9)
        if mcbpc_val is None:
            return _stop(mb_index, "MCBPC: no VLC match", mb_start_bit, total_bits, mb_log)
        mb_type, cbpc = mcbpc_val
        if mb_type == "stuffing":
            return _stop(mb_index, "MCBPC decoded as stuffing", mb_start_bit, total_bits, mb_log)

        # --- CBPY ---
        cbpy_val, cbpy_code = match_vlc(br, CBPY_INTRA, 6)
        if cbpy_val is None:
            return _stop(mb_index, "CBPY: no VLC match", mb_start_bit, total_bits, mb_log)

        # --- DQUANT (MB type 4 only) ---
        dquant = None
        if mb_type == 4:
            if br.bits_remaining() < 2:
                return _stop(mb_index, "ran out of bits reading DQUANT",
                             br.pos, total_bits, mb_log)
            dquant = br.read(2)

        # --- Per-block: INTRADC + [TCOEFF] ---
        # Block order: Y1, Y2, Y3, Y4, Cb, Cr
        # Coded flags: cbpy_val bits 3..0 for Y1..Y4; cbpc bits 1..0 for Cb, Cr
        coded_flags = [
            (cbpy_val >> 3) & 1,  # Y1
            (cbpy_val >> 2) & 1,  # Y2
            (cbpy_val >> 1) & 1,  # Y3
            (cbpy_val >> 0) & 1,  # Y4
            (cbpc    >> 1) & 1,   # Cb
            (cbpc    >> 0) & 1,   # Cr
        ]
        block_names = ["Y1", "Y2", "Y3", "Y4", "Cb", "Cr"]

        blocks = []
        for block_idx, (coded, bname) in enumerate(zip(coded_flags, block_names)):
            # INTRADC (always present for INTRA MBs)
            if br.bits_remaining() < 8:
                return _stop(mb_index,
                             f"ran out of bits reading INTRADC for block {bname}",
                             br.pos, total_bits, mb_log)
            intradc_byte = br.read(8)
            if intradc_byte in FORBIDDEN_INTRADC:
                return _stop(mb_index,
                             f"INTRADC forbidden value {hex(intradc_byte)} at block {bname}",
                             mb_start_bit, total_bits, mb_log)

            # TCOEFF (only for coded blocks)
            tcoeff_events = None
            if coded:
                tcoeff_events, err = read_tcoeff_block(br)
                if err:
                    return _stop(mb_index,
                                 f"TCOEFF error at block {bname} (MB {mb_index}): {err}",
                                 br.pos, total_bits, mb_log)

            blocks.append({
                "name": bname, "coded": bool(coded),
                "intradc": hex(intradc_byte),
                "tcoeff_event_count": len(tcoeff_events) if tcoeff_events else 0,
            })

        mb_log.append({
            "mb_index": mb_index, "start_bit": mb_start_bit,
            "mb_type": mb_type, "mcbpc_code": mcbpc_code,
            "cbpc": f"{cbpc:02b}", "cbpy_code": cbpy_code,
            "cbpy_pattern": f"{cbpy_val:04b}", "dquant": dquant,
            "blocks": blocks,
            "bits_consumed": br.pos - mb_start_bit,
        })

    return {
        "mb_count": QCIF_MB_COUNT,
        "stop_reason": f"SUCCESS -- decoded all {QCIF_MB_COUNT} MBs",
        "stop_bit": br.pos, "total_bits": total_bits, "log": mb_log,
    }


def _stop(mb_index, reason, stop_bit, total_bits, log):
    return {
        "mb_count": mb_index,
        "stop_reason": reason,
        "stop_bit": stop_bit,
        "total_bits": total_bits,
        "log": log,
    }


# ---------------------------------------------------------------------------
# Run across all sample frames and report
# ---------------------------------------------------------------------------
FFMPEG_FAILURES = {
    "frame_00000.bin": (2,  "illegal ac vlc code"),
    "frame_00001.bin": (8,  "run overflow"),
    "frame_00002.bin": (3,  "illegal dc"),
    "frame_00003.bin": (3,  "cbpy damaged"),
}

print("Extended MB walker: MCBPC -> CBPY -> [DQUANT] -> per-block (INTRADC + TCOEFF)")
print(f"Using standard TABLE 4/10/13/14 from H.263 spec. QCIF = {QCIF_MB_COUNT} MBs/frame.")
print(f"{'Frame':<20} {'Decoded':>8} {'Stop reason'}")
print("-" * 80)

results = []
for entry in manifest["frames"]:
    path = SAMPLES / entry["file"]
    data = path.read_bytes()
    start_bit = entry["mb_data_bit_offset_within_frame"]
    result = walk_frame(data, start_bit, entry["byte_length"])
    results.append((entry["file"], result))

    fname = entry["file"]
    decoded = result["mb_count"]
    reason = result["stop_reason"]
    ffmpeg_ref = ""
    if fname in FFMPEG_FAILURES:
        ffmpeg_mb, ffmpeg_err = FFMPEG_FAILURES[fname]
        ffmpeg_ref = f"  [FFmpeg fails @ MB {ffmpeg_mb}: {ffmpeg_err}]"
    print(f"{fname:<20} {decoded:>3}/{QCIF_MB_COUNT}  {reason}{ffmpeg_ref}")

# Summary
successes = sum(1 for _, r in results if r["mb_count"] == QCIF_MB_COUNT)
fail_mb_counts = [r["mb_count"] for _, r in results if r["mb_count"] < QCIF_MB_COUNT]
print(f"\nSummary: {successes}/{len(results)} frames fully decoded.")
if fail_mb_counts:
    print(f"Failed frames decoded MB counts: min={min(fail_mb_counts)}, "
          f"max={max(fail_mb_counts)}, mean={sum(fail_mb_counts)/len(fail_mb_counts):.1f}")

# Detailed breakdown for the first 5 frames (where FFmpeg errors are characterised)
print(f"\n=== Per-MB detail for first 5 frames ===")
for fname, result in results[:5]:
    print(f"\n{fname}: {result['mb_count']}/{QCIF_MB_COUNT} MBs, stop: {result['stop_reason']}")
    for mb in result["log"][:5]:  # show first 5 MBs per frame
        coded_blocks = [b["name"] for b in mb["blocks"] if b["coded"]]
        tcoeff_counts = [b["tcoeff_event_count"] for b in mb["blocks"] if b["coded"]]
        print(f"  MB {mb['mb_index']:3d} @ bit {mb['start_bit']:6d}: "
              f"type={mb['mb_type']} CBPY={mb['cbpy_pattern']} CBPC={mb['cbpc']}  "
              f"coded_blocks={coded_blocks}  tcoeff_events={tcoeff_counts}  "
              f"[{mb['bits_consumed']} bits total]")
    if len(result["log"]) > 5:
        print(f"  ... ({len(result['log'])} MBs decoded total, showing first 5)")

# For any fully-decoded frames, show coefficient statistics
full_frames = [(fname, result) for fname, result in results if result["mb_count"] == QCIF_MB_COUNT]
if full_frames:
    print(f"\n=== Coefficient statistics for fully-decoded frames ===")
    for fname, result in full_frames:
        all_events = []
        for mb in result["log"]:
            for block in mb["blocks"]:
                pass  # tcoeff events not stored in summary; see below
        print(f"{fname}: fully decoded ({result['mb_count']} MBs)")
else:
    print(f"\nNo frames fully decoded -- standard TABLE 13 likely differs from the proprietary table.")
    print("Failure pattern above indicates where to focus reverse-engineering effort.")
