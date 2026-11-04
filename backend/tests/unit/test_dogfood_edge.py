"""The dogfood Cloudflare edge (A5): Caddy serves only the Cloudflare Origin CA certificate,
requires Cloudflare's origin-pull client certificate (Authenticated Origin Pulls) and trusts
client-IP headers only from Cloudflare's ranges, the dogfood Compose file exposes only Caddy
over TCP, the backend services trust forwarded headers only from Caddy, deploy.sh checks the
edge before it changes anything and rolls a failed deploy back, the deploy workflow keeps
dependency installs away from its secrets, and the smoke check refuses responses that bypass
Cloudflare or are challenge pages.

The Caddyfile is read as text: global options, snippets and sites are top-level blocks, and
each line holds one directive. The Compose file is read through ``docker compose config``,
which needs only the Docker CLI.
"""

import importlib.util
import ipaddress
import json
import re
import shutil
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

ORIGIN_PULL_CA = DOGFOOD / "cloudflare-origin-pull-ca.pem"
# Every site imports the origin_tls snippet: the origin certificate, and Cloudflare's
# origin-pull client certificate (or deploy.sh's health-check one) required of every client.
ORIGIN_TLS_IMPORT = "import origin_tls"
ORIGIN_TLS = [
    "tls /etc/caddy/origin/cert.pem /etc/caddy/origin/key.pem {",
    "client_auth {",
    "mode require_and_verify",
    "trust_pool file /etc/caddy/cloudflare-origin-pull-ca.pem"
    " /etc/caddy/origin/health-check-cert.pem",
    "}",
    "}",
]
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
    """One message per site of the Caddyfile ``text`` that does not import ``origin_tls``,
    for an ``origin_tls`` snippet that does not serve the origin certificate and require a
    trusted client certificate, and per line anywhere that would make Caddy obtain a
    certificate."""
    blocks = caddy_blocks(text)
    problems = []
    for header, lines in blocks.items():
        for line in lines:
            if CERTIFICATE_AUTOMATION.match(line) or AUTOMATED_TLS.match(line):
                problems.append(f"{header or 'global options'}: certificate automation: {line}")
        is_site = header != "" and not header.startswith("(")
        if is_site and ORIGIN_TLS_IMPORT not in lines:
            problems.append(f"{header}: does not import origin_tls")
    if blocks.get("(origin_tls)") != ORIGIN_TLS:
        problems.append("origin_tls: does not serve the origin certificate requiring a client one")
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
        (lambda text: text.replace(ORIGIN_TLS_IMPORT, "", 1), "does not import origin_tls"),
        (lambda text: text.replace(ORIGIN_TLS_IMPORT, "tls internal", 1), "certificate automation"),
        (lambda text: text.replace(ORIGIN_TLS_IMPORT, "tls ops@example.test", 1), "automation"),
        (
            lambda text: text.replace("require_and_verify", "verify_if_given", 1),
            "origin_tls: does not serve the origin certificate requiring a client one",
        ),
        (
            lambda text: text.replace(
                "\t\tclient_auth {", "\t\tclient_auth {\n\t\t\tmode request", 1
            ),
            "origin_tls: does not serve",
        ),
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
            "extra.example.test: does not import origin_tls",
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


