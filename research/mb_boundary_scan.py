"""
Scan for valid MB boundaries near the drift failure point.

For frame_00000, we know:
- MB 0-2 decode correctly with TABLE 13 + 2 placeholder proprietary codes
- MB 3 CBPY fails at bit 1968 (the bits there are 6 leading zeros, not any valid CBPY code)

The drift means MB 3 actually STARTS at a different bit position than where we land.
Scanning ±200 bits around bit 1968 for valid (MCBPC + CBPY + 6×valid INTRADC)
sequences will reveal the true MB 3 start position, and the offset from 1968 tells
us how many bits the proprietary code in MB 2 was off by.

A "valid MB start" here means:
 - Bits parse as a valid MCBPC_INTRA code (1-9 bits, not stuffing)
 - Immediately followed by a valid CBPY_INTRA code (2-6 bits)
 - Immediately followed by 6 consecutive 8-bit values, none == 0x00 or 0x80
 - Total bits consumed <= ~600 per MB (sanity cap)
"""
import json
from pathlib import Path
from bitreader import BitReader

SAMPLES = Path("samples")
manifest = json.loads((SAMPLES / "manifest.json").read_text())

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


def match_vlc(br, table, max_len):
    if br.bits_remaining() < 1:
        return None, None
    peek_len = min(max_len, br.bits_remaining())
    bits = format(br.peek(peek_len), f"0{peek_len}b")
    for length in range(1, peek_len + 1):
        if bits[:length] in table:
            br.skip(length)
            return table[bits[:length]], bits[:length]
    return None, None


def try_mb_at(data, frame_byte_len, start_bit):
    """Try to parse a complete MB header (MCBPC+CBPY+6 INTRADC) starting at start_bit.
    Returns (success, bits_consumed, cbpc_coded_count, cbpy_val, note) or (False, 0, 0, 0, reason)"""
    br = BitReader(data, start_bit)
    total_bits = frame_byte_len * 8

    if br.bits_remaining() < 20:
        return False, 0, 0, 0, "too few bits"

    mcbpc_val, mcbpc_code = match_vlc(br, MCBPC_INTRA, 9)
    if mcbpc_val is None:
        return False, 0, 0, 0, "MCBPC fail"
    mb_type, cbpc = mcbpc_val
    if mb_type == "stuffing":
        return False, 0, 0, 0, "stuffing"

    cbpy_val, cbpy_code = match_vlc(br, CBPY_INTRA, 6)
    if cbpy_val is None:
        return False, 0, 0, 0, "CBPY fail"

    # DQUANT
    if mb_type == 4:
        if br.bits_remaining() < 2:
            return False, 0, 0, 0, "DQUANT eof"
        br.read(2)

    # Read 6 INTRADC bytes
    intradc_vals = []
    for _ in range(6):
        if br.bits_remaining() < 8:
            return False, 0, 0, 0, "INTRADC eof"
        idc = br.read(8)
        if idc in FORBIDDEN_INTRADC:
            return False, 0, 0, 0, f"INTRADC forbidden {hex(idc)}"
        intradc_vals.append(idc)

    bits_consumed = br.pos - start_bit
    coded_count = bin(cbpy_val).count('1') + bin(cbpc).count('1')
    return True, bits_consumed, coded_count, cbpy_val, f"OK: type={mb_type} CBPY={cbpy_val:04b} CBPC={cbpc:02b} IDC={[hex(v) for v in intradc_vals]}"


# Load frame_00000
entry = next(e for e in manifest["frames"] if e["file"] == "frame_00000.bin")
data = (SAMPLES / entry["file"]).read_bytes()
frame_byte_len = entry["byte_length"]

DRIFT_BIT = 1968  # where the CBPY failure was detected
SCAN_RANGE = 300  # scan ±300 bits

print(f"Scanning for valid MB start positions in frame_00000.bin")
print(f"Drift failure at bit {DRIFT_BIT}. Scanning [{DRIFT_BIT - SCAN_RANGE}, {DRIFT_BIT + SCAN_RANGE}]")
print()

hits = []
for offset in range(-SCAN_RANGE, SCAN_RANGE + 1):
    candidate = DRIFT_BIT + offset
    if candidate < 0 or candidate >= frame_byte_len * 8 - 50:
        continue
    ok, bits_consumed, coded_count, cbpy_val, note = try_mb_at(data, frame_byte_len, candidate)
    if ok:
        hits.append((candidate, bits_consumed, coded_count, cbpy_val, note))

print(f"Found {len(hits)} valid MB-header candidates:")
print(f"{'Bit':>8}  {'Offset':>8}  {'Bits':>6}  {'Coded':>6}  Details")
print("-" * 80)
for candidate, bits_consumed, coded_count, cbpy_val, note in hits:
    offset = candidate - DRIFT_BIT
    marker = " <-- DRIFT POINT" if offset == 0 else ""
    print(f"{candidate:>8}  {offset:>+8}  {bits_consumed:>6}  {coded_count:>6}  {note}{marker}")

# Find the most likely true MB 3 start by looking for the candidate closest to DRIFT_BIT
# that has high coded_count (most MBs are fully coded based on what we've seen)
print()
if hits:
    # Candidates within ±20 bits of drift point (likely the real MB 3 start)
    near_candidates = [(c, o, b, cc, n) for (c, o, b, cc, n) in [(h[0], h[0]-DRIFT_BIT, h[1], h[2], h[4]) for h in hits] if abs(o) <= 50]
    if near_candidates:
        print(f"Candidates within ±50 bits of drift point:")
        for c, o, b, cc, n in near_candidates:
            print(f"  bit {c:5d} (offset {o:+4d}): {n}")

# Also show what MB 2's end should be based on known MB start positions
# MB 0 @ 50 (start_bit), MB 1 @ 442, MB 2 @ 1195
# Average MB size from MB 0→1: 442-50=392 bits, MB 1→2: 1195-442=753 bits
# That's highly variable (because of coded content) -- can't reliably predict MB 3 start

# What we know about the expected drift:
# If the proprietary code is a 12-bit VLC (+ sign = 13 bits total) being
# misread as something else, the question is what it looks like to TABLE 13.
# Most likely: it starts with a 7-bit ESCAPE marker `0000011` and gets
# consumed as 22 bits (ESCAPE), causing 22-13 = 9 extra bits of consumption.
# OR: it matches a short TABLE 13 code (e.g. `10` = 2 bits + sign = 3 total),
# causing 13-3 = 10 fewer bits consumed than expected.
print()
print("Expected drift amounts if proprietary code in MB 2 is silently misread:")
print("  If it's treated as ESCAPE (22 bits) but is 13 bits: +9 bits drift")
print("  If it's treated as `10` (3 bits) but is 13 bits: -10 bits drift")
print("  If it's treated as MCBPC/CBPY/INTRADC: complex drift")
