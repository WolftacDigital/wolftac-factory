// gates.test.mjs -- regression harness for the wolftac-design proof gates (WD-339).
//
//   node tests/gates.test.mjs          (run from skills/wolftac-design)
//
// Every gate here is asserted in BOTH directions: a clean artifact must pass, and a
// deliberately broken one must fail while naming the offender. A gate that only ever
// passes is not a gate -- that is how a 512px-wide "mobile" render with its Status
// column clipped once reported success.
//
// Environment: renders need Chrome or Edge (see scripts/render-proof.mjs). Two blocks
// read repositories outside this one; when those are absent the block SKIPS loudly
// and is counted separately, never silently folded into the pass count.
//   OMAS_SITE_ROOT      default D:/Wolftac/omas-relief-site
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, writeFile, mkdir, readdir, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const exec = promisify(execFile);
const SKILL = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = process.env.WOLFTAC_DESIGN_TEST_OUT || join(tmpdir(), "wolftac-design-gates");
const OMAS_SITE_ROOT = process.env.OMAS_SITE_ROOT || "D:/Wolftac/omas-relief-site";
let pass = 0, fail = 0, skip = 0;
const failures = [];

function ok(name, cond, detail = "") {
  if (cond) { pass++; console.log(`PASS ${name}`); }
  else { fail++; failures.push(`${name}  ${detail}`); console.log(`FAIL ${name}  ${detail}`); }
}
function skipped(name, why) { skip++; console.log(`SKIP ${name}  (${why})`); }

async function run(script, args) {
  try {
    const { stdout, stderr } = await exec(process.execPath, [join(SKILL, "scripts", script), ...args], {
      cwd: SKILL, maxBuffer: 20 * 1024 * 1024,
    });
    return { code: 0, out: stdout + stderr };
  } catch (e) {
    return { code: e.code ?? 1, out: (e.stdout || "") + (e.stderr || "") };
  }
}

async function make(template, profile, outFile) {
  return run("make-artifact.mjs", ["--template", template, "--profile", profile, "--out", join(OUT, outFile)]);
}
async function assertBrand(file, profile) {
  return run("brand-assert.mjs", [join(OUT, file), "--profile", profile]);
}
await mkdir(OUT, { recursive: true });

// Seed the two baseline artifacts every later block mutates. The original session
// harness read these off disk from an earlier manual run; a committed test has to
// produce its own inputs or it passes only on the machine that made them.
console.log("=== seed: baseline artifacts ===");
for (const [profile, file] of [["wolftac", "wolftac-landing.html"], ["neutral", "neutral-landing.html"]]) {
  const seed = await make("landing-page", profile, file);
  ok(`seed ${profile} landing-page generated`, seed.code === 0 && existsSync(join(OUT, file)), seed.out.slice(0, 300));
}

// ---------------------------------------------------------------- AC-8: seeded defects
console.log("=== AC-8: four seeded defects must each fail, naming the offender ===");
const clean = await readFile(join(OUT, "wolftac-landing.html"), "utf8");

const defects = [
  { name: "off-palette hex", file: "defect-color.html",
    mutate: (h) => h.replace(".lede {", ".lede { color: #B21FCB;"),
    expect: /OFF-PALETTE/ },
  // Injected as TRAILING overrides. An earlier declaration in the same cascade is
  // beaten by the template's own later rule, so a naive prepend seeds a defect that
  // never reaches the rendered page -- and the gate correctly reports conformance.
  { name: "off-profile font-family", file: "defect-font.html",
    mutate: (h) => h.replace("</style>", '.lede { font-family: "Comic Sans MS", cursive; }\n  </style>'),
    expect: /OFF-PROFILE-FONT/ },
  { name: "border-radius under wolftac", file: "defect-radius.html",
    mutate: (h) => h.replace("</style>", ".panel { border-radius: 8px; }\n  </style>"),
    expect: /SHAPE-VIOLATION/ },
  { name: "console error on load", file: "defect-console.html",
    mutate: (h) => h.replace("</body>", "<script>console.error('seeded failure');</script></body>"),
    expect: /CONSOLE-ERROR/ },
];

for (const d of defects) {
  await writeFile(join(OUT, d.file), d.mutate(clean), "utf8");
  const r = await assertBrand(d.file, "wolftac");
  ok(`AC-8 ${d.name} exits non-zero`, r.code !== 0, `code=${r.code}`);
  ok(`AC-8 ${d.name} names the violation`, d.expect.test(r.out), r.out.slice(0, 200));
}
const cleanAgain = await assertBrand("wolftac-landing.html", "wolftac");
ok("AC-8 clean original still exits 0", cleanAgain.code === 0, cleanAgain.out.slice(0, 200));

// ---------------------------------------------------------------- AC-9: revealed end-state
console.log("=== AC-9: a reveal gate that never reveals must fail ===");
// Break it the way WD-217 broke: a rule whose specificity beats .in.
const broken = clean.replace(
  "[data-reveal].in { opacity: 1; transform: none; }",
  "[data-reveal].in { opacity: 1; transform: none; }\n    body [data-reveal][data-reveal] { opacity: 0 !important; }",
);
await writeFile(join(OUT, "broken-reveal.html"), broken, "utf8");
const brokenR = await assertBrand("broken-reveal.html", "wolftac");
ok("AC-9 broken reveal exits non-zero", brokenR.code !== 0, `code=${brokenR.code}`);
ok("AC-9 message names the hidden element", /HIDDEN-REVEALED-CONTENT/.test(brokenR.out), brokenR.out.slice(0, 300));
const repairedR = await assertBrand("wolftac-landing.html", "wolftac");
ok("AC-9 repaired artifact exits 0", repairedR.code === 0);

