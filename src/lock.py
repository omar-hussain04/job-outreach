"""قفل يمنع تشغيل حملتين في وقت واحد.

صار للنظام واجهتان: الطرفية وتطبيق سطح المكتب. لو بدأت حملة من كلٍّ منهما،
فكل عملية تقرأ قائمة «لم تُرسل بعد» قبل أن تكتب الأخرى نتيجتها — فتصل
الشركة الواحدة رسالتان. القفل يجعل الحملة الثانية ترفض البدء بدل ذلك.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# لو تجاوز عمر آخر نبضة هذه المدة اعتبرنا القفل متروكاً من عملية ماتت.
# استراحة الدفعة الافتراضية 5 دقائق، فـ 15 هامش آمن.
STALE_SECONDS = 15 * 60


class LockBusy(Exception):
    """حملة أخرى تعمل بالفعل."""


@dataclass
class LockInfo:
    pid: int
    started: str
    source: str
    age_seconds: float

    @property
    def stale(self) -> bool:
        return self.age_seconds > STALE_SECONDS


class CampaignLock:
    def __init__(self, path: Path, source: str = "cli"):
        self.path = path
        self.source = source
        self._held = False

    # ------------------------------------------------------------------ #
    def read(self) -> LockInfo | None:
        if not self.path.exists():
            return None
        try:
            pid, started, source = self.path.read_text(encoding="utf-8").split("\n")[:3]
            age = datetime.now().timestamp() - self.path.stat().st_mtime
            return LockInfo(int(pid), started, source, age)
        except (OSError, ValueError):
            return None

    def acquire(self) -> None:
        existing = self.read()
        if existing is not None and not existing.stale:
            raise LockBusy(
                f"حملة أخرى تعمل الآن (من {existing.source}، المعرّف {existing.pid}، "
                f"بدأت {existing.started[11:19]}). انتظر انتهاءها أو أوقفها أولاً."
            )
        if existing is not None:
            self.path.unlink(missing_ok=True)   # قفل متروك من عملية ماتت

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:          # سبقتنا عملية أخرى بجزء من الثانية
            raise LockBusy("حملة أخرى بدأت للتو — حاول بعد قليل.") from exc

        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(
                f"{os.getpid()}\n"
                f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
                f"{self.source}\n"
            )
        self._held = True

    def touch(self) -> None:
        """نبضة تثبت أن الحملة ما زالت حيّة."""
        if self._held:
            try:
                os.utime(self.path, None)
            except OSError:
                pass

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    # ------------------------------------------------------------------ #
    def __enter__(self) -> "CampaignLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
