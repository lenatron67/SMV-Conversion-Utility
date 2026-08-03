"""
Deeper analysis of the SMV file structure.
Since H.263 decode gives blank frames, let's:
1. Look at the first frame bits carefully
2. Try different codec interpretations
3. Look for any recognisable structure in the data
"""
from pathlib import Path
import struct

data = Path("Nana playing conputer.smv").read_bytes()

# =============================================
# 1. Manual bit decoder for the first "frame"
# =============================================
print("=== Manual bit decode of frame at offset 454 ===")
frame_start = 454
frame_bytes = data[frame_start:frame_start+20]
print(f"Bytes: {frame_bytes.hex(' ').upper()}")

def get_bits(data, bit_offset, num_bits):
    result = 0
    for i in range(num_bits):
        byte_idx = (bit_offset + i) // 8
        bit_idx = 7 - ((bit_offset + i) % 8)
        result = (result << 1) | ((data[byte_idx] >> bit_idx) & 1)
    return result

bit = 0
print(f"Bits 0-21 (PSC): {get_bits(frame_bytes, 0, 22):022b}")
bit = 22
tr = get_bits(frame_bytes, bit, 8); bit += 8
print(f"TR (8 bits): {tr} = 0x{tr:02X}")

ptype0 = get_bits(frame_bytes, bit, 1); bit += 1  # must be 1
ptype1 = get_bits(frame_bytes, bit, 1); bit += 1  # must be 0
ptype2 = get_bits(frame_bytes, bit, 1); bit += 1  # must be 0
split  = get_bits(frame_bytes, bit, 1); bit += 1
doccam = get_bits(frame_bytes, bit, 1); bit += 1
freeze = get_bits(frame_bytes, bit, 1); bit += 1
srcfmt = get_bits(frame_bytes, bit, 3); bit += 3
ptype  = get_bits(frame_bytes, bit, 1); bit += 1  # 0=I, 1=P
annexD = get_bits(frame_bytes, bit, 1); bit += 1
annexE = get_bits(frame_bytes, bit, 1); bit += 1
annexF = get_bits(frame_bytes, bit, 1); bit += 1
print(f"PTYPE[0-2]: {ptype0},{ptype1},{ptype2} (should be 1,0,0)")
print(f"Split screen: {split}, Doc camera: {doccam}, Freeze: {freeze}")
fmt_names = {0:'FORBIDDEN', 1:'sub-QCIF(128x96)', 2:'QCIF(176x144)',
             3:'CIF(352x288)', 4:'4CIF(704x576)', 5:'16CIF', 6:'rsvd', 7:'PLUSPTYPE'}
print(f"Source format: {srcfmt} = {fmt_names.get(srcfmt, 'unknown')}")
print(f"Picture type: {'P-frame' if ptype else 'I-frame'}")
print(f"Annexes D,E,F: {annexD},{annexE},{annexF}")

# Check CPM and PEI
cpm = get_bits(frame_bytes, bit, 1); bit += 1
pei = get_bits(frame_bytes, bit, 1); bit += 1
print(f"CPM: {cpm}, PEI: {pei}")
if cpm:
    psbi = get_bits(frame_bytes, bit, 2); bit += 2
    print(f"PSBI: {psbi}")
while pei:
    pspare = get_bits(frame_bytes, bit, 8); bit += 8
    pei = get_bits(frame_bytes, bit, 1); bit += 1

print(f"Picture header ends at bit: {bit}")
print(f"Next bits (GQUANT if no GBSC for GOB0): {get_bits(frame_bytes, bit, 5)}")
gquant = get_bits(frame_bytes, bit, 5); bit += 5
print(f"GQUANT: {gquant}")

# =============================================
# 2. Look for patterns that look like valid H.263 in the data
# =============================================
print("\n=== Searching for PSCs that give valid QCIF (srcfmt=2) ===")
count = 0
for offset in range(452, min(len(data)-20, 100000)):
    if data[offset] == 0 and data[offset+1] == 0 and (data[offset+2] & 0xFC) == 0x80:
        # Try to read PSC + header
        db = data[offset:offset+10]
        psc = get_bits(db, 0, 22)
        if psc == 0:  # bits 0-21 are zero (PSC is all zeros except bit 16=1)
            # PSC has bit 16=1. Let me verify:
            pass
        # Check source format at bits 36-38
        sf = get_bits(db, 36, 3)
        p0 = get_bits(db, 30, 1)
        p1 = get_bits(db, 31, 1)
        p2 = get_bits(db, 32, 1)
        if p0 == 1 and p1 == 0 and p2 == 0:  # valid PTYPE start
            count += 1
            if count <= 10:
                tr_val = get_bits(db, 22, 8)
                print(f"  offset={offset}, srcfmt={sf} ({fmt_names.get(sf,'?')}), TR={tr_val}")

# =============================================
# 3. Check if the data might be H.263 with a 2-byte mux prefix per frame
# =============================================
print("\n=== Checking if each frame has a 2-byte prefix (after header) ===")
# Pattern: [10 56] [H.263 frame data]
# The first frame has 10 56 at offset 452, PSC at 454
# Let's see if this pattern repeats
print("Looking for '10 56' + '00 00 8x' patterns:")
for i in range(452, min(len(data)-10, 500000)):
    if data[i] == 0x10 and data[i+1] == 0x56 and data[i+2] == 0x00 and data[i+3] == 0x00 and (data[i+4] & 0xFC) == 0x80:
        print(f"  Found '10 56' + PSC at offset {i}")
        break

# =============================================
# 4. Look at what's special about the 4th byte pattern
# =============================================
print("\n=== Distribution of 4th bytes at PSC locations ===")
from collections import Counter
fourth_bytes = Counter()
for offset in range(452, len(data)-8):
    if data[offset] == 0 and data[offset+1] == 0 and (data[offset+2] & 0xFC) == 0x80:
        sf = get_bits(data[offset:offset+8], 36, 3)
        if get_bits(data[offset:offset+8], 30, 3) == 4:  # valid PTYPE start 100
            fourth_bytes[sf] += 1
print(f"Source format distribution at PSC locations: {dict(fourth_bytes)}")

# =============================================
# 5. Try to look at the last ~100 bytes before each PSC interval
# =============================================
pscs = []
for i in range(452, len(data)-8):
    if data[i] == 0 and data[i+1] == 0 and (data[i+2] & 0xFC) == 0x80:
        sf = get_bits(data[i:i+8], 36, 3)
        if get_bits(data[i:i+8], 30, 3) == 4:
            pscs.append(i)

print(f"\nTotal valid PSCs: {len(pscs)}")
print("Are there any bytes between PSC end and next PSC start that look non-H.263?")
# Check first 5 inter-frame regions
for k in range(min(5, len(pscs)-1)):
    start = pscs[k]
    end = pscs[k+1]
    frame_data = data[start:end]
    print(f"Frame {k}: {start}→{end} ({end-start} bytes)")
    # Look for any repeated patterns in the frame data
    chunk = frame_data[:32]
    print(f"  First 32 bytes: {chunk.hex(' ').upper()}")
    chunk2 = frame_data[-16:]
    print(f"  Last 16 bytes: {chunk2.hex(' ').upper()}")
