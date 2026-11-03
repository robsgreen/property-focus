"""The dogfood Cloudflare edge (A5): Caddy serves only the Cloudflare Origin CA certificate and
trusts client-IP headers only from Cloudflare's ranges, the dogfood Compose file exposes only
Caddy over TCP, and the smoke check refuses responses that bypass Cloudflare or are
challenge pages.

The Caddyfile is read as text: global options, snippets and sites are top-level blocks, and
each line holds one directive. The Compose file is read through ``docker compose config``,
which needs only the Docker CLI.
"""

import importlib.util
import ipaddress
import json
import re
import socket
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

REPO_ROOT = Path(__file__).resolve().parents[3]
DOGFOOD = REPO_ROOT / "infrastructure" / "dogfood"
CADDYFILE = DOGFOOD / "Caddyfile"
CLOUDFLARE_IPS = DOGFOOD / "cloudflare-ips.txt"
DEPLOY_SCRIPT = DOGFOOD / "deploy.sh"
SMOKE_SCRIPT = REPO_ROOT / "backend" / "scripts" / "smoke_dogfood.py"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-dogfood.yml"

ORIGIN_TLS = "tls /etc/caddy/origin/cert.pem /etc/caddy/origin/key.pem"
SITES = ("{$PF_DOGFOOD_APP_HOST}", "{$PF_DOGFOOD_AUTH_HOST}")
# Directives and subdirectives through which Caddy would obtain or issue a certificate.
CERTIFICATE_AUTOMATION = re.compile(
    r"^(acme\w*|email|issuer|cert_issuer|on_demand\w*|local_certs|ca|ca_root|dns|eab)\b"
)
# A `tls` line that hands certificates to automation instead of naming files.
AUTOMATED_TLS = re.compile(r"^tls\b.*\b(internal|acme|on_demand|issuer|\S+@\S+)\b")
PLACEHOLDER = re.compile(r"\{[^{}\s]+\}")


def caddy_blocks(text: str) -> dict[str, list[str]]:
    """Each top-level block of a Caddyfile by its header (``""`` for the global options):
    the block's lines, stripped, without comments, blank lines or its closing brace."""
    blocks: dict[str, list[str]] = {}
    header = ""
    depth = 0
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        if depth == 0:
            header = line.removesuffix("{").strip()
            blocks[header] = []
        elif not (line == "}" and depth == 1):
            blocks[header].append(line)
        # Placeholders such as {$PF_DOGFOOD_APP_HOST} or {path} are not block braces.
        braces = PLACEHOLDER.sub("", line)
        depth += braces.count("{") - braces.count("}")
    return blocks


def site_problems(text: str) -> list[str]:
    """One message per site of the Caddyfile ``text`` that does not serve the origin
    certificate, or per line anywhere that would make Caddy obtain a certificate."""
    blocks = caddy_blocks(text)
    problems = []
    for header, lines in blocks.items():
        for line in lines:
            if CERTIFICATE_AUTOMATION.match(line) or AUTOMATED_TLS.match(line):
                problems.append(f"{header or 'global options'}: certificate automation: {line}")
        is_site = header != "" and not header.startswith("(")
        if is_site and ORIGIN_TLS not in lines:
            problems.append(f"{header}: does not serve the origin certificate")
    if "auto_https disable_certs" not in blocks.get("", []):
        problems.append("global options: certificate automation is not disabled")
    return problems


def global_options(text: str) -> list[list[str]]:
    """The directives of the global options and their ``servers`` block, each split into words."""
    return [
        line.split() for line in caddy_blocks(text)[""] if line != "}" and not line.endswith("{")
    ]


def cloudflare_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """The networks of cloudflare-ips.txt; raises ``ValueError`` for a malformed line."""
    networks = []
    for raw in CLOUDFLARE_IPS.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            networks.append(ipaddress.ip_network(line, strict=True))
    return networks


