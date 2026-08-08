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
`http://localhost:8000`. That hostname is your shareable URL.

## 6. Updating

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
