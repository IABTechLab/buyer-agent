# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Ad Buyer System - CrewAI-based advertising buyer agent using IAB OpenDirect standards."""

from importlib.metadata import version

from ad_buyer import _telemetry_shim  # noqa: F401  # MUST be first import

__version__ = version("ad-buyer-system")
