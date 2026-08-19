#!/usr/bin/env python3
"""
Greffer-only integration + smoke runner for catalog entries.

Deploys each changed greffon through the **real public greffer** (no manager,
no private repos, no PAT) and runs its Playwright smoke spec against the live
instance, then tears it down.

How it bypasses the manager
---------------------------
The greffer is the component that actually renders the compose and brings the
stack up (verbatim service names, stripped networks, per-instance nginx with
X-Forwarded-Proto, SMTP injection, secret rendering — i.e. where essentially
every catalog bug lives). The manager's role at deploy time is the thin one:
mint the greffer token, issue a cert, and POST a `greffon` payload to the
greffer's `/api/controller/start/`. This runner reproduces that payload from the
entry's own metadata.json + a self-signed cert + a known token (the greffer
exposes `GREFFER_TOKEN` "primarily for tests", per app/settings.py).

instance_url
------------
We send an empty `ports` map, so the greffer takes its documented dev/test
fallback and builds `instance_url = ${GREFFER_PUBLIC_SCHEME}://${GREFFER_PUBLIC_HOST}:<allocated-port>`
— i.e. https://<CI_PUBLIC_HOST>:<port>, WITH the real port. That mirrors a real deploy
closely and avoids the portless-placeholder problem a manager-driven harness has.

Usage
-----
    GREFFER_DIR=/path/to/greffer python ci_greffer_smoke.py --changed-dirs "multica/1.0\nmetabase/1.0"
    GREFFER_DIR=/path/to/greffer python ci_greffer_smoke.py --all
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid

import requests

CATALOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GREFFER_DIR = os.environ.get("GREFFER_DIR", os.path.join(os.path.dirname(CATALOG_DIR), "greffer"))

GREFFER_TOKEN = os.environ.get("CI_GREFFER_TOKEN", "ci-greffer-token")
GREFFER_PORT = int(os.environ.get("CI_GREFFER_PORT", "9001"))
CATALOG_PORT = int(os.environ.get("CI_CATALOG_PORT", "9999"))
# NOT "localhost", deliberately, and the mechanism is worth stating precisely
# because the obvious guess is wrong. The discriminator is the HOST, not the
# perceived scheme. Apps that implement the W3C potentially-trustworthy-origin
# rule server-side treat a loopback Host as secure REGARDLESS of scheme: Keycloak's
# SecureContextResolver accepts `scheme == https` OR a loopback host, and
# DefaultCookieProvider then drops `Secure` and downgrades SameSite=None to Lax
# when the origin is not trusted. Measured on 26.7.2 with the request scheme held
# at http and X-Forwarded-Proto: https sent in every case, varying only the Host:
#
#   Host localhost,        proxy-headers unset -> Secure; SameSite=None
#   Host catalog-ci.test,  proxy-headers unset -> no Secure; SameSite=Lax
#   Host catalog-ci.test,  xforwarded          -> Secure; SameSite=None
#
# So deploying at localhost hid a real regression, and CI went green on it.
#
# Note what this does NOT cover, so nobody scopes an audit by the wrong rule:
# frameworks that key purely on the scheme are unaffected by the hostname. Django's
# HttpRequest.is_secure() is `return self.scheme == "https"` with no loopback
# carve-out, so a Django greffon emits identical cookie flags either way and needs
# its proxy/scheme settings checked directly instead.
#
# The workflow maps this name to 127.0.0.1 in /etc/hosts, so it resolves with no
# external DNS dependency.
PUBLIC_HOST = os.environ.get("CI_PUBLIC_HOST", "catalog-ci.test")
GREFFER_BASE = f"http://127.0.0.1:{GREFFER_PORT}"
TOKEN_HEADER = "X-GREFFON-TOKEN"
# GREFFON_PATH for the greffer: each instance's rendered compose lands at
# DATA_DIR/<instance_id>/docker-compose.yml. Teardown targets that exact file.
DATA_DIR = os.environ.get("CI_GREFFON_DATA", os.path.join("/tmp", "ci-greffon-data"))

ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def log(msg: str) -> None:
    print(f"[ci-smoke] {msg}", flush=True)


# ── secret generation (mirrors the manager's greffon-secret formats) ──────────
def gen_secret(length: int = 48) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(length)).decode().rstrip("=")[:max(length, 32)]


def gen_secret_alnum(length: int = 48) -> str:
    return "".join(secrets.choice(ALNUM) for _ in range(max(length, 32)))


# ── self-signed cert for the per-instance nginx (Playwright ignores cert errs) ─
def make_cert(tmp: str) -> dict:
    key_path = os.path.join(tmp, "ci.key")
    crt_path = os.path.join(tmp, "ci.crt")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", key_path, "-out", crt_path, "-days", "2",
         "-subj", f"/CN={PUBLIC_HOST}",
         # SAN, not just CN. OpenSSL and Chromium both ignore CN for hostname
         # matching, so a CN-only cert is name-invalid no matter what it says.
         # Without this the cookie probe below could not verify the certificate
         # and would be back to verify=False.
         "-addext", f"subjectAltName=DNS:{PUBLIC_HOST}"],
        check=True, capture_output=True,
    )
    with open(crt_path) as f:
        cert = f.read()
    with open(key_path) as f:
        key = f.read()
    return {"certificate": cert, "private_key": key}


def load_required_config(entry_dir: str) -> dict:
    """``required_config`` from the entry's optional smoke_test.json (may be {})."""
    path = os.path.join(CATALOG_DIR, entry_dir, "smoke_test.json")
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        pins = (json.load(f) or {}).get("required_config") or {}
    if pins:
        log(f"{entry_dir}: pinning config {sorted(pins)} from smoke_test.json")
    return pins


