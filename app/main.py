"""AI 기반 뉴스 요약 및 분석 서비스 - Article CRUD + Summary API + 회원/관심설정."""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any

import httpx
import pymysql
from dotenv import load_dotenv
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .briefing import load_today_briefing, send_today_briefing
from .scheduler import ensure_notify_column, format_send_time, start_scheduler

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY_LEVELS = ("ONE_LINE", "SHORT", "DETAIL", "KEY_EVENTS")
CATEGORIES = ("RANKING", "SPORTS", "ECONOMY", "POLITICS", "IT", "SOCIETY", "WORLD", "CULTURE")
SORT_MODES = ("LATEST", "POPULAR")
APP_SECRET = os.getenv("APP_SECRET", "dev-only-change-me")
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7

@asynccontextmanager
async def lifespan(_app):
    scheduler = start_scheduler()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="News Summary API", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def setting(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def get_conn():
    return pymysql.connect(
        host=setting("DB_HOST", "MYSQLHOST", "MYSQL_HOST", default="localhost"),
        port=int(setting("DB_PORT", "MYSQLPORT", "MYSQL_PORT", default="3306")),
        user=setting("DB_USER", "MYSQLUSER", "MYSQL_USER", default="root"),
        password=setting("DB_PASSWORD", "MYSQLPASSWORD", "MYSQL_PASSWORD"),
        database=setting("DB_NAME", "MYSQLDATABASE", "MYSQL_DATABASE", default="news_summary"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


class ArticleCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    user_id: int
    title: str = Field(min_length=1, max_length=300)
    source_press: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, max_length=500)
    raw_text: str = Field(min_length=1)


class ArticleUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    source_press: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, max_length=500)
    raw_text: str | None = Field(default=None, min_length=1)


class AuthRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=100)


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    categories: list[str] = Field(min_length=1)
    sort_mode: str = "LATEST"
    item_count: int = Field(default=5, ge=1, le=20)
    summary_level: str = "SHORT"
    send_time: str = "08:00"
    discord_webhook: str | None = None


class SummaryRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    summary_level: str = Field(description="ONE_LINE / SHORT / DETAIL / KEY_EVENTS")
    summary_text: str = Field(min_length=1)
    people_orgs: list[str] | None = None
    keywords: list[str] | None = None
    theme: str | None = None


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": "잘못된 요청입니다. 입력값을 확인하세요."})


@app.exception_handler(pymysql.MySQLError)
async def db_error_handler(request: Request, exc: pymysql.MySQLError):
    return JSONResponse(status_code=500, content={"detail": "서버(DB) 오류가 발생했습니다."})


def fetch_article(cur, article_id: int):
    cur.execute("SELECT * FROM articles WHERE article_id=%s", (article_id,))
    return cur.fetchone()


def parse_json_value(value: Any):
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            return value
    return value


def normalize_summary_row(row: dict[str, Any]):
    row["people_orgs"] = parse_json_value(row.get("people_orgs"))
    row["keywords"] = parse_json_value(row.get("keywords"))
    return row


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return (
        "pbkdf2$"
        + base64.urlsafe_b64encode(salt).decode()
        + "$"
        + base64.urlsafe_b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$", 2)
        if scheme != "pbkdf2":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return hmac.compare_digest(digest, expected)


def make_token(user_id: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "exp": int(time.time()) + TOKEN_TTL_SECONDS}).encode()
    ).decode()
    signature = hmac.new(APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def read_token(token: str) -> int:
    try:
        payload, signature = token.split(".", 1)
        expected = hmac.new(APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad signature")
        data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
        if int(data["exp"]) < int(time.time()):
            raise ValueError("expired")
        return int(data["user_id"])
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")


def require_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    user_id = read_token(authorization.removeprefix("Bearer ").strip())
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, email, created_at FROM users WHERE user_id=%s",
                (user_id,),
            )
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
            return user
    finally:
        conn.close()


def default_preferences(user_id: int):
    return {
        "user_id": user_id,
        "categories": [],
        "sort_mode": "LATEST",
        "item_count": 5,
        "summary_level": "SHORT",
        "send_time": "08:00",
        "discord_webhook": None,
    }


def normalize_preferences(row: dict[str, Any]):
    row["categories"] = parse_json_value(row.get("categories")) or []
    row["send_time"] = format_send_time(row.get("send_time"))
    return row


def validate_preferences(body: PreferenceUpdate):
    unknown = [item for item in body.categories if item not in CATEGORIES]
    if unknown or len(set(body.categories)) != len(body.categories):
        raise HTTPException(
            status_code=400,
            detail=f"categories는 {CATEGORIES} 안에서 중복 없이 선택해야 합니다.",
        )
    if "RANKING" in body.categories and len(body.categories) > 1:
        raise HTTPException(status_code=400, detail="전체 랭킹은 다른 분야와 함께 고를 수 없습니다.")
    if "RANKING" in body.categories:
        body.sort_mode = "POPULAR"
    if body.sort_mode not in SORT_MODES:
        raise HTTPException(status_code=400, detail=f"sort_mode는 {SORT_MODES} 중 하나여야 합니다.")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", body.send_time):
        raise HTTPException(status_code=400, detail="발송 시각은 00:00부터 23:59 형식이어야 합니다.")
    if body.summary_level not in SUMMARY_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"summary_level은 {SUMMARY_LEVELS} 중 하나여야 합니다.",
        )
    if body.discord_webhook:
        if not (
            body.discord_webhook.startswith("https://discord.com/api/webhooks/")
            or body.discord_webhook.startswith("https://discordapp.com/api/webhooks/")
        ):
            raise HTTPException(status_code=400, detail="디스코드 웹훅 주소 형식이 아닙니다.")


