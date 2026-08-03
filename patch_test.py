"""
Test bitstream-patching hypotheses on a single frame.

Observed at the first PSC (offset 454), picture-header bits 30-42:
  bits 30-32 (PTYPE prefix) = 1,0,0   <- matches standard, untouched
  bits 33-35                = 0,1,0   <- standard calls these split/doccam/freeze;
                                          read as a 3-bit value = 2 = QCIF
  bits 36-38                = 1,0,0   <- standard calls this "source format" = 4 = 4CIF
  bit  39 (pic type)        = 0
  bits 40-42 (annex D/E/F)  = 0,0,0

Hypothesis: the encoder simply writes source-format BEFORE split/doccam/freeze
(a 3-bit field swap, same total length -- no bit insertion needed). To make a
standard-compliant header we want:
  bits 33-35 (split/doccam/freeze) = 0,0,0
  bits 36-38 (source format)       = 0,1,0   (QCIF)
  bit 39 onward                    = unchanged

This script patches just the FIRST frame this way (leaving its size identical),
re-assembles a tiny test stream (patched frame 0 + a few following original
frames so the demuxer can find packet boundaries), and decodes it with PyAV to
check whether the output is no longer solid black.
"""
from pathlib import Path
import os
import av

SMV = Path("Nana playing conputer.smv")
data = SMV.read_bytes()

# Locate first 4 PSCs precisely (same filter as analyze_h263.py)
pscs = []
for i in range(454, 200000):
    if data[i] == 0 and data[i+1] == 0 and (data[i+2] & 0xFC) == 0x80 \
       and data[i+4] == 0x28 and data[i+5] == 0x04:
        pscs.append(i)
        if len(pscs) >= 4:
            break
print("PSC offsets used:", pscs)

frame0_start, frame1_start, frame2_start, frame3_start = pscs


def get_bit(buf, bitpos):
    byte_idx = bitpos // 8
    bit_idx = 7 - (bitpos % 8)
    return (buf[byte_idx] >> bit_idx) & 1


def set_bit(buf, bitpos, value):
    byte_idx = bitpos // 8
    bit_idx = 7 - (bitpos % 8)
    if value:
        buf[byte_idx] |= (1 << bit_idx)
    else:
        buf[byte_idx] &= ~(1 << bit_idx)


def patch_frame_swap(frame_bytes):
    """Swap the two 3-bit fields at stream-bits 33-35 and 36-38 so that
    source format (currently readable at 33-35 as 010=QCIF) lands at the
    standard location (36-38), and zero out split/doccam/freeze (33-35)."""
    buf = bytearray(frame_bytes)
    # current values
    b33, b34, b35 = get_bit(buf, 33), get_bit(buf, 34), get_bit(buf, 35)
    b36, b37, b38 = get_bit(buf, 36), get_bit(buf, 37), get_bit(buf, 38)
    print(f"  original bits[33-35]={b33}{b34}{b35}  bits[36-38]={b36}{b37}{b38}")
    # write source format (QCIF = 010) into 36-38
    set_bit(buf, 36, 0); set_bit(buf, 37, 1); set_bit(buf, 38, 0)
    # zero split/doccam/freeze (33-35)
    set_bit(buf, 33, 0); set_bit(buf, 34, 0); set_bit(buf, 35, 0)
    return bytes(buf)


def patch_frame_direct_overwrite(frame_bytes):
    """Alternative: just force bits 36-38 = QCIF and leave 33-35 as-is
    (in case split/doccam/freeze really are meaningful and shouldn't be
    clobbered)."""
    buf = bytearray(frame_bytes)
    set_bit(buf, 36, 0); set_bit(buf, 37, 1); set_bit(buf, 38, 0)
    return bytes(buf)


def try_decode(label, stream_bytes, outdir):
    os.makedirs(outdir, exist_ok=True)
    test_path = Path(outdir) / "test_stream.bin"
    test_path.write_bytes(stream_bytes)
    print(f"\n--- {label} ---")
    try:
        container = av.open(str(test_path), format="h263")
        stream = container.streams.video[0]
        print(f"  Codec: {stream.codec_context.name}  Res: {stream.width}x{stream.height}")
        n = 0
        non_black = 0
        for packet in container.demux(stream):
            for frame in packet.decode():
                img = frame.to_image()
                pixels = list(img.getdata())
                r = [p[0] for p in pixels]
                g = [p[1] for p in pixels]
                b = [p[2] for p in pixels]
                rng = (max(r)-min(r) + max(g)-min(g) + max(b)-min(b))
                img.save(str(Path(outdir) / f"frame_{n:02d}.png"))
                tag = "HAS CONTENT" if rng > 10 else "blank"
                print(f"  frame {n}: pixel range sum={rng}  ({tag})")
                if rng > 10:
                    non_black += 1
                n += 1
        container.close()
        print(f"  => decoded {n} frames, {non_black} non-black")
    except Exception as e:
        print(f"  FAILED: {e!r}")


# Build a small test stream: frame0 (patched) + frames 1,2 (original, untouched)
# so the demuxer has follow-on packets to bound frame 0.
orig_frame0 = data[frame0_start:frame1_start]
rest = data[frame1_start:frame3_start]

print(f"frame0 length = {len(orig_frame0)} bytes")

# Baseline: completely unpatched (sanity check we reproduce the black-frame result)
try_decode("BASELINE (unpatched)", orig_frame0 + rest, "patch_baseline")

# Hypothesis 1: swap the two 3-bit fields
patched1 = patch_frame_swap(orig_frame0)
try_decode("HYPOTHESIS 1: swap srcfmt into standard slot, zero split/doccam/freeze",
           patched1 + rest, "patch_h1")

# Hypothesis 2: only overwrite bits 36-38, leave 33-35 untouched
patched2 = patch_frame_direct_overwrite(orig_frame0)
try_decode("HYPOTHESIS 2: force bits[36-38]=QCIF, leave 33-35 as-is",
           patched2 + rest, "patch_h2")
