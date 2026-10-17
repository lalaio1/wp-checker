"""Per-target pipeline: ping -> login -> (version | permissions | plugins)."""

from __future__ import annotations

import time

from func.check_permissions import probe_capabilities
from func.check_wp_credentials import login
from func.check_wp_version import check_wp_version
from func.ping_site import ping_site
from func.report import FIELDS
from utils.ansi import c

__all__ = ["process_target", "empty_record", "FIELDS"]


def empty_record(target) -> dict:
    return {
        "url": target.url,
        "username": target.username,
        "password": target.password,
        "status": "Error",
        "wp_version": "",
        "role": "",
        "admin": "",
        "name": "",
        "capabilities": "",
        "plugin_install": "",
        "http_code": "",
        "waf": "",
        "error": "",
        "elapsed": "",
    }


def _probe_version(ctx, base_url: str, headers: dict | None = None) -> str:
    if ctx.cancel.is_set():
        return ""
    cached = ctx.version_cache.get(base_url)
    if cached is not None:
        return str(cached)
    version = check_wp_version(
        ctx.http, base_url, timeout=min(ctx.args.timeout, 12.0), headers=headers, abort=ctx.cancel
    ) or ""
    ctx.version_cache.set(base_url, version)
    return version


def _probe_permissions(ctx, base_url: str, username: str, admin_html, session, extra_headers=None) -> dict:
    key = f"{base_url}|{username.lower()}"
    cached = ctx.permission_cache.get(key)
    if cached is not None:
        return cached
    try:
        data = probe_capabilities(
            ctx.http,
            base_url,
            admin_html=admin_html,
            session=session,
            abort=ctx.cancel,
            check_capabilities=ctx.args.check_permissions,
            check_plugin_install=ctx.args.check_plugin_install,
            verbose=ctx.args.verbose,
            log=ctx.verbose_log,
            extra_headers=extra_headers,
        )
    except Exception as exc:  # never let a probe kill the scan
        data = {"role": "", "admin": False, "capabilities_list": [], "plugin_install": "", "error": str(exc)}
    ctx.permission_cache.set(key, data)
    return data


def process_target(ctx, target) -> dict:
    """Run the full check for a single credential pair."""
    args = ctx.args
    started = time.perf_counter()
    record = empty_record(target)

    # per target fingerprint rotation
    if args.random_agent:
        ctx.http.randomize_agent()
    if ctx.proxy_count > 1:
        ctx.http.rotate_proxy()

    index = ctx.counter.inc()
    ctx.maybe_rotate_tor(index)

    if args.verbose and ctx.verbose_log:
        ctx.verbose_log(f"{c('→', 'cyan')} [{target.url}] {target.username}:{'*' * len(target.password)}")

    # ------------------------------------------------------------------ #
    # 1. reachability (cached per host)
    # ------------------------------------------------------------------ #
    if not args.skip_ping:
        online = ctx.ping_cache.get(target.url)
        if online is None:
            online = ping_site(target.url, timeout=args.ping_timeout)
            if not online:
                # TCP probes are often filtered while HTTP still works
                probe = ctx.http.get(target.login_url, timeout=min(8.0, args.timeout))
                online = probe is not None
                if args.verbose and ctx.verbose_log:
                    ctx.verbose_log(
                        c(f"  ping failed, http probe {'ok' if online else 'failed'}", "yellow")
                    )
            ctx.ping_cache.set(target.url, online)
        if not online:
            record["status"] = "Offline"
            record["error"] = "host unreachable"
            record["elapsed"] = f"{time.perf_counter() - started:.2f}"
            return record

    # ------------------------------------------------------------------ #
    # 2. login
    # ------------------------------------------------------------------ #
    need_admin = bool(args.check_permissions or args.check_plugin_install)
    outcome = login(
        ctx.http,
        target.url,
        target.username,
        target.password,
        verbose=args.verbose,
        log=ctx.log,
        waf_recheck=args.recheck_waf,
        recheck_attempts=args.recheck_attempts,
        fetch_admin=need_admin,
    )

    record["status"] = outcome.status
    record["http_code"] = outcome.http_code if outcome.http_code is not None else ""
    record["error"] = outcome.error or ""
    if outcome.bypass:
        record["waf"] = f"{outcome.waf or 'waf'} (bypassed: {outcome.bypass})"
    else:
        record["waf"] = outcome.waf or ""

    # A transport failure while the host itself was reachable is an error of
    # the scan path (dead proxy, Tor down, TLS problem) - not an offline site.
    if outcome.status == "Error" and outcome.details.get("network_error") and args.skip_ping:
        record["status"] = "Offline"
        record["error"] = outcome.error or "connection failed"

    if args.verbose and ctx.verbose_log:
        ctx.verbose_log(
            f"    {record['status']} http={record['http_code']} waf={record['waf'] or '-'} {record['error']}"
        )

    if outcome.status != "Valid":
        record["elapsed"] = f"{time.perf_counter() - started:.2f}"
        return record

    # ------------------------------------------------------------------ #
    # 3. enrichment (only for valid credentials, skipped when cancelling)
    # ------------------------------------------------------------------ #
    if ctx.cancel.is_set():
        record["elapsed"] = f"{time.perf_counter() - started:.2f}"
        return record

    if args.check_wp_version:
        record["wp_version"] = _probe_version(ctx, target.url, outcome.bypass_headers or None) or "Unknown"

    if need_admin and not ctx.cancel.is_set():
        data = _probe_permissions(
            ctx, target.url, target.username, outcome.admin_html, outcome.session, outcome.bypass_headers or None
        )
        if args.check_permissions:
            caps = data.get("capabilities_list") or []
            record["role"] = data.get("role", "")
            record["admin"] = "Yes" if data.get("admin") else "No"
            record["name"] = (data.get("display_name") or "")[:80]
            record["capabilities"] = ",".join(caps)
        if args.check_plugin_install:
            record["plugin_install"] = data.get("plugin_install", "")

    record["elapsed"] = f"{time.perf_counter() - started:.2f}"
    return record
