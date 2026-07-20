# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""HTTP client for IAB OpenDirect 2.1 API."""

from typing import Any

import httpx

# Shared contract catalog envelope (iab_agentic_primitives) adopted at the wire
# edge for product discovery (EP-12.1): GET /products returns the shared
# ProductListResponse; there is no POST /products/search — filtering is
# client-side over the returned Product records.
from iab_agentic_primitives.primitives import Product as WireProduct
from iab_agentic_primitives.protocol import ProductListResponse as WireProductListResponse

from ..models.opendirect import (
    Account,
    AvailsRequest,
    AvailsResponse,
    Creative,
    Line,
    LineStats,
    Order,
    Product,
)
from .contract_mappers import from_wire_product

# ad_format vocabulary reconciliation (bead ar-mxsp).
#
# The buyer's research crew searches with OpenRTB-style *placement* terms
# ("banner", "interstitial", "video", "rewarded"), while sellers declare the
# IAB inventory_type *channel* taxonomy in ``Product.ad_formats`` ("display",
# "video", "ctv", "native", "audio", "mobile_app", "linear_tv"). An exact-match
# filter therefore dropped ALL display inventory for a "banner" search — the
# root cause of the real-mode S1 walk (every display product, including an
# under-ceiling $8 CPM one, was filtered out client-side before the LLM saw it;
# only shared-vocabulary "video" survived, and the sole video product busted the
# buyer's CPM ceiling).
#
# Both the requested format and each product's declared formats are normalized
# to the shared canonical category below before comparison. This is an EXPLICIT
# reconciliation, not a loose "match everything": unknown terms fall through to
# themselves (lowercased), so filtering still discriminates.
#
# Judgment calls (documented deliberately):
#   * banner / interstitial -> "display": both are display *placements*; the
#     seller taxonomy expresses this as the "display" channel.
#   * rewarded -> "video": rewarded video is the dominant reading of the
#     "rewarded" placement advertised in the product-search tool's vocabulary.
#   * ctv is kept DISTINCT from "video" (NOT folded in): CTV is a separate
#     buying context (pricing, creative specs), so a plain "video" search must
#     not pull CTV-only inventory and a "ctv" search must not pull generic
#     online video.
#   * native / audio / mobile_app / linear_tv map to themselves (identity) —
#     listed for documentation; unknown terms would normalize to themselves
#     anyway.
_AD_FORMAT_ALIASES: dict[str, str] = {
    "banner": "display",
    "interstitial": "display",
    "display": "display",
    "video": "video",
    "rewarded": "video",
    "ctv": "ctv",
    "native": "native",
    "audio": "audio",
    "mobile_app": "mobile_app",
    "linear_tv": "linear_tv",
}


def _normalize_ad_format(term: str) -> str:
    """Map a buyer/seller ad_format term onto the shared canonical category.

    Unknown terms normalize to themselves (lowercased) so exact-match
    discrimination is preserved for any vocabulary not in the alias table.
    """
    return _AD_FORMAT_ALIASES.get(term.strip().lower(), term.strip().lower())


def _filter_wire_products(
    products: list[WireProduct], filters: dict[str, Any]
) -> list[WireProduct]:
    """Client-side catalog filtering over the shared Product fields.

    Replaces the retired ``POST /products/search``. Understood filter keys map
    onto shared Product fields (``adFormat`` -> ``ad_formats``, ``deliveryType``
    -> ``delivery_type``, ``publisherIds`` -> ``seller_organization_id``);
    unknown keys (e.g. free-text/targeting) are ignored here — rich discovery
    remains the media-kit search surface, not the catalog.

    adFormat semantics: a product with EMPTY/absent ``ad_formats`` is
    "undeclared — do not exclude" and always survives the filter; only
    products that DECLARE formats not matching the requested one are
    excluded. Some sellers serve ``ad_formats: []`` (their taxonomy living
    in ``ext``), and excluding undeclared products made every
    adFormat-filtered search deterministically return zero results.

    adFormat vocabulary: the requested format and each declared product format
    are normalized through ``_normalize_ad_format`` before comparison, so the
    buyer's placement vocabulary ("banner", "interstitial") reconciles with the
    seller's IAB channel taxonomy ("display"). See ``_AD_FORMAT_ALIASES`` for
    the mapping and its judgment calls.

    Note on ``channel``: the tools also pass a ``channel`` filter (e.g.
    "display"), which is intentionally NOT used here — format normalization
    already resolves the vocabulary mismatch, and treating ``channel`` as an
    ad_format would conflate two distinct taxonomies and risk re-introducing
    over-filtering. ``channel`` remains an unknown/ignored key by design.
    """
    result = products

    ad_format = filters.get("adFormat")
    if ad_format:
        # Normalize BOTH sides to the shared canonical taxonomy so the buyer's
        # placement vocabulary ("banner") reconciles with the seller's channel
        # taxonomy ("display"). Empty/absent ad_formats still means "undeclared
        # — do not exclude" (ar-mkq5 semantics preserved).
        requested = _normalize_ad_format(ad_format)
        result = [
            p
            for p in result
            if not p.ad_formats
            or requested in {_normalize_ad_format(f) for f in p.ad_formats}
        ]

    delivery_type = filters.get("deliveryType")
    if delivery_type:
        result = [p for p in result if p.delivery_type.value == delivery_type]

    publisher_ids = filters.get("publisherIds")
    if publisher_ids:
        result = [p for p in result if p.seller_organization_id in publisher_ids]

    return result


