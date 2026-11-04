# Dogfood Operations

Runbook for the DigitalOcean dogfood environment: one host running `infrastructure/dogfood/compose.yaml`. Every deploy, rollback and Keycloak change below touches the live environment and needs explicit approval from the user before it runs.

## The host

The dogfood host runs Docker Engine with the Compose plugin 2.27.2 or later, the first with `docker compose config --environment` (`deploy.sh` checks the version first and stops with "Docker Compose >= 2.27.2 is required"), `python3`, `curl`, `openssl`, `iptables` and `rsync`. It allows inbound traffic only on port 22 and, from Cloudflare's published ranges only, on ports 80 and 443 (see [Cloudflare edge](#cloudflare-edge)). Both public hostnames are proxied by Cloudflare, and Caddy terminates Cloudflare's TLS connections with a Cloudflare Origin CA certificate:

- `PF_DOGFOOD_APP_HOST` (`propertyfocus.treb.tech`) serves the web app, the REST API under `/v1`, `/health/*`, the MCP adapter at `/mcp` and its protected-resource metadata. Every other `/.well-known/*` path and `/authorize`, `/register` and `/token` are a `404` there; they belong to the auth host.
- `PF_DOGFOOD_AUTH_HOST` (`auth-propertyfocus.treb.tech`) serves only the `property-focus` realm's sign-in and protocol paths, `/resources/*` and the realm's authorization-server metadata. Every other path, including `/admin/*` and `/realms/master/*`, is a `404`.

