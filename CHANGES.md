# Changes

## Reasoning

This pass started as a full repository security and correctness review of the production Slack Assistant. The main risks were not classic injection bugs; they were operational and workflow risks around secrets, OAuth identity binding, approval replay, tracing sensitive content, and database races.

The implementation therefore focused on hardening the existing architecture instead of redesigning it:

- Keep the current single-container Cloud Run + Slack Socket Mode shape.
- Preserve the existing approval-card UX, but make approval execution idempotent at the database level.
- Keep per-user OAuth token storage, but validate callback identity more strictly before storing tokens.
- Keep Phoenix observability, but make content tracing opt-in instead of default.
- Keep simple shell-based infrastructure, but make script ordering and secret access explicit.
- Add database constraints for logical identities, with a migration that can handle legacy duplicates.

This follow-up adds Google Ads KPI reporting while keeping the same principle: deterministic data access and math in code, with the agent used only for short narrative explanations from a bounded fact pack.

## Google Ads KPI Changes

- Added `/kpi google <account> [cpa|roas]`.
  - Searches enabled non-manager Google Ads accounts under the configured MCC only.
  - Supports `cpa` and `roas`, defaulting to `cpa`.
  - Handles multiple account matches with a Slack account picker before approval.

- Added Google Ads OAuth as a separate `/connect` provider.
  - Uses the Google Ads `adwords` scope.
  - Stores tokens in a new encrypted `GoogleAdsToken` table.
  - Keeps Google Ads consent separate from Google Calendar consent.

- Added Google Ads REST API integration.
  - Uses GAQL `searchStream`.
  - Requires `GOOGLE_ADS_DEVELOPER_TOKEN`.
  - Requires `GOOGLE_ADS_LOGIN_CUSTOMER_ID`; all account search and fetches are scoped to that MCC.
  - Revalidates the selected account under the MCC at approval time before querying metrics.

- Added deterministic KPI windows and metric math.
  - Last 7 completed days vs previous 7 completed days.
  - Month-to-date vs the same day range last month.
  - Anchors on the Google Ads account timezone and excludes the current partial day.
  - Computes CPA and ROAS from raw totals rather than averaging campaign-level ratios.

- Added approval-gated KPI fetches.
  - The user sees the account, metric mode, scope, and frozen date ranges before data is pulled.
  - Existing approve/deny card behavior and one-time DB claim protection are reused.
  - KPI pulls are treated as approved read actions, separate from write tools.

- Added agent-written explanations.
  - The agent receives only computed metrics and top keyword-driver facts.
  - It does not calculate spend metrics, choose accounts, or fetch data.
  - If the agent summary fails, the report still returns the deterministic KPI numbers.

- Added Google Ads deployment support.
  - `cloudbuild.yaml` maps new Google Ads secrets into Cloud Run.
  - `infrastructure/02-create-secrets.sh` uploads the new Pronto Google Ads secrets.
  - `infrastructure/11-migrate-google-ads-kpi.sh` creates the existing-DB schema.

## Follow-up Fixes

- Replaced internal OAuth callback exception text with a generic browser message.
  - Detailed errors remain in server logs.
  - This avoids exposing DB/API/internal failure detail to whoever completes an OAuth flow.

- Retained fire-and-forget task handles until completion.
  - OAuth connect-card cleanup tasks are stored in a module-level set.
  - Slack reaction helper tasks are also tracked and logged on failure.

- Changed `/connect` cleanup to use core integrations.
  - Google Calendar, Wrike, and Slack search are enough to tidy the card.
  - Google Ads remains available through `/connect` later when the user wants `/kpi`.

- Serialized per-thread conversation history writes.
  - Reduces races between agent-turn finalization and approval-card settlement.

- Parallelized Google Ads KPI fetches.
  - After MCC account validation, independent campaign and keyword-driver GAQL calls run concurrently.
  - Added a short per-user MCC account cache for repeated account searches.

- Added startup hardening and cleanup.
  - `TOKEN_ENCRYPTION_KEY` is validated as a Fernet key at boot.
  - Expired `ConversationSession` rows and old `ApprovalExecution` rows are purged at startup.

- Removed minor correctness drift.
  - Gave the shutdown wait task an explicit name.
  - Switched event-loop access to `asyncio.get_running_loop()`.
  - Updated the tools docstring to include the Google Ads KPI tool.

## Performance Changes

- Reused shared `httpx.AsyncClient` instances.
  - Google Ads, Wrike, and OAuth calls now use keep-alive connection pools instead of opening a new client per API call.

