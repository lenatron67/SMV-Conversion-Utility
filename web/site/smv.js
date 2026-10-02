// smv.js — JavaScript port of the container/timing logic in smv2mp4.py.
// Copyright (C) 2026 Michael Leonard — SPDX-License-Identifier: GPL-3.0-or-later
//
// Pure byte handling: finding the packet stream, splitting the 'V'/'A'
// packets, H.263 TR timestamps, and restoring recording pauses in the
// audio. The codec work (H.263 / G.723.1 decode, H.264 / AAC encode)
// is done by ffmpeg.wasm; buildMkv() hands it the video frames with
// their real timestamps, since the ffmpeg command line has no way to
// take per-frame timestamps for a raw H.263 stream.

export const VIDEO_TAG = 0x56;      // 'V'
export const AUDIO_TAG = 0x41;      // 'A'
export const VIDEO_PAYLOAD = 64;
export const AUDIO_PAYLOAD = 24;
export const ARATE = 8000;          // G.723.1 sample rate
export const FRAME_SAMPLES = 240;   // one G.723.1 frame = 30 ms
export const GAP_THRESH = 0.25;     // seconds of mux-timeline lead = a real pause

// One H.263 TR tick = 1001/30000 s (29.97 Hz clock).
export const tickSeconds = (ticks) => ticks * 1001 / 30000;

export class SmvFormatError extends Error {
  constructor(msg) { super(msg); this.name = "SmvFormatError"; }
}

const hex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(" ");

// ---------------------------------------------------------------- container

function trialWalk(data, start, maxPackets = 256) {
  let q = start, seen = 0;
  while (q < data.length && seen < maxPackets) {
    const tag = data[q];
    if (tag === VIDEO_TAG) q += 1 + VIDEO_PAYLOAD;
    else if (tag === AUDIO_TAG) q += 1 + AUDIO_PAYLOAD;
    else return false;
    seen++;
  }
  return true;
}

// Locate the first 'V' packet tag (end of the .smv header): a 0x56 byte
// immediately followed by a Picture Start Code (00 00 8x), confirmed by
// trial-walking the packet stream.
export function findFirstVideoTag(data) {
  for (let i = 0; i + 3 < data.length; i++) {
    if (data[i] === 0x56 && data[i + 1] === 0 && data[i + 2] === 0 &&
        (data[i + 3] & 0xFC) === 0x80 && trialWalk(data, i)) return i;
  }
  throw new SmvFormatError(
    "no 'V'-tagged H.263 packet stream found — this file does not " +
    "look like a VLMUX .smv recording");
}

// Walk the tag+payload packet stream, splitting elementary streams.
// aVbytes[i] is the count of video bytes preceding audio packet i in the
// mux — the interleaving record needed to re-time the audio.
export function depacketize(data, start, log = () => {}) {
  const n = data.length;
  // Pass 1: validate every tag and size the output buffers.
  let q = start, nV = 0, nA = 0, vLen = 0, aLen = 0;
  while (q < n) {
    const tag = data[q];
    if (tag === VIDEO_TAG) {
      vLen += Math.min(VIDEO_PAYLOAD, n - q - 1);
      q += 1 + VIDEO_PAYLOAD;
      nV++;
    } else if (tag === AUDIO_TAG) {
      aLen += Math.min(AUDIO_PAYLOAD, n - q - 1);
      q += 1 + AUDIO_PAYLOAD;
      nA++;
    } else {
      throw new SmvFormatError(
        `bad packet tag 0x${tag.toString(16).padStart(2, "0")} at file ` +
        `offset ${q} (expected 'V' 0x56 or 'A' 0x41; context: ` +
        `${hex(data.subarray(Math.max(q - 4, 0), q + 5))}) — refusing ` +
        `to guess; the container walk must be exact`);
    }
  }
  if (q > n) log(`note: trailing partial packet (${q - n} bytes short)`);

  // Pass 2: copy the payloads out.
  const video = new Uint8Array(vLen);
  const audio = new Uint8Array(aLen);
  const aVbytes = new Uint32Array(nA);
  let vp = 0, ap = 0, ai = 0, vbytes = 0;
  q = start;
  while (q < n) {
    if (data[q] === VIDEO_TAG) {
      const p = data.subarray(q + 1, q + 1 + VIDEO_PAYLOAD);
      video.set(p, vp);
      vp += p.length;
      vbytes += VIDEO_PAYLOAD;
      q += 1 + VIDEO_PAYLOAD;
    } else {
      const p = data.subarray(q + 1, q + 1 + AUDIO_PAYLOAD);
      audio.set(p, ap);
      ap += p.length;
      aVbytes[ai++] = vbytes;
      q += 1 + AUDIO_PAYLOAD;
    }
  }
  log(`${nV} video packets (${vLen} bytes), ${nA} audio packets (${aLen} bytes)`);
  return { video, audio, aVbytes, nVideoPackets: nV, nAudioPackets: nA };
}

