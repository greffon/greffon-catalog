#!/usr/bin/env python3
"""
Integration test for greffon catalog entries.

Spins up manager + greffer (reusing the Behave E2E pattern),
imports changed greffons, creates instances, starts them,
and verifies containers reach STARTED status.

This script is designed to run from within the parent greffon repo
after the catalog submodule has been replaced with the PR's code.

Usage:
    python test_greffon.py --changed-dirs "nextcloud/1.0\nplausible/2.0"
    python test_greffon.py --all
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

import requests

# --- Paths (relative to parent greffon repo root) ---
ROOT_DIR = os.environ.get(
    "GREFFON_ROOT",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
)
MANAGER_DIR = os.path.join(ROOT_DIR, "manager", "backend")
GREFFER_DIR = os.path.join(ROOT_DIR, "greffer")
CATALOG_DIR = os.path.join(ROOT_DIR, "greffon-catalog")

# --- Ports ---
MANAGER_PORT = int(os.environ.get("CI_MANAGER_PORT", "9000"))
CATALOG_PORT = int(os.environ.get("CI_CATALOG_PORT", "9999"))
GREFFER_PORT = int(os.environ.get("CI_GREFFER_PORT", "9001"))

MANAGER_URL = f"http://localhost:{MANAGER_PORT}"

# --- Constants ---
GREFFER_ID = "00000000-ci00-test-0000-000000000001"
ADMIN_TOKEN = "ci-admin-token"
COMPOSE_PROJECT = "greffer-ci"
CATALOG_CONTAINER = "ci-catalog-server"

# Status codes from manager models
STATUS_STARTED = 2


def log(msg):
    print(f"[test_greffon] {msg}", flush=True)


def wait_for_http(url, name, timeout=30):
    """Poll a URL until it responds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(url, timeout=2)
            return True
        except requests.ConnectionError:
            time.sleep(1)
    raise TimeoutError(f"{name} did not start within {timeout}s at {url}")


