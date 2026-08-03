# SMV Video Recovery — Project Index

## STATUS: ✅ SOLVED (session 11, 2026-08-03)

- **File:** `Nana playing conputer.smv` (62 MB, dated 14/01/2001) — Philips
  webcam recording made with SmithMicro VideoLink Mail software.
- **Goal (achieved):** decoded and converted to MP4 with 100% open-source
  tooling — **`Nana playing computer (with audio).mp4`** (10,208 frames,
  18:31, QCIF 176×144 VFR ~10fps, AAC audio).

## The answer (ledger #37 in VLC_REVERSE_ENGINEERING.md)

**There was never a proprietary codec.** The .smv file is a VLMUX *packet
container*:

- byte 0–452: header; first packet tag at file offset **453**
- `'V' (0x56)` + 64 bytes video payload  → 65-byte video packets (941,059)
- `'A' (0x41)` + 24 bytes audio payload  → 25-byte audio packets (36,812)

Concatenated V payloads = **plain standard H.263** (stock FFmpeg decodes
all 10,208 frames with zero errors). Concatenated A payloads = **G.723.1
@ 6.3 kbit/s** (24-byte/30ms frames — the file HAS audio, 1104s of it).
Every earlier "proprietary VLC table" symptom was mux tag bytes
interrupting the bitstream at byte-aligned positions; the multi-session
statistical campaign (hypothesis ledger #1–#36) was chasing a container
layer, and its known-plaintext harvest (#35/#36) is what finally exposed
the constant byte-aligned 0x56 insertions.

## The recovery pipeline (all open-source, rerunnable)

1. `depacket.py` — walks the .smv-derived `raw_h263.bin` as a tag+payload
   packet stream (zero tolerance: the full-file walk has 0 bad tags)
   → `video.h263` + `audio.raw`
2. `assemble_mp4.py` — stock FFmpeg h263 decode → H.264/MP4, faithful
   variable-frame-rate timing from H.263 TR ticks (time base 1001/30000)
   → `Nana playing computer.mp4`
3. `mux_audio.py` — decodes `audio.raw` as `g723_1`, encodes AAC 8kHz
   mono, muxes with the video → `Nana playing computer (with audio).mp4`

## Reference material

- `VLC_REVERSE_ENGINEERING.md` — the full session-by-session research log
  (hypothesis ledger #1–#37). The final section explains how the mining
  of known-plaintext pairs cracked the container. Historically valuable;
  no active work remains.
- `bitreader.py` — canonical MSB-first bit reader (still the thing to
  import if any bitstream poking is ever needed again).
- Earlier exploration/attack scripts and their outputs remain in the
  folder for the record; none are needed for the pipeline above.

## Standing instructions (kept for history)

- Do not check the Windows registry for codec entries without the user's
  explicit go-ahead in that session.
- The user chose the open-source reverse-engineering path (Option E) over
  installing the original codec — vindicated: the final pipeline has no
  proprietary dependency.
