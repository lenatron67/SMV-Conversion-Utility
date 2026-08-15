"""
Brute-force every possible 3-bit 'source format' value (and a couple of
split/doccam/freeze combos) in frame 0's PTYPE, re-decode with PyAV, and
report the resolution + whether the frame is non-black.

Goal: empirically find which bit pattern makes FFmpeg's h263 decoder both
(a) report 176x144 (QCIF, matching the known frame size), and
(b) produce non-blank pixel output.
"""
from pathlib import Path
import os
import av

SMV = Path("Nana playing conputer.smv")
data = SMV.read_bytes()

pscs = []
for i in range(454, 200000):
    if data[i] == 0 and data[i+1] == 0 and (data[i+2] & 0xFC) == 0x80 \
       and data[i+4] == 0x28 and data[i+5] == 0x04:
        pscs.append(i)
        if len(pscs) >= 4:
            break

frame0_start, frame1_start, _, frame3_start = pscs
orig_frame0 = data[frame0_start:frame1_start]
rest = data[frame1_start:frame3_start]


def set_bit(buf, bitpos, value):
    byte_idx = bitpos // 8
    bit_idx = 7 - (bitpos % 8)
    if value:
        buf[byte_idx] |= (1 << bit_idx)
    else:
        buf[byte_idx] &= ~(1 << bit_idx)


def make_variant(srcfmt_bits, split_bits):
    buf = bytearray(orig_frame0)
    for i, v in enumerate(split_bits):
        set_bit(buf, 33 + i, v)
    for i, v in enumerate(srcfmt_bits):
        set_bit(buf, 36 + i, v)
    return bytes(buf)


def bits3(n):
    return [(n >> 2) & 1, (n >> 1) & 1, n & 1]


outdir = Path("patch_brute")
outdir.mkdir(exist_ok=True)
results = []

for sf_val in range(8):
    for split_val in (0, 2):  # 000 or 010 (original) for split/doccam/freeze
        srcfmt_bits = bits3(sf_val)
        split_bits = bits3(split_val)
        variant = make_variant(srcfmt_bits, split_bits)
        test_path = outdir / f"sf{sf_val}_sp{split_val}.bin"
        test_path.write_bytes(variant + rest)
        label = f"srcfmt={sf_val:03b} split/doc/frz={split_val:03b}"
        try:
            container = av.open(str(test_path), format="h263")
            stream = container.streams.video[0]
            w, h = stream.width, stream.height
            n = 0
            ranges = []
            for packet in container.demux(stream):
                for frame in packet.decode():
                    img = frame.to_image()
                    pixels = list(img.getdata())
                    r = [p[0] for p in pixels]; g = [p[1] for p in pixels]; b = [p[2] for p in pixels]
                    rng = (max(r)-min(r)) + (max(g)-min(g)) + (max(b)-min(b))
                    ranges.append(rng)
                    n += 1
            container.close()
            best = max(ranges) if ranges else -1
            tag = "CONTENT" if best > 10 else "blank"
            print(f"{label}: res={w}x{h} frames={n} max_pixel_range={best} [{tag}]")
            results.append((sf_val, split_val, w, h, best))
        except Exception as e:
            print(f"{label}: FAILED {e!r}")

print("\n=== Variants matching 176x144 AND non-black ===")
for sf, sp, w, h, best in results:
    if w == 176 and h == 144 and best > 10:
        print(f"  srcfmt={sf:03b} split={sp:03b} -> {w}x{h}, range={best}  <-- CANDIDATE")
