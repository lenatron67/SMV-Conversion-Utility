// Copy the ffmpeg.wasm runtime from node_modules into site/vendor/ so the
// site serves it from its own origin (no CDN, nothing in git). Runs on
// `npm install` / `npm ci` and in the GitHub Pages workflow.

import { cpSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const mods = join(web, "node_modules", "@ffmpeg");
const out = join(web, "site", "vendor");

rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, "ffmpeg"), { recursive: true });
mkdirSync(join(out, "core"), { recursive: true });

const wrapper = join(mods, "ffmpeg", "dist", "esm");
for (const f of readdirSync(wrapper)) {
  if (/\.m?js$/.test(f)) cpSync(join(wrapper, f), join(out, "ffmpeg", f));
}
const core = join(mods, "core", "dist", "esm");
for (const f of ["ffmpeg-core.js", "ffmpeg-core.wasm"]) cpSync(join(core, f), join(out, "core", f));

const version = (pkg) => JSON.parse(readFileSync(join(mods, pkg, "package.json"), "utf8")).version;
const manifest = {
  ffmpeg: version("ffmpeg"),
  core: version("core"),
  wasmBytes: statSync(join(out, "core", "ffmpeg-core.wasm")).size,
};
writeFileSync(join(out, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");

writeFileSync(join(out, "LICENSES.txt"), `Third-party software served from this directory
================================================

ffmpeg/   @ffmpeg/ffmpeg ${manifest.ffmpeg} — MIT License
core/     @ffmpeg/core ${manifest.core} — FFmpeg and x264 compiled to
          WebAssembly, GPL-2.0-or-later

Source code, including the build scripts and the exact FFmpeg and x264
versions used: https://github.com/ffmpegwasm/ffmpeg.wasm
FFmpeg: https://ffmpeg.org/   x264: https://www.videolan.org/developers/x264.html
`);

console.log(`vendored @ffmpeg/ffmpeg ${manifest.ffmpeg} + @ffmpeg/core ${manifest.core} -> site/vendor/`);
