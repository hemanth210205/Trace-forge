# Deploying Traceforge

This guide puts Traceforge on one Linux server, behind [Caddy](https://caddyserver.com) for HTTPS. It keeps the
application's own design intact: one `python -m app.serve` process that listens only on `127.0.0.1`, PostgreSQL 16 in
Docker bound to `127.0.0.1`, and Caddy as the only thing reachable from the internet.

```
 Internet ──443/80──▶ Caddy (TLS, HTTP→HTTPS) ──127.0.0.1:8000──▶ python -m app.serve --database main
                                                                          │
                                                          127.0.0.1:5433 ─┴─▶ PostgreSQL 16 (Docker)
```

Read **[What this deployment does not change](#what-this-deployment-does-not-change)** before you start.

## Contents

1. [Requirements](#1-requirements)
2. [Prepare the server](#2-prepare-the-server)
3. [Get the code and build it](#3-get-the-code-and-build-it)
4. [Configure](#4-configure)
5. [Database](#5-database)
6. [Migrations](#6-migrations)
7. [Create the first account](#7-create-the-first-account)
8. [Run as a service (systemd)](#8-run-as-a-service-systemd)
9. [HTTPS with Caddy](#9-https-with-caddy)
10. [Firewall](#10-firewall)
11. [Google and GitHub sign-in](#11-google-and-github-sign-in)
12. [Health checks and smoke test](#12-health-checks-and-smoke-test)
13. [Backups](#13-backups)
14. [Restore test](#14-restore-test)
15. [Upgrading](#15-upgrading)
16. [Rollback](#16-rollback)
17. [Operations](#17-operations)

---

## 1. Requirements

- A Linux VPS (Ubuntu 24.04 LTS is assumed below; any systemd distribution works). 2 vCPU and 2 GB RAM are enough
  to start.
- A domain name whose `A` (and optionally `AAAA`) record points at the server. `traceforge.example.com` is used
  below; replace it everywhere.
- SSH access with `sudo`.

Platforms that run your app on `0.0.0.0` inside their own containers (Render, Railway, Fly and similar) do not fit:
`app.serve` deliberately refuses to listen on anything but loopback.

## 2. Prepare the server

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install git python3.11 python3.11-venv curl ufw debian-keyring debian-archive-keyring apt-transport-https

# Docker Engine and the compose plugin (https://docs.docker.com/engine/install/ubuntu/)
curl -fsSL https://get.docker.com | sudo sh

# Node.js 20+ to build the console once (https://nodejs.org); for example via NodeSource:
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt -y install nodejs

# Caddy (https://caddyserver.com/docs/install#debian-ubuntu-raspbian)
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt -y install caddy

# A dedicated, unprivileged user that owns the application. It is in the docker group so it can run backups.
sudo useradd --system --create-home --home-dir /opt/traceforge --shell /usr/sbin/nologin traceforge
sudo usermod -aG docker traceforge
```

Membership of the `docker` group is equivalent to root on that machine; keep the account non-login as shown.

## 3. Get the code and build it

```bash
sudo -u traceforge -s /bin/bash   # work as the service user from here on
cd /opt/traceforge
git clone <your repository URL> .
git checkout <release tag or commit>   # deploy a known revision, not a moving branch

python3.11 -m venv .venv
.venv/bin/pip install -e "backend" -c backend/constraints.txt
.venv/bin/pip install -e sdk/python      # only needed if you run the sample agents on this server

cd frontend && npm ci && npm run build && cd ..   # produces frontend/dist, served by the application
exit
```

Note the revision you deployed (`git rev-parse HEAD`); rollback needs it.

## 4. Configure

All configuration is environment variables, read from `/opt/traceforge/.env`. Start from the production template,
which contains placeholders only:

```bash
sudo install -m 600 -o traceforge -g traceforge /opt/traceforge/deploy/.env.production.example /opt/traceforge/.env
sudo -u traceforge nano /opt/traceforge/.env
```

Generate every secret on the server, one at a time, and never reuse development values:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

| Variable | Production value | Why |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | new random value | Database password; also inside `DATABASE_URL` (URL-encode special characters) |
| `TRACEFORGE_SECRET_KEY`, `TRACEFORGE_API_KEY_PEPPER` | new random values, 32+ characters | Sign sessions; hash API keys. Changing the pepper invalidates every API key |
| `TRACEFORGE_PUBLIC_URL` | `https://traceforge.example.com` | Origin of the console; OAuth redirect URLs are built from it |
| `TRACEFORGE_ALLOWED_HOSTS` | `traceforge.example.com,127.0.0.1` | Any other `Host` gets 400; `127.0.0.1` keeps local health checks working |
| `TRACEFORGE_CORS_ORIGINS` | `https://traceforge.example.com` | Exact origin trusted for login and registration |
| `TRACEFORGE_COOKIE_SECURE` | `true` | Session cookies only over HTTPS |
| `TRACEFORGE_TRUSTED_PROXIES` | `127.0.0.1,::1` | Only Caddy, on loopback, may set `X-Forwarded-For` / `-Proto` |
| `TRACEFORGE_REGISTRATION` | `invite` or `disabled` to start | Who may create accounts, by password **and** by Google/GitHub |

**Trusted proxies.** Forwarded headers are read only when the request's direct peer is in
`TRACEFORGE_TRUSTED_PROXIES`; from anyone else they are ignored. Behind Caddy that gives the login rate limiter the
visitor's real address instead of one shared `127.0.0.1` budget. Because the list trusts loopback, any process on
this server could set those headers: run nothing else untrusted on it. Do not list addresses outside your own
infrastructure.

**Registration modes** (existing accounts can always sign in, whatever the mode):

| Mode | New accounts | Notes |
| --- | --- | --- |
| `open` | Anyone | The development default. Not recommended on the internet |
| `disabled` | None | Create accounts with `python -m app.accounts user create` (section 7) |
| `domain` | Emails in `TRACEFORGE_REGISTRATION_DOMAINS` | Password sign-up does not verify the address (Traceforge sends no email), so anyone may *claim* such an address with a password. Google/GitHub sign-in requires a provider-verified email. Prefer `invite` if that matters |
| `invite` | Holders of an invitation | An invitation is tied to one email, used once, expires (default 7 days) and can be revoked. Password sign-up needs its code; Google/GitHub sign-in needs a pending invitation for the verified email |

Leave `TEST_DATABASE_URL` and `DEV_DATABASE_URL` unset on the server: the production server runs only against the
main database, and the test suite (which truncates its database) must never run here.

## 5. Database

```bash
cd /opt/traceforge
sudo -u traceforge docker compose up -d postgres
sudo -u traceforge docker ps --filter name=traceforge-postgres   # wait for "healthy"
```

PostgreSQL listens on `127.0.0.1:5433` only and keeps its data in the Docker volume `traceforge_pgdata`. On first
start the image creates `traceforge` (production) and, from `docker/postgres/init/`, the empty `traceforge_dev` and
`traceforge_test` databases. Those two are never used on the server; leave them empty.

## 6. Migrations

`python -m app.serve --database main` never migrates: if the main database is not at the code's schema revision it
refuses to start. Migrating is a separate, explicit step, guarded by a confirmation argument. The current head is
**0007**; a new database goes through 0001 → 0007 in one command.

```bash
cd /opt/traceforge/backend
# On an existing database: back up first (section 13).
sudo -u traceforge ../.venv/bin/python -m alembic -x database=main -x confirm=traceforge upgrade head
sudo -u traceforge ../.venv/bin/python -m alembic -x database=main -x confirm=traceforge current   # expect 0007 (head)
```

Migrations 0006 (Google/GitHub identities) and 0007 (invitations) only add tables and make the password column
nullable; they change no existing rows. Their downgrades are covered in [Rollback](#16-rollback).

## 7. Create the first account

With `TRACEFORGE_REGISTRATION=disabled` or `invite` nobody can sign up yet. Shell access to the server is what
authorises creating accounts:

```bash
cd /opt/traceforge/backend
# Directly (asks for the password twice; nothing is echoed or logged):
sudo -u traceforge ../.venv/bin/python -m app.accounts --database main user create you@example.com --name "Your Name"

# Or with an invitation (prints a code and a link once; only a hash is stored):
sudo -u traceforge ../.venv/bin/python -m app.accounts --database main invite create you@example.com --days 3
```

The invitation link has the form `https://traceforge.example.com/register#invite=<code>`. The code is in the
fragment, which browsers never send to the server, so it does not appear in Caddy or application logs. Send it over
a private channel.

Managing invitations later:

```bash
python -m app.accounts --database main invite list            # pending; add --all for used, revoked, expired
python -m app.accounts --database main invite revoke <id or email>
python -m app.accounts --database main invite prune --older-than-days 30
```

(Run them as `sudo -u traceforge ../.venv/bin/python ...` from `/opt/traceforge/backend`, as above.)

## 8. Run as a service (systemd)

```bash
sudo cp /opt/traceforge/deploy/traceforge.service /etc/systemd/system/traceforge.service
sudo systemctl daemon-reload
sudo systemctl enable --now traceforge
systemctl status traceforge
journalctl -u traceforge -n 50 --no-pager   # "Traceforge on database 'traceforge' at revision 0007 ..."
```

The unit runs `python -m app.serve --database main --host 127.0.0.1 --port 8000` as the `traceforge` user with a
hardened sandbox. It restarts on crashes but **not** after exit status 2 (schema out of date or unsafe database
choice), because restarting cannot fix that: run section 6 and `sudo systemctl restart traceforge`.

## 9. HTTPS with Caddy

```bash
sudo cp /opt/traceforge/deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/traceforge.example.com/YOUR.DOMAIN/' /etc/caddy/Caddyfile
sudo mkdir -p /var/log/caddy && sudo chown caddy:caddy /var/log/caddy
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy gets and renews the certificate automatically (ports 80 and 443 must be reachable and DNS must point here),
redirects HTTP to HTTPS, and forwards to `127.0.0.1:8000`. It discards `X-Forwarded-*` headers sent by clients and
sets its own; Traceforge believes them only because the peer is loopback (`TRACEFORGE_TRUSTED_PROXIES`).

## 10. Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

Port 8000 (Traceforge) and 5433 (PostgreSQL) must not be reachable from outside. They are bound to `127.0.0.1`,
which also matters because Docker's published ports bypass `ufw` rules. Verify from another machine:

```bash
nc -vz YOUR.DOMAIN 8000   # must fail
nc -vz YOUR.DOMAIN 5433   # must fail
```

## 11. Google and GitHub sign-in

Optional; without credentials the buttons explain that sign-in is not set up. Production needs its own OAuth apps
(or extra redirect URIs), registered with the public HTTPS address:

- **GitHub** (an OAuth App holds one callback URL, so create a production app separate from your local one):
  Settings → Developer settings → OAuth Apps → New OAuth App. Homepage `https://YOUR.DOMAIN`, callback
  `https://YOUR.DOMAIN/api/v1/auth/oauth/github/callback`. Generate a client secret.
- **Google**: Cloud console → APIs & Services → Credentials → OAuth client ID (Web application). Authorised redirect
  URI `https://YOUR.DOMAIN/api/v1/auth/oauth/google/callback`. While the consent screen is in *Testing*, only the
  test users you list can sign in; publish it for everyone (the `openid email profile` scopes need no review).

Add `TRACEFORGE_GITHUB_CLIENT_ID/SECRET` and/or `TRACEFORGE_GOOGLE_CLIENT_ID/SECRET` to `/opt/traceforge/.env` and
`sudo systemctl restart traceforge`. `TRACEFORGE_PUBLIC_URL` must be exactly the origin you registered.

The registration mode applies to Google/GitHub too: in `invite` mode a first sign-in needs a pending invitation for
the provider-verified email; in `disabled` mode only existing accounts can sign in.

Provider sign-in puts a one-time authorization `code` (and the `state`) in the callback URL. Neither is logged:
Traceforge's own logs record paths without query strings, and the shipped `deploy/Caddyfile` filters its access log
so those values (and other token-like query parameters, the `state` in the redirect to the provider, the CSRF header
and URLs in `Referer`) are written as `REDACTED`. Caddy itself already redacts `Cookie`, `Set-Cookie` and
`Authorization`. This was tested with Caddy 2.10.2 by sending real callback requests through it and inspecting the log.

## 12. Health checks and smoke test

On the server:

```bash
curl -fsS http://127.0.0.1:8000/api/v1/health                 # {"status":"ok",...}
curl -fsS https://YOUR.DOMAIN/api/v1/health                   # same, through Caddy and TLS
curl -sS -o /dev/null -w "%{http_code}\n" http://YOUR.DOMAIN/ # 308: redirected to HTTPS
curl -fsS https://YOUR.DOMAIN/api/v1/auth/registration        # {"mode":"invite"} (your mode)
curl -fsS https://YOUR.DOMAIN/api/v1/auth/providers           # the OAuth providers you configured
```

For external uptime monitoring, poll `https://YOUR.DOMAIN/api/v1/health` every minute and alert on non-200.

Smoke test in a browser:

1. Sign in with the first account; sign out; a wrong password is refused.
2. In the browser's developer tools, the `tf_session` cookie is `Secure`, `HttpOnly`, `SameSite=Lax`.
3. Registration behaves as configured (for `invite`: no code → refused; a fresh invitation → works once).
4. Create a project and an API key; from your laptop, send a trace:
   `TRACEFORGE_API_KEY=tf_... python examples/sample_agent/agent.py --endpoint https://YOUR.DOMAIN`
5. The trace appears under Traces. Optionally run `evaluate.py` and `experiment.py` from `examples/sample_agent`.

## 13. Backups

Use logical backups of the `traceforge` database (`pg_dump` custom format); they are consistent while the server
runs. Backups contain password and API-key hashes and everything your agents recorded: keep them private, off the
repository, and copy them off the server.

```bash
sudo install -d -m 700 -o traceforge -g traceforge /var/backups/traceforge
```

A cron entry is enough. As the `traceforge` user (`sudo crontab -u traceforge -e`):

```cron
# Daily at 03:15: dump inside the container, check the archive is readable, copy it out, keep 14 days.
15 3 * * * f=traceforge-$(date +\%Y\%m\%d-\%H\%M\%S).dump; docker exec traceforge-postgres sh -c "pg_dump -U \"\$POSTGRES_USER\" -d traceforge --format=custom --file=/tmp/$f && pg_restore --list /tmp/$f > /dev/null" && docker cp traceforge-postgres:/tmp/$f /var/backups/traceforge/$f; docker exec traceforge-postgres rm -f /tmp/$f; find /var/backups/traceforge -name 'traceforge-*.dump' -mtime +14 -delete
```

The dump is written inside the container and copied out with `docker cp` (never piped through the shell), and it
is copied only if `pg_restore --list` can read it. Check that files appear each day: `ls -lh /var/backups/traceforge`.

Then copy `/var/backups/traceforge` to storage outside the server (another machine, or object storage with
encryption), for example with `rsync` or `rclone` on a second cron entry. A backup that lives only on the server is
lost with the server.

Always take a fresh backup before migrating or upgrading.

## 14. Restore test

A backup counts only once it has been restored. Restore into a **scratch** database, check it, and drop it; the
production database is not touched:

```bash
f=/var/backups/traceforge/traceforge-YYYYMMDD-HHMMSS.dump
docker cp "$f" traceforge-postgres:/tmp/restore.dump
docker exec traceforge-postgres sh -c 'createdb -U "$POSTGRES_USER" traceforge_restore_check'
docker exec traceforge-postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d traceforge_restore_check --no-owner --exit-on-error /tmp/restore.dump'

# Same revision and plausible row counts as production:
docker exec traceforge-postgres sh -c 'psql -U "$POSTGRES_USER" -d traceforge_restore_check -Atc "select version_num from alembic_version"'
for t in users projects traces spans datasets evaluation_runs invitations; do
  docker exec traceforge-postgres sh -c "psql -U \"\$POSTGRES_USER\" -d traceforge_restore_check -Atc \"select '$t', count(*) from $t\""
done

docker exec traceforge-postgres sh -c 'dropdb -U "$POSTGRES_USER" traceforge_restore_check'
docker exec traceforge-postgres rm -f /tmp/restore.dump
```

Do this after setting up backups, and then regularly (monthly is a reasonable rhythm). Restoring over the real
`traceforge` database is a disaster-recovery step: stop Traceforge first (`sudo systemctl stop traceforge`), take a
backup of the current state, then follow `docs/backup-and-restore.md`.

## 15. Upgrading

```bash
cd /opt/traceforge
git rev-parse HEAD                        # note the current revision for rollback
# take a backup (section 13)
sudo -u traceforge git fetch && sudo -u traceforge git checkout <new tag or commit>
sudo -u traceforge .venv/bin/pip install -e "backend" -c backend/constraints.txt
cd frontend && sudo -u traceforge npm ci && sudo -u traceforge npm run build && cd ..
sudo systemctl stop traceforge
cd backend && sudo -u traceforge ../.venv/bin/python -m alembic -x database=main -x confirm=traceforge upgrade head && cd ..
sudo systemctl start traceforge
curl -fsS http://127.0.0.1:8000/api/v1/health
```

## 16. Rollback

1. **Code only (no new migration in the release):** check out the previous revision, reinstall, rebuild the
   console and restart, as in section 15 without the migration.
2. **The release added a migration:** prefer restoring the backup taken just before the upgrade (section 14 shows how
   to verify it first). Alembic downgrades exist but are not all lossless:
   - `0007 → 0006` drops the `invitations` table (pending invitations are lost; accounts are kept).
   - `0006 → 0005` drops linked Google/GitHub identities **and deletes accounts that have no password** (created
     through Google/GitHub), because the 0005 schema cannot hold them. Do not run it on data you want to keep.

   ```bash
   sudo systemctl stop traceforge
   cd /opt/traceforge/backend
   sudo -u traceforge ../.venv/bin/python -m alembic -x database=main -x confirm=traceforge downgrade 0006   # example
   cd .. && sudo -u traceforge git checkout <previous revision>   # then reinstall, rebuild, start
   ```
3. Confirm with the health checks in section 12.

## 17. Operations

- **Logs:** `journalctl -u traceforge` (application) and `/var/log/caddy/traceforge-access.log` (requests: method,
  path, status, timing, client address and user agent; sensitive query values and headers appear as `REDACTED`).
  If you add query parameters that carry secrets, add them to the `request>uri query` filter in the Caddyfile.
  Application logs record each request's method, path (no query string), status and duration, never bodies or
  headers, and scrub keys, tokens and passwords from messages; invitation codes are never logged. Uvicorn's own
  access log is turned off (`app/serve.py`), because it would print full URLs.
- **Rotating secrets:** a new `TRACEFORGE_SECRET_KEY` signs everyone out; a new `TRACEFORGE_API_KEY_PEPPER`
  invalidates every API key (users must create new ones). Restart after editing `.env`.
- **System updates:** `sudo apt update && sudo apt upgrade`, and `docker compose pull postgres` within PostgreSQL 16
  (take a backup first).

## What this deployment does not change

- **One process.** The login rate limiter and the background recovery sweep live in the server process. Do not run
  several copies of the service against one database.
- **No email.** Traceforge sends no email: no password reset, no address verification for password sign-ups. Use
  `invite` or `disabled` registration, or Google/GitHub sign-in, accordingly.
- **The rest of `docs/release-checklist.md`** still applies: the Anthropic diagnosis provider is experimental, and
  interrupted work is marked failed rather than resumed.
