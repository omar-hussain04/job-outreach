"""قراءة قائمة الشركات من CSV والتحقق من صحتها."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

REQUIRED_COLUMNS = {"company_name", "email"}
KNOWN_COLUMNS = REQUIRED_COLUMNS | {
    "contact_name",
    "role_target",
    "language",
    "custom_note",
    "website",
    "attachments",
    "skip",
}


@dataclass
class Company:
    row_number: int
    company_name: str
    email: str
    contact_name: str = ""
    role_target: str = ""
    language: str = "en"
    custom_note: str = ""
    website: str = ""
    attachments: list[str] = field(default_factory=list)
    skip: bool = False

    @property
    def key(self) -> str:
        """المفتاح الفريد المستخدم في قاعدة البيانات."""
        return self.email.strip().lower()


@dataclass
class LoadResult:
    companies: list[Company]
    errors: list[str]
    warnings: list[str]


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "نعم", "x"}


def load_companies(
    path: Path,
    default_role_en: str = "Software Developer",
    default_role_ar: str = "مطوّر برمجيات",
) -> LoadResult:
    """يقرأ ملف الشركات ويرجع القائمة مع الأخطاء والتحذيرات."""
    errors: list[str] = []
    warnings: list[str] = []
    companies: list[Company] = []

    if not path.exists():
        return LoadResult([], [f"ملف الشركات غير موجود: {path}"], [])

    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = set(reader.fieldnames or [])

        missing = REQUIRED_COLUMNS - headers
        if missing:
            return LoadResult(
                [], [f"أعمدة مفقودة في {path.name}: {', '.join(sorted(missing))}"], []
            )

        for extra in sorted(headers - KNOWN_COLUMNS):
            warnings.append(f"عمود غير معروف سيتم تجاهله: {extra}")

        seen: dict[str, int] = {}

        for i, row in enumerate(reader, start=2):  # 2 = أول سطر بيانات بعد العناوين
            email = (row.get("email") or "").strip()
            name = (row.get("company_name") or "").strip()

            if not email and not name:
                continue  # سطر فارغ

            if not name:
                errors.append(f"سطر {i}: اسم الشركة فارغ")
                continue
            if not email:
                errors.append(f"سطر {i}: الإيميل فارغ ({name})")
                continue
            if not EMAIL_RE.match(email):
                errors.append(f"سطر {i}: صيغة الإيميل غير صحيحة → {email}")
                continue

            key = email.lower()
            if key in seen:
                warnings.append(
                    f"سطر {i}: إيميل مكرر {email} (موجود في سطر {seen[key]}) — سيتم تجاهله"
                )
                continue
            seen[key] = i

            language = (row.get("language") or "en").strip().lower()
            if language not in {"ar", "en", "both"}:
                warnings.append(f"سطر {i}: لغة غير معروفة '{language}' — سنستخدم en")
                language = "en"

            role = (row.get("role_target") or "").strip()
            if not role:
                if language == "both":
                    role = f"{default_role_ar} | {default_role_en}"
                elif language == "ar":
                    role = default_role_ar
                else:
                    role = default_role_en

            raw_attach = (row.get("attachments") or "").strip()
            attachments = [a.strip() for a in raw_attach.split(";") if a.strip()]

            companies.append(
                Company(
                    row_number=i,
                    company_name=name,
                    email=email,
                    contact_name=(row.get("contact_name") or "").strip(),
                    role_target=role,
                    language=language,
                    custom_note=(row.get("custom_note") or "").strip(),
                    website=(row.get("website") or "").strip(),
                    attachments=attachments,
                    skip=_truthy(row.get("skip") or ""),
                )
            )

    return LoadResult(companies, errors, warnings)


# الحقول التي تحدد «غنى» الصف عند المفاضلة بين نسختين مكررتين
DEDUPE_SCORE_FIELDS = ("custom_note", "contact_name", "role_target",
                       "website", "attachments")


def dedupe_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """يزيل الصفوف المكررة بالإيميل (بلا حساسية لحالة الأحرف).

    عند التكرار نحتفظ بالصف الأغنى بيانات — الذي فيه ملاحظة مخصصة أو جهة
    اتصال أفضل من نسخة فارغة منه — وعند التعادل نحتفظ بالأقدم (الأول).
    يرجع (الصفوف النظيفة، الصفوف المحذوفة).
    """
    def score(row: dict) -> int:
        return sum(
            1 for f in DEDUPE_SCORE_FIELDS if str(row.get(f) or "").strip()
        )

    best: dict[str, tuple[int, int]] = {}   # email → (score, index)
    for i, row in enumerate(rows):
        email = str(row.get("email") or "").strip().lower()
        if not email:
            continue    # صف بلا إيميل ليس تكراراً — يعالجه التحقق العادي
        s = score(row)
        if email not in best or s > best[email][0]:
            best[email] = (s, i)

    keep = {i for _, i in best.values()}
    clean: list[dict] = []
    removed: list[dict] = []
    for i, row in enumerate(rows):
        email = str(row.get("email") or "").strip().lower()
        if not email or i in keep:
            clean.append(row)
        else:
            removed.append(row)
    return clean, removed


def write_template_csv(path: Path) -> None:
    """ينشئ ملف CSV فارغاً بالأعمدة الصحيحة."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "company_name",
                "email",
                "contact_name",
                "role_target",
                "language",
                "custom_note",
                "website",
                "attachments",
                "skip",
            ]
        )
