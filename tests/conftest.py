import json
from pathlib import Path

import pytest

from timofey.config import Config
from timofey.db import Store
from timofey.knowledge import Knowledge


@pytest.fixture
def config():
    return Config(
        bot_token="123456:TEST_token_never_use_for_real_requests",
        api_key="test-only-not-a-real-key",
        channel_id=-1001,
        discussion_id=-1002,
    )


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def kb(store):
    k = Knowledge(store)
    k.load(Path("knowledge/canon.jsonl"))
    return k


class FakeModel:
    def __init__(self, text=None, conflict=False):
        self.calls = []
        self.text = text
        self.conflict = conflict

    async def generate(self, messages, purpose, **kwargs):
        self.calls.append((messages, purpose))
        if purpose == "canon_audit":
            return json.dumps(
                {"ok": not self.conflict, "conflicts": ["conflict"] if self.conflict else []}
            )
        if purpose == "comment":
            return self.text or "Вмешательство."
        request = json.loads(messages[1]["content"])
        heading = (
            "Белая горячка Тимофея Рализма"
            if "Белая горячка" in request["task"]
            else "Заявление городской комиссии"
        )
        return json.dumps(
            {
                "text": self.text
                or heading
                + "\n\nКомиссия объявила карандаш исполняющим обязанности секретаря. Карандаш воздержался от подписи",
                "organizations": ["Городская комиссия"],
                "characters": [],
                "consequences": "Решение ожидает подписи",
                "story_status": "open",
            },
            ensure_ascii=False,
        )
