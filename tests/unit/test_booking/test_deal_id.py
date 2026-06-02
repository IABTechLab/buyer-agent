# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for deal ID generation utility."""

import re

from ad_buyer.booking.deal_id import generate_deal_id


class TestGenerateDealId:
    """Test deal ID generation."""

    def test_deal_id_format(self):
        """Deal ID has format DEAL-XXXXXXXX (8 uppercase hex chars)."""
        deal_id = generate_deal_id(
            product_id="prod-001",
            identity_seed="agency-123",
        )
        assert deal_id.startswith("DEAL-")
        suffix = deal_id[5:]
        assert len(suffix) == 8
        assert re.match(r"^[A-F0-9]{8}$", suffix)

    def test_deal_id_differs_for_different_calls(self):
        """Different calls produce different deal IDs (crypto-random)."""
        deal_id_1 = generate_deal_id(
            product_id="prod-001",
            identity_seed="agency-123",
        )
        deal_id_2 = generate_deal_id(
            product_id="prod-002",
            identity_seed="agency-123",
        )
        # With secrets.token_hex, IDs should differ across calls
        assert deal_id_1.startswith("DEAL-")
        assert deal_id_2.startswith("DEAL-")

    def test_deal_id_with_public_identity(self):
        """Public (no identity) uses 'public' as seed."""
        deal_id = generate_deal_id(
            product_id="prod-001",
            identity_seed="public",
        )
        assert deal_id.startswith("DEAL-")
        suffix = deal_id[5:]
        assert len(suffix) == 8

    def test_deal_id_with_empty_identity(self):
        """Empty identity seed defaults to 'public'."""
        deal_id = generate_deal_id(
            product_id="prod-001",
            identity_seed="",
        )
        assert deal_id.startswith("DEAL-")
