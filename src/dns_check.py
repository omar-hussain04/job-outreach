"""التحقق من أن نطاق الإيميل يستقبل بريداً أصلاً — قبل الإرسال لا بعده.

الارتداد أخطر إشارة سبام عند جيميل، وأكثر أسبابه شيوعاً في القوائم المجمّعة
هو نطاق مخمَّن لا وجود له (مثل arabnationalbank.com بينما النطاق الحقيقي
anb.com.sa). فحص سجل MX يكشف هذا في ثوانٍ بدل أن تكتشفه بعد حرق سمعتك.

لا يفحص وجود الصندوق نفسه — لا توجد طريقة موثوقة لذلك دون إرسال — لكنه
يقطع بأن النطاق بلا MX لن يستلم شيئاً أبداً.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import dns.exception
import dns.resolver

# حالات النطاق
LIVE = "live"        # له سجل MX — يستقبل بريداً
NO_MX = "no_mx"      # النطاق موجود لكن بلا خادم بريد → ارتداد مؤكد
MISSING = "missing"  # النطاق نفسه غير مسجّل → ارتداد مؤكد
UNKNOWN = "unknown"  # تعذّر الفحص (انقطاع شبكة أو مهلة) — لا نحكم


@dataclass
class DomainResult:
    domain: str
    state: str
    detail: str

    @property
    def will_bounce(self) -> bool:
        return self.state in (NO_MX, MISSING)


def _resolver(timeout: float) -> dns.resolver.Resolver:
    r = dns.resolver.Resolver()
    r.timeout = timeout
    r.lifetime = timeout
    return r


def check_domain(domain: str, timeout: float = 5.0) -> DomainResult:
    domain = domain.strip().lower().rstrip(".")
    if not domain:
        return DomainResult(domain, MISSING, "نطاق فارغ")

    resolver = _resolver(timeout)
    try:
        answers = resolver.resolve(domain, "MX")
        hosts = sorted(str(r.exchange).rstrip(".") for r in answers)
        if hosts:
            return DomainResult(domain, LIVE, hosts[0])
    except dns.resolver.NoAnswer:
        # النطاق مسجّل لكن بلا MX. بعض النطاقات تستقبل عبر سجل A كبديل،
        # لكن الشركات الجادة لا تفعل ذلك — نعدّه ارتداداً شبه مؤكد.
        return DomainResult(domain, NO_MX, "النطاق موجود لكن بلا خادم بريد")
    except dns.resolver.NXDOMAIN:
        return DomainResult(domain, MISSING, "النطاق غير مسجّل إطلاقاً")
    except (dns.resolver.NoNameservers, dns.exception.Timeout):
        return DomainResult(domain, UNKNOWN, "تعذّر الفحص — تحقق من اتصالك")
    except dns.exception.DNSException as exc:
        return DomainResult(domain, UNKNOWN, str(exc)[:80])

    return DomainResult(domain, NO_MX, "لا سجلات بريد")


def check_domains(domains: list[str], timeout: float = 5.0,
                  workers: int = 12) -> dict[str, DomainResult]:
    """يفحص عدة نطاقات بالتوازي — 160 نطاقاً في ثوانٍ بدل دقائق."""
    unique = sorted({d.strip().lower() for d in domains if d and d.strip()})
    if not unique:
        return {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(lambda d: check_domain(d, timeout), unique)
    return {r.domain: r for r in results}


def domain_of(email: str) -> str:
    _, _, domain = (email or "").partition("@")
    return domain.strip().lower()
