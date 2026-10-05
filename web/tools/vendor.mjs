// Copy the ffmpeg.wasm runtime from node_modules into site/vendor/ so the
// site serves it from its own origin (no CDN, nothing in git). Runs on
// `npm install` / `npm ci` and in the GitHub Pages workflow.
//
// Also fetches the same core built without WebAssembly SIMD, for browsers
// on CPUs without SSE4.1 (site/converter.js picks one). That build comes
// from .github/workflows/core-nosimd.yml, published as a release, and is
// pinned here by hash.

import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const NOSIMD = {
  version: "0.12.10",
  url: "https://github.com/lenatron67/SMV-Conversion-Utility/releases/download/core-nosimd-0.12.10/",
  sha256: {
    "ffmpeg-core.js": "275bdbf0cef606a60be2b528d62e6ee5d177954a84d5dd2e667dfb62f2e84dd2",
    "ffmpeg-core.wasm": "9f83ef7489af1d261a6e1471bba0e34d732b6343937ea123971e20dbca0027ca",
  },
};

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const mods = join(web, "node_modules", "@ffmpeg");
const out = join(web, "site", "vendor");

const version = (pkg) => JSON.parse(readFileSync(join(mods, pkg, "package.json"), "utf8")).version;
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

if (version("core") !== NOSIMD.version) {
  throw new Error(`@ffmpeg/core is ${version("core")} but the no-SIMD core is ${NOSIMD.version}: run the ` +
    `"Build no-SIMD core" workflow for the new version, then update NOSIMD in tools/vendor.mjs`);
}

rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, "ffmpeg"), { recursive: true });
mkdirSync(join(out, "core"), { recursive: true });
mkdirSync(join(out, "core-nosimd"), { recursive: true });

const wrapper = join(mods, "ffmpeg", "dist", "esm");
for (const f of readdirSync(wrapper)) {
  if (/\.m?js$/.test(f)) cpSync(join(wrapper, f), join(out, "ffmpeg", f));
}
const core = join(mods, "core", "dist", "esm");
for (const f of ["ffmpeg-core.js", "ffmpeg-core.wasm"]) cpSync(join(core, f), join(out, "core", f));

// Downloaded once into node_modules/.cache/, re-checked on every run.
const cache = join(web, "node_modules", ".cache", `core-nosimd-${NOSIMD.version}`);
mkdirSync(cache, { recursive: true });
for (const [f, sum] of Object.entries(NOSIMD.sha256)) {
  const cached = join(cache, f);
  if (!existsSync(cached) || sha256(readFileSync(cached)) !== sum) {
    const res = await fetch(NOSIMD.url + f);
    if (!res.ok) throw new Error(`downloading ${NOSIMD.url + f}: HTTP ${res.status}`);
    const bytes = Buffer.from(await res.arrayBuffer());
    if (sha256(bytes) !== sum) throw new Error(`${NOSIMD.url + f} does not match its pinned SHA-256`);
    writeFileSync(cached, bytes);
  }
  cpSync(cached, join(out, "core-nosimd", f));
}

const manifest = {
  ffmpeg: version("ffmpeg"),
  core: version("core"),
  wasmBytes: statSync(join(out, "core", "ffmpeg-core.wasm")).size,
  nosimdWasmBytes: statSync(join(out, "core-nosimd", "ffmpeg-core.wasm")).size,
};
writeFileSync(join(out, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");

writeFileSync(join(out, "LICENSES.txt"), `Third-party software served from this directory
================================================

ffmpeg/       @ffmpeg/ffmpeg ${manifest.ffmpeg} — MIT License
core/         @ffmpeg/core ${manifest.core} — FFmpeg and x264 compiled to
              WebAssembly, GPL-2.0-or-later
core-nosimd/  the same, built without WebAssembly SIMD for older
              processors — GPL-2.0-or-later

Source code, including the build scripts and the exact FFmpeg and x264
versions used: https://github.com/ffmpegwasm/ffmpeg.wasm
core-nosimd/ is upstream's build of ffmpeg.wasm v${NOSIMD.version} with "-O3" in place of
"-O3 -msimd128" and libwebp's SIMD turned off, made by this workflow (the
release page links its build log):
https://github.com/lenatron67/SMV-Conversion-Utility/blob/main/.github/workflows/core-nosimd.yml
Release: ${NOSIMD.url.replace("/download/", "/tag/").replace(/\/$/, "")}
FFmpeg: https://ffmpeg.org/   x264: https://www.videolan.org/developers/x264.html
`);

console.log(`vendored @ffmpeg/ffmpeg ${manifest.ffmpeg} + @ffmpeg/core ${manifest.core} ` +
  `(+ no-SIMD build) -> site/vendor/`);