// ---------------------------------------------------------------- AC-6: loud degrade
console.log("=== AC-6: unreachable canonical source degrades loudly, still produces ===");
const profilePath = join(SKILL, "brands", "wolftac.json");
const original = await readFile(profilePath, "utf8");
const broken_profile = original.replace(
  /"tokens_source": "[^"]*"/,
  '"tokens_source": "D:\\\\Wolftac\\\\Brand\\\\tokens\\\\NO-SUCH-FILE.json"',
);
// try/finally, because this block edits a tracked profile in the working tree. An
// assertion that throws mid-block must not leave brands/wolftac.json broken.
try {
  await writeFile(profilePath, broken_profile, "utf8");
  const degraded = await make("landing-page", "wolftac", "degraded.html");
  ok("AC-6 artifact is still produced", existsSync(join(OUT, "degraded.html")), degraded.out.slice(0, 200));
  ok("AC-6 prints the snapshot warning verbatim",
     degraded.out.includes("TOKENS: snapshot v4.0 (canonical source unreachable)"), degraded.out.slice(0, 300));
} finally {
  await writeFile(profilePath, original, "utf8");
}
const restored = await make("landing-page", "wolftac", "restored.html");
ok("AC-6 profile restored to canonical", restored.out.includes("brand-tokens.json (canonical"), restored.out.slice(0, 200));

// ---------------------------------------------------------------- AC-3/AC-4/AC-5 isolation
console.log("=== AC-3/4/5: brand isolation ===");
const neutralHtml = await readFile(join(OUT, "neutral-landing.html"), "utf8");
ok("AC-3 neutral contains zero Wolftac tokens",
   !/#FF5E00|#00F0FF|#050706|Rajdhani|Chakra Petch/i.test(neutralHtml));
const wolftacHtml = await readFile(join(OUT, "wolftac-landing.html"), "utf8");
ok("AC-4 wolftac contains zero v3 values", !/#7CFF98|#8FF0BC|Archivo/i.test(wolftacHtml));

await make("landing-page", "omas-atlas", "omas-landing.html");
const omasHtml = await readFile(join(OUT, "omas-landing.html"), "utf8");
ok("AC-5 omas contains zero Wolftac tokens",
   !/#FF5E00|#00F0FF|#050706|Rajdhani|Chakra Petch/i.test(omasHtml));
ok("AC-5 omas carries the 14px cut corner", /--cut:\s*14px/.test(omasHtml));
const omasAssert = await assertBrand("omas-landing.html", "omas-atlas");
ok("AC-5 omas passes its own profile", omasAssert.code === 0, omasAssert.out.slice(0, 300));

// AC-5 value-by-value against ground truth
const omasCssPath = join(OMAS_SITE_ROOT, "assets", "css", "main.css");
if (!existsSync(omasCssPath)) {
  skipped("AC-5 every profile palette value exists in the source :root",
          `${omasCssPath} not present -- set OMAS_SITE_ROOT`);
} else {
const omasCss = await readFile(omasCssPath, "utf8");
const rootBlock = (omasCss.match(/:root\s*\{[\s\S]*?\}/) || [""])[0];
const profileJson = JSON.parse(await readFile(join(SKILL, "brands", "omas-atlas.json"), "utf8"));
const mismatches = [];
for (const hex of profileJson.palette) {
  if (!new RegExp(hex.replace("#", "#"), "i").test(rootBlock)) mismatches.push(hex);
}
ok("AC-5 every profile palette value exists in the source :root", mismatches.length === 0, `missing=${mismatches}`);
}

// ---------------------------------------------------------------- AC-10: template matrix
console.log("=== AC-10: three templates x two profiles, full gate ===");
for (const template of ["landing-page", "visual-identity-board", "dashboard-shell"]) {
  for (const profile of ["wolftac", "omas-atlas"]) {
    const file = `${profile}-${template}.html`;
    const m = await make(template, profile, file);
    ok(`AC-10 generate ${profile}/${template}`, m.code === 0, m.out.slice(0, 200));
    const proof = await run("render-proof.mjs", [join(OUT, file)]);
    ok(`AC-10 render ${profile}/${template} (2 viewports, 0 console errors)`,
       proof.code === 0 && /desktop-1440x900/.test(proof.out) && /mobile-390x844/.test(proof.out),
       proof.out.slice(0, 300));
    const a = await assertBrand(file, profile);
    ok(`AC-10 assert ${profile}/${template}`, a.code === 0, a.out.slice(0, 400));
  }
}

// AC-7: screenshot dimensions and non-trivial content
console.log("=== AC-7: screenshots exist at both viewports with real content ===");
const shots = (await readdir(OUT)).filter((f) => f.endsWith(".png"));
ok("AC-7 twelve matrix screenshots written", shots.length >= 12, `count=${shots.length}`);
let tiny = 0;
for (const s of shots) {
  const st = await stat(join(OUT, s));
  if (st.size < 5000) tiny++;
}
ok("AC-7 no screenshot is blank-sized (<5KB)", tiny === 0, `tiny=${tiny}`);

console.log("");
console.log(`RESULT pass=${pass} fail=${fail} skip=${skip}`);
if (failures.length) { console.log("FAILURES:"); failures.forEach((f) => console.log("  " + f)); }
process.exitCode = fail ? 1 : 0;
