# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Ad server integration clients for creative serving and measurement.

Provides abstract base class and stub implementations for Innovid (CTV)
and Flashtalking (display) ad server platforms. The AdServerManager routes
operations to the correct client based on ad server type.

Per Karl's H-1 finding: These are stub/mock implementations designed with
production-ready interfaces so they can be swapped for real API integrations
when business partnerships are established.

References:
  - Campaign Automation Strategic Plan, Section 7.5

bead: buyer-7m8
"""

from .base import AdServerClient
from .flashtalking import FlashtalkingClient
from .innovid import InnovidClient
from .manager import AdServerManager

__all__ = [
    "AdServerClient",
    "InnovidClient",
    "FlashtalkingClient",
    "AdServerManager",
]
