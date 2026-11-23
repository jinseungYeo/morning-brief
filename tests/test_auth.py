import os
import time

import pymysql
import pytest
from fastapi.testclient import TestClient

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


def ensure_preferences_table():
    conn = app_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS user_preferences ("
                "user_id INT PRIMARY KEY,"
                "categories JSON NOT NULL,"
                "sort_mode ENUM('LATEST','POPULAR') NOT NULL DEFAULT 'LATEST',"
                "item_count INT NOT NULL DEFAULT 5,"
                "summary_level ENUM('ONE_LINE','SHORT','DETAIL','KEY_EVENTS') NOT NULL DEFAULT 'SHORT',"
                "discord_webhook VARCHAR(500) NULL,"
                "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
                "CONSTRAINT fk_pref_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE)"
            )
    finally:
        conn.close()


@pytest.fixture(scope="module", autouse=True)
def setup_preferences():
    try:
        ensure_preferences_table()
    except pymysql.MySQLError as exc:
        pytest.skip(f"MySQL 연결 실패: {exc}")


@pytest.fixture()
def client():
    return TestClient(app)


def unique_email():
    return f"user_{int(time.time() * 1000)}@example.com"


def test_register_and_login(client):
    email = unique_email()
    created = client.post("/auth/register", json={"email": email, "password": "password123"})
    assert created.status_code == 201
    assert created.json()["email"] == email
    assert created.json()["token"]

    again = client.post("/auth/register", json={"email": email, "password": "password123"})
    assert again.status_code == 400

    logged = client.post("/auth/login", json={"email": email, "password": "password123"})
    assert logged.status_code == 200

    wrong = client.post("/auth/login", json={"email": email, "password": "wrongpass1"})
    assert wrong.status_code == 401

    user_id = created.json()["user_id"]
    conn = app_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
    finally:
        conn.close()


def test_preferences_and_discord_guard(client):
    email = unique_email()
    created = client.post("/auth/register", json={"email": email, "password": "password123"})
    token = created.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    saved = client.put(
        "/me/preferences",
        headers=headers,
        json={
            "categories": ["SPORTS", "ECONOMY"],
            "sort_mode": "POPULAR",
            "item_count": 5,
            "summary_level": "SHORT",
            "discord_webhook": None,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["categories"] == ["SPORTS", "ECONOMY"]
    assert saved.json()["sort_mode"] == "POPULAR"

    ranking = client.put(
        "/me/preferences",
        headers=headers,
        json={
            "categories": ["RANKING"],
            "sort_mode": "LATEST",
            "item_count": 5,
            "summary_level": "SHORT",
        },
    )
    assert ranking.status_code == 200
    assert ranking.json()["categories"] == ["RANKING"]
    assert ranking.json()["sort_mode"] == "POPULAR"

    mixed = client.put(
        "/me/preferences",
        headers=headers,
        json={"categories": ["RANKING", "SPORTS"], "sort_mode": "POPULAR", "item_count": 5, "summary_level": "SHORT"},
    )
    assert mixed.status_code == 400

    bad = client.put(
        "/me/preferences",
        headers=headers,
        json={"categories": ["GAME"], "sort_mode": "LATEST", "item_count": 5, "summary_level": "SHORT"},
    )
    assert bad.status_code == 400

    missing = client.post("/me/discord/test", headers=headers)
    assert missing.status_code == 400

    conn = app_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id=%s", (created.json()["user_id"],))
    finally:
        conn.close()
