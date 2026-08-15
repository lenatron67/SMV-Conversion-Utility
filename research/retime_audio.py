"""
Session 11 addendum -- fix A/V sync by restoring the mux's audio timing.

Diagnostic finding: audio packets and video frames start aligned, but the
recorder paused audio ~550 times (silence suppression / encoder stalls),
visible as mux stretches with V packets and no A packets. depacket.py's
gapless concatenation removed those pauses, so audio drifts up to ~6.9 s
ahead of video by the end.

Fix: each A packet's capture time is approximated by the video timeline
at its position in the mux (frame containing the video byte it follows,
timed by cumulative TR ticks -- same clock as assemble_mp4.py). Audio is
kept continuous (30 ms/frame) through normal interleave jitter, but when
the mux shows a gap >= GAP_THRESH the audio timeline jumps forward and
digital silence fills the hole. The retimed PCM is AAC-encoded and muxed
with the already-encoded H.264 video.

Usage: python retime_audio.py [in.mp4] [out.mp4]
"""
import bisect
import sys
from fractions import Fraction

import av

from bitreader import find_psc_offsets, decode_picture_header

VIN = sys.argv[1] if len(sys.argv) > 1 else "Nana playing computer.mp4"
OUT = sys.argv[2] if len(sys.argv) > 2 else "Nana playing computer (synced audio).mp4"
GAP_THRESH = 0.25   # seconds of mux-timeline lead that counts as a real pause
RATE = 8000
FRAME_S = 240       # samples per G.723.1 frame (30 ms @ 8 kHz)


def mux_positions():
    """Video-byte count preceding each A packet, in mux order."""
    raw = open("raw_h263.bin", "rb").read()
    out = []
    q, vbytes = 64, 64
    while q < len(raw):
        if raw[q] == 0x56:
            vbytes += 64
            q += 65
        else:  # 0x41, guaranteed by the zero-bad-tag walk
            out.append(vbytes)
            q += 25
    return out


def video_timeline():
    """(frame byte offsets, cumulative TR seconds) for video.h263."""
    v = open("video.h263", "rb").read()
    pscs = find_psc_offsets(v)
    cum, prev, times = 0, None, []
    for off in pscs:
        h = decode_picture_header(v, off)
        tr = h["tr"] if isinstance(h, dict) else h.tr
        if prev is not None:
            d = (tr - prev) % 256
            if d == 0 or d > 100:
                d = 3
            cum += d
        prev = tr
        times.append(cum * 1001 / 30000)
    return pscs, times


def main():
    av.logging.set_level(av.logging.ERROR)
    a_vbytes = mux_positions()
    pscs, times = video_timeline()

    def stream_time(vb):
        return times[max(bisect.bisect_right(pscs, vb) - 1, 0)]

    # decode G.723.1 to PCM, placing each frame on the corrected timeline
    pcm = bytearray()
    t = 0.0          # current audio-timeline position, seconds
    n_gaps = 0
    silence_total = 0.0
    src = av.open("audio.raw", format="g723_1")
    i = 0
    for packet in src.demux(audio=0):
        for frame in packet.decode():
            s = stream_time(a_vbytes[i])
            if s - t >= GAP_THRESH:
                pad = int(round((s - t) * RATE)) * 2
                pcm += b"\x00" * pad
                silence_total += s - t
                n_gaps += 1
                t = s
            pcm += bytes(frame.planes[0])[: frame.samples * 2]
            t += frame.samples / RATE
            i += 1
    print(f"{i} audio frames; {n_gaps} gaps restored, "
          f"{silence_total:.2f}s silence inserted; "
          f"audio now {len(pcm) / 2 / RATE:.2f}s")

    # mux: copy video, encode retimed PCM as AAC
    vin = av.open(VIN)
    out = av.open(OUT, "w")
    vst_in = vin.streams.video[0]
    vst = out.add_stream_from_template(vst_in)
    ast = out.add_stream("aac", rate=RATE, layout="mono")
    atb = Fraction(1, RATE)

    apkts = []
    CHUNK = 1024
    total = len(pcm) // 2
    for s0 in range(0, total, CHUNK):
        n = min(CHUNK, total - s0)
        af = av.AudioFrame(format="s16", layout="mono", samples=n)
        af.sample_rate = RATE
        af.planes[0].update(bytes(pcm[s0 * 2:(s0 + n) * 2]))
        af.pts = s0
        af.time_base = atb
        apkts += list(ast.encode(af))
    apkts += list(ast.encode())
    apkts = [p for p in apkts if p.dts is not None]

    vpkts = []
    for p in vin.demux(vst_in):
        if p.dts is None:
            continue
        p.stream = vst
        vpkts.append(p)

    for p in sorted(vpkts + apkts, key=lambda p: float(p.dts * p.time_base)):
        out.mux(p)
    out.close()
    print(f"muxed {len(vpkts)} video + {len(apkts)} audio packets -> {OUT}")


if __name__ == "__main__":
    main()
