# Deploying the demo (AWS free tier + Cloudflare Tunnel)

One `t3.micro` running the app under systemd, reachable over HTTPS through a
Cloudflare tunnel. **No inbound web ports are opened** — the tunnel dials out.

Design rationale: `docs/superpowers/specs/2026-08-07-aws-demo-deployment-design.md`

---

## 0. Before you start

- The migrations in `backend/migrations/` must be applied to the Powabase
  project (Studio → SQL Editor). Powabase has no SQL endpoint, so this is
  always a manual step — including after any future migration.
- The deployed app **shares one Powabase project with local development**:
  the same users, agents and chats. That is deliberate for a demo.

## 1. Launch the instance

EC2 → Launch instance:

| | |
|---|---|
| AMI | Ubuntu Server 24.04 LTS |
| Type | `t3.micro` (free tier — check it is the free-tier type in your region; some are `t2.micro`) |
| Key pair | an existing one, or create one |
| Storage | 8–30 GB gp3 (30 GB is the free-tier ceiling) |

**Security group — inbound: SSH (22) from _My IP_ only.** Nothing else. No
HTTP, no HTTPS. The tunnel makes them unnecessary, and leaving them shut is
what keeps the box off the public internet.

## 2. Install

```bash
ssh -i ~/path/to/key.pem ubuntu@<public-ip>

sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/zhengoscar50/chatbot.git rag-chatbot
cd rag-chatbot/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` is pinned, so this installs exactly the versions the test
suite passes against. If you ever unpin it, this step silently becomes a
different app.

## 3. Configure

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Set:

| Variable | Notes |
|---|---|
| `POWABASE_BASE_URL` | from Powabase → Connect |
| `POWABASE_SERVICE_ROLE_KEY` | **secret** — full database access, server-side only |
| `AUTH_JWT_SECRET` | a long random string; `openssl rand -hex 32` |
| `ADMIN_PASSWORD` | enables `/admin`; leave unset to disable it |
| `SIGNUP_INVITE_CODE` | **set this.** Registration requires it. Without it, anyone with the URL can register and spend your LLM credits |
| `ORCHESTRATOR_MODEL` | the model that makes **routing** decisions. Default `gpt-4o-mini` |
| `DEFAULT_AGENT_MODEL` | model for new agents whose creator picks none. Default `gpt-4o-mini`. Applies at creation only — changing it never touches agents that already exist |
| `GENERAL_ASSISTANT_MODEL` | the no-agent fallback. Default `gpt-4o-mini` |

`POWABASE_AGENT_MODEL` is **not** in that list on purpose: nothing but the
optional bootstrap script reads it. Setting it changes no behaviour. This
deployment ran for two days advertising `claude-sonnet-5` while routing, new
agents and the general assistant were all still on `gpt-4o-mini`, because that
was the only model variable set.

> ### ⚠️ Local development and the deployment share one orchestrator
>
> The orchestrator and general assistant are **single shared Powabase agents**,
> and `ensure_orchestrator_agent` re-syncs their prompt *and model* on every
> startup. Both this box and your laptop point at the same Powabase project, so
> **starting a local server rewrites the deployment's router.** If your local
> `.env` and the box's disagree on `ORCHESTRATOR_MODEL`, whichever started last
> wins, and the demo silently changes model.
>
> Keep the model variables identical in both `.env` files, or point local
> development at a separate Powabase project.

## 4. Run it as a service

```bash
sudo cp ~/rag-chatbot/deploy/ragchat.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ragchat
systemctl status ragchat
curl -s localhost:8000/health      # {"status":"ok", ...}
```

If it fails to start, the cause is nearly always `.env`: startup calls
Powabase and exits if it can't reach it. `journalctl -u ragchat -n 50`.

## 5. The tunnel

### Quick tunnel (no Cloudflare account)

Fastest path, and what the demo currently runs:

```bash
curl -sL -o cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

sudo cp ~/rag-chatbot/deploy/cloudflared-quick.service /etc/systemd/system/cloudflared.service
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared
```

Read the URL it was given:

```bash
journalctl -u cloudflared | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1
```

**The hostname changes on every restart of this service**, including a reboot.
Cloudflare offers no uptime guarantee for quick tunnels. Fine for a demo link
you re-send; use a named tunnel below if you need it to stay put.

Nothing reports that move on its own — the service is healthy, the app is
healthy, and the only broken thing is a page you do not own. `tunnel-status.sh`
is what notices:

```bash
~/rag-chatbot/deploy/tunnel-status.sh
```

It prints the current URL, checks the app actually answers *through* it rather
than only on localhost, and compares against the last URL it saw. Exit status
is 0 unchanged, 3 moved, 1 broken — so `--quiet` works from cron:

```
*/30 * * * * ~/rag-chatbot/deploy/tunnel-status.sh --quiet || logger -t tunnel "demo URL moved or broken"
```

It reads only the CURRENT run's journal entries. The journal keeps every URL
this box has ever been given, so taking the last one overall would report a
hostname from three restarts ago as though it were live.

### Named tunnel (stable hostname)

In the Cloudflare dashboard: **Zero Trust → Networks → Tunnels → Create a
tunnel** (Cloudflared). Copy the token it shows, then on the box:

