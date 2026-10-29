import json

import httpx
import pytest

from timofey.provider import MiniMax, ModelError


def catalog(config, price=0, tier="free"):
    return {
        "data": [
            {"id": config.model, "access_tier": tier, "pricing": {"input": price, "output": price}}
        ]
    }


async def test_provider_format_usage_and_secrets(store, config):
    def handler(request):
        if request.url.path.endswith("models"):
            return httpx.Response(200, json=catalog(config))
        assert request.headers["Authorization"] == "Bearer " + config.api_key
        body = json.loads(request.content)
        assert body["model"].startswith("minimax/")
        assert "test-only" not in json.dumps(body)
        return httpx.Response(
            200,
            json={
                "model": config.model,
                "choices": [{"message": {"content": "Ответ"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = MiniMax(config, store, client)
    assert await model.generate([{"role": "user", "content": "Вопрос"}], "test") == "Ответ"
    assert store.conn.execute("SELECT total FROM calls").fetchone()[0] == 14
    await model.close()


@pytest.mark.parametrize("price,tier", [(1, "free"), (0, "paid"), (None, "free")])
async def test_no_paid_route(store, config, price, tier):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=catalog(config, price, tier))

    model = MiniMax(config, store, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ModelError):
        await model.generate([{"role": "user", "content": "Вопрос"}], "test")
    assert paths == ["/v1/models"]
    await model.close()


async def test_outage_no_leak(store, config):
    def handler(request):
        if request.url.path.endswith("models"):
            return httpx.Response(200, json=catalog(config))
        raise httpx.ConnectError("SECRET " + config.api_key, request=request)

    model = MiniMax(config, store, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ModelError, match="PROVIDER_UNAVAILABLE") as exc:
        await model.generate([], "test")
    assert config.api_key not in str(exc)
    assert config.api_key not in str(store.conn.execute("SELECT * FROM errors").fetchall())
    assert store.conn.execute("SELECT state FROM calls").fetchone()[0] == "unknown"
    await model.close()


async def test_key_and_model_change(store, config, monkeypatch):
    monkeypatch.delenv("XKIRO_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)
    free_model = "minimax/test-free"

    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": free_model, "access_tier": "free", "pricing": {"input": 0, "output": 0}}
                ]
            },
        )

    model = MiniMax(config, store, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await model.change_key("test-replacement-key")
    assert config.api_key == "test-replacement-key"
    assert not store.conn.execute(
        "SELECT * FROM settings WHERE value LIKE '%test-replacement-key%'"
    ).fetchall()
    await model.change_model(free_model)
    assert store.get("model") == free_model
    with pytest.raises(ModelError):
        await model.change_model("minimax/paid-test")
    assert config.model == free_model
    await model.close()


async def test_truncated_answer_is_rejected(store, config):
    def handler(request):
        if request.url.path.endswith("models"):
            return httpx.Response(200, json=catalog(config))
        return httpx.Response(
            200,
            json={
                "model": config.model,
                "choices": [{"message": {"content": "Обрыв"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            },
        )

    model = MiniMax(config, store, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ModelError, match="INCOMPLETE_GENERATION"):
        await model.generate([], "test")
    assert store.conn.execute("SELECT total FROM calls").fetchone()[0] == 5
    await model.close()


async def test_retry_is_bounded_and_body_identical(store, config, monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr("timofey.provider.asyncio.sleep", AsyncMock())
    requests = []

    def handler(request):
        if request.url.path.endswith("models"):
            return httpx.Response(200, json=catalog(config))
        requests.append(request.content)
        return httpx.Response(503, json={"error": config.api_key})

    model = MiniMax(config, store, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ModelError, match="HTTP_503"):
        await model.generate([{"role": "user", "content": "Вопрос"}], "test")
    assert len(requests) == 3 and len(set(requests)) == 1
    assert store.conn.execute("SELECT count(*) FROM calls").fetchone()[0] == 1
    await model.close()
