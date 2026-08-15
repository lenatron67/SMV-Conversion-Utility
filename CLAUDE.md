# SMV Video Recovery — Project Index

## STATUS: ✅ SOLVED (session 11, 2026-08-03)

**Active work: session 12 — tidy-up & packaging.** See "Next actions
(session 12)" at the bottom of `VLC_REVERSE_ENGINEERING.md`. Progress:
steps 1–2 done (single `smv2mp4.py` command, output verified identical
to the session-11 deliverable modulo 3 metadata bytes; repo restructured
with research scripts → `research/`). License decided: **GPLv3**.
Remaining: README.md (step 3), LICENSE file (step 4), clean-checkout
validation + delete superseded MP4 (step 5).

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

**`smv2mp4.py <input.smv> [output.mp4]`** — the single-command tool
(GPLv3, only dependency PyAV, pinned in `requirements.txt`). In memory,
no intermediate files: locates the first 'V' tag by scan (not hardcoded
offset), depacketizes with zero tolerance (any bad tag aborts loudly),
decodes H.263 → encodes H.264 (crf 18, bf=0) with VFR timing from TR
ticks (time base 1001/30000, `codec_context.time_base` set explicitly —
session-11 PTS-quantization bug), re-times G.723.1 audio against the mux
interleaving (the recorder paused audio 25 times; 6.79s of silence
restored — without this, audio drifts ~7s ahead by the end), encodes
AAC, muxes. Options: `--video-only`, `--keep-elementary`. Tolerates
files with no audio. Output verified: identical to the session-11
deliverable except 3 bytes of `btrt` avgBitrate metadata.

(The original three-step pipeline it folded — `depacket.py` →
`assemble_mp4.py` → `retime_audio.py` — now lives in `research/`.)

## Reference material

- `VLC_REVERSE_ENGINEERING.md` — the full session-by-session research log
  (hypothesis ledger #1–#37). The final section explains how the mining
  of known-plaintext pairs cracked the container. Historically valuable;
  no active work remains.
- `research/` — all exploration/attack scripts, their outputs, the
  original pipeline scripts, `bitreader.py` (canonical MSB-first bit
  reader; `smv2mp4.py` inlined the PSC/TR scan so nothing imports it
  anymore), and the I263/FFmpeg reference sources. See
  `research/README.md`. None of it is needed to convert files.

## Standing instructions (kept for history)

- Do not check the Windows registry for codec entries without the user's
  explicit go-ahead in that session.
- The user chose the open-source reverse-engineering path (Option E) over
  installing the original codec — vindicated: the final pipeline has no
  proprietary dependency.
