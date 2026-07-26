"""منطق الحملة: تجهيز الرسائل، ضبط الإيقاع، والإرسال الفعلي."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .config import Config
from .contacts import Company
from .gmail_client import GmailClient, GmailError, build_message
from .templating import RenderedEmail, render
from .tracker import STAGE_FOLLOWUP, STAGE_INITIAL, Tracker

Reporter = Callable[[str], None]


@dataclass
class PlannedEmail:
    company: Company
    stage: str
    rendered: RenderedEmail
    attachments: list[Path]
    thread_id: str = ""
    in_reply_to: str = ""


@dataclass
class SkippedEmail:
    company_name: str
    email: str
    reason: str


@dataclass
class Plan:
    planned: list[PlannedEmail]
    skipped: list[SkippedEmail]


@dataclass
class ExecutionResult:
    succeeded: int = 0
    failed: int = 0
    stopped_reason: str = ""


def _pause(seconds: float, stop_event: "threading.Event | None") -> bool:
    """ينتظر المدة المطلوبة. يرجع True إذا طُلب الإيقاف أثناء الانتظار.

    نستخدم Event.wait بدل time.sleep حتى يستجيب زر الإيقاف فوراً بدل أن
    ينتظر المستخدم انقضاء فاصل قد يبلغ دقيقتين.
    """
    if stop_event is None:
        time.sleep(seconds)
        return False
    return stop_event.wait(seconds)


# --------------------------------------------------------------- مرفقات #
def resolve_attachments(config: Config, company: Company) -> list[Path]:
    """مرفقات الشركة إن وُجدت، وإلا المرفقات الافتراضية."""
    if company.attachments:
        paths = [
            p if Path(p).is_absolute() else config.root / p for p in company.attachments
        ]
        return [Path(p) for p in paths]
    return config.default_attachments()


def check_attachments(config: Config, paths: list[Path]) -> list[str]:
    """يرجع قائمة أخطاء المرفقات (فارغة = سليمة)."""
    errors: list[str] = []
    total = 0
    for p in paths:
        if not p.exists():
            errors.append(f"المرفق غير موجود: {p}")
            continue
        total += p.stat().st_size
    limit_mb = float(config.attachments.get("max_total_mb", 20))
    if total > limit_mb * 1024 * 1024:
        errors.append(
            f"حجم المرفقات {total / 1024 / 1024:.1f}MB يتجاوز الحد {limit_mb}MB"
        )
    return errors


# --------------------------------------------------------------- توقيت #
def sending_window_error(config: Config, now: datetime | None = None) -> str:
    """يرجع سبب منع الإرسال الآن، أو نصاً فارغاً إذا كان الوقت مناسباً."""
    now = now or datetime.now()
    s = config.sending

    skip_days = s.get("skip_weekdays") or []
    if now.weekday() in skip_days:
        names = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
        return f"اليوم ({names[now.weekday()]}) ضمن الأيام المستثناة في settings.yaml"

    start, end = s.get("allowed_hours_start"), s.get("allowed_hours_end")
    if start is not None and end is not None and not (start <= now.hour < end):
        return f"الساعة {now.hour}:00 خارج نافذة الإرسال ({start}:00–{end}:00)"

    return ""


# ----------------------------------------------------------------- خطة #
def plan_initial(
    config: Config, tracker: Tracker, companies: Iterable[Company]
) -> Plan:
    planned: list[PlannedEmail] = []
    skipped: list[SkippedEmail] = []

    for company in companies:
        if company.skip:
            skipped.append(
                SkippedEmail(company.company_name, company.email, "معلّم skip في CSV")
            )
            continue
        if tracker.was_sent(company.key, STAGE_INITIAL):
            skipped.append(
                SkippedEmail(company.company_name, company.email, "أُرسلت مسبقاً")
            )
            continue

        attachments = resolve_attachments(config, company)
        rendered = render(
            templates_dir=config.paths.templates_dir,
            stage=STAGE_INITIAL,
            company=company,
            profile=config.profile,
            attachment_names=[p.name for p in attachments],
        )
        planned.append(PlannedEmail(company, STAGE_INITIAL, rendered, attachments))

    return Plan(planned, skipped)


def plan_followup(
    config: Config, tracker: Tracker, companies: Iterable[Company]
) -> Plan:
    by_key = {c.key: c for c in companies}
    planned: list[PlannedEmail] = []
    skipped: list[SkippedEmail] = []

    after_days = int(config.followup.get("after_days", 7))
    skip_if_replied = bool(config.followup.get("skip_if_replied", True))

    for record in tracker.due_for_followup(after_days):
        company = by_key.get(record.email)
        if company is None:
            skipped.append(
                SkippedEmail(record.company_name, record.email, "لم تعد في ملف CSV")
            )
            continue
        if company.skip:
            skipped.append(
                SkippedEmail(company.company_name, company.email, "معلّم skip في CSV")
            )
            continue
        if skip_if_replied and tracker.has_replied(company.key):
            skipped.append(
                SkippedEmail(company.company_name, company.email, "ردّت الشركة بالفعل")
            )
            continue

        initial = tracker.get_initial(company.key)
        sent_date = (initial.created_at[:10] if initial else "")

        attachments = resolve_attachments(config, company)
        rendered = render(
            templates_dir=config.paths.templates_dir,
            stage=STAGE_FOLLOWUP,
            company=company,
            profile=config.profile,
            attachment_names=[p.name for p in attachments],
            extra={"initial_sent_date": sent_date},
        )
        planned.append(
            PlannedEmail(
                company=company,
                stage=STAGE_FOLLOWUP,
                rendered=rendered,
                attachments=attachments,
                thread_id=record.thread_id,
                in_reply_to=_rfc_id_of(tracker, company.key),
            )
        )

    return Plan(planned, skipped)


def _rfc_id_of(tracker: Tracker, email: str) -> str:
    record = tracker.conn.execute(
        "SELECT message_id FROM outreach WHERE email = ? AND stage = ?"
        " AND status = 'sent' ORDER BY created_at LIMIT 1",
        (email, STAGE_INITIAL),
    ).fetchone()
    return (record["message_id"] if record else "") or ""


# --------------------------------------------------------------- تنفيذ #
def execute(
    *,
    config: Config,
    tracker: Tracker,
    client: GmailClient,
    plan: Plan,
    mode: str,               # "send" أو "draft"
    limit: int | None,
    report: Reporter,
    stop_event: "threading.Event | None" = None,
) -> ExecutionResult:
    result = ExecutionResult()
    s = config.sending

    daily_limit = int(s.get("daily_limit", 40))
    already_today = tracker.sent_since(24)
    remaining = max(0, daily_limit - already_today) if mode == "send" else 10**6

    queue = plan.planned[: limit if limit else len(plan.planned)]
    if mode == "send" and len(queue) > remaining:
        report(
            f"[dim]الحد اليومي {daily_limit}؛ أُرسل {already_today} خلال 24 ساعة →"
            f" سنرسل {remaining} فقط الآن[/dim]"
        )
        queue = queue[:remaining]

    if not queue:
        result.stopped_reason = "لا توجد رسائل للإرسال"
        return result

    batch_size = int(s.get("batch_size", 10))
    batch_pause = int(s.get("batch_pause_seconds", 300))
    min_delay = int(s.get("min_delay_seconds", 45))
    max_delay = int(s.get("max_delay_seconds", 120))

    sender_email = config.sender["email"]
    reply_to = config.sender.get("reply_to") or ""

    for index, item in enumerate(queue, start=1):
        if stop_event is not None and stop_event.is_set():
            result.stopped_reason = (
                f"أُوقفت بطلب منك بعد {index - 1} من {len(queue)}"
            )
            break

        company = item.company
        label = f"[{index}/{len(queue)}] {company.company_name} <{company.email}>"

        attach_errors = check_attachments(config, item.attachments)
        if attach_errors:
            report(f"[red]✗[/red] {label} — {attach_errors[0]}")
            tracker.record(
                email=company.key,
                company_name=company.company_name,
                stage=item.stage,
                status="failed",
                subject=item.rendered.subject,
                error=attach_errors[0],
            )
            result.failed += 1
            continue

        try:
            msg = build_message(
                to=company.email,
                sender_email=sender_email,
                sender_name=config.display_name(company.language),
                subject=item.rendered.subject,
                text_body=item.rendered.text,
                html_body=item.rendered.html,
                attachments=item.attachments,
                reply_to=reply_to,
                in_reply_to=item.in_reply_to,
            )
            if mode == "draft":
                sent = client.create_draft(msg, thread_id=item.thread_id)
                status = "drafted"
            else:
                sent = client.send(msg, thread_id=item.thread_id)
                status = "sent"

            tracker.record(
                email=company.key,
                company_name=company.company_name,
                stage=item.stage,
                status=status,
                subject=item.rendered.subject,
                message_id=sent.rfc_message_id,
                thread_id=sent.thread_id,
            )
            result.succeeded += 1
            verb = "مسودة" if mode == "draft" else "أُرسلت"
            report(f"[green]✓[/green] {label} — {verb}")

        except GmailError as exc:
            tracker.record(
                email=company.key,
                company_name=company.company_name,
                stage=item.stage,
                status="failed",
                subject=item.rendered.subject,
                error=str(exc)[:500],
            )
            result.failed += 1
            report(f"[red]✗[/red] {label} — {exc}")

        # الإيقاع: لا انتظار بعد آخر رسالة، ولا انتظار عند إنشاء المسودات
        if mode == "send" and index < len(queue):
            if index % batch_size == 0:
                report(f"[dim]استراحة دفعة: {batch_pause} ثانية…[/dim]")
                interrupted = _pause(batch_pause, stop_event)
            else:
                delay = random.uniform(min_delay, max_delay)
                report(f"[dim]انتظار {delay:.0f} ثانية…[/dim]")
                interrupted = _pause(delay, stop_event)

            if interrupted:
                result.stopped_reason = f"أُوقفت بطلب منك بعد {index} من {len(queue)}"
                break

    return result
