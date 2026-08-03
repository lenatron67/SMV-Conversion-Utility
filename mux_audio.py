"""
Session 11 -- add the recovered audio track to the recovered video.

audio.raw (depacket.py's concatenated 'A' payloads) is G.723.1 at
6.3 kbit/s: 24-byte frames, 30 ms each -- 36,812 frames = 1104.4 s,
matching the video duration. Decode it with FFmpeg's g723_1 decoder,
encode AAC, and mux with the already-encoded H.264 video (stream copy)
into the final deliverable.

Usage: python mux_audio.py [in.mp4] [out.mp4]
"""
import sys

import av

VIN = sys.argv[1] if len(sys.argv) > 1 else "Nana playing computer.mp4"
OUT = sys.argv[2] if len(sys.argv) > 2 else "Nana playing computer (with audio).mp4"


def main():
    av.logging.set_level(av.logging.ERROR)
    vin = av.open(VIN)
    ain = av.open("audio.raw", format="g723_1")
    out = av.open(OUT, "w")

    vst_in = vin.streams.video[0]
    vst = out.add_stream_from_template(vst_in)
    ast = out.add_stream("aac", rate=8000, layout="mono")

    vpkts = []
    for p in vin.demux(vst_in):
        if p.dts is None:
            continue
        p.stream = vst
        vpkts.append(p)

    apkts = []
    from fractions import Fraction
    atb = Fraction(1, 8000)
    nsamp = 0
    for p in ain.demux(audio=0):
        for frame in p.decode():
            frame.pts = nsamp
            frame.time_base = atb
            nsamp += frame.samples
            for op in ast.encode(frame):
                apkts.append(op)
    for op in ast.encode():
        apkts.append(op)
    apkts = [p for p in apkts if p.dts is not None]

    # merge by time for sane interleaving
    def key(p):
        return float(p.dts * p.time_base)
    for p in sorted(vpkts + apkts, key=key):
        out.mux(p)
    out.close()
    print(f"muxed {len(vpkts)} video + {len(apkts)} audio packets -> {OUT}")


if __name__ == "__main__":
    main()
