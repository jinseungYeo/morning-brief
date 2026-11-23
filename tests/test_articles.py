import os
import time

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.main import app


def db_config():
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
    }


def root_conn():
    cfg = db_config()
    return pymysql.connect(**cfg)


def app_conn():
    cfg = db_config()
    cfg["database"] = os.getenv("DB_NAME", "news_summary")
    return pymysql.connect(**cfg)


def ensure_schema():
    conn = root_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS news_summary "
                "DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci"
            )
            cur.execute("USE news_summary")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS users ("
                "user_id INT AUTO_INCREMENT PRIMARY KEY,"
                "email VARCHAR(255) NOT NULL UNIQUE,"
                "password_hash VARCHAR(255) NOT NULL,"
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS articles ("
                "article_id INT AUTO_INCREMENT PRIMARY KEY,"
                "user_id INT NOT NULL,"
                "title VARCHAR(300) NOT NULL,"
                "source_press VARCHAR(100),"
                "source ENUM('MANUAL','RSS') NOT NULL DEFAULT 'MANUAL',"
                "url VARCHAR(500),"
                "raw_text TEXT NOT NULL,"
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
                "CONSTRAINT fk_articles_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,"
                "INDEX idx_articles_user_created (user_id, created_at))"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS summaries ("
                "result_id INT AUTO_INCREMENT PRIMARY KEY,"
                "article_id INT NOT NULL,"
                "summary_level ENUM('ONE_LINE','SHORT','DETAIL','KEY_EVENTS') NOT NULL,"
                "summary_text TEXT NOT NULL,"
                "people_orgs JSON,"
                "keywords JSON,"
                "theme TEXT,"
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "CONSTRAINT fk_summaries_article FOREIGN KEY (article_id) REFERENCES articles(article_id) ON DELETE CASCADE,"
                "CONSTRAINT uq_summaries_article_level UNIQUE (article_id, summary_level))"
            )
            cur.execute(
                "INSERT IGNORE INTO users (user_id, email, password_hash) "
                "VALUES (1, 'test@example.com', 'dummy_hash')"
            )
    finally:
        conn.close()


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    try:
        ensure_schema()
    except pymysql.MySQLError as exc:
        pytest.skip(f"MySQL 연결 실패: {exc}")


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def test_user_id():
    uid = int(time.time() * 1000) % 1_000_000
    email = f"test_{uid}@example.com"
    conn = app_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, %s)",
                (email, "pw"),
            )
            user_id = cur.lastrowid
    finally:
        conn.close()
    yield user_id
    conn = app_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
    finally:
        conn.close()


def create_article(client: TestClient, user_id: int, title="제목", raw_text="본문"):
    res = client.post(
        "/articles",
        json={"user_id": user_id, "title": title, "raw_text": raw_text},
    )
    assert res.status_code == 201
    return res.json()["article_id"]


def test_create_article_201(client, test_user_id):
    res = client.post(
        "/articles",
        json={"user_id": test_user_id, "title": "뉴스", "raw_text": "내용"},
    )
    assert res.status_code == 201
    assert res.json()["title"] == "뉴스"


def test_create_article_user_not_found_404(client):
    res = client.post(
        "/articles",
        json={"user_id": 99999999, "title": "뉴스", "raw_text": "내용"},
    )
    assert res.status_code == 404


def test_list_articles_200(client, test_user_id):
    create_article(client, test_user_id, "목록 기사")
    res = client.get(f"/articles?user_id={test_user_id}&page=1&size=10")
    assert res.status_code == 200
    assert "items" in res.json()


def test_list_articles_paging_400(client, test_user_id):
    res = client.get(f"/articles?user_id={test_user_id}&page=0&size=200")
    assert res.status_code == 400


def test_get_article_200(client, test_user_id):
    article_id = create_article(client, test_user_id, "단건 기사")
    res = client.get(f"/articles/{article_id}")
    assert res.status_code == 200
    assert res.json()["article_id"] == article_id


def test_get_article_404(client):
    res = client.get("/articles/99999999")
    assert res.status_code == 404


def test_patch_article_200(client, test_user_id):
    article_id = create_article(client, test_user_id, "수정 전")
    res = client.patch(f"/articles/{article_id}", json={"title": "수정 후"})
    assert res.status_code == 200
    assert res.json()["title"] == "수정 후"


def test_patch_article_400_when_empty_body(client, test_user_id):
    article_id = create_article(client, test_user_id, "수정 테스트")
    res = client.patch(f"/articles/{article_id}", json={})
    assert res.status_code == 400


def test_delete_article_204(client, test_user_id):
    article_id = create_article(client, test_user_id, "삭제 테스트")
    res = client.delete(f"/articles/{article_id}")
    assert res.status_code == 204


def test_delete_article_404(client):
    res = client.delete("/articles/99999999")
    assert res.status_code == 404


def test_summary_create_and_duplicate_400_and_json_array(client, test_user_id):
    article_id = create_article(client, test_user_id, "요약 기사")
    payload = {
        "summary_level": "SHORT",
        "summary_text": "요약문",
        "people_orgs": ["정부", "국회"],
        "keywords": ["정책", "발표"],
        "theme": "정치",
    }
    first = client.post(f"/articles/{article_id}/summaries", json=payload)
    assert first.status_code == 201
    assert first.json()["keywords"] == ["정책", "발표"]
    assert first.json()["people_orgs"] == ["정부", "국회"]

    second = client.post(f"/articles/{article_id}/summaries", json=payload)
    assert second.status_code == 400


def test_summary_invalid_level_400(client, test_user_id):
    article_id = create_article(client, test_user_id, "요약 레벨 검사")
    res = client.post(
        f"/articles/{article_id}/summaries",
        json={"summary_level": "BAD", "summary_text": "요약"},
    )
    assert res.status_code == 400


def test_summary_list_200_and_article_404(client, test_user_id):
    article_id = create_article(client, test_user_id, "요약 목록")
    client.post(
        f"/articles/{article_id}/summaries",
        json={"summary_level": "ONE_LINE", "summary_text": "한 줄"},
    )
    ok = client.get(f"/articles/{article_id}/summaries")
    assert ok.status_code == 200
    assert isinstance(ok.json(), list)

    not_found = client.get("/articles/99999999/summaries")
    assert not_found.status_code == 404


def test_summary_concurrent_duplicate_returns_400(client, test_user_id):
    """동시 INSERT로 UNIQUE(1062)가 나도 500이 아니라 400으로 내려오는지 확인."""
    import concurrent.futures

    article_id = create_article(client, test_user_id, "동시 중복 요약")
    payload = {
        "summary_level": "DETAIL",
        "summary_text": "동시 요청 요약",
        "people_orgs": ["기관A"],
        "keywords": ["동시성"],
        "theme": "테스트",
    }

    def post_once():
        return client.post(f"/articles/{article_id}/summaries", json=payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: post_once(), range(2)))

    statuses = sorted(res.status_code for res in results)
    assert statuses == [201, 400]
    assert any(
        "중복" in (res.json().get("detail") or "")
        for res in results
        if res.status_code == 400
    )