def dogfood_services() -> dict[str, dict[str, Any]]:
    """The dogfood Compose file's services, uninterpolated, in all profiles."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--file",
            str(DOGFOOD / "compose.yaml"),
            "--profile",
            "*",
            "config",
            "--format",
            "json",
            "--no-interpolate",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    services: dict[str, dict[str, Any]] = json.loads(result.stdout)["services"]
    return services


def load_smoke_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_dogfood", SMOKE_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load_smoke_script()


def test_both_sites_serve_the_origin_certificate_and_nothing_obtains_one() -> None:
    text = CADDYFILE.read_text()

    assert set(SITES) <= set(caddy_blocks(text))
    assert site_problems(text) == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda text: text.replace(ORIGIN_TLS, "", 1), "does not serve the origin certificate"),
        (lambda text: text.replace(ORIGIN_TLS, "tls internal", 1), "certificate automation"),
        (lambda text: text.replace(ORIGIN_TLS, "tls ops@example.test", 1), "automation"),
        (
            lambda text: text.replace("\tservers {", "\temail ops@example.test\n\tservers {", 1),
            "certificate automation: email",
        ),
        (
            lambda text: text.replace(
                "auto_https disable_certs", "acme_ca https://acme.example.test", 1
            ),
            "certificate automation",
        ),
        (
            lambda text: text + "\nextra.example.test {\n\trespond 404\n}\n",
            "extra.example.test: does not serve the origin certificate",
        ),
    ],
)
def test_flags_a_site_without_the_origin_certificate_or_with_certificate_automation(
    change: Any, problem: str
) -> None:
    problems = site_problems(change(CADDYFILE.read_text()))

    assert any(problem in message for message in problems), problems


def test_trusted_proxies_and_client_ip_headers_come_only_from_cloudflare() -> None:
    options = global_options(CADDYFILE.read_text())
    trusted = [words for words in options if words[0].startswith("trusted_proxies")]

    assert trusted == [
        ["trusted_proxies", "static", "{$PF_CLOUDFLARE_IP_RANGES}"],
        ["trusted_proxies_strict"],
    ]
    assert ["client_ip_headers", "CF-Connecting-IP", "X-Forwarded-For"] in options
    caddy_environment = dogfood_services()["caddy"]["environment"]
    assert caddy_environment["PF_CLOUDFLARE_IP_RANGES"].startswith("${PF_CLOUDFLARE_IP_RANGES:?")


def test_deploy_passes_exactly_the_committed_ranges_to_caddy() -> None:
    script = DEPLOY_SCRIPT.read_text()
    function = re.search(r"^cloudflare_ranges\(\) \{.*\}$", script, re.MULTILINE)
    assert function is not None
    assert "PF_CLOUDFLARE_IP_RANGES=$(cloudflare_ranges | paste -sd ' ')" in script

    result = subprocess.run(
        ["bash", "-c", f"{function.group()}\ncloudflare_ranges | paste -sd ' '"],
        env={"PATH": "/usr/bin:/bin", "dogfood_dir": str(DOGFOOD)},
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.split() == [str(network) for network in cloudflare_networks()]


def test_cloudflare_ranges_are_valid_networks_of_both_families() -> None:
    networks = cloudflare_networks()

    assert {network.version for network in networks} == {4, 6}
    assert len(set(networks)) == len(networks)


def test_only_caddy_publishes_ports_over_tcp_with_the_origin_directory_read_only() -> None:
    services = dogfood_services()
    caddy = services["caddy"]

    assert {port.get("protocol", "tcp") for port in caddy["ports"]} == {"tcp"}
    assert {str(port["target"]) for port in caddy["ports"]} == {"80", "443"}
    origin = [
        (volume["target"], volume.get("read_only", False))
        for volume in caddy["volumes"]
        if volume.get("source") == "/etc/property-focus/origin"
    ]
    assert origin == [("/etc/caddy/origin", True)]
    assert not services["api"].get("ports")
    assert not services["mcp"].get("ports")


def headers(**values: str) -> Message:
    message = Message()
    for name, value in values.items():
        message[name.replace("_", "-")] = value
    return message


def test_smoke_accepts_a_response_that_came_through_cloudflare() -> None:
    smoke.expect_through_cloudflare("https://app.test/", headers(cf_ray="8f00-SJC"))


def test_smoke_rejects_a_response_that_bypassed_cloudflare() -> None:
    with pytest.raises(smoke.SmokeCheckError, match="no cf-ray header"):
        smoke.expect_through_cloudflare("https://app.test/", headers(server="Caddy"))


def test_smoke_names_bot_fight_mode_for_a_challenge_page() -> None:
    challenge = headers(cf_ray="8f00-SJC", cf_mitigated="challenge")

    with pytest.raises(smoke.SmokeCheckError, match="Bot Fight Mode"):
        smoke.expect_through_cloudflare("https://app.test/mcp", challenge)


def test_smoke_names_bot_fight_mode_when_the_sign_in_meets_a_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def challenged(*_: object, **__: object) -> None:
        raise AssertionError(
            'token request failed: 403 <script src="/cdn-cgi/challenge-platform/h/b/orchestrate">'
        )

    monkeypatch.setattr(smoke.OidcRealm, "sign_in", challenged)

    with pytest.raises(smoke.SmokeCheckError, match="Bot Fight Mode"):
        smoke.sign_in("https://app.test", "https://auth.test", "smoke@example.test", "pw")


def test_smoke_fails_when_the_origin_accepts_a_direct_connection() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]

        with pytest.raises(smoke.SmokeCheckError, match="accepted a direct connection"):
            smoke.check_origin_unreachable("127.0.0.1", port=port)


def test_smoke_passes_when_the_origin_refuses_a_direct_connection() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        closed_port = server.getsockname()[1]

    smoke.check_origin_unreachable("127.0.0.1", port=closed_port)


def test_smoke_requires_an_ip_address_for_the_origin() -> None:
    with pytest.raises(smoke.SmokeCheckError, match="not an IP address"):
        smoke.check_origin_unreachable("origin.invalid")


APP_HOST = "app.example.test"
AUTH_HOST = "auth.example.test"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
# Stands in for Docker: answers `compose config --environment` with the hosts and the smoke
# user, and fails every other command, so a deploy stops at its first image build.
DOCKER_STUB = f"""#!/bin/sh
case "$*" in
*"config --environment"*)
	printf 'PF_DOGFOOD_APP_HOST={APP_HOST}\\nPF_DOGFOOD_AUTH_HOST={AUTH_HOST}\\n'
	printf 'PF_SMOKE_USER_EMAIL=smoke@example.test\\n' ;;
