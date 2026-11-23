import os
import time
from datetime import date, datetime, timezone

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.briefing import briefing_text, build_discord_messages, clean_entry, ensure_news_table, select_briefing
from app.scheduler import matches_send_time, should_notify
from app.main import app


def app_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "news_summary"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def test_clean_entry_splits_korean_press():
    item = clean_entry(
        {
            "title": "한국 축구 대표팀 승리 - 연합뉴스",
            "link": "https://example.com/soccer",
            "summary": "<p>연장 끝에 역전승을 거뒀다.</p>",
        },
        {"category": "SPORTS", "press": "구글 뉴스"},
        1,
    )
    assert item["title"] == "한국 축구 대표팀 승리"
    assert item["source_press"] == "연합뉴스"
    assert item["category"] == "SPORTS"
    assert "연장" in item["excerpt"]


def test_morning_notify_once_per_day():
    today = date(2026, 11, 23)
    assert should_notify(None, today) is True
    assert should_notify(date(2026, 11, 22), today) is True
    assert should_notify(today, today) is False


def test_send_time_matches_chosen_clock():
    eight = datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc)
    seven = datetime(2026, 11, 23, 7, 30, tzinfo=timezone.utc)
    assert matches_send_time("08:00:00", eight) is True
    assert matches_send_time("07:30", seven) is True
    assert matches_send_time("08:00", seven) is False


def test_briefing_text_prefers_ai_summary():
    assert briefing_text({"ai_summary": "핵심 한 줄", "excerpt": "긴 본문"}) == "핵심 한 줄"
    assert briefing_text({"excerpt": "피드 요약"}) == "피드 요약"
    assert briefing_text({}) == "원문에서 자세히 볼 수 있습니다."


def test_discord_message_includes_summary_and_link():
    messages = build_discord_messages(
        [
            {
                "title": "한국 증시 상승",
                "url": "https://example.com/market",
                "excerpt": "코스피가 장 초반 올랐다.",
                "source_press": "연합뉴스",
            }
        ]
    )
    embed = messages[0]["embeds"][0]
    assert embed["url"] == "https://example.com/market"
    assert embed["description"] == "코스피가 장 초반 올랐다."
    assert "오늘의 브리핑" in messages[0]["content"]


def test_clean_entry_skips_non_korean():
    item = clean_entry(
        {"title": "Only English headline", "link": "https://example.com/en"},
        {"category": "WORLD", "press": "구글 뉴스"},
        1,
    )
    assert item is None


def test_select_briefing_mixes_categories_and_limits_press():
    rows = []
    for index in range(4):
        rows.append(
            {
                "category": "SPORTS",
                "title": f"스포츠 소식 {index}",
                "source_press": "연합뉴스",
                "published_at": datetime(2026, 11, 23, 9, index),
                "rank_no": index + 1,
            }
        )
    rows.append(
        {
            "category": "ECONOMY",
            "title": "환율 상승",
            "source_press": "한국경제",
            "published_at": datetime(2026, 11, 23, 8, 0),
            "rank_no": 1,
        }
    )
    picked = select_briefing(rows, 3, "POPULAR")
    categories = {row["category"] for row in picked}
    assert categories == {"SPORTS", "ECONOMY"}
    assert sum(row["source_press"] == "연합뉴스" for row in picked) <= 2


@pytest.fixture()
def client():
    return TestClient(app)


def test_today_briefing_uses_saved_korean_articles(client, monkeypatch):
    monkeypatch.setattr("app.briefing.collect_news", lambda: 0)
    email = f"brief_{int(time.time() * 1000)}@example.com"
    created = client.post("/auth/register", json={"email": email, "password": "password123"})
    token = created.json()["token"]
    user_id = created.json()["user_id"]
    headers = {"Authorization": f"Bearer {token}"}
    client.put(
        "/me/preferences",
        headers=headers,
        json={
            "categories": ["SPORTS"],
            "sort_mode": "LATEST",
            "item_count": 5,
            "summary_level": "SHORT",
        },
    )
    url_hash = f"test-hash-{int(time.time() * 1000)}"
    conn = app_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO collected_news (category, title, source_press, url, url_hash, published_at, rank_no) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    "SPORTS",
                    "프로야구 개막",
                    "연합뉴스",
                    f"https://example.com/{url_hash}",
                    url_hash,
                    "2099-01-01 00:00:00",
                    1,
                ),
            )
    finally:
        conn.close()

    res = client.get("/briefings/today", headers=headers)
    assert res.status_code == 200
    titles = [item["title"] for item in res.json()["items"]]
    assert "프로야구 개막" in titles

    conn = app_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM collected_news WHERE url_hash=%s", (url_hash,))
            cur.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
    finally:
        conn.close()
