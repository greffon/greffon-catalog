# Greffon Catalog

This repository contains the whitelisted greffon definitions — Docker Compose templates and configuration metadata for each deployable application on the Greffon platform.

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

1. Create a folder: `<greffon-name>/<version>/`
2. Add a `docker-compose.yml` — see [How the Greffer Transforms Your Compose File](../docs/adding-a-greffon.md#how-the-greffer-transforms-your-compose-file) for what happens at deploy time and which Jinja2 template vars are available.
3. Add a `metadata.json` describing the greffon and its configuration schema.
4. Add a `smoke_test.spec.ts` — a Playwright spec that deploys the greffon with default config and asserts the real user-landing-task works (e.g. "admin can log in", "uploaded file persists", "API endpoint returns 200").
5. Open a PR for review. CI runs `validate_catalog.py` and the smoke spec against a real dev environment.

See [How to Add a New Greffon](../docs/adding-a-greffon.md) for the full guide.

## Jinja Template Vars in `docker-compose.yml`

The greffer renders each catalog `docker-compose.yml` as a Jinja2 template at deploy time. These instance-scoped variables are available to you:

| Variable            | Value at deploy time                                       | Use case                                                    |
|---------------------|------------------------------------------------------------|-------------------------------------------------------------|
| `{{ instance_id }}` | Short UUID of this greffon instance (e.g. `e71c060d`)      | Per-instance keys, filenames                                |
| `{{ instance_url }}` | Full public URL where browsers reach this instance        | OAuth callback base, app-self-URL env vars                  |
| `{{ instance_host }}` | Hostname portion of `instance_url`                       | `ALLOWED_HOSTS`, trusted-domain lists (`NEXTCLOUD_TRUSTED_DOMAINS`) |
| `{{ instance_port }}` | Dynamically-allocated host port for this instance        | Rare — most apps don't need it                              |

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

Plus the Playwright `smoke_test.spec.ts` runs against a real dev environment and must deploy the greffon from defaults and assert the primary user task.

Both must pass before merge.

## Syncing to Manager Database

After merging, create entries in the manager backend via:

- **Django admin** at `/admin/greffonmanager/greffon/` — create the Greffon, GreffonVersion (pointing `compose_path` to the raw URL of the compose file), and GreffonVersionConfiguration records
- **Django fixture** — write a JSON fixture matching the manager models and load with `poetry run python manage.py loaddata <fixture>.json`
- **Django shell** — create records programmatically

See [How to Add a New Greffon](../docs/adding-a-greffon.md) for the full guide.

> **Note:** `metadata.json` is catalog documentation, not a Django fixture. The manager DB records must be created separately.
