---
name: add-greffon
description: Scaffold a new greffon catalog entry from an app name (e.g., /add-greffon ghost). Researches upstream docs, drafts compose + metadata + smoke test, and probes the real greffer to verify which configs are required.
user_invocable: true
---

# Add Greffon

Scaffold a new entry in the greffon catalog: research the upstream `docker-compose.yml` AND its configuration docs, transform the compose to platform rules, infer user-configurable settings, draft a Playwright smoke test, then verify which configs are truly required by deploying through a local greffer.

## Usage

```
/add-greffon <app-name> [version]
```

Default version: `1.0`. Examples:

- `/add-greffon ghost`
- `/add-greffon mealie 2.0`

## Prerequisites

- Run from inside the `greffon-catalog` repo (the skill checks for `_template/`, `playwright.config.ts`, `.github/scripts/validate_catalog.py`).
- **`python3` with `pyyaml`** on PATH (the validator imports `yaml`). On macOS the system Python doesn't ship pyyaml; offer the user `python3 -m venv .venv-validate && .venv-validate/bin/pip install pyyaml` as a one-shot, or use a project-level venv if one exists.
- **Docker daemon running.** If Docker is unavailable, Phase 7c (probe) is skipped with a warning; scaffolding still completes.
- **A built greffer image.** The greffer submodule's `docker-compose.yml` builds locally as `app` (no published `greffon/greffer:*` image exists). The skill auto-detects in this order: (1) `GREFFER_PROBE_IMAGE` env var if set; (2) `app` if `docker image inspect app` succeeds (the dev-stack default); (3) build it: `docker build -t app <path-to-greffer-submodule>`. Surface the resolved image name to the user.
- **Python `cryptography` package** for minting the throwaway probe cert (`python3 -c "import cryptography"` to check).
- **Node.js + Playwright browsers** for Phase 7d (live smoke). Run `npm install && npx playwright install chromium` in the catalog root once. If browsers aren't installed, Phase 7d is skipped with a warning; scaffolding still completes.

## Phases

### Phase 1: Parse and pre-flight

1. Parse `<app-name>` (lowercase, hyphenated) and optional version (default `1.0`).
2. Refuse if `<app-name>/<version>/` already exists. Tell the user to pick a different version or delete the existing folder.
3. Confirm the catalog-root markers are present (see Prerequisites).

### Phase 2: Research upstream — compose AND configuration docs

The compose file alone usually shows the bare minimum. The real config knobs live in README, install docs, and `.env.example`. Two passes:

**2a. Find the canonical sources.** Use WebSearch + WebFetch:

- The app's main GitHub repo.
- The repo's `docker-compose.yml` (try common paths: root, `docker/`, `examples/`, `install/`).
- `.env.example` / `.env.sample` / `env.example` — usually documents every env var with inline comments. Highest-signal source.
- `README.md`, `INSTALL.md`, `docs/install*.md`, `docs/configuration*.md`.
- The official self-hosting / Docker docs page (often linked from README).

For long files, ask WebFetch a focused question: *"List every environment variable this app reads, with its default and whether it is required."*

**2b. Build a config inventory.** Cross-reference sources to produce, for each env var:

- Name and target service.
- Required vs optional, secret vs non-secret. Look for "must set", "secret", "do not share", `writeOnly`-style language.
- Sensible default: URLs → `{{ instance_url }}`; SMTP → `{{ smtp.* }}`; everything else → upstream's documented default, or empty for required secrets.
- One-line description for the schema `title`, paraphrased from upstream.

**Show the user a summary table:** services found, env vars per service, which will become user configurations vs hardcoded defaults. Surface conflicts (compose says `DB_PASS`, README says `DATABASE_PASSWORD`) — don't silently pick one. Ask the user to confirm before continuing.

If no reliable upstream compose can be found, **stop and tell the user**. Do not invent a compose file.

### Phase 3: Transform the compose file

Start from `_template/<version>/docker-compose.yml`. Apply the rules from [docs/adding-a-greffon.md](../../../docs/adding-a-greffon.md) §1:

