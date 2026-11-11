// regressions.test.mjs -- the four defects the WD-339 review found in a suite that
// was already green, pinned as reproducible assertions.
//
//   node tests/regressions.test.mjs    (run from skills/wolftac-design)
//
// Each one is asserted in BOTH directions. A gate that fires is only half the proof;
// the other half is that the legitimate case it resembles still passes. Three of
// these four shipped green because only the firing half was ever checked.
//
//   1. RGBA was an escape hatch. brand-assert skipped every rgba(...) value on the
//      theory that alpha is compositing of an allowed hue, so an off-palette colour
//      smuggled in with an alpha channel passed. The inverse matters just as much:
//      Wolftac's own rgba(255,255,255,0.05) hairline border must keep passing.
//   2. document.fonts.check() is not proof of loading. It returns true when a
//      FALLBACK satisfies the request, so a document with every webfont <link>
//      stripped reported its fonts as present.
//   3. There was no contrast measurement at all. The gate now composites the
//      effective background and measures; the anti-vacuity proof is that it
//      reproduces the exact pre-fix ratio rather than merely asserting a rule.
//   4. A screenshot proves a render happened, not that it is usable. The 390px
//      dashboard overflowed to 512px and clipped its Status column while
//      render-proof.mjs still reported success.
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const exec = promisify(execFile);
const SKILL = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = process.env.WOLFTAC_DESIGN_TEST_OUT || join(tmpdir(), "wolftac-design-regressions");
let pass = 0, fail = 0;
const failures = [];
function ok(n, c, d = "") {
  if (c) { pass++; console.log(`PASS ${n}`); }
  else { fail++; failures.push(`${n}  ${d}`); console.log(`FAIL ${n}  ${d}`); }
}
async function run(script, args) {
  try {
    const { stdout, stderr } = await exec(process.execPath, [join(SKILL, "scripts", script), ...args],
      { cwd: SKILL, maxBuffer: 20 * 1024 * 1024 });
    return { code: 0, out: stdout + stderr };
  } catch (e) { return { code: e.code ?? 1, out: (e.stdout || "") + (e.stderr || "") }; }
}
const make = (template, profile, file) =>
  run("make-artifact.mjs", ["--template", template, "--profile", profile, "--out", join(OUT, file)]);
const assertBrand = (file, profile) =>
  run("brand-assert.mjs", [join(OUT, file), "--profile", profile]);

// Seed defects as TRAILING overrides, immediately before </style>. Prepending into
// an existing rule is beaten by the template's own later declaration in the same
// cascade, so the defect never reaches the rendered page and the gate correctly
// reports conformance -- which is how two seeded defects went undetected once.
const override = (html, css) => html.replace("</style>", css + "\n  </style>");

await mkdir(OUT, { recursive: true });

console.log("=== baseline: a clean artifact of each profile passes ===");
for (const [profile, file] of [["wolftac", "base-wolftac.html"], ["omas-atlas", "base-omas.html"]]) {
  const m = await make("landing-page", profile, file);
  ok("baseline " + profile + " generated", m.code === 0, m.out.slice(0, 200));
  const a = await assertBrand(file, profile);
  ok("baseline " + profile + " passes the gate", a.code === 0, a.out.slice(0, 400));
}
const cleanWolftac = await readFile(join(OUT, "base-wolftac.html"), "utf8");

console.log("=== 1. alpha is not an escape hatch from the palette ===");
await writeFile(join(OUT, "r1-rgba.html"),
  override(cleanWolftac, ".panel { color: rgba(1, 2, 3, .8) !important; }"), "utf8");
