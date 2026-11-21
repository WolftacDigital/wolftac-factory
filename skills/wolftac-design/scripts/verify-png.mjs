#!/usr/bin/env node
// verify-png.mjs — raw-RGBA pixel scan of an exported PNG.
//
// This is the proof half of the asset pipeline. A successful sharp render says
// nothing about whether the mark is the right colour, actually transparent, or
// centred -- an all-white or fully-empty PNG renders "successfully" too. So this
// reads raw pixels and reports dimensions, target-colour count, transparent count,
// and the bounding box of visible pixels with its offset from centre.
//
// Usage: node verify-png.mjs <file.png> [--expect-color "#RRGGBB"] [--size <px>] [--tolerance <px>]
// Exit:  non-zero if the image is empty, opaque-everywhere, wrong-sized, or the
//        content bounding box is off-centre beyond tolerance.

import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

function parseArgs(argv) {
  const args = { file: "", expectColor: "", size: 0, tolerance: 8 };
  for (let i = 0; i < argv.length; i += 1) {
    const v = argv[i];
    if (v === "--expect-color") args.expectColor = argv[++i] || "";
    else if (v === "--size") args.size = Number(argv[++i]);
    else if (v === "--tolerance") args.tolerance = Number(argv[++i]);
    else if (!args.file) args.file = v;
    else throw new Error(`Unknown argument: ${v}`);
  }
  if (!args.file) throw new Error("Usage: node verify-png.mjs <file.png> [--expect-color '#RRGGBB'] [--size <px>]");
  return args;
}

function hexToRgb(hex) {
  const h = hex.replace("#", "").trim();
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full.slice(0, 6), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export async function verifyPng({ file, expectColor = "", size = 0, tolerance = 8 }) {
  const sharp = (await import("sharp")).default;
  const path = resolve(file);
  if (!existsSync(path)) throw new Error(`MISSING-PNG: ${path}`);

  const image = sharp(path).ensureAlpha();
  const { data, info } = await image.raw().toBuffer({ resolveWithObject: true });
  const { width, height, channels } = info;

  const target = expectColor ? hexToRgb(expectColor) : null;
  let transparent = 0;
  let opaque = 0;
  let targetCount = 0;
  let minX = width, minY = height, maxX = -1, maxY = -1;

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * channels;
      const a = data[i + 3];
      if (a < 16) { transparent += 1; continue; }
      opaque += 1;
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
      if (target) {
        const dr = Math.abs(data[i] - target[0]);
        const dg = Math.abs(data[i + 1] - target[1]);
        const db = Math.abs(data[i + 2] - target[2]);
        if (dr <= 12 && dg <= 12 && db <= 12) targetCount += 1;
      }
    }
  }

  const problems = [];
  if (opaque === 0) problems.push("EMPTY: no visible pixel -- the export produced nothing.");
  if (transparent === 0) problems.push("NO-TRANSPARENCY: every pixel is opaque; the background was matted in.");
  if (size && (width !== size || height !== size)) {
    problems.push(`WRONG-SIZE: expected ${size}x${size}, got ${width}x${height}`);
  }
  if (target && targetCount === 0 && opaque > 0) {
    problems.push(`WRONG-COLOR: no pixel within tolerance of ${expectColor}`);
  }

  let box = null;
  if (maxX >= 0) {
    const boxW = maxX - minX + 1;
    const boxH = maxY - minY + 1;
    const boxCenterX = minX + boxW / 2;
    const boxCenterY = minY + boxH / 2;
    const offsetX = Math.abs(boxCenterX - width / 2);
    const offsetY = Math.abs(boxCenterY - height / 2);
    box = { minX, minY, maxX, maxY, width: boxW, height: boxH, offsetX, offsetY };
    if (offsetX > tolerance || offsetY > tolerance) {
      problems.push(
        `OFF-CENTER: content centre is ${offsetX.toFixed(1)}px/${offsetY.toFixed(1)}px from image centre (tolerance ${tolerance}px)`,
      );
    }
  }

  return {
    file: path,
    dimensions: { width, height },
    pixels: { total: width * height, opaque, transparent, targetColorCount: targetCount },
    boundingBox: box,
    problems,
  };
}

const invokedDirectly =
  process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  try {
    const args = parseArgs(process.argv.slice(2));
    const r = await verifyPng(args);
    console.log(JSON.stringify(r, null, 2));
    if (r.problems.length) {
      console.error(`VERIFY-PNG FAILED (${r.problems.length}):`);
      for (const p of r.problems) console.error(`  ${p}`);
      process.exitCode = 1;
    } else {
      console.log("VERIFY-PNG PASSED.");
    }
  } catch (error) {
    console.error(`VERIFY-PNG ERROR: ${error.message || error}`);
    process.exitCode = 1;
  }
}