Keycloak's HTTP port (8080) and management port (9000) and the OpenObserve UI (5080) bind to the host's loopback interface only. Postgres, the API, the MCP adapter and the collector publish no ports at all. Caddy reaches the API, the MCP adapter and Keycloak over the Compose network `proxy` (`10.250.10.0/28`), the only one it joins, from the fixed address `10.250.10.2`; the API and MCP adapter trust `X-Forwarded-Proto` and `X-Forwarded-For` only from that address (uvicorn's `FORWARDED_ALLOW_IPS`). If that subnet collides with a network on the host, change the subnet and the `x-caddy-address` value together in `infrastructure/dogfood/compose.yaml`. Every container runs with `no-new-privileges` and without Linux capabilities, except those Postgres needs to prepare its data directory, and Caddy's `NET_BIND_SERVICE` (ports 80 and 443) and `DAC_READ_SEARCH` (to read the origin key, which belongs to the deploy user with mode 600); Keycloak (1.5 GB), Postgres (1 GB) and OpenObserve (1 GB) have memory limits, so the host needs at least 4 GB.

One-time setup by an operator:

1. Create a `deploy` user in the `docker` group whose `authorized_keys` holds the public half of the `DOGFOOD_SSH_PRIVATE_KEY` deployment key.
2. Create `/srv/property-focus/releases`, `/etc/property-focus` (mode 700) and `/var/lib/property-focus`, all owned by `deploy`.
3. Put Cloudflare in front of both hostnames as [Cloudflare edge](#cloudflare-edge) describes: proxied DNS records, SSL mode Full (strict), the Origin CA certificate, Authenticated Origin Pulls (on before the first deploy), the host firewall and the dashboard's security settings. Caddy obtains no certificates of its own, so the origin certificate must be stored before the first deploy.
4. Set up the SMTP relay (see [Mail relay](#mail-relay)): add and verify the sending domain in Mailgun, and create its SMTP credentials.
5. Configure GitHub as the header of `.github/workflows/deploy-dogfood.yml` describes:
    - **Repository variables** (Settings > Secrets and variables > Actions > Variables), read by all three jobs, the build job among them, which runs outside any environment: `PF_DOGFOOD_APP_HOST=propertyfocus.treb.tech`, `PF_DOGFOOD_AUTH_HOST=auth-propertyfocus.treb.tech`, `PF_SMOKE_USER_EMAIL` and `PF_DOGFOOD_ORIGIN_ADDRESS` (the host's public IP address). Delete any copies of these from the `dogfood` environment, where they would override the repository values for the deploy job only.
    - **Environment `dogfood`** (Settings > Environments): required reviewers; under **Deployment branches and tags**, **Selected branches and tags** with the single rule `main`, so no other ref can reach its secrets even if the workflow's `if: github.ref == 'refs/heads/main'` were removed; the variables `DOGFOOD_SSH_TARGET` and `DOGFOOD_SSH_KNOWN_HOSTS`; the secrets `DOGFOOD_SSH_PRIVATE_KEY`, `DOGFOOD_ENV_FILE`, `PF_SMOKE_USER_PASSWORD`, `DOGFOOD_ORIGIN_CERT` and `DOGFOOD_ORIGIN_KEY`.
    - **Environment `dogfood-smoke`**: no required reviewers (it runs right after the approved deploy), the same `main`-only deployment branch rule, and only the secret `PF_SMOKE_USER_PASSWORD`, with the same value as in `dogfood`; change both when you change the smoke user's password.

### Mail relay

Keycloak sends verification and password-reset email through Mailgun's SMTP relay at `smtp.mailgun.org`, port `2525`, with STARTTLS. The user owns the Mailgun account and the sending domain:

1. In Mailgun, add a sending domain under `treb.tech`, for example `mg.treb.tech`.
2. Add the DNS records Mailgun lists for it in the `treb.tech` zone: the SPF `TXT` record and the DKIM `TXT` record, plus the `MX` and tracking `CNAME` records only if you want Mailgun to receive mail or track links. Wait until Mailgun shows the domain as verified; Mailgun does not deliver mail from an unverified domain.
3. Create an SMTP user for that domain in Mailgun and put its login and password in `DOGFOOD_ENV_FILE` as `PF_KEYCLOAK_SMTP_USER` and `PF_KEYCLOAK_SMTP_PASSWORD`.

The relay credentials live only in the `dogfood` GitHub environment's `DOGFOOD_ENV_FILE` secret and in the host's environment file the workflow writes from it, never in Git.

## Environment file

`/etc/property-focus/dogfood.env` holds the deployment-injected settings and secrets (D49), one `KEY=value` per line. On every deploy the workflow writes a new file beside it, `/etc/property-focus/dogfood.env.new` (mode 600), from the `DOGFOOD_ENV_FILE` secret plus the hostnames and smoke-user values; `deploy.sh` checks the new file in its preflight and moves it over the live one only once the whole preflight passes (see [Deploy](#deploy)). Nothing in Git, an image, the realm file or a log holds these values.

Docker Compose is the only program that parses the file: `deploy.sh` and the operator commands below pass it with `--env-file`, and `deploy.sh` reads the values it needs itself through `docker compose config --environment`. Write each value bare, or in single quotes when it holds `$`, a quote, a backslash, `#` or a space: `PF_KEYCLOAK_SMTP_PASSWORD='pa$$word'` is read as `pa$$word`, but unquoted Compose would treat `$$` and `$word` as substitutions. A single-quoted value cannot hold a single quote. `deploy.sh` refuses a file that breaks these rules, naming the line and key but never the value.

| Variable | Meaning |
|---|---|
| `PF_DOGFOOD_APP_HOST`, `PF_DOGFOOD_AUTH_HOST` | The two public hostnames: `propertyfocus.treb.tech` and `auth-propertyfocus.treb.tech` (written by the workflow from the environment's variables) |
| `PF_POSTGRES_PASSWORD` | Postgres superuser, used only by the `db-init` step |
| `PF_MIGRATION_DATABASE_PASSWORD` | Migration owner `pf_migrator` |
| `PF_DATABASE_PASSWORD` | Runtime role `pf_runtime` (the `database_password` secret) |
| `PF_KEYCLOAK_DB_PASSWORD` | Keycloak's own `keycloak` role and database |
| `PF_KEYCLOAK_ADMIN_USERNAME`, `PF_KEYCLOAK_ADMIN_PASSWORD` | The master-realm admin that `deploy.sh` and `keycloak_realm.py` act as |
| `PF_KEYCLOAK_SMTP_HOST`, `PF_KEYCLOAK_SMTP_PORT` | Outbound relay for verification and password-reset emails: `smtp.mailgun.org` and `2525` |
| `PF_KEYCLOAK_SMTP_FROM` | Sender address on the verified Mailgun domain, for example `no-reply@mg.treb.tech` |
| `PF_KEYCLOAK_SMTP_USER`, `PF_KEYCLOAK_SMTP_PASSWORD` | The Mailgun SMTP user's login and password for that domain |
| `PF_KEYCLOAK_SMTP_AUTH`, `PF_KEYCLOAK_SMTP_STARTTLS`, `PF_KEYCLOAK_SMTP_SSL` | Optional; default `true`, `true`, `false`, which is what Mailgun on port 2525 needs (STARTTLS, not implicit TLS) |
| `PF_KEYCLOAK_MAX_CLIENTS` | Optional cap on clients in the realm; default 100 |
| `PF_OBSERVABILITY_ADMIN_EMAIL`, `PF_OBSERVABILITY_ADMIN_PASSWORD` | OpenObserve root user, whose password needs 8 to 128 characters with a lower-case and an upper-case letter, a digit and a symbol; the collector exports with it |
| `PF_SMOKE_USER_EMAIL`, `PF_SMOKE_USER_PASSWORD` | Synthetic smoke user kept in the realm; the password must meet the realm's password policy (see [Password policy](#password-policy)) |

Operator commands below use this shell function on the host, which runs Compose with the current release's Compose file, the environment file, the current release's image tag and its Cloudflare ranges (the proxies Caddy trusts, which `deploy.sh` passes the same way):

```sh
dogfood_compose() {
  local release=/srv/property-focus/current/infrastructure/dogfood
  PF_IMAGE_TAG=$(basename "$(readlink /srv/property-focus/current)") \
  PF_CLOUDFLARE_IP_RANGES=$(awk '$1 !~ /^#/ && NF { print $1 }' "$release/cloudflare-ips.txt" | paste -sd ' ' -) \
    docker compose --file "$release/compose.yaml" --env-file /etc/property-focus/dogfood.env "$@"
}
```

## Deploy

Run the `deploy-dogfood` workflow from GitHub Actions on `main`; it deploys nothing from any other ref. It runs three jobs, so that no dependency install shares a job with the SSH key or the environment file's secrets:

1. `build`, with no environment and no secrets, installs the web app's dependencies, builds it with the dogfood issuer and uploads the commit's tree with `frontend/dist` as the `release` artifact.
2. `deploy`, once the `dogfood` environment's reviewers approve it, installs nothing: it downloads the artifact, copies it to a new release directory `/srv/property-focus/releases/<commit>`, writes the new environment file (`/etc/property-focus/dogfood.env.new`) and the new origin certificate and key beside the live pair (`/etc/property-focus/origin/cert.pem.new` and `key.pem.new`, the key with mode 600), runs that release's `infrastructure/dogfood/deploy.sh <commit>` on the host and removes its SSH key.
3. `smoke`, in the `dogfood-smoke` environment, which holds only the smoke user's password, installs the backend and runs `make smoke-dogfood` from the runner, outside the host and through Cloudflare.

The running release's files, web build included, stay untouched until step 6 below.

`deploy.sh` first checks the environment file's format (the new file when the workflow wrote one, otherwise the live one) and reads it through Compose, and then, before it changes anything, the Cloudflare edge. It fails when:

- the release's `cloudflare-ips.txt` differs from Cloudflare's published lists (`https://api.cloudflare.com/client/v4/ips`); it prints the difference;
- the live firewall is not exactly what `firewall.sh` applies, so ports 80 or 443 could be reached from outside Cloudflare (`firewall.sh --check`, which reads the rules through the deploy user's `sudo` rule);
- the release's `cloudflare-origin-pull-ca.pem` (see [Authenticated Origin Pulls](#authenticated-origin-pulls)) is not a certificate or expires within 30 days;
- the origin certificate or key is missing, the key's mode is not 600, the certificate is not issued by the Cloudflare Origin CA, does not cover both hostnames, does not match the key, or expires within 30 days. It checks the new pair the workflow wrote (`cert.pem.new`, `key.pem.new`), or the live pair when there is none.

Only once all of these pass does it move the new environment file over `/etc/property-focus/dogfood.env` and the new pair over the live `cert.pem` and `key.pem`, each with an atomic rename. A deploy the preflight rejects leaves the live environment file and pair as they were, so the running services, Caddy's next restart and any rollback still use what was live; the rejected files stay beside them until the next deploy writes new ones.

Then it stops at the first failing step:

1. Builds the backend and Keycloak images once, tags them `property-focus-<service>:<commit>`, and records each image's digest in `/var/lib/property-focus/releases.tsv`.
2. Pulls the third-party images, each pinned by digest.
3. Starts Postgres and runs `db-init`, which creates missing roles and databases and sets their passwords from the environment file.
4. Runs `migrate` as one controlled task.
5. Starts Keycloak, which imports the realm on first start, then runs `keycloak_realm.py apply` and `check` over the loopback port, and `ensure-user` for the smoke user.
6. Checks that every tag still carries its recorded digest, creates the health-check client certificate when it is missing or expires within 30 days (`/etc/property-focus/origin/health-check-cert.pem` and `health-check-key.pem`, the key with mode 600), starts the API, MCP adapter, scheduler, Caddy and the observability stack from the new release, recreates Caddy and the collector so they read this release's Caddyfile, collector configuration, web build and the origin certificate, and waits for their health checks.
7. Requests, on the host's loopback address, verifying the origin certificate against `cloudflare-origin-ca.pem` and presenting the health-check client certificate, readiness; checks that both hostnames refuse a client without a certificate during the TLS handshake; requests the protected-resource metadata, the web app and both realm discovery forms through Caddy, checks that the app host's `/.well-known/oauth-authorization-server` (for 2025-03-26 MCP clients) names the realm on the auth host as its issuer, confirms `/.well-known/openid-configuration` on the app host and `/admin/` and `/realms/master/` on the auth host answer `404`, checks OpenObserve on loopback, and only then points the `/srv/property-focus/current` symlink at the new release.

If anything fails from step 6 on, once the new release's services may be running, `deploy.sh` rolls back by itself before it exits with the failure: it runs `deploy.sh --rollback <commit>` of the release `/srv/property-focus/current` names (see [Rollback](#rollback)), so that release's application, Caddyfile, collector configuration and web build serve again and `current` keeps pointing at it. The migration from step 4 stays. On a first deploy, or a redeploy of the commit `current` already names (whose images the build retagged), there is no earlier release to return to: it stops `api`, `mcp`, `scheduler` and `caddy` instead, so a release that failed its checks never keeps serving. When the automatic rollback itself fails, the log says so; roll back by hand. A failure before step 6 changes no running service, so nothing is rolled back.

Migrations must stay expand/contract: a release may add columns and tables, and removes what the previous release still reads only in a later release. That keeps every rollback below safe without reversing a migration.

## Rollback

Roll back to an earlier release, never the schema:

```sh
ssh deploy@<host> /srv/property-focus/current/infrastructure/dogfood/deploy.sh --rollback <commit>
```

`--rollback` refuses a commit whose release directory is gone, or whose `api`, `mcp` or `scheduler` image is gone or no longer matches the digest recorded at its deploy. It then runs, from that commit's release directory:

- restores the API, MCP adapter and scheduler on that commit's images;
- recreates Caddy and the collector with that release's Caddyfile, collector configuration and web build;
- repeats the health checks and points `/srv/property-focus/current` at that release.

It does not restore:

- the database schema: migrations are never reversed, which is why they must stay expand/contract;
- Keycloak: its image, its own database and the realm stay as the newer release left them, because Keycloak cannot run an older version on a database a newer one migrated. Do not run `dogfood_compose up keycloak` after a rollback; the next deploy brings Keycloak in line again;
- the environment file, which only a deploy's preflight replaces.

A deploy that fails after starting its services runs this same rollback by itself (see [Deploy](#deploy)).

`--rollback` also refuses a release from before the Cloudflare edge (one without `infrastructure/dogfood/cloudflare-ips.txt`): its Caddy would try to obtain certificates the firewall no longer lets it get. Unlike a deploy, a rollback skips the edge preflight, so a Cloudflare API outage cannot block it.

Pick the commit from `releases.tsv` or `ls /srv/property-focus/releases`. Keep at least the previous release: its directory under `/srv/property-focus/releases` and its tagged images. Removing either (`rm -r`, `docker image rm`, `docker image prune --all`) deletes a release you could roll back to; older releases can go.

## Cloudflare edge

Cloudflare proxies both dogfood hostnames (A5). The user's Cloudflare account already serves the `treb.tech` zone on the Free plan. Every step here is a live change: ask the user before each, and record the settings you configured for the Foundation ready gate. The rate-limiting threshold below is the value the user confirmed on 2026-11-02; tune it from the access logs and record each change here.

### Edge certificates: both hostnames are first-level names

Cloudflare's free Universal SSL certificate covers only the zone apex and first-level subdomains ([Universal SSL limitations](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/)). Both dogfood hostnames, `propertyfocus.treb.tech` and `auth-propertyfocus.treb.tech`, are first-level names under `treb.tech`, so Universal SSL covers both and no Advanced Certificate Manager certificate is needed. Keep any new dogfood hostname first-level for the same reason.

Sign-in was first planned at the second-level `auth.propertyfocus.treb.tech`, which Universal SSL does not cover; the user moved it to `auth-propertyfocus.treb.tech` on 2026-11-02.

### DNS records and TLS mode

1. In the `treb.tech` zone, create `A` records for `propertyfocus.treb.tech` and `auth-propertyfocus.treb.tech` pointing at the host's public IPv4 address, with **Proxy status: Proxied** (orange cloud). Add matching proxied `AAAA` records only if the host has IPv6. A DNS-only record would send visitors straight to the origin, which the firewall refuses.
2. **SSL/TLS > Overview:** set the encryption mode to **Full (strict)**: Cloudflare connects to the origin over TLS and requires a valid certificate, which the Origin CA certificate is ([Origin CA](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/)).
3. **SSL/TLS > Edge Certificates:** turn on **Always Use HTTPS** ([Always Use HTTPS](https://developers.cloudflare.com/ssl/edge-certificates/additional-options/always-use-https/)).

### Origin certificate

1. **SSL/TLS > Origin Server > Create Certificate:** choose "Generate private key and CSR with Cloudflare" (RSA is the default key type; ECC also works), list exactly `propertyfocus.treb.tech` and `auth-propertyfocus.treb.tech` (replace the default apex and `*.treb.tech` entries: the certificate names only the two dogfood hosts, and `deploy.sh` refuses one that does not cover both), choose the validity, and create it.
2. Choose the PEM key format. Copy the certificate into the `dogfood` GitHub environment secret `DOGFOOD_ORIGIN_CERT` and the private key into `DOGFOOD_ORIGIN_KEY`. Cloudflare shows the key only on this screen; never store it anywhere else.
3. The next deploy writes both to `/etc/property-focus/origin/` as `cert.pem.new` and `key.pem.new`, checks them, and installs them over the live pair once its preflight passes. `infrastructure/dogfood/cloudflare-origin-ca.pem` holds Cloudflare's public Origin CA roots (RSA and ECC), against which `deploy.sh` verifies the certificate.

### Authenticated Origin Pulls

The firewall admits every Cloudflare range, which any Cloudflare customer's traffic also comes from, so Caddy also requires Cloudflare's origin-pull client certificate (A5). Cloudflare's current documentation calls this **global** Authenticated Origin Pulls: Cloudflare presents one client certificate, shared by all Cloudflare zones, issued by the CA in `infrastructure/dogfood/cloudflare-origin-pull-ca.pem` ([Global AOP](https://developers.cloudflare.com/ssl/origin-configuration/authenticated-origin-pull/set-up/global/)). It proves a connection comes from Cloudflare's network, not from this zone; Cloudflare's zone-level and per-hostname modes, with a certificate of our own, would prove the zone too.

Both sites complete a TLS handshake only with a client that presents a certificate from that CA, or the host's own health-check certificate, which `deploy.sh` creates beside the origin pair and presents in its loopback health checks. Every other client fails the handshake (`tlsv13 alert certificate required`).

Order matters, because Cloudflare sends its client certificate only when the origin asks for one, and Caddy asks only from the release that requires it:

1. **SSL/TLS > Origin Server > Authenticated Origin Pulls** tab, **Global** section: switch the toggle to **On**. The running Caddy, which does not ask for a client certificate, keeps serving as before.
2. Deploy the release whose Caddyfile requires the certificate. From then on Caddy asks, and Cloudflare presents it.
3. Confirm with `make smoke-dogfood` (the workflow's `smoke` job). If Cloudflare does not present the certificate, the origin refuses its handshake, Cloudflare answers `525`, and the smoke check names this setting.

Deploying first and switching the toggle on later breaks all traffic in between (`525`). To turn it off, do the reverse: roll back or deploy a release without the requirement first, then switch the toggle off.

`cloudflare-origin-pull-ca.pem` is valid until 2029-11-01; `deploy.sh` refuses to deploy within 30 days of that. Cloudflare announces a replacement certificate in advance: put the new CA beside the old one in the file (Caddy trusts every certificate in it) and deploy before Cloudflare switches, then remove the old one. Record the source URL, fetch date and SHA-256 fingerprint in the file's header; Cloudflare publishes no fingerprint, so compare the download with the copy in `github.com/cloudflare/cloudflare-docs` (`public/ssl/static/authenticated_origin_pull_ca.pem`), as on 2026-11-03.

### Host firewall

Docker-published ports bypass the host's `INPUT` rules and `ufw`, so `infrastructure/dogfood/firewall.sh` guards ports 80 and 443 in `DOCKER-USER`, the chain Docker sends published ports through, and in `INPUT`, for both IPv4 and IPv6. It admits them only from `cloudflare-ips.txt` and drops them from any other source; port 22 and all other traffic keep the rules they have.

1. As root, from the current release: `/srv/property-focus/current/infrastructure/dogfood/firewall.sh`. It prints the rules it applied. It guards the interface of the default route; set `PF_FIREWALL_INTERFACE` if the public interface is another one. Rerunning it is harmless.
2. Kernel rules are lost on reboot. Run it at boot, after Docker, with a systemd unit `/etc/systemd/system/pf-cloudflare-firewall.service` (then `systemctl enable pf-cloudflare-firewall`):

    ```ini
    [Unit]
    Description=Admit ports 80 and 443 only from Cloudflare
    After=docker.service
    Requires=docker.service

    [Service]
    Type=oneshot
    ExecStart=/srv/property-focus/current/infrastructure/dogfood/firewall.sh

    [Install]
    WantedBy=multi-user.target
    ```

3. Let the deploy user read the rules for the deploy check, and nothing else: `visudo -f /etc/sudoers.d/pf-firewall-check` with `deploy ALL=(root) NOPASSWD: /usr/sbin/iptables -S, /usr/sbin/ip6tables -S` (adjust the paths to `command -v iptables`).
4. Check from outside: `curl --max-time 5 https://<host IP>/` must time out, and `make smoke-dogfood` checks the same from the runner.

### WAF managed rules

The Free plan includes the **Cloudflare Free Managed Ruleset**, deployed by default on Free zones ([WAF get started](https://developers.cloudflare.com/waf/get-started/), [Managed rules](https://developers.cloudflare.com/waf/managed-rules/)). Under **Security > WAF > Managed rules**, confirm it is deployed and enabled for the zone; leave its rules at their defaults.

### Rate limiting rule

The Free plan allows **one** rate-limiting rule; it counts requests per IP over a fixed **10-second** period, its mitigation lasts **10 seconds**, and its expression can use only the **URI path** (and the verified-bot flag), not the hostname ([Rate limiting rules](https://developers.cloudflare.com/waf/rate-limiting-rules/)). The rule therefore applies to these paths on every `treb.tech` hostname; only the dogfood hosts serve them today. Counting is approximate and per data center, so a few requests over the threshold can reach the origin ([Rate limiting parameters](https://developers.cloudflare.com/waf/rate-limiting-rules/parameters/)).

Under **Security > WAF > Rate limiting rules**, create one rule:

- **Name:** `property-focus sign-in, registration and MCP`.
- **Expression** (Edit expression):

    ```text
    (http.request.uri.path wildcard "/realms/property-focus/clients-registrations/*")
    or (http.request.uri.path wildcard "/realms/property-focus/login-actions/registration*")
    or (http.request.uri.path eq "/realms/property-focus/protocol/openid-connect/auth")
    or (http.request.uri.path eq "/realms/property-focus/protocol/openid-connect/token")
    or (http.request.uri.path wildcard "/realms/property-focus/login-actions/authenticate*")
    or (http.request.uri.path eq "/mcp")
    ```

    If the dashboard refuses an operator on the Free plan, build the same conditions with the expression builder's "wildcard" or "starts with" path operators.
- **With the same characteristics:** IP.
- **When rate exceeds:** **20 requests per 10 seconds** (confirmed by the user, 2026-11-02). One sign-in takes about four requests to these paths and an MCP session's start a handful, so a person or a single MCP client stays well below it; registration spam does not.
- **Then take action:** **Block**, which answers `429` for the 10-second mitigation period. Do not use a challenge action: MCP clients and token requests are not browsers and cannot solve one.

Raise or lower the threshold from the access logs (`dogfood_compose logs caddy`, whose entries carry the real `client_ip`), and record each change.

### Bot Fight Mode

**Bot Fight Mode stays off** (user decision, 2026-11-02; the plan first had it on). Bot Fight Mode challenges traffic that matches known bot patterns with computationally expensive challenges, cannot be skipped or adjusted by WAF custom rules or Page Rules on the Free plan (only an IP Access rule that matches first avoids it), and may challenge API traffic ([Bot Fight Mode](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/)). MCP clients, token requests and the smoke check are all automated and would be challenged. The managed ruleset and the rate-limiting rule above protect sign-in, registration and `/mcp` without it.

The setting is under **Security > Settings**, filter **Bot traffic**, **Bot fight mode**: confirm it is off. If `make smoke-dogfood` ever fails on a challenge page (`cf-mitigated: challenge`), its message names Bot Fight Mode; check that the setting is still off.

### Refreshing `cloudflare-ips.txt`

Cloudflare publishes its ranges at `https://www.cloudflare.com/ips-v4`, `https://www.cloudflare.com/ips-v6` and `https://api.cloudflare.com/client/v4/ips`. When a deploy fails because the committed list differs:

1. Replace the CIDRs in `infrastructure/dogfood/cloudflare-ips.txt` with the published ones and update the fetch date in its header; run `make be-unit` and commit.
2. Deploy that commit. Its preflight then fails on the firewall check, because the live rules hold the old ranges: rerun `firewall.sh` as root from the new release directory (`/srv/property-focus/releases/<commit>/infrastructure/dogfood/firewall.sh`), with approval, and run the workflow again.

### Renewing the origin certificate

`deploy.sh` refuses to deploy within 30 days of the certificate's expiry. Create a new certificate as in [Origin certificate](#origin-certificate), replace both `dogfood` secrets, and deploy; the deploy writes, checks and installs the files and recreates Caddy, which loads them. Then revoke the old certificate under **SSL/TLS > Origin Server**.

### Sources

Checked 2026-11-02:

- Published ranges: <https://www.cloudflare.com/ips-v4>, <https://www.cloudflare.com/ips-v6>, <https://api.cloudflare.com/client/v4/ips>
- Origin CA certificates and root downloads: <https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/> (`origin_ca_rsa_root.pem`, `origin_ca_ecc_root.pem`)
- Global Authenticated Origin Pulls and its CA (checked 2026-11-03): <https://developers.cloudflare.com/ssl/origin-configuration/authenticated-origin-pull/set-up/global/>, <https://developers.cloudflare.com/ssl/static/authenticated_origin_pull_ca.pem>
- Universal SSL coverage: <https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/>
- Always Use HTTPS: <https://developers.cloudflare.com/ssl/edge-certificates/additional-options/always-use-https/>
- Managed rules on the Free plan: <https://developers.cloudflare.com/waf/managed-rules/>, <https://developers.cloudflare.com/waf/get-started/>
- Rate limiting limits and parameters: <https://developers.cloudflare.com/waf/rate-limiting-rules/>, <https://developers.cloudflare.com/waf/rate-limiting-rules/parameters/>
- Bot Fight Mode: <https://developers.cloudflare.com/bots/get-started/bot-fight-mode/>
- Detecting a challenge page (`cf-mitigated: challenge`): <https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/>

## Replay

Work is replayed by running it again, never by editing rows:

- Stalled or lost workflow runs are relaunched by the `repair` scheduled kind every five minutes. To run it now: `dogfood_compose exec scheduler python -m property_focus.cli run-task repair`.
- Outbox events stranded by a crash are launched by `outbox-sweep` each minute; run it the same way to sweep now.
- Scheduled kinds that later phases add (`imap-poll`, `invalidation-batch`, `backup`) replay the same way, through `run-task <kind>`.

## Keycloak

### Reaching the admin console

The console is not on the public hostname. Open an SSH tunnel and use it locally:

```sh
ssh -L 8080:127.0.0.1:8080 deploy@<host>
# then browse to http://localhost:8080/admin/
```

The dogfood Keycloak names `http://localhost:8080` as its admin hostname, so the console works through the tunnel only.

### Replacing the temporary bootstrap admin

The first start creates a temporary master-realm admin from `PF_KEYCLOAK_ADMIN_USERNAME` and `PF_KEYCLOAK_ADMIN_PASSWORD`. Replace it with a named operator account:

1. Sign in to the console as the bootstrap admin through the tunnel.
2. In the `master` realm, create a user for the operator with a verified email, set a strong password, and assign the `admin` role.
3. Sign out, sign in as the operator, and delete the temporary admin user.
4. Put the operator's username and password in `DOGFOOD_ENV_FILE`, because `deploy.sh` reconciles the realm as that admin. Keycloak creates no new bootstrap admin while the master realm has one.

### Password policy

The realm requires passwords of at least 12 characters that differ from the username and the email address (`length(12) and notUsername and notEmail`), for self-registered users, the smoke user and anyone an operator creates. People who forget theirs use "Forgot password?" on the sign-in page, which emails a reset link to their verified address through the SMTP relay. Changing the policy means changing `passwordPolicy` in the realm file and deploying; existing passwords keep working until their owners change them.

### Changing the SMTP password

Keycloak never returns a stored SMTP password, so `keycloak_realm.py check` cannot see that it changed. After updating `PF_KEYCLOAK_SMTP_PASSWORD`, set the same value in the console under Realm settings, Email, for the `property-focus` realm.

### Upgrading Keycloak

1. Dump the `keycloak` database first (until automated backups exist): `dogfood_compose exec postgres pg_dump --username=postgres --format=custom keycloak > keycloak-<date>.dump`, and copy the dump off the host.
2. Bump the pinned image, tag and digest, in both the root `compose.yaml` and `infrastructure/docker/keycloak.Dockerfile`; `test_runtime_manifests.py` fails when they differ.
3. Rerun the realm tests (`backend/tests/integration/test_keycloak_realm.py`) and the MCP dynamic-registration flow (`backend/tests/contract/test_mcp.py`) against the new version locally, through `make be-int`.
4. Deploy. Keycloak migrates its own database on start; restore the dump if it fails.

### Pruning registered MCP clients

MCP clients register themselves, and registration stops at `PF_KEYCLOAK_MAX_CLIENTS` clients. Prune clients that are no longer used:

1. In the console (`property-focus` realm, Clients), list clients with generated IDs; these are the dynamically registered ones. The realm's own clients (`property-focus-web` and the built-in ones) are never pruned.
2. Check each candidate's Sessions tab; delete clients with no sessions that their users no longer need. Users of a deleted client register again on their next sign-in.
3. `keycloak_realm.py apply` never deletes clients, so pruning is always this manual step.

## Database

What operators see when they work on the dogfood database directly (`dogfood_compose exec postgres psql ...`); [database and tenancy](system/modules/database-and-tenancy.md) has the detail.

- **Row security covers identity and events.** Since migration 0002, `users`, `tenants`, `domain_events`, `outbox_events` and `processed_events` have forced row-level security, like the tenant-private tables. A runtime session (`pf_runtime`) sees no rows of them until it sets a scope: `SET pf.service_scope = on` for every row, or a tenant scope. To read rows without one, use the backup role (`pf_backup`, whose login needs `BYPASSRLS`); the maintenance role `pf_maintenance` runs `VACUUM`, `ANALYZE`, `REINDEX` and `CLUSTER` but reads no rows. Sign-in creates users and tenants only through `pf_provision_account()`, a `SECURITY DEFINER` function that only `pf_runtime` may execute; do not insert those rows by hand.
- **Idle transactions are ended.** Runtime connections set `idle_in_transaction_session_timeout` to one minute: the server terminates a session left idle inside a transaction that long and releases its locks. The application sets it on its own connections; a `psql` session you open is not affected.
- **Idempotency records expire.** A request's idempotency key replays for 24 hours. The scheduled `repair` task (every 5 minutes) also deletes expired records, up to 1000 per pass, so the table stays bounded without manual pruning.

## Observability

Services export traces and metrics to the collector, which forwards them to OpenObserve. Open its UI through a tunnel: `ssh -L 5080:127.0.0.1:5080 deploy@<host>`, then `http://localhost:5080` as the `PF_OBSERVABILITY_ADMIN_EMAIL` user. Logs are JSON lines on each container's standard error: `dogfood_compose logs <service>`, rotated at five 10 MB files per container.