const r1 = await assertBrand("r1-rgba.html", "wolftac");
ok("1 off-palette rgba exits non-zero", r1.code !== 0, "code=" + r1.code);
ok("1 off-palette rgba is named as OFF-PALETTE", /OFF-PALETTE/.test(r1.out), r1.out.slice(0, 300));
ok("1 the offending value is quoted back", /rgba\(1, ?2, ?3/.test(r1.out), r1.out.slice(0, 300));
// The inverse. This exemption is load-bearing: translucent white/black hairlines are
// legitimate structure, and a check that rejected them would be unusable.
ok("1 legitimate translucent hairline still present in the template",
   /rgba\(255,\s*255,\s*255,\s*0?\.0\d/.test(cleanWolftac),
   "template no longer carries the hairline -- the inverse case is now vacuous");
const r1b = await assertBrand("base-wolftac.html", "wolftac");
ok("1 clean artifact carrying that hairline still exits 0", r1b.code === 0, r1b.out.slice(0, 300));
// A fully transparent value is compositing, not a colour choice, and must not fire.
await writeFile(join(OUT, "r1-transparent.html"),
  override(cleanWolftac, ".panel { outline-color: rgba(1, 2, 3, 0); }"), "utf8");
const r1c = await assertBrand("r1-transparent.html", "wolftac");
ok("1 fully transparent value does not fire the palette check", r1c.code === 0, r1c.out.slice(0, 300));

console.log("=== 2. a fallback satisfying document.fonts.check() is not a loaded font ===");
const stripped = cleanWolftac.replace(/[ \t]*<link[^>]*fonts\.(googleapis|gstatic)[^>]*>\n?/g, "");
ok("2 every webfont link was actually removed", !/fonts\.googleapis/.test(stripped),
   "the strip regex matched nothing -- the test would be vacuous");
await writeFile(join(OUT, "r2-nofonts.html"), stripped, "utf8");
const r2 = await assertBrand("r2-nofonts.html", "wolftac");
ok("2 stripped webfonts exit non-zero", r2.code !== 0, "code=" + r2.code);
ok("2 the failure is FONT-NOT-LOADED, naming the family",
   /FONT-NOT-LOADED\s+"[^"]+"/.test(r2.out), r2.out.slice(0, 400));
ok("2 the message records what check() claimed, so the trap stays visible",
   /document\.fonts\.check\(\)/.test(r2.out), r2.out.slice(0, 400));

console.log("=== 3. contrast is measured, not assumed ===");
// Run against omas-atlas, where BOTH colours below are on the palette -- so the only
// thing that can fire is the contrast gate, and the assertions cannot be satisfied by
// an unrelated OFF-PALETTE violation.
//
// The exact pre-fix OMAS state: red-500 #C43B3B on ink #0B0B0D is 3.78:1, under the
// 4.5:1 AA threshold for normal text. Reproducing the RATIO is what proves the gate
// measures rather than pattern-matching a known-bad hex.
const cleanOmas = await readFile(join(OUT, "base-omas.html"), "utf8");
await writeFile(join(OUT, "r3-contrast.html"),
  override(cleanOmas, ".lede { color: #C43B3B; background: #0B0B0D; font-size: 12px; }"), "utf8");
const r3 = await assertBrand("r3-contrast.html", "omas-atlas");
ok("3 low-contrast text exits non-zero", r3.code !== 0, "code=" + r3.code);
ok("3 the failure is CONTRAST-BELOW-AA", /CONTRAST-BELOW-AA/.test(r3.out), r3.out.slice(0, 400));
ok("3 nothing else fired -- this is a contrast failure, not a palette one",
   !/OFF-PALETTE/.test(r3.out), r3.out.slice(0, 400));
ok("3 it reports a measured ratio and the threshold it missed",
   /\d\.\d+:1 at [\d.]+px[\s\S]*?needs [\d.]+:1/.test(r3.out), r3.out.slice(0, 400));
const ratio = (r3.out.match(/(\d\.\d+):1 at/) || [])[1];
ok("3 the measured ratio is the real one, not a placeholder",
   !!ratio && Math.abs(parseFloat(ratio) - 3.78) < 0.15, "ratio=" + ratio + " expected ~3.78");
// The SAME colour pair at large size must NOT fire: AA allows 3:1 at >=24px. Only the
// font-size changes between these two cases, so this isolates the size rule exactly.
// A contrast gate that ignores size is a gate nobody can ship a brand against.
await writeFile(join(OUT, "r3-large.html"),
  override(cleanOmas, ".lede { color: #C43B3B; background: #0B0B0D; font-size: 28px; }"), "utf8");
const r3b = await assertBrand("r3-large.html", "omas-atlas");
ok("3 the same pair at large size does not fire", r3b.code === 0, r3b.out.slice(0, 400));

console.log("=== 4. a screenshot is not proof the render is usable ===");
await writeFile(join(OUT, "r4-overflow.html"),
  override(cleanWolftac, ".panel { min-width: 900px; }"), "utf8");
const r4 = await run("render-proof.mjs", [join(OUT, "r4-overflow.html")]);
ok("4 horizontal overflow exits non-zero", r4.code !== 0, "code=" + r4.code);
ok("4 the failure names the overflow and the viewport",
   /horizontal overflow: scrollWidth=\d+ > clientWidth=\d+/.test(r4.out), r4.out.slice(0, 600));
ok("4 it names the widest element and its right edge",
   /widest element \S+ right edge \d+/.test(r4.out), r4.out.slice(0, 600));
ok("4 it still wrote both viewport screenshots before failing",
   /desktop-1440x900/.test(r4.out) && /mobile-390x844/.test(r4.out), r4.out.slice(0, 600));
// The inverse: the shipped templates must survive 390px, including the dashboard
// whose Status column was the original casualty.
for (const template of ["landing-page", "dashboard-shell", "visual-identity-board"]) {
  const file = "r4-" + template + ".html";
  await make(template, "wolftac", file);
  const proof = await run("render-proof.mjs", [join(OUT, file)]);
  ok("4 " + template + " has no horizontal overflow at 390px", proof.code === 0, proof.out.slice(0, 400));
}

console.log("");
console.log("RESULT pass=" + pass + " fail=" + fail);
if (failures.length) { console.log("FAILURES:"); failures.forEach((f) => console.log("  " + f)); }
process.exitCode = fail ? 1 : 0;
