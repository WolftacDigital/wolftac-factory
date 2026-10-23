// package.test.mjs -- regression harness for the wolftac-design package contract
// and its delegate/brand-extension behaviour (WD-339).
//
//   node tests/package.test.mjs        (run from skills/wolftac-design)
//
// Checks the things that rot silently: frontmatter shape, LF endings, every path a
// reference doc cites still existing, a new brand profile staying isolated, and the
// asset-export pixel scan rejecting a wrong-sized export.
//
// Two blocks reach outside this repo and SKIP loudly when their dependency is absent:
//   WOLFTAC_BRAND_ROOT     default D:/Wolftac/Brand  (AC-14 logo SVG)
//   WOLFTAC_DELEGATE_PATH  default the Hermes ui-ux-pro-max search.py (AC-11)
//
// AC-11 temporarily RENAMES that delegate to prove the DELEGATE-UNAVAILABLE path,
// and restores it in a finally block. If this suite is ever killed mid-run, check
// for a leftover *.wd339-bak beside it.
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, readdir, writeFile, rename, stat } from "node:fs/promises";
import { existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const exec = promisify(execFile);
const SKILL = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = process.env.WOLFTAC_DESIGN_TEST_OUT || join(tmpdir(), "wolftac-design-package");
const BRAND_ROOT = process.env.WOLFTAC_BRAND_ROOT || "D:/Wolftac/Brand";
const DELEGATE = process.env.WOLFTAC_DELEGATE_PATH ||
  join(process.env.LOCALAPPDATA || "", "hermes", "skills", "creative", "ui-ux-pro-max", "scripts", "search.py");
let pass = 0, fail = 0, skip = 0;
const failures = [];
function ok(n, c, d = "") {
  if (c) { pass++; console.log(`PASS ${n}`); }
  else { fail++; failures.push(`${n}  ${d}`); console.log(`FAIL ${n}  ${d}`); }
}
function skipped(n, why) { skip++; console.log(`SKIP ${n}  (${why})`); }
async function run(script, args) {
  try {
    const { stdout, stderr } = await exec(process.execPath, [join(SKILL, "scripts", script), ...args],
      { cwd: SKILL, maxBuffer: 20 * 1024 * 1024 });
    return { code: 0, out: stdout + stderr };
  } catch (e) { return { code: e.code ?? 1, out: (e.stdout || "") + (e.stderr || "") }; }
}

async function walk(dir, acc = []) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules") continue;
    const p = join(dir, entry.name);
    if (entry.isDirectory()) await walk(p, acc);
    else acc.push(p);
  }
  return acc;
}

// ------------------------------------------------- AC-1: package validation
console.log("=== AC-1: package validates as a well-formed repository package ===");
const skillMd = await readFile(join(SKILL, "SKILL.md"), "utf8");

// frontmatter parses
const fm = skillMd.match(/^---\n([\s\S]*?)\n---\n/);
ok("AC-1 frontmatter block present at file start", !!fm);
if (fm) {
  const body = fm[1];
  const nameLine = body.match(/^name:\s*(.+)$/m);
  const descLine = body.match(/^description:\s*(.+)$/m);
  ok("AC-1 frontmatter has a name", !!nameLine && nameLine[1].trim() === "wolftac-design",
     nameLine ? nameLine[1] : "missing");
  ok("AC-1 frontmatter has a description", !!descLine && descLine[1].trim().length > 40);
  ok("AC-1 frontmatter keys are only name/description",
     body.split("\n").filter(Boolean).every((l) => /^(name|description):/.test(l) || /^\s/.test(l)),
     body.slice(0, 120));
}

// every file LF-terminated (no CR anywhere)
const files = await walk(SKILL);
const crlf = [];
for (const f of files) {
  if (/\.(png|jpg|jpeg|ico|woff2?)$/i.test(f)) continue;
  const buf = await readFile(f);
  if (buf.includes(0x0d)) crlf.push(relative(SKILL, f));
}
ok("AC-1 every file is LF-terminated (zero CR bytes)", crlf.length === 0, `crlf=${crlf.join(", ")}`);
console.log(`     (${files.length} files scanned)`);

