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

If a catalog template needs the host portion (or `host:port`) of the URL rather than the full URL, use Jinja string ops on `instance_url` at the call site rather than expecting a separate variable. The most common pattern:

```jinja
# Just the host[:port] part — what a browser sends in the `Host:` header.
{{ instance_url.split('://')[1] }}
```

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
| `json` | `volume`, `name`                | Write JSON file into a named volume      |
| `file` | `volume`, `name`                | Write uploaded file into a named volume  |
| `smtp` | `container`, `key`              | Mark an env key as SMTP-integration-managed (value comes from the operator's SMTP integration, not user input) |

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
      SMTP_HOST_SSL_ENABLED: "{{ 'true' if smtp.tls_mode == 'tls' else 'false' }}"
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

## CI Quality Gate

Every PR to this repo runs `.github/scripts/validate_catalog.py`, which enforces:

- `metadata.json` schema validity (required fields, types, destination shape)
- No phantom-required fields (every `required` property in a schema must actually exist in `properties`)
- No empty-string defaults on required file uploads
- No hard-coded secrets in default values (opt out with `x-greffon-allow-empty-secret: true` when a field is legitimately empty by default and the user must fill it)
- No reserved-TLD email defaults (e.g. `.local`, `.test`) that break downstream validators
- No dangling volume references — every volume used in a destination must be declared in the compose `volumes:` block
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
