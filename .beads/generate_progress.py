#!/usr/bin/env python3
"""Generate PROGRESS.md from beads issues.jsonl for GitHub visibility.

V3: Epic-driven discovery replaces hardcoded phase maps.
- Auto-discovers epics and extracts phase info from titles
- Creates synthetic phases for numbered-prefix beads without epics
- Three-step bead assignment prevents collisions
- Refreshes JSONL from database when running locally
- Handles sub-epics (Phase 4A, 4B, etc.) automatically
"""

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BEADS_DIR = Path(__file__).parent
JSONL_PATH = BEADS_DIR / "issues.jsonl"
OUTPUT_PATH = BEADS_DIR / "PROGRESS.md"

# Cross-repo blockers that can't be tracked as formal bd dependencies.
CROSS_REPO_BLOCKERS = {
    "buyer-4bg": ["seller-dcd"],
    "buyer-kyo": ["seller-awh"],
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def refresh_jsonl():
    """Re-export JSONL from the local beads database before generating.

    Skipped in CI (GITHUB_ACTIONS env) where no SQLite DB exists, and
    skipped when `bd` isn't installed.
    """
    if os.environ.get("GITHUB_ACTIONS"):
        return  # CI uses committed JSONL
    if not shutil.which("bd"):
        return
    try:
        subprocess.run(
            ["bd", "export", "--no-auto-import", "-o", str(JSONL_PATH)],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Fall back to existing JSONL


def load_issues():
    """Load issues from JSONL, filtering tombstones and LEGACY beads."""
    issues = []
    if not JSONL_PATH.exists():
        return issues
    with open(JSONL_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            issue = json.loads(line)
            if issue.get("status") == "tombstone":
                continue
            if "[LEGACY]" in issue.get("title", ""):
                continue
            issues.append(issue)
    return issues


# ---------------------------------------------------------------------------
# Epic discovery and phase extraction
# ---------------------------------------------------------------------------

_PHASE_RE = re.compile(r"Phase\s+(\d+)([A-Z])?[\s:—]")


def extract_phase_info(title):
    """Extract (phase_number, sub_letter_or_None) from an epic title.

    Examples:
        "Phase 1: Seller Interoperability" -> (1, None)
        "Phase 4A: MVP DealJockey"         -> (4, "A")
    """
    m = _PHASE_RE.search(title)
    if m:
        return int(m.group(1)), m.group(2)
    return None, None


def discover_epics(issues):
    """Find all epics with phase info in their titles.

    Returns a dict keyed by epic ID:
        {epic_id: {
            "issue": <issue dict>,
            "phase": int,
            "sub": str or None,   # e.g. "A"
            "deps": [dep_id, ...],
            "is_sub_epic": bool,  # True if this epic is a dep of another phase epic
        }}
    """
    epics = {}
    for issue in issues:
        if issue.get("issue_type") != "epic":
            continue
        iid = issue["id"]
        title = issue.get("title", "")
        phase, sub = extract_phase_info(title)
        if phase is None:
            continue
        deps = [d["depends_on_id"] for d in (issue.get("dependencies") or [])
                if d.get("depends_on_id")]
        epics[iid] = {
            "issue": issue,
            "phase": phase,
            "sub": sub,
            "deps": deps,
            "is_sub_epic": False,
        }

    # Detect sub-epics: if an epic's deps include another epic, those deps
    # are sub-epics (e.g. buyer-te6b depends on buyer-te6b.1..5)
    epic_ids = set(epics.keys())
    for eid, info in epics.items():
        for dep_id in info["deps"]:
            if dep_id in epic_ids:
                epics[dep_id]["is_sub_epic"] = True

    return epics


# ---------------------------------------------------------------------------
# Phase structure building
# ---------------------------------------------------------------------------

_NUMBERED_PREFIX_RE = re.compile(r"^(\d)([A-Z])[:\-]")


def build_phase_structure(epics, issues):
    """Build ordered phase structure from discovered epics and issue titles.

    Discovers phases from epics, then creates synthetic phases for any
    numbered-prefix beads (e.g. "1A:", "3B:") whose phase number has no
    epic. This eliminates the need for hardcoded phase maps.

    Returns a list of phase dicts, sorted by phase number:
        [{"phase": 1, "label": "Phase 1", "desc": "...",
          "top_epic_id": "buyer-xyz" or None,
          "sub_phases": [...],
          "deps": [...],
         }, ...]
    """
    # Group epics by phase number
    by_phase = {}  # phase_num -> [epic_info, ...]
    for eid, info in epics.items():
        by_phase.setdefault(info["phase"], []).append((eid, info))

    # Detect phase numbers that have numbered-prefix beads but no epic
    epic_phase_nums = set(by_phase.keys())
    synthetic_phase_nums = set()
    for issue in issues:
        title = issue.get("title", "")
        m = _NUMBERED_PREFIX_RE.match(title)
        if m:
            pn = int(m.group(1))
            if pn not in epic_phase_nums:
                synthetic_phase_nums.add(pn)

    phases = []

    # Build phases from epics
    for phase_num in sorted(by_phase.keys()):
        group = by_phase[phase_num]
        phase_entry = _build_epic_phase(phase_num, group)
        phases.append(phase_entry)

    # Build synthetic phases for numbered-prefix-only phases
    # Fallback descriptions for phases that have numbered-prefix beads but
    # no corresponding epic record in the database.
    _SYNTHETIC_PHASE_DESCRIPTIONS = {
        1: "Seller Interoperability",
        3: "Platform & Infrastructure",
    }
    for phase_num in sorted(synthetic_phase_nums):
        phases.append({
            "phase": phase_num,
            "label": f"Phase {phase_num}",
            "desc": _SYNTHETIC_PHASE_DESCRIPTIONS.get(phase_num, ""),
            "top_epic_id": None,
            "sub_phases": [],
            "deps": [],
            "synthetic": True,
        })

    # Sort all phases by phase number
    phases.sort(key=lambda p: p["phase"])
    return phases


def _build_epic_phase(phase_num, group):
    """Build a phase entry from a group of epics with the same phase number."""
    # Find top-level epic (the one without a sub-letter, or the one that
    # isn't a sub-epic of another)
    top_epic = None
    sub_epics = []
    for eid, info in group:
        if info["sub"] is None and not info["is_sub_epic"]:
            top_epic = (eid, info)
        elif info["is_sub_epic"] or info["sub"] is not None:
            sub_epics.append((eid, info))
        else:
            sub_epics.append((eid, info))

    # If no top-level epic found, pick the first one without a sub-letter
    if top_epic is None:
        for eid, info in group:
            if info["sub"] is None:
                top_epic = (eid, info)
                sub_epics = [(e, i) for e, i in group if e != eid]
                break
    # Still none? Just pick first
    if top_epic is None:
        top_epic = group[0]
        sub_epics = group[1:]

    top_eid, top_info = top_epic

    # Extract description from title (after "Phase N[X]: " or "Phase N[X] — ")
    title = top_info["issue"]["title"]
    desc_match = re.search(r"Phase\s+\d+[A-Z]?\s*[:\s—]+\s*(.*)", title)
    desc = desc_match.group(1).strip() if desc_match else ""

    phase_entry = {
        "phase": phase_num,
        "label": f"Phase {phase_num}",
        "desc": desc,
        "top_epic_id": top_eid,
        "sub_phases": [],
        "deps": top_info["deps"] if not sub_epics else [],
        "synthetic": False,
    }

    # Sort sub-epics by sub letter
    sub_epics.sort(key=lambda x: x[1].get("sub") or "Z")
    for sub_eid, sub_info in sub_epics:
        sub_title = sub_info["issue"]["title"]
        sub_desc_match = re.search(r"Phase\s+\d+[A-Z]?\s*[:\s—]+\s*(.*)", sub_title)
        sub_desc = sub_desc_match.group(1).strip() if sub_desc_match else ""
        phase_entry["sub_phases"].append({
            "sub": sub_info["sub"],
            "label": f"Phase {phase_num}{sub_info['sub'] or ''}",
            "desc": sub_desc,
            "epic_id": sub_eid,
            "deps": sub_info["deps"],
        })

    return phase_entry


# ---------------------------------------------------------------------------
# Three-step bead assignment
# ---------------------------------------------------------------------------

def assign_beads(issues, phases, epics):
    """Assign each non-epic bead to exactly one section. Returns:
        bead_to_section: {bead_id: section_key}
    where section_key is one of:
        - ("phase", phase_num, None) for flat phases
        - ("phase", phase_num, sub_letter) for sub-phases
        - ("other",)
    """
    claimed = {}  # bead_id -> section_key
    epic_ids = set(epics.keys())
    phase_nums = {p["phase"] for p in phases}

    # Collect all non-epic bead IDs
    bead_ids = set()
    for issue in issues:
        iid = issue["id"]
        if iid not in epic_ids:
            bead_ids.add(iid)

    # --- Step 1: Numbered prefix assignment ---
    # Beads with titles like "2A: Something" -> Phase 2
    for issue in issues:
        iid = issue["id"]
        if iid in claimed or iid in epic_ids:
            continue
        title = issue.get("title", "")
        m = _NUMBERED_PREFIX_RE.match(title)
        if m:
            phase_num = int(m.group(1))
            if phase_num in phase_nums:
                section = _find_phase_section(phase_num, phases)
                if section:
                    claimed[iid] = section

    # --- Step 2: Epic direct deps (first match, in phase order) ---
    # Process phases in order; for phases with sub-phases, process sub-phases
    # in sub-letter order. First epic to claim a bead wins.
    for phase in phases:
        if phase["sub_phases"]:
            for sub in phase["sub_phases"]:
                section_key = ("phase", phase["phase"], sub["sub"])
                for dep_id in sub["deps"]:
                    if dep_id not in claimed and dep_id in bead_ids:
                        claimed[dep_id] = section_key
        else:
            section_key = ("phase", phase["phase"], None)
            for dep_id in phase["deps"]:
                if dep_id not in claimed and dep_id in bead_ids:
                    claimed[dep_id] = section_key

    # --- Step 3: Reverse dep propagation ---
    # If all of a bead's dependents (beads that depend on it) are in the
    # same section, assign the bead there too. Repeat until stable.
    reverse_deps = {}
    for issue in issues:
        for dep in (issue.get("dependencies") or []):
            dep_id = dep.get("depends_on_id", "")
            if dep_id and dep_id in bead_ids:
                reverse_deps.setdefault(dep_id, set()).add(issue["id"])

    changed = True
    max_iterations = 50
    iteration = 0
    while changed and iteration < max_iterations:
        changed = False
        iteration += 1
        for bead_id in bead_ids:
            if bead_id in claimed:
                continue
            dependents = reverse_deps.get(bead_id, set())
            if not dependents:
                continue
            # Only consider dependents that are non-epic beads
            relevant = dependents - epic_ids
            if not relevant:
                continue
            # Check if all relevant dependents are in the same section
            sections = set()
            all_claimed = True
            for did in relevant:
                if did in claimed:
                    sections.add(claimed[did])
                else:
                    all_claimed = False
                    break
            if all_claimed and len(sections) == 1:
                claimed[bead_id] = sections.pop()
                changed = True

    # --- Step 4: Everything else -> "other" ---
    for bead_id in bead_ids:
        if bead_id not in claimed:
            claimed[bead_id] = ("other",)

    return claimed


def _find_phase_section(phase_num, phases):
    """Find the section key for a given phase number (flat phases only)."""
    for phase in phases:
        if phase["phase"] == phase_num:
            if phase["sub_phases"]:
                # Numbered prefix beads in a sub-phase world: put in first sub
                return ("phase", phase_num, phase["sub_phases"][0]["sub"])
            return ("phase", phase_num, None)
    return None


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

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


def get_sort_key(title):
    """Sort key: phase number then letter then sub-phase."""
    m = re.match(r"(\d)([A-Z])-Phase(\d+):", title)
    if m:
        return (int(m.group(1)), m.group(2), int(m.group(3)))
    m = re.match(r"(\d)([A-Z]):", title)
    if m:
        return (int(m.group(1)), m.group(2), 0)
    return (99, title, 0)


def get_parent_task(title):
    """Extract parent task prefix from sub-task title like '1A-Phase1: ...' -> '1A'."""
    m = re.match(r"(\d[A-Z])-", title)
    if m:
        return m.group(1)
    return None


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


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate():
    refresh_jsonl()

    issues = load_issues()
    if not issues:
        OUTPUT_PATH.write_text("# Progress\n\nNo issues found.\n")
        return

    # Build lookup sets
    closed_ids = {i["id"] for i in issues if i.get("status") == "closed"}
    all_ids = {i["id"] for i in issues}

    # Discover epics and build phase structure
    epics = discover_epics(issues)
    epic_ids = set(epics.keys())

    # Also exclude non-phase epics from display (e.g. "Epic: Buyer reporting agent")
    all_epic_ids = {i["id"] for i in issues if i.get("issue_type") == "epic"}

    display_issues = [i for i in issues
                      if i["id"] not in all_epic_ids
                      and i.get("issue_type") != "bug"]
    total = len(display_issues)
    closed = len([i for i in display_issues if i.get("status") == "closed"])
    in_progress = len([i for i in display_issues if i.get("status") == "in_progress"])
    blocked = len([i for i in display_issues
                   if i.get("status") not in ("closed",)
                   and is_blocked(i, closed_ids, all_ids)])
    open_count = total - closed - in_progress

    # Build phase structure and assign beads
    phases = build_phase_structure(epics, issues)
    bead_to_section = assign_beads(issues, phases, epics)

    # Group display issues by section
    sections = {}  # section_key -> [issues]
    for issue in display_issues:
        section = bead_to_section.get(issue["id"], ("other",))
        sections.setdefault(section, []).append(issue)

    # Build markdown
    lines = []
    lines.append("# Buyer Agent V2 — Progress\n")
    lines.append(f"**{open_count} open** | **{in_progress} in progress** | "
                 f"**{closed} closed** | **{blocked} blocked** | {total} total\n")
    lines.append(f"{progress_bar(closed, total)}\n")

    # Render each phase
    for phase in phases:
        phase_num = phase["phase"]

        if phase["sub_phases"]:
            # Phase with sub-phases (e.g., Phase 4 DealJockey)
            lines.append(f"## {phase['label']} — {phase['desc']}\n")

            for sub in phase["sub_phases"]:
                section_key = ("phase", phase_num, sub["sub"])
                phase_issues = sections.get(section_key, [])
                if not phase_issues:
                    continue

                lines.append(f"### {sub['label']} — {sub['desc']}\n")
                lines.append("| | ID | Task | Priority | Blockers | Done |")
                lines.append("|---|---|---|---|---|---|")

                phase_issues.sort(key=lambda i: i.get("title", ""))
                for issue in phase_issues:
                    lines.append(render_issue_row(issue, closed_ids, all_ids))
                lines.append("")
        else:
            # Flat phase (Phase 1, 2, 3, or synthetic)
            section_key = ("phase", phase_num, None)
            phase_issues = sections.get(section_key, [])
            if not phase_issues:
                continue

            desc_part = f" — {phase['desc']}" if phase["desc"] else ""
            lines.append(f"## {phase['label']}{desc_part}\n")
            lines.append("| | ID | Task | Priority | Blockers | Done |")
            lines.append("|---|---|---|---|---|---|")

            # Split into numbered-prefix (top-level + sub-tasks) and gap beads
            numbered = []
            gap = []
            for issue in phase_issues:
                title = issue.get("title", "")
                if _NUMBERED_PREFIX_RE.match(title):
                    numbered.append(issue)
                else:
                    gap.append(issue)

            # Sort numbered by prefix, then render with sub-tasks indented
            numbered.sort(key=lambda i: get_sort_key(i.get("title", "")))
            top_level = []
            sub_tasks = {}
            for issue in numbered:
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

            # Gap beads sorted by title
            gap.sort(key=lambda i: i.get("title", ""))
            for issue in gap:
                lines.append(render_issue_row(issue, closed_ids, all_ids))

            lines.append("")

    # Other section
    other_issues = sections.get(("other",), [])
    if other_issues:
        lines.append("## Other\n")
        lines.append("| | ID | Task | Priority | Blockers | Done |")
        lines.append("|---|---|---|---|---|---|")
        other_issues.sort(key=lambda i: i.get("title", ""))
        for issue in other_issues:
            lines.append(render_issue_row(issue, closed_ids, all_ids))
        lines.append("")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("---")
    lines.append(f"*Last updated: {now} — auto-generated by beads*\n")

    OUTPUT_PATH.write_text("\n".join(lines))


if __name__ == "__main__":
    generate()
