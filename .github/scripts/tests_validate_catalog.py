#!/usr/bin/env python3
"""Regression tests for the catalog linter.

Each test reproduces a real bug we shipped before the linter existed and
asserts the linter would now catch it. Run via:

    python3 .github/scripts/tests_validate_catalog.py

The tests build minimal valid greffon directories in a tmpdir, mutate one
property to reintroduce the bug, and check the linter raises the expected
error string.
"""
import json
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from validate_catalog import validate_greffon_dir


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
