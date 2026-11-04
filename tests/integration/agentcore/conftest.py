"""Conftest for AgentCore runtime integration tests.

Registers custom pytest CLI options for AWS profile and runtime ARN.
"""


def pytest_addoption(parser):
    """Add AgentCore-specific CLI options."""
    parser.addoption("--profile", action="store", default=None, help="AWS CLI profile")
    parser.addoption("--runtime-arn", action="store", default=None, help="Runtime ARN override")
    parser.addoption(
        "--agent-name", action="store", default=None, help="Agent name in .bedrock_agentcore.yaml"
    )


# ---------------------------------------------------------------------------
# Per-test wall-clock timing
#
# These live tests each drive a full Bedrock crew invoke, so how long each one
# takes is a first-class signal (latency is dominated by LLM round-trips, not
# the framework — see the runtime OTEL logs). Record the call-phase duration of
# every test and print a sorted summary at the end so slow paths are obvious
# without editing individual tests.
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

_TEST_DURATIONS: list[tuple[str, float]] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Capture each test's call-phase (body) duration."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        _TEST_DURATIONS.append((report.nodeid, report.duration))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print a sorted wall-clock table for the AgentCore live tests."""
    if not _TEST_DURATIONS:
        return
    rows = sorted(_TEST_DURATIONS, key=lambda r: r[1], reverse=True)
    total = sum(d for _, d in rows)
    terminalreporter.write_sep("=", "AgentCore live test durations (slowest first)")
    for nodeid, dur in rows:
        # Trim to the test name (drop the file path prefix) for readability.
        name = nodeid.split("::", 1)[-1] if "::" in nodeid else nodeid
        terminalreporter.write_line(f"  {dur:7.1f}s  {name}")
    terminalreporter.write_line(f"  {'-' * 40}")
    terminalreporter.write_line(f"  {total:7.1f}s  TOTAL ({len(rows)} tests)")
