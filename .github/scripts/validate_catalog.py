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
import shlex
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
# instance_l4_* are the Tier-C/L4 endpoint vars the greffer hands a self-
# configuring L4 app (e.g. a WebRTC media server advertising its host:port).
# Same provenance as the bare instance_* vars (greffer render context), safe to
# bake the same way.
_RENDER_ALLOWED_BARE = {
    "instance_id", "instance_url", "instance_host", "instance_port",
    "instance_l4_host", "instance_l4_port", "instance_l4_endpoint",
    "instance_l4_proto",
}
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
                    "{{ config.NAME }}, {{ instance_url/_id/_host/_port }}, "
                    "{{ instance_l4_host/_port/_endpoint/_proto }}, string "
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


def _compose_exposed_ports(compose):
    """Map of ``{service}_{container_port}`` -> transport (``'tcp'``/``'udp'``)
    that the importer/greffer derive from a compose. Mirrors
    import_catalog._parse_ports: short-form ``ports:`` string entries only
    (``"published:container"`` with an optional bind-IP prefix and ``/proto``
    suffix); long-form target/published mappings and bare single ports yield no
    entry (no catalog entry uses them, and neither does the importer)."""
    ports = {}
    if not isinstance(compose, dict):
        return ports
    services = compose.get("services")
    if not isinstance(services, dict):
        return ports  # shape error already recorded by the compose validation
    for svc_name, svc_def in services.items():
        if not isinstance(svc_def, dict):
            continue
        for entry in svc_def.get("ports") or []:
            if not isinstance(entry, str):
                continue
            spec, _, proto = entry.strip().partition("/")
            parts = spec.split(":")
            if len(parts) >= 2 and parts[-1].isdigit():
                ports[f"{svc_name}_{parts[-1]}"] = "udp" if proto.strip().lower() == "udp" else "tcp"
    return ports


# A one-shot service (DB migration, object-store bucket creation, first-run
# superuser seed) runs to completion and then sits in `exited` forever. The
# greffer's status monitor must EXCLUDE such a container, otherwise a healthy
# multi-service instance reads as "mixed" and the greffer reports the `unknow`
# sentinel, which the manager rejects (HTTP 400). The instance is then stuck
# showing a stale status. The catalog declares the exclusion with this label;
# the greffer's compose.get_status skips any container that carries it.
ONE_SHOT_STATUS_LABEL = "com.greffon.status"
ONE_SHOT_STATUS_VALUE = "ignore"


def _service_labels(svc_def):
    """Normalise a compose `labels:` block (dict OR `key=value` list) to a
    plain `{key: value}` dict so the presence check works for both forms."""
    labels = svc_def.get("labels")
    if isinstance(labels, dict):
        return {str(k): str(v) for k, v in labels.items()}
    out = {}
    if isinstance(labels, list):
        for item in labels:
            if isinstance(item, str) and "=" in item:
                k, _, v = item.partition("=")
                out[k.strip()] = v.strip()
    return out


def _command_text(svc_def):
    """`command` + `entrypoint` flattened to one searchable string (each may be
    a string or a list)."""
    parts = []
    for key in ("command", "entrypoint"):
        val = svc_def.get(key)
        if isinstance(val, str):
            parts.append(val)
        elif isinstance(val, list):
            parts.append(" ".join(str(x) for x in val))
    return " ".join(parts)


# Restart policies that mean "keep this running": a service declaring one is by
# definition long-running, never a one-shot, so it's never asked for the label.
_ALWAYS_UP_RESTART = {"always", "unless-stopped"}

# A literal `exit 0` as the FINAL command of the line (optionally followed by a
# closing quote/paren/semicolon/whitespace). This is what a one-shot does when
# its job is done (`... && mc mb && exit 0;`). It deliberately does NOT match an
# `exit 0` buried mid-command, such as a clean-shutdown SIGTERM trap on a
# long-running server (`trap 'exit 0' TERM; app & wait`), which ends with the
# server command, not with `exit 0`.
_TERMINAL_EXIT0_RE = re.compile(r"""exit\s+0\b[\s;"')]*$""")


def _looks_one_shot(svc_name, svc_def):
    """Heuristic for a service that runs to completion rather than staying up.

    High-precision signals only, so the label is never forced onto a genuine
    long-running service:
      - name contains `migrate` (the migration-helper convention, and the
        greffer's own legacy status-skip fallback), or
      - it uses the `minio/mc` client image (the canonical bucket-init helper), or
      - its command/entrypoint ENDS WITH a literal `exit 0` (a one-shot signals
        completion that way; a server never does).

    A service kept alive by `restart: always`/`unless-stopped`, or that
    publishes a port, is long-running by definition and is never classified as
    a one-shot regardless of the above (guards against a clean-shutdown
    `exit 0` trap forcing the label onto a real app container).
    """
    restart = svc_def.get("restart")
    if isinstance(restart, str) and restart.strip() in _ALWAYS_UP_RESTART:
        return False
    if svc_def.get("ports"):
        return False
    if "migrate" in svc_name:
        return True
    image = svc_def.get("image")
    if isinstance(image, str) and image.split(":")[0] == "minio/mc":
        return True
    return bool(_TERMINAL_EXIT0_RE.search(_command_text(svc_def)))


# Any token made ENTIRELY of shell punctuation is an operator. An allowlist was
# wrong three times running (`2>&1` lexes as '>&', a here-string as '<<<'), and
# each miss looked like a passing check. Punctuation-only is the property that
# actually matters, so it is tested directly instead of enumerated.
# Always an operator wherever it appears unquoted. The backtick is command
# substitution; `$(` is covered by the paren.
_PUNCT = "();<>|&`"
# Syntax only at the START of a word: `-d a#b` is a literal argument, while
# `# comment` and `! pg_dump` are not.
_WORD_INITIAL_SYNTAX = "#!"
# $VAR, ${VAR} and compose's escaped $$VAR. Nothing expands them without a shell.
_EXPANSION_RE = re.compile(r"\$\$?\{?[A-Za-z_]")
# A lone $ before a name. `$$` is compose's escape for a literal $, so runs of
# dollars are collapsed pairwise first and only an odd one left over counts.
_UNESCAPED_DOLLAR_RE = re.compile(r"(?<!\$)(?:\$\$)*\$(?=\{?[A-Za-z_])")
_SIGNED_INT_RE = re.compile(r"[+-]?\d+")
# A leading NAME=value word. Shell syntax; docker exec has no idea.
_ENV_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# The greffer renders compose through Jinja, so CI reads the TEMPLATE.
# `{# ... #}` renders to the empty string, so a hook that looks non-empty
# here becomes an absent one at deploy. All three openers, not just two.
_JINJA_RE = re.compile(r"\{\{|\{%|\{#")
_SHELL_BASENAMES = {"sh", "bash", "ash", "dash", "zsh", "ksh"}
_BUSYBOX_SHELLS = {"sh", "ash"}
# The greffer excludes its regenerated sidecar volume by SUFFIX match, so this
# ending is reserved for every entry, not just for the volume it generates.
_NGINX_VOLUME_SUFFIX = "_nginx_volume"


