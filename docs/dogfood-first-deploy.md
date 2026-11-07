# First dogfood deploy, step by step

This runbook takes a new DigitalOcean host from nothing to a running, smoke-checked dogfood environment behind Cloudflare. [operations.md](operations.md) explains each part and covers later deploys, rollback and recovery; this page is the exact command sequence for the first time.

Run every command exactly as written. Lines starting with `#` are comments.
Placeholders you fill in yourself are written `<LIKE_THIS>`; each is defined where it first appears.

Fixed values used throughout:

| Name | Value |
|---|---|
| GitHub repo | `robsgreen/property-focus` |
| App host | `propertyfocus.treb.tech` |
| Sign-in host | `auth-propertyfocus.treb.tech` |
| Mail sending domain | `mg.treb.tech` |
| Server user for deploys | `deploy` |
| `<YOUR_EMAIL>` | Your own email address: the OpenObserve admin login (Step 7) |

---

## Step 0 — Tools on your Mac

```bash
# Homebrew packages: GitHub CLI, Node 22, uv, OpenSSL, DigitalOcean CLI
brew install gh node@22 uv openssl doctl
brew install --cask docker        # Docker Desktop (Compose 2.27.2+ needed; if already installed: brew upgrade --cask docker); open it once
echo 'export PATH="/opt/homebrew/opt/node@22/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
corepack enable
gh auth login                     # choose GitHub.com, HTTPS, log in with a browser
doctl auth init                   # paste a DigitalOcean API token (Control Panel > API > Generate New Token, read+write)
```

Make a private working folder for the secrets you generate (deleted at the end):

```bash
mkdir -m 700 ~/pf-dogfood-secrets && cd ~/pf-dogfood-secrets
```

## Step 1 — Mac bootstrap (gate item 0.12.1)

```bash
cd ~ && gh repo clone robsgreen/property-focus && cd property-focus
make install
cp .env.example .env
# Edit .env and set your own local values for these two lines:
#   PF_KEYCLOAK_ADMIN_USERNAME=localadmin
#   PF_KEYCLOAK_ADMIN_PASSWORD=<any local password>
make verify 2>&1 | tee ~/pf-dogfood-secrets/macos-verify.log
```

The log holds no secrets. Record it as the macOS bootstrap evidence (gate item 0.12.1) in `specs/property-focus/gates/foundation-ready.md`.

## Step 2 — Generate the deploy SSH key and all passwords

```bash
cd ~/pf-dogfood-secrets

# SSH key pair GitHub Actions uses to reach the server (no passphrase, by design).
# Private half -> GitHub secret DOGFOOD_SSH_PRIVATE_KEY; public half -> server.
ssh-keygen -t ed25519 -N "" -C "property-focus deploy" -f pf_deploy_key

# Hex passwords contain only 0-9a-f, so they never need quoting.
openssl rand -hex 24 > PF_POSTGRES_PASSWORD
openssl rand -hex 24 > PF_MIGRATION_DATABASE_PASSWORD
openssl rand -hex 24 > PF_DATABASE_PASSWORD
openssl rand -hex 24 > PF_KEYCLOAK_DB_PASSWORD
openssl rand -hex 24 > PF_KEYCLOAK_ADMIN_PASSWORD
openssl rand -hex 24 > PF_SMOKE_USER_PASSWORD
# OpenObserve requires a lower-case letter, upper-case letter, digit and symbol:
echo "Oo9-$(openssl rand -hex 16)" > PF_OBSERVABILITY_ADMIN_PASSWORD

ls     # you should see pf_deploy_key, pf_deploy_key.pub and 7 password files
```

## Step 3 — Create the DigitalOcean server

```bash
# Upload your personal SSH key (for your own logins) to DigitalOcean, once:
doctl compute ssh-key import my-mac --public-key-file ~/.ssh/id_ed25519.pub
# (If you have no ~/.ssh/id_ed25519.pub, create one first: ssh-keygen -t ed25519)

MY_KEY_ID=$(doctl compute ssh-key list --format ID,Name --no-header | awk '$2=="my-mac"{print $1}')

# 4 GB RAM is the minimum (Keycloak, Postgres and OpenObserve have memory limits).
# Region: pick the one closest to you (nyc3, sfo3, tor1, lon1, ...).
doctl compute droplet create property-focus-dogfood \
  --region nyc3 --size s-2vcpu-4gb --image ubuntu-24-04-x64 \
  --ssh-keys "$MY_KEY_ID" --wait

SERVER_IP=$(doctl compute droplet get property-focus-dogfood --format PublicIPv4 --no-header)
echo "$SERVER_IP" > SERVER_IP && cat SERVER_IP

# Cloud firewall: only SSH, HTTP and HTTPS in; everything out.
DROPLET_ID=$(doctl compute droplet get property-focus-dogfood --format ID --no-header)
doctl compute firewall create --name property-focus-dogfood \
  --droplet-ids "$DROPLET_ID" \
  --inbound-rules "protocol:tcp,ports:22,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:80,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:443,address:0.0.0.0/0,address:::/0" \
  --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0 protocol:icmp,address:0.0.0.0/0,address:::/0"
```