def test_deploy_passes_exactly_the_committed_ranges_to_caddy(tmp_path: Path) -> None:
    script = DEPLOY_SCRIPT.read_text()
    function = re.search(r"^cloudflare_ranges\(\) \{.*\}$", script, re.MULTILINE)
    assignment = re.search(r"^PF_CLOUDFLARE_IP_RANGES=.*$", script, re.MULTILINE)
    assert function is not None
    assert assignment is not None
    bin_dir = write_bsd_tools(tmp_path)

    result = subprocess.run(
        [
            "bash",
            "-c",
            f'{function.group()}\n{assignment.group()}\nprintf %s "$PF_CLOUDFLARE_IP_RANGES"',
        ],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "dogfood_dir": str(DOGFOOD)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split(" ") == [str(network) for network in cloudflare_networks()]


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


def test_caddy_mounts_the_committed_origin_pull_ca_read_only() -> None:
    caddy = dogfood_services()["caddy"]

    mounts = [
        (volume["source"], volume.get("read_only", False))
        for volume in caddy["volumes"]
        if volume["target"] == "/etc/caddy/cloudflare-origin-pull-ca.pem"
    ]
    assert mounts == [(str(ORIGIN_PULL_CA), True)]


def test_committed_origin_pull_ca_is_the_certificate_its_header_names() -> None:
    text = ORIGIN_PULL_CA.read_text()
    recorded = re.search(r"^# SHA-256 fingerprint: ([0-9A-F:]{95})$", text, re.MULTILINE)
    certificate = x509.load_pem_x509_certificate(text.encode())

    assert recorded is not None
    assert certificate.fingerprint(hashes.SHA256()).hex(":").upper() == recorded.group(1)
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert certificate.subject.rfc4514_string().startswith("CN=origin-pull.cloudflare.net,")
    assert certificate.not_valid_after_utc > datetime.now(UTC) + timedelta(days=30)


def test_backend_services_trust_forwarded_headers_only_from_caddys_fixed_address() -> None:
    services = dogfood_services()
    proxy = services["caddy"]["networks"]
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--file",
            str(DOGFOOD / "compose.yaml"),
            "config",
            "--format",
            "json",
            "--no-interpolate",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    subnet = json.loads(result.stdout)["networks"]["proxy"]["ipam"]["config"][0]["subnet"]

    assert list(proxy) == ["proxy"]
    caddy_address = proxy["proxy"]["ipv4_address"]
    assert ipaddress.ip_address(caddy_address) in ipaddress.ip_network(subnet)
    for name in ("api", "mcp"):
        assert services[name]["environment"]["FORWARDED_ALLOW_IPS"] == caddy_address, name
    assert services["keycloak"]["environment"]["KC_PROXY_TRUSTED_ADDRESSES"] == caddy_address
    for name in ("api", "mcp", "keycloak"):
        assert "proxy" in services[name]["networks"], name


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


def test_smoke_names_authenticated_origin_pulls_when_cloudflare_cannot_reach_the_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handshake_failed(*_: object, **__: object) -> Any:
        return smoke.Response(525, "https://app.test/health/ready", headers(cf_ray="8f00"), "")

    monkeypatch.setattr(smoke.Browser, "request", handshake_failed)

    with pytest.raises(smoke.SmokeCheckError, match="Authenticated Origin Pulls"):
        smoke.check_ready("https://app.test")


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


# Stand-ins that behave like the macOS (BSD and LibreSSL) tools where those differ from GNU
# coreutils and OpenSSL, so the tests catch a GNU-only option on any machine: paste reads
# standard input only with a `-` operand, stat has no -c, and openssl x509 has no -checkhost.
# Every other use runs the real tool.
BSD_TOOLS = {
    "paste": """#!/bin/sh
operands() {
	while getopts sd: option; do
		[ "$option" = "?" ] && return 1
	done
	shift $((OPTIND - 1))
	echo $#
}
if [ "$(operands "$@")" = 0 ]; then
	echo "usage: paste [-s] [-d delimiters] file ..." >&2
	exit 1
fi
exec {real} "$@"
""",
    "stat": """#!/bin/sh
case " $* " in
*" -c "*) echo "stat: illegal option -- c" >&2; exit 1 ;;
esac
exec {real} "$@"
""",
    "openssl": """#!/bin/sh
case " $* " in
*" -checkhost "*) echo "x509: unknown option -checkhost" >&2; exit 1 ;;
esac
exec {real} "$@"
""",
}


def write_bsd_tools(directory: Path) -> Path:
    """Write the ``BSD_TOOLS`` stand-ins to ``directory/bsd-bin`` and return that directory,
    to put first on a ``PATH`` that otherwise holds /usr/bin and /bin."""
    bin_dir = directory / "bsd-bin"
    bin_dir.mkdir()
    for name, template in BSD_TOOLS.items():
        real = shutil.which(name, path="/usr/bin:/bin")
        assert real is not None, f"{name} is not in /usr/bin or /bin"
        path = bin_dir / name
        path.write_text(template.replace("{real}", real))
        path.chmod(0o755)
    return bin_dir


APP_HOST = "app.example.test"
AUTH_HOST = "auth.example.test"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
# Stands in for Docker: answers `compose version` with the minimum version and
# `compose config --environment` with the hosts and the smoke user, and fails every other
# command, so a deploy stops at its first image build.
DOCKER_STUB = f"""#!/bin/sh
case "$*" in
"compose version") echo "Docker Compose version v2.27.2" ;;
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
    days: int = 90,
) -> x509.Certificate:
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(issuer if ca else x509.Name([]))
        .issuer_name(issuer)
        .public_key(subject_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
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


def published_ranges() -> str:
    """cloudflare-ips.txt as Cloudflare's API publishes it, in JSON."""
    networks = cloudflare_networks()
    return json.dumps(
        {
            "result": {
                "ipv4_cidrs": [str(n) for n in networks if n.version == 4],
                "ipv6_cidrs": [str(n) for n in networks if n.version == 6],
            }
        }
    )


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
    (dogfood / "cloudflare-origin-pull-ca.pem").write_bytes(ORIGIN_PULL_CA.read_bytes())
    (release / "frontend" / "dist").mkdir(parents=True)
    (release / "frontend" / "dist" / "index.html").write_text("<!doctype html>\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stubs = {
        bin_dir / "docker": DOCKER_STUB,
        bin_dir / "curl": f"#!/bin/sh\ncat <<'JSON'\n{published_ranges()}\nJSON\n",
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
        "PATH": f"{bin_dir}:{write_bsd_tools(tmp_path)}:/usr/bin:/bin",
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


def test_deploy_refuses_an_old_compose_before_it_changes_anything(
    deploy_host: tuple[OriginPairs, dict[str, str]],
) -> None:
    pairs, environment = deploy_host
    pairs.write(".new", *pairs.pair())
    files = pairs.files()
    docker = Path(environment["PATH"].split(":")[0]) / "docker"
    docker.write_text(DOCKER_STUB.replace("v2.27.2", "v2.7.0"))

    result = run_deploy(environment)

    assert result.returncode == 1
    assert "Docker Compose >= 2.27.2 is required, found 2.7.0" in result.stdout, result.stderr
    assert pairs.files() == files


def test_deploy_workflow_writes_only_a_new_origin_pair_for_deploy_sh_to_check() -> None:
    workflow = DEPLOY_WORKFLOW.read_text()

    written = re.findall(r"cat > (/etc/property-focus/origin/\S+?)['\s]", workflow)

    assert written == [
        "/etc/property-focus/origin/cert.pem.new",
        "/etc/property-focus/origin/key.pem.new",
    ]


PREVIOUS_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
# Stands in for Docker through a whole deploy: answers what deploy.sh reads (the Compose
# version, the environment, the resolved Keycloak environment and image digests), logs every
# command to $DOCKER_LOG, and fails any command whose arguments contain $DOCKER_FAIL.
FULL_DOCKER_STUB = f"""#!/bin/sh
printf '%s\\n' "$*" >>"$DOCKER_LOG"
if [ -n "$DOCKER_FAIL" ]; then
	case "$*" in *"$DOCKER_FAIL"*) echo "docker stub: failing $*" >&2; exit 1 ;; esac
fi
case "$*" in
"compose version") echo "Docker Compose version v2.27.2" ;;
*"config --environment"*)
	printf 'PF_DOGFOOD_APP_HOST={APP_HOST}\\nPF_DOGFOOD_AUTH_HOST={AUTH_HOST}\\n'
	printf 'PF_SMOKE_USER_EMAIL=smoke@example.test\\n' ;;
*"config --format json"*) echo '{{"services": {{"keycloak": {{"environment": {{}}}}}}}}' ;;
"image inspect --format {{{{.Id}}}} property-focus-"*) echo "sha256:${{5%%:*}}" ;;
esac
"""
# Stands in for curl through a whole deploy: Cloudflare's published ranges; for a request to
# Caddy, the TLS failure Caddy gives a client without a client certificate (unless
# $CURL_ACCEPTS_ANY_CLIENT is set), else 404 for the paths deploy.sh expects to be hidden and
# 200 otherwise, with metadata naming $CURL_ISSUER. Logs every request to $CURL_LOG.
FULL_CURL_STUB = f"""#!/bin/sh
printf '%s\\n' "$*" >>"$CURL_LOG"
case "$*" in
*api.cloudflare.com*) cat <<'JSON'
{{published}}
JSON
	exit 0 ;;
esac
case "$*" in
*" --cert "*) ;;
*https://*)
	if [ -z "$CURL_ACCEPTS_ANY_CLIENT" ]; then
		case "$*" in *--write-out*) printf 000 ;; esac
		exit 56
	fi ;;
esac
case "$*" in
*--write-out*)
	case "$*" in
	*"{APP_HOST}/.well-known/openid-configuration"|*/admin/|*/realms/master/) printf 404 ;;
	*) printf 200 ;;
	esac ;;
*) printf '{{"issuer": "%s", "authorization_servers": ["%s"]}}' "$CURL_ISSUER" "$CURL_ISSUER" ;;
esac
"""
ISSUER = f"https://{AUTH_HOST}/realms/property-focus"


@dataclass
class DeployRun:
    """A host prepared for a whole deploy.sh run, with what the run did to it."""

    tmp_path: Path
    environment: dict[str, str]

    @property
    def current(self) -> Path:
        return self.tmp_path / "current"

    def docker_commands(self) -> list[str]:
        log = Path(self.environment["DOCKER_LOG"])
        return log.read_text().splitlines() if log.exists() else []

    def curl_requests(self) -> list[str]:
        log = Path(self.environment["CURL_LOG"])
        return log.read_text().splitlines() if log.exists() else []

    def rollbacks(self) -> list[str]:
        log = self.tmp_path / "rollback.log"
        return log.read_text().splitlines() if log.exists() else []

    def add_previous_release(self) -> None:
        """A release that `current` names, whose deploy.sh records how it was run."""
        dogfood = self.tmp_path / "releases" / PREVIOUS_COMMIT / "infrastructure" / "dogfood"
        dogfood.mkdir(parents=True)
        script = dogfood / "deploy.sh"
        script.write_text(f'#!/bin/sh\necho "$*" >>"{self.tmp_path}/rollback.log"\n')
        script.chmod(0o755)
        self.current.symlink_to(dogfood.parents[1])


@pytest.fixture
def full_deploy(tmp_path: Path, deploy_host: tuple[OriginPairs, dict[str, str]]) -> DeployRun:
    """``deploy_host`` with Docker, curl and the realm script stubbed for a whole deploy."""
    _, environment = deploy_host
    bin_dir = Path(environment["PATH"].split(":")[0])
    (bin_dir / "docker").write_text(FULL_DOCKER_STUB)
    (bin_dir / "curl").write_text(FULL_CURL_STUB.replace("{published}", published_ranges()))
    realm_script = tmp_path / "releases" / COMMIT / "backend" / "scripts" / "keycloak_realm.py"
    realm_script.parent.mkdir(parents=True)
    realm_script.write_text("")
    environment = environment | {
        "DOCKER_LOG": str(tmp_path / "docker.log"),
        "CURL_LOG": str(tmp_path / "curl.log"),
        "CURL_ISSUER": ISSUER,
    }
    return DeployRun(tmp_path, environment)


def test_deploy_failing_its_health_checks_rolls_back_to_the_release_current_names(
    full_deploy: DeployRun,
) -> None:
    full_deploy.add_previous_release()
    full_deploy.environment["CURL_ISSUER"] = "https://elsewhere.test/realms/property-focus"

    result = run_deploy(full_deploy.environment)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "names issuer 'https://elsewhere.test" in result.stdout
    assert full_deploy.rollbacks() == [f"--rollback {PREVIOUS_COMMIT}"]
    assert full_deploy.current.resolve().name == PREVIOUS_COMMIT
    assert f"rolling back to {PREVIOUS_COMMIT}" in result.stdout


def test_deploy_whose_services_fail_to_start_rolls_back_to_the_release_current_names(
    full_deploy: DeployRun,
) -> None:
    full_deploy.add_previous_release()
    full_deploy.environment["DOCKER_FAIL"] = "--remove-orphans"

    result = run_deploy(full_deploy.environment)

    assert result.returncode == 1, result.stdout + result.stderr
    assert full_deploy.rollbacks() == [f"--rollback {PREVIOUS_COMMIT}"]


def test_deploy_failing_before_it_starts_the_new_services_does_not_roll_back(
    full_deploy: DeployRun,
) -> None:
    full_deploy.add_previous_release()
    full_deploy.environment["DOCKER_FAIL"] = "run --rm --no-deps migrate"

    result = run_deploy(full_deploy.environment)

    assert result.returncode == 1, result.stdout + result.stderr
    assert full_deploy.rollbacks() == []
    assert not any(" stop " in command for command in full_deploy.docker_commands())


def test_first_deploy_failing_its_health_checks_stops_the_new_application_and_caddy(
    full_deploy: DeployRun,
) -> None:
    full_deploy.environment["CURL_ISSUER"] = "https://elsewhere.test/realms/property-focus"

    result = run_deploy(full_deploy.environment)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "no earlier release to roll back to" in result.stdout
    assert full_deploy.docker_commands()[-1].endswith("stop api mcp scheduler caddy")
    assert not full_deploy.current.exists()


@pytest.mark.skipif(
    subprocess.run(["mv", "--version"], capture_output=True, check=False).returncode != 0,
    reason="switch_current needs GNU mv -T, which the dogfood host has",
)
def test_deploy_passing_its_health_checks_switches_current_and_does_not_roll_back(
    full_deploy: DeployRun,
) -> None:
    full_deploy.add_previous_release()

    result = run_deploy(full_deploy.environment)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"deployed {COMMIT}" in result.stdout
    assert full_deploy.current.resolve().name == COMMIT
    assert full_deploy.rollbacks() == []


def test_deploy_installs_a_new_environment_file_once_the_preflight_accepts_it(
    deploy_host: tuple[OriginPairs, dict[str, str]],
) -> None:
    _, environment = deploy_host
    env_file = Path(environment["PF_DOGFOOD_ENV_FILE"])
    Path(f"{env_file}.new").write_text("PF_ENVIRONMENT=dogfood\nPF_SMOKE_USER_PASSWORD='p$ss'\n")

    result = run_deploy(environment)

    assert "building images" in result.stdout, result.stdout + result.stderr
    assert env_file.read_text() == "PF_ENVIRONMENT=dogfood\nPF_SMOKE_USER_PASSWORD='p$ss'\n"
    assert not Path(f"{env_file}.new").exists()


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        pytest.param("bad-line", ".new line 2 (PF_SMOKE_USER_PASSWORD): single-quote", id="format"),
        pytest.param("bad-origin-pair", "is not the key of", id="edge-preflight"),
    ],
)
def test_deploy_rejecting_a_new_environment_file_leaves_the_live_one_untouched(
    deploy_host: tuple[OriginPairs, dict[str, str]], defect: str, message: str
) -> None:
    pairs, environment = deploy_host
    env_file = Path(environment["PF_DOGFOOD_ENV_FILE"])
    live = env_file.read_text()
    password = "p$ss" if defect == "bad-line" else "'p$ss'"
    Path(f"{env_file}.new").write_text(
        f"PF_ENVIRONMENT=dogfood\nPF_SMOKE_USER_PASSWORD={password}\n"
    )
    if defect == "bad-origin-pair":
        pairs.write(".new", pairs.pair()[0], pairs.pair()[1])

    result = run_deploy(environment)

    assert result.returncode == 1
    assert message in result.stdout, result.stdout + result.stderr
    assert "building images" not in result.stdout
    assert env_file.read_text() == live
    assert Path(f"{env_file}.new").exists()
    assert "p$ss" not in result.stdout + result.stderr


