# Greffon Catalog

This repository contains the whitelisted greffon definitions — Docker Compose templates and configuration metadata for each deployable application on the Greffon platform.

## Structure

```
greffon-catalog/
├── <greffon-name>/
│   └── <version>/
│       ├── docker-compose.yml   # Standard Docker Compose file (no Jinja2 — see docs)
│       └── metadata.json        # Catalog entry: name, logo, description, configs, destinations
└── README.md
```

## Adding a New Greffon

1. Create a folder: `<greffon-name>/<version>/`
2. Add a `docker-compose.yml` — a standard Docker Compose file. See [How the Greffer Transforms Your Compose File](../docs/adding-a-greffon.md#how-the-greffer-transforms-your-compose-file) for what happens at deploy time.
3. Add a `metadata.json` describing the greffon and its configuration schema.
4. Open a PR for review.

See [How to Add a New Greffon](../docs/adding-a-greffon.md) for the full guide.

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

## Syncing to Manager Database

After merging, create entries in the manager backend via:

- **Django admin** at `/admin/greffonmanager/greffon/` — create the Greffon, GreffonVersion (pointing `compose_path` to the raw URL of the compose file), and GreffonVersionConfiguration records
- **Django fixture** — write a JSON fixture matching the manager models and load with `poetry run python manage.py loaddata <fixture>.json`
- **Django shell** — create records programmatically

See [How to Add a New Greffon](../docs/adding-a-greffon.md) for the full guide.

> **Note:** `metadata.json` is catalog documentation, not a Django fixture. The manager DB records must be created separately.
