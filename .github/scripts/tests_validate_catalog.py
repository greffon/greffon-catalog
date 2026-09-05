#!/usr/bin/env python3
"""Regression tests for the catalog linter.

Each test reproduces a real bug we shipped before the linter existed and
asserts the linter would now catch it. Run via:

    python3 .github/scripts/tests_validate_catalog.py

The tests build minimal valid greffon directories in a tmpdir, mutate one
property to reintroduce the bug, and check the linter raises the expected
error string.
"""
import base64
import json
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from validate_catalog import (
    KNOWN_INTEGRATION_NAMESPACES,
    _render_block_problem,
    _value_references_smtp,
    _value_uses_integration,
    _integration_field_refs,
    validate_greffon_dir,
)


def _write_greffon(tmpdir, *, metadata, compose_yaml=None, app="test"):
    """Write a complete greffon dir under tmpdir/<app>/1.0/. Returns the rel dir.

    `app` matters for rules keyed by application rather than by setting name (the
    host-allowlist map), where the parser is a property of the app."""
    greffon_dir = os.path.join(tmpdir, app, "1.0")
    os.makedirs(greffon_dir, exist_ok=True)
    with open(os.path.join(greffon_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f)
    with open(os.path.join(greffon_dir, "docker-compose.yml"), "w") as f:
        f.write(compose_yaml or textwrap.dedent("""\
            services:
              app:
                image: nginx
                volumes:
                  - data:/data
            volumes:
              data:
            """))
    return f"{app}/1.0"


def _base_metadata(**overrides):
    base = {
        "name": "Test",
        "description": "Test greffon",
        "configurations": [],
    }
    base.update(overrides)
    return base


class FreqtradePhantomRequiredTest(unittest.TestCase):
    """Freqtrade shipped with `required: ['unfilledtimeout', ...]` referencing
    fields that didn't exist in `properties` or `default_value`."""

    def test_phantom_required_field_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[{
                "title": "Configuration",
                "schema": {
                    "type": "object",
                    "required": ["max_open_trades", "unfilledtimeout"],
                    "properties": {"max_open_trades": {"type": "integer"}},
                },
                "default_value": {"max_open_trades": 3},
                "destinations": [{"type": "env", "container": "app", "key": "FT_CFG"}],
            }]))
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any("schema.required 'unfilledtimeout'" in e for e in errs),
                f"expected phantom-required error, got {errs}",
            )


class FileDestinationEmptyDefaultTest(unittest.TestCase):
    """Plausible/Freqtrade had `file` destinations with `default_value: {}`,
    causing greffer to crash with KeyError: 'file' on install-from-defaults."""

    def test_empty_file_default_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[{
                "title": "Strategy",
                "schema": {"properties": {"file": {"type": "string", "format": "data-url"}}},
                "default_value": {},
                "destinations": [{"type": "file", "volume": "data", "name": "x.py"}],
            }]))
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any("file destination has no default_value.file" in e for e in errs),
                f"expected empty-file-default error, got {errs}",
            )

    def test_required_file_no_default_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[{
                "title": "Strategy",
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "data-url"}},
                },
                "default_value": {"file": ""},
                "destinations": [{"type": "file", "volume": "data", "name": "x.py"}],
            }]))
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("file destination" in e for e in errs),
                f"required+empty-default file should pass, got {errs}",
            )


class SecretEmptyDefaultTest(unittest.TestCase):
    """Catches `Admin Password: ""` and similar shipped without `required`."""

    def test_password_with_empty_default_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[{
                "title": "ADMIN_PASSWORD",
                "schema": {"properties": {"value": {"type": "string"}}},
                "default_value": {"value": ""},
                "destinations": [{"type": "env", "container": "app", "key": "PASSWORD"}],
            }]))
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any("looks like a secret" in e for e in errs),
                f"expected secret-empty-default error, got {errs}",
            )

    def test_required_password_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[{
                "title": "ADMIN_PASSWORD",
                "schema": {
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "string"}},
                },
                "default_value": {"value": ""},
                "destinations": [{"type": "env", "container": "app", "key": "PASSWORD"}],
            }]))
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("looks like a secret" in e for e in errs),
                f"required password should pass, got {errs}",
            )

    def test_opt_out_passes(self):
        """OpenClaw any-of: ANTHROPIC_API_KEY is fine empty if user uses OpenAI."""
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[{
                "title": "ANTHROPIC_API_KEY",
                "schema": {"properties": {"value": {"type": "string"}}},
                "default_value": {"value": ""},
                "destinations": [{"type": "env", "container": "app", "key": "ANTHROPIC_API_KEY"}],
                "x-greffon-allow-empty-secret": True,
            }]))
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(any("looks like a secret" in e for e in errs))


class ReservedTldEmailTest(unittest.TestCase):
    """GlitchTip's `admin@greffon.local` was rejected by Pydantic email-validator."""

    def test_local_tld_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[{
                "title": "ADMIN_EMAIL",
                "schema": {"properties": {"value": {"type": "string", "format": "email"}}},
                "default_value": {"value": "admin@greffon.local"},
                "destinations": [{"type": "env", "container": "app", "key": "ADMIN_EMAIL"}],
            }]))
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any("reserved/special-use TLD '.local'" in e for e in errs),
                f"expected reserved-TLD error, got {errs}",
            )

    def test_real_tld_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[{
                "title": "ADMIN_EMAIL",
                "schema": {"properties": {"value": {"type": "string", "format": "email"}}},
                "default_value": {"value": "admin@greffon.io"},
                "destinations": [{"type": "env", "container": "app", "key": "ADMIN_EMAIL"}],
            }]))
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("reserved/special-use TLD" in e for e in errs),
                f"valid TLD should pass, got {errs}",
            )


class MissingComposeTest(unittest.TestCase):
    """If docker-compose.yml is absent, the validator must still run all
    metadata checks and report the missing-file error — not crash with
    UnboundLocalError on `compose`. Caught by Codex review on PR #7."""

    def test_no_compose_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            greffon_dir = os.path.join(tmp, "test", "1.0")
            os.makedirs(greffon_dir, exist_ok=True)
            with open(os.path.join(greffon_dir, "metadata.json"), "w") as f:
                json.dump(_base_metadata(), f)
            # Intentionally NO docker-compose.yml
            errs = validate_greffon_dir(tmp, "test/1.0")
            self.assertTrue(
                any("missing required file 'docker-compose.yml'" in e for e in errs),
                f"expected missing-compose error, got {errs}",
            )


class DanglingVolumeTest(unittest.TestCase):
    """A top-level `volumes: { db_data: }` declared but never mounted is dead code
    that often signals a mis-pasted compose. Catch it."""

    def test_dangling_volume_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(
                tmp,
                metadata=_base_metadata(),
                compose_yaml=textwrap.dedent("""\
                    services:
                      app:
                        image: nginx
                    volumes:
                      orphan_data:
                    """),
            )
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any("'orphan_data' is declared but never mounted" in e for e in errs),
                f"expected dangling-volume error, got {errs}",
            )


# ---------------------------------------------------------------------------
# SMTP integration destination type (HLD: Integrations / Feature #2).
#
# Rules 5.1 / 5.2 / 5.3 / 5.4 / 5.5 — the new `smtp` destination type and the
# bidirectional metadata-to-compose match.
# ---------------------------------------------------------------------------


SMTP_METADATA_BLOCK = {
    "title": "SMTP",
    "schema": {"properties": {}},
    "default_value": {},
    "destinations": [
        {"type": "smtp", "container": "app", "key": "SMTP_HOST"},
        {"type": "smtp", "container": "app", "key": "SMTP_PORT"},
    ],
}


class SmtpValidDeclarationTest(unittest.TestCase):
    """A minimal SMTP-aware greffon: both metadata destinations have matching
    `{{ smtp.* }}` Jinja values in the compose — passes cleanly."""

    def test_valid_smtp_declaration_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(
                tmp,
                metadata=_base_metadata(configurations=[SMTP_METADATA_BLOCK]),
                compose_yaml=textwrap.dedent("""\
                    services:
                      app:
                        image: nginx
                        environment:
                          SMTP_HOST: "{{ smtp.host }}"
                          SMTP_PORT: "{{ smtp.port }}"
                    """),
            )
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("SMTP" in e or "smtp" in e for e in errs),
                f"valid smtp declaration should pass, got {errs}",
            )


class SmtpMetadataDeclaredButComposeMissingTest(unittest.TestCase):
    """Metadata declares `smtp` destinations, but the compose file doesn't
    have the env keys at all — must error in the metadata-to-compose direction
    (Rule 5.3 direction 1)."""

    def test_missing_compose_key_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(
                tmp,
                metadata=_base_metadata(configurations=[SMTP_METADATA_BLOCK]),
                compose_yaml=textwrap.dedent("""\
                    services:
                      app:
                        image: nginx
                        environment:
                          OTHER_KEY: "value"
                    """),
            )
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any(
                    "declares SMTP env key 'SMTP_HOST'" in e
                    and "not present in docker-compose.yml's environment" in e
                    for e in errs
                ),
                f"expected metadata-without-compose error, got {errs}",
            )

    def test_compose_key_present_but_not_jinja_caught(self):
        """Metadata declares smtp destination, compose has the key but its
        value doesn't reference `smtp.*` — caught with a separate error
        instructing the maintainer to use the Jinja reference."""
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(
                tmp,
                metadata=_base_metadata(configurations=[SMTP_METADATA_BLOCK]),
                compose_yaml=textwrap.dedent("""\
                    services:
                      app:
                        image: nginx
                        environment:
                          SMTP_HOST: "smtp.hardcoded.example"
                          SMTP_PORT: "{{ smtp.port }}"
                    """),
            )
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any(
                    "declares SMTP env key 'SMTP_HOST'" in e
                    and "does not reference the 'smtp' Jinja context" in e
                    for e in errs
                ),
                f"expected compose-key-without-smtp-jinja error, got {errs}",
            )


class SmtpJinjaRefWithoutMetadataTest(unittest.TestCase):
    """A compose file references `{{ smtp.* }}` in an env value but the
    corresponding `smtp` destination is missing from metadata.json — must
    error in the compose-to-metadata direction (Rule 5.3 direction 2)."""

    def test_jinja_ref_without_metadata_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(
                tmp,
                metadata=_base_metadata(configurations=[]),
                compose_yaml=textwrap.dedent("""\
                    services:
                      app:
                        image: nginx
                        environment:
                          SMTP_HOST: "{{ smtp.host }}"
                    """),
            )
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any(
                    "env 'SMTP_HOST'" in e
                    and "references the smtp Jinja context" in e
                    and "no smtp destination" in e
                    for e in errs
                ),
                f"expected jinja-without-metadata error, got {errs}",
            )


class SmtpListFormEnvironmentTest(unittest.TestCase):
    """Rule 5.4: a service with an smtp destination whose `environment:` is
    list form (`["KEY=value", ...]`) is rejected — mapping form is required
    so the bidirectional Jinja match is well-defined."""

    def test_list_form_environment_with_smtp_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(
                tmp,
                metadata=_base_metadata(configurations=[SMTP_METADATA_BLOCK]),
                compose_yaml=textwrap.dedent("""\
                    services:
                      app:
                        image: nginx
                        environment:
                          - "SMTP_HOST={{ smtp.host }}"
                          - "SMTP_PORT={{ smtp.port }}"
                    """),
            )
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any(
                    "service 'app' has smtp destination(s)" in e
                    and "list-form" in e
                    and "convert to mapping form" in e
                    for e in errs
                ),
                f"expected list-form-environment error, got {errs}",
            )


class SmtpNonSmtpGreffonUnchangedTest(unittest.TestCase):
    """Rule 5.5: a greffon that has no smtp destinations AND no `{{ smtp.* }}`
    Jinja fragments in compose gets zero new errors. Guards the "additive,
    no net change for existing greffons" promise."""

    def test_plain_env_greffon_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(
                tmp,
                metadata=_base_metadata(configurations=[{
                    "title": "BASE_URL",
                    "schema": {"properties": {"value": {"type": "string"}}},
                    "default_value": {"value": "{{ instance_url }}"},
                    "destinations": [{"type": "env", "container": "app", "key": "BASE_URL"}],
                }]),
                compose_yaml=textwrap.dedent("""\
                    services:
                      app:
                        image: nginx
                        environment:
                          BASE_URL: "http://example.com"
                          DATABASE_URL: "postgres://postgres:postgres@db:5432/app"
                    """),
            )
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("smtp" in e.lower() for e in errs),
                f"non-SMTP greffon should not trigger smtp rules, got {errs}",
            )


class SmtpShapedJinjaExpressionsTest(unittest.TestCase):
    """The three real V1 expressions from the HLD — Plausible's conditional
    boolean, Nextcloud's dict-lookup (with literal braces), GlitchTip's
    composed URL — must all register as SMTP-referencing."""

    def _one_service_passes(self, key, value):
        meta_block = {
            "title": "SMTP",
            "schema": {"properties": {}},
            "default_value": {},
            "destinations": [{"type": "smtp", "container": "app", "key": key}],
        }
        compose = (
            "services:\n"
            "  app:\n"
            "    image: nginx\n"
            "    environment:\n"
            f"      {key}: {json.dumps(value)}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(
                tmp,
                metadata=_base_metadata(configurations=[meta_block]),
                compose_yaml=compose,
            )
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("smtp" in e.lower() for e in errs),
                f"shaped SMTP Jinja should pass for key={key}, got {errs}",
            )

    # All three of the following use *double-quoted* Jinja string
    # literals. PyYAML's yaml.dump round-trip in the greffer wraps env
    # values in single quotes and escapes any inner single quote as
    # '' — turning `'true'` into the bareword `true` and breaking
    # Jinja parse. Catalog templates therefore stick to double quotes
    # inside the {{ … }} expression. (The validator must also accept
    # this shape; see the matching test_*_round_trips_through_yaml
    # tests below for the regression guard.)

    def test_plausible_boolean_expression(self):
        self._one_service_passes(
            "SMTP_HOST_SSL_ENABLED",
            '{{ "true" if smtp.tls_mode == "tls" else "false" }}',
        )

    def test_nextcloud_dict_lookup_with_literal_braces(self):
        self._one_service_passes(
            "SMTP_SECURE",
            '{{ {"tls": "ssl", "starttls": "tls", "none": ""}[smtp.tls_mode] }}',
        )

    def test_glitchtip_composed_url(self):
        self._one_service_passes(
            "EMAIL_URL",
            'smtp{{ "s" if smtp.tls_mode == "tls" else "" }}://'
            "{{ smtp.username | urlencode }}:{{ smtp.password | urlencode }}@"
            "{{ smtp.host }}:{{ smtp.port }}"
            '{% if smtp.tls_mode == "starttls" %}?tls=True{% endif %}',
        )


