"""WordPress version fingerprinting (few requests, cached per host).

Order of probes (cheap -> expensive, stops at the first hit):

1. ``<meta name="generator">`` on the homepage
2. ``?ver=x.y.z`` query strings of ``wp-includes`` assets (very reliable and
   present even when the generator tag was removed)
3. ``/readme.html`` heading
4. RSS ``<generator>`` feed
5. ``/wp-includes/version.php`` (only when exposed by bad server configs)
"""

from __future__ import annotations

import re

__all__ = ["check_wp_version", "VERSION_RE"]

VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")
_GENERATOR_RE = re.compile(r"wordpress\s+([\d.]+)", re.IGNORECASE)
_VER_PARAM_RE = re.compile(r"wp-(?:includes|content|admin)/[^\"'\s>]+\?ver=(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)
_README_RE = re.compile(r"version\s+([\d.]+)", re.IGNORECASE)
_FEED_RE = re.compile(r"wordpress\.org/\?v=([\d.]+)", re.IGNORECASE)
_VERSION_PHP_RE = re.compile(r"\$wp_version\s*=\s*'([\d.]+)'")


def _extract_from_html(html: str) -> str | None:
    if not html:
        return None
    match = _GENERATOR_RE.search(html)
    if match:
        return match.group(1)
    match = _VER_PARAM_RE.search(html)
    if match:
        return match.group(1)
    return None


def check_wp_version(
    http,
    base_url: str,
    timeout: float | None = None,
    headers: dict | None = None,
    abort=None,
) -> str | None:
    """Return the WordPress version string or ``None`` when undiscoverable."""
    base = base_url.rstrip("/")

    if abort is not None and abort.is_set():
        return None
    response = http.get(f"{base}/", timeout=timeout, headers=headers)
    if response is not None and response.status_code < 400:
        try:
            html = response.text[:600_000]
        except Exception:
            html = ""
        version = _extract_from_html(html)
        if version:
            return version
    else:
        html = ""

    if abort is not None and abort.is_set():
        return None
    response = http.get(f"{base}/readme.html", timeout=timeout, headers=headers)
    if response is not None and response.status_code == 200:
        try:
            body = response.text[:200_000]
        except Exception:
            body = ""
        if "wordpress" in body.lower():
            match = _README_RE.search(body)
            if match:
                return match.group(1)

    if abort is not None and abort.is_set():
        return None
    response = http.get(f"{base}/feed/", timeout=timeout, headers=headers)
    if response is not None and response.status_code == 200:
        try:
            body = response.text[:200_000]
        except Exception:
            body = ""
        match = _FEED_RE.search(body)
        if match:
            return match.group(1)
        if not version and body:
            version = _extract_from_html(body)
            if version:
                return version

    if not html:
        return None

    if abort is not None and abort.is_set():
        return None
    response = http.get(f"{base}/wp-includes/version.php", timeout=timeout, headers=headers)
    if response is not None and response.status_code == 200:
        try:
            body = response.text[:20_000]
        except Exception:
            body = ""
        match = _VERSION_PHP_RE.search(body)
        if match:
            return match.group(1)

    return None
