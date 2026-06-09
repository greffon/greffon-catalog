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
    _value_references_smtp,
    validate_greffon_dir,
)


def _write_greffon(tmpdir, *, metadata, compose_yaml=None):
    """Write a complete greffon dir under tmpdir/test/1.0/. Returns the rel dir."""
    greffon_dir = os.path.join(tmpdir, "test", "1.0")
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
    return "test/1.0"


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
        self.assertTrue(_value_references_smtp(
            '{{ "true" if smtp.tls_mode == "tls" else "false" }}'
        ))

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
        self.assertTrue(any("integration namespace" in e for e in errs), errs)

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
        self.assertTrue(any("bypasses" in e for e in errs), errs)

    def test_default_filter_bypass_rejected(self):
        errs = self._run(
            {"type": "file", "volume": "data", "name": "f", "x-greffon-render": True},
            {"file": _data_uri("secret = {{ config.X | default('') }}")},
        )
        self.assertTrue(any("bypasses" in e for e in errs), errs)

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
        self.assertTrue(any("integration namespace" in e for e in errs), errs)

    def test_render_json_config_ref_without_env_key_rejected(self):
        errs = self._run(
            {"type": "json", "volume": "data", "name": "f.json", "x-greffon-render": True},
            {"secret": "{{ config.MISSING }}"},
            schema={"properties": {}},
        )
        self.assertTrue(any("config.MISSING" in e for e in errs), errs)


class IntegrationNamespaceParityTest(unittest.TestCase):
    """Tripwire: pin the validator's integration-namespace list. It is a copy of
    the greffer's KNOWN_INTEGRATION_TYPES (separate repo — this test can't import
    it). Pinning forces a deliberate, reviewed edit; when the greffer adds an
    integration type, this assertion (and the linked comment in validate_catalog)
    must be updated in the same change so the integration-reference check doesn't
    silently fail open for the new namespace."""

    def test_known_namespaces_pinned(self):
        self.assertEqual(KNOWN_INTEGRATION_NAMESPACES, ("smtp",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