def test_deploy_preflight_reads_the_new_environment_file_through_compose(
    full_deploy: DeployRun,
) -> None:
    env_file = Path(full_deploy.environment["PF_DOGFOOD_ENV_FILE"])
    Path(f"{env_file}.new").write_text("PF_ENVIRONMENT=dogfood\n")
    full_deploy.environment["DOCKER_FAIL"] = "build --pull"

    run_deploy(full_deploy.environment)

    configs = [c for c in full_deploy.docker_commands() if "config --environment" in c]
    assert configs
    assert all(f"--env-file {env_file}.new " in c for c in configs), configs


def test_deploy_workflow_writes_only_a_new_environment_file_for_deploy_sh_to_check() -> None:
    workflow = DEPLOY_WORKFLOW.read_text()

    written = re.findall(r"cat > (/etc/property-focus/\S+?\.env\S*?)['\s]", workflow)

    assert written == ["/etc/property-focus/dogfood.env.new"]


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        pytest.param(
            "expiring", "cloudflare-origin-pull-ca.pem expires within 30 days", id="expiring"
        ),
        pytest.param("missing", "cloudflare-origin-pull-ca.pem is not a certificate", id="missing"),
    ],
)
def test_deploy_refuses_an_unusable_origin_pull_ca_before_it_changes_anything(
    deploy_host: tuple[OriginPairs, dict[str, str]], defect: str, message: str
) -> None:
    pairs, environment = deploy_host
    pairs.write(".new", *pairs.pair())
    files = pairs.files()
    release_ca = (
        Path(environment["PF_DOGFOOD_RELEASES_DIR"]) / COMMIT / "infrastructure" / "dogfood"
    ) / "cloudflare-origin-pull-ca.pem"
    if defect == "missing":
        release_ca.unlink()
    else:
        key = _private_key()
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "origin-pull.test")])
        expiring = _certificate(key, name, key, ca=True, days=10)
        release_ca.write_bytes(expiring.public_bytes(serialization.Encoding.PEM))

    result = run_deploy(environment)

    assert result.returncode == 1
    assert message in result.stdout, result.stdout + result.stderr
    assert "building images" not in result.stdout
    assert pairs.files() == files


