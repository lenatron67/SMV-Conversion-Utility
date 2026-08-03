"""
Track B: Statistical analysis of successfully-decoded TCOEFF events.

Runs the MB walker (TABLE 13/H.263) and pools TCOEFF events from every MB
that decoded without error.  Checks whether RUN/LEVEL/LAST distributions
look physically plausible for DCT video coefficients.

Expected for real intra-coded video:
  - RUN: very heavy at 0, rapid exponential-ish falloff (most nonzero
    coefficients are adjacent or near-adjacent in the zigzag)
  - |LEVEL|: heavy at 1-2, rapid falloff (quantized DCT values are small)
  - Events per coded block: typically 1-15
  - Escape-coded events: rare (< 5% of total)

If distributions look flat/uniform or dominated by extreme values it means
TABLE 13's "successful" decodes are actually garbage -- the desync starts
at the very first TCOEFF event, earlier than the explicit VLC-no-match
failures.
"""
import json
from collections import Counter
from pathlib import Path

from bitreader import BitReader
from tcoef_tables import (
    TCOEF_VLC, ESCAPE_CODE, MAX_TCOEF_VLC_LEN,
    FORBIDDEN_LEVEL_BYTES, decode_run_flc, decode_level_flc,
)

SAMPLES = Path("samples")
manifest = json.loads((SAMPLES / "manifest.json").read_text())
QCIF_MB_COUNT = 11 * 9  # 99

# ── same VLC tables as mcbpc_boundary_walker.py ──────────────────────────────
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
CBPY_INTRA = {
    "0011":   0b0000, "00101":  0b0001, "00100":  0b0010, "1001":   0b0011,
    "00011":  0b0100, "0111":   0b0101, "000010": 0b0110, "1011":   0b0111,
    "00010":  0b1000, "000011": 0b1001, "0101":   0b1010, "1010":   0b1011,
    "0100":   0b1100, "1000":   0b1101, "0110":   0b1110, "11":     0b1111,
}
FORBIDDEN_INTRADC = {0x00, 0x80}
_ESCAPE_SENTINEL = "ESCAPE"
_TCOEF_LOOKUP = {**TCOEF_VLC, ESCAPE_CODE: _ESCAPE_SENTINEL}
_TCOEF_MAX_LEN = MAX_TCOEF_VLC_LEN


def _match_vlc(br, table, max_len):
    if br.bits_remaining() < 1:
        return None, None
    peek_len = min(max_len, br.bits_remaining())
    bits = format(br.peek(peek_len), f"0{peek_len}b")
    for length in range(1, peek_len + 1):
        if bits[:length] in table:
            br.skip(length)
            return table[bits[:length]], bits[:length]
    return None, None


def _read_tcoeff_block(br):
    """Read TCOEFF events until LAST=1.  Returns (events, None) or (None, err)."""
    events = []
    while len(events) < 64:
        val, code = _match_vlc(br, _TCOEF_LOOKUP, _TCOEF_MAX_LEN)
        if val is None:
            return None, "no VLC match"
        if val is _ESCAPE_SENTINEL:
            if br.bits_remaining() < 15:
                return None, "ran out of bits in ESCAPE payload"
            last = br.read(1)
            run = decode_run_flc(br.read(6))
            level_byte = br.read(8)
            if level_byte in FORBIDDEN_LEVEL_BYTES:
                return None, f"ESCAPE forbidden LEVEL {hex(level_byte)}"
            level = decode_level_flc(level_byte)
            events.append({"type": "esc", "last": last, "run": run, "level": level})
        else:
            last, run, level = val
            if br.bits_remaining() < 1:
                return None, "ran out of bits reading sign"
            sign = br.read(1)
            if sign:
                level = -level
            events.append({"type": "vlc", "last": last, "run": run, "level": level, "code": code})
        if last:
            break
    else:
        return None, ">64 events without LAST=1 (desync)"
    return events, None


