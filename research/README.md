# research/ — historical reverse-engineering material

Everything in this directory is the archaeological record of how the
.smv format was cracked, preserved as-is. **None of it is needed to
convert files** — the working tool is `../smv2mp4.py`.

The full session-by-session story (hypothesis ledger #1–#37) is in
`../VLC_REVERSE_ENGINEERING.md`. The short version: what looked for ten
sessions like a proprietary H.263 variant turned out to be a trivial
packet container (VLMUX) interleaving standard H.263 video with G.723.1
audio; the statistical campaign's known-plaintext mining is what finally
exposed the constant byte-aligned tag insertions.

Notes:

- These scripts were written to run from the repository root and assume
  their inputs (`raw_h263.bin`, `video.h263`, `audio.raw`, catalogs,
  sample dumps) in the working directory. The regenerable artifacts now
  live in this directory, so run scripts from here — but expect
  breakage; nothing here is maintained.
- `depacket.py` → `assemble_mp4.py` → `retime_audio.py` is the original
  three-step pipeline that `smv2mp4.py` superseded (folded into one
  command, no intermediate files). `mux_audio.py` was the first, naive
  gapless audio mux — superseded by `retime_audio.py`.
- `bitreader.py` is the canonical MSB-first bit reader all later
  analysis scripts shared. `smv2mp4.py` inlined the small PSC/TR scan
  it needs, so nothing outside this directory imports it anymore.
- `i263_src/` (and `I263Src.zip`) is Maxim Poliakovski's LGPL "Free
  Implementation of the I.263 Video decoder"
  (http://multimedia.cx/I263Src.zip, via
  https://wiki.multimedia.cx/index.php/I263), used strictly as a
  *reading reference* during research. LGPL v2-or-later, so
  redistribution here is fine — but no code from it appears in
  `smv2mp4.py` or anywhere outside this directory.
- `ffmpeg_src/` holds reference copies of FFmpeg's H.263 decoder tables
  consulted during the campaign (LGPL, reference only, same deal).
