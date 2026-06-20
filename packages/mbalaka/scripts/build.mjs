import { copyFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const packageRoot = resolve(root, "..");
const dist = resolve(packageRoot, "dist");

await mkdir(dist, { recursive: true });
await copyFile(resolve(packageRoot, "src", "index.js"), resolve(dist, "index.js"));
await copyFile(resolve(packageRoot, "src", "styles.css"), resolve(dist, "styles.css"));