class SmtpDestinationBadContainerTest(unittest.TestCase):
    """Rule 5.2: an smtp destination pointing at a non-existent service
    errors just like an `env` destination does."""

    def test_missing_container_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(
                tmp,
                metadata=_base_metadata(configurations=[{
                    "title": "SMTP",
                    "schema": {"properties": {}},
                    "default_value": {},
                    "destinations": [
                        {"type": "smtp", "container": "nonexistent", "key": "SMTP_HOST"},
                    ],
                }]),
                compose_yaml=textwrap.dedent("""\
                    services:
                      app:
                        image: nginx
                        environment:
                          SMTP_HOST: "{{ smtp.host }}"
                    """),
            )
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any(
                    "references container 'nonexistent'" in e
                    and "not found in docker-compose.yml services" in e
                    for e in errs
                ),
                f"expected missing-container error, got {errs}",
            )


class SmtpRealCatalogPassesTest(unittest.TestCase):
    """Rule 5.5: the live catalog's three SMTP-aware greffons (Plausible,
    Nextcloud, GlitchTip) validate cleanly under the extended rules."""

    def test_plausible_passes(self):
        from validate_catalog import find_catalog_root
        root = find_catalog_root()
        errs = validate_greffon_dir(root, "plausible/1.0")
        self.assertEqual(errs, [], f"plausible/1.0 should pass, got {errs}")

    def test_nextcloud_passes(self):
        from validate_catalog import find_catalog_root
        root = find_catalog_root()
        errs = validate_greffon_dir(root, "nextcloud/1.0")
        self.assertEqual(errs, [], f"nextcloud/1.0 should pass, got {errs}")

    def test_glitchtip_passes(self):
        from validate_catalog import find_catalog_root
        root = find_catalog_root()
        errs = validate_greffon_dir(root, "glitchtip/1.0")
        self.assertEqual(errs, [], f"glitchtip/1.0 should pass, got {errs}")


class SmtpJinjaRegexTest(unittest.TestCase):
    """Unit tests for `_value_references_smtp` — addresses Codex 2xP2 on PR #10:

    1. Case-sensitive: `{{ SMTP.host }}` is NOT a valid SMTP reference because
       Jinja variable lookup is case-sensitive (it would render to undefined).
    2. Scoped to `{{ ... }}`: `smtp.host` sitting OUTSIDE a Jinja expression is
       not a reference; the previous regex wrongly accepted
       `"{{ instance_url }} smtp.host"` as SMTP-managed.
    """

    def test_simple_smtp_reference_matches(self):
        self.assertTrue(_value_references_smtp("{{ smtp.host }}"))

    def test_plausible_conditional_matches(self):
        # Plausible's boolean expression — uses double-quoted string
        # literals inside Jinja so the value survives yaml.dump's
        # single-quote-escape round-trip in the greffer.
        #
        # It is a GUARD (a conditional test), so the two directions of
        # Rule 5.3 answer differently, and both answers are load-bearing:
        # the declared destination is satisfied (`_value_uses_integration`),
        # while the value needs no destination on its own account
        # (`_value_references_integration`) because the greffer keeps a
        # guard rather than stripping it. visio/1.0 ships this shape.
        value = '{{ "true" if smtp.tls_mode == "tls" else "false" }}'
        self.assertTrue(_value_uses_integration(value, 'smtp'))
        # `_value_references_smtp` is now an alias for the same loose
        # question; the guard-aware predicate it used to name is gone.
        self.assertTrue(_value_references_smtp(value))

    def test_glitchtip_composed_url_matches(self):
        # GlitchTip's multi-expression URL: at least one `{{ ... smtp.* ... }}`
        # block must trigger the match.
        self.assertTrue(_value_references_smtp(
            'smtp{{ "s" if smtp.tls_mode == "tls" else "" }}://'
            "{{ smtp.username | urlencode }}:{{ smtp.password | urlencode }}@"
            "{{ smtp.host }}:{{ smtp.port }}"
            '{% if smtp.tls_mode == "starttls" %}?tls=True{% endif %}'
        ))

    def test_nextcloud_dict_lookup_matches(self):
        # Nextcloud's dict-literal inside a Jinja block — the regex must admit
        # `{` and `}` that aren't a full `}}` boundary.
        self.assertTrue(_value_references_smtp(
            '{{ {"tls": "ssl", "starttls": "tls", "none": ""}[smtp.tls_mode] }}'
        ))

    def test_smtp_reference_outside_braces_rejected(self):
        # Codex P2 #2: `smtp.host` is outside any `{{ }}` block, so this is a
        # malformed env mapping that would render literally. Must be rejected.
        self.assertFalse(_value_references_smtp("{{ instance_url }} smtp.host"))

    def test_uppercase_smtp_rejected(self):
        # Codex P2 #1: Jinja lookup is case-sensitive; `SMTP.host` renders to
        # undefined. Must be rejected.
        self.assertFalse(_value_references_smtp("{{ SMTP.host }}"))

    def test_smtps_prefix_rejected(self):
        # Identifier is `smtps.host`, not `smtp.host` — the `.` doesn't follow
        # `smtp` directly, so the reference is not to the SMTP context.
        self.assertFalse(_value_references_smtp("{{ smtps.host }}"))

    def test_bare_smtp_host_without_jinja_rejected(self):
        self.assertFalse(_value_references_smtp("smtp.host"))

    def test_word_boundary_prevents_notsmtp_match(self):
        # `\b` fails mid-word: `notsmtp.host` must NOT match `smtp.host`.
        self.assertFalse(_value_references_smtp("{{ notsmtp.host }}"))

    def test_empty_string_rejected(self):
        self.assertFalse(_value_references_smtp(""))

    def test_non_string_rejected(self):
        self.assertFalse(_value_references_smtp(None))


class JinjaSurvivesYamlDumpRoundTrip(unittest.TestCase):
    """Regression guard for the bug found during the integrations-epic
    QA on 2026-05-04: catalog templates using single-quoted string
    literals inside Jinja `{{ … }}` were broken because the greffer's
    render path is `yaml.dump(compose) → Template(...).render()`.
    PyYAML wraps env values in single-quoted scalars and doubles any
    inner single quote as ''; Jinja then sees `''true''` as
    `empty + bareword + empty` and raises TemplateSyntaxError.

    The fix is to use double-quoted string literals inside Jinja —
    PyYAML doesn't need to escape those when the outer wrapper is
    single-quoted. These tests simulate the round-trip in-process so
    a future catalog template with the wrong quoting fails CI before
    it hits a real deploy.
    """

    @staticmethod
    def _round_trip(value, *, smtp_context):
        """Mirror greffer/apps/utils/docker/compose.py:create_compose:
        load → mutate → yaml.dump → Jinja Template → render.
        Returns the rendered string (or raises if Jinja can't parse)."""
        import yaml
        from jinja2 import Template

        compose = {"services": {"app": {"environment": {"X": value}}}}
        rendered = Template(yaml.dump(compose)).render(smtp=smtp_context)
        return rendered

    SMTP_CONTEXT = {
        "host": "smtp.example.com",
        "port": 587,
        "username": "u",
        "password": "p",
        "from_address": "noreply@example.com",
        "tls_mode": "starttls",
    }

    def test_plausible_boolean_round_trips(self):
        out = self._round_trip(
            '{{ "true" if smtp.tls_mode == "tls" else "false" }}',
            smtp_context=self.SMTP_CONTEXT,
        )
        self.assertIn("X: 'false'", out)

    def test_nextcloud_dict_lookup_round_trips(self):
        out = self._round_trip(
            '{{ {"tls": "ssl", "starttls": "tls", "none": ""}[smtp.tls_mode] }}',
            smtp_context=self.SMTP_CONTEXT,
        )
        # tls_mode='starttls' → 'tls' on Nextcloud's mapping
        self.assertIn("X: 'tls'", out)

    def test_glitchtip_email_url_round_trips(self):
        out = self._round_trip(
            'smtp{{ "s" if smtp.tls_mode == "tls" else "" }}://'
            "{{ smtp.username | urlencode }}:{{ smtp.password | urlencode }}@"
            "{{ smtp.host }}:{{ smtp.port }}"
            '{% if smtp.tls_mode == "starttls" %}?tls=True{% endif %}',
            smtp_context=self.SMTP_CONTEXT,
        )
        self.assertIn("smtp://u:p@smtp.example.com:587?tls=True", out)

    def test_single_quoted_jinja_breaks_after_round_trip(self):
        """Confirms the failure mode the fix above prevents — a
        template with the broken quoting must raise. If this ever
        starts passing it means PyYAML changed its escape rule and
        the assert above can be relaxed; until then, this is the
        canary that stops anyone from undoing the catalog fix."""
        from jinja2 import TemplateSyntaxError
        with self.assertRaises(TemplateSyntaxError):
            self._round_trip(
                "{{ 'true' if smtp.tls_mode == 'tls' else 'false' }}",
                smtp_context=self.SMTP_CONTEXT,
            )


class L4PortsMetadataTest(unittest.TestCase):
    """The `ports[]` L4 declarations added for the l4-network-exposure epic:
    exposure_tier / protocol / boolean shapes, the same_port->l4 pairing, the
    same_port min_greffer_version floor, and the compose-name cross-check that
    mirrors the importer's hard-fail on a same_port port the compose doesn't
    expose."""

    # Exposes app_51820/udp and app_51821/tcp (so the cross-check has real
    # names to match against).
    COMPOSE = textwrap.dedent("""\
        services:
          app:
            image: nginx
            ports:
              - "51821:51821"
              - "51820:51820/udp"
            volumes:
              - data:/data
        volumes:
          data:
        """)

    def _errs(self, *, ports, min_greffer_version=None):
        with tempfile.TemporaryDirectory() as tmp:
            extra = {"ports": ports}
            if min_greffer_version is not None:
                extra["min_greffer_version"] = min_greffer_version
            rel = _write_greffon(
                tmp, metadata=_base_metadata(**extra), compose_yaml=self.COMPOSE)
            return validate_greffon_dir(tmp, rel)

    def test_valid_l4_ports_block_passes(self):
        errs = self._errs(
            ports=[
                {"name": "app_51820", "exposure_tier": "l4", "protocol": "udp",
                 "udp_reviewed": True, "same_port": True},
                {"name": "app_51821", "exposure_tier": "http", "protocol": "tcp"},
            ],
            min_greffer_version="0.3.3")
        self.assertFalse(
            any("ports[" in e or "min_greffer_version" in e for e in errs),
            f"valid L4 ports block should pass, got {errs}")

    def test_ports_not_a_list_rejected(self):
        errs = self._errs(ports={"name": "app_80"})
        self.assertTrue(
            any("'ports' must be a list" in e for e in errs),
            f"expected 'ports must be a list' error, got {errs}")

    def test_bad_exposure_tier_rejected(self):
        errs = self._errs(ports=[{"name": "app_51821", "exposure_tier": "internal"}])
        self.assertTrue(
            any("exposure_tier must be 'http' or 'l4'" in e for e in errs),
            f"expected exposure_tier error, got {errs}")

    def test_bad_protocol_rejected(self):
        errs = self._errs(ports=[{"name": "app_51821", "protocol": "sctp"}])
        self.assertTrue(
            any("protocol must be 'tcp' or 'udp'" in e for e in errs),
            f"expected protocol error, got {errs}")

    def test_non_bool_udp_reviewed_rejected(self):
        errs = self._errs(ports=[{"name": "app_51821", "udp_reviewed": "yes"}])
        self.assertTrue(
            any("udp_reviewed must be a boolean" in e for e in errs),
            f"expected udp_reviewed bool error, got {errs}")

    def test_same_port_on_http_tier_rejected(self):
        errs = self._errs(
            ports=[{"name": "app_51821", "exposure_tier": "http", "same_port": True}],
            min_greffer_version="0.3.3")
        self.assertTrue(
            any("same_port requires exposure_tier 'l4'" in e for e in errs),
            f"expected same_port->l4 error, got {errs}")

    def test_same_port_floor_below_0_3_3_rejected(self):
        errs = self._errs(
            ports=[{"name": "app_51820", "exposure_tier": "l4", "protocol": "udp",
                    "udp_reviewed": True, "same_port": True}],
            min_greffer_version="0.3.2")
        self.assertTrue(
            any("requires 'min_greffer_version'" in e for e in errs),
            f"expected min_greffer_version floor error, got {errs}")

    def test_same_port_floor_at_0_3_3_passes(self):
        errs = self._errs(
            ports=[{"name": "app_51820", "exposure_tier": "l4", "protocol": "udp",
                    "udp_reviewed": True, "same_port": True}],
            min_greffer_version="0.3.3")
        self.assertFalse(
            any("min_greffer_version" in e for e in errs),
            f"0.3.3 floor should pass, got {errs}")

    def test_same_port_name_not_exposed_rejected(self):
        # app_99999 is not in the compose -> the greffer rewrite would target
        # nothing (the importer hard-fails this; so must CI).
        errs = self._errs(
            ports=[{"name": "app_99999", "exposure_tier": "l4", "same_port": True}],
            min_greffer_version="0.3.3")
        self.assertTrue(
            any("the compose does not expose" in e for e in errs),
            f"expected same_port name cross-check error, got {errs}")

    def test_same_port_name_exposed_passes(self):
        errs = self._errs(
            ports=[{"name": "app_51820", "exposure_tier": "l4", "protocol": "udp",
                    "udp_reviewed": True, "same_port": True}],
            min_greffer_version="0.3.3")
        self.assertFalse(
            any("the compose does not expose" in e for e in errs),
            f"exposed same_port name should pass cross-check, got {errs}")

    def test_udp_l4_without_reviewed_rejected(self):
        # l4 + udp without udp_reviewed:true validates here but the manager
        # default-denies it at start, so CI must reject it.
        errs = self._errs(
            ports=[{"name": "app_51820", "exposure_tier": "l4", "protocol": "udp"}])
        self.assertTrue(
            any("requires 'udp_reviewed': true" in e for e in errs),
            f"expected udp_reviewed-required error, got {errs}")

    def test_udp_l4_reviewed_false_rejected(self):
        errs = self._errs(ports=[{
            "name": "app_51820", "exposure_tier": "l4",
            "protocol": "udp", "udp_reviewed": False}])
        self.assertTrue(
            any("requires 'udp_reviewed': true" in e for e in errs),
            f"udp_reviewed:false should be rejected, got {errs}")

    def test_udp_l4_reviewed_true_passes(self):
        errs = self._errs(ports=[{
            "name": "app_51820", "exposure_tier": "l4",
            "protocol": "udp", "udp_reviewed": True}])
        self.assertFalse(
            any("udp_reviewed" in e for e in errs),
            f"reviewed udp l4 port should pass, got {errs}")

    def test_protocol_mismatch_with_compose_rejected(self):
        # compose app_51820 is published /udp; declaring it tcp would ship the
        # wrong transport (the greffer republishes L4 ports from metadata).
        errs = self._errs(ports=[{
            "name": "app_51820", "exposure_tier": "l4", "protocol": "tcp"}])
        self.assertTrue(
            any("does not match the compose port" in e for e in errs),
            f"expected protocol mismatch error, got {errs}")

    def test_protocol_omitted_defaults_tcp_mismatch_rejected(self):
        # No protocol -> defaults tcp, but compose app_51820 is /udp: a UDP app
        # would silently get a TCP port. This is the case Codex flagged.
        errs = self._errs(ports=[{"name": "app_51820", "exposure_tier": "l4"}])
        self.assertTrue(
            any("does not match the compose port" in e for e in errs),
            f"omitted protocol on a /udp compose port should be rejected, got {errs}")

    def test_protocol_match_with_compose_passes(self):
        errs = self._errs(ports=[{
            "name": "app_51820", "exposure_tier": "l4",
            "protocol": "udp", "udp_reviewed": True}])
        self.assertFalse(
            any("does not match the compose port" in e for e in errs),
            f"matching protocol should pass, got {errs}")


