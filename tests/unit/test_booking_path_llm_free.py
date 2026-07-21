# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CI-enforceable proof that the money/booking call path is LLM-free (EP-4.1/4.2).

The rule: booking never invokes an LLM. The money-commit steps -- price
computation, ceiling check, deal_id capture, booking record -- are pure
deterministic code, and no LLM client / crew / agent module is reachable
from that path.

This is asserted two ways, both mechanically (no runtime LLM needed):

1. Module-import closure of the booking engine
   (``MultiSellerOrchestrator`` lives in
   ``ad_buyer.orchestration.multi_seller``): the transitive first-party
   import graph of that module must never pull in ``crewai`` or any
   ``ad_buyer.agents`` / ``ad_buyer.crews`` module. ``select_and_book`` and
   the whole discover->quote->rank->book engine sit inside this closure, so
   an LLM cannot be reached from booking without breaking this test.

2. Call-graph walk of ``DealBookingFlow``'s booking half. The flow module
   legitimately imports crews for its *research* steps, so a module-level
   assertion cannot apply to it. Instead we AST-walk the methods reachable
   from ``_execute_bookings`` / ``_book_approved`` and assert none of them
   invokes a crew (``.kickoff()``) or constructs one (``create_*_crew``) --
   proving the booking half is disjoint from the LLM-driven research half.

Part of EP-4.2.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
AD_BUYER_ROOT = SRC_ROOT / "ad_buyer"

# Modules/packages that mean "an LLM is in reach".
_LLM_PACKAGES = {"crewai", "litellm", "langchain", "openai", "anthropic"}
_LLM_FIRST_PARTY_PREFIXES = ("ad_buyer.agents", "ad_buyer.crews")


def _module_path(dotted: str) -> Path | None:
    """Resolve a first-party ``ad_buyer.*`` dotted module to a source file."""
    if not dotted.startswith("ad_buyer"):
        return None
    rel = dotted.split(".")
    candidate = SRC_ROOT.joinpath(*rel).with_suffix(".py")
    if candidate.exists():
        return candidate
    pkg_init = SRC_ROOT.joinpath(*rel, "__init__.py")
    if pkg_init.exists():
        return pkg_init
    return None


def _imports_of(path: Path, module_dotted: str) -> set[str]:
    """Return the fully-qualified module names imported by a source file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    package = module_dotted.rsplit(".", 1)[0] if "." in module_dotted else module_dotted
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import: resolve against this module's package.
                base_parts = package.split(".")
                # `level` steps up from the current package.
                base = base_parts[: len(base_parts) - (node.level - 1)]
                prefix = ".".join(base)
                mod = f"{prefix}.{node.module}" if node.module else prefix
            else:
                mod = node.module or ""
            if mod:
                found.add(mod)
    return found


def _transitive_import_closure(entry_dotted: str) -> set[str]:
    """Walk the first-party import graph from ``entry_dotted``.

    Returns every module name encountered (first-party and external). Only
    first-party ``ad_buyer.*`` modules are recursed into; external modules
    are recorded but not expanded (we cannot / need not read their source).
    """
    seen: set[str] = set()
    stack = [entry_dotted]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        path = _module_path(current)
        if path is None:
            continue  # external module: record, do not expand
        for imported in _imports_of(path, current):
            if imported not in seen:
                stack.append(imported)
    return seen


class TestBookingEngineImportClosureIsLLMFree:
    """The orchestrator's transitive import graph never reaches an LLM."""

    def test_multi_seller_closure_has_no_llm_module(self):
        closure = _transitive_import_closure("ad_buyer.orchestration.multi_seller")

        # Sanity: we actually walked a non-trivial graph including the engine
        # and its deterministic collaborators.
        assert "ad_buyer.orchestration.multi_seller" in closure
        assert "ad_buyer.booking.quote_normalizer" in closure

        offenders = sorted(
            mod
            for mod in closure
            if mod.split(".")[0] in _LLM_PACKAGES
            or mod.startswith(_LLM_FIRST_PARTY_PREFIXES)
        )
        assert not offenders, (
            "The MultiSellerOrchestrator import closure must be LLM-free, but "
            f"these LLM/crew/agent modules are reachable from it: {offenders}"
        )


class TestDealBookingFlowBookingHalfIsLLMFree:
    """The flow's booking half never invokes or constructs a crew."""

    @staticmethod
    def _load_flow_methods() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
        flow_src = AD_BUYER_ROOT / "flows" / "deal_booking_flow.py"
        tree = ast.parse(flow_src.read_text(), filename=str(flow_src))
        class_def = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "DealBookingFlow"
        )
        return {
            member.name: member
            for member in class_def.body
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    @staticmethod
    def _self_methods_called(func: ast.AST) -> set[str]:
        """Names of ``self.<method>(...)`` calls inside a function body."""
        called: set[str] = set()
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
            ):
                called.add(node.func.attr)
        return called

    @staticmethod
    def _invokes_a_crew(func: ast.AST) -> bool:
        """True if the function calls ``.kickoff()`` or a ``create_*_crew``."""
        for node in ast.walk(func):
            if isinstance(node, ast.Call):
                func_node = node.func
                if isinstance(func_node, ast.Attribute) and func_node.attr == "kickoff":
                    return True
                if isinstance(func_node, ast.Name) and func_node.id.startswith("create_") and (
                    func_node.id.endswith("_crew")
                ):
                    return True
        return False

    def _reachable_from(self, entry: str) -> set[str]:
        methods = self._load_flow_methods()
        reachable: set[str] = set()
        stack = [entry]
        while stack:
            name = stack.pop()
            if name in reachable or name not in methods:
                continue
            reachable.add(name)
            stack.extend(self._self_methods_called(methods[name]))
        return reachable

    def test_booking_methods_never_invoke_a_crew(self):
        methods = self._load_flow_methods()
        booking_reachable = self._reachable_from("_execute_bookings") | self._reachable_from(
            "_book_approved"
        )

        # The booking entry points must be present (guards against silent
        # renames making this test vacuous).
        assert "_execute_bookings" in booking_reachable
        assert "_book_approved" in booking_reachable

        offenders = sorted(
            name for name in booking_reachable if self._invokes_a_crew(methods[name])
        )
        assert not offenders, (
            "Booking must never invoke an LLM/crew, but these methods reachable "
            f"from the booking path call a crew: {offenders}"
        )

    def test_research_half_does_use_crews_so_the_guard_is_meaningful(self):
        """Control: the research half DOES invoke crews (test isn't vacuous)."""
        methods = self._load_flow_methods()
        crew_users = {name for name, node in methods.items() if self._invokes_a_crew(node)}

        # These research/allocation steps are expected to drive crews...
        assert "allocate_budget" in crew_users
        assert "research_branding" in crew_users

        # ...and they must NOT be reachable from the booking path.
        booking_reachable = self._reachable_from("_execute_bookings") | self._reachable_from(
            "_book_approved"
        )
        assert not (crew_users & booking_reachable), (
            "A crew-invoking method is reachable from the booking path: "
            f"{sorted(crew_users & booking_reachable)}"
        )
