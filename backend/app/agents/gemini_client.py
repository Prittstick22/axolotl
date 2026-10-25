"""Gemini Interactions transport. No local tools execute inside the SDK."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.agents.prompts import data_block
from app.config import get_settings

# #47: one retry policy, and it is this one. The SDK's own retries are switched off
# (`_without_sdk_retries`): they slept on a 429 without reading the error's
# `retryDelay`, and on a daily quota they outlasted the timeout below, so the real
# error was lost behind a TimeoutError.
# 240, not 120: on 25 Oct one Argon turn on `cli.py`, the largest chess module, took
# longer than 120 s and blocked the unit (`run_a6435773b085`); normal turns take 15-70 s.
# Still not retried: a timed-out call may have been charged, and a retry doubles the wait.
REQUEST_TIMEOUT_SECONDS = 240
# A per-minute quota resets within a minute. A 429 asking for longer than this is a
# daily quota: waiting would stall the run for hours, so it fails at once and says so.
RATE_LIMIT_WAIT_SECONDS = 90
RATE_LIMIT_BACKOFF_SECONDS = (2, 4, 8, 16, 32)  # when a 429 names no delay
RETRY_DELAY_MARGIN_SECONDS = 1                 # retryDelay is a minimum, not a promise
DROPPED_CONNECTION_RETRIES = 1
# The model is busy or briefly down: "503 UNAVAILABLE - high demand". Backed off like
# a 429 that names no delay, within the same budget (#97). Same set as search/retry.py.
SERVER_ERROR_STATUSES = (500, 502, 503, 504)


class ModelUnavailable(RuntimeError):
    """The configured model failed; never substitute a successful outcome."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Any


@dataclass
class Turn:
    id: str
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)


def parse_turn(response: Any) -> Turn:
    data = response if isinstance(response, dict) else response.model_dump()
    if data.get("status") not in ("completed", "requires_action"):
        raise ModelUnavailable("Gemini interaction did not complete: " + str(data.get("status")))
    if not data.get("id"):
        raise ModelUnavailable("Gemini returned no interaction id")
    turn = Turn(id=data["id"])
    # Current API uses steps; outputs supports earlier SDK response shapes.
    for step in data.get("steps", data.get("outputs", [])):
        if step.get("type") == "function_call":
            turn.calls.append(ToolCall(step["id"], step["name"], step.get("arguments")))
        elif step.get("type") == "model_output":
            turn.text += "".join(
                c.get("text", "") for c in step.get("content", []) if c.get("type") == "text"
            )
        elif step.get("type") == "text":
            turn.text += step.get("text", "")
    return turn


def function_result(call: ToolCall, result: Any) -> dict:
    return {
        "type": "function_result",
        "name": call.name,
        "call_id": call.id,
        "is_error": isinstance(result, dict) and "error" in result,
        "result": [{"type": "text", "text": data_block(result)}],
    }


def _status(exc: BaseException) -> int | None:
    """HTTP status of an SDK error. Interactions errors carry `status_code`."""
    for name in ("status_code", "code"):
        try:
            return int(getattr(exc, name))
        except (AttributeError, TypeError, ValueError):
            continue
    return None


def retry_delay_seconds(exc: BaseException) -> float | None:
    """The wait a 429 asks for: `RetryInfo.retryDelay` in the body, else `Retry-After`."""
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else None
    details = error.get("details", []) if isinstance(error, dict) else []
    for detail in details if isinstance(details, list) else []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                pass
    headers = getattr(getattr(exc, "response", None), "headers", None)
    header = headers.get("retry-after") if headers is not None else None
    try:
        return float(header) if header is not None else None
    except ValueError:
        return None


def _is_dropped_connection(exc: BaseException) -> bool:
    """The transport failed before an answer arrived. A timeout is not one of these."""
    names = {cls.__name__ for cls in type(exc).__mro__}
    if "APITimeoutError" in names or isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return False
    return "APIConnectionError" in names or isinstance(exc, httpx.TransportError)


def _human(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 120:
        return f"{seconds}s"
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


async def _wait(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _without_sdk_retries(client):
    """Interactions read `retry_config` per call; None means the SDK never retries.

    `HttpOptions.retry_options` cannot get there: the SDK raises attempts=0 to one retry.
    """
    client.aio.interactions.sdk_configuration.retry_config = None
    return client


class GeminiClient:
    def __init__(self, client=None):
        settings = get_settings()
        self.model = settings.gemini_model
        self.thinking_level = settings.gemini_thinking_level
        if client is None:
            from google import genai

            if not settings.gemini_api_key:
                raise ModelUnavailable("GEMINI_API_KEY is not configured")
            client = _without_sdk_retries(genai.Client(api_key=settings.gemini_api_key))
        self.client = client

    async def close(self):
        await self.client.aio.aclose()

    async def turn(
        self,
        input,
        *,
        instruction: str,
        tools: list,
        previous_id: str | None = None,
        repair: bool = False,
    ) -> Turn:
        body = {
            "model": self.model,
            "input": input,
            "system_instruction": instruction,
            "tools": tools,
            "generation_config": {"thinking_level": "high" if repair else self.thinking_level},
        }
        if previous_id:
            body["previous_interaction_id"] = previous_id
        waited = 0.0
        rate_limited = 0
        reconnects = 0
        while True:
            try:
                response = await asyncio.wait_for(
                    self.client.aio.interactions.create(**body),
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                return parse_turn(response)
            except ModelUnavailable:
                raise
            except Exception as exc:
                status = _status(exc)
                if status == 429 or status in SERVER_ERROR_STATUSES:
                    asked = retry_delay_seconds(exc) if status == 429 else None
                    if asked is not None and asked > RATE_LIMIT_WAIT_SECONDS:
                        raise ModelUnavailable(
                            f"Gemini quota exhausted for {self.model}: the API asks to wait "
                            f"{_human(asked)} before retrying"
                        ) from exc
                    delay = (
                        asked + RETRY_DELAY_MARGIN_SECONDS if asked is not None
                        else RATE_LIMIT_BACKOFF_SECONDS[
                            min(rate_limited, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)
                        ]
                    )
                    if waited + delay > RATE_LIMIT_WAIT_SECONDS:
                        still = "rate-limited" if status == 429 else f"failing ({status})"
                        raise ModelUnavailable(
                            f"Gemini still {still} for {self.model} after waiting "
                            f"{_human(waited)}"
                        ) from exc
                    rate_limited += 1
                    waited += delay
                    await _wait(delay)
                    continue
                if _is_dropped_connection(exc) and reconnects < DROPPED_CONNECTION_RETRIES:
                    reconnects += 1
                    continue
                # Avoid propagating SDK request bodies or credentials into events.
                raise ModelUnavailable(
                    f"Gemini request failed ({type(exc).__name__}, status={status})"
                ) from exc
