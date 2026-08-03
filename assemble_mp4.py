"""
Session 11 -- M6: assemble the recovered video into an MP4.

Decodes video.h263 (the depacketized, fully-standard H.263 elementary
stream from depacket.py) with FFmpeg's stock h263 decoder and encodes
H.264/MP4 with faithful variable-frame-rate timing: each H.263 picture
header's TR (temporal reference) is a tick of the 30000/1001 Hz clock,
so PTS = unwrapped cumulative TR in a 1001/30000 time base.

Usage: python assemble_mp4.py [out.mp4]
"""
import sys
from fractions import Fraction

import av

OUT = sys.argv[1] if len(sys.argv) > 1 else "Nana playing computer.mp4"


def tr_of_packet(data):
    """TR = the 8 bits after the 22-bit PSC (packet starts at a PSC)."""
    v = int.from_bytes(data[:4], "big")
    return (v >> 2) & 0xFF


def main():
    av.logging.set_level(av.logging.ERROR)
    src = av.open("video.h263", format="h263")
    out = av.open(OUT, "w")
    tb = Fraction(1001, 30000)
    ost = out.add_stream("libx264", rate=None, options={
        "crf": "18", "preset": "medium", "bf": "0"})
    ost.width, ost.height = 176, 144
    ost.pix_fmt = "yuv420p"
    ost.time_base = tb
    ost.codec_context.time_base = tb  # else defaults to 1/24 and quantizes PTS

    n = 0
    cum = 0
    prev_tr = None
    last_pts = -1
    for packet in src.demux(video=0):
        if packet.size == 0:
            continue
        tr = tr_of_packet(bytes(packet)[:4])
        if prev_tr is None:
            delta = 0
        else:
            delta = (tr - prev_tr) % 256
            if delta == 0 or delta > 100:  # duplicate TR / wrap glitch
                delta = 3
        cum += delta
        prev_tr = tr
        for frame in packet.decode():
            if cum <= last_pts:
                cum = last_pts + 1
            last_pts = cum
            frame.pts = cum
            frame.time_base = tb
            for op in ost.encode(frame):
                out.mux(op)
            n += 1
            if n % 2000 == 0:
                print(f"  {n} frames, t={float(cum * tb):.1f}s")
    for op in ost.encode():
        out.mux(op)
    out.close()
    print(f"done: {n} frames, duration {float(cum * tb):.1f}s -> {OUT}")


if __name__ == "__main__":
    main()
