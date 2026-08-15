import struct
from pathlib import Path

data = Path("Nana playing conputer.smv").read_bytes()
size = len(data)

# Find ALL PSC candidates in full file
print("Scanning full file for H.263 PSCs (00 00 8x where bit7 of byte3 = 1)...")
pscs = []
for i in range(size - 8):
    if data[i] == 0x00 and data[i+1] == 0x00 and (data[i+2] & 0xFC) == 0x80:
        # Validate: after PSC (22 bits), TR (8 bits), PTYPE (13 bits min)
        # Check that the subsequent bytes look like a valid picture header
        b2 = data[i+2]  # bits 16-23: PSC(bits16-21) + TR[0-1]
        b3 = data[i+3]  # bits 24-31: TR[2-7] start of PTYPE?
        b4 = data[i+4]  #
        b5 = data[i+5]  #

        # Filter: require the pattern "28 04" at bytes+4,+5 which appears consistently
        # This is specific to this file's encoding pattern
        pscs.append(i)

print(f"Total PSC candidates in full file: {len(pscs)}")

# Show distribution
print(f"First PSC: offset {pscs[0]} (0x{pscs[0]:X})")
print(f"Last PSC: offset {pscs[-1]} (0x{pscs[-1]:X})")
gaps = [pscs[i+1]-pscs[i] for i in range(len(pscs)-1)]
min_gap = min(gaps)
max_gap = max(gaps)
avg_gap = sum(gaps) // len(gaps)
print(f"Frame sizes: min={min_gap}, max={max_gap}, avg={avg_gap} bytes")

# Look for the consistent pattern "28 04 1E" in frames
consistent = [p for p in pscs if p+6 < size and data[p+4]==0x28 and data[p+5]==0x04]
print(f"\nPSCs with '28 04' at +4,+5 (likely valid frames): {len(consistent)}")
print(f"First 5 offsets: {consistent[:5]}")
gaps_c = [consistent[i+1]-consistent[i] for i in range(min(20, len(consistent)-1))]
print(f"Spacing of first 20: {gaps_c}")

# Decode TR field from each PSC (bits 22-29 = 8-bit temporal reference)
print("\n=== TR analysis (Temporal Reference) ===")
trs = []
for p in consistent[:50]:
    b2 = data[p+2]  # bits 16-23
    b3 = data[p+3]  # bits 24-31
    # TR is bits 22-29 in the bitstream
    # bit22 = bit6 of b2 (0-indexed from MSB), bit23 = bit7 of b2
    # bit24 = bit0 of b3 (MSB), ...
    tr_high = (b2 & 0x03)  # bits 22-23 = 2 LSBs of b2
    tr_low = (b3 >> 2)      # bits 24-29 = 6 MSBs of b3
    tr = (tr_high << 6) | tr_low
    trs.append(tr)

print(f"TR values for first 50 frames: {trs[:20]}...")
tr_diffs = [trs[i+1]-trs[i] for i in range(min(20, len(trs)-1))]
# Handle wrap-around (TR is 8-bit, wraps at 256)
tr_diffs_wrap = [(d % 256) for d in tr_diffs]
print(f"TR differences: {tr_diffs_wrap}")
avg_tr_diff = sum(tr_diffs_wrap) / len(tr_diffs_wrap)
print(f"Avg TR diff: {avg_tr_diff:.1f}")
print(f"Implied frame rate: {30.0 / avg_tr_diff:.2f} fps (H.263 uses 30000/1001 base)")

print(f"\n=== Duration estimate ===")
total_frames = len(consistent)
fps_estimate = 30.0 / avg_tr_diff
duration_sec = total_frames / fps_estimate
print(f"Total frames: {total_frames}")
print(f"Estimated fps: {fps_estimate:.2f}")
print(f"Estimated duration: {duration_sec:.1f}s ({duration_sec/60:.1f} min)")
