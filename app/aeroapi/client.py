from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from app.aeroapi.errors import AeroApiError
from app.config import Settings
from app.storage.db import Database
from app.tracking.quota import QuotaManager

LOGGER = logging.getLogger(__name__)


class AeroApiClient:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        quota: QuotaManager,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.quota = quota
        self._semaphore = asyncio.Semaphore(settings.aeroapi_max_concurrency)
        timeout = httpx.Timeout(
            connect=settings.http_connect_timeout_seconds,
            read=settings.http_read_timeout_seconds,
            write=settings.http_read_timeout_seconds,
            pool=settings.http_connect_timeout_seconds,
        )
        limits = httpx.Limits(
            max_connections=settings.aeroapi_max_concurrency,
            max_keepalive_connections=settings.aeroapi_max_concurrency,
        )
        self.http = httpx.AsyncClient(
            base_url=settings.aeroapi_base_url,
            timeout=timeout,
            limits=limits,
            headers={
                "Accept": "application/json",
                "User-Agent": "UnderpigFlightBot/0.2",
                "x-apikey": settings.aeroapi_key,
            },
            transport=transport,
        )

    async def initialize(self) -> None:
        fingerprint = hashlib.sha256(self.settings.aeroapi_key.encode()).hexdigest()[:16]
        stored = await self.db.get_service_state("aeroapi_key_fingerprint")
        if stored != fingerprint:
            await self.db.set_service_state("aeroapi_circuit", "closed")
            await self.db.set_service_state("aeroapi_key_fingerprint", fingerprint)

    async def close(self) -> None:
        await self.http.aclose()

    async def search_flights(
        self,
        flight_designator: str,
        *,
        flight_id: int | None,
        trigger_type: str,
        priority: int,
    ) -> dict[str, Any]:
        return await self._request(
            f"/flights/{quote(flight_designator, safe='')}",
            params={"ident_type": "designator", "max_pages": 1},
            endpoint_name="flights_by_ident",
            flight_id=flight_id,
            trigger_type=trigger_type,
            priority=priority,
        )

    async def get_flight(
        self,
        provider_flight_id: str,
        *,
        flight_id: int | None,
        trigger_type: str,
        priority: int,
    ) -> dict[str, Any]:
        return await self._request(
            f"/flights/{quote(provider_flight_id, safe='')}",
            params={"ident_type": "fa_flight_id", "max_pages": 1},
            endpoint_name="flight_by_fa_id",
            flight_id=flight_id,
            trigger_type=trigger_type,
            priority=priority,
        )

    async def search_schedules(
        self,
        flight_iata: str,
        *,
        flight_date: str,
        flight_id: int | None,
        trigger_type: str,
        priority: int,
    ) -> dict[str, Any]:
        airline, number = split_flight_designator(flight_iata)
        selected = date.fromisoformat(flight_date)
        start = selected - timedelta(days=1)
        end = selected + timedelta(days=2)
        return await self._request(
            f"/schedules/{start.isoformat()}/{end.isoformat()}",
            params={
                "airline": airline,
                "flight_number": int(number),
                "include_codeshares": "true",
                "include_regional": "true",
                "max_pages": 1,
            },
            endpoint_name="schedules",
            flight_id=flight_id,
            trigger_type=trigger_type,
            priority=priority,
        )

    async def get_airport(
        self,
        airport_code: str,
        *,
        flight_id: int | None,
        priority: int,
    ) -> dict[str, Any]:
        code = airport_code.upper()
        cache_key = f"aeroapi_airport:{code}"
        cached = await self.db.get_service_state(cache_key)
        if cached:
            try:
                payload = json.loads(cached)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                pass
        payload = await self._request(
            f"/airports/{quote(code, safe='')}",
            params={},
            endpoint_name="airport",
            flight_id=flight_id,
            trigger_type="airport_metadata",
            priority=priority,
        )
        await self.db.set_service_state(cache_key, json.dumps(payload, ensure_ascii=False))
        return payload

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any],
        endpoint_name: str,
        flight_id: int | None,
        trigger_type: str,
        priority: int,
    ) -> dict[str, Any]:
        if await self.db.get_service_state("aeroapi_circuit") == "open":
            raise AeroApiError(
                "AeroAPI circuit is open because of an authentication error",
                code="provider_circuit_open",
            )
        request_id = await self.quota.reserve_request(
            endpoint_name=endpoint_name,
            flight_id=flight_id,
            trigger_type=trigger_type,
            priority=priority,
        )
        started = time.monotonic()
        status: int | None = None
        error_code: str | None = None
        success = False
        try:
            request = self.http.build_request("GET", path, params=params)
            LOGGER.info("AeroAPI request: %s %s", request.method, request.url)
            async with self._semaphore:
                response = await self.http.send(request)
            status = response.status_code
            if self.settings.aeroapi_extended_logging:
                LOGGER.info(
                    "AeroAPI response body: %s",
                    response.text,
                    extra={
                        "event": "aeroapi_response",
                        "endpoint_name": endpoint_name,
                        "http_status": status,
                        "flight_id": flight_id,
                    },
                )
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                error_code = f"http_{status}" if status >= 400 else "invalid_json"
                raise AeroApiError(
                    f"AeroAPI HTTP {status} returned a non-JSON response",
                    code=error_code,
                    http_status=status,
                    transient=status == 429 or status >= 500,
                    retry_after=retry_after,
                ) from exc
            if not isinstance(payload, dict):
                raise AeroApiError(
                    "AeroAPI returned a non-object response",
                    code="invalid_response_type",
                    http_status=status,
                    transient=status >= 500,
                )
            if status >= 400:
                error_code = _error_code(payload, status)
                if status == 401:
                    await self.db.set_service_state("aeroapi_circuit", "open")
                raise AeroApiError(
                    _error_message(payload, status),
                    code=error_code,
                    http_status=status,
                    transient=status == 429 or status >= 500,
                    retry_after=retry_after,
                )
            success = True
            return payload
        except httpx.TimeoutException as exc:
            error_code = "timeout"
            raise AeroApiError(
                "AeroAPI request timed out", code=error_code, transient=True
            ) from exc
        except httpx.NetworkError as exc:
            error_code = "network_error"
            raise AeroApiError("AeroAPI network error", code=error_code, transient=True) from exc
        except AeroApiError as exc:
            error_code = exc.code
            raise
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            await self.db.finish_api_request(
                request_id,
                success=success,
                http_status=status,
                api_error_code=error_code,
                duration_ms=duration_ms,
            )


def split_flight_designator(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"([A-Z0-9]{2})([0-9]{1,4})[A-Z]?", value)
    if not match:
        raise ValueError("Invalid IATA flight designator")
    return match.group(1), match.group(2)


def _error_code(payload: dict[str, Any], status: int) -> str:
    value = payload.get("code") or payload.get("error") or payload.get("title")
    if isinstance(value, dict):
        value = value.get("code") or value.get("title")
    if value:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
        if normalized:
            return normalized
    return f"http_{status}"


def _error_message(payload: dict[str, Any], status: int) -> str:
    for key in ("detail", "message", "title", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"AeroAPI HTTP {status}"


def _retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(1, int(value))
    except ValueError:
        return None