`<SERVER_IP>` from here on means the address in `~/pf-dogfood-secrets/SERVER_IP`.

## Step 4 — Prepare the server

Copy the deploy public key and the firewall files up, then log in as root:

```bash
cd ~/pf-dogfood-secrets
scp pf_deploy_key.pub root@$(cat SERVER_IP):/root/
scp ~/property-focus/infrastructure/dogfood/firewall.sh \
    ~/property-focus/infrastructure/dogfood/cloudflare-ips.txt root@$(cat SERVER_IP):/root/
ssh root@$(cat SERVER_IP)
```

Now on the server (as root):

```bash
apt-get update && apt-get -y upgrade
apt-get install -y curl rsync python3 openssl iptables
curl -fsSL https://get.docker.com | sh          # Docker Engine + Compose plugin
docker compose version                          # must print v2.27.2 or newer

# Deploy user, in the docker group, reachable only with the deploy key
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
install -m 600 -o deploy -g deploy /root/pf_deploy_key.pub /home/deploy/.ssh/authorized_keys

# Directories the deploy writes
install -d -o deploy -g deploy /srv/property-focus/releases /var/lib/property-focus
install -d -m 700 -o deploy -g deploy /etc/property-focus

# Let deploy read (only read) the firewall rules for the deploy check
echo "deploy ALL=(root) NOPASSWD: $(command -v iptables) -S, $(command -v ip6tables) -S" \
  > /etc/sudoers.d/pf-firewall-check
chmod 440 /etc/sudoers.d/pf-firewall-check && visudo -c

# Firewall: ports 80/443 only from Cloudflare (both files must sit in the same folder)
chmod +x /root/firewall.sh && /root/firewall.sh
/root/firewall.sh --check && echo FIREWALL_OK

# Re-apply the firewall at every boot (points at the release copy once deploys exist)
cat > /etc/systemd/system/pf-cloudflare-firewall.service <<'EOF'
[Unit]
Description=Admit ports 80 and 443 only from Cloudflare
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'if [ -x /srv/property-focus/current/infrastructure/dogfood/firewall.sh ]; then exec /srv/property-focus/current/infrastructure/dogfood/firewall.sh; else exec /root/firewall.sh; fi'

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable pf-cloudflare-firewall

exit
```

Back on your Mac, record the server's SSH host key (GitHub uses it to trust the server):

```bash
cd ~/pf-dogfood-secrets
ssh-keyscan -t ed25519 $(cat SERVER_IP) > known_hosts && cat known_hosts
ssh -i pf_deploy_key deploy@$(cat SERVER_IP) 'docker ps && sudo -n iptables -S | head -3'   # must work without a password
```

## Step 5 — Cloudflare

In the Cloudflare dashboard, zone `treb.tech`:

1. **DNS > Records > Add record**, twice:
   - Type `A`, Name `propertyfocus`, IPv4 `<SERVER_IP>`, Proxy status **Proxied** (orange).
   - Type `A`, Name `auth-propertyfocus`, IPv4 `<SERVER_IP>`, Proxy status **Proxied** (orange).
2. **SSL/TLS > Overview:** encryption mode **Full (strict)**.
3. **SSL/TLS > Edge Certificates:** **Always Use HTTPS** = On.
4. **SSL/TLS > Origin Server > Create Certificate:**
   - "Generate private key and CSR with Cloudflare", key type RSA.
   - Hostnames: delete the defaults and enter exactly `propertyfocus.treb.tech` and `auth-propertyfocus.treb.tech`.
   - Validity: 15 years. Key format: PEM. Create.
   - Save the two boxes as files in `~/pf-dogfood-secrets` (Cloudflare shows the key only once):

     ```bash
     cd ~/pf-dogfood-secrets
     pbpaste > origin_cert.pem   # after copying the "Origin Certificate" box
     pbpaste > origin_key.pem    # after copying the "Private Key" box
     openssl x509 -in origin_cert.pem -noout -text | grep -A1 "Subject Alternative Name"   # must list both hostnames
     ```

