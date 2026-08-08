# AWS Demo Deployment — Design Spec

**Date:** 2026-08-07
**Status:** Approved for planning

## Goal

Put the app behind a shareable HTTPS URL, running on AWS free tier, without
exposing credentials or the owner's LLM spend to anyone who forwards the link.

## Decisions

1. **EC2 `t3.micro`, not Lambda.** 750 free hours/month for 12 months is one
   always-on instance, which matches the app's documented single-worker
   constraint. Lambda was rejected on a hard limit: API Gateway caps a request
   at **29 seconds** and `POST /agents/{id}/train` waits up to
   `ingest_max_wait_seconds` (60) for extraction and indexing, so training
   would time out. Lambda would also re-run the startup bootstrap on every cold
   container.

2. **Cloudflare Tunnel for TLS.** The app has password login; on a bare EC2
   public IP, credentials and JWTs would cross the internet in cleartext. A
   tunnel gives HTTPS with **no inbound ports open at all**, no certificate
   renewal, and no domain purchase. Rejected: Caddy + DuckDNS (works, but opens
   80/443 and adds a third-party DNS dependency).

3. **venv + systemd, not Docker.** One service on a 1 GB box: a venv and a unit
   file are fewer moving parts and easier to debug over SSH. Pinned
   requirements already buy the reproducibility Docker would have.

4. **Pin `requirements.txt` before anything ships.** It is currently unpinned
   (`fastapi`, `uvicorn`, `httpx`, … with no versions), so a cloud build would
   install whatever is newest that day — a different FastAPI or Pydantic than
   the 279 passing tests ran against. This is a prerequisite, not a nicety.

5. **Invite code on registration.** A shared link can be forwarded, and anyone
   who opens it could register and spend the owner's LLM credits — training
   uploads especially. One shared secret in the environment, required by
   `POST /auth/register` only; login is unaffected.

6. **One Powabase project, shared with local.** The deployed app and local
   development use the same database, agents and users. Accepted for a demo;
   separating them means a second project and a second set of keys.

## Constraints carried from the app

- **Single worker.** `uvicorn --workers 1`. Multiple workers would race on the
  startup find-or-create for the general KB, orchestrator and general
  assistant, and could duplicate them.
- **Startup hard-fails without Powabase.** `Restart=on-failure` with backoff so
  a transient blip at boot self-heals rather than leaving a dead box.
- **Migrations are manual.** Powabase exposes no SQL endpoint; each numbered
  migration is pasted into the Studio SQL Editor by hand.
- **Frontend is same-origin.** It calls relative paths (`/agents`, `/chat`), so
  it works under any hostname with no CORS changes.

## Architecture

```
browser ──HTTPS──▶ Cloudflare ──tunnel──▶ cloudflared (systemd)
                                              │ localhost:8000
                                              ▼
                                      uvicorn app.main:app  (systemd, 1 worker)
                                              │
                                              ▼
                                        Powabase project
```

Security group: inbound **SSH from the owner's IP only**. No web ports.

## Invite code

- `SIGNUP_INVITE_CODE` setting. **Empty means open registration**, so local
  development and the existing tests are unaffected by default.
- When set, `POST /auth/register` requires a matching `invite_code` and returns
  **403** with a clear message otherwise.
- `POST /auth/login` is untouched — existing users must never be locked out by
  a code rotation.
- Compared with `hmac.compare_digest`, matching how `ADMIN_PASSWORD` is already
  checked.
- The frontend registration form shows the field only when the server says one
  is required, so the local experience is unchanged. `GET /auth/signup-policy`
  reports `{"invite_required": bool}`.

## Deliverables

- `backend/requirements.txt` — pinned
- `backend/app/core/config.py` — `signup_invite_code`
- `backend/app/api/routes/auth.py` — gate + policy endpoint
- `frontend/index.html`, `frontend/app.js` — conditional invite field
- `deploy/ragchat.service`, `deploy/cloudflared.service` — systemd units
- `deploy/README.md` — the runbook: instance setup, deploy, update, rollback,
  logs
- `README.md` — a deployment section pointing at the runbook

## Out of scope

- Autoscaling, load balancing, multi-instance
- A CI/CD pipeline (deploys are a documented `git pull` + restart)
- Rate limiting and per-user spend caps
- A separate staging Powabase project
- Custom domain

## Risks

- **Shared Powabase project.** A visitor's agents and chats appear in local
  development and vice versa. Accepted; noted so it isn't a surprise.
- **No spend cap.** The invite code limits *who* can register, not how much an
  invited user costs. A trusted person can still run up credits.
- **Free tier expires after 12 months**, and it is per-account, not per-instance.
- **Secrets live in a file on the box.** Fine for a demo; SSM Parameter Store is
  the answer if this becomes more.
- **Do not cut over before the orchestrator smoke passes.** Migration 006 is
  unapplied, so the app currently has no conversation memory; deploying that
  would ship a known-broken experience to a public URL.