# Host allowlists this rule understands, mapped to the separator the APP parses.
# An explicit map, not a name pattern. A pattern matched DISALLOWED_HOSTS (a
# denylist, where the advice would be backwards) and OIDC_REDIRECT_ALLOWED_HOSTS
# (URLs), and it could not know each app's encoding, which produced a false result
# in one direction or the other every time a new encoding turned up: JSON arrays,
# semicolons, whitespace inside JSON strings. Keyed on the exact setting, the
# separator is a fact rather than a guess.
#
# The cost is honest and small: a new app's allowlist is unchecked until it is
# added here, one line. The prevention that scales is the documentation, which now
# names the right idiom in the catalog README and in add-greffon.md.
# Per setting: how the APP splits entries, and any prefix it treats as part of a
# host pattern. Both are properties of that app's parser, which is the whole reason
# this is a map. Only settings whose parser is verifiable from an entry in this
# catalog are listed; SECURITY_ALLOWED_HOSTS was dropped for that reason, since
# nothing here uses it and its separator would have been a guess, which is the
# behaviour this map exists to remove.
# Keyed by (app, setting), not by setting alone. The README says parsing is a
# property of the app, and keying on the name alone contradicted that: two Django
# apps can both read DJANGO_ALLOWED_HOSTS and cast it differently (a plain
# os.environ split, django-environ's Env.list, a JSON cast), so the name does not
# determine the parser even when it looks app-specific.
_HOST_ALLOWLISTS = {
    # Django reads ALLOWED_HOSTS comma separated, and treats a leading dot as its
    # documented subdomain pattern (".example.com" matches example.com and any
    # subdomain), which carries no scheme or port.
    ("docs", "DJANGO_ALLOWED_HOSTS"): {"split": r"\s*,\s*", "prefixes": (".",)},
    ("visio", "DJANGO_ALLOWED_HOSTS"): {"split": r"\s*,\s*", "prefixes": (".",)},
    # Nextcloud's entrypoint reads trusted_domains through shell word splitting, so
    # entries are separated by the DEFAULT IFS characters and only those. `\s+`
    # was too generous: it also matches \r, \v, \f and unicode spaces, none of
    # which the shell splits on, so a value the app sees as one broken token would
    # have validated as two good ones.
    ("nextcloud", "NEXTCLOUD_TRUSTED_DOMAINS"): {"split": r"[ \t\n]+", "prefixes": ()},
}
_JINJA_EXPR_RE = re.compile(r"\{\{(.*?)\}\}", re.S)
_JINJA_STATEMENT_RE = re.compile(r"\{%")
_MASK = "__GREFFON_EXPR_{}__"
_MASK_RE = re.compile(r"__GREFFON_EXPR_(\d+)__")

# Each entry must be one of these, or a plain literal. Everything else is refused:
# the validator cannot evaluate Jinja, and five separate bypasses came from trying
# to decide what arbitrary template text renders by matching its source.
_EXPR_PORTED_RE = re.compile(
    r"""^\s*instance_url\s*\.\s*split\(\s*['"]://['"]\s*\)\s*\[\s*1\s*\]\s*$""")
_EXPR_BARE_RE = re.compile(
    r"""^\s*(?:instance_host"""
    r"""|instance_url\s*\.\s*split\(\s*['"]://['"]\s*\)\s*\[\s*1\s*\]"""
    r"""\s*\.\s*split\(\s*['"]:['"]\s*\)\s*\[\s*0\s*\])\s*$""")
_INSTANCE_VAR_RE = re.compile(r"\binstance_(?:url|host|port|id)\b")


def _is_standalone_literal(expr):
    """True only when the expression is ONE quoted string and nothing else."""
    s = (expr or "").strip()
    if s[:1] not in ("'", '"'):
        return False
    quote, i = s[0], 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == quote:
            return not s[i + 1:].strip()
        i += 1
    return False


def _classify_expr(expr):
    if _EXPR_BARE_RE.match(expr):
        return "bare"
    if _EXPR_PORTED_RE.match(expr):
        return "ported"
    if _is_standalone_literal(expr) and not _INSTANCE_VAR_RE.search(expr):
        return "const"
    return None


def _host_allowlist_problem(app, key, value):
    """None when fine, else a short reason code. `app` is the entry's directory
    name, since the parser belongs to the app rather than to the setting name."""
    if not isinstance(key, str) or not isinstance(value, str):
        return None
    spec = _HOST_ALLOWLISTS.get((app, key))
    if spec is None:
        return None
    if "{#" in value or "#}" in value or _JINJA_STATEMENT_RE.search(value):
        return "control-flow"
    if "__GREFFON_EXPR_" in value:
        return "unrecognised"  # would collide with the masking below
    if "{{" not in value:
        return None  # a literal allowlist; nothing derived from the instance

    exprs = []

    def stash(m):
        exprs.append(m.group(1))
        return _MASK.format(len(exprs) - 1)

    masked = _JINJA_EXPR_RE.sub(stash, value)
    # Any delimiter still standing means the template is malformed. Counting `{{`
    # against `}}` passed `localhost }} {{ instance_host`, where the counts match
    # and the order does not; masking well-formed pairs first leaves the strays.
    if "{{" in masked or "}}" in masked:
        return "control-flow"

    kinds = set()
    for token in re.split(spec["split"], masked.strip()):
        token = token.strip()
        if not token:
            continue
        marks = _MASK_RE.findall(token)
        if not marks:
            kinds.add("const")
            continue
        # A prefix the app treats as part of the host pattern is not "embedded in
        # other text": Django's ".{{ instance_host }}" is a subdomain wildcard and
        # carries no scheme or port.
        bare_token = token
        for prefix in spec["prefixes"]:
            if bare_token.startswith(prefix):
                bare_token = bare_token[len(prefix):]
                break
        if not _MASK_RE.fullmatch(bare_token):
            # `https://{{ instance_host }}` renders a URL, not a host.
            return "embedded"
        token = bare_token
        kind = _classify_expr(exprs[int(marks[0])])
        if kind is None:
            return "unrecognised"
        kinds.add(kind)
    if "ported" in kinds and "bare" not in kinds:
        return "no-bare-host"
    return None


