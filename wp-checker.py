#!/usr/bin/env python3
"""wp-checker - advanced WordPress credential & permission checker.

Rewrite highlights (see README for the full story):

* thread pool + streaming I/O  -> no more memory/thread crashes
* --auto-threads               -> concurrency follows the machine in real time
* --check-permissions          -> role + real capability matrix per account
* --check-plugin-install       -> can the account install/upload/update?
* --tor / --proxy / --proxy-file
* --random-agent / --user-agent / --user-agent-file
* --recheck-waf                -> 403/429 evasion retries
* --timeout / --retries / --verbose / --progress
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from utils import ansi

VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.txt")
try:
    with open(VERSION_FILE, "r", encoding="utf-8") as _handle:
        VERSION = _handle.read().strip() or "dev"
except OSError:
    VERSION = "dev"

from banner.banner import render_banner  # noqa: E402
from func.factory import build_context  # noqa: E402
from func.process_file import run_scan  # noqa: E402
from func.report import FORMATS  # noqa: E402
from func.print_proses import print_summary  # noqa: E402
from loggin.logging_config import setup_logging, set_log_sink  # noqa: E402
from utils.progress import ProgressBar  # noqa: E402

EPILOG = """examples:
  # basic scan, 30 threads, keep everything in files
  python wp-checker.py targets.txt -v valid.txt -i invalid.txt -off offline.txt -t 30

  # let the tool decide how many threads your machine can take
  python wp-checker.py targets.txt -A --min-threads 5 --max-threads 200

  # full recon on the accounts that log in successfully
  python wp-checker.py targets.txt --check-permissions --check-plugin-install --check-wp-version

  # stealth mode: Tor + random user agents + 1.5s delay + retries on 403/429
  python wp-checker.py targets.txt --tor --random-agent -d 1.5 --recheck-waf --timeout 30

  # a single credential to double check, verbose and silent progress
  echo "https://example.com:admin:senha" > one.txt
  python wp-checker.py one.txt --progress none --verbose --check-permissions
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wp-checker",
        description=(
            "Advanced WordPress credential checker with permission/role enumeration, "
            "WAF evasion retries, adaptive threading, Tor support and streaming reports."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )

    parser.add_argument(
        "file",
        help=(
            "Targets file. Accepts both 'url:user:password' lines and block files "
            "(URL:/Username:/Password:) - including the ones this tool writes itself."
        ),
    )

    out = parser.add_argument_group("output")
    out.add_argument("-v", "--valid", metavar="FILE", help="save valid credentials to FILE")
    out.add_argument("-i", "--invalid", metavar="FILE", help="save invalid credentials to FILE")
    out.add_argument("-off", "--offline", metavar="FILE", help="save unreachable hosts to FILE")
    out.add_argument("--blocked", metavar="FILE", help="save WAF-blocked targets to FILE")
    out.add_argument("-o", "--output", default="report", metavar="NAME", help="report name without extension (default: report)")
    out.add_argument("-f", "--format", default="csv", choices=FORMATS, help="report format (default: csv)")
    out.add_argument("-od", "--output-dir", default=".", metavar="DIR", help="directory for the report file (default: current)")
    out.add_argument("--no-report", action="store_true", help="do not write the aggregated report")
    out.add_argument("--append", action="store_true", help="append to -v/-i/-off/--blocked files instead of overwriting")

    checks = parser.add_argument_group("checks")
    checks.add_argument("-cwv", "--check-wp-version", action="store_true", help="detect the WordPress version of each valid host")
    checks.add_argument(
        "-perm",
        "--check-permissions",
        action="store_true",
        help="enumerate the account role, display name and real capability matrix (administrator? editor? subscriber?)",
    )
    checks.add_argument(
        "-pi",
        "--check-plugin-install",
        action="store_true",
        help="check whether the account can install/upload new plugins and update the core",
    )
    checks.add_argument("-s", "--skip-ping", action="store_true", help="skip the reachability probe (faster, less accurate offline detection)")
    checks.add_argument("--ping-timeout", type=float, default=6.0, metavar="SEC", help="TCP reachability timeout (default: 6)")
    checks.add_argument("-w", "--recheck-waf", action="store_true", help="retry 403/429/503 and WAF interstitials with evasion techniques")
    checks.add_argument("--recheck-attempts", type=int, default=8, metavar="N", help="max evasion attempts per blocked request (default: 8)")

    perf = parser.add_argument_group("performance")
    perf.add_argument("-t", "--threads", type=int, default=10, metavar="N", help="worker threads (default: 10)")
    perf.add_argument(
        "-A",
        "--auto-threads",
        action="store_true",
        help="adapt the thread count to the machine's CPU/RAM in real time (prevents freezes and OOM)",
    )
    perf.add_argument("--min-threads", type=int, default=2, metavar="N", help="lower bound used by --auto-threads (default: 2)")
    perf.add_argument("--max-threads", type=int, default=100, metavar="N", help="upper bound used by --auto-threads (default: 100)")
    perf.add_argument("--max-cpu", type=float, default=85.0, metavar="PCT", help="CPU ceiling for --auto-threads (default: 85)")
    perf.add_argument("--max-mem", type=float, default=85.0, metavar="PCT", help="RAM ceiling for --auto-threads (default: 85)")
    perf.add_argument("-d", "--delay", type=float, default=0.0, metavar="SEC", help="delay between requests (default: 0)")
    perf.add_argument("--limit", type=int, default=0, metavar="N", help="only process the first N targets (0 = all)")
    perf.add_argument("--dedupe", action="store_true", help="skip duplicated url:user:password triples")

    net = parser.add_argument_group("network, stealth & evasion")
    net.add_argument("--timeout", type=float, default=15.0, metavar="SEC", help="HTTP read timeout in seconds (default: 15)")
    net.add_argument("--connect-timeout", type=float, default=6.0, metavar="SEC", help="HTTP connect timeout in seconds (default: 6)")
    net.add_argument("--retries", type=int, default=2, metavar="N", help="network level retries per request (default: 2)")
    net.add_argument("-ua", "--user-agent", metavar="UA", help="use a fixed User-Agent string")
    net.add_argument("-ra", "--random-agent", action="store_true", help="pick a random User-Agent for every target")
    net.add_argument("--user-agent-file", metavar="FILE", help="custom User-Agent list (one per line, used with --random-agent)")
    net.add_argument("--proxy", metavar="URL", help="single proxy, e.g. http://127.0.0.1:8080 or socks5://127.0.0.1:1080")
    net.add_argument("--proxy-file", metavar="FILE", help="rotate through the proxies listed in FILE")
    net.add_argument("--proxy-random", action="store_true", help="pick proxies randomly instead of round-robin")
    net.add_argument("--tor", action="store_true", help="route every request through Tor (SOCKS5, DNS resolved by Tor)")
    net.add_argument("--tor-host", default="127.0.0.1", metavar="HOST", help="Tor SOCKS host (default: 127.0.0.1)")
    net.add_argument("--tor-port", type=int, default=9050, metavar="PORT", help="Tor SOCKS port (default: 9050)")
    net.add_argument("--tor-control-port", type=int, default=9051, metavar="PORT", help="Tor control port (default: 9051)")
    net.add_argument("--tor-password", metavar="PASS", help="Tor control password (if not using cookie auth)")
    net.add_argument("--tor-rotate", type=int, default=0, metavar="N", help="request a new Tor circuit every N targets (0 = never)")
    net.add_argument("--no-verify", action="store_true", help="do not validate TLS certificates")

    misc = parser.add_argument_group("interface")
    misc.add_argument("--verbose", action="store_true", help="print every request/decision as it happens")
    misc.add_argument("--progress", choices=("auto", "plain", "none"), default="auto", help="progress bar mode (default: auto)")
    misc.add_argument("--no-color", action="store_true", help="disable ANSI colours")
    misc.add_argument("--no-banner", action="store_true", help="do not print the ASCII banner")
    misc.add_argument("--log-file", metavar="FILE", help="also write the debug log to FILE")
    misc.add_argument(
        "--interrupt-grace",
        type=float,
        default=5.0,
        metavar="SEC",
        help="after Ctrl-C, how long to wait for running requests before exiting (default: 5)",
    )
    misc.add_argument("--version", action="version", version=f"wp-checker {VERSION}")
    return parser


