# Author: SafeGuard Privacy
# Donated to IAB Tech Lab

"""Tests for the IAB Diligence Platform (SGP) client.

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
    extract_product_domain,
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
            ("example.com.", "example.com"),
            ("https://www.example.com./path", "example.com"),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_normalizes(self, raw: str, expected: str) -> None:
        assert SGPClient.normalize_domain(raw) == expected


# ---------------------------------------------------------------------------
# Product domain extraction
# ---------------------------------------------------------------------------


class TestExtractProductDomain:
    def test_reads_domain_from_opendirect_product_schema(self) -> None:
        """The field the Product resource actually defines must be honored.

        Regression: the extractor originally probed only deal-record and
        SSP-connector field names, so a spec-conformant product with
        ``domain`` populated was reported as having no seller domain and
        blocked under SGP_ENFORCE.
        """
        from ad_buyer.models.opendirect import Product

        # Constructed by field name (populate_by_name) rather than by wire
        # alias, so this stays valid if the alias convention changes again.
        product = Product(
            id="espn-sports-pmp",
            publisher_id="pub_espn",
            name="ESPN Sports PMP",
            base_price=18.5,
            rate_type="CPM",
            domain="espn.com",
            available_impressions=1_000_000,
        ).model_dump(by_alias=True)

        assert extract_product_domain(product) == "espn.com"

    @pytest.mark.parametrize(
        "key",
        ["domain", "seller_domain", "sellerDomain"],
    )
    def test_product_vocabulary_keys(self, key: str) -> None:
        assert extract_product_domain({"id": "p1", key: "example.com"}) == "example.com"

    @pytest.mark.parametrize(
        "key, value",
        [
            ("publisherDomain", "roku.com"),
            ("publisher_domain", "pub1.example.com"),
            ("seller_url", "http://seller.example.com:8001"),
        ],
    )
    def test_deal_and_ssp_vocabulary_keys_still_resolve(self, key: str, value: str) -> None:
        """Connector-derived deal dicts must keep working."""
        assert extract_product_domain({"id": "p1", key: value}) == value

    def test_product_domain_preferred_over_seller_endpoint(self) -> None:
        """`domain` identifies the vendor; `seller_url` is only a transport endpoint."""
        product = {
            "id": "p1",
            "domain": "espn.com",
            "seller_url": "http://broker.example.com:8001",
        }
        assert extract_product_domain(product) == "espn.com"

    def test_opaque_publisher_id_is_not_a_domain(self) -> None:
        assert extract_product_domain({"id": "p1", "publisherId": "pub_abc"}) is None

    def test_no_domain_field_returns_none(self) -> None:
        assert extract_product_domain({"id": "p1", "name": "Untagged"}) is None


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
                json=_success_body(
                    [
                        _record("a.com", True),
                        _record("b.com", False),
                    ]
                ),
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
            return httpx.Response(200, json=_success_body([_record("example.com", True)]))

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
            return httpx.Response(200, json=_success_body([_record("known.com", True)]))

        client = _make_client(handler)
        results = await client.check_approvals(["known.com", "mystery.com"])
        assert results["known.com"] is not None
        assert results["mystery.com"] is None


# ---------------------------------------------------------------------------
# Response-to-request domain matching
#
# SGP does not guarantee it echoes the exact spelling that was queried -- it
# may answer with the vendor's canonical/apex domain. Records must still be
# paired back to the requested domain, or an approved vendor is reported
# UNKNOWN and that verdict is cached for the full TTL.
# ---------------------------------------------------------------------------


class TestDomainEchoMatching:
    @pytest.mark.asyncio
    async def test_apex_echo_resolves_to_queried_subdomain(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            # Queried news.foo.com; SGP answers with the vendor's apex domain.
            return httpx.Response(200, json=_success_body([_record("foo.com", True)]))

        client = _make_client(handler)
        results = await client.check_approvals(["news.foo.com"])
        record = results["news.foo.com"]
        assert record is not None, "apex echo must not be discarded"
        assert record.iab_buyer_agent_approval is True

    @pytest.mark.asyncio
    async def test_subdomain_echo_resolves_to_queried_apex(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_body([_record("www2.foo.com", True)]))

        client = _make_client(handler)
        results = await client.check_approvals(["foo.com"])
        assert results["foo.com"] is not None
        assert results["foo.com"].iab_buyer_agent_approval is True

    @pytest.mark.asyncio
    async def test_exact_match_wins_over_suffix_match(self) -> None:
        """An apex record must not leak onto a subdomain that got its own record."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_success_body(
                    [
                        _record("news.foo.com", True),
                        _record("foo.com", False),
                    ]
                ),
            )

        client = _make_client(handler)
        results = await client.check_approvals(["foo.com", "news.foo.com"])
        assert results["news.foo.com"].iab_buyer_agent_approval is True
        assert results["foo.com"].iab_buyer_agent_approval is False

    @pytest.mark.asyncio
    async def test_apex_record_covers_all_pending_subdomains(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_body([_record("foo.com", True)]))

        client = _make_client(handler)
        results = await client.check_approvals(["a.foo.com", "b.foo.com"])
        assert results["a.foo.com"] is not None
        assert results["b.foo.com"] is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("also_requested", [[], ["other.com"]])
    async def test_suffix_match_respects_label_boundary(self, also_requested) -> None:
        """notfoo.com must never be matched by a foo.com record.

        Parametrized over a single-domain and a multi-domain request: the
        boundary rule must hold even when the response contains exactly one
        record, which is the shape the deal-request gate always produces.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_body([_record("foo.com", True)]))

        client = _make_client(handler)
        results = await client.check_approvals(["notfoo.com", *also_requested])
        assert results["notfoo.com"] is None

    @pytest.mark.asyncio
    async def test_lone_unrelated_record_is_not_attributed(self, caplog) -> None:
        """A one-record answer to a one-domain query must not be trusted blindly.

        Regression: a "lone record answers a lone query" fallback made the
        gate fail open -- SGP answering about any other vendor was accepted
        as approval for the queried seller.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_body([_record("vendor-canonical.com", True)]))

        client = _make_client(handler)
        with caplog.at_level("WARNING"):
            results = await client.check_approvals(["seller-alias.com"])
        assert results["seller-alias.com"] is None
        assert "vendor-canonical.com" in caplog.text

    @pytest.mark.asyncio
    async def test_empty_domain_record_is_not_attributed(self) -> None:
        """A record with no domain must not be pinned onto the queried domain."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_body([_record("", True)]))

        client = _make_client(handler)
        results = await client.check_approvals(["foo.com"])
        assert results["foo.com"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("order", [("foo.com", "b.foo.com"), ("b.foo.com", "foo.com")])
    async def test_most_specific_parent_wins_regardless_of_response_order(self, order) -> None:
        """Verdicts must not depend on the order records appear in the response."""
        verdicts = {"foo.com": False, "b.foo.com": True}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_success_body([_record(d, verdicts[d]) for d in order]),
            )

        client = _make_client(handler)
        results = await client.check_approvals(["a.b.foo.com"])
        record = results["a.b.foo.com"]
        assert record is not None
        # b.foo.com is the more specific parent, so its verdict applies.
        assert record.domain == "b.foo.com"
        assert record.iab_buyer_agent_approval is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("order", [(True, False), (False, True)])
    async def test_conflicting_duplicate_records_resolve_to_denial(self, order) -> None:
        """Equally specific but contradictory records must not grant approval."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_success_body([_record("foo.com", approved) for approved in order]),
            )

        client = _make_client(handler)
        results = await client.check_approvals(["news.foo.com"])
        assert results["news.foo.com"] is not None
        assert results["news.foo.com"].iab_buyer_agent_approval is False

    @pytest.mark.asyncio
    async def test_trailing_dot_fqdn_echo_matches(self) -> None:
        """An FQDN-style echo (foo.com.) must resolve a queried foo.com."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_body([_record("foo.com.", True)]))

        client = _make_client(handler)
        results = await client.check_approvals(["foo.com", "other.com"])
        assert results["foo.com"] is not None
        assert results["other.com"] is None

    @pytest.mark.asyncio
    async def test_unmatchable_record_is_logged_not_silently_dropped(self, caplog) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_body([_record("stray.com", True)]))

        client = _make_client(handler)
        with caplog.at_level("WARNING"):
            await client.check_approvals(["a.com", "b.com"])
        assert "stray.com" in caplog.text

    @pytest.mark.asyncio
    async def test_resolved_record_is_what_gets_cached(self) -> None:
        """Regression: an apex echo used to cache None, blocking for the TTL."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=_success_body([_record("foo.com", True)]))

        client = _make_client(handler)
        first = await client.check_approvals(["news.foo.com"])
        second = await client.check_approvals(["news.foo.com"])
        assert calls["n"] == 1, "second lookup should be served from cache"
        assert first["news.foo.com"] is not None
        assert second["news.foo.com"] is not None
        assert second["news.foo.com"].iab_buyer_agent_approval is True


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
            return httpx.Response(200, json=_success_body([_record("cached.com", True)]))

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
