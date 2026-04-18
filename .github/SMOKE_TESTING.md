# Catalog smoke testing

Every greffon directory ships with a `smoke_test.spec.ts` Playwright spec that
runs a real-user happy path against a deployed instance. The same specs run
locally (`./scripts/setup-dev.sh` + `npx playwright test`) and in CI on every
PR that touches a greffon.

## What a good smoke spec covers

- Loads the greffon's URL with `ignoreHTTPSErrors: true` (greffer-issued certs).
- Drives the **login or first-user-creation flow** using credentials the
  install dialog would have set (the metadata `default_value` for everything
  except secrets, plus a known password the CI workflow injects).
- Asserts at least one **post-auth** signal: dashboard URL change, an API
  call returning `is_authenticated: true`, etc. **Don't** stop at "login form
  rendered" — that wouldn't have caught any of the bugs we shipped.
- Times out after 60s on individual actions, 180s on the full test.

## Reading the spec

Each spec reads `process.env.<GREFFON>_URL`:

| Greffon | Env var |
|---|---|
| Freqtrade | `FREQTRADE_URL` |
| GlitchTip | `GLITCHTIP_URL` |
| Nextcloud | `NEXTCLOUD_URL` |
| OpenClaw | `OPENCLAW_URL` |
| Plausible | `PLAUSIBLE_URL` |
| VS Code | `VSCODE_URL` |

The local dev workflow (greffon root `scripts/setup-dev.sh` + the install
dialog or the API) deploys an instance, the greffer assigns a port, and
you set the env var to `https://127.0.0.1:<port>`.

## Running locally

```bash
cd greffon-catalog
npm install
npx playwright install chromium

# After deploying the greffon you want to test:
GLITCHTIP_URL=https://127.0.0.1:46991 npx playwright test glitchtip/1.0/smoke_test.spec.ts
```

## Running everything against a fresh dev env

```bash
# 1. From the greffon repo root:
./scripts/setup-dev.sh

# 2. Deploy each greffon via the install dialog OR via this catalog repo's
#    helper (see scripts/deploy-all.sh — TODO if not present).

# 3. Run the suite:
cd greffon-catalog
npx playwright test
```

## Adding a new greffon

1. `metadata.json` + `docker-compose.yml` (existing requirements).
2. **`smoke_test.spec.ts` is now also required** — the linter
   (`.github/scripts/validate_catalog.py`) refuses any greffon without one.
3. Use a sibling greffon's spec as a template. Pick the closest match:
   - server-rendered login form → `nextcloud/1.0/smoke_test.spec.ts`
   - SPA with allauth/CSRF → `glitchtip/1.0/smoke_test.spec.ts`
   - JSON-only API → `freqtrade/1.0/smoke_test.spec.ts`
4. Run `npx playwright test <your-greffon>/<version>/smoke_test.spec.ts`
   against a local deploy until it passes.
5. Add a row to the env-var table above.

## What the linter catches before merge

See `.github/scripts/tests_validate_catalog.py` for the full list with one
test per check. Every check has a regression test that reproduces a real
bug we shipped before the linter existed.
