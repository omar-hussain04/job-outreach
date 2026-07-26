#!/usr/bin/env python
"""
تطبيق سطح المكتب لنظام إرسال إيميلات التقديم.

يفتح نافذة أصلية (WebView2) تعرض الواجهة، ويستدعي نفس منطق src/
الذي تستخدمه الطرفية — لا تكرار للمنطق ولا سلوك مختلف بين الاثنين.

    شغّله بنقرة مزدوجة على «تشغيل النظام.vbs»
"""

from __future__ import annotations

import csv
import io
import re
import sys
import threading
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import webview
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.config import ConfigError, load_config  # noqa: E402
from src.contacts import (  # noqa: E402
    EMAIL_RE,
    Company,
    dedupe_rows,
    load_companies,
)
from src.dns_check import UNKNOWN  # noqa: E402
from src.dns_check import check_domains as dns_check_domains  # noqa: E402
from src.dns_check import domain_of  # noqa: E402
from src.gmail_client import GmailClient, GmailError  # noqa: E402
from src.lock import CampaignLock, LockBusy  # noqa: E402
from src.sender import (  # noqa: E402
    check_attachments,
    execute,
    plan_followup,
    plan_initial,
    resolve_attachments,
    sending_window_error,
)
from src.spam_check import analyze as spam_analyze  # noqa: E402
from src.templating import render  # noqa: E402
from src.tracker import STAGE_INITIAL, Tracker  # noqa: E402

CSV_COLUMNS = [
    "company_name", "email", "contact_name", "role_target",
    "language", "custom_note", "website", "attachments", "skip",
]

# نزع وسوم التلوين التي يفهمها rich ولا تفهمها الواجهة
RICH_TAG = re.compile(r"\[/?[a-z ]+\]")


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096          # يمنع كسر الأسطر الطويلة في منتصف الجملة العربية
    y.allow_unicode = True
    return y


def _fail(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc) or exc.__class__.__name__}


