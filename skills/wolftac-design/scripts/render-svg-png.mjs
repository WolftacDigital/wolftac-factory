#!/usr/bin/env node
// render-svg-png.mjs — SVG to transparent PNG via sharp.
//
// This host has no ImageMagick (`convert` is Windows' disk formatter), no
// rsvg-convert, no Inkscape, and a broken `uv`, so sharp is the only working path.
// A clean render return is NEVER treated as success -- run verify-png.mjs after
// this and read the pixel scan. That pairing is the point.
//
// Usage: node render-svg-png.mjs <input.svg> <output.png> [--size <px>] [--width <px>] [--height <px>]

import { readFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

function parseArgs(argv) {
  const args = { input: "", output: "", size: 0, width: 0, height: 0 };
  for (let i = 0; i < argv.length; i += 1) {
    const v = argv[i];
    if (v === "--size") args.size = Number(argv[++i]);
    else if (v === "--width") args.width = Number(argv[++i]);
    else if (v === "--height") args.height = Number(argv[++i]);
    else if (!args.input) args.input = v;
    else if (!args.output) args.output = v;
    else throw new Error(`Unknown argument: ${v}`);
  }
  if (!args.input || !args.output) {
    throw new Error("Usage: node render-svg-png.mjs <input.svg> <output.png> [--size <px>]");
  }
  return args;
}

export async function renderSvgToPng({ input, output, size = 0, width = 0, height = 0 }) {
  const sharp = (await import("sharp")).default;
  const src = resolve(input);
  if (!existsSync(src)) throw new Error(`MISSING-SVG: ${src}`);

  const svg = await readFile(src);
  const out = resolve(output);
  await mkdir(dirname(out), { recursive: true });

  const w = width || size || 0;
  const h = height || size || 0;

  let pipeline = sharp(svg, { density: 384 });
  if (w || h) {
    pipeline = pipeline.resize({
      width: w || null,
      height: h || null,
      fit: "contain",
      // Transparent, not white. A white matte silently destroys the alpha the
      // pixel scan is about to check.
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    });
  }

  const info = await pipeline.png({ compressionLevel: 9 }).toFile(out);
  return { output: out, width: info.width, height: info.height, format: info.format };
}

const invokedDirectly =
  process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  try {
    const args = parseArgs(process.argv.slice(2));
    const r = await renderSvgToPng(args);
    console.log(JSON.stringify({ status: "rendered", ...r }, null, 2));
    console.log("NOTE: a clean render is not proof. Run verify-png.mjs on this file.");
  } catch (error) {
    console.error(JSON.stringify({ status: "failed", error: String(error.message || error) }));
    process.exitCode = 1;
  }
}
