from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any

import httpx

from app.aviationstack.errors import AviationstackError
from app.config import Settings
from app.storage.db import Database
from app.tracking.quota import QuotaManager

PERMANENT_AUTH_CODES = {
    "invalid_access_key",
    "missing_access_key",
    "inactive_user",
}


class AviationstackClient:
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
        self._semaphore = asyncio.Semaphore(settings.aviationstack_max_concurrency)
        timeout = httpx.Timeout(
            connect=settings.http_connect_timeout_seconds,
            read=settings.http_read_timeout_seconds,
            write=settings.http_read_timeout_seconds,
            pool=settings.http_connect_timeout_seconds,
        )
        limits = httpx.Limits(
            max_connections=settings.aviationstack_max_concurrency,
            max_keepalive_connections=settings.aviationstack_max_concurrency,
        )
        self.http = httpx.AsyncClient(
            base_url=settings.aviationstack_base_url,
            timeout=timeout,
            limits=limits,
            headers={"Accept": "application/json", "User-Agent": "UnderpigFlightBot/0.1"},
            transport=transport,
        )

    async def initialize(self) -> None:
        fingerprint = hashlib.sha256(self.settings.aviationstack_key.encode()).hexdigest()[:16]
        stored = await self.db.get_service_state("aviationstack_key_fingerprint")
        if stored != fingerprint:
            await self.db.set_service_state("aviationstack_circuit", "closed")
            await self.db.set_service_state("aviationstack_key_fingerprint", fingerprint)

    async def close(self) -> None:
        await self.http.aclose()

    async def search_flights(
        self,
        flight_iata: str,
        *,
        flight_date: str | None,
        flight_id: int | None,
        trigger_type: str,
        priority: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"flight_iata": flight_iata, "limit": 100, "offset": 0}
        if flight_date and self.settings.aviationstack_use_flight_date_filter:
            params["flight_date"] = flight_date
        return await self._request(
            "/flights",
            params=params,
            endpoint_name="flights",
            flight_id=flight_id,
            trigger_type=trigger_type,
            priority=priority,
        )

    async def search_future(
        self,
        flight_iata: str,
        *,
        flight_date: str,
        departure_iata: str,
        flight_id: int | None,
        trigger_type: str,
        priority: int,
    ) -> dict[str, Any]:
        airline_iata, flight_number = split_flight_iata(flight_iata)
        return await self._request(
            "/flightsFuture",
            params={
                "date": flight_date,
                "iataCode": departure_iata,
                "type": "departure",
                "airline_iata": airline_iata,
                "flight_number": flight_number,
                "limit": 100,
                "offset": 0,
            },
            endpoint_name="flights_future",
            flight_id=flight_id,
            trigger_type=trigger_type,
            priority=priority,
        )

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
        if await self.db.get_service_state("aviationstack_circuit") == "open":
            raise AviationstackError(
                "Aviationstack circuit is open because of an authentication error",
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
            request_params = dict(params)
            request_params["access_key"] = self.settings.aviationstack_key
            async with self._semaphore:
                response = await self.http.get(path, params=request_params)
            status = response.status_code
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                if status >= 400:
                    error_code = f"http_{status}"
                    raise AviationstackError(
                        f"Aviationstack HTTP {status} returned a non-JSON response",
                        code=error_code,
                        http_status=status,
                        transient=status == 429 or status >= 500,
                        retry_after=retry_after,
                    ) from exc
                raise AviationstackError(
                    "Aviationstack returned invalid JSON",
                    code="invalid_json",
                    http_status=status,
                    transient=status >= 500,
                ) from exc
            if not isinstance(payload, dict):
                raise AviationstackError(
                    "Aviationstack returned a non-object response",
                    code="invalid_response_type",
                    http_status=status,
                    transient=status >= 500,
                )
            error = payload.get("error")
            if isinstance(error, dict):
                error_code = str(error.get("code") or "api_error")
                if error_code in PERMANENT_AUTH_CODES:
                    await self.db.set_service_state("aviationstack_circuit", "open")
                raise AviationstackError(
                    str(error.get("message") or "Aviationstack API error"),
                    code=error_code,
                    http_status=status,
                    transient=status == 429 or status >= 500,
                    retry_after=retry_after,
                )
            if status >= 400:
                error_code = f"http_{status}"
                raise AviationstackError(
                    f"Aviationstack HTTP {status}",
                    code=error_code,
                    http_status=status,
                    transient=status == 429 or status >= 500,
                    retry_after=retry_after,
                )
            success = True
            return payload
        except httpx.TimeoutException as exc:
            error_code = "timeout"
            raise AviationstackError(
                "Aviationstack request timed out", code=error_code, transient=True
            ) from exc
        except httpx.NetworkError as exc:
            error_code = "network_error"
            raise AviationstackError(
                "Aviationstack network error", code=error_code, transient=True
            ) from exc
        except AviationstackError as exc:
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


def split_flight_iata(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"([A-Z0-9]{2})([0-9]{1,4})[A-Z]?", value)
    if not match:
        raise ValueError("Invalid IATA flight number")
    return match.group(1), match.group(2)


def _retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(1, int(value))
    except ValueError:
        return None
