import json

import pytest

from timofey.news import NewsDesk
from timofey.provider import ModelError


class ReplyModel:
    def __init__(self, *, rejects=0):
        self.rejects = rejects
        self.comments = 0
        self.audits = 0
        self.policies = []

    async def generate(self, messages, purpose, **kwargs):
        if purpose == "comment":
            self.comments += 1
            return "Ложь." if self.comments == 1 else "Несогласен."
        if purpose == "canon_audit":
            self.audits += 1
            self.policies.append(messages[0]["content"])
            reject = self.audits <= self.rejects
            return json.dumps({
                "ok": not reject,
                "conflicts": ["Противоречие установленному канону"] if reject else [],
            })
        raise AssertionError(purpose)


async def test_comment_uses_comment_specific_canon_policy(store, kb, config):
    model = ReplyModel()
    desk = NewsDesk(store, kb, model, config)
    response = await desk.reply(
        123, "Почему Рализм молчит?", "Официальная новость", style=1
    )
    assert response == "Ложь."
    assert model.comments == 1
    assert model.audits == 1
    assert "Это КОММЕНТАРИЙ" in model.policies[0]
    assert "При неуверенности отклони" not in model.policies[0]


async def test_comment_retries_once_if_first_answer_contradicts_canon(store, kb, config):
    model = ReplyModel(rejects=1)
    desk = NewsDesk(store, kb, model, config)
    response = await desk.reply(
        123, "Почему Рализм молчит?", "Официальная новость", style=1
    )
    assert response == "Несогласен."
    assert model.comments == 2
    assert model.audits == 2
    assert all("КОММЕНТАРИЙ" in policy for policy in model.policies)


async def test_comment_never_sends_unverified_answer_after_two_rejections(store, kb, config):
    model = ReplyModel(rejects=2)
    desk = NewsDesk(store, kb, model, config)
    with pytest.raises(ModelError, match="CANON_REVIEW_REQUIRED"):
        await desk.reply(123, "Кто президент?", "Официальная новость", style=1)
    assert model.comments == 2
    assert model.audits == 2


async def test_news_still_uses_strict_canon_review(store, kb, config):
    model = ReplyModel()
    desk = NewsDesk(store, kb, model, config)
    await desk.audit("В городе произошло событие", "Каменный Мост")
    assert "При неуверенности отклони" in model.policies[0]


async def test_one_word_reply_preserved_during_retry(store, kb, config):
    model = ReplyModel(rejects=1)
    desk = NewsDesk(store, kb, model, config)
    answer = await desk.reply(123, "Что ответите?", "Официальная новость", style=5)
    assert answer == "Несогласен."
    assert model.comments == 2