def walk_frame_with_events(data: bytes, start_bit: int, frame_byte_len: int):
    """Walk MB layer; store full TCOEFF events for each decoded block."""
    br = BitReader(data, start_bit)
    total_bits = frame_byte_len * 8
    mb_log = []

    for mb_index in range(QCIF_MB_COUNT):
        mb_start_bit = br.pos

        mcbpc_val, mcbpc_code = _match_vlc(br, MCBPC_INTRA, 9)
        if mcbpc_val is None:
            return mb_log, mb_index, "MCBPC no VLC match"
        mb_type, cbpc = mcbpc_val
        if mb_type == "stuffing":
            return mb_log, mb_index, "MCBPC stuffing"

        cbpy_val, _ = _match_vlc(br, CBPY_INTRA, 6)
        if cbpy_val is None:
            return mb_log, mb_index, "CBPY no VLC match"

        if mb_type == 4:
            if br.bits_remaining() < 2:
                return mb_log, mb_index, "ran out reading DQUANT"
            br.read(2)  # dquant consumed, not stored

        coded_flags = [
            (cbpy_val >> 3) & 1, (cbpy_val >> 2) & 1,
            (cbpy_val >> 1) & 1, (cbpy_val >> 0) & 1,
            (cbpc    >> 1) & 1,  (cbpc    >> 0) & 1,
        ]
        block_names = ["Y1", "Y2", "Y3", "Y4", "Cb", "Cr"]
        blocks = []

        for coded, bname in zip(coded_flags, block_names):
            if br.bits_remaining() < 8:
                return mb_log, mb_index, f"ran out reading INTRADC ({bname})"
            intradc = br.read(8)
            if intradc in FORBIDDEN_INTRADC:
                return mb_log, mb_index, f"INTRADC forbidden {hex(intradc)} ({bname})"
            tcoeff_events = []
            if coded:
                evs, err = _read_tcoeff_block(br)
                if err:
                    return mb_log, mb_index, f"TCOEFF {bname}: {err}"
                tcoeff_events = evs
            blocks.append({"name": bname, "coded": bool(coded),
                           "intradc": intradc, "tcoeff": tcoeff_events})

        mb_log.append({"mb_index": mb_index, "cbpy": cbpy_val,
                        "cbpc": cbpc, "blocks": blocks})

    return mb_log, QCIF_MB_COUNT, "SUCCESS"


# ── run all 15 sample frames ──────────────────────────────────────────────────
all_events = []        # every TCOEFF event from decoded MBs
events_per_block = []  # event count per coded block
intradc_values = []    # INTRADC bytes from all decoded blocks

frame_stats = []
for entry in manifest["frames"]:
    data = (SAMPLES / entry["file"]).read_bytes()
    mb_log, mb_count, stop = walk_frame_with_events(
        data, entry["mb_data_bit_offset_within_frame"], entry["byte_length"]
    )
    frame_stats.append((entry["file"], mb_count, stop))
    for mb in mb_log:
        for blk in mb["blocks"]:
            intradc_values.append(blk["intradc"])
            if blk["coded"]:
                events_per_block.append(len(blk["tcoeff"]))
                all_events.extend(blk["tcoeff"])

# ── summary ───────────────────────────────────────────────────────────────────
print("=== Frame decode summary ===")
total_mbs = 0
for fname, mb_count, stop in frame_stats:
    print(f"  {fname}: {mb_count:2d}/{QCIF_MB_COUNT} MBs  stop={stop}")
    total_mbs += mb_count

n_events   = len(all_events)
n_vlc      = sum(1 for e in all_events if e["type"] == "vlc")
n_esc      = sum(1 for e in all_events if e["type"] == "esc")
n_blocks   = len(events_per_block)
n_intradc  = len(intradc_values)

print(f"\n=== Totals ===")
print(f"  Decoded MBs (across all frames): {total_mbs}")
print(f"  Coded blocks decoded:            {n_blocks}")
print(f"  TCOEFF events total:             {n_events}  (VLC={n_vlc}, ESC={n_esc})")
print(f"  INTRADC values collected:        {n_intradc}")

if n_events == 0:
    print("\nNo TCOEFF events collected -- nothing to analyse.")
    raise SystemExit

# ── RUN distribution ─────────────────────────────────────────────────────────
print(f"\n=== RUN distribution (all {n_events} events) ===")
run_counter = Counter(e["run"] for e in all_events)
max_run_shown = max(run_counter) if run_counter else 0
print(f"  {'RUN':>4}  {'count':>6}  {'%':>6}  bar")
cumulative = 0
for run in range(min(max_run_shown + 1, 30)):
    cnt = run_counter.get(run, 0)
    cumulative += cnt
    pct = cnt / n_events * 100
    bar = "#" * min(int(pct / 2), 40)
    print(f"  {run:>4}  {cnt:>6}  {pct:>5.1f}%  {bar}")
if max_run_shown >= 30:
    remainder = sum(v for k, v in run_counter.items() if k >= 30)
    print(f"  {'30+':>4}  {remainder:>6}  {remainder/n_events*100:>5.1f}%")

# ── |LEVEL| distribution ─────────────────────────────────────────────────────
print(f"\n=== |LEVEL| distribution (all {n_events} events) ===")
abs_level_counter = Counter(abs(e["level"]) for e in all_events)
max_level_shown = max(abs_level_counter) if abs_level_counter else 0
print(f"  {'|LEV|':>6}  {'count':>6}  {'%':>6}  bar")
for lev in range(1, min(max_level_shown + 1, 30)):
    cnt = abs_level_counter.get(lev, 0)
    pct = cnt / n_events * 100
    bar = "#" * min(int(pct / 2), 40)
    print(f"  {lev:>6}  {cnt:>6}  {pct:>5.1f}%  {bar}")
