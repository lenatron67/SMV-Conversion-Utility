"""
M1 — Constrained brute-force scanner for "DC-only MB" candidates.

Follow-up to mcbpc_boundary_walker.py (chain stalled after MB 0 in every
sample -- ledger #7, inconclusive). Rather than only chaining forward from a
known-good start, this scans EVERY bit position in each frame's MB layer and
asks: "could a DC-only MB (CBPY=0000, CBPC=00 -- the one config we can fully
validate without the missing TCOEFF table, see mcbpc_boundary_walker.py's
docstring) start here?"

Why this is meaningfully more discriminating than the disproven single-field
test (ledger #6, where random bits scored a HIGHER match rate than real data
-- TABLE 4/H.263 alone is a near-complete prefix code that matches almost
anything): this test stacks FIVE independent constraints:

  1. MCBPC must match a TABLE 4/H.263 code                  (~98% by chance)
  2. ...decoding to a non-stuffing MB type (3 or 4)         (~89% by chance)
  3. CBPY must match a TABLE 10/H.263 code                  (~97% by chance)
  4. ...decoding to EXACTLY pattern 0000 (not just "some
     valid pattern" -- 1 of 16 possible)                    (~6.7% by chance,
                                                              weighted by code
                                                              probability)
  5. ...AND CBPC must be EXACTLY 00 (1 of 4)                (~25% by chance)
  6. All 6 INTRADC bytes must avoid the forbidden
     0x00/0x80 values                                       (~95.4% by chance)

Multiplying through gives a back-of-envelope false-positive rate around
0.1-0.2% per bit position -- two to three orders of magnitude tighter than
the ~97% the single-field test let through. If TABLE 4/TABLE 10 (or something
close to them) genuinely describe this region, real candidate positions
should: (a) appear at a noticeably different RATE than the same scan run over
randomized control bits, and/or (b) cluster at spacings resembling a
plausible whole-MB size. If the real-vs-random rates and gap distributions are
statistically indistinguishable, that's a null result just like ledger #6 --
informative in its own right (tells us this avenue is exhausted, time to
prioritize M3 / TCOEFF).
"""
import json
import random
from pathlib import Path

from bitreader import BitReader
from mcbpc_boundary_walker import (
    MCBPC_INTRA, CBPY_INTRA, FORBIDDEN_INTRADC, match_vlc, QCIF_MB_COUNT,
)

SAMPLES = Path("samples")
manifest = json.loads((SAMPLES / "manifest.json").read_text())

CHAIN_MIN_BITS = 9 + 6 + 2 + 6 * 8  # max MCBPC + max CBPY + DQUANT + 6 INTRADC bytes


def find_dc_only_candidates(data: bytes, start_bit: int, end_bit: int):
    """Scan every bit position in [start_bit, end_bit) for a position where
    MCBPC -> CBPY=0000/CBPC=00 -> [DQUANT] -> 6x valid-INTRADC all validate.
    Returns a list of (bit_position, mb_type, dquant_or_None)."""
    candidates = []
    for pos in range(start_bit, end_bit - CHAIN_MIN_BITS):
        br = BitReader(data, pos)
        mcbpc_val, _ = match_vlc(br, MCBPC_INTRA, 9)
        if mcbpc_val is None:
            continue
        mb_type, cbpc = mcbpc_val
        if mb_type == "stuffing" or cbpc != 0b00:
            continue
        cbpy_val, _ = match_vlc(br, CBPY_INTRA, 6)
        if cbpy_val != 0b0000:
            continue
        dquant = None
        if mb_type == 4:
            dquant = br.read(2)
        intradc = [br.read(8) for _ in range(6)]
        if any(v in FORBIDDEN_INTRADC for v in intradc):
            continue
        candidates.append((pos, mb_type, dquant))
    return candidates


def gap_stats(positions):
    if len(positions) < 2:
        return None
    gaps = [b - a for a, b in zip(positions, positions[1:])]
    return {
        "count": len(gaps),
        "min": min(gaps), "max": max(gaps),
        "mean": sum(gaps) / len(gaps),
    }


print("Scanning every bit position in each frame's MB layer for DC-only-MB "
      "candidates (MCBPC -> CBPY=0000,CBPC=00 -> [DQUANT] -> 6x valid INTRADC).\n")

real_counts = []
random_counts = []
all_real_gap_means = []

random.seed(20260607)  # reproducible control

