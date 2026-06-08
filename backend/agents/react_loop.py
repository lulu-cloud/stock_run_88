"""Shared ReAct loop infrastructure for trading and recommendation agents."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


@dataclass
class ReActResult:
    output: str
    trace: list[dict]
    state: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0


def sanitize_ai_message(response) -> AIMessage:
    """Strip provider-only payload before replaying an assistant message."""
    return AIMessage(
        content=getattr(response, "content", "") or "",
        tool_calls=getattr(response, "tool_calls", None) or [],
        invalid_tool_calls=getattr(response, "invalid_tool_calls", None) or [],
        id=getattr(response, "id", None),
    )


def stringify_tool_result(result, max_chars: int = 5000) -> str:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > max_chars:
        return text[:max_chars] + f"\n...[工具结果已截断，共{len(text)}字符]"
    return text


def append_text_log(log_path: str, text: str):
    if not log_path:
        return
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


def start_text_log(log_path: str, text: str):
    if not log_path:
        return
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


def append_trace_jsonl(log_path: str, record: dict):
    if not log_path:
        return
    folder = os.path.dirname(log_path)
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    trace_path = os.path.join(folder, "thinking_trace.jsonl")
    with open(trace_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def invoke_llm_with_retry(llm_obj, messages, log_path: str = "", max_attempts: int = 3):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            started = time.perf_counter()
            response = llm_obj.invoke(messages)
            return response, (time.perf_counter() - started) * 1000, attempt, ""
        except Exception as exc:
            last_error = exc
            if attempt >= max_attempts:
                break
            delay = 2 ** (attempt - 1)
            append_text_log(log_path, f"[LLM Retry] attempt={attempt} error={exc}; sleep={delay}s")
            time.sleep(delay)
    return None, 0.0, max_attempts, str(last_error or "LLM invoke failed")


class ReActLoop:
    def __init__(
        self,
        llm,
        tools: list,
        *,
        max_result_chars: int = 5000,
        metadata: dict | None = None,
    ):
        self.llm = llm
        self.tools = tools or []
        self.max_result_chars = max_result_chars
        self.metadata = metadata or {}

    def run(
        self,
        system_prompt: str,
        user_input: str,
        *,
        max_turns: int,
        log_path: str = "",
        reset_log: bool = True,
        stage_config: list[dict] | None = None,
        final_instruction: str = "",
        state: dict | None = None,
        event_callback=None,
    ) -> ReActResult:
        started = time.perf_counter()
        state = state if state is not None else {}
        messages = [SystemMessage(content=system_prompt)]
        if user_input:
            messages.append(HumanMessage(content=user_input))

        trace: list[dict] = []
        output_parts: list[str] = []
        write_start = start_text_log if reset_log else append_text_log
        write_start(
            log_path,
            "\n".join([
                "=" * 88,
                f"ReAct session start {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "-" * 88,
                "[System Prompt]",
                system_prompt,
                "-" * 88,
                "[User Input]",
                user_input or "",
            ]),
        )

        reached_tool_limit = False
        empty_rounds = 0
        for turn in range(max_turns):
            current_tools = self._tools_for_turn(turn, stage_config)
            tool_map = {getattr(t, "name", ""): t for t in current_tools}
            tool_descriptions = {
                name: self._tool_description(tool)
                for name, tool in tool_map.items()
            }
            self._emit_event(event_callback, {
                "type": "llm_turn",
                "turn": turn + 1,
                "stage_tools": list(tool_map.keys()),
                "stage_tool_descriptions": tool_descriptions,
            })
            llm_with_tools = self.llm.bind_tools(current_tools) if current_tools else self.llm
            response, llm_latency_ms, attempts, llm_error = invoke_llm_with_retry(llm_with_tools, messages, log_path)
            if response is None:
                self._append_trace(trace, log_path, {
                    "type": "llm",
                    "turn": turn + 1,
                    "latency_ms": round(llm_latency_ms, 2),
                    "attempts": attempts,
                    "error": llm_error,
                })
                append_text_log(log_path, f"[LLM Error Turn {turn + 1}] {llm_error}")
                break

            messages.append(sanitize_ai_message(response))
            usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("token_usage", {}) or {}
            self._append_trace(trace, log_path, {
                "type": "llm",
                "turn": turn + 1,
                "stage_tools": list(tool_map.keys()),
                "latency_ms": round(llm_latency_ms, 2),
                "prompt_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
                "completion_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "attempts": attempts,
            })

            if response.content:
                output_parts.append(str(response.content))
            invalid_tool_calls = getattr(response, "invalid_tool_calls", None) or []
            if invalid_tool_calls:
                self._append_trace(trace, log_path, {
                    "type": "invalid_tool_calls",
                    "turn": turn + 1,
                    "count": len(invalid_tool_calls),
                    "preview": str(invalid_tool_calls)[:1000],
                })
            append_text_log(
                log_path,
                "\n".join([
                    "-" * 88,
                    f"[LLM Output Turn {turn + 1}]",
                    str(response.content or "").strip() or "(no visible content)",
                ]),
            )

            tool_calls = getattr(response, "tool_calls", None) or []
            has_content = bool(str(response.content or "").strip())
            self._emit_event(event_callback, {
                "type": "llm_decision",
                "turn": turn + 1,
                "tool_count": len(tool_calls),
                "tools": [tc.get("name", "") for tc in tool_calls],
                "tool_descriptions": tool_descriptions,
                "has_visible_content": has_content,
            })
            if not tool_calls and not has_content:
                empty_rounds += 1
                self._append_trace(trace, log_path, {
                    "type": "quality_short_circuit",
                    "turn": turn + 1,
                    "empty_rounds": empty_rounds,
                    "reason": "llm_empty_content_no_tool_calls",
                })
                if empty_rounds >= 3:
                    append_text_log(log_path, "[Quality Short Circuit] 连续3轮无内容且无工具调用，提前终止")
                    break
            else:
                empty_rounds = 0
            if not tool_calls:
                self._emit_event(event_callback, {
                    "type": "finalizing",
                    "turn": turn + 1,
                    "reason": "no_more_tool_calls",
                })
                break
            if turn == max_turns - 1:
                reached_tool_limit = True

            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {})
                tool_id = tc.get("id", "")
                tool_fn = tool_map.get(tool_name)
                tool_desc = self._tool_description(tool_fn) if tool_fn else ""
                if tool_fn:
                    try:
                        tool_started = time.perf_counter()
                        self._emit_event(event_callback, {
                            "type": "tool_start",
                            "turn": turn + 1,
                            "tool": tool_name,
                            "description": tool_desc,
                            "args": tool_args,
                        })
                        result = tool_fn.invoke(tool_args)
                        tool_latency_ms = (time.perf_counter() - tool_started) * 1000
                        tool_error = ""
                    except Exception as exc:
                        result = f"工具执行错误: {exc}"
                        tool_latency_ms = 0.0
                        tool_error = str(exc)
                else:
                    result = f"当前阶段不可用或未知工具: {tool_name}"
                    tool_latency_ms = 0.0
                    tool_error = result
                result_text = stringify_tool_result(result, self.max_result_chars)
                append_text_log(
                    log_path,
                    "\n".join([
                        f"[Tool Call Turn {turn + 1}] {tool_name}",
                        f"args: {json.dumps(tool_args, ensure_ascii=False, default=str)}",
                        "[Tool Result]",
                        result_text,
                    ]),
                )
                self._append_trace(trace, log_path, {
                    "type": "tool",
                    "turn": turn + 1,
                    "tool": tool_name,
                    "args": tool_args,
                    "latency_ms": round(tool_latency_ms, 2),
                    "error": tool_error,
                    "result_preview": result_text[:1000],
                })
                self._emit_event(event_callback, {
                    "type": "tool",
                    "turn": turn + 1,
                    "tool": tool_name,
                    "description": tool_desc,
                    "args": tool_args,
                    "result_preview": self._result_preview(result_text),
                    "error": tool_error,
                })
                messages.append(ToolMessage(content=result_text, tool_call_id=tool_id))

        if reached_tool_limit and final_instruction:
            self._emit_event(event_callback, {
                "type": "finalizing",
                "turn": max_turns + 1,
                "reason": "tool_limit",
            })
            messages.append(HumanMessage(content=final_instruction))
            try:
                response, llm_latency_ms, attempts, llm_error = invoke_llm_with_retry(self.llm, messages, log_path)
                if response is None:
                    raise RuntimeError(llm_error)
                usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("token_usage", {}) or {}
                self._append_trace(trace, log_path, {
                    "type": "llm",
                    "turn": max_turns + 1,
                    "latency_ms": round(llm_latency_ms, 2),
                    "prompt_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "attempts": attempts,
                })
                if response.content:
                    output_parts.append(str(response.content))
                append_text_log(
                    log_path,
                    "\n".join([
                        "-" * 88,
                        "[LLM Finalization After Tool Limit]",
                        str(response.content or "").strip() or "(no visible content)",
                    ]),
                )
            except Exception as exc:
                append_text_log(log_path, f"[LLM Finalization Error] {exc}")

        output = "\n".join(output_parts)
        append_text_log(log_path, "\n".join(["-" * 88, "[Final Visible Output]", output]))
        return ReActResult(
            output=output,
            trace=trace,
            state=state,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _tools_for_turn(self, turn: int, stage_config: list[dict] | None) -> list:
        if not stage_config:
            return self.tools
        all_by_name = {getattr(t, "name", ""): t for t in self.tools}
        for stage in stage_config:
            start = int(stage.get("start", 0))
            end = int(stage.get("end", start + 1))
            if start <= turn < end:
                names = stage.get("tools") or []
                selected = [all_by_name[name] for name in names if name in all_by_name]
                return selected
        return self.tools

    def _append_trace(self, trace: list[dict], log_path: str, item: dict):
        record = {**self.metadata, **item, "created_at": datetime.now().isoformat(timespec="seconds")}
        trace.append(record)
        append_trace_jsonl(log_path, record)

    def _emit_event(self, callback, item: dict):
        if not callback:
            return
        try:
            callback({**self.metadata, **item})
        except Exception:
            pass

    def _tool_description(self, tool) -> str:
        if not tool:
            return ""
        desc = (getattr(tool, "description", "") or getattr(tool, "__doc__", "") or "").strip()
        if not desc:
            return ""
        return re.sub(r"\s+", " ", desc.splitlines()[0]).strip()[:160]

    def _result_preview(self, result: str) -> str:
        text = str(result or "").strip()
        if not text:
            return ""
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                summary = data.get("summary") or data.get("message") or data.get("explanation") or data.get("error")
                if summary:
                    text = str(summary)
                else:
                    keys = list(data.keys())[:5]
                    text = "返回字段: " + ", ".join(str(k) for k in keys)
            elif isinstance(data, list):
                text = f"返回列表 {len(data)} 条"
        except Exception:
            pass
        return re.sub(r"\s+", " ", text).strip()[:220]
