"""The assistant as a LangGraph graph.

    START → retrieve → agent ⇄ tools → END

- retrieve: when the message looks like a question about the past, runs the
  hybrid search locally and puts the best passages in the state. Most memory
  questions are then answered in ONE model request instead of two (search tool
  call + answer) — on a 50-requests/day free tier, that is the difference.
- agent: the chat model with tools bound (OpenRouter free models → Ollama).
- tools: LangGraph's ToolNode runs whatever the model asked for, then loops back.

Conversation history is stored in OwnLife's own tables (chat_messages), not in a
LangGraph checkpointer, so it stays readable and exportable like the rest.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any, TypedDict

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from app.ai.llm import describe_error

_MEMORY_HINTS = re.compile(
    r"\?|\b(remember|recall|journal|memory|wrote|written|when did|what did|why did|did i|have i|"
    r"day \d+|yesterday|last (week|month|time)|past|history|before|thought|felt|said|talked|met|"
    r"watched|read|souviens|journal|quand|pourquoi|hier|avant)\b",
    re.IGNORECASE,
)


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    memory: str


def wants_memory(text: str) -> bool:
    return bool(_MEMORY_HINTS.search(text or ""))


def build_graph(
    llm_with_tools: Runnable,
    tools: list,
    system_prompt: Callable[[str], str],
    retrieve_memory: Callable[[str], str],
):
    async def retrieve(state: ChatState) -> dict:
        last = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
        if last is None or not wants_memory(str(last.content)):
            return {"memory": ""}
        return {"memory": await asyncio.to_thread(retrieve_memory, str(last.content))}

    async def agent(state: ChatState) -> dict:
        messages = [SystemMessage(system_prompt(state.get("memory", ""))), *state["messages"]]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    graph = StateGraph(ChatState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


def text_of(content: Any) -> str:
    """Message content can be a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
            if not isinstance(b, dict) or b.get("type") in (None, "text")
        )
    return str(content or "")


def rows_to_messages(rows: list[dict]) -> list[BaseMessage]:
    """Rebuilds LangChain messages from stored rows, starting at a user message so
    that no ToolMessage is ever separated from the AIMessage that requested it."""
    while rows and rows[0]["role"] != "user":
        rows = rows[1:]
    out: list[BaseMessage] = []
    for r in rows:
        if r["role"] == "user":
            out.append(HumanMessage(r["content"]))
        elif r["role"] == "assistant":
            out.append(AIMessage(r["content"], tool_calls=r.get("tool_calls") or []))
        elif r["role"] == "tool":
            out.append(ToolMessage(r["content"], tool_call_id=r.get("tool_call_id") or "", name=r.get("name")))
    return out


def message_to_row(m: BaseMessage) -> dict | None:
    if isinstance(m, AIMessage):
        calls = [
            {"name": c["name"], "args": c.get("args", {}), "id": c.get("id"), "type": "tool_call"}
            for c in (m.tool_calls or [])
        ]
        return {"role": "assistant", "content": text_of(m.content), "tool_calls": calls or None}
    if isinstance(m, ToolMessage):
        return {
            "role": "tool",
            "content": text_of(m.content),
            "tool_call_id": m.tool_call_id,
            "name": m.name,
        }
    return None


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_turn(
    graph,
    history: list[BaseMessage],
    user_text: str,
    max_tool_rounds: int,
    new_messages: list[BaseMessage],
) -> AsyncIterator[str]:
    """Runs one turn and yields Server-Sent Events: token, tool_call, tool_result,
    memory, error. Messages produced are appended to `new_messages` for storage."""
    inputs = {"messages": [*history, HumanMessage(user_text)], "memory": ""}
    config = {"recursion_limit": 2 * max_tool_rounds + 4}
    try:
        async for mode, chunk in graph.astream(inputs, config=config, stream_mode=["messages", "updates"]):
            if mode == "messages":
                msg, meta = chunk
                # Streaming models send AIMessageChunks; a model that cannot stream
                # sends its whole AIMessage once. LangGraph never sends both.
                if meta.get("langgraph_node") == "agent" and isinstance(msg, (AIMessageChunk, AIMessage)):
                    piece = text_of(msg.content)
                    if piece:
                        yield sse("token", {"text": piece})
            elif mode == "updates":
                for node, update in (chunk or {}).items():
                    if not update:
                        continue
                    if node == "retrieve":
                        memory = update.get("memory") or ""
                        if memory:
                            yield sse("memory", {"passages": memory.count("\n---\n") + 1})
                    for m in update.get("messages", []) if isinstance(update, dict) else []:
                        new_messages.append(m)
                        if isinstance(m, AIMessage) and m.tool_calls:
                            for c in m.tool_calls:
                                yield sse("tool_call", {"name": c["name"], "args": c.get("args", {})})
                        elif isinstance(m, ToolMessage):
                            yield sse("tool_result", {"name": m.name, "content": text_of(m.content)[:600]})
    except GraphRecursionError:
        yield sse("error", {"message": f"Stopped after {max_tool_rounds} tool rounds without a final answer."})
    except Exception as e:  # network, rate limit, provider error: tell the person what to do
        yield sse("error", {"message": describe_error(e)})
