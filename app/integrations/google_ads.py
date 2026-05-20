"""Per-user Google Ads REST client and deterministic KPI calculations."""

from __future__ import annotations

import calendar
import asyncio
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from app.config import settings
from app.db import tokens as token_repo
from app.db.engine import session_scope
from app.oauth import google_ads as google_ads_oauth
from app.oauth._logging import safe_error_summary
from app.utils.http_client import shared_async_client


class GoogleAdsNotConnectedError(RuntimeError):
    pass


class GoogleAdsConfigError(RuntimeError):
    pass


_CUSTOMER_ID_RE = re.compile(r"^\d{10,}$")
_API_VERSION_RE = re.compile(r"^v\d+$")
_ACCOUNT_CACHE_TTL_SECONDS = 300
_ACCOUNT_CACHE: dict[tuple[int, str], tuple[float, list["GoogleAdsAccount"]]] = {}


def normalize_customer_id(value: str | int | None) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _api_version() -> str:
    version = (settings.google_ads_api_version or "v22").strip()
    if not _API_VERSION_RE.match(version):
        raise GoogleAdsConfigError("GOOGLE_ADS_API_VERSION must look like v22.")
    return version


@dataclass(frozen=True)
class GoogleAdsAccount:
    customer_id: str
    name: str
    time_zone: str
    currency_code: str


@dataclass(frozen=True)
class DateWindow:
    label: str
    start: date
    end: date

    def as_dict(self) -> dict[str, str]:
        return {
            "label": self.label,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }


@dataclass(frozen=True)
class KpiWindows:
    last_7: DateWindow
    previous_7: DateWindow
    mtd: DateWindow | None
    previous_mtd: DateWindow | None
    anchor_date: date

    def as_dict(self) -> dict[str, Any]:
        return {
            "last_7": self.last_7.as_dict(),
            "previous_7": self.previous_7.as_dict(),
            "mtd": self.mtd.as_dict() if self.mtd else None,
            "previous_mtd": self.previous_mtd.as_dict() if self.previous_mtd else None,
            "anchor_date": self.anchor_date.isoformat(),
        }


@dataclass
class GoogleAdsAuth:
    access_token: str
    login_customer_id: str
    user_id: int

    @property
    def base_url(self) -> str:
        return f"https://googleads.googleapis.com/{_api_version()}"


async def _auth_for(user_id: int) -> GoogleAdsAuth:
    login_customer_id = normalize_customer_id(settings.google_ads_login_customer_id)
    if not login_customer_id:
        raise GoogleAdsConfigError("GOOGLE_ADS_LOGIN_CUSTOMER_ID is required for /kpi.")
    if not settings.google_ads_developer_token:
        raise GoogleAdsConfigError("GOOGLE_ADS_DEVELOPER_TOKEN is required for /kpi.")

    async with session_scope() as session:
        tok = await token_repo.get_google_ads_token(session, user_id)
        if tok is None:
            raise GoogleAdsNotConnectedError("Google Ads not connected. Run /connect.")
        refresh_token, access_token = token_repo.decrypt_google_ads(tok)
        expires_at = tok.access_token_expires_at

    if not access_token or expires_at is None:
        expires_at = datetime.fromtimestamp(0, UTC)
    elif expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if expires_at <= datetime.now(UTC) + timedelta(seconds=30):
        logger.info(f"Refreshing Google Ads token for user {user_id}")
        body = await google_ads_oauth.refresh_access_token(refresh_token)
        access_token = body["access_token"]
        new_refresh = body.get("refresh_token", refresh_token)
        new_expires = google_ads_oauth.expires_at_from_seconds(body.get("expires_in"))
        async with session_scope() as session:
            await token_repo.upsert_google_ads_token(
                session,
                user_id=user_id,
                refresh_token=new_refresh,
                access_token=access_token,
                access_token_expires_at=new_expires,
                scopes=body.get("scope", ""),
                google_email=None,
            )

    return GoogleAdsAuth(
        access_token=access_token,
        login_customer_id=login_customer_id,
        user_id=user_id,
    )