- Strip every `container_name`.
- Keep `ports:` lists — greffer reads, then strips them.
- Convert anonymous volumes to named volumes; declare them at top level.
- For env vars holding a public URL (callback bases, `*_BASE_URL`, `*_TRUSTED_DOMAINS`), substitute `{{ instance_url }}` or `{{ instance_host }}`.
- For SMTP-related env vars (`SMTP_*`, `MAIL_*`, `MAILER_*`), use the `{{ smtp.* }}` Jinja context. The `environment:` block on any service with SMTP must be **mapping form**, not list form. See the catalog `README.md` § "SMTP destinations" for shaping examples.
- Do NOT use Jinja vars outside the allowed set: `instance_id`, `instance_url`, `instance_host`, `instance_port`, `smtp.*`.
- **Hardcode operational defaults that aren't user knobs.** Vars with a single sensible value — `NODE_ENV=production`, `database__client=mysql`, fixed image flags, internal hostnames matching the service name (`DB_HOST=db`) — go in the compose `environment:` block as literal strings with NO matching configuration in `metadata.json`. A configuration is for things the user might reasonably want to change; everything else stays inline.

### Phase 4: Infer configurations

For each user-relevant env var from the Phase 2b inventory, draft a `GreffonVersionConfiguration`:

- `title`: short label.
- `schema`: JSON Schema with a single `value` property.
  - Secrets (matched by `password|secret|token|api[_-]?key|priv(ate)?[_-]?key`, case-insensitive) → set `writeOnly: true`, `minLength: 8`, and `required: ["value"]` at the schema root.
  - File uploads → `format: "data-url"`.
- `default_value`: `{ "value": "{{ instance_url }}" }` for URLs; `{ "value": "" }` for required secrets; documented upstream default otherwise.
- `destinations`: typically `[{ "type": "env", "container": "<service>", "key": "<ENV_KEY>" }]`.
  - `smtp` type for SMTP env keys.
  - `json` type for config files written into a volume.
  - `file` type for binary uploads.

For SMTP, emit ONE configuration with `title: "SMTP"`, empty schema/default, and one `smtp` destination per related env key. See `plausible/1.0/metadata.json` for the canonical shape.

**Linked secrets — one configuration, multiple destinations.** When the same value must appear in multiple env vars (typical: a DB password is set on the app service AND as `MYSQL_ROOT_PASSWORD` on the db service), emit ONE configuration with multiple destinations, NOT two separate configs. The user fills the value once and it lands in every wired-up env var:

```json
{
  "title": "DB_PASSWORD",
  "schema": { "type": "object", "required": ["value"], "properties": { "value": { "type": "string", "writeOnly": true, "minLength": 8 } } },
  "default_value": { "value": "" },
  "destinations": [
    { "type": "env", "container": "ghost", "key": "database__connection__password" },
    { "type": "env", "container": "db",    "key": "MYSQL_ROOT_PASSWORD" }
  ]
}
```

Detect link candidates by scanning for env vars whose names suggest the same secret (`*_PASSWORD` / `*_PASS` / `MYSQL_ROOT_PASSWORD` etc.) where the upstream's documented invariant is "set them to the same value." Surface ambiguous cases to the user before merging.

Reference shapes:
- `plausible/1.0/metadata.json` — templated `BASE_URL`, required secret, SMTP block.
- `nextcloud/1.0/metadata.json` — many configurations, trusted-domains via `{{ instance_host }}`.
- `vscode/1.0/metadata.json` — minimal entry, no configurations.

### Phase 5: Draft smoke test

Copy `_template/<version>/smoke_test.spec.ts`. Replace:

- The env-var name to `<APP_NAME>_URL` (uppercase, hyphens → underscores).
- `test.describe('TEMPLATE', ...)` → the app's display name.
- The body assertion with reasonable selectors based on what the app renders (a known heading, a login-form input). Mark every guess with `// TODO:` and explain why.

The smoke test is not run automatically by this skill. The user runs Playwright after the live deploy (Tier 2, separate skill). The goal here is a working starting point.

### Phase 6: Write files

Write all three files to `<app-name>/<version>/`:

- `docker-compose.yml`
- `metadata.json`
- `smoke_test.spec.ts`

### Phase 7: Validate (three passes)

**7a. Static validator.**

```bash
python3 .github/scripts/validate_catalog.py --dir <app-name>/<version>
```

If `python3` errors with `ModuleNotFoundError: No module named 'yaml'`, set up a one-shot venv: `python3 -m venv .venv-validate && .venv-validate/bin/pip install pyyaml && .venv-validate/bin/python .github/scripts/validate_catalog.py --dir <app-name>/<version>` (and clean it up after).

If it fails, parse error messages and self-correct:

