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
from datauri import DataURI
from datauri.exceptions import InvalidDataURI

# Names that strongly imply a secret value the user must supply.
SECRET_NAME_RE = re.compile(r"(?i)password|secret(?!_id)|token|api[_-]?key|priv(?:ate)?[_-]?key")

# Reserved/special-use TLDs that Python's email-validator (and most others) reject.
# Catches the GlitchTip-style `admin@greffon.local` regression.
RESERVED_TLDS = {"local", "localhost", "test", "example", "invalid", "internal"}

# Detects a Jinja fragment that references the `smtp` context variable — the
# signal that a compose env value is SMTP-managed. The match is scoped to the
# *inside of a `{{ ... }}` expression block* and is *case-sensitive*:
#
#   - A reference must appear INSIDE a `{{ ... }}`: the value
#     `"{{ instance_url }} smtp.host"` is rejected because `smtp.host` sits
#     outside any Jinja expression and would render literally.
#   - The identifier must be lowercase `smtp.<field>`: Jinja variable lookup
#     is case-sensitive, so `{{ SMTP.host }}` would render undefined and is
#     rejected at lint time.
#   - A word boundary before `smtp` prevents `{{ notsmtp.host }}` from matching.
#
# The `(?:(?!\}\}).)*?` tempered-greedy token matches any char that isn't the
# start of `}}`, so dict literals inside the expression (e.g. Nextcloud's
# `{{ {'tls': 'ssl', ...}[smtp.tls_mode] }}`) are admitted while the match
# still can't cross an expression boundary.
_SMTP_JINJA_RE = re.compile(
    r"\{\{(?:(?!\}\}).)*?\bsmtp\.[a-z_][a-z0-9_]*(?:(?!\}\}).)*?\}\}"
)


def _value_references_smtp(value) -> bool:
    """Returns True iff the compose value contains a `{{ smtp.<field> }}`
    Jinja expression that reads from the SMTP integration context.

    The match is scoped to the *inside of a Jinja expression block* —
    `"{{ instance_url }} smtp.host"` is rejected because `smtp.host` is
    outside any `{{ }}` and would render literally.

    The match is *case-sensitive* because Jinja variable lookup is
    case-sensitive; `{{ SMTP.host }}` would render undefined, so the
    validator rejects it.
    """
    return isinstance(value, str) and bool(_SMTP_JINJA_RE.search(value))


# --- baked-config-files feature ----------------------------------------------
# Config visibility tiers (declared INSIDE a config's `schema`).
VALID_VISIBILITIES = {"visible", "advanced", "hidden"}

# Integration namespaces a render-flagged `file` MUST NOT reference: an unset
# integration renders to `{}` and the greffer's StrictUndefined file env would
# hard-abort the deploy. This set MUST stay in sync with the greffer's
# ``KNOWN_INTEGRATION_TYPES`` (greffer/apps/utils/docker/compose.py) — the two
# repos are coupled. ``tests_validate_catalog.py`` asserts this exact value so a
# greffer-side change can't silently drift the validator open. When a new
# integration type is added to the greffer, add it here too.
KNOWN_INTEGRATION_NAMESPACES = ("smtp",)

# dict built-ins a `config.<name>` scan would falsely flag.
_CONFIG_DICT_BUILTINS = {
    "items", "keys", "values", "get", "update", "pop", "copy", "clear", "setdefault",
}
# A `{{ ... }}` expression block, a `{% ... %}` statement block, and a
# `config.<name>` attribute inside one.
_JINJA_BLOCK_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_JINJA_STMT_RE = re.compile(r"\{%(.*?)%\}", re.DOTALL)
_CONFIG_NAME_RE = re.compile(r"\bconfig\.([A-Za-z_][A-Za-z0-9_]*)")


def _config_refs(text):
    """All `config.<name>` attribute names referenced inside `{{ }}` blocks.

    Scans EVERY ref in EVERY block (a single block may hold several, e.g.
    ``{{ config.USER ~ ':' ~ config.PASS }}``), excluding dict built-ins.
    """
    names = set()
    for block in _JINJA_BLOCK_RE.findall(text):
        for name in _CONFIG_NAME_RE.findall(block):
            if name not in _CONFIG_DICT_BUILTINS:
                names.add(name)
    return names


