// End-to-end test: drive the real page in headless Chrome like a user —
// choose .smv files, wait for every card to finish, press each "Save"
// button — and collect the downloaded MP4s for test/compare.py.
//
//   node test/e2e.mjs [file.smv ...]        (default: the Nana sample)
//   CHROME=/path/to/chrome  to use a different browser binary
//
// Writes test/out/<name>.mp4 and test/out/results.json.

import { existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";
import { startServer } from "../tools/serve.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..", "..");
const outDir = join(here, "out");

const CHROME_CANDIDATES = [
  process.env.CHROME,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].filter(Boolean);
const chrome = CHROME_CANDIDATES.find((p) => existsSync(p));
if (!chrome) throw new Error("no Chrome/Edge found — set CHROME=/path/to/browser");

const inputs = (process.argv.length > 2 ? process.argv.slice(2) : [join(repo, "Nana playing conputer.smv")])
  .map((p) => resolve(p));
for (const p of inputs) {
  if (!existsSync(p)) throw new Error(`not found: ${p} — pass .smv file paths as arguments`);
}

rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });

const server = await startServer();
const url = `http://127.0.0.1:${server.address().port}/`;
const browser = await puppeteer.launch({ executablePath: chrome, headless: true, protocolTimeout: 0 });
let failed = false;

try {
  console.log(`${await browser.version()} — ${url}`);
  const page = await browser.newPage();
  page.on("console", (m) => { if (m.type() === "error") console.log("[page error]", m.text()); });
  page.on("pageerror", (e) => console.log("[page exception]", e.message));

  const cdp = await page.createCDPSession();
  await cdp.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: outDir, eventsEnabled: true });
  const downloads = new Map();  // guid -> resolve
  cdp.on("Browser.downloadProgress", (e) => {
    if (e.state === "completed" || e.state === "canceled") downloads.get(e.guid)?.(e.state);
  });
  const nextDownload = () => new Promise((res) => {
    cdp.once("Browser.downloadWillBegin", (e) => downloads.set(e.guid, (state) => res({ state, name: e.suggestedFilename })));
  });

  await page.goto(url);
  await page.waitForSelector("#controls:not([hidden])");
  const t0 = Date.now();
  await (await page.$("#file-input")).uploadFile(...inputs);

  // Poll the cards, logging status changes, until all are done or failed.
  const seen = new Map();
  const timing = new Map();
  let finalCards;
  for (;;) {
    const cards = await page.$$eval(".card", (els) => els.map((el) => ({
      name: el.querySelector(".card-name").textContent,
      state: el.dataset.state,
      status: el.querySelector(".card-status").textContent,
      frames: el.dataset.frames, seconds: el.dataset.seconds, pauses: el.dataset.pauses,
    })));
    for (const [i, c] of cards.entries()) {
      const key = c.status.replace(/\d+%.*$/, "");
      if (seen.get(i) !== key) {
        seen.set(i, key);
        console.log(`  [${((Date.now() - t0) / 1000).toFixed(0)}s] ${c.name}: ${c.status}`);
      }
      if (c.state === "working" && !timing.has(`${i}s`)) timing.set(`${i}s`, Date.now());
      if ((c.state === "done" || c.state === "error") && !timing.has(`${i}e`)) timing.set(`${i}e`, Date.now());
    }
    if (cards.length === inputs.length && cards.every((c) => c.state === "done" || c.state === "error")) {
      finalCards = cards;
      break;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }

  // Press each Save button, like a user would.
  const results = [];
  const saveButtons = await page.$$(".card");
  for (const [i, c] of finalCards.entries()) {
    const r = { smv: inputs[i], name: c.name, state: c.state, status: c.status,
                frames: +c.frames || null, seconds: +c.seconds || null, pauses: c.pauses ? +c.pauses : null,
                convertSecs: (timing.get(`${i}e`) - (timing.get(`${i}s`) ?? t0)) / 1000 };
    if (c.state === "done") {
      const dl = nextDownload();
      await (await saveButtons[i].$(".save")).click();
      const { state, name } = await dl;
      r.download = state === "completed" ? join(outDir, name) : null;
    } else {
      r.error = await saveButtons[i].$eval(".card-error pre", (el) => el.textContent);
      failed = true;
    }
    results.push(r);
  }

  writeFileSync(join(outDir, "results.json"), JSON.stringify(results, null, 2));
  console.log("\nresults:");
  for (const r of results) {
    console.log(`  ${r.state === "done" ? "OK  " : "FAIL"} ${r.name}: ${r.state}` +
      (r.state === "done" ? `, ${r.frames} frames, ${r.seconds}s, ${r.pauses} pauses, ` +
        `converted in ${r.convertSecs.toFixed(0)}s -> ${r.download ? basename(r.download) : "DOWNLOAD FAILED"}` : `\n       ${r.error.split("\n")[0]}`));
    if (r.state === "done" && !r.download) failed = true;
  }
  console.log(`total ${((Date.now() - t0) / 1000).toFixed(0)}s; downloads in ${outDir}: ${readdirSync(outDir).filter((f) => f.endsWith(".mp4")).length}`);
} finally {
  await browser.close();
  server.close();
}
process.exit(failed ? 1 : 0);
