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

Most browser-facing specs read `process.env.<GREFFON>_URL`:

| Greffon | Env var |
|---|---|
| Freqtrade | `FREQTRADE_URL` |
| GlitchTip | `GLITCHTIP_URL` |
| Nextcloud | `NEXTCLOUD_URL` |
| OpenClaw | `OPENCLAW_URL` |
| Plausible | `PLAUSIBLE_URL` |
| VS Code | `VSCODE_URL` |

The GitHub Actions Runner greffon is headless and does not expose an HTTP URL.
Its spec is skipped by default. To verify a deployed runner through GitHub's
API, set:

| Variable | Description |
|---|---|
| `GITHUB_RUNNER_SMOKE_TOKEN` | PAT that can list self-hosted runners for the target repo or org |
| `GITHUB_RUNNER_SMOKE_SCOPE` | `repo` or `org`; defaults to `repo` |
| `GITHUB_RUNNER_SMOKE_REPO` | `owner/repo`, required for repo scope |
| `GITHUB_RUNNER_SMOKE_ORG` | Organization name, required for org scope |
| `GITHUB_RUNNER_SMOKE_NAME_PREFIX` | Required unique runner name prefix for the deployment under test |
| `GITHUB_RUNNER_SMOKE_HOST` | GitHub host; defaults to `github.com` |

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

## GitHub Actions Runner notes

Self-hosted GitHub Actions runners execute workflow code on your greffer host.
Only attach them to trusted private repositories or controlled organizations,
and avoid exposing them to untrusted pull requests.

The catalog entry uses one grouped install form so the UI can validate the
required auth and scope combinations before deployment. It supports two GitHub
auth modes:

- `RUNNER_TOKEN`: the short-lived registration token from GitHub's standard
  `./config.sh --url ... --token ...` flow. This is convenient for manual
  first-start testing, but it expires quickly and must be refreshed before a
  later reconfigure or restart.
- `ACCESS_TOKEN`: a PAT used by the container to mint fresh registration
  tokens automatically. This is the recommended mode for durable catalog
  installs.

For repo scope PAT auto-registration, use a PAT from a user with admin access
to the repository; fine-grained PATs need repository Administration
read/write permission. Classic PATs typically need `repo` for private
repositories or `public_repo` for public repositories. For org scope, use an
organization owner/admin PAT with self-hosted runner management permission;
classic PATs typically need `admin:org`.

Runner jobs that use Docker are served by a Docker-in-Docker sidecar. The
catalog intentionally does not mount the host Docker socket because greffer
rewrites compose volumes and host socket mounts would also grant broad host
control to jobs.

## CI infrastructure note

The Integration Test job in `.github/workflows/validate-greffon.yml` deploys
each changed greffon through the **public `greffon/greffer`** and runs its
smoke spec against the live instance — see `.github/scripts/ci_greffer_smoke.py`.

It needs **no private repo and no PAT**: the greffer is the component that does
the real render + deploy (verbatim service names, stripped networks,
per-instance nginx with X-Forwarded-Proto, SMTP injection, secret rendering —
where catalog bugs actually live). The thin manager role at deploy time (mint a
token, issue a cert, POST the `/start` payload) is reproduced by the runner from
each entry's own `metadata.json`:

- boots the public greffer with a known `GREFFER_TOKEN` (the greffer exposes
  this env "primarily for tests");
- self-signs a per-instance cert (Playwright ignores cert errors);
- builds `configurations[]` from `metadata.json`, generating values for
  `greffon-secret`/`greffon-secret-alnum` fields;
- sends an empty `ports` map so the greffer's dev/test fallback builds
  `instance_url = https://<host>:<port>` (with the real port — no portless
  placeholder), then runs the entry's Playwright spec against it.

The job is a **hard gate**: a failing smoke blocks merge (proven end-to-end in
CI on a static app and a stateful/volume app). It runs only when a PR changes a
greffon entry. If a legitimately heavy greffon needs more than the 45-minute
timeout, raise the timeout rather than weakening the gate. Static Validation +
the regression suite gate every PR regardless.

## What the linter catches before merge

See `.github/scripts/tests_validate_catalog.py` for the full list with one
test per check. Every check has a regression test that reproduces a real
bug we shipped before the linter existed.