- *"looks like a secret but no default and not required"* → add `"required": ["value"]` at schema root, OR set `"x-greffon-allow-empty-secret": true` if intentional (e.g. any-of auth like OpenClaw).
- *"top-level volume X declared but never mounted"* → add the mount to a service or remove the volume.
- *"destination references container X not found"* → fix the container name in `destinations`.
- *"SMTP env key declared in metadata but not in compose" / "compose env references smtp.* but metadata has no smtp destination"* → reconcile both sides.
- *"destination uses list-form environment"* → convert that service's `environment:` to mapping form.

Loop up to 3 times. Surface remaining errors to the user.

**7b. Cross-checks.** Parse both files yourself and verify:

- Every `destinations[].container` is a service in the compose file.
- Every `destinations[].volume` is a top-level named volume.
- Every Jinja `{{ var }}` outside `{{ smtp.* }}` references one of `instance_id`, `instance_url`, `instance_host`, `instance_port`.
- Every env-type destination's `key` is actually a key in the target service's `environment:` block (catches `DB_PASS` vs `DB_PASSWORD` typos).

If anything fails, surface the offending line and stop.

**7c. Empirical config probe — verify what's actually required, via the real greffer.**

Docs lie. The only reliable signal for "is this config required?" is to deploy without it and see what breaks. Drive the greffer over HTTP — no manager involved.

**Skip conditions** (with clear warning, then proceed to Phase 8):

- Docker daemon not running.
- No greffer image resolvable (see Prerequisites — print the build command for the user).
- `cryptography` Python module not importable.

**Setup (once per skill run):**

