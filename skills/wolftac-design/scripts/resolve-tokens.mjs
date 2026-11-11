#!/usr/bin/env node
// resolve-tokens.mjs — brand token resolution with a LOUD degrade.
//
// This is the whole point of the issue. Read `tokens_source` first. If it is
// unreadable, fall back to the profile's `snapshot` AND say so in the provenance
// header. Silent staleness is exactly the bug that left two v3-green Wolftac brand
// skills confidently emitting a retired palette.
//
// Usage: node resolve-tokens.mjs --profile <id> [--brands <dir>]
// Prints the provenance header and the resolved token bundle as JSON.

import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_BRANDS = resolve(HERE, "..", "brands");

export async function loadProfile(profileId, brandsDir = DEFAULT_BRANDS) {
  const path = join(brandsDir, `${profileId}.json`);
  if (!existsSync(path)) throw new Error(`UNKNOWN-PROFILE: ${profileId} (looked in ${brandsDir})`);
  return JSON.parse(await readFile(path, "utf8"));
}

// Flatten the canonical Wolftac token file into the same shape the snapshot uses,
// so a caller never has to care which source answered.
function adaptCanonical(raw) {
  if (!raw || typeof raw !== "object") return null;
  if (!raw.color && !raw.typography) return null;
  return {
    color: raw.color,
    typography: raw.typography,
    layout: raw.layout,
    effects: raw.effects,
    logo: raw.logo,
    version: raw.meta?.version || null,
  };
}

export async function resolveTokens(profile) {
  const source = profile.tokens_source;

  if (!source) {
    // A profile with no canonical source is not degraded -- the snapshot IS canon
    // (neutral, and any brand onboarded from source material rather than a token file).
    return {
      tokens: profile.snapshot,
      provenance: `TOKENS: profile snapshot v${profile.snapshot_version} (profile declares no external source)`,
      degraded: false,
      sourceUsed: null,
    };
  }

  if (existsSync(source)) {
    try {
      const text = await readFile(source, "utf8");
      // A .json source is canonical tokens; anything else (e.g. a CSS file) is
      // treated as unparsed provenance and the snapshot carries the values.
      if (source.toLowerCase().endsWith(".json")) {
        const adapted = adaptCanonical(JSON.parse(text));
        if (adapted) {
          return {
            tokens: adapted,
            provenance: `TOKENS: ${source} (canonical, v${adapted.version || "unversioned"})`,
            degraded: false,
            sourceUsed: source,
          };
        }
      } else {
        return {
          tokens: profile.snapshot,
          provenance: `TOKENS: snapshot v${profile.snapshot_version} extracted from ${source} (canonical source is not a token file)`,
          degraded: false,
          sourceUsed: source,
        };
      }
    } catch {
      // fall through to the loud degrade
    }
  }

  return {
    tokens: profile.snapshot,
    provenance: `TOKENS: snapshot v${profile.snapshot_version} (canonical source unreachable)`,
    degraded: true,
    sourceUsed: null,
  };
}

export function provenanceHeader({ profile, resolved, artifactType, mode }) {
  return [
    "PROVENANCE",
    `  BRAND: ${profile.id} (${profile.name})`,
    `  ${resolved.provenance}`,
    `  TYPE:  ${artifactType}`,
    `  MODE:  ${mode}`,
  ].join("\n");
}

const invokedDirectly =
  process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  const argv = process.argv.slice(2);
  let profileId = "";
  let brandsDir = DEFAULT_BRANDS;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--profile") profileId = argv[++i] || "";
    else if (argv[i] === "--brands") brandsDir = argv[++i] || "";
  }
  try {
    const profile = await loadProfile(profileId, brandsDir);
    const resolved = await resolveTokens(profile);
    console.log(provenanceHeader({ profile, resolved, artifactType: "(none)", mode: "resolve-only" }));
    console.log(JSON.stringify({ degraded: resolved.degraded, tokens: resolved.tokens }, null, 2));
  } catch (error) {
    console.error(`RESOLVE-ERROR: ${error.message || error}`);
    process.exitCode = 1;
  }
}
