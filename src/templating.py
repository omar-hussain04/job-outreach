"""رندر قوالب الرسائل: يحوّل ملف القالب + بيانات الشركة إلى (عنوان، نص، HTML)."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined, TemplateError

from .contacts import Company

URL_RE = re.compile(r"(https?://[^\s<>\"]+)")


class TemplateNotFound(Exception):
    pass


@dataclass
class RenderedEmail:
    subject: str
    text: str
    html: str
    language: str


def _jinja_env() -> Environment:
    # StrictUndefined عمداً: خطأ صريح أفضل من رسالة فيها فراغ مكان اسم الشركة.
    return Environment(undefined=StrictUndefined, trim_blocks=False, lstrip_blocks=False)


def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """يفصل ترويسة YAML (قبل سطر ---) عن نص الرسالة."""
    parts = raw.split("\n---\n", 1)
    if len(parts) != 2:
        raise TemplateError("القالب يجب أن يحتوي على سطر '---' يفصل الترويسة عن النص")
    meta = yaml.safe_load(parts[0]) or {}
    if not isinstance(meta, dict) or "subject" not in meta:
        raise TemplateError("ترويسة القالب يجب أن تحتوي على حقل subject")
    return meta, parts[1]


def _clean_text(text: str) -> str:
    """يزيل الأسطر الفارغة الزائدة الناتجة عن كتل Jinja."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _to_html(text: str, language: str) -> str:
    """يحوّل النص العادي إلى HTML بسيط ونظيف مع دعم اتجاه العربية.

    في الرسالة الثنائية (both) تحدد كل فقرة اتجاهها بنفسها عبر dir="auto":
    الفقرات العربية تنضبط يميناً والإنجليزية يساراً داخل نفس الرسالة.
    """
    both = language == "both"
    rtl = language == "ar" or both      # الحاوية تبدأ عربية في الثنائية
    direction = "rtl" if rtl else "ltr"
    align = "right" if rtl else "left"

    if both:
        p_attr = ' dir="auto" style="margin:0 0 16px;text-align:start"'
        ul_attr = (' dir="auto" style="margin:0 0 16px;'
                   'padding-inline-start:20px;text-align:start"')
    else:
        p_attr = ' style="margin:0 0 16px"'
        ul_attr = f' style="margin:0 0 16px;padding-{align}:20px"'

    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    parts: list[str] = []

    for block in blocks:
        # الفقرة الواحدة قد تخلط سطر تمهيد مع قائمة نقطية بعده، فنقسمها
        # إلى مقاطع متجانسة بدل معاملتها ككتلة واحدة.
        for is_list, lines in _runs(block.split("\n")):
            if is_list:
                items = "".join(f"<li>{_inline(l.lstrip()[2:])}</li>" for l in lines)
                parts.append(f"<ul{ul_attr}>{items}</ul>")
            else:
                body = "<br>".join(_inline(l) for l in lines)
                parts.append(f"<p{p_attr}>{body}</p>")

    body_html = "\n".join(parts)
    return (
        f'<div dir="{direction}" style="direction:{direction};text-align:{align};'
        f'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:15px;line-height:1.7;color:#1a1a1a;max-width:640px">'
        f"{body_html}</div>"
    )


def _runs(lines: list[str]) -> list[tuple[bool, list[str]]]:
    """يجمّع الأسطر في مقاطع متتالية: (هل هي قائمة نقطية؟، الأسطر)."""
    runs: list[tuple[bool, list[str]]] = []
    for line in lines:
        if not line.strip():
            continue
        is_list = line.lstrip().startswith("- ")
        if runs and runs[-1][0] == is_list:
            runs[-1][1].append(line)
        else:
            runs.append((is_list, [line]))
    return runs


def _inline(text: str) -> str:
    escaped = html.escape(text)
    return URL_RE.sub(r'<a href="\1">\1</a>', escaped)


def render(
    templates_dir: Path,
    stage: str,
    company: Company,
    profile: dict[str, Any],
    attachment_names: list[str],
    extra: dict[str, Any] | None = None,
) -> RenderedEmail:
    """يرندر قالب المرحلة المطلوبة (initial / followup) لشركة واحدة."""
    path = templates_dir / company.language / f"{stage}.md"
    if not path.exists():
        raise TemplateNotFound(f"القالب غير موجود: {path}")

    meta, body_src = _split_frontmatter(path.read_text(encoding="utf-8"))

    context: dict[str, Any] = {
        "me": profile,
        "company_name": company.company_name,
        "contact_name": company.contact_name,
        "role_target": company.role_target,
        "custom_note": company.custom_note,
        "website": company.website,
        "attachment_names": attachment_names,
        "today": date.today().isoformat(),
        "initial_sent_date": "",
    }
    context.update(extra or {})

    env = _jinja_env()
    subject = env.from_string(str(meta["subject"])).render(**context).strip()
    text = _clean_text(env.from_string(body_src).render(**context))

    return RenderedEmail(
        subject=subject,
        text=text,
        html=_to_html(text, company.language),
        language=company.language,
    )