// ------------------------------------------------------------------- video

// TR = the 8 bits after the 22-bit PSC.
const trAt = (b, off) => ((b[off + 2] & 0x03) << 6) | (b[off + 3] >> 2);

// Unwrapped TR advance, guarding duplicate-TR / wrap glitches.
function cumTrDelta(tr, prevTr) {
  if (prevTr === null) return 0;
  const delta = (((tr - prevTr) % 256) + 256) % 256;
  return (delta === 0 || delta > 100) ? 3 : delta;
}

// Byte-aligned PSCs (00 00 8x). With requireSignature, also demand the
// `28 04` fixed PTYPE/QCIF bytes at +4/+5 (as smv2mp4.video_timeline).
function scanPscs(video, requireSignature, tailGuard) {
  const offs = [];
  for (let i = 0; i < video.length - tailGuard; i++) {
    if (video[i] === 0 && video[i + 1] === 0 && (video[i + 2] & 0xFC) === 0x80 &&
        (!requireSignature || (video[i + 4] === 0x28 && video[i + 5] === 0x04))) {
      offs.push(i);
    }
  }
  return offs;
}

// Split the H.263 stream into frames exactly as FFmpeg's h263 parser does
// (byte-aligned PSC boundaries) and give each its TR timestamp in ticks —
// the same timing smv2mp4.encode_video assigns.
export function videoFrames(video) {
  const offs = scanPscs(video, false, 3);
  const ticks = new Array(offs.length);
  let cum = 0, prev = null, last = -1;
  for (let k = 0; k < offs.length; k++) {
    const tr = trAt(video, offs[k]);
    cum += cumTrDelta(tr, prev);
    prev = tr;
    if (cum <= last) cum = last + 1;
    last = cum;
    ticks[k] = cum;
  }
  return { offs, ticks };
}

// (frame byte offsets, cumulative TR seconds) for the H.263 stream, as
// smv2mp4.video_timeline: signature-filtered PSCs, else bare PSCs.
export function videoTimeline(video) {
  let pscs = scanPscs(video, true, 8);
  if (pscs.length === 0) pscs = scanPscs(video, false, 8);
  const times = new Array(pscs.length);
  let cum = 0, prev = null;
  for (let k = 0; k < pscs.length; k++) {
    const tr = trAt(video, pscs[k]);
    cum += cumTrDelta(tr, prev);
    prev = tr;
    times[k] = tickSeconds(cum);
  }
  return { pscs, times };
}

// Frame size from the PTYPE source-format field (bits 35-37).
const SOURCE_FORMATS = { 1: [128, 96], 2: [176, 144], 3: [352, 288], 4: [704, 576], 5: [1408, 1152] };
export function frameSize(video, off = 0) {
  return SOURCE_FORMATS[(video[off + 4] >> 2) & 0x07] || [176, 144];
}

// ------------------------------------------------------------------- audio

// Python's round(): ties go to the even neighbour.
function roundHalfEven(x) {
  const r = Math.round(x);
  return (Math.abs(x % 1) === 0.5 && r % 2 !== 0) ? r - 1 : r;
}

function bisectRight(arr, x) {
  let lo = 0, hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (x < arr[mid]) hi = mid; else lo = mid + 1;
  }
  return lo;
}

// Restore recording pauses as silence in decoded G.723.1 PCM (s16le,
// 240 samples per frame), as smv2mp4.retime_audio: each audio packet's
// capture time is approximated by the video timeline at its mux
// position; a mux gap >= GAP_THRESH seconds is a real recording pause.
export function retimeAudio(pcmBytes, aVbytes, pscs, times, log = () => {}) {
  const pcm = new Int16Array(pcmBytes.slice().buffer);
  const nFrames = Math.ceil(pcm.length / FRAME_SAMPLES);
  const streamTime = (vb) => times[Math.max(bisectRight(pscs, vb) - 1, 0)];

  const gaps = [];  // [frame index, silence samples before it]
  let t = 0, silenceTotal = 0, gapSamples = 0;
  for (let i = 0; i < nFrames; i++) {
    if (i < aVbytes.length) {
      const s = streamTime(aVbytes[i]);
      if (s - t >= GAP_THRESH) {
        const n = roundHalfEven((s - t) * ARATE);
        gaps.push([i, n]);
        gapSamples += n;
        silenceTotal += s - t;
        t = s;
      }
    }
    t += Math.min(FRAME_SAMPLES, pcm.length - i * FRAME_SAMPLES) / ARATE;
  }

  const outPcm = new Int16Array(pcm.length + gapSamples);  // zero-filled
  let src = 0, dst = 0;
  for (const [i, n] of gaps) {
    const upto = i * FRAME_SAMPLES;
    outPcm.set(pcm.subarray(src, upto), dst);
    dst += upto - src + n;
    src = upto;
  }
  outPcm.set(pcm.subarray(src), dst);

  log(`${nFrames} audio frames; ${gaps.length} pauses restored ` +
      `(${silenceTotal.toFixed(2)}s silence); audio ` +
      `${(outPcm.length / ARATE).toFixed(2)}s`);
  return { pcm: new Uint8Array(outPcm.buffer), nGaps: gaps.length, silenceTotal };
}