// every path SKILL.md references exists
const refs = new Set();
for (const m of skillMd.matchAll(/`([a-zA-Z0-9_\-./]+\.(?:md|json|mjs|html))`/g)) refs.add(m[1]);
for (const m of skillMd.matchAll(/`(references|brands|templates|scripts|testdata)\/\*[^`]*`/g)) {
  refs.add(m[1] + "/");
}
const missing = [];
for (const r of refs) {
  if (r.endsWith("/")) { if (!existsSync(join(SKILL, r))) missing.push(r); continue; }
  if (r.includes("*")) continue;
  if (!existsSync(join(SKILL, r))) missing.push(r);
}
ok("AC-1 every path SKILL.md references exists", missing.length === 0, `missing=${missing.join(", ")}`);
console.log(`     (${refs.size} referenced paths checked)`);

// reference docs also sweep clean
const refFiles = await readdir(join(SKILL, "references"));
const refMissing = [];
for (const rf of refFiles) {
  const text = await readFile(join(SKILL, "references", rf), "utf8");
  for (const m of text.matchAll(/`((?:references|brands|templates|scripts|testdata)\/[a-zA-Z0-9_\-.]+\.(?:md|json|mjs|html))`/g)) {
    if (!existsSync(join(SKILL, m[1]))) refMissing.push(`${rf} -> ${m[1]}`);
  }
}
ok("AC-1 every path the reference docs cite exists", refMissing.length === 0, refMissing.join(", "));

// ------------------------------------------------- AC-11: delegate degradation
console.log("=== AC-11: an unavailable delegate degrades visibly, artifact still completes ===");
const delegatePresent = existsSync(DELEGATE);
console.log(`     ui-ux-pro-max search.py present: ${delegatePresent}`);
let moved = false;
if (delegatePresent) {
  await rename(DELEGATE, DELEGATE + ".wd339-bak");
  moved = true;
}
// try/finally: everything below moves a file on the LIVE Hermes install out of the
// way. An assertion that throws mid-block would leave that install missing a script.
try {
// The skill's documented degradation contract: emit the line, complete on doctrine.
const stillThere = existsSync(DELEGATE);
ok("AC-11 delegate is genuinely unreachable for the test", !stillThere);
const degradedRun = await run("make-artifact.mjs",
  ["--template", "landing-page", "--profile", "wolftac", "--out", join(OUT, "delegate-degraded.html")]);
ok("AC-11 artifact still completes with the delegate absent",
   degradedRun.code === 0 && existsSync(join(OUT, "delegate-degraded.html")), degradedRun.out.slice(0, 200));
const degradedAssert = await run("brand-assert.mjs", [join(OUT, "delegate-degraded.html"), "--profile", "wolftac"]);
ok("AC-11 degraded artifact still passes the gate", degradedAssert.code === 0, degradedAssert.out.slice(0, 200));
const mapText = await readFile(join(SKILL, "references", "delegation-map.md"), "utf8");
ok("AC-11 the DELEGATE-UNAVAILABLE contract is documented verbatim",
   mapText.includes("DELEGATE-UNAVAILABLE: <skill>"));
ok("AC-11 SKILL.md instructs the agent to emit it",
   skillMd.includes("DELEGATE-UNAVAILABLE"));
} finally {
  if (moved) await rename(DELEGATE + ".wd339-bak", DELEGATE);
}
ok("AC-11 delegate path restored", !delegatePresent || existsSync(DELEGATE));

// ------------------------------------------------- AC-12: anti-slop fixture
console.log("=== AC-12: anti-slop fixture carries both cases ===");
const sloppy = await readFile(join(SKILL, "testdata", "sloppy-sample.html"), "utf8");
ok("AC-12 fixture contains the fabricated testimonial", /Sarah Chen, VP Engineering, Acme/.test(sloppy));
ok("AC-12 fixture contains a visible bracket placeholder", /\[Client Name\]/.test(sloppy));
const slop = await readFile(join(SKILL, "references", "anti-slop-diagnostic.md"), "utf8");
ok("AC-12 diagnostic says fabricated proof is deleted", /Fabricated proof gets deleted/i.test(slop));
ok("AC-12 diagnostic says bracket placeholders may stay", /placeholders may stay/i.test(slop));
ok("AC-12 diagnostic names the testimonial case", /testimonial/i.test(slop));
ok("AC-12 diagnostic requires stating which placeholders are left",
   /which placeholders you are \*\*leaving\*\*/i.test(slop) || /leaving/i.test(slop));

// ------------------------------------------------- AC-13: new-brand onboarding
console.log("=== AC-13: onboard a new brand from the checked-in fixture ===");
const fixture = await readFile(join(SKILL, "testdata", "acme-relief-source.html"), "utf8");
const rootBlock = (fixture.match(/:root\s*\{[\s\S]*?\}/) || [""])[0];
const hexes = Array.from(new Set((rootBlock.match(/#[0-9A-Fa-f]{6}/g) || []).map((h) => h.toUpperCase())));
const meridian = {
  id: "meridian",
  name: "Meridian Field Systems",
  tokens_source: join(SKILL, "testdata", "acme-relief-source.html"),
  snapshot_version: "1.0",
  snapshot: {
    color: {
      backgrounds: { primary: "#14181F", secondary: "#1C222B", tertiary: "#262E3A" },
      text: { primary: "#F2F0EB", secondary: "#8892A0", accent: "#2E8B74" },
      borders: { default: "rgba(242, 240, 235, 0.10)" },
    },
    typography: {
      // A profile that names webfont families MUST carry the import that loads
      // them. Left null, every artifact this profile generates fails the
      // FONT-NOT-LOADED gate -- correctly, because the families never arrive.
      font_imports: "https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600&family=Source+Sans+3:wght@400&family=Roboto+Mono:wght@400&display=swap",
      headings: { font_family: "Barlow Condensed", weights: [600] },
      body: { font_family: "Source Sans 3", weights: [400] },
      monospace_labels: { font_family: "Roboto Mono", weights: [400] },
    },
    layout: { border_radius: "3px", content_max_width: "1120px" },
    effects: { restraint: "Editorial. No gradient, no glow." },
  },
  voice: "Practical, field-tested, unadorned.",
  artifact_defaults: { "landing-page": { background: "#14181F", max_width: "1120px", reveal: true } },
  signature_tokens: ["#2E8B74", "#C8622F", "Barlow Condensed"],
  prohibitions: [],
  // #2E8B74 stays the brand accent for structure and large type, but it is only
  // 4.29:1 on #14181F -- under AA for 12px labels. Same fix the OMAS profile needed:
  // name AA-safe UI colours instead of retuning the brand. Both must also be in the
  // palette, or the artifact trades a contrast violation for an off-palette one.
  ui: { accent_text: "#3AA88C", cta_background: "#1F6B58", cta_text: "#F2F0EB" },
  palette: [...hexes, "#3AA88C", "#1F6B58"],
  font_families: ["Barlow Condensed", "Source Sans 3", "Roboto Mono"],
  shape_rules: { border_radius_px: 3 },
};
const meridianPath = join(SKILL, "brands", "meridian.json");
await writeFile(meridianPath, JSON.stringify(meridian, null, 2) + "\n", "utf8");
// This writes into the tracked brands/ directory. Register the cleanup now, before
// any assertion can throw, so a failing run cannot leave an untracked profile behind.
process.on("exit", () => { try { rmSync(meridianPath, { force: true }); } catch {} });

// schema rules from brand-profile-schema.md, checked mechanically
const otherProfiles = [];
for (const f of (await readdir(join(SKILL, "brands"))).filter((f) => f.endsWith(".json"))) {
  otherProfiles.push(JSON.parse(await readFile(join(SKILL, "brands", f), "utf8")));
}
ok("AC-13 rule 1: id matches filename stem", meridian.id === "meridian");
const paletteSet = new Set(meridian.palette);
const sigOk = meridian.signature_tokens.every((t) => paletteSet.has(t) || meridian.font_families.includes(t));
ok("AC-13 rule 3: signature tokens are a subset of palette + families", sigOk);
const generics = ["#FFFFFF", "#000000", "#F2F0EB", "#14181F"];
ok("AC-13 rule 4: no generic neutral is a signature",
   !meridian.signature_tokens.some((t) => generics.includes(t)));
let disjoint = true;
for (const p of otherProfiles) {
  if (p.id === "meridian" || p.id === "_template") continue;
  for (const t of p.signature_tokens || []) if (meridian.signature_tokens.includes(t)) disjoint = false;
}
ok("AC-13 rule 5: signature tokens disjoint from every live profile", disjoint);

const mk = await run("make-artifact.mjs",
  ["--template", "landing-page", "--profile", "meridian", "--out", join(OUT, "meridian-landing.html")]);
ok("AC-13 artifact generates from the new profile", mk.code === 0, mk.out.slice(0, 200));
const mHtml = await readFile(join(OUT, "meridian-landing.html"), "utf8");
ok("AC-13 artifact carries only the new brand's values",
   /#2E8B74/i.test(mHtml) && !/#FF5E00|#00F0FF|Rajdhani|Chakra Petch|#C43B3B|Space Grotesk/i.test(mHtml));
const mAssert = await run("brand-assert.mjs", [join(OUT, "meridian-landing.html"), "--profile", "meridian"]);
ok("AC-13 new-brand artifact passes the full gate", mAssert.code === 0, mAssert.out.slice(0, 400));

// ------------------------------------------------- AC-14: asset export
console.log("=== AC-14: SVG to transparent PNG plus pixel verification ===");
const svg = join(BRAND_ROOT, "assets", "logo", "web", "svg", "mark-white.svg");
if (!existsSync(svg)) {
  skipped("AC-14 SVG to transparent PNG plus pixel verification",
          `${svg} not present -- set WOLFTAC_BRAND_ROOT`);
} else {
const r14 = await run("render-svg-png.mjs", [svg, join(OUT, "mark-ac14.png"), "--size", "500"]);
ok("AC-14 export succeeds", r14.code === 0, r14.out.slice(0, 200));
const v14 = await run("verify-png.mjs", [join(OUT, "mark-ac14.png"), "--size", "500", "--expect-color", "#E8EAE9"]);
ok("AC-14 pixel scan passes", v14.code === 0, v14.out.slice(0, 400));
const parsed = JSON.parse(v14.out.slice(v14.out.indexOf("{"), v14.out.lastIndexOf("}") + 1));
ok("AC-14 reports 500x500", parsed.dimensions.width === 500 && parsed.dimensions.height === 500);
ok("AC-14 reports target-colour and transparent pixel counts",
   parsed.pixels.targetColorCount > 0 && parsed.pixels.transparent > 0,
   JSON.stringify(parsed.pixels));
ok("AC-14 bounding box is centred within tolerance",
   parsed.boundingBox.offsetX <= 8 && parsed.boundingBox.offsetY <= 8,
   JSON.stringify(parsed.boundingBox));
// anti-vacuity: the scan must FAIL on a bad export
const badPng = join(OUT, "mark-bad.png");
const rBad = await run("render-svg-png.mjs", [svg, badPng, "--size", "120"]);
const vBad = await run("verify-png.mjs", [badPng, "--size", "500"]);
ok("AC-14 scan rejects a wrong-sized export", vBad.code !== 0 && /WRONG-SIZE/.test(vBad.out), vBad.out.slice(0, 200));
}

console.log("");
console.log(`RESULT pass=${pass} fail=${fail} skip=${skip}`);
if (failures.length) { console.log("FAILURES:"); failures.forEach((f) => console.log("  " + f)); }
process.exitCode = fail ? 1 : 0;
