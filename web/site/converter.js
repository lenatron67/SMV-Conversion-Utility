// converter.js — the smv2mp4 pipeline in the browser.
// Copyright (C) 2026 Michael Leonard — SPDX-License-Identifier: GPL-3.0-or-later
//
// smv.js does the container work (depacketizing, TR timestamps, audio
// pause restoration); ffmpeg.wasm (single-threaded, in a Web Worker)
// does the codec work with the same encoder settings as smv2mp4.py.
// Everything stays on the user's machine.

import { FFmpeg } from "./vendor/ffmpeg/index.js";
import * as smv from "./smv.js";

export { SmvFormatError } from "./smv.js";

// The browser could not read the file at all (e.g. a folder was dropped).
export class FileReadError extends Error {
  constructor(msg) { super(msg); this.name = "FileReadError"; }
}

const VENDOR = new URL("./vendor/", import.meta.url);
const SNIFF_BYTES = 1 << 20;  // ample for the header + trial walk

// Same x264 settings as smv2mp4.encode_video.
const X264 = ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-bf", "0", "-pix_fmt", "yuv420p"];

// The smallest module using WebAssembly SIMD. Browsers only enable SIMD on
// x86 CPUs with SSE4.1 (not on e.g. AMD Phenom II / Athlon II, or Intel
// before 2008), and the standard ffmpeg.wasm core needs it, so those
// computers get the same core built without SIMD: vendor/core-nosimd/,
// from .github/workflows/core-nosimd.yml.
const SIMD_PROBE = new Uint8Array([
  0, 97, 115, 109, 1, 0, 0, 0,  // "\0asm", version 1
  1, 5, 1, 0x60, 0, 1, 0x7b,    // type section: () -> v128
  3, 2, 1, 0,                   // function section: one function of that type
  10, 22, 1, 20, 0, 0xfd, 12,   // code section: v128.const 0 (16 bytes), end
  0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 11,
]);

export class Converter {
  #ffmpeg = null;
  #loading = null;
  #log = [];
  #onLine = null;
  #simd = null;

  get loaded() { return this.#ffmpeg !== null; }

  // Whether load() chose the standard (SIMD) core; null before load().
  get simd() { return this.#simd; }

  // Download (first visit only — then the browser cache has it) and
  // start ffmpeg.wasm. onProgress(fraction) reports the download.
  load(onProgress = () => {}) {
    if (!this.#loading) {
      this.#loading = this.#load(onProgress).catch((e) => {
        this.#loading = null;
        throw e;
      });
    }
    return this.#loading;
  }

