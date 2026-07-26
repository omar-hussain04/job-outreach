"""فحص عوامل خطر تصنيف رسائلك سباماً.

إرشادي لا تمويهي: يقيس ما تقيسه فلاتر البريد فعلاً — سمعة الارتدادات،
تطابق المحتوى، إيقاع الإرسال، وبنية الرسالة — ويشرح كيف تخفض كل خطر.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # لتلميحات الأنواع فقط — لا استيراد دائري وقت التشغيل
    from .config import Config
    from .sender import PlannedEmail
    from .tracker import Tracker

URL_RE = re.compile(r"https?://", re.IGNORECASE)

# كلمات ترفع درجة السبام في العناوين لدى الفلاتر التجارية
SUBJECT_TRIGGERS = re.compile(
    r"free|urgent|winner|guarantee|100%|\$\$|مجاني|عاجل|اربح|جائزة",
    re.IGNORECASE,
)


def _risk(level: str, title: str, advice: str) -> dict[str, str]:
    return {"level": level, "title": title, "advice": advice}


def analyze(
    config: "Config",
    tracker: "Tracker",
    planned: list["PlannedEmail"],
) -> list[dict[str, str]]:
    """يرجع قائمة مخاطر مرتبة: high ثم med ثم low. فارغة = لا ملاحظات."""
    risks: list[dict[str, str]] = []

    # ── 1) سمعة الارتدادات — أقوى إشارة سبام على الإطلاق ────────────────
    stats = tracker.stats()
    delivered = stats["initial_sent"] + stats["followup_sent"]
    bounced = stats["bounced"]
    attempted = delivered + bounced
    if attempted >= 5 and bounced / attempted >= 0.2:
        risks.append(_risk(
            "high",
            f"نسبة ارتداد {bounced}/{attempted} ({bounced * 100 // attempted}%)",
            "القوائم المليئة بعناوين ميتة هي بصمة السبام الأوضح عند جيميل. "
            "لا ترسل دفعة جديدة قبل تنظيف القائمة: تحقق من العناوين على مواقع "
            "الشركات، واحذف المخمّنة التي لا مصدر لها.",
        ))

    if not planned:
        return risks

    companies = [p.company for p in planned]
    n = len(companies)

    # ── 1ب) نطاقات لا تستقبل بريداً — ارتداد مؤكد لو أُرسل ──────────────
    from .dns_check import check_domains, domain_of

    checked = check_domains([domain_of(c.email) for c in companies], timeout=3.0)
    dead = [c for c in companies
            if (r := checked.get(domain_of(c.email))) is not None and r.will_bounce]
    if dead:
        names = "، ".join(c.company_name for c in dead[:3])
        risks.append(_risk(
            "high",
            f"{len(dead)} من {n} نطاقاً لا يستقبل بريداً ({names}…)",
            "هذه العناوين سترتدّ حتماً وترفع نسبة ارتدادك. اضغط «افحص "
            "النطاقات» في قسم الشركات لتجاوزها، أو صحّح النطاق من موقع الشركة.",
        ))

    # ── 2) تطابق المحتوى — علاجه الحقيقي التخصيص لا التمويه ─────────────
    no_note = sum(1 for c in companies if not c.custom_note.strip())
    if n >= 5 and no_note / n > 0.5:
        risks.append(_risk(
            "med",
            f"{no_note} من {n} رسالة بلا ملاحظة مخصصة",
            "الرسائل شبه المتطابقة لعشرات المستقبلين نمط إرسال جماعي. "
            "سطر واحد يخص كل شركة (من صفحتها أو أخبارها) يجعل كل رسالة "
            "فريدة فعلاً — وهو أيضاً ما يرفع نسبة الرد.",
        ))

    reused = [
        (note, count) for note, count in Counter(
            c.custom_note.strip() for c in companies if c.custom_note.strip()
        ).items() if count >= 3
    ]
    if reused:
        worst = max(reused, key=lambda x: x[1])
        risks.append(_risk(
            "med",
            f"نفس الملاحظة «{worst[0][:40]}…» مكررة في {worst[1]} رسالة",
            "الملاحظة المنسوخة تفقد غرضها: لا تميّز الرسالة عند الفلاتر "
            "ولا عند القارئ. اكتب ملاحظة تخص كل شركة وحدها.",
        ))

    # ── 3) إيقاع الإرسال ────────────────────────────────────────────────
    s = config.sending
    if int(s.get("min_delay_seconds", 45)) < 20:
        risks.append(_risk(
            "high",
            f"الفاصل الأدنى {s.get('min_delay_seconds')} ثانية — سريع جداً",
            "أقل من 20 ثانية بين الرسائل إيقاع آلة لا إنسان. "
            "أعده إلى 30 ثانية فأكثر من الإعدادات.",
        ))
    if int(s.get("daily_limit", 40)) > 100:
        risks.append(_risk(
            "med",
            f"الحد اليومي {s.get('daily_limit')} مرتفع",
            "لحساب جيميل شخصي، تجاوز مئة رسالة متشابهة يومياً يرفع "
            "احتمال التقييد. 40–60 يومياً آمن ويكفي حملتك.",
        ))

    # ── 4) بنية الرسالة — نفحص عيّنة مما سيُرسل فعلاً ────────────────────
    sample = planned[0].rendered
    links = len(URL_RE.findall(sample.text))
    if links > 5:
        risks.append(_risk(
            "med",
            f"{links} روابط في الرسالة",
            "كثرة الروابط سمة تسويقية. أبقِ روابط التوقيع (لينكدإن/جيت‌هَب/"
            "المعرض) واحذف الزائد من نص الرسالة.",
        ))

    trigger = SUBJECT_TRIGGERS.search(sample.subject)
    if trigger:
        risks.append(_risk(
            "med",
            f"كلمة «{trigger.group(0)}» في عنوان الرسالة",
            "من الكلمات التي تزن سلبياً عند الفلاتر — أعد صياغة العنوان بدونها.",
        ))
    if "!" in sample.subject or len(sample.subject) > 100:
        risks.append(_risk(
            "low",
            "عنوان طويل أو فيه علامة تعجب",
            "العناوين القصيرة الهادئة (أقل من 80 حرفاً، بلا تعجب) تمر أنظف.",
        ))

    caps_words = [w for w in sample.subject.split()
                  if len(w) > 3 and w.isascii() and w.isupper()]
    if caps_words:
        risks.append(_risk(
            "low",
            f"كلمات بأحرف كبيرة كاملة في العنوان: {', '.join(caps_words[:3])}",
            "الأحرف الكبيرة المتصلة تُقرأ صراخاً وتَزِن سلبياً.",
        ))

    order = {"high": 0, "med": 1, "low": 2}
    risks.sort(key=lambda r: order[r["level"]])
    return risks
