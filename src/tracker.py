"""تتبّع حالة كل شركة في قاعدة بيانات SQLite.

الهدف الأساسي: استحالة إرسال نفس الرسالة مرتين لنفس الشركة، حتى لو
توقّف النظام في منتصف الإرسال أو أعدت تشغيل الأمر.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

STAGE_INITIAL = "initial"
STAGE_FOLLOWUP = "followup"

SCHEMA = """
CREATE TABLE IF NOT EXISTS outreach (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email        TEXT    NOT NULL,
    company_name TEXT    NOT NULL,
    stage        TEXT    NOT NULL,
    status       TEXT    NOT NULL,          -- sent | failed | drafted
    subject      TEXT,
    message_id   TEXT,
    thread_id    TEXT,
    error        TEXT,
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outreach_email ON outreach(email);
CREATE INDEX IF NOT EXISTS idx_outreach_lookup ON outreach(email, stage, status);

CREATE TABLE IF NOT EXISTS replies (
    email      TEXT PRIMARY KEY,
    thread_id  TEXT,
    detected_at TEXT NOT NULL,
    snippet    TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SentRecord:
    email: str
    company_name: str
    stage: str
    status: str
    subject: str
    thread_id: str
    created_at: str


class Tracker:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        with closing(self.conn.cursor()) as cur:
            cur.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Tracker":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -------------------------------------------------------------- تسجيل #
    def record(
        self,
        *,
        email: str,
        company_name: str,
        stage: str,
        status: str,
        subject: str = "",
        message_id: str = "",
        thread_id: str = "",
        error: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT INTO outreach (email, company_name, stage, status, subject,"
            " message_id, thread_id, error, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                email.lower(),
                company_name,
                stage,
                status,
                subject,
                message_id,
                thread_id,
                error,
                _now(),
            ),
        )
        self.conn.commit()

    def record_reply(self, email: str, thread_id: str, snippet: str) -> bool:
        """يسجّل رداً جديداً. يرجع True إذا كان الرد جديداً."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO replies (email, thread_id, detected_at, snippet)"
            " VALUES (?, ?, ?, ?)",
            (email.lower(), thread_id, _now(), snippet[:500]),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------ استعلام #
    def was_sent(self, email: str, stage: str) -> bool:
        # bounced ضمن الحاجبات عمداً: العنوان ميت، وإعادة الإرسال إليه
        # آلياً تضر السمعة البريدية. تصحيح الإيميل ينشئ مفتاحاً جديداً مؤهلاً.
        row = self.conn.execute(
            "SELECT 1 FROM outreach WHERE email = ? AND stage = ?"
            " AND status IN ('sent', 'drafted', 'bounced') LIMIT 1",
            (email.lower(), stage),
        ).fetchone()
        return row is not None

    def has_bounced(self, email: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM outreach WHERE email = ? AND status = 'bounced' LIMIT 1",
            (email.lower(),),
        ).fetchone()
        return row is not None

    def mark_bounced(self, email: str) -> None:
        """ارتدّت الرسالة: العنوان غير صالح. تُزال من الردود إن سُجّلت خطأً."""
        self.conn.execute(
            "UPDATE outreach SET status = 'bounced',"
            " error = 'ارتدّت الرسالة — العنوان غير صالح أو الصندوق ممتلئ'"
            " WHERE email = ? AND status = 'sent'",
            (email.lower(),),
        )
        self.conn.execute("DELETE FROM replies WHERE email = ?", (email.lower(),))
        self.conn.commit()

    def has_replied(self, email: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM replies WHERE email = ? LIMIT 1", (email.lower(),)
        ).fetchone()
        return row is not None

    def sent_since(self, hours: int = 24) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
            timespec="seconds"
        )
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM outreach WHERE status = 'sent' AND created_at >= ?",
            (cutoff,),
        ).fetchone()
        return int(row["c"])

    def get_initial(self, email: str) -> SentRecord | None:
        row = self.conn.execute(
            "SELECT * FROM outreach WHERE email = ? AND stage = ? AND status = 'sent'"
            " ORDER BY created_at LIMIT 1",
            (email.lower(), STAGE_INITIAL),
        ).fetchone()
        if row is None:
            return None
        return SentRecord(
            email=row["email"],
            company_name=row["company_name"],
            stage=row["stage"],
            status=row["status"],
            subject=row["subject"] or "",
            thread_id=row["thread_id"] or "",
            created_at=row["created_at"],
        )

    def due_for_followup(self, after_days: int) -> list[SentRecord]:
        """الشركات التي مضى على رسالتها الأولى المدة المطلوبة ولم تُرسل لها متابعة."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=after_days)).isoformat(
            timespec="seconds"
        )
        rows = self.conn.execute(
            "SELECT o.* FROM outreach o"
            " WHERE o.stage = ? AND o.status = 'sent' AND o.created_at <= ?"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM outreach f WHERE f.email = o.email"
            "       AND f.stage = ? AND f.status IN ('sent','drafted'))"
            " ORDER BY o.created_at",
            (STAGE_INITIAL, cutoff, STAGE_FOLLOWUP),
        ).fetchall()
        return [
            SentRecord(
                email=r["email"],
                company_name=r["company_name"],
                stage=r["stage"],
                status=r["status"],
                subject=r["subject"] or "",
                thread_id=r["thread_id"] or "",
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def drafted_rows(self) -> list[SentRecord]:
        """كل المسودات التي أنشأها النظام ولم يُحسم مصيرها بعد."""
        rows = self.conn.execute(
            "SELECT * FROM outreach WHERE status = 'drafted' AND thread_id != ''"
            " ORDER BY created_at"
        ).fetchall()
        return [
            SentRecord(
                email=r["email"], company_name=r["company_name"],
                stage=r["stage"], status=r["status"],
                subject=r["subject"] or "", thread_id=r["thread_id"] or "",
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def resolve_draft_sent(self, email: str, stage: str,
                           rfc_message_id: str, sent_at_iso: str) -> None:
        """المستخدم أرسل المسودة يدوياً من جيميل — نحوّلها إلى رسالة مُرسلة.

        نستبدل created_at بوقت الإرسال الفعلي حتى تُحسب مهلة المتابعة منه،
        ونخزّن Message-ID الحقيقي حتى تُربط المتابعة بنفس المحادثة.
        """
        self.conn.execute(
            "UPDATE outreach SET status = 'sent', message_id = ?,"
            " created_at = ?, error = 'أُرسلت يدوياً من جيميل'"
            " WHERE email = ? AND stage = ? AND status = 'drafted'",
            (rfc_message_id, sent_at_iso, email.lower(), stage),
        )
        self.conn.commit()

    def resolve_draft_deleted(self, email: str, stage: str) -> None:
        """المستخدم حذف المسودة — الشركة تعود مؤهلة لحملة قادمة."""
        self.conn.execute(
            "UPDATE outreach SET status = 'deleted',"
            " error = 'حذف المستخدم المسودة من جيميل'"
            " WHERE email = ? AND stage = ? AND status = 'drafted'",
            (email.lower(), stage),
        )
        self.conn.commit()

    def all_sent_threads(self) -> list[tuple[str, str]]:
        """كل (إيميل، thread_id) للرسائل المُرسلة — يُستخدم لفحص الردود."""
        rows = self.conn.execute(
            "SELECT DISTINCT email, thread_id FROM outreach"
            " WHERE status = 'sent' AND thread_id != ''"
        ).fetchall()
        return [(r["email"], r["thread_id"]) for r in rows]

    def stats(self) -> dict[str, int]:
        def scalar(sql: str, args: tuple = ()) -> int:
            return int(self.conn.execute(sql, args).fetchone()[0])

        return {
            "initial_sent": scalar(
                "SELECT COUNT(DISTINCT email) FROM outreach"
                " WHERE stage = ? AND status = 'sent'",
                (STAGE_INITIAL,),
            ),
            "followup_sent": scalar(
                "SELECT COUNT(DISTINCT email) FROM outreach"
                " WHERE stage = ? AND status = 'sent'",
                (STAGE_FOLLOWUP,),
            ),
            "drafted": scalar("SELECT COUNT(*) FROM outreach WHERE status = 'drafted'"),
            "bounced": scalar(
                "SELECT COUNT(DISTINCT email) FROM outreach WHERE status = 'bounced'"
            ),
            "failed": scalar("SELECT COUNT(*) FROM outreach WHERE status = 'failed'"),
            "replies": scalar("SELECT COUNT(*) FROM replies"),
            "sent_last_24h": self.sent_since(24),
        }

    def history(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT company_name, email, stage, status, subject, error, created_at"
            " FROM outreach ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