5. **Security > WAF > Managed rules:** confirm the **Cloudflare Free Managed Ruleset** is enabled.
6. **Security > WAF > Rate limiting rules > Create rule:**
   - Name: `property-focus sign-in, registration and MCP`
   - Edit expression, paste:

     ```text
     (http.request.uri.path wildcard "/realms/property-focus/clients-registrations/*")
     or (http.request.uri.path wildcard "/realms/property-focus/login-actions/registration*")
     or (http.request.uri.path eq "/realms/property-focus/protocol/openid-connect/auth")
     or (http.request.uri.path eq "/realms/property-focus/protocol/openid-connect/token")
     or (http.request.uri.path wildcard "/realms/property-focus/login-actions/authenticate*")
     or (http.request.uri.path eq "/mcp")
     ```

   - Characteristics: IP. Rate: **20 requests per 10 seconds**. Action: **Block**. Deploy.
7. **Security > Settings > Bot traffic:** **Bot fight mode** = Off.
8. **SSL/TLS > Origin Server > Authenticated Origin Pulls** = **On**. This is the zone-wide ("Global") setting, and it must be on *before* the first deploy. With it on, Cloudflare presents its client certificate to your server, and Caddy refuses any HTTPS connection that lacks it. If it is off, every page shows Cloudflare error 525, and the smoke check fails with a message naming Authenticated Origin Pulls.

## Step 6 — Mailgun

1. Mailgun dashboard > **Send > Sending > Domains > Add new domain**: `mg.treb.tech`.
2. Mailgun shows DNS records. In Cloudflare DNS for `treb.tech`, add each one exactly as shown, with Proxy status **DNS only** (grey):
   - the **TXT** record for `mg` (SPF, `v=spf1 include:mailgun.org ~all`),
   - the **TXT** record for `<selector>._domainkey.mg` (DKIM, long `k=rsa; p=...` value),
   - the **MX** records and the tracking **CNAME** only if Mailgun marks them required.
3. Back in Mailgun, click **Verify DNS settings** and wait until the domain shows **Verified**.
4. **Domain settings > SMTP credentials > Add new SMTP user**: login `postmaster`, Mailgun generates the password. Save it:

   ```bash
   cd ~/pf-dogfood-secrets
   pbpaste > PF_KEYCLOAK_SMTP_PASSWORD      # after copying the generated password
   grep -q "'" PF_KEYCLOAK_SMTP_PASSWORD && echo "Contains a single quote: reset the password in Mailgun and try again"
   ```

## Step 7 — Build the server environment file

```bash
cd ~/pf-dogfood-secrets
cat > dogfood.env <<EOF
PF_POSTGRES_PASSWORD=$(cat PF_POSTGRES_PASSWORD)
PF_MIGRATION_DATABASE_PASSWORD=$(cat PF_MIGRATION_DATABASE_PASSWORD)
PF_DATABASE_PASSWORD=$(cat PF_DATABASE_PASSWORD)
PF_KEYCLOAK_DB_PASSWORD=$(cat PF_KEYCLOAK_DB_PASSWORD)
PF_KEYCLOAK_ADMIN_USERNAME=pf-admin
PF_KEYCLOAK_ADMIN_PASSWORD=$(cat PF_KEYCLOAK_ADMIN_PASSWORD)
PF_KEYCLOAK_SMTP_HOST=smtp.mailgun.org
PF_KEYCLOAK_SMTP_PORT=2525
PF_KEYCLOAK_SMTP_FROM=no-reply@mg.treb.tech
PF_KEYCLOAK_SMTP_USER=postmaster@mg.treb.tech
PF_KEYCLOAK_SMTP_PASSWORD='$(cat PF_KEYCLOAK_SMTP_PASSWORD)'
PF_OBSERVABILITY_ADMIN_EMAIL=<YOUR_EMAIL>
PF_OBSERVABILITY_ADMIN_PASSWORD=$(cat PF_OBSERVABILITY_ADMIN_PASSWORD)
EOF
cat dogfood.env     # check: 13 lines, no empty values
```

Replace `<YOUR_EMAIL>` in the file with your address before going on; it is only the login for the monitoring UI.

## Step 8 — GitHub environment, variables and secrets