def run_manage(*args, env=None):
    """Run a manage.py command in the manager backend."""
    return subprocess.run(
        [sys.executable, "manage.py"] + list(args),
        cwd=MANAGER_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestEnvironment:
    """Manages the lifecycle of manager + greffer + catalog server."""

    def __init__(self):
        self.manager_proc = None
        self.greffer_proc = None
        self.db_dir = None
        self.manager_env = None

    def start(self):
        log("Setting up test environment...")
        self.db_dir = tempfile.mkdtemp(prefix="ci_catalog_")

        # Manager environment
        self.manager_env = os.environ.copy()
        self.manager_env["DJANGO_SETTINGS_MODULE"] = "backend.settings_e2e"
        self.manager_env["E2E_DB_DIR"] = self.db_dir

        # Migrate
        log("Running migrations...")
        result = run_manage("migrate", "--run-syncdb", env=self.manager_env)
        if result.returncode != 0:
            raise RuntimeError(f"Migrate failed: {result.stderr}")

        # Create django Site (required by allauth)
        run_manage(
            "shell", "-c",
            "from django.contrib.sites.models import Site; "
            "Site.objects.update_or_create(id=1, defaults={'domain': 'localhost', 'name': 'localhost'})",
            env=self.manager_env,
        )

        # Create greffer record
        log(f"Creating greffer record {GREFFER_ID}...")
        run_manage(
            "shell", "-c",
            f"from apps.greffer.models import Greffer; "
            f"Greffer.objects.create(id='{GREFFER_ID}', name='ci-greffer')",
            env=self.manager_env,
        )

        # Start manager
        log(f"Starting manager on port {MANAGER_PORT}...")
        self.manager_proc = subprocess.Popen(
            [sys.executable, "manage.py", "runserver", f"0.0.0.0:{MANAGER_PORT}", "--noreload"],
            cwd=MANAGER_DIR,
            env=self.manager_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        wait_for_http(f"{MANAGER_URL}/api/greffer/", "Manager", timeout=20)

        # Create admin user + token
        log("Creating admin user...")
        run_manage(
            "shell", "-c",
            "from django.contrib.auth import get_user_model; "
            "User = get_user_model(); "
            f"u = User.objects.create_superuser('admin', 'admin@ci.com', 'adminpass'); "
            f"from rest_framework.authtoken.models import Token; "
            f"Token.objects.create(user=u, key='{ADMIN_TOKEN}')",
            env=self.manager_env,
        )

        # Assign greffer to admin
        run_manage(
            "shell", "-c",
            f"from apps.greffer.models import Greffer; "
            f"from django.contrib.auth import get_user_model; "
            f"g = Greffer.objects.get(id='{GREFFER_ID}'); "
            f"g.owner = get_user_model().objects.get(username='admin'); g.save()",
            env=self.manager_env,
        )

        # Write greffer env file
        greffer_env_file = os.path.join(self.db_dir, "greffer_ci.env")
        with open(greffer_env_file, "w") as f:
            f.write(f"GREFFON_BASE_SERVER=http://host.docker.internal:{MANAGER_PORT}\n")
            f.write("GREFFON_PATH=/app/data\n")
            f.write("GREFFER_ADDRESS=host.docker.internal\n")
            f.write(f"GREFFER_PORT={GREFFER_PORT}\n")
            f.write("GREFFER_PROTOCOL=http\n")
            f.write(f"GREFFER_ID={GREFFER_ID}\n")
            f.write("GREFFER_SSL_VERIFY=false\n")
            f.write(f"DOCKER_NGINX_NAME={COMPOSE_PROJECT}-nginx-1\n")

        # Start greffer
        log("Starting greffer...")
        self.greffer_proc = subprocess.Popen(
            [
                "docker", "compose",
                "-f", "docker-compose.yml",
                "-p", COMPOSE_PROJECT,
                "up", "--build",
            ],
            cwd=GREFFER_DIR,
            env={**os.environ, "COMPOSE_ENV_FILE": greffer_env_file},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for greffer registration
        log("Waiting for greffer registration...")
        self._wait_for_registration(timeout=60)

        # Accept registration
        log("Accepting greffer registration...")
        headers = {"Authorization": f"Token {ADMIN_TOKEN}"}
        resp = requests.post(
            f"{MANAGER_URL}/api/greffer/register/accept/{GREFFER_ID}/",
            headers=headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Accept registration failed: {resp.status_code} {resp.text}")

        # Start catalog HTTP server
        log(f"Starting catalog server on port {CATALOG_PORT}...")
        subprocess.run(["docker", "rm", "-f", CATALOG_CONTAINER], capture_output=True)
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", CATALOG_CONTAINER,
                "--network", "host",
                "-v", f"{CATALOG_DIR}:/catalog:ro",
                "-w", "/catalog",
                "python:3.11-slim",
                "python", "-m", "http.server", str(CATALOG_PORT),
            ],
            check=True,
            capture_output=True,
        )
        wait_for_http(f"http://localhost:{CATALOG_PORT}/", "Catalog server", timeout=15)

        # Give greffer a moment to finish cert setup
        time.sleep(3)
        log("Test environment ready.")

    def _wait_for_registration(self, timeout=60):
        """Wait until greffer has registered (status=1 REGISTERING)."""
        headers = {"Authorization": f"Token {ADMIN_TOKEN}"}
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = requests.get(f"{MANAGER_URL}/api/greffer/", headers=headers)
                if resp.status_code == 200:
                    for g in resp.json().get("data", []):
                        if str(g["id"]) == GREFFER_ID and g["status"] == 1:
                            return
            except requests.ConnectionError:
                pass
            time.sleep(2)
        raise TimeoutError("Greffer did not register within timeout")

    def teardown(self):
        log("Tearing down test environment...")

        # Stop greffer containers
        subprocess.run(
            ["docker", "compose", "-p", COMPOSE_PROJECT, "down", "--remove-orphans"],
            cwd=GREFFER_DIR,
            capture_output=True,
            timeout=30,
        )
        if self.greffer_proc:
            self.greffer_proc.terminate()
            try:
                self.greffer_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.greffer_proc.kill()

        # Stop catalog server
        subprocess.run(["docker", "rm", "-f", CATALOG_CONTAINER], capture_output=True)

        # Stop manager
        if self.manager_proc:
            self.manager_proc.terminate()
            try:
                self.manager_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.manager_proc.kill()

        log("Teardown complete.")


def import_greffon(env, greffon_dir):
    """Import a greffon into the manager DB. Returns (greffon_version_id, config_ids)."""
    parts = greffon_dir.strip("/").split("/")
    greffon_name, version = parts[0], parts[1]

    meta_path = os.path.join(CATALOG_DIR, greffon_dir, "metadata.json")
    with open(meta_path) as f:
        meta = json.load(f)

    display_name = meta["name"]
    compose_url = f"http://host.docker.internal:{CATALOG_PORT}/{greffon_name}/{version}/docker-compose.yml"

    # Create Greffon + GreffonVersion
    result = run_manage(
        "shell", "-c",
        f"from django.contrib.auth import get_user_model; "
        f"from apps.greffonmanager.models import Greffon, GreffonVersion; "
        f"user = get_user_model().objects.get(username='admin'); "
        f"g, _ = Greffon.objects.get_or_create(name='{display_name}', defaults={{'owner': user}}); "
        f"gv, _ = GreffonVersion.objects.get_or_create("
        f"  greffon=g, version='{version}', "
        f"  defaults={{'compose_path': '{compose_url}'}}); "
        f"print(gv.id)",
        env=env.manager_env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to import greffon: {result.stderr}")
    version_id = result.stdout.strip().split("\n")[-1]

    # Create GreffonVersionConfiguration records from metadata
    configurations = meta.get("configurations", [])
    for i, cfg in enumerate(configurations):
        title = cfg.get("title", f"config_{i}")
        schema_json = json.dumps(cfg.get("schema", {})).replace("'", "\\'")
        default_json = json.dumps(cfg.get("default_value", {})).replace("'", "\\'")
        destinations_json = json.dumps(cfg.get("destinations", [])).replace("'", "\\'")

        run_manage(
            "shell", "-c",
            "import json; "
            "from apps.greffonmanager.models import GreffonVersionConfiguration; "
            f"GreffonVersionConfiguration.objects.get_or_create("
            f"  greffon_version_id={version_id}, "
            f"  title='{title}', "
            f"  defaults={{"
            f"    'schema': json.loads('{schema_json}'), "
            f"    'default_value': json.loads('{default_json}'), "
            f"    'destinations': json.loads('{destinations_json}'), "
            f"  }}"
            f")",
            env=env.manager_env,
        )

    # Import port info from docker-compose.yml
    compose_path = os.path.join(CATALOG_DIR, greffon_dir, "docker-compose.yml")
    try:
        import yaml
        with open(compose_path) as f:
            compose = yaml.safe_load(f)
        services = compose.get("services", {})
        for svc_name, svc_def in services.items():
            if not isinstance(svc_def, dict):
                continue
            ports = svc_def.get("ports", [])
            for port_entry in ports:
                # Parse port string like "8080:80" or just "80"
                port_str = str(port_entry).split(":")[-1].split("/")[0]
                try:
                    port_num = int(port_str)
                except ValueError:
                    continue
                run_manage(
                    "shell", "-c",
                    "from apps.greffonmanager.models import GreffonVersionPort; "
                    f"GreffonVersionPort.objects.get_or_create("
                    f"  greffon_version_id={version_id}, "
                    f"  port={port_num}, "
                    f"  defaults={{'protocol': 'https'}}"
                    f")",
                    env=env.manager_env,
                )
    except Exception as e:
        log(f"  Warning: could not parse ports from compose: {e}")

    log(f"  Imported greffon '{display_name}' v{version} (version_id={version_id})")
    return version_id


def build_instance_configurations(env, version_id, smoke_test):
    """Build configurations list for instance creation if smoke_test has required_config."""
    required_config = smoke_test.get("required_config") if smoke_test else None
    if not required_config:
        return []

    # Look up GreffonVersionConfiguration records and match by title
    configurations = []
    for config_title, config_value in required_config.items():
        result = run_manage(
            "shell", "-c",
            "from apps.greffonmanager.models import GreffonVersionConfiguration; "
            f"cfgs = GreffonVersionConfiguration.objects.filter("
            f"  greffon_version_id={version_id}, title='{config_title}'); "
            f"print(cfgs[0].id if cfgs.exists() else '')",
            env=env.manager_env,
        )
        cfg_id = result.stdout.strip().split("\n")[-1]
        if cfg_id:
            configurations.append({
                "greffon_version_configuration_id": cfg_id,
                "value": {"value": config_value},
            })
            log(f"  Config: {config_title} = {config_value[:20]}...")
    return configurations


def test_greffon_lifecycle(env, greffon_dir, version_id, smoke_test=None, timeout=120):
    """Create instance, start it, verify it reaches STARTED status. Returns instance address."""
    headers = {"Authorization": f"Token {ADMIN_TOKEN}"}
    instance_name = f"ci-test-{greffon_dir.replace('/', '-')}"

    # Build configurations if smoke_test requires them
    configurations = build_instance_configurations(env, version_id, smoke_test)

    # Create instance
    log(f"  Creating instance '{instance_name}'...")
    resp = requests.post(
        f"{MANAGER_URL}/api/greffon/instances/",
        headers=headers,
        json={
            "name": instance_name,
            "greffon_version_id": version_id,
            "greffer_id": GREFFER_ID,
            "configurations": configurations,
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Create instance failed: {resp.status_code} {resp.text}")
    instance = resp.json()

    # Start instance
    log(f"  Starting instance '{instance_name}'...")
    resp = requests.put(
        f"{MANAGER_URL}/api/greffer/",
        headers=headers,
        json={
            "greffon_instance_id": str(instance["id"]),
            "action": "start",
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Start instance failed: {resp.status_code} {resp.text}")

    # Poll for STARTED status
    log(f"  Waiting for STARTED status (timeout={timeout}s)...")
    deadline = time.time() + timeout
    last_status = None
    instance_address = None
    while time.time() < deadline:
        resp = requests.get(f"{MANAGER_URL}/api/greffon/instances/", headers=headers)
        if resp.status_code == 200:
            for inst in resp.json().get("greffons", []):
                if inst["name"] == instance_name:
                    last_status = inst["status"]
                    fields = inst.get("fields", [])
                    if fields and fields[0].get("address"):
                        instance_address = fields[0]["address"]
                    if last_status == STATUS_STARTED:
                        log(f"  PASS: '{instance_name}' reached STARTED")
                        return instance_address
        time.sleep(5)

    raise RuntimeError(
        f"Instance '{instance_name}' did not reach STARTED within {timeout}s "
        f"(last status: {last_status})"
    )


def run_smoke_test(greffon_dir, instance_address, smoke_test, timeout=60):
    """Hit the greffon's HTTP endpoint and verify it responds correctly."""
    if not smoke_test:
        log("  SKIP: no smoke_test defined")
        return True

    path = smoke_test.get("path", "/")
    expected_status = smoke_test.get("expected_status", [200])
    expected_body = smoke_test.get("expected_body_contains")

    # Replace host.docker.internal with localhost for host-side access
    url = instance_address.replace("host.docker.internal", "localhost")
    if not url.startswith("http"):
        url = f"https://{url}"
    url = f"{url}{path}"

    log(f"  Smoke test: GET {url}")
    log(f"  Expecting status: {expected_status}, body contains: {expected_body!r}")

    # Retry loop — app may need time after containers are up
    deadline = time.time() + timeout
    last_error = None
    last_code = None

    while time.time() < deadline:
        try:
            resp = requests.get(url, verify=False, timeout=10, allow_redirects=False)
            last_code = resp.status_code

            if resp.status_code in expected_status:
                # Status matches — check body if required
                if expected_body is not None:
                    if expected_body.lower() in resp.text.lower():
                        log(f"  SMOKE PASS: HTTP {resp.status_code}, body contains '{expected_body}'")
                        return True
                    else:
                        last_error = (
                            f"HTTP {resp.status_code} but body does not contain '{expected_body}' "
                            f"(got {len(resp.text)} bytes)"
                        )
                else:
                    log(f"  SMOKE PASS: HTTP {resp.status_code}")
                    return True
            else:
                last_error = f"HTTP {resp.status_code} not in {expected_status}"

        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = str(exc)

        time.sleep(5)

    raise RuntimeError(
        f"Smoke test failed for {greffon_dir} at {url}: "
        f"{last_error} (last HTTP status: {last_code})"
    )


def stop_instance(instance_name):
    """Stop a running instance to free resources for the next test."""
    headers = {"Authorization": f"Token {ADMIN_TOKEN}"}
    resp = requests.get(f"{MANAGER_URL}/api/greffon/instances/", headers=headers)
    if resp.status_code != 200:
        return
    for inst in resp.json().get("greffons", []):
        if inst["name"] == instance_name:
            requests.put(
                f"{MANAGER_URL}/api/greffer/",
                headers=headers,
                json={
                    "greffon_instance_id": str(inst["id"]),
                    "action": "stop",
                },
            )
            # Wait a moment for containers to stop
            time.sleep(5)
            return


def find_all_greffon_dirs():
    """Find all greffon dirs in the catalog."""
    dirs = []
    for name in sorted(os.listdir(CATALOG_DIR)):
        name_path = os.path.join(CATALOG_DIR, name)
        if not os.path.isdir(name_path) or name.startswith("."):
            continue
        for version in sorted(os.listdir(name_path)):
            version_path = os.path.join(name_path, version)
            if os.path.isdir(version_path):
                dirs.append(f"{name}/{version}")
    return dirs


def main():
    parser = argparse.ArgumentParser(description="Integration test for greffon catalog")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--changed-dirs", type=str, help="Newline-separated list of changed greffon dirs")
    group.add_argument("--all", action="store_true", help="Test all greffons in catalog")
    args = parser.parse_args()

    if args.all:
        greffon_dirs = find_all_greffon_dirs()
    else:
        greffon_dirs = [d.strip() for d in args.changed_dirs.strip().split("\n") if d.strip()]

    if not greffon_dirs:
        log("No greffons to test.")
        return

    log(f"Will test {len(greffon_dirs)} greffon(s): {greffon_dirs}")

    env = TestEnvironment()
    failed = []

    # Handle SIGTERM/SIGINT gracefully
    def handle_signal(signum, frame):
        env.teardown()
        sys.exit(1)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        env.start()

        for greffon_dir in greffon_dirs:
            log(f"\n{'='*60}")
            log(f"Testing: {greffon_dir}")
            log(f"{'='*60}")

            instance_name = f"ci-test-{greffon_dir.replace('/', '-')}"
            try:
                # Load smoke_test from separate file
                smoke_path = os.path.join(CATALOG_DIR, greffon_dir, "smoke_test.json")
                smoke_test = None
                if os.path.isfile(smoke_path):
                    with open(smoke_path) as f:
                        smoke_test = json.load(f)

                version_id = import_greffon(env, greffon_dir)
                instance_address = test_greffon_lifecycle(
                    env, greffon_dir, version_id, smoke_test=smoke_test,
                )
                run_smoke_test(greffon_dir, instance_address, smoke_test)
            except Exception as e:
                log(f"  FAIL: {greffon_dir} - {e}")
                failed.append((greffon_dir, str(e)))
            finally:
                # Stop instance to free resources for next test
                stop_instance(instance_name)

    finally:
        env.teardown()

    # Summary
    print(f"\n{'='*60}")
    print(f"RESULTS: {len(greffon_dirs) - len(failed)}/{len(greffon_dirs)} passed")
    print(f"{'='*60}")

    if failed:
        print("\nFailed greffons:")
        for name, err in failed:
            print(f"  FAIL: {name} - {err}")
        sys.exit(1)
    else:
        print("\nAll greffons passed integration tests.")
        sys.exit(0)


if __name__ == "__main__":
    main()