class ComposeShapeRobustnessTest(unittest.TestCase):
    """The validator must report a malformed compose as a lint error, not crash
    on it (Codex P3: a non-mapping `services` made `_compose_exposed_ports` and
    the volume cross-check raise AttributeError and abort the whole run)."""

    def test_non_mapping_services_does_not_crash(self):
        compose = textwrap.dedent("""\
            services:
              - app
            volumes:
              data:
            """)
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(), compose_yaml=compose)
            errs = validate_greffon_dir(tmp, rel)  # must not raise
        self.assertTrue(
            any("services" in e for e in errs),
            f"expected a 'services' shape error, got {errs}")


def _data_uri(text):
    return "data:text/plain;base64," + base64.b64encode(text.encode("utf-8")).decode("ascii")


def _feature_errors(errs):
    """baked-config-files errors only (filter out the minimal fixture's
    unrelated missing-smoke-test noise)."""
    needles = ("x-greffon-visibility", "x-greffon-render", "render-flagged", "hidden config",
               "integration namespace", "config.")
    return [e for e in errs if any(n in e for n in needles)]


class VisibilityFlagTest(unittest.TestCase):
    """baked-config-files: x-greffon-visibility enum, placement, hidden-default."""

    def _run(self, schema, default_value, destinations=None):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[{
                "title": "C",
                "schema": schema,
                "default_value": default_value,
                "destinations": destinations or [{"type": "env", "container": "app", "key": "K"}],
            }]))
            return validate_greffon_dir(tmp, rel)

    def test_valid_advanced_passes(self):
        errs = self._run(
            {"properties": {"value": {"type": "string"}}, "x-greffon-visibility": "advanced"},
            {"value": "x"},
        )
        self.assertFalse(any("x-greffon-visibility" in e for e in errs), errs)

    def test_invalid_value_rejected(self):
        errs = self._run({"properties": {}, "x-greffon-visibility": "bogus"}, {})
        self.assertTrue(any("x-greffon-visibility 'bogus' invalid" in e for e in errs), errs)

    def test_flag_at_config_root_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[{
                "title": "C",
                "x-greffon-visibility": "hidden",  # wrong place — ingestion drops it
                "schema": {"properties": {"value": {"type": "string"}}},
                "default_value": {"value": "x"},
                "destinations": [{"type": "env", "container": "app", "key": "K"}],
            }]))
            errs = validate_greffon_dir(tmp, rel)
        self.assertTrue(any("must live inside 'schema'" in e for e in errs), errs)

    def test_hidden_without_default_rejected(self):
        errs = self._run(
            {"properties": {"value": {"type": "string"}}, "x-greffon-visibility": "hidden"}, {}
        )
        self.assertTrue(any("hidden config" in e and "default_value" in e for e in errs), errs)


class RenderFlagTest(unittest.TestCase):
    """baked-config-files: x-greffon-render type-gating + render-flagged content."""

    def _run(self, dest, default_value, schema=None):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[{
                "title": "C",
                "schema": schema or {"properties": {"file": {"type": "string", "format": "data-url"}}},
                "default_value": default_value,
                "destinations": [dest],
            }]))
            return validate_greffon_dir(tmp, rel)

    def test_render_on_env_rejected(self):
        errs = self._run(
            {"type": "env", "container": "app", "key": "K", "x-greffon-render": True},
            {"value": "x"},
            schema={"properties": {"value": {"type": "string"}}},
        )
        self.assertTrue(any("only valid on file/json" in e for e in errs), errs)

    def test_render_non_bool_rejected(self):
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": "yes"},
            {"file": _data_uri("hello")},
        )
        self.assertTrue(any("must be a boolean" in e for e in errs), errs)

    def test_render_file_valid_passes(self):
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True},
            {"file": _data_uri("url = {{ instance_url }}")},
        )
        # No baked-config-files error (an unrelated missing-smoke-test error from
        # the minimal fixture is fine).
        self.assertFalse(_feature_errors(errs), errs)

    def test_render_file_non_utf8_rejected(self):
        uri = "data:application/octet-stream;base64," + base64.b64encode(b"\xff\xfe").decode("ascii")
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True}, {"file": uri}
        )
        self.assertTrue(any("not valid/UTF-8" in e for e in errs), errs)

    def test_render_file_smtp_reference_rejected(self):
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True},
            {"file": _data_uri("host = {{ smtp.host }}")},
        )
        self.assertTrue(any("render-flagged" in e and "smtp" in e for e in errs), errs)

    def test_render_file_config_ref_without_env_key_rejected(self):
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True},
            {"file": _data_uri("secret = {{ config.MISSING_KEY }}")},
        )
        self.assertTrue(any("config.MISSING_KEY" in e for e in errs), errs)

    def test_render_file_config_ref_with_matching_env_key_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[
                {
                    "title": "Secret",
                    "schema": {"properties": {"value": {
                        "type": "string", "writeOnly": True, "minLength": 8, "format": "greffon-secret",
                    }}},
                    "default_value": {"value": ""},
                    "destinations": [{"type": "env", "container": "app", "key": "OIDC_SECRET"}],
                },
                {
                    "title": "Realm",
                    "schema": {"properties": {"file": {"type": "string", "format": "data-url"}}},
                    "default_value": {"file": _data_uri("secret = {{ config.OIDC_SECRET }}")},
                    "destinations": [{"type": "file", "volume": "data", "name": "realm", "x-greffon-render": True}],
                },
            ]))
            errs = validate_greffon_dir(tmp, rel)
        self.assertFalse(_feature_errors(errs), errs)


    def test_uppercase_base64_flag_rejected(self):
        # Greffer's datauri rejects `;BASE64` (lowercase only); the validator
        # (same lib) must too — a false-accept would fail only at deploy.
        uri = "data:text/plain;BASE64," + base64.b64encode(b"hello").decode("ascii")
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True}, {"file": uri}
        )
        self.assertTrue(any("not valid/UTF-8-decodable" in e for e in errs), errs)

    def test_percent_encoded_default_passes(self):
        from urllib.parse import quote
        uri = "data:text/plain," + quote("url = {{ instance_url }}")
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True}, {"file": uri}
        )
        self.assertFalse(_feature_errors(errs), errs)

    def test_config_get_bypass_rejected(self):
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True},
            {"file": _data_uri("secret = {{ config.get('X') }}")},
        )
        self.assertTrue(any("render-flagged" in e and "unsafe" in e for e in errs), errs)

    def test_bypass_in_statement_block_rejected(self):
        # A {% %} statement is not allowed in a baked file (bypass-prone).
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True},
            {"file": _data_uri("{% set s = config.get('X') %}secret={{ s }}")},
        )
        self.assertTrue(any("statement block" in e for e in errs), errs)

    def test_default_filter_bypass_rejected(self):
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True},
            {"file": _data_uri("secret = {{ config.X | default('') }}")},
        )
        self.assertTrue(any("render-flagged" in e and "default" in e for e in errs), errs)

    def test_d_alias_filter_bypass_rejected(self):
        # The `| d` alias for `default` must be caught too (blocklist missed it).
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True},
            {"file": _data_uri("secret = {{ config.X | d('') }}")},
        )
        self.assertTrue(any("render-flagged" in e and "'|d'" in e for e in errs), errs)

    def test_attr_filter_and_paren_get_rejected(self):
        # config|attr('get')(...) and (config).get(...) bypass StrictUndefined
        # silently — the allowlist rejects them (any call/subscript).
        for body in ("{{ config|attr('get')('X') }}", "{{ (config).get('X') }}",
                     "{{ config['X'] }}", "{{ config.X or 'fallback' }}"):
            errs = self._run(
                {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True},
                {"file": _data_uri(body)},
            )
            self.assertTrue(any("render-flagged" in e and "unsafe" in e for e in errs),
                            f"{body!r} should be rejected, got {errs}")

    def test_config_dict_method_rejected(self):
        # {{ config.get }} (a dict method, uncalled) renders garbage, not a value.
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True},
            {"file": _data_uri("x = {{ config.get }}")},
        )
        self.assertTrue(any("dict method" in e for e in errs), errs)

    def test_tojson_filter_and_concat_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[
                {"title": "S", "schema": {"properties": {"value": {"type": "string"}}},
                 "default_value": {"value": "x"},
                 "destinations": [{"type": "env", "container": "app", "key": "S"}]},
                {"title": "Realm",
                 "schema": {"properties": {"file": {"type": "string", "format": "data-url"}}},
                 "default_value": {"file": _data_uri('{"u": "{{ instance_url }}/cb", "s": {{ config.S | tojson }}}')},
                 "destinations": [{"type": "file", "volume": "data", "name": "f", "x-greffon-render": True}]},
            ]))
            errs = validate_greffon_dir(tmp, rel)
        self.assertFalse(_feature_errors(errs), errs)

    def test_bypass_idiom_in_plain_prose_not_flagged(self):
        # A literal "| default" / "config.get(" in file prose (outside any
        # {{ }} block) must NOT be flagged — only Jinja-expression idioms are.
        body = "# tuning: leave logging | default off; do not call config.get() here\nlevel = info\n"
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True},
            {"file": _data_uri(body)},
        )
        self.assertFalse(any("render-flagged" in e and "unsafe" in e for e in errs), errs)

    def test_multiple_config_refs_in_one_block_all_checked(self):
        # Two refs in a SINGLE {{ }} block; the second is a typo with no env key.
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(configurations=[
                {
                    "title": "User",
                    "schema": {"properties": {"value": {"type": "string"}}},
                    "default_value": {"value": "u"},
                    "destinations": [{"type": "env", "container": "app", "key": "USER"}],
                },
                {
                    "title": "Realm",
                    "schema": {"properties": {"file": {"type": "string", "format": "data-url"}}},
                    "default_value": {"file": _data_uri("{{ config.USER ~ ':' ~ config.PASS }}")},
                    "destinations": [{"type": "file", "volume": "data", "name": "f", "x-greffon-render": True}],
                },
            ]))
            errs = validate_greffon_dir(tmp, rel)
        self.assertTrue(any("config.PASS" in e for e in errs), errs)   # 2nd ref caught
        self.assertFalse(any("config.USER" in e for e in errs), errs)  # 1st ref matches an env key

    def test_render_json_smtp_reference_rejected(self):
        errs = self._run(
            {"type": "json", "volume": "data", "name": "f.json", "x-greffon-render": True},
            {"host": "{{ smtp.host }}"},
            schema={"properties": {}},
        )
        self.assertTrue(any("render-flagged" in e and "smtp" in e for e in errs), errs)

    def test_render_json_config_ref_without_env_key_rejected(self):
        errs = self._run(
            {"type": "json", "volume": "data", "name": "f.json", "x-greffon-render": True},
            {"secret": "{{ config.MISSING }}"},
            schema={"properties": {}},
        )
        self.assertTrue(any("config.MISSING" in e for e in errs), errs)


class FieldScanEdgeShapesTest(unittest.TestCase):
    """Shapes a mutation audit found unpinned. Each already behaves
    correctly; nothing pinned it, so each was one edit from regressing.
    """

    def _fields(self, value):
        return _integration_field_refs(value, 'oidc')

    def test_a_non_string_get_argument_does_not_crash_the_validator(self):
        # `{{ oidc.get(5) }}` -- the caller does `sorted(fields)`, so a
        # non-string field name raises TypeError comparing int to str
        # and the validator dies instead of reporting.
        self.assertEqual(self._fields('{{ oidc.get(5) }}{{ oidc.issuer }}'),
                         {'<dynamic>', 'issuer'})
        self.assertEqual(sorted(self._fields('{{ oidc.get(5) }}')),
                         ['<dynamic>'])

    def test_a_set_block_alias_is_refused(self):
        # `{% set x %}...{% endset %}` is an AssignBlock, a different
        # node from `{% set x = ... %}`, and binds the mapping just as
        # effectively.
        self.assertEqual(
            self._fields('{% set x %}{{ oidc }}{% endset %}{{ x }}'),
            {'<dynamic>'})

    def test_a_parse_failure_contributes_no_fields(self):
        # Rule 5.6 reports the syntax error. Guessing field names out of
        # a template nobody can parse would add a second, bogus error.
        self.assertEqual(self._fields('{{ oidc['), set())

    def test_a_loop_that_SHADOWS_the_name_is_not_the_integration(self):
        # `{% for oidc in xs %}` rebinds the name locally, so `oidc.a`
        # inside is the loop variable. Refusing it would reject a valid
        # entry; the greffer agrees and keeps such a value.
        self.assertEqual(self._fields(
            '{% for oidc in xs %}{{ oidc.a }}{% endfor %}'), {'a'})

    def test_binding_an_attr_lookup_is_binding_a_FIELD(self):
        # `oidc|attr("issuer")` evaluates to the field's value, not the
        # mapping, so aliasing it is readable rather than `<dynamic>`.
        self.assertEqual(
            self._fields('{% set y = oidc|attr("issuer") %}{{ y }}'),
            {'issuer'})


