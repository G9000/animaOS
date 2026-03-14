from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from anima_server.config import settings
from anima_server.services.agent.llm import create_llm
from anima_server.services.agent.messages import message_content, render_scaffold_response
from anima_server.services.agent.runner import GraphRunner
from anima_server.services.agent.state import AgentState
from anima_server.services.agent.tools import get_tools

logger = logging.getLogger(__name__)


def build_graph() -> GraphRunner:
    """Build the configured LangGraph agent."""
    if settings.agent_provider == "scaffold":
        logger.info("Building scaffold agent graph")
        return build_scaffold_graph()

    llm = create_llm()
    tools = get_tools()
    llm_with_tools = llm.bind_tools(tools) if tools else llm

    async def chatbot(state: AgentState) -> dict[str, list[Any]]:
        response = await llm_with_tools.ainvoke(state["messages"])
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("chatbot", chatbot)
    graph.set_entry_point("chatbot")

    if tools:
        graph.add_node("tools", ToolNode(tools=tools))

        def should_continue(state: AgentState) -> str:
            last = state["messages"][-1]
            if hasattr(last, "tool_calls") and last.tool_calls:
                return "tools"
            return END

        graph.add_conditional_edges(
            "chatbot",
            should_continue,
            {"tools": "tools", END: END},
        )
        graph.add_edge("tools", "chatbot")
    else:
        graph.add_edge("chatbot", END)

    logger.info(
        "Built agent graph with provider=%s model=%s",
        settings.agent_provider,
        settings.agent_model,
    )
    return GraphRunner(graph.compile(), is_scaffold=False)


def build_scaffold_graph() -> GraphRunner:
    """Scaffold graph that returns a canned response."""

    async def scaffold_node(state: AgentState) -> dict[str, list[Any]]:
        messages = state["messages"]
        human_turns = [message for message in messages if getattr(message, "type", "") == "human"]
        user_message = message_content(human_turns[-1]) if human_turns else ""
        response = render_scaffold_response(
            user_id=state["user_id"],
            user_message=user_message,
            turn_number=len(human_turns),
        )
        return {"messages": [AIMessage(content=response)]}

    graph = StateGraph(AgentState)
    graph.add_node("scaffold", scaffold_node)
    graph.set_entry_point("scaffold")
    graph.add_edge("scaffold", END)
    return GraphRunner(graph.compile(), is_scaffold=True)
