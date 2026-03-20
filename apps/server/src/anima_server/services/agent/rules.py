from __future__ import annotations

import json
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TerminalToolRule:
    tool_name: str


@dataclass(frozen=True, slots=True)
class InitToolRule:
    tool_name: str


@dataclass(frozen=True, slots=True)
class ChildToolRule:
    tool_name: str
    children: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RequiresApprovalToolRule:
    tool_name: str


@dataclass(frozen=True, slots=True)
class PrerequisiteToolRule:
    """Tool B is unavailable until tool A has been called this turn."""

    prerequisite_tool: str
    dependent_tool: str


@dataclass(frozen=True, slots=True)
class ConditionalToolRule:
    """Output-based routing: tool_name's return value maps to a child tool."""

    tool_name: str
    child_output_mapping: dict[str, str]  # output value → child tool name
    default_child: str | None = None
    require_output_mapping: bool = False


ToolRule = (
    TerminalToolRule
    | InitToolRule
    | ChildToolRule
    | RequiresApprovalToolRule
    | PrerequisiteToolRule
    | ConditionalToolRule
)


class ToolRulesSolver:
    """Stateful solver for Letta-style tool orchestration rules."""

    def __init__(self, tool_rules: Sequence[ToolRule] = ()) -> None:
        self._init_rules = tuple(
            rule for rule in tool_rules if isinstance(rule, InitToolRule)
        )
        self._terminal_tools = {
            rule.tool_name
            for rule in tool_rules
            if isinstance(rule, TerminalToolRule)
        }
        self._child_rules = {
            rule.tool_name: rule
            for rule in tool_rules
            if isinstance(rule, ChildToolRule)
        }
        self._approval_tools = {
            rule.tool_name
            for rule in tool_rules
            if isinstance(rule, RequiresApprovalToolRule)
        }
        self._prerequisite_rules = tuple(
            rule for rule in tool_rules if isinstance(rule, PrerequisiteToolRule)
        )
        self._conditional_rules = {
            rule.tool_name: rule
            for rule in tool_rules
            if isinstance(rule, ConditionalToolRule)
        }
        self._call_history: list[str] = []
        self._last_tool_return_value: str | None = None

        # Validate: warn on empty conditional mappings
        for rule in tool_rules:
            if isinstance(rule, ConditionalToolRule) and not rule.child_output_mapping:
                raise ValueError(
                    f"ConditionalToolRule for {rule.tool_name!r} has empty child_output_mapping."
                )

        # Detect circular dependencies in prerequisite rules
        _detect_cycles(self._prerequisite_rules, self._conditional_rules)

    def warn_unknown_tools(self, known_tools: Collection[str]) -> None:
        """Log warnings for rules that reference tools not in the registry."""
        known = _normalize_tool_names(known_tools)
        for rule in self._init_rules:
            if rule.tool_name not in known:
                _warn_rule_issue(
                    "InitToolRule references unknown tool %r", rule.tool_name)
        for name in self._terminal_tools:
            if name not in known:
                _warn_rule_issue(
                    "TerminalToolRule references unknown tool %r", name)
        for rule in self._prerequisite_rules:
            if rule.prerequisite_tool not in known:
                _warn_rule_issue(
                    "PrerequisiteToolRule references unknown prerequisite tool %r", rule.prerequisite_tool)
            if rule.dependent_tool not in known:
                _warn_rule_issue(
                    "PrerequisiteToolRule references unknown dependent tool %r", rule.dependent_tool)
        for rule in self._conditional_rules.values():
            if rule.tool_name not in known:
                _warn_rule_issue(
                    "ConditionalToolRule references unknown tool %r", rule.tool_name)
            for child in rule.child_output_mapping.values():
                if child not in known:
                    _warn_rule_issue(
                        "ConditionalToolRule maps to unknown child tool %r", child)
            if rule.default_child and rule.default_child not in known:
                _warn_rule_issue(
                    "ConditionalToolRule default_child references unknown tool %r", rule.default_child)

    @property
    def call_history(self) -> tuple[str, ...]:
        return tuple(self._call_history)

    @property
    def last_tool_return_value(self) -> str | None:
        return self._last_tool_return_value

    def get_allowed_tools(self, all_tools: Collection[str]) -> set[str]:
        available_tools = _normalize_tool_names(all_tools)
        if not available_tools:
            return set()

        if not self._call_history and self._init_rules:
            allowed = {
                rule.tool_name
                for rule in self._init_rules
                if rule.tool_name in available_tools
            }
            return self._apply_prerequisite_filter(allowed, available_tools)

        if self._call_history:
            last_tool = self._call_history[-1]

            # Conditional rules (output-based routing) take priority
            cond_rule = self._conditional_rules.get(last_tool)
            if cond_rule is not None:
                cond_result = self._evaluate_conditional_rule(
                    cond_rule, available_tools)
                if cond_result is not None:
                    return self._apply_prerequisite_filter(cond_result, available_tools)

            # Child rules (static parent→children mapping)
            child_rule = self._child_rules.get(last_tool)
            if child_rule is not None:
                allowed = {
                    child
                    for child in child_rule.children
                    if child in available_tools
                }
                return self._apply_prerequisite_filter(allowed, available_tools)

        return self._apply_prerequisite_filter(set(available_tools), available_tools)

    def _apply_prerequisite_filter(
        self, allowed: set[str], available_tools: set[str],
    ) -> set[str]:
        """Remove tools whose prerequisites have not been called this turn."""
        if not self._prerequisite_rules:
            return allowed
        called = set(self._call_history)
        for rule in self._prerequisite_rules:
            if (
                rule.dependent_tool in allowed
                and rule.prerequisite_tool not in called
            ):
                allowed = allowed - {rule.dependent_tool}
        return allowed

    def _evaluate_conditional_rule(
        self, rule: ConditionalToolRule, available_tools: set[str],
    ) -> set[str] | None:
        """Return the allowed tool set based on the last tool's output."""
        response = self._last_tool_return_value
        if response is None:
            if rule.require_output_mapping:
                return set()
            return {rule.default_child} & available_tools if rule.default_child else None

        # Try to parse JSON and extract message field (Letta convention)
        output_value = response
        try:
            parsed = json.loads(response)
            if isinstance(parsed, dict) and "message" in parsed:
                output_value = str(parsed["message"])
        except (json.JSONDecodeError, TypeError):
            pass

        # Match output to mapping
        for key, child_tool in rule.child_output_mapping.items():
            if _matches_conditional_key(output_value, key):
                return {child_tool} & available_tools

        # No match
        if rule.require_output_mapping:
            return set()
        if rule.default_child:
            return {rule.default_child} & available_tools
        return None

    def should_force_tool_call(self) -> bool:
        if not self._call_history and self._init_rules:
            return True
        if self._call_history and self._call_history[-1] in self._child_rules:
            return True
        return False

    def is_terminal(self, tool_name: str) -> bool:
        return tool_name in self._terminal_tools

    def requires_approval(self, tool_name: str) -> bool:
        return tool_name in self._approval_tools

    def validate_tool_call(
        self,
        tool_name: str,
        all_tools: Collection[str],
    ) -> str | None:
        normalized_name = tool_name.strip()
        if not normalized_name:
            return "Tool name is empty."

        available_tools = _normalize_tool_names(all_tools)
        if normalized_name not in available_tools:
            return f"Tool {normalized_name!r} is not registered for this agent."

        allowed_tools = self.get_allowed_tools(available_tools)
        if normalized_name in allowed_tools:
            return None

        if not self._call_history and self._init_rules:
            init_tools = ", ".join(
                sorted(
                    rule.tool_name
                    for rule in self._init_rules
                    if rule.tool_name in available_tools
                )
            )
            return (
                f"Tool {normalized_name!r} is not allowed yet. "
                f"The first tool call must be one of: {init_tools}."
            )

        if self._call_history:
            last_tool = self._call_history[-1]
            child_rule = self._child_rules.get(last_tool)
            if child_rule is not None:
                children = ", ".join(child_rule.children)
                return (
                    f"Tool {normalized_name!r} is not allowed after {last_tool!r}. "
                    f"Allowed next tools: {children}."
                )

        allowed_display = ", ".join(sorted(allowed_tools))
        return (
            f"Tool {normalized_name!r} is not allowed right now. "
            f"Allowed tools: {allowed_display}."
        )

    def update_state(self, tool_name: str, return_value: str | None = None) -> None:
        normalized_name = tool_name.strip()
        if not normalized_name:
            return
        self._call_history.append(normalized_name)
        self._last_tool_return_value = return_value