class GoogleAdsClient:
    def __init__(self, auth: GoogleAdsAuth):
        self._auth = auth

    @classmethod
    async def for_user(cls, user_id: int) -> GoogleAdsClient:
        return cls(await _auth_for(user_id))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth.access_token}",
            "developer-token": settings.google_ads_developer_token,
            "login-customer-id": self._auth.login_customer_id,
            "Content-Type": "application/json",
        }

    async def search_stream(self, customer_id: str, query: str) -> list[dict[str, Any]]:
        cid = normalize_customer_id(customer_id)
        if not _CUSTOMER_ID_RE.match(cid):
            raise ValueError("Invalid Google Ads customer ID.")
        url = f"{self._auth.base_url}/customers/{cid}/googleAds:searchStream"
        resp = await shared_async_client(timeout=45).post(
            url, headers=self._headers(), json={"query": query}
        )
        if resp.status_code != 200:
            logger.error(f"Google Ads searchStream failed: {safe_error_summary(resp)}")
        resp.raise_for_status()
        batches = resp.json()
        rows: list[dict[str, Any]] = []
        if isinstance(batches, list):
            for batch in batches:
                rows.extend(batch.get("results") or [])
        elif isinstance(batches, dict):
            rows.extend(batches.get("results") or [])
        return rows

    async def mcc_accounts(self) -> list[GoogleAdsAccount]:
        cache_key = (self._auth.user_id, self._auth.login_customer_id)
        cached = _ACCOUNT_CACHE.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[0] < _ACCOUNT_CACHE_TTL_SECONDS:
            return list(cached[1])

        query = """
            SELECT
              customer_client.id,
              customer_client.descriptive_name,
              customer_client.status,
              customer_client.manager,
              customer_client.hidden,
              customer_client.time_zone,
              customer_client.currency_code
            FROM customer_client
            WHERE customer_client.status = 'ENABLED'
              AND customer_client.manager = FALSE
              AND customer_client.hidden = FALSE
        """
        rows = await self.search_stream(self._auth.login_customer_id, query)
        accounts: list[GoogleAdsAccount] = []
        for row in rows:
            cc = row.get("customerClient") or row.get("customer_client") or {}
            customer_id = normalize_customer_id(cc.get("id"))
            if not customer_id:
                continue
            accounts.append(
                GoogleAdsAccount(
                    customer_id=customer_id,
                    name=str(cc.get("descriptiveName") or cc.get("descriptive_name") or ""),
                    time_zone=str(cc.get("timeZone") or cc.get("time_zone") or "UTC"),
                    currency_code=str(
                        cc.get("currencyCode") or cc.get("currency_code") or "USD"
                    ),
                )
            )
        _ACCOUNT_CACHE[cache_key] = (now, list(accounts))
        return accounts

    async def search_accounts(self, query_text: str, *, limit: int = 25) -> list[GoogleAdsAccount]:
        needle = " ".join(query_text.split()).casefold()
        if not needle:
            return []
        matches = [a for a in await self.mcc_accounts() if needle in a.name.casefold()]
        matches.sort(key=lambda a: (0 if a.name.casefold() == needle else 1, a.name.casefold()))
        return matches[:limit]

    async def account_by_id(self, customer_id: str) -> GoogleAdsAccount | None:
        cid = normalize_customer_id(customer_id)
        if not _CUSTOMER_ID_RE.match(cid):
            return None
        query = f"""
            SELECT
              customer_client.id,
              customer_client.descriptive_name,
              customer_client.status,
              customer_client.manager,
              customer_client.hidden,
              customer_client.time_zone,
              customer_client.currency_code
            FROM customer_client
            WHERE customer_client.id = {cid}
              AND customer_client.status = 'ENABLED'
              AND customer_client.manager = FALSE
              AND customer_client.hidden = FALSE
        """
        rows = await self.search_stream(self._auth.login_customer_id, query)
        if not rows:
            return None
        cc = rows[0].get("customerClient") or rows[0].get("customer_client") or {}
        return GoogleAdsAccount(
            customer_id=cid,
            name=str(cc.get("descriptiveName") or cc.get("descriptive_name") or ""),
            time_zone=str(cc.get("timeZone") or cc.get("time_zone") or "UTC"),
            currency_code=str(cc.get("currencyCode") or cc.get("currency_code") or "USD"),
        )

    async def campaign_metrics(
        self, customer_id: str, window: DateWindow
    ) -> list[dict[str, Any]]:
        query = f"""
            SELECT
              campaign.id,
              campaign.name,
              campaign.advertising_channel_type,
              campaign.status,
              metrics.cost_micros,
              metrics.conversions,
              metrics.conversions_value,
              metrics.clicks,
              metrics.impressions
            FROM campaign
            WHERE campaign.status = 'ENABLED'
              AND segments.date BETWEEN '{window.start.isoformat()}' AND '{window.end.isoformat()}'
        """
        rows = await self.search_stream(customer_id, query)
        return [_campaign_row(row) for row in rows]

    async def keyword_metrics(
        self, customer_id: str, window: DateWindow, *, limit: int = 250
    ) -> list[dict[str, Any]]:
        query = f"""
            SELECT
              campaign.id,
              campaign.name,
              ad_group_criterion.keyword.text,
              ad_group_criterion.keyword.match_type,
              ad_group_criterion.status,
              metrics.cost_micros,
              metrics.conversions,
              metrics.clicks,
              metrics.impressions
            FROM keyword_view
            WHERE campaign.status = 'ENABLED'
              AND ad_group_criterion.status = 'ENABLED'
              AND segments.date BETWEEN '{window.start.isoformat()}' AND '{window.end.isoformat()}'
            ORDER BY metrics.cost_micros DESC
            LIMIT {int(limit)}
        """
        rows = await self.search_stream(customer_id, query)
        return [_keyword_row(row) for row in rows]


