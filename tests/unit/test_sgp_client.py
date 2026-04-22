# Author: SafeGuard Privacy
# Donated to IAB Tech Lab

"""Tests for the SafeGuard Privacy (SGP) client.

Covers domain normalization, batch chunking to 10, HTTP status handling
(200 / 400 / 401 / 404 / 5xx), response parsing, TTL cache, and the
api-key header.
"""

from __future__ import annotations

import httpx
import pytest

from ad_buyer.clients.sgp_client import (
    SGPAuthError,
    SGPClient,
    SGPClientError,
)

BASE_URL = "https://sgp.test"


def _make_client(handler, *, cache_ttl_seconds: int = 900) -> SGPClient:
    """Build an SGPClient whose internal httpx client uses MockTransport."""
    c = SGPClient(
        api_key="test-key",
        base_url=BASE_URL,
        cache_ttl_seconds=cache_ttl_seconds,
        timeout=5.0,
    )
    transport = httpx.MockTransport(handler)
    c._http = httpx.AsyncClient(
        transport=transport,
        base_url=BASE_URL,
        headers=dict(c._http.headers),
        timeout=5.0,
    )
    return c


def _success_body(records: list[dict]) -> dict:
    return {
        "status": "success",
        "code": 200,
        "message": "",
        "data": records,
        "pagination": {},
    }


def _record(domain: str, approved: bool, approved_at: str | None = "2026-03-14T12:00:00Z") -> dict:
    return {
        "vendorId": hash(domain) & 0xFFFF,
        "vendorCompanyId": (hash(domain) + 1) & 0xFFFF,
        "companyName": domain.split(".")[0].title() + " Inc.",
        "domain": domain,
        "internalId": "",
        "iabBuyerAgentApproval": approved,
        "iabBuyerAgentApprovedAt": approved_at,
    }


# ---------------------------------------------------------------------------
# Domain normalization
# ---------------------------------------------------------------------------


class TestNormalizeDomain:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("example.com", "example.com"),
            ("Example.COM", "example.com"),
            ("www.example.com", "example.com"),
            ("http://example.com", "example.com"),
            ("https://www.example.com/path?q=1", "example.com"),
            ("http://seller.example.com:8001", "seller.example.com"),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_normalizes(self, raw: str, expected: str) -> None:
        assert SGPClient.normalize_domain(raw) == expected


# ---------------------------------------------------------------------------
# Successful lookups
# ---------------------------------------------------------------------------


class TestCheckApprovalsSuccess:
    @pytest.mark.asyncio
    async def test_single_approved_vendor(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v1/integrations/iab/buyer-agent-approval"
            assert request.url.params["domain"] == "example.com"
            assert request.headers["api-key"] == "test-key"
            return httpx.Response(200, json=_success_body([_record("example.com", True)]))

        client = _make_client(handler)
        results = await client.check_approvals(["https://example.com/foo"])
        assert set(results) == {"example.com"}
        record = results["example.com"]
        assert record is not None
        assert record.iab_buyer_agent_approval is True
        assert record.iab_buyer_agent_approved_at is not None

    @pytest.mark.asyncio
    async def test_multiple_domains_single_call(self) -> None:
        seen_params: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params["domain"])
            return httpx.Response(
                200,
                json=_success_body([
                    _record("a.com", True),
                    _record("b.com", False),
                ]),
            )

        client = _make_client(handler)
        results = await client.check_approvals(["a.com", "b.com"])
        assert seen_params == ["a.com,b.com"]
        assert results["a.com"].iab_buyer_agent_approval is True
        assert results["b.com"].iab_buyer_agent_approval is False

    @pytest.mark.asyncio
    async def test_batches_more_than_ten_domains(self) -> None:
        captured: list[list[str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            domains = request.url.params["domain"].split(",")
            captured.append(domains)
            records = [_record(d, True) for d in domains]
            return httpx.Response(200, json=_success_body(records))

        client = _make_client(handler)
        domains = [f"d{i}.com" for i in range(25)]
        results = await client.check_approvals(domains)

        assert [len(c) for c in captured] == [10, 10, 5]
        assert len(results) == 25
        assert all(r is not None and r.iab_buyer_agent_approval for r in results.values())

    @pytest.mark.asyncio
    async def test_dedupes_input(self) -> None:
        captured_domains: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_domains.extend(request.url.params["domain"].split(","))
            return httpx.Response(
                200, json=_success_body([_record("example.com", True)])
            )

        client = _make_client(handler)
        await client.check_approvals(["example.com", "www.example.com", "EXAMPLE.COM"])
        assert captured_domains == ["example.com"]


# ---------------------------------------------------------------------------
# Not-found / unknown vendor
# ---------------------------------------------------------------------------


class TestUnknownVendor:
    @pytest.mark.asyncio
    async def test_404_marks_all_batch_domains_unknown(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"status": "error", "code": 404, "data": None})

        client = _make_client(handler)
        results = await client.check_approvals(["unknown1.com", "unknown2.com"])
        assert results == {"unknown1.com": None, "unknown2.com": None}

    @pytest.mark.asyncio
    async def test_partial_batch_response_marks_missing_as_unknown(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            # SGP only returns records for domains it actually knows; the
            # unknown ones are simply absent from the data array.
            return httpx.Response(
                200, json=_success_body([_record("known.com", True)])
            )

        client = _make_client(handler)
        results = await client.check_approvals(["known.com", "mystery.com"])
        assert results["known.com"] is not None
        assert results["mystery.com"] is None


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="unauthorized")

        client = _make_client(handler)
        with pytest.raises(SGPAuthError):
            await client.check_approvals(["example.com"])

    @pytest.mark.asyncio
    async def test_400_raises_client_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="bad domain")

        client = _make_client(handler)
        with pytest.raises(SGPClientError) as exc_info:
            await client.check_approvals(["example.com"])
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_5xx_raises_client_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="maintenance")

        client = _make_client(handler)
        with pytest.raises(SGPClientError) as exc_info:
            await client.check_approvals(["example.com"])
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_transport_error_wrapped_as_client_error(self) -> None:
        """Real httpx transport failures (connect/timeout/DNS) surface as SGPClientError."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _make_client(handler)
        with pytest.raises(SGPClientError) as exc_info:
            await client.check_approvals(["example.com"])
        assert "ConnectError" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class TestCache:
    @pytest.mark.asyncio
    async def test_cache_hit_avoids_second_request(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(
                200, json=_success_body([_record("cached.com", True)])
            )

        client = _make_client(handler)
        first = await client.check_approvals(["cached.com"])
        second = await client.check_approvals(["cached.com"])
        assert calls["n"] == 1
        assert first["cached.com"].vendor_id == second["cached.com"].vendor_id

    @pytest.mark.asyncio
    async def test_cache_stores_unknown_result(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404, json={"status": "error", "code": 404})

        client = _make_client(handler)
        await client.check_approvals(["mystery.com"])
        await client.check_approvals(["mystery.com"])
        assert calls["n"] == 1