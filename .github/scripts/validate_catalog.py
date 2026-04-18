#!/usr/bin/env python3
"""
Static validator for greffon catalog entries.

Validates metadata.json schema, docker-compose.yml structure,
and cross-references between them (destinations vs services/volumes).

Usage:
    python validate_catalog.py --all              # Validate every greffon
    python validate_catalog.py --dir plausible/1.0 # Validate one greffon
"""

import argparse
import json
import os
import re
import sys

import yaml

# Names that strongly imply a secret value the user must supply.
SECRET_NAME_RE = re.compile(r"(?i)password|secret(?!_id)|token|api[_-]?key|priv(?:ate)?[_-]?key")

# Reserved/special-use TLDs that Python's email-validator (and most others) reject.
# Catches the GlitchTip-style `admin@greffon.local` regression.
RESERVED_TLDS = {"local", "localhost", "test", "example", "invalid", "internal"}

REQUIRED_FILES = ["metadata.json", "docker-compose.yml", "smoke_test.spec.ts"]

METADATA_REQUIRED_FIELDS = ["name", "description", "configurations"]

VALID_DESTINATION_TYPES = {"env", "json", "file"}
DESTINATION_REQUIRED_KEYS = {
    "env": {"type", "container", "key"},
    "json": {"type", "volume", "name"},
    "file": {"type", "volume", "name"},
}


