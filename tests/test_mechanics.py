import json
import random
import time
from collections import Counter

import pytest
from conftest import FakeModel

from timofey.config import Config
from timofey.db import Store
from timofey.news import NewsDesk, choose_kind
from timofey.provider import ModelError
from timofey.service import Service


def add_event(store, n, story=None, depth=0, status="open", topic="транспорт", kind="new"):
    data = {
        "text": f"Новость номер {n}. Комиссия перенесла заседание на другую остановку",
        "story_status": status,
    }
    eid = store.draft(data, kind, topic, "Кефирск", story or str(n), depth)
    store.published(eid, n)
    return eid


def test_distribution_independent():
    rng = random.Random(129)
    c = Counter(choose_kind(rng) for _ in range(100000))
    for kind, p in (("delirium", 0.05), ("crossover", 0.05), ("continuation", 0.15), ("new", 0.75)):
        assert abs(c[kind] / 100000 - p) < 0.004
    rng = random.Random(5)
    draws = [choose_kind(rng) for _ in range(1000)]
    # Not forced once per 20; empty and multiple-special windows both occur.
    windows = [draws[i : i + 20].count("delirium") for i in range(0, 1000, 20)]
    assert 0 in windows and max(windows) > 1


async def test_normal_and_special(store, kb, config):
    desk = NewsDesk(store, kb, FakeModel(), config)
    eid = await desk.generate("new")
    assert store.event(eid)["kind"] == "new"
    assert store.event(eid)["state"] == "draft"
    sid = await desk.generate("delirium")
    assert "Белая горячка Тимофея Рализма" in store.event(sid)["body"]
    assert json.loads(store.event(sid)["payload"])["story_status"] == "closed"


def test_diversity(store, kb, config):
    desk = NewsDesk(store, kb, FakeModel(), config, rng=random.Random(19))
    for i, t in enumerate(("транспорт", "образование", "наука"), 1):
        add_event(store, i, topic=t)
    assert desk.plan("new")["topic"] not in ("транспорт", "образование", "наука")
    assert desk.plan("new")["location"] != "Кефирск"


def test_continuations_and_closed_story(store, kb, config):
    desk = NewsDesk(store, kb, FakeModel(), config, rng=random.Random(6))
    a = add_event(store, 1, story="a")
    plan = desk.plan("continuation")
    assert [p["id"] for p in plan["parents"]] == [a] and plan["depth"] == 1
    add_event(store, 2, story="a", depth=1, status="closed")
    assert desk.plan("continuation")["kind"] == "new"


def test_crossover_two_stories(store, kb, config):
    desk = NewsDesk(store, kb, FakeModel(), config)
    add_event(store, 1, story="a")
    add_event(store, 2, story="b")
    assert len(desk.plan("crossover")["parents"]) == 2


async def test_canon_conflict_no_draft(store, kb, config):
    desk = NewsDesk(store, kb, FakeModel(conflict=True), config)
    with pytest.raises(ModelError, match="CANON_REVIEW_REQUIRED"):
        await desk.generate("new")
    assert not store.conn.execute("SELECT id FROM events").fetchall()


async def test_one_word_and_user_memory(store, kb, config):
    store.remember(101, "user", "Я уже спрашивал о выборах")
    store.remember(202, "user", "Чужой секрет")
    model = FakeModel()
    desk = NewsDesk(store, kb, model, config)
    assert (
        await desk.reply(101, "Почему отменили выборы?", "Контекст новости", style=5)
        == "Вмешательство."
    )
    prompt = json.loads(model.calls[0][0][1]["content"])
    assert prompt["this_citizen_memory"][0]["body"] == "Я уже спрашивал о выборах"
    assert "Чужой секрет" not in str(prompt)
    desk.model = FakeModel("Ну это вмешательство")
    with pytest.raises(ModelError, match="NOT_ONE_WORD"):
        await desk.reply(101, "Почему?", "", style=5)


def test_memory_bounded_and_expiring(store):
    for i in range(50):
        store.remember(1, "user", str(i), limit=6)
    assert len(store.history(1)) == 6
    store.conn.execute("UPDATE memory SET created=1")
    store.prune()
    assert store.history(1) == []