```bash
cd ~/pf-dogfood-secrets
R=robsgreen/property-focus

# Environment "dogfood": you approve each deploy, and only main may use it.
# (Required reviewers on a private repo need GitHub Pro/Team; if this errors, drop the reviewers line.)
gh api -X PUT repos/$R/environments/dogfood \
  -F 'reviewers[][type]=User' -F "reviewers[][id]=$(gh api user --jq .id)" \
  -F 'deployment_branch_policy[protected_branches]=false' \
  -F 'deployment_branch_policy[custom_branch_policies]=true'
gh api -X POST repos/$R/environments/dogfood/deployment-branch-policies -f name=main

# Environment "dogfood-smoke": the smoke check runs here right after the deploy.
# No reviewers, main only, and it holds just the smoke user's password.
gh api -X PUT repos/$R/environments/dogfood-smoke \
  -F 'deployment_branch_policy[protected_branches]=false' \
  -F 'deployment_branch_policy[custom_branch_policies]=true'
gh api -X POST repos/$R/environments/dogfood-smoke/deployment-branch-policies -f name=main

# Repository variables (not secret; all three jobs read them, the build job too).
gh variable set PF_DOGFOOD_APP_HOST       -R $R --body propertyfocus.treb.tech
gh variable set PF_DOGFOOD_AUTH_HOST      -R $R --body auth-propertyfocus.treb.tech
gh variable set PF_DOGFOOD_ORIGIN_ADDRESS -R $R --body "$(cat SERVER_IP)"
gh variable set PF_SMOKE_USER_EMAIL       -R $R --body smoke@mg.treb.tech

# "dogfood" environment variables (only the deploy job needs these)
gh variable set DOGFOOD_SSH_TARGET        --env dogfood -R $R --body "deploy@$(cat SERVER_IP)"
gh variable set DOGFOOD_SSH_KNOWN_HOSTS   --env dogfood -R $R < known_hosts

# Secrets
gh secret set DOGFOOD_SSH_PRIVATE_KEY --env dogfood -R $R < pf_deploy_key
gh secret set DOGFOOD_ENV_FILE        --env dogfood -R $R < dogfood.env
gh secret set DOGFOOD_ORIGIN_CERT     --env dogfood -R $R < origin_cert.pem
gh secret set DOGFOOD_ORIGIN_KEY      --env dogfood -R $R < origin_key.pem
gh secret set PF_SMOKE_USER_PASSWORD  --env dogfood -R $R < PF_SMOKE_USER_PASSWORD
# The same password again, for the smoke job's environment:
gh secret set PF_SMOKE_USER_PASSWORD  --env dogfood-smoke -R $R < PF_SMOKE_USER_PASSWORD

# Check: 4 repository variables; 2 variables and 5 secrets on dogfood; 1 secret on dogfood-smoke.
gh variable list -R $R
gh variable list --env dogfood -R $R && gh secret list --env dogfood -R $R
gh secret list --env dogfood-smoke -R $R
```

`PF_SMOKE_USER_EMAIL` is a synthetic account the deploy creates in Keycloak for the smoke test; it never needs to receive mail. If you ever change the smoke password, update it in both `dogfood` and `dogfood-smoke`. Don't set the four repository variables on the `dogfood` environment as well: an environment copy would override the repository value for the deploy job only.

## Step 9 — Deploy

The deploy workflow can only be started once it exists on `main`, so the phase 0 pull request must be merged first.

```bash
gh workflow run deploy-dogfood -R robsgreen/property-focus --ref main
gh run watch -R robsgreen/property-focus $(gh run list -R robsgreen/property-focus --workflow deploy-dogfood --limit 1 --json databaseId --jq '.[0].databaseId')
```

GitHub asks you to approve the run (Actions tab, "Review deployments"). The run ends with `make smoke-dogfood`; a green run completes gate items 0.12.5 and 0.12.8.

The workflow only deploys from `main`. If the new release fails its health checks, the deploy rolls back to the previous release by itself. On the very first deploy there is no previous release, so it stops the new app services instead. A settings file or origin certificate that fails the pre-checks never replaces the live one.

## Step 10 — Check it, and replace the temporary Keycloak admin

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://propertyfocus.treb.tech/health/ready   # 200
curl -sS --max-time 5 -k https://$(cat ~/pf-dogfood-secrets/SERVER_IP)/ ; echo "exit=$?"   # must time out (exit=28)
```

Open <https://propertyfocus.treb.tech>, sign up with your email, click the verification link, sign in.

Replace the bootstrap admin (`pf-admin`):

```bash
ssh -i ~/pf-dogfood-secrets/pf_deploy_key -L 8080:127.0.0.1:8080 deploy@$(cat ~/pf-dogfood-secrets/SERVER_IP)
# then browse to http://localhost:8080/admin/ and sign in as pf-admin / (cat ~/pf-dogfood-secrets/PF_KEYCLOAK_ADMIN_PASSWORD)
```

In the `master` realm: Users > Add user (your name, verified email) > Credentials > set a strong password > Role mapping > assign `admin`. Sign out, sign in as that user, delete `pf-admin`. Then update `PF_KEYCLOAK_ADMIN_USERNAME` / `PF_KEYCLOAK_ADMIN_PASSWORD` in `dogfood.env` and re-run `gh secret set DOGFOOD_ENV_FILE --env dogfood -R robsgreen/property-focus < dogfood.env`.

## Step 11 — Clean up the local secrets

Keep a copy of the passwords in your password manager first (database and admin passwords are needed for recovery), then:

```bash
rm -P ~/pf-dogfood-secrets/*        # macOS secure delete
rmdir ~/pf-dogfood-secrets
```
