"""한국 기사 RSS 수집과 오늘의 브리핑 선별."""
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from html import unescape

import feedparser
import httpx

FEEDS = [
    {"category": "RANKING", "press": "구글 뉴스", "url": "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"},
    {"category": "RANKING", "press": "연합뉴스", "url": "https://www.yna.co.kr/rss/news.xml"},
    {"category": "RANKING", "press": "한겨레", "url": "https://www.hani.co.kr/rss/"},
    {"category": "RANKING", "press": "경향신문", "url": "https://www.khan.co.kr/rss/rssdata/total_news.xml"},
    {"category": "RANKING", "press": "동아일보", "url": "https://rss.donga.com/total.xml"},
    {"category": "RANKING", "press": "조선일보", "url": "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml"},
    {"category": "RANKING", "press": "JTBC", "url": "https://fs.jtbc.co.kr/RSS/newsflash.xml"},
    {"category": "RANKING", "press": "뉴스1", "url": "https://www.news1.kr/rss/S1N1.xml"},
    {"category": "SPORTS", "press": "구글 뉴스", "url": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=ko&gl=KR&ceid=KR:ko"},
    {"category": "SPORTS", "press": "연합뉴스", "url": "https://www.yna.co.kr/rss/sports.xml"},
    {"category": "SPORTS", "press": "SBS", "url": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=09&plink=RSSREADER"},
    {"category": "ECONOMY", "press": "구글 뉴스", "url": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko"},
    {"category": "ECONOMY", "press": "연합뉴스", "url": "https://www.yna.co.kr/rss/economy.xml"},
    {"category": "ECONOMY", "press": "한국경제", "url": "https://www.hankyung.com/feed/all-news"},
    {"category": "POLITICS", "press": "구글 뉴스", "url": "https://news.google.com/rss/search?q=%EC%A0%95%EC%B9%98&hl=ko&gl=KR&ceid=KR:ko"},
    {"category": "POLITICS", "press": "연합뉴스", "url": "https://www.yna.co.kr/rss/politics.xml"},
    {"category": "POLITICS", "press": "SBS", "url": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=01&plink=RSSREADER"},
    {"category": "IT", "press": "구글 뉴스", "url": "https://news.google.com/rss/search?q=%EC%9D%B8%EA%B3%B5%EC%A7%80%EB%8A%A5+OR+%EB%B0%98%EB%8F%84%EC%B2%B4+OR+IT&hl=ko&gl=KR&ceid=KR:ko"},
    {"category": "IT", "press": "연합뉴스", "url": "https://www.yna.co.kr/rss/industry.xml"},
    {"category": "SOCIETY", "press": "구글 뉴스", "url": "https://news.google.com/rss/headlines/section/topic/NATION?hl=ko&gl=KR&ceid=KR:ko"},
    {"category": "SOCIETY", "press": "연합뉴스", "url": "https://www.yna.co.kr/rss/society.xml"},
    {"category": "SOCIETY", "press": "SBS", "url": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=03&plink=RSSREADER"},
    {"category": "WORLD", "press": "구글 뉴스", "url": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ko&gl=KR&ceid=KR:ko"},
    {"category": "WORLD", "press": "연합뉴스", "url": "https://www.yna.co.kr/rss/international.xml"},
    {"category": "CULTURE", "press": "구글 뉴스", "url": "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=ko&gl=KR&ceid=KR:ko"},
    {"category": "CULTURE", "press": "연합뉴스", "url": "https://www.yna.co.kr/rss/culture.xml"},
]

USER_AGENT = "MorningBrief/0.1 (+https://localhost; Korean news briefing)"
STALE_MINUTES = 20


def has_korean(text: str) -> bool:
    return any("\uac00" <= char <= "\ud7a3" for char in text)


def plain_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def excerpt_from(entry, title: str) -> str:
    raw = entry.get("summary") or entry.get("description") or ""
    text = plain_text(raw)
    if not text or text == title or not has_korean(text):
        return ""
    if len(text) > 180:
        return text[:180].rstrip() + "…"
    return text


def clean_entry(entry, feed: dict, rank_no: int):
    title = (entry.get("title") or "").strip()
    url = (entry.get("link") or "").strip()
    if not title or not url.startswith("http") or not has_korean(title):
        return None

    press = feed["press"]
    source = entry.get("source")
    source_title = ""
    if source is not None:
        source_title = (source.get("title") or "").strip()
    if source_title and has_korean(source_title):
        press = source_title
    elif " - " in title:
        head, tail = title.rsplit(" - ", 1)
        if head and len(tail) <= 30 and has_korean(head):
            title = head.strip()
            press = tail.strip()

    published = entry.get("published_parsed") or entry.get("updated_parsed")
    published_at = None
    if published:
        try:
            published_at = datetime(*published[:6])
        except (TypeError, ValueError):
            published_at = None

    return {
        "category": feed["category"],
        "title": title[:300],
        "source_press": press[:100],
        "url": url,
        "url_hash": hashlib.sha256(f"{feed['category']}|{url}".encode()).hexdigest(),
        "published_at": published_at,
        "rank_no": rank_no,
        "excerpt": excerpt_from(entry, title),
    }


def fetch_feed(feed: dict):
    try:
        response = httpx.get(
            feed["url"],
            headers={"User-Agent": USER_AGENT},
            timeout=8,
            follow_redirects=True,
        )
        if response.status_code != 200:
            return []
        parsed = feedparser.parse(response.content)
    except (httpx.HTTPError, ValueError):
        return []

    items = []
    for rank_no, entry in enumerate(parsed.entries[:12], start=1):
        item = clean_entry(entry, feed, rank_no)
        if item:
            items.append(item)
    return items


def _time_value(value):
    published = value or datetime(2000, 1, 1)
    return (
        published.year * 10000000000
        + published.month * 100000000
        + published.day * 1000000
        + published.hour * 10000
        + published.minute * 100
        + published.second
    )


def _sort_key(row, sort_mode: str):
    published = _time_value(row.get("published_at"))
    if sort_mode == "LATEST":
        return (-published, row.get("rank_no") or 999)
    return (row.get("rank_no") or 999, -published)


def select_briefing(rows: list[dict], item_count: int, sort_mode: str):
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(row["category"], []).append(row)
    for category in buckets:
        buckets[category].sort(key=lambda row: _sort_key(row, sort_mode))

    ordered = []
    indexes = {category: 0 for category in buckets}
    categories = list(buckets)
    while any(indexes[category] < len(buckets[category]) for category in categories):
        for category in categories:
            if indexes[category] < len(buckets[category]):
                ordered.append(buckets[category][indexes[category]])
                indexes[category] += 1

    picked = []
    seen_titles = set()
    press_count: dict[str, int] = {}

    def take(row, limit_press: bool):
        title_key = row["title"].strip()
        press = row.get("source_press") or "기타"
        if title_key in seen_titles:
            return False
        if limit_press and press_count.get(press, 0) >= 2:
            return False
        seen_titles.add(title_key)
        press_count[press] = press_count.get(press, 0) + 1
        picked.append(row)
        return True

    for row in ordered:
        if len(picked) >= item_count:
            break
        take(row, limit_press=True)
    if len(picked) < item_count:
        for row in ordered:
            if len(picked) >= item_count:
                break
            take(row, limit_press=False)
    return picked


def ensure_news_table(cur):
    cur.execute(
        "CREATE TABLE IF NOT EXISTS collected_news ("
        "news_id INT AUTO_INCREMENT PRIMARY KEY,"
        "category VARCHAR(20) NOT NULL,"
        "title VARCHAR(300) NOT NULL,"
        "source_press VARCHAR(100),"
        "url TEXT NOT NULL,"
        "url_hash CHAR(64) NOT NULL,"
        "published_at DATETIME NULL,"
        "rank_no INT NOT NULL DEFAULT 0,"
        "collected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "UNIQUE KEY uq_collected_category_url (category, url_hash),"
        "INDEX idx_collected_category_time (category, collected_at))"
    )
    cur.execute("SHOW COLUMNS FROM collected_news LIKE 'excerpt'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE collected_news ADD COLUMN excerpt TEXT NULL")
    cur.execute("SHOW COLUMNS FROM collected_news LIKE 'ai_summary'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE collected_news ADD COLUMN ai_summary TEXT NULL")


def is_stale(cur) -> bool:
    cur.execute("SELECT MAX(collected_at) AS latest FROM collected_news")
    latest = cur.fetchone()["latest"]
    if not latest:
        return True
    return latest < datetime.now() - timedelta(minutes=STALE_MINUTES)


def collect_news():
    from app.main import get_conn

    found = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_feed, feed) for feed in FEEDS]
        for future in as_completed(futures):
            found.extend(future.result())

    unique = {}
    for item in found:
        unique[(item["category"], item["url_hash"])] = item

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            ensure_news_table(cur)
            cur.execute("DELETE FROM collected_news WHERE collected_at < NOW() - INTERVAL 3 DAY")
            for item in unique.values():
                cur.execute(
                    "INSERT INTO collected_news "
                    "(category, title, source_press, url, url_hash, published_at, rank_no, excerpt) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE title=VALUES(title), source_press=VALUES(source_press), "
                    "published_at=VALUES(published_at), rank_no=VALUES(rank_no), excerpt=VALUES(excerpt), "
                    "collected_at=CURRENT_TIMESTAMP",
                    (
                        item["category"],
                        item["title"],
                        item["source_press"],
                        item["url"],
                        item["url_hash"],
                        item["published_at"],
                        item["rank_no"],
                        item["excerpt"],
                    ),
                )
        conn.commit()
    finally:
        conn.close()
    return len(unique)


def load_today_briefing(user_id: int, refresh: bool = False):
    from app.main import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            ensure_news_table(cur)
            cur.execute("SELECT * FROM user_preferences WHERE user_id=%s", (user_id,))
            prefs = cur.fetchone()
            categories = []
            sort_mode = "LATEST"
            item_count = 5
            summary_level = "SHORT"
            if prefs:
                import json

                raw = prefs["categories"]
                if isinstance(raw, str):
                    categories = json.loads(raw)
                else:
                    categories = raw or []
                sort_mode = prefs["sort_mode"] or "LATEST"
                item_count = prefs["item_count"] or 5
                summary_level = prefs.get("summary_level") or "SHORT"
            if not categories:
                return {"items": [], "message": "관심 분야를 먼저 고르세요."}
            should_collect = refresh or is_stale(cur)
    finally:
        conn.close()

    if should_collect:
        collect_news()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            marks = ", ".join(["%s"] * len(categories))
            cur.execute(
                f"SELECT category, title, source_press, url, url_hash, published_at, rank_no, excerpt, ai_summary "
                f"FROM collected_news WHERE category IN ({marks}) "
                f"AND collected_at >= NOW() - INTERVAL 2 DAY",
                tuple(categories),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    items = select_briefing(rows, item_count, sort_mode)
    items = attach_summaries(items, summary_level)
    for item in items:
        if item.get("published_at"):
            item["published_at"] = item["published_at"].isoformat(sep=" ")
    return {"items": items, "message": "" if items else "지금 표시할 한국 기사가 없습니다."}


def briefing_text(item: dict) -> str:
    return (item.get("ai_summary") or item.get("excerpt") or "").strip() or "원문에서 자세히 볼 수 있습니다."


def attach_summaries(items: list[dict], summary_level: str):
    import json
    import os

    import httpx

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    pending = [item for item in items if not (item.get("ai_summary") or "").strip()]
    if not api_key or not pending:
        return items

    length = "한 문장" if summary_level == "ONE_LINE" else "두 문장 이내"
    payload_items = [
        {
            "id": index,
            "title": item["title"],
            "context": (item.get("excerpt") or "")[:500],
        }
        for index, item in enumerate(pending)
    ]
    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "한국 뉴스 브리핑 작성자다. 주어진 제목과 내용만 보고 핵심을 요약한다. "
                            f"각 기사는 {length}. 없는 사실은 만들지 않는다. "
                            'JSON만 반환: {"items":[{"id":0,"summary":"..."}]}'
                        ),
                    },
                    {"role": "user", "content": json.dumps({"articles": payload_items}, ensure_ascii=False)},
                ],
            },
            timeout=30,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        summaries = {
            int(row["id"]): str(row.get("summary") or "").strip()
            for row in parsed.get("items", [])
        }
    except (httpx.HTTPError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return items

    from app.main import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for index, item in enumerate(pending):
                summary = summaries.get(index, "")
                if not summary:
                    continue
                item["ai_summary"] = summary
                if item.get("url_hash") and item.get("category"):
                    cur.execute(
                        "UPDATE collected_news SET ai_summary=%s WHERE category=%s AND url_hash=%s",
                        (summary, item["category"], item["url_hash"]),
                    )
        conn.commit()
    finally:
        conn.close()
    return items


def build_discord_messages(items: list[dict]):
    messages = []
    chunks = [items[index:index + 10] for index in range(0, len(items), 10)]
    for index, chunk in enumerate(chunks):
        embeds = []
        for item in chunk:
            summary = briefing_text(item)
            embeds.append(
                {
                    "title": item["title"][:256],
                    "url": item["url"],
                    "description": summary[:300],
                    "footer": {"text": item.get("source_press") or ""},
                }
            )
        message = {"embeds": embeds}
        if index == 0:
            message["content"] = "오늘의 브리핑입니다. 제목을 누르면 원문으로 이동합니다."
        messages.append(message)
    return messages


def send_today_briefing(user_id: int):
    from fastapi import HTTPException

    import httpx

    from app.main import get_conn

    briefing = load_today_briefing(user_id, refresh=False)
    items = briefing["items"]
    if not items:
        raise HTTPException(status_code=400, detail=briefing["message"] or "보낼 브리핑이 없습니다.")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT discord_webhook FROM user_preferences WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    webhook = (row or {}).get("discord_webhook") if row else None
    if not webhook:
        raise HTTPException(status_code=400, detail="디스코드 웹훅을 먼저 저장하세요.")

    try:
        for message in build_discord_messages(items):
            response = httpx.post(webhook, json=message, timeout=8)
            if response.status_code >= 400:
                raise HTTPException(status_code=400, detail="디스코드가 브리핑 전송을 거절했습니다.")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail="디스코드 전송에 실패했습니다.") from exc
    return {"ok": True, "sent_count": len(items)}