def caddy_requests(run: DeployRun) -> list[str]:
    """The health checks' requests to Caddy over loopback, as curl was called."""
    return [request for request in run.curl_requests() if "--resolve" in request]


def test_deploy_fails_and_rolls_back_when_caddy_accepts_a_client_without_a_certificate(
    full_deploy: DeployRun,
) -> None:
    full_deploy.add_previous_release()
    full_deploy.environment["CURL_ACCEPTS_ANY_CLIENT"] = "1"

    result = run_deploy(full_deploy.environment)

    assert result.returncode == 1
    assert "accepted a client without a trusted client certificate" in result.stdout, (
        result.stdout + result.stderr
    )
    assert full_deploy.rollbacks() == [f"--rollback {PREVIOUS_COMMIT}"]


def test_health_checks_present_the_host_health_check_certificate_and_expect_its_absence_refused(
    full_deploy: DeployRun,
) -> None:
    origin = Path(full_deploy.environment["PF_DOGFOOD_ORIGIN_DIR"])

    result = run_deploy(full_deploy.environment)

    requests = caddy_requests(full_deploy)
    client = f"--cert {origin}/health-check-cert.pem --key {origin}/health-check-key.pem"
    without = [request for request in requests if client not in request]
    assert len(requests) > len(without) >= 2, result.stdout + result.stderr
    assert {request.split("https://")[1].split("/")[0] for request in without} == {
        APP_HOST,
        AUTH_HOST,
    }
    assert (origin / "health-check-key.pem").stat().st_mode & 0o777 == 0o600
    certificate = x509.load_pem_x509_certificate((origin / "health-check-cert.pem").read_bytes())
    assert certificate.not_valid_after_utc > datetime.now(UTC) + timedelta(days=365)


