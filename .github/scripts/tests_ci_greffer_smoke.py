#!/usr/bin/env python3
"""Regression tests for check_cookie_security() in ci_greffer_smoke.py.

Why these exist. The catalog's integration job only smoke-tests CHANGED entries,
and its canary (excalidraw) sets no cookie on `/` and issues no redirect. So the
whole redirect-following path in this check can break and every PR stays green:
a change to the check is exactly the change its own CI cannot see. Each test below
drives the real function against a real TLS server rather than a mock, because the
behaviour under test is the interaction with requests' redirect and cookie
handling, which a mock would simply reassert.

Each test is mutation-checked. Reverting the shared Session to a per-hop
requests.get() fails test_insecure_cookie_behind_a_state_cookie and nothing else.
"""
import contextlib
import http.server
import importlib.util
import io
import os
import ssl
import subprocess
import tempfile
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "ci_greffer_smoke", os.path.join(_HERE, "ci_greffer_smoke.py"))
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

# Set per-test; the handler branches on it so one server serves every scenario.
SCENARIO = {"mode": "chain"}
PORT = None


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        mode, path = SCENARIO["mode"], self.path
        if mode == "chain":
            # Hop 1 sets a state cookie and redirects. Hop 2 serves the insecure
            # cookie ONLY when that state cookie comes back, which is what makes
            # this a test of the shared jar and not just of following redirects:
            # without it the probe lands on the unauthenticated branch, sees a
            # clean response, and reports success.
            if path == "/":
                self.send_response(302)
                self.send_header("Set-Cookie", "state=abc; Path=/; HttpOnly; Secure")
                self.send_header("Location", f"https://127.0.0.1:{PORT}/login")
                self.end_headers()
                return
            if path == "/login":
                if "state=abc" not in (self.headers.get("Cookie") or ""):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"unauthenticated branch")
                    return
                self.send_response(200)
                self.send_header("Set-Cookie", "session=zzz; Path=/; HttpOnly")
                self.end_headers()
                self.wfile.write(b"ok")
                return
        if mode == "offhost":
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/sso")
            self.end_headers()
            return
        if mode == "loop":
            self.send_response(302)
            self.send_header("Location", f"https://127.0.0.1:{PORT}/next")
            self.end_headers()
            return
        if mode == "clean":
            self.send_response(200)
            self.send_header("Set-Cookie", "session=zzz; Path=/; HttpOnly; Secure")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


class CookieSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global PORT
        cls._tmp = tempfile.mkdtemp()
        cls.crt = os.path.join(cls._tmp, "c.pem")
        key = os.path.join(cls._tmp, "k.pem")
        # Self-signed and its own CA bundle, the same shape the real check is
        # handed by the greffer.
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", key, "-out", cls.crt, "-days", "1", "-subj", "/CN=127.0.0.1",
             "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
            check=True, capture_output=True)
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        PORT = cls.srv.server_address[1]
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cls.crt, key)
        cls.srv.socket = ctx.wrap_socket(cls.srv.socket, server_side=True)
        cls._thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls._thread.start()
        cls.url = f"https://127.0.0.1:{PORT}/"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _run(self, mode):
        SCENARIO["mode"] = mode
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = smoke.check_cookie_security("probe/1.0", self.url, self.crt)
        return ok, buf.getvalue()

    def test_secure_cookie_passes(self):
        ok, _ = self._run("clean")
        self.assertTrue(ok)

    def test_insecure_cookie_behind_a_state_cookie(self):
        """The cookie is two hops in and gated on hop 1's jar being carried."""
        ok, out = self._run("chain")
        self.assertFalse(ok, out)
        self.assertIn("session is HttpOnly but not Secure", out)

    def test_offhost_redirect_is_not_followed(self):
        """An app redirecting to an external IdP must not fail the entry: the
        CA bundle trusts only this instance, so fetching it raises SSLError."""
        ok, out = self._run("offhost")
        self.assertTrue(ok, out)
        self.assertIn("leaves the instance host", out)

    def test_redirect_budget_exhaustion_fails(self):
        """A same-host loop leaves `r` holding a 3xx, which would otherwise sail
        through the <500 check and report success on an unreached destination."""
        ok, out = self._run("loop")
        self.assertFalse(ok, out)
        self.assertIn("still redirecting on the same host", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