*) echo "docker stub: $*" >&2; exit 97 ;;
esac
"""


def _private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _certificate(
    subject_key: rsa.RSAPrivateKey,
    issuer: x509.Name,
    issuer_key: rsa.RSAPrivateKey,
    *,
    hosts: tuple[str, ...] = (),
    ca: bool = False,
) -> x509.Certificate:
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(issuer if ca else x509.Name([]))
        .issuer_name(issuer)
        .public_key(subject_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if hosts:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(host) for host in hosts]), critical=False
        )
    return builder.sign(issuer_key, hashes.SHA256())


@dataclass
class OriginPairs:
    """A test Origin CA and the deploy's origin directory, with a pair the CA issued live."""

    ca_name: x509.Name
    ca_key: rsa.RSAPrivateKey
    directory: Path

    def pair(self, *, hosts: tuple[str, ...] = (APP_HOST, AUTH_HOST)) -> tuple[bytes, bytes]:
        """A certificate for ``hosts`` from the test CA and its key, as PEM."""
        key = _private_key()
        certificate = _certificate(key, self.ca_name, self.ca_key, hosts=hosts)
        return (
            certificate.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )

    def write(self, suffix: str, certificate: bytes, key: bytes, *, key_mode: int = 0o600) -> None:
        (self.directory / f"cert.pem{suffix}").write_bytes(certificate)
        key_path = self.directory / f"key.pem{suffix}"
        key_path.write_bytes(key)
        key_path.chmod(key_mode)

    def files(self) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in sorted(self.directory.iterdir())}


@pytest.fixture
def deploy_host(tmp_path: Path) -> tuple[OriginPairs, dict[str, str]]:
    """A release directory, environment file, origin directory and stubbed Docker, curl and
    firewall check for running deploy.sh up to its first image build."""
    ca_key = _private_key()
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Origin CA")])
    ca = _certificate(ca_key, ca_name, ca_key, ca=True)
    release = tmp_path / "releases" / COMMIT
    dogfood = release / "infrastructure" / "dogfood"
    dogfood.mkdir(parents=True)
    (dogfood / "compose.yaml").write_text("services: {}\n")
    (dogfood / "cloudflare-ips.txt").write_text(CLOUDFLARE_IPS.read_text())
    (dogfood / "cloudflare-origin-ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (release / "frontend" / "dist").mkdir(parents=True)
    (release / "frontend" / "dist" / "index.html").write_text("<!doctype html>\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    networks = cloudflare_networks()
    published = {
        "result": {
            "ipv4_cidrs": [str(n) for n in networks if n.version == 4],
            "ipv6_cidrs": [str(n) for n in networks if n.version == 6],
        }
    }
    stubs = {
        bin_dir / "docker": DOCKER_STUB,
        bin_dir / "curl": f"#!/bin/sh\ncat <<'JSON'\n{json.dumps(published)}\nJSON\n",
        dogfood / "firewall.sh": "#!/bin/sh\nexit 0\n",
    }
    for path, text in stubs.items():
        path.write_text(text)
        path.chmod(0o755)
    env_file = tmp_path / "dogfood.env"
    env_file.write_text("PF_ENVIRONMENT=dogfood\n")
    origin = tmp_path / "origin"
    origin.mkdir()
    pairs = OriginPairs(ca_name, ca_key, origin)
    pairs.write("", *pairs.pair())
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "PF_DOGFOOD_ENV_FILE": str(env_file),
        "PF_DOGFOOD_RELEASES_DIR": str(tmp_path / "releases"),
        "PF_DOGFOOD_RELEASES_FILE": str(tmp_path / "releases.tsv"),
        "PF_DOGFOOD_ORIGIN_DIR": str(origin),
    }
    return pairs, environment


def run_deploy(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), COMMIT],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_deploy_installs_a_new_origin_pair_once_the_preflight_accepts_it(
    deploy_host: tuple[OriginPairs, dict[str, str]],
) -> None:
    pairs, environment = deploy_host
    certificate, key = pairs.pair()
    pairs.write(".new", certificate, key)

    result = run_deploy(environment)

    # The preflight passed and the deploy went on to the (stubbed) image build.
    assert "docker stub: compose" in result.stderr, result.stdout + result.stderr
    assert "building images" in result.stdout
    assert pairs.files() == {"cert.pem": certificate, "key.pem": key}
    assert (pairs.directory / "key.pem").stat().st_mode & 0o777 == 0o600
    assert key.decode() not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        pytest.param("mismatched-key", "is not the key of", id="mismatched-key"),
        pytest.param("missing-host", f"does not cover {AUTH_HOST}", id="missing-host"),
        pytest.param("readable-key", "must have mode 600", id="readable-key"),
        pytest.param("missing-key", "no origin certificate and key", id="missing-key"),
    ],
)
def test_deploy_rejecting_a_new_origin_pair_leaves_the_live_pair_untouched(
    deploy_host: tuple[OriginPairs, dict[str, str]], defect: str, message: str
) -> None:
    pairs, environment = deploy_host
    live = pairs.files()
    certificate, key = pairs.pair(
        hosts=(APP_HOST,) if defect == "missing-host" else (APP_HOST, AUTH_HOST)
    )
    if defect == "mismatched-key":
        key = pairs.pair()[1]
    pairs.write(".new", certificate, key, key_mode=0o644 if defect == "readable-key" else 0o600)
    if defect == "missing-key":
        (pairs.directory / "key.pem.new").unlink()

    result = run_deploy(environment)

    assert result.returncode == 1
    assert message in result.stdout, result.stdout + result.stderr
    assert "building images" not in result.stdout
    assert {name: pairs.files()[name] for name in live} == live


