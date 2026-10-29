from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, MessageOriginChannel, Update, User
from conftest import FakeModel

from timofey.news import NewsDesk
from timofey.service import Service
from timofey.telegram import Access, admin_allowed, build_router, resolve_chats


class Session(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (DeleteMessage, AnswerCallbackQuery)):
            return True
        if isinstance(method, SendMessage):
            return Message(
                message_id=500 + len(self.calls),
                date=datetime.now(timezone.utc),
                chat=Chat(
                    id=int(method.chat_id),
                    type="private" if int(method.chat_id) > 0 else "supergroup",
                ),
                from_user=User(id=bot.id, is_bot=True, first_name="Bot"),
                text=method.text,
            )
        raise AssertionError(type(method))

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""


def message(user_id, chat_id, kind="private", text="/admin", **kwargs):
    return Message(
        message_id=17,
        date=datetime.now(timezone.utc),
        text=text,
        chat=Chat(id=chat_id, type=kind),
        from_user=User(id=user_id, is_bot=False, first_name="User"),
        **kwargs,
    )


def setup(config, store, kb, model=None):
    session = Session()
    bot = Bot(config.bot_token, session=session)
    svc = Service(config, store, NewsDesk(store, kb, model or FakeModel(), config), bot)
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(Access(config, store))
    dispatcher.include_router(build_router(svc))
    return dispatcher, bot, session, svc


@pytest.mark.parametrize(
    "uid,cid,kind",
    [
        (555, 555, "private"),
        (7787565361, -1005, "supergroup"),
        (555, -1002, "supergroup"),
        (555, -1999, "supergroup"),
    ],
)
async def test_admin_rejected_outside_owner_dm(config, store, kb, uid, cid, kind):
    d, b, s, svc = setup(config, store, kb)
    await d.feed_update(b, Update(update_id=1, message=message(uid, cid, kind)))
    assert not s.calls
    assert not svc.desk.model.calls
    await b.session.close()


async def test_owner_panel_and_duplicate_update(config, store, kb):
    d, b, s, svc = setup(config, store, kb)
    update = Update(update_id=1, message=message(config.owner_id, config.owner_id))
    await d.feed_update(b, update)
    await d.feed_update(b, update)
    assert len(s.calls) == 1
    assert "/key" in s.calls[0].text
    await b.session.close()


async def test_stolen_callback_rejected(config, store, kb):
    d, b, s, svc = setup(config, store, kb)
    cb = CallbackQuery(
        id="q",
        from_user=User(id=123, is_bot=False, first_name="Thief"),
        chat_instance="x",
        message=message(config.owner_id, config.owner_id),
        data="cmd:mode automatic",
    )
    await d.feed_update(b, Update(update_id=1, callback_query=cb))
    assert not s.calls and store.get("mode", "manual") == "manual"
    await b.session.close()


async def test_owner_callback(config, store, kb):
    d, b, s, svc = setup(config, store, kb)
    cb = CallbackQuery(
        id="q",
        from_user=User(id=config.owner_id, is_bot=False, first_name="Owner"),
        chat_instance="x",
        message=message(b.id, config.owner_id),
        data="cmd:mode automatic",
    )
    await d.feed_update(b, Update(update_id=1, callback_query=cb))
    assert store.get("mode") == "automatic"
    await b.session.close()


async def test_key_deleted_not_echoed_or_stored(config, store, kb):
    model = FakeModel()
    model.change_key = AsyncMock()
    d, b, s, svc = setup(config, store, kb, model)
    key = "test-secret-to-be-deleted"
    await d.feed_update(
        b,
        Update(update_id=1, message=message(config.owner_id, config.owner_id, text="/key " + key)),
    )
    model.change_key.assert_awaited_once_with(key)
    assert isinstance(s.calls[0], DeleteMessage)
    assert key not in str(s.calls[1:])
    assert key not in str(store.conn.execute("SELECT * FROM settings").fetchall())
    await b.session.close()


async def test_foreign_chat_key_ignored(config, store, kb):
    model = FakeModel()
    model.change_key = AsyncMock()
    d, b, s, svc = setup(config, store, kb, model)
    await d.feed_update(
        b,
        Update(
            update_id=1, message=message(config.owner_id, -900, "supergroup", text="/key stolen")
        ),
    )
    model.change_key.assert_not_awaited()
    assert not s.calls
    await b.session.close()


async def test_comment_context_reply_and_no_loop(config, store, kb, monkeypatch):
    monkeypatch.setattr("timofey.telegram.random.random", lambda: 0)
    d, b, s, svc = setup(config, store, kb)
    # Force separate one-word mechanic for deterministic test.
    svc.desk.rng = SimpleNamespace(randrange=lambda n: 5)
    source = Message(
        message_id=100,
        date=datetime.now(timezone.utc),
        chat=Chat(id=config.discussion_id, type="supergroup"),
        sender_chat=Chat(id=config.channel_id, type="channel"),
        is_automatic_forward=True,
        forward_origin=MessageOriginChannel(
            type="channel",
            date=datetime.now(timezone.utc),
            chat=Chat(id=config.channel_id, type="channel"),
            message_id=77,
        ),
        text="ЦИК прокомментировала выборы",
    )
    await d.feed_update(b, Update(update_id=1, message=source))
    comment = message(
        555,
        config.discussion_id,
        "supergroup",
        text="Почему отменили выборы?",
        reply_to_message=source,
    )
    await d.feed_update(b, Update(update_id=2, message=comment))
    assert len(s.calls) == 1 and s.calls[0].text == "Вмешательство."
    assert store.history(555)[-1]["role"] == "assistant"
    bot_comment = comment.model_copy(
        update={"from_user": User(id=666, is_bot=True, first_name="Other bot")}
    )
    await d.feed_update(b, Update(update_id=3, message=bot_comment))
    assert len(s.calls) == 1
    await b.session.close()


async def test_no_reply_to_general_chat_or_wrong_forward(config, store, kb):
    d, b, s, svc = setup(config, store, kb)
    msg = message(555, config.discussion_id, "supergroup", text="Просто разговор в чате")
    await d.feed_update(b, Update(update_id=1, message=msg))
    wrong = msg.model_copy(
        update={"is_automatic_forward": True, "sender_chat": Chat(id=-999, type="channel")}
    )
    await d.feed_update(b, Update(update_id=2, message=wrong))
    assert not s.calls and not svc.desk.model.calls
    await b.session.close()


async def test_resolve_linked_numeric_ids(config):
    channel = SimpleNamespace(type="channel", id=-1001, linked_chat_id=-1002)
    group = SimpleNamespace(type="supergroup", id=-1002, linked_chat_id=-1001)
    bot = SimpleNamespace(
        id=123,
        get_chat=AsyncMock(side_effect=[channel, group]),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status="administrator", can_post_messages=True)
        ),
    )
    result = await resolve_chats(bot, config)
    assert result.channel_id == -1001 and result.discussion_id == -1002
    group.linked_chat_id = -777
    bot.get_chat = AsyncMock(side_effect=[channel, group])
    with pytest.raises(ValueError):
        await resolve_chats(bot, config)


def test_admin_identity_numeric(config):
    assert admin_allowed(config.owner_id, config.owner_id, "private", config)
    assert not admin_allowed(123, config.owner_id, "private", config)
    assert not admin_allowed(config.owner_id, -1002, "supergroup", config)
