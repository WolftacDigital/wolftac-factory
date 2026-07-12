# Preview bypass secret masking review

Use this reference when `/wolftac-review` audits tooling that carries automation bypass secrets in URLs, especially Vercel preview probes and Lighthouse commands using `x-vercel-protection-bypass`.

## Review lesson

A raw `string.replace(secret, '[redacted]')` check is insufficient once the tool logs a constructed URL. `URLSearchParams` encodes secrets before they appear in the query string, so a network error that includes the URL can leak an encoded form even when the raw secret is masked.

## What to verify

1. Confirm the secret is sourced only from env (for Vercel: `VERCEL_AUTOMATION_BYPASS_SECRET`) and not from CLI args, constants, README examples, or committed config.
2. Exercise a fake secret containing reserved URL characters, e.g. `fake/plus+space value`.
3. Force a failure path that logs the constructed URL, such as a mocked fetch/network error.
4. Assert the output does **not** contain:
   - the raw fake secret,
   - `encodeURIComponent(fakeSecret)`,
   - `new URLSearchParams([["value", fakeSecret]]).toString().replace(/^value=/, "")`.
5. Assert the output still contains a redaction marker such as `[redacted]`, so the test proves masking happened rather than avoiding the path.

## Acceptable fix pattern

```js
export function maskSecret(text, secret) {
  if (!secret) return text;
  const encodedForUrlSearchParams = new URLSearchParams([["value", secret]])
    .toString()
    .replace(/^value=/, "");
  const variants = new Set([
    secret,
    encodeURIComponent(secret),
    encodedForUrlSearchParams,
  ].filter(Boolean));
  let masked = String(text);
  for (const variant of variants) {
    masked = masked.split(variant).join("[redacted]");
  }
  return masked;
}
```

## Reporting nuance

This is usually a minor, review-stage fix if the tool already keeps the real secret env-only and the defect is limited to encoded logging. Treat it as blocking if a real secret is committed, printed directly, or passed through public build logs.
