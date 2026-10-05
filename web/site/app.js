// app.js — the page: choosing/dropping files, a queue, progress, results.
// Copyright (C) 2026 Michael Leonard — SPDX-License-Identifier: GPL-3.0-or-later

import { Converter, FileReadError, SmvFormatError, mp4Name } from "./converter.js";

const $ = (sel, root = document) => root.querySelector(sel);

const supported = typeof WebAssembly === "object" && typeof Worker === "function" &&
  typeof ReadableStream === "function" && typeof Blob.prototype.arrayBuffer === "function";
if (!supported) {
  $("#old-browser").hidden = false;
  throw new Error("browser lacks WebAssembly / Worker / streams support");
}
$("#controls").hidden = false;

const converter = new Converter();
const input = $("#file-input");
const queueEl = $("#queue");

// ------------------------------------------------------------ the converter

const engineEl = $("#engine");
let enginePromise = null;

// The browser downloaded the converter but can't run it (its WebAssembly
// lacks something the converter needs).
const cantRun = (e) => /CompileError|LinkError/.test(String(e && e.message || e));
const CANT_RUN = "Sorry — this web browser can't run the converter. Please try an up-to-date " +
  "Chrome, Edge or Firefox. If that doesn't help, try another computer.";

// Start downloading the converter as soon as the page opens, so it's
// usually ready by the time someone has found their file.
function ensureEngine() {
  if (converter.loaded) return Promise.resolve();
  if (!enginePromise) {
    engineEl.textContent = "Getting the converter ready…";
    enginePromise = converter
      .load((f) => { engineEl.textContent = `Getting the converter ready… ${Math.round(f * 100)}%`; })
      .then(() => { engineEl.textContent = "The converter is ready."; })
      .catch((e) => {
        engineEl.textContent = cantRun(e) ? CANT_RUN :
          "Couldn't get the converter ready. Please check your internet connection, then " +
          "reload this page.";
        throw e;
      })
      .finally(() => { enginePromise = null; });
  }
  return enginePromise;
}
ensureEngine().catch((e) => console.error(e));

// --------------------------------------------------------------- the queue

const jobs = [];
let running = false;

function addFiles(files) {
  const added = [...files].map((file) => ({ file, card: makeCard(file.name) }));
  if (!added.length) return;
  jobs.push(...added);
  added[0].card.el.scrollIntoView({ behavior: "smooth", block: "center" });
  if (!running) runQueue();
}

async function runQueue() {
  running = true;
  setBusy(true);
  while (jobs.length) {
    const { file, card } = jobs.shift();
    await convertOne(file, card);
  }
  running = false;
  setBusy(false);
}

async function convertOne(file, card) {
  card.state("working");
  try {
    card.status("Getting ready…");
    await ensureEngine();
    const result = await converter.convert(file, (stage, d) => {
      if (stage === "reading") card.status("Reading your video…");
      else if (stage === "sound") card.status("Converting the sound…");
      else if (stage === "picture") {
        card.progress(d.fraction);
        card.status(`Converting the picture… ${Math.floor(d.fraction * 100)}%${timeLeft(d.secondsLeft)}`);
      }
    });
    card.done(result, mp4Name(file.name));
  } catch (e) {
    console.error(e);
    card.fail(e);
  }
}

function timeLeft(s) {
  if (s === null || s === undefined || s <= 0) return "";
  if (s < 50) return " — less than a minute left";
  const m = Math.round(s / 60);
  return m <= 1 ? " — about 1 minute left" : ` — about ${m} minutes left`;
}

// Whole seconds, rounded down like video players show it.
function describeLength(seconds) {
  const total = Math.max(1, Math.floor(seconds));
  const m = Math.floor(total / 60), s = total % 60;
  const parts = [];
  if (m) parts.push(`${m} minute${m === 1 ? "" : "s"}`);
  if (s || !m) parts.push(`${s} second${s === 1 ? "" : "s"}`);
  return parts.join(" ");
}

// ---------------------------------------------------------------- the cards