def deploy_workflow_jobs() -> dict[str, list[str]]:
    """The lines of each job of the deploy workflow, by job name."""
    lines = DEPLOY_WORKFLOW.read_text().splitlines()
    jobs: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in lines[lines.index("jobs:") + 1 :]:
        header = re.match(r"^  ([a-z][a-z0-9-]*):\s*$", line)
        if header:
            current = jobs.setdefault(header.group(1), [])
        elif current is not None:
            current.append(line)
    return jobs


def job_setting(lines: list[str], name: str) -> str | None:
    """The value of a job-level ``name:`` setting, or None."""
    for line in lines:
        if line.startswith(f"    {name}:"):
            return line.split(":", 1)[1].strip()
    return None


# What installs packages from a registry: the setup actions, package managers and checkout of
# code whose lockfiles they would read.
DEPENDENCY_INSTALLS = re.compile(
    r"setup-node|setup-uv|setup-python|corepack|\bpnpm\b|\bnpm\b|\buv\b|\bpip\b|make\s"
)


def test_deploy_workflow_builds_deploys_and_smoke_checks_in_separate_jobs() -> None:
    jobs = deploy_workflow_jobs()

    assert list(jobs) == ["build", "deploy", "smoke"]
    assert job_setting(jobs["deploy"], "needs") == "build"
    assert job_setting(jobs["smoke"], "needs") == "deploy"


