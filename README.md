# Greffon Catalog

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-join-5865F2.svg)](https://discord.gg/vBmhUGPY)

This repository contains the whitelisted greffon definitions — Docker Compose templates and configuration metadata for each deployable application on the [Greffon](https://greffon.io) platform.

**License:** Apache 2.0 (see [LICENSE](LICENSE)). The catalog is permissive so anyone can contribute and copy a recipe without license friction. Greffon's product code (the manager and greffer) is AGPL v3 — the catalog is content (recipes for other people's apps), not product features, so it's permissive. This is not "open core."

**Contributing:** see [CONTRIBUTING.md](CONTRIBUTING.md). DCO sign-off required (`git commit -s`). New greffon? Use `/add-greffon <name>` in Claude Code (fast path) or follow the manual steps in [Adding a New Greffon](#adding-a-new-greffon) below.

**Community:** [Discord](https://discord.gg/vBmhUGPY) · bugs/new-greffon requests in this repo's [Issues](https://github.com/greffon/greffon-catalog/issues) · [Code of Conduct](CODE_OF_CONDUCT.md)

**Security:** report privately via [GitHub Security Advisories](https://github.com/greffon/greffon-catalog/security/advisories/new) or `security@greffon.io`. See [SECURITY.md](SECURITY.md).

---

## Structure

```
greffon-catalog/
├── <greffon-name>/
│   └── <version>/
│       ├── docker-compose.yml   # Docker Compose file (Jinja2 allowed for approved instance vars)
│       ├── metadata.json        # Catalog entry: name, logo, description, configs, destinations
│       └── smoke_test.spec.ts   # Playwright spec — real user-task assertion for this greffon
└── README.md
```

## Adding a New Greffon

**Fast path: `/add-greffon <name>` in Claude Code.** The skill at [.claude/skills/add-greffon.md](.claude/skills/add-greffon.md) researches the upstream `docker-compose.yml` and config docs, drafts all three required files, runs the validator, and probes a local greffer to verify which configurations are actually required. You review the diff and open the PR.

**Manual path:**

1. Copy [`_template/1.0/`](_template/) to `<greffon-name>/<version>/` and fill in the `TODO:` comments.
2. The folder must contain `docker-compose.yml`, `metadata.json`, and `smoke_test.spec.ts` — see [Jinja Template Vars](#jinja-template-vars-in-docker-composeyml) and [metadata.json Format](#metadatajson-format) below for the deploy-time transformation rules and the Jinja2 vars you can use.
3. Run `python .github/scripts/validate_catalog.py --dir <greffon-name>/<version>` until it exits 0.
4. Open a PR. CI runs the validator and the smoke spec against a real dev environment.

The rest of this README is the full guide: [Jinja Template Vars](#jinja-template-vars-in-docker-composeyml), [metadata.json Format](#metadatajson-format), [Destination Types](#destination-types), and the [CI Quality Gate](#ci-quality-gate).

## Jinja Template Vars in `docker-compose.yml`

The greffer renders each catalog `docker-compose.yml` as a Jinja2 template at deploy time. These instance-scoped variables are available to you:

| Variable            | Value at deploy time                                       | Use case                                                    |
|---------------------|------------------------------------------------------------|-------------------------------------------------------------|
| `{{ instance_id }}` | Short UUID of this greffon instance (e.g. `e71c060d`)      | Per-instance keys, filenames                                |
| `{{ instance_url }}` | Full public URL where browsers reach this instance (e.g. `https://abc.my.greffon.local`). | OAuth callback base, app-self-URL env vars, anywhere a full URL is needed. |
| `{{ instance_host }}` | Host only, no scheme and **no port** (e.g. `abc.my.greffon.local`). | Anything matched against the `Host:` header the app receives, such as a trusted-domain or allowed-hosts list. |
| `{{ instance_port }}` | The port, or empty when the URL uses the default. | Rarely needed on its own; prefer `instance_url`. |

`instance_url` is the source of truth and the greffer's own guidance prefers deriving
from it at the call site. `instance_host` and `instance_port` are parsed from it and
are kept for the cases where a bare host is what the app wants. This table previously
listed only the first two, while `add-greffon.md` and the baked-file render context
both listed all four, so the same variable was documented as available in one place
and absent in another.

To take the host portion from `instance_url` directly, the common pattern is:

```jinja
# The host[:port] part, for BUILDING a URL or a WebSocket origin.
{{ instance_url.split('://')[1] }}

# The bare host, for anything MATCHED AGAINST the Host header the app receives.
{{ instance_url.split('://')[1].split(':')[0] }}
```

**Pick by what consumes the value, not by what the browser sends.** The browser does
send `host:port`, but the per-instance sidecar proxies with `proxy_set_header Host
$host`, and nginx's `$host` carries no port. So an app validating an incoming Host
sees the bare host, and a host allowlist built from the first form matches nothing
and rejects every request, on any deployment whose URL has a port.

| Setting | Form | Why |
|---|---|---|
| `DJANGO_ALLOWED_HOSTS`, `NEXTCLOUD_TRUSTED_DOMAINS` | bare host | compared against the Host the app receives |
| `N8N_HOST`, `FORGEJO__server__DOMAIN` | `host[:port]` | used to generate URLs |
| `COLLABORATION_WS_URL`, `LIVEKIT_API_URL` | full URL | they are URLs |

Declaring both forms is fine and is the safest default for an allowlist, since it
holds whichever arrives.

CI checks this for a deliberately short list of settings it knows by name
(`DJANGO_ALLOWED_HOSTS`, `NEXTCLOUD_TRUSTED_DOMAINS`), because how an allowlist is
parsed is a property of the app, not something a rule can infer. Django splits on
commas and treats a leading dot as a subdomain pattern; Nextcloud splits on any
shell whitespace and has no such pattern. **Adding a new app's allowlist means
adding one entry to `_HOST_ALLOWLISTS` in `validate_catalog.py`**, keyed by
`(<app>, <ENV_KEY>)`. Only `split` is required; `trim`, `prefixes` and `wildcards`
describe what else that app's parser does and each defaults to nothing when
omitted.
Until it is there, the setting is not checked, which is why the guidance above
matters more than the check: three entries shipped the port-only form and rejected
every request until it was found.

This works whether the URL has an explicit port (`https://example.com:8443`) or uses the default (`https://abc.my.greffon.local`). The catalog stays declarative, with a single source-of-truth Jinja variable, and there's no cross-PR contract about pre-parsed pieces for a reviewer to track. The `_template/` reference compose has an example.

Volumes you declare are automatically namespaced by instance id — a volume named `db-data` in your compose becomes `<instance_id>_db-data` at runtime, so two instances of the same greffon on one greffer never share data.

Nginx-internal template vars like `{{ports[i].port_host}}` are reserved for the auto-added `greffon_nginx` service. Don't use them in your own services.

## metadata.json Format

```json
{
  "name": "My App",
  "logo": "https://example.com/logo.png",
  "description": "Short description of the app",
  "categories": ["category1"],
  "images": ["https://example.com/screenshot.png"],
  "configurations": [
    {
      "title": "Config Section Title",
      "schema": { },
      "default_value": { },
      "destinations": [
        { "type": "env", "container": "service_name", "key": "ENV_VAR" }
      ]
    }
  ]
}
```

### Destination Types

| Type   | Fields                          | Description                              |
|--------|---------------------------------|------------------------------------------|
| `env`  | `container`, `key`              | Inject as environment variable           |
| `json` | `volume`, `name`, *(opt)* `x-greffon-render` | Write JSON file into a named volume      |
| `file` | `volume`, `name`, *(opt)* `x-greffon-render` | Write uploaded/baked file into a named volume |
| `smtp` | `container`, `key`              | Mark an env key as SMTP-integration-managed (value comes from the operator's SMTP integration, not user input) |
| `oidc` | `container`, `key`              | Mark an env key as OIDC-integration-managed (same, for the operator's OIDC provider) |

The optional **`x-greffon-render: true`** on a `file`/`json` destination Jinja-renders the file contents at deploy time. See [Baked config files](#baked-config-files-visibility--render-time-templating).

#### Integration-managed destinations (`smtp`, `oidc`)

These two carry **no user value**. The operator configures the integration once
on the Integrations page, and the greffer fills the env key from that blob at
deploy time. Your `metadata.json` declares which env key is managed; the value
never comes from the install form.

The blob is also exposed to `docker-compose.yml` as a Jinja variable, so you can
build a value out of several fields:

| Variable | Fields available today |
|----------|------------------------|
| `{{ smtp.* }}` | `host`, `port`, `username`, `password`, `from_address`, `tls_mode` |
| `{{ oidc.* }}` | `issuer` |

`oidc` is issuer-only on purpose right now; per-instance client credentials
arrive with the client-registration work and are not available yet. Do not
write an entry that needs `oidc.client_id`.

**When the operator has NOT linked an integration**, the greffer removes every
env key that reads it, so the container starts without the variable rather than
with an empty one. That matters: a half-interpolated `smtp://:@:` is worse than
an absent `EMAIL_URL`, because the app parses it at boot.

**Do not rely on the unset branch rendering.** A key you declare a destination
for may be removed outright, whatever its value: the greffer strips
metadata-declared keys by destination, before it looks at the template at
all. So `{% if smtp %}{{ smtp.host }}{% else %}localhost{% endif %}` is not a
way to get `localhost` -- the whole key can go. Put a fallback in the image,
or in a separate non-integration config.

Several shipping entries do use a test-only value for an `*_ENABLED` flag
(`{{ "true" if smtp.host else "false" }}`), and it does currently survive and
render `false`. That works because the manager materialises no config row for
an empty-schema integration config, so there is nothing for the
destination-driven strip to act on. It is a consequence of two components'
current behaviour rather than a contract, so treat a rendered fallback as a
bonus, not something to design around.

`|default` behaves differently on the blob than on a field: the blob is defined
but empty, so `{{ oidc|default('x') }}` renders `{}`, while
`{{ oidc.issuer|default('x') }}` renders `x`.

### Port Exposure Tiers (L4)

By default every compose-exposed port is **Tier A**: the greffer strips it from the host and serves it through the per-instance nginx sidecar (TLS). An optional top-level `ports` list annotates individual ports to expose them as raw **Tier C (L4)** TCP/UDP instead, published directly on the greffer host (proxy mode) or the rathole relay (tunnel mode), bypassing nginx.

```json
{
  "min_greffer_version": "0.3.3",
  "ports": [
    { "name": "wg-easy_51820", "exposure_tier": "l4", "protocol": "udp", "udp_reviewed": true, "same_port": true },
    { "name": "wg-easy_51821", "exposure_tier": "http", "protocol": "tcp" }
  ]
}
```

| Field            | Values                          | Meaning                                                                                                  |
|------------------|---------------------------------|----------------------------------------------------------------------------------------------------------|
| `name`           | `{service}_{container_port}`    | Required. Matches the port the same way the importer derives it (last `published:container` pair). A name matching no exposed port is ignored, or rejected for `same_port` (see below). |
| `exposure_tier`  | `"http"` (default), `"l4"`      | `http` is nginx-fronted Tier A. `l4` publishes the raw port; nginx does not proxy it.                     |
| `protocol`       | `"tcp"` (default), `"udp"`      | Raw transport for an `l4` port.                                                                           |
| `udp_reviewed`   | boolean                         | A UDP `l4` port is default-denied by the manager unless set `true`. Set it only after confirming the protocol emits no response before authentication, so it cannot be used for reflection/amplification (record the rationale in a `review_note`). |
| `same_port`      | boolean (`l4` only)             | The greffer publishes the container side on the advertised port number so advertise == listen == public in both proxy and tunnel modes. For apps that bake their advertised endpoint into client configs (e.g. WireGuard). Requires `min_greffer_version >= 0.3.3`. |

`min_greffer_version` (top-level, optional) makes the manager refuse to start the greffon on an older greffer. It is required at `>= 0.3.3` whenever any port sets `same_port` (the floor that covers both the proxy and tunnel datapaths).

### Hot Backup (`backup.volumes` + dump/restore hooks)

By default an instance is **unclassified**, and a backup is COLD: the greffer stops the instance,
snapshots its volumes, and starts it again. Classifying volumes opts the entry into HOT backup (no
downtime), and what that requires depends on the classes you use:

| Entry shape | Needs | Greffer floor | Below the floor |
|---|---|---|---|
| only `data` / `regenerable` volumes | just `backup.volumes`, **no hooks** | >= 0.8.0 | falls back to COLD |
| any `database` volume | `backup.volumes` **and** a dump + restore hook on that service | >= 0.9.0 | falls back to COLD |

The fallback is deliberate: an older worker takes a COLD backup rather than failing, so an entry
declaring hooks still works everywhere, just without the no-downtime path.

Two constraints on the hook command itself, both enforced by CI:

- **It is exec'd with no shell.** A pipe, a redirect or a `$VAR` is handed to the program as a
  literal argument. Write `$$VAR` so compose passes the `$` through rather than interpolating it
  away, and when you need a shell use exactly one of these two forms, with nothing after the script:

  ```
  sh -c '<script>'
  busybox sh -c '<script>'
  ```

  Shell options are not accepted around the `-c`: put `set -eu` inside the script instead. The
  narrowness is deliberate, so that what the shell interprets is never in question.
- **Its service must run exactly one container, and keep running.** The greffer finds the hook by
  looking at running containers, so a service behind a `profiles:` never starts (no_dump_hook), one
  with `replicas: 2` presents the label twice (multiple_database_unsupported), and a one-shot that
  exits after deploy has nothing left to exec into (no_dump_hook).
- **No Jinja, and no leading `VAR=value`.** The greffer renders the compose file before deploying,
  so CI reads the template and the runtime gets whatever it rendered to. And an environment
  assignment is shell syntax: with no shell, `docker exec` looks for a program by that name. Put
  the variable in the service's `environment:`, which `docker exec` inherits and which keeps it off
  the command line.
- **It must mount the `database` volume, and must not mount a `data` one.** The first loses data:
  the hot path snapshots only `data` volumes, so a `database` volume on any other service is
  captured by nothing at all. The second blocks recovery: the restore guard reads database volumes from
  docker state, so any volume on the dump service counts as database state; if it is also classed
  `data` it sits in both sets and the restore refuses with db_volume_misclassified. Both fail with
  every backup still reporting success.

The two halves live in different files and the platform reads them from different places, which is
exactly why they drift, and why CI now checks they agree.

**metadata.json** classifies every volume:

```json
{
  "backup": { "volumes": { "postgres_data": "database" } }
}
```

| Class | Meaning |
|-------|---------|
| `data` | live-snapshot the volume with restic |
| `regenerable` | skip it; the app rebuilds it on start |
| `database` | do NOT snapshot; capture it with the dump hook below |

**docker-compose.yml** carries the hooks, as SERVICE labels on the database service, plus a
`healthcheck` the greffer's restore waits on before streaming the dump back in:

```yaml
  postgresql:
    labels:
      com.greffon.backup.dump: "pg_dump -U app -d app -Fc"
      com.greffon.backup.restore: "pg_restore -U app -d app --clean --if-exists --no-owner --single-transaction"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U app -d app"]
```

Hooks run as argv (never a shell), so DB credentials must come from the container's environment
rather than the command line. See `umami/1.0` and `keycloak/1.0` for worked examples.

**The two halves must agree, and CI now enforces it.** Declaring hooks without `backup.volumes` is
the trap: the manager reads classes only from metadata, so the instance stays unclassified, the
backup silently falls back to COLD, and the hooks are never invoked. That shipped once, review-ready
and validator-green, before this check existed. The validator now rejects hooks without a block, a
`database` class without both hooks, more than one `database` volume or hook (the greffer's hot path
is single-DB), a classified volume that is not a top-level compose volume, and a hook service with
no healthcheck.

### Custom Schema Formats

The platform reads JSON Schema's `format` keyword to dispatch special handling for fields whose intent goes beyond plain validation. Custom formats use the `greffon-` prefix to avoid collision with standard JSON Schema formats (`email`, `uri`, `date-time`, …) and with vendor formats from other tools.

| Format             | Intent                                                                                                                                                                                       | Required schema keywords        |
|--------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------|
| `greffon-secret`   | Cryptographic secret the platform generates and persists at instance creation. The user never sees an empty input — manager mints a URL-safe base64 value of exactly `minLength` chars and stores it on `GreffonInstanceConfiguration` before the install form opens. The frontend renders the field password-masked with a regenerate button. Use for `SECRET_KEY_BASE`-style values where the underlying greffon enforces a minimum entropy (e.g. Plausible's 32-byte floor) and no human-typed value can satisfy it.                                            | `type: "string"`, `minLength`, `writeOnly: true` |
| `greffon-secret-alnum` | Same as `greffon-secret`, but generated from a strict **`[A-Za-z0-9]`** alphabet (no `-`/`_`). Use for greffons whose own startup validator rejects the URL-safe-base64 characters — e.g. Activepieces' `AP_ENCRYPTION_KEY` requires `^[A-Za-z0-9]{32}$`, so a base64 key fails ~64% of the time. Identical shape rules and UI (masked + regenerate) as `greffon-secret`. | `type: "string"`, `minLength`, `writeOnly: true` |

Example:

```json
{
  "title": "SECRET_KEY_BASE",
  "schema": {
    "type": "object",
    "properties": {
      "value": {
        "type": "string",
        "title": "Secret key base",
        "writeOnly": true,
        "minLength": 64,
        "format": "greffon-secret"
      }
    },
    "required": ["value"]
  },
  "default_value": { "value": "" },
  "destinations": [
    { "type": "env", "container": "plausible", "key": "SECRET_KEY_BASE" }
  ]
}
```

`default_value.value` stays empty — the value source is the manager, not the catalog. Setting `format: "greffon-secret"` without `minLength` is a validator error (the platform needs an explicit length).

#### SMTP destinations

An `smtp` destination declares that a given env var on a given service is **driven by the operator's SMTP integration**, not by per-instance user input. The value is rendered at deploy time from the greffer-side Jinja context variable `smtp` — a dict with fields `host`, `port`, `username`, `password`, `from_address`, `tls_mode` (`"none"` / `"starttls"` / `"tls"`). Write the shaping expression inline in the compose file's `environment:` mapping:

```yaml
services:
  app:
    environment:
      SMTP_HOST_ADDR: "{{ smtp.host }}"
      SMTP_HOST_PORT: "{{ smtp.port }}"
      SMTP_USER_NAME: "{{ smtp.username }}"
      SMTP_USER_PWD: "{{ smtp.password }}"
      MAILER_EMAIL: "{{ smtp.from_address }}"
      SMTP_HOST_SSL_ENABLED: '{{ "true" if smtp.tls_mode == "tls" else "false" }}'
```

The `metadata.json` entry for the same config section:

```json
{
  "title": "SMTP",
  "schema": { "properties": {} },
  "default_value": {},
  "destinations": [
    { "type": "smtp", "container": "app", "key": "SMTP_HOST_ADDR" },
    { "type": "smtp", "container": "app", "key": "SMTP_HOST_PORT" }
  ]
}
```

Notes:

- `schema` stays empty (`{"properties": {}}`) and `default_value` stays empty (`{}`) — SMTP is not user-configurable at instance-creation time; the value source is the operator's integration.
- When an instance is deployed **without** an SMTP integration selected, the greffer removes each metadata-declared `smtp` env key from the rendered compose before starting the instance. The env var is absent in the container rather than empty-string noise, regardless of how Jinja rendered the expression.
- The compose `environment:` must be mapping form (`KEY: value`) on every service that has an `smtp` destination; list form (`["KEY=value", ...]`) is rejected by the validator because the bidirectional Jinja check can't inspect list entries cleanly.
- Value shaping (booleans, tri-state strings, composed URLs) lives in the compose Jinja, not in a named transform — see the Plausible / Nextcloud / GlitchTip entries for worked examples.

#### Baked config files: visibility & render-time templating

Two optional flags let a greffon ship a config file that is **baked** (the operator never sees or edits it) and **per-instance** (its contents are templated at deploy time). Use them for internal plumbing like a reverse-proxy config or an identity-realm import.

**`x-greffon-visibility`** — declared **inside a configuration's `schema`** (a config-root key is silently dropped on ingestion, and the validator rejects it there):

| Value | Effect |
|-------|--------|
| `visible` (default) | Rendered normally in the install form. |
| `advanced` | Rendered, but tucked inside a collapsed "Advanced settings" section. Still editable. Use for power-user knobs. |
| `hidden` | Never rendered. The manager forces the value to the catalog `default_value` server-side (non-tamperable), so a `hidden` config **must** ship a complete `default_value`. |

**`x-greffon-render: true`** — on a `file`/`json` destination, Jinja-renders the file contents on the greffer before it is written into the volume. The render context is the same as the compose: `instance_id`, `instance_url`, `instance_host`, `instance_port`, plus a **`config`** namespace exposing every `env`-destination value by its key. So a baked file can scope itself to the instance and embed a per-install secret that matches the container's env var:

```jsonc
// metadata.json — a hidden, render-flagged Keycloak realm + the minted secret it embeds
{
  "title": "OIDC client secret",
  "schema": { "properties": { "value": {
    "type": "string", "writeOnly": true, "minLength": 32, "format": "greffon-secret" } } },
  "default_value": { "value": "" },
  "destinations": [{ "type": "env", "container": "backend", "key": "OIDC_RP_CLIENT_SECRET" }]
},
{
  "title": "Identity realm import",
  "schema": { "x-greffon-visibility": "hidden",
              "properties": { "file": { "type": "string", "format": "data-url" } } },
  "default_value": { "file": "data:application/json;base64,<base64 of the realm template>" },
  "destinations": [{ "type": "file", "volume": "kc_import", "name": "realm.json", "x-greffon-render": true }]
}
```

The realm template (before base64-encoding) references the shared context:

```jsonc
"redirectUris": ["{{ instance_url }}/*"],
"webOrigins": ["{{ instance_url }}"],
"secret": "{{ config.OIDC_RP_CLIENT_SECRET }}"
```

Rules and gotchas (all validator-enforced):

- **Strict rendering.** A render-flagged file is rendered with Jinja `StrictUndefined`: a missing/typo'd variable (e.g. `{{ config.OIDC_CLIENT_SECRET }}` missing the `_RP`) **fails the deploy loudly** rather than baking an empty secret. Use the plain attribute form `{{ config.X }}` — the validator rejects `{{ config.get('X') }}` and `| default` in a render-flagged file because they silently bypass the strict check.
- **JSON safety.** Rendering is plain string substitution (no auto-escaping). A value that lands in a JSON string position and may contain `"`/`\` must be wrapped with `| tojson` (e.g. `"secret": {{ config.X | tojson }}`) so the output stays valid JSON.
- **`{{ config.X }}` must match an `env` destination.** The file and the container read the same value by key; a reference with no matching `env` key is a validator error.
- **No integration namespaces in a render-flagged file.** `{{ smtp.* }}` (and other integration namespaces) are rejected: an unset integration renders to `{}` and would hard-abort the deploy.
- **UTF-8 only.** A render-flagged file's `default_value.file` must decode as valid UTF-8 (it is rendered as text). Non-text/binary files must not set `x-greffon-render`.
- **Brace safety.** Avoid Jinja-colliding braces in the file body; in particular, Keycloak's own `${...}` placeholders must not sit adjacent to `{`/`}`.
- **Rollout.** Render-flagged greffons require a render-capable greffer. Upgrade workers before publishing a render-flagged catalog entry (ship order: greffer → manager/front → catalog).

## Cookie security and why CI does not use `localhost`

Every instance is served over TLS by the greffer's per-instance nginx sidecar, which
terminates TLS and proxies **plain HTTP** upstream. Unless an app is told it sits behind a
TLS-terminating proxy, it can emit session cookies without `Secure` and downgrade
`SameSite=None` to `Lax`.

That is not theoretical. The Keycloak entry shipped this way briefly: with `KC_PROXY_HEADERS`
unset, `AUTH_SESSION_ID` and `KC_RESTART` came back `SameSite=Lax` with no `Secure`. A session
cookie without `Secure` can ride a plaintext request, and `Lax` breaks iframe SSO.

**CI could not see it**, for two reasons that both had to be fixed. It deployed every instance
at `https://localhost:<port>`, and the discriminator here is the **host**, not the scheme: apps
applying the W3C potentially-trustworthy-origin rule server-side (Keycloak's
`SecureContextResolver` accepts `scheme == https` **or** a loopback host) treat localhost as
trusted however they are configured. And nothing looked at the cookies: the Keycloak spec
exercised discovery and a password grant, neither of which sets one. Demonstrated directly, same
entry and same assertion, only the host differing:

| `CI_PUBLIC_HOST` | Result |
|---|---|
| `localhost` | PASS (regression invisible) |
| a real hostname | FAIL: `session cookie AUTH_SESSION_ID is HttpOnly but not Secure` |

So the smoke harness now defaults `CI_PUBLIC_HOST` to `catalog-ci.test`, which the workflow
maps to `127.0.0.1` in `/etc/hosts`. Running the harness locally needs the same mapping, or
`CI_PUBLIC_HOST=localtest.me` (a public name that resolves to loopback); the harness fails fast
with instructions if the name does not resolve. **Do not set it back to `localhost`.**

The harness also applies a generic check after each deploy: it polls the instance root until it
answers non-5xx (compose reporting `running` is not the same as the app serving, and a 502 carries
no cookies), follows the redirect chain on the same host, and fails the entry if any cookie in that
chain is `HttpOnly` but not `Secure`.

**Do not mistake it for coverage.** It was measured across the catalog and it sees very little:

| entry | root | cookies the generic check can see |
|---|---|---|
| freshrss | 302 → 200 | 1 (`HttpOnly`, `Secure`, `SameSite`) |
| stirling-pdf | 200 | none |
| memos | 200 | none |
| vscode | 200 | none |
| uptime-kuma | 302 → 200 | none |
| keycloak | 302 → console | none |

Most apps set their session cookie on a login or authorization endpoint, not on the landing page,
so the generic check simply has nothing to look at. That is why `keycloak/1.0/smoke_test.spec.ts`
carries its own assertion against the authorization endpoint, and why **a per-entry assertion is
the real mechanism**. Treat the generic check as a cheap catch for the minority of apps that do set
a cookie on `/`, not as a reason to skip writing one.

Scope an audit by the right rule. The hostname matters only for apps applying the secure-context
rule to the **host**. A framework keying purely on the scheme is unaffected by it: Django's
`HttpRequest.is_secure()` is `return self.scheme == "https"` with no loopback carve-out, so a
Django greffon emits the same flags either way and needs its proxy and scheme settings checked
directly.

If your app has sessions, assert this in your `smoke_test.spec.ts` rather than relying on the
floor:

```ts
const setCookie = r.headersArray().filter((h) => h.name.toLowerCase() === 'set-cookie');
// Assert the list is non-empty FIRST. Without this the loop below passes happily
// when the response sets no cookies at all, which is the same vacuous-green shape
// this whole section exists to remove: point it at the endpoint that really sets
// your session cookie, not just at `/`.
expect(setCookie.length, 'expected this endpoint to set cookies').toBeGreaterThan(0);
for (const { value } of setCookie) {
  const attrs = value.split(';').slice(1).map((a) => a.trim().split('=')[0].toLowerCase());
  if (attrs.includes('httponly')) {
    expect(value, `cookie ${value.split('=')[0]} is HttpOnly but not Secure`).toMatch(/;\s*Secure/i);
  }
}
```

## CI Quality Gate

Every PR to this repo runs `.github/scripts/validate_catalog.py`, which enforces:

- `metadata.json` schema validity (required fields, types, destination shape)
- No phantom-required fields (every `required` property in a schema must actually exist in `properties`)
- No empty-string defaults on required file uploads
- No hard-coded secrets in default values (opt out with `x-greffon-allow-empty-secret: true` when a field is legitimately empty by default and the user must fill it)
- No reserved-TLD email defaults (e.g. `.local`, `.test`) that break downstream validators
- No dangling volume references — every volume used in a destination must be declared in the compose `volumes:` block
- Baked-config-files flags: valid `x-greffon-visibility` (inside `schema`, not at config root; `hidden` requires a complete default), `x-greffon-render` only on `file`/`json` and boolean, and for a render-flagged file: UTF-8-decodable default, no integration-namespace references, and every `{{ config.X }}` matched by an `env` destination key
- L4 `ports[]` shape (valid `exposure_tier` / `protocol`, boolean `udp_reviewed` / `same_port`, and `same_port` only on an `l4` port)
- A `same_port` port requires `min_greffer_version >= 0.3.3`, and its `name` must match a port the compose actually exposes (otherwise the greffer rewrite targets nothing)

Plus the Playwright `smoke_test.spec.ts` runs against a real dev environment and must deploy the greffon from defaults and assert the primary user task.

Both must pass before merge.

## Syncing to Manager Database

After merging, create entries in the manager backend via:

- **Django admin** at `/admin/greffonmanager/greffon/` — create the Greffon, GreffonVersion (pointing `compose_path` to the raw URL of the compose file), and GreffonVersionConfiguration records
- **Django fixture** — write a JSON fixture matching the manager models and load with `poetry run python manage.py loaddata <fixture>.json`
- **Django shell** — create records programmatically

> **Note:** `metadata.json` is catalog documentation, not a Django fixture. The manager DB records must be created separately.