function makeCard(name) {
  const el = $("#card-template").content.firstElementChild.cloneNode(true);
  $(".card-name", el).textContent = name;
  $(".card-status", el).textContent = "Waiting its turn…";
  queueEl.append(el);
  const bar = $("progress", el);

  return {
    el,
    state(s) { el.dataset.state = s; },
    status(text) { $(".card-status", el).textContent = text; },
    progress(f) { bar.hidden = false; bar.value = f; },
    done(result, outName) {
      const url = URL.createObjectURL(result.blob);
      bar.hidden = true;
      el.dataset.state = "done";
      el.dataset.frames = result.frames;
      el.dataset.seconds = result.seconds.toFixed(3);
      el.dataset.pauses = result.pauses;
      this.status("Finished! Press play to watch it, then save it to your computer.");
      $("video", el).src = url;
      $(".card-facts", el).textContent =
        `${describeLength(result.seconds)} long, ${result.hasAudio ? "with sound" : "no sound in this recording"}.`;
      const save = $(".save", el);
      save.href = url;
      save.download = outName;
      $(".save-hint", el).textContent = `It will be saved in your Downloads folder as “${outName}”.`;
      $(".card-result", el).hidden = false;
    },
    fail(e) {
      bar.hidden = true;
      el.dataset.state = "error";
      if (e instanceof SmvFormatError) {
        this.status("Sorry — this file can't be converted. It doesn't look like a VideoLink Mail " +
          "video. (Some MP3 and MP4 players also make files ending in .smv — this page can't " +
          "convert those.)");
      } else if (e instanceof FileReadError) {
        this.status("Sorry — this file couldn't be opened. If you chose a folder, open the folder " +
          "and choose the videos inside it instead.");
      } else if (cantRun(e)) {
        this.status(CANT_RUN);
      } else {
        this.status("Sorry — something went wrong while converting this video. Please reload the " +
          "page and try again. If it keeps happening, the computer may not have enough free " +
          "memory; closing other programs can help.");
      }
      const details = $(".card-error", el);
      $("pre", details).textContent = String(e && (e.stack || e.message) || e) +
        (converter.simd === null ? "" : `\n\n[core: ${converter.simd ? "standard" : "no SIMD"}]`);
      details.hidden = false;
    },
  };
}

// ----------------------------------------------------- choosing / dropping

$("#choose").addEventListener("click", () => input.click());
input.addEventListener("change", () => {
  addFiles(input.files);
  input.value = "";  // so choosing the same file again still works
});

const overlay = $("#drop-overlay");
const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
let dragDepth = 0;

window.addEventListener("dragenter", (e) => {
  if (!hasFiles(e)) return;
  dragDepth++;
  overlay.hidden = false;
});
window.addEventListener("dragleave", (e) => {
  if (!hasFiles(e)) return;
  if (--dragDepth <= 0) {
    dragDepth = 0;
    overlay.hidden = true;
  }
});
// Without these, a file dropped anywhere on the page would make the
// browser leave the page to open it.
window.addEventListener("dragover", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
});
window.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  overlay.hidden = true;
  addFiles(e.dataTransfer.files);
});

// ------------------------------------------ while converting: keep it alive

let wakeLock = null;

async function holdWakeLock() {
  try {
    wakeLock = await navigator.wakeLock?.request("screen");
  } catch {
    wakeLock = null;  // not allowed right now (e.g. tab hidden) — not essential
  }
}

function warnBeforeLeaving(e) {
  e.preventDefault();
  e.returnValue = "";
}

function setBusy(busy) {
  $("#keep-open").hidden = !busy;
  if (busy) {
    window.addEventListener("beforeunload", warnBeforeLeaving);
    holdWakeLock();
  } else {
    window.removeEventListener("beforeunload", warnBeforeLeaving);
    wakeLock?.release().catch(() => {});
    wakeLock = null;
  }
}

// The screen wake lock is dropped when the tab is hidden; take it back.
document.addEventListener("visibilitychange", () => {
  if (running && document.visibilityState === "visible") holdWakeLock();
});
