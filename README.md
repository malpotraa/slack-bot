# Pronto

> Multi-user Slack-native AI assistant for Google Calendar, Google Ads, Wrike, and Slack — built on Claude (Anthropic), deployed on Google Cloud Run, observable through Braintrust.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Runtime](https://img.shields.io/badge/runtime-Cloud%20Run-4285F4.svg)](https://cloud.google.com/run)
[![LLM](https://img.shields.io/badge/LLM-Claude%20Sonnet%204.6-D97757.svg)](https://www.anthropic.com/claude)
[![License](https://img.shields.io/badge/license-Proprietary-lightgrey.svg)](#license)

Talk to it in a Slack DM. It reads your calendar, Google Ads KPIs, Wrike tasks, and unreplied @-mentions, and helps you act on them — with explicit approval before any write or sensitive KPI pull.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Slash commands](#slash-commands)
- [Agent capabilities & scope](#agent-capabilities--scope)
- [Operations runbook](#operations-runbook)
- [Observability](#observability)
- [Cost](#cost)
- [Security](#security)
- [Local development](#local-development)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Features

| Capability | Description |
|---|---|
| **Conversational agent** | Free-form chat in the bot's DM. Replies stream token-by-token (perceived TTFT ~1–2s). Threaded conversation memory with auto-summarization. |
| **`/connect`** | One-command OAuth flow for Google Calendar, Google Ads, Wrike, and Slack search. Card auto-cleans up after all integrations are linked. |
| **`/goodmorning`** | Daily briefing: unreplied @-mentions (last 7 days), Wrike "New" tasks, today's calendar with busy/free summary. |
| **`/wrike`** | Schedule Wrike "New" tasks onto your calendar via a multi-turn natural-language flow with overlap detection. |
| **`/kpi google <account> [cpa\|roas]`** | Google Ads KPI report for one account under the configured MCC, with approval before pulling campaign and keyword-driver data. |
| **Approval-gated writes** | Every write (create/update calendar event, change Wrike status, post Wrike comment) is two-phase: preview → user confirms → execute. |
| **Restricted scope** | Hard boundaries enforced in code + system prompt. No deletes on Calendar. No task creates/deletes on Wrike. Slack is read-only. |
| **Braintrust tracing** | Every conversation, tool call, and result captured as an OpenTelemetry trace with user attribution. |

## Architecture

```
                              ┌─────────────────────┐
Slack workspace ──Socket Mode►│ Cloud Run service   │──► Anthropic API
                              │  Bolt + FastAPI     │──► Google Calendar API
                              │                     │──► Google Ads API
User browsers ─OAuth callback►│  in one container   │──► Wrike API
                              └──────────┬──────────┘
                                         │
                              ┌──────────▼──────────┐
                              │  Cloud SQL Postgres │  (Cloud SQL Auth Proxy)
                              │  user, tokens,      │
                              │  sessions, cache    │
                              └─────────────────────┘
                              ┌─────────────────────┐
                              │  Braintrust         │◄── OTel HTTP exporter
                              └─────────────────────┘
                              ┌─────────────────────┐
                              │  Secret Manager     │ (injected as env vars)
                              └─────────────────────┘
```

**Key design decisions**

- **Single container, two services.** Slack Bolt (Socket Mode WebSocket) and FastAPI (OAuth callbacks) share one asyncio event loop. No reverse proxy needed.
- **Always-on CPU + `min=max=1` Cloud Run instance.** Required for Socket Mode reliability — see [Operations runbook](#operations-runbook).
- **Tokens encrypted at rest** with Fernet keys stored in Secret Manager. Database itself has no plaintext OAuth tokens.
- **Commands are deterministic first.** `/goodmorning`, `/wrike`, and `/kpi` use hand-coded data pulls and math. `/kpi` lets the agent write short explanations only from a bounded fact pack; it never calculates ad spend metrics.

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Package manager | uv |
| Web framework | FastAPI |
| Slack SDK | `slack-bolt` (async) + `slack-sdk` |
| LLM | Claude Sonnet 4.6 (agent) + Claude Haiku 4.5 (entity extraction) |
| ORM | SQLModel (Pydantic + SQLAlchemy) |
| DB driver | `asyncpg` (production) / `aiosqlite` (local dev) |
| Observability | OpenTelemetry + OpenInference + Braintrust |
| Compute | Google Cloud Run (gen2) |
| Database | Google Cloud SQL Postgres 15 |
| Secrets | Google Secret Manager |
| CI/CD | Google Cloud Build (manual `gcloud builds submit`; GitHub trigger optional) |
| Container registry | Google Artifact Registry |

## Repository layout

```
prod/
├── app/                              # Application code
│   ├── main.py                       # Entrypoint — runs Bolt + FastAPI on one event loop
│   ├── config.py                     # pydantic-settings; reads $PORT for Cloud Run
│   ├── observability.py              # Braintrust / OTel setup
│   ├── logging_setup.py              # loguru bridge for stdlib + uvicorn
│   ├── db/                           # SQLModel tables, token crypto (Fernet), engine
│   ├── oauth/                        # Google / Wrike / Slack OAuth flows + FastAPI callbacks
│   ├── slack_app/                    # Bolt app factory + slash commands + event handlers
│   │   └── commands/                 #   /connect, /goodmorning, /kpi, /wrike
│   ├── agent/                        # Conversational agent: runner, tools, prompts
│   ├── integrations/                 # Per-user clients: Google Calendar/Ads, Wrike REST, Slack search
│   ├── llm/                          # Structured extraction (slot-filling for /wrike)
│   ├── sessions/                     # Per-thread session store (Postgres)
│   ├── formatters/                   # Block Kit builders
│   └── utils/                        # Timezone, working hours, mrkdwn converter
│
├── infrastructure/                   # GCP setup scripts (run in numeric order)
│   ├── _env.sh                       # Common config — EDIT THIS FIRST
│   ├── 00-enable-apis.sh             # Enable required GCP APIs
│   ├── 01-create-cloud-sql.sh        # Postgres instance + database + user
│   ├── 02-create-secrets.sh          # Upload secrets to Secret Manager
│   ├── 03-create-artifact-registry.sh # Docker repo + service accounts + IAM
│   ├── 04-build-and-deploy.sh        # Build image + deploy to Cloud Run
│   ├── 05-update-secret.sh           # Helper to rotate a single secret
│   ├── 06-create-cicd-trigger.sh     # Optional: wire Cloud Build to GitHub
│   ├── 07-reset-db-schema.sh         # Wipe + recreate DB schema (dev/test only)
│   ├── 08-migrate-add-connect-card.sh # Example migration helper
│   └── 99-tear-down.sh               # Remove everything (typed-confirm)
│
├── scripts/                          # Local utility scripts
│   ├── gen_keys.py                   # Generate APP_SECRET_KEY + TOKEN_ENCRYPTION_KEY
│   ├── init_db.py                    # Manually run init_db
│   ├── clear_status_cache.py         # Invalidate Wrike workflow-status cache
│   ├── build_prod_guide.py           # Generate the Production Deployment Guide .docx
│   └── build_setup_guide.py          # Generate the local dev .docx (kept for reference)
│
├── docs/
│   └── Production_Deployment_Guide.docx  # Step-by-step deploy walkthrough
│
├── cloudbuild.yaml                   # Cloud Build pipeline (build → push → deploy)
├── Dockerfile                        # Multi-stage build with uv
├── .dockerignore
├── .gitignore
├── pyproject.toml
├── uv.lock
├── .env.example                      # Template for runtime env vars
└── README.md                         # You are here
```

## Prerequisites

You'll need accounts and apps configured at:

| Service | Why | Cost |
|---|---|---|
| **Slack** workspace (admin) | Bot + slash commands + per-user OAuth | Free |
| **Google Cloud** project | Hosting + Calendar OAuth client | Pay as Use |
| **Wrike** account (admin) | OAuth app for Wrike REST | Free trial |
| **Anthropic** account | Claude API key | Pay-as-you-go |
| **Braintrust** | Trace ingestion and evals (optional) | Free tier |

**Local CLI tools:**

```bash
# macOS
brew install --cask google-cloud-sdk
brew install uv

# Sign in
gcloud auth login
gcloud auth application-default login
```

## Quick start

> **Detailed walkthrough**: see `docs/Production_Deployment_Guide.docx` for the full step-by-step (Slack manifest, Google OAuth consent screen setup, Wrike OAuth app creation, GCP project setup with screenshots-style detail).

### 1 — Configure your project

```bash
cd prod
# Edit infrastructure/_env.sh, set:
#   PROJECT_ID="your-gcp-project-id"
```

### 2 — Provision GCP resources

```bash
./infrastructure/00-enable-apis.sh             # Enable APIs
./infrastructure/01-create-cloud-sql.sh        # ~5 min, prints DATABASE_URL
./infrastructure/03-create-artifact-registry.sh # Image repo + service accounts
```

### 3 — Generate secrets locally

```bash
uv run python scripts/gen_keys.py              # APP_SECRET_KEY + TOKEN_ENCRYPTION_KEY
cp infrastructure/secrets.env.example infrastructure-secrets.env
# Open infrastructure-secrets.env and fill in every value
#   (DATABASE_URL is in /tmp/slack-assistant-database-url after step 01)
```

### 4 — Upload secrets and deploy

```bash
./infrastructure/02-create-secrets.sh          # Upload to Secret Manager
./infrastructure/04-build-and-deploy.sh        # Build + deploy (~5 min)
# Prints the Cloud Run service URL at the end
```

### 5 — Wire OAuth providers to the production URL

For each of Slack / Google / Wrike, add the production redirect URI:

```
https://<your-cloud-run-url>/oauth/{slack|google|wrike}/callback
```

(Details in `docs/Production_Deployment_Guide.docx` §13.)

### 6 — Smoke test

In Slack, in the bot's DM:

```
/connect     ← runs first; click each OAuth button
/goodmorning ← briefing arrives in DM
/kpi google jump cpa
/wrike       ← list of "New" tasks with available time slots
```

Free-form: `"how does my day look tomorrow?"` — agent replies in-thread with streaming.

## Configuration

All runtime configuration is environment-driven. Cloud Run injects values from Secret Manager via `--update-secrets`. For local smoke testing (rare; we typically iterate in the dev tree), copy `.env.example` to `.env`.

| Variable | Required | Description |
|---|:---:|---|
| `APP_BASE_URL` | ✓ | Your Cloud Run service URL — patched automatically by `04-build-and-deploy.sh` |
| `APP_SECRET_KEY` | ✓ | Signs OAuth state tokens (CSRF protection). Generate with `gen_keys.py` |
| `TOKEN_ENCRYPTION_KEY` | ✓ | Fernet key for token encryption at rest. Generate with `gen_keys.py` |
| `DATABASE_URL` | ✓ | Postgres DSN (Cloud SQL Auth Proxy socket form) |
| `ANTHROPIC_API_KEY` | ✓ | From console.anthropic.com |
| `AGENT_MODEL` | — | Default `claude-sonnet-4-6` |
| `EXTRACTION_MODEL` | — | Default `claude-haiku-4-5-20251001` |
| `SLACK_BOT_TOKEN` | ✓ | `xoxb-…` from Slack app OAuth & Permissions |
| `SLACK_APP_TOKEN` | ✓ | `xapp-…` from App-Level Tokens (scope: `connections:write`) |
| `SLACK_SIGNING_SECRET` | ✓ | From Slack Basic Information |
| `SLACK_CLIENT_ID` | ✓ | From Slack Basic Information |
| `SLACK_CLIENT_SECRET` | ✓ | From Slack Basic Information |
| `GOOGLE_CLIENT_ID` | ✓ | From Google Cloud Credentials → OAuth client |
| `GOOGLE_CLIENT_SECRET` | ✓ | From Google Cloud Credentials → OAuth client |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | ✓ for `/kpi` | From Google Ads API Center |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | ✓ for `/kpi` | MCC customer ID used as the only account search root |
| `GOOGLE_ADS_API_VERSION` | — | Default `v22` |
| `WRIKE_CLIENT_ID` | ✓ | From wrike.com/frame/oauth2/apps |
| `WRIKE_CLIENT_SECRET` | ✓ | From wrike.com/frame/oauth2/apps |
| `BRAINTRUST_API_KEY` | — | Enables Braintrust OTLP trace export |
| `BRAINTRUST_PROJECT` | — | Default `pronto-ads-analyst` |
| `EVAL_CAPTURE` | — | Default `false`; when true, captures Google Ads analyst turns to the Braintrust eval dataset |
| `TRACE_SENSITIVE_DATA` | — | Default `false`: prompts, tool names/args and replies traced in full; tool *responses* traced as shape + types only (values masked). `true`: everything raw + Anthropic auto-instrumentation |
| `APP_ENV` | — | `prod` |
| `LOG_LEVEL` | — | `INFO` |

## Slash commands

| Command | Behavior |
|---|---|
| `/connect` | DMs you OAuth buttons for Google Calendar, Google Ads, Wrike, Slack search. Auto-cleans up the card 20s after all integrations are linked. |
| `/goodmorning` | Daily briefing: unreplied @-mentions (last 7d, with your own bot messages filtered out), Wrike "New" tasks + due-in-48h, today's calendar with busy/free totals. |
| `/kpi google <account> [cpa\|roas]` | Finds enabled accounts under the configured MCC whose name contains `<account>`, asks for disambiguation if needed, shows date ranges and scope, then pulls the KPI report after approval. |
| `/wrike` | Lists Wrike "New" tasks + open calendar slots. Reply in-thread with `"schedule #2 tomorrow 10–11"` (natural language). Bot extracts intent, previews the event, and creates it on approval with overlap detection. |

## Agent capabilities & scope

The conversational agent (free-form DM chat) operates under hard constraints enforced in both code and system prompt.

| Domain | Allowed | Forbidden |
|---|---|---|
| Google Calendar | List events, create events, **update** events | Delete events |
| Wrike | List tasks, get task details, change task status, post comments | Create / rename / delete tasks, edit fields |
| Slack | Search the user's unreplied @-mentions (read-only) | Send messages or replies on the user's behalf |
| Anything else | — | Web search, code generation, file uploads, etc. |

**Approval flow.** Every write tool (`create_calendar_event`, `update_calendar_event`, `update_wrike_task_status`, `post_wrike_task_comment`) is two-phase:

1. Agent calls the tool with `confirmed=false` → tool returns a preview.
2. Agent shows the preview to the user and asks "Approve?".
3. Only on explicit user approval, agent calls the tool again with `confirmed=true` → executes.

The agent cannot skip the preview step.

## Operations runbook

### Deploy a code change

```bash
./infrastructure/04-build-and-deploy.sh
```

Rebuilds the image, rolls a new Cloud Run revision. ~3–5 minutes. Zero-downtime.

### Rotate a single secret

```bash
./infrastructure/05-update-secret.sh ANTHROPIC_API_KEY "sk-ant-NEW-KEY"
gcloud run services update slack-assistant --region=us-central1 \
  --update-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest
```

### View live logs

```bash
gcloud run services logs tail slack-assistant --region=us-central1
```

### Roll back to the previous revision

```bash
gcloud run revisions list --service=slack-assistant --region=us-central1 --limit=5
gcloud run services update-traffic slack-assistant \
  --region=us-central1 --to-revisions=<previous-revision-name>=100
```

Takes ~10 seconds, zero downtime.

### Connect to Cloud SQL

```bash
gcloud sql connect slack-assistant-pg --user=app --database=slack_assistant
# password is in: gcloud secrets versions access latest --secret=DATABASE_URL
```

### Critical Cloud Run settings

| Setting | Value | Why |
|---|---|---|
| `--no-cpu-throttling` | ✓ | Socket Mode WebSocket stalls under CPU throttling between requests |
| `--min-instances=1` | ✓ | One always-warm replica holds the Slack websocket |
| `--max-instances=1` | ✓ | Socket Mode delivers an event to one replica only — extra replicas miss events |
| `--execution-environment=gen2` | ✓ | Required for long-running outbound connections |
| `--timeout=3600` | ✓ | OAuth callbacks can take a while; ample headroom |

These are codified in `cloudbuild.yaml`. Don't change them lightly.

## Observability

Every conversation, tool call, and DB operation is captured as an OpenTelemetry trace.

**Braintrust dashboard**: project from `BRAINTRUST_PROJECT`.

Span structure for a typical DM:

```
agent.turn   (input=user message, output=assistant reply, user.name, user.email, session.id)
├── agent.iteration  (stop_reason, tokens_in, tokens_out, cache_read, cache_create)
│   └── tool.list_calendar_events  (tool.input, tool.output)
└── agent.iteration  (final text response, streaming)
```

Filter by `user.name="Abhishek Malpotra"` to find a specific user's sessions. Filter by `session.id=<channel>:<thread_ts>` to replay a whole conversation.

**Anthropic prompt caching** is enabled. Watch `llm.token_count.cache_read` on the second turn in a thread — should be ~1500 tokens served from cache.

## Cost

Recurring infra costs at idle, before Anthropic API usage:

| Component | Tier | Monthly |
|---|---|---|
| Cloud Run (1 vCPU, 512 MB, min=max=1, no-cpu-throttling) | always-on | ~$13–18 |
| Cloud SQL Postgres `db-f1-micro` + 10 GB SSD | shared core | ~$8 |
| Secret Manager (14 secrets) | first 6 free | ~$1 |
| Cloud Build (120 build-min/day free) | — | $0 |
| Artifact Registry (first 0.5 GB free) | — | $0 |
| Braintrust (free tier) | — | $0 |
| **Total infra** | | **~$22–27** |

**Anthropic usage** is the variable cost — depends on how much your team uses the bot. With prompt caching, expect ~$0.10–0.50 per active user per day. A 10-person team running heavy use is ~$30–100/month of API.

## Security

- **All OAuth tokens encrypted at rest** with Fernet. The encryption key (`TOKEN_ENCRYPTION_KEY`) lives only in Secret Manager.
- **OAuth state tokens are signed** (`itsdangerous`) with a separate key (`APP_SECRET_KEY`) to prevent CSRF on the OAuth callback.
- **Slack requests are signature-verified** via `SLACK_SIGNING_SECRET`.
- **Cloud SQL has no public IP**. The container reaches it via the Cloud SQL Auth Proxy Unix socket — no network exposure.
- **The Cloud Run runtime service account** has only three IAM roles: `cloudsql.client`, `secretmanager.secretAccessor`, `logging.logWriter`. No project-wide admin.
- **Secrets are not committed to the repo**. `.gitignore` covers `.env`, `infrastructure-secrets.env`, and all generated state.
- **The agent's tool surface is restricted by code**, not just by prompt — even a jailbreak couldn't make it delete tasks or send Slack messages, because the tools don't exist in `app/agent/tools.py`.
- **Every write requires user approval**, enforced via `confirmed: bool` on every write tool.

### If a credential leaks

| Credential | Rotation procedure |
|---|---|
| `ANTHROPIC_API_KEY` | Revoke at console.anthropic.com → create new → `05-update-secret.sh` |
| `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` | Reinstall the Slack app, copy new tokens → `05-update-secret.sh` |
| `GOOGLE_CLIENT_SECRET` / `WRIKE_CLIENT_SECRET` | Rotate in provider's UI → `05-update-secret.sh` |
| `TOKEN_ENCRYPTION_KEY` | **Worst case** — invalidates every stored OAuth token. Generate new, `05-update-secret.sh`, all users must re-run `/connect` |

## Local development

For local iteration without deploying:

```bash
cd ..                                   # the dev tree at repo root
uv sync
uv run python scripts/gen_keys.py       # generate local keys
cp .env.example .env                    # fill in
uv run python -m app.main
```

The dev tree (`../app/`) is structurally identical to `prod/app/` but uses SQLite locally. Changes you validate locally can be mirrored to `prod/` and deployed.

## Troubleshooting

See `docs/Production_Deployment_Guide.docx` §15 for the full troubleshooting reference. Most-common gotchas:

| Symptom | Likely cause | Fix |
|---|---|---|
| "Sending messages to this app has been turned off" in Slack | Manifest is missing `messages_tab_read_only_enabled: false` | App Home → enable the messages tab toggle → Reinstall |
| `/connect failed: app did not respond` | Bolt isn't connected to Socket Mode | Check logs for `⚡️ Bolt app is running!`; verify `SLACK_APP_TOKEN` and Socket Mode toggle in Slack app |
| Container restarts mid-request | Default CPU throttling | Confirm `--no-cpu-throttling`, `--min-instances=1`, `--max-instances=1` |
| OAuth `invalid_client` | Client ID env var is wrong / placeholder | Confirm `gcloud run services describe ... --format='value(...env)'` shows secret-sourced values, not literals |
| Braintrust traces missing | Missing or wrong API key | Check `BRAINTRUST_API_KEY` and `BRAINTRUST_PROJECT` |
| `/goodmorning` includes bot's own messages | Aggressive bot-message filter not loaded | Check logs for `bot identity for filter:` line; redeploy |
| `/wrike` errors with `invalid_blocks` | Section overflow | Already fixed via `_section_chunks` — make sure latest is deployed |

## Contributing

This is a single-team internal tool. If you're collaborating:

1. Branch from `main`. Name branches `feat/...`, `fix/...`, or `chore/...`.
2. Make changes in both `app/` (dev tree) and `prod/app/` — see [Local development](#local-development).
3. Verify imports cleanly:
   ```bash
   cd prod && uv run python -c "
   import importlib
   for m in [
       'app.main', 'app.agent.runner', 'app.slack_app.handlers',
       'app.oauth.server', 'app.observability',
   ]:
       importlib.import_module(m)
   print('OK')
   "
   ```
4. Open a PR against `main`. If you've wired up the Cloud Build GitHub trigger (`06-create-cicd-trigger.sh`), the PR description should reference the auto-deploy build ID.

### Schema changes

If you change `app/db/models.py`, write an ALTER TABLE migration script in `infrastructure/` (see `08-migrate-add-connect-card.sh` as an example) and run it against prod before the code deploy that depends on it.

## License

Proprietary — internal Opensail tooling. All rights reserved.

## Acknowledgments

Built with:
- [Anthropic Claude](https://www.anthropic.com/claude)
- [slack-bolt](https://slack.dev/bolt-python/)
- [Braintrust](https://www.braintrust.dev/)
- [uv](https://docs.astral.sh/uv/)

---

*Last updated 2026-05-10 · Document version 1.1*