if max_level_shown >= 30:
    remainder = sum(v for k, v in abs_level_counter.items() if k >= 30)
    print(f"  {'30+':>6}  {remainder:>6}  {remainder/n_events*100:>5.1f}%")

# ── signed LEVEL balance ──────────────────────────────────────────────────────
n_pos = sum(1 for e in all_events if e["level"] > 0)
n_neg = sum(1 for e in all_events if e["level"] < 0)
print(f"\n  Sign balance: +{n_pos} / -{n_neg}  "
      f"(ratio {n_pos/(n_neg or 1):.3f}, expect ~1.0 for real video)")

# ── events-per-coded-block distribution ──────────────────────────────────────
print(f"\n=== Events per coded block ({n_blocks} coded blocks) ===")
epb_counter = Counter(events_per_block)
print(f"  {'#evts':>6}  {'blocks':>6}  {'%':>6}  bar")
for n in range(0, min(max(epb_counter or [0]) + 1, 25)):
    cnt = epb_counter.get(n, 0)
    pct = cnt / n_blocks * 100 if n_blocks else 0
    bar = "#" * min(int(pct / 2), 40)
    print(f"  {n:>6}  {cnt:>6}  {pct:>5.1f}%  {bar}")

# ── LAST flag sanity ──────────────────────────────────────────────────────────
last1_count = sum(1 for e in all_events if e["last"] == 1)
last0_count = n_events - last1_count
print(f"\n=== LAST flag ===")
print(f"  LAST=0: {last0_count}  LAST=1: {last1_count}")
print(f"  LAST=1 count should equal number of coded blocks ({n_blocks}): "
      f"{'OK' if last1_count == n_blocks else f'MISMATCH -- off by {last1_count - n_blocks}'}")

# ── INTRADC sanity ────────────────────────────────────────────────────────────
print(f"\n=== INTRADC sanity ({n_intradc} values) ===")
intradc_counter = Counter(intradc_values)
mean_dc = sum(intradc_values) / n_intradc if n_intradc else 0
print(f"  Mean INTRADC byte: {mean_dc:.1f}  (expect ~128 for typical mid-gray luma)")
print(f"  Min: {min(intradc_values) if intradc_values else 'n/a'}  "
      f"Max: {max(intradc_values) if intradc_values else 'n/a'}")
print(f"  Distinct values:   {len(intradc_counter)}")
top5 = intradc_counter.most_common(5)
print(f"  Top-5 most common: {top5}")

# ── plausibility verdict ──────────────────────────────────────────────────────
print("\n=== Plausibility assessment ===")

run_at_0 = run_counter.get(0, 0) / n_events if n_events else 0
level_at_1 = abs_level_counter.get(1, 0) / n_events if n_events else 0
level_at_2 = abs_level_counter.get(2, 0) / n_events if n_events else 0
sign_ratio = n_pos / (n_neg or 1)
last_ok = (last1_count == n_blocks)

checks = [
    ("RUN=0 > 30% of events",            run_at_0 > 0.30,  f"{run_at_0*100:.1f}%"),
    ("|LEVEL|=1 > 20% of events",        level_at_1 > 0.20, f"{level_at_1*100:.1f}%"),
    ("|LEVEL|=1+2 > 40% of events",      level_at_1 + level_at_2 > 0.40,
                                          f"{(level_at_1+level_at_2)*100:.1f}%"),
    ("Sign balance 0.8 < ratio < 1.25",  0.80 < sign_ratio < 1.25, f"{sign_ratio:.3f}"),
    ("LAST=1 count matches block count", last_ok, "OK" if last_ok else "MISMATCH"),
]
passes = sum(1 for _, ok, _ in checks if ok)
for desc, ok, val in checks:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {desc}  -> {val}")

print(f"\n  {passes}/{len(checks)} checks passed.")
if passes == len(checks):
    print("  -> Distributions look PLAUSIBLE for real DCT video data.")
    print("     TABLE 13 may be correct for MBs that decoded without error.")
    print("     Desync likely starts only when the first proprietary code is hit.")
elif passes >= 3:
    print("  -> MIXED result -- some plausibility, but review individual checks.")
    print("     Possible: TABLE 13 partially overlaps proprietary table.")
else:
    print("  -> Distributions look IMPLAUSIBLE for real video.")
    print("     Even the 'successful' decodes are likely garbage.")
    print("     The table mismatch may start from the very first TCOEFF event.")