# ── build the /start/ configurations[] from an entry's metadata.json ───────────
def build_configurations(metadata: dict, required_config: dict | None = None) -> list:
    """Values for the /start/ payload; ``required_config`` pins them by title.

    A spec that logs in has to know the credential it is logging in with.
    Without a pin, a config with an empty default (an admin password) gets a
    fresh random value on every run — see the fallback at the end of this
    function — so any credential hardcoded in a spec can never match, and the
    spec fails on every run. smoke_test.json's ``required_config`` is the
    documented way to pin one (.github/README.md), but this greffer-only
    harness never read it, silently breaking every such spec.
    """
    required_config = required_config or {}
    titles = {c.get("title") for c in metadata.get("configurations", []) or []}
    unknown = set(required_config) - titles
    if unknown:
        # A typo'd key would otherwise pin nothing and read as working — the
        # exact silent-no-op this function is being fixed for.
        raise SystemExit(
            f"smoke_test.json required_config keys match no configuration title: {sorted(unknown)}")
    configs = []
    for cfg in metadata.get("configurations", []) or []:
        dests = cfg.get("destinations", []) or []
        types = {d.get("type") for d in dests}
        pinned = required_config.get(cfg.get("title"))
        if pinned is not None and types & {"smtp", "file", "json"}:
            # Only a scalar {"value": ...} config can be pinned; smtp/file/json
            # values have a different shape entirely.
            raise SystemExit(
                f"smoke_test.json required_config pins {cfg.get('title')!r}, but its "
                f"destinations are {'/'.join(sorted(types))} — only scalar configs can be pinned")
        # SMTP-only configs carry no user value; the greffer fills them from the
        # integrations.smtp blob (or strips them when no integration is linked).
        if types == {"smtp"}:
            configs.append({"value": {}, "destinations": dests})
            continue
        # File configs (baked data-URI files: nginx confs, Keycloak realms, app
        # config files). Their value shape is {"file": <data-uri>}, NOT the
        # scalar {"value": ...} below — the greffer's ``file`` destination reads
        # ``configuration["value"]["file"]``. These are typically hidden,
        # render-flagged entries, so pass the baked default_value.file straight
        # through (the greffer renders any {{ ... }} markers in it). Falls back
        # to an empty data-URI so a default-less file config writes an empty
        # file rather than crashing DataURI() on "".
        if "file" in types:
            # `or` (not .get's default) so an explicit empty-string default,
            # default_value {"file": ""} which the validator allows for a
            # required file with no baked content, also falls back. Otherwise
            # "" is sent through and the greffer crashes parsing DataURI("").
            file_default = (cfg.get("default_value", {}) or {}).get("file") or "data:text/plain;base64,"
            configs.append({"value": {"file": file_default}, "destinations": dests})
            continue
        schema_val = (cfg.get("schema", {}) or {}).get("properties", {}).get("value", {}) or {}
        fmt = schema_val.get("format")
        default = (cfg.get("default_value", {}) or {}).get("value")
        if pinned is not None:
            value = str(pinned)  # explicit smoke_test.json pin wins
        elif fmt == "greffon-secret":
            value = gen_secret()
        elif fmt == "greffon-secret-alnum":
            value = gen_secret_alnum()
        elif default not in (None, ""):
            value = default  # may contain {{ instance_url }} etc.; rendered by the greffer
        else:
            # user-required field with no default (e.g. an admin password)
            value = "CiSmoke-" + gen_secret_alnum(20)
        configs.append({"value": {"value": value}, "destinations": dests})
    return configs


