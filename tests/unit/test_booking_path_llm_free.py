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

3. Crew tool-inventory check on the actually-built crews. (1) and (2) prove
   booking is unreachable from deterministic code, but say nothing about
   what a crew's own manager LLM can do at runtime: in crewai's hierarchical
   process, the manager may delegate a task to ANY agent present in a
   crew's ``agents=[]`` list, whether or not a task was ever assigned to
   that agent. A research crew that also carried an idle agent wired with
   live OpenDirect order-writing tools (``CreateOrderTool``,
   ``CreateLineTool``, ``ReserveLineTool``, ``BookLineTool``) would be one
   delegation away from writing a real order outside the booking path
   proven LLM-free above. We build each production crew the way the app
   does and assert no agent -- manager or otherwise -- carries one of
   those tool classes.

Part of EP-4.2.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from unittest.mock import MagicMock

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
AD_BUYER_ROOT = SRC_ROOT / "ad_buyer"

# Agents validate their LLM client on creation; a dummy key is enough since
# no crew in this file is ever kicked off.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-ci")

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
            if mod.split(".")[0] in _LLM_PACKAGES or mod.startswith(_LLM_FIRST_PARTY_PREFIXES)
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
                if (
                    isinstance(func_node, ast.Name)
                    and func_node.id.startswith("create_")
                    and (func_node.id.endswith("_crew"))
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


class TestNoCrewAgentCarriesAnOrderWritingTool:
    """No agent in any built crew may carry a live order-writing tool.

    Import closure and call-graph checks above prove the booking path
    cannot reach an LLM. They do not prove the converse: that an LLM-driven
    crew cannot reach booking. In crewai's hierarchical process, the
    manager LLM may delegate a task to any agent present in a crew's
    ``agents=[]`` list -- including an agent that was never assigned a
    task. A research/recommendation crew has no business holding an agent
    wired with tools that write real OpenDirect orders (``CreateOrderTool``,
    ``CreateLineTool``, ``ReserveLineTool``, ``BookLineTool``); all real
    booking must go through the deterministic ``DealBookingFlow`` /
    ``MultiSellerOrchestrator`` path proven LLM-free above.

    This test builds every production crew the way the application does
    (mocked OpenDirect client, no network calls at construction time) and
    inspects every agent's tool list -- manager included -- for those four
    tool classes. If someone re-attaches an execution agent, or any other
    agent, with one of these tools to a research crew, this test fails.
    """

    @staticmethod
    def _order_writing_tool_classes() -> tuple[type, ...]:
        from ad_buyer.tools.execution.line_management import (
            BookLineTool,
            CreateLineTool,
            ReserveLineTool,
        )
        from ad_buyer.tools.execution.order_management import CreateOrderTool

        return (CreateOrderTool, CreateLineTool, ReserveLineTool, BookLineTool)

    @staticmethod
    def _built_crews() -> dict[str, object]:
        """Build every production crew with a mocked OpenDirect client.

        MagicMock is safe here: none of these factories dispatch a network
        call at construction time, only at ``.kickoff()``, which this test
        never calls.
        """
        from ad_buyer.crews.channel_crews import (
            create_branding_crew,
            create_ctv_crew,
            create_mobile_crew,
            create_performance_crew,
            create_social_crew,
        )
        from ad_buyer.crews.portfolio_crew import create_portfolio_crew

        client = MagicMock()
        channel_brief = {
            "budget": 10_000,
            "start_date": "2025-03-01",
            "end_date": "2025-03-31",
            "target_audience": {"age": "25-54"},
            "objectives": ["awareness"],
            "kpis": {"cpa": 10},
        }
        campaign_brief = {
            "name": "Test Campaign",
            "objectives": ["awareness"],
            "budget": 50_000,
            "start_date": "2025-03-01",
            "end_date": "2025-03-31",
            "target_audience": {"age": "25-54"},
            "kpis": {"viewability": 70},
        }

        return {
            "branding": create_branding_crew(client, channel_brief),
            "mobile": create_mobile_crew(client, channel_brief),
            "ctv": create_ctv_crew(client, channel_brief),
            "performance": create_performance_crew(client, channel_brief),
            "social": create_social_crew(client, channel_brief),
            "portfolio": create_portfolio_crew(client, campaign_brief),
        }

    def test_no_agent_in_any_built_crew_carries_an_order_writing_tool(self):
        order_writing_tool_classes = self._order_writing_tool_classes()
        crews = self._built_crews()

        offenders: list[str] = []
        for crew_name, crew in crews.items():
            agents = list(crew.agents)
            if crew.manager_agent is not None and crew.manager_agent not in agents:
                agents.append(crew.manager_agent)
            for agent in agents:
                for tool in getattr(agent, "tools", None) or []:
                    if isinstance(tool, order_writing_tool_classes):
                        offenders.append(
                            f"{crew_name} crew: agent role={agent.role!r} carries "
                            f"{type(tool).__name__}"
                        )

        assert not offenders, (
            "Found order-writing OpenDirect tools reachable from a crewai "
            "hierarchical-process agent list. In that process the manager "
            "LLM may delegate to ANY agent in `agents=[]`, whether or not a "
            "task was ever assigned to it, so this is a live booking "
            "loophole -- all order writes must go through the deterministic "
            "DealBookingFlow path instead. Offending agents:\n  - "
            + "\n  - ".join(offenders)
        )

    def test_the_tool_classes_checked_are_the_real_execution_tool_bundle(self):
        """Control: the checked classes are exactly `_create_execution_tools`'s.

        Guards against the offender list silently going stale (e.g. a
        renamed or added execution tool) by cross-checking against the
        crew module's own execution-tool factory.
        """
        from ad_buyer.crews.channel_crews import _create_execution_tools

        execution_tools = _create_execution_tools(MagicMock())
        execution_tool_types = {type(t) for t in execution_tools}

        assert execution_tool_types == set(self._order_writing_tool_classes())