- Reduced conversational-agent Calendar prefetch cost.
  - Upcoming-events prefetch is skipped once a thread already has history.
  - Fresh-thread prefetches are cached briefly per user/channel/thread.

- Reduced repeated Calendar work in conflict paths.
  - Calendar previews and `/wrike` scheduling reuse one 5-day horizon event fetch for both overlap detection and alternate-slot suggestions.

- Cached Google Calendar credentials and service objects in process.
  - Avoids repeated token DB reads, Fernet decrypts, and Calendar service reconstruction during common multi-call flows.
  - Google Calendar OAuth reconnect invalidates the per-user cache.

## KPI UX Changes

- Google Ads connect prompts now update after successful OAuth.
  - `/kpi` stores the one-off connect prompt location on the user row.
  - The Google Ads OAuth callback updates it to "Google Ads connected" and deletes it after a short delay.

- KPI approval cards are visually clearer.
  - Account, metric, WOW/MOM date ranges, and scope are split into sections.
  - The primary button now reads `📊 Pull KPI`.

- KPI approval clicks now show immediate progress.
  - After the approval card is claimed, Slack updates to "Fetching Google Ads KPI report..." while the Google Ads calls run.
  - Extra clicks are ignored by the existing one-time approval claim.

- `/kpi` now uses the invoking thread.
  - The command posts an emoji status message first.
  - Account selection, approval, connect prompts, and KPI output stay in the same thread.
  - The fetching state removes the buttons and no longer tells the user not to double-click.

## Security Changes

- Added Docker build-context protection in `.dockerignore` for:
  - `infrastructure-secrets.env`
  - `infrastructure/secrets.env`
  - `*.pem`
  - `*.key`

- Redacted database URL logging in `app/db/engine.py`.
  - Passwords in the URL authority are hidden with SQLAlchemy URL rendering.
  - Query-string secret-like keys such as `password`, `pass`, `token`, and `secret` are also masked.

- Escaped public OAuth callback HTML in `app/oauth/server.py`.
  - Provider names and callback error messages are HTML-escaped before rendering.
  - This removes reflected HTML/script injection from public callback URLs.

- Validated OAuth state provider binding in `app/oauth/server.py`.
  - Google callbacks require `state.provider == "google"`.
  - Wrike callbacks require `state.provider == "wrike"`.
  - Slack user-token callbacks require `state.provider == "slack_user"`.

- Added Slack OAuth identity checks.
  - Slack `authed_user.id` must match the signed state user when Slack returns it.
  - Slack `team.id` must match the signed state team when Slack returns it.

- Sanitized Slack OAuth failure logging in `app/oauth/slack_user.py`.
  - Logs only the Slack error code, not full OAuth response bodies.

- Sanitized Wrike REST failure logging in `app/integrations/wrike.py`.
  - Uses `safe_error_summary()` instead of raw `resp.text`.

- Added Slack mrkdwn escaping helpers in `app/utils/slack_mrkdwn.py`.
  - `escape_slack_text()` escapes `&`, `<`, and `>`.
  - `quote_slack_text()` safely renders untrusted multi-line text as a Slack quote.
  - Applied to calendar titles, Wrike task titles/comments, Slack snippets, user notes previews, and error messages.

- Made sensitive tracing opt-in.
  - Added `TRACE_SENSITIVE_DATA=false` in `app/config.py`, `.env.example`, and `README.md`.
  - Custom Phoenix spans redact user prompts, tool inputs, tool outputs, final text, and user email unless enabled.
  - Anthropic auto-instrumentation is skipped unless `TRACE_SENSITIVE_DATA=true`.

## Approval Flow Changes

- Added `ApprovalExecution` in `app/db/models.py`.
  - Stores one-time approval-card claims.
  - Tracks `claimed`, `succeeded`, `failed`, or `cancelled`.

- Hardened `app/slack_app/approval.py`.
  - Approval clicks claim a DB row keyed by Slack team, channel, and message timestamp before dispatching any write.
  - Duplicate clicks or Slack retries cannot execute the same write twice.
  - Only approval-key unique conflicts are treated as already-claimed.
  - Other database integrity errors update the card with a clear failure.
  - Cancel clicks also claim the approval, so approve/cancel races settle once.

- Preserved the existing local UX improvements.
  - Alternate approvals record `approved_alternate`.
  - Update-event approval buttons distinguish Move, Rename, and Update.

## Database Changes

- Added uniqueness constraints in `app/db/models.py`.
  - `User`: `(slack_team_id, slack_user_id)`
  - `ConversationSession`: `(user_id, channel_id, thread_ts)`
  - `WorkflowStatusCache`: `(user_id, status_name, custom_status_id)`
  - `ApprovalExecution`: `approval_key`