def url_env_name(greffon_dir: str) -> str:
    """multica -> MULTICA_URL ; paperless-ngx -> PAPERLESS_NGX_URL (matches specs)."""
    return re.sub(r"[^A-Z0-9]", "_", greffon_dir.upper()) + "_URL"


def require_public_host_resolves() -> None:
    """Fail fast, and legibly, when PUBLIC_HOST does not resolve.

    The default is deliberately not `localhost` (see PUBLIC_HOST above), which
    means a fresh local checkout has nothing mapping it. Without this check that
    shows up much later as a connection error against the deployed instance and
    reads like a broken greffon, which is the sort of misdirection that costs an
    afternoon."""
    try:
        socket.gethostbyname(PUBLIC_HOST)
    except OSError:
        raise SystemExit(
            f"CI_PUBLIC_HOST={PUBLIC_HOST!r} does not resolve.\n"
            f"  CI maps it in /etc/hosts (see the workflow's 'Map the smoke hostname' step).\n"
            f"  Locally, either add it:   echo '127.0.0.1 {PUBLIC_HOST}' | sudo tee -a /etc/hosts\n"
            f"  or point it at a public loopback name:   CI_PUBLIC_HOST=localtest.me\n"
            f"  Do NOT set it to 'localhost': apps applying the secure-context rule to\n"
            f"  the Host treat loopback as trusted, which hides cookie-security\n"
            f"  regressions (that is why this default changed).")