@app.post("/auth/register", status_code=201)
def register(body: AuthRequest):
    if "@" not in body.email or "." not in body.email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="이메일 형식을 확인하세요.")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s)",
                    (body.email, hash_password(body.password)),
                )
            except pymysql.IntegrityError as exc:
                conn.rollback()
                if exc.args and exc.args[0] == 1062:
                    raise HTTPException(status_code=400, detail="이미 가입된 이메일입니다.") from exc
                raise
            user_id = cur.lastrowid
        conn.commit()
        return {"token": make_token(user_id), "user_id": user_id, "email": body.email}
    finally:
        conn.close()


@app.post("/auth/login")
def login(body: AuthRequest):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, email, password_hash FROM users WHERE email=%s",
                (body.email,),
            )
            user = cur.fetchone()
            if not user or not verify_password(body.password, user["password_hash"]):
                raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
            return {
                "token": make_token(user["user_id"]),
                "user_id": user["user_id"],
                "email": user["email"],
            }
    finally:
        conn.close()


@app.get("/me")
def get_me(authorization: str | None = Header(default=None)):
    return require_user(authorization)


@app.get("/me/preferences")
def get_preferences(authorization: str | None = Header(default=None)):
    user = require_user(authorization)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM user_preferences WHERE user_id=%s", (user["user_id"],))
            row = cur.fetchone()
            if not row:
                return default_preferences(user["user_id"])
            return normalize_preferences(row)
    finally:
        conn.close()


@app.put("/me/preferences")
def update_preferences(body: PreferenceUpdate, authorization: str | None = Header(default=None)):
    user = require_user(authorization)
    validate_preferences(body)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            ensure_notify_column(cur)
            cur.execute(
                "INSERT INTO user_preferences "
                "(user_id, categories, sort_mode, item_count, summary_level, send_time, discord_webhook) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE "
                "categories=VALUES(categories), sort_mode=VALUES(sort_mode), "
                "item_count=VALUES(item_count), summary_level=VALUES(summary_level), "
                "send_time=VALUES(send_time), discord_webhook=VALUES(discord_webhook)",
                (
                    user["user_id"],
                    json.dumps(body.categories, ensure_ascii=False),
                    body.sort_mode,
                    body.item_count,
                    body.summary_level,
                    f"{body.send_time}:00",
                    body.discord_webhook or None,
                ),
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM user_preferences WHERE user_id=%s", (user["user_id"],))
            return normalize_preferences(cur.fetchone())
    finally:
        conn.close()


@app.post("/me/discord/test")
def test_discord(authorization: str | None = Header(default=None)):
    user = require_user(authorization)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT discord_webhook FROM user_preferences WHERE user_id=%s",
                (user["user_id"],),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    webhook = (row or {}).get("discord_webhook") if row else None
    if not webhook:
        raise HTTPException(status_code=400, detail="디스코드 웹훅을 먼저 저장하세요.")

    payload = {
        "content": "Morning Brief 테스트입니다. 이 채널로 아침 브리핑을 보낼 수 있습니다."
    }
    try:
        response = httpx.post(webhook, json=payload, timeout=8)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail="디스코드 전송에 실패했습니다.") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=400, detail="디스코드가 웹훅 요청을 거절했습니다.")
    return {"ok": True}


@app.get("/briefings/today")
def today_briefing(refresh: int = 0, authorization: str | None = Header(default=None)):
    user = require_user(authorization)
    return load_today_briefing(user["user_id"], refresh=bool(refresh))


