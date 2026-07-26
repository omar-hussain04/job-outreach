"""طبقة التعامل مع Gmail API: المصادقة، الإرسال، المسودات، وكشف الردود."""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# compose  = إنشاء المسودات وإرسال الرسائل
# readonly = قراءة المحادثات لكشف الردود
SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
]


class GmailError(Exception):
    pass


@dataclass
class SendResult:
    gmail_id: str
    thread_id: str
    rfc_message_id: str


@dataclass
class Reply:
    email: str
    thread_id: str
    snippet: str
    is_bounce: bool = False   # رسالة ارتداد من خادم البريد، لا ردّ بشري


# مرسِلو إشعارات الفشل: أي رسالة منهم تعني أن العنوان لم يستلم رسالتك
BOUNCE_SENDER_RE = re.compile(
    r"mailer-daemon@|postmaster@|mail delivery (?:subsystem|system)",
    re.IGNORECASE,
)


@dataclass
class DraftOutcome:
    """مصير مسودة أنشأها النظام: هل أرسلها المستخدم يدوياً أم حذفها أم ما زالت تنتظر."""
    email: str
    thread_id: str
    state: str            # "sent" | "waiting" | "deleted"
    rfc_message_id: str   # ترويسة Message-ID للرسالة المُرسلة (لربط المتابعة)
    sent_at_ms: int       # وقت الإرسال الفعلي من جيميل (ملّي ثانية)


def _guess_type(path: Path) -> tuple[str, str]:
    ctype, _ = mimetypes.guess_type(path.name)
    if ctype is None:
        return "application", "octet-stream"
    main, _, sub = ctype.partition("/")
    return main, sub or "octet-stream"


def build_message(
    *,
    to: str,
    sender_email: str,
    sender_name: str,
    subject: str,
    text_body: str,
    html_body: str,
    attachments: list[Path],
    reply_to: str = "",
    in_reply_to: str = "",
    references: str = "",
) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = formataddr((sender_name, sender_email)) if sender_name else sender_email
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to

    msg.set_content(text_body, subtype="plain", charset="utf-8")
    msg.add_alternative(html_body, subtype="html", charset="utf-8")

    for path in attachments:
        if not path.exists():
            raise GmailError(f"المرفق غير موجود: {path}")
        main, sub = _guess_type(path)
        msg.add_attachment(
            path.read_bytes(), maintype=main, subtype=sub, filename=path.name
        )
    return msg


