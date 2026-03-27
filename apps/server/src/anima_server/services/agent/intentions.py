"""Intentional agency: goal tracking and procedural memory.

Manages the agent's ongoing intentions (goals it's pursuing across sessions)
and procedural rules (self-derived behavioral rules from experience).

Intentions and rules are stored in the self-model 'intentions' section
as structured markdown, making them human-readable and user-editable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from anima_server.services.agent.self_model import (
    get_active_intentions,
    set_active_intentions,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Intention:
    title: str
    evidence: str = ""
    status: str = "detected"  # detected, active, advanced, stalled, completed, abandoned
    priority: str = "background"  # high, ongoing, background
    deadline: str | None = None
    strategy: str = ""


@dataclass(slots=True)
class ProceduralRule:
    rule: str
    evidence: str = ""
    confidence: str = "low"  # low, medium, high
    added_date: str = ""

    def render(self) -> str:
        line = f"- {self.rule}"
        if self.evidence:
            line += f"\n  (Derived: {self.evidence})"
        return line


MAX_INTENTIONS = 10
MAX_PROCEDURAL_RULES = 10


def get_intentions_text(
    db: Session,
    *,
    user_id: int,
) -> str:
    """Get the raw intentions section content."""
    block = get_active_intentions(db, user_id=user_id)
    return block.content if block else ""


def add_intention(
    db: Session,
    *,
    user_id: int,
    title: str,
    evidence: str = "",
    priority: str = "background",
    deadline: str | None = None,
    strategy: str = "",
) -> str:
    """Add a new intention to the intentions section. Returns updated content."""
    block = get_active_intentions(db, user_id=user_id)
    content = block.content if block else ""

    # Check if intention already exists (fuzzy match on title)
    title_lower = title.lower().strip()
    if title_lower in content.lower():
        return content

    # Build intention entry
    entry_lines = [f"- **{title.strip()}**"]
    if evidence:
        entry_lines.append(f"  - Evidence: {evidence}")
    entry_lines.append(f"  - Status: {_status_label('detected')}")
    if deadline:
        entry_lines.append(f"  - Deadline: {deadline}")
    if strategy:
        entry_lines.append(f"  - Strategy: {strategy}")
    entry = "\n".join(entry_lines)

    # Insert into appropriate priority section
    section_header = _priority_section_header(priority)
    if section_header in content:
        # Insert after section header
        idx = content.index(section_header) + len(section_header)
        content = content[:idx] + "\n\n" + entry + content[idx:]
    else:
        # Append section before behavioral rules
        rules_idx = content.find("# Behavioral Rules")
        if rules_idx == -1:
            content += f"\n\n{section_header}\n\n{entry}"
        else:
            content = content[:rules_idx] + f"{section_header}\n\n{entry}\n\n" + content[rules_idx:]

    set_active_intentions(
        db,
        user_id=user_id,
        content=content,
        updated_by="post_turn",
    )
    return content


def complete_intention(
    db: Session,
    *,
    user_id: int,
    title: str,
) -> bool:
    """Mark an intention as completed. Returns True if found."""
    block = get_active_intentions(db, user_id=user_id)
    if block is None:
        return False

    content = block.content
    # Find the intention line and update status
    pattern = re.compile(
        rf"(\*\*{re.escape(title.strip())}\*\*.*?)(Status:\s*\S+)",
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(content)
    if match is None:
        # Try looser match
        title_lower = title.lower().strip()
        if title_lower not in content.lower():
            return False
        # Replace status line near the title
        lines = content.split("\n")
        found = False
        for i, line in enumerate(lines):
            if title_lower in line.lower() and "**" in line:
                # Find next status line
                for j in range(i + 1, min(i + 5, len(lines))):
                    if "status:" in lines[j].lower():
                        lines[j] = re.sub(
                            r"Status:\s*\S+.*",
                            "Status: Completed",
                            lines[j],
                            flags=re.IGNORECASE,
                        )
                        found = True
                        break
                break
        if not found:
            return False
        content = "\n".join(lines)
    else:
        content = content[: match.start(2)] + "Status: Completed" + content[match.end(2) :]

    set_active_intentions(
        db,
        user_id=user_id,
        content=content,
        updated_by="post_turn",
    )
    return True


def add_procedural_rule(
    db: Session,
    *,
    user_id: int,
    rule: str,
    evidence: str = "",
    confidence: str = "low",
) -> str:
    """Add a behavioral rule to the intentions section."""
    block = get_active_intentions(db, user_id=user_id)
    content = block.content if block else ""

    # Check for duplicate rule
    rule_lower = rule.lower().strip()
    if rule_lower in content.lower():
        return content

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    entry = f"- {rule.strip()}"
    if evidence:
        entry += f"\n  (Derived: {evidence})"
    entry += f"\n  (Added: {date_str}, Confidence: {confidence})"

    rules_marker = "# Behavioral Rules I've Learned"
    if rules_marker in content:
        idx = content.index(rules_marker) + len(rules_marker)
        content = content[:idx] + "\n\n" + entry + content[idx:]
    else:
        content += f"\n\n{rules_marker}\n\n{entry}"

    set_active_intentions(
        db,
        user_id=user_id,
        content=content,
        updated_by="sleep_time",
    )
    return content


def _priority_section_header(priority: str) -> str:
    headers = {
        "high": "## High Priority",
        "ongoing": "## Ongoing",
        "background": "## Background",
    }
    return headers.get(priority, "## Background")


def _status_label(status: str) -> str:
    labels = {
        "detected": "Detected — observing",
        "active": "Active — pursuing",
        "advanced": "Advanced — progress made",
        "stalled": "Stalled — no recent activity",
        "completed": "Completed",
        "abandoned": "Abandoned",
    }
    return labels.get(status, status)
