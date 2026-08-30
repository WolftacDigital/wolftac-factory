#!/usr/bin/env node
// brand-assert.mjs — machine-checked brand conformance for a rendered artifact.
//
// Walks the RENDERED DOM (not the source text) and asserts, against a brand profile:
//   1. every computed color / background-color / border-color is in the profile
//      palette or the documented neutral set;
//   2. every computed font-family resolves to a profile family, AND each profile
//      webfont is verified actually loaded via document.fonts.check() -- a computed
//      fontFamily returns the authored list even when the font file 404s, so the
//      computed value alone proves nothing about what rendered;
//   3. shape rules hold (Wolftac: computed border-radius is 0px everywhere);
//   4. REVEALED END-STATE: any element behind a reveal gate is visible in its
//      revealed state -- non-zero opacity and non-zero box. WD-217 shipped invisible
//      content to production because a gate's specificity beat the reveal class and
//      the spot check only proved the element existed in the DOM.
//   5. no signature token of a DIFFERENT profile appears (signature tokens only --
//      shared generic neutrals must never false-positive).
// A console error during load fails the gate.
//
// Usage: node brand-assert.mjs <artifact.html> --profile <id> [--brands <dir>]
// Exit:  0 conformant, non-zero otherwise with the offending selector and value.

import { readFile, readdir, mkdtemp, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { findChrome, launchArgs } from "./render-proof.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_BRANDS = resolve(HERE, "..", "brands");

// Colors that legitimately recur across every brand. Never a contamination signal.
const NEUTRAL_OK = new Set([
  "rgba(0, 0, 0, 0)",
  "transparent",
  "rgb(0, 0, 0)",
  "rgb(255, 255, 255)",
  "currentcolor",
]);

const GENERIC_FAMILIES = new Set([
  "system-ui", "ui-monospace", "sans-serif", "serif", "monospace",
  "-apple-system", "blinkmacsystemfont", "segoe ui", "roboto", "helvetica",
  "arial", "ui-sans-serif", "menlo", "consolas", "courier new", "emoji",
  "apple color emoji", "segoe ui emoji", "segoe ui symbol", "noto color emoji",
]);

function hexToRgb(hex) {
  const h = hex.replace("#", "").trim();
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full.slice(0, 6), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function parseArgs(argv) {
  const args = { artifact: "", profile: "", brands: DEFAULT_BRANDS };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--profile") args.profile = argv[++i] || "";
    else if (argv[i] === "--brands") args.brands = argv[++i] || "";
    else if (!args.artifact) args.artifact = argv[i];
    else throw new Error(`Unknown argument: ${argv[i]}`);
  }
  if (!args.artifact || !args.profile) {
    throw new Error("Usage: node brand-assert.mjs <artifact.html> --profile <id> [--brands <dir>]");
  }
  return args;
}

async function loadProfiles(brandsDir) {
  const files = (await readdir(brandsDir)).filter((f) => f.endsWith(".json"));
  const out = [];
  for (const f of files) {
    const profile = JSON.parse(await readFile(join(brandsDir, f), "utf8"));
    out.push(profile);
  }
  return out;
}

export async function assertBrand({ artifact, profileId, brandsDir }) {
  const artifactPath = resolve(artifact);
  if (!existsSync(artifactPath)) throw new Error(`MISSING-ARTIFACT: ${artifactPath}`);

  const profiles = await loadProfiles(brandsDir);
  const profile = profiles.find((p) => p.id === profileId);
  if (!profile) throw new Error(`UNKNOWN-PROFILE: ${profileId}`);

  // Cross-profile contamination uses signature tokens ONLY, never full palettes.
  const foreignSignatures = profiles
    .filter((p) => p.id !== profileId && p.id !== "_template")
    .flatMap((p) => (p.signature_tokens || []).map((t) => ({ brand: p.id, token: t })));

  const allowedRgb = new Set(
    (profile.palette || []).map((hex) => {
      const [r, g, b] = hexToRgb(hex);
      return `rgb(${r}, ${g}, ${b})`;
    }),
  );

  const puppeteer = (await import("puppeteer-core")).default;
  const userDataDir = await mkdtemp(join(tmpdir(), "wolftac-design-assert-"));
  const violations = [];
  const consoleErrors = [];

  const browser = await puppeteer.launch({
    executablePath: findChrome(),
    headless: "new",
    args: launchArgs(userDataDir),
  });

  try {
    const page = await browser.newPage();
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text());
    });
    page.on("pageerror", (e) => consoleErrors.push(e.message));

    await page.setViewport({ width: 1440, height: 900 });
    await page.goto(pathToFileURL(artifactPath).href, {
      waitUntil: ["load", "networkidle0"],
      timeout: 30000,
    });
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => new Promise((r) => setTimeout(r, 400)));

    const report = await page.evaluate(
      (allowedRgbList, neutralList, genericList, shapeRules, fontFamilies) => {
        const allowed = new Set(allowedRgbList);
        const neutral = new Set(neutralList);
        const generic = new Set(genericList);
        const families = fontFamilies.map((f) => f.toLowerCase());

        function selectorFor(el) {
          if (el.id) return `#${el.id}`;
          const cls = (el.className && typeof el.className === "string")
            ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".")
            : "";
          return `${el.tagName.toLowerCase()}${cls}`;
        }

        const colorHits = [];
        const fontHits = [];
        const shapeHits = [];
        const hiddenHits = [];
        const seenFamilies = new Set();

        // Only rendered content is evidence. head-level nodes (meta, title, link,
        // style) and script/template inherit the UA default font and have no box,
        // so including them produced nine "Times New Roman" violations for a page
        // that renders entirely in its profile families.
        const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "LINK", "META", "TITLE", "HEAD", "TEMPLATE", "NOSCRIPT", "BR"]);
        const all = [document.body, ...document.body.querySelectorAll("*")].filter(
          (el) => el && !SKIP_TAGS.has(el.tagName),
        );
        for (const el of all) {
          const cs = getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          if (cs.display === "none") continue;

          for (const prop of ["color", "backgroundColor", "borderTopColor", "borderBottomColor", "borderLeftColor", "borderRightColor"]) {
            const v = (cs[prop] || "").trim();
            if (!v) continue;
            if (neutral.has(v.toLowerCase())) continue;
            if (v.startsWith("rgba(")) continue; // alpha compositing of allowed hues
            if (!allowed.has(v)) {
              colorHits.push({ selector: selectorFor(el), property: prop, value: v });
            }
          }

          const fam = (cs.fontFamily || "").split(",")[0].replace(/["']/g, "").trim();
          if (fam) {
            seenFamilies.add(fam);
            const famLower = fam.toLowerCase();
            if (!generic.has(famLower) && !families.includes(famLower)) {
              fontHits.push({ selector: selectorFor(el), property: "font-family", value: fam });
            }
          }

          if (shapeRules && shapeRules.border_radius_px === 0) {
            for (const prop of ["borderTopLeftRadius", "borderTopRightRadius", "borderBottomLeftRadius", "borderBottomRightRadius"]) {
              const v = cs[prop];
              if (v && v !== "0px" && !v.startsWith("0")) {
                shapeHits.push({ selector: selectorFor(el), property: prop, value: v });
              }
            }
          }

          // Revealed end-state: an element that declares a reveal gate must have
          // actually revealed. Existing in the DOM is not the same as being visible.
          const gated = el.matches("[data-reveal], .reveal, .fade-in, [data-animate]");
          if (gated) {
            const opacity = parseFloat(cs.opacity || "1");
            if (opacity === 0 || rect.width === 0 || rect.height === 0 || cs.visibility === "hidden") {
              hiddenHits.push({
                selector: selectorFor(el),
                opacity: cs.opacity,
                width: rect.width,
                height: rect.height,
                visibility: cs.visibility,
              });
            }
          }
        }

        // document.fonts.check is the only honest proof a webfont actually loaded.
        const fontLoaded = {};
        for (const f of fontFamilies) {
          if (generic.has(f.toLowerCase())) continue;
          fontLoaded[f] = document.fonts.check(`16px "${f}"`);
        }

        return {
          colorHits, fontHits, shapeHits, hiddenHits, fontLoaded,
          seenFamilies: Array.from(seenFamilies),
          html: document.documentElement.outerHTML,
        };
      },
      Array.from(allowedRgb),
      Array.from(NEUTRAL_OK),
      Array.from(GENERIC_FAMILIES),
      profile.shape_rules || {},
      profile.font_families || [],
    );

    for (const h of report.colorHits) {
      violations.push(`OFF-PALETTE  ${h.selector} { ${h.property}: ${h.value} }`);
    }
    for (const h of report.fontHits) {
      violations.push(`OFF-PROFILE-FONT  ${h.selector} { font-family: ${h.value} }`);
    }
    for (const h of report.shapeHits) {
      violations.push(`SHAPE-VIOLATION  ${h.selector} { ${h.property}: ${h.value} } (profile requires 0)`);
    }
    for (const h of report.hiddenHits) {
      violations.push(
        `HIDDEN-REVEALED-CONTENT  ${h.selector} is behind a reveal gate but never revealed ` +
          `(opacity=${h.opacity}, ${h.width}x${h.height}, visibility=${h.visibility})`,
      );
    }
    for (const [family, loaded] of Object.entries(report.fontLoaded)) {
      if (!loaded) violations.push(`FONT-NOT-LOADED  "${family}" is declared by the profile but document.fonts.check() is false`);
    }

    // Cross-profile contamination -- signature tokens only.
    const haystack = report.html;
    for (const { brand, token } of foreignSignatures) {
      const re = new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
      if (re.test(haystack)) {
        violations.push(`FOREIGN-BRAND-TOKEN  "${token}" (signature of profile "${brand}") appears in a "${profileId}" artifact`);
      }
    }

    for (const e of consoleErrors) violations.push(`CONSOLE-ERROR  ${e}`);
  } finally {
    await browser.close();
    await rm(userDataDir, { recursive: true, force: true }).catch(() => {});
  }

  return { profile: profileId, violations };
}

const invokedDirectly =
  process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  try {
    const args = parseArgs(process.argv.slice(2));
    const result = await assertBrand({
      artifact: args.artifact,
      profileId: args.profile,
      brandsDir: args.brands,
    });
    if (result.violations.length) {
      console.error(`BRAND-ASSERT FAILED (${result.violations.length}) for profile "${result.profile}":`);
      for (const v of result.violations) console.error(`  ${v}`);
      process.exitCode = 1;
    } else {
      console.log(`BRAND-ASSERT PASSED for profile "${result.profile}".`);
    }
  } catch (error) {
    console.error(`BRAND-ASSERT ERROR: ${error.message || error}`);
    process.exitCode = 1;
  }
}
