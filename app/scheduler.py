"""매일 아침 8시, 관심 설정이 있는 사용자에게 디스코드 브리핑을 보낸다."""
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

KST = timezone(timedelta(hours=9))


def should_notify(last_notified_on, today) -> bool:
    return last_notified_on is None or last_notified_on < today


def format_send_time(value) -> str:
    if isinstance(value, timedelta):
        seconds = int(value.total_seconds()) % (24 * 3600)
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        return f"{hours:02d}:{minutes:02d}"
    text = str(value or "08:00")
    return text[:5] if len(text) >= 5 else "08:00"


def matches_send_time(send_time, now: datetime) -> bool:
    return format_send_time(send_time) == now.strftime("%H:%M")


def ensure_notify_column(cur):
    cur.execute("SHOW COLUMNS FROM user_preferences LIKE 'last_notified_on'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE user_preferences ADD COLUMN last_notified_on DATE NULL")
    cur.execute("SHOW COLUMNS FROM user_preferences LIKE 'send_time'")
    if not cur.fetchone():
        cur.execute(
            "ALTER TABLE user_preferences ADD COLUMN send_time TIME NOT NULL DEFAULT '08:00:00'"
        )


def dispatch_morning_briefings():
    from app.briefing import collect_news, send_today_briefing
    from app.main import get_conn

    now = datetime.now(KST)
    today = now.date()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            ensure_notify_column(cur)
            cur.execute(
                "SELECT user_id, last_notified_on, send_time FROM user_preferences "
                "WHERE discord_webhook IS NOT NULL AND discord_webhook <> ''"
            )
            users = [
                user
                for user in cur.fetchall()
                if should_notify(user["last_notified_on"], today)
                and matches_send_time(user["send_time"], now)
            ]
    finally:
        conn.close()

    if not users:
        return

    try:
        collect_news()
    except Exception:
        pass

    for user in users:
        try:
            send_today_briefing(user["user_id"])
        except Exception:
            continue
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE user_preferences SET last_notified_on=%s WHERE user_id=%s",
                    (today, user["user_id"]),
                )
            conn.commit()
        finally:
            conn.close()


def start_scheduler():
    scheduler = BackgroundScheduler(timezone=KST)
    scheduler.add_job(
        dispatch_morning_briefings,
        CronTrigger(minute="*", timezone=KST),
        id="morning_briefing",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=30,
    )
    scheduler.start()
    return scheduler