# ALLOWLIST for the Jinja in a render-flagged baked file. Only bare instance
# vars, ``config.<NAME>``, string literals, ``~`` concatenation, and the
# ``tojson`` filter are permitted; everything else is rejected. A blocklist of
# bypass idioms (``config.get`` / ``| default`` / ``config|attr('get')`` /
# ``| d`` / ``... or 'x'`` / ``config['X']`` / ``{{ smtp.host }}`` …) loses the
# arms race — Jinja has too many equivalent spellings, each of which silently
# bakes an empty/wrong value into a secret, and integration refs only fail at
# deploy. An allowlist can't be spelled around, and subsumes the old integration
# /bypass checks (a non-``config``/``instance_*`` name like ``smtp`` is rejected).
_RENDER_ALLOWED_BARE = {"instance_id", "instance_url", "instance_host", "instance_port"}
_RENDER_SAFE_FILTERS = {"tojson"}


def _unsafe_render_expr(inner):
    """Reason a render-flagged ``{{ ... }}`` block is not a safe baked
    expression, else None."""
    s = re.sub(r"'[^']*'|\"[^\"]*\"", "", inner)  # drop string literals
    for fm in re.finditer(r"\|\s*([A-Za-z_]\w*)", s):
        if fm.group(1) not in _RENDER_SAFE_FILTERS:
            return f"filter '|{fm.group(1)}'"
    s = re.sub(r"\|\s*[A-Za-z_]\w*", " ", s)  # strip the now-vetted filters
    if "(" in s or "[" in s:
        return "a call or subscript"
    for m in re.finditer(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", s):
        ref = m.group(0)
        head, _, tail = ref.partition(".")
        if head == "config" and tail and "." not in tail:
            if tail in _CONFIG_DICT_BUILTINS:
                # config.get/items/keys/... is a dict METHOD — renders a garbage
                # "<built-in method ...>" string, not a config value.
                return f"dict method 'config.{tail}'"
            continue  # config.NAME
        if ref in _RENDER_ALLOWED_BARE:
            continue  # bare instance_* var
        return f"reference '{ref}'"
    return None


def _render_block_problem(text):
    """First problem with a render-flagged file's Jinja: a ``{% %}`` statement
    (control flow is unneeded in a baked config and is bypass-prone) or an
    unsafe ``{{ }}`` expression. Returns a message, or None."""
    if _JINJA_STMT_RE.search(text):
        return "{% %} statement blocks are not allowed (use plain {{ ... }} substitutions)"
    for inner in _JINJA_BLOCK_RE.findall(text):
        reason = _unsafe_render_expr(inner)
        if reason:
            return (f"unsafe expression — {reason}; baked files may only use "
                    "{{ config.NAME }}, {{ instance_url/_id/_host/_port }}, string "
                    "concatenation (~), and the | tojson filter")
    return None


def decode_data_uri(data_uri):
    """Decode a data-URI with the SAME library the greffer uses
    (``python-datauri``), so a render-flagged file that passes validation
    decodes byte-identically at deploy (no false-accept of inputs the greffer
    rejects). ``DataURI.data`` is ``bytes`` for base64 URIs, ``str`` for
    percent-encoded ones. Raises ValueError / UnicodeDecodeError on malformed
    or non-UTF-8 input.
    """
    if not isinstance(data_uri, str):
        raise ValueError("not a data-URI")
    try:
        data = DataURI(data_uri).data
    except (InvalidDataURI, ValueError, TypeError) as exc:
        raise ValueError(f"invalid data-URI: {exc}") from exc
    return data.decode("utf-8") if isinstance(data, bytes) else data


REQUIRED_FILES = ["metadata.json", "docker-compose.yml", "smoke_test.spec.ts"]

METADATA_REQUIRED_FIELDS = ["name", "description", "configurations"]

VALID_DESTINATION_TYPES = {"env", "json", "file", "smtp"}
DESTINATION_REQUIRED_KEYS = {
    "env": {"type", "container", "key"},
    "json": {"type", "volume", "name"},
    "file": {"type", "volume", "name"},
    "smtp": {"type", "container", "key"},
}


def find_catalog_root():
    """Catalog root is two levels up from this script."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


SKIP_TOP_LEVEL = {"node_modules", "playwright-report", "playwright-results", ".playwright", "_template"}


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


def _compose_exposed_port_names(compose):
    """Port names the importer/greffer derive from a compose, as
    ``{service}_{container_port}``. Mirrors import_catalog._parse_ports:
    short-form ``ports:`` string entries only (``"published:container"`` with an
    optional bind-IP prefix and ``/proto`` suffix); long-form target/published
    mappings and bare single ports yield no name (no catalog entry uses them,
    and neither does the importer)."""
    names = set()
    if not isinstance(compose, dict):
        return names
    for svc_name, svc_def in (compose.get("services") or {}).items():
        if not isinstance(svc_def, dict):
            continue
        for entry in svc_def.get("ports") or []:
            if not isinstance(entry, str):
                continue
            spec = entry.split("/", 1)[0].strip()
            parts = spec.split(":")
            if len(parts) >= 2 and parts[-1].isdigit():
                names.add(f"{svc_name}_{parts[-1]}")
    return names


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

    # L4 per-port declarations (optional `ports` list). Mirrors the structural
    # checks in the manager's import_catalog._validate_meta (the importer is
    # still authoritative server-side) so a malformed entry fails at CI, not
    # only at import. One deliberate divergence: the same_port version floor
    # below is stricter here than in the importer (see that block).
    ports_meta = meta.get("ports")
    if ports_meta is not None and not isinstance(ports_meta, list):
        errors.append(f"{rel_dir}: metadata.json 'ports' must be a list")
        ports_meta = []
    for p in ports_meta or []:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"].strip():
            errors.append(
                f"{rel_dir}: each 'ports' entry must be an object with a non-empty 'name'")
            continue
        pname = p["name"]
        if p.get("exposure_tier") not in (None, "http", "l4"):
            errors.append(
                f"{rel_dir}: ports[{pname!r}].exposure_tier must be 'http' or 'l4'")
        if p.get("protocol") not in (None, "tcp", "udp"):
            errors.append(
                f"{rel_dir}: ports[{pname!r}].protocol must be 'tcp' or 'udp'")
        for bool_key in ("udp_reviewed", "same_port"):
            if p.get(bool_key) is not None and not isinstance(p.get(bool_key), bool):
                errors.append(
                    f"{rel_dir}: ports[{pname!r}].{bool_key} must be a boolean")
        # same_port rewrites the published container port; only meaningful for
        # a raw (Tier-C) port the greffer host-publishes.
        if p.get("same_port") and p.get("exposure_tier") != "l4":
            errors.append(
                f"{rel_dir}: ports[{pname!r}].same_port requires exposure_tier 'l4'")
    # Pairing: same_port needs a greffer that implements it on EVERY mode the
    # entry can be deployed to, enforced at start by the min_greffer_version
    # compat gate. Proxy-mode same_port shipped in greffer 0.3.0; tunnel-mode
    # in 0.3.3 (container-side = instance_l4_port; a 0.3.0-0.3.2 greffer
    # publishes the proxy-semantics container port while the app listens on
    # the relay port, a silent datapath mismatch). The catalog cannot know
    # which mode an entry lands on, so the floor is the max of the two: 0.3.3
    # (zero-padded dotted-numeric compare, matching the manager's comparator).
    # NOTE: the importer's own same_port floor is only 0.3.0 (it does not yet
    # enforce the mode-agnostic 0.3.3), so this CI gate is intentionally the
    # stricter of the two; the importer floor should be raised to match in a
    # separate manager change.
    if any(isinstance(p, dict) and p.get("same_port") for p in ports_meta or []):
        mgv = meta.get("min_greffer_version")
        try:
            parts = tuple(int(x) for x in str(mgv).split(".")) if mgv else None
            mgv_tuple = (parts + (0,) * (3 - len(parts)))[:3] if parts else None
        except (ValueError, AttributeError):
            mgv_tuple = None
        if mgv_tuple is None or mgv_tuple < (0, 3, 3):
            errors.append(
                f"{rel_dir}: a 'same_port' port requires 'min_greffer_version' "
                f">= 0.3.3 (proxy-mode same_port shipped in greffer 0.3.0, "
                f"tunnel-mode in 0.3.3; the floor must cover both deploy modes)")

    # Cross-check ports[] names against the compose-exposed ports. The importer
    # hard-errors when a `same_port` entry names a port the compose does not
    # expose (the rewrite would target nothing and the L4 datapath silently
    # drops); mirror that here so a name typo fails at CI, not only at server
    # import. A non-same_port name mismatch only degrades to Tier-A in the
    # importer (a warning, not a failure), so it is not gated here.
    if isinstance(compose, dict) and ports_meta:
        exposed = _compose_exposed_port_names(compose)
        for p in ports_meta:
            if not (isinstance(p, dict) and p.get("same_port")):
                continue
            pname = p.get("name")
            if isinstance(pname, str) and pname.strip() and pname not in exposed:
                errors.append(
                    f"{rel_dir}: ports[{pname!r}] sets same_port but names a port "
                    f"the compose does not expose (exposed: {sorted(exposed)}); "
                    f"the greffer rewrite would target nothing")

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

    # Accumulators for the bidirectional SMTP metadata-to-compose match (Rule 5.3).
    # Keyed by service name; values are sets of env keys.
    metadata_smtp_keys: dict = {}
    # baked-config-files: every env-destination key across the greffon (for the
    # `{{ config.X }}` bidirectional check), and the decoded text of each
    # render-flagged file (checked after all env keys are known).
    all_env_keys: set = set()
    render_flagged_files: list = []

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

            # --- baked-config-files: x-greffon-visibility (enum + placement) ---
            # The flag MUST live inside `schema` (ingestion copies only
            # schema/default_value/destinations; a config-root key is dropped).
            if "x-greffon-visibility" in cfg:
                errors.append(
                    f"{prefix} 'x-greffon-visibility' must live inside 'schema', "
                    "not at the config root (it would be dropped on ingestion)"
                )
            visibility = schema.get("x-greffon-visibility") if isinstance(schema, dict) else None
            if visibility is not None and visibility not in VALID_VISIBILITIES:
                errors.append(
                    f"{prefix} schema.x-greffon-visibility '{visibility}' invalid "
                    f"(must be one of {sorted(VALID_VISIBILITIES)})"
                )
            if visibility == "hidden":
                # The operator can't supply a hidden config's value, so it must
                # ship a complete, non-empty catalog default (the per-field
                # required-key presence is already enforced above).
                if not (isinstance(default_value, dict) and default_value):
                    errors.append(
                        f"{prefix} hidden config (x-greffon-visibility: hidden) must have a "
                        "non-empty default_value; the operator cannot supply one"
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

                # --- baked-config-files: x-greffon-render (bool, file/json only) ---
                if "x-greffon-render" in dest:
                    render_flag = dest.get("x-greffon-render")
                    if not isinstance(render_flag, bool):
                        errors.append(f"{dprefix} 'x-greffon-render' must be a boolean")
                    elif render_flag and dtype not in ("file", "json"):
                        errors.append(
                            f"{dprefix} 'x-greffon-render' is only valid on file/json "
                            f"destinations, not '{dtype}'"
                        )

                # Collect env keys for the `{{ config.X }}` bidirectional check.
                if dtype == "env" and dest.get("key"):
                    all_env_keys.add(dest["key"])

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

                # --- Rule 5.2: smtp destinations must target a real service ---
                # Also accumulate declared keys per service for the bidirectional
                # match in Rule 5.3 below.
                if dtype == "smtp":
                    container = dest.get("container", "")
                    key = dest.get("key", "")
                    if container and compose_services and container not in compose_services:
                        errors.append(
                            f"{dprefix} references container '{container}' "
                            f"not found in docker-compose.yml services: "
                            f"{sorted(compose_services)}"
                        )
                    if container and key:
                        metadata_smtp_keys.setdefault(container, set()).add(key)

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

            # --- baked-config-files: render-flagged content checks (file + json) ---
            # A render-flagged destination's baked content must (a) for `file`,
            # decode as UTF-8 (the greffer renders it as text); (b) contain only
            # allowlisted Jinja (bare instance vars, config.NAME, ~, | tojson) —
            # which rejects integration refs, bypass idioms, and statements in one
            # check. Collect the text for the post-loop config.X bidirectional check.
            for dest in destinations:
                if not isinstance(dest, dict) or not dest.get("x-greffon-render"):
                    continue
                dtype = dest.get("type")
                if dtype == "file":
                    data_uri = default_value.get("file") if isinstance(default_value, dict) else None
                    if not data_uri:
                        continue  # the file-default rule above already flagged this
                    try:
                        text = decode_data_uri(data_uri)
                    except (ValueError, UnicodeDecodeError) as exc:
                        errors.append(
                            f"{prefix} render-flagged file default is not valid/UTF-8-decodable: {exc}"
                        )
                        continue
                elif dtype == "json":
                    # The greffer renders json.dumps(value); scan that text.
                    text = json.dumps(default_value)
                else:
                    continue
                problem = _render_block_problem(text)
                if problem:
                    errors.append(f"{prefix} render-flagged {dtype}: {problem}")
                render_flagged_files.append((prefix, text))

            # Rule: configs whose title or any env-key looks like a secret must be required
            # OR have a non-empty default. Catches "user installs with empty password,
            # service silently broken or insecure".
            #
            # Only scan env-type destinations: smtp destinations get their value from
            # the operator's SMTP integration (render-time Jinja), not from user input,
            # so an empty schema/default_value is expected and correct for them.
            looks_like_secret = bool(SECRET_NAME_RE.search(title))
            for dest in destinations:
                if not isinstance(dest, dict):
                    continue
                if dest.get("type") != "env":
                    continue
                if SECRET_NAME_RE.search(dest.get("key", "")):
                    looks_like_secret = True
                    break
            # Walk the schema's `value` property (if any) to detect special
            # formats. ``greffon-secret`` declares "platform mints this
            # value at instance creation"; it implies the field is
            # legitimately empty in the catalog (the manager populates it)
            # and is exempt from the looks-like-secret-but-empty lint
            # below. It also has its own minimum-shape requirements
            # enforced here.
            value_prop = (
                schema.get("properties", {}).get("value", {})
                if isinstance(schema, dict)
                else {}
            )
            value_format = value_prop.get("format") if isinstance(value_prop, dict) else None
            # Two platform-minted secret formats share identical shape rules:
            #   greffon-secret        — URL-safe base64
            #   greffon-secret-alnum  — strict [A-Za-z0-9], for greffons whose
            #                           validators reject base64's - and _
            #                           (e.g. Activepieces AP_ENCRYPTION_KEY).
            GREFFON_SECRET_FORMATS = ("greffon-secret", "greffon-secret-alnum")
            is_greffon_secret = value_format in GREFFON_SECRET_FORMATS

            if is_greffon_secret:
                if value_prop.get("type") != "string":
                    errors.append(
                        f"{prefix} '{title}' declares format='{value_format}' but the "
                        "schema's `value` property is not type=string. The platform only "
                        "mints string secrets."
                    )
                # ``isinstance(True, int)`` is True in Python (bool is an
                # int subclass), so an explicit bool reject is needed —
                # otherwise ``minLength: true`` slips through and the
                # generator runs with an effective length of 1 char,
                # silently violating the minimum-entropy contract.
                min_length = value_prop.get("minLength")
                if isinstance(min_length, bool) or not isinstance(min_length, int) or min_length <= 0:
                    errors.append(
                        f"{prefix} '{title}' declares format='{value_format}' but no "
                        "positive integer minLength. The platform needs an explicit "
                        "length to generate against — set minLength to the underlying "
                        "greffon's documented minimum (e.g. 64 for Plausible "
                        "SECRET_KEY_BASE)."
                    )
                # Strict-true check (not truthiness): JSON Schema's
                # ``writeOnly`` contract is boolean-only. Truthy non-
                # bool values (``"yes"``, ``1``) would pass a vanilla
                # ``if not value_prop.get("writeOnly")`` and let invalid
                # schemas drive consumers that look up the literal
                # boolean to mis-handle the field (e.g. skip masking).
                if value_prop.get("writeOnly") is not True:
                    errors.append(
                        f"{prefix} '{title}' declares format='{value_format}' but is not "
                        "writeOnly: true. Platform-minted secrets must be writeOnly so "
                        "they're not echoed back to API consumers; set the value to a "
                        "literal boolean ``true``."
                    )

            if looks_like_secret and not is_greffon_secret:
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
                        "intentional (e.g. any-of auth), or set format='greffon-secret' "
                        "if the platform should generate the value."
                    )

    # --- baked-config-files: render-flagged `{{ config.X }}` must match an env key ---
    # The file and the container read the same minted value by env key, so a
    # `{{ config.X }}` with no matching env destination is almost always a typo
    # that would silently bake an empty value. Dict built-ins (config.items, …)
    # are excluded to avoid false positives.
    for fprefix, text in render_flagged_files:
        for name in _config_refs(text):
            if name not in all_env_keys:
                errors.append(
                    f"{fprefix} render-flagged content references '{{{{ config.{name} }}}}' "
                    f"but no env destination declares key '{name}'"
                )

    # --- Rule 5.3 / 5.4: bidirectional SMTP metadata-to-compose match ---
    # Walk the compose services, compute the set of env keys whose value is a
    # Jinja expression referencing `smtp.*`, and cross-check against the
    # metadata-declared SMTP destinations collected above. Errors are emitted
    # in both directions.
    #
    # Rule 5.4: if a service has any smtp destination AND its `environment:`
    # block is list-form (["KEY=value", ...]), we cannot cleanly inspect the
    # value — error and require mapping form.
    #
    # Rule 5.5: non-SMTP greffons are untouched. Every check below is gated on
    # "at least one smtp destination OR at least one `{{ smtp.* }}` env value"
    # so existing catalog entries pass unchanged.
    compose_smtp_env_keys: dict = {}
    list_form_smtp_services: set = set()
    if isinstance(compose, dict) and isinstance(compose.get("services"), dict):
        for svc_name, svc_def in compose["services"].items():
            if not isinstance(svc_def, dict):
                continue
            env = svc_def.get("environment")
            if isinstance(env, dict):
                for k, v in env.items():
                    if _value_references_smtp(v):
                        compose_smtp_env_keys.setdefault(svc_name, set()).add(k)
            elif isinstance(env, list):
                # List form: KEY=value strings. If the service has any smtp
                # destination on the metadata side, flag it — we require
                # mapping form for SMTP-aware services (Rule 5.4). Also scan
                # list entries for an obvious `{{ smtp.` reference so a
                # maintainer who wrote list-form Jinja still trips Rule 5.3.
                for entry in env:
                    if _value_references_smtp(entry):
                        # Best-effort key extraction for Rule 5.3 parity;
                        # the Rule 5.4 error below is the real fix.
                        if isinstance(entry, str) and "=" in entry:
                            key = entry.split("=", 1)[0].strip()
                            compose_smtp_env_keys.setdefault(svc_name, set()).add(key)
                if svc_name in metadata_smtp_keys:
                    list_form_smtp_services.add(svc_name)

    for svc_name in sorted(list_form_smtp_services):
        errors.append(
            f"{rel_dir}: service '{svc_name}' has smtp destination(s) but its "
            "'environment' is list-form; convert to mapping form "
            "(KEY: value) so SMTP Jinja values can be validated"
        )

    # Bidirectional match (Rule 5.3).
    affected_services = set(metadata_smtp_keys) | set(compose_smtp_env_keys)
    for svc in sorted(affected_services):
        meta_keys = metadata_smtp_keys.get(svc, set())
        compose_keys = compose_smtp_env_keys.get(svc, set())

        compose_env = {}
        if isinstance(compose, dict):
            svc_def = (compose.get("services") or {}).get(svc)
            if isinstance(svc_def, dict) and isinstance(svc_def.get("environment"), dict):
                compose_env = svc_def["environment"]

        for key in sorted(meta_keys - compose_keys):
            if key in compose_env:
                # Key is present in compose but its value doesn't reference smtp.*
                errors.append(
                    f"{rel_dir}: metadata.json declares SMTP env key '{key}' for "
                    f"service '{svc}' but its compose value does not reference the "
                    f"'smtp' Jinja context (got: '{compose_env[key]}'). "
                    "SMTP-managed keys must render from 'smtp.*'"
                )
            else:
                errors.append(
                    f"{rel_dir}: metadata.json declares SMTP env key '{key}' for "
                    f"service '{svc}' but it is not present in docker-compose.yml's "
                    f"environment for that service"
                )

        for key in sorted(compose_keys - meta_keys):
            errors.append(
                f"{rel_dir}: docker-compose.yml env '{key}' on service '{svc}' "
                "references the smtp Jinja context but metadata.json has no smtp "
                f"destination for it. Add a destination of type 'smtp' with "
                f"container='{svc}' key='{key}', or remove the Jinja reference"
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