def compute_windows(time_zone: str, *, today: date | None = None) -> KpiWindows:
    try:
        tz = ZoneInfo(time_zone)
    except Exception:
        tz = ZoneInfo("UTC")
    account_today = today or datetime.now(tz).date()
    yesterday = account_today - timedelta(days=1)

    last_7 = DateWindow("last_7", yesterday - timedelta(days=6), yesterday)
    previous_7 = DateWindow("previous_7", yesterday - timedelta(days=13), yesterday - timedelta(days=7))

    mtd: DateWindow | None = None
    previous_mtd: DateWindow | None = None
    if yesterday.month == account_today.month and yesterday.year == account_today.year:
        current_start = yesterday.replace(day=1)
        mtd = DateWindow("mtd", current_start, yesterday)
        prev_year, prev_month = _previous_month(account_today.year, account_today.month)
        prev_day = min(yesterday.day, calendar.monthrange(prev_year, prev_month)[1])
        previous_mtd = DateWindow(
            "previous_mtd",
            date(prev_year, prev_month, 1),
            date(prev_year, prev_month, prev_day),
        )

    return KpiWindows(
        last_7=last_7,
        previous_7=previous_7,
        mtd=mtd,
        previous_mtd=previous_mtd,
        anchor_date=yesterday,
    )


