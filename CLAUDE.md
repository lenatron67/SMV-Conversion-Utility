# SMV Video Recovery — Project Index

## STATUS: ✅ SOLVED (session 11, 2026-08-03) — web version built (session 13)

**Session 13 (2026-10-02): in-browser web version** for non-technical
users (the main audience — people with ~2000-era home videos). `web/` is
a static site: `web/site/smv.js` ports the container/timing logic to JS;
ffmpeg.wasm 0.12 (single-threaded, served same-origin from
`site/vendor/`, copied there by `npm install` → `tools/vendor.mjs`, not in
git) does the codecs. Video frames go to ffmpeg in a JS-built Matroska
wrapper carrying the TR timestamps (ffmpeg CLI can't take per-frame
timestamps for raw H.263). Validated on the Nana file via the real page
in headless Chrome (`npm test` → `test/compare.py`): 10,208 frames,
timestamps exact, 25 pauses / audio 1111.15s, luma PSNR 44 dB vs source;
JS audio re-timing is byte-identical to Python's. ~2.5–4.5 min per 18-min
file in Chrome on this machine. Published by `.github/workflows/pages.yml`
(needs repo Settings → Pages → Source: GitHub Actions).
Open items: deploy + check the live site (gzip on the 32 MB wasm), test in
a real browser window / Edge / Firefox / Safari / an older PC, run the 18
other real .smv files through `npm test`.
Also fixed in session 13: every H.263 picture in these files is intra,
the decoded frames carry pict_type I, and PyAV passed that to x264, so
`smv2mp4.py` output was all-keyframe (10,208 keyframes, ~59 MB). It now
clears pict_type before encoding: 43 keyframes, 34.6 MB, PSNR 44.0 dB vs
source (was 45.7 — both invisible), all timing/audio numbers unchanged;
still deterministic (SHA256 8d27c429…, identical across runs).

**Session 12 (tidy-up & packaging) complete, 2026-08-15.** (The Python
tool is finished; session 13's web version is the only active work.) The repo is a finished, GPLv3-licensed tool: single
`smv2mp4.py` command, stranger-friendly README, research material under
`research/`. Clean-checkout validated: fresh clone + fresh venv
reproduces the session-11 deliverable bit-for-bit (deterministic
SHA256), all numbers matching (10,208 frames / 1111.2s / audio
1111.15s). Also batch-validated against 18 additional real .smv files —
all converted; header sizes observed: 453, 478, 503 (the scan-based
header detection handles all three; a hardcoded 453 would have failed
on 8 of them).

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
files with no audio. Output verified (session 12): identical to the
session-11 deliverable except 3 bytes of `btrt` avgBitrate metadata.
Since session 13's keyframe fix the output is ~40% smaller and no longer
byte-comparable to the session-11 file (same frames, timing and audio).

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
