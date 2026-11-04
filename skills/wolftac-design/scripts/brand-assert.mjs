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
        const contrastHits = [];
        const seenFamilies = new Set();

        // --- computed WCAG AA contrast ------------------------------------------
        // Measured on the RENDERED page, not asserted in prose. A palette can be
        // internally consistent and still fail AA -- #C43B3B on #0B0B0D is 3.78:1.
        function parseRgb(v) {
          const m = (v || "").match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)\s*(?:[,/]\s*([\d.%]+)\s*)?\)$/i);
          if (!m) return null;
          const a = m[4] === undefined ? 1 : (m[4].endsWith("%") ? parseFloat(m[4]) / 100 : parseFloat(m[4]));
          return { r: +m[1], g: +m[2], b: +m[3], a };
        }
        function over(fg, bg) {
          // Composite a translucent colour onto its backdrop before measuring.
          const a = fg.a;
          return { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a), a: 1 };
        }
        function lum(c) {
          const f = (x) => {
            const s = x / 255;
            return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
          };
          return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
        }
        function ratio(a, b) {
          const l1 = lum(a);
          const l2 = lum(b);
          return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
        }
        function effectiveBackground(el) {
          // Walk up until an opaque background is found; fall back to white.
          let node = el;
          let acc = null;
          while (node && node !== document.documentElement.parentNode) {
            const bg = parseRgb(getComputedStyle(node).backgroundColor);
            if (bg && bg.a > 0) {
              acc = acc ? over(acc, bg) : bg;
              if (acc.a >= 1 || bg.a >= 1) return acc.a >= 1 ? acc : over(acc, { r: 255, g: 255, b: 255, a: 1 });
            }
            node = node.parentElement;
          }
          return acc ? over(acc, { r: 255, g: 255, b: 255, a: 1 }) : { r: 255, g: 255, b: 255, a: 1 };
        }
        function hasOwnText(el) {
          for (const n of el.childNodes) {
            if (n.nodeType === 3 && n.textContent.trim().length) return true;
          }
          return false;
        }

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
            // Alpha-aware. The first version skipped EVERY rgba(...) on the theory
            // that alpha was just compositing of an allowed hue -- which let any
            // off-palette colour through simply by carrying an alpha channel.
            // Check the hue; alpha only decides whether a fully transparent value
            // is exempt.
            const m = v.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)\s*(?:[,/]\s*([\d.%]+)\s*)?\)$/i);
            if (m) {
              let a = m[4] === undefined ? 1 : (m[4].endsWith("%") ? parseFloat(m[4]) / 100 : parseFloat(m[4]));
              if (a === 0) continue; // fully transparent paints nothing
              const hue = `rgb(${Math.round(+m[1])}, ${Math.round(+m[2])}, ${Math.round(+m[3])})`;
              // A translucent white or black hairline is a documented neutral, not a
              // brand colour -- Wolftac's structural border is rgba(255,255,255,0.05).
              // Any OTHER hue must still be in the palette, alpha or no alpha.
              if (neutral.has(hue.toLowerCase())) continue;
              if (!allowed.has(hue)) {
                colorHits.push({ selector: selectorFor(el), property: prop, value: v });
              }
              continue;
            }
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

          // Contrast is measured only where text is actually painted by this
          // element -- an inherited colour on a wrapper is not a rendering.
          if (hasOwnText(el) && rect.width > 0 && rect.height > 0 && cs.visibility !== "hidden") {
            const fg = parseRgb(cs.color);
            const opacity = parseFloat(cs.opacity || "1");
            if (fg && opacity > 0) {
              const bg = effectiveBackground(el);
              const composited = over({ ...fg, a: fg.a * opacity }, bg);
              const size = parseFloat(cs.fontSize) || 16;
              const weight = parseInt(cs.fontWeight, 10) || 400;
              // WCAG large text: >=24px, or >=18.66px when bold.
              const large = size >= 24 || (size >= 18.66 && weight >= 700);
              const required = large ? 3 : 4.5;
              const r = ratio(composited, bg);
              if (r < required) {
                contrastHits.push({
                  selector: selectorFor(el),
                  ratio: r.toFixed(2),
                  required,
                  fontSize: size.toFixed(1),
                  weight,
                  color: cs.color,
                  background: `rgb(${Math.round(bg.r)}, ${Math.round(bg.g)}, ${Math.round(bg.b)})`,
                });
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

        // document.fonts.check() alone is NOT proof: it returns true when no
        // matching FontFace exists at all and a fallback satisfies the request, so
        // an artifact with every webfont <link> deleted still reported "loaded".
        // Require an actual FontFace entry for the family, in status "loaded".
        const fontLoaded = {};
        const faces = Array.from(document.fonts);
        for (const f of fontFamilies) {
          if (generic.has(f.toLowerCase())) continue;
          const wanted = f.toLowerCase();
          const matching = faces.filter(
            (face) => (face.family || "").replace(/["']/g, "").trim().toLowerCase() === wanted,
          );
          fontLoaded[f] = {
            faces: matching.length,
            loaded: matching.some((face) => face.status === "loaded"),
            checkSaysYes: document.fonts.check(`16px "${f}"`),
          };
        }

        return {
          colorHits, fontHits, shapeHits, hiddenHits, contrastHits, fontLoaded,
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
    for (const [family, info] of Object.entries(report.fontLoaded)) {
      if (info.faces === 0) {
        violations.push(
          `FONT-NOT-LOADED  "${family}" is declared by the profile but no matching FontFace exists in the document ` +
            `(document.fonts.check() returned ${info.checkSaysYes} -- it is satisfied by a fallback, which is why it is not proof)`,
        );
      } else if (!info.loaded) {
        violations.push(`FONT-NOT-LOADED  "${family}" has ${info.faces} FontFace entr(ies) but none reached status "loaded"`);
      }
    }
    for (const h of report.contrastHits) {
      violations.push(
        `CONTRAST-BELOW-AA  ${h.selector} ${h.ratio}:1 at ${h.fontSize}px ${h.weight} (needs ${h.required}:1) ` +
          `-- ${h.color} on ${h.background}`,
      );
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
