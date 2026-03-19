#!/usr/bin/env python3
"""Generate PROGRESS.md from beads issues.jsonl for GitHub visibility."""

import json
import re
import os
from datetime import datetime, timezone
from pathlib import Path

BEADS_DIR = Path(__file__).parent
JSONL_PATH = BEADS_DIR / "issues.jsonl"
OUTPUT_PATH = BEADS_DIR / "PROGRESS.md"

# Phase grouping by title prefix (numbered phases like "1A:", "2B:")
PHASE_MAP = {
    "1": ("Phase 1", "Seller Interoperability"),
    "2": ("Phase 2", "Campaign Automation"),
    "3": ("Phase 3", "Platform & Infrastructure"),
    "4": ("Phase 4", "Production Hardening"),
}

# DealJockey phase epic IDs -> display names
# These are the canonical phase epics created per the revised 5-phase plan
# (DEAL_JOCKEY_STRATEGIC_PLAN.md Section 9)
DJ_PHASE_EPICS = {
    "buyer-te6b.1": ("DealJockey Phase 1", "MVP DealJockey"),
    "buyer-te6b.2": ("DealJockey Phase 2", "Templates & Seller Integration"),
    "buyer-te6b.3": ("DealJockey Phase 3", "Portfolio Intelligence"),
    "buyer-te6b.4": ("DealJockey Phase 4", "Platform Integrations"),
    "buyer-te6b.5": ("DealJockey Phase 5", "External Model Integration"),
}

# Ordered list of DJ phase epic IDs for rendering order
DJ_PHASE_ORDER = ["buyer-te6b.1", "buyer-te6b.2", "buyer-te6b.3", "buyer-te6b.4", "buyer-te6b.5"]


