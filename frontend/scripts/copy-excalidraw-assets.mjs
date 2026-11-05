// Self-host Excalidraw fonts so the whiteboard works offline (no CDN fetches).
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "node_modules/@excalidraw/excalidraw/dist/prod/fonts");
const dest = join(root, "public/excalidraw-assets/fonts");
if (existsSync(src)) {
  mkdirSync(dest, { recursive: true });
  cpSync(src, dest, { recursive: true });
  console.log("copied excalidraw fonts →", dest);
} else {
  console.warn("excalidraw fonts not found; whiteboard will fall back to CDN fonts");
}
