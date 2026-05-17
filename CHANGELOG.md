# Changelog

All notable changes to the **production** deployment of Slack Assistant are documented here. This file tracks the prod tree only.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) with a **Why & tradeoffs** section per release so future you understands *why* a decision was made, not just *what*. The project follows pragmatic versioning — minor for new behavior, patch for bug fixes — without strict semver compatibility guarantees yet.

---

## [Unreleased]

Nothing pending right now. The previous release (`v1.6.1`) is what's queued for deploy.

---

## [1.6.1] — 2026-05-17

Hot-fix after the v1.6.0 deploy: the conversational agent was hitting the tool-iteration cap on multi-step write flows.

### Changed

- **Default `max_tool_iterations` raised from 3 → 4** in `app/agent/runner.py`. The 3-turn cap was too tight for the common pattern `list_calendar_events → update_calendar_event(confirmed=false) → speak`, especially when the model defensively re-checked before writing. Symptom: user saw `I hit my tool-use limit before finishing — try asking again more specifically.` instead of an approval card, because the preview tool never got invoked and `pending_actions` was empty.
- **System prompt** in `app/agent/prompts.py` now tells the model that `create_calendar_event` and `update_calendar_event` already run their own conflict check + refetch the current event, so it should go straight to the write tool after a single `list_calendar_events`. Updated the "BE DECISIVE" section's iteration count to match (3 → 4).

### Why & tradeoffs

- *Why bump the cap by only 1?* 3 turns is the asymptotic minimum for a write flow without persisted tool results (`list → preview → speak`), so any defensiveness blows through. 4 buys exactly one extra step for re-fetch or follow-up; 5+ starts to allow runaway loops. The prompt change attacks the same problem from the other direction — fewer wasted turns to begin with — so 4 should be ample headroom.
- *Cost impact?* One extra Anthropic call worst-case, ~$0.005 per affected turn at current Sonnet 4.6 pricing. Cached prompt makes the input side nearly free. Worth it to avoid the dead-end "limit reached" UX.
- *Why call out the conflict check in the prompt?* Without it, the model has no way to know `update_calendar_event` got smarter. We surface tool behaviour at the prompt level so the model's planning matches what the tools actually do — keeps the iteration budget aligned with the contract.

---

## [1.6.0] — 2026-05-16

Approval-card UX: human-readable times and conflict detection on calendar create/update.

### Added

