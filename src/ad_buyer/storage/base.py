# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Base storage backend interface.

Mirrors the seller-agent's StorageBackend ABC to maintain structural
consistency across the buyer/seller ecosystem.  All concrete backends
(SQLite, Redis, Postgres, Hybrid) implement this interface.

The base class provides low-level key-value primitives and higher-level
domain helpers for buyer-specific entities (deals, campaigns, conversions,
optimization decisions, experiments, etc.).
"""

from abc import ABC, abstractmethod
from typing import Any


class StorageBackend(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to storage backend."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to storage backend."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Retrieve a value by key."""

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value with optional TTL (seconds)."""

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True if key existed."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if key exists."""

    @abstractmethod
    async def keys(self, pattern: str = "*") -> list[str]:
        """List keys matching pattern."""

    # ------------------------------------------------------------------
    # Deal operations
    # ------------------------------------------------------------------

    async def get_deal(self, deal_id: str) -> dict | None:
        """Get a deal by ID."""
        return await self.get(f"deal:{deal_id}")

    async def set_deal(self, deal_id: str, deal_data: dict) -> None:
        """Store a deal."""
        await self.set(f"deal:{deal_id}", deal_data)

    async def delete_deal(self, deal_id: str) -> bool:
        """Delete a deal."""
        return await self.delete(f"deal:{deal_id}")

    async def list_deals(self, filters: dict | None = None) -> list[dict]:
        """List all deals, optionally filtered."""
        keys = await self.keys("deal:*")
        deals = []
        for key in keys:
            deal = await self.get(key)
            if deal is None:
                continue
            if filters:
                if "status" in filters and deal.get("status") != filters["status"]:
                    continue
                if "deal_type" in filters and deal.get("deal_type") != filters["deal_type"]:
                    continue
            deals.append(deal)
        return deals

    # ------------------------------------------------------------------
    # Campaign operations
    # ------------------------------------------------------------------

    async def get_campaign(self, campaign_id: str) -> dict | None:
        """Get a campaign by ID."""
        return await self.get(f"campaign:{campaign_id}")

    async def set_campaign(self, campaign_id: str, data: dict) -> None:
        """Store a campaign."""
        await self.set(f"campaign:{campaign_id}", data)

    async def list_campaigns(self, filters: dict | None = None) -> list[dict]:
        """List campaigns, optionally filtered by status."""
        keys = await self.keys("campaign:*")
        campaigns = []
        for key in keys:
            campaign = await self.get(key)
            if campaign is None:
                continue
            if filters:
                if "status" in filters and campaign.get("status") != filters["status"]:
                    continue
            campaigns.append(campaign)
        return campaigns

    # ------------------------------------------------------------------
    # Order operations
    # ------------------------------------------------------------------

    async def get_order(self, order_id: str) -> dict | None:
        """Get an order by ID."""
        return await self.get(f"order:{order_id}")

    async def set_order(self, order_id: str, data: dict) -> None:
        """Store an order."""
        await self.set(f"order:{order_id}", data)

    async def list_orders(self, filters: dict | None = None) -> list[dict]:
        """List orders, optionally filtered by status."""
        keys = await self.keys("order:*")
        orders = []
        for key in keys:
            order = await self.get(key)
            if order is None:
                continue
            if filters:
                if "status" in filters and order.get("status") != filters["status"]:
                    continue
            orders.append(order)
        return orders

    # ------------------------------------------------------------------
    # Session operations
    # ------------------------------------------------------------------

    async def get_session(self, session_id: str) -> dict | None:
        """Get a session by ID."""
        return await self.get(f"session:{session_id}")

    async def set_session(self, session_id: str, data: dict, ttl: int | None = None) -> None:
        """Store a session with optional TTL."""
        await self.set(f"session:{session_id}", data, ttl=ttl)

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        return await self.delete(f"session:{session_id}")

    async def list_sessions(self) -> list[dict]:
        """List all sessions."""
        keys = await self.keys("session:*")
        sessions = []
        for key in keys:
            if key.startswith("session_index:"):
                continue
            session = await self.get(key)
            if session:
                sessions.append(session)
        return sessions

    # ------------------------------------------------------------------
    # Conversion event operations (optimization)
    # ------------------------------------------------------------------

    async def get_conversion(self, event_id: str) -> dict | None:
        """Get a conversion event by ID."""
        return await self.get(f"conversion:{event_id}")

    async def set_conversion(self, event_id: str, data: dict) -> None:
        """Store a conversion event."""
        await self.set(f"conversion:{event_id}", data)

    async def list_conversions(self, filters: dict | None = None) -> list[dict]:
        """List conversion events, optionally filtered."""
        keys = await self.keys("conversion:*")
        conversions = []
        for key in keys:
            conversion = await self.get(key)
            if conversion is None:
                continue
            if filters:
                if "deal_id" in filters and conversion.get("deal_id") != filters["deal_id"]:
                    continue
                if (
                    "campaign_id" in filters
                    and conversion.get("campaign_id") != filters["campaign_id"]
                ):  # noqa: E501
                    continue
            conversions.append(conversion)
        return conversions

    # ------------------------------------------------------------------
    # Optimization decision operations
    # ------------------------------------------------------------------

    async def get_optimization_decision(self, decision_id: str) -> dict | None:
        """Get an optimization decision by ID."""
        return await self.get(f"opt_decision:{decision_id}")

    async def set_optimization_decision(self, decision_id: str, data: dict) -> None:
        """Store an optimization decision."""
        await self.set(f"opt_decision:{decision_id}", data)

    async def list_optimization_decisions(self, filters: dict | None = None) -> list[dict]:
        """List optimization decisions, optionally filtered."""
        keys = await self.keys("opt_decision:*")
        decisions = []
        for key in keys:
            decision = await self.get(key)
            if decision is None:
                continue
            if filters:
                if (
                    "campaign_id" in filters
                    and decision.get("campaign_id") != filters["campaign_id"]
                ):  # noqa: E501
                    continue
            decisions.append(decision)
        return decisions

    # ------------------------------------------------------------------
    # Experiment operations
    # ------------------------------------------------------------------

    async def get_experiment(self, experiment_id: str) -> dict | None:
        """Get an experiment by ID."""
        return await self.get(f"experiment:{experiment_id}")

    async def set_experiment(self, experiment_id: str, data: dict) -> None:
        """Store an experiment."""
        await self.set(f"experiment:{experiment_id}", data)

    async def list_experiments(self, filters: dict | None = None) -> list[dict]:
        """List experiments, optionally filtered."""
        keys = await self.keys("experiment:*")
        experiments = []
        for key in keys:
            if key.startswith("experiment_result:"):
                continue
            experiment = await self.get(key)
            if experiment is None:
                continue
            if filters:
                if (
                    "campaign_id" in filters
                    and experiment.get("campaign_id") != filters["campaign_id"]
                ):  # noqa: E501
                    continue
                if "status" in filters and experiment.get("status") != filters["status"]:
                    continue
            experiments.append(experiment)
        return experiments

    # ------------------------------------------------------------------
    # Supply path score operations
    # ------------------------------------------------------------------

    async def get_supply_path_score(self, supply_path_hash: str) -> dict | None:
        """Get a supply path score."""
        return await self.get(f"supply_path:{supply_path_hash}")

    async def set_supply_path_score(self, supply_path_hash: str, data: dict) -> None:
        """Store a supply path score."""
        await self.set(f"supply_path:{supply_path_hash}", data)

    async def list_supply_path_scores(self) -> list[dict]:
        """List all supply path scores."""
        keys = await self.keys("supply_path:*")
        scores = []
        for key in keys:
            score = await self.get(key)
            if score:
                scores.append(score)
        return scores

    # ------------------------------------------------------------------
    # Quote operations
    # ------------------------------------------------------------------

    async def get_quote(self, quote_id: str) -> dict | None:
        """Get a quote by ID."""
        return await self.get(f"quote:{quote_id}")

    async def set_quote(self, quote_id: str, data: dict, ttl: int = 86400) -> None:
        """Store a quote with TTL (default 24 hours)."""
        await self.set(f"quote:{quote_id}", data, ttl=ttl)

    # ------------------------------------------------------------------
    # Negotiation operations
    # ------------------------------------------------------------------

    async def get_negotiation(self, proposal_id: str) -> dict | None:
        """Get negotiation history by proposal ID."""
        return await self.get(f"negotiation:{proposal_id}")

    async def set_negotiation(self, proposal_id: str, data: dict) -> None:
        """Store negotiation history."""
        await self.set(f"negotiation:{proposal_id}", data)

    # ------------------------------------------------------------------
    # Model artifact operations
    # ------------------------------------------------------------------

    async def get_model_artifact(self, model_name: str) -> dict | None:
        """Get a serialized model artifact."""
        return await self.get(f"model:{model_name}")

    async def set_model_artifact(self, model_name: str, data: dict) -> None:
        """Store a serialized model artifact."""
        await self.set(f"model:{model_name}", data)

    # ------------------------------------------------------------------
    # Pacing snapshot operations
    # ------------------------------------------------------------------

    async def get_pacing_snapshot(self, snapshot_id: str) -> dict | None:
        """Get a pacing snapshot by ID."""
        return await self.get(f"pacing:{snapshot_id}")

    async def set_pacing_snapshot(self, snapshot_id: str, data: dict) -> None:
        """Store a pacing snapshot."""
        await self.set(f"pacing:{snapshot_id}", data)