def _encode(msg: EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


class GmailClient:
    def __init__(self, credentials_path: Path, token_path: Path):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._service = None

    # ------------------------------------------------------------ مصادقة #
    def authenticate(self, force: bool = False) -> str:
        """يسجّل الدخول ويحفظ التوكن. يرجع الإيميل الذي تمت المصادقة به."""
        creds: Credentials | None = None

        if self.token_path.exists() and not force:
            creds = Credentials.from_authorized_user_file(
                str(self.token_path), SCOPES
            )

        if not creds or not creds.valid:
            refreshed = False
            if creds and creds.expired and creds.refresh_token and not force:
                try:
                    creds.refresh(Request())
                    refreshed = True
                except RefreshError:
                    # وضع Testing في جوجل يُبطل التوكن بعد 7 أيام،
                    # فنعيد تسجيل الدخول بدل أن نفشل برسالة غامضة.
                    creds = None

            if not refreshed:
                if not self.credentials_path.exists():
                    raise GmailError(
                        f"ملف الاعتماد غير موجود: {self.credentials_path}\n"
                        "ضع ملف OAuth المحمَّل من Google Cloud في مجلد config/ "
                        "(باسم credentials.json أو باسمه الأصلي client_secret*.json)\n"
                        "راجع قسم 'إعداد Gmail API' في README.md"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                creds = flow.run_local_server(
                    port=0, prompt="consent", access_type="offline"
                )
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")

        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = self._service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress", "")

    @property
    def service(self):
        if self._service is None:
            self.authenticate()
        return self._service

    # ------------------------------------------------------------- إرسال #
    def send(self, msg: EmailMessage, thread_id: str = "") -> SendResult:
        body: dict[str, str] = {"raw": _encode(msg)}
        if thread_id:
            body["threadId"] = thread_id
        try:
            sent = self.service.users().messages().send(userId="me", body=body).execute()
        except HttpError as exc:
            raise GmailError(f"فشل الإرسال: {exc}") from exc

        return SendResult(
            gmail_id=sent["id"],
            thread_id=sent.get("threadId", ""),
            rfc_message_id=self._fetch_rfc_message_id(sent["id"]),
        )

    def create_draft(self, msg: EmailMessage, thread_id: str = "") -> SendResult:
        message: dict[str, str] = {"raw": _encode(msg)}
        if thread_id:
            message["threadId"] = thread_id
        try:
            draft = (
                self.service.users()
                .drafts()
                .create(userId="me", body={"message": message})
                .execute()
            )
        except HttpError as exc:
            raise GmailError(f"فشل إنشاء المسودة: {exc}") from exc

        inner = draft.get("message", {})
        return SendResult(
            gmail_id=draft["id"],
            thread_id=inner.get("threadId", ""),
            rfc_message_id="",
        )

    def _fetch_rfc_message_id(self, gmail_id: str) -> str:
        """يجلب ترويسة Message-ID الحقيقية — نحتاجها لربط رسالة المتابعة بنفس المحادثة."""
        try:
            meta = (
                self.service.users()
                .messages()
                .get(
                    userId="me",
                    id=gmail_id,
                    format="metadata",
                    metadataHeaders=["Message-ID"],
                )
                .execute()
            )
        except HttpError:
            return ""
        for header in meta.get("payload", {}).get("headers", []):
            if header.get("name", "").lower() == "message-id":
                return header.get("value", "")
        return ""

    # ----------------------------------------------------- مصير المسودات #
    def draft_outcomes(self, items: list[tuple[str, str]]) -> list[DraftOutcome]:
        """يفحص محادثات المسودات: أُرسلت يدوياً؟ ما زالت مسودة؟ حُذفت؟

        نميّز بوسوم جيميل داخل المحادثة: رسالة تحمل SENT تعني أن المستخدم
        أرسل المسودة بنفسه؛ وسم DRAFT يعني أنها ما زالت تنتظر؛ وغياب
        الاثنين (أو غياب المحادثة كلها) يعني أنه حذفها.
        """
        out: list[DraftOutcome] = []
        for email, thread_id in items:
            try:
                thread = (
                    self.service.users()
                    .threads()
                    .get(userId="me", id=thread_id, format="metadata",
                         metadataHeaders=["Message-ID"])
                    .execute()
                )
            except HttpError:
                out.append(DraftOutcome(email, thread_id, "deleted", "", 0))
                continue

            messages = thread.get("messages", [])
            sent_msg = next(
                (m for m in messages if "SENT" in m.get("labelIds", [])), None
            )
            if sent_msg is not None:
                headers = sent_msg.get("payload", {}).get("headers", [])
                rfc_id = next(
                    (h["value"] for h in headers
                     if h["name"].lower() == "message-id"), ""
                )
                out.append(DraftOutcome(
                    email, thread_id, "sent", rfc_id,
                    int(sent_msg.get("internalDate", 0)),
                ))
            elif any("DRAFT" in m.get("labelIds", []) for m in messages):
                out.append(DraftOutcome(email, thread_id, "waiting", "", 0))
            else:
                out.append(DraftOutcome(email, thread_id, "deleted", "", 0))
        return out

    # -------------------------------------------------------------- ردود #
    def find_replies(self, my_email: str, threads: list[tuple[str, str]]) -> list[Reply]:
        """يفحص المحادثات المُرسلة ويرجع تلك التي فيها رسالة من طرف آخر."""
        found: list[Reply] = []
        me = my_email.lower()

        for email, thread_id in threads:
            try:
                thread = (
                    self.service.users()
                    .threads()
                    .get(userId="me", id=thread_id, format="metadata",
                         metadataHeaders=["From"])
                    .execute()
                )
            except HttpError:
                continue  # المحادثة حُذفت أو لا يمكن الوصول إليها

            for message in thread.get("messages", []):
                headers = message.get("payload", {}).get("headers", [])
                sender = next(
                    (h["value"] for h in headers if h["name"].lower() == "from"), ""
                )
                if me in sender.lower():
                    continue  # رسالتنا نحن
                found.append(
                    Reply(
                        email=email,
                        thread_id=thread_id,
                        snippet=message.get("snippet", ""),
                        # إشعار فشل التسليم ليس رداً بشرياً — يُصنّف ارتداداً
                        is_bounce=bool(BOUNCE_SENDER_RE.search(sender)),
                    )
                )
                break

        return found
