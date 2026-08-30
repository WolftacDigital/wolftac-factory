#!/usr/bin/env node
// make-artifact.mjs — fill a template with a resolved brand profile.
//
// Templates are brand-agnostic: they carry structure and layout, and reach every
// colour, family and shape through a CSS custom property. This script resolves the
// profile (see resolve-tokens.mjs, including the loud degrade), injects the :root
// block at the /* @BRAND-TOKENS */ marker, stamps the provenance header into the
// document, and writes the artifact. It exists so the AC-3/4/5/10 matrix is one
// reproducible command rather than a hand-assembly a reviewer cannot repeat.
//
// Usage: node make-artifact.mjs --template <name> --profile <id> --out <file.html>

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { loadProfile, resolveTokens, provenanceHeader } from "./resolve-tokens.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const TEMPLATES = resolve(HERE, "..", "templates");
const BRANDS = resolve(HERE, "..", "brands");

// Pull a value out of whichever token shape answered (canonical or snapshot).
function pick(tokens, paths, fallback) {
  for (const path of paths) {
    let cur = tokens;
    let ok = true;
    for (const key of path.split(".")) {
      if (cur && typeof cur === "object" && key in cur) cur = cur[key];
      else { ok = false; break; }
    }
    if (ok && typeof cur === "string" && cur.trim()) return cur;
  }
  return fallback;
}

function firstFamily(profile, index, fallback) {
  const fam = (profile.font_families || [])[index];
  return fam || fallback;
}

export function cssVariables(profile, tokens) {
  const bgPrimary = pick(tokens, ["color.backgrounds.primary", "color.ink.primary"], "#FFFFFF");
  const bgSecondary = pick(tokens, ["color.backgrounds.secondary", "color.ink.secondary"], "#F5F5F5");
  const bgTertiary = pick(tokens, ["color.backgrounds.tertiary", "color.ink.tertiary"], "#EBEBEB");
  const textPrimary = pick(tokens, ["color.text.primary", "color.bone.primary"], "#1A1A1A");
  const textSecondary = pick(tokens, ["color.text.secondary", "color.ash.primary"], "#595959");
  const accent = pick(tokens, ["color.text.hud_active", "color.red.500", "color.text.accent"], "#2F5B8C");
  const accent2 = pick(tokens, ["color.text.hud_cyan", "color.accent.amber", "color.text.accent"], accent);
  const line = pick(
    tokens,
    ["color.borders.structural", "color.lines.default", "color.borders.default"],
    "rgba(0,0,0,0.12)",
  );

  const radius = profile.shape_rules?.border_radius_px;
  const radiusCss = radius === null || radius === undefined ? "4px" : `${radius}px`;
  const cut = pick(tokens, ["layout.cut"], "");

  // The brand accent and the accent that may carry small text are not always the
  // same value. OMAS red-500 is the brand accent but only 3.78:1 on ink, so the
  // profile names an AA-safe member of the same scale for text and CTAs. Falls back
  // to the brand accent when a profile declares none.
  const ui = profile.ui || {};
  const accentText = ui.accent_text || accent;
  const ctaBackground = ui.cta_background || accent;
  const ctaText = ui.cta_text || bgPrimary;

  const lines = [
    `--bg: ${bgPrimary};`,
    `--bg-2: ${bgSecondary};`,
    `--bg-3: ${bgTertiary};`,
    `--text: ${textPrimary};`,
    `--text-2: ${textSecondary};`,
    `--accent: ${accent};`,
    `--accent-2: ${accent2};`,
    `--accent-text: ${accentText};`,
    `--cta-bg: ${ctaBackground};`,
    `--cta-text: ${ctaText};`,
    `--line: ${line};`,
    `--radius: ${radiusCss};`,
    `--font-display: "${firstFamily(profile, 0, "system-ui")}", system-ui, sans-serif;`,
    `--font-body: "${firstFamily(profile, 1, "system-ui")}", system-ui, sans-serif;`,
    `--font-mono: "${firstFamily(profile, 2, "ui-monospace")}", ui-monospace, monospace;`,
    `--max-w: ${pick(tokens, ["layout.content_max_width", "layout.container"], "960px")};`,
  ];
  if (cut) {
    lines.push(`--cut: ${cut};`);
    lines.push(
      "--clip-cut: polygon(var(--cut) 0, 100% 0, 100% calc(100% - var(--cut)), calc(100% - var(--cut)) 100%, 0 100%, 0 var(--cut));",
    );
  } else {
    lines.push("--cut: 0px;");
    lines.push("--clip-cut: none;");
  }
  return lines.map((l) => "      " + l).join("\n");
}

function fontLink(tokens) {
  const url = pick(tokens, ["typography.font_imports"], "");
  if (!url) return "";
  const href = url.startsWith("@import") ? (url.match(/url\(['"]?([^'")]+)/) || [])[1] : url;
  if (!href) return "";
  return `  <link rel="preconnect" href="https://fonts.googleapis.com" />\n  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />\n  <link rel="stylesheet" href="${href}" />`;
}

export async function makeArtifact({ template, profileId, out, brandsDir = BRANDS }) {
  const templatePath = join(TEMPLATES, `${template}.html`);
  if (!existsSync(templatePath)) throw new Error(`UNKNOWN-TEMPLATE: ${template}`);

  const profile = await loadProfile(profileId, brandsDir);
  const resolved = await resolveTokens(profile);
  const header = provenanceHeader({ profile, resolved, artifactType: template, mode: "generate" });

  let html = await readFile(templatePath, "utf8");
  html = html.replace("/* @BRAND-TOKENS */", cssVariables(profile, resolved.tokens));
  html = html.replace("<!-- @FONT-LINKS -->", fontLink(resolved.tokens));
  html = html.replace("<!-- @PROVENANCE -->", `<!--\n${header}\n-->`);
  html = html.replace(/@BRAND-NAME@/g, profile.name);
  html = html.replace(/@BRAND-VOICE@/g, profile.voice || "");

  const outPath = resolve(out);
  await mkdir(dirname(outPath), { recursive: true });
  await writeFile(outPath, html, "utf8");

  return { outPath, header, degraded: resolved.degraded, profile: profile.id };
}

const invokedDirectly =
  process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  const argv = process.argv.slice(2);
  const args = { template: "", profile: "", out: "", brands: BRANDS };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--template") args.template = argv[++i] || "";
    else if (argv[i] === "--profile") args.profile = argv[++i] || "";
    else if (argv[i] === "--out") args.out = argv[++i] || "";
    else if (argv[i] === "--brands") args.brands = argv[++i] || "";
  }
  try {
    if (!args.template || !args.profile || !args.out) {
      throw new Error("Usage: node make-artifact.mjs --template <name> --profile <id> --out <file.html>");
    }
    const r = await makeArtifact({
      template: args.template,
      profileId: args.profile,
      out: args.out,
      brandsDir: args.brands,
    });
    // The provenance header goes to stdout too -- AC-2 and AC-6 read it from here.
    console.log(r.header);
    console.log(`WROTE: ${r.outPath}`);
  } catch (error) {
    console.error(`MAKE-ARTIFACT ERROR: ${error.message || error}`);
    process.exitCode = 1;
  }
}
