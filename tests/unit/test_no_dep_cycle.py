# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression guard: no clients <-> orchestration circular dependency.

Part of EP-2.3. `clients.capability_client` used to reach up into
`orchestration.audience_degradation` for the `SellerAudienceCapabilities`
model, papered over with function-local deferred imports. The shared model
now lives in `models.audience_capabilities`, so the dependency flows one way:
orchestration -> clients -> models.

These tests fail if the cycle is reintroduced:

1. Importing either module first (in a fresh interpreter) must succeed with
   no ImportError -- a true cycle would blow up when the module imported
   first tries to resolve the other at top level.
2. A static scan asserts nothing under `clients/` imports from
   `orchestration/`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_MODULES = (
    "ad_buyer.clients.capability_client",
    "ad_buyer.orchestration.multi_seller",
)


@pytest.mark.parametrize(
    "first,second",
    [
        (_MODULES[0], _MODULES[1]),
        (_MODULES[1], _MODULES[0]),
    ],
)
def test_top_level_import_both_orders(first: str, second: str) -> None:
    """Both modules import at top level in either order without error.

    Runs in a fresh subprocess per ordering so sys.modules caching from
    other tests can't mask a real cycle.
    """

    code = f"import {first}\nimport {second}\n"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"importing {first} then {second} failed:\n{result.stderr}"
    )


def test_clients_do_not_import_orchestration() -> None:
    """Static guard: no module under `clients/` imports from `orchestration/`.

    A grep-style scan of the source tree. Catches both `import
    ad_buyer.orchestration...` and `from ..orchestration...` / `from
    ad_buyer.orchestration...`, including function-local (deferred) imports
    that a plain module-graph check would miss.
    """

    clients_dir = Path(__file__).resolve().parents[2] / "src" / "ad_buyer" / "clients"
    assert clients_dir.is_dir(), f"clients dir not found: {clients_dir}"

    offenders: list[str] = []
    for path in sorted(clients_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if (
                "import ad_buyer.orchestration" in stripped
                or "from ad_buyer.orchestration" in stripped
                or "from ..orchestration" in stripped
                or "from .orchestration" in stripped
            ):
                offenders.append(f"{path.name}:{lineno}: {stripped}")

    assert not offenders, "clients/ must not import from orchestration/:\n" + "\n".join(
        offenders
    )
