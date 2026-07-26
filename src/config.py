"""تحميل الإعدادات والملف الشخصي والتحقق من صحتهما."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """خطأ في الإعدادات — رسالته تُعرض للمستخدم مباشرة."""


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        example = path.with_suffix(".example.yaml")
        hint = (
            f"\nانسخ {example.name} إلى {path.name}، أو شغّل: python main.py init"
            f"\nCopy {example.name} to {path.name}, or run: python main.py init"
            if example.exists() else ""
        )
        raise ConfigError(f"ملف الإعدادات غير موجود: {path}{hint}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"صيغة الملف غير صحيحة (يجب أن يكون قاموس YAML): {path}")
    return data


def _resolve(rel: str) -> Path:
    """يحوّل مساراً نسبياً في ملف الإعدادات إلى مسار مطلق داخل المشروع."""
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


@dataclass
class Paths:
    companies_csv: Path
    database: Path
    templates_dir: Path
    previews_dir: Path
    credentials: Path
    token: Path


@dataclass
class Config:
    raw: dict[str, Any]
    profile: dict[str, Any]
    paths: Paths
    root: Path = ROOT

    # ------------------------------------------------------------------ #
    @property
    def sender(self) -> dict[str, Any]:
        return self.raw.get("sender", {})

    @property
    def sending(self) -> dict[str, Any]:
        return self.raw.get("sending", {})

    @property
    def followup(self) -> dict[str, Any]:
        return self.raw.get("followup", {})

    @property
    def attachments(self) -> dict[str, Any]:
        return self.raw.get("attachments", {})

    def default_attachments(self) -> list[Path]:
        return [_resolve(a) for a in self.attachments.get("default", [])]

    def display_name(self, language: str) -> str:
        if language == "both":
            en = self.sender.get("display_name_en") or ""
            ar = self.sender.get("display_name_ar") or ""
            return f"{en} | {ar}".strip(" |") or en or ar
        key = "display_name_ar" if language == "ar" else "display_name_en"
        return self.sender.get(key) or self.sender.get("display_name_en") or ""

    # ------------------------------------------------------------------ #
    def unfilled_profile_fields(self) -> list[str]:
        """يرجع أسماء الحقول التي ما زالت تحمل علامة TODO."""
        missing: list[str] = []

        def walk(value: Any, path: str) -> None:
            if isinstance(value, str):
                if value.strip().upper().startswith("TODO"):
                    missing.append(path)
            elif isinstance(value, dict):
                for k, v in value.items():
                    walk(v, f"{path}.{k}" if path else str(k))
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    walk(v, f"{path}[{i}]")

        walk(self.profile, "")
        return missing


def load_config(
    settings_path: Path | None = None,
    profile_path: Path | None = None,
) -> Config:
    settings_path = settings_path or ROOT / "config" / "settings.yaml"
    profile_path = profile_path or ROOT / "config" / "profile.yaml"

    raw = _load_yaml(settings_path)
    profile = _load_yaml(profile_path)

    p = raw.get("paths", {})
    paths = Paths(
        companies_csv=_resolve(p.get("companies_csv", "data/companies.csv")),
        database=_resolve(p.get("database", "data/outreach.db")),
        templates_dir=_resolve(p.get("templates_dir", "templates")),
        previews_dir=_resolve(p.get("previews_dir", "previews")),
        credentials=_resolve(p.get("credentials", "config/credentials.json")),
        token=_resolve(p.get("token", "config/token.json")),
    )

    # جوجل يحمّل الملف باسم client_secret_<id>.apps.googleusercontent.com.json،
    # فنقبله كما هو بدل إجبار المستخدم على إعادة التسمية بعد كل تحميل.
    if not paths.credentials.exists():
        found = sorted(paths.credentials.parent.glob("client_secret*.json"))
        if found:
            paths.credentials = found[0]

    if not raw.get("sender", {}).get("email"):
        raise ConfigError("sender.email مفقود في config/settings.yaml")

    return Config(raw=raw, profile=profile, paths=paths)


def enable_utf8_console() -> None:
    """يضمن ظهور العربية بشكل صحيح في طرفية ويندوز."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