// -------------------------------------------------------------- matroska

// Minimal Matroska writer: one H.263 video track (VfW 'H263' FourCC),
// one SimpleBlock per frame at its TR timestamp (1 ms resolution).
// Elements use 8-byte size fields throughout — valid EBML, simplest.

const enc = new TextEncoder();

function idBytes(id) {
  const b = [];
  for (let v = id; v > 0; v = Math.floor(v / 256)) b.unshift(v & 0xFF);
  return b;
}

function uintBytes(v) {
  const b = [];
  do { b.unshift(v & 0xFF); v = Math.floor(v / 256); } while (v > 0);
  return new Uint8Array(b);
}

const el = (id, ...kids) => ({ id, kids });
const uintEl = (id, v) => el(id, uintBytes(v));
const strEl = (id, s) => el(id, enc.encode(s));

function sizeOf(node) {
  if (node instanceof Uint8Array) return node.length;
  if (node.len === undefined) node.len = node.kids.reduce((a, k) => a + sizeOf(k), 0);
  return idBytes(node.id).length + 8 + node.len;
}

function writeNode(node, out, pos) {
  if (node instanceof Uint8Array) {
    out.set(node, pos);
    return pos + node.length;
  }
  for (const b of idBytes(node.id)) out[pos++] = b;
  out[pos++] = 0x01;  // 8-byte size vint
  for (let i = 6, v = node.len; i >= 0; i--) {
    out[pos + i] = v & 0xFF;
    v = Math.floor(v / 256);
  }
  pos += 7;
  for (const k of node.kids) pos = writeNode(k, out, pos);
  return pos;
}

function bitmapInfoHeader(width, height) {
  const b = new Uint8Array(40);
  const dv = new DataView(b.buffer);
  dv.setUint32(0, 40, true);       // biSize
  dv.setInt32(4, width, true);
  dv.setInt32(8, height, true);
  dv.setUint16(12, 1, true);       // biPlanes
  dv.setUint16(14, 24, true);      // biBitCount
  b.set(enc.encode("H263"), 16);   // biCompression FourCC
  return b;
}

export function buildMkv(video, { offs, ticks }) {
  const [width, height] = frameSize(video, offs[0] || 0);
  const clusters = [];
  let cluster = null, clusterMs = 0;
  for (let k = 0; k < offs.length; k++) {
    const ms = Math.round(ticks[k] * 1001 / 30);
    if (!cluster || ms - clusterMs > 30000) {
      clusterMs = ms;
      cluster = el(0x1F43B675, uintEl(0xE7, ms));
      clusters.push(cluster);
    }
    const rel = ms - clusterMs;
    const intra = (video[offs[k] + 4] & 0x02) === 0;  // PTYPE picture coding type
    const head = new Uint8Array([0x81, (rel >> 8) & 0xFF, rel & 0xFF, intra ? 0x80 : 0x00]);
    const end = k + 1 < offs.length ? offs[k + 1] : video.length;
    cluster.kids.push(el(0xA3, head, video.subarray(offs[k], end)));
  }

  const doc = [
    el(0x1A45DFA3,                        // EBML
      uintEl(0x4286, 1), uintEl(0x42F7, 1), uintEl(0x42F2, 4), uintEl(0x42F3, 8),
      strEl(0x4282, "matroska"), uintEl(0x4287, 2), uintEl(0x4285, 2)),
    el(0x18538067,                        // Segment
      el(0x1549A966,                      // Info
        uintEl(0x2AD7B1, 1000000),        // TimestampScale: 1 ms
        strEl(0x4D80, "smv.js"), strEl(0x5741, "smv.js")),
      el(0x1654AE6B,                      // Tracks
        el(0xAE,                          // TrackEntry
          uintEl(0xD7, 1), uintEl(0x73C5, 1), uintEl(0x83, 1), uintEl(0x9C, 0),
          strEl(0x86, "V_MS/VFW/FOURCC"),
          el(0x63A2, bitmapInfoHeader(width, height)),
          el(0xE0, uintEl(0xB0, width), uintEl(0xBA, height)))),
      ...clusters),
  ];

  const total = doc.reduce((a, n) => a + sizeOf(n), 0);
  const out = new Uint8Array(total);
  let pos = 0;
  for (const n of doc) pos = writeNode(n, out, pos);
  return out;
}
