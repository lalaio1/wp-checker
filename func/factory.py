"""Build the runtime objects (HTTP pool, proxies, Tor, capacity, caches)."""

from __future__ import annotations

from func.context import ScanContext
from func.net import HttpPool
from func.proxy import ProxyPool, TorController, tor_proxies
from utils.ansi import c
from utils.resources import DynamicCapacity
from utils.user_agents import AgentPool

__all__ = ["build_agent_pool", "build_context"]


def build_agent_pool(args) -> AgentPool:
    return AgentPool(
        fixed=getattr(args, "user_agent", None),
        randomize=getattr(args, "random_agent", False),
        file_path=getattr(args, "user_agent_file", None),
    )


def build_context(args, log=None, verbose_log=None) -> ScanContext:
    """Assemble the scan context from the parsed CLI arguments."""
    agents = build_agent_pool(args)

    proxy_pool = ProxyPool(
        single=getattr(args, "proxy", None),
        file_path=getattr(args, "proxy_file", None),
        log=log,
        randomize=getattr(args, "proxy_random", False),
    )

    tor_controller = None
    static_proxies = None
    if getattr(args, "tor", False):
        static_proxies = tor_proxies(args.tor_host, args.tor_port)
        tor_controller = TorController(
            host=args.tor_host,
            port=args.tor_control_port,
            password=getattr(args, "tor_password", None),
            log=log,
        )
        if log:
            log(c(f"tor enabled: socks5h {args.tor_host}:{args.tor_port}", "bright_magenta"))
    elif proxy_pool.enabled and log:
        log(c(f"using {proxy_pool.count} proxy(ies)", "bright_magenta"))

    http = HttpPool(
        timeout=args.timeout,
        connect_timeout=min(args.connect_timeout, args.timeout) if getattr(args, "connect_timeout", None) else None,
        retries=args.retries,
        verify=not args.no_verify,
        proxies=static_proxies,
        proxy_pool=proxy_pool if proxy_pool.enabled else None,
        agents=agents,
        delay=args.delay,
        verbose=args.verbose,
        log=verbose_log,
    )

    capacity = None
    if args.auto_threads:
        capacity = DynamicCapacity(
            initial=max(1, args.threads),
            minimum=max(1, args.min_threads),
            maximum=max(args.threads, args.max_threads),
        )
    else:
        capacity = DynamicCapacity(initial=max(1, args.threads), minimum=max(1, args.threads), maximum=max(1, args.threads))

    ctx = ScanContext(args=args, http=http, log=log, verbose_log=verbose_log, capacity=capacity)
    ctx.tor_controller = tor_controller
    ctx.tor_rotate_every = getattr(args, "tor_rotate", 0) or 0
    ctx.proxy_count = proxy_pool.count if proxy_pool.enabled else 0
    return ctx
