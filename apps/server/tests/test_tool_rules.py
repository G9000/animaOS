"""Unit tests for Phase 2 tool rule additions — PrerequisiteToolRule, ConditionalToolRule."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from anima_server.services.agent.rules import (
    ChildToolRule,
    ConditionalToolRule,
    InitToolRule,
    PrerequisiteToolRule,
    ToolRulesSolver,
)


ALL_TOOLS = ("tool_a", "tool_b", "tool_c", "send_message")


# --- PrerequisiteToolRule ---


class TestPrerequisiteToolRule:
    def test_dependent_blocked_until_prerequisite_called(self) -> None:
        solver = ToolRulesSolver([
            PrerequisiteToolRule(prerequisite_tool="tool_a",
                                 dependent_tool="tool_b"),
        ])
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert "tool_a" in allowed
        assert "tool_b" not in allowed

    def test_dependent_available_after_prerequisite(self) -> None:
        solver = ToolRulesSolver([
            PrerequisiteToolRule(prerequisite_tool="tool_a",
                                 dependent_tool="tool_b"),
        ])
        solver.update_state("tool_a")
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert "tool_b" in allowed

    def test_chained_prerequisites(self) -> None:
        solver = ToolRulesSolver([
            PrerequisiteToolRule(prerequisite_tool="tool_a",
                                 dependent_tool="tool_b"),
            PrerequisiteToolRule(prerequisite_tool="tool_b",
                                 dependent_tool="tool_c"),
        ])
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert "tool_b" not in allowed
        assert "tool_c" not in allowed

        solver.update_state("tool_a")
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert "tool_b" in allowed
        assert "tool_c" not in allowed

        solver.update_state("tool_b")
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert "tool_c" in allowed

    def test_prerequisite_combined_with_init_rules(self) -> None:
        solver = ToolRulesSolver([
            InitToolRule(tool_name="tool_a"),
            PrerequisiteToolRule(prerequisite_tool="tool_a",
                                 dependent_tool="tool_b"),
        ])
        # Before any call, init rules take priority
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert allowed == {"tool_a"}

        solver.update_state("tool_a")
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert "tool_b" in allowed


# --- ConditionalToolRule ---


class TestConditionalToolRule:
    def test_output_routes_to_mapped_child(self) -> None:
        solver = ToolRulesSolver([
            ConditionalToolRule(
                tool_name="tool_a",
                child_output_mapping={"yes": "tool_b", "no": "tool_c"},
            ),
        ])
        solver.update_state("tool_a", return_value="yes")
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert allowed == {"tool_b"}

    def test_default_child_when_no_match(self) -> None:
        solver = ToolRulesSolver([
            ConditionalToolRule(
                tool_name="tool_a",
                child_output_mapping={"yes": "tool_b"},
                default_child="tool_c",
            ),
        ])
        solver.update_state("tool_a", return_value="maybe")
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert allowed == {"tool_c"}

    def test_require_output_mapping_strict_mode(self) -> None:
        solver = ToolRulesSolver([
            ConditionalToolRule(
                tool_name="tool_a",
                child_output_mapping={"yes": "tool_b"},
                require_output_mapping=True,
            ),
        ])
        solver.update_state("tool_a", return_value="maybe")
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert allowed == set()

    def test_no_output_value_with_default(self) -> None:
        solver = ToolRulesSolver([
            ConditionalToolRule(
                tool_name="tool_a",
                child_output_mapping={"yes": "tool_b"},
                default_child="tool_c",
            ),
        ])
        solver.update_state("tool_a", return_value=None)
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert allowed == {"tool_c"}

    def test_json_message_field_extracted(self) -> None:
        """Output is JSON with a 'message' field — value should be extracted."""
        solver = ToolRulesSolver([
            ConditionalToolRule(
                tool_name="tool_a",
                child_output_mapping={"approve": "tool_b", "reject": "tool_c"},
            ),
        ])
        solver.update_state("tool_a", return_value='{"message": "approve"}')
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert allowed == {"tool_b"}

    def test_case_insensitive_matching(self) -> None:
        solver = ToolRulesSolver([
            ConditionalToolRule(
                tool_name="tool_a",
                child_output_mapping={"yes": "tool_b"},
                default_child="tool_c",
            ),
        ])
        solver.update_state("tool_a", return_value="YES")
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert allowed == {"tool_b"}

    def test_conditional_takes_priority_over_child_rule(self) -> None:
        solver = ToolRulesSolver([
            ChildToolRule(tool_name="tool_a", children=("tool_c",)),
            ConditionalToolRule(
                tool_name="tool_a",
                child_output_mapping={"yes": "tool_b"},
            ),
        ])
        solver.update_state("tool_a", return_value="yes")
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        assert allowed == {"tool_b"}

    def test_empty_mapping_raises(self) -> None:
        with pytest.raises(ValueError, match="empty child_output_mapping"):
            ToolRulesSolver([
                ConditionalToolRule(
                    tool_name="tool_a",
                    child_output_mapping={},
                ),
            ])

    def test_conditional_with_prerequisite(self) -> None:
        solver = ToolRulesSolver([
            ConditionalToolRule(
                tool_name="tool_a",
                child_output_mapping={"yes": "tool_b"},
                default_child="tool_c",
            ),
            PrerequisiteToolRule(prerequisite_tool="tool_c",
                                 dependent_tool="tool_b"),
        ])
        solver.update_state("tool_a", return_value="yes")
        allowed = solver.get_allowed_tools(ALL_TOOLS)
        # tool_b has a prerequisite on tool_c which hasn't been called
        assert "tool_b" not in allowed


# --- Cycle detection ---


class TestCycleDetection:
    def test_prerequisite_cycle_raises(self) -> None:
        with pytest.raises(ValueError, match="Circular tool rule dependency"):
            ToolRulesSolver([
                PrerequisiteToolRule(
                    prerequisite_tool="tool_a", dependent_tool="tool_b"),
                PrerequisiteToolRule(
                    prerequisite_tool="tool_b", dependent_tool="tool_a"),
            ])

    def test_conditional_cycle_raises(self) -> None:
        with pytest.raises(ValueError, match="Circular tool rule dependency"):
            ToolRulesSolver([
                ConditionalToolRule(
                    tool_name="tool_a",
                    child_output_mapping={"yes": "tool_b"},
                ),
                ConditionalToolRule(
                    tool_name="tool_b",
                    child_output_mapping={"yes": "tool_a"},
                ),
            ])

    def test_no_cycle_no_raise(self) -> None:
        solver = ToolRulesSolver([
            PrerequisiteToolRule(prerequisite_tool="tool_a",
                                 dependent_tool="tool_b"),
            ConditionalToolRule(
                tool_name="tool_b",
                child_output_mapping={"yes": "tool_c"},
            ),
        ])
        assert solver is not None


class TestWarnUnknownTools:
    def test_logs_warning_for_unknown_init_tool(self) -> None:
        solver = ToolRulesSolver([InitToolRule(tool_name="ghost_tool")])
        with patch("anima_server.services.agent.rules.logger.warning") as warning_mock:
            solver.warn_unknown_tools(["real_tool"])
        warning_mock.assert_called_once_with(
            "InitToolRule references unknown tool %r",
            "ghost_tool",
        )

    def test_no_warning_when_all_tools_known(self) -> None:
        solver = ToolRulesSolver([InitToolRule(tool_name="tool_a")])
        with patch("anima_server.services.agent.rules.logger.warning") as warning_mock:
            solver.warn_unknown_tools(["tool_a", "tool_b"])
        warning_mock.assert_not_called()

    def test_logs_warning_for_unknown_prerequisite(self) -> None:
        solver = ToolRulesSolver([
            PrerequisiteToolRule(
                prerequisite_tool="unknown_pre", dependent_tool="tool_a"),
        ])
        with patch("anima_server.services.agent.rules.logger.warning") as warning_mock:
            solver.warn_unknown_tools(["tool_a"])
        warning_mock.assert_called_once_with(
            "PrerequisiteToolRule references unknown prerequisite tool %r",
            "unknown_pre",
        )
