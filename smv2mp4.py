#!/usr/bin/env python3
# smv2mp4 — convert SmithMicro VideoLink Mail .smv recordings to MP4.
#
# Copyright (C) 2026 Michael Leonard
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert a SmithMicro VideoLink Mail .smv recording to a playable MP4.

An .smv file is a VLMUX packet container (see VLC_REVERSE_ENGINEERING.md
for the full reverse-engineering story):

    header (typically 453 bytes), then a packet stream of
    'V' (0x56) + 64 bytes video payload   (65-byte packets)
    'A' (0x41) + 24 bytes audio payload   (25-byte packets)

Concatenated V payloads are a plain standard H.263 elementary stream
(QCIF); concatenated A payloads are G.723.1 audio at 6.3 kbit/s
(24-byte / 30 ms frames). The recorder pauses audio during silence, which
shows up as stretches of the mux with V packets and no A packets — audio
must be re-timed against the mux interleaving or it drifts ahead of the
video.

Pipeline (all in memory, no intermediate files):
  1. locate the first 'V' tag and depacketize the two elementary streams
  2. decode H.263 with FFmpeg, encode H.264 with faithful variable-
     frame-rate timing from the H.263 TR ticks (30000/1001 Hz clock)
  3. decode G.723.1, restore recording pauses as digital silence using
     the video timeline at each audio packet's mux position, encode AAC
  4. mux both streams into the output MP4

Usage:
    python smv2mp4.py input.smv [output.mp4] [--video-only]
                      [--keep-elementary]

