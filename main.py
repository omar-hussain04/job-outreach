#!/usr/bin/env python
"""
نظام إرسال إيميلات التقديم على الوظائف وفرص التدريب.

    python main.py --help          لعرض كل الأوامر
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.config import ConfigError, enable_utf8_console, load_config
from src.contacts import dedupe_rows, load_companies, write_template_csv
from src.gmail_client import GmailClient, GmailError
from src.lock import CampaignLock, LockBusy
from src.sender import (
    check_attachments,
    execute,
    plan_followup,
    plan_initial,
    resolve_attachments,
    sending_window_error,
)
from src.spam_check import analyze as spam_analyze
from src.templating import render
from src.tracker import STAGE_INITIAL, Tracker

console = Console()
ROOT = Path(__file__).resolve().parent


# ================================================================ أدوات #
def _load():
    try:
        return load_config()
    except ConfigError as exc:
        console.print(f"[red]خطأ في الإعدادات:[/red] {exc}")
        sys.exit(1)


def _load_companies_or_exit(config):
    result = load_companies(config.paths.companies_csv)
    for warning in result.warnings:
        console.print(f"[yellow]تنبيه:[/yellow] {warning}")
    if result.errors:
        for error in result.errors:
            console.print(f"[red]خطأ:[/red] {error}")
        sys.exit(1)
    return result.companies


def _client(config) -> GmailClient:
    return GmailClient(config.paths.credentials, config.paths.token)


def _print_plan(plan, title: str) -> None:
    if plan.skipped:
        table = Table(title="مُستثناة", show_header=True, header_style="dim")
        table.add_column("الشركة")
        table.add_column("السبب", style="dim")
        for s in plan.skipped[:20]:
            table.add_row(s.company_name, s.reason)
        if len(plan.skipped) > 20:
            table.add_row("…", f"و{len(plan.skipped) - 20} أخرى")
        console.print(table)

    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("#", width=4)
    table.add_column("الشركة")
    table.add_column("الإيميل")
    table.add_column("اللغة", width=6)
    for i, item in enumerate(plan.planned, start=1):
        table.add_row(
            str(i), item.company.company_name, item.company.email, item.company.language
        )
    console.print(table)


# ================================================================ أوامر #
def cmd_init(args) -> None:
    """تهيئة أول مرة: ينسخ ملفات الإعدادات المثال وينشئ المجلدات."""
    import shutil

    # 1) ملفات الإعدادات من نسخ .example — قبل تحميل الإعدادات نفسها
    for name in ("settings", "profile"):
        target = ROOT / "config" / f"{name}.yaml"
        example = ROOT / "config" / f"{name}.example.yaml"
        if target.exists() and not args.force:
            console.print(f"[dim]•[/dim] config/{name}.yaml موجود — لم يُلمس")
        elif example.exists():
            shutil.copyfile(example, target)
            console.print(f"[green]✓[/green] أُنشئ config/{name}.yaml")
        else:
            console.print(f"[red]✗[/red] {example.name} مفقود من المستودع")

    config = _load()
    csv_path = config.paths.companies_csv
    if csv_path.exists() and not args.force:
        console.print(f"[dim]•[/dim] {csv_path.name} موجود — لم يُلمس")
    else:
        write_template_csv(csv_path)
        console.print(f"[green]✓[/green] أُنشئ {csv_path.name}")

    for folder in (ROOT / "attachments", config.paths.previews_dir):
        folder.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] المجلد جاهز: {folder.name}/")

    console.print(
        Panel(
            "الخطوات التالية:\n"
            "1. ضع سيرتك الذاتية في [bold]attachments/CV.pdf[/bold]\n"
            "2. املأ [bold]config/profile.yaml[/bold] (كل حقول TODO)\n"
            "3. اضبط اسمك وإيميلك في [bold]config/settings.yaml[/bold]\n"
            "4. أعدّ Gmail API وشغّل [bold]python main.py auth[/bold] — انظر README\n"
            "5. أضف الشركات ثم شغّل [bold]python main.py validate[/bold]",
            title="تمت التهيئة",
            border_style="green",
        )
    )


def cmd_import(args) -> None:
    """يستورد إيميلات من ملف نصي عادي (إيميل في كل سطر) إلى companies.csv."""
    import csv as csv_module

    config = _load()
    source = Path(args.file)
    if not source.exists():
        console.print(f"[red]الملف غير موجود:[/red] {source}")
        sys.exit(1)

    existing: set[str] = set()
    csv_path = config.paths.companies_csv
    if csv_path.exists():
        existing = {c.key for c in load_companies(csv_path).companies}
    else:
        write_template_csv(csv_path)

    added, duplicates, unparsed = 0, 0, 0
    rows: list[list[str]] = []

    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip().strip(",;")
        if not line:
            continue
        # يقبل: "إيميل" أو "اسم الشركة, إيميل" أو "اسم <إيميل>"
        match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", line)
        if not match:
            unparsed += 1
            continue
        email = match.group(0)
        if email.lower() in existing:
            duplicates += 1
            continue
        existing.add(email.lower())

        name = line.replace(email, "").strip(" ,;<>\t-")
        if not name:
            name = email.split("@")[1].split(".")[0].title()

        rows.append([name, email, "", "", args.language, "", "", "", ""])
        added += 1

    with csv_path.open("a", encoding="utf-8-sig", newline="") as fh:
        csv_module.writer(fh).writerows(rows)

    console.print(f"[green]✓[/green] أُضيفت {added} شركة إلى {csv_path.name}")
    if duplicates:
        console.print(f"[yellow]تم تجاهل {duplicates} إيميل مكرر[/yellow]")
    if unparsed:
        console.print(f"[yellow]تعذّرت قراءة {unparsed} سطر[/yellow]")


def cmd_dedupe(args) -> None:
    """يزيل الإيميلات المكررة من ملف الشركات، محتفظاً بالنسخة الأغنى بيانات."""
    import csv as csv_module

    config = _load()
    path = config.paths.companies_csv
    if not path.exists():
        console.print("[yellow]ملف الشركات غير موجود.[/yellow]")
        return

    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv_module.DictReader(fh)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    clean, removed = dedupe_rows(rows)
    if not removed:
        console.print(f"[green]✓[/green] لا تكرارات — {len(clean)} شركة، القائمة نظيفة.")
        return

    if args.dry_run:
        for r in removed:
            console.print(
                f"[yellow]-[/yellow] {r.get('company_name', '')} <{r.get('email', '')}>"
            )
        console.print(f"[cyan]تشغيل تجريبي — سيُزال {len(removed)} مكرر.[/cyan]")
        return

    # كتابة عبر ملف مؤقت ثم استبدال، حتى لا تفسد القائمة لو انقطع الحفظ
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(clean)
    tmp.replace(path)

    for r in removed:
        console.print(
            f"[yellow]-[/yellow] {r.get('company_name', '')} <{r.get('email', '')}>"
        )
    console.print(f"[green]✓[/green] أُزيل {len(removed)} مكرر — بقيت {len(clean)} شركة.")


def cmd_check_domains(args) -> None:
    """يفحص سجلات MX: أي نطاق بلا خادم بريد سيرتدّ حتماً."""
    import csv as csv_module

    from src.dns_check import check_domains, domain_of

    config = _load()
    companies = _load_companies_or_exit(config)
    console.print(f"جارٍ فحص نطاقات {len(companies)} شركة…")
    results = check_domains([domain_of(c.email) for c in companies])

    dead, unknown = [], 0
    for company in companies:
        res = results.get(domain_of(company.email))
        if res is None:
            continue
        if res.will_bounce:
            dead.append((company, res))
        elif res.state == "unknown":
            unknown += 1

    if not dead:
        console.print(f"[green]✓[/green] كل النطاقات ({len(results)}) تستقبل بريداً.")
        return

    table = Table(title=f"{len(dead)} عنوان سيرتدّ حتماً", header_style="bold")
    table.add_column("الشركة")
    table.add_column("الإيميل")
    table.add_column("السبب", style="dim")
    for company, res in dead:
        table.add_row(company.company_name, company.email, res.detail)
    console.print(table)
    if unknown:
        console.print(f"[dim]{unknown} نطاق تعذّر فحصه — لم يُحكم عليه.[/dim]")

    if not args.skip:
        console.print("\nاستخدم [bold]--skip[/bold] لتعليمها بالتجاوز تلقائياً.")
        return

    path = config.paths.companies_csv
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv_module.DictReader(fh)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    doomed = {c.key for c, _ in dead}
    marked = 0
    for row in rows:
        if (row.get("email") or "").strip().lower() in doomed and not row.get("skip"):
            row["skip"] = "1"
            marked += 1

    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)
    console.print(f"[green]✓[/green] عُلّم {marked} عنواناً بالتجاوز.")


def cmd_auth(args) -> None:
    config = _load()
    console.print("سيُفتح المتصفح لتسجيل الدخول إلى جوجل…")
    try:
        email = _client(config).authenticate(force=args.force)
    except GmailError as exc:
        console.print(f"[red]فشلت المصادقة:[/red] {exc}")
        sys.exit(1)

    console.print(f"[green]✓[/green] تمت المصادقة كـ [bold]{email}[/bold]")
    if email.lower() != config.sender["email"].lower():
        console.print(
            f"[yellow]تنبيه:[/yellow] الحساب المصادَق عليه ({email}) يختلف عن "
            f"sender.email في settings.yaml ({config.sender['email']})"
        )


def cmd_validate(args) -> None:
    config = _load()
    problems: list[str] = []
    ok: list[str] = []

    # 1) الملف الشخصي
    unfilled = config.unfilled_profile_fields()
    if unfilled:
        problems.append(
            f"{len(unfilled)} حقل في profile.yaml ما زال TODO: "
            + ", ".join(unfilled[:6])
            + ("…" if len(unfilled) > 6 else "")
        )
    else:
        ok.append("الملف الشخصي مكتمل")

    # 2) الشركات
    result = load_companies(config.paths.companies_csv)
    problems.extend(result.errors)
    for w in result.warnings:
        console.print(f"[yellow]تنبيه:[/yellow] {w}")
    active = [c for c in result.companies if not c.skip]
    if result.companies:
        ok.append(f"{len(result.companies)} شركة في CSV ({len(active)} نشطة)")
    else:
        problems.append(
            f"{config.paths.companies_csv.name} فارغ — أضف الشركات أو استورد قائمة "
            "عبر: python main.py import emails.txt"
        )

    # 3) المرفقات — نفحص كل مجموعة مرفقات مستخدمة مرة واحدة
    checked: set[tuple[str, ...]] = set()
    for company in result.companies:
        paths = resolve_attachments(config, company)
        key = tuple(str(p) for p in paths)
        if key in checked:
            continue
        checked.add(key)
        problems.extend(check_attachments(config, paths))
    if result.companies and not any("المرفق" in p for p in problems):
        ok.append("المرفقات موجودة وحجمها ضمن الحد")

    # 4) القوالب — نجرّب رندر فعلي لكل لغة مستخدمة
    for language in sorted({c.language for c in result.companies}):
        sample = next(c for c in result.companies if c.language == language)
        for stage in (STAGE_INITIAL, "followup"):
            try:
                render(
                    config.paths.templates_dir,
                    stage,
                    sample,
                    config.profile,
                    [p.name for p in resolve_attachments(config, sample)],
                    extra={"initial_sent_date": "2026-01-01"},
                )
                ok.append(f"القالب {language}/{stage} يعمل")
            except Exception as exc:  # noqa: BLE001 — أي خطأ رندر يجب أن يظهر للمستخدم
                problems.append(f"القالب {language}/{stage}: {exc}")

    # 5) المصادقة
    if config.paths.token.exists():
        ok.append("توكن جوجل موجود")
    else:
        problems.append("لم تتم المصادقة بعد — شغّل: python main.py auth")

    for line in ok:
        console.print(f"[green]✓[/green] {line}")
    for line in problems:
        console.print(f"[red]✗[/red] {line}")

    if problems:
        console.print("\n[red]النظام غير جاهز للإرسال.[/red]")
        sys.exit(1)
    console.print("\n[green bold]النظام جاهز للإرسال.[/green bold]")


def cmd_preview(args) -> None:
    config = _load()
    companies = _load_companies_or_exit(config)
    if args.company:
        needle = args.company.lower()
        companies = [
            c
            for c in companies
            if needle in c.company_name.lower() or needle in c.email.lower()
        ]
        if not companies:
            console.print(f"[red]لا توجد شركة مطابقة لـ:[/red] {args.company}")
            sys.exit(1)

    out_dir = config.paths.previews_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = args.stage

    count = 0
    for company in companies[: args.limit]:
        rendered = render(
            config.paths.templates_dir,
            stage,
            company,
            config.profile,
            [p.name for p in resolve_attachments(config, company)],
            extra={"initial_sent_date": "2026-01-01"},
        )
        safe = re.sub(r"[^\w\-]+", "_", company.company_name)[:40]
        path = out_dir / f"{stage}_{safe}.html"
        path.write_text(
            f"<!doctype html><meta charset='utf-8'>"
            f"<title>{rendered.subject}</title>"
            f"<div style='padding:24px;background:#f5f5f5'>"
            f"<div style='max-width:680px;margin:auto;background:#fff;"
            f"padding:28px;border-radius:8px'>"
            f"<p style='color:#666;font:13px sans-serif;margin:0 0 4px'>"
            f"To: {company.email}</p>"
            f"<p style='font:600 16px sans-serif;margin:0 0 20px;"
            f"padding-bottom:16px;border-bottom:1px solid #eee'>"
            f"{rendered.subject}</p>{rendered.html}</div></div>",
            encoding="utf-8",
        )
        count += 1

        if count == 1:
            console.print(
                Panel(
                    f"[bold]إلى:[/bold] {company.email}\n"
                    f"[bold]العنوان:[/bold] {rendered.subject}\n\n{rendered.text}",
                    title=f"معاينة — {company.company_name}",
                    border_style="cyan",
                )
            )

    console.print(f"[green]✓[/green] حُفظت {count} معاينة HTML في {out_dir}")


def cmd_send(args) -> None:
    _run_campaign(args, stage="initial")


def cmd_followup(args) -> None:
    config = _load()
    if not config.followup.get("enabled", True):
        console.print("[yellow]المتابعة معطّلة في settings.yaml[/yellow]")
        return
    _run_campaign(args, stage="followup")


def _run_campaign(args, stage: str) -> None:
    config = _load()
    companies = _load_companies_or_exit(config)

    unfilled = config.unfilled_profile_fields()
    if unfilled and not args.force:
        console.print(
            f"[red]توقف:[/red] {len(unfilled)} حقل في profile.yaml ما زال TODO "
            f"({', '.join(unfilled[:3])}…). أكملها أولاً أو استخدم --force."
        )
        sys.exit(1)

    mode = "draft" if args.draft else "send"

    if mode == "send" and not args.ignore_schedule:
        window_error = sending_window_error(config)
        if window_error:
            console.print(f"[yellow]{window_error}[/yellow]")
            console.print("استخدم [bold]--ignore-schedule[/bold] لتجاوز هذا القيد.")
            sys.exit(1)

    with Tracker(config.paths.database) as tracker:
        plan = (
            plan_initial(config, tracker, companies)
            if stage == "initial"
            else plan_followup(config, tracker, companies)
        )

        title = "رسائل أولى" if stage == "initial" else "رسائل متابعة"
        _print_plan(plan, f"{title} جاهزة للإرسال")

        if not plan.planned:
            console.print("[yellow]لا توجد رسائل للإرسال.[/yellow]")
            return

        # فحص خطر السبام — إرشادي، لا يمنع الإرسال
        count = min(len(plan.planned), args.limit or len(plan.planned))
        risks = spam_analyze(config, tracker, plan.planned[:count])
        if risks:
            colors = {"high": "red", "med": "yellow", "low": "dim"}
            console.print("\n[bold]فحص خطر السبام:[/bold]")
            for risk in risks:
                color = colors[risk["level"]]
                console.print(f"[{color}]• {risk['title']}[/{color}]")
                console.print(f"  [dim]{risk['advice']}[/dim]")
        else:
            console.print("[green]✓ فحص خطر السبام: لا ملاحظات[/green]")

        if args.dry_run:
            console.print("[cyan]تشغيل تجريبي — لم يُرسل شيء.[/cyan]")
            return

        count = min(len(plan.planned), args.limit or len(plan.planned))
        verb = "إنشاء مسودة لـ" if mode == "draft" else "إرسال"
        if not args.yes:
            console.print(f"\nعلى وشك {verb} [bold]{count}[/bold] رسالة.")
            if input("اكتب yes للمتابعة: ").strip().lower() not in {"yes", "y", "نعم"}:
                console.print("أُلغيت العملية.")
                return

        try:
            client = _client(config)
            client.authenticate()
        except GmailError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(1)

        lock = CampaignLock(config.paths.database.with_name("campaign.lock"), "الطرفية")
        try:
            lock.acquire()
        except LockBusy as exc:
            console.print(f"[red]توقف:[/red] {exc}")
            sys.exit(1)

        def report(message: str) -> None:
            lock.touch()
            console.print(message)

        try:
            result = execute(
                config=config,
                tracker=tracker,
                client=client,
                plan=plan,
                mode=mode,
                limit=args.limit,
                report=report,
            )
        finally:
            lock.release()

        console.print(
            Panel(
                f"[green]نجحت: {result.succeeded}[/green]\n"
                f"[red]فشلت: {result.failed}[/red]"
                + (f"\n{result.stopped_reason}" if result.stopped_reason else ""),
                title="انتهت الحملة",
                border_style="green" if result.failed == 0 else "yellow",
            )
        )


def cmd_check_replies(args) -> None:
    from datetime import datetime, timezone

    config = _load()
    with Tracker(config.paths.database) as tracker:
        threads = tracker.all_sent_threads()
        drafts = tracker.drafted_rows()
        if not threads and not drafts:
            console.print("[yellow]لا توجد رسائل مُرسلة ولا مسودات لفحصها.[/yellow]")
            return

        try:
            client = _client(config)
            client.authenticate()
        except GmailError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(1)

        # مصير المسودات: ما أُرسل يدوياً من جيميل يتحول إلى «مُرسلة»
        if drafts:
            console.print(f"جارٍ التحقق من {len(drafts)} مسودة…")
            by_email = {d.email: d for d in drafts}
            for o in client.draft_outcomes([(d.email, d.thread_id) for d in drafts]):
                record = by_email[o.email]
                if o.state == "sent":
                    sent_iso = (
                        datetime.fromtimestamp(o.sent_at_ms / 1000, timezone.utc)
                        .isoformat(timespec="seconds")
                        if o.sent_at_ms else record.created_at
                    )
                    tracker.resolve_draft_sent(
                        o.email, record.stage, o.rfc_message_id, sent_iso)
                    console.print(
                        f"[green]✓[/green] {record.company_name} — أُرسلت يدوياً"
                        f" ({sent_iso[:16].replace('T', ' ')})"
                    )
                elif o.state == "deleted":
                    tracker.resolve_draft_deleted(o.email, record.stage)
                    console.print(
                        f"[yellow]•[/yellow] {record.company_name} — حُذفت المسودة،"
                        " عادت الشركة مؤهلة للإرسال"
                    )
                else:
                    console.print(f"[dim]…[/dim] {record.company_name} — ما زالت مسودة")

        if not threads:
            return
        console.print(f"جارٍ فحص {len(threads)} محادثة…")
        try:
            replies = client.find_replies(config.sender["email"], threads)
        except GmailError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(1)

        new_count, bounced = 0, 0
        table = Table(title="ردود بشرية", header_style="bold")
        table.add_column("الإيميل")
        table.add_column("مقتطف", style="dim")
        for reply in replies:
            if reply.is_bounce:
                tracker.mark_bounced(reply.email)
                bounced += 1
                console.print(
                    f"[red]↩[/red] {reply.email} — ارتدّت (عنوان غير صالح)"
                )
                continue
            if tracker.record_reply(reply.email, reply.thread_id, reply.snippet):
                new_count += 1
            table.add_row(reply.email, reply.snippet[:80])

        human = len(replies) - bounced
        if human:
            console.print(table)
        console.print(
            f"[green]✓[/green] ردود بشرية: {human} (جديد: {new_count})"
            + (f" — [red]ارتدادات: {bounced}[/red]" if bounced else "")
        )


def cmd_status(args) -> None:
    config = _load()
    companies = load_companies(config.paths.companies_csv).companies

    with Tracker(config.paths.database) as tracker:
        stats = tracker.stats()

        table = Table(title="حالة الحملة", header_style="bold")
        table.add_column("المؤشر")
        table.add_column("العدد", justify="right")
        table.add_row("شركات في القائمة", str(len(companies)))
        table.add_row("رسائل أولى أُرسلت", str(stats["initial_sent"]))
        table.add_row("متبقٍ", str(len(companies) - stats["initial_sent"]))
        table.add_row("رسائل متابعة", str(stats["followup_sent"]))
        table.add_row("مسودات", str(stats["drafted"]))
        table.add_row("[red]ارتدّت (عنوان غير صالح)[/red]", str(stats["bounced"]))
        table.add_row("[red]فشل[/red]", str(stats["failed"]))
        table.add_row("[green]ردود[/green]", str(stats["replies"]))
        table.add_row("أُرسل خلال 24 ساعة", str(stats["sent_last_24h"]))
        console.print(table)

        due = tracker.due_for_followup(int(config.followup.get("after_days", 7)))
        if due:
            console.print(f"[cyan]{len(due)} شركة تستحق رسالة متابعة الآن.[/cyan]")


def cmd_history(args) -> None:
    config = _load()
    with Tracker(config.paths.database) as tracker:
        rows = tracker.history(args.limit)
        if not rows:
            console.print("[yellow]السجل فارغ.[/yellow]")
            return
        table = Table(title=f"آخر {len(rows)} عملية", header_style="bold")
        table.add_column("التاريخ", style="dim")
        table.add_column("الشركة")
        table.add_column("المرحلة")
        table.add_column("الحالة")
        for row in rows:
            color = {"sent": "green", "failed": "red", "drafted": "cyan"}.get(
                row["status"], "white"
            )
            table.add_row(
                row["created_at"][:16].replace("T", " "),
                row["company_name"],
                row["stage"],
                f"[{color}]{row['status']}[/{color}]",
            )
        console.print(table)


# ================================================================== CLI #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="نظام إرسال إيميلات التقديم على الوظائف وفرص التدريب",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="تهيئة المجلدات وملف الشركات")
    p.add_argument("--force", action="store_true", help="استبدال companies.csv الموجود")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("import", help="استيراد إيميلات من ملف نصي إلى companies.csv")
    p.add_argument("file", help="ملف نصي، إيميل (أو 'اسم, إيميل') في كل سطر")
    p.add_argument("--language", default="en", choices=["en", "ar", "both"],
                   help="لغة الرسالة (both = عربي وإنجليزي في رسالة واحدة)")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("check-domains",
                       help="فحص سجلات MX — يكشف العناوين التي سترتدّ حتماً")
    p.add_argument("--skip", action="store_true",
                   help="تعليم النطاقات الميتة بالتجاوز تلقائياً")
    p.set_defaults(func=cmd_check_domains)

    p = sub.add_parser("dedupe", help="إزالة الإيميلات المكررة من قائمة الشركات")
    p.add_argument("--dry-run", action="store_true", help="عرض ما سيُزال دون تنفيذ")
    p.set_defaults(func=cmd_dedupe)

    p = sub.add_parser("auth", help="تسجيل الدخول إلى جوجل")
    p.add_argument("--force", action="store_true", help="إعادة المصادقة من الصفر")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("validate", help="فحص شامل قبل الإرسال")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("preview", help="معاينة الرسائل دون إرسال")
    p.add_argument("--company", help="فلترة باسم الشركة أو الإيميل")
    p.add_argument("--stage", default="initial", choices=["initial", "followup"])
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_preview)

    for name, func, help_text in (
        ("send", cmd_send, "إرسال الرسائل الأولى"),
        ("followup", cmd_followup, "إرسال رسائل المتابعة للشركات التي لم ترد"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--limit", type=int, help="حد أقصى لعدد الرسائل في هذه الجلسة")
        p.add_argument("--draft", action="store_true", help="إنشاء مسودات بدل الإرسال")
        p.add_argument("--dry-run", action="store_true", help="عرض الخطة فقط")
        p.add_argument("--yes", "-y", action="store_true", help="تخطي سؤال التأكيد")
        p.add_argument("--force", action="store_true", help="تجاهل حقول TODO الناقصة")
        p.add_argument(
            "--ignore-schedule", action="store_true", help="تجاهل قيود الأيام والساعات"
        )
        p.set_defaults(func=func)

    p = sub.add_parser("check-replies", help="فحص جيميل بحثاً عن ردود الشركات")
    p.set_defaults(func=cmd_check_replies)

    p = sub.add_parser("status", help="ملخص حالة الحملة")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("history", help="سجل آخر العمليات")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_history)

    return parser


def main() -> None:
    enable_utf8_console()
    args = build_parser().parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]أُوقفت العملية. ما أُرسل فعلاً مسجَّل في قاعدة البيانات.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