def validate_args(args, parser) -> None:
    if args.threads < 1:
        parser.error("--threads must be >= 1")
    if args.min_threads < 1:
        parser.error("--min-threads must be >= 1")
    if args.max_threads < args.min_threads:
        parser.error("--max-threads must be >= --min-threads")
    if args.timeout <= 0 or args.connect_timeout <= 0:
        parser.error("--timeout/--connect-timeout must be > 0")
    if args.recheck_attempts < 1:
        parser.error("--recheck-attempts must be >= 1")
    if not os.path.isfile(args.file):
        parser.error(f"targets file not found: {args.file}")
    if args.tor and args.proxy:
        print("\033[33m[!]\033[0m --tor overrides --proxy\n", file=sys.stderr)


def banner_head(args) -> None:
    if args.no_banner:
        return
    print(ansi.gradient(render_banner(VERSION)))


def config_line(args) -> str:
    enabled = []
    if args.check_permissions:
        enabled.append("permissions")
    if args.check_plugin_install:
        enabled.append("plugin-install")
    if args.check_wp_version:
        enabled.append("wp-version")
    if args.recheck_waf:
        enabled.append(f"waf-recheck({args.recheck_attempts})")
    bits = [
        f"threads={args.threads}" + (" (auto)" if args.auto_threads else ""),
        f"timeout={args.timeout:g}s",
        f"retries={args.retries}",
        f"format={args.format}",
    ]
    if args.delay:
        bits.append(f"delay={args.delay:g}s")
    if args.random_agent:
        bits.append("random-ua")
    elif args.user_agent:
        bits.append("fixed-ua")
    if args.tor:
        bits.append(f"tor={args.tor_host}:{args.tor_port}")
    if args.proxy or args.proxy_file:
        bits.append("proxy")
    if enabled:
        bits.append("checks=" + ",".join(enabled))
    return "  ".join(bits)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(args, parser)

    if args.no_color or args.progress == "plain":
        ansi.enable_ansi(False)
    else:
        ansi.enable_ansi()
    if args.progress == "plain":
        ansi.set_unicode(False)

    banner_head(args)

    logger = setup_logging(verbose=args.verbose, log_file=args.log_file)

    bar_enabled = args.progress != "none" and (
        args.progress == "plain" or (sys.stdout.isatty() or os.environ.get("FORCE_COLOR"))
    )
    bar = ProgressBar(total=None, enabled=bar_enabled, title="scanning")

    def log(message: str) -> None:
        bar.log(message)

    def verbose_log(message: str) -> None:
        if args.verbose:
            bar.log(ansi.c(message, "gray") if not message.startswith("\033") else message)

    set_log_sink(log)

    print(ansi.info(f"targets file: {ansi.c(args.file, 'bright_cyan')}"))
    print(ansi.info(config_line(args)))
    http_info = ""

    if args.format == "parquet":
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            print(ansi.err("parquet output requires pyarrow: pip install pyarrow"))
            return 2

    try:
        ctx = build_context(args, log=log, verbose_log=verbose_log)
    except Exception as exc:
        print(ansi.err(f"could not initialise the scan: {exc}"))
        return 2

    http_info = ctx.http.describe()
    if args.verbose:
        print(ansi.info(f"http: {http_info}"))
    print()

    started = time.time()
    try:
        result = run_scan(args, ctx, bar=bar)
    except FileNotFoundError as exc:
        bar.close()
        print(ansi.err(str(exc)))
        return 2
    except ValueError as exc:
        bar.close()
        print(ansi.err(str(exc)))
        return 2
    except RuntimeError as exc:
        bar.close()
        print(ansi.err(str(exc)))
        return 2
    except KeyboardInterrupt:
        bar.close()
        print(ansi.warn("aborted"))
        return 130
    except Exception as exc:  # pragma: no cover - unexpected
        bar.close()
        logger.exception("fatal error")
        print(ansi.err(f"unexpected error: {exc}"))
        return 1

    bar.close()

    output_file = result.report_path or f"{args.output}.{args.format}"
    print_summary(result, args, output_file, http_info=http_info)

    if result.stuck_workers:
        # The report is flushed and closed; a hanging socket must not keep the
        # operator waiting after Ctrl-C.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(130)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
