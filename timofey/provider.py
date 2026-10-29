import asyncio
import json
import os
import random
import time
from decimal import Decimal, InvalidOperation

import httpx

from .config import valid_model_id


class ModelError(Exception):
    """Public error codes contain no provider response or credentials."""


class MiniMax:
    BASE = "https://api.xkiro.com/v1"

    def __init__(self, config, store, client=None):
        self.config, self.store = config, store
        self.client = client or httpx.AsyncClient(timeout=90, follow_redirects=False)
        self.lock = asyncio.Lock()
        self.catalog_at = 0
        self.failures = 0
        self.blocked_until = 0

    async def close(self):
        await self.client.aclose()

    async def change_key(self, key):
        if not 12 <= len(key) <= 512 or any(c.isspace() for c in key):
            raise ModelError("INVALID_KEY_FORMAT")
        async with self.lock:
            self.config.api_key = key
            os.environ["XKIRO_API_KEY"] = key
            self.failures = 0
            self.blocked_until = 0

    async def change_model(self, model):
        if not valid_model_id(model):
            raise ModelError("INVALID_MODEL_ID")
        async with self.lock:
            try:
                response = await self.client.get(self.BASE + "/models")
                response.raise_for_status()
                item = next((m for m in response.json()["data"] if m.get("id") == model), None)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise ModelError("CATALOG_UNAVAILABLE") from None
            if not item:
                raise ModelError("MODEL_NOT_IN_CATALOG")
            self.config.model = model
            self.catalog_at = 0
            os.environ["AI_MODEL"] = model
            self.store.set("model", model)
            self.failures = 0
            self.blocked_until = 0
            return item.get("access_tier", "unknown")

    async def free_route(self):
        if time.monotonic() - self.catalog_at < 300:
            return
        try:
            r = await self.client.get(self.BASE + "/models")
            r.raise_for_status()
            item = next((m for m in r.json()["data"] if m["id"] == self.config.model), None)
            if not item:
                raise ModelError("NO_VERIFIED_FREE_MODEL_ROUTE")
            if item.get("access_tier") != "free":
                raise ModelError("PAID_ROUTE_BLOCKED")
            prices = item.get("pricing", {})
            if not all(k in prices for k in ("input", "output")):
                raise ModelError("UNVERIFIED_PRICING")
            for k in ("input", "output", "cache_read", "cache_write"):
                if k in prices and Decimal(str(prices[k])) != 0:
                    raise ModelError("PAID_ROUTE_BLOCKED")
            self.catalog_at = time.monotonic()
        except ModelError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidOperation):
            raise ModelError("CATALOG_UNAVAILABLE") from None

    async def generate(self, messages, purpose, *, json_mode=False, max_tokens=None):
        if not self.config.api_key:
            raise ModelError("XKIRO_API_KEY_MISSING")
        async with self.lock:
            if time.time() < self.blocked_until:
                raise ModelError("CIRCUIT_OPEN")
            await self.free_route()

            output = max_tokens if max_tokens is not None else self.config.max_output_tokens
            if not isinstance(output, int) or not 1 <= output <= 8192:
                raise ModelError("INVALID_OUTPUT_LIMIT")

            # The xKiro 'length' finish reason means max_tokens or context was
            # exhausted. Retrying the exact same body cannot fix either.
            # Reasoning tokens also consume the output allowance.
            for generation_attempt in range(2):
                body = {
                    "model": self.config.model,
                    "messages": messages,
                    "max_tokens": output,
                    "temperature": 0.9,
                    "reasoning_effort": "none",
                }
                if json_mode:
                    body["response_format"] = {"type": "json_object"}

                # Conservative upper bound for Cyrillic: one UTF-8 byte/token.
                reservation = len(json.dumps(body, ensure_ascii=False).encode()) + output
                try:
                    call_id = self.store.reserve(
                        purpose,
                        reservation,
                        self.config.daily_token_limit,
                        self.config.requests_per_minute,
                    )
                except ValueError:
                    raise ModelError("LOCAL_BUDGET_OR_RATE_LIMIT") from None

                settled = False
                headers = {"Authorization": "Bearer " + self.config.api_key}
                try:
                    response = None
                    for attempt in range(3):
                        response = await self.client.post(
                            self.BASE + "/chat/completions", json=body, headers=headers
                        )
                        if response.status_code not in (429, 500, 502, 503):
                            break
                        if attempt < 2:
                            try:
                                delay = float(response.headers.get("Retry-After", 2**attempt))
                            except ValueError:
                                delay = 2**attempt
                            if delay > 30:
                                raise ModelError("RATE_LIMIT_RETRY_LATER")
                            await asyncio.sleep(max(0, delay) + random.random())
                    if response.status_code != 200:
                        raise ModelError("HTTP_" + str(response.status_code))

                    data = response.json()
                    usage = data.get("usage")
                    if usage and all(
                        isinstance(usage.get(k), int) and usage[k] >= 0
                        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                    ):
                        self.store.settle(call_id, usage)
                    else:
                        self.store.settle(call_id)
                    settled = True

                    if data.get("model") != self.config.model:
                        raise ModelError("UNEXPECTED_MODEL")
                    choice = data["choices"][0]
                    reason = choice.get("finish_reason")

                    if reason == "length":
                        if generation_attempt == 0 and output < 8192:
                            output = min(8192, max(output * 2, output + 512))
                            continue
                        raise ModelError("INCOMPLETE_GENERATION")
                    if reason == "content_filter":
                        raise ModelError("CONTENT_FILTERED")
                    if reason != "stop":
                        raise ModelError("INCOMPLETE_GENERATION")

                    text = choice["message"]["content"]
                    if not isinstance(text, str) or not text.strip():
                        raise ModelError("EMPTY_GENERATION")
                    if "<think>" in text or "</think>" in text:
                        raise ModelError("REASONING_IN_CONTENT")
                    self.failures = 0
                    return text.strip()
                except asyncio.CancelledError:
                    if not settled:
                        self.store.settle(call_id)
                    raise
                except Exception as exc:
                    if not settled:
                        self.store.settle(call_id)
                    self.failures += 1
                    if self.failures >= 3:
                        self.blocked_until = time.time() + 300
                    code = str(exc) if isinstance(exc, ModelError) else "PROVIDER_UNAVAILABLE"
                    self.store.error("xkiro", code)
                    raise ModelError(code) from None
            raise ModelError("INCOMPLETE_GENERATION")