def _service_named_volumes(svc_def):
    """Named volumes a service mounts, from both the short and long compose forms.

    Bind mounts (a source containing a / ) are skipped: they are host paths, not
    named volumes, and never appear in backup.volumes."""
    out = set()
    vols = svc_def.get("volumes")
    if not isinstance(vols, list):
        return out  # `volumes: 1` is malformed; crashing here would suppress
                    # every diagnostic --all had accumulated for the catalog
    for entry in vols:
        src = None
        if isinstance(entry, str) and ":" in entry:
            src = entry.split(":", 1)[0]
        elif isinstance(entry, dict) and entry.get("type") == "volume":
            src = entry.get("source")
        if isinstance(src, str) and src and "/" not in src:
            out.add(src)
    return out


def _compose_bool(value):
    """compose's boolean coercion: YAML gives us a bool, but a QUOTED "true" (or
    an interpolated one) arrives as a string that compose still reads as true.
    An `is True` identity test missed those and accepted a disabled healthcheck."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "on", "1"}
    return False


def _shell_script_of(argv):
    """The script of a `sh -c <script>` hook, or None if this is not that shape.

    EXACTLY two forms are accepted, and nothing may follow the script:

        <shell> -c <script>
        busybox <shell> -c <script>

    This is deliberately narrower than what a shell would accept, because the
    wider version kept being wrong. Walking option words to find the -c was
    revised three times and broke three times: membership let
    `busybox timeout 10 pg_dump -c db | gzip` borrow an unrelated -c; requiring
    argv[1] == "-c" rejected the legitimate `sh -eu -c`; and walking the options
    then matched `--norc` (it contains a c) and ran past a `--` terminator. Every
    fix grew the parser and the next round found another hole in it.

    So the parser is gone. A catalog entry has no need of shell options: `set -eu`
    belongs INSIDE the script, where it is also more obvious. Refusing
    `sh -c <script> <extra>` costs nothing either, and removes the whole question
    of what the words after a script mean, which was the source of two more bugs.
    """
    if len(argv) == 4 and os.path.basename(argv[0]) == "busybox":
        # busybox dispatches an APPLET, and it ships sh/ash, not bash or zsh, and
        # not a path. `busybox bash -c ...` fails at exec however valid it looks.
        if argv[1] not in _BUSYBOX_SHELLS:
            return None
        shell, rest = argv[1], argv[2:]
    elif len(argv) == 3:
        shell, rest = argv[0], argv[1:]
    else:
        return None
    if os.path.basename(shell) not in _SHELL_BASENAMES or rest[0] != "-c":
        return None
    return rest[1]


def _names_a_shell(argv):
    """argv starts with a shell (or busybox+shell), whatever follows it."""
    # ANY word, not just the first. `env sh -c pg_dump -U app` hides the shell
    # behind a wrapper, falls through to the plain-argv path (it has no operators
    # and no $), and validates, while sh actually takes -U as $0. Checking every
    # word costs one membership test and needs no wrapper list, where recognising
    # `env` specifically would just invite the next wrapper.
    return any(os.path.basename(a) in _SHELL_BASENAMES | {"busybox"} for a in argv)


def _shell_operators_in(cmd):
    """Shell operator characters appearing OUTSIDE quotes in the raw command.

    Scans the raw string rather than lexed tokens, because the tokens have already
    lost the distinction that matters: shlex strips quotes, so a legitimate literal
    argument (tool --separator '|') and a real pipe both arrive as the token "|",
    and matching tokens rejected the working hook. I accepted that ambiguity
    earlier on this branch, reasoning such an argument was unlikely. That was the
    wrong call. A validator that rejects correct entries teaches people to work
    around it, and quote state is cheap to track.

    Deliberately not a shell parser: quoting and word starts, nothing else. `#`
    and `!` are syntax only at the start of a word, so `-d a#b` stays a literal."""
    found, quote, escaped, word_start = [], None, False, True
    for ch in cmd:
        if escaped:
            escaped, word_start = False, False
        elif quote:
            if ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = None
        elif ch == "\\":
            escaped, word_start = True, False
        elif ch in "\"'":
            quote, word_start = ch, False
        elif ch.isspace():
            word_start = True
        elif ch in _PUNCT:
            found.append(ch)
            word_start = True
        elif ch in _WORD_INITIAL_SYNTAX and word_start:
            found.append(ch)
            word_start = False
        else:
            word_start = False
    return found
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

                # Per-service checks
                for svc_name, svc_def in services.items():
                    if not isinstance(svc_def, dict):
                        continue
                    # No service may pin container_name
                    if "container_name" in svc_def:
                        errors.append(
                            f"{rel_dir}: service '{svc_name}' must not use 'container_name' "
                            "(greffer assigns names dynamically)"
                        )
                    # A one-shot helper must declare itself ignorable for status,
                    # else its normal `exited` state poisons the instance status.
                    if _looks_one_shot(svc_name, svc_def) and (
                        _service_labels(svc_def).get(ONE_SHOT_STATUS_LABEL) != ONE_SHOT_STATUS_VALUE
                    ):
                        errors.append(
                            f"{rel_dir}: one-shot service '{svc_name}' must carry label "
                            f"'{ONE_SHOT_STATUS_LABEL}: {ONE_SHOT_STATUS_VALUE}' so it is excluded "
                            "from instance status (a completed one-shot otherwise counts as a "
                            "stopped container and forces the instance to 'unknow')"
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

    # Hot-backup declarations (optional `backup.volumes`). Two halves have to
    # agree and nothing checked that before: the compose-side dump/restore hooks,
    # and the metadata-side volume classification the MANAGER reads. A real defect
    # shipped review-ready because of it. The keycloak entry declared both hooks
    # and a healthcheck, with comments saying the greffer read them, while
    # metadata.json had no `backup` block. The manager takes classes only from
    # there (`import_catalog.py`: `(meta.get("backup") or {}).get("volumes")`),
    # empty means unclassified, unclassified means COLD, and the COLD path never
    # invokes a dump hook. So every backup would have stopped the instance and
    # snapshotted raw volumes while the hooks sat unused, and the validator was
    # green. Mirrors the importer's shape checks, plus the pairing rules the
    # importer cannot express because it never sees the compose.
    _BACKUP_CLASSES = {"data", "regenerable", "database"}
    backup_meta = meta.get("backup")
    backup_vols = {}
    if backup_meta is not None:
        if not isinstance(backup_meta, dict):
            errors.append(f"{rel_dir}: metadata.json 'backup' must be an object")
        else:
            raw_vols = backup_meta.get("volumes")
            if raw_vols is not None and not isinstance(raw_vols, dict):
                errors.append(
                    f"{rel_dir}: 'backup.volumes' must be an object {{volume: class}}")
            elif isinstance(raw_vols, dict):
                backup_vols = raw_vols
                for vol_name, vol_class in raw_vols.items():
                    if not isinstance(vol_name, str) or not vol_name.strip():
                        errors.append(
                            f"{rel_dir}: 'backup.volumes' keys must be non-empty volume names")
                        continue
                    if not isinstance(vol_class, str):
                        errors.append(
                            f"{rel_dir}: backup.volumes[{vol_name!r}] must be a string, got "
                            f"{type(vol_class).__name__}. A non-string is unhashable and would "
                            f"raise rather than lint")
                        continue
                    if vol_class not in _BACKUP_CLASSES:
                        errors.append(
                            f"{rel_dir}: backup.volumes[{vol_name!r}] must be one of "
                            f"{sorted(_BACKUP_CLASSES)}, got {vol_class!r}")
                    if isinstance(compose, dict) and vol_name not in compose_volumes:
                        errors.append(
                            f"{rel_dir}: backup.volumes names {vol_name!r}, which is not a "
                            f"top-level volume in docker-compose.yml. The greffer looks the "
                            f"class up by compose name and would raise volume_unclassified")

    # Dump/restore hooks are SERVICE labels the greffer reads at backup time.
    dump_hooks, restore_hooks, hook_services = [], [], set()
    if isinstance(compose, dict) and isinstance(compose.get("services"), dict):
        for svc_name, svc in compose["services"].items():
            if not isinstance(svc, dict):
                continue
            labels = svc.get("labels") or {}
            if isinstance(labels, list):  # list form: ["k=v", ...]
                labels = dict(
                    (item.split("=", 1) + [""])[:2] for item in labels if isinstance(item, str))
            if not isinstance(labels, dict):
                continue
            for kind, bucket in (("dump", dump_hooks), ("restore", restore_hooks)):
                val = labels.get(f"com.greffon.backup.{kind}")
                if val is None:
                    continue
                hook_services.add(svc_name)
                if isinstance(val, str) and _JINJA_RE.search(val):
                    # The greffer renders the compose file through Jinja before
                    # docker-compose ever sees it, so this validator is reading the
                    # TEMPLATE. `{{ "" }}` is a non-empty string here and an absent
                    # hook at deploy, which is precisely the silent degrade every
                    # other check on this branch exists to prevent.
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} backup.{kind} label contains a Jinja "
                        f"expression. The greffer renders the compose file before deploying, so "
                        f"CI validates the template and the runtime gets something else. Write "
                        f"the command literally")
                    continue
                if not isinstance(val, str):
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} backup.{kind} label must be a string, "
                        f"got {type(val).__name__}")
                    continue
                try:
                    argv = shlex.split(val)
                except ValueError as exc:
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} backup.{kind} label does not parse as a "
                        f"command ({exc}). The greffer runs it through shlex.split and would "
                        f"raise at backup time, not here")
                    continue
                script = _shell_script_of(argv) if argv else None
                # A single $ is eaten by COMPOSE, before any container exists, and
                # it interpolates from the compose process's own environment. So
                # `$PGUSER` resolves to empty and the hook silently loses the
                # argument. True with or without a shell, so it is checked on the
                # raw label ahead of everything else.
                if argv and _UNESCAPED_DOLLAR_RE.search(val):
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} backup.{kind} label uses a single-$ "
                        f"variable. compose interpolates that from its OWN environment at "
                        f"deploy time, not the container's, so it resolves to empty and the "
                        f"argument is lost. Write $$VAR to pass a literal $ through to the "
                        f"container")
                    continue
                if argv and _names_a_shell(argv) and script is None:
                    # Anything shell-shaped that is not exactly `<shell> -c <script>`:
                    # a missing script, options around the -c, or trailing words after
                    # it. Narrow on purpose (see _shell_script_of): the permissive
                    # version of this was wrong three rounds running.
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} backup.{kind} label invokes a shell "
                        f"but is not exactly \"sh -c '<script>'\" (or \"busybox sh -c "
                        f"'<script>'\"). Only those two forms are accepted, so that what the "
                        f"shell interprets is unambiguous: put any options such as `set -eu` "
                        f"INSIDE the script, and fold trailing words into it rather than "
                        f"passing them after it, where the shell treats them as $0 and $1 "
                        f"instead of running them")
                    continue
                if script is not None and not script.strip():
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} backup.{kind} label passes an empty "
                        f"script to the shell. It exits 0 having written nothing, so the backup "
                        f"fails with dump_empty rather than reporting an error")
                    continue
                if argv and not argv[0].strip():
                    # shlex.split("''") is [''], which is non-empty, so the
                    # no-argv branch below never sees it. The greffer then builds
                    # `timeout 3600 ''` and execs nothing.
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} backup.{kind} label has an empty "
                        f"program name. The greffer builds a timeout argv around it and there "
                        f"is nothing to execute, so every backup or restore fails")
                    continue
                if argv and script is None and _ENV_ASSIGN_RE.match(argv[0]):
                    # `PGPASSWORD=x pg_dump ...` is shell syntax with no punctuation
                    # and no $, so both scans below pass it. docker exec then looks
                    # for a program literally named "PGPASSWORD=x".
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} backup.{kind} label starts with the "
                        f"environment assignment {argv[0]!r}. Only a shell applies those; the "
                        f"greffer execs argv[0] as a program name, so every backup fails. Set it "
                        f"in the service's `environment:` (which docker exec inherits, and which "
                        f"keeps it off the command line), or use \"sh -c '...'\"")
                    continue
                if argv and script is None:
                    # No shell involved: the greffer runs `docker exec <container>
                    # <argv>` (backup.py:272, :346), so a pipe, a redirect or a $VAR
                    # is handed to the program as a literal argument. With the two
                    # shell forms pinned above, the script is the ONLY interpreted
                    # text in a hook, and nothing can sit outside it, so this branch
                    # no longer has to reason about scope at all.
                    stray = _shell_operators_in(val)
                    expand = [tok for tok in argv if _EXPANSION_RE.search(tok)]
                    if stray or expand:
                        what = (f"shell operators {stray}" if stray
                                else f"variable expansions {expand}")
                        errors.append(
                            f"{rel_dir}: service {svc_name!r} backup.{kind} label uses {what} "
                            f"but does not invoke a shell. The greffer execs the argv directly, "
                            f"so these are passed to the program as literal arguments and never "
                            f"interpreted. Wrap the command in \"sh -c '...'\" if you need a "
                            f"shell (note compose eats a single $, so write $$VAR)")
                        continue
                if not argv:
                    # Two different failures, both silent, hence one check:
                    # an EMPTY value never reaches shlex at all -- the greffer's
                    # `if not cmd: continue` treats the hook as absent, so the
                    # entry quietly degrades to a COLD backup (or fails the
                    # restore with no_restore_hook). A WHITESPACE-ONLY value is
                    # truthy, gets past that guard, and builds a bare
                    # `timeout <secs>` argv with no command after it.
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} backup.{kind} label is empty or only "
                        f"whitespace, so it yields no command. An empty value is skipped by the "
                        f"greffer as if the hook were absent (silent COLD backup / "
                        f"no_restore_hook); a whitespace-only value builds a 'timeout' argv with "
                        f"nothing to run")
                    continue
                bucket.append(svc_name)

    if backup_vols and isinstance(compose, dict):
        for vol_name in sorted(compose_volumes - set(backup_vols), key=str):
            errors.append(
                f"{rel_dir}: compose declares volume {vol_name!r} but backup.volumes does not "
                f"classify it. The greffer looks the class up by compose name and refuses the "
                f"backup with volume_unclassified rather than guessing")

    for vol_name in sorted(backup_vols, key=str):
        if not isinstance(vol_name, str):
            continue
        if _JINJA_RE.search(vol_name):
            # The compose key is RENDERED before deploy; this metadata key is not.
            # Both files reading `data_{{ instance_id }}` looks consistent here and
            # is two different names at runtime, so the greffer looks up the
            # rendered one, finds no class, and refuses with volume_unclassified.
            errors.append(
                f"{rel_dir}: backup.volumes classifies {vol_name!r}, which contains a Jinja "
                f"expression. The greffer renders the compose volume name but not this key, "
                f"so the two stop matching at deploy and the backup fails with "
                f"volume_unclassified. Use a literal name")
    # Every declared volume, not only the classified ones, and tested against the
    # RUNTIME name. Two ways to miss this, and the first version missed both:
    #   - an entry with no backup.volumes never entered the loop above, yet COLD
    #     backups go through the same _data_volumes (backup.py:476) and drop these
    #     volumes just the same;
    #   - a compose volume named exactly `nginx_volume` does not end with
    #     `_nginx_volume`, but namespacing makes it `<instance_id>_nginx_volume`,
    #     which does, and which additionally collides with the sidecar's own.
    # Prefixing with "_" models the namespacing without inventing an instance id.
    for vol_name in sorted(compose_volumes, key=str):
        # Jinja is refused on EVERY volume name, not just on the classified ones.
        # This is the structural half of the fix rather than another special case:
        # three rounds running found this rule family applied to the wrong set of
        # names, because each rule had to reason about template-vs-rendered on its
        # own. With no name containing Jinja, every rule below is comparing literal
        # strings and the whole question stops arising. `app_{{ "nginx_volume" }}`
        # was the case in hand: it does not end with the reserved suffix here and
        # renders to a name that does.
        if isinstance(vol_name, str) and _JINJA_RE.search(vol_name):
            errors.append(
                f"{rel_dir}: compose declares volume {vol_name!r}, which contains a Jinja "
                f"expression. The greffer renders it before deploying, so CI cannot know the "
                f"runtime name and cannot tell whether it is classified, reserved, or mounted "
                f"by the right service. Use a literal name")
            continue
        vol_def = ((compose.get("volumes") or {}).get(vol_name)
                   if isinstance(compose, dict) else None)
        if isinstance(vol_def, dict) and (vol_def.get("name") or vol_def.get("external")):
            # The greffer collects an instance's volumes by the `<instance_id>_`
            # PREFIX that compose's project namespacing produces (backup.py:190).
            # Both `name:` and `external: true` opt out of that namespacing, so the
            # docker volume does not carry the prefix, is never collected, and is
            # absent from hot backups, cold backups and the restore safety snapshot
            # alike. The backup still succeeds whenever another artifact exists.
            why = "name:" if vol_def.get("name") else "external: true"
            errors.append(
                f"{rel_dir}: compose volume {vol_name!r} sets {why}, which opts out of "
                f"compose's project namespacing. The greffer collects an instance's volumes "
                f"by their '<instance_id>_' prefix, so this one is invisible to it and is "
                f"silently left out of every backup and of the restore safety snapshot while "
                f"the backup reports success. Use a plain volume key")
            continue
        if isinstance(vol_name, str) and f"_{vol_name}".endswith(_NGINX_VOLUME_SUFFIX):
            errors.append(
                f"{rel_dir}: compose declares volume {vol_name!r}, whose runtime name ends "
                f"with the reserved suffix {_NGINX_VOLUME_SUFFIX!r}. That is how the greffer "
                f"skips its own regenerated sidecar volume, so this one is dropped with it "
                f"and left out of the snapshot while the backup still reports success. "
                f"Applies to cold backups too, so it holds whether or not the entry "
                f"classifies its volumes. Rename it")

    db_vols = [v for v, c in backup_vols.items() if c == "database"]
    # `all(... == "regenerable")` and not merely "no data and no database": a map
    # holding only an invalid class ({"x": "bogus"}) satisfies the weaker form and
    # would draw a second, false error saying every volume is regenerable, next to
    # the real schema complaint. Say one true thing rather than two things.
    if backup_vols and all(c == "regenerable" for c in backup_vols.values()):
        errors.append(
            f"{rel_dir}: backup.volumes classifies every volume as 'regenerable', which the "
            f"manager still reads as opted-in and runs HOT. The greffer then has nothing to "
            f"snapshot and nothing to dump, and fails every backup with no_data_volumes. "
            f"Classify at least one volume 'data' or 'database', or drop the block entirely "
            f"to take COLD backups")

    if (dump_hooks or restore_hooks) and not backup_vols:
        errors.append(
            f"{rel_dir}: declares backup hooks on {sorted(hook_services, key=str)} but metadata.json has "
            f"no 'backup.volumes'. The manager reads classes only from there, so the instance is "
            f"unclassified, the backup falls back to COLD (stop the instance, snapshot volumes) "
            f"and these hooks are never invoked. Add the block, or drop the hooks")
    if (dump_hooks or restore_hooks) and backup_vols and not db_vols:
        errors.append(
            f"{rel_dir}: declares backup hooks on {sorted(hook_services, key=str)} but no volume is "
            # str() every value and dedupe on that: a malformed class may be a list
            # or dict, and set() over raw values raises TypeError on the unhashable
            # ones, which is the crash the type check above exists to prevent.
            f"classed 'database' (classes present: "
            f"{sorted({str(v) for v in backup_vols.values()})}). The "
            f"manager selects the dump path from the volume class, so these hooks are never "
            f"invoked and the database is snapshotted raw instead of dumped")
    if db_vols and not dump_hooks:
        errors.append(
            f"{rel_dir}: backup.volumes classes {db_vols[0]!r} as 'database' but no service "
            f"declares a 'com.greffon.backup.dump' label. The greffer refuses the backup with "
            f"no_dump_hook rather than snapshotting a database volume it was told not to")
    if db_vols and not restore_hooks:
        errors.append(
            f"{rel_dir}: backup.volumes classes {db_vols[0]!r} as 'database' but no service "
            f"declares a 'com.greffon.backup.restore' label, so a backup could be taken and "
            f"never restored")
    if len(db_vols) > 1:
        errors.append(
            f"{rel_dir}: {len(db_vols)} volumes classed 'database' ({sorted(db_vols, key=str)}). The "
            f"greffer's hot path is single-DB; the manager silently downgrades this entry to "
            f"COLD backups, so the hooks would never run")
    if dump_hooks and restore_hooks and set(dump_hooks) != set(restore_hooks):
        errors.append(
            f"{rel_dir}: the dump hook is on {sorted(dump_hooks, key=str)} but the restore hook is on "
            f"{sorted(restore_hooks, key=str)}. The greffer keys its manifest by the DUMP service and "
            f"then looks the restore hook up on that same service, so a split pair backs up "
            f"fine and fails the restore with no_restore_hook. Put both labels on one service")
    for kind, hooks in (("dump", dump_hooks), ("restore", restore_hooks)):
        if len(hooks) > 1:
            errors.append(
                f"{rel_dir}: {len(hooks)} services declare a backup.{kind} hook ({sorted(hooks, key=str)}). "
                f"The greffer refuses with multiple_database_unsupported")
    # The greffer's hot restore waits on the DB service's compose healthcheck
    # before streaming pg_restore in; without one it cannot know when to start.
    if isinstance(compose, dict):
        for svc_name in sorted(hook_services, key=str):
            svc = (compose.get("services") or {}).get(svc_name)
            if not isinstance(svc, dict):
                continue
            # The hook is found by COUNTING RUNNING CONTAINERS, not by reading the
            # compose file: _dump_hooks() walks the instance's running containers
            # and reads the label off each (backup.py:541-549). So the checks above,
            # which count SERVICES, can pass while the runtime count is 0 or 2.
            # The greffer enumerates RUNNING containers, so a service that runs to
            # completion has no container to exec into by the time the backup
            # starts. Same no_dump_hook as the profiles case, different reason.
            if _looks_one_shot(svc_name, svc):
                errors.append(
                    f"{rel_dir}: hook service {svc_name!r} looks like a one-shot (it runs to "
                    f"completion rather than staying up). The greffer looks for the hook on a "
                    f"RUNNING container, so there is nothing to exec into and the backup fails "
                    f"with no_dump_hook. Put the hook on the long-running database service")
            # P0-adjacent: the greffer's restore guard reads DB volumes from DOCKER
            # STATE, treating every volume mounted by the dump service as database
            # state (backup.py _db_volumes_from_containers). If one of those is
            # classed 'data' it is also in the data-restore set, the guard sees the
            # overlap and aborts with db_volume_misclassified. The backup succeeds
            # every time and the restore can never run, which is the worst shape a
            # backup defect can take.
            # The other half of the same binding, and the one that loses data
            # rather than blocking a restore: nothing tied the 'database' volume to
            # the service that dumps it. Class the DB service's volume
            # 'regenerable' and an APP volume 'database' and every check here
            # passed, while the hot path skips both classes (only 'data' is
            # snapshotted) and captures only the hook's dump. The app volume is
            # then in no artifact at all, and the backup still reports success.
            if db_vols and not any(backup_vols.get(v) == "database"
                                   for v in _service_named_volumes(svc)):
                errors.append(
                    f"{rel_dir}: hook service {svc_name!r} mounts none of the volumes classed "
                    f"'database' ({sorted(db_vols, key=str)}). The greffer captures a database "
                    f"through the dump hook on the service that HOLDS it, and the hot path "
                    f"snapshots only 'data' volumes, so a 'database' volume on any other "
                    f"service lands in no artifact at all while the backup reports success. "
                    f"Put the hook on the service that mounts it")
            for vol_name in _service_named_volumes(svc):
                if backup_vols.get(vol_name) == "data":
                    errors.append(
                        f"{rel_dir}: hook service {svc_name!r} mounts volume {vol_name!r}, which "
                        f"backup.volumes classes 'data'. The greffer reads DB volumes from "
                        f"docker state, so this volume counts as database state AND is in the "
                        f"data-restore set; the restore refuses with db_volume_misclassified "
                        f"while every backup reports success. Move it off the database service, "
                        f"or class it 'database'")
            if svc.get("profiles"):
                errors.append(
                    f"{rel_dir}: hook service {svc_name!r} declares profiles "
                    f"{svc['profiles']!r}. The greffer starts the stack with a plain "
                    f"`docker-compose up -d` and passes no --profile (backup.py:628), so this "
                    f"service never starts and the backup fails with no_dump_hook")
            for field, probe in (("scale", svc.get("scale")),
                                 ("deploy.replicas", (svc.get("deploy") or {}).get("replicas")
                                  if isinstance(svc.get("deploy"), dict) else None),
                                 ("healthcheck.disable", (svc.get("healthcheck") or {}).get("disable")
                                  if isinstance(svc.get("healthcheck"), dict) else None),
                                 ("healthcheck.test", (svc.get("healthcheck") or {}).get("test")
                                  if isinstance(svc.get("healthcheck"), dict) else None)):
                # A list too: `test: ["{{ 'NONE' }}"]` renders to a disabled check.
                if isinstance(probe, list):
                    probe = " ".join(str(x) for x in probe)
                if isinstance(probe, str) and (_JINJA_RE.search(probe)
                                               or _UNESCAPED_DOLLAR_RE.search(probe)):
                    errors.append(
                        f"{rel_dir}: hook service {svc_name!r} sets {field} from a Jinja or "
                        f"interpolated expression ({probe!r}). The greffer renders the compose file before "
                        f"deploying, so this validates as a harmless string and becomes a "
                        f"container count or a disabled healthcheck at runtime")
            for field, count in (("scale", svc.get("scale")),
                                 ("deploy.replicas",
                                  (svc.get("deploy") or {}).get("replicas")
                                  if isinstance(svc.get("deploy"), dict) else None)):
                # No coercion ladder any more. Every round found another spelling
                # compose accepts and this did not: "2", 2.0, "${N:-2}", 2e0, 0o2.
                # Chasing them meant reimplementing compose's number parsing, and
                # losing that race silently accepted the entry. A hook service runs
                # exactly one container, so the only value worth accepting is a
                # literal 1, and anything else is refused WITHOUT being understood.
                if count is None:
                    continue
                if isinstance(count, bool) or not (
                        isinstance(count, (int, float)) and count == 1):
                    errors.append(
                        f"{rel_dir}: hook service {svc_name!r} sets {field} to {count!r}. A "
                        f"hook service must run exactly one container: the greffer finds the "
                        f"hook by enumerating running containers, so zero gives no_dump_hook "
                        f"and more than one gives multiple_database_unsupported. Write the "
                        f"literal 1, or leave it unset")
            hc = svc.get("healthcheck")
            hc_test = hc.get("test") if isinstance(hc, dict) else None
            disabled = (
                isinstance(hc, dict) and (
                    _compose_bool(hc.get("disable")) is True
                    # docker disables on a LEADING NONE, whatever follows it,
                    # so an exact comparison against ["NONE"] was too narrow.
                    or hc_test == "NONE"
                    or (isinstance(hc_test, list) and hc_test[:1] == ["NONE"])
                )
            )
            # A truthy mapping is not a healthcheck. `healthcheck: {interval: 5s}`
            # and `test: []` both pass a presence test while supplying no command,
            # so docker reports no health state and _wait_db_healthy waits for a
            # 'healthy' that cannot arrive. Same outcome as `disable: true`, so it
            # is the same error, reached by a different route.
            no_command = isinstance(hc, dict) and not disabled and not hc_test
            if not hc:
                errors.append(
                    f"{rel_dir}: service {svc_name!r} declares a backup hook but has no "
                    f"'healthcheck'. The greffer's hot restore waits for that healthcheck "
                    f"before streaming the dump back in")
            elif no_command:
                errors.append(
                    f"{rel_dir}: service {svc_name!r} declares a backup hook and a "
                    f"'healthcheck' with no usable 'test' ({hc_test!r}). Docker reports no "
                    f"health state without a command to run, so the hot restore waits for a "
                    f"'healthy' that never arrives and times out, exactly as if the "
                    f"healthcheck were disabled")
            elif disabled:
                errors.append(
                    f"{rel_dir}: service {svc_name!r} declares a backup hook but its "
                    f"healthcheck is DISABLED ({'disable: true' if _compose_bool(hc.get('disable')) else 'test: NONE'}). "
                    f"Docker then reports no health state at all, so the hot restore waits for "
                    f"a 'healthy' that never arrives and times out")

    # Host-allowlist settings must accept the port-less host (see the note on
    # _HOST_ALLOWLISTS): the sidecar's `Host $host` drops the port.
    if isinstance(compose, dict) and isinstance(compose.get("services"), dict):
        for svc_name, svc_def in compose["services"].items():
            if not isinstance(svc_def, dict):
                continue
            # Branch on the two shapes compose actually accepts and ignore
            # anything else. `env or []` looked like a guard but only covers
            # falsey values: `environment: 1` is truthy and not iterable, and
            # iterating it raises, which aborts `--all` for the WHOLE catalog and
            # discards every diagnostic collected so far. Malformed compose is
            # reported by the shape checks elsewhere; it must not crash this one.
            env = svc_def.get("environment")
            if isinstance(env, dict):
                items = list(env.items())
            elif isinstance(env, list):
                items = [(e.split("=", 1) + [""])[:2] for e in env if isinstance(e, str)]
            else:
                items = []
            for key, value in items:
                why = _host_allowlist_problem(rel_dir.split("/")[0], key, value)
                if why == "embedded":
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} embeds a host expression inside a "
                        f"larger {key} entry. The entry has to BE the host: something like "
                        f"https://{{{{ instance_host }}}} renders a URL, and the app compares a "
                        f"bare host, so it matches nothing")
                elif why == "unrecognised":
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} builds {key} from a Jinja expression "
                        f"this validator does not recognise. A host allowlist accepts only "
                        f"{{{{ instance_url.split(\"://\")[1] }}}} (the ported host), "
                        f"{{{{ instance_host }}}} or its split equivalent (the bare host), and "
                        f"plain literals. Notably {{{{ instance_url }}}} is NOT one of them: it "
                        f"renders a scheme and possibly a port, and the app compares a bare host")
                elif why == "control-flow":
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} builds {key} with Jinja control "
                        f"flow ({{% ... %}}). Whether the bare host survives depends on what "
                        f"the greffer renders, which CI cannot decide by reading the template, "
                        f"so the allowlist has to be written without it")
                elif why:
                    errors.append(
                        f"{rel_dir}: service {svc_name!r} sets {key} from "
                        f"instance_url WITH its port and never the bare host. The greffer's "
                        f"sidecar proxies with `Host $host`, which drops the port, so this "
                        f"allowlist cannot match and the app rejects every request on any "
                        f"deployment whose URL has a port. Add "
                        f"{{{{ instance_url.split(\"://\")[1].split(\":\")[0] }}}} alongside it")
    # Same reasoning: `configurations: 1` is truthy and not iterable, and a
    # configuration whose default_value is a scalar has no .get. Both are ordinary
    # validation failures reported elsewhere, so guard the shapes rather than
    # letting them terminate the run.
    meta_configs = meta.get("configurations")
    for cfg in (meta_configs if isinstance(meta_configs, list) else []):
        if not isinstance(cfg, dict):
            continue
        raw_default = cfg.get("default_value")
        default = raw_default.get("value") if isinstance(raw_default, dict) else None
        dests = cfg.get("destinations")
        if not isinstance(dests, list):
            continue
        for dest in dests:
            if not isinstance(dest, dict):
                continue
            why = _host_allowlist_problem(rel_dir.split("/")[0], dest.get("key"), default)
            if why == "embedded":
                errors.append(
                    f"{rel_dir}: configuration {cfg.get('title')!r} embeds a host expression "
                    f"inside a larger {dest.get('key')} entry. The entry has to BE the host; a "
                    f"URL around it matches no Host header")
            elif why == "unrecognised":
                errors.append(
                    f"{rel_dir}: configuration {cfg.get('title')!r} builds {dest.get('key')} "
                    f"from a Jinja expression this validator does not recognise. Use the ported "
                    f"host, the bare host ({{{{ instance_host }}}}), or plain literals. "
                    f"{{{{ instance_url }}}} renders a scheme and cannot match a Host header")
            elif why == "control-flow":
                errors.append(
                    f"{rel_dir}: configuration {cfg.get('title')!r} builds "
                    f"{dest.get('key')} with Jinja control flow ({{% ... %}}). What it renders "
                    f"cannot be decided by reading the template, so write the allowlist "
                    f"without it")
            elif why:
                errors.append(
                    f"{rel_dir}: configuration {cfg.get('title')!r} defaults "
                    f"{dest.get('key')} to instance_url WITH its port and never the bare "
                    f"host. The sidecar's `Host $host` drops the port, so the app rejects "
                    f"every request on a ported URL. Add "
                    f"{{{{ instance_url.split(\"://\")[1].split(\":\")[0] }}}} alongside it")

    # L4 per-port declarations (optional `ports` list). Mirrors the structural
    # checks in the manager's import_catalog._validate_meta (the importer is
    # still authoritative server-side) so a malformed entry fails at CI, not
    # only at import. One deliberate divergence: the same_port version floor
    # below is stricter here than in the importer (see that block).
    ports_meta = meta.get("ports")
    if ports_meta is not None and not isinstance(ports_meta, list):
        errors.append(f"{rel_dir}: metadata.json 'ports' must be a list")
        ports_meta = []
    exposed_ports = _compose_exposed_ports(compose) if isinstance(compose, dict) else {}
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
        # A raw UDP (Tier-C) port is default-denied by the manager
        # (assert_udp_allowed) unless its catalog entry has been reviewed as
        # non-amplifiable. Such a port with udp_reviewed != true validates here
        # but then fails to start, so require the review flag at CI (record the
        # rationale in a `review_note`).
        if p.get("exposure_tier") == "l4" and p.get("protocol") == "udp" \
                and p.get("udp_reviewed") is not True:
            errors.append(
                f"{rel_dir}: ports[{pname!r}] is a raw UDP (l4) port and requires "
                f"'udp_reviewed': true (the manager default-denies unreviewed UDP "
                f"as a reflection/amplification guard)")
        # The published transport follows metadata `protocol`: the greffer clears
        # the compose ports and republishes L4 ports from it, dropping the compose
        # `/proto` suffix. A metadata/compose protocol mismatch therefore ships
        # the wrong transport (e.g. the compose marks `/udp` but metadata defaults
        # to tcp, so a UDP app gets a TCP port and, conversely, an unreviewed UDP
        # intent slips the udp_reviewed gate). It is almost always an authoring
        # slip, so reject it and keep the two in sync.
        compose_proto = exposed_ports.get(pname)
        if compose_proto is not None and p.get("protocol") in (None, "tcp", "udp"):
            meta_proto = p.get("protocol") or "tcp"
            if meta_proto != compose_proto:
                errors.append(
                    f"{rel_dir}: ports[{pname!r}].protocol ({meta_proto!r}) does not "
                    f"match the compose port's transport ({compose_proto!r}); the "
                    f"published transport follows metadata, so align them")
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
        for p in ports_meta:
            if not (isinstance(p, dict) and p.get("same_port")):
                continue
            pname = p.get("name")
            if isinstance(pname, str) and pname.strip() and pname not in exposed_ports:
                errors.append(
                    f"{rel_dir}: ports[{pname!r}] sets same_port but names a port "
                    f"the compose does not expose (exposed: {sorted(exposed_ports)}); "
                    f"the greffer rewrite would target nothing")

    # Cross-check: top-level volumes must be referenced by at least one service mount.
    if isinstance(compose, dict) and compose_volumes and isinstance(compose.get("services"), dict):
        used_volumes = set()
        for svc_def in compose["services"].values():
            if not isinstance(svc_def, dict):
                continue
            # `volumes: 1` is malformed compose, and iterating it raised TypeError,
            # which aborted `--all` and discarded every diagnostic collected so far
            # for every entry. Left alone earlier in this branch because I could not
            # make it crash; the regression test for the sibling helper reaches it.
            svc_vols = svc_def.get("volumes")
            if not isinstance(svc_vols, list):
                continue
            for vol_entry in svc_vols:
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
        _services = compose.get("services") if isinstance(compose, dict) else None
        if isinstance(_services, dict):
            svc_def = _services.get(svc)
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
            elif isinstance(required_config, dict) and isinstance(meta, dict):
                # Keys are configuration TITLES. A key matching no title pins
                # nothing: the CI smoke then generates a random value, and a
                # spec that logs in with a hardcoded credential fails on every
                # run with no hint as to why. Catch the typo here instead.
                titles = {
                    c.get("title")
                    for c in (meta.get("configurations") or [])
                    if isinstance(c, dict)
                }
                unknown = sorted(k for k in required_config if k not in titles)
                if unknown:
                    errors.append(
                        f"{prefix} 'required_config' keys match no configuration title: "
                        f"{unknown}"
                    )

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