1. **Resolve the greffer image.** Try, in order:
   - `$GREFFER_PROBE_IMAGE` if set.
   - `app` (the dev-stack default tag — built by the greffer submodule's `docker-compose.yml`).
   - Stop with the build command if neither exists.

2. **Pick a host-from-container alias for the local HTTP server.** Detect once at setup, reuse for all probes:
   - On macOS / Docker Desktop, `host.docker.internal` resolves automatically.
   - On Linux, it doesn't unless you pass `--add-host=host.docker.internal:host-gateway` to the greffer container.
   - Test by running `docker run --rm --add-host=host.docker.internal:host-gateway alpine getent hosts host.docker.internal` once. If it resolves, always pass the flag (no-op on Docker Desktop, required on Linux). Use `host.docker.internal` in the compose URL.

3. **Pick a greffer.** Reuse a running probe greffer if `GREFFER_PROBE_URL` env var is set; otherwise:
   ```bash
   docker run -d --rm --name greffer-probe-<rand> \
     -p <free-host-port>:8000 \
     -e GREFFER_TOKEN=<random-32-char> \
     -e GREFFER_WORKERS_ENABLED=false \
     -e GREFFER_ID=<random-uuid> \
     -e GREFFON_PATH=/tmp/greffon-data \
     --add-host=host.docker.internal:host-gateway \
     -v /var/run/docker.sock:/var/run/docker.sock \
     <resolved-image>
   ```
   Notes:
   - **Internal port is 8000** (not 8001 — the `8001` in the greffer's own compose is the TLS-terminating sibling nginx, not the FastAPI app).
   - **`GREFFER_TOKEN`** drives auth (header is `X-GREFFON-TOKEN`, env var is `GREFFER_TOKEN`).
   - **`GREFFER_ID`** is required (no default in the pydantic settings); a random UUID is fine.
   - **`GREFFON_PATH`** is where the greffer writes per-instance compose / volume metadata. `/tmp/greffon-data` is fine for a probe.
   - **`GREFFER_WORKERS_ENABLED=false`** skips the register/monitor/CRL loops that would otherwise try to call a manager.

   Wait up to 30s for the greffer's healthcheck at `GET /healthz` (NOT `/health/`).

4. **Serve the catalog locally.** Start `python3 -m http.server <free-port>` in the catalog root in the background. Compose URL: `http://host.docker.internal:<port>/<app>/<version>/docker-compose.yml`.

5. **Mint a throwaway cert** using the `cryptography` Python lib (self-signed RSA-2048, CN matches the instance id). The greffer expects an mTLS cert in the payload — it's for the deployed instance's nginx, not for greffer auth, and any well-formed cert PEM is accepted.

6. **Cleanup hook.** Register `atexit` + SIGINT handler that always `docker rm -f greffer-probe-<rand>`, kills the HTTP server, and `docker rm -f` any leftover instance containers from probes.

**Baseline run.** POST `/api/controller/start/` with the new compose URL, all configurations at their drafted defaults, throwaway cert, and instance id `probe-baseline-<rand>`. Headers: `X-GREFFON-TOKEN: <the-token-we-set>`. Wait for HTTP success, then poll `GET /api/controller/greffon/<instance-id>/` (the greffer's own status endpoint, [greffer/app/routers/controller.py:200](../../../greffer/app/routers/controller.py:200)) until every service is `running` for 60s or timeout. If that endpoint isn't returning what you need, fall back to `docker ps --filter "label=com.docker.compose.project=<instance-id>"`. If baseline fails, surface the greffer's error response and **stop** — the drafted compose is broken. Tear down via `POST /api/controller/stop/`.

**Per-config probe loop.** Cap at 8 ambiguous-required configs from Phase 2b. For each:

1. Build a fresh `/start/` payload with that configuration's value omitted (or empty string for secrets). New instance id `probe-<config-name>-<rand>`.
2. POST. Poll the greffer status endpoint for up to 30s. Capture `docker logs` from the namespaced containers.
3. Classify:
   - **HTTP 4xx/5xx from greffer** → config is **required**. Mark `schema.required: ["value"]` in metadata.
   - **All services running, no restart loop after 30s, no error logs** → config is **optional**. Leave out of `schema.required`, keep the documented default.
   - **Service starts but enters restart loop OR logs an obvious error** → mark **probably required**, surface the offending log line, do NOT auto-promote.
   - **Conflict between docs and probe.** If Phase 2b's docs research said "required" but the probe found the container boots fine without it (typical for SMTP, optional plugins, feature flags), **prefer the docs**: keep `required: ["value"]`, and note in the report "docs override probe — boots without value but feature is documented as required." The probe only verifies boot, not feature correctness.
4. Tear down via `/api/controller/stop/`. `docker rm -f` any stragglers labeled with this instance id.
5. Print live progress: `[3/5] probing ADMIN_PASSWORD … required (greffer rejected: missing required value)`.

If more than 8 ambiguous configs exist, surface the rest as `// TODO: probe manually` comments in metadata.json.

After the probe loop, **re-run 7a + 7b** to ensure the metadata edits from probing didn't break static validation.

**Caveats** to surface alongside results:
- Probe verifies "container survives boot under the greffer pipeline" — not "feature works." A misconfigured SMTP greffon boots fine; mail just won't send. Phase 7d (smoke test) is the next layer of defense.
- The probe greffer has `GREFFER_WORKERS_ENABLED=false`, so the monitor callback loop doesn't run. This is intentional and safe for one-shot deploys.

**7d. Live smoke test — run Playwright against a real deploy.**

Final layer: the contributor's `smoke_test.spec.ts` runs against an actual deployed instance, so we know the app responds, not just that the container booted. Reuses the probe's machinery — no manager involved.

**Skip conditions** (with clear warning, then proceed to Phase 8):

- Phase 7c was skipped (no Docker / no greffer image / no `cryptography`).
- `node_modules/@playwright/test` not present in the catalog root (print `npm install && npx playwright install chromium`).
- `~/.cache/ms-playwright/chromium-*` not present (print `npx playwright install chromium`).

**Steps:**

1. **Reuse the greffer + HTTP server** from Phase 7c setup (don't tear them down between 7c and 7d — the cleanup hook still owns them).

2. **Single deploy with port-mismatch awareness.** The greffer's port allocator (see [`apps/utils/os/network.py:get_free_ports`](../../../greffer/apps/utils/os/network.py)) asks the kernel for a fresh port via `bind(0)` then closes the socket. Once a deploy is torn down, the kernel holds the port in TIME_WAIT and **won't reassign it for ~60-120s**, so a "discover-then-redeploy" two-step never converges — verified empirically with Ghost (4 deploys, 4 different ports). Just deploy once with a placeholder `url` (e.g. `https://localhost`) and use the assigned `port_host` for Playwright.

   The smoke test must be written to **avoid routes that depend on the configured URL matching the request URL**. Specifically: Ghost, Nextcloud, and similar apps issue a canonical-URL 301 from `/` to whatever `url` config says — when the configured URL doesn't match the assigned port, that redirect lands somewhere dead. Write smoke tests against **API endpoints that return JSON** — those are exempt from the canonical-URL redirect because they're not HTML routes. *Don't* assume admin SPA paths like `/ghost/` or `/admin` are safe — Ghost 5.x redirects those too (verified empirically). Pick a public-facing JSON endpoint that returns a stable shape on a fresh install — e.g. for Ghost: `/ghost/api/admin/authentication/setup/` returns `{"setup":[{"status":false}]}` before any admin signs up. The Ghost catalog entry [smoke_test.spec.ts](../../ghost/1.0/smoke_test.spec.ts) is the canonical example.

   *This is a known limitation of the greffer's port allocator. If it grows a "respect requested port_host" mode, this section can be simplified.*

   **Known greffer-side bug (work around in the smoke test if you hit it):** the auto-generated `greffon_nginx` doesn't forward `X-Forwarded-Proto: https`. Apps like Ghost behind the SSL-terminating sibling nginx see plain HTTP and 301-loop every HTML route to itself indefinitely. JSON API endpoints aren't affected because they don't issue protocol-upgrade redirects. If you observe the loop, log it as a follow-up issue against the greffer's nginx template (in `greffer/apps/utils/conf/`); don't try to work around it inside the catalog entry.

3. **Wait for the deployed instance to actually serve traffic.** The greffer's status endpoint reports "running" before nginx has bound the host port (its own [`_wait_for_compose_running`](../../../greffer/app/routers/controller.py:137) only waits for compose state, not port bind). The reliable signal is **TLS handshake completes** — poll `requests.get(smoke_url, verify=False, timeout=2)` until any HTTP response comes back, OR until **180s** elapses (DB-backed apps like Ghost+MySQL routinely take 90-120s on cold image pull). The Playwright config has `ignoreHTTPSErrors: true`, so the self-signed cert is fine.

4. **Run Playwright** scoped to just this greffon's spec:
   ```bash
   <APP_NAME>_URL="https://localhost:<port_host>" \
     npx playwright test --config=playwright.config.ts <app>/<version>/smoke_test.spec.ts
   ```
   `<APP_NAME>_URL` is the env var name the smoke test reads (uppercase app name, hyphens → underscores, suffixed `_URL`). The skill set this name in Phase 5; reuse the same string here.

5. **Capture the result.** Pass / fail / timeout. Don't loop or retry on failure (Playwright config already has `retries: 1`). On failure, surface the path to `.playwright/report/index.html` and the per-test artifact dir (videos, screenshots, trace) under `.playwright/results/<test-id>/`.

6. **Tear down** the smoke instance via `/api/controller/stop/`. The skill's atexit hook cleans up the greffer container + HTTP server.

**Behavior:**

- **Test passes** → record a checkmark in the Phase 8 report. The contributor knows defaults work end-to-end against the real greffer pipeline.
- **Test fails** → record the failure verbatim and continue to Phase 8. **Non-blocking.** A common cause is the contributor hasn't replaced the template's placeholder selector yet — the report flags this so they know to fix the spec, not the metadata.
- **Greffer rejects the deploy** → likely a regression introduced by Phase 7c's auto-edits to `metadata.json`. Surface the greffer's error; the user re-runs `/add-greffon` after fixing.

**Caveats:**
- Smoke runs against a freshly-deployed instance with empty data. Tests that assume "an admin already exists" or "this content is seeded" will fail — write smoke tests that handle a clean install (e.g., assert the setup wizard appears).
- If a smoke test creates an admin via a setup wizard, pick a random-looking password (e.g. `K3l9-XmzQr-7Bv-Tw`), not one containing the substring `password` or any dictionary word. Ghost, Nextcloud, GitLab and others reject "weak" passwords with no way to disable the check, and the rejection looks like a generic form error — easy to misdiagnose.
- The smoke greffer has `GREFFER_WORKERS_ENABLED=false`, so any feature that depends on the monitor callback (status updates, restart-on-crash detection) won't behave like production. This is fine for a one-shot smoke; flag it if a test relies on it.

### Phase 8: Report

Print:

1. Summary of what was created — services, configurations, which were probed and which were docs-derived.
2. List of remaining `// TODO:` markers (smoke test selectors, logo URL, description copy if placeholder).
3. The probe results table (config / source / decision / log excerpt if relevant).
4. **Smoke test result** — pass / fail / skipped, with the verbatim failure or the path to the Playwright HTML report on failure. Failure is informational only, not a block on the PR.
5. The PR command, **without running it**:
   ```
   git checkout -b feat/add-<app>-greffon
   git add <app>/<version>/
   git commit -m "feat: add <app> to catalog"
   gh pr create --title "Add <app> to catalog" --body "..."
   ```

## Constraints

- **Never** generate Django fixtures for the manager DB. Catalog and manager are decoupled.
- **Never** push the branch or open the PR. Stop at the diff and the suggested commands.
- **Never** invent a compose file when upstream lookup fails.
- **Never** modify existing entries. Only write to `<app-name>/<version>/`.
- **Never** leave probe containers / volumes / HTTP server / greffer container behind on exit, even on failure.
