"""
M1 — Statistically characterize the macroblock-layer bitstream.

For each frozen sample frame (samples/frame_*.bin + manifest.json), slice out
the bits starting where the picture header ends (manifest's
mb_data_bit_offset_within_frame -- currently +50 bits into every sampled
frame) through to the end of the frame. That's the MCBPC/CBPY/MVD/TCOEFF
region -- the part FFmpeg's stock H.263 decoder desyncs on within the first
~10 macroblocks.

For candidate VLC code-window widths 1-9 bits (the length range of standard
H.263's intra MCBPC table), build a SLIDING-WINDOW frequency histogram across
every bit position (not just byte-aligned or width-aligned positions, since we
don't yet know where code boundaries fall). A real prefix code produces a
visibly skewed distribution at its natural width (short/common codes recur
constantly), and a shift in distribution shape between widths is itself a clue
about where code boundaries land. Pure noise or a fixed-width field would look
close to uniform at every width.

Reports, per width:
  - Shannon entropy (bits) and normalized entropy (entropy / width) -- lower
    normalized entropy = more skewed = more "code-like"
  - Concentration: % of all occurrences covered by the most frequent 25% of
    observed values -- higher = more skewed
  - The top 8 most frequent windows with their frequencies, to eyeball

This does NOT yet attempt to assign meaning to any code -- it's purely "does
this bit-width look structured, and if so, roughly how" -- groundwork for
actually proposing a table in a later session.
"""
import json
import math
from collections import Counter
from pathlib import Path

from bitreader import BitReader

SAMPLES = Path("samples")
manifest = json.loads((SAMPLES / "manifest.json").read_text())

MIN_WIDTH = 1
MAX_WIDTH = 9   # standard H.263 intra MCBPC codes range from 1 to 9 bits

# ---------------------------------------------------------------------------
# 1. Collect the post-header bit sequence for every sample frame
# ---------------------------------------------------------------------------
frame_bits = {}   # filename -> list[int] of individual bits (0/1)

for entry in manifest["frames"]:
    path = SAMPLES / entry["file"]
    data = path.read_bytes()
    start_bit = entry["mb_data_bit_offset_within_frame"]
    total_bits = len(data) * 8
    br = BitReader(data, start_bit)
    bits = [br.read(1) for _ in range(total_bits - start_bit)]
    frame_bits[entry["file"]] = bits
    print(f"{entry['file']}: {len(bits)} MB-layer bits "
          f"(frame is {len(data)} bytes, header used {start_bit} bits)")

all_bits = [b for bits in frame_bits.values() for b in bits]
print(f"\nTotal MB-layer bits pooled across {len(frame_bits)} sample frames: {len(all_bits)}")

# ---------------------------------------------------------------------------
# 2. Sliding-window histograms per candidate width
# ---------------------------------------------------------------------------
def sliding_windows(bits, width):
    """Yield every width-bit integer formed by consecutive bits, one bit apart
    (overlapping). This deliberately ignores code-boundary alignment, since we
    don't know it yet -- skew at the *correct* width should still show up
    strongly because real codes recur far more often than their neighbours-by-
    one-bit-shift."""
    value = 0
    mask = (1 << width) - 1
    for i, b in enumerate(bits):
        value = ((value << 1) | b) & mask
        if i >= width - 1:
            yield value


def shannon_entropy(counter, total):
    h = 0.0
    for count in counter.values():
        p = count / total
        h -= p * math.log2(p)
    return h


def concentration_top_quarter(counter, total, width):
    possible = 1 << width
    top_n = max(1, possible // 4)
    top_counts = sorted(counter.values(), reverse=True)[:top_n]
    return sum(top_counts) / total


print("\n=== Sliding-window histogram analysis (pooled across all sample frames) ===")
print(f"{'width':>5} | {'entropy (bits)':>15} | {'norm. entropy':>14} | "
      f"{'top-25%-of-values':>18} | top values (value:count)")
print("-" * 100)

results = []
for width in range(MIN_WIDTH, MAX_WIDTH + 1):
    counter = Counter(sliding_windows(all_bits, width))
    total = sum(counter.values())
    h = shannon_entropy(counter, total)
    norm_h = h / width
    conc = concentration_top_quarter(counter, total, width)
    top = counter.most_common(8)
    top_str = ", ".join(f"{v:0{width}b}:{c}" for v, c in top)
    print(f"{width:>5} | {h:>15.3f} | {norm_h:>14.3f} | {conc*100:>17.1f}% | {top_str}")
    results.append((width, h, norm_h, conc))

print("""
How to read this:
  - norm. entropy near 1.0  -> looks close to uniform/random at this width
                               (consistent with: wrong width, or this region
                               genuinely is high-entropy coefficient data with
                               no structure visible at this granularity)
  - norm. entropy well below 1.0, with a few values dominating the top-25%
    concentration figure -> suggests a skewed, structured distribution at this
    width: a candidate for "this might be (part of) a VLC code length"

Remember: MCBPC is only the FIRST field per macroblock, and is itself a VLC
(variable length) -- so even at the "correct" width, the *sliding* histogram
mixes genuine MCBPC codes with mid-code and cross-boundary windows. Don't
expect a clean signal; look for which width(s) stand out *relative to their
neighbours*, then move to a boundary-aware (greedy prefix-walk) analysis next.
""")

# ---------------------------------------------------------------------------
# 3. Cross-reference against the standard H.263 intra MCBPC table
# ---------------------------------------------------------------------------
# Sourced verbatim from "Draft Recommendation H.263" (the ITU-T H.263 draft
# text), TABLE 4/H.263 "VLC table for MCBPC (for I-pictures)", page 14:
# https://hlevkin.com/hlevkin/Standards/h263v1.pdf
#
# NOTE: the table we actually want is TABLE 4/H.263, not "Table 7" as an
# earlier session note assumed -- Table 7/H.263 in this spec text is
# "Macroblock types and included data elements for PB-frames", an unrelated
# table. MB type 3 = INTRA, MB type 4 = INTRA+Q (see TABLE 6/H.263).
STANDARD_INTRA_MCBPC = {
    "1":         (3, 0b00),       # 1 bit
    "001":       (3, 0b01),       # 3 bits
    "010":       (3, 0b10),       # 3 bits
    "011":       (3, 0b11),       # 3 bits
    "0001":      (4, 0b00),       # 4 bits
    "000001":    (4, 0b01),       # 6 bits
    "000010":    (4, 0b10),       # 6 bits
    "000011":    (4, 0b11),       # 6 bits
    "000000001": ("stuffing", None),  # 9 bits
}

if STANDARD_INTRA_MCBPC:
    print("=== Cross-reference against standard intra MCBPC table ===")
    # Walk each sample frame's bits greedily matching the standard table,
    # and report how often we get a clean run vs. an immediate mismatch.
    for fname, bits in frame_bits.items():
        bitstr = "".join(str(b) for b in bits[:2000])
        pos = 0
        matches = 0
        misses = 0
        while pos < len(bitstr) - 9:
            for code, meaning in STANDARD_INTRA_MCBPC.items():
                if bitstr.startswith(code, pos):
                    matches += 1
                    pos += len(code)
                    break
            else:
                misses += 1
                pos += 1  # bit-slip and keep going, just to gather a rough rate
        print(f"  {fname}: {matches} clean matches, {misses} non-matches "
              f"(first 2000 bits, greedy walk)")
else:
    print("(Skipping standard-MCBPC cross-reference -- STANDARD_INTRA_MCBPC "
          "table not yet populated. See TODO comment above this section.)")