def wait_port(port: int, name: str, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                log(f"{name} listening on :{port}")
                return
        time.sleep(0.5)
    raise SystemExit(f"{name} did not come up on :{port}")


def deploy(entry_dir: str, metadata: dict, cert: dict,
           required_config: dict | None = None) -> tuple[str, str]:
    """POST /start/, wait for running, return (instance_id, url)."""
    # Must be a real UUID: /start/ accepts any [A-Za-z0-9_-] id, but the status
    # route is typed `greffon_id: UUID` and 422s on anything else, so the poll
    # below would never see "running". A UUID satisfies both.
    instance_id = str(uuid.uuid4())
    greffon_path, version = entry_dir.split("/", 1)
    repo_url = f"http://127.0.0.1:{CATALOG_PORT}/{greffon_path}/{version}/docker-compose.yml"
    payload = {
        "id": instance_id,
        "repository_url": repo_url,
        "cert": cert,
        "configurations": build_configurations(metadata, required_config),
        "ports": {},          # empty -> greffer fallback builds https://host:port
        "integrations": {},   # no SMTP integration -> smtp env keys stripped
    }
    log(f"deploy {entry_dir} as {instance_id}")
    r = requests.post(
        f"{GREFFER_BASE}/api/controller/start/",
        json=payload, headers={TOKEN_HEADER: GREFFER_TOKEN}, timeout=300,
    )
    if r.status_code != 200:
        raise RuntimeError(f"/start/ -> {r.status_code}: {r.text[:500]}")
    ports = r.json().get("ports") or []
    port_host = next((p.get("port_host") for p in ports if isinstance(p, dict) and p.get("port_host")), None)
    if not port_host:
        raise RuntimeError(f"no port_host in start response: {r.json()}")
    url = f"https://{PUBLIC_HOST}:{port_host}"

    # Poll the greffer for container running status.
    deadline = time.time() + 240
    while time.time() < deadline:
        s = requests.get(f"{GREFFER_BASE}/api/controller/greffon/{instance_id}/",
                         headers={TOKEN_HEADER: GREFFER_TOKEN}, timeout=30)
        if s.status_code == 200 and s.json().get("status") == "running":
            log(f"{entry_dir} running at {url}")
            return instance_id, url
        time.sleep(3)
    raise RuntimeError(f"{entry_dir} did not reach running status")


def teardown(instance_id: str) -> None:
    try:
        requests.post(f"{GREFFER_BASE}/api/controller/stop/", json={"id": instance_id},
                      headers={TOKEN_HEADER: GREFFER_TOKEN}, timeout=120)
    except requests.RequestException:
        pass
    # Drop the project's containers + its namespaced named volumes. Must pass
    # the generated per-instance compose with `-f`: `down -v` only removes
    # volumes declared in the loaded compose file, and the file's parent dir
    # basename (== instance_id) is the project name the greffer created it under.
    # Without `-f` this would run against the greffer checkout's cwd and leak the
    # app's namespaced volumes across entries.
    compose_file = os.path.join(DATA_DIR, instance_id, "docker-compose.yml")
    if os.path.isfile(compose_file):
        subprocess.run(["docker", "compose", "-f", compose_file, "down", "-v", "--remove-orphans"],
                       capture_output=True)


def check_cookie_security(entry_dir: str, url: str, ca_path: str) -> bool:
    """Every instance is served over TLS, so a session cookie without `Secure` is
    wrong regardless of the app. Fetch the instance root and fail the entry if any
    HttpOnly cookie it sets lacks Secure.

    Two things this deliberately does NOT do, because getting either wrong makes
    the check worse than useless:

    1. It does not fire immediately. ``deploy`` waits for compose to report
       ``running``, which is not the same as the app serving: a slow starter has
       the sidecar answering 502 for tens of seconds, and a 502 carries no cookies,
       so a one-shot probe would pass vacuously on exactly the apps most likely to
       be misconfigured. Poll until a non-5xx response.
    2. It does not disable certificate verification. The repo rule is "never
       verify=False", and beyond the rule, verifying is what proves the cert minted
       for PUBLIC_HOST is actually usable for that name, which is the whole point of
       moving off localhost. The cert is self-signed, so it is its own CA bundle.

    Scope, stated so nobody over-trusts it: it only sees cookies set on the ROOT.
    An app that sets its session cookie on a login or authorization endpoint (Keycloak
    does) needs the assertion in its own spec. This is a floor, not coverage."""
    deadline = time.time() + 180
    r = None
    while time.time() < deadline:
        try:
            r = requests.get(url, verify=ca_path, timeout=10, allow_redirects=False)
        except requests.RequestException:
            r = None
        else:
            if r.status_code < 500:
                break
        time.sleep(3)
    if r is None or r.status_code >= 500:
        log(f"{entry_dir}: cookie check INCONCLUSIVE, instance never returned a non-5xx "
            f"response within 180s (last: {getattr(r, 'status_code', 'no response')}). "
            f"Failing rather than passing silently: an unreachable instance proves nothing.")
        return False
    raw = r.raw.headers.get_all("Set-Cookie") if hasattr(r.raw, "headers") else None
    bad = [c for c in (raw or [])
           if "httponly" in c.lower() and not re.search(r";\s*secure", c, re.I)]
    if bad:
        for c in bad:
            log(f"{entry_dir}: INSECURE COOKIE {c.split('=')[0]} is HttpOnly but not Secure")
        log(f"{entry_dir}: instances are served over TLS, so an HttpOnly cookie without "
            f"Secure can ride a plaintext request. If the app derives this from the request "
            f"scheme it needs telling it sits behind a TLS-terminating proxy.")
        return False
    return True


def run_smoke(entry_dir: str, url: str) -> bool:
    spec = os.path.join(CATALOG_DIR, entry_dir, "smoke_test.spec.ts")
    if not os.path.isfile(spec):
        log(f"{entry_dir}: no smoke_test.spec.ts — skipping (deploy succeeded)")
        return True
    env = {**os.environ, url_env_name(entry_dir.split("/")[0]): url, "NODE_TLS_REJECT_UNAUTHORIZED": "0"}
    log(f"{entry_dir}: playwright {url_env_name(entry_dir.split('/')[0])}={url}")
    res = subprocess.run(
        ["npx", "playwright", "test", os.path.relpath(spec, CATALOG_DIR), "--workers=1"],
        cwd=CATALOG_DIR, env=env,
    )
    return res.returncode == 0


def start_greffer(tmp_data: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "GREFFER_ID": os.environ.get("CI_GREFFER_ID", "ci-greffer"),  # required setting (register payload only; workers off)
        "GREFFER_TOKEN": GREFFER_TOKEN,
        "GREFFON_PATH": tmp_data,
        "GREFFER_PUBLIC_HOST": PUBLIC_HOST,
        "GREFFER_PUBLIC_SCHEME": "https",
        "GREFFER_WORKERS_ENABLED": "false",  # no register/monitor/crl against a manager
    }
    log(f"starting greffer ({GREFFER_DIR}) on :{GREFFER_PORT}")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "--factory", "app.main:create_app",
         "--host", "127.0.0.1", "--port", str(GREFFER_PORT)],
        cwd=GREFFER_DIR, env=env,
    )
    wait_port(GREFFER_PORT, "greffer")
    return proc