class OpenDirectClient:
    """Async HTTP client for OpenDirect API v2.1."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        oauth_token: str | None = None,
        timeout: float = 30.0,
    ):
        """Initialize the client.

        Args:
            base_url: Base URL for the OpenDirect API
            api_key: Optional API key for authentication
            oauth_token: Optional OAuth bearer token
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self._headers = self._build_headers(api_key, oauth_token)
        self._timeout = timeout
        # Test seam: when set, injected into each per-request client (e.g.
        # ``httpx.MockTransport``). Never set in production.
        self._transport: httpx.AsyncBaseTransport | None = None

    def _build_headers(self, api_key: str | None, oauth_token: str | None) -> dict[str, str]:
        """Build request headers."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if oauth_token:
            headers["Authorization"] = f"Bearer {oauth_token}"
        elif api_key:
            headers["X-API-Key"] = api_key
        return headers

    def _make_client(self) -> httpx.AsyncClient:
        """Create a fresh ``httpx.AsyncClient`` scoped to a single request.

        The client is deliberately NOT persistent: the sync CrewAI tools drive
        this class through ``ad_buyer.async_utils.run_async``, which runs each
        coroutine on a fresh event loop that is closed afterwards. A persistent
        AsyncClient binds its connection pool to the first loop and every later
        call then fails with ``RuntimeError: Event loop is closed``. A
        per-request client always lives and dies on the loop that is actually
        running the call.
        """
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            timeout=self._timeout,
            transport=self._transport,
        )

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Perform one HTTP request on a fresh per-request client."""
        async with self._make_client() as client:
            return await client.request(method, url, **kwargs)

    # -------------------------------------------------------------------------
    # Products
    # -------------------------------------------------------------------------

    async def _fetch_wire_products(self, *, limit: int, offset: int) -> list[Any]:
        """GET /products and return the shared Product records (unmapped).

        Serializes the shared ``ProductListRequest`` pagination params
        (``limit``/``offset``) and parses the shared ``ProductListResponse``
        envelope. Filtering is done client-side by the callers.
        """
        params = {"limit": limit, "offset": offset}
        response = await self._request("GET", "/products", params=params)
        response.raise_for_status()
        wire = WireProductListResponse.model_validate(response.json())
        return list(wire.products)

    async def list_products(self, skip: int = 0, top: int = 50, **filters: Any) -> list[Product]:
        """List available products with pagination.

        Emits the shared ``ProductListRequest`` pagination (``limit``/``offset``)
        and maps the returned shared ``Product`` records to the OpenDirect model
        at the boundary. Any ``**filters`` are applied client-side.

        Args:
            skip: Number of items to skip (shared ``offset``)
            top: Maximum number of items to return (shared ``limit``)
            **filters: Additional filter parameters, applied client-side

        Returns:
            List of Product objects
        """
        wire_products = await self._fetch_wire_products(limit=top, offset=skip)
        if filters:
            wire_products = _filter_wire_products(wire_products, filters)
        return [from_wire_product(p) for p in wire_products]

    async def get_product(self, product_id: str) -> Product:
        """Get a single product by ID.

        GET /products/{product_id} returns the shared ``Product`` primitive
        (no wrapper); it is mapped to the OpenDirect model at the boundary.

        Args:
            product_id: The product ID

        Returns:
            Product object
        """
        response = await self._request("GET", f"/products/{product_id}")
        response.raise_for_status()
        wire_product = WireProduct.model_validate(response.json())
        return from_wire_product(wire_product)

    async def search_products(self, filters: dict[str, Any]) -> list[Product]:
        """Search products with filters.

        The shared catalog has NO ``POST /products/search`` route (remediation
        plan §7 amendment 3): the seller returns the full filterable product
        record on ``GET /products`` and the buyer filters CLIENT-SIDE over the
        returned fields. This method fetches the catalog and applies the filters
        locally instead of POSTing to the retired search route.

        Args:
            filters: Search filter parameters (channel, format, pricing, etc.)

        Returns:
            List of matching Product objects
        """
        wire_products = await self._fetch_wire_products(limit=500, offset=0)
        wire_products = _filter_wire_products(wire_products, filters)
        return [from_wire_product(p) for p in wire_products]

    async def check_avails(self, request: AvailsRequest) -> AvailsResponse:
        """Check availability and pricing for a product.

        Args:
            request: Availability check request parameters

        Returns:
            AvailsResponse with availability and pricing info
        """
        # mode="json" renders the datetime start_date/end_date fields as
        # ISO-8601 strings so the request body is JSON-serializable at the httpx
        # boundary. Without it the raw datetime objects hit Python's default
        # json encoder and the POST crashes with "Object of type datetime is not
        # JSON serializable" before the request ever reaches the seller (bead
        # ar-rs25). by_alias keeps the spec-lowercase wire field names
        # (startdate/enddate/productid) the seller's avails endpoint expects.
        response = await self._request(
            "POST",
            "/products/avails",
            json=request.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return AvailsResponse.model_validate(response.json())

    # -------------------------------------------------------------------------
    # Accounts
    # -------------------------------------------------------------------------

    async def create_account(self, account: Account) -> Account:
        """Create a new account.

        Args:
            account: Account data to create

        Returns:
            Created Account with ID
        """
        response = await self._request(
            "POST", "/accounts", json=account.model_dump(by_alias=True, exclude_none=True)
        )
        response.raise_for_status()
        return Account.model_validate(response.json())

    async def get_account(self, account_id: str) -> Account:
        """Get an account by ID.

        Args:
            account_id: The account ID

        Returns:
            Account object
        """
        response = await self._request("GET", f"/accounts/{account_id}")
        response.raise_for_status()
        return Account.model_validate(response.json())

    async def list_accounts(self, skip: int = 0, top: int = 50) -> list[Account]:
        """List accounts with pagination.

        Args:
            skip: Number of items to skip
            top: Maximum number of items to return

        Returns:
            List of Account objects
        """
        params = {"$skip": skip, "$top": top}
        response = await self._request("GET", "/accounts", params=params)
        response.raise_for_status()
        data = response.json()
        accounts = data.get("accounts", data) if isinstance(data, dict) else data
        return [Account.model_validate(a) for a in accounts]

    # -------------------------------------------------------------------------
    # Orders
    # -------------------------------------------------------------------------

    async def create_order(self, account_id: str, order: Order) -> Order:
        """Create a new order under an account.

        Args:
            account_id: The account ID
            order: Order data to create

        Returns:
            Created Order with ID
        """
        response = await self._request(
            "POST",
            f"/accounts/{account_id}/orders",
            json=order.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return Order.model_validate(response.json())

    async def get_order(self, account_id: str, order_id: str) -> Order:
        """Get an order by ID.

        Args:
            account_id: The account ID
            order_id: The order ID

        Returns:
            Order object
        """
        response = await self._request("GET", f"/accounts/{account_id}/orders/{order_id}")
        response.raise_for_status()
        return Order.model_validate(response.json())

    async def list_orders(self, account_id: str, skip: int = 0, top: int = 50) -> list[Order]:
        """List orders for an account.

        Args:
            account_id: The account ID
            skip: Number of items to skip
            top: Maximum number of items to return

        Returns:
            List of Order objects
        """
        params = {"$skip": skip, "$top": top}
        response = await self._request("GET", f"/accounts/{account_id}/orders", params=params)
        response.raise_for_status()
        data = response.json()
        orders = data.get("orders", data) if isinstance(data, dict) else data
        return [Order.model_validate(o) for o in orders]

    async def update_order(self, account_id: str, order_id: str, order: Order) -> Order:
        """Update an existing order.

        Args:
            account_id: The account ID
            order_id: The order ID
            order: Updated order data

        Returns:
            Updated Order object
        """
        response = await self._request(
            "PATCH",
            f"/accounts/{account_id}/orders/{order_id}",
            json=order.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return Order.model_validate(response.json())

    # -------------------------------------------------------------------------
    # Lines
    # -------------------------------------------------------------------------

    async def create_line(self, account_id: str, order_id: str, line: Line) -> Line:
        """Create a new line item under an order.

        Args:
            account_id: The account ID
            order_id: The order ID
            line: Line data to create

        Returns:
            Created Line with ID
        """
        response = await self._request(
            "POST",
            f"/accounts/{account_id}/orders/{order_id}/lines",
            json=line.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return Line.model_validate(response.json())

    async def get_line(self, account_id: str, order_id: str, line_id: str) -> Line:
        """Get a line item by ID.

        Args:
            account_id: The account ID
            order_id: The order ID
            line_id: The line ID

        Returns:
            Line object
        """
        response = await self._request(
            "GET", f"/accounts/{account_id}/orders/{order_id}/lines/{line_id}"
        )
        response.raise_for_status()
        return Line.model_validate(response.json())

    async def list_lines(
        self, account_id: str, order_id: str, skip: int = 0, top: int = 50
    ) -> list[Line]:
        """List line items for an order.

        Args:
            account_id: The account ID
            order_id: The order ID
            skip: Number of items to skip
            top: Maximum number of items to return

        Returns:
            List of Line objects
        """
        params = {"$skip": skip, "$top": top}
        response = await self._request(
            "GET", f"/accounts/{account_id}/orders/{order_id}/lines", params=params
        )
        response.raise_for_status()
        data = response.json()
        lines = data.get("lines", data) if isinstance(data, dict) else data
        return [Line.model_validate(ln) for ln in lines]

    async def reserve_line(self, account_id: str, order_id: str, line_id: str) -> Line:
        """Reserve inventory for a line item.

        Args:
            account_id: The account ID
            order_id: The order ID
            line_id: The line ID

        Returns:
            Updated Line with Reserved status
        """
        response = await self._request(
            "PATCH",
            f"/accounts/{account_id}/orders/{order_id}/lines/{line_id}",
            params={"action": "reserve"},
        )
        response.raise_for_status()
        return Line.model_validate(response.json())

    async def book_line(self, account_id: str, order_id: str, line_id: str) -> Line:
        """Confirm booking for a line item.

        Args:
            account_id: The account ID
            order_id: The order ID
            line_id: The line ID

        Returns:
            Updated Line with Booked status
        """
        response = await self._request(
            "PATCH",
            f"/accounts/{account_id}/orders/{order_id}/lines/{line_id}",
            params={"action": "book"},
        )
        response.raise_for_status()
        return Line.model_validate(response.json())

    async def cancel_line(self, account_id: str, order_id: str, line_id: str) -> Line:
        """Cancel a line item.

        Args:
            account_id: The account ID
            order_id: The order ID
            line_id: The line ID

        Returns:
            Updated Line with Canceled status
        """
        response = await self._request(
            "PATCH",
            f"/accounts/{account_id}/orders/{order_id}/lines/{line_id}",
            params={"action": "cancel"},
        )
        response.raise_for_status()
        return Line.model_validate(response.json())

    async def get_line_stats(self, account_id: str, order_id: str, line_id: str) -> LineStats:
        """Get performance statistics for a line item.

        Args:
            account_id: The account ID
            order_id: The order ID
            line_id: The line ID

        Returns:
            LineStats with delivery and performance metrics
        """
        response = await self._request(
            "GET", f"/accounts/{account_id}/orders/{order_id}/lines/{line_id}/stats"
        )
        response.raise_for_status()
        return LineStats.model_validate(response.json())

    # -------------------------------------------------------------------------
    # Creatives
    # -------------------------------------------------------------------------

    async def create_creative(self, account_id: str, creative: Creative) -> Creative:
        """Create a new creative.

        Args:
            account_id: The account ID
            creative: Creative data to create

        Returns:
            Created Creative with ID
        """
        response = await self._request(
            "POST",
            f"/accounts/{account_id}/creatives",
            json=creative.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return Creative.model_validate(response.json())

    async def get_creative(self, account_id: str, creative_id: str) -> Creative:
        """Get a creative by ID.

        Args:
            account_id: The account ID
            creative_id: The creative ID

        Returns:
            Creative object
        """
        response = await self._request("GET", f"/accounts/{account_id}/creatives/{creative_id}")
        response.raise_for_status()
        return Creative.model_validate(response.json())

    async def list_creatives(self, account_id: str, skip: int = 0, top: int = 50) -> list[Creative]:
        """List creatives for an account.

        Args:
            account_id: The account ID
            skip: Number of items to skip
            top: Maximum number of items to return

        Returns:
            List of Creative objects
        """
        params = {"$skip": skip, "$top": top}
        response = await self._request("GET", f"/accounts/{account_id}/creatives", params=params)
        response.raise_for_status()
        data = response.json()
        creatives = data.get("creatives", data) if isinstance(data, dict) else data
        return [Creative.model_validate(c) for c in creatives]

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def close(self) -> None:
        """Close the client.

        No-op retained for API compatibility: HTTP clients are opened
        per-request (see ``_make_client``), so there is no persistent
        connection pool to close.
        """
        return None

    async def __aenter__(self) -> "OpenDirectClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()
