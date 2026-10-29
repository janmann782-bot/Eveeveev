import json

import pytest

from timofey.news import NewsDesk, validate_text
from timofey.provider import ModelError


@pytest.mark.parametrize(
    'text,code',
    [
        ('{"Заголовок": "Скандал", "Текст": "Проверка завода"}', "RAW_JSON_IN_POST"),
        ("Сборная сообщила о результате\n\nУчастники мероприятиенeros дошли до склада", "CORRUPTED_MIXED_SCRIPT"),
        ("В ведомстве нашли проблему\n\nnouveau powerlifting Discipline Weight Sport Code Industrial Code", "EXCESSIVE_FOREIGN_LANGUAGE"),
        ("Ведомство объявило рекорд\n\nСлужба дала ответ — принято", "INVALID_POST_STYLE"),
    ],
)
def test_rejects_unpublishable_post(text, code):
    with pytest.raises(ModelError, match=code):
        validate_text(text)


def test_accepts_russian_publication_with_lore_names():
    validate_text("Ниженовогородский завод объявил конкурс\n\nГКАЗ передал на конкурс 12 грузовиков. Делегация Kevirytsyt приехала из Каменного Моста")


class FakeEditorialModel:
    def __init__(self, drafts):
        self.drafts = drafts
        self.news_attempts = 0
        self.news_messages = []

    async def generate(self, messages, purpose, **kwargs):
        if purpose == "canon_audit":
            candidate = json.loads(messages[1]["content"])["candidate"]
            ok = "24 тонны" not in candidate
            return json.dumps({"ok": ok, "conflicts": [] if ok else ["Невозможный рекорд"]})
        assert purpose == "news"
        self.news_messages.append(messages.copy())
        index = min(self.news_attempts, len(self.drafts) - 1)
        self.news_attempts += 1
        return json.dumps(
            {
                "text": self.drafts[index],
                "organizations": [],
                "characters": [],
                "consequences": "Организаторы пересматривают результаты",
                "story_status": "closed",
            },
            ensure_ascii=False,
        )


async def test_malformed_json_post_is_regenerated_before_save(store, config, kb):
    model = FakeEditorialModel([
        '{"Заголовок": "Новая акция", "Текст": "Бригада покинула завод"}',
        "Завод отправил мешки на соревнования\n\nОрганизаторы забыли оформить накладную и теперь спорят с весами",
    ])
    event_id = await NewsDesk(store, kb, model, config).generate("new")
    assert model.news_attempts == 2
    assert len(model.news_messages[1]) == 3
    assert store.event(event_id)["body"].startswith("Завод отправил мешки")


async def test_unrealistic_numeric_news_is_rejected_by_editor_and_retried(store, config, kb):
    model = FakeEditorialModel([
        "Спортсмены установили рекорд\n\n12 спортсменов без механизмов подняли 24 тонны одновременно, комиссия признала результат",
        "Спортивная комиссия лишилась печати\n\nСудьи обнаружили, что печать неделю использовали как гирю. Результат аннулировали",
    ])
    event_id = await NewsDesk(store, kb, model, config).generate("new")
    assert model.news_attempts == 2
    assert "печать" in store.event(event_id)["body"]


async def test_two_malformed_attempts_do_not_leave_draft(store, config, kb):
    bad = '{"Заголовок": "Запись", "Текст": "Битый формат"}'
    model = FakeEditorialModel([bad, bad])
    with pytest.raises(ModelError, match="RAW_JSON_IN_POST"):
        await NewsDesk(store, kb, model, config).generate("new")
    assert model.news_attempts == 2
    assert store.conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