def find_catalog_root():
    """Catalog root is two levels up from this script."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


SKIP_TOP_LEVEL = {"node_modules", "playwright-report", "playwright-results", ".playwright"}


def find_all_greffon_dirs(catalog_root):
    """Find all {name}/{version}/ directories."""
    dirs = []
    for name in sorted(os.listdir(catalog_root)):
        name_path = os.path.join(catalog_root, name)
        if not os.path.isdir(name_path) or name.startswith(".") or name in SKIP_TOP_LEVEL:
            continue
        for version in sorted(os.listdir(name_path)):
            version_path = os.path.join(name_path, version)
            if os.path.isdir(version_path):
                dirs.append(os.path.join(name, version))
    return dirs


def validate_greffon_dir(catalog_root, rel_dir):
    """Validate a single greffon directory. Returns list of error strings."""
    errors = []
    abs_dir = os.path.join(catalog_root, rel_dir)

    if not os.path.isdir(abs_dir):
        return [f"{rel_dir}: directory does not exist"]

    # --- Required files ---
    for fname in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(abs_dir, fname)):
            errors.append(f"{rel_dir}: missing required file '{fname}'")

    # --- Validate docker-compose.yml ---
    compose_path = os.path.join(abs_dir, "docker-compose.yml")
    compose = None  # so the cross-checks below can guard on truthiness
    compose_services = set()
    compose_volumes = set()

    if os.path.isfile(compose_path):
        try:
            with open(compose_path) as f:
                compose = yaml.safe_load(f)
        except yaml.YAMLError as e:
            errors.append(f"{rel_dir}: docker-compose.yml is not valid YAML: {e}")
            return errors

        if not isinstance(compose, dict):
            errors.append(f"{rel_dir}: docker-compose.yml root must be a mapping")
        elif "services" not in compose:
            errors.append(f"{rel_dir}: docker-compose.yml missing 'services' key")
        else:
            services = compose["services"]
            if not isinstance(services, dict) or not services:
                errors.append(f"{rel_dir}: docker-compose.yml 'services' must be a non-empty mapping")
            else:
                compose_services = set(services.keys())

                # Check no service uses container_name
                for svc_name, svc_def in services.items():
                    if isinstance(svc_def, dict) and "container_name" in svc_def:
                        errors.append(
                            f"{rel_dir}: service '{svc_name}' must not use 'container_name' "
                            "(greffer assigns names dynamically)"
                        )

        # Collect top-level volumes
        if isinstance(compose, dict) and "volumes" in compose:
            vols = compose.get("volumes")
            if isinstance(vols, dict):
                compose_volumes = set(vols.keys())

    # --- Validate metadata.json ---
    meta_path = os.path.join(abs_dir, "metadata.json")
    if not os.path.isfile(meta_path):
        return errors

    try:
        with open(meta_path) as f:
            meta = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"{rel_dir}: metadata.json is not valid JSON: {e}")
        return errors

    if not isinstance(meta, dict):
        errors.append(f"{rel_dir}: metadata.json root must be an object")
        return errors

    # Required fields
    for field in METADATA_REQUIRED_FIELDS:
        if field not in meta:
            errors.append(f"{rel_dir}: metadata.json missing required field '{field}'")

    # Name and description must be non-empty strings
    for field in ("name", "description"):
        val = meta.get(field)
        if val is not None and (not isinstance(val, str) or not val.strip()):
            errors.append(f"{rel_dir}: metadata.json '{field}' must be a non-empty string")

    # Categories and images must be lists
    for field in ("categories", "images"):
        val = meta.get(field)
        if val is not None and not isinstance(val, list):
            errors.append(f"{rel_dir}: metadata.json '{field}' must be a list")

    # Cross-check: top-level volumes must be referenced by at least one service mount.
    if isinstance(compose, dict) and compose_volumes:
        used_volumes = set()
        for svc_def in (compose.get("services") or {}).values():
            if not isinstance(svc_def, dict):
                continue
            for vol_entry in svc_def.get("volumes") or []:
                if isinstance(vol_entry, str) and ":" in vol_entry:
                    used_volumes.add(vol_entry.split(":", 1)[0])
        for vol_name in compose_volumes - used_volumes:
            errors.append(
                f"{rel_dir}: docker-compose.yml top-level volume '{vol_name}' is "
                "declared but never mounted by a service"
            )

    # Configurations
    configs = meta.get("configurations")
    if configs is not None and not isinstance(configs, list):
        errors.append(f"{rel_dir}: metadata.json 'configurations' must be a list")
    elif isinstance(configs, list):
        for i, cfg in enumerate(configs):
            prefix = f"{rel_dir}: configurations[{i}]"

            if not isinstance(cfg, dict):
                errors.append(f"{prefix} must be an object")
                continue

            title = cfg.get("title", "")
            if "title" not in cfg:
                errors.append(f"{prefix} missing 'title'")

            # --- Schema sanity (regression: Freqtrade phantom-required fields) ---
            schema = cfg.get("schema") or {}
            schema_required = list(schema.get("required") or [])
            schema_props = (schema.get("properties") or {}).keys()
            default_value = cfg.get("default_value") or {}
            for req_field in schema_required:
                if req_field not in schema_props:
                    errors.append(
                        f"{prefix} schema.required '{req_field}' has no matching "
                        "entry in schema.properties"
                    )
                if isinstance(default_value, dict) and req_field not in default_value:
                    errors.append(
                        f"{prefix} schema.required '{req_field}' has no entry in default_value"
                    )

            # --- Email-format sanity (regression: admin@greffon.local rejected by Pydantic) ---
            for prop_name, prop in (schema.get("properties") or {}).items():
                if not isinstance(prop, dict) or prop.get("format") != "email":
                    continue
                if not isinstance(default_value, dict):
                    continue
                default_email = default_value.get(prop_name, "")
                if isinstance(default_email, str) and "@" in default_email:
                    tld = default_email.rsplit(".", 1)[-1].lower()
                    if tld in RESERVED_TLDS:
                        errors.append(
                            f"{prefix} default email '{default_email}' uses reserved/special-use "
                            f"TLD '.{tld}'; some validators (Pydantic, email-validator) reject it"
                        )

            if "destinations" not in cfg:
                errors.append(f"{prefix} missing 'destinations'")
                continue

            destinations = cfg["destinations"]
            if not isinstance(destinations, list):
                errors.append(f"{prefix} 'destinations' must be a list")
                continue

            for j, dest in enumerate(destinations):
                dprefix = f"{prefix}.destinations[{j}]"

                if not isinstance(dest, dict):
                    errors.append(f"{dprefix} must be an object")
                    continue

                dtype = dest.get("type")
                if dtype not in VALID_DESTINATION_TYPES:
                    errors.append(
                        f"{dprefix} invalid type '{dtype}' "
                        f"(must be one of {sorted(VALID_DESTINATION_TYPES)})"
                    )
                    continue

                # Check required keys for this destination type
                required_keys = DESTINATION_REQUIRED_KEYS[dtype]
                missing = required_keys - set(dest.keys())
                if missing:
                    errors.append(f"{dprefix} missing keys: {sorted(missing)}")

                # Cross-reference: env destinations must reference a valid service
                if dtype == "env" and compose_services:
                    container = dest.get("container", "")
                    if container and container not in compose_services:
                        errors.append(
                            f"{dprefix} references container '{container}' "
                            f"not found in docker-compose.yml services: "
                            f"{sorted(compose_services)}"
                        )

                # Cross-reference: json/file destinations reference a volume
                if dtype in ("json", "file") and compose_volumes:
                    vol = dest.get("volume", "")
                    if vol and vol not in compose_volumes:
                        errors.append(
                            f"{dprefix} references volume '{vol}' "
                            f"not found in docker-compose.yml volumes: "
                            f"{sorted(compose_volumes)}"
                        )

            # --- Per-config rules that need all destinations + schema in scope ---
            schema_required_set = set(schema_required)

            # Rule: file-type destinations must have either a default file or be required.
            # (Regression: Freqtrade Strategy / Plausible clickhouse-* crashed greffer with KeyError.)
            for dest in destinations:
                if not isinstance(dest, dict) or dest.get("type") != "file":
                    continue
                has_default_file = isinstance(default_value, dict) and bool(default_value.get("file"))
                if not has_default_file and "file" not in schema_required_set:
                    errors.append(
                        f"{prefix} file destination has no default_value.file AND no "
                        "schema.required: ['file']; greffer will crash with KeyError on empty install"
                    )
                    break

            # Rule: configs whose title or any env-key looks like a secret must be required
            # OR have a non-empty default. Catches "user installs with empty password,
            # service silently broken or insecure".
            looks_like_secret = bool(SECRET_NAME_RE.search(title))
            for dest in destinations:
                if isinstance(dest, dict) and SECRET_NAME_RE.search(dest.get("key", "")):
                    looks_like_secret = True
                    break
            if looks_like_secret:
                marked_required = "value" in schema_required_set
                has_meaningful_default = (
                    isinstance(default_value, dict)
                    and isinstance(default_value.get("value"), str)
                    and default_value.get("value", "").strip() != ""
                )
                # Escape hatch for "any-of" auth (e.g. OpenClaw needs ANTHROPIC_API_KEY
                # OR OPENAI_API_KEY, neither alone is required). Set this flag in
                # metadata.json on the config to silence the lint and rely on a custom
                # smoke test to verify the user-supplied any-of constraint.
                opt_out = bool(cfg.get("x-greffon-allow-empty-secret"))
                if not marked_required and not has_meaningful_default and not opt_out:
                    errors.append(
                        f"{prefix} '{title}' looks like a secret (password/token/key) but is "
                        "neither marked schema.required ['value'] nor given a non-empty default. "
                        "Set 'x-greffon-allow-empty-secret: true' on the config if this is "
                        "intentional (e.g. any-of auth)."
                    )

    # Smoke test (separate file, optional but validated if present)
    smoke_path = os.path.join(abs_dir, "smoke_test.json")
    if os.path.isfile(smoke_path):
        try:
            with open(smoke_path) as f:
                smoke_test = json.load(f)
        except json.JSONDecodeError as e:
            errors.append(f"{rel_dir}: smoke_test.json is not valid JSON: {e}")
            return errors

        prefix = f"{rel_dir}: smoke_test.json"
        if not isinstance(smoke_test, dict):
            errors.append(f"{prefix} root must be an object")
        else:
            if "path" not in smoke_test:
                errors.append(f"{prefix} missing 'path'")
            elif not isinstance(smoke_test["path"], str) or not smoke_test["path"].startswith("/"):
                errors.append(f"{prefix} 'path' must be a string starting with '/'")

            if "expected_status" not in smoke_test:
                errors.append(f"{prefix} missing 'expected_status'")
            elif not isinstance(smoke_test["expected_status"], list):
                errors.append(f"{prefix} 'expected_status' must be a list of HTTP status codes")
            elif not all(isinstance(s, int) for s in smoke_test["expected_status"]):
                errors.append(f"{prefix} 'expected_status' must contain integers only")

            body = smoke_test.get("expected_body_contains")
            if body is not None and not isinstance(body, str):
                errors.append(f"{prefix} 'expected_body_contains' must be a string or null")

            required_config = smoke_test.get("required_config")
            if required_config is not None and not isinstance(required_config, dict):
                errors.append(f"{prefix} 'required_config' must be an object or null")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate greffon catalog entries")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Validate all greffons")
    group.add_argument("--dir", type=str, help="Validate a specific greffon dir (e.g. plausible/1.0)")
    args = parser.parse_args()

    catalog_root = find_catalog_root()
    all_errors = []

    if args.all:
        dirs = find_all_greffon_dirs(catalog_root)
        if not dirs:
            print("WARNING: No greffon directories found")
            sys.exit(0)
        print(f"Validating {len(dirs)} greffon(s)...")
        for d in dirs:
            errors = validate_greffon_dir(catalog_root, d)
            all_errors.extend(errors)
    else:
        print(f"Validating {args.dir}...")
        errors = validate_greffon_dir(catalog_root, args.dir)
        all_errors.extend(errors)

    if all_errors:
        print(f"\nVALIDATION FAILED ({len(all_errors)} error(s)):\n")
        for err in all_errors:
            print(f"  ERROR: {err}")
        sys.exit(1)
    else:
        print("\nAll validations passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