- Updated `app/db/users.py`.
  - `get_or_create_user()` now handles concurrent insert races with a nested transaction.
  - The outer session is not rolled back when another request creates the same user first.

- Updated `app/integrations/wrike.py`.
  - Concurrent first-touch status-cache insert races are handled without surfacing a user-facing tool error.

- Added `infrastructure/10-migrate-security-hardening.sh`.
  - Runs the hardening migration in one transaction.
  - Creates `approvalexecution`.
  - Deduplicates legacy duplicate users, sessions, and Wrike status-cache rows.
  - Adds the new unique indexes.
  - Rolls back the entire migration if any statement fails.

## Infrastructure Changes

- Updated `cloudbuild.yaml`.
  - Adds `_RUN_SA_EMAIL`.
  - Deploys Cloud Run with `--service-account=${_RUN_SA_EMAIL}`.

- Updated `infrastructure/04-build-and-deploy.sh`.
  - Passes `_RUN_SA_EMAIL` into Cloud Build.

- Updated `infrastructure/06-create-cicd-trigger.sh`.
  - Passes `_RUN_SA_EMAIL` into the Cloud Build trigger substitutions.

- Updated `infrastructure/03-create-artifact-registry.sh`.
  - Removed broad project-level `roles/secretmanager.secretAccessor` grants.
  - Keeps Cloud SQL, logging, Cloud Run deploy, Artifact Registry, and service-account impersonation permissions.

- Updated `infrastructure/02-create-secrets.sh`.
  - Verifies the runtime service account exists before granting per-secret access.
  - Grants `roles/secretmanager.secretAccessor` on each managed secret individually.

- Updated `infrastructure/01-create-cloud-sql.sh`.
  - Writes local password and DATABASE_URL helper files with mode `600`.
  - Stops printing the credentialed `DATABASE_URL` to stdout.
  - Tells the operator to delete local `/tmp` helper files after use.

## Runtime Correctness Changes

- Added time-range validation in `app/agent/tools.py`.
  - Rejects `end <= start`.
  - Rejects timed blocks longer than 12 hours.
  - Allows existing all-day calendar events to be moved without hitting the 12-hour cap.

- Fixed `/wrike` long-duration handling in `app/slack_app/commands/wrike_cmd.py`.
  - Durations over 8 hours now stop immediately with a user-facing message instead of continuing to approval.

- Fixed Ruff issues found during the review.
  - Moved a late `re` import.
  - Replaced a lambda with a small function.
  - Removed an unnecessary f-string.
  - Removed an unused exception binding.

## Dependency Changes

- Removed unused `claude-agent-sdk` from `pyproject.toml`.
  - The app imports and uses `anthropic` directly.
  - `uv.lock` no longer includes the unused SDK and its MCP/jsonschema/SSE transitive dependencies.

## Documentation Changes

- Updated `README.md`.
  - Documented `TRACE_SENSITIVE_DATA`.

- Updated `CHANGELOG.md`.
  - Added release `1.8.0 — 2026-05-19`.
  - Included migration instructions, security changes, behavior changes, verification, and tradeoffs.

## Verification

The following checks passed:

```bash
python3 -m compileall app scripts
uv --cache-dir /private/tmp/uv-cache run ruff check .
uv --cache-dir /private/tmp/uv-cache tool run pip-audit --path .venv
bash -n infrastructure/01-create-cloud-sql.sh infrastructure/02-create-secrets.sh infrastructure/03-create-artifact-registry.sh infrastructure/04-build-and-deploy.sh infrastructure/06-create-cicd-trigger.sh infrastructure/10-migrate-security-hardening.sh
```

`pip-audit` reported no known vulnerabilities in the installed environment.

## Deploy Notes

For an existing deployment, run:

```bash
./infrastructure/02-create-secrets.sh
./infrastructure/10-migrate-security-hardening.sh
./infrastructure/04-build-and-deploy.sh
```

If secrets did not change, `02-create-secrets.sh` can be skipped, but it must have been run at least once after this change so the runtime service account has per-secret access.

For a fresh deployment, run the normal sequence:

```bash
./infrastructure/00-enable-apis.sh
./infrastructure/01-create-cloud-sql.sh
./infrastructure/03-create-artifact-registry.sh
./infrastructure/02-create-secrets.sh
./infrastructure/10-migrate-security-hardening.sh
./infrastructure/04-build-and-deploy.sh
```
