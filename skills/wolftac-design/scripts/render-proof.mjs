#!/usr/bin/env node
// render-proof.mjs — headless render proof for a wolftac-design artifact.
//
// Launches Chrome with a FRESH --user-data-dir and --disable-extensions. This is not
// a nicety: daily-driver extensions have already produced false verification verdicts
// on this machine (WD-218), so a clean profile is the only trustworthy one.
//
// Usage:  node render-proof.mjs <artifact.html> [--out <dir>]
// Writes: <artifact>.desktop-1440x900.png and <artifact>.mobile-390x844.png
// Exit:   0 on success, non-zero on any launch/load/console failure.

import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname, basename, extname } from "node:path";
import { pathToFileURL } from "node:url";

const VIEWPORTS = [
  { label: "desktop", width: 1440, height: 900 },
  { label: "mobile", width: 390, height: 844 },
];

const CHROME_CANDIDATES = [
  process.env.PUPPETEER_EXECUTABLE_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(Boolean);

export function findChrome() {
  for (const candidate of CHROME_CANDIDATES) {
    if (existsSync(candidate)) return candidate;
  }
  throw new Error(
    "NO-CHROME: set PUPPETEER_EXECUTABLE_PATH or install Chrome/Edge at a standard Windows path.",
  );
}

function parseArgs(argv) {
  const args = { artifact: "", out: "" };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--out") args.out = argv[++i] || "";
    else if (!args.artifact) args.artifact = argv[i];
    else throw new Error(`Unknown argument: ${argv[i]}`);
  }
  if (!args.artifact) throw new Error("Usage: node render-proof.mjs <artifact.html> [--out <dir>]");
  return args;
}

// The launch args are returned so the test plan can assert on them (AC-7) rather
// than taking the script's word for the clean profile.
export function launchArgs(userDataDir) {
  return [
    `--user-data-dir=${userDataDir}`,
    "--disable-extensions",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--hide-scrollbars",
  ];
}

export async function renderProof(artifactPath, outDir) {
  const puppeteer = (await import("puppeteer-core")).default;
  const artifact = resolve(artifactPath);
  if (!existsSync(artifact)) throw new Error(`MISSING-ARTIFACT: ${artifact}`);

  const target = outDir ? resolve(outDir) : dirname(artifact);
  await mkdir(target, { recursive: true });

  const stem = basename(artifact, extname(artifact));
  const userDataDir = await mkdtemp(join(tmpdir(), "wolftac-design-profile-"));
  const consoleErrors = [];
  const overflows = [];
  const written = [];

  const browser = await puppeteer.launch({
    executablePath: findChrome(),
    headless: "new",
    args: launchArgs(userDataDir),
  });

  try {
    for (const vp of VIEWPORTS) {
      const page = await browser.newPage();
      page.on("console", (msg) => {
        if (msg.type() === "error") consoleErrors.push(`[${vp.label}] ${msg.text()}`);
      });
      page.on("pageerror", (err) => consoleErrors.push(`[${vp.label}] ${err.message}`));

      await page.setViewport({ width: vp.width, height: vp.height });
      await page.goto(pathToFileURL(artifact).href, {
        waitUntil: ["load", "networkidle0"],
        timeout: 30000,
      });
      // Fonts must be settled before the shot, or the PNG proves nothing about type.
      await page.evaluate(() => document.fonts.ready);
      // Let any reveal gate run to its end state; the assertion checks the result.
      await page.evaluate(() => new Promise((r) => setTimeout(r, 400)));

      // A screenshot proves a render happened, not that the render is usable. The
      // 390px dashboard overflowed to 512px and clipped its entire Status column
      // while this command still reported success, so measure the document and fail
      // on horizontal overflow rather than leaving it to someone's eye.
      const overflow = await page.evaluate(() => {
        const de = document.documentElement;
        const widest = Array.from(document.body.querySelectorAll("*"))
          .map((el) => {
            const r = el.getBoundingClientRect();
            return { right: Math.round(r.right), tag: el.tagName.toLowerCase(), cls: (el.className || "").toString().trim().split(/\s+/)[0] || "" };
          })
          .sort((a, b) => b.right - a.right)[0] || null;
        return { clientWidth: de.clientWidth, scrollWidth: de.scrollWidth, widest };
      });
      if (overflow.scrollWidth > overflow.clientWidth + 1) {
        overflows.push(
          `[${vp.label} ${vp.width}x${vp.height}] horizontal overflow: scrollWidth=${overflow.scrollWidth} > clientWidth=${overflow.clientWidth}` +
            (overflow.widest ? ` (widest element ${overflow.widest.tag}${overflow.widest.cls ? "." + overflow.widest.cls : ""} right edge ${overflow.widest.right})` : ""),
        );
      }

      const file = join(target, `${stem}.${vp.label}-${vp.width}x${vp.height}.png`);
      await page.screenshot({ path: file });
      written.push({ file, ...vp, ...overflow });
      await page.close();
    }
  } finally {
    await browser.close();
    await rm(userDataDir, { recursive: true, force: true }).catch(() => {});
  }

  return { written, consoleErrors, overflows, userDataDir, args: launchArgs(userDataDir) };
}

const invokedDirectly =
  process.argv[1] && resolve(process.argv[1]) === resolve(new URL(import.meta.url).pathname.replace(/^\/(?=[A-Za-z]:)/, ""));

if (invokedDirectly) {
  try {
    const args = parseArgs(process.argv.slice(2));
    const result = await renderProof(args.artifact, args.out);
    console.log(
      JSON.stringify(
        {
          status: result.consoleErrors.length || result.overflows.length ? "failed" : "ok",
          screenshots: result.written.map((w) => ({
            file: w.file,
            width: w.width,
            height: w.height,
            clientWidth: w.clientWidth,
            scrollWidth: w.scrollWidth,
          })),
          cleanProfile: true,
          launchArgs: result.args,
          consoleErrors: result.consoleErrors,
          overflows: result.overflows,
        },
        null,
        2,
      ),
    );
    if (result.consoleErrors.length || result.overflows.length) process.exitCode = 1;
  } catch (error) {
    console.error(JSON.stringify({ status: "failed", error: String(error.message || error) }));
    process.exitCode = 1;
  }
}
