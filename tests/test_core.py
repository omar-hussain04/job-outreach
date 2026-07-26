"""Offline tests — no Gmail account, no network, no config of your own needed.

    python -m pytest tests/ -v

اختبارات تعمل بلا حساب جيميل وبلا شبكة وبلا إعداداتك الشخصية.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Config, Paths  # noqa: E402
from src.contacts import (  # noqa: E402
    Company,
    dedupe_rows,
    load_companies,
    write_template_csv,
)
from src.dns_check import domain_of  # noqa: E402
from src.gmail_client import BOUNCE_SENDER_RE, build_message  # noqa: E402
from src.lock import CampaignLock, LockBusy  # noqa: E402
from src.templating import render  # noqa: E402
from src.tracker import STAGE_FOLLOWUP, STAGE_INITIAL, Tracker  # noqa: E402

import yaml  # noqa: E402


# ─────────────────────────────── fixtures ──────────────────────────────
@pytest.fixture
def profile() -> dict:
    """A filled-in profile built from the shipped example (TODO → real text)."""
    raw = (ROOT / "config" / "profile.example.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw)

    def fill(value):
        if isinstance(value, str):
            return value.replace("TODO:", "").strip() or "sample"
        if isinstance(value, list):
            return [fill(v) for v in value]
        return value

    return {k: fill(v) for k, v in data.items()}


@pytest.fixture
def config(tmp_path, profile) -> Config:
    settings = yaml.safe_load(
        (ROOT / "config" / "settings.example.yaml").read_text(encoding="utf-8")
    )
    return Config(
        raw=settings,
        profile=profile,
        paths=Paths(
            companies_csv=tmp_path / "companies.csv",
            database=tmp_path / "t.db",
            templates_dir=ROOT / "templates",
            previews_dir=tmp_path / "previews",
            credentials=tmp_path / "credentials.json",
            token=tmp_path / "token.json",
        ),
        root=ROOT,
    )


def company(**kw) -> Company:
    base = dict(row_number=1, company_name="Test Co", email="hr@test.example",
                role_target="Software Developer", language="en")
    base.update(kw)
    return Company(**base)


# ─────────────────────────────── templating ────────────────────────────
@pytest.mark.parametrize("language", ["ar", "en", "both"])
@pytest.mark.parametrize("stage", [STAGE_INITIAL, STAGE_FOLLOWUP])
def test_every_template_renders(config, profile, language, stage):
    out = render(config.paths.templates_dir, stage, company(language=language),
                 profile, ["CV.pdf"], extra={"initial_sent_date": "2026-01-01"})
    assert "{{" not in out.text and "{%" not in out.text
    assert out.subject and "TODO" not in out.subject
    assert "Test Co" in out.text


def test_direction_matches_language(config, profile):
    ar = render(config.paths.templates_dir, STAGE_INITIAL,
                company(language="ar"), profile, ["CV.pdf"])
    en = render(config.paths.templates_dir, STAGE_INITIAL,
                company(language="en"), profile, ["CV.pdf"])
    both = render(config.paths.templates_dir, STAGE_INITIAL,
                  company(language="both"), profile, ["CV.pdf"])
    assert 'dir="rtl"' in ar.html
    assert 'dir="ltr"' in en.html
    # bilingual: container is RTL but each paragraph decides for itself
    assert 'dir="rtl"' in both.html and 'dir="auto"' in both.html


def test_custom_note_appears_and_leaves_no_empty_paragraph(config, profile):
    with_note = render(config.paths.templates_dir, STAGE_INITIAL,
                       company(custom_note="A note about them."),
                       profile, ["CV.pdf"])
    without = render(config.paths.templates_dir, STAGE_INITIAL,
                     company(), profile, ["CV.pdf"])
    assert "A note about them." in with_note.text
    assert "<p></p>" not in without.html


def test_contact_name_changes_greeting(config, profile):
    named = render(config.paths.templates_dir, STAGE_INITIAL,
                   company(contact_name="Sarah"), profile, ["CV.pdf"])
    anon = render(config.paths.templates_dir, STAGE_INITIAL,
                  company(), profile, ["CV.pdf"])
    assert "Sarah" in named.text
    assert "there" in anon.text


# ─────────────────────────────── contacts ──────────────────────────────
def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["company_name", "email", "contact_name", "role_target",
                    "language", "custom_note", "website", "attachments", "skip"])
        w.writerows(rows)


def test_csv_validation(tmp_path):
    path = tmp_path / "c.csv"
    _write_csv(path, [
        ["Good", "a@b.com", "", "", "en", "", "", "", ""],
        ["Bad email", "not-an-email", "", "", "en", "", "", "", ""],
        ["", "c@d.com", "", "", "en", "", "", "", ""],
        ["Unknown lang", "e@f.com", "", "", "fr", "", "", "", ""],
        ["Skipped", "g@h.com", "", "", "en", "", "", "", "1"],
    ])
    result = load_companies(path)
    assert any("صيغة الإيميل" in e for e in result.errors)
    assert any("اسم الشركة فارغ" in e for e in result.errors)
    assert any("fr" in w for w in result.warnings)
    assert [c.language for c in result.companies if c.email == "e@f.com"] == ["en"]
    assert [c.skip for c in result.companies if c.email == "g@h.com"] == [True]


def test_duplicate_emails_warn(tmp_path):
    path = tmp_path / "c.csv"
    _write_csv(path, [
        ["First", "dup@x.com", "", "", "en", "", "", "", ""],
        ["Second", "DUP@x.com", "", "", "en", "", "", "", ""],
    ])
    result = load_companies(path)
    assert len(result.companies) == 1
    assert any("مكرر" in w for w in result.warnings)


def test_dedupe_keeps_richest_row():
    rows = [
        {"company_name": "empty", "email": "d@x.com", "custom_note": ""},
        {"company_name": "rich", "email": "D@x.com",
         "custom_note": "note", "contact_name": "Sam"},
        {"company_name": "unique", "email": "u@x.com"},
        {"company_name": "no email", "email": ""},
    ]
    clean, removed = dedupe_rows(rows)
    assert len(removed) == 1 and removed[0]["company_name"] == "empty"
    assert {r["company_name"] for r in clean} == {"rich", "unique", "no email"}


def test_dedupe_ties_keep_first():
    clean, removed = dedupe_rows([
        {"company_name": "first", "email": "t@x.com"},
        {"company_name": "second", "email": "t@x.com"},
    ])
    assert clean[0]["company_name"] == "first"
    assert removed[0]["company_name"] == "second"


def test_template_csv_has_expected_header(tmp_path):
    path = tmp_path / "new.csv"
    write_template_csv(path)
    assert load_companies(path).companies == []
    header = path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.startswith("company_name,email")


# ─────────────────────────────── tracker ───────────────────────────────
def test_never_sends_twice(tmp_path):
    with Tracker(tmp_path / "t.db") as t:
        assert not t.was_sent("a@x.com", STAGE_INITIAL)
        t.record(email="a@x.com", company_name="A", stage=STAGE_INITIAL,
                 status="sent", thread_id="th1")
        assert t.was_sent("a@x.com", STAGE_INITIAL)
        # a draft also counts as contacted
        t.record(email="b@x.com", company_name="B", stage=STAGE_INITIAL,
                 status="drafted", thread_id="th2")
        assert t.was_sent("b@x.com", STAGE_INITIAL)


def test_draft_resolution(tmp_path):
    with Tracker(tmp_path / "t.db") as t:
        for email in ("sent@x.com", "gone@x.com"):
            t.record(email=email, company_name="C", stage=STAGE_INITIAL,
                     status="drafted", thread_id="th")
        assert len(t.drafted_rows()) == 2

        t.resolve_draft_sent("sent@x.com", STAGE_INITIAL,
                             "<id@mail>", "2026-01-01T00:00:00+00:00")
        assert t.was_sent("sent@x.com", STAGE_INITIAL)
        assert t.get_initial("sent@x.com").created_at.startswith("2026-01-01")

        t.resolve_draft_deleted("gone@x.com", STAGE_INITIAL)
        assert not t.was_sent("gone@x.com", STAGE_INITIAL)


def test_bounce_blocks_resending_and_clears_reply(tmp_path):
    with Tracker(tmp_path / "t.db") as t:
        t.record(email="dead@x.com", company_name="D", stage=STAGE_INITIAL,
                 status="sent", thread_id="th")
        t.record_reply("dead@x.com", "th", "Address not found")
        assert t.has_replied("dead@x.com")

        t.mark_bounced("dead@x.com")
        assert t.has_bounced("dead@x.com")
        assert not t.has_replied("dead@x.com")   # not a human reply
        assert t.was_sent("dead@x.com", STAGE_INITIAL)   # still blocked
        assert t.stats()["bounced"] == 1


def test_followup_becomes_due_after_the_wait(tmp_path):
    with Tracker(tmp_path / "t.db") as t:
        t.record(email="a@x.com", company_name="A", stage=STAGE_INITIAL,
                 status="sent", thread_id="th")
        assert t.due_for_followup(7) == []      # just sent
        assert len(t.due_for_followup(0)) == 1  # zero-day wait → due

        t.record(email="a@x.com", company_name="A", stage=STAGE_FOLLOWUP,
                 status="sent", thread_id="th")
        assert t.due_for_followup(0) == []      # already followed up


# ─────────────────────────────── mime ──────────────────────────────────
def test_message_has_text_html_and_attachment(tmp_path, config, profile):
    cv = tmp_path / "CV.pdf"
    cv.write_bytes(b"%PDF-1.4\n%%EOF\n")
    out = render(config.paths.templates_dir, STAGE_INITIAL,
                 company(language="ar"), profile, ["CV.pdf"])
    msg = build_message(
        to="hr@test.example", sender_email="me@gmail.com", sender_name="Me",
        subject=out.subject, text_body=out.text, html_body=out.html,
        attachments=[cv],
    )
    types = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in types and "text/html" in types
    assert "application/pdf" in types
    assert b"CV.pdf" in msg.as_bytes()


@pytest.mark.parametrize("sender,expected", [
    ("Mail Delivery Subsystem <mailer-daemon@googlemail.com>", True),
    ("postmaster@example.com", True),
    ("Sarah <sarah@company.com>", False),
])
def test_bounce_sender_detection(sender, expected):
    assert bool(BOUNCE_SENDER_RE.search(sender)) is expected


def test_domain_of():
    assert domain_of("Ali@Example.COM ") == "example.com"
    assert domain_of("no-at-sign") == ""


# ─────────────────────────────── lock ──────────────────────────────────
def test_lock_blocks_a_second_campaign(tmp_path):
    path = tmp_path / "campaign.lock"
    first = CampaignLock(path, "cli")
    first.acquire()
    with pytest.raises(LockBusy):
        CampaignLock(path, "app").acquire()
    first.release()
    CampaignLock(path, "app").acquire()   # free again


def test_stale_lock_is_reclaimed(tmp_path):
    import os

    from src.lock import STALE_SECONDS

    path = tmp_path / "campaign.lock"
    CampaignLock(path, "cli").acquire()
    old = os.path.getmtime(path) - STALE_SECONDS - 60
    os.utime(path, (old, old))
    CampaignLock(path, "app").acquire()   # dead process → taken over
    assert path.exists()