class Api:
    """كل ما تستدعيه الواجهة. كل دالة ترجع dict قابلاً للتحويل إلى JSON."""

    def __init__(self) -> None:
        self._log: deque[dict[str, str]] = deque(maxlen=600)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._summary: dict[str, Any] | None = None

    # ------------------------------------------------------------ أدوات #
    def _report(self, message: str) -> None:
        text = RICH_TAG.sub("", str(message)).strip()
        if not text:
            return
        kind = "ok" if text.startswith("✓") else "err" if text.startswith("✗") else "dim"
        with self._lock:
            self._log.append(
                {"time": datetime.now().strftime("%H:%M:%S"), "text": text, "kind": kind}
            )

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------ الحالة #
    def get_state(self) -> dict[str, Any]:
        try:
            cfg = load_config()
        except ConfigError as exc:
            return _fail(exc)

        problems: list[str] = []
        checks: list[dict[str, Any]] = []

        unfilled = cfg.unfilled_profile_fields()
        checks.append({
            "label": "الملف الشخصي",
            "ok": not unfilled,
            "detail": "مكتمل" if not unfilled else f"{len(unfilled)} حقل ناقص",
        })
        if unfilled:
            problems.append("الملف الشخصي غير مكتمل")

        result = load_companies(cfg.paths.companies_csv)
        active = [c for c in result.companies if not c.skip]
        checks.append({
            "label": "قائمة الشركات",
            "ok": bool(active) and not result.errors,
            "detail": f"{len(result.companies)} شركة، {len(active)} نشطة"
            if result.companies else "فارغة",
        })
        if not active or result.errors:
            problems.append("قائمة الشركات غير صالحة")

        attach_errors: list[str] = []
        seen: set[tuple[str, ...]] = set()
        for company in result.companies:
            paths = resolve_attachments(cfg, company)
            key = tuple(str(p) for p in paths)
            if key in seen:
                continue
            seen.add(key)
            attach_errors.extend(check_attachments(cfg, paths))
        checks.append({
            "label": "المرفقات",
            "ok": not attach_errors,
            "detail": "موجودة" if not attach_errors else attach_errors[0],
        })
        if attach_errors:
            problems.append("مرفق ناقص")

        has_token = cfg.paths.token.exists()
        checks.append({
            "label": "الاتصال بجيميل",
            "ok": has_token,
            "detail": "متصل" if has_token else "لم تسجّل الدخول بعد",
        })
        if not has_token:
            problems.append("لم تسجّل الدخول إلى جيميل")

        with Tracker(cfg.paths.database) as tracker:
            stats = tracker.stats()
            due = len(tracker.due_for_followup(int(cfg.followup.get("after_days", 7))))
            initial_plan = plan_initial(cfg, tracker, result.companies)
            followup_plan = plan_followup(cfg, tracker, result.companies)

        return {
            "ok": True,
            "ready": not problems,
            "checks": checks,
            "problems": problems,
            "warnings": result.warnings + result.errors,
            "stats": {
                **stats,
                "total": len(result.companies),
                "active": len(active),
                "remaining": len(initial_plan.planned),
                "followup_ready": len(followup_plan.planned),
                "followup_due": due,
            },
            "window_error": sending_window_error(cfg),
            "daily_limit": int(cfg.sending.get("daily_limit", 40)),
            "attachments": [p.name for p in cfg.default_attachments()],
            "running": self.running,
            "busy_elsewhere": self._busy_elsewhere(cfg),
            "actions": self._next_actions(cfg, result.companies, stats,
                                          initial_plan, followup_plan),
            "owner": (cfg.profile.get("full_name_ar")
                      or cfg.profile.get("full_name_en") or ""),
        }

    def _next_actions(self, cfg, companies, stats, initial_plan,
                      followup_plan) -> list[dict[str, Any]]:
        """يقترح الخطوة التالية بترتيب الأولوية بدل أن يترك المستخدم يخمّن."""
        items: list[dict[str, Any]] = []

        if stats["replies"]:
            items.append({
                "kind": "good", "title": f"{stats['replies']} شركة ردّت عليك",
                "body": "افتح الردود وتابعها بنفسك — النظام لن يراسلها مجدداً.",
                "go": "replies", "label": "اعرض الردود",
            })

        if stats["drafted"]:
            items.append({
                "kind": "warn", "title": f"{stats['drafted']} مسودة تنتظر قرارك",
                "body": "أنشأها النظام للمراجعة ولم تُرسل. أرسلها من جيميل ثم "
                        "زامن — وسيتحدّث وضعها هنا إلى «أُرسلت يدوياً».",
                "go": "replies", "label": "زامن مع جيميل",
            })

        if stats.get("bounced"):
            items.append({
                "kind": "warn",
                "title": f"{stats['bounced']} رسالة ارتدّت — العنوان غير صالح",
                "body": "هذه العناوين ميتة ولن يصلها شيء. ابحث عن الإيميل الصحيح "
                        "من موقع الشركة وعدّله — التصحيح يجعلها مؤهلة للإرسال من جديد.",
                "go": "companies", "filter": "bounced", "label": "صحّح العناوين",
            })

        skipped = [c for c in companies if c.skip]
        if skipped:
            items.append({
                "kind": "warn", "title": f"{len(skipped)} شركة متجاوَزة",
                "body": "غالباً بسبب نطاق خاطئ أو اسم غير معروف. صحّحها "
                        "لتدخل الحملة، أو اتركها متجاوَزة.",
                "go": "companies", "filter": "skip", "label": "راجعها",
            })

        if followup_plan.planned:
            items.append({
                "kind": "info",
                "title": f"{len(followup_plan.planned)} شركة تستحق متابعة",
                "body": f"مضى {cfg.followup.get('after_days', 7)} أيام على رسالتها "
                        "الأولى ولم تردّ.",
                "go": "send", "stage": "followup", "label": "أرسل المتابعة",
            })

        pending = [p.company for p in initial_plan.planned]
        no_note = [c for c in pending if not c.custom_note]
        if no_note:
            items.append({
                "kind": "info", "title": f"{len(no_note)} شركة بلا ملاحظة مخصصة",
                "body": "سطر واحد يخص الشركة هو أقوى ما يميّز رسالتك عن عشرات "
                        "الرسائل المتطابقة التي تصلها.",
                "go": "companies", "filter": "nonote", "label": "أضف ملاحظات",
            })

        if pending:
            items.append({
                "kind": "primary", "title": f"{len(pending)} شركة لم تراسلها بعد",
                "body": "ابدأ دفعة جديدة — النظام يرسل ببطء ولن يكرر أي شركة.",
                "go": "send", "label": "ابدأ الإرسال",
            })

        return items

    def _busy_elsewhere(self, cfg) -> str:
        """يرجع وصف الحملة العاملة في عملية أخرى، أو نصاً فارغاً."""
        if self.running:
            return ""

        info = CampaignLock(cfg.paths.database.with_name("campaign.lock")).read()
        if info is not None and not info.stale:
            return f"حملة تعمل الآن من {info.source} (المعرّف {info.pid})"

        # شبكة أمان: حملة بدأت قبل وجود القفل، أو حُذف ملف القفل بالخطأ.
        # أقصى فاصل بين رسالتين دقيقتان، فوصول رسالة خلال أربع يعني نشاطاً جارياً.
        with Tracker(cfg.paths.database) as tracker:
            row = tracker.conn.execute(
                "SELECT created_at FROM outreach WHERE status = 'sent'"
                " ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return ""
        try:
            last = datetime.fromisoformat(row["created_at"])
        except ValueError:
            return ""
        age = (datetime.now(last.tzinfo) - last).total_seconds()
        if age < 240:
            return f"يبدو أن حملة تعمل الآن (آخر رسالة قبل {age:.0f} ثانية)"
        return ""

    def get_history(self, limit: int = 60) -> dict[str, Any]:
        try:
            cfg = load_config()
            with Tracker(cfg.paths.database) as tracker:
                rows = [
                    {
                        "company": r["company_name"],
                        "email": r["email"],
                        "stage": r["stage"],
                        "status": r["status"],
                        "subject": r["subject"] or "",
                        "error": r["error"] or "",
                        "at": (r["created_at"] or "")[:16].replace("T", " "),
                    }
                    for r in tracker.history(limit)
                ]
            return {"ok": True, "rows": rows}
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ---------------------------------------------------------- الشركات #
    def list_companies(self) -> dict[str, Any]:
        try:
            cfg = load_config()
            result = load_companies(cfg.paths.companies_csv)
            with Tracker(cfg.paths.database) as tracker:
                rows = []
                for c in result.companies:
                    initial = tracker.get_initial(c.key)
                    # was_sent يشمل المسودات والمرتدّات: كلاهما يحجب الإرسال،
                    # فيجب أن يعرف المستخدم ذلك بدل أن يفاجأ برفض الإرسال.
                    contacted = tracker.was_sent(c.key, STAGE_INITIAL)
                    bounced = tracker.has_bounced(c.key)
                    rows.append({
                        "company_name": c.company_name,
                        "email": c.email,
                        "contact_name": c.contact_name,
                        "role_target": c.role_target,
                        "language": c.language,
                        "custom_note": c.custom_note,
                        "website": c.website,
                        "attachments": ";".join(c.attachments),
                        "skip": c.skip,
                        "sent": initial is not None,
                        "sent_at": initial.created_at[:10] if initial else "",
                        "drafted": contacted and initial is None and not bounced,
                        "bounced": bounced,
                        "blocked": contacted,
                        "replied": tracker.has_replied(c.key),
                    })
            return {"ok": True, "rows": rows,
                    "warnings": result.warnings, "errors": result.errors}
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    def save_companies(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            cfg = load_config()
            path = cfg.paths.companies_csv

            # التكرارات تُزال تلقائياً: نُبقي النسخة الأغنى بيانات لكل إيميل
            deduped, removed = dedupe_rows(rows)

            clean: list[dict[str, Any]] = []
            for row in deduped:
                email = (row.get("email") or "").strip()
                name = (row.get("company_name") or "").strip()
                if not email and not name:
                    continue
                if not name:
                    return {"ok": False, "error": f"اسم الشركة فارغ للإيميل {email}"}
                if not EMAIL_RE.match(email):
                    return {"ok": False, "error": f"صيغة إيميل غير صحيحة: {email}"}
                clean.append(row)

            # نكتب لملف مؤقت ثم نستبدل، حتى لا تفسد القائمة لو انقطع الحفظ
            tmp = path.with_suffix(".csv.tmp")
            with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(CSV_COLUMNS)
                for row in clean:
                    writer.writerow([
                        row.get("company_name", "").strip(),
                        row.get("email", "").strip(),
                        row.get("contact_name", "").strip(),
                        row.get("role_target", "").strip(),
                        (row.get("language") or "en").strip(),
                        row.get("custom_note", "").strip(),
                        row.get("website", "").strip(),
                        row.get("attachments", "").strip(),
                        "1" if row.get("skip") else "",
                    ])
            tmp.replace(path)
            return {
                "ok": True,
                "count": len(clean),
                "deduped": len(removed),
                "removed": [
                    {"company": str(r.get("company_name") or ""),
                     "email": str(r.get("email") or "")}
                    for r in removed
                ],
            }
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    def import_emails(self, text: str, language: str = "en") -> dict[str, Any]:
        try:
            cfg = load_config()
            existing = {c.key for c in load_companies(cfg.paths.companies_csv).companies}
            added, duplicates, unparsed = [], 0, 0

            for line in (text or "").splitlines():
                line = line.strip().strip(",;")
                if not line:
                    continue
                match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", line)
                if not match:
                    unparsed += 1
                    continue
                email = match.group(0)
                if email.lower() in existing:
                    duplicates += 1
                    continue
                existing.add(email.lower())
                name = re.sub(r"^\d+\s*[-.)]\s*", "", line.replace(email, "")).strip(" ,;<>\t-")
                if not name:
                    name = email.split("@")[1].split(".")[0].title()
                added.append({
                    "company_name": name, "email": email, "contact_name": "",
                    "role_target": "", "language": language, "custom_note": "",
                    "website": "", "attachments": "", "skip": False,
                })
            return {"ok": True, "added": added,
                    "duplicates": duplicates, "unparsed": unparsed}
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    def check_domains(self) -> dict[str, Any]:
        """يفحص سجلات MX لكل نطاقات القائمة — يكشف ما سيرتد قبل الإرسال."""
        try:
            cfg = load_config()
            companies = load_companies(cfg.paths.companies_csv).companies
            results = dns_check_domains([domain_of(c.email) for c in companies])

            rows, dead = [], 0
            for c in companies:
                res = results.get(domain_of(c.email))
                if res is None:
                    continue
                if res.will_bounce:
                    dead += 1
                rows.append({
                    "email": c.email,
                    "company": c.company_name,
                    "domain": res.domain,
                    "state": res.state,
                    "detail": res.detail,
                    "skip": c.skip,
                })
            return {
                "ok": True,
                "rows": rows,
                "checked": len(results),
                "dead": dead,
                "unknown": sum(1 for r in results.values() if r.state == UNKNOWN),
            }
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    def skip_dead_domains(self) -> dict[str, Any]:
        """يعلّم كل صف نطاقه ميت بـ skip — لا يحذف، فقد تصحّح العنوان لاحقاً."""
        try:
            cfg = load_config()
            result = load_companies(cfg.paths.companies_csv)
            checked = dns_check_domains(
                [domain_of(c.email) for c in result.companies])

            rows, marked = [], []
            for c in result.companies:
                res = checked.get(domain_of(c.email))
                dead = res is not None and res.will_bounce
                if dead and not c.skip:
                    marked.append(c.company_name)
                rows.append({
                    "company_name": c.company_name, "email": c.email,
                    "contact_name": c.contact_name, "role_target": c.role_target,
                    "language": c.language, "custom_note": c.custom_note,
                    "website": c.website, "attachments": ";".join(c.attachments),
                    "skip": c.skip or dead,
                })
            saved = self.save_companies(rows)
            if not saved.get("ok"):
                return saved
            return {"ok": True, "marked": len(marked), "names": marked[:12]}
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # --------------------------------------------------------- المعاينة #
    def preview_row(self, row: dict[str, Any],
                    stage: str = STAGE_INITIAL) -> dict[str, Any]:
        """يعاين من بيانات لم تُحفظ بعد — ليرى أثر الملاحظة المخصصة فوراً."""
        try:
            cfg = load_config()
            language = (row.get("language") or "en").strip().lower()
            if language not in {"ar", "en", "both"}:
                language = "en"
            default_role = {
                "ar": "مطوّر برمجيات",
                "en": "Software Developer",
                "both": "مطوّر برمجيات | Software Developer",
            }[language]
            company = Company(
                row_number=0,
                company_name=(row.get("company_name") or "").strip() or "—",
                email=(row.get("email") or "").strip() or "sample@example.com",
                contact_name=(row.get("contact_name") or "").strip(),
                role_target=(row.get("role_target") or "").strip() or default_role,
                language=language,
                custom_note=(row.get("custom_note") or "").strip(),
                attachments=[a for a in (row.get("attachments") or "").split(";") if a],
            )
            attachments = resolve_attachments(cfg, company)
            rendered = render(
                cfg.paths.templates_dir, stage, company, cfg.profile,
                [p.name for p in attachments],
                extra={"initial_sent_date": datetime.now().strftime("%Y-%m-%d")},
            )
            return {"ok": True, "subject": rendered.subject, "html": rendered.html}
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    def preview(self, email: str, stage: str = STAGE_INITIAL) -> dict[str, Any]:
        try:
            cfg = load_config()
            companies = load_companies(cfg.paths.companies_csv).companies
            company = next((c for c in companies if c.key == (email or "").lower()), None)
            if company is None:
                company = companies[0] if companies else Company(
                    0, "شركة تجريبية", "sample@example.com", language="ar")

            attachments = resolve_attachments(cfg, company)
            rendered = render(
                cfg.paths.templates_dir, stage, company, cfg.profile,
                [p.name for p in attachments],
                extra={"initial_sent_date": datetime.now().strftime("%Y-%m-%d")},
            )
            return {
                "ok": True,
                "to": company.email,
                "company": company.company_name,
                "language": company.language,
                "subject": rendered.subject,
                "html": rendered.html,
                "text": rendered.text,
                "attachments": [
                    {"name": p.name,
                     "kb": round(p.stat().st_size / 1024) if p.exists() else 0,
                     "missing": not p.exists()}
                    for p in attachments
                ],
            }
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------- الإعدادات #
    def get_profile(self) -> dict[str, Any]:
        try:
            cfg = load_config()
            return {"ok": True, "profile": cfg.profile,
                    "sending": cfg.sending, "followup": cfg.followup,
                    "sender": cfg.sender}
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    def save_profile(self, values: dict[str, Any]) -> dict[str, Any]:
        """يحدّث حقول profile.yaml مع الحفاظ على تعليقاته وترتيبه."""
        try:
            path = ROOT / "config" / "profile.yaml"
            yaml = _yaml()
            with path.open(encoding="utf-8") as fh:
                doc = yaml.load(fh)
            for key, value in (values or {}).items():
                if key in doc:
                    doc[key] = value
            buf = io.StringIO()
            yaml.dump(doc, buf)
            path.write_text(buf.getvalue(), encoding="utf-8")
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    def save_settings(self, sending: dict[str, Any],
                      followup: dict[str, Any]) -> dict[str, Any]:
        try:
            path = ROOT / "config" / "settings.yaml"
            yaml = _yaml()
            with path.open(encoding="utf-8") as fh:
                doc = yaml.load(fh)
            for key, value in (sending or {}).items():
                if key in doc.get("sending", {}):
                    doc["sending"][key] = value
            for key, value in (followup or {}).items():
                if key in doc.get("followup", {}):
                    doc["followup"][key] = value
            buf = io.StringIO()
            yaml.dump(doc, buf)
            path.write_text(buf.getvalue(), encoding="utf-8")
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ---------------------------------------------------------- جيميل #
    def authenticate(self) -> dict[str, Any]:
        try:
            cfg = load_config()
            client = GmailClient(cfg.paths.credentials, cfg.paths.token)
            email = client.authenticate(force=True)
            return {"ok": True, "email": email,
                    "matches": email.lower() == cfg.sender["email"].lower()}
        except (GmailError, ConfigError) as exc:
            return _fail(exc)
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    def check_replies(self) -> dict[str, Any]:
        """مزامنة مع جيميل: يكشف الردود، ويحسم مصير المسودات المُرسلة يدوياً."""
        try:
            cfg = load_config()
            with Tracker(cfg.paths.database) as tracker:
                threads = tracker.all_sent_threads()
                drafts = tracker.drafted_rows()
                if not threads and not drafts:
                    return {"ok": True, "replies": [], "new": 0, "drafts": [],
                            "note": "لا توجد رسائل مُرسلة ولا مسودات لفحصها بعد"}

                client = GmailClient(cfg.paths.credentials, cfg.paths.token)
                client.authenticate()
                names = {c.key: c.company_name
                         for c in load_companies(cfg.paths.companies_csv).companies}

                # 1) مصير المسودات — قبل الردود، حتى تدخل المُرسلة يدوياً
                #    ضمن فحص الردود في نفس المزامنة القادمة
                draft_updates: list[dict[str, str]] = []
                if drafts:
                    outcomes = client.draft_outcomes(
                        [(d.email, d.thread_id) for d in drafts]
                    )
                    by_email = {d.email: d for d in drafts}
                    for o in outcomes:
                        record = by_email[o.email]
                        company = names.get(o.email, record.company_name)
                        if o.state == "sent":
                            sent_iso = (
                                datetime.fromtimestamp(
                                    o.sent_at_ms / 1000, timezone.utc
                                ).isoformat(timespec="seconds")
                                if o.sent_at_ms else record.created_at
                            )
                            tracker.resolve_draft_sent(
                                o.email, record.stage, o.rfc_message_id, sent_iso)
                            draft_updates.append(
                                {"company": company, "state": "sent",
                                 "at": sent_iso[:16].replace("T", " ")})
                        elif o.state == "deleted":
                            tracker.resolve_draft_deleted(o.email, record.stage)
                            draft_updates.append(
                                {"company": company, "state": "deleted", "at": ""})
                        else:
                            draft_updates.append(
                                {"company": company, "state": "waiting", "at": ""})

                # 2) الردود — مع فرز رسائل الارتداد عن الردود البشرية
                found = client.find_replies(cfg.sender["email"], threads)
                real, bounces, new = [], [], 0
                for r in found:
                    if r.is_bounce:
                        tracker.mark_bounced(r.email)
                        bounces.append(
                            {"email": r.email,
                             "company": names.get(r.email, r.email),
                             "snippet": r.snippet})
                    else:
                        if tracker.record_reply(r.email, r.thread_id, r.snippet):
                            new += 1
                        real.append(
                            {"email": r.email,
                             "company": names.get(r.email, r.email),
                             "snippet": r.snippet})
            return {
                "ok": True, "new": new,
                "replies": real,
                "bounces": bounces,
                "drafts": draft_updates,
            }
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ---------------------------------------------------------- الحملة #
    def preflight(self, stage: str, mode: str, limit: int | None,
                  ignore_schedule: bool) -> dict[str, Any]:
        """يحسب ما سيُرسل فعلاً قبل أن يؤكد المستخدم."""
        try:
            cfg = load_config()
            companies = load_companies(cfg.paths.companies_csv).companies
            with Tracker(cfg.paths.database) as tracker:
                plan = (plan_initial(cfg, tracker, companies) if stage == STAGE_INITIAL
                        else plan_followup(cfg, tracker, companies))
                already = tracker.sent_since(24)

            total = len(plan.planned)
            if limit:
                total = min(total, limit)
            if mode == "send":
                remaining = max(0, int(cfg.sending.get("daily_limit", 40)) - already)
                capped = min(total, remaining)
            else:
                capped = total

            window = "" if (ignore_schedule or mode == "draft") else sending_window_error(cfg)
            minutes = 0
            if mode == "send" and capped > 1:
                avg = (int(cfg.sending.get("min_delay_seconds", 45))
                       + int(cfg.sending.get("max_delay_seconds", 120))) / 2
                batch = int(cfg.sending.get("batch_size", 10))
                pauses = (capped - 1) // batch
                minutes = round(((capped - 1 - pauses) * avg
                                 + pauses * int(cfg.sending.get("batch_pause_seconds", 300))) / 60)

            with Tracker(cfg.paths.database) as tracker:
                risks = spam_analyze(cfg, tracker, plan.planned[:capped])

            return {
                "ok": True, "planned": len(plan.planned), "will_send": capped,
                "skipped": [{"company": s.company_name, "email": s.email,
                             "reason": s.reason} for s in plan.skipped],
                "targets": [{"company": p.company.company_name,
                             "email": p.company.email,
                             "language": p.company.language}
                            for p in plan.planned[:capped]],
                "window_error": window,
                "sent_last_24h": already,
                "eta_minutes": minutes,
                "busy_elsewhere": self._busy_elsewhere(cfg),
                "risks": risks,
            }
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    def start_campaign(self, stage: str, mode: str, limit: int | None,
                       ignore_schedule: bool, only: str = "") -> dict[str, Any]:
        """يبدأ حملة. `only` يقصرها على شركة واحدة بعينها."""
        if self.running:
            return {"ok": False, "error": "هناك حملة تعمل بالفعل"}

        with self._lock:
            self._log.clear()
        self._summary = None
        self._stop.clear()

        def worker() -> None:
            lock: CampaignLock | None = None
            try:
                cfg = load_config()
                companies = load_companies(cfg.paths.companies_csv).companies

                if mode == "send" and not ignore_schedule:
                    window = sending_window_error(cfg)
                    if window:
                        self._report(f"✗ {window}")
                        self._summary = {"succeeded": 0, "failed": 0,
                                         "stopped_reason": window}
                        return

                # يمنع أن يرسل التطبيق والطرفية لنفس الشركة في آنٍ واحد
                lock = CampaignLock(
                    cfg.paths.database.with_name("campaign.lock"), "التطبيق")
                lock.acquire()

                client = GmailClient(cfg.paths.credentials, cfg.paths.token)
                client.authenticate()

                def report(message: str) -> None:
                    lock.touch()
                    self._report(message)

                with Tracker(cfg.paths.database) as tracker:
                    plan = (plan_initial(cfg, tracker, companies)
                            if stage == STAGE_INITIAL
                            else plan_followup(cfg, tracker, companies))
                    if only:
                        plan.planned = [p for p in plan.planned
                                        if p.company.key == only.lower()]
                        if not plan.planned:
                            self._report("✗ هذه الشركة غير مؤهلة للإرسال الآن")
                            self._summary = {"succeeded": 0, "failed": 0,
                                             "stopped_reason": "غير مؤهلة"}
                            return
                    result = execute(
                        config=cfg, tracker=tracker, client=client, plan=plan,
                        mode=mode, limit=limit, report=report,
                        stop_event=self._stop,
                    )
                self._summary = {
                    "succeeded": result.succeeded, "failed": result.failed,
                    "stopped_reason": result.stopped_reason,
                }
            except LockBusy as exc:
                self._report(f"✗ {exc}")
                self._summary = {"succeeded": 0, "failed": 0,
                                 "stopped_reason": str(exc)}
            except Exception as exc:  # noqa: BLE001
                self._report(f"✗ {exc}")
                traceback.print_exc()
                self._summary = {"succeeded": 0, "failed": 0,
                                 "stopped_reason": str(exc)}
            finally:
                if lock is not None:
                    lock.release()

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        return {"ok": True}

    def stop_campaign(self) -> dict[str, Any]:
        self._stop.set()
        self._report("… طلب الإيقاف — ستتوقف بعد الرسالة الحالية")
        return {"ok": True}

    def poll(self) -> dict[str, Any]:
        with self._lock:
            lines = list(self._log)
        return {"ok": True, "lines": lines,
                "running": self.running, "summary": self._summary}

    # ----------------------------------------------------------- ملفات #
    def open_path(self, which: str) -> dict[str, Any]:
        import os
        targets = {
            "attachments": ROOT / "attachments",
            "config": ROOT / "config",
            "data": ROOT / "data",
            "root": ROOT,
        }
        target = targets.get(which, ROOT)
        try:
            os.startfile(str(target))  # noqa: S606 — فتح مجلد في مستكشف ويندوز
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)


def _report_crash(exc: BaseException) -> None:
    """يعرض الخطأ في نافذة ويحفظه في ملف.

    التطبيق يعمل بـ pythonw بلا نافذة طرفية، فلو انهار عند الإقلاع لما رأى
    المستخدم شيئاً على الإطلاق — لذلك نلتقط الخطأ ونعرضه صراحةً.
    """
    detail = "".join(traceback.format_exception(exc))
    log = ROOT / "data" / "app_error.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            f"{datetime.now().isoformat(timespec='seconds')}\n{detail}",
            encoding="utf-8",
        )
    except OSError:
        pass

    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            f"تعذّر تشغيل التطبيق:\n\n{exc}\n\nالتفاصيل الكاملة في:\n{log}",
            "نظام إرسال إيميلات التقديم",
            0x10,
        )
    except Exception:  # noqa: BLE001 — لا شيء أكثر يمكن فعله هنا
        sys.stderr.write(detail)


def main() -> None:
    try:
        load_config()          # يفشل مبكراً برسالة مفهومة لو كانت الإعدادات ناقصة
        api = Api()
        webview.create_window(
            "نظام إرسال إيميلات التقديم",
            str(ROOT / "ui" / "index.html"),
            js_api=api,
            width=1180,
            height=820,
            min_size=(900, 640),
            text_select=True,
        )
        webview.start()
    except BaseException as exc:  # noqa: BLE001 — نلتقط كل شيء لنعرضه للمستخدم
        _report_crash(exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