```bash
curl -L -o cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb && sudo mv /usr/bin/cloudflared /usr/local/bin/cloudflared

echo 'TUNNEL_TOKEN=<paste-token>' | sudo tee /home/ubuntu/.cloudflared.env
sudo chmod 600 /home/ubuntu/.cloudflared.env
sudo chown ubuntu:ubuntu /home/ubuntu/.cloudflared.env

sudo cp ~/rag-chatbot/deploy/cloudflared.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared
```

Back in the dashboard, add a **public hostname** for the tunnel pointing at
`http://localhost:8000`. That hostname is your shareable URL. Without this
mapping the tunnel connects and routes nothing — a healthy `cloudflared` and a
404 for every request is what a missing public hostname looks like.

### Switching a box that already runs the quick tunnel

The steps above assume a fresh box. Migrating an existing one differs in two
ways that are easy to get wrong.

**`cloudflared` is already installed.** Skip the download and the
`mv /usr/bin/cloudflared` line; check with `which cloudflared`, which should
already print `/usr/local/bin/cloudflared`.

**Both variants install to the same unit name.** `cloudflared-quick.service`
is copied to `/etc/systemd/system/cloudflared.service`, and so is the named
one — so copying the file replaces the quick tunnel's definition, but
`systemctl enable --now` will NOT pick it up: `--now` starts a unit that is
stopped and does nothing to one already running. The box keeps serving the old
quick tunnel, from the old definition, until it is restarted explicitly:

```bash
which cloudflared        # expect /usr/local/bin/cloudflared; skip the install if so

echo 'TUNNEL_TOKEN=<paste-token>' | sudo tee /home/ubuntu/.cloudflared.env
sudo chmod 600 /home/ubuntu/.cloudflared.env
sudo chown ubuntu:ubuntu /home/ubuntu/.cloudflared.env

sudo cp ~/rag-chatbot/deploy/cloudflared.service /etc/systemd/system/cloudflared.service
sudo systemctl daemon-reload
sudo systemctl restart cloudflared          # restart, NOT enable --now
systemctl show cloudflared -p ExecStart --value | grep -q -- --url \
  && echo "STILL THE QUICK TUNNEL" || echo "named tunnel active"
```

**Every embed already pasted on someone else's site breaks once**, and this is
the last time. Those snippets carry the old `trycloudflare.com` host, which
stops resolving; the share *token* is unchanged, so re-copying the snippet from
**Share** on the chatbot card is the whole fix. After this switch the hostname
survives restarts and reboots, which is the point of doing it.

The app needs no configuration for any of this: share and embed snippets are
built from `request.base_url`, and uvicorn honours the `X-Forwarded-Proto` and
`Host` headers `cloudflared` sends from localhost, so the new hostname and
`https://` appear in the snippets on their own.

## 6. Before you deploy

```bash
./deploy/preflight.sh https://<current-public-url>
```

Backend tests, all 241 DOM checks, uncommitted work, unpushed commits, and —
given a URL — whether what is CURRENTLY deployed still answers. Exits non-zero
if anything is wrong.

The last check is the point. Twice a fully green suite sat over something that
could not run at all: a missing CORS middleware, and a server left on stale
code after a deploy that reported success. Both were found by a person using
the app. Unit tests answer "is the logic right", never "does the thing that was
just committed actually run".

It is still not enough on its own. A 401 from an auth-gated route looks the
same whether the query behind it works or not — which is how the inbox shipped
selecting a column that does not exist. For that, run the smoke test **on the
box**, where the credentials and the database are:

```bash
cd ~/rag-chatbot/backend && set -a && . ./.env && set +a
.venv/bin/python ../deploy/smoke.py --token <a-chatbot-share-token>
```

It mints a real token with the app's own secret, creates a visitor conversation
through the public route, reads it back through the authenticated inbox, checks
the delete guards refuse the owner's private chats, then deletes what it made
and confirms it is gone. It touches no data it did not create, and cleans up
even when a check fails partway.

Verified against the real failure: reintroduce the `text`/`content` column
mistake and it reports `HTTP 502 column messages.text does not exist` and exits
non-zero, where the offline suite stays green.

## 7. Updating

```bash
cd ~/rag-chatbot
git pull
backend/.venv/bin/pip install -r backend/requirements.txt   # only if pins changed
sudo systemctl restart ragchat
```

Apply any new migration in Studio **before** restarting, or the new code will
be talking to an old schema.

## Rollback

```bash
cd ~/rag-chatbot
git log --oneline -10
git checkout <previous-good-sha>
sudo systemctl restart ragchat
```

Migrations do not roll back automatically. Check whether the version you are
returning to can live with the current schema.

## Logs and health

```bash
journalctl -u ragchat -f          # app
journalctl -u cloudflared -f      # tunnel
curl -s localhost:8000/health
```

## Cost

`t3.micro` is free for 750 h/month for 12 months — one always-on instance.
**The clock is per AWS account, not per instance**, and after 12 months it
bills at the normal rate. Stop the instance when you are done demoing.

LLM usage is billed by Powabase/your provider and is **not** capped by
anything in this app. The invite code limits who can register; it does not
limit what an invited person costs you.