def test_deploy_without_a_new_origin_pair_checks_and_keeps_the_live_one(
    deploy_host: tuple[OriginPairs, dict[str, str]],
) -> None:
    pairs, environment = deploy_host
    live = pairs.files()

    result = run_deploy(environment)

    assert "building images" in result.stdout, result.stdout + result.stderr
    assert pairs.files() == live


def test_deploy_workflow_writes_only_a_new_origin_pair_for_deploy_sh_to_check() -> None:
    workflow = DEPLOY_WORKFLOW.read_text()

    written = re.findall(r"cat > (/etc/property-focus/origin/\S+?)['\s]", workflow)

    assert written == [
        "/etc/property-focus/origin/cert.pem.new",
        "/etc/property-focus/origin/key.pem.new",
    ]


def reverse_proxy_blocks(text: str) -> list[tuple[str, list[str]]]:
    """Each ``reverse_proxy`` directive of the Caddyfile ``text`` with the lines of its block
    (stripped, without comments; empty when it has no block), with snippet imports expanded."""
    blocks = caddy_blocks(text)
    snippets = {header[1:-1]: lines for header, lines in blocks.items() if header.startswith("(")}
    proxies: list[tuple[str, list[str]]] = []
    sites = [body for header, body in blocks.items() if header and not header.startswith("(")]
    lines = [line for body in sites for line in body]
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.startswith("reverse_proxy"):
            continue
        body: list[str] = []
        if line.endswith("{"):
            while lines[index] != "}":
                words = lines[index].split()
                body.extend(snippets[words[1]] if words[0] == "import" else [lines[index]])
                index += 1
        proxies.append((line.removesuffix("{").strip(), body))
    return proxies


def test_every_upstream_gets_only_the_resolved_client_ip_and_the_requested_host() -> None:
    proxies = reverse_proxy_blocks(CADDYFILE.read_text())

    assert len(proxies) >= 8
    hosts = []
    for directive, body in proxies:
        settings = [line.split() for line in body]
        headers = {words[1]: words[2] for words in settings if words[0] == "header_up"}
        assert headers.get("X-Forwarded-For") == "{client_ip}", directive
        # Only the app host's authorization-server metadata is requested as the auth host.
        hosts.append((headers.get("Host", "{host}"), headers.get("X-Forwarded-Host")))
    assert sorted(set(hosts)) == [
        ("{$PF_DOGFOOD_AUTH_HOST}", "{$PF_DOGFOOD_AUTH_HOST}"),
        ("{host}", "{host}"),
    ]
    assert hosts.count(("{$PF_DOGFOOD_AUTH_HOST}", "{$PF_DOGFOOD_AUTH_HOST}")) == 1
