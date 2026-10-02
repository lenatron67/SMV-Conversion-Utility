# smv2mp4 — recover SmithMicro VideoLink Mail (.smv) recordings

Around 1999–2002, Philips webcams shipped with **SmithMicro VideoLink
Mail**, a video-email application that saved recordings as `.smv` files.
No modern player opens them, the original software is long gone, and the
format was never documented. If you have old `.smv` home recordings you
thought were lost, this tool converts them to ordinary MP4 (H.264 +
AAC) that plays anywhere — using only open-source software.

**The easiest way:** open
**<https://lenatron67.github.io/SMV-Conversion-Utility/>**, choose your
`.smv` file, and save the MP4. It runs entirely inside your web browser —
nothing to install, and your video is never uploaded anywhere.

Or, with Python:

```
python smv2mp4.py "my old recording.smv"
```

## The format (so you don't have to reverse-engineer it again)

An `.smv` file is not a proprietary codec — it's a trivial packet
container (internally "VLMUX") wrapping two completely standard streams:

```
offset 0        header (453 bytes in known files; the tool locates
                the first packet by scanning, so it doesn't assume 453)
then, packets:  'V' (0x56) + 64 bytes of video payload   (65-byte packets)
                'A' (0x41) + 24 bytes of audio payload   (25-byte packets)
```

- Concatenated **V** payloads form a plain **ITU-T H.263** elementary
  stream (QCIF 176×144 in known files), decodable by stock FFmpeg. Frame
  timing comes from each picture header's TR field, a tick counter of
  the 30000/1001 Hz clock — the recordings are variable-frame-rate,
  around 10 fps.
- Concatenated **A** payloads form a **G.723.1** audio stream at
  6.3 kbit/s (24-byte frames, 30 ms each, 8 kHz mono).
- The recorder pauses the audio stream during silence. The pauses
  survive only as stretches of the mux containing V packets but no A
  packets, so a naive gapless decode of the audio drifts steadily ahead
  of the video. The tool reconstructs each audio packet's capture time
  from its position between video packets and fills real gaps
  (≥ 0.25 s) with silence, keeping A/V sync over the whole recording.

The one-byte tags interrupting the video stream at byte-aligned
positions are what made the format look like a proprietary H.263 variant
for a very long time. The full reverse-engineering story — eleven
sessions of statistical attacks on what turned out to be a mux layer —
is preserved in [`VLC_REVERSE_ENGINEERING.md`](VLC_REVERSE_ENGINEERING.md),
and the scripts that got there live in [`research/`](research/README.md).

## Install

Python 3 and [PyAV](https://pyav.org/) (its wheels bundle FFmpeg, so
there is nothing else to install):

```
pip install -r requirements.txt
```

## Usage

```
python smv2mp4.py input.smv [output.mp4]
```

With no output name, `input.smv` becomes `input.mp4`. Options:

- `--video-only` — skip the audio stream even if present.
- `--keep-elementary` — also write the depacketized raw streams as
  `video.h263` and `audio.raw` (useful if you want to run your own
  tools on them).

The conversion re-encodes video with x264 at CRF 18 (visually lossless
for this material) and audio as AAC. A ~62 MB, 18-minute recording
converts in about a minute.

If the tool reports a bad packet tag, the file is either damaged or a
container variant we haven't seen — please open an issue and include the
reported offset and hex context.

## Web version

[`web/`](web/) is the same converter as a static web page, published to
GitHub Pages by [`.github/workflows/pages.yml`](.github/workflows/pages.yml).
[`web/site/smv.js`](web/site/smv.js) is a JavaScript port of the container
and timing logic above; FFmpeg compiled to WebAssembly
([ffmpeg.wasm](https://github.com/ffmpegwasm/ffmpeg.wasm)) does the codec
work in the browser with the same x264/AAC settings. Because the FFmpeg
command line can't take per-frame timestamps for raw H.263, the page hands
the video frames to FFmpeg in a small Matroska wrapper that carries the TR
timestamps.

```
cd web
npm install                  # also copies ffmpeg.wasm into site/vendor/
npm run serve                # http://127.0.0.1:8080/
npm test -- some.smv ...     # headless Chrome converts via the real page
python test/compare.py       # checks those MP4s against smv2mp4.py
```

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