class ValuesMustSurviveTheDumpRoundTripTest(unittest.TestCase):
    """The greffer renders `yaml.dump(compose)`, and the dump doubles
    single quotes, so a single-quoted Jinja literal stops being valid
    Jinja by the time it is rendered -- with the integration CONFIGURED,
    the path least likely to be tested. This README taught that exact
    spelling.
    """

    def _errors(self, value):
        with tempfile.TemporaryDirectory() as tmp:
            md = _base_metadata()
            compose = ("services:\n  app:\n    image: nginx\n"
                       "    environment:\n      K: " + value + "\n")
            rel = _write_greffon(tmp, metadata=md, compose_yaml=compose)
            return validate_greffon_dir(tmp, rel)

    def test_single_quoted_jinja_literals_are_rejected(self):
        # json.dumps gives a correctly DOUBLE-quoted YAML scalar, so the
        # single quotes inside reach the validator intact. Quoting this
        # by hand produced invalid YAML and the test passed on the wrong
        # error.
        errs = self._errors(json.dumps(
            "{{ 'X' if smtp.tls_mode == 'tls' else 'Y' }}"))
        self.assertTrue(any('round-trip' in e for e in errs), errs)

    def test_double_quoted_jinja_literals_are_accepted(self):
        errs = self._errors(json.dumps(
            '{{ "X" if smtp.tls_mode == "tls" else "Y" }}'))
        self.assertFalse(any('round-trip' in e for e in errs), errs)

    def test_a_value_that_is_not_valid_jinja_says_so(self):
        errs = self._errors(json.dumps('{{ oidc. }}'))
        self.assertTrue(any('is not valid Jinja' in e for e in errs), errs)


class OidcSupportedFieldsTest(unittest.TestCase):
    """`oidc` supplies only `issuer` today.

    The shape matcher accepts any syntactically valid field, so
    `{{ oidc.client_id }}` -- which does not exist yet -- and a typo like
    `{{ oidc.isssuer }}` both satisfied the bidirectional check while
    rendering empty at deploy.
    """

    def _errors(self, value):
        with tempfile.TemporaryDirectory() as tmp:
            md = _base_metadata()
            md["configurations"] = [{
                "name": "c", "type": "text",
                "destinations": [
                    {"type": "oidc", "container": "app", "key": "K"}],
            }]
            compose = ("services:\n  app:\n    image: nginx\n"
                       "    environment:\n      K: '" + value + "'\n")
            rel = _write_greffon(tmp, metadata=md, compose_yaml=compose)
            return [e for e in validate_greffon_dir(tmp, rel)
                    if "does not supply" in e]

    def test_the_supported_field_is_accepted(self):
        self.assertFalse(self._errors("{{ oidc.issuer }}"))

    def test_a_field_that_does_not_exist_yet_is_rejected(self):
        self.assertTrue(self._errors("{{ oidc.client_id }}"))

    def test_a_typo_is_rejected(self):
        self.assertTrue(self._errors("{{ oidc.isssuer }}"))

    def test_a_field_name_inside_a_string_literal_is_data_not_a_lookup(self):
        # `default("https://oidc.client_id")` is a hostname, not a context
        # read. Scanning the raw scalar rejected this valid issuer-only value.
        self.assertFalse(
            self._errors('{{ oidc.issuer | default("https://oidc.client_id") }}'))

    def test_a_field_name_outside_a_jinja_block_is_data_not_a_lookup(self):
        self.assertFalse(self._errors("{{ oidc.issuer }}#oidc.client_id"))

    def test_a_bracket_lookup_is_a_field_read_too(self):
        # Jinja reads a field three ways. Scanning only the dotted form
        # let `{{ oidc.issuer }}:{{ oidc['client_id'] }}` through: the
        # dotted half marked the value OIDC-managed while the bracket
        # half read a field the greffer does not supply.
        self.assertTrue(
            self._errors("{{ oidc.issuer }}:{{ oidc[\"client_id\"] }}"))

    def test_an_attr_lookup_is_a_field_read_too(self):
        self.assertTrue(
            self._errors("{{ oidc.issuer }}{{ oidc|attr(\"client_id\") }}"))

    def test_whitespace_around_the_dot_is_still_a_field_read(self):
        # Jinja allows it; a text scan required `oidc.` with nothing
        # between. Parsing sees the same Getattr either way.
        self.assertTrue(self._errors(
            "{{ oidc.issuer }}{% if oidc . client_id %}:enabled{% endif %}"))

    def test_operations_on_the_field_value_are_not_fields(self):
        # Only the FIRST access off the mapping names a field. A string
        # method applied to the result is an operation on the issuer,
        # and reporting it rejected valid entries -- the catalog already
        # does this with `smtp.from_address.split('@')` in nextcloud.
        for value in ("{{ oidc.issuer.rstrip('/') }}",
                      "{{ oidc.issuer.split('://')[1] }}"):
            with self.subTest(value=value):
                self.assertFalse(self._errors(value))

    def test_binding_a_FIELD_VALUE_is_not_aliasing(self):
        # `{% set base = oidc.issuer %}` binds the field's VALUE, which
        # is readable and already counted. Only binding the MAPPING
        # moves reads out of sight. Testing every child of the binding
        # node conflated the two and refused a valid entry.
        for value in ('{% set base = oidc.issuer %}{{ base }}/realms/x',
                      '{% with b = oidc.issuer %}{{ b }}{% endwith %}',
                      '{% for p in oidc.issuer.split("/") %}{{ p }}{% endfor %}'):
            with self.subTest(value=value):
                self.assertFalse(self._errors(value))

    def test_a_dict_method_is_not_a_field(self):
        # An integration is bound to a dict, so `oidc.get` is the
        # built-in. The greffer documents `.get(k, default)` as safe
        # when the integration is unset; reporting `get` as a missing
        # field rejected it.
        # DOUBLE quotes inside: `_errors` wraps the value in a
        # single-quoted YAML scalar, so single quotes here break the
        # parse and the check never runs.
        self.assertFalse(self._errors('{{ oidc.get("issuer", "") }}'))

    def test_get_names_its_field_in_the_argument(self):
        self.assertTrue(self._errors('{{ oidc.get("client_id") }}'))

    def test_a_with_block_alias_is_refused(self):
        self.assertTrue(self._errors(
            "{{ oidc.issuer }}{% with x=oidc %}{{ x.client_id }}{% endwith %}"))

    def test_a_loop_alias_is_refused(self):
        self.assertTrue(self._errors(
            "{{ oidc.issuer }}{% for x in [oidc] %}{{ x.client_id }}{% endfor %}"))

    def test_a_lookup_through_a_wrapper_is_still_a_field_read(self):
        # The receiver need not BE the name: `dict(oidc)['client_id']`
        # wraps it in a call and reads the field all the same.
        self.assertTrue(self._errors(
            "{{ oidc.issuer }}{{ dict(oidc)[\"client_id\"] }}"))

    def test_aliasing_the_namespace_is_refused(self):
        # `{% set x = oidc %}` moves the reads onto a name this scan
        # does not follow, so the entry is refused rather than guessed at.
        self.assertTrue(self._errors(
            "{% set x = oidc %}{{ x.client_id }}"))

    def test_a_subscript_the_validator_cannot_resolve_is_refused(self):
        # `oidc[var]` names a field only at render time. Refuse what
        # cannot be read rather than wave it through.
        self.assertTrue(self._errors("{{ oidc.issuer }}{{ oidc[var] }}"))

    def test_a_field_read_in_a_statement_block_counts(self):
        # A compose value can read a field in `{% ... %}` too. Scanning
        # expressions alone reported only `issuer` here, so the
        # unsupported lookup passed.
        self.assertTrue(self._errors(
            "{{ oidc.issuer }}{% if oidc.client_id %}:enabled{% endif %}"))

    def test_a_supported_field_read_in_a_statement_block_is_accepted(self):
        self.assertFalse(self._errors(
            "{{ oidc.issuer }}{% if oidc.issuer %}:enabled{% endif %}"))

    def test_a_supported_field_read_by_bracket_is_accepted(self):
        self.assertFalse(
            self._errors("{{ oidc.issuer }}:{{ oidc[\"issuer\"] }}"))

    def test_smtp_is_not_field_checked(self):
        # Its field set is long-established; enforcing it here would
        # reject shipping entries for no new safety.
        with tempfile.TemporaryDirectory() as tmp:
            md = _base_metadata()
            md["configurations"] = [{
                "name": "c", "type": "text",
                "destinations": [
                    {"type": "smtp", "container": "app", "key": "K"}],
            }]
            compose = ("services:\n  app:\n    image: nginx\n"
                       "    environment:\n      K: '{{ smtp.anything }}'\n")
            rel = _write_greffon(tmp, metadata=md, compose_yaml=compose)
            self.assertFalse([e for e in validate_greffon_dir(tmp, rel)
                              if "does not supply" in e])


class OidcBidirectionalKeyMatchTest(unittest.TestCase):
    """Rule 5.3, for oidc as well as smtp.

    The rule was smtp-only, so an `oidc` destination naming a mistyped or
    absent env key passed validation -- the destination is only the
    marker for a value rendered from `oidc.*`, and nothing checked the
    two agreed. The entry would ship and the app would never receive its
    issuer.
    """

    DEST = {"type": "oidc", "container": "app", "key": "OIDC_ISSUER"}

    def _errors(self, compose_env, dest=None):
        with tempfile.TemporaryDirectory() as tmp:
            md = _base_metadata()
            md["configurations"] = [{
                "name": "c", "type": "text",
                "destinations": [dest] if dest else [],
            }]
            compose = ("services:\n  app:\n    image: nginx\n"
                       "    environment:\n" + compose_env)
            rel = _write_greffon(tmp, metadata=md, compose_yaml=compose)
            return validate_greffon_dir(tmp, rel)

    def test_a_matching_pair_is_accepted(self):
        errs = self._errors("      OIDC_ISSUER: '{{ oidc.issuer }}'\n", self.DEST)
        self.assertFalse([e for e in errs if "oidc" in e.lower()], errs)

    def test_a_mistyped_compose_key_is_caught(self):
        errs = self._errors("      OIDC_ISUER: '{{ oidc.issuer }}'\n", self.DEST)
        self.assertTrue(any("not present in docker-compose" in e for e in errs), errs)

    def test_a_value_that_does_not_reference_oidc_is_caught(self):
        errs = self._errors("      OIDC_ISSUER: 'literal'\n", self.DEST)
        self.assertTrue(
            any("does not reference the 'oidc' Jinja context" in e for e in errs),
            errs)

    def test_a_reference_with_no_destination_is_caught(self):
        errs = self._errors("      OIDC_ISSUER: '{{ oidc.issuer }}'\n", None)
        self.assertTrue(
            any("has no oidc destination for it" in e for e in errs), errs)

    def test_the_smtp_rule_is_unaffected(self):
        errs = self._errors("      SMTP_HOST: '{{ smtp.host }}'\n", None)
        self.assertTrue(
            any("has no smtp destination for it" in e for e in errs), errs)

    def test_a_whole_mapping_read_is_a_reference(self):
        # The greffer's rule is "any non-guard USE", not "a field
        # lookup". These read the mapping itself, are stripped at deploy
        # when the integration is unset, and were invisible here: with a
        # destination they were rejected, without one they passed.
        for value in ('{{ oidc|tojson }}', '{{ oidc }}',
                      '{{ oidc.items()|list }}', '{{ oidc.copy().issuer }}'):
            with self.subTest(value=value):
                errs = self._errors(
                    "      OIDC_ISSUER: '" + value + "'\n", self.DEST)
                self.assertFalse([e for e in errs if 'oidc' in e.lower()], errs)

    def test_a_whole_mapping_read_without_a_destination_is_caught(self):
        errs = self._errors("      OIDC_ISSUER: '{{ oidc|tojson }}'\n", None)
        self.assertTrue(
            any('has no oidc destination for it' in e for e in errs), errs)

    def test_a_guard_needs_a_destination_TOO(self):
        # Deliberately looser than the greffer, which KEEPS a guard-only
        # value. A guard-aware mirror of the greffer's rule drifted from
        # it within a day, and CI then told authors a key was fine while
        # the greffer dropped it. Requiring a destination for any use is
        # the safe direction, and it is what shipping entries already do
        # -- plausible, grafana and visio all declare destinations for
        # their `{{ "true" if smtp... }}` flags.
        for value in ('{% if oidc %}on{% else %}off{% endif %}',
                      '{{ "y" if oidc.issuer else "n" }}'):
            with self.subTest(value=value):
                errs = self._errors(
                    "      SOMETHING: '" + value + "'\n", None)
                self.assertTrue(
                    any('has no oidc destination for it' in e for e in errs),
                    errs)

    def test_a_guard_satisfies_a_declared_destination(self):
        # The other direction, and the reason the loose predicate is the
        # one to keep: visio/1.0 ships exactly this with a destination.
        errs = self._errors(
            "      OIDC_ISSUER: '{% if oidc %}on{% else %}off{% endif %}'\n",
            self.DEST)
        self.assertFalse([e for e in errs if 'oidc' in e.lower()], errs)

    def test_a_bracket_only_reference_is_a_reference(self):
        # Valid Jinja reading the same field. Requiring the dotted form
        # reported this as "does not reference the context" against its
        # own destination.
        for value in ('{{ oidc[\"issuer\"] }}', "{{ oidc|attr('issuer') }}"):
            with self.subTest(value=value):
                errs = self._errors(
                    "      OIDC_ISSUER: '" + value + "'\n", self.DEST)
                self.assertFalse(
                    [e for e in errs if "oidc" in e.lower()], errs)

    def test_a_name_inside_a_string_literal_is_not_a_reference(self):
        # `{{ "oidc.issuer" }}` renders the literal text, so the app
        # gets `oidc.issuer` where the issuer URL belongs. Counting it
        # as a read let the destination look satisfied.
        errs = self._errors(
            "      OIDC_ISSUER: '{{ \"oidc.issuer\" }}'\n", self.DEST)
        self.assertTrue(
            any("does not reference the 'oidc' Jinja context" in e
                for e in errs), errs)

    def test_a_bracket_only_reference_with_no_destination_is_caught(self):
        # Without this it slipped past BOTH checks: no dotted match here,
        # so the supported-field scan behind it never ran either.
        errs = self._errors(
            "      OIDC_ISSUER: '{{ oidc[\"client_id\"] }}'\n", None)
        self.assertTrue(
            any("has no oidc destination for it" in e for e in errs), errs)

    def test_a_greffon_using_neither_is_untouched(self):
        errs = self._errors("      PLAIN: 'x'\n", None)
        self.assertFalse(
            [e for e in errs if "smtp" in e.lower() or "oidc" in e.lower()], errs)