def load_issues():
    issues = []
    if not JSONL_PATH.exists():
        return issues
    with open(JSONL_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                issue = json.loads(line)
                if issue.get("status") == "tombstone":
                    continue
                issues.append(issue)
    return issues


def get_phase(title):
    """Extract phase number from title like '1D: ...', '2B: ...', or '1A-Phase1: ...'."""
    m = re.match(r"(\d)[A-Z]-", title)
    if m:
        return m.group(1)
    m = re.match(r"(\d)[A-Z]:", title)
    if m:
        return m.group(1)
    return None


def get_parent_task(title):
    """Extract parent task ID from sub-task title like '1A-Phase1: ...' -> '1A'."""
    m = re.match(r"(\d[A-Z])-", title)
    if m:
        return m.group(1)
    return None


def get_sort_key(title):
    """Sort key: phase number then letter then sub-phase, e.g. '1A-Phase2' -> (1, 'A', 2)."""
    m = re.match(r"(\d)([A-Z])-Phase(\d+):", title)
    if m:
        return (int(m.group(1)), m.group(2), int(m.group(3)))
    m = re.match(r"(\d)([A-Z]):", title)
    if m:
        return (int(m.group(1)), m.group(2), 0)
    return (99, title, 0)


# Cross-repo blockers that can't be tracked as formal bd dependencies.
CROSS_REPO_BLOCKERS = {
    "buyer-4bg": ["seller-dcd"],
    "buyer-kyo": ["seller-awh"],
}


def get_cross_repo_blockers(issue):
    """Get cross-repo blockers for an issue from the static map."""
    return CROSS_REPO_BLOCKERS.get(issue.get("id", ""), [])


def is_blocked(issue, closed_ids, all_ids):
    """Check if issue has unresolved blockers."""
    if issue.get("status") == "closed":
        return False
    deps = issue.get("dependencies") or []
    for dep in deps:
        blocker_id = dep.get("depends_on_id", "")
        if blocker_id and blocker_id in all_ids and blocker_id not in closed_ids:
            return True
    if get_cross_repo_blockers(issue):
        return True
    return False


def get_blocker_ids(issue, all_ids):
    """Get list of depends_on_id values plus cross-repo blockers."""
    deps = issue.get("dependencies") or []
    blockers = [dep.get("depends_on_id", "") for dep in deps
                if dep.get("depends_on_id") and dep.get("depends_on_id") in all_ids]
    blockers.extend(get_cross_repo_blockers(issue))
    return blockers


def build_dj_phase_membership(issues):
    """Build a map of bead ID -> DJ phase epic ID based on dependency relationships.

    A bead belongs to a DJ phase if the phase epic depends on it (i.e., the epic
    lists the bead as a dependency, meaning 'epic blocked by bead').
    """
    bead_to_phase = {}
    for issue in issues:
        iid = issue.get("id", "")
        if iid not in DJ_PHASE_EPICS:
            continue
        # This is a phase epic. Its dependencies are the beads in this phase.
        deps = issue.get("dependencies") or []
        for dep in deps:
            child_id = dep.get("depends_on_id", "")
            if child_id:
                bead_to_phase[child_id] = iid
    return bead_to_phase


def progress_bar(done, total, width=20):
    """Generate a Unicode progress bar."""
    if total == 0:
        return f"`[{'░' * width}] 0%`"
    filled = round(width * done / total)
    empty = width - filled
    pct = round(100 * done / total)
    return f"`[{'█' * filled}{'░' * empty}] {pct}% ({done}/{total})`"


def status_icon(issue, closed_ids, all_ids):
    """Return status icon for an issue."""
    s = issue.get("status", "open")
    if s == "closed":
        return "\\[x]"
    if s == "in_progress":
        return "\\[~]"
    if is_blocked(issue, closed_ids, all_ids):
        return "\\[!]"
    return "\\[ ]"


def format_date(iso_str):
    """Extract just the date from an ISO timestamp."""
    if not iso_str:
        return ""
    return iso_str[:10]


def render_issue_row(issue, closed_ids, all_ids, indent=False):
    """Render a single issue as a markdown table row."""
    icon = status_icon(issue, closed_ids, all_ids)
    iid = issue["id"]
    title = issue.get("title", "")
    if indent:
        title = f"&nbsp;&nbsp;↳ {title}"
    priority = f"P{issue.get('priority', '?')}"
    blockers = get_blocker_ids(issue, all_ids)
    unresolved = [b for b in blockers if b not in closed_ids]
    blocker_str = ", ".join(unresolved) if unresolved else "—"
    done = format_date(issue.get("closed_at", ""))
    return f"| {icon} | {iid} | {title} | {priority} | {blocker_str} | {done} |"


def generate():
    issues = [i for i in load_issues() if i.get("status") != "tombstone"]
    if not issues:
        OUTPUT_PATH.write_text("# Progress\n\nNo issues found.\n")
        return

    # Filter out LEGACY beads
    issues = [i for i in issues if "[LEGACY]" not in i.get("title", "")]

    # Build lookup sets
    closed_ids = {i["id"] for i in issues if i.get("status") == "closed"}
    all_ids = {i["id"] for i in issues}

    # Stats
    total = len(issues)
    closed = len(closed_ids)
    in_progress = len([i for i in issues if i.get("status") == "in_progress"])
    blocked = len([i for i in issues if i.get("status") not in ("closed",) and is_blocked(i, closed_ids, all_ids)])
    open_count = total - closed - in_progress

    # Build DJ phase membership from dependency relationships
    dj_bead_to_phase = build_dj_phase_membership(issues)

    # Group issues
    numbered_phases = {}  # "1" -> [issues], "2" -> [issues], etc.
    dj_phases = {}        # phase_epic_id -> [issues]
    ungrouped = []

    for issue in issues:
        iid = issue.get("id", "")
        title = issue.get("title", "")

        # Skip DJ phase epics themselves and top-level DJ epic (they're headers, not rows)
        if iid in DJ_PHASE_EPICS or iid == "buyer-te6b":
            continue

        # Check if this bead belongs to a DJ phase via dependency
        if iid in dj_bead_to_phase:
            phase_epic_id = dj_bead_to_phase[iid]
            dj_phases.setdefault(phase_epic_id, []).append(issue)
            continue

        # Numbered phase tasks (1A:, 2B:, etc.)
        phase = get_phase(title)
        if phase:
            numbered_phases.setdefault(phase, []).append(issue)
        else:
            ungrouped.append(issue)

    # Sort each group
    for phase in numbered_phases:
        numbered_phases[phase].sort(key=lambda i: get_sort_key(i.get("title", "")))
    for phase_id in dj_phases:
        dj_phases[phase_id].sort(key=lambda i: i.get("title", ""))
    ungrouped.sort(key=lambda i: i.get("title", ""))

    # Build markdown
    lines = []
    lines.append("# Buyer Agent V2 — Progress\n")
    lines.append(f"**{open_count} open** | **{in_progress} in progress** | **{closed} closed** | **{blocked} blocked** | {total} total\n")
    lines.append(f"{progress_bar(closed, total)}\n")

    # Render numbered phases
    for phase_num in sorted(numbered_phases.keys()):
        phase_name, phase_desc = PHASE_MAP.get(phase_num, (f"Phase {phase_num}", ""))
        lines.append(f"## {phase_name} — {phase_desc}\n")
        lines.append("| | ID | Task | Priority | Blockers | Done |")
        lines.append("|---|---|---|---|---|---|")

        top_level = []
        sub_tasks = {}
        for issue in numbered_phases[phase_num]:
            title = issue.get("title", "")
            parent = get_parent_task(title)
            if parent:
                sub_tasks.setdefault(parent, []).append(issue)
            else:
                top_level.append(issue)

        for issue in top_level:
            lines.append(render_issue_row(issue, closed_ids, all_ids))
            task_key_match = re.match(r"(\d[A-Z]):", issue.get("title", ""))
            if task_key_match:
                task_key = task_key_match.group(1)
                for sub in sub_tasks.get(task_key, []):
                    lines.append(render_issue_row(sub, closed_ids, all_ids, indent=True))

        lines.append("")

    # Render DealJockey phases in order
    for phase_epic_id in DJ_PHASE_ORDER:
        if phase_epic_id not in dj_phases:
            continue
        phase_name, phase_desc = DJ_PHASE_EPICS[phase_epic_id]
        lines.append(f"## {phase_name} — {phase_desc}\n")
        lines.append("| | ID | Task | Priority | Blockers | Done |")
        lines.append("|---|---|---|---|---|---|")

        for issue in dj_phases[phase_epic_id]:
            lines.append(render_issue_row(issue, closed_ids, all_ids))
        lines.append("")

    # Ungrouped issues
    if ungrouped:
        lines.append("## Other\n")
        lines.append("| | ID | Task | Priority | Blockers | Done |")
        lines.append("|---|---|---|---|---|---|")
        for issue in ungrouped:
            lines.append(render_issue_row(issue, closed_ids, all_ids))
        lines.append("")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("---")
    lines.append(f"*Last updated: {now} — auto-generated by beads*\n")

    OUTPUT_PATH.write_text("\n".join(lines))


if __name__ == "__main__":
    generate()
