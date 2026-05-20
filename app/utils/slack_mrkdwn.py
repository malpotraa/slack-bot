"""Convert standard Markdown into Slack `mrkdwn`.

Slack's `chat.postMessage` text uses *mrkdwn*, which differs from CommonMark:

  Bold       *bold*           (single asterisks; **bold** does NOT bold)
  Italic     _italic_
  Strike     ~strike~
  Code       `code`           (same)
  Block      ```code```       (same)
  Bullets    • item           (literal bullet — `- ` and `* ` don't render as lists)
  Quote      > line           (same)
  Link       <https://x|text> (NOT [text](url))
  Headings   — none — use *bold* on its own line instead

This module is a defensive post-processor. The agent's system prompt also
asks the model to output mrkdwn directly; this catches drift.
"""

from __future__ import annotations

import re

# Order matters: protect code regions first so we don't transform inside them.

_FENCE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*([^\n*][^*]*?)\*\*")  # **bold**
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")  # [text](url)
_BULLET_RE = re.compile(r"^[ \t]*[-*]\s+", re.MULTILINE)

_SLACK_USER_WITH_LABEL_RE = re.compile(r"<@([A-Z0-9]+)\|([^>]+)>")
_SLACK_USER_RE = re.compile(r"<@([A-Z0-9]+)>")
_SLACK_CHANNEL_WITH_LABEL_RE = re.compile(r"<#([A-Z0-9]+)\|([^>]+)>")
_SLACK_CHANNEL_RE = re.compile(r"<#([A-Z0-9]+)>")
_SLACK_SPECIAL_WITH_LABEL_RE = re.compile(r"<!(?:subteam\^[A-Z0-9]+|[^>|]+)\|([^>]+)>")
_SLACK_SPECIAL_RE = re.compile(r"<!(channel|here|everyone)>")
_SLACK_LINK_WITH_LABEL_RE = re.compile(r"<(https?://[^>|]+)\|([^>]+)>")
_SLACK_BARE_LINK_RE = re.compile(r"<(https?://[^>]+)>")


def escape_slack_text(text: object) -> str:
    """Escape user/provider text before embedding it in Slack mrkdwn."""
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_markup_to_text(text: object) -> str:
    """Convert Slack's internal markup tokens into readable plain text."""
    if text is None:
        return ""
    body = str(text)
    body = _SLACK_LINK_WITH_LABEL_RE.sub(lambda m: m.group(2), body)
    body = _SLACK_BARE_LINK_RE.sub(lambda m: m.group(1), body)
    body = _SLACK_USER_WITH_LABEL_RE.sub(lambda m: f"@{m.group(2)}", body)
    body = _SLACK_USER_RE.sub(lambda m: f"@{m.group(1)}", body)
    body = _SLACK_CHANNEL_WITH_LABEL_RE.sub(lambda m: f"#{m.group(2)}", body)
    body = _SLACK_CHANNEL_RE.sub(lambda m: f"#{m.group(1)}", body)
    body = _SLACK_SPECIAL_WITH_LABEL_RE.sub(lambda m: m.group(1), body)
    body = _SLACK_SPECIAL_RE.sub(lambda m: f"@{m.group(1)}", body)
    return body


def quote_slack_text(text: object) -> str:
    """Render untrusted text as a Slack quote without allowing control syntax."""
    escaped = escape_slack_text(text)
    return "\n".join(f"> {line}" for line in escaped.splitlines()) or ">"


def _protect(text: str) -> tuple[str, list[str]]:
    """Replace code regions with placeholders so we don't transform inside them."""
    saved: list[str] = []

    def stash(match: re.Match) -> str:
        saved.append(match.group(0))
        return f"\x00CODE{len(saved) - 1}\x00"

    text = _FENCE_RE.sub(stash, text)
    text = _INLINE_CODE_RE.sub(stash, text)
    return text, saved


def _restore(text: str, saved: list[str]) -> str:
    for i, original in enumerate(saved):
        text = text.replace(f"\x00CODE{i}\x00", original)
    return text


def to_slack_mrkdwn(text: str) -> str:
    """Best-effort Markdown → Slack mrkdwn."""
    if not text:
        return text

    body, saved = _protect(text)

    # Headings (# / ## / ### / etc.) → bold line
    body = _HEADING_RE.sub(lambda m: f"*{m.group(2)}*", body)

    # **bold** → *bold*
    body = _BOLD_RE.sub(lambda m: f"*{m.group(1)}*", body)

    # [text](url) → <url|text>
    body = _LINK_RE.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", body)

    # Bullet lines: leading `- ` or `* ` → `• `
    body = _BULLET_RE.sub("• ", body)

    return _restore(body, saved)