  async #load(onProgress) {
    this.#simd = WebAssembly.validate(SIMD_PROBE);
    const core = this.#simd ? "core" : "core-nosimd";
    const manifest = await (await fetch(new URL("manifest.json", VENDOR))).json();
    const wasmBytes = this.#simd ? manifest.wasmBytes : manifest.nosimdWasmBytes;
    const res = await fetch(new URL(`${core}/ffmpeg-core.wasm`, VENDOR));
    if (!res.ok) throw new Error(`could not download the converter (HTTP ${res.status})`);
    // The stream yields decompressed bytes, so measure against the
    // uncompressed size recorded at build time.
    const reader = res.body.getReader();
    const chunks = [];
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      got += value.length;
      onProgress(Math.min(got / wasmBytes, 1));
    }
    const wasmURL = URL.createObjectURL(new Blob(chunks, { type: "application/wasm" }));
    try {
      const ffmpeg = new FFmpeg();
      ffmpeg.on("log", ({ message }) => {
        this.#log.push(message);
        if (this.#log.length > 400) this.#log.splice(0, 200);
        this.#onLine?.(message);
      });
      await ffmpeg.load({ coreURL: new URL(`${core}/ffmpeg-core.js`, VENDOR).href, wasmURL });
      this.#ffmpeg = ffmpeg;
    } finally {
      URL.revokeObjectURL(wasmURL);
    }
  }

  async #exec(args, onLine = null) {
    this.#log = [];
    this.#onLine = onLine;
    try {
      const ret = await this.#ffmpeg.exec(args);
      if (ret !== 0) throw new Error(`ffmpeg exited with code ${ret}:\n${this.#log.slice(-12).join("\n")}`);
    } finally {
      this.#onLine = null;
    }
  }

  // ffmpeg.wasm can be left in a bad state by a failed run; start afresh.
  #reset() {
    try { this.#ffmpeg?.terminate(); } catch { /* already gone */ }
    this.#ffmpeg = null;
    this.#loading = null;
  }

  // Convert one .smv file (a File or Blob). onStage(stage, detail) reports
  // progress: "reading", "sound", "picture" (detail = {fraction, secondsLeft}).
  // Resolves to {blob, frames, seconds, hasAudio, pauses}.
  async convert(file, onStage = () => {}) {
    onStage("reading");
    const read = async (blob) => {
      try {
        return new Uint8Array(await blob.arrayBuffer());
      } catch (e) {
        throw new FileReadError(`could not read the file (${e.name}: ${e.message})`);
      }
    };
    // Reject the wrong kind of file from its first megabyte, before
    // reading what might be a very large file into memory.
    smv.findFirstVideoTag(await read(file.slice(0, SNIFF_BYTES)));
    let data = await read(file);
    const start = smv.findFirstVideoTag(data);
    const dp = smv.depacketize(data, start);
    data = null;
    const vf = smv.videoFrames(dp.video);
    if (vf.offs.length === 0) throw new smv.SmvFormatError("no video pictures found in this file");

    await this.load();

    const files = [];
    const write = async (name, bytes) => {
      files.push(name);
      await this.#ffmpeg.writeFile(name, bytes);  // transfers (detaches) bytes
    };
    try {
      let pauses = 0;
      const hasAudio = dp.audio.length > 0;
      if (hasAudio) {
        onStage("sound");
        await write("in.g723", dp.audio);
        await this.#exec(["-hide_banner", "-f", "g723_1", "-i", "in.g723",
                          "-f", "s16le", "-c:a", "pcm_s16le", "-ac", "1", "-ar", "8000", "decoded.pcm"]);
        files.push("decoded.pcm");
        const pcm = await this.#ffmpeg.readFile("decoded.pcm");
        const tl = smv.videoTimeline(dp.video);
        const rt = smv.retimeAudio(pcm, dp.aVbytes, tl.pscs, tl.times);
        pauses = rt.nGaps;
        await write("audio.pcm", rt.pcm);
      }

      const total = vf.offs.length;
      const seconds = smv.tickSeconds(vf.ticks[total - 1]);
      await write("video.mkv", smv.buildMkv(dp.video, vf));

      onStage("picture", { fraction: 0, secondsLeft: null });
      const t0 = performance.now();
      const onLine = (line) => {
        const m = /frame=\s*(\d+)/.exec(line);
        if (!m) return;
        const done = Math.min(+m[1] / total, 1);
        const elapsed = (performance.now() - t0) / 1000;
        const secondsLeft = done > 0.02 ? elapsed * (1 - done) / done : null;
        onStage("picture", { fraction: done, secondsLeft });
      };
      const audioIn = hasAudio ? ["-f", "s16le", "-ar", "8000", "-ac", "1", "-i", "audio.pcm"] : [];
      const audioOut = hasAudio ? ["-map", "1:a:0", "-c:a", "aac"] : [];
      files.push("out.mp4");
      await this.#exec(["-hide_banner",
                        "-f", "matroska", "-i", "video.mkv", ...audioIn,
                        "-map", "0:v:0", ...X264, "-fps_mode", "passthrough",
                        ...audioOut, "out.mp4"], onLine);
      const mp4 = await this.#ffmpeg.readFile("out.mp4");
      onStage("picture", { fraction: 1, secondsLeft: 0 });
      return { blob: new Blob([mp4], { type: "video/mp4" }), frames: total, seconds, hasAudio, pauses };
    } catch (e) {
      this.#reset();
      throw e;
    } finally {
      if (this.#ffmpeg) {
        for (const f of files) {
          try { await this.#ffmpeg.deleteFile(f); } catch { /* not created */ }
        }
      }
    }
  }
}

// "My video.smv" -> "My video.mp4"
export const mp4Name = (name) => name.replace(/\.smv$/i, "") + ".mp4";
