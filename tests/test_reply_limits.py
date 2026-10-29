import pytest


def test_comments_bypass_local_daily_and_minute_budget(store):
    for i in range(12):
        call_id = store.reserve("comment", 5000, budget=1, rpm=1)
        store.settle(call_id, {
            "prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5,
        })
        audit_id = store.reserve("comment_audit", 3000, budget=1, rpm=1)
        store.settle(audit_id, {
            "prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5,
        })
    assert store.conn.execute("SELECT count(*) FROM calls").fetchone()[0] == 24


def test_comment_usage_does_not_block_news_budget(store):
    for i in range(9):
        call_id = store.reserve("comment", 8000, budget=10, rpm=1)
        store.settle(call_id, {
            "prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200,
        })
    first_news = store.reserve("news", 5, budget=10, rpm=1)
    store.settle(first_news, {
        "prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5,
    })
    with pytest.raises(ValueError, match="LOCAL_BUDGET_OR_RATE_LIMIT"):
        store.reserve("news", 5, budget=10, rpm=1)
    assert store.reserve("comment", 100, budget=10, rpm=1) > first_news