class OidcDestinationTypeTest(unittest.TestCase):
    """An `oidc` destination must be declarable, like `smtp`.

    The greffer's pass 1 pops env keys by `destination.type`, so without
    this the oidc half of the feature had no way to be declared: an
    author writing `type: oidc` was rejected by the validator, and the
    type could only ever be reached through the template scan.
    """

    def _errors(self, dest):
        with tempfile.TemporaryDirectory() as tmp:
            md = _base_metadata()
            md["configurations"] = [
                {"name": "c", "type": "text", "destinations": [dest]}
            ]
            rel = _write_greffon(
                tmp, metadata=md,
                compose_yaml="services:\n  app:\n    image: nginx\n")
            return validate_greffon_dir(tmp, rel)

    def test_an_oidc_destination_is_accepted(self):
        errs = self._errors(
            {"type": "oidc", "container": "app", "key": "OIDC_ISSUER"})
        self.assertFalse([e for e in errs if "invalid type" in e], errs)

    def test_an_oidc_destination_must_target_a_real_service(self):
        errs = self._errors(
            {"type": "oidc", "container": "nope", "key": "OIDC_ISSUER"})
        self.assertTrue(
            any("not found in docker-compose.yml" in e for e in errs), errs)

    def test_an_unknown_type_is_still_rejected(self):
        errs = self._errors({"type": "ldap", "container": "app", "key": "K"})
        self.assertTrue(any("invalid type" in e for e in errs), errs)

    def test_oidc_does_not_feed_the_smtp_bidirectional_rule(self):
        # Rule 5.3 cross-checks declared smtp destinations against
        # `{{ smtp.* }}` env values. An oidc destination must not be
        # counted there, or it would demand a matching smtp reference.
        with tempfile.TemporaryDirectory() as tmp:
            md = _base_metadata()
            md["configurations"] = [{
                "name": "c", "type": "text",
                "destinations": [
                    {"type": "oidc", "container": "app", "key": "OIDC_ISSUER"}],
            }]
            rel = _write_greffon(
                tmp, metadata=md,
                compose_yaml="services:\n  app:\n    image: nginx\n")
            errs = validate_greffon_dir(tmp, rel)
        self.assertFalse([e for e in errs if "smtp" in e.lower()], errs)


class IntegrationNamespaceParityTest(unittest.TestCase):
    """Tripwire: pin the validator's integration-namespace list. It is a copy of
    the greffer's KNOWN_INTEGRATION_TYPES (separate repo — this test can't import
    it). Pinning forces a deliberate, reviewed edit; when the greffer adds an
    integration type, this assertion (and the linked comment in validate_catalog)
    must be updated in the same change so the integration-reference check doesn't
    silently fail open for the new namespace."""

    def test_known_namespaces_pinned(self):
        self.assertEqual(KNOWN_INTEGRATION_NAMESPACES, ("smtp", "oidc"))


    def test_the_allowlist_is_what_actually_rejects_a_namespace(self):
        # The tuple is a tripwire, not the enforcement: `_RENDER_ALLOWED_BARE`
        # is an allowlist and refuses any unknown name without being told it.
        # Pin that, so the comment on KNOWN_INTEGRATION_NAMESPACES cannot
        # quietly become a lie -- every listed namespace must be refused in a
        # render-flagged baked file, and refused BY NAME.
        for ns in KNOWN_INTEGRATION_NAMESPACES:
            problem = _render_block_problem("{{ %s.field }}" % ns)
            self.assertIsNotNone(problem, ns)
            self.assertIn(ns, problem)


class OneShotStatusLabelTest(unittest.TestCase):
    """glitchtip's `glitchtip_seed` and docs/visio's `createbuckets` are one-shot
    helpers that exit after their job. They are not named `migrate`, so the
    greffer counted them as stopped containers and reported the `unknow` status
    the manager rejects (HTTP 400). They must carry `com.greffon.status: ignore`
    so the greffer excludes them from instance status."""

    _ONESHOT_COMPOSE = textwrap.dedent("""\
        services:
          app:
            image: nginx
          createbuckets:
            image: minio/mc
            entrypoint: sh -c "/usr/bin/mc mb x && exit 0;"
        """)

    def test_unlabeled_one_shot_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(), compose_yaml=self._ONESHOT_COMPOSE)
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any("one-shot service 'createbuckets'" in e for e in errs),
                f"expected one-shot label error, got {errs}",
            )

    def test_labeled_one_shot_passes(self):
        compose = textwrap.dedent("""\
            services:
              app:
                image: nginx
              createbuckets:
                image: minio/mc
                labels:
                  com.greffon.status: "ignore"
                entrypoint: sh -c "/usr/bin/mc mb x && exit 0;"
            """)
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(), compose_yaml=compose)
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("one-shot service" in e for e in errs),
                f"labeled one-shot should pass, got {errs}",
            )

    def test_labeled_one_shot_list_form_passes(self):
        """`labels` may be a `key=value` list, not only a mapping."""
        compose = textwrap.dedent("""\
            services:
              app:
                image: nginx
              app_migrate:
                image: nginx
                command: ./manage.py migrate
                labels:
                  - "com.greffon.status=ignore"
            """)
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(), compose_yaml=compose)
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("one-shot service" in e for e in errs),
                f"list-form label should pass, got {errs}",
            )

    def test_migrate_named_service_caught(self):
        compose = textwrap.dedent("""\
            services:
              app:
                image: nginx
              app_migrate:
                image: nginx
                command: ./manage.py migrate
            """)
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(), compose_yaml=compose)
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any("one-shot service 'app_migrate'" in e for e in errs),
                f"expected migrate-named one-shot error, got {errs}",
            )

    def test_plain_long_running_service_not_flagged(self):
        """A normal server (no exit-0, not minio/mc, not migrate-named) must not
        be forced to carry the label. Guards against false positives."""
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata())  # default app: nginx
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("one-shot service" in e for e in errs),
                f"plain service should not be flagged, got {errs}",
            )

    def test_sigterm_trap_exit0_not_flagged(self):
        """A long-running server whose command has a clean-shutdown SIGTERM trap
        (`trap 'exit 0' TERM; app & wait`) ends with the server command, not with
        `exit 0`, so it must NOT be classified as a one-shot. Forcing the label
        here would make the greffer skip a real app container in status checks.
        (Codex review on PR #58.)"""
        compose = textwrap.dedent("""\
            services:
              app:
                image: myapp
                command: sh -c "trap 'exit 0' TERM; myapp & wait"
            """)
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(), compose_yaml=compose)
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("one-shot service" in e for e in errs),
                f"SIGTERM-trap server should not be flagged, got {errs}",
            )

    def test_restart_always_never_one_shot(self):
        """`restart: always` means stay-up by definition, so even a terminal
        `exit 0` (unusual but possible) must not classify the service as a
        one-shot."""
        compose = textwrap.dedent("""\
            services:
              app:
                image: myapp
                restart: always
                command: sh -c "myapp || exit 0"
            """)
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=_base_metadata(), compose_yaml=compose)
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("one-shot service" in e for e in errs),
                f"restart:always service should not be flagged, got {errs}",
            )


class SmokeRequiredConfigTitleTest(unittest.TestCase):
    """Linkding pinned credentials in smoke_test.json that the CI smoke runner
    never read; glitchtip's spec logged in with a password the runner had
    randomized. Once required_config is honoured, a key that matches no
    configuration title silently pins nothing and the spec fails on every run
    with no hint why — so the linter must reject the typo."""

    @staticmethod
    def _write_smoke(tmp, rel, required_config):
        path = os.path.join(tmp, rel, "smoke_test.json")
        with open(path, "w") as f:
            json.dump({
                "path": "/",
                "expected_status": [200],
                "expected_body_contains": None,
                "required_config": required_config,
            }, f)

    def _metadata(self):
        return _base_metadata(configurations=[{
            "title": "ADMIN_PASSWORD",
            "schema": {"properties": {"value": {"type": "string"}}},
            "default_value": {"value": ""},
            "destinations": [{"type": "env", "container": "app", "key": "ADMIN_PASSWORD"}],
        }])

    def test_unknown_title_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=self._metadata())
            self._write_smoke(tmp, rel, {"ADMIN_PASSWROD": "x"})  # typo
            errs = validate_greffon_dir(tmp, rel)
            self.assertTrue(
                any("match no configuration title" in e for e in errs),
                f"expected unknown-title error, got {errs}",
            )

    def test_matching_title_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=self._metadata())
            self._write_smoke(tmp, rel, {"ADMIN_PASSWORD": "x"})
            errs = validate_greffon_dir(tmp, rel)
            self.assertFalse(
                any("match no configuration title" in e for e in errs),
                f"matching title should not be flagged, got {errs}",
            )



# ---------------------------------------------------------------------------
# Hot-backup pairing. Every check below reproduces a way an entry can declare
# backup and have it fail at RUNTIME while looking fine, which is exactly what
# shipped on keycloak/1.0: hooks and a healthcheck in the compose, no
# backup.volumes in the metadata, so the manager read the instance as
# unclassified, took a COLD backup, and never invoked the hooks.
# ---------------------------------------------------------------------------

_BACKUP_COMPOSE = textwrap.dedent("""\
    services:
      app:
        image: nginx
        ports:
          - "8080:8080"
      db:
        image: postgres:16-alpine
        labels:
          com.greffon.backup.dump: "pg_dump -U a -d a -Fc"
          com.greffon.backup.restore: "pg_restore -U a -d a --clean"
        healthcheck:
          test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U a -d a"]
        volumes:
          - db_data:/var/lib/postgresql/data
    volumes:
      db_data:
    """)


def _backup_meta(**over):
    meta = {
        "name": "Test", "logo": "https://example.com/l.png", "description": "d",
        "categories": [], "images": [], "configurations": [],
        "backup": {"volumes": {"db_data": "database"}},
    }
    meta.update(over)
    return meta


