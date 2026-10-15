"""The eligibility-criteria "pharmacist" agent, as a LangGraph graph.

    START -> prepare -> pharmacist <-> tools
                           |  (submit_criteria accepted)
                           v
                        finalize -> END

The model only reads trial text and tool results (never member data). Everything after the model
submits (code checks, NDC expansion, SQL) is deterministic code in services/criteria_filter.py.
"""

from datetime import date
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from webapp.backend.agents.prompts import SYSTEM_PROMPT, user_message
from webapp.backend.agents.tools import SUBMIT, build_tools
from webapp.backend.services.criteria_filter import build_criteria_filter

# Each model turn plus its tool turn is two graph steps; this allows ~45 rounds of lookups
RECURSION_LIMIT = 100


class CriteriaState(TypedDict, total=False):
    context: dict
    messages: Annotated[list, add_messages]
    reminders: int
    submitted: dict | None
    error: str | None
    result: Any


def submitted_call(messages: list) -> dict | None:
    """The submit_criteria call from the last model turn, if its tool run succeeded."""
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    if not last_ai:
        return None
    call = next((c for c in last_ai.tool_calls if c["name"] == SUBMIT), None)
    if not call:
        return None
    result = next((m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id == call["id"]), None)
    return call["args"] if result is not None and getattr(result, "status", "success") != "error" else None


def build_criteria_graph(llm, umls, rxnorm, *, model_name: str, reference_date: date | None = None):
    tools = build_tools(umls, rxnorm)
    pharmacist_llm = llm.bind_tools(tools, tool_choice="auto")  # forced tool_choice is a 400 on Opus 5.5

    async def prepare(state: CriteriaState) -> dict:
        system = SystemMessage(content=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}])
        return {"messages": [system, HumanMessage(content=user_message(state["context"]))], "reminders": 0}

    async def pharmacist(state: CriteriaState) -> dict:
        response = await pharmacist_llm.ainvoke(state["messages"])
        if response.response_metadata.get("stop_reason") == "refusal":
            return {"messages": [response], "error": "The model declined to review these criteria."}
        if response.response_metadata.get("stop_reason") == "max_tokens" and not response.tool_calls:
            return {"messages": [response], "error": "The model's answer was cut off before it finished."}
        return {"messages": [response]}

    async def remind(state: CriteriaState) -> dict:
        return {"messages": [HumanMessage(content="Please finish by calling submit_criteria with every rule.")],
                "reminders": state.get("reminders", 0) + 1}

    async def finalize(state: CriteriaState) -> dict:
        result = await build_criteria_filter(
            submitted=state["submitted"], context=state["context"], messages=state["messages"],
            umls=umls, rxnorm=rxnorm, model=model_name, reference_date=reference_date,
        )
        return {"result": result}

    async def collect(state: CriteriaState) -> dict:
        return {"submitted": submitted_call(state["messages"])}

    def after_pharmacist(state: CriteriaState) -> str:
        if state.get("error"):
            return END
        last = state["messages"][-1]
        if last.tool_calls:
            return "tools"
        if state.get("reminders", 0) < 1:
            return "remind"
        return "gave_up"

    async def gave_up(state: CriteriaState) -> dict:
        return {"error": "The model answered in text twice instead of submitting rules."}

    def after_tools(state: CriteriaState) -> str:
        return "collect" if submitted_call(state["messages"]) is not None else "pharmacist"

    graph = StateGraph(CriteriaState)
    graph.add_node("prepare", prepare)
    graph.add_node("pharmacist", pharmacist)
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_node("remind", remind)
    graph.add_node("gave_up", gave_up)
    graph.add_node("collect", collect)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "pharmacist")
    graph.add_conditional_edges("pharmacist", after_pharmacist, ["tools", "remind", "gave_up", END])
    graph.add_conditional_edges("tools", after_tools, ["collect", "pharmacist"])
    graph.add_edge("remind", "pharmacist")
    graph.add_edge("gave_up", END)
    graph.add_edge("collect", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
