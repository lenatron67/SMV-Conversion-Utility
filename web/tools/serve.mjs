// Minimal static file server for local testing of site/.
// `npm run serve` -> http://127.0.0.1:8080/   (PORT env var to change)

import { createReadStream, statSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".txt": "text/plain; charset=utf-8",
};

export const SITE = join(dirname(fileURLToPath(import.meta.url)), "..", "site");

export function startServer(root = SITE, port = 0) {
  const server = createServer((req, res) => {
    let path = decodeURIComponent(new URL(req.url, "http://x").pathname);
    if (path.endsWith("/")) path += "index.html";
    const file = normalize(join(root, path));
    if (!file.startsWith(normalize(root) + sep)) {
      res.writeHead(403).end();
      return;
    }
    let st;
    try {
      st = statSync(file);
    } catch {
      res.writeHead(404).end("not found");
      return;
    }
    if (!st.isFile()) {
      res.writeHead(404).end("not found");
      return;
    }
    res.writeHead(200, {
      "Content-Type": TYPES[extname(file)] || "application/octet-stream",
      "Content-Length": st.size,
      "Cache-Control": "no-cache",
    });
    createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(server)));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === normalize(process.argv[1])) {
  const server = await startServer(SITE, Number(process.env.PORT) || 8080);
  console.log(`serving ${SITE} at http://127.0.0.1:${server.address().port}/`);
}