class HostAllowlistTest(unittest.TestCase):
    """A host allowlist built from instance_url must accept the PORT-LESS host.

    The sidecar proxies with `Host $host`, which drops the port, so an allowlist
    holding only host:port matches nothing. Three entries shipped that way.
    """

    HOSTPORT = '{{ instance_url.split("://")[1] }}'
    BARE = '{{ instance_url.split("://")[1].split(":")[0] }}'

    def _compose(self, key, value):
        # json.dumps for the scalar: JSON is valid YAML and escapes quotes
        # correctly. Hand-wrapping in single quotes silently mangled any value
        # containing one, which made a test fail against correct code.
        return textwrap.dedent(f"""\
            services:
              app:
                image: nginx
                ports:
                  - "8080:8080"
                environment:
                  {key}: {json.dumps(value)}
            """)

    # The map is keyed by (app, setting), so a fixture has to be written as the
    # app that owns the setting. Defaults to docs, which owns DJANGO_ALLOWED_HOSTS.
    def _run(self, *, compose=None, metadata=None, app="docs"):
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=metadata or _base_metadata(),
                                 compose_yaml=compose, app=app)
            return [e for e in validate_greffon_dir(tmp, rel) if "bare host" in e]

    def test_django_allowed_hosts_without_bare_host_is_rejected(self):
        errs = self._run(compose=self._compose("DJANGO_ALLOWED_HOSTS",
                                               f"{self.HOSTPORT},localhost,backend"))
        self.assertTrue(errs, "host:port-only allowlist must be rejected")

    def test_trusted_domains_without_bare_host_is_rejected(self):
        errs = self._run(compose=self._compose("NEXTCLOUD_TRUSTED_DOMAINS",
                                               f"{self.HOSTPORT} localhost"),
                         app="nextcloud")
        self.assertTrue(errs, errs)

    def test_both_forms_present_is_accepted(self):
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", f"{self.HOSTPORT},{self.BARE},localhost"))
        self.assertEqual(errs, [])

    def test_url_building_setting_is_not_flagged(self):
        """n8n's N8N_HOST and forgejo's server.DOMAIN legitimately want the port:
        they GENERATE urls rather than validate an incoming Host. Guards the rule
        against over-reach, which is how it stays useful."""
        for key in ("N8N_HOST", "FORGEJO__server__DOMAIN", "COLLABORATION_WS_URL"):
            with self.subTest(key=key):
                self.assertEqual(self._run(compose=self._compose(key, self.HOSTPORT)), [])

    def test_bare_host_inside_a_jinja_comment_does_not_satisfy_the_rule(self):
        """`{# ... #}` renders to nothing, so a bare host commented out is absent
        from the deployed value. Matching raw text let it satisfy the check."""
        commented = (f"{self.HOSTPORT}"
                     "{# " + self.BARE + " #}")
        errs = self._run(compose=self._compose("DJANGO_ALLOWED_HOSTS", commented))
        self.assertTrue(errs, "a commented-out bare host must not satisfy the rule")

    def test_any_jinja_comment_in_an_allowlist_is_refused(self):
        """DELIBERATE over-rejection, replacing an earlier assertion that this was
        fine. A commented-out ported host does render to just `localhost`, so
        refusing it rejects a harmless value.

        It is refused because the alternative is worse: telling a real `{# #}` from
        the characters "{#" inside a string literal cannot be done by text
        substitution, and getting it wrong deleted a genuine expression and reported
        the allowlist clean. An allowlist has no use for a comment, so the ambiguity
        is removed rather than resolved."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", "{# " + self.HOSTPORT + " #}localhost"))
        self.assertTrue(errs, "comments are refused in allowlists, not interpreted")

    def test_redirect_allowlist_is_not_flagged(self):
        """OIDC_REDIRECT_ALLOWED_HOSTS holds URLs, not bare hosts. It matches the
        name pattern, so without the exclusion the rule would demand a bare-host
        entry that would be wrong to add."""
        errs = self._run(compose=self._compose(
            "OIDC_REDIRECT_ALLOWED_HOSTS", f'["https://{self.HOSTPORT}"]'))
        self.assertEqual(errs, [])

    def test_csrf_trusted_origins_is_not_flagged(self):
        """Origins carry scheme and port by definition."""
        errs = self._run(compose=self._compose(
            "DJANGO_CSRF_TRUSTED_ORIGINS", f"https://{self.HOSTPORT}"))
        self.assertEqual(errs, [])

    def test_bare_host_in_a_dead_branch_is_rejected(self):
        """`{% if false %}` keeps the expression in the source and out of the
        rendered value, so a text search sees a bare host that never deploys."""
        val = self.HOSTPORT + "{% if false %}," + self.BARE + "{% endif %}"
        errs = self._run(compose=self._compose("DJANGO_ALLOWED_HOSTS", val))
        self.assertTrue(errs, "control flow must not be accepted on faith")

    def test_bare_host_inside_raw_is_rejected(self):
        """`{% raw %}` emits the expression as literal text instead of evaluating
        it, so the deployed allowlist contains the source, not a host."""
        val = self.HOSTPORT + "{% raw %}" + self.BARE + "{% endraw %}"
        errs = self._run(compose=self._compose("DJANGO_ALLOWED_HOSTS", val))
        self.assertTrue(errs, errs)

    def test_settings_outside_the_map_are_not_checked(self):
        """SECURITY_ALLOWED_HOSTS was in the map briefly and is not any more: no
        entry uses it, so its separator would have been a guess, and guessing is
        what the map exists to stop. Documents the deliberate coverage limit."""
        errs = self._run(compose=self._compose(
            "SECURITY_ALLOWED_HOSTS", f"{self.HOSTPORT},localhost"))
        self.assertEqual(errs, [])

    def test_bare_host_as_a_jinja_string_literal_is_rejected(self):
        """`{{ 'instance_url...' }}` renders the idiom as literal text instead of
        evaluating it, so the allowlist gains a useless string and no host."""
        quoted = self.BARE.replace("{{ ", "{{ '").replace(" }}", "' }}")
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", f"{self.HOSTPORT},{quoted}"))
        self.assertTrue(errs, errs)

    def test_computed_expression_is_refused_even_though_it_renders(self):
        """DELIBERATE over-rejection, and a reversal of the previous round.

        `{{ ",".join([hostport, bare]) }}` does render both forms, so refusing it is
        strictly speaking a false positive. It is refused anyway: accepting arbitrary
        expressions is what produced five separate bypasses (comments, {% if false %},
        {% raw %}, string literals, inline conditionals), each of which let a
        port-only allowlist through while CI called it safe. The validator cannot
        evaluate Jinja, so it recognises a few forms and refuses the rest.

        The trade is cheap in one direction and expensive in the other: refusing
        costs an author one rewrite into two expressions, with the accepted forms
        named in the error. Accepting costs a guarantee CI cannot deliver."""
        val = ('{{ ",".join([instance_url.split("://")[1], '
               'instance_url.split("://")[1].split(":")[0]]) }}')
        errs = self._run(compose=self._compose("DJANGO_ALLOWED_HOSTS", val))
        self.assertTrue(errs, "computed expressions are refused, not analysed")

    def test_full_instance_url_is_refused(self):
        """`{{ instance_url }}` renders a scheme and possibly a port, so it cannot
        match a Host header. The rule only knew the split idiom before, so this
        skipped validation entirely, and add-greffon.md recommended exactly this."""
        errs = self._run(compose=self._compose(
            "NEXTCLOUD_TRUSTED_DOMAINS", "{{ instance_url }} localhost"),
            app="nextcloud")
        self.assertTrue(errs, errs)

    def test_instance_host_counts_as_the_bare_host(self):
        """The platform already exposes the port-less host; it should be the easy
        way to satisfy this rule, not a form the validator fails to recognise."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", "{{ instance_host }},localhost"))
        self.assertEqual(errs, [])

    def test_inline_conditional_is_refused(self):
        """`{{ x if false }}` has no {% block %}, so the control-flow refusal missed
        it while the text search saw a bare host that never renders."""
        val = self.HOSTPORT + ',{{ instance_url.split("://")[1].split(":")[0] if false }}'
        errs = self._run(compose=self._compose("DJANGO_ALLOWED_HOSTS", val))
        self.assertTrue(errs, errs)

    def test_unrelated_literal_expression_does_not_block(self):
        """A constant alongside the two required forms is harmless. Rejecting on
        ANY literal expression blocked a correct allowlist."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", f'{self.HOSTPORT},{self.BARE},{{{{ "localhost" }}}}'))
        self.assertEqual(errs, [])

    def test_computed_host_port_is_not_a_constant(self):
        """`{{ "" ~ instance_host ~ ":" ~ instance_port ~ "" }}` renders host:port,
        the exact value this rule rejects. A regex spanning first quote to last
        called the whole computation a constant and let it through."""
        val = '{{ "" ~ instance_host ~ ":" ~ instance_port ~ "" }}'
        errs = self._run(compose=self._compose("DJANGO_ALLOWED_HOSTS", val))
        self.assertTrue(errs, "a computed host:port must not pass as a constant")

    def test_comment_markers_inside_a_literal_do_not_hide_an_expression(self):
        """`{{ "{#" if false else "" }}` puts comment characters in a STRING. A
        textual strip read them as a real comment and deleted the ported
        expression between them, leaving nothing to complain about."""
        val = ('{{ "{#" if false else "" }}' + self.HOSTPORT
               + '{{ "#}" if false else "" }}')
        errs = self._run(compose=self._compose("DJANGO_ALLOWED_HOSTS", val))
        self.assertTrue(errs, errs)

    def test_bare_host_embedded_in_a_url_is_rejected(self):
        """`https://{{ instance_host }}` contains a recognised bare-host expression
        and is still unusable: it renders a URL, and the app compares a bare Host.
        Checking that the expression APPEARED, rather than that the entry IS the
        host, accepted it."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", "https://{{ instance_host }}"))
        self.assertTrue(errs, "an expression inside a larger entry must be rejected")

    def test_bare_host_glued_to_a_port_is_rejected(self):
        """Same shape without a scheme: the entry has to be the host on its own."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", "{{ instance_host }}:8443"))
        self.assertTrue(errs, errs)

    def test_space_separated_allowlist_is_parsed_per_entry(self):
        """Nextcloud separates trusted_domains by SPACE while Django uses commas,
        and the idioms contain spaces themselves. Guards the splitter against
        tearing an expression in half."""
        errs = self._run(compose=self._compose(
            "NEXTCLOUD_TRUSTED_DOMAINS", f"{self.HOSTPORT} {self.BARE} localhost"),
            app="nextcloud")
        self.assertEqual(errs, [])

    def test_json_array_is_rejected_for_a_comma_separated_setting(self):
        """Reverses an earlier round, and is more correct than what it replaces.

        Django parses ALLOWED_HOSTS as a COMMA-separated string, so a JSON array is
        not a valid value for it and the brackets and quotes become part of the
        hosts. The previous version accepted it because the rule guessed at
        encodings; with the separator known per setting there is nothing to guess.
        An app that genuinely wants JSON gets an entry in the map with its own
        handling, rather than every setting inheriting a format none of them
        declared."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", '["{{ instance_host }}","localhost"]'))
        self.assertTrue(errs, "JSON is not Django's encoding for this setting")

    def test_unknown_allowlist_names_are_not_checked(self):
        """The map is deliberately narrow. A denylist (DISALLOWED_HOSTS) matched the
        old substring pattern, where the advice would have been backwards."""
        errs = self._run(compose=self._compose("DISALLOWED_HOSTS", self.HOSTPORT))
        self.assertEqual(errs, [])

    def test_semicolon_is_not_a_separator(self):
        """Neither Django nor Nextcloud splits on `;`, so this is ONE unusable entry
        even though it contains both idioms. A generic separator list accepted it."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", f"{self.HOSTPORT};{self.BARE}"))
        self.assertTrue(errs, errs)

    def test_misordered_delimiters_are_rejected(self):
        """`localhost }} {{ instance_host` has equal counts and invalid ordering.
        Counting delimiters passed it; masking well-formed pairs leaves the strays."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", "localhost }} {{ instance_host"))
        self.assertTrue(errs, errs)

    def test_placeholder_collision_does_not_crash(self):
        """A literal entry shaped like the internal masking token indexed past the
        table and raised IndexError, aborting --all for the whole catalog."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", f"{self.HOSTPORT},__GREFFON_EXPR_1__"))
        self.assertTrue(errs, errs)

    def test_json_array_missing_the_bare_host_is_rejected(self):
        """The encoding is understood, so the rule still applies inside it."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", f'["{self.HOSTPORT}","localhost"]'))
        self.assertTrue(errs, errs)

    def test_unbalanced_jinja_delimiters_are_rejected(self):
        """An unclosed `{{` stashes no expression, so the leftovers were read as
        literal text and the value passed. Jinja refuses it at deploy."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", '{{ instance_url.split("://")[1] '))
        self.assertTrue(errs, errs)

    def test_nextcloud_entries_split_on_any_whitespace(self):
        """Nextcloud's entrypoint reads trusted_domains through shell word
        splitting, so tabs and newlines separate entries just as spaces do.
        Splitting on a single space glued them together and reported a valid
        allowlist as embedded."""
        for name, sep in (("tab", chr(9)), ("newline", chr(10)), ("double space", "  ")):
            with self.subTest(sep=name):
                val = f"{self.HOSTPORT}{sep}{{{{ instance_host }}}}{sep}localhost"
                self.assertEqual(self._run(compose=self._compose(
                    "NEXTCLOUD_TRUSTED_DOMAINS", val), app="nextcloud"), [])

    def test_django_leading_dot_is_a_host_pattern(self):
        """`.example.com` is Django's documented subdomain pattern, matching the
        base host and any subdomain. It carries no scheme or port, so refusing it
        as `embedded` blocked a valid tenant-subdomain allowlist."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", ".{{ instance_host }},localhost"))
        self.assertEqual(errs, [])

    def test_leading_dot_is_not_accepted_for_nextcloud(self):
        """The prefix is per app: Nextcloud matches trusted_domains explicitly and
        has no leading-dot pattern, so the same text there is a broken entry."""
        errs = self._run(compose=self._compose(
            "NEXTCLOUD_TRUSTED_DOMAINS", "{{ instance_host }} .{{ instance_host }}"),
            app="nextcloud")
        self.assertTrue(errs, errs)

    def test_rules_are_scoped_to_the_owning_app(self):
        """Keyed by (app, setting). Two Django apps can both read
        DJANGO_ALLOWED_HOSTS and cast it differently, so the name alone does not
        determine the parser, and an unlisted app is not judged by another's."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", f"{self.HOSTPORT},localhost"), app="someapp")
        self.assertEqual(errs, [], "an app outside the map is not checked")

    def test_nextcloud_splits_only_on_default_ifs(self):
        """Shell word splitting uses space, tab and newline. `\\s+` also matches
        CR, vertical tab and unicode spaces, none of which the shell splits on, so
        a value the app receives as ONE broken token validated as two good ones."""
        for name, sep in (("carriage return", chr(13)), ("vertical tab", chr(11)),
                          ("non-breaking space", chr(160))):
            with self.subTest(sep=name):
                val = f"{self.HOSTPORT}{sep}{{{{ instance_host }}}}"
                self.assertTrue(self._run(compose=self._compose(
                    "NEXTCLOUD_TRUSTED_DOMAINS", val), app="nextcloud"),
                    f"{name} is not an IFS separator")

    def test_django_wildcard_satisfies_the_requirement(self):
        """`*` disables Django's host check, so it matches the bare host too.
        Rejecting it was also inconsistent: an allowlist of only `*` was already
        accepted, because it contains no expression and never reached the check."""
        errs = self._run(compose=self._compose(
            "DJANGO_ALLOWED_HOSTS", f"{self.HOSTPORT},*"))
        self.assertEqual(errs, [])

    def test_wildcard_is_per_app_not_universal(self):
        """The map claims a wildcard only where this catalog can demonstrate one.
        Nextcloud gets none, so `*` there is an ordinary literal and the ported
        form still needs its bare companion."""
        errs = self._run(compose=self._compose(
            "NEXTCLOUD_TRUSTED_DOMAINS", f"{self.HOSTPORT} *"), app="nextcloud")
        self.assertTrue(errs, errs)

    def test_app_lookup_survives_path_shape(self):
        """`--dir ./docs/1.0` and Windows backslash paths both produced an app name
        the map could not match, so the rule skipped silently and the run still
        reported success. Tests the resolver directly, since a fixture cannot
        produce a Windows path on this host."""
        from validate_catalog import _app_of
        for rel, expected in (("docs/1.0", "docs"), ("./docs/1.0", "docs"),
                              ("docs/1.0/", "docs"), ("nextcloud\\1.0", "nextcloud"),
                              # absolute, which the CLI accepts: taking the FIRST
                              # component returned "Users" and skipped the rule
                              ("/home/me/catalog/docs/1.0", "docs"),
                              ("../catalog/nextcloud/1.0", "nextcloud")):
            with self.subTest(rel=rel):
                self.assertEqual(_app_of(rel), expected)

    def test_a_spec_with_only_a_separator_does_not_crash(self):
        """The README and add-greffon.md invite people to add entries to the map.
        A missing optional key raised KeyError, which aborts --all for the entire
        catalog: a documented extension path must not contain a crash."""
        from validate_catalog import _HOST_ALLOWLISTS, _host_allowlist_problem
        key = ("regtest", "DJANGO_ALLOWED_HOSTS")
        _HOST_ALLOWLISTS[key] = {"split": ","}
        try:
            self.assertEqual(
                _host_allowlist_problem("regtest", "DJANGO_ALLOWED_HOSTS",
                                        f"{self.HOSTPORT},localhost"),
                "no-bare-host")
        finally:
            del _HOST_ALLOWLISTS[key]

    def test_non_ifs_whitespace_is_not_trimmed_off_an_entry(self):
        """Python's str.strip() discards CR, vertical tab and NBSP; the shell that
        parses trusted_domains does not. So `{{ instance_host }}\\r` validated as a
        clean bare host while Nextcloud received `host\\r` and matched nothing."""
        for name, ch in (("carriage return", chr(13)), ("non-breaking space", chr(160)),
                         ("vertical tab", chr(11))):
            with self.subTest(ch=name):
                val = f"{self.HOSTPORT} {{{{ instance_host }}}}{ch}"
                self.assertTrue(self._run(compose=self._compose(
                    "NEXTCLOUD_TRUSTED_DOMAINS", val), app="nextcloud"),
                    f"{name} is not trimmed by the app")

    def test_ifs_whitespace_around_an_entry_is_fine(self):
        """The other direction, so the trim set is not simply empty: space, tab and
        newline ARE discarded by the shell, so they must not fail the entry."""
        for name, ch in (("space", " "), ("tab", chr(9)), ("newline", chr(10))):
            with self.subTest(ch=name):
                val = f"{self.HOSTPORT} {{{{ instance_host }}}}{ch}"
                self.assertEqual(self._run(compose=self._compose(
                    "NEXTCLOUD_TRUSTED_DOMAINS", val), app="nextcloud"), [])

    # --- malformed input must not abort the run --------------------------
    # `--all` validates the whole catalog in one process, so a crash here reports
    # NOTHING for any entry, which is strictly worse than the error it replaces.

    def test_scalar_environment_does_not_crash(self):
        for shape in ("environment: 1", "environment: true", "environment: hello"):
            with self.subTest(shape=shape):
                compose = textwrap.dedent(f"""\
                    services:
                      app:
                        image: nginx
                        ports:
                          - "8080:8080"
                        {shape}
                    """)
                self._run(compose=compose)  # must return, not raise

    def test_scalar_configurations_does_not_crash(self):
        meta = _base_metadata()
        meta["configurations"] = 1
        self._run(metadata=meta)  # must return, not raise

    def test_scalar_default_value_does_not_crash(self):
        meta = _base_metadata()
        meta["configurations"] = [{
            "title": "X",
            "schema": {"properties": {"value": {"type": "string"}}},
            "default_value": "not-a-mapping",
            "destinations": [{"type": "env", "container": "app", "key": "DJANGO_ALLOWED_HOSTS"}],
        }]
        self._run(metadata=meta)  # must return, not raise

    def test_scalar_destinations_does_not_crash(self):
        meta = _base_metadata()
        meta["configurations"] = [{
            "title": "X",
            "schema": {"properties": {"value": {"type": "string"}}},
            "default_value": {"value": "x"},
            "destinations": 7,
        }]
        self._run(metadata=meta)  # must return, not raise

    def test_literal_allowlist_is_not_flagged(self):
        """No instance_url idiom at all: nothing to say."""
        errs = self._run(compose=self._compose("DJANGO_ALLOWED_HOSTS", "example.com,localhost"))
        self.assertEqual(errs, [])


class BackupPairingTest(unittest.TestCase):
    def _run(self, *, metadata=None, compose=None):
        """Backup-related errors only. The shared fixture writes no
        smoke_test.spec.ts, so the linter always reports that separately and it
        is not what these tests are about (matches the SMTP tests' convention)."""
        with tempfile.TemporaryDirectory() as tmp:
            rel = _write_greffon(tmp, metadata=metadata or _backup_meta(),
                                 compose_yaml=compose or _BACKUP_COMPOSE)
            errs = validate_greffon_dir(tmp, rel)
            terms = ("backup", "hook", "healthcheck", "regenerable", "volume")
            return [e for e in errs if any(x in e.lower() for x in terms)]

    def test_valid_declaration_passes(self):
        self.assertEqual(self._run(), [], "a correct hot-backup declaration must lint clean")

    def test_hooks_without_classification(self):
        """The keycloak defect: hooks present, no backup.volumes -> silent COLD."""
        meta = _backup_meta(); del meta["backup"]
        errs = self._run(metadata=meta)
        self.assertTrue(any("no 'backup.volumes'" in e for e in errs), errs)

    def test_database_class_without_dump_hook(self):
        compose = _BACKUP_COMPOSE.replace(
            '      com.greffon.backup.dump: "pg_dump -U a -d a -Fc"\n', "")
        errs = self._run(compose=compose)
        self.assertTrue(any("backup.dump" in e or "dump" in e for e in errs), errs)

    def test_split_dump_and_restore_services(self):
        """Manifest is keyed by the dump service, so a split pair fails restore."""
        compose = _BACKUP_COMPOSE.replace(
            '      com.greffon.backup.restore: "pg_restore -U a -d a --clean"\n', ""
        ).replace("  db:\n", "  other:\n    image: busybox\n    labels:\n"
                              "      com.greffon.backup.restore: \"pg_restore -U a -d a --clean\"\n"
                              "  db:\n", 1)
        errs = self._run(compose=compose)
        self.assertTrue(any("restore hook is on" in e for e in errs), errs)

    def test_all_regenerable_is_rejected(self):
        """Non-empty map selects HOT, but nothing to snapshot -> no_data_volumes."""
        compose = _BACKUP_COMPOSE.replace(
            '      com.greffon.backup.dump: "pg_dump -U a -d a -Fc"\n', ""
        ).replace('      com.greffon.backup.restore: "pg_restore -U a -d a --clean"\n', "")
        errs = self._run(metadata=_backup_meta(backup={"volumes": {"db_data": "regenerable"}}),
                         compose=compose)
        self.assertTrue(any("regenerable" in e for e in errs), errs)

    def test_unclassified_compose_volume(self):
        compose = _BACKUP_COMPOSE.replace(
            "volumes:\n  db_data:\n", "volumes:\n  db_data:\n  extra:\n")
        errs = self._run(compose=compose)
        # Assert the BACKUP message, not just the volume name: the pre-existing
        # 'declared but never mounted' check also names 'extra', so a looser
        # assertion passes with this rule deleted and pins nothing.
        self.assertTrue(
            any("backup.volumes does not classify it" in e for e in errs), errs)

    def test_classified_volume_absent_from_compose(self):
        errs = self._run(metadata=_backup_meta(backup={"volumes": {"nope": "database"}}))
        self.assertTrue(any("'nope'" in e for e in errs), errs)

    def test_invalid_class(self):
        errs = self._run(metadata=_backup_meta(backup={"volumes": {"db_data": "bogus"}}))
        self.assertTrue(any("must be one of" in e for e in errs), errs)

    def test_non_string_class_does_not_crash(self):
        """A list class is unhashable; membership must not raise and kill --all."""
        errs = self._run(metadata=_backup_meta(backup={"volumes": {"db_data": []}}))
        self.assertTrue(any("must be a string" in e for e in errs), errs)

    def test_missing_healthcheck(self):
        compose = _BACKUP_COMPOSE.replace(
            '    healthcheck:\n      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U a -d a"]\n', "")
        errs = self._run(compose=compose)
        self.assertTrue(any("healthcheck" in e for e in errs), errs)

    def test_disabled_healthcheck(self):
        """`disable: true` is truthy but Docker then reports no health at all."""
        compose = _BACKUP_COMPOSE.replace(
            '      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U a -d a"]',
            "      disable: true")
        errs = self._run(compose=compose)
        self.assertTrue(any("DISABLED" in e for e in errs), errs)

    def test_whitespace_only_hook_command(self):
        """shlex.split yields no argv, so the greffer has nothing to execute."""
        compose = _BACKUP_COMPOSE.replace('"pg_dump -U a -d a -Fc"', '"   "')
        errs = self._run(compose=compose)
        self.assertTrue(any("empty or only" in e for e in errs), errs)

    def test_empty_hook_command(self):
        """An empty value is skipped by the greffer as if the hook were absent."""
        compose = _BACKUP_COMPOSE.replace('"pg_dump -U a -d a -Fc"', '""')
        errs = self._run(compose=compose)
        self.assertTrue(any("empty or only" in e for e in errs), errs)

    def test_unmatched_quote_hook_command(self):
        compose = _BACKUP_COMPOSE.replace('"pg_dump -U a -d a -Fc"', "\"pg_dump -c 'oops\"")
        errs = self._run(compose=compose)
        self.assertTrue(any("does not parse" in e for e in errs), errs)

    # --- the command must be runnable without a shell -----------------------
    # The greffer execs the argv directly, so shell syntax is inert. These pin
    # the distinction between "uses metacharacters" and "invokes no shell",
    # which matters because the MySQL entries legitimately use `sh -c`.

    def test_shell_pipe_without_a_shell_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"pg_dump -U a -d a | gzip"')
        errs = self._run(compose=compose)
        self.assertTrue(any("does not invoke a shell" in e for e in errs), errs)

    def test_variable_expansion_without_a_shell_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"pg_dump -U $$PGUSER -d a -Fc"')
        errs = self._run(compose=compose)
        self.assertTrue(any("does not invoke a shell" in e for e in errs), errs)

    def test_sh_dash_c_hook_is_accepted(self):
        """Guards the rule against over-reach: this is the real shape the MySQL
        and MariaDB entries use, and rejecting it would block them."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"',
            "\"sh -c 'export MYSQL_PWD=$$MYSQL_ROOT_PASSWORD; exec mysqldump -u root a'\"")
        self.assertEqual(self._run(compose=compose), [])

    # --- the hook must resolve to exactly one running container -------------
    # The greffer finds hooks by counting RUNNING CONTAINERS, so a check that
    # counts services can pass while the runtime count is 0 or 2.

    def test_hook_service_behind_a_profile_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace(
            "    image: postgres:16-alpine\n",
            "    image: postgres:16-alpine\n    profiles: [\"donotstart\"]\n")
        errs = self._run(compose=compose)
        self.assertTrue(any("never starts" in e for e in errs), errs)

    def test_hook_service_scaled_to_zero_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace(
            "    image: postgres:16-alpine\n",
            "    image: postgres:16-alpine\n    scale: 0\n")
        errs = self._run(compose=compose)
        self.assertTrue(any("no_dump_hook" in e for e in errs), errs)

    def test_hook_service_with_replicas_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace(
            "    image: postgres:16-alpine\n",
            "    image: postgres:16-alpine\n    deploy:\n      replicas: 2\n")
        errs = self._run(compose=compose)
        self.assertTrue(
            any("multiple_database_unsupported" in e for e in errs), errs)

    def test_invalid_class_does_not_also_claim_all_regenerable(self):
        """One true error, not a real one plus a false one."""
        meta = _backup_meta()
        meta["backup"] = {"volumes": {"db_data": "bogus"}}
        errs = self._run(metadata=meta)
        self.assertTrue(any("bogus" in e for e in errs), errs)
        self.assertFalse([e for e in errs if "every volume as 'regenerable'" in e], errs)

    def test_shell_operator_glued_to_a_word_is_rejected(self):
        """`pg_dump|gzip` lexes as ONE token, so whole-token matching missed it."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"pg_dump -U a -d a|gzip"')
        errs = self._run(compose=compose)
        self.assertTrue(any("does not invoke a shell" in e for e in errs), errs)

    def test_glued_redirect_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"pg_dump -U a -d a 2>/tmp/e"')
        errs = self._run(compose=compose)
        self.assertTrue(any("does not invoke a shell" in e for e in errs), errs)

    def test_quoted_operator_is_not_a_false_positive(self):
        """A quoted & belongs to the argument, and docker exec passes it through
        untouched. Substring matching would have flagged this."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', """'pg_dump -d "postgres://a?x=1&y=2"'""")
        self.assertEqual(self._run(compose=compose), [])

    def test_shell_basename_without_dash_c_is_not_a_shell(self):
        """`busybox timeout 10 pg_dump -c db | gzip` once faked a shell invocation
        by borrowing pg_dump's -c. Still rejected, now by the shape rule: naming a
        shell anywhere obliges the hook to be exactly `sh -c <script>`."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"busybox timeout 10 pg_dump -c db | gzip"')
        errs = self._run(compose=compose)
        self.assertTrue(any("not exactly" in e for e in errs), errs)

    def test_replicas_as_a_numeric_string_is_rejected(self):
        """compose coerces "0", so it really does mean zero containers."""
        compose = _BACKUP_COMPOSE.replace(
            "    image: postgres:16-alpine\n",
            '    image: postgres:16-alpine\n    scale: "0"\n')
        errs = self._run(compose=compose)
        self.assertTrue(any("no_dump_hook" in e for e in errs), errs)

    def test_healthcheck_with_no_test_is_rejected(self):
        """A truthy mapping is not a healthcheck: docker reports no health state
        without a command, so the restore waits for a healthy that never comes."""
        compose = _BACKUP_COMPOSE.replace(
            '    healthcheck:\n      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U a -d a"]\n',
            "    healthcheck:\n      interval: 5s\n")
        errs = self._run(compose=compose)
        self.assertTrue(any("no usable 'test'" in e for e in errs), errs)

    def test_healthcheck_with_empty_test_list_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace(
            '      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U a -d a"]\n',
            "      test: []\n")
        errs = self._run(compose=compose)
        self.assertTrue(any("no usable 'test'" in e for e in errs), errs)

    def test_data_volume_on_the_hook_service_is_rejected(self):
        """Backs up fine forever and can never be restored: the greffer's restore
        guard reads DB volumes from docker state, so a data-classed volume on the
        dump service is in both sets and aborts with db_volume_misclassified."""
        compose = _BACKUP_COMPOSE.replace(
            "      - db_data:/var/lib/postgresql/data\n",
            "      - db_data:/var/lib/postgresql/data\n      - db_extra:/extra\n"
        ).replace("volumes:\n  db_data:\n", "volumes:\n  db_data:\n  db_extra:\n")
        meta = _backup_meta()
        meta["backup"] = {"volumes": {"db_data": "database", "db_extra": "data"}}
        errs = self._run(metadata=meta, compose=compose)
        self.assertTrue(
            any("db_volume_misclassified" in e for e in errs), errs)

    def test_single_dollar_inside_a_shell_hook_is_rejected(self):
        """compose eats it before the container exists, so the arg goes missing."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', """'sh -c "pg_dump -U $PGUSER"'""")
        errs = self._run(compose=compose)
        self.assertTrue(any("single-$" in e for e in errs), errs)

    def test_words_after_the_shell_script_are_rejected(self):
        """`sh -c pg_dump | gzip` puts the pipe outside the script, where the shell
        takes it as $1 rather than running it. Caught by the shape rule now: only
        `sh -c <script>` is accepted, so there is no 'outside' left to reason about."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"sh -c pg_dump | gzip"')
        errs = self._run(compose=compose)
        self.assertTrue(any("not exactly" in e for e in errs), errs)

    def test_compound_redirection_is_rejected(self):
        """punctuation_chars lexes 2>&1 as ['2', '>&', '1'], so a bare '>' misses."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"pg_dump -U a -d a 2>&1"')
        errs = self._run(compose=compose)
        self.assertTrue(any("does not invoke a shell" in e for e in errs), errs)

    def test_shell_options_before_dash_c_are_rejected(self):
        """A DELIBERATE reversal of the previous round, which added option-word
        walking so `sh -eu -c` would pass. That parser was then wrong twice more
        (`--norc` contains a c; `--` was walked past), so the option surface is
        gone: `set -eu` goes inside the script, where it is clearer anyway."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', """'sh -eu -c "pg_dump -U a -d a -Fc"'""")
        errs = self._run(compose=compose)
        self.assertTrue(any("not exactly" in e for e in errs), errs)

    def test_double_dash_before_dash_c_is_rejected(self):
        """`sh -- -c '...'` makes sh open a FILE named -c and exit; the old walker
        ran straight past the -- terminator and called it a shell invocation."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', """'sh -- -c "pg_dump -d a"'""")
        errs = self._run(compose=compose)
        self.assertTrue(any("not exactly" in e for e in errs), errs)

    def test_long_option_containing_c_is_rejected(self):
        """`bash --norc '...'` matched the old `"c" in word` test and was treated
        as a -c invocation, which silently switched off every syntax check."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', """'bash --norc "pg_dump -d a"'""")
        errs = self._run(compose=compose)
        self.assertTrue(any("not exactly" in e for e in errs), errs)

    def test_here_string_is_rejected(self):
        """`<<<` lexes as one token and was absent from the operator allowlist.
        Detection is now punctuation-only, so it needs no enumeration."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"pg_restore -d a <<< dump"')
        errs = self._run(compose=compose)
        self.assertTrue(any("does not invoke a shell" in e for e in errs), errs)

    def test_quoted_disable_true_is_rejected(self):
        """compose reads "true" as true; an `is True` identity test did not."""
        compose = _BACKUP_COMPOSE.replace(
            '      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U a -d a"]\n',
            '      disable: "true"\n')
        errs = self._run(compose=compose)
        self.assertTrue(any("DISABLED" in e for e in errs), errs)

    def test_interpolated_replica_count_is_rejected(self):
        """It resolves against the greffer's scrubbed env, so the default wins."""
        compose = _BACKUP_COMPOSE.replace(
            "    image: postgres:16-alpine\n",
            '    image: postgres:16-alpine\n    scale: "${DB_REPLICAS:-2}"\n')
        errs = self._run(compose=compose)
        self.assertTrue(any("interpolated" in e for e in errs), errs)

    def test_shell_with_no_script_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace('"pg_dump -U a -d a -Fc"', '"sh -c"')
        errs = self._run(compose=compose)
        self.assertTrue(any("not exactly" in e for e in errs), errs)

    def test_shell_with_an_empty_script_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace('"pg_dump -U a -d a -Fc"', """'sh -c ""'""")
        errs = self._run(compose=compose)
        self.assertTrue(any("empty script" in e for e in errs), errs)

    def test_one_shot_hook_service_is_rejected(self):
        """It has exited by backup time, so there is no container to exec into."""
        compose = _BACKUP_COMPOSE.replace(
            "    image: postgres:16-alpine\n",
            '    image: postgres:16-alpine\n    command: ["sh", "-c", "setup; exit 0"]\n')
        errs = self._run(compose=compose)
        self.assertTrue(any("one-shot" in e for e in errs), errs)

    def test_malformed_replica_string_does_not_crash(self):
        """int("--1") raised and aborted --all for the entire catalog."""
        compose = _BACKUP_COMPOSE.replace(
            "    image: postgres:16-alpine\n",
            '    image: postgres:16-alpine\n    scale: "--1"\n')
        self._run(compose=compose)  # must return, not raise

    def test_database_volume_not_on_the_hook_service_is_rejected(self):
        """Loses data silently: the hot path snapshots only 'data' volumes, so a
        'database' volume on some other service is captured by nothing at all."""
        compose = _BACKUP_COMPOSE.replace(
            "      - db_data:/var/lib/postgresql/data\n",
            "      - db_cache:/var/lib/postgresql/data\n"
        ).replace("  app:\n    image: nginx\n",
                  "  app:\n    image: nginx\n    volumes:\n      - db_data:/app\n"
        ).replace("volumes:\n  db_data:\n", "volumes:\n  db_data:\n  db_cache:\n")
        meta = _backup_meta()
        meta["backup"] = {"volumes": {"db_data": "database", "db_cache": "regenerable"}}
        errs = self._run(metadata=meta, compose=compose)
        self.assertTrue(any("mounts none of the volumes classed" in e for e in errs), errs)

    def test_hash_comment_hides_no_syntax(self):
        """shlex.shlex strips # comments by default; the greffer's shlex.split does
        not, so those words really do reach docker exec."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"pg_dump -U a -d a # dump | gzip"')
        errs = self._run(compose=compose)
        self.assertTrue(any("does not invoke a shell" in e for e in errs), errs)

    def test_leading_env_assignment_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"PGPASSWORD=x pg_dump -U a -d a -Fc"')
        errs = self._run(compose=compose)
        self.assertTrue(any("environment assignment" in e for e in errs), errs)

    def test_float_replica_count_is_rejected(self):
        """compose normalises 2.0 to 2; PyYAML hands us a float."""
        compose = _BACKUP_COMPOSE.replace(
            "    image: postgres:16-alpine\n",
            "    image: postgres:16-alpine\n    scale: 2.0\n")
        errs = self._run(compose=compose)
        self.assertTrue(any("multiple_database_unsupported" in e for e in errs), errs)

    def test_jinja_in_a_hook_label_is_rejected(self):
        """The greffer renders compose before deploying, so CI reads the template."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', """'{{ "" }}'""")
        errs = self._run(compose=compose)
        self.assertTrue(any("Jinja expression" in e for e in errs), errs)

    def test_jinja_in_a_replica_count_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace(
            "    image: postgres:16-alpine\n",
            '    image: postgres:16-alpine\n    scale: "{{ 2 }}"\n')
        errs = self._run(compose=compose)
        self.assertTrue(any("Jinja" in e for e in errs), errs)

    def test_exotic_numeric_replica_spellings_are_rejected(self):
        """PyYAML leaves 2e0 and 0o2 as strings and compose reads both as 2. The
        rule no longer tries to parse them: anything but a literal 1 is refused."""
        for spelling in ("2e0", "0o2", "2.0", '"2"'):
            with self.subTest(spelling=spelling):
                compose = _BACKUP_COMPOSE.replace(
                    "    image: postgres:16-alpine\n",
                    f"    image: postgres:16-alpine\n    scale: {spelling}\n")
                errs = self._run(compose=compose)
                self.assertTrue(
                    any("exactly one container" in e for e in errs), errs)

    def test_literal_one_replica_is_accepted(self):
        """The rule refuses everything it cannot understand, so this pins that it
        still understands the one value an entry is allowed to write."""
        compose = _BACKUP_COMPOSE.replace(
            "    image: postgres:16-alpine\n",
            "    image: postgres:16-alpine\n    scale: 1\n")
        self.assertEqual(self._run(compose=compose), [])

    def test_jinja_comment_in_a_hook_is_rejected(self):
        """`{# ... #}` renders to nothing, so a hook that looks present here is
        absent at deploy. The regex matched {{ and {% but not {#."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_restore -U a -d a --clean"', '"{# disabled #}"')
        errs = self._run(compose=compose)
        self.assertTrue(any("Jinja" in e for e in errs), errs)

    def test_jinja_in_the_healthcheck_test_is_rejected(self):
        """test: ["{{ 'NONE' }}"] renders to a disabled healthcheck."""
        compose = _BACKUP_COMPOSE.replace(
            '      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U a -d a"]\n',
            '      test: ["{{ \'NONE\' }}"]\n')
        errs = self._run(compose=compose)
        self.assertTrue(any("healthcheck.test" in e for e in errs), errs)

    def test_busybox_with_a_non_applet_shell_is_rejected(self):
        """busybox ships sh and ash; `busybox bash -c` fails at exec."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', """'busybox bash -c "pg_dump -d a"'""")
        errs = self._run(compose=compose)
        self.assertTrue(any("not exactly" in e for e in errs), errs)

    def test_shell_behind_a_wrapper_is_rejected(self):
        """`env sh -c pg_dump -U app` hid the shell from a first-word check, then
        passed the plain-argv path having no operators and no $, while sh really
        takes -U as $0."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"env sh -c pg_dump -U app"')
        errs = self._run(compose=compose)
        self.assertTrue(any("not exactly" in e for e in errs), errs)

    def test_malformed_volumes_block_does_not_crash(self):
        """`volumes: 1` raised TypeError, aborting --all for the whole catalog."""
        compose = _BACKUP_COMPOSE.replace(
            "    volumes:\n      - db_data:/var/lib/postgresql/data\n",
            "    volumes: 1\n")
        self._run(compose=compose)  # must return, not raise

    def test_backtick_substitution_is_rejected(self):
        """Backticks are neither punctuation-only nor a $ expansion, so both scans
        passed them; with no shell they reach the program as literal characters."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', "'pg_dump -d `hostname`'")
        errs = self._run(compose=compose)
        self.assertTrue(any("does not invoke a shell" in e for e in errs), errs)

    def test_jinja_in_a_classified_volume_name_is_rejected(self):
        """The compose key is rendered and this one is not, so two files that look
        consistent here name different volumes at deploy."""
        meta = _backup_meta()
        meta["backup"] = {"volumes": {"db_data": "database",
                                      "data_{{ instance_id }}": "data"}}
        errs = self._run(metadata=meta)
        # The Jinja message specifically. Asserting on "volume_unclassified" alone
        # passed with this rule deleted, because the pre-existing "classified but
        # absent from compose" check says it too.
        self.assertTrue(
            any("contains a Jinja expression" in e for e in errs), errs)

    def test_reserved_nginx_suffix_is_rejected(self):
        """The greffer skips its sidecar volume by SUFFIX, so a catalog volume
        ending the same way is dropped with it, silently, on a green backup."""
        compose = _BACKUP_COMPOSE.replace(
            "      - db_data:/var/lib/postgresql/data\n",
            "      - db_data:/var/lib/postgresql/data\n"
        ).replace("  app:\n    image: nginx\n",
                  "  app:\n    image: nginx\n    volumes:\n"
                  "      - app_nginx_volume:/data\n"
        ).replace("volumes:\n  db_data:\n",
                  "volumes:\n  db_data:\n  app_nginx_volume:\n")
        meta = _backup_meta()
        meta["backup"] = {"volumes": {"db_data": "database",
                                      "app_nginx_volume": "data"}}
        errs = self._run(metadata=meta, compose=compose)
        self.assertTrue(any("reserved suffix" in e for e in errs), errs)

    def test_bare_nginx_volume_name_is_rejected(self):
        """`nginx_volume` does not end with `_nginx_volume`, but namespacing makes
        it <id>_nginx_volume, which is both excluded and a sidecar collision."""
        compose = _BACKUP_COMPOSE.replace(
            "  app:\n    image: nginx\n",
            "  app:\n    image: nginx\n    volumes:\n      - nginx_volume:/data\n"
        ).replace("volumes:\n  db_data:\n", "volumes:\n  db_data:\n  nginx_volume:\n")
        meta = _backup_meta()
        meta["backup"] = {"volumes": {"db_data": "database", "nginx_volume": "data"}}
        errs = self._run(metadata=meta, compose=compose)
        self.assertTrue(any("reserved suffix" in e for e in errs), errs)

    def test_reserved_suffix_applies_without_a_backup_block(self):
        """Cold backups use the same _data_volumes, so the rule cannot be scoped
        to entries that classify their volumes."""
        compose = _BACKUP_COMPOSE.replace(
            "  app:\n    image: nginx\n",
            "  app:\n    image: nginx\n    volumes:\n      - app_nginx_volume:/data\n"
        ).replace("volumes:\n  db_data:\n",
                  "volumes:\n  db_data:\n  app_nginx_volume:\n")
        meta = _backup_meta()
        meta.pop("backup", None)  # no classification at all
        errs = self._run(metadata=meta, compose=compose)
        self.assertTrue(any("reserved suffix" in e for e in errs), errs)

    def test_jinja_in_a_compose_volume_name_is_rejected(self):
        """`app_{{ "nginx_volume" }}` does not end with the reserved suffix in
        template form and does after rendering. Rejecting Jinja on every volume
        name is what makes the other volume rules sound, rather than each of them
        having to reason about template-vs-rendered separately."""
        compose = _BACKUP_COMPOSE.replace(
            "  app:\n    image: nginx\n",
            '  app:\n    image: nginx\n    volumes:\n'
            '      - \'app_{{ "nginx_volume" }}:/data\'\n'
        ).replace("volumes:\n  db_data:\n",
                  'volumes:\n  db_data:\n  \'app_{{ "nginx_volume" }}\':\n')
        meta = _backup_meta()
        meta.pop("backup", None)  # cold entry: the rule must not need a backup block
        errs = self._run(metadata=meta, compose=compose)
        self.assertTrue(
            any("cannot know the runtime name" in e for e in errs), errs)

    def test_empty_program_name_is_rejected(self):
        """shlex.split("''") is [''], non-empty, so the no-argv branch missed it."""
        # Double-quoted YAML so the VALUE is the two characters '', which is what
        # shlex.split turns into ['']. Single-quoting it yields a literal quote.
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"\'\'"')
        errs = self._run(compose=compose)
        self.assertTrue(any("empty program name" in e for e in errs), errs)

    def test_none_prefixed_healthcheck_is_disabled(self):
        """docker disables on a LEADING NONE, whatever follows it."""
        compose = _BACKUP_COMPOSE.replace(
            '      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U a -d a"]\n',
            '      test: ["NONE", "anything"]\n')
        errs = self._run(compose=compose)
        self.assertTrue(any("DISABLED" in e for e in errs), errs)

    def test_bare_shell_negation_is_rejected(self):
        """`! pg_dump ...` asks docker exec to run a program named `!`."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', '"! pg_dump -U a -d a -Fc"')
        errs = self._run(compose=compose)
        self.assertTrue(any("does not invoke a shell" in e for e in errs), errs)

    def test_custom_volume_name_is_rejected(self):
        """`name:` opts out of project namespacing, so the docker volume has no
        <instance_id>_ prefix and the greffer never collects it."""
        compose = _BACKUP_COMPOSE.replace(
            "volumes:\n  db_data:\n", "volumes:\n  db_data:\n    name: shared_data\n")
        errs = self._run(compose=compose)
        self.assertTrue(any("project namespacing" in e for e in errs), errs)

    def test_external_volume_is_rejected(self):
        compose = _BACKUP_COMPOSE.replace(
            "volumes:\n  db_data:\n", "volumes:\n  db_data:\n    external: true\n")
        errs = self._run(compose=compose)
        self.assertTrue(any("project namespacing" in e for e in errs), errs)

    def test_quoted_operator_as_a_literal_argument_is_accepted(self):
        """`--separator '|'` is a working hook: docker exec passes the literal
        through. Matching lexed tokens rejected it, since shlex strips the quotes
        and a real pipe lexes identically."""
        compose = _BACKUP_COMPOSE.replace(
            '"pg_dump -U a -d a -Fc"', """'pgtool --separator "|" -d a'""")
        self.assertEqual(self._run(compose=compose), [])

    def test_no_backup_block_is_fine(self):
        """An entry that never opts in stays COLD and must not be nagged."""
        compose = _BACKUP_COMPOSE.replace(
            '    labels:\n      com.greffon.backup.dump: "pg_dump -U a -d a -Fc"\n'
            '      com.greffon.backup.restore: "pg_restore -U a -d a --clean"\n', "")
        meta = _backup_meta(); del meta["backup"]
        self.assertEqual(self._run(metadata=meta, compose=compose), [],
                         "an entry that never opts in must not be nagged")


if __name__ == "__main__":
    unittest.main(verbosity=2)
