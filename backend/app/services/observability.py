"""Model-call timing, provider cache hits and optional Langfuse tracing.

Langfuse is enabled only when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set and
the `langfuse` package is installed; otherwise tracing is a no-op.
"""
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache


@lru_cache(maxsize=1)
def _langfuse_handler():
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return None
    try:
        from langfuse.langchain import CallbackHandler  # langfuse >= 3
    except ImportError:
        try:
            from langfuse.callback import CallbackHandler  # langfuse 2.x
        except ImportError:
            print("⚠️ 已配置 Langfuse 密钥，但未安装 langfuse 包，跳过链路追踪")
            return None
    return CallbackHandler()


def tracing_callbacks() -> list:
    handler = _langfuse_handler()
    return [handler] if handler else []


_usage: ContextVar[dict | None] = ContextVar("model_usage", default=None)


@contextmanager
def usage_scope():
    """Collect model-call totals for the enclosed work (used by the eval harness)."""
    totals = {"calls": 0, "seconds": 0.0, "prompt": 0, "cached": 0, "completion": 0, "events": {}}
    token = _usage.set(totals)
    try:
        yield totals
    finally:
        _usage.reset(token)


def record_event(name: str, value: float = 1):
    """Numeric counters only; never collect keys, locations, prompts or URLs."""
    totals = _usage.get()
    if totals is not None:
        events = totals['events']
        events[name] = events.get(name, 0) + value


def _log_usage(message, started: float) -> None:
    usage = (getattr(message, "response_metadata", None) or {}).get("token_usage") or {}
    meta = getattr(message, "usage_metadata", None) or {}
    prompt = usage.get("prompt_tokens") or meta.get("input_tokens") or 0
    completion = usage.get("completion_tokens") or meta.get("output_tokens") or 0
    # DeepSeek reports prefix-cache hits as prompt_cache_hit_tokens.
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = (meta.get("input_token_details") or {}).get("cache_read")
    cache = f"，缓存命中 {hit}" if hit is not None else ""
    totals = _usage.get()
    if totals is not None:
        totals["calls"] += 1
        totals["seconds"] += time.monotonic() - started
        totals["prompt"] += prompt
        totals["cached"] += hit or 0
        totals["completion"] += completion
    print(f"   ⏱️ 模型调用 {time.monotonic() - started:.1f}s，输入 {prompt}{cache}，输出 {completion}", flush=True)


async def call_model(model, messages, progress=None):
    """Invoke the model; when progress is given, stream and report each itinerary day as it is written."""
    started = time.monotonic()
    if progress is None or not hasattr(model, "astream"):
        response = await model.ainvoke(messages)
        _log_usage(response, started)
        return response
    full = None
    days_seen = 0
    async for chunk in model.astream(messages):
        full = chunk if full is None else full + chunk
        content = full.content if isinstance(full.content, str) else ""
        days = len(re.findall(r'"date"\s*:', content))
        if days > days_seen:
            days_seen = days
            await progress(f"正在写出行程：第 {days} 天")
    from langchain_core.messages import AIMessage
    if full is None:
        return AIMessage(content="")
    response = AIMessage(
        content=full.content, tool_calls=full.tool_calls, id=full.id,
        response_metadata=full.response_metadata, usage_metadata=full.usage_metadata,
    )
    _log_usage(response, started)
    return response