def build_default_tool_rules(tool_names: Collection[str]) -> tuple[ToolRule, ...]:
    normalized_tools = _normalize_tool_names(tool_names)
    rules: list[ToolRule] = []
    if "inner_thought" in normalized_tools:
        rules.append(InitToolRule(tool_name="inner_thought"))
    if "send_message" in normalized_tools:
        rules.append(TerminalToolRule(tool_name="send_message"))
    return tuple(rules)


def _warn_rule_issue(message: str, *args: object) -> None:
    """Emit rule warnings even if a prior test raised this logger's level."""
    if logger.isEnabledFor(logging.WARNING):
        logger.warning(message, *args)
        return
    logging.getLogger().warning(message, *args)


def _normalize_tool_names(tool_names: Collection[str]) -> set[str]:
    normalized: set[str] = set()
    for tool_name in tool_names:
        name = str(tool_name).strip()
        if name:
            normalized.add(name)
    return normalized


def _matches_conditional_key(output: str, key: str) -> bool:
    """Check if an output string matches a conditional mapping key."""
    output_lower = output.strip().lower()
    key_lower = key.strip().lower()
    if key_lower in ("true", "false"):
        return output_lower == key_lower
    return output_lower == key_lower


def _detect_cycles(
    prerequisite_rules: Sequence[PrerequisiteToolRule],
    conditional_rules: dict[str, ConditionalToolRule],
) -> None:
    """Detect circular dependencies across prerequisite and conditional rules.

    Builds a directed graph of all tool dependencies and checks for cycles
    using DFS.  Raises ValueError if a cycle is found.
    """
    # Build adjacency list: edge from A → B means "A must run before B"
    edges: dict[str, set[str]] = {}
    for rule in prerequisite_rules:
        edges.setdefault(rule.prerequisite_tool, set()
                         ).add(rule.dependent_tool)
    for rule_tool, rule in conditional_rules.items():
        for child in rule.child_output_mapping.values():
            edges.setdefault(rule_tool, set()).add(child)
        if rule.default_child:
            edges.setdefault(rule_tool, set()).add(rule.default_child)

    if not edges:
        return

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}

    def dfs(node: str) -> list[str] | None:
        color[node] = GRAY
        for neighbour in edges.get(node, ()):
            c = color.get(neighbour, WHITE)
            if c == GRAY:
                return [node, neighbour]
            if c == WHITE:
                path = dfs(neighbour)
                if path is not None:
                    return [node] + path
        color[node] = BLACK
        return None

    for node in list(edges):
        if color.get(node, WHITE) == WHITE:
            cycle_path = dfs(node)
            if cycle_path is not None:
                raise ValueError(
                    f"Circular tool rule dependency detected: {' → '.join(cycle_path)}"
                )