Requires PyAV (pip install av); its wheels bundle FFmpeg.
"""
import argparse
import bisect
import io
import sys
from fractions import Fraction
from pathlib import Path

import av
from av.video.frame import PictureType

VIDEO_TAG = 0x56          # 'V'
AUDIO_TAG = 0x41          # 'A'
VIDEO_PAYLOAD = 64
AUDIO_PAYLOAD = 24
TICK = Fraction(1001, 30000)  # one H.263 TR tick (29.97 Hz clock)
ARATE = 8000              # G.723.1 sample rate
GAP_THRESH = 0.25         # seconds of mux-timeline lead = a real pause


class SmvFormatError(Exception):
    """The input does not look like a VLMUX .smv packet stream."""


# ---------------------------------------------------------------- container

def trial_walk(data, start, max_packets=256):
    """Check that a tag+payload walk from `start` stays consistent."""
    q, seen = start, 0
    while q < len(data) and seen < max_packets:
        tag = data[q]
        if tag == VIDEO_TAG:
            q += 1 + VIDEO_PAYLOAD
        elif tag == AUDIO_TAG:
            q += 1 + AUDIO_PAYLOAD
        else:
            return False
        seen += 1
    return True


def find_first_video_tag(data):
    """Locate the first 'V' packet tag (end of the .smv header).

    The first video payload starts the H.263 stream, so the tag is a
    0x56 byte immediately followed by a Picture Start Code (00 00 8x).
    Candidates are confirmed by trial-walking the packet stream, so a
    stray match inside the header cannot win. Known files put this at
    offset 453, but the scan does not assume that.
    """
    i = data.find(b"\x56\x00\x00")
    while i != -1:
        if i + 3 < len(data) and (data[i + 3] & 0xFC) == 0x80 \
                and trial_walk(data, i):
            return i
        i = data.find(b"\x56\x00\x00", i + 1)
    raise SmvFormatError(
        "no 'V'-tagged H.263 packet stream found — this file does not "
        "look like a VLMUX .smv recording")


def depacketize(data, start):
    """Walk the tag+payload packet stream, splitting elementary streams.

    Returns (video_bytes, audio_bytes, a_vbytes) where a_vbytes[i] is
    the count of video bytes preceding audio packet i in the mux — the
    interleaving record needed to re-time the audio later.
    """
    video, audio, a_vbytes = [], [], []
    vbytes = 0
    q, n = start, len(data)
    while q < n:
        tag = data[q]
        if tag == VIDEO_TAG:
            video.append(data[q + 1:q + 1 + VIDEO_PAYLOAD])
            vbytes += VIDEO_PAYLOAD
            q += 1 + VIDEO_PAYLOAD
        elif tag == AUDIO_TAG:
            audio.append(data[q + 1:q + 1 + AUDIO_PAYLOAD])
            a_vbytes.append(vbytes)
            q += 1 + AUDIO_PAYLOAD
        else:
            raise SmvFormatError(
                f"bad packet tag 0x{tag:02x} at file offset {q} "
                f"(expected 'V' 0x56 or 'A' 0x41; context: "
                f"{data[max(q - 4, 0):q + 5].hex(' ')}) — refusing to "
                f"guess; the container walk must be exact")
        if q > n:
            print(f"  note: trailing partial packet ({q - n} bytes short)")
    v, a = b"".join(video), b"".join(audio)
    print(f"  {len(video)} video packets ({len(v)} bytes), "
          f"{len(audio)} audio packets ({len(a)} bytes)")
    return v, a, a_vbytes


# ------------------------------------------------------------------- video

def tr_of_packet(data):
    """TR = the 8 bits after the 22-bit PSC (packets start at a PSC)."""
    v = int.from_bytes(data[:4], "big")
    return (v >> 2) & 0xFF


def cum_tr_delta(tr, prev_tr):
    """Unwrapped TR advance, guarding duplicate-TR / wrap glitches."""
    if prev_tr is None:
        return 0
    delta = (tr - prev_tr) % 256
    if delta == 0 or delta > 100:
        delta = 3
    return delta


def encode_video(video, out):
    """Decode the H.263 stream, encode H.264 with VFR timing from TR.

    Returns (encoded packets, frame count, duration in seconds).
    """
    src = av.open(io.BytesIO(video), format="h263")
    ist = src.streams.video[0]
    if not ist.width or not ist.height:
        raise SmvFormatError("could not determine video dimensions "
                             "from the H.263 stream")
    ost = out.add_stream("libx264", rate=None, options={
        "crf": "18", "preset": "medium", "bf": "0"})
    ost.width, ost.height = ist.width, ist.height
    ost.pix_fmt = "yuv420p"
    ost.time_base = TICK
    ost.codec_context.time_base = TICK  # else 1/24 default quantizes PTS

    pkts = []
    n = 0
    cum = 0
    prev_tr = None
    last_pts = -1
    for packet in src.demux(ist):
        if packet.size == 0:
            continue
        tr = tr_of_packet(bytes(packet)[:4])
        cum += cum_tr_delta(tr, prev_tr)
        prev_tr = tr
        for frame in packet.decode():
            if cum <= last_pts:
                cum = last_pts + 1
            last_pts = cum
            frame.pts = cum
            frame.time_base = TICK
            # Every H.263 picture in these files is intra-coded, and x264
            # obeys an input frame's I type — clear it, or the H.264
            # output is all keyframes (~75% larger, no visible gain).
            frame.pict_type = PictureType.NONE
            pkts += list(ost.encode(frame))
            n += 1
            if n % 2000 == 0:
                print(f"  {n} frames, t={float(cum * TICK):.1f}s")
    pkts += list(ost.encode())
    print(f"  video: {n} frames, {ist.width}x{ist.height}, "
          f"duration {float(cum * TICK):.1f}s")
    return pkts, n, float(cum * TICK)


def video_timeline(video):
    """(frame byte offsets, cumulative TR seconds) for the H.263 stream.

    PSCs are byte-aligned 00 00 8x in this stream; the `28 04` bytes at
    +4/+5 (fixed PTYPE/QCIF signature of these recordings) filter out
    coincidental byte patterns in the compressed data. If nothing
    matches the signature (a non-QCIF file), fall back to bare PSCs.
    """
    def scan(require_signature):
        offs = []
        i = video.find(b"\x00\x00")
        while i != -1 and i < len(video) - 8:
            if (video[i + 2] & 0xFC) == 0x80 and (
                    not require_signature
                    or (video[i + 4] == 0x28 and video[i + 5] == 0x04)):
                offs.append(i)
            i = video.find(b"\x00\x00", i + 1)
        return offs

    pscs = scan(require_signature=True) or scan(require_signature=False)
    cum, prev, times = 0, None, []
    for off in pscs:
        tr = tr_of_packet(video[off:off + 4])
        cum += cum_tr_delta(tr, prev)
        prev = tr
        times.append(float(cum * TICK))
    return pscs, times


# ------------------------------------------------------------------- audio

def retime_audio(audio, a_vbytes, pscs, times):
    """Decode G.723.1 to PCM, restoring recording pauses as silence.

    Each audio packet's capture time is approximated by the video
    timeline at its mux position (the frame containing the video byte it
    follows). Audio stays continuous (30 ms/frame) through normal
    interleave jitter, but a mux gap >= GAP_THRESH seconds is a real
    recording pause and is filled with digital silence.
    """
    def stream_time(vb):
        return times[max(bisect.bisect_right(pscs, vb) - 1, 0)]

    pcm = bytearray()
    t = 0.0
    n_gaps = 0
    silence_total = 0.0
    src = av.open(io.BytesIO(audio), format="g723_1")
    i = 0
    for packet in src.demux(audio=0):
        for frame in packet.decode():
            if i < len(a_vbytes):
                s = stream_time(a_vbytes[i])
                if s - t >= GAP_THRESH:
                    pcm += b"\x00" * (int(round((s - t) * ARATE)) * 2)
                    silence_total += s - t
                    n_gaps += 1
                    t = s
            pcm += bytes(frame.planes[0])[:frame.samples * 2]
            t += frame.samples / ARATE
            i += 1
    print(f"  {i} audio frames; {n_gaps} pauses restored "
          f"({silence_total:.2f}s silence); audio {len(pcm) / 2 / ARATE:.2f}s")
    return bytes(pcm)


def encode_audio(pcm, out):
    """Encode the re-timed PCM as AAC; returns the encoded packets."""
    ast = out.add_stream("aac", rate=ARATE, layout="mono")
    atb = Fraction(1, ARATE)
    pkts = []
    CHUNK = 1024
    total = len(pcm) // 2
    for s0 in range(0, total, CHUNK):
        n = min(CHUNK, total - s0)
        af = av.AudioFrame(format="s16", layout="mono", samples=n)
        af.sample_rate = ARATE
        af.planes[0].update(pcm[s0 * 2:(s0 + n) * 2])
        af.pts = s0
        af.time_base = atb
        pkts += list(ast.encode(af))
    pkts += list(ast.encode())
    return [p for p in pkts if p.dts is not None]


# -------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert a SmithMicro VideoLink Mail .smv recording "
                    "to MP4 (H.264 + AAC).")
    ap.add_argument("input", help="input .smv file")
    ap.add_argument("output", nargs="?",
                    help="output .mp4 (default: input name with .mp4)")
    ap.add_argument("--video-only", action="store_true",
                    help="skip the audio stream even if present")
    ap.add_argument("--keep-elementary", action="store_true",
                    help="also dump the depacketized elementary streams "
                         "as video.h263 / audio.raw")
    args = ap.parse_args(argv)

    in_path = Path(args.input)
    out_path = Path(args.output) if args.output else in_path.with_suffix(".mp4")
    av.logging.set_level(av.logging.ERROR)

    data = in_path.read_bytes()
    start = find_first_video_tag(data)
    print(f"{in_path.name}: {len(data)} bytes, header ends at offset {start}")
    video, audio, a_vbytes = depacketize(data, start)

    if args.keep_elementary:
        Path("video.h263").write_bytes(video)
        Path("audio.raw").write_bytes(audio)
        print("  wrote video.h263 and audio.raw")

    out = av.open(str(out_path), "w")
    print("encoding video (H.263 -> H.264, VFR from TR ticks)...")
    vpkts, n_frames, duration = encode_video(video, out)

    apkts = []
    if args.video_only:
        print("audio: skipped (--video-only)")
    elif not audio:
        print("audio: none in this file")
    else:
        print("re-timing audio against the mux interleaving...")
        pscs, times = video_timeline(video)
        pcm = retime_audio(audio, a_vbytes, pscs, times)
        apkts = encode_audio(pcm, out)

    pkts = [p for p in vpkts + apkts if p.dts is not None]
    for p in sorted(pkts, key=lambda p: float(p.dts * p.time_base)):
        out.mux(p)
    out.close()
    print(f"done: {n_frames} frames, {duration:.1f}s -> {out_path}")


if __name__ == "__main__":
    try:
        main()
    except SmvFormatError as e:
        sys.exit(f"error: {e}")
