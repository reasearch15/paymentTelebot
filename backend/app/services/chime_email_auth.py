"""Fail-closed authenticity validation for Chime payment emails.

Trust only Authentication-Results produced by Gmail's receiving infrastructure.
Never trust a visible From header or a sender-supplied Authentication-Results block alone.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Mapping

logger = logging.getLogger(__name__)

# Visible From allowlist — necessary but never sufficient.
APPROVED_FROM_ADDRESSES = frozenset(
    {
        "alerts@account.chime.com",
    }
)

# DKIM signing domains accepted when Gmail reports dkim=pass.
APPROVED_DKIM_DOMAINS = frozenset(
    {
        "account.chime.com",
    }
)

# SPF / envelope / mailed-by domains accepted when Gmail reports spf=pass.
APPROVED_SPF_DOMAINS = frozenset(
    {
        "em.account.chime.com",
        "account.chime.com",
        "chime.com",
    }
)

# DMARC header.from alignment domains (includes org domain used by Gmail for Chime).
APPROVED_DMARC_FROM_DOMAINS = frozenset(
    {
        "account.chime.com",
        "chime.com",
    }
)

# Return-Path / envelope sender domains.
APPROVED_RETURN_PATH_DOMAINS = frozenset(
    {
        "em.account.chime.com",
        "account.chime.com",
        "chime.com",
    }
)

# Reply-To is supporting evidence only; reject external domains when present.
APPROVED_REPLY_TO_ADDRESSES = frozenset(
    {
        "noreply@chime.com",
    }
)
APPROVED_REPLY_TO_DOMAINS = frozenset(
    {
        "chime.com",
        "account.chime.com",
        "em.account.chime.com",
    }
)

PASS_RESULTS = frozenset({"pass"})

FORWARD_SUBJECT_RE = re.compile(r"^\s*(?:fwd|fw)\s*:", re.IGNORECASE)
FORWARD_BODY_MARKERS = (
    "---------- forwarded message ----------",
    "--------- forwarded message ---------",
    "begin forwarded message",
    "forwarded message:",
    "original message-----",
)

RESENT_HEADERS = (
    "Resent-From",
    "Resent-Sender",
    "Resent-To",
    "Resent-Date",
    "Resent-Message-ID",
)
FORWARD_HEADERS = (
    "X-Forwarded-For",
    "X-Forwarded-To",
    "X-Forwarded-By",
    "X-Forwarded-Return-Path",
)

_COMMENT_RE = re.compile(r"\([^)]*\)")
_METHOD_RE = re.compile(r"\b(dkim|spf|dmarc)\s*=\s*([a-z0-9_-]+)", re.IGNORECASE)
_DKIM_D_RE = re.compile(r"\bheader\.d=(?P<q>\"([^\"]+)\"|'([^']+)'|([^\s;]+))", re.IGNORECASE)
_DKIM_I_RE = re.compile(r"\bheader\.i=(?P<q>\"([^\"]+)\"|'([^']+)'|@?([^\s;]+))", re.IGNORECASE)
_SPF_MAILFROM_RE = re.compile(
    r"\b(?:smtp\.mailfrom|envelope-from)=(?P<q>\"([^\"]+)\"|'([^']+)'|([^\s;]+))",
    re.IGNORECASE,
)
_DMARC_FROM_RE = re.compile(r"\bheader\.from=(?P<q>\"([^\"]+)\"|'([^']+)'|([^\s;]+))", re.IGNORECASE)


@dataclass(frozen=True)
class ChimeAuthValidationResult:
    accepted: bool
    reason: str
    normalized_from: str | None = None
    authenticated_dkim_domain: str | None = None
    authenticated_spf_domain: str | None = None
    dmarc_result: str | None = None
    forwarded_detected: bool = False

    def to_log_fields(self) -> dict[str, object]:
        from_domain = domain_of_address(self.normalized_from) if self.normalized_from else None
        return {
            "email_auth_valid": self.accepted,
            "email_auth_reason": self.reason,
            "from_domain": from_domain,
            "dkim_domain": self.authenticated_dkim_domain,
            "spf_domain": self.authenticated_spf_domain,
            "dmarc": self.dmarc_result,
            "forwarded_detected": self.forwarded_detected,
        }


@dataclass(frozen=True)
class _AuthMethod:
    result: str
    domain: str | None = None


@dataclass(frozen=True)
class _ParsedAuthResults:
    authserv_id: str
    dkim: _AuthMethod | None
    spf: _AuthMethod | None
    dmarc: _AuthMethod | None


def normalize_email_address(value: str | None) -> str | None:
    if not value:
        return None
    _, addr = parseaddr(value)
    addr = (addr or value).strip().lower()
    addr = addr.strip("<>").strip()
    return addr or None


def domain_of_address(address: str | None) -> str | None:
    if not address or "@" not in address:
        return None
    return address.rsplit("@", 1)[-1].strip().lower().rstrip(".") or None


def domain_matches(candidate: str | None, allowed: str) -> bool:
    """Exact domain or safe subdomain match. Rejects suffix tricks like chime.com.attacker.example."""
    if not candidate:
        return False
    c = candidate.lower().strip().rstrip(".")
    a = allowed.lower().strip().rstrip(".")
    if not c or not a:
        return False
    return c == a or c.endswith("." + a)


def domain_in_allowlist(candidate: str | None, allowed_domains: frozenset[str]) -> bool:
    if not candidate:
        return False
    return any(domain_matches(candidate, allowed) for allowed in allowed_domains)


def header_values(headers: Mapping[str, object], name: str) -> list[str]:
    raw = headers.get(name)
    if raw is None:
        # Case-insensitive fallback for stored keys.
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                raw = value
                break
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    return [text] if text else []


def is_trusted_gmail_authserv(authserv_id: str) -> bool:
    host = authserv_id.strip().lower().rstrip(".")
    if not host:
        return False
    # authserv-id may include trailing details; take first token.
    host = host.split()[0].rstrip(".")
    return host == "mx.google.com" or host.endswith(".google.com")


def _strip_comments(value: str) -> str:
    return _COMMENT_RE.sub(" ", value)


def _strip_quotes(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        return cleaned[1:-1].strip()
    return cleaned


def _first_capture(match: re.Match[str] | None) -> str | None:
    if match is None:
        return None
    for group in match.groups():
        if group:
            return _strip_quotes(group)
    return None


def _domain_from_mailfrom(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _strip_quotes(value).strip().strip("<>").lower()
    if "@" in cleaned:
        return domain_of_address(cleaned)
    return cleaned.rstrip(".") or None


def parse_authentication_results(value: str) -> _ParsedAuthResults | None:
    cleaned = _strip_comments(value).strip()
    if not cleaned:
        return None
    if ";" in cleaned:
        authserv_id, rest = cleaned.split(";", 1)
    else:
        authserv_id, rest = cleaned, ""
    authserv_id = authserv_id.strip()
    if not authserv_id:
        return None

    methods: dict[str, _AuthMethod] = {}
    for match in _METHOD_RE.finditer(rest):
        method = match.group(1).lower()
        result = match.group(2).lower()
        # First occurrence of each method wins within one authserv block.
        if method in methods:
            continue
        domain: str | None = None
        # Slice from this method to the next method-ish boundary for property lookup.
        start = match.end()
        next_method = _METHOD_RE.search(rest, start)
        segment = rest[start : next_method.start() if next_method else len(rest)]
        if method == "dkim":
            d_value = _first_capture(_DKIM_D_RE.search(segment) or _DKIM_D_RE.search(rest))
            i_value = _first_capture(_DKIM_I_RE.search(segment) or _DKIM_I_RE.search(rest))
            if d_value:
                domain = d_value.strip().lower().lstrip("@").rstrip(".")
            elif i_value:
                domain = _domain_from_mailfrom(i_value if "@" in i_value else f"x@{i_value.lstrip('@')}")
        elif method == "spf":
            mf = _first_capture(_SPF_MAILFROM_RE.search(segment) or _SPF_MAILFROM_RE.search(rest))
            domain = _domain_from_mailfrom(mf)
        elif method == "dmarc":
            hf = _first_capture(_DMARC_FROM_RE.search(segment) or _DMARC_FROM_RE.search(rest))
            domain = hf.strip().lower().rstrip(".") if hf else None
        methods[method] = _AuthMethod(result=result, domain=domain)

    return _ParsedAuthResults(
        authserv_id=authserv_id,
        dkim=methods.get("dkim"),
        spf=methods.get("spf"),
        dmarc=methods.get("dmarc"),
    )


def select_trusted_gmail_auth_results(headers: Mapping[str, object]) -> _ParsedAuthResults | None:
    """
    Prefer Authentication-Results added by Gmail's receiving infrastructure.

    Header order from extract_headers follows MIME top-to-bottom order; Gmail prepends
    its results, so the first trusted Google authserv entry is preferred.
    """
    trusted: list[_ParsedAuthResults] = []
    for value in header_values(headers, "Authentication-Results"):
        parsed = parse_authentication_results(value)
        if parsed is None:
            continue
        if is_trusted_gmail_authserv(parsed.authserv_id):
            trusted.append(parsed)
    return trusted[0] if trusted else None


def detect_forwarding(
    headers: Mapping[str, object],
    subject: str | None,
    raw_text: str | None,
    html_visible_text: str | None,
) -> bool:
    subject_text = subject or ""
    if FORWARD_SUBJECT_RE.match(subject_text):
        return True

    for name in RESENT_HEADERS + FORWARD_HEADERS:
        if header_values(headers, name):
            return True

    body = "\n".join(part for part in [raw_text or "", html_visible_text or ""] if part).lower()
    if any(marker in body for marker in FORWARD_BODY_MARKERS):
        return True

    # ARC chain from a non-Google / non-Chime intermediary without Gmail Chime pass is handled
    # by DKIM/SPF/DMARC checks. Additionally reject when ARC results exist only from foreign authservs
    # and no trusted Gmail Authentication-Results are present (caller checks missing auth separately).
    return False


def validate_chime_email_authenticity(
    raw_headers: Mapping[str, object] | None,
    parsed_headers: Mapping[str, object] | None = None,
    *,
    subject: str | None = None,
    raw_text: str | None = None,
    html_visible_text: str | None = None,
    sender_address: str | None = None,
) -> ChimeAuthValidationResult:
    """
    Canonical Chime authenticity gate. Fail closed unless all required checks pass.
    """
    headers: dict[str, object] = {}
    if raw_headers:
        headers.update(dict(raw_headers))
    if parsed_headers:
        # Parsed overlays only fill missing keys; raw wins for conflicts.
        for key, value in parsed_headers.items():
            headers.setdefault(key, value)

    from_header = sender_address or (header_values(headers, "From")[0] if header_values(headers, "From") else None)
    normalized_from = normalize_email_address(from_header)
    forwarded = detect_forwarding(headers, subject, raw_text, html_visible_text)

    def reject(
        reason: str,
        *,
        dkim_domain: str | None = None,
        spf_domain: str | None = None,
        dmarc_result: str | None = None,
        forwarded_detected: bool | None = None,
    ) -> ChimeAuthValidationResult:
        result = ChimeAuthValidationResult(
            accepted=False,
            reason=reason,
            normalized_from=normalized_from,
            authenticated_dkim_domain=dkim_domain,
            authenticated_spf_domain=spf_domain,
            dmarc_result=dmarc_result,
            forwarded_detected=forwarded if forwarded_detected is None else forwarded_detected,
        )
        logger.info(
            "email_auth_valid=false email_auth_reason=%s from_domain=%s dkim_domain=%s spf_domain=%s dmarc=%s forwarded_detected=%s",
            result.reason,
            domain_of_address(result.normalized_from),
            result.authenticated_dkim_domain,
            result.authenticated_spf_domain,
            result.dmarc_result,
            result.forwarded_detected,
        )
        return result

    if forwarded:
        return reject("forwarded_message", forwarded_detected=True)

    if not normalized_from or normalized_from not in APPROVED_FROM_ADDRESSES:
        return reject("sender_not_allowed")

    auth = select_trusted_gmail_auth_results(headers)
    if auth is None:
        return reject("authentication_headers_missing")

    # DKIM
    if auth.dkim is None:
        return reject("authentication_headers_missing")
    if auth.dkim.result not in PASS_RESULTS:
        return reject("dkim_failed", dkim_domain=auth.dkim.domain)
    if not domain_in_allowlist(auth.dkim.domain, APPROVED_DKIM_DOMAINS):
        return reject("dkim_domain_mismatch", dkim_domain=auth.dkim.domain)

    # SPF
    if auth.spf is None:
        return reject("authentication_headers_missing")
    if auth.spf.result not in PASS_RESULTS:
        return reject("spf_failed", dkim_domain=auth.dkim.domain, spf_domain=auth.spf.domain)
    spf_domain = auth.spf.domain
    if not spf_domain:
        # Gmail sometimes omits smtp.mailfrom properties; fall back to Return-Path domain.
        return_path_values = header_values(headers, "Return-Path")
        if return_path_values:
            spf_domain = domain_of_address(normalize_email_address(return_path_values[0])) or _domain_from_mailfrom(
                return_path_values[0]
            )
    if not domain_in_allowlist(spf_domain, APPROVED_SPF_DOMAINS):
        return reject(
            "spf_domain_mismatch",
            dkim_domain=auth.dkim.domain,
            spf_domain=spf_domain,
        )

    # DMARC — require pass when present; if absent, DKIM+SPF alignment is sufficient.
    dmarc_result_value: str | None = None
    if auth.dmarc is not None:
        dmarc_result_value = auth.dmarc.result
        if auth.dmarc.result not in PASS_RESULTS:
            return reject(
                "dmarc_failed",
                dkim_domain=auth.dkim.domain,
                spf_domain=spf_domain,
                dmarc_result=auth.dmarc.result,
            )
        dmarc_from = auth.dmarc.domain or domain_of_address(normalized_from)
        if not domain_in_allowlist(dmarc_from, APPROVED_DMARC_FROM_DOMAINS):
            return reject(
                "dmarc_failed",
                dkim_domain=auth.dkim.domain,
                spf_domain=spf_domain,
                dmarc_result=auth.dmarc.result,
            )

    # Return-Path / envelope alignment (supporting, but required when present).
    return_path_values = header_values(headers, "Return-Path")
    if return_path_values:
        return_path_addr = normalize_email_address(return_path_values[0])
        return_path_domain = domain_of_address(return_path_addr) or _domain_from_mailfrom(return_path_values[0])
        if not domain_in_allowlist(return_path_domain, APPROVED_RETURN_PATH_DOMAINS):
            return reject(
                "return_path_mismatch",
                dkim_domain=auth.dkim.domain,
                spf_domain=spf_domain,
                dmarc_result=dmarc_result_value,
            )

    # Reply-To: supporting only; reject inconsistent external domains.
    reply_to_values = header_values(headers, "Reply-To")
    if reply_to_values:
        reply_to = normalize_email_address(reply_to_values[0])
        reply_domain = domain_of_address(reply_to)
        if reply_to and reply_to not in APPROVED_REPLY_TO_ADDRESSES:
            if not domain_in_allowlist(reply_domain, APPROVED_REPLY_TO_DOMAINS):
                return reject(
                    "reply_to_mismatch",
                    dkim_domain=auth.dkim.domain,
                    spf_domain=spf_domain,
                    dmarc_result=dmarc_result_value,
                )

    accepted = ChimeAuthValidationResult(
        accepted=True,
        reason="ok",
        normalized_from=normalized_from,
        authenticated_dkim_domain=auth.dkim.domain,
        authenticated_spf_domain=spf_domain,
        dmarc_result=dmarc_result_value or "absent",
        forwarded_detected=False,
    )
    logger.info(
        "email_auth_valid=true email_auth_reason=ok from_domain=%s dkim_domain=%s spf_domain=%s dmarc=%s forwarded_detected=false",
        domain_of_address(accepted.normalized_from),
        accepted.authenticated_dkim_domain,
        accepted.authenticated_spf_domain,
        accepted.dmarc_result,
    )
    return accepted
