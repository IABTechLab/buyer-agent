# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SSP deal import connectors for the deal library.

Each connector implements the SSPConnector interface from
``ssp_connector_base`` and provides deal import for a specific SSP.

Available connectors:
    - PubMaticConnector: PubMatic API connector (Priority 1)
    - MagniteConnector: Magnite Streaming (CTV) and DV+ connector (Priority 2)
    - IndexExchangeConnector: Index Exchange API connector (Priority 3)
"""

from .index_exchange import IndexExchangeConnector
from .magnite import MagniteConnector
from .pubmatic import PubMaticConnector

__all__ = ["IndexExchangeConnector", "MagniteConnector", "PubMaticConnector"]
