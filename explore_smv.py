import struct
import sys
from pathlib import Path

FILE = Path("Nana playing conputer.smv")
data = FILE.read_bytes()
size = len(data)
print(f"File size: {size:,} bytes ({size/1024/1024:.1f} MB)")

# Show first 512 bytes
print("\n=== First 512 bytes ===")
for i in range(0, 512, 16):
    hex_part = " ".join(f"{data[i+j]:02X}" for j in range(min(16, 512-i)))
    text_part = "".join(chr(data[i+j]) if 32 <= data[i+j] < 127 else '.' for j in range(min(16, 512-i)))
    print(f"  {i:04X}: {hex_part:<48}  {text_part}")

# Find all FF D8 and examine what follows
print("\n=== Sampling FF D8 occurrences (first 20) ===")
found = []
for i in range(size - 4):
    if data[i] == 0xFF and data[i+1] == 0xD8:
        found.append(i)

print(f"Total FF D8 count: {len(found)}")
print(f"Spacing between first few: {[found[i+1]-found[i] for i in range(min(10, len(found)-1))]}")

print("\nFirst 20 FF D8 offsets and following bytes:")
for i, off in enumerate(found[:20]):
    following = data[off:off+8].hex(' ').upper()
    print(f"  [{i:3d}] offset {off:8d} (0x{off:07X}): {following}")

# Look for JPEG-like SOI: FF D8 followed by FF Cx or FF Dx or FF Ex
print("\n=== Looking for 'clean' JPEG starts (FF D8 FF [valid_marker]) ===")
valid_after = set([0xC0, 0xC1, 0xC2, 0xC3, 0xC4, 0xC8, 0xC9, 0xCA,
                   0xCC, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7,
                   0xD8, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF,
                   0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7,
                   0xE8, 0xE9, 0xEA, 0xEB, 0xEC, 0xED, 0xEE, 0xEF,
                   0xFE])
clean = [(off, data[off+2]) for off in found if off+2 < size and data[off+2] == 0xFF and data[off+3] in valid_after]
print(f"Found {len(clean)} clean JPEG SOI (FF D8 FF [valid])")
for off, _ in clean[:5]:
    print(f"  offset {off:8d}: {data[off:off+12].hex(' ').upper()}")

# Look for FF D9 (EOI)
eoi = []
for i in range(size - 1):
    if data[i] == 0xFF and data[i+1] == 0xD9:
        eoi.append(i)
print(f"\nTotal FF D9 (EOI) count: {len(eoi)}")
print(f"First 10 EOI offsets: {eoi[:10]}")

# Try to pair SOI with subsequent EOI and check sizes
print("\n=== Trying SOI/EOI pairing ===")
pairs = []
eoi_idx = 0
for soi in found:
    while eoi_idx < len(eoi) and eoi[eoi_idx] <= soi:
        eoi_idx += 1
    if eoi_idx < len(eoi):
        end = eoi[eoi_idx] + 2
        frame_size = end - soi
        pairs.append((soi, end, frame_size))

print(f"Total SOI/EOI pairs: {len(pairs)}")
sizes = [p[2] for p in pairs]
if sizes:
    print(f"Frame size range: {min(sizes):,} to {max(sizes):,} bytes")
    print(f"Average frame size: {sum(sizes)//len(sizes):,} bytes")
    print(f"First 15 pairs (offset, end, size):")
    for soi, end, sz in pairs[:15]:
        print(f"  SOI=0x{soi:07X} ({soi:8d}), EOI=0x{end-2:07X} ({end-2:8d}), size={sz:6,}")

# Look at the structure just before each SOI
print("\n=== Bytes before first 10 SOI markers ===")
for soi, end, sz in pairs[:10]:
    pre = data[max(0,soi-8):soi].hex(' ').upper()
    post = data[soi:soi+16].hex(' ').upper()
    print(f"  soi=0x{soi:07X}: [...{pre}] [FF D8 {post[6:]}]")

# Check for repeating chunk headers before SOI
print("\n=== Looking for chunk structure ===")
# Check if there's a repeating N-byte pattern before each SOI
for pre_len in [4, 6, 8, 10, 12]:
    headers = set()
    for soi, _, _ in pairs[:50]:
        if soi >= pre_len:
            h = data[soi-pre_len:soi].hex()
            headers.add(h[:8])  # first 4 bytes of header
    if len(headers) <= 5:
        print(f"  pre_len={pre_len}: only {len(headers)} unique {pre_len}-byte prefixes!")
        print(f"    Values: {headers}")
