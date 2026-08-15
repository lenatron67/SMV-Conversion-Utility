"""
Session 5, step 0 — sanity-check the i263_decoder.py port against a GENUINE
I263 file (samples.mplayerhq.hu/V-codecs/I263/i263.avi, 352x240, 200 frames,
frame 0 intra).

Genuine I263-in-AVI uses Intel's own picture header (see
ffmpeg_src/intelh263dec.c ff_intel_h263_decode_picture_header), which differs
from the SMV stream's header — parse it here, then hand the MB layer to the
SAME decode_mb/maybe_gob_header/decode_block code the SMV analysis uses.

Success criterion: frame 0 (intra) decodes ALL MBs with zero errors and lands
within stuffing distance of the packet end. That validates the port's MCBPC/
CBPY/INTRADC/TCOEF consumption logic against real I263 data, so failures on
the SMV stream can be attributed to the SMV stream, not port bugs.
"""
import sys

import av

from bitreader import BitReader
from i263_decoder import DecodeError, decode_mb, maybe_gob_header


def parse_intel_picture_header(br: BitReader):
    """Port of ff_intel_h263_decode_picture_header (intelh263dec.c)."""
    if br.read(22) != 0x20:
        raise DecodeError("bad PSC")
    tr = br.read(8)
    if br.read(1) != 1:
        raise DecodeError("bad marker")
    if br.read(1) != 0:
        raise DecodeError("bad H.263 id")
    br.skip(3)                      # split screen / camera / freeze release
    fmt = br.read(3)
    if fmt in (0, 6):
        raise DecodeError(f"free format {fmt} not supported")
    pict_type = br.read(1)          # 0 = I, 1 = P
    br.skip(1)                      # long vectors
    if br.read(1) != 0:
        raise DecodeError("SAC not supported")
    obmc = br.read(1)
    pb_frame = br.read(1)
    width = height = None
    if fmt < 6:
        std = {1: (128, 96), 2: (176, 144), 3: (352, 288),
               4: (704, 576), 5: (1408, 1152)}
        width, height = std[fmt]
    else:                           # fmt == 7: extended format follows
        fmt = br.read(3)
        if fmt in (0, 7):
            raise DecodeError("wrong Intel H.263 format")
        if br.read(2):
            raise DecodeError("bad reserved field")
        loop_filter = br.read(1)
        if br.read(1):
            raise DecodeError("bad reserved field")
        if br.read(1):
            pb_frame = 2
        if br.read(5):
            raise DecodeError("bad reserved field")
        if br.read(5) != 1:
            raise DecodeError("invalid marker")
    if fmt == 6:                    # custom dimensions
        ar = br.read(4)
        width = (br.read(9) << 2) + 4
        if br.read(1) != 1:
            raise DecodeError("bad marker in dimensions")
        height = (br.read(9) << 2) + 4
        if ar == 15:
            br.skip(16)             # explicit aspect ratio w/h
    qscale = br.read(5)
    br.skip(1)                      # CPM off
    if pb_frame:
        br.skip(5)                  # TRB(3) + dbquant(2)
    while br.read(1):               # PEI
        br.skip(8)
    return {"tr": tr, "pict_type": pict_type, "width": width,
            "height": height, "qscale": qscale, "pb_frame": pb_frame,
            "obmc": obmc, "header_end_bit": br.pos}


def decode_intra_frame(data: bytes):
    br = BitReader(data, 0)
    hdr = parse_intel_picture_header(br)
    if hdr["pict_type"] != 0:
        return hdr, 0, 0, "not an I-frame"
    mb_w, mb_h = hdr["width"] // 16, hdr["height"] // 16
    gquant = hdr["qscale"]
    n = 0
    for row in range(mb_h):
        if row > 0:
            g = maybe_gob_header(br, hdr["qscale"])
            if g is not None:
                gquant = g
        for col in range(mb_w):
            try:
                _, gquant = decode_mb(br, gquant)
            except DecodeError as e:
                return hdr, n, br.pos, f"MB {row}x{col}: {e}"
            n += 1
    return hdr, n, br.pos, None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "i263_sample.avi"
    container = av.open(path)
    stream = container.streams.video[0]
    results = []
    for i, pkt in enumerate(container.demux(stream)):
        if pkt.size <= 8:           # dummy frames (FFmpeg: FRAME_SKIPPED)
            continue
        data = bytes(pkt)
        br = BitReader(data, 0)
        try:
            hdr = parse_intel_picture_header(br)
        except DecodeError as e:
            print(f"pkt {i:3d} size {pkt.size:6d}: header error: {e}")
            continue
        kind = "I" if hdr["pict_type"] == 0 else "P"
        if hdr["pict_type"] == 0:
            hdr, n, end_bit, fail = decode_intra_frame(data)
            total = len(data) * 8
            mb_total = (hdr["width"] // 16) * (hdr["height"] // 16)
            status = "OK" if fail is None else f"FAIL @ {fail}"
            print(f"pkt {i:3d} size {pkt.size:6d}  {kind}  "
                  f"{hdr['width']}x{hdr['height']} q={hdr['qscale']} "
                  f"pb={hdr['pb_frame']} obmc={hdr['obmc']}  "
                  f"MBs {n}/{mb_total}  end {end_bit}/{total} "
                  f"(slack {total - end_bit})  {status}")
            results.append((i, n, mb_total, fail))
        else:
            print(f"pkt {i:3d} size {pkt.size:6d}  {kind}  "
                  f"{hdr['width']}x{hdr['height']} q={hdr['qscale']} "
                  f"pb={hdr['pb_frame']} obmc={hdr['obmc']}  (skipped)")
    ok = sum(1 for _, n, t, f in results if f is None and n == t)
    print(f"\nIntra frames fully decoded: {ok}/{len(results)}")


if __name__ == "__main__":
    main()