def test_rate_budget_and_restart(tmp_path):
    p = tmp_path / "test.sqlite3"
    s = Store(p)
    s.reserve("news", 60, 100, 6)
    s.close()
    s = Store(p)
    with pytest.raises(ValueError):
        s.reserve("news", 60, 100, 6)
    with pytest.raises(ValueError):
        s.reserve("news", 1, 100, 1)
    s.close()


class FakeBot:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        if self.fail:
            raise TimeoutError()
        from types import SimpleNamespace

        return SimpleNamespace(message_id=900)


async def test_duplicate_publish(store, kb, config):
    bot = FakeBot()
    svc = Service(config, store, NewsDesk(store, kb, FakeModel(), config), bot)
    eid = await svc.generate("new")
    assert await svc.publish(eid) == 900
    with pytest.raises(ModelError):
        await svc.publish(eid)
    assert len(bot.sent) == 1


async def test_timeout_not_retried(store, kb, config):
    bot = FakeBot(fail=True)
    svc = Service(config, store, NewsDesk(store, kb, FakeModel(), config), bot)
    eid = await svc.generate("new")
    with pytest.raises(ModelError, match="DELIVERY_UNCERTAIN"):
        await svc.publish(eid)
    assert store.event(eid)["state"] == "uncertain"
    with pytest.raises(ModelError):
        await svc.publish(eid)
    assert len(bot.sent) == 1


async def test_manual_scheduler_and_pause(store, kb, config):
    bot = FakeBot()
    svc = Service(config, store, NewsDesk(store, kb, FakeModel(), config), bot)
    assert store.get("mode", "manual") == "manual"
    store.set("autopost", True)
    await svc.scheduler_tick(time.time())
    assert bot.sent[0][0] == config.owner_id
    assert store.recent() == []
    store.set("paused", True)
    with pytest.raises(ModelError, match="BOT_PAUSED"):
        await svc.publish(1)


async def test_automatic_scheduler(store, kb, config):
    bot = FakeBot()
    svc = Service(config, store, NewsDesk(store, kb, FakeModel(), config), bot)
    store.set("autopost", True)
    store.set("mode", "automatic")
    await svc.scheduler_tick(time.time())
    assert bot.sent[0][0] == config.channel_id
    assert len(store.recent()) == 1


async def test_edited_text_audit(store, kb, config):
    model = FakeModel()
    svc = Service(config, store, NewsDesk(store, kb, model, config), FakeBot())
    eid = await svc.generate("new")
    model.conflict = True
    original = store.event(eid)["body"]
    with pytest.raises(ModelError):
        await svc.edit(eid, "Новый текст с историческим противоречием в публикации")
    assert store.event(eid)["body"] == original


def test_config_no_secret_repr(config):
    assert config.api_key not in repr(config)
    assert config.bot_token not in repr(config)
    with pytest.raises(ValueError):
        Config(model="openai/not-permitted")


async def test_edit_locks_publication(store, kb, config):
    import asyncio

    model = FakeModel()
    svc = Service(config, store, NewsDesk(store, kb, model, config), FakeBot())
    eid = await svc.generate("new")
    started, release = asyncio.Event(), asyncio.Event()

    async def audit(*args):
        started.set()
        await release.wait()

    svc.desk.audit = audit
    edit = asyncio.create_task(
        svc.edit(eid, "Новый текст городской новости с исправлением владельца")
    )
    await started.wait()
    with pytest.raises(ModelError, match="ALREADY_SENT_OR_NOT_APPROVED"):
        await svc.publish(eid)
    release.set()
    await edit
    assert store.event(eid)["state"] == "draft"


def test_inflight_send_after_restart_is_uncertain(tmp_path):
    path = tmp_path / "restart.sqlite3"
    s = Store(path)
    eid = s.draft(
        {"text": "Новость после которой выключилось электричество"},
        "new",
        "транспорт",
        "Кефирск",
        "s",
        0,
    )
    assert s.claim_publication(eid)
    s.close()
    s = Store(path)
    assert s.event(eid)["state"] == "uncertain"
    assert not s.claim_publication(eid)
    s.close()