- **Time-formatting helpers** in `app/utils/timezone.py`: `fmt_local_dt(dt, tz_name)` and `fmt_local_range(start, end, tz_name)`. Both convert to the user's tz first, then render `Mon May 18, 10:00am – 10:30am` (same day) or `Mon May 18, 11:30pm – Tue May 19, 12:30am` (cross-day). Replaces raw ISO strings (`2026-05-18T10:00:00-06:00`) in every approval preview.
- **Public overlap helpers** in `app/integrations/google_calendar.py`: `find_overlaps(user_id, start, end, *, exclude_event_id=None)` and `busy_slots_in_horizon(user_id, anchor, *, days=5)`. Skips declined / transparent (Free) / all-day events and the event being updated itself. Extracted from `/wrike`'s scheduler so the conversational agent's create/update tools can reuse the same logic.
- **Conflict detection on `create_calendar_event` and `update_calendar_event` previews.** When the proposed time overlaps existing events, the preview lists each conflicting event (title + time, capped at 5) and includes an `alternate` field with the next free slot of the same duration on/after the proposed start (search horizon: 5 days, computed inside the user's working window via `first_free_slot_for_duration`).
- **Alternate-aware approval card** rendered by `app/slack_app/handlers.py`. Each pending action becomes its own card. When the tool returned an `alternate`, the card has three buttons: `✅ Use this time (keep conflict)` (re-dispatches with the user's original times, conflict ignored — that was an explicit ask), `🔁 Use suggested slot` (re-dispatches with the alternate's start/end), and `❌ Disapprove`. When there's no conflict, the existing two-button card is used.

### Changed

- **`update_calendar_event` preview layout** now puts `*Current:*` and `*New:*` on separate lines instead of `Current → New` on one line. Title and time render on independent lines so long event names don't break the layout.
- **`dispatch_tool` signature use** — `create_calendar_event` and `update_calendar_event` now receive `workday_start` / `workday_end` from the dispatcher (they were already plumbed in for other tools). Required for the alternate-slot search to stay inside the user's working hours.
- **`run_agent_turn` `pending_actions` items** gained an optional `alternate` field, captured from the tool's preview result and passed through to the handler.

### Why & tradeoffs

- *Why human-readable times?* The previous preview showed `start: 2026-05-18T10:00:00-06:00 → 2026-05-19T10:00:00-06:00`. That's three things at once (date, time, offset) in a format no one reads quickly, and the offset doubles as a fake confirmation that the tz is right — even though we already converted to the user's tz internally. Users were eyeballing the offset to verify, which is exactly the wrong load-bearing decision. `Mon May 18, 10:00am – 10:30am` is unambiguous in the user's frame because the bot already knows their tz.
- *Why detect conflicts at preview time, not just hope the user noticed?* The old flow trusted the LLM to call `list_calendar_events` first. It often didn't, especially for one-shot "move it to Tuesday" requests where the model has the event_id and just patches. Two real cases hit this: an existing meeting was double-booked because the model didn't re-check, and a focus block was placed inside an existing 1:1. Adding the check at the tool level makes it impossible to skip and means the approval card itself is the conflict review surface.
- *Why allow "Use this time (keep conflict)" instead of forcing the alternate?* The user explicitly asked for this — sometimes you *want* to overlap (e.g. you'll only attend the first half of one meeting). Refusing or forcing a re-pick would be paternalistic. The three-button card surfaces both options so the choice is explicit and recorded (Slack message log).
- *Why search 5 days and not, say, 30?* P50 of `list_events` over a 5-day window with the gen2 Cloud Run sizing is ~300–500ms; a 30-day window with paging is multi-second and we're already inside the user's first turn. Five days covers "today" / "tomorrow" / "this week" intents, which is what the conversational write path is for. The Wrike scheduler still has its own horizon (`/wrike` is built for multi-day scheduling).
- *Tradeoff: cards now post one-per-action instead of one-bundled.* If the LLM proposes two writes in one turn (rare in practice — most turns are single-action), the user sees two cards. Acceptable: each is independently approvable, and the alternate path only makes sense per-action anyway.
- *Tradeoff: the alternate's `args` payload now embeds an extra `start_iso`/`end_iso` pair.* Approval-button `value` size is bounded at 2000 chars; current payloads are ~250 chars even with the alternate, well under the limit. If a future tool grows args dramatically, `_encode_payload` already truncates gracefully.

---

## [1.5.0] — 2026-05-16

Security hardening pass + repo hygiene. Follows a full audit of the prod codebase.

### Added

- **Approval-handler tool allow-list** (`app/slack_app/approval.py`). New `WRITE_TOOL_ALLOWLIST` set restricts which tool names the Approve-button handler will dispatch, regardless of what the button payload claims. Currently allows: `create_calendar_event`, `update_calendar_event`, `update_wrike_task_status`, `post_wrike_task_comment`, `schedule_wrike_task`, `update_working_hours`. Rejected attempts are logged with `clicker / channel / message_ts` for auditability and the card updates to "⚠️ Rejected".
- **`app/oauth/_logging.py`** (new module). `safe_error_summary(resp)` extracts `error` + `error_description` from JSON OAuth-failure responses (capped at 200 chars), or returns `status=N (non-JSON body, omitted)`. Replaces the previous "log `resp.text` verbatim" pattern.
- **`.env.example` is now tracked** (gitignore re-includes it via `!.env.example`). It was unintentionally being captured by the broader `.env.*` rule and not landing in the repo for new contributors.

### Changed

- **OAuth refresh + code-exchange failure logging sanitized.** `app/oauth/google.py` (1 call site), `app/oauth/wrike.py` (2 call sites) now log via `safe_error_summary` instead of raw `resp.text`. Eliminates the risk of a provider echoing request bodies (or, in rare misconfigurations, the refresh_token / client_secret) into Cloud Logging.
- **User-facing error messages no longer interpolate the exception.** `app/slack_app/handlers.py` (agent-turn failure), `app/slack_app/commands/goodmorning.py` (briefing failure), `app/slack_app/commands/wrike_cmd.py` (date/time parse failure) now post a generic "try again" message to Slack. Full traceback still captured server-side via `logger.exception()` and the Phoenix span.
- **`infrastructure/_env.sh` `PROJECT_ID` default reverted from `slack-marketing-bot` to `CHANGE-ME`.** A real GCP project ID shouldn't be baked into the public template. Users now set `PROJECT_ID` themselves before running the numbered scripts (same as initial deploy required).
- **`.gitignore` expanded** to cover `.pytest_cache/`, `.mypy_cache/`, `.coverage`, `htmlcov/`, `Thumbs.db`, `.idea/`, `.vscode/`, `*.swp` / `*.swo` / `*.bak`, `*.pid`, `*.sock`, `.python-version` — common cache/editor/runtime artefacts that shouldn't ride along in the repo. Also gained `data/*.sqlite*` for completeness.

### Removed

- **`scripts/build_setup_guide.py`** — generates the local-dev setup guide `.docx`, irrelevant to the prod tree. Was a leftover from the dev → prod copy.

### Security audit summary (what we checked and found clean)

A full sweep covered: SQL injection (none — SQLModel ORM only), command injection (no `eval`/`exec`/`subprocess`/`shell=True`), insecure deserialization (no `pickle.loads`/`yaml.load_unsafe`), Slack signing verification (Bolt handles it), OAuth state CSRF (signed + 10-min TTL via itsdangerous), token encryption at rest (Fernet symmetric), container privileges (non-root uid 1001), Cloud SQL public IP (none, Auth Proxy only), service-account scopes (3 minimal roles), dependency pinning (`uv.lock`), hardcoded credentials (none in tracked files — verified by grep), open redirects (provider-fixed redirect URIs), CSRF on the FastAPI side (no state-changing POST endpoints public).

Open items deferred (not exploitable in single-tenant): task_id shape validation before path interpolation in `WrikeClient.task()` (low — httpx URL-encodes); signed-payload + per-user-bound approval cards (recommended before going multi-tenant). Documented in `claude.md` "Planned work".

### Why & tradeoffs

- *Why an allow-list when Slack already signs the outer request?* Defense in depth. Bolt validates the HMAC over the request body, so an attacker can't forge a click. But the *button payload itself* (the JSON in `value`) is set by us at card-creation time and trusted blindly at click-time. A future regression that built a button with a non-write tool name would silently let users invoke that tool through the approval path. The allow-list closes that gap explicitly. Five lines of code.
- *Tradeoff: every new write tool now requires a code change in two places* — `app/agent/tools.py` for the dispatcher, plus an entry in `WRITE_TOOL_ALLOWLIST`. Forgetting the allow-list entry means buttons silently get rejected with a clear log line — failure is loud, not silent. Acceptable.
- *Why sanitize the OAuth error log instead of removing it entirely?* When refresh fails, we need to know *why* (expired token vs. revoked vs. provider outage vs. wrong client secret). The structured `status=400 error='invalid_grant' desc='Token expired'` is enough to triage without leaking request bodies.
- *Why generic user messages?* Stack traces and DB error strings leak schema details and internal paths. The user is authenticated (it's their DM), but minimising info disclosure costs nothing and removes a low-effort recon vector. Operators still see everything via Cloud Logging + Phoenix.
- *Why `CHANGE-ME` not `slack-marketing-bot` in `_env.sh`?* The project ID alone isn't a credential, but it's also not something a public GitHub repo should advertise. Anyone reading the repo learns a real GCP project name; combined with social engineering it's a small uplift for an attacker. Cost to switch: zero.

---

## [1.4.1] — 2026-05-12

Hot-fix release after the v1.4.0 deploy.

### Fixed

- **`KeyError: '\n  "task_index"'` in `/wrike` slot extractor.** The `SLOT_EXTRACTION_SYSTEM_PROMPT` contains an example JSON output with literal `{` / `}`. We called `str.format(today_iso=..., tz=...)` on it, which tried to interpret every `{` as a placeholder. Switched the prompt-substitution from `.format()` to two `.replace()` calls so literal braces in the prompt are safe. (`app/llm/extract_schedule.py`)

### Added

- **`/wrike` now updates the Wrike task's status to *"Accepted & Scheduled"*** after the calendar event is created. Implemented via a new agent tool `schedule_wrike_task` that does both writes atomically (Calendar event + Wrike status). Status update is best-effort — if the status name doesn't exist in the workspace, the event is still created and a warning is logged. (`app/agent/tools.py`)
- **Approval-button card for `/wrike`'s scheduler.** Previously the scheduler either auto-created on a clean slot, or asked the user to type "schedule anyway" / "use suggested" on an overlap. Now every scheduling action goes through the same Approve / Disapprove button card the conversational agent uses — single button for clean slots, three buttons for overlaps (Schedule anyway / Use suggested slot / Disapprove). (`app/slack_app/commands/wrike_cmd.py`, `app/slack_app/approval.py`)
- **`build_approval_blocks_with_alternates()`** in approval module, and a new `agent_approve_alt` action_id, so a single card can offer two paths (e.g. "approve original time" vs "approve suggested time") plus Cancel.

### Removed

- **`_create_event_and_finish`** helper in `wrike_cmd.py` — superseded by the `schedule_wrike_task` tool which runs from the Approve-button handler.
- **`AWAITING_OVERRIDE` state machine stage.** Buttons carry their own action payload; we no longer need text-based override handling in session state.

### Why & tradeoffs

- *Why update Wrike status on schedule?* The user explicitly asked for it — having "scheduled" status on the Wrike task is the visible signal back into Wrike that the work has been planned. Without it, the bot creating a calendar event is invisible to anyone looking at Wrike.
- *Best-effort status update.* If the workspace doesn't have a "Accepted & Scheduled" status, we don't fail the whole action — the calendar event was the primary intent. Tradeoff: silent partial success that only shows up in logs. Acceptable for our single-tenant deploy; a future change should surface this back to the user in the success card.
- *Why route `/wrike` through the same approval card as the agent?* User UX consistency. The user said the missing buttons on `/wrike` "is super critical" — having two different approval styles in the same bot is confusing.
- *Tradeoff: lost the typed "schedule anyway" muscle memory.* A long-time user who knew to type "schedule anyway" now has to click instead. Net positive — buttons are clearer for first-time users and remove the LLM round-trip needed to parse "schedule anyway".

---

## [1.4.0] — 2026-05-11

Major UX upgrade: approval buttons everywhere, Wrike URL handling, `/wrike` thread non-scheduling fallback.

### Added

- **Approval-button card for every write tool.** Replaces the text-based "type yes" pattern. When the agent calls a write tool with `confirmed=false`, the runner captures it as a `pending_action`. After streaming finishes, the handler posts a separate Block Kit card with **✅ Approve** / **❌ Disapprove** buttons. Clicking Approve re-dispatches the tool with `confirmed=true` and updates the card to "✅ Done." (`app/slack_app/approval.py` — new module, `app/agent/runner.py`, `app/slack_app/handlers.py`)
- **Wrike URL → task lookup.** `WrikeClient.task_by_permalink(url)` extracts the numeric task id from any Wrike URL (workspace.htm, open.htm, app-eu.wrike.com…) and resolves it to the alphanumeric API task id via `GET /tasks?permalink=...`. All Wrike tools (`get_wrike_task`, `update_wrike_task_status`, `post_wrike_task_comment`) now accept either `task_id` (alphanumeric) OR `task_ref` (a URL). (`app/integrations/wrike.py`, `app/agent/tools.py`)
- **`/wrike` thread fallback to conversational agent.** If a user replies inside a `/wrike` thread with non-scheduling intent ("update status of #15 to complete", "post a comment saying I'll get this tomorrow"), the slot extractor returns nulls. We now detect this (heuristic: action verbs like "update", "complete", "post comment" + no schedule signals) and forward the message to the conversational agent with the task list as extra system context. The agent can resolve "task #15" to the right Wrike API id and act on it. (`app/slack_app/commands/wrike_cmd.py`, `app/agent/runner.py` accepts new `extra_system_context` param)
- **`update_working_hours` / `get_working_hours` tools.** Users can say "set my working hours to 8am to 4pm" in DM. Google Calendar's Working Hours feature has no public API, so we store the window in our own User row. (`app/agent/tools.py`, prompt updated)
- **Multi-signal bot-message filter** in Slack search. Filters bot/app messages by `bot_id`, `subtype`, our bot's `user_id` from `auth_test`, AND display-name match. Fixes the bot's own DM posts appearing as "unreplied mentions" in `/goodmorning`. (`app/integrations/slack_search.py`)
- **Section chunking for `/wrike` task list.** `wrike_blocks.py` now uses the same `_section_chunks` helper as `briefing.py` to keep each Slack section block under the 3000-char limit when the user has many "New" tasks.
- **Anthropic SDK extra-field filter (`_clean_assistant_block`).** Newer SDK versions decorate text blocks with `parsed_output` etc. The API rejects those on inbound. We whitelist-filter blocks to `{type, text}` (or `{type, id, name, input}` for tool_use) before round-tripping. (`app/agent/runner.py`)

### Changed

- **System prompt** updated to instruct the LLM: when calling a write tool with `confirmed=false`, keep the reply text short (one sentence) — the system auto-posts the Approve / Disapprove card; the LLM should not say "type yes". Also added a new section explaining Wrike URL handling: pass URLs as `task_ref`, not `task_id`. (`app/agent/prompts.py`)
- **Slack reply detection** in `find_unreplied_mentions` now uses `match.thread_ts` (parent thread ts) instead of `match.ts` (which might be a reply ts). Fixes the false-positive where you were marked as not having replied to a thread you had clearly replied to. (`app/integrations/slack_search.py`)
- **`/wrike` task list chunked** the same way as briefing sections.

### Why & tradeoffs

- *Why buttons instead of typed yes/no?* The text approach required the LLM to (a) interpret arbitrary user responses ("yep", "sure", "go ahead") and (b) re-emit the exact same tool args on the second turn. Buttons eliminate both ambiguities and remove an LLM round-trip from the write path. The Slack action handler dispatches the tool directly, deterministically.
- *Tradeoff: the button value field has a 2000-char limit.* Our tool args are well under that for now. If we ever exceed it (e.g. a long Wrike comment), we fall back to truncated payload with a `_truncated` flag and let the handler refuse cleanly.
- *Why one big approval card, not per-tool?* The card is generic in `value` JSON — any future write tool gets approval support for free. Adding a new write tool requires no UI changes.
- *Why fallback `/wrike` thread replies to the agent (instead of building a richer scheduler)?* Cheaper. The conversational agent already knows how to call `update_wrike_task_status` and `post_wrike_task_comment` with approval flow. The fallback only needs to inject the task list as context — five lines of code.
- *Tradeoff: the heuristic for "non-scheduling intent" is keyword-based, not LLM-judged.* Could mis-route in edge cases ("update my Friday 2pm meeting" has both action and schedule keywords). Acceptable for v1; if false routes become common, we can run a cheap Haiku call to classify intent.
- *Why permalink resolution for Wrike URLs?* The user pastes URLs naturally and our previous code tried to use the numeric ID directly with the Wrike API, which always 400'd. Permalink lookup is one extra HTTP call that wins us full URL paste support.
- *Why store working hours in our DB instead of fetching from Google?* Google Calendar's Working Hours feature is UI-only — `users.settings.list()` doesn't return it. Storing it ourselves is the only working option today; we expose set/get as agent tools so the user sets it via natural language.
- *Tradeoff: working hours window is a single Mon–Sun range.* Users with truly different weekday vs Friday hours can't model that. Acceptable for the current single user; a future change adds per-day storage.

---

## [1.3.0] — 2026-05-10

Latency optimization. Big perceived speed-up.

### Added

- **Streaming responses.** Anthropic SDK's `messages.stream()` yields text deltas; the handler debounces `chat.update` calls at ~700ms with a `▌` cursor. The user sees text appearing within ~1.5s instead of waiting for the full reply. `finalize()` removes the cursor when done. (`app/agent/runner.py`, `app/slack_app/handlers.py` — new `SlackStreamUpdater` class)
- **Anthropic prompt caching.** System prompt and tool definitions are marked `cache_control: {type: "ephemeral"}`. Warm-cache turns cost ~10% of the input-token charge and return faster. (`app/agent/runner.py`)
- **Markdown→Slack mrkdwn post-processor.** Defensive `to_slack_mrkdwn()` converts `**bold**`→`*bold*`, `## H`→`*H*`, `[text](url)`→`<url|text>`, `- item`→`• item`, with code-block protection. The system prompt also explicitly tells the model to output mrkdwn. (`app/utils/slack_mrkdwn.py`)

### Changed

- **System prompt tightened from ~3000 to ~1200 tokens.** Removed redundant scope blocks, trimmed verbose examples to the four essentials, added a *Be Decisive* section nudging the model toward "one tool, one answer". (`app/agent/prompts.py`)
- **`max_tool_iterations` default reduced 8 → 3.** Bounds the worst-case latency. Most queries answer in one tool call + one text turn; chained exploratory tool use is rare and usually harmful.
- **Phoenix span attributes include `cache_read` / `cache_create` token counts** per iteration, so the savings show up in traces.

### Why & tradeoffs

- *Why all three at once?* They reinforce each other. Streaming hides latency. Prompt caching reduces it. Iteration cap bounds the tail. Done piecemeal, the perceived win is much smaller.
- *Tradeoff: occasional Slack rate-limit collisions during heavy streaming.* The debouncer at 700ms is well under Slack's 1/sec cap. We catch the error and continue if it ever fires.
- *Tradeoff: cap=3 means a rare complex query (multi-step debugging) gets cut off mid-loop.* Users who hit the cap get a clear "I hit my tool-use limit" message. P50 wall-clock down ~40%, P99 perceived latency down ~80% — worth the rare cut-off.
- *Why ephemeral cache (not 1h/24h)?* Ephemeral (5-min TTL) is what Anthropic supports and is sufficient for the conversational pattern. The cache is keyed by the prefix's exact byte sequence; our system prompt + tools are stable across turns.

---

## [1.2.0] — 2026-05-10

Restricted scope + DM-only architecture + two-phase approval pattern.

### Added

- **Two-phase approval flow.** Every write tool (`create_calendar_event`, `update_calendar_event`, `update_wrike_task_status`, `post_wrike_task_comment`) accepts `confirmed: bool`. Call with `confirmed=false` returns a preview. The model relays the preview to the user; only after explicit user approval does the model re-call with `confirmed=true`. *(Later replaced by buttons in v1.4.0.)*
- **DM-only architecture.** The bot only listens inside its own Messages tab. Slash commands invoked from a channel get an ephemeral "📬 I sent you a DM" notice; all conversation happens in the DM. (`app/slack_app/handlers.py`)
- **`/connect` card auto-cleanup.** When all three OAuth integrations are connected, the connect card updates to "✅ All set!" and self-deletes 20s later. Tracked via two new columns on `User`: `connect_card_channel_id`, `connect_card_ts`. (`app/db/models.py`, `app/oauth/server.py`)
- **Restricted agent tool surface.** Code-level enforcement: only Calendar add/update (no delete), Wrike status + comments (no task creates/deletes), Slack read-only. The system prompt repeats these rules but the *real* enforcement is which functions exist in `app/agent/tools.py`. (`app/agent/tools.py`, `app/agent/prompts.py`)
- **Rolling-summary context compaction** for long agent threads. When `agent_history` grows past ~12 turns, the older half is summarized by Haiku into a single "Earlier conversation summary" message; recent turns stay verbatim. (`app/sessions/store.py`)

### Changed

- **Calendar `update_calendar_event`** added (PATCH-style); **`delete_calendar_event` removed** from the tool surface — the agent can change events but never delete them. The user must delete via Google Calendar UI.
- **Slack manifest** has `messages_tab_read_only_enabled: false` so the Messages tab accepts user input.

### Why & tradeoffs

- *Why approval-gated writes at all?* Trust gradient — a user shouldn't wake up to find the bot did something destructive. Every write is explicit consent. Even if the LLM is jailbroken, it can't bypass the `confirmed=false` → preview → re-call dance.
- *Why enforce scope in code, not just prompt?* A prompt-only rule is bypassable. By not exposing a `delete_calendar_event` function at all, even a jailbreak can't delete events — the tool doesn't exist in the runtime.
- *Tradeoff: scope feels narrow.* Users will ask "can you delete this event?" and get refused. Acceptable; the bot's purpose is augmentation, not unattended automation. If we widen scope later, each addition is a deliberate decision, not an accident.
- *Why DM-only?* Channel bots are noisy and the bot can't always be sure who's authorized for what. DM puts the user firmly in control of their own data.
- *Why connect-card cleanup?* The connect card has buttons that take you outside Slack. Once all three are connected, the card is dead clutter. Auto-deleting after a celebration message gives a clear "done" signal without leaving a tombstone.

---

## [1.1.0] — 2026-05-10

Bug fixes from the first deploy. The big "make it actually work in Cloud Run" release.

### Fixed

- **`'AsyncSession' object has no attribute 'exec'`.** SQLModel adds `.exec()` on its `AsyncSession` subclass; we had imported the SQLAlchemy base. Switched the session factory to `sqlmodel.ext.asyncio.session.AsyncSession`. (`app/db/engine.py`)
- **`can't subtract offset-naive and offset-aware datetimes`** in Wrike + Google Calendar token-expiry checks. SQLite drops tzinfo on read; the comparison failed. Added `if x.tzinfo is None: x = x.replace(tzinfo=UTC)` defensively at both call sites. (`app/integrations/wrike.py`, `app/integrations/google_calendar.py`)
- **Postgres `TIMESTAMP WITHOUT TIME ZONE` rejecting tz-aware datetimes** on INSERT. Models declared bare `datetime`, which SQLModel maps to `WITHOUT TIME ZONE`; code passed `datetime.now(UTC)`. Added `sa_type=DateTime(timezone=True)` to every datetime column. (`app/db/models.py`)
- **Bolt OAuth distribution mode auto-enabling and breaking single-tenant operation.** When `SLACK_CLIENT_ID` + `SLACK_CLIENT_SECRET` are in env, Bolt switches to file-based InstallationStore and ignores the static bot token. Every event fails with "AuthorizeResult not found". Fix: `os.environ.pop()` both env vars while constructing `AsyncApp`, then restore. The OAuth callback server reads them via `settings.*` after that, unaffected. (`app/slack_app/app.py`)
- **OAuth `invalid_client` / `Invalid client_id` / `unauthorized_client`** from all three providers (Slack, Google, Wrike). `cloudbuild.yaml` tried `--set-env-vars=...,GOOGLE_CLIENT_ID=$$GOOGLE_CLIENT_ID_VAL,...` with build-time `secretEnv`. But `entrypoint: gcloud` + `args: [...]` calls gcloud without a shell, so `$$VAR` was passed literally. Moved all three CLIENT_IDs to `--update-secrets` like everything else. (`cloudbuild.yaml`)
- **Cloud Run container killed mid-request (~9s in).** Socket Mode + default CPU throttling + max-instances=2 = unreliable websocket and event splitting across replicas. Added `--no-cpu-throttling` + pinned `min-instances=1 max-instances=1`. (`cloudbuild.yaml`)
- **Cloud Build: `invalid image name "...:..."`.** `$SHORT_SHA` is only populated for Git triggers; local-source submits leave it empty. Switched to `$BUILD_ID`. (`cloudbuild.yaml`)
- **Cloud Build: `denied ... gcr.io/google.cloud-sdk/cloud-sdk`.** That path doesn't exist (project name typo). Canonical image is `gcr.io/google.com/cloudsdktool/cloud-sdk:slim`. (`cloudbuild.yaml`)
- **`Permission 'secretmanager.versions.access' denied` for Cloud Build SA.** Newer GCP projects (created after Google's 2024 change) run builds as the Compute Engine default SA, not the legacy `<PROJECT>@cloudbuild.gserviceaccount.com`. Updated `03-create-artifact-registry.sh` to grant the required roles to BOTH SAs. (`infrastructure/03-create-artifact-registry.sh`)
- **Phoenix `401 Unauthorized`.** Two compounding issues: we were sending both `api_key` and `Authorization: Bearer` headers manually, and an "endpoint normalizer" stripped the `/s/<space>` path from the URL. Now we use `phoenix.otel.register()`'s built-in `api_key=` parameter (it sets the right header) and only append `/v1/traces` if missing — `/s/<space>` is preserved. (`app/observability.py`)
- **Wrike API rejects `fields=[customStatuses, dueDate, permalink, ...]`.** These aren't valid optional fields on the LIST endpoint. Reduced fields list to `["responsibleIds", "description"]`. The default response already includes `customStatusId` and `dates`; `permalink` is reconstructed via `task_permalink()` fallback URL.
- **`/goodmorning` Wrike section empty even with tasks.** Wrike workspaces have multiple workflows, each with its own "New" status (different IDs). Code resolved only the first match. `resolve_status_ids()` now returns ALL matching IDs across all workflows; cache is multi-row. (`app/integrations/wrike.py`)
- **Slack `invalid_blocks: must be less than 3001 characters`** in `/goodmorning`. Many unreplied mentions or Wrike tasks overflow a single section block. Added `_section_chunks()` that splits on line boundaries into multiple ≤2900-char blocks. (`app/formatters/briefing.py`)
- **`/connect` slash command timing out.** Container had crashed during startup due to one of the above; surfaced as "app did not respond" within 3s. Resolved by the OAuth-mode + env-var fixes.
- **`02-create-secrets.sh` couldn't find `infrastructure-secrets.env`.** Script `cd`'d into `infrastructure/` then looked for the file in the wrong directory. Resolved `PROD_ROOT` properly so the default path is `${PROD_ROOT}/infrastructure-secrets.env`. (`infrastructure/02-create-secrets.sh`)

### Changed

- **`/goodmorning` Wrike status from "Now" → "New".** Per workspace's actual status name. (`app/slack_app/commands/goodmorning.py`)

### Why & tradeoffs

- *Why `min=max=1` Cloud Run replica?* Socket Mode is a single outbound websocket. Two replicas = Slack delivers a given event to one of them, but our reply might come from the other. Plus default CPU throttling kills the websocket between requests. The settings codify the "single always-on replica" design.
- *Tradeoff: ~25–30% higher Cloud Run cost vs. throttled.* Worth it for Socket Mode reliability. Without it, the bot is non-deterministically broken.
- *Why grant Cloud Build roles to both legacy + new compute SA?* Future-proof against Google's SA-model change continuing to evolve. Both grants are harmless idempotent operations.
- *Tradeoff with `DateTime(timezone=True)` everywhere.* SQLite ignores the timezone hint and stores naive strings; Postgres respects it. We added defensive `if tzinfo is None` guards on the SQLite read path so the same code works on both backends. Means more code but supports the dev (SQLite) → prod (Postgres) workflow.

---

## [1.0.0] — 2026-05-09

Initial production deployment.

### Added

- **Three slash commands**: `/connect` (per-user OAuth: Google + Wrike + Slack user-token), `/goodmorning` (daily briefing — unreplied mentions, Wrike tasks, calendar), `/wrike` (multi-turn flow to plot Wrike tasks onto the calendar with overlap detection).
- **Conversational agent** in DM. Anthropic Claude (Sonnet 4.6 default, Haiku 4.5 for entity extraction). Tool-use loop with a custom tool surface; the agent doesn't directly post Slack messages — the handler does.
- **Cloud Run + Cloud SQL Postgres + Phoenix Cloud + Secret Manager** infrastructure. Single-container design: Slack Bolt (Socket Mode) and FastAPI (OAuth callbacks) on one asyncio event loop. Cloud SQL Auth Proxy via Unix socket; no public IP.
- **Per-user OAuth + token storage**. Google, Wrike, and Slack-user (xoxp) tokens stored encrypted with Fernet, keyed by `(slack_team_id, slack_user_id)`. Auto-refresh on expiry.
- **Per-thread conversation memory.** Each `(channel_id, thread_ts)` pair gets a `ConversationSession` row holding agent history + any active command state machine.
- **Wrike workflow status cache** so we resolve "New" → custom_status_id once per (user, status_name) and don't hit `/workflows` on every command call.
- **Phoenix tracing.** OpenInference Anthropic instrumentor captures every model call automatically; we add custom spans on tools, commands, and the overall agent turn. Each span carries `user.id`, `user.name`, `user.email`, `session.id`, channel, thread.
- **Infrastructure scripts** in `prod/infrastructure/`: numbered shell scripts that enable APIs, create Cloud SQL, upload secrets, create Artifact Registry + service accounts, build + deploy, rotate secrets, and tear down.
- **Two Word docs** in `prod/docs/`: a local-dev guide and a production deployment guide for someone non-technical to follow.

### Why & tradeoffs

- *Cloud Run over a VM.* Managed HTTPS, painless rollback, no Linux ops. Costs ~$15/mo more than a free-tier `e2-micro` GCE VM. Worth it for a team deployment where unattended reliability matters.
- *Cloud SQL Postgres over Firestore.* SQLModel works locally (SQLite) and in prod (asyncpg) with the same code. Firestore would require a NoSQL rewrite of every query. Cloud SQL `db-f1-micro` is cheapest tier; ~$8/mo.
- *Fernet encryption (symmetric).* We're the only consumer of the keys. KMS-backed envelope encryption is the next step if/when we get security review. For now, the Fernet key in Secret Manager is "good enough" — losing it would invalidate every stored OAuth refresh token, forcing users to re-`/connect`.
- *Commands as deterministic Python flows, not agent loops.* `/goodmorning` and `/wrike` use the LLM only for entity extraction (slot-filling). The actual work is hand-coded Python. The conversational agent has the LLM-in-the-loop, but with a restricted tool surface. This split keeps deterministic things deterministic and reserves LLM judgment for free-form chat.
- *Wrike via REST + OAuth, not via an MCP.* The Wrike MCP available inside Claude.ai runs on Anthropic's MCP host and isn't callable from a self-hosted bot. No officially self-hostable Wrike MCP exists. Direct REST is the production-correct pattern for multi-user OAuth anyway.
- *Single-tenant deployment.* Tied to one Slack workspace (`opensailgroup`). Multi-tenant would require per-workspace data isolation everywhere. Acceptable for our scale; documented as a known limit.
- *Manual deploys via `04-build-and-deploy.sh` from a laptop.* CI/CD from GitHub is scaffolded (`06-create-cicd-trigger.sh`) but not wired. Trade-off: every deploy is a deliberate action; downside is no PR-driven preview environments.

---

## A note on what's NOT versioned here

- **Dev tree** (`../app/`, `../scripts/`, etc.) — local-only, not committed to git, intentionally not in this changelog.
- **Word docs in `docs/`** — regenerable from `scripts/build_prod_guide.py`; versioned alongside the source they describe.
- **`claude.md`** — meta-context for future contributors / AI sessions, gitignored, not in this file.
- **Infrastructure secrets** — values in `infrastructure-secrets.env` are gitignored; rotations are operational, not changelog-worthy.

---

## How to update this file

When you ship a non-trivial change to `prod/`:

1. Decide if it's `[Added]`, `[Changed]`, `[Fixed]`, `[Deprecated]`, `[Removed]`, or `[Security]`.
2. Add a new `[x.y.z] — YYYY-MM-DD` section at the top, *under `[Unreleased]`*.
3. Write the **what**: 1–3 sentences per change. Include file paths so a reader can locate the code.
4. Write the **Why & tradeoffs** section. What problem did this solve? What did we give up? Future contributors will thank you.
5. Bump the version: minor for new behavior, patch for bug fixes only.

If the change isn't worth a version (typo fix, doc tweak), it doesn't go here.