@app.post("/briefings/today/notify")
def notify_today_briefing(authorization: str | None = Header(default=None)):
    user = require_user(authorization)
    return send_today_briefing(user["user_id"])


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.post("/articles", status_code=201)
def create_article(body: ArticleCreate):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE user_id=%s", (body.user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="존재하지 않는 user_id 입니다.")
            cur.execute(
                "INSERT INTO articles (user_id, title, source_press, url, raw_text) "
                "VALUES (%s, %s, %s, %s, %s)",
                (body.user_id, body.title, body.source_press, body.url, body.raw_text),
            )
            new_id = cur.lastrowid
        conn.commit()
        with conn.cursor() as cur:
            return fetch_article(cur, new_id)
    finally:
        conn.close()


@app.get("/articles")
def list_articles(user_id: int, page: int = 1, size: int = 10):
    if page < 1 or size < 1 or size > 100:
        raise HTTPException(status_code=400, detail="page는 1 이상, size는 1~100 이어야 합니다.")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM articles WHERE user_id=%s", (user_id,))
            total = cur.fetchone()["c"]
            cur.execute(
                "SELECT article_id, title, source_press, source, created_at FROM articles "
                "WHERE user_id=%s ORDER BY created_at DESC, article_id DESC LIMIT %s OFFSET %s",
                (user_id, size, (page - 1) * size),
            )
            return {"items": cur.fetchall(), "page": page, "size": size, "total": total}
    finally:
        conn.close()


@app.get("/articles/{article_id}")
def get_article(article_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            row = fetch_article(cur, article_id)
            if not row:
                raise HTTPException(status_code=404, detail="기사를 찾을 수 없습니다.")
            return row
    finally:
        conn.close()


@app.patch("/articles/{article_id}")
def update_article(article_id: int, body: ArticleUpdate):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="수정할 필드가 없습니다.")
    if fields.get("title", "x") is None or fields.get("raw_text", "x") is None:
        raise HTTPException(status_code=400, detail="title, raw_text는 null로 수정할 수 없습니다.")

    set_clause = ", ".join(f"{key}=%s" for key in fields)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if not fetch_article(cur, article_id):
                raise HTTPException(status_code=404, detail="기사를 찾을 수 없습니다.")
            cur.execute(
                f"UPDATE articles SET {set_clause} WHERE article_id=%s",
                (*fields.values(), article_id),
            )
        conn.commit()
        with conn.cursor() as cur:
            return fetch_article(cur, article_id)
    finally:
        conn.close()


@app.delete("/articles/{article_id}", status_code=204)
def delete_article(article_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM articles WHERE article_id=%s", (article_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="기사를 찾을 수 없습니다.")
        conn.commit()
        return Response(status_code=204)
    finally:
        conn.close()


@app.post("/articles/{article_id}/summaries", status_code=201)
def request_summary(article_id: int, body: SummaryRequest):
    if body.summary_level not in SUMMARY_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"summary_level은 {SUMMARY_LEVELS} 중 하나여야 합니다.",
        )

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if not fetch_article(cur, article_id):
                raise HTTPException(status_code=404, detail="기사를 찾을 수 없습니다.")

            cur.execute(
                "SELECT result_id FROM summaries WHERE article_id=%s AND summary_level=%s",
                (article_id, body.summary_level),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"이미 '{body.summary_level}' 수준의 요약이 존재합니다. 중복 신청은 거절됩니다.",
                )

            try:
                cur.execute(
                    "INSERT INTO summaries (article_id, summary_level, summary_text, people_orgs, keywords, theme) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        article_id,
                        body.summary_level,
                        body.summary_text,
                        json.dumps(body.people_orgs, ensure_ascii=False) if body.people_orgs is not None else None,
                        json.dumps(body.keywords, ensure_ascii=False) if body.keywords is not None else None,
                        body.theme,
                    ),
                )
            except pymysql.IntegrityError as exc:
                if exc.args and exc.args[0] == 1062:
                    raise HTTPException(
                        status_code=400,
                        detail=f"이미 '{body.summary_level}' 수준의 요약이 존재합니다. 중복 신청은 거절됩니다.",
                    ) from exc
                raise
            new_id = cur.lastrowid
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM summaries WHERE result_id=%s", (new_id,))
            return normalize_summary_row(cur.fetchone())
    finally:
        conn.close()


@app.get("/articles/{article_id}/summaries")
def list_summaries(article_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if not fetch_article(cur, article_id):
                raise HTTPException(status_code=404, detail="기사를 찾을 수 없습니다.")
            cur.execute(
                "SELECT * FROM summaries WHERE article_id=%s ORDER BY created_at DESC, result_id DESC",
                (article_id,),
            )
            rows = cur.fetchall()
            return [normalize_summary_row(row) for row in rows]
    finally:
        conn.close()
