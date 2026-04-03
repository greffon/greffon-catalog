# Greffon Catalog Validation

This directory contains CI workflows and scripts to validate greffon catalog entries, both automatically on PRs and manually before submitting.

## CI Validation (Automatic)

Every pull request to `main` triggers the **Validate Greffon Catalog** workflow with two jobs:

### Static Validation

Runs on every PR. Checks all greffons in the catalog for:

- **File structure**: each `{name}/{version}/` dir must contain `metadata.json` and `docker-compose.yml` (plus optional `smoke_test.json`)
- **Compose validity**: valid YAML, non-empty `services`, no hardcoded `container_name`
- **Metadata schema**: required fields (`name`, `description`, `configurations`), correct types
- **Configuration destinations**: valid `type` (env/json/file), required keys per type
- **Cross-references**: `env` destinations reference services that exist in the compose file; `json`/`file` destinations reference volumes that exist in the compose file

### Integration Test

Only runs when greffon files actually changed. For each changed greffon:

1. Spins up the manager (Django with SQLite + mocked Vault) and a real greffer
2. Imports the greffon into the catalog database
3. Creates an instance (with `required_config` from `smoke_test.json` if needed) and starts it on the greffer
4. Polls until the instance reaches **STARTED** status (120s timeout)
5. **Smoke test**: if `smoke_test.json` exists, hits the greffon's HTTP endpoint and verifies it responds correctly (expected status code, expected body content)
6. Stops the instance and moves to the next greffon

If any greffon fails to start or fails its smoke test, the workflow fails with a detailed error message.

### Smoke Test File (`smoke_test.json`)

Optional file that defines how to verify a greffon actually works after deployment:

```json
{
  "path": "/status.php",
  "expected_status": [200],
  "expected_body_contains": "installed",
  "required_config": {
    "BASE_URL": "http://localhost:8000"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `path` | Yes | HTTP path to hit (e.g. `/`, `/api/v1/ping`) |
| `expected_status` | Yes | List of acceptable HTTP status codes |
| `expected_body_contains` | No | String to search for in the response body (case-insensitive), `null` to skip |
| `required_config` | No | Config values needed for the greffon to start (key = config title, value = config value) |

### Reading CI Failure Logs

- **Static validation failures**: look for `ERROR:` lines with the specific check that failed (e.g., `missing required file`, `references container 'foo' not found in services`)
- **Integration test failures**: look for `FAIL:` lines. Common causes:
  - Image pull timeout (increase timeout or use smaller images)
  - Compose syntax issues that YAML parsing doesn't catch (e.g., invalid `depends_on`)
  - Services that crash on startup (check container logs in the workflow output)

## Manual Validation (Before PR)

Run these checks locally before submitting a PR to catch issues early.

### Step 1: Run Static Validation

```bash
# Validate your specific greffon
python .github/scripts/validate_catalog.py --dir mygreffon/1.0

# Or validate everything
python .github/scripts/validate_catalog.py --all
```

Requires Python 3.9+ and `pyyaml`:
```bash
pip install pyyaml
```

### Step 2: Test Compose Locally

Verify your services start without errors:

```bash
docker compose -f mygreffon/1.0/docker-compose.yml up
```

Check that:
- All services start without crashing
- No missing images or build errors
- Services can communicate with each other

### Step 3: Check Ports

At least one service must expose a port. The greffer reads these ports to set up the nginx reverse proxy:

```yaml
services:
  myapp:
    image: myapp:latest
    ports:
      - "8080:80"   # Required: greffer uses this
```

### Step 4: Verify Configurations

For each entry in `metadata.json` `configurations`:

- **`env` destinations**: the `container` field must match a service name in `docker-compose.yml`
- **`json`/`file` destinations**: the `volume` field must match a top-level volume name in `docker-compose.yml`
- **`schema`** should be valid JSON Schema (the manager-front uses RJSF to render forms from it)
- **`default_value`** should match the schema structure

### Step 5: Full Stack Test (Optional)

For the most thorough validation, test with the full greffon platform:

```bash
# From the main greffon repo
./scripts/setup-dev.sh

# Your greffon will be imported automatically if it's in the catalog
# Then test it via the UI at https://app.greffon.local
```

## PR Checklist

Copy this into your PR description:

```markdown
### Greffon Validation Checklist

- [ ] `metadata.json` and `docker-compose.yml` present in `{name}/{version}/`
- [ ] `docker compose up` starts without errors
- [ ] Configuration destinations reference valid services/volumes
- [ ] No hardcoded `container_name` in compose
- [ ] Named volumes used (not bind mounts)
- [ ] At least one service exposes a port
- [ ] `smoke_test.json` added with path, expected status, and body check
- [ ] `python .github/scripts/validate_catalog.py --dir {name}/{version}` passes
```

## File Structure

Each greffon version directory contains:

```
mygreffon/1.0/
  docker-compose.yml   # What to deploy (services, ports, volumes)
  metadata.json        # Catalog entry (name, logo, configs for the UI)
  smoke_test.json      # How to verify it works (HTTP check for CI)
```

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/validate_catalog.py` | Static validation of metadata + compose + smoke_test |
| `scripts/test_greffon.py` | Integration test: deploy, start, and smoke test the greffon |