def test_deploy_workflow_deploys_only_main() -> None:
    deploy = deploy_workflow_jobs()["deploy"]

    assert job_setting(deploy, "if") == "github.ref == 'refs/heads/main'"
    assert job_setting(deploy, "environment") == "dogfood"


def test_deploy_workflow_build_job_holds_no_secrets() -> None:
    build = "\n".join(deploy_workflow_jobs()["build"])

    assert "environment:" not in build
    assert "secrets." not in build
    assert "actions/upload-artifact@" in build


def test_deploy_job_installs_nothing_from_a_package_registry() -> None:
    deploy = deploy_workflow_jobs()["deploy"]

    installs = [line for line in deploy if DEPENDENCY_INSTALLS.search(line.split("#")[0])]
    assert installs == []
    assert any("actions/download-artifact@" in line for line in deploy)
    assert not any("actions/checkout@" in line for line in deploy)


def test_smoke_job_gets_only_the_smoke_password_and_no_deploy_secrets() -> None:
    smoke_job = "\n".join(deploy_workflow_jobs()["smoke"])

    assert re.findall(r"secrets\.([A-Z_]+)", smoke_job) == ["PF_SMOKE_USER_PASSWORD"]
    assert "DOGFOOD_SSH" not in smoke_job
    assert "make smoke-dogfood" in smoke_job
    assert job_setting(deploy_workflow_jobs()["smoke"], "environment") != "dogfood"


def test_deploy_workflow_pins_every_action_by_commit_with_its_release() -> None:
    uses = re.findall(r"uses: (\S+)(.*)$", DEPLOY_WORKFLOW.read_text(), re.MULTILINE)

    assert uses
    for action, comment in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action
        assert re.fullmatch(r" # v\d+\.\d+\.\d+", comment), action


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
