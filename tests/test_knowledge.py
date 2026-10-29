import json
from pathlib import Path

from timofey.knowledge import chunks


def test_actual_canon_dates_and_person(kb):
    evidence = "\n".join(r["text"] for r in kb.search("Рализм выборы отмена 2059 2061", 20))
    assert "65%" in evidence and "12%" in evidence
    evidence = "\n".join(r["text"] for r in kb.foundational())
    assert "2025" in evidence and "Темнозоринск" in evidence
    assert "Павел Каменец" in evidence


def test_retrieval_bounds_and_separate_layers(store, kb):
    store.remember(1, "user", "Кефирстан теперь империя бананов")
    data = {"text": "Кефирстан теперь империя бананов", "story_status": "open"}
    store.draft(data, "delirium", "наука", "Кефирск", "s", 0)
    result = kb.search("Кефирстан бананов")
    assert len(result) <= 8
    assert "империя бананов" not in str(result)
    assert all(r["source"] for r in result)


def test_chunks_bounded():
    result = list(chunks("А" * 9000))
    assert all(len(r) <= 1800 for r in result)
    assert "".join(result) == "А" * 9000


def test_no_private_history_in_canon_file():
    records = [json.loads(x) for x in Path("knowledge/canon.jsonl").read_text().splitlines()]
    assert len(records) > 1000
    assert all(r["layer"] == "canon" and r["source"] == "Aurelia_Wiki_updated.xml" for r in records)


def test_context_has_author_guards_and_is_bounded(kb):
    context = kb.context("Йогуртстанская война дроны")
    assert sum(len(r["text"]) for r in context) <= 14000
    assert "НЕ применялись" in context[0]["text"]


def test_owner_lore_survives_corpus_update(store, kb, tmp_path):
    kb.add("Авторское уточнение", "Пробный факт из документа владельца")
    modified = tmp_path / "new.jsonl"
    modified.write_text(
        json.dumps(
            {
                "id": "new",
                "title": "Новая статья",
                "text": "Новые данные",
                "source": "wiki",
                "layer": "canon",
            }
        )
    )
    kb.load(modified)
    rows = store.conn.execute("SELECT text FROM canon WHERE source='owner:telegram'").fetchall()
    assert rows and "Пробный факт" in rows[0][0]


def test_sor_spelling_variant_finds_named_article(store, kb):
    kb.add(
        "СОР",
        "Северные Объединенные Республики - отдельное государство Аурелии, не САР Кефирстан",
    )
    query = "Что такое Северные Объеденные Республики"
    selected = kb.mentioned_articles(query)
    assert selected
    assert selected[0]["title"] == "СОР"
    context = kb.context("Случайный текст о Кефирстане", priority_query=query)
    assert any(item["title"] == "СОР" for item in context)
    assert sum(len(item["text"]) for item in context) <= 14000


def test_specific_other_country_is_preferred_over_fixed_kefirstan_anchors(store, kb):
    kb.add("Фалк Вейсман", "Фалк Вейсман - отдельный персонаж, не Тимофей Рализм")
    results = kb.context(
        "Кефирстан Рализм ЛПК",
        priority_query="Кто такой Фалк Вейсман?",
    )
    assert results[0]["title"] == "Подтвержденные автором ориентиры Аурелии"
    assert results[1]["title"] == "Фалк Вейсман"
    assert sum(len(item["text"]) for item in results) <= 14000
