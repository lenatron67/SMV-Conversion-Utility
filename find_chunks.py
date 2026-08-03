import struct
from pathlib import Path

data = Path("Nana playing conputer.smv").read_bytes()
size = len(data)

# Find all "10 56" occurrences (potential video chunk markers)
v10_56 = [i for i in range(size-4) if data[i]==0x10 and data[i+1]==0x56]
print(f"'10 56' occurrences (first 200KB): {sum(1 for x in v10_56 if x < 200000)}")
print("First 15 with following bytes:")
for off in v10_56[:15]:
    hex_str = data[off:off+16].hex(' ').upper()
    print(f"  {off:6d} (0x{off:06X}): {hex_str}")
if len(v10_56) > 1:
    gaps = [v10_56[i+1]-v10_56[i] for i in range(min(15, len(v10_56)-1))]
    print(f"Spacing: {gaps}")

print()
# Find all PSC candidates in full file (first 500KB for speed)
pscs = [i for i in range(min(size-4, 500000)) if data[i]==0 and data[i+1]==0 and (data[i+2] & 0xFC) == 0x80]
print(f"PSC candidates in first 500KB: {len(pscs)}")
print("First 20:")
for off in pscs[:20]:
    hex_str = data[off:off+8].hex(' ').upper()
    print(f"  {off:6d} (0x{off:06X}): {hex_str}")
gaps = [pscs[i+1]-pscs[i] for i in range(min(20, len(pscs)-1))]
print(f"Spacing between first 20: {gaps}")

print()
# Cross-reference: is there a "10 56" near each PSC?
print("Bytes 8 before each PSC:")
for psc in pscs[:15]:
    pre = data[max(0,psc-8):psc].hex(' ').upper()
    print(f"  PSC@{psc}: [...{pre}]")