def start_catalog_server() -> subprocess.Popen:
    log(f"serving catalog on :{CATALOG_PORT}")
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(CATALOG_PORT)],
        cwd=CATALOG_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    wait_port(CATALOG_PORT, "catalog-server")
    return proc


def discover_all() -> list:
    dirs = []
    for g in sorted(os.listdir(CATALOG_DIR)):
        gp = os.path.join(CATALOG_DIR, g)
        if not os.path.isdir(gp) or g.startswith(".") or g in {"unsupported"}:
            continue
        for v in sorted(os.listdir(gp)):
            if os.path.isfile(os.path.join(gp, v, "metadata.json")):
                dirs.append(f"{g}/{v}")
    return dirs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changed-dirs", default="")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if args.all:
        entries = discover_all()
    else:
        entries = [d.strip() for d in re.split(r"[\n,]", args.changed_dirs) if d.strip()]
    entries = [e for e in entries if os.path.isfile(os.path.join(CATALOG_DIR, e, "metadata.json"))]
    if not entries:
        log("no entries to test")
        return 0
    log(f"entries: {entries}")

    tmp_data = DATA_DIR
    shutil.rmtree(tmp_data, ignore_errors=True)
    os.makedirs(tmp_data, exist_ok=True)

    require_public_host_resolves()
    greffer_proc = start_greffer(tmp_data)
    catalog_proc = start_catalog_server()
    results = {}
    try:
        for entry in entries:
            with open(os.path.join(CATALOG_DIR, entry, "metadata.json")) as f:
                metadata = json.load(f)
            required_config = load_required_config(entry)
            tmp_cert = os.path.join(tmp_data, "certs", entry.replace("/", "_"))
            os.makedirs(tmp_cert, exist_ok=True)
            instance_id = None
            try:
                cert = make_cert(tmp_cert)
                instance_id, url = deploy(entry, metadata, cert, required_config)
                cookies_ok = check_cookie_security(
                    entry, url, os.path.join(tmp_cert, "ci.crt"))
                results[entry] = run_smoke(entry, url) and cookies_ok
            except Exception as e:  # noqa: BLE001 — report per-entry, continue
                log(f"{entry}: FAILED — {e}")
                results[entry] = False
            finally:
                if instance_id:
                    teardown(instance_id)
    finally:
        greffer_proc.terminate()
        catalog_proc.terminate()

    log("── results ──")
    for entry, ok in results.items():
        log(f"  {'PASS' if ok else 'FAIL'}  {entry}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