async def build_kpi_report(
    *,
    user_id: int,
    customer_id: str,
    mode: str,
    frozen_windows: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metric_mode = mode if mode in {"cpa", "roas"} else "cpa"
    client = await GoogleAdsClient.for_user(user_id)
    account = await client.account_by_id(customer_id)
    if account is None:
        raise PermissionError("Selected account is not an enabled client under the configured MCC.")
    windows = _windows_from_payload(frozen_windows) or compute_windows(account.time_zone)

    tasks = [
        _campaign_pair(
            client, account.customer_id, windows.last_7, windows.previous_7, metric_mode
        )
    ]
    include_mom = bool(windows.mtd and windows.previous_mtd)
    if windows.mtd and windows.previous_mtd:
        tasks.append(
            _campaign_pair(
                client, account.customer_id, windows.mtd, windows.previous_mtd, metric_mode
            )
        )
        tasks.append(
            _keyword_drivers(
                client, account.customer_id, windows.mtd, windows.previous_mtd
            )
        )

    results = await asyncio.gather(*tasks)
    last_7_rows, previous_7_rows = results[0]
    campaign_ids = set(last_7_rows) | set(previous_7_rows)

    mtd_rows: dict[str, dict[str, Any]] = {}
    previous_mtd_rows: dict[str, dict[str, Any]] = {}
    keyword_drivers: dict[str, list[dict[str, Any]]] = {}
    if include_mom:
        mtd_rows, previous_mtd_rows = results[1]
        campaign_ids |= set(mtd_rows) | set(previous_mtd_rows)
        keyword_drivers = results[2]

    campaigns = []
    for campaign_id in campaign_ids:
        current = last_7_rows.get(campaign_id) or previous_7_rows.get(campaign_id)
        name = (current or mtd_rows.get(campaign_id) or previous_mtd_rows.get(campaign_id) or {}).get(
            "name", f"Campaign {campaign_id}"
        )
        channel = (current or mtd_rows.get(campaign_id) or previous_mtd_rows.get(campaign_id) or {}).get(
            "channel", ""
        )
        row = {
            "campaign_id": campaign_id,
            "name": name,
            "channel": channel,
            "wow": _comparison(
                last_7_rows.get(campaign_id), previous_7_rows.get(campaign_id), metric_mode
            ),
            "mom": _comparison(
                mtd_rows.get(campaign_id), previous_mtd_rows.get(campaign_id), metric_mode
            )
            if windows.mtd and windows.previous_mtd
            else None,
            "drivers": keyword_drivers.get(campaign_id, []),
        }
        campaigns.append(row)

    campaigns.sort(
        key=lambda c: (
            -float(((c.get("wow") or {}).get("current") or {}).get("cost") or 0),
            str(c.get("name") or ""),
        )
    )

    account_total = {
        "name": "Overall",
        "wow": _comparison(_sum_rows(last_7_rows.values()), _sum_rows(previous_7_rows.values()), metric_mode),
        "mom": _comparison(_sum_rows(mtd_rows.values()), _sum_rows(previous_mtd_rows.values()), metric_mode)
        if windows.mtd and windows.previous_mtd
        else None,
        "drivers": [],
    }

    return {
        "account": {
            "customer_id": account.customer_id,
            "name": account.name,
            "time_zone": account.time_zone,
            "currency_code": account.currency_code,
        },
        "mode": metric_mode,
        "windows": windows.as_dict(),
        "campaigns": campaigns,
        "overall": account_total,
    }


async def _campaign_pair(
    client: GoogleAdsClient,
    customer_id: str,
    current: DateWindow,
    previous: DateWindow,
    mode: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    current_raw, previous_raw = await asyncio.gather(
        client.campaign_metrics(customer_id, current),
        client.campaign_metrics(customer_id, previous),
    )
    current_rows = {r["campaign_id"]: _with_metric(r, mode) for r in current_raw}
    previous_rows = {r["campaign_id"]: _with_metric(r, mode) for r in previous_raw}
    return current_rows, previous_rows


async def _keyword_drivers(
    client: GoogleAdsClient,
    customer_id: str,
    current: DateWindow,
    previous: DateWindow,
) -> dict[str, list[dict[str, Any]]]:
    try:
        current_rows, previous_rows = await asyncio.gather(
            client.keyword_metrics(customer_id, current),
            client.keyword_metrics(customer_id, previous),
        )
    except Exception as exc:
        logger.warning(f"Google Ads keyword-driver fetch failed: {exc}")
        return {}
    previous_by_key = {
        (r["campaign_id"], r["keyword"].casefold(), r["match_type"]): r for r in previous_rows
    }
    drivers: dict[str, list[dict[str, Any]]] = {}
    for row in current_rows:
        key = (row["campaign_id"], row["keyword"].casefold(), row["match_type"])
        prev = previous_by_key.get(key) or {
            "cost": 0.0,
            "conversions": 0.0,
            "clicks": 0,
            "impressions": 0,
        }
        item = {
            "keyword": row["keyword"],
            "match_type": row["match_type"],
            "current": row,
            "previous": prev,
            "cost_change": row["cost"] - float(prev.get("cost") or 0),
            "conversion_change": row["conversions"] - float(prev.get("conversions") or 0),
        }
        score = abs(item["cost_change"]) + abs(item["conversion_change"]) * 100
        item["score"] = score
        drivers.setdefault(row["campaign_id"], []).append(item)
    for campaign_id, items in drivers.items():
        items.sort(key=lambda i: -float(i.get("score") or 0))
        drivers[campaign_id] = items[:3]
    return drivers


def _campaign_row(row: dict[str, Any]) -> dict[str, Any]:
    campaign = row.get("campaign") or {}
    metrics = row.get("metrics") or {}
    return {
        "campaign_id": str(campaign.get("id") or ""),
        "name": str(campaign.get("name") or ""),
        "channel": str(campaign.get("advertisingChannelType") or ""),
        "cost": _micros_to_units(metrics.get("costMicros")),
        "conversions": _float(metrics.get("conversions")),
        "conversion_value": _float(metrics.get("conversionsValue")),
        "clicks": int(_float(metrics.get("clicks"))),
        "impressions": int(_float(metrics.get("impressions"))),
    }


def _keyword_row(row: dict[str, Any]) -> dict[str, Any]:
    campaign = row.get("campaign") or {}
    ad_group_criterion = row.get("adGroupCriterion") or {}
    keyword = ad_group_criterion.get("keyword") or {}
    metrics = row.get("metrics") or {}
    return {
        "campaign_id": str(campaign.get("id") or ""),
        "campaign_name": str(campaign.get("name") or ""),
        "keyword": str(keyword.get("text") or ""),
        "match_type": str(keyword.get("matchType") or ""),
        "cost": _micros_to_units(metrics.get("costMicros")),
        "conversions": _float(metrics.get("conversions")),
        "clicks": int(_float(metrics.get("clicks"))),
        "impressions": int(_float(metrics.get("impressions"))),
    }


def _with_metric(row: dict[str, Any], mode: str) -> dict[str, Any]:
    out = dict(row)
    out["metric_value"] = _metric_value(row, mode)
    out["cpc"] = row["cost"] / row["clicks"] if row["clicks"] else None
    out["conv_rate"] = row["conversions"] / row["clicks"] if row["clicks"] else None
    return out


def _comparison(
    current: dict[str, Any] | None, previous: dict[str, Any] | None, mode: str
) -> dict[str, Any]:
    current = _with_metric(current or _empty_metrics(), mode)
    previous = _with_metric(previous or _empty_metrics(), mode)
    cur_value = current.get("metric_value")
    prev_value = previous.get("metric_value")
    return {
        "current": current,
        "previous": previous,
        "change_pct": _pct_change(cur_value, prev_value),
        "metric": mode,
    }


def _sum_rows(rows: Any) -> dict[str, Any]:
    total = _empty_metrics()
    total["campaign_id"] = "overall"
    total["name"] = "Overall"
    for row in rows:
        total["cost"] += float(row.get("cost") or 0)
        total["conversions"] += float(row.get("conversions") or 0)
        total["conversion_value"] += float(row.get("conversion_value") or 0)
        total["clicks"] += int(row.get("clicks") or 0)
        total["impressions"] += int(row.get("impressions") or 0)
    return total


def _empty_metrics() -> dict[str, Any]:
    return {
        "campaign_id": "",
        "name": "",
        "channel": "",
        "cost": 0.0,
        "conversions": 0.0,
        "conversion_value": 0.0,
        "clicks": 0,
        "impressions": 0,
    }


def _metric_value(row: dict[str, Any], mode: str) -> float | None:
    cost = float(row.get("cost") or 0)
    conversions = float(row.get("conversions") or 0)
    conversion_value = float(row.get("conversion_value") or 0)
    if mode == "roas":
        return conversion_value / cost if cost > 0 else None
    return cost / conversions if conversions > 0 else None


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return ((current - previous) / previous) * 100


def _micros_to_units(value: Any) -> float:
    return _float(value) / 1_000_000


def _float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _windows_from_payload(payload: dict[str, Any] | None) -> KpiWindows | None:
    if not payload:
        return None
    try:
        last_7 = _window_from_dict(payload["last_7"])
        previous_7 = _window_from_dict(payload["previous_7"])
        mtd = _window_from_dict(payload["mtd"]) if payload.get("mtd") else None
        previous_mtd = (
            _window_from_dict(payload["previous_mtd"]) if payload.get("previous_mtd") else None
        )
        return KpiWindows(
            last_7=last_7,
            previous_7=previous_7,
            mtd=mtd,
            previous_mtd=previous_mtd,
            anchor_date=date.fromisoformat(payload["anchor_date"]),
        )
    except Exception:
        return None


def _window_from_dict(data: dict[str, str]) -> DateWindow:
    return DateWindow(
        label=data["label"],
        start=date.fromisoformat(data["start"]),
        end=date.fromisoformat(data["end"]),
    )
