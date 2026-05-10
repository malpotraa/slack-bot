"""Generate `Slack_Assistant_Setup_Guide.docx` — the full setup walkthrough.

Run with:  uv run python scripts/build_setup_guide.py
Output:    docs/Slack_Assistant_Setup_Guide.docx
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor, Cm

OUT_DIR = Path(__file__).resolve().parent.parent / "docs"
OUT_PATH = OUT_DIR / "Slack_Assistant_Setup_Guide.docx"


# ── Style helpers ──────────────────────────────────────────────────────────


def _shade_cell(cell, fill_hex: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)
    tc_pr.append(shd)


def add_title(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = p.add_run(text)
    run.font.size = Pt(28)
    run.bold = True
    run.font.color.rgb = RGBColor(0x1F, 0x29, 0x37)


def add_subtitle(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(13)
    run.italic = True
    run.font.color.rgb = RGBColor(0x55, 0x65, 0x75)


def add_h1(doc: Document, text: str) -> None:
    h = doc.add_heading(text, level=1)
    for r in h.runs:
        r.font.color.rgb = RGBColor(0x11, 0x18, 0x27)


def add_h2(doc: Document, text: str) -> None:
    h = doc.add_heading(text, level=2)
    for r in h.runs:
        r.font.color.rgb = RGBColor(0x1F, 0x29, 0x37)


def add_h3(doc: Document, text: str) -> None:
    h = doc.add_heading(text, level=3)
    for r in h.runs:
        r.font.color.rgb = RGBColor(0x37, 0x41, 0x51)


def add_para(doc: Document, text: str, *, italic: bool = False, bold: bool = False) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.italic = italic
    run.bold = bold
    run.font.size = Pt(11)


def add_rich_para(doc: Document, parts: list[tuple[str, dict]]) -> None:
    """Mixed-formatting paragraph: list of (text, {bold:..., italic:..., code:...})."""
    p = doc.add_paragraph()
    for text, opts in parts:
        run = p.add_run(text)
        run.font.size = Pt(11)
        if opts.get("bold"):
            run.bold = True
        if opts.get("italic"):
            run.italic = True
        if opts.get("code"):
            run.font.name = "Consolas"
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(0x86, 0x19, 0x54)


def add_bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        p = doc.add_paragraph(item, style="List Bullet")
        for run in p.runs:
            run.font.size = Pt(11)


def add_numbered(doc: Document, items: list[str]) -> None:
    for item in items:
        p = doc.add_paragraph(item, style="List Number")
        for run in p.runs:
            run.font.size = Pt(11)


def add_code(doc: Document, code: str, *, language: str = "") -> None:
    """Code block: single-cell shaded table with monospace text."""
    table = doc.add_table(rows=1, cols=1)
    table.autofit = True
    cell = table.cell(0, 0)
    cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
    _shade_cell(cell, "F3F4F6")
    p = cell.paragraphs[0]
    run = p.add_run(code)
    run.font.name = "Consolas"
    run.font.size = Pt(9.5)
    run.font.color.rgb = RGBColor(0x11, 0x18, 0x27)
    if language:
        cap = doc.add_paragraph()
        cap_run = cap.add_run(f"  ({language})")
        cap_run.italic = True
        cap_run.font.size = Pt(9)
        cap_run.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)


def add_callout(doc: Document, title: str, body: str, *, kind: str = "note") -> None:
    """Single-cell shaded box for tips, warnings, notes."""
    palette = {
        "note": ("EFF6FF", "1E40AF"),
        "tip": ("ECFDF5", "065F46"),
        "warn": ("FEF3C7", "92400E"),
        "danger": ("FEE2E2", "991B1B"),
    }
    fill, text_color = palette.get(kind, palette["note"])
    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    _shade_cell(cell, fill)
    p = cell.paragraphs[0]
    label_run = p.add_run(f"{title.upper()}  ")
    label_run.bold = True
    label_run.font.size = Pt(10)
    label_run.font.color.rgb = RGBColor.from_string(text_color)
    body_run = p.add_run(body)
    body_run.font.size = Pt(10.5)
    body_run.font.color.rgb = RGBColor(0x1F, 0x29, 0x37)


def add_inline_code(doc: Document, label: str, code: str) -> None:
    """A label followed by a code-styled value, e.g. for inline keys."""
    p = doc.add_paragraph()
    r1 = p.add_run(label + " ")
    r1.font.size = Pt(11)
    r2 = p.add_run(code)
    r2.font.name = "Consolas"
    r2.font.size = Pt(10)
    r2.font.color.rgb = RGBColor(0x86, 0x19, 0x54)


def page_break(doc: Document) -> None:
    doc.add_page_break()


# ── Sections ───────────────────────────────────────────────────────────────


def section_cover(doc: Document) -> None:
    add_title(doc, "Slack Assistant — Complete Setup Guide")
    add_subtitle(
        doc,
        "Multi-user Slack-native AI assistant with Google Calendar + Wrike integrations, "
        "built on Claude (Anthropic) with Arize Phoenix observability.",
    )
    add_para(doc, "Project: ai-agent")
    add_para(doc, "Audience: developer setting up the assistant locally and deploying to GCP.")
    add_para(doc, "Document version: 1.0 (May 2026)")
    page_break(doc)


def section_overview(doc: Document) -> None:
    add_h1(doc, "1. Overview")
    add_para(
        doc,
        "This document walks you end-to-end: from a fresh laptop to a fully working "
        "Slack-native AI assistant with Google Calendar and Wrike integrations, plus "
        "production deployment on Google Cloud Run.",
    )

    add_h2(doc, "What the assistant does")
    add_bullets(
        doc,
        [
            "Free-form chat: open a DM with the bot from your Slack sidebar (the app's "
            "Messages tab) and ask anything. The assistant answers using Claude with "
            "read-only Calendar and Wrike tools.",
            "/connect: per-user OAuth flow — run this first to link your Google Calendar, "
            "Wrike, and Slack search permissions.",
            "/goodmorning: daily briefing with unreplied @-mentions from the last 7 days, "
            "Wrike tasks in the 'Now' status, tasks due in the next 48 hours, and a "
            "calendar summary for today (busy/free time and the first open block).",
            "/wrike: lists Wrike tasks in the 'New' status, then runs a multi-turn "
            "conversation to plot one onto your Google Calendar with overlap detection.",
        ],
    )

    add_h2(doc, "Where the bot lives")
    add_para(
        doc,
        "All bot conversation happens inside the Slack app itself — the 'Messages' tab "
        "you reach by clicking the bot in your Slack sidebar. The bot does not read or "
        "post in any channels and does not respond to @-mentions in channels. Slash "
        "commands work from anywhere (any channel or DM), but their replies always land "
        "in your DM with the bot, keeping the conversation private and consolidated.",
    )

    add_h2(doc, "What you'll need to provision")
    add_bullets(
        doc,
        [
            "A Slack workspace where you have admin rights to install custom apps.",
            "A Google Cloud project (free tier is fine for development).",
            "A Wrike account with permission to create OAuth apps.",
            "An Anthropic API account.",
            "(Optional) Docker for running Phoenix locally; or sign up at phoenix.arize.com.",
            "(For production) A Google Cloud project with billing enabled.",
        ],
    )

    add_h2(doc, "Reading order")
    add_para(
        doc,
        "Sections 2–7 walk you through local setup. Section 8 covers the first-run user "
        "experience. Section 9 covers GCP deployment. Section 10 lists common issues. "
        "Section 11 has next-step ideas.",
    )
    page_break(doc)


def section_local_setup(doc: Document) -> None:
    add_h1(doc, "2. Local Project Setup")

    add_h2(doc, "2.1 Install uv (Python toolchain)")
    add_para(
        doc,
        "uv manages Python versions, virtualenvs, and dependencies in one tool. "
        "It is faster and simpler than pip + venv + pyenv.",
    )
    add_code(doc, "brew install uv", language="bash")
    add_para(doc, "Verify the install:")
    add_code(doc, "uv --version", language="bash")

    add_h2(doc, "2.2 Install project dependencies")
    add_para(doc, "From the project root (where pyproject.toml lives):")
    add_code(doc, "uv sync", language="bash")
    add_para(
        doc,
        "This creates a .venv, installs all runtime dependencies (slack-bolt, "
        "claude-agent-sdk, anthropic, fastapi, sqlmodel, google-api-python-client, "
        "httpx, arize-phoenix-otel, openinference instrumentors, etc.) and dev tools.",
    )

    add_h2(doc, "2.3 Generate secrets")
    add_para(
        doc,
        "Two secrets are required: APP_SECRET_KEY (signs OAuth state tokens) and "
        "TOKEN_ENCRYPTION_KEY (encrypts stored OAuth refresh tokens with Fernet).",
    )
    add_code(doc, "uv run python scripts/gen_keys.py", language="bash")
    add_para(doc, "Sample output (yours will differ):")
    add_code(
        doc,
        "APP_SECRET_KEY=iXphu-Sj4p-Gi907NxcLJCF4-AFbRmIXLliU-uSTJBUGxd8ZPa20V-N83MOIIVAP\n"
        "TOKEN_ENCRYPTION_KEY=Q3XiY-Ao0g7WeBY9IxXN5p5aDkHvYekieasZ35RKx9s=",
    )
    add_callout(
        doc,
        "Important",
        "Keep TOKEN_ENCRYPTION_KEY safe. If you rotate or lose it, every user has "
        "to re-OAuth all their accounts because their stored tokens become unreadable.",
        kind="warn",
    )

    add_h2(doc, "2.4 Create .env from the template")
    add_code(doc, "cp .env.example .env", language="bash")
    add_para(
        doc,
        "Open .env in your editor. You'll fill it in as you complete the next sections "
        "(Slack, Google, Wrike, Anthropic). For now, paste in the two keys you generated:",
    )
    add_code(
        doc,
        "APP_SECRET_KEY=<paste from gen_keys.py>\nTOKEN_ENCRYPTION_KEY=<paste from gen_keys.py>",
    )

    add_h2(doc, "2.5 Initialize the database")
    add_para(
        doc,
        "The project uses SQLite locally (data/app.db). The schema is created on first boot, "
        "but you can also create it manually:",
    )
    add_code(doc, "uv run python -m scripts.init_db", language="bash")
    add_para(doc, "You should see:")
    add_code(doc, "DB ready at sqlite+aiosqlite:///.../data/app.db")

    add_h2(doc, "2.6 Project layout")
    add_para(doc, "The codebase is organized like this:")
    add_code(
        doc,
        "app/\n"
        "├── main.py                  # entrypoint (Bolt + FastAPI in one process)\n"
        "├── config.py                # pydantic-settings\n"
        "├── observability.py         # Arize Phoenix / OpenTelemetry\n"
        "├── logging_setup.py         # loguru\n"
        "├── db/                      # SQLModel tables + Fernet token crypto\n"
        "├── oauth/                   # Google, Wrike, Slack-user OAuth + FastAPI server\n"
        "├── slack_app/               # Bolt app, handlers, slash commands\n"
        "├── integrations/            # GCal, Wrike REST, Slack search clients\n"
        "├── agent/                   # Conversational agent runner + tools\n"
        "├── llm/                     # Structured LLM extraction (slot-filling)\n"
        "├── sessions/                # Per-thread session store\n"
        "├── formatters/              # Block Kit builders for /goodmorning, /wrike\n"
        "└── utils/                   # Timezone, working hours / gap-finder",
    )
    page_break(doc)


def section_slack_app(doc: Document) -> None:
    add_h1(doc, "3. Slack App — Create, Install, Configure")
    add_para(
        doc,
        "The assistant uses two Slack auth modes simultaneously:",
    )
    add_bullets(
        doc,
        [
            "Bot token (xoxb-…): for posting messages, reading channels, listening to events.",
            "User token (xoxp-…): per-user, granted via /connect; used by /goodmorning to "
            "search the user's mentions across all channels they're in. Bot tokens cannot "
            "call search.messages, so this is required.",
        ],
    )

    add_h2(doc, "3.1 Create the app from manifest")
    add_numbered(
        doc,
        [
            "Go to https://api.slack.com/apps and click 'Create New App'.",
            "Choose 'From an app manifest'.",
            "Select your workspace.",
            "Switch the manifest tab to YAML and paste the manifest below, then click Next, "
            "then Create.",
        ],
    )
    add_callout(
        doc,
        "Important",
        "The bot is DM-only. It does not read or post in channels and does not respond "
        "to channel @-mentions. The manifest below uses the minimum scopes required for "
        "this design.",
        kind="note",
    )
    add_code(
        doc,
        "display_information:\n"
        "  name: Work Assistant\n"
        "features:\n"
        "  bot_user:\n"
        "    display_name: Work Assistant\n"
        "    always_online: true\n"
        "  slash_commands:\n"
        "    - command: /connect\n"
        "      description: Connect Google Calendar and Wrike to the assistant\n"
        "      usage_hint: \"Run this first\"\n"
        "    - command: /goodmorning\n"
        "      description: Daily briefing\n"
        "    - command: /wrike\n"
        "      description: Schedule Wrike 'New' tasks onto your calendar\n"
        "oauth_config:\n"
        "  redirect_urls:\n"
        "    - http://localhost:8000/oauth/slack/callback\n"
        "  scopes:\n"
        "    # User scopes (xoxp-): only for /goodmorning's mention search.\n"
        "    user:\n"
        "      - search:read\n"
        "      - channels:history\n"
        "      - groups:history\n"
        "      - im:history\n"
        "      - mpim:history\n"
        "      - users:read\n"
        "    # Bot scopes (xoxb-): DM only.\n"
        "    bot:\n"
        "      - chat:write\n"
        "      - commands\n"
        "      - im:history\n"
        "      - im:read\n"
        "      - im:write\n"
        "      - reactions:read\n"
        "      - reactions:write\n"
        "      - users:read\n"
        "      - users:read.email\n"
        "settings:\n"
        "  event_subscriptions:\n"
        "    bot_events:\n"
        "      - message.im       # bot only listens inside its own DM\n"
        "  interactivity:\n"
        "    is_enabled: true\n"
        "  socket_mode_enabled: true\n"
        "  org_deploy_enabled: false",
        language="yaml",
    )

    add_h2(doc, "3.2 Install the app to your workspace")
    add_numbered(
        doc,
        [
            "In the app settings, go to 'OAuth & Permissions'.",
            "Click 'Install to <your workspace>'.",
            "Approve the requested scopes.",
            "After install, copy the 'Bot User OAuth Token' — it starts with 'xoxb-'. "
            "Paste it into .env as SLACK_BOT_TOKEN.",
        ],
    )

    add_h2(doc, "3.3 Get the App-Level token (for Socket Mode)")
    add_numbered(
        doc,
        [
            "Go to 'Basic Information' → 'App-Level Tokens' → 'Generate Token and Scopes'.",
            "Name it 'socket-mode'. Add the scope 'connections:write'. Click Generate.",
            "Copy the token — it starts with 'xapp-'. Paste it into .env as SLACK_APP_TOKEN.",
        ],
    )

    add_h2(doc, "3.4 Get the Signing Secret + Client credentials")
    add_numbered(
        doc,
        [
            "On 'Basic Information', scroll to 'App Credentials'.",
            "Copy 'Signing Secret' → SLACK_SIGNING_SECRET in .env.",
            "Copy 'Client ID' → SLACK_CLIENT_ID in .env.",
            "Copy 'Client Secret' → SLACK_CLIENT_SECRET in .env.",
        ],
    )

    add_h2(doc, "3.5 Confirm Socket Mode is on")
    add_para(
        doc,
        "On the 'Socket Mode' page, the toggle should be ON. The manifest sets this for you, "
        "but verify after install. Socket Mode means the bot connects outbound to Slack — "
        "you don't need a public URL or ngrok for development.",
    )

    add_h2(doc, "3.6 Open the bot's DM in your Slack sidebar")
    add_para(
        doc,
        "There is nothing to invite to channels — by design, the bot only operates inside "
        "its own DM. To start chatting:",
    )
    add_numbered(
        doc,
        [
            "In Slack, click 'Apps' in the left sidebar (or use Cmd/Ctrl+K and type the bot name).",
            "Click 'Work Assistant' to open the app.",
            "Click the 'Messages' tab.",
            "This DM is where /connect, /goodmorning, /wrike, and free-form chat all happen.",
        ],
    )

    add_callout(
        doc,
        "Tip",
        "If you change scopes later, you must reinstall the app for the new permissions to "
        "take effect (OAuth & Permissions → Reinstall to Workspace).",
        kind="tip",
    )
    page_break(doc)


def section_google(doc: Document) -> None:
    add_h1(doc, "4. Google Calendar — OAuth Setup")
    add_para(
        doc,
        "The assistant reads and writes events on each user's primary Google Calendar. "
        "Google requires you to register an OAuth 2.0 client and host a redirect URI.",
    )

    add_h2(doc, "4.1 Create a Google Cloud project")
    add_numbered(
        doc,
        [
            "Go to https://console.cloud.google.com.",
            "Click the project picker in the top bar → 'New Project'.",
            "Name it 'slack-assistant' (or whatever you prefer). Note the Project ID.",
        ],
    )

    add_h2(doc, "4.2 Enable the Google Calendar API")
    add_numbered(
        doc,
        [
            "In the search bar, type 'Calendar API' and select 'Google Calendar API'.",
            "Click 'Enable'.",
        ],
    )

    add_h2(doc, "4.3 Configure the OAuth consent screen")
    add_numbered(
        doc,
        [
            "In the left nav: 'APIs & Services' → 'OAuth consent screen'.",
            "User Type: 'External' (unless you have Google Workspace and want 'Internal').",
            "Fill in the required fields: app name = 'Slack Assistant', support email = "
            "your email, developer contact email = your email.",
            "Click Save and Continue.",
            "Scopes step: click 'Add or Remove Scopes' and add: "
            ".../auth/calendar, .../auth/userinfo.email, openid. Save and continue.",
            "Test users step: add the email addresses of every user who will OAuth during "
            "development (you, your teammates). 'External' apps in 'Testing' status only "
            "let test users authorize. Save and continue.",
            "Review the summary, then 'Back to dashboard'.",
        ],
    )
    add_callout(
        doc,
        "Note",
        "While the app is in 'Testing' mode, only the test users you listed can sign in, "
        "and refresh tokens expire after 7 days. For production, you'll publish the app — "
        "see Section 9.",
        kind="note",
    )

    add_h2(doc, "4.4 Create the OAuth 2.0 Client ID")
    add_numbered(
        doc,
        [
            "Left nav: 'APIs & Services' → 'Credentials'.",
            "'Create Credentials' → 'OAuth client ID'.",
            "Application type: 'Web application'. Name: 'Slack Assistant'.",
            "Authorized redirect URIs: add http://localhost:8000/oauth/google/callback "
            "(for local dev). You'll add the production URL later (Section 9).",
            "Click Create.",
        ],
    )
    add_para(
        doc,
        "Copy the Client ID and Client Secret. Paste them into .env as GOOGLE_CLIENT_ID "
        "and GOOGLE_CLIENT_SECRET.",
    )

    add_h2(doc, "4.5 Verify the setup")
    add_para(
        doc,
        "After all four are filled in (.env updated, Slack app installed, Google "
        "consent screen + client ID created), you'll be able to test /connect. "
        "Don't run the assistant yet — finish Wrike and Anthropic first.",
    )
    page_break(doc)


def section_wrike(doc: Document) -> None:
    add_h1(doc, "5. Wrike — OAuth App Setup")

    add_callout(
        doc,
        "Architecture note",
        "We use Wrike's REST API with OAuth 2.0, not Wrike MCP. The Wrike MCP server "
        "available inside Claude.ai runs on Anthropic's MCP host and is not callable from "
        "an external deployment. For a multi-user, self-hosted Slack assistant, OAuth "
        "REST is the production-correct pattern: each user authorizes once, tokens are "
        "encrypted at rest, and refresh happens automatically.",
        kind="note",
    )

    add_h2(doc, "5.1 Create a Wrike OAuth 2.0 application")
    add_numbered(
        doc,
        [
            "Log into Wrike with an account that has permission to create apps.",
            "Open https://www.wrike.com/frame/oauth2/apps in your browser.",
            "Click 'Create new app'.",
            "Fill in: name = 'Slack Assistant'. (Description and logo are optional.)",
            "Click 'Save'.",
        ],
    )

    add_h2(doc, "5.2 Configure the redirect URI")
    add_numbered(
        doc,
        [
            "On the app's detail page, find the 'Redirect URIs' field.",
            "Add: http://localhost:8000/oauth/wrike/callback for local development.",
            "Save.",
        ],
    )

    add_h2(doc, "5.3 Set permissions")
    add_para(
        doc,
        "Wrike OAuth apps grant a fixed set of permissions configured at the app level "
        "(unlike Google, where scopes are requested per authorization). Recommended "
        "permissions:",
    )
    add_bullets(
        doc,
        [
            "Read tasks and folders",
            "Write tasks (for future task-creation features)",
            "Read user info (so we can map Slack user → Wrike contact via email)",
            "Read workflows (so we can resolve custom statuses like 'New' and 'Now' to IDs)",
        ],
    )
    add_callout(
        doc,
        "Tip",
        "If 'Write tasks' isn't strictly required for v1 (the assistant currently only "
        "reads tasks and writes Calendar events), you can skip it and add it later. The "
        "principle of least privilege applies.",
        kind="tip",
    )

    add_h2(doc, "5.4 Copy credentials to .env")
    add_numbered(
        doc,
        [
            "Copy 'Client ID' from the app page → WRIKE_CLIENT_ID in .env.",
            "Click 'Show' next to Client Secret → copy → WRIKE_CLIENT_SECRET in .env.",
        ],
    )

    add_h2(doc, "5.5 Confirm 'Now' and 'New' custom statuses exist in your workspace")
    add_para(
        doc,
        "/goodmorning expects a custom status named 'Now' and /wrike expects 'New'. "
        "Most Wrike workspaces have these out of the box, but if your workspace renamed "
        "them (e.g. 'In Progress', 'Backlog'), you have two options:",
    )
    add_bullets(
        doc,
        [
            "Rename one of your statuses to 'New' / 'Now' in Wrike Settings → Workflows.",
            "Or update the constants in the code: app/slack_app/commands/goodmorning.py "
            "and wrike_cmd.py both call resolve_status_id(user_id, '<name>') — change the "
            "string literal to match what your workspace uses.",
        ],
    )
    add_para(
        doc,
        "The first time the assistant queries each status, it caches the (name → ID) "
        "mapping in the workflowstatuscache table to avoid repeated API calls.",
    )
    page_break(doc)


def section_anthropic(doc: Document) -> None:
    add_h1(doc, "6. Anthropic — API Key")

    add_h2(doc, "6.1 Create an account and key")
    add_numbered(
        doc,
        [
            "Sign up at https://console.anthropic.com if you don't have an account.",
            "In the console, go to 'Settings' → 'API Keys'.",
            "Click 'Create Key', name it 'slack-assistant-local'.",
            "Copy the key (starts with sk-ant-). It will only be shown once.",
            "Paste into .env as ANTHROPIC_API_KEY.",
        ],
    )

    add_h2(doc, "6.2 Models used")
    add_bullets(
        doc,
        [
            "AGENT_MODEL: claude-sonnet-4-6 (default for the conversational agent and "
            "tool-use loop). Good balance of cost and capability.",
            "EXTRACTION_MODEL: claude-haiku-4-5-20251001 (used for structured slot "
            "extraction in /wrike). Cheap, fast, accurate at structured JSON output.",
        ],
    )
    add_para(
        doc,
        "You can swap these via .env without code changes. For higher-stakes deployments "
        "consider claude-opus-4-7 for AGENT_MODEL.",
    )

    add_h2(doc, "6.3 Billing")
    add_para(
        doc,
        "Add a payment method or initial credits in 'Settings' → 'Billing'. The free tier "
        "lets you test, but the conversational agent will hit limits quickly under real use.",
    )
    page_break(doc)


def section_phoenix(doc: Document) -> None:
    add_h1(doc, "7. Arize Phoenix — Observability")
    add_para(
        doc,
        "Phoenix shows every LLM call, tool call, and agent loop iteration as a "
        "searchable, replayable trace. It uses OpenTelemetry under the hood, with "
        "OpenInference instrumentors that wrap the Anthropic SDK automatically.",
    )

    add_h2(doc, "7.1 Self-hosted via Docker (recommended for development)")
    add_para(doc, "Run in a separate terminal — Phoenix should be running before you start the assistant:")
    add_code(
        doc,
        "docker run -p 6006:6006 -p 4317:4317 arizephoenix/phoenix:latest",
        language="bash",
    )
    add_bullets(
        doc,
        [
            "Port 6006: Phoenix UI (open http://localhost:6006).",
            "Port 4317: OTel gRPC ingestion (the assistant pushes traces here).",
            "Data lives in the container's volume; restart loses it. For persistence, "
            "mount a volume: -v phoenix-data:/phoenix.",
        ],
    )
    add_para(
        doc,
        ".env defaults already point at http://localhost:6006, so no .env change is needed "
        "for the local-Docker path.",
    )

    add_h2(doc, "7.2 Phoenix Cloud (optional)")
    add_numbered(
        doc,
        [
            "Sign up at https://app.phoenix.arize.com.",
            "Create a project. Note its endpoint URL and your API key.",
            "Update .env: PHOENIX_COLLECTOR_ENDPOINT=https://app.phoenix.arize.com  and  "
            "PHOENIX_API_KEY=<your key>.",
        ],
    )

    add_h2(doc, "7.3 What you'll see in the UI")
    add_bullets(
        doc,
        [
            "command.goodmorning trace: three parallel children for Slack search, Wrike "
            "fetch, and Calendar fetch. Latency breakdown shows which one is slowest.",
            "command.wrike.start + per-turn 'wrike.parse_intent', 'gcal.create_event' "
            "spans. The override_used attribute lets you find every overlap-override.",
            "agent.turn root span per Slack message, with one tool.<name> span per tool "
            "call. The Anthropic instrumentor adds prompt/response/token-count attributes.",
        ],
    )
    page_break(doc)


def section_first_run(doc: Document) -> None:
    add_h1(doc, "8. First Run + User Onboarding")

    add_h2(doc, "8.1 Validate your .env")
    add_para(
        doc,
        "Required keys (the app refuses to start if any are missing):",
    )
    add_bullets(
        doc,
        [
            "APP_SECRET_KEY",
            "TOKEN_ENCRYPTION_KEY",
            "ANTHROPIC_API_KEY",
            "SLACK_SIGNING_SECRET",
            "SLACK_BOT_TOKEN",
            "SLACK_APP_TOKEN",
        ],
    )
    add_para(
        doc,
        "Required for /connect to work end-to-end (warned but not blocked at startup): "
        "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, WRIKE_CLIENT_ID, WRIKE_CLIENT_SECRET, "
        "SLACK_CLIENT_ID, SLACK_CLIENT_SECRET.",
    )

    add_h2(doc, "8.2 Start the assistant")
    add_code(doc, "uv run python -m app.main", language="bash")
    add_para(doc, "You should see a startup log like:")
    add_code(
        doc,
        "INFO     app.observability:configure_observability — Phoenix tracer registered\n"
        "INFO     app.db.engine:init_db — DB ready at sqlite+aiosqlite:///.../data/app.db\n"
        "INFO     app.main:amain — Booting on local | base_url=http://localhost:8000\n"
        "INFO     slack_bolt.AsyncApp — A new session has been established",
    )

    add_h2(doc, "8.3 Verify HTTP and the Slack connection")
    add_bullets(
        doc,
        [
            "Open http://localhost:8000/healthz — should return JSON with ok=true.",
            "In your Slack sidebar, find 'Work Assistant' under Apps. Click it, switch to "
            "the Messages tab. Send a quick 'hello'. The bot should react with 👀 and "
            "reply within a few seconds.",
        ],
    )

    add_h2(doc, "8.4 Run /connect (first-time setup for each user)")
    add_para(
        doc,
        "Each user runs /connect once. This is the entry point that links their Google "
        "Calendar, Wrike, and Slack search to the assistant.",
    )
    add_numbered(
        doc,
        [
            "In the bot's DM (or anywhere in Slack — slash commands work workspace-wide), "
            "type /connect.",
            "The bot DMs you a card with three 'Connect' buttons: Google Calendar, Wrike, "
            "and Slack search. (If you ran the command from a channel, you'll get an "
            "ephemeral 'I sent you a DM' note in that channel.)",
            "Click 'Google Calendar' → authorize in browser → see the 'Connected' page.",
            "Click 'Wrike' → authorize → see the 'Connected' page.",
            "Click 'Slack search' → authorize → see the 'Connected' page.",
            "Run /connect again to verify. All three buttons now show 'Reconnect' with a "
            "✅ — that's the indicator that the integration is linked.",
        ],
    )

    add_h2(doc, "8.5 Test the slash commands")
    add_bullets(
        doc,
        [
            "/goodmorning — the briefing arrives in your DM with the bot. Verify each "
            "section renders. If a section is empty, that's real data (no unreplied "
            "mentions / no 'Now' tasks / no events today). If a section is missing, run "
            "/connect for that integration.",
            "/wrike — the task list and open slots arrive in your DM. Reply in-thread "
            "with e.g. \"schedule #1 tomorrow 10am for 1 hour\". Confirm the event is "
            "created in Google Calendar with the Wrike link in the description.",
            "Free-form: in the bot's DM, ask \"what's on my calendar today?\". The agent "
            "uses the list_calendar_events tool and replies with a summary.",
        ],
    )

    add_callout(
        doc,
        "Tip",
        "Open the Phoenix UI at http://localhost:6006 while testing. You'll see traces "
        "appear within a second of each command — extremely useful when something goes wrong.",
        kind="tip",
    )
    page_break(doc)


def section_gcp(doc: Document) -> None:
    add_h1(doc, "9. Deploying to Google Cloud (Production)")
    add_para(
        doc,
        "Local development uses SQLite and Slack Socket Mode — both work great in production "
        "for small/medium scale, but for a multi-user assistant you'll usually swap SQLite "
        "for Cloud SQL Postgres and run on Cloud Run with min-instances=1 to keep the Socket "
        "Mode connection alive.",
    )

    add_h2(doc, "9.1 Architecture for production")
    add_code(
        doc,
        "                              ┌─────────────────────┐\n"
        "Slack ──Socket Mode──────────►│ Cloud Run (1 svc)   │──► Anthropic API\n"
        "                              │  app.main           │──► Google Calendar API\n"
        "Browser ─OAuth callbacks────►│  (Bolt + FastAPI)    │──► Wrike API\n"
        "                              └──────────┬──────────┘\n"
        "                                         │\n"
        "                              ┌──────────┴──────────┐\n"
        "                              │  Cloud SQL Postgres │\n"
        "                              └─────────────────────┘\n"
        "                              ┌─────────────────────┐\n"
        "                              │  Phoenix (Cloud or  │◄── traces (OTel)\n"
        "                              │  GCE VM with Docker)│\n"
        "                              └─────────────────────┘\n"
        "                              ┌─────────────────────┐\n"
        "                              │  Secret Manager     │\n"
        "                              │  - APP_SECRET_KEY   │\n"
        "                              │  - TOKEN_ENC_KEY    │\n"
        "                              │  - SLACK / GOOGLE / │\n"
        "                              │    WRIKE / ANTHROPIC│\n"
        "                              └─────────────────────┘",
    )

    add_h2(doc, "9.2 Provision Cloud SQL Postgres")
    add_numbered(
        doc,
        [
            "In GCP Console: SQL → Create instance → Postgres.",
            "Pick the smallest tier (db-f1-micro for dev, db-g1-small or higher for prod).",
            "Set a strong root password. Note the connection name (project:region:instance).",
            "Create a database named 'slack_assistant'.",
            "Create a service account user with permissions to that database.",
        ],
    )
    add_para(
        doc,
        "Update .env / Cloud Run environment with a DATABASE_URL like:",
    )
    add_code(
        doc,
        "DATABASE_URL=postgresql+asyncpg://USER:PASS@/slack_assistant?host=/cloudsql/PROJECT:REGION:INSTANCE",
        language="env",
    )
    add_callout(
        doc,
        "Note",
        "Add asyncpg to dependencies (uv add asyncpg) before deploying — SQLite's aiosqlite "
        "is local-only.",
        kind="note",
    )

    add_h2(doc, "9.3 Store secrets in Secret Manager")
    add_para(doc, "For each secret, create a version in Secret Manager:")
    add_code(
        doc,
        "gcloud secrets create APP_SECRET_KEY --replication-policy=automatic\n"
        "echo -n 'your-app-secret' | gcloud secrets versions add APP_SECRET_KEY --data-file=-\n"
        "\n"
        "# Repeat for: TOKEN_ENCRYPTION_KEY, ANTHROPIC_API_KEY, SLACK_BOT_TOKEN,\n"
        "# SLACK_APP_TOKEN, SLACK_SIGNING_SECRET, SLACK_CLIENT_SECRET,\n"
        "# GOOGLE_CLIENT_SECRET, WRIKE_CLIENT_SECRET, DATABASE_URL",
        language="bash",
    )
    add_para(
        doc,
        "Grant the Cloud Run service account the 'Secret Manager Secret Accessor' role on "
        "each secret.",
    )

    add_h2(doc, "9.4 Containerize the app")
    add_para(doc, "Create Dockerfile in the project root:")
    add_code(
        doc,
        "FROM python:3.12-slim\n"
        "RUN pip install --no-cache-dir uv\n"
        "WORKDIR /app\n"
        "COPY pyproject.toml uv.lock ./\n"
        "RUN uv sync --frozen --no-dev\n"
        "COPY . .\n"
        "ENV PYTHONUNBUFFERED=1\n"
        "EXPOSE 8000\n"
        'CMD [\"uv\", \"run\", \"python\", \"-m\", \"app.main\"]',
        language="dockerfile",
    )

    add_h2(doc, "9.5 Build and deploy to Cloud Run")
    add_code(
        doc,
        "gcloud builds submit --tag gcr.io/PROJECT_ID/slack-assistant\n"
        "\n"
        "gcloud run deploy slack-assistant \\\n"
        "  --image gcr.io/PROJECT_ID/slack-assistant \\\n"
        "  --region us-central1 \\\n"
        "  --platform managed \\\n"
        "  --port 8000 \\\n"
        "  --min-instances=1 \\\n"
        "  --max-instances=2 \\\n"
        "  --memory=1Gi \\\n"
        "  --cpu=1 \\\n"
        "  --timeout=3600 \\\n"
        "  --no-allow-unauthenticated \\\n"
        "  --add-cloudsql-instances=PROJECT:REGION:INSTANCE \\\n"
        "  --update-secrets=APP_SECRET_KEY=APP_SECRET_KEY:latest,"
        "TOKEN_ENCRYPTION_KEY=TOKEN_ENCRYPTION_KEY:latest,"
        "ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,"
        "SLACK_BOT_TOKEN=SLACK_BOT_TOKEN:latest,"
        "SLACK_APP_TOKEN=SLACK_APP_TOKEN:latest,"
        "SLACK_SIGNING_SECRET=SLACK_SIGNING_SECRET:latest,"
        "SLACK_CLIENT_SECRET=SLACK_CLIENT_SECRET:latest,"
        "GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,"
        "WRIKE_CLIENT_SECRET=WRIKE_CLIENT_SECRET:latest,"
        "DATABASE_URL=DATABASE_URL:latest \\\n"
        "  --set-env-vars=APP_ENV=prod,APP_BASE_URL=https://YOUR-SERVICE.run.app,"
        "SLACK_CLIENT_ID=...,GOOGLE_CLIENT_ID=...,WRIKE_CLIENT_ID=..."
        ,
        language="bash",
    )

    add_callout(
        doc,
        "Important",
        "min-instances=1 is critical. Slack Socket Mode is a long-lived websocket; if "
        "Cloud Run scales to zero, the websocket disconnects and reconnects, dropping "
        "events.",
        kind="warn",
    )
    add_callout(
        doc,
        "Important",
        "OAuth callbacks need a public URL. Cloud Run's URL works, but you must "
        "--allow-unauthenticated for the /oauth/* routes to be reachable from a user's "
        "browser. The cleanest pattern: a dedicated Cloud Run service with auth disabled "
        "ONLY for /oauth/*, or wrap public auth via a Load Balancer in front. For an "
        "internal-only deployment with IAP, expose only OAuth callback paths.",
        kind="warn",
    )

    add_h2(doc, "9.6 Update OAuth redirect URIs to production")
    add_para(doc, "After deploy, update each OAuth provider with the new redirect URIs:")
    add_bullets(
        doc,
        [
            "Google: APIs & Services → Credentials → your OAuth client → add "
            "https://YOUR-SERVICE.run.app/oauth/google/callback",
            "Wrike: app settings → Redirect URIs → add "
            "https://YOUR-SERVICE.run.app/oauth/wrike/callback",
            "Slack: api.slack.com/apps → your app → OAuth & Permissions → Redirect URLs → "
            "add https://YOUR-SERVICE.run.app/oauth/slack/callback",
        ],
    )

    add_h2(doc, "9.7 Publish the Google OAuth consent screen")
    add_para(
        doc,
        "If you'll use the assistant outside your test-user list, publish the consent "
        "screen. For internal-only use within a Google Workspace org, set User Type to "
        "'Internal' instead — no review required.",
    )
    add_numbered(
        doc,
        [
            "OAuth consent screen → 'Publish App'.",
            "If you've requested sensitive scopes (Calendar is sensitive), Google "
            "prompts you to submit for verification. This takes 1–6 weeks. During "
            "verification, current test users continue to work.",
        ],
    )

    add_h2(doc, "9.8 Phoenix in production")
    add_para(
        doc,
        "Two options:",
    )
    add_bullets(
        doc,
        [
            "Phoenix Cloud (managed): set PHOENIX_COLLECTOR_ENDPOINT and PHOENIX_API_KEY "
            "via Secret Manager. No infra to run.",
            "Self-hosted on a small Compute Engine VM running the Phoenix Docker image, "
            "with a persistent disk volume. Connect Cloud Run via VPC connector + private "
            "IP.",
        ],
    )

    add_h2(doc, "9.9 Continuous deployment")
    add_para(
        doc,
        "Wire up Cloud Build with a trigger on your git repo so that pushes to main "
        "rebuild and redeploy. The Cloud Build YAML for this is straightforward:",
    )
    add_code(
        doc,
        "steps:\n"
        "- name: gcr.io/cloud-builders/docker\n"
        "  args: ['build', '-t', 'gcr.io/$PROJECT_ID/slack-assistant', '.']\n"
        "- name: gcr.io/cloud-builders/docker\n"
        "  args: ['push', 'gcr.io/$PROJECT_ID/slack-assistant']\n"
        "- name: gcr.io/google.cloud-sdk/cloud-sdk\n"
        "  entrypoint: gcloud\n"
        "  args:\n"
        "  - run\n"
        "  - deploy\n"
        "  - slack-assistant\n"
        "  - --image=gcr.io/$PROJECT_ID/slack-assistant\n"
        "  - --region=us-central1",
        language="yaml",
    )
    page_break(doc)


def section_troubleshooting(doc: Document) -> None:
    add_h1(doc, "10. Troubleshooting")

    add_h2(doc, "10.1 'Missing required env vars' on startup")
    add_para(
        doc,
        "The validator at startup lists exactly which env vars are missing. Re-check .env "
        "and remember that uv reads .env at the project root, not from a subdirectory.",
    )

    add_h2(doc, "10.2 Bot doesn't respond to messages in its DM")
    add_bullets(
        doc,
        [
            "Confirm Socket Mode is enabled (Slack app → Socket Mode toggle ON).",
            "Confirm message.im is in the bot's event subscriptions.",
            "Check the assistant logs for 'A new session has been established' — that's "
            "Bolt connecting to Slack.",
            "Note: by design, the bot only listens inside its own DM. Channel mentions "
            "are intentionally ignored.",
        ],
    )

    add_h2(doc, "10.3 /goodmorning shows nothing under Slack")
    add_para(
        doc,
        "Likely cause: the user hasn't completed the Slack user-token OAuth (the third "
        "/connect button). The bot token can't search.messages — only an xoxp- user token "
        "can. Re-run /connect and click the Slack button.",
    )

    add_h2(doc, "10.4 'No custom status named X' from Wrike")
    add_para(
        doc,
        "Your workspace has different status names. Either rename a status to 'Now' / 'New' "
        "in Wrike Workflows, or change the literal in the code "
        "(app/slack_app/commands/goodmorning.py and wrike_cmd.py).",
    )

    add_h2(doc, "10.5 Google: 'redirect_uri_mismatch'")
    add_para(
        doc,
        "The redirect URI Google sees doesn't exactly match what's configured in your "
        "OAuth client. Check protocol (http vs https), port, and trailing slash. For local "
        "dev: http://localhost:8000/oauth/google/callback (no trailing slash).",
    )

    add_h2(doc, "10.6 Calendar event creation works but no Wrike link")
    add_para(
        doc,
        "Confirm task.permalink is populated in the Wrike API response. If not, the "
        "fallback URL pattern is https://www.wrike.com/open.htm?id=<task_id>, which always "
        "resolves to the task in the user's browser.",
    )

    add_h2(doc, "10.7 Token decryption fails after key change")
    add_para(
        doc,
        "If TOKEN_ENCRYPTION_KEY ever changes, every user must re-OAuth. There is no way "
        "to decrypt old tokens with a new key. Treat the key like a database password.",
    )

    add_h2(doc, "10.8 Phoenix UI shows no traces")
    add_bullets(
        doc,
        [
            "Confirm Phoenix is running and reachable: curl http://localhost:6006.",
            "Check PHOENIX_COLLECTOR_ENDPOINT in .env matches the running instance.",
            "The first trace can take a few seconds to flush — try mentioning the bot a "
            "couple of times.",
            "If using Phoenix Cloud, confirm PHOENIX_API_KEY is set.",
        ],
    )

    add_h2(doc, "10.9 Slack rate limits hit during /goodmorning")
    add_para(
        doc,
        "search.messages is Tier 2 (~20 req/min) and conversations.replies is Tier 3 "
        "(~50 req/min). For very active users, consider caching results or capping the "
        "lookback window. The current implementation caps at 50 search results and uses a "
        "concurrency semaphore of 5 for reply checks.",
    )
    page_break(doc)


def section_next_steps(doc: Document) -> None:
    add_h1(doc, "11. Next Steps and Future Enhancements")

    add_h2(doc, "11.1 Auto-scheduled morning briefing")
    add_para(
        doc,
        "Wire APScheduler or a Cloud Scheduler cron job to call the same workflow used by "
        "/goodmorning at 8 AM in each user's timezone. The handler already separates the "
        "data-fetch logic from the slash-command trigger, so this is a few lines.",
    )

    add_h2(doc, "11.2 /eod end-of-day summary")
    add_para(
        doc,
        "Mirror /goodmorning, but show: today's completed Wrike tasks, calendar events "
        "you actually attended, Slack messages you sent. Same scaffolding, opposite "
        "queries.",
    )

    add_h2(doc, "11.3 /focustime")
    add_para(
        doc,
        "Find the next free block of N minutes, create a 'Focus' calendar event, and set "
        "Slack DnD for the duration. Reuse first_free_slot_for_duration in "
        "app/utils/working_hours.py.",
    )

    add_h2(doc, "11.4 Task reschedule / cancel")
    add_para(
        doc,
        "When /wrike is run again, detect events created by this bot via "
        "extendedProperties.private.wrikeTaskId and offer to reschedule rather than "
        "duplicate.",
    )

    add_h2(doc, "11.5 Per-user working hours")
    add_para(
        doc,
        "The User table has workday_start and workday_end columns. Add a /settings command "
        "(or read from Google Calendar's working-hours feature) so users can override the "
        "9–6 default.",
    )

    add_h2(doc, "11.6 Online evals with Phoenix")
    add_para(
        doc,
        "Add a nightly Phoenix evals job that scores recent traces on (a) tool selection "
        "correctness and (b) response helpfulness. Promote interesting traces into Phoenix "
        "datasets, then run experiments before changing the system prompt.",
    )

    add_h2(doc, "11.7 Two-way sync between Wrike status and Calendar event")
    add_para(
        doc,
        "On calendar event end, optionally move the Wrike task to 'In Progress'. On Wrike "
        "task completion, optionally delete the calendar event. Implement via Calendar "
        "push notifications or polling.",
    )
    page_break(doc)


def section_appendix(doc: Document) -> None:
    add_h1(doc, "12. Appendix — Reference Tables")

    add_h2(doc, "12.1 Required redirect URIs")
    table = doc.add_table(rows=4, cols=3)
    table.style = "Light Grid Accent 1"
    headers = ["Provider", "Local dev", "Production"]
    rows = [
        ("Google", "http://localhost:8000/oauth/google/callback", "https://YOUR-DOMAIN/oauth/google/callback"),
        ("Wrike", "http://localhost:8000/oauth/wrike/callback", "https://YOUR-DOMAIN/oauth/wrike/callback"),
        ("Slack (user-token)", "http://localhost:8000/oauth/slack/callback", "https://YOUR-DOMAIN/oauth/slack/callback"),
    ]
    for i, h in enumerate(headers):
        cell = table.cell(0, i)
        cell.text = h
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(10)
        _shade_cell(cell, "E5E7EB")
    for r, row in enumerate(rows, start=1):
        for c, val in enumerate(row):
            cell = table.cell(r, c)
            cell.text = val
            for run in cell.paragraphs[0].runs:
                run.font.size = Pt(9)
                if c > 0:
                    run.font.name = "Consolas"

    add_h2(doc, "12.2 .env reference")
    add_code(
        doc,
        "# Generated\n"
        "APP_SECRET_KEY=...                 # CSRF signer for OAuth state\n"
        "TOKEN_ENCRYPTION_KEY=...           # Fernet key (urlsafe base64)\n"
        "\n"
        "# App\n"
        "APP_ENV=local                      # local | prod\n"
        "APP_BASE_URL=http://localhost:8000 # used in OAuth redirect URIs\n"
        "LOG_LEVEL=INFO                     # DEBUG / INFO / WARNING / ERROR\n"
        "\n"
        "# Anthropic\n"
        "ANTHROPIC_API_KEY=sk-ant-...\n"
        "AGENT_MODEL=claude-sonnet-4-6\n"
        "EXTRACTION_MODEL=claude-haiku-4-5-20251001\n"
        "\n"
        "# Slack\n"
        "SLACK_SIGNING_SECRET=...\n"
        "SLACK_BOT_TOKEN=xoxb-...\n"
        "SLACK_APP_TOKEN=xapp-...\n"
        "SLACK_CLIENT_ID=...\n"
        "SLACK_CLIENT_SECRET=...\n"
        "\n"
        "# Google\n"
        "GOOGLE_CLIENT_ID=...\n"
        "GOOGLE_CLIENT_SECRET=...\n"
        "\n"
        "# Wrike\n"
        "WRIKE_CLIENT_ID=...\n"
        "WRIKE_CLIENT_SECRET=...\n"
        "\n"
        "# Phoenix\n"
        "PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006\n"
        "PHOENIX_PROJECT_NAME=slack-assistant\n"
        "PHOENIX_API_KEY=                   # leave empty for self-host",
        language="env",
    )

    add_h2(doc, "12.3 Slash commands quick reference")
    table = doc.add_table(rows=4, cols=2)
    table.style = "Light Grid Accent 1"
    headers = ["Command", "What it does"]
    rows = [
        ("/connect", "DMs the user three OAuth buttons (Google, Wrike, Slack search)."),
        ("/goodmorning", "Daily briefing: unreplied @-mentions (7d) + Wrike Now/due-soon + today's calendar."),
        ("/wrike", "Lists 'New' Wrike tasks; converse in-thread to schedule one onto your calendar."),
    ]
    for i, h in enumerate(headers):
        cell = table.cell(0, i)
        cell.text = h
        for run in cell.paragraphs[0].runs:
            run.bold = True
        _shade_cell(cell, "E5E7EB")
    for r, row in enumerate(rows, start=1):
        for c, val in enumerate(row):
            cell = table.cell(r, c)
            cell.text = val
            if c == 0:
                for run in cell.paragraphs[0].runs:
                    run.font.name = "Consolas"
                    run.font.color.rgb = RGBColor(0x86, 0x19, 0x54)


# ── Main ───────────────────────────────────────────────────────────────────


def build() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = Document()

    # Page margins — slightly tighter than default for better code-block fit
    for section in doc.sections:
        section.left_margin = Cm(2.0)
        section.right_margin = Cm(2.0)
        section.top_margin = Cm(2.2)
        section.bottom_margin = Cm(2.2)

    section_cover(doc)
    section_overview(doc)
    section_local_setup(doc)
    section_slack_app(doc)
    section_google(doc)
    section_wrike(doc)
    section_anthropic(doc)
    section_phoenix(doc)
    section_first_run(doc)
    section_gcp(doc)
    section_troubleshooting(doc)
    section_next_steps(doc)
    section_appendix(doc)

    doc.save(OUT_PATH)
    return OUT_PATH


if __name__ == "__main__":
    out = build()
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
