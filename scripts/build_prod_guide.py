"""Generate `Production_Deployment_Guide.docx` — the step-by-step prod deploy walkthrough.

Run from prod/:  uv run python scripts/build_prod_guide.py
Output:          prod/docs/Production_Deployment_Guide.docx
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT_DIR = Path(__file__).resolve().parent.parent / "docs"
OUT_PATH = OUT_DIR / "Production_Deployment_Guide.docx"


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
    run.font.color.rgb = RGBColor(0x11, 0x18, 0x27)


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


def add_code(doc: Document, code: str) -> None:
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


def add_callout(doc: Document, title: str, body: str, *, kind: str = "note") -> None:
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


def add_kv_table(doc: Document, headers: list[str], rows: list[tuple]) -> None:
    table = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    table.style = "Light Grid Accent 1"
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
            cell.text = str(val)
            for run in cell.paragraphs[0].runs:
                run.font.size = Pt(9)


def page_break(doc: Document) -> None:
    doc.add_page_break()


# ── Sections ───────────────────────────────────────────────────────────────


def section_cover(doc: Document) -> None:
    add_title(doc, "Slack Assistant — Production Deployment Guide")
    add_subtitle(
        doc,
        "Step-by-step instructions to deploy the multi-user Slack assistant to "
        "Google Cloud Run, including all third-party app setup (Slack, Google, Wrike, "
        "Anthropic, Phoenix). Written for a careful follower.",
    )
    add_para(doc, "Deployment target:  Google Cloud Run + Cloud SQL Postgres")
    add_para(doc, "Estimated cost:      ~$20–25 / month (excluding Anthropic API usage)")
    add_para(doc, "Document version:    1.0  (May 2026)")
    page_break(doc)


def section_how_to_use(doc: Document) -> None:
    add_h1(doc, "How to use this document")
    add_para(
        doc,
        "Read every section in order. Each one ends with a clear 'success check' so you "
        "know whether to move on. If a step fails, see Section 14 (Troubleshooting) before "
        "going further.",
    )
    add_h2(doc, "Conventions")
    add_bullets(
        doc,
        [
            "Boxes with grey backgrounds are commands you paste into a terminal or a value "
            "you paste into a website form.",
            "Yellow callouts are warnings. Read them carefully.",
            "Green callouts are tips that save time.",
            "Blue callouts are clarifying notes.",
            "Wherever you see <ANGLE-BRACKETS>, replace the angle brackets and the text inside "
            "with your actual value.",
        ],
    )
    add_h2(doc, "Where commands run")
    add_para(
        doc,
        "All terminal commands assume you are inside the project's prod/ folder, in your "
        "computer's terminal application (Terminal on macOS, or Command Prompt / PowerShell on "
        "Windows). When something must run in a different location, the document says so "
        "explicitly.",
    )
    page_break(doc)


def section_overview(doc: Document) -> None:
    add_h1(doc, "1. Overview")

    add_h2(doc, "1.1 What you're building")
    add_para(
        doc,
        "A Slack-native AI assistant that lives inside the Slack app (no channel "
        "intrusion), helps users with their Google Calendar and Wrike tasks, and is "
        "deployed on Google Cloud so it's always available to your team.",
    )

    add_h2(doc, "1.2 The high-level flow you'll execute")
    add_numbered(
        doc,
        [
            "Sign up for or sign in to all the third-party services (Slack, Google, "
            "Wrike, Anthropic, Phoenix). Section 2.",
            "Install command-line tools on your laptop (gcloud, uv, git). Section 3.",
            "Create the Slack app and get its tokens. Section 4.",
            "Create the Google OAuth client. Section 5.",
            "Create the Wrike OAuth app. Section 6.",
            "Create the Anthropic API key. Section 7.",
            "Sign up for Phoenix Cloud. Section 8.",
            "Create the Google Cloud project and enable APIs. Section 9.",
            "Create the Cloud SQL Postgres database. Section 10.",
            "Upload all secrets to Google Secret Manager. Section 11.",
            "Build the container and deploy to Cloud Run. Section 12.",
            "Update the third-party apps with the production URL and test. Section 13.",
        ],
    )

    add_h2(doc, "1.3 Architecture diagram")
    add_code(
        doc,
        "                          ┌─────────────────────┐\n"
        "Your Slack workspace ──►│ Cloud Run service     │──► Anthropic API\n"
        "                          │  Bolt (Socket Mode)  │──► Google Calendar API\n"
        "User browsers (OAuth) ──►│  + FastAPI (HTTP)    │──► Wrike API\n"
        "                          └──────────┬──────────┘\n"
        "                                     │\n"
        "                          ┌──────────▼──────────┐\n"
        "                          │  Cloud SQL Postgres │\n"
        "                          │  via Auth Proxy     │\n"
        "                          └─────────────────────┘\n"
        "                          ┌─────────────────────┐\n"
        "                          │  Phoenix Cloud      │◄── traces (OTel)\n"
        "                          └─────────────────────┘\n"
        "                          ┌─────────────────────┐\n"
        "                          │  Secret Manager     │\n"
        "                          └─────────────────────┘",
    )

    add_h2(doc, "1.4 Why this stack?")
    add_para(doc, "Picked for the best balance of cost, reliability, and complexity:")
    add_bullets(
        doc,
        [
            "Cloud Run runs containers; you only pay for what runs. Setting min-instances=1 "
            "keeps the container always warm so the Slack websocket stays connected. Cost: "
            "~$10–15 a month at this size.",
            "Cloud SQL Postgres is a managed database. Backups, patching, and security all "
            "handled by Google. Cheapest tier (db-f1-micro) is enough for hundreds of users.",
            "Phoenix Cloud's free tier removes infrastructure work for observability.",
            "Secret Manager keeps API keys out of source control and out of environment "
            "variables in the console UI.",
        ],
    )
    page_break(doc)


def section_accounts(doc: Document) -> None:
    add_h1(doc, "2. Accounts you'll need")
    add_para(
        doc,
        "Sign up for any of these you don't already have. None of them cost money to create "
        "an account; some require a credit card later when you start using them.",
    )

    rows = [
        ("Service", "Sign-up URL", "Cost to sign up"),
        ("Slack", "https://slack.com — you also need admin rights to a workspace", "Free"),
        ("Google Cloud", "https://console.cloud.google.com — sign in with a Google account", "$300 free trial credit on first sign-up"),
        ("Wrike", "https://www.wrike.com — sign up for a free trial", "Free trial; OAuth app creation needs Wrike admin"),
        ("Anthropic", "https://console.anthropic.com", "Free to sign up; pay-as-you-go for API"),
        ("Phoenix (Arize)", "https://app.phoenix.arize.com", "Free tier"),
    ]
    add_kv_table(doc, list(rows[0]), rows[1:])

    add_callout(
        doc,
        "Tip",
        "Use a single email address (ideally your work email) across all five services. "
        "It makes finding things and recovering accounts easier later.",
        kind="tip",
    )
    page_break(doc)


def section_local_tools(doc: Document) -> None:
    add_h1(doc, "3. Tools to install on your laptop")
    add_para(
        doc,
        "These three tools must be installed before you start. Each section ends with the "
        "exact command to verify the install worked.",
    )

    add_h2(doc, "3.1 Google Cloud CLI (gcloud)")
    add_para(
        doc,
        "The gcloud command is how you talk to your Google Cloud project from your terminal.",
    )
    add_h3(doc, "macOS")
    add_code(doc, "brew install --cask google-cloud-sdk")
    add_h3(doc, "Windows")
    add_para(
        doc,
        "Download and run the installer at https://cloud.google.com/sdk/docs/install. "
        "Pick all defaults.",
    )
    add_h3(doc, "Linux")
    add_code(
        doc,
        "curl https://sdk.cloud.google.com | bash\n"
        "exec -l $SHELL",
    )
    add_h3(doc, "Verify")
    add_code(doc, "gcloud --version")
    add_para(doc, "You should see a version line. If 'command not found', restart your terminal.")

    add_h2(doc, "3.2 uv (Python toolchain)")
    add_para(doc, "uv installs Python 3.12 and manages dependencies for the project.")
    add_h3(doc, "macOS")
    add_code(doc, "brew install uv")
    add_h3(doc, "Windows / Linux")
    add_code(doc, "curl -LsSf https://astral.sh/uv/install.sh | sh")
    add_h3(doc, "Verify")
    add_code(doc, "uv --version")

    add_h2(doc, "3.3 Docker Desktop (only for local image builds)")
    add_para(
        doc,
        "You technically don't need Docker locally because Cloud Build builds the image in "
        "the cloud. Install Docker Desktop only if you want to test the container on your "
        "laptop before deploying. Skip this section if you don't.",
    )
    add_para(doc, "Download from https://www.docker.com/products/docker-desktop/.")

    add_h2(doc, "3.4 Sign in to gcloud")
    add_code(doc, "gcloud auth login\ngcloud auth application-default login")
    add_para(
        doc,
        "Both commands open your browser. Sign in with the Google account you'll use for "
        "the deployment. The first authenticates the gcloud CLI; the second sets up "
        "default credentials used by some tools.",
    )

    add_callout(doc, "Success check", "Run gcloud auth list — your email should appear with an asterisk.", kind="tip")
    page_break(doc)


def section_slack_app(doc: Document) -> None:
    add_h1(doc, "4. Create the Slack app")
    add_para(
        doc,
        "Total time: ~10 minutes. You will end this section with five values you save into "
        "a notepad: SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_SIGNING_SECRET, SLACK_CLIENT_ID, "
        "and SLACK_CLIENT_SECRET.",
    )

    add_h2(doc, "4.1 Open the Slack apps page")
    add_numbered(
        doc,
        [
            "Open https://api.slack.com/apps in your browser.",
            "If you see multiple workspaces in the top right, pick the one you want to "
            "install the assistant into.",
            "Click the green button 'Create New App'.",
            "Choose 'From an app manifest'. (NOT 'From scratch'.)",
            "On the next screen, pick the workspace where this app will live.",
        ],
    )

    add_h2(doc, "4.2 Paste the manifest")
    add_para(doc, "Switch the manifest editor to YAML. Delete whatever's there. Paste this exactly:")
    add_code(
        doc,
        "display_information:\n"
        "  name: Work Assistant\n"
        "features:\n"
        "  app_home:\n"
        "    home_tab_enabled: false\n"
        "    messages_tab_enabled: true\n"
        "    messages_tab_read_only_enabled: false\n"
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
        "    - https://placeholder.invalid/oauth/slack/callback\n"
        "  scopes:\n"
        "    user:\n"
        "      - search:read\n"
        "      - channels:history\n"
        "      - groups:history\n"
        "      - im:history\n"
        "      - mpim:history\n"
        "      - users:read\n"
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
        "      - message.im\n"
        "  interactivity:\n"
        "    is_enabled: true\n"
        "  socket_mode_enabled: true\n"
        "  org_deploy_enabled: false",
    )
    add_para(
        doc,
        "Click Next, then Create. Slack will sometimes warn that the redirect URL contains "
        "a placeholder — that's fine, you'll fix it in Section 13.",
    )
    add_callout(
        doc,
        "Critical — Messages Tab",
        "The line `messages_tab_read_only_enabled: false` is what makes the bot's "
        "Messages tab accept user input. Without it, users see 'Sending messages to "
        "this app has been turned off' and cannot talk to the bot. If your app was "
        "created from an older manifest, fix it in the UI: Slack app settings → "
        "'App Home' → tick 'Allow users to send Slash commands and messages from "
        "the messages tab' → 'Reinstall to Workspace'.",
        kind="warn",
    )

    add_h2(doc, "4.3 Install the app to the workspace")
    add_numbered(
        doc,
        [
            "On the left sidebar, click 'OAuth & Permissions'.",
            "At the top, click 'Install to <Your Workspace>'.",
            "Click 'Allow' on the consent screen.",
            "You're back on OAuth & Permissions. Copy the value labelled 'Bot User OAuth "
            "Token' (it starts with xoxb-). Save it as SLACK_BOT_TOKEN in your notepad.",
        ],
    )

    add_h2(doc, "4.4 Generate the App-Level Token (for Socket Mode)")
    add_numbered(
        doc,
        [
            "Left sidebar → 'Basic Information'.",
            "Scroll to 'App-Level Tokens'. Click 'Generate Token and Scopes'.",
            "Token name: socket-mode. Click 'Add Scope', pick 'connections:write'. Click "
            "'Generate'.",
            "Copy the token (starts with xapp-). Save it as SLACK_APP_TOKEN.",
            "Click 'Done'.",
        ],
    )

    add_h2(doc, "4.5 Save the remaining three values")
    add_para(doc, "Still on Basic Information, scroll down to 'App Credentials'. Save:")
    add_bullets(
        doc,
        [
            "Signing Secret → SLACK_SIGNING_SECRET",
            "Client ID → SLACK_CLIENT_ID",
            "Client Secret (click 'Show', then copy) → SLACK_CLIENT_SECRET",
        ],
    )

    add_callout(
        doc,
        "Success check",
        "Your notepad now has 5 SLACK_… values. Don't share them. They are the equivalent "
        "of passwords for your bot.",
        kind="tip",
    )
    page_break(doc)


def section_google(doc: Document) -> None:
    add_h1(doc, "5. Create the Google OAuth client")
    add_para(
        doc,
        "Total time: ~15 minutes. You will end this section with two values: GOOGLE_CLIENT_ID "
        "and GOOGLE_CLIENT_SECRET.",
    )

    add_h2(doc, "5.1 Create or select a Google Cloud project")
    add_numbered(
        doc,
        [
            "Open https://console.cloud.google.com.",
            "Click the project picker in the top bar (just to the right of 'Google Cloud').",
            "Click 'New Project' (top right of the picker dialog).",
            "Project name: 'slack-assistant'.",
            "Make a note of the auto-generated Project ID — you'll use it in Section 9.",
            "Click Create. Wait 5 seconds, then re-open the project picker and select it.",
        ],
    )

    add_h2(doc, "5.2 Enable the Google Calendar API")
    add_numbered(
        doc,
        [
            "In the search bar at the top of the console, type 'Calendar API'.",
            "Click 'Google Calendar API' in the results.",
            "Click 'Enable'. Wait until the page reloads showing the API as enabled.",
        ],
    )

    add_h2(doc, "5.3 Configure the OAuth consent screen")
    add_numbered(
        doc,
        [
            "Left side menu → 'APIs & Services' → 'OAuth consent screen'.",
            "User Type: pick 'External' if your users are on different Google accounts; pick "
            "'Internal' only if everyone uses Google Workspace under the same domain. "
            "(Internal skips the verification step.)",
            "Click 'Create'.",
            "On the next screen fill in: App name = 'Slack Assistant'. User support email = "
            "your email. Developer contact email = your email. Leave the rest blank.",
            "Click 'Save and Continue'.",
            "Scopes step: click 'Add or Remove Scopes'. In the filter box, paste "
            "'auth/calendar'. Tick the row whose scope is exactly "
            "'.../auth/calendar' (full Calendar access). Also tick "
            "'.../auth/userinfo.email' and 'openid'. Click 'Update', then 'Save and Continue'.",
            "Test users step: click 'Add Users'. Paste in the email of every person who "
            "will OAuth into the assistant during testing. Click 'Add', then 'Save and Continue'.",
            "Review summary, then 'Back to Dashboard'.",
        ],
    )
    add_callout(
        doc,
        "Note",
        "While the app is in 'Testing' mode, only the test users you listed can connect, "
        "and Google refresh tokens expire after 7 days. To remove these limits you must "
        "submit the app for verification (Section 13.4 of this guide).",
        kind="note",
    )

    add_h2(doc, "5.4 Create the OAuth Client ID")
    add_numbered(
        doc,
        [
            "Left side menu → 'APIs & Services' → 'Credentials'.",
            "Click '+ Create Credentials' (top of page) → 'OAuth client ID'.",
            "Application type: 'Web application'.",
            "Name: 'Slack Assistant'.",
            "Under 'Authorized redirect URIs', click '+ Add URI' and paste exactly: "
            "https://placeholder.invalid/oauth/google/callback (we'll fix this after deploy).",
            "Click 'Create'.",
        ],
    )
    add_para(doc, "A modal pops up showing two values. Copy both:")
    add_bullets(
        doc,
        [
            "'Your Client ID' → save as GOOGLE_CLIENT_ID",
            "'Your Client Secret' → save as GOOGLE_CLIENT_SECRET",
        ],
    )
    add_para(doc, "Click 'OK'.")

    add_callout(
        doc,
        "Success check",
        "Your notepad now has GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
        kind="tip",
    )
    page_break(doc)


def section_wrike(doc: Document) -> None:
    add_h1(doc, "6. Create the Wrike OAuth app")
    add_para(
        doc,
        "Total time: ~5 minutes. You will end this section with WRIKE_CLIENT_ID and "
        "WRIKE_CLIENT_SECRET.",
    )

    add_callout(
        doc,
        "Architecture note",
        "We use Wrike's REST API with OAuth 2.0, not the Wrike MCP server. The Wrike MCP "
        "available inside Claude.ai cannot be called from a self-hosted deployment; OAuth "
        "REST is the production-correct pattern for a multi-user app.",
        kind="note",
    )

    add_h2(doc, "6.1 Open the Wrike OAuth apps page")
    add_numbered(
        doc,
        [
            "In a browser, sign in to Wrike (https://www.wrike.com).",
            "Visit https://www.wrike.com/frame/oauth2/apps.",
            "Click 'Create new app'.",
        ],
    )

    add_h2(doc, "6.2 Configure the app")
    add_numbered(
        doc,
        [
            "App name: Slack Assistant.",
            "Description (optional): 'Slack-native AI assistant'.",
            "Click 'Save'. The app is created.",
            "On the app's detail page, find the 'Permissions' or 'Scopes' section and grant: "
            "'Read tasks', 'Read folders/projects', 'Read user info', 'Read workflows', and "
            "(optional) 'Write tasks'. Save.",
            "Find the 'Redirect URIs' field. Add: "
            "https://placeholder.invalid/oauth/wrike/callback. Save.",
        ],
    )

    add_h2(doc, "6.3 Copy credentials")
    add_para(doc, "Still on the app's detail page, copy:")
    add_bullets(
        doc,
        [
            "Client ID → save as WRIKE_CLIENT_ID",
            "Client Secret (click 'Show' if needed) → save as WRIKE_CLIENT_SECRET",
        ],
    )

    add_h2(doc, "6.4 Confirm the 'New' status exists in your workspace")
    add_para(
        doc,
        "Both /goodmorning and /wrike key off Wrike's custom status named exactly "
        "'New' (case-insensitive). If your workspace doesn't have one, you'll need "
        "to either add it or change the status name referenced in the source code.",
    )
    add_numbered(
        doc,
        [
            "In Wrike, click the gear icon (top right) → 'Workflows'.",
            "For every workflow your tasks live in, confirm there is a status named "
            "exactly 'New' (case-insensitive). Wrike workspaces often have multiple "
            "workflows (one per folder/project) — the assistant resolves all of them "
            "automatically and queries every matching status ID.",
            "If a workflow doesn't have 'New', either rename one of its statuses to "
            "'New' (be careful — this affects existing tasks), or edit "
            "prod/app/slack_app/commands/goodmorning.py and wrike_cmd.py before "
            "deploying, replacing the literal string 'New' with the name your "
            "workspace actually uses.",
        ],
    )
    add_callout(
        doc,
        "Multi-workflow note",
        "There's no need to enumerate workflow IDs anywhere. The integration calls "
        "/workflows on first use, finds every status named 'New' across all of them, "
        "and caches the IDs. The cache table is `workflowstatuscache` in Postgres.",
        kind="note",
    )

    add_callout(
        doc,
        "Success check",
        "WRIKE_CLIENT_ID and WRIKE_CLIENT_SECRET are saved. You've confirmed your "
        "workspace's status names.",
        kind="tip",
    )
    page_break(doc)


def section_anthropic(doc: Document) -> None:
    add_h1(doc, "7. Get the Anthropic API key")

    add_numbered(
        doc,
        [
            "Sign in at https://console.anthropic.com.",
            "Top right → your account icon → 'API Keys'.",
            "Click 'Create Key'. Name it 'slack-assistant-prod'.",
            "Copy the key (starts with sk-ant-). It is shown only once. Save as "
            "ANTHROPIC_API_KEY.",
            "Top right → 'Billing'. Add a payment method or top up credits. The free tier "
            "is enough for testing but you will hit limits in production.",
        ],
    )

    add_h2(doc, "Costs to expect")
    add_para(
        doc,
        "The conversational agent uses Sonnet 4.6 (default) and the slot extractor in /wrike "
        "uses Haiku 4.5. A casual user runs ~$1–3 a month in tokens; an active user ~$5–10. "
        "These figures change as Anthropic adjusts pricing.",
    )
    page_break(doc)


def section_phoenix(doc: Document) -> None:
    add_h1(doc, "8. Sign up for Phoenix Cloud")

    add_para(
        doc,
        "Phoenix is the observability tool that records every model call and tool call so "
        "you can debug what the assistant did. The Cloud free tier is plenty for a small "
        "team.",
    )

    add_numbered(
        doc,
        [
            "Sign up at https://app.phoenix.arize.com.",
            "After confirming your email, you'll land on the dashboard.",
            "Top right → your name → 'Settings' (or 'API Keys').",
            "Click 'Create API Key'. Copy it. Save as PHOENIX_API_KEY.",
            "Note the collector endpoint — typically https://app.phoenix.arize.com (the "
            "default in our config).",
        ],
    )
    page_break(doc)


def section_gcp_setup(doc: Document) -> None:
    add_h1(doc, "9. Enable Google Cloud APIs")

    add_para(
        doc,
        "From here on, you'll mostly run terminal commands inside the prod/ folder. The "
        "scripts in prod/infrastructure/ are numbered to be run in order.",
    )

    add_h2(doc, "9.1 Open a terminal in the prod folder")
    add_code(doc, "cd /path/to/your/project/prod")
    add_para(
        doc,
        "On macOS, the easy way: drag the prod folder onto a Terminal window — it auto-"
        "fills the path. On Windows, in PowerShell, navigate with `cd`.",
    )

    add_h2(doc, "9.2 Edit infrastructure/_env.sh")
    add_para(
        doc,
        "Open infrastructure/_env.sh in any text editor. Change PROJECT_ID to your Google "
        "Cloud project ID (from Section 5.1). Save the file.",
    )
    add_code(doc, "PROJECT_ID=\"slack-assistant-XXXXX\"   # replace with your actual project ID")

    add_h2(doc, "9.3 Run the API enabler")
    add_code(doc, "./infrastructure/00-enable-apis.sh")
    add_para(doc, "Expected output: about 30 seconds of progress, ending with '✅ APIs enabled.'")
    add_para(doc, "If the command fails with 'permission denied', mark it executable first:")
    add_code(doc, "chmod +x infrastructure/*.sh")

    add_h2(doc, "9.4 Enable billing (one-time)")
    add_numbered(
        doc,
        [
            "Open https://console.cloud.google.com/billing.",
            "If you don't have a billing account, click 'Add Billing Account' and follow "
            "the prompts. New Google Cloud users get $300 free trial credit.",
            "Go to your project → 'Billing' → 'Link a billing account'. Link the account "
            "you just created.",
            "Confirm the link by visiting https://console.cloud.google.com/billing/linkedaccount.",
        ],
    )
    add_callout(
        doc,
        "Important",
        "Without billing enabled, Cloud SQL and Cloud Run will refuse to create resources. "
        "The free $300 of credit covers months of this deployment.",
        kind="warn",
    )
    page_break(doc)


def section_cloud_sql(doc: Document) -> None:
    add_h1(doc, "10. Create the Cloud SQL Postgres database")

    add_para(
        doc,
        "This step takes ~5 minutes (Cloud SQL provisioning is the slowest step in the "
        "whole deploy). Run from inside prod/:",
    )
    add_code(doc, "./infrastructure/01-create-cloud-sql.sh")

    add_h2(doc, "What it does")
    add_bullets(
        doc,
        [
            "Creates a Postgres 15 instance named 'slack-assistant-pg' (db-f1-micro tier, "
            "10 GB SSD, ~$8/month).",
            "Creates a database called 'slack_assistant'.",
            "Creates a user 'app' with a generated password.",
            "Prints the DATABASE_URL the app needs and saves it to /tmp/slack-assistant-database-url.",
        ],
    )

    add_h2(doc, "Capture the DATABASE_URL it prints")
    add_para(
        doc,
        "At the end you'll see a line that starts with 'export DATABASE_URL=…'. Copy the "
        "entire URL (everything between the single quotes). Paste it into your notepad as "
        "DATABASE_URL. You'll need it in the next section.",
    )
    add_callout(
        doc,
        "Success check",
        "Run 'gcloud sql instances list'. You should see slack-assistant-pg with state "
        "RUNNABLE.",
        kind="tip",
    )
    page_break(doc)


def section_secrets(doc: Document) -> None:
    add_h1(doc, "11. Upload secrets to Secret Manager")

    add_h2(doc, "11.1 Generate the encryption keys")
    add_para(
        doc,
        "Two values must be locally generated: APP_SECRET_KEY (signs OAuth state tokens) and "
        "TOKEN_ENCRYPTION_KEY (encrypts stored OAuth refresh tokens). Run:",
    )
    add_code(doc, "uv run python scripts/gen_keys.py")
    add_para(doc, "Copy the two lines that print into your notepad.")

    add_h2(doc, "11.2 Build the secrets.env file")
    add_para(
        doc,
        "Make a private copy of the template:",
    )
    add_code(doc, "cp infrastructure/secrets.env.example infrastructure-secrets.env")
    add_para(
        doc,
        "Open infrastructure-secrets.env in your editor. Paste in every value from your "
        "notepad. Each line should look like:",
    )
    add_code(
        doc,
        "ANTHROPIC_API_KEY=\"sk-ant-…\"\n"
        "SLACK_BOT_TOKEN=\"xoxb-…\"\n"
        "SLACK_APP_TOKEN=\"xapp-…\"\n"
        "SLACK_SIGNING_SECRET=\"…\"\n"
        "SLACK_CLIENT_ID=\"…\"\n"
        "SLACK_CLIENT_SECRET=\"…\"\n"
        "GOOGLE_CLIENT_ID=\"…apps.googleusercontent.com\"\n"
        "GOOGLE_CLIENT_SECRET=\"…\"\n"
        "WRIKE_CLIENT_ID=\"…\"\n"
        "WRIKE_CLIENT_SECRET=\"…\"\n"
        "PHOENIX_API_KEY=\"…\"\n"
        "APP_SECRET_KEY=\"…\"\n"
        "TOKEN_ENCRYPTION_KEY=\"…\"\n"
        "DATABASE_URL=\"postgresql+asyncpg://app:…@/slack_assistant?host=/cloudsql/PROJECT:REGION:slack-assistant-pg\"",
    )
    add_callout(
        doc,
        "Important",
        "infrastructure-secrets.env is in .gitignore. Never commit it. After running the "
        "next step you can delete the file safely — the secrets live in Secret Manager.",
        kind="warn",
    )

    add_h2(doc, "11.3 Run the secrets uploader")
    add_code(doc, "./infrastructure/02-create-secrets.sh")
    add_para(
        doc,
        "Expected output: a line for each secret saying '+ creating SECRET_NAME' or '↻ updating'. "
        "Ends with '✅ Secrets uploaded to Secret Manager.'",
    )
    add_callout(
        doc,
        "Success check",
        "Open https://console.cloud.google.com/security/secret-manager. You should see "
        "all 14 secrets listed.",
        kind="tip",
    )
    page_break(doc)


def section_artifact_registry(doc: Document) -> None:
    add_h1(doc, "12. Build, deploy, and connect")

    add_h2(doc, "12.1 Create Artifact Registry + service accounts")
    add_code(doc, "./infrastructure/03-create-artifact-registry.sh")
    add_para(
        doc,
        "What this does: makes a Docker image repository, creates the runtime service "
        "account that Cloud Run will run as, and grants Cloud Build the rights it needs to "
        "deploy on your behalf.",
    )

    add_h2(doc, "12.2 Build the container and deploy to Cloud Run")
    add_code(doc, "./infrastructure/04-build-and-deploy.sh")
    add_para(
        doc,
        "This is the longest single step (4–7 minutes the first time). It does:",
    )
    add_numbered(
        doc,
        [
            "Submits the Dockerfile to Cloud Build, which builds the image in the cloud "
            "(no Docker needed locally).",
            "Pushes the image to your Artifact Registry repo.",
            "Deploys the image to Cloud Run with all the secrets wired in and the Cloud "
            "SQL Auth Proxy attached.",
            "Reads back the URL Cloud Run assigned (something like https://slack-assistant-"
            "abcd1234-uc.a.run.app), then re-deploys with that URL set as APP_BASE_URL so "
            "OAuth callbacks resolve correctly.",
            "Prints the URL and a curl command to check health.",
        ],
    )
    add_callout(
        doc,
        "Success check",
        "The script ends with 'Service deployed.' followed by the URL. Run the printed "
        "curl command — it should return JSON with ok=true.",
        kind="tip",
    )

    add_h2(doc, "12.3 Verify the service is alive")
    add_code(doc, "curl https://YOUR-CLOUD-RUN-URL/healthz")
    add_para(doc, "If it returns {\"ok\": true, \"ts\": \"…\"} you're good. If not, see Section 14.")
    page_break(doc)


def section_post_deploy(doc: Document) -> None:
    add_h1(doc, "13. Wire the third-party apps to your production URL")
    add_para(
        doc,
        "All three OAuth providers were configured in Sections 4–6 with the placeholder "
        "redirect URI 'https://placeholder.invalid/...'. Now that Cloud Run has assigned "
        "you a real URL, replace the placeholder in each provider.",
    )
    add_para(
        doc,
        "Throughout this section, replace <SERVICE_URL> with the URL printed by 04-build-"
        "and-deploy.sh (something like https://slack-assistant-abcd1234-uc.a.run.app).",
    )

    add_h2(doc, "13.1 Update the Slack redirect URL")
    add_numbered(
        doc,
        [
            "Open https://api.slack.com/apps and pick your 'Work Assistant' app.",
            "Left sidebar → 'OAuth & Permissions'.",
            "Under 'Redirect URLs', click 'Edit'.",
            "Remove the placeholder. Add: <SERVICE_URL>/oauth/slack/callback. Click 'Save URLs'.",
        ],
    )

    add_h2(doc, "13.2 Update the Google redirect URI")
    add_numbered(
        doc,
        [
            "Open https://console.cloud.google.com/apis/credentials.",
            "Click your OAuth 2.0 Client ID 'Slack Assistant'.",
            "Under 'Authorized redirect URIs', remove the placeholder. Add: "
            "<SERVICE_URL>/oauth/google/callback. Click 'Save'.",
        ],
    )

    add_h2(doc, "13.3 Update the Wrike redirect URI")
    add_numbered(
        doc,
        [
            "Open https://www.wrike.com/frame/oauth2/apps and pick your app.",
            "In Redirect URIs, replace the placeholder with <SERVICE_URL>/oauth/wrike/callback. Save.",
        ],
    )

    add_h2(doc, "13.4 (Optional) Submit Google for verification")
    add_para(
        doc,
        "If you set User Type to 'External' and your testers list is too small, submit the "
        "consent screen for verification. Calendar is a sensitive scope, so verification "
        "takes 1–6 weeks. During the wait, your existing test users continue to work.",
    )
    add_para(doc, "Internal Workspace deployments don't need verification.")

    add_h2(doc, "13.5 First-user smoke test")
    add_numbered(
        doc,
        [
            "In Slack, open the 'Work Assistant' app from the left sidebar (Apps section).",
            "Click 'Messages'.",
            "Type /connect. The bot DMs three buttons.",
            "Click 'Google Calendar' → authorize → see 'Connected'.",
            "Click 'Wrike' → authorize → see 'Connected'.",
            "Click 'Slack search' → authorize → see 'Connected'.",
            "Type /goodmorning. A 🌀 placeholder appears, then is replaced with the briefing.",
            "Type /wrike. Your 'New' tasks list appears with available time slots.",
            "In the Messages tab, type 'fetch my calendar for tomorrow'. The bot reacts "
            "with 👀, posts a 'Thinking…' placeholder, then replies in-thread with your "
            "events.",
            "Reply in that same thread with 'and the day after?' — the bot has context "
            "from the earlier turn.",
        ],
    )
    add_callout(
        doc,
        "Success check",
        "All three /connect buttons show ✅ Reconnect, /goodmorning runs end-to-end, "
        "/wrike successfully creates a calendar event after approval, and the "
        "conversational agent replies in-thread with proper Slack formatting "
        "(no `**` artifacts).",
        kind="tip",
    )
    page_break(doc)


def section_agent_behavior(doc: Document) -> None:
    add_h1(doc, "14. How the agent behaves — what to expect")
    add_para(
        doc,
        "Worth reading once, and worth sharing with your end users. The bot is "
        "deliberately constrained — these constraints exist for safety and to keep "
        "noise in your tools low.",
    )

    add_h2(doc, "14.1 Where it talks")
    add_bullets(
        doc,
        [
            "Conversation only happens in the bot's own DM (the 'Messages' tab in your "
            "Slack sidebar). The bot does NOT respond to channel @-mentions and does "
            "NOT post in channels.",
            "Slash commands (/connect, /goodmorning, /wrike) work from anywhere, but "
            "their replies always land in the bot's DM with you. If you run a command "
            "from a channel, you'll see a small 'Sent to our DM' note in that channel.",
            "Free-form replies in DM are posted IN-THREAD. Reply in the same thread "
            "to continue a conversation with shared context.",
        ],
    )

    add_h2(doc, "14.2 What it can do")
    add_kv_table(
        doc,
        ["Service", "Allowed", "Not allowed"],
        [
            (
                "Google Calendar",
                "List events, create events, delete events",
                "Update existing events",
            ),
            (
                "Wrike",
                "List tasks, get task details, change a task's status, post comments",
                "Create, rename, delete, or update task fields",
            ),
            (
                "Slack",
                "Search your unreplied @-mentions (read-only)",
                "Send any messages or replies on your behalf",
            ),
            (
                "Anything else",
                "—",
                "Web search, file uploads, code generation, etc.",
            ),
        ],
    )

    add_h2(doc, "14.3 The approval flow")
    add_para(
        doc,
        "Every WRITE action (create/delete event, change Wrike status, post Wrike "
        "comment) is two-phase:",
    )
    add_numbered(
        doc,
        [
            "You ask: \"schedule a focus block tomorrow 2–3pm\"",
            "Bot replies with a preview: 'I'll create an event titled \"Focus block\" "
            "from Tue May 13, 2:00pm to 3:00pm. Approve?'",
            "You reply 'yes' (or 'approve' / 'do it' / 'go ahead').",
            "Bot executes and confirms: 'Created.'",
        ],
    )
    add_para(
        doc,
        "If you say 'no' or ask for changes, the bot adjusts the proposal and "
        "re-previews — it will not act without explicit approval. There is no way to "
        "skip the preview for 'obvious' requests.",
    )

    add_h2(doc, "14.4 Context handling")
    add_para(
        doc,
        "Each Slack thread is one conversation. The agent remembers what was said in "
        "the same thread. When a thread gets long, older messages are automatically "
        "compressed into a short summary so you don't pay for ballooning token costs. "
        "Top-level messages (not replies) start a fresh conversation with no carry-over.",
    )

    add_h2(doc, "14.5 Observability")
    add_para(
        doc,
        "Every conversation, tool call, and result is recorded in Phoenix Cloud "
        "(at PHOENIX_COLLECTOR_ENDPOINT). Each trace is tagged with the user's "
        "real Slack name, email, and the channel/thread ID — useful when "
        "debugging 'why did the bot say X to user Y'. Sessions group by Slack "
        "thread so a single trace covers a whole conversation.",
    )
    page_break(doc)


def section_troubleshooting(doc: Document) -> None:
    add_h1(doc, "15. Troubleshooting")

    add_h2(doc, "15.1 'Sending messages to this app has been turned off'")
    add_para(
        doc,
        "Slack's Messages tab defaults to read-only on apps that don't explicitly "
        "enable input. The manifest in Section 4.2 sets "
        "`messages_tab_read_only_enabled: false`, but if your app was created from "
        "an older manifest:",
    )
    add_numbered(
        doc,
        [
            "Open https://api.slack.com/apps → your app.",
            "Left sidebar → 'App Home'.",
            "Under 'Messages Tab', tick 'Allow users to send Slash commands and "
            "messages from the messages tab'.",
            "Save, then on 'OAuth & Permissions' click 'Reinstall to Workspace'.",
        ],
    )

    add_h2(doc, "15.2 Cloud Build fails with 'permission denied' on Artifact Registry")
    add_para(
        doc,
        "Re-run ./infrastructure/03-create-artifact-registry.sh — it grants the Cloud Build "
        "service account the artifactregistry.writer role.",
    )

    add_h2(doc, "15.3 Cloud Run service shows 'Container failed to start'")
    add_bullets(
        doc,
        [
            "Open Cloud Run → your service → Logs tab. The startup error is usually clear.",
            "Most common: a missing required env var. The validator at startup names which one.",
            "If the log says 'Token decryption failed': you've changed TOKEN_ENCRYPTION_KEY. "
            "Roll it back, or have every user re-OAuth.",
        ],
    )

    add_h2(doc, "15.4 Slack: 'redirect_uri did not match any configured URIs'")
    add_para(
        doc,
        "Slack's redirect URL must exactly match the one configured. Check protocol "
        "(https), no trailing slash, exact path /oauth/slack/callback. Same fix applies to "
        "Google and Wrike.",
    )

    add_h2(doc, "15.5 The bot doesn't reply in its DM")
    add_bullets(
        doc,
        [
            "Cloud Run service is set to min-instances=1 (verify in console). Without it, "
            "the Slack websocket disconnects when the container scales to zero.",
            "Confirm Socket Mode is on in the Slack app's 'Socket Mode' page.",
            "Confirm SLACK_APP_TOKEN starts with xapp- and the 'connections:write' scope.",
            "Confirm `messages_tab_read_only_enabled: false` (see 15.1).",
        ],
    )

    add_h2(doc, "15.6 Phoenix shows '401 Unauthorized' for trace exports")
    add_bullets(
        doc,
        [
            "PHOENIX_API_KEY is missing or wrong. Re-copy from app.phoenix.arize.com → Settings.",
            "PHOENIX_COLLECTOR_ENDPOINT can be either the bare host "
            "(`https://app.phoenix.arize.com`) or your space URL "
            "(`https://app.phoenix.arize.com/s/<your-space>`). The code accepts both.",
            "After updating either, redeploy: ./infrastructure/04-build-and-deploy.sh --no-build",
        ],
    )

    add_h2(doc, "15.7 'invalid_grant' from Google after a few days")
    add_para(
        doc,
        "If your OAuth consent screen is in 'Testing' mode, refresh tokens expire after "
        "7 days. Either submit for verification (Section 13.4) or have the user re-OAuth.",
    )

    add_h2(doc, "15.8 /goodmorning shows nothing under Slack")
    add_para(
        doc,
        "The user hasn't completed the third /connect step (Slack search). search.messages "
        "needs an xoxp- user token; the bot token can't do it. Re-run /connect and click "
        "the Slack button.",
    )

    add_h2(doc, "15.9 /goodmorning Wrike section is empty but tasks exist")
    add_para(
        doc,
        "Most likely your workspace has multiple Wrike workflows (one per "
        "folder/project), each with its own 'New' status that has a different ID. The "
        "code resolves ALL matching status IDs across every workflow automatically — "
        "no config change needed. If you previously deployed a build that only picked "
        "the first one, run this once to clear the stale cache: "
        "`gcloud sql connect <instance> --user=app --database=slack_assistant` then "
        "`DELETE FROM workflowstatuscache;`. The next /goodmorning re-resolves.",
    )

    add_h2(doc, "15.10 Bot replies have raw '**bold**' or '## headings'")
    add_para(
        doc,
        "Should not happen — there's a Markdown→mrkdwn post-processor on every reply. "
        "If you see it, it likely means a deploy regression or the converter was bypassed. "
        "Confirm the latest image is deployed and the agent's system prompt mentions "
        "Slack mrkdwn formatting.",
    )

    add_h2(doc, "15.11 Cost is higher than expected")
    add_bullets(
        doc,
        [
            "Open https://console.cloud.google.com/billing/reports. Filter by service.",
            "Cloud Run 'always-on' is the largest cost. If you can tolerate a 5-second "
            "cold-start delay on first user message, drop min-instances to 0 — but be aware "
            "the Slack websocket will reconnect every cold start.",
            "Cloud SQL is fixed at ~$8/mo on db-f1-micro; only goes up if you upgrade tier.",
            "Anthropic spend depends on usage; review at console.anthropic.com.",
        ],
    )
    page_break(doc)


def section_runbook(doc: Document) -> None:
    add_h1(doc, "16. Day-2 operations runbook")

    add_h2(doc, "16.1 Update a single secret")
    add_para(doc, "Use the helper script:")
    add_code(doc, "./infrastructure/05-update-secret.sh ANTHROPIC_API_KEY \"sk-ant-NEW-KEY\"")
    add_para(doc, "Then redeploy so Cloud Run picks up the new version:")
    add_code(doc, "./infrastructure/04-build-and-deploy.sh --no-build")

    add_h2(doc, "16.2 Push a code change")
    add_code(doc, "./infrastructure/04-build-and-deploy.sh")
    add_para(doc, "This rebuilds the image and rolls out the new revision.")

    add_h2(doc, "16.3 View live logs")
    add_code(
        doc,
        "gcloud run services logs tail slack-assistant --region=us-central1",
    )

    add_h2(doc, "16.4 Roll back a deploy")
    add_para(doc, "List revisions and route 100% of traffic to a previous one:")
    add_code(
        doc,
        "gcloud run revisions list --service=slack-assistant --region=us-central1\n"
        "gcloud run services update-traffic slack-assistant \\\n"
        "    --region=us-central1 --to-revisions=<REVISION_NAME>=100",
    )

    add_h2(doc, "16.5 Continuous deployment from GitHub")
    add_para(
        doc,
        "Open https://console.cloud.google.com/cloud-build/triggers and connect your "
        "repository. Create a trigger that runs cloudbuild.yaml on every push to main. The "
        "substitutions to set on the trigger:",
    )
    add_kv_table(
        doc,
        ["Substitution", "Value"],
        [
            ("_REGION", "us-central1"),
            ("_SERVICE_NAME", "slack-assistant"),
            ("_AR_REPO", "slack-assistant"),
            ("_SQL_INSTANCE", "PROJECT:REGION:slack-assistant-pg"),
            ("_APP_BASE_URL", "https://YOUR-SERVICE.run.app"),
        ],
    )

    add_h2(doc, "16.6 Tear it all down")
    add_callout(
        doc,
        "Danger",
        "This deletes everything — Cloud Run, Cloud SQL (and its data), secrets, "
        "service accounts. Only run if you've confirmed the project is no longer needed.",
        kind="danger",
    )
    add_code(doc, "./infrastructure/99-tear-down.sh")
    page_break(doc)


def section_security(doc: Document) -> None:
    add_h1(doc, "17. Security checklist")
    add_bullets(
        doc,
        [
            "TOKEN_ENCRYPTION_KEY is stored only in Secret Manager. Never log it. If it leaks, "
            "rotate it AND have every user re-OAuth.",
            "infrastructure-secrets.env is in .gitignore. Confirm with: git check-ignore "
            "infrastructure-secrets.env (should output the path).",
            "The Cloud Run service runtime SA has only three roles: cloudsql.client, "
            "secretmanager.secretAccessor, logging.logWriter. No project-wide admin.",
            "Cloud SQL has no public IP. Only Cloud Run reaches it via the Auth Proxy "
            "Unix socket.",
            "Cloud Run /oauth/* paths must be reachable from a user's browser, which is why "
            "the service is --allow-unauthenticated. The bot itself only acts on signed "
            "Slack events (signed with SLACK_SIGNING_SECRET) and signed OAuth state tokens "
            "(signed with APP_SECRET_KEY).",
            "Set up a Cloud Logging-based alert on token decryption failures — they "
            "indicate either key rotation or tampering.",
        ],
    )
    page_break(doc)


def section_appendix(doc: Document) -> None:
    add_h1(doc, "18. Appendix — Reference")

    add_h2(doc, "18.1 Where each value ends up")
    add_kv_table(
        doc,
        ["Value", "Source", "Stored as"],
        [
            ("SLACK_BOT_TOKEN", "Slack OAuth & Permissions", "Secret Manager"),
            ("SLACK_APP_TOKEN", "Slack Basic Information → App-Level Tokens", "Secret Manager"),
            ("SLACK_SIGNING_SECRET", "Slack Basic Information → App Credentials", "Secret Manager"),
            ("SLACK_CLIENT_ID", "Slack Basic Information → App Credentials", "Secret Manager"),
            ("SLACK_CLIENT_SECRET", "Slack Basic Information → App Credentials", "Secret Manager"),
            ("GOOGLE_CLIENT_ID", "GCP Console → Credentials → OAuth Client", "Secret Manager"),
            ("GOOGLE_CLIENT_SECRET", "GCP Console → Credentials → OAuth Client", "Secret Manager"),
            ("WRIKE_CLIENT_ID", "Wrike → OAuth apps → your app", "Secret Manager"),
            ("WRIKE_CLIENT_SECRET", "Wrike → OAuth apps → your app", "Secret Manager"),
            ("ANTHROPIC_API_KEY", "console.anthropic.com → API Keys", "Secret Manager"),
            ("PHOENIX_API_KEY", "app.phoenix.arize.com → Settings", "Secret Manager"),
            ("APP_SECRET_KEY", "scripts/gen_keys.py output", "Secret Manager"),
            ("TOKEN_ENCRYPTION_KEY", "scripts/gen_keys.py output", "Secret Manager"),
            ("DATABASE_URL", "Printed by 01-create-cloud-sql.sh", "Secret Manager"),
        ],
    )

    add_h2(doc, "18.2 Redirect URIs at a glance")
    add_kv_table(
        doc,
        ["Provider", "Redirect URI"],
        [
            ("Slack", "<SERVICE_URL>/oauth/slack/callback"),
            ("Google", "<SERVICE_URL>/oauth/google/callback"),
            ("Wrike", "<SERVICE_URL>/oauth/wrike/callback"),
        ],
    )

    add_h2(doc, "18.3 Quick command reference")
    add_kv_table(
        doc,
        ["Action", "Command"],
        [
            ("Tail logs", "gcloud run services logs tail slack-assistant --region=us-central1"),
            ("Inspect SQL", "gcloud sql connect slack-assistant-pg --user=app --database=slack_assistant"),
            ("Show secrets", "gcloud secrets list"),
            ("Update secret", "./infrastructure/05-update-secret.sh NAME 'value'"),
            ("Rebuild + deploy", "./infrastructure/04-build-and-deploy.sh"),
            ("Redeploy only", "./infrastructure/04-build-and-deploy.sh --no-build"),
            ("Health check", "curl <SERVICE_URL>/healthz"),
            ("Tear down", "./infrastructure/99-tear-down.sh"),
        ],
    )


# ── Main ───────────────────────────────────────────────────────────────────


def build() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for s in doc.sections:
        s.left_margin = Cm(2.0)
        s.right_margin = Cm(2.0)
        s.top_margin = Cm(2.2)
        s.bottom_margin = Cm(2.2)

    section_cover(doc)
    section_how_to_use(doc)
    section_overview(doc)
    section_accounts(doc)
    section_local_tools(doc)
    section_slack_app(doc)
    section_google(doc)
    section_wrike(doc)
    section_anthropic(doc)
    section_phoenix(doc)
    section_gcp_setup(doc)
    section_cloud_sql(doc)
    section_secrets(doc)
    section_artifact_registry(doc)
    section_post_deploy(doc)
    section_agent_behavior(doc)
    section_troubleshooting(doc)
    section_runbook(doc)
    section_security(doc)
    section_appendix(doc)

    doc.save(OUT_PATH)
    return OUT_PATH


if __name__ == "__main__":
    out = build()
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