for entry in manifest["frames"]:
    path = SAMPLES / entry["file"]
    data = path.read_bytes()
    start_bit = entry["mb_data_bit_offset_within_frame"]
    end_bit = entry["byte_length"] * 8

    real_candidates = find_dc_only_candidates(data, start_bit, end_bit)
    real_positions = [c[0] for c in real_candidates]
    real_counts.append(len(real_positions))

    # Control: shuffle the MB-layer bits of THIS SAME frame (preserves the
    # exact bit-frequency / byte-value statistics, destroys any positional
    # structure) and run the identical scan. A fairer control than fresh
    # random bits -- isolates "does structure matter" from "are the marginal
    # bit/byte frequencies just generically scanner-friendly".
    bits = []
    br = BitReader(data, start_bit)
    for _ in range(end_bit - start_bit):
        bits.append(br.read(1))
    random.shuffle(bits)
    shuffled = bytearray(len(data))
    shuffled[: start_bit // 8] = data[: start_bit // 8]  # keep header bytes intact
    # repack header bits + shuffled MB-layer bits into a byte buffer
    out_bits = []
    hb = BitReader(data, 0)
    for _ in range(start_bit):
        out_bits.append(hb.read(1))
    out_bits.extend(bits)
    while len(out_bits) % 8:
        out_bits.append(0)
    for i in range(0, len(out_bits), 8):
        byte = 0
        for b in out_bits[i:i+8]:
            byte = (byte << 1) | b
        shuffled[i // 8] = byte

    shuffled_candidates = find_dc_only_candidates(bytes(shuffled), start_bit, end_bit)
    random_counts.append(len(shuffled_candidates))

    gstats = gap_stats(real_positions)
    if gstats:
        all_real_gap_means.append(gstats["mean"])

    mb_layer_bits = end_bit - start_bit
    avg_bits_per_mb = mb_layer_bits / QCIF_MB_COUNT
    gap_str = (f"gaps: min={gstats['min']} max={gstats['max']} "
               f"mean={gstats['mean']:.0f}" if gstats else "gaps: n/a (<2 candidates)")
    print(f"{entry['file']}: {len(real_positions):3d} real candidates  vs  "
          f"{len(shuffled_candidates):3d} shuffled-control candidates   "
          f"({gap_str}; avg bits/MB ~ {avg_bits_per_mb:.0f})")

print(f"\n=== Summary across {len(real_counts)} sample frames ===")
print(f"Real candidates:     total={sum(real_counts):4d}  "
      f"mean/frame={sum(real_counts)/len(real_counts):.1f}  "
      f"min={min(real_counts)} max={max(real_counts)}")
print(f"Shuffled-control:    total={sum(random_counts):4d}  "
      f"mean/frame={sum(random_counts)/len(random_counts):.1f}  "
      f"min={min(random_counts)} max={max(random_counts)}")

if all_real_gap_means:
    overall_gap_mean = sum(all_real_gap_means) / len(all_real_gap_means)
    print(f"\nMean real candidate-to-candidate gap (averaged across frames with "
          f">=2 candidates): {overall_gap_mean:.0f} bits")
    print(f"For reference, average bits/MB at ~99 MBs/QCIF-frame: "
          f"~{(manifest['frames'][0]['byte_length']*8 - 50) / QCIF_MB_COUNT:.0f} bits")

print("""
How to read this:
  - If real-frame counts are systematically LOWER or HIGHER than the
    shuffled-control counts (which share the exact same bit/byte frequency
    statistics but have no positional structure), that's a signal the region
    has genuine VLC-table-shaped structure that this candidate query is
    sensitive to.
  - If real and shuffled counts are statistically indistinguishable, the
    5-constraint stack still isn't enough to separate "structured H.263-like
    region" from "same byte statistics, scrambled" -- a null result, BUT a
    more informative one than ledger #6 (at least we're now testing something
    discriminating in principle, just not finding a difference).
  - The mean candidate-to-candidate gap vs the expected "bits per MB" figure
    is a weak secondary check: real MBs are NOT all DC-only (most aren't --
    that's WHY the chain in mcbpc_boundary_walker.py stalled at MB 0), so
    candidates won't appear once-per-MB; expect gaps to be considerably
    LARGER than one MB's average size if these are genuine sparse DC-only MBs
    in a sea of coded ones. Wildly smaller/irregular gaps would suggest noise.
""")
