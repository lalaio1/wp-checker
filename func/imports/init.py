"""Convenience re-exports.

``from func.imports.init import *`` keeps working for anyone (or any script)
written against the older layout, while the package now lives in focused,
single-responsibility modules.
"""

from func.check_permissions import probe_capabilities
from func.check_wp_credentials import login as check_wp_credentials
from func.check_wp_version import check_wp_version
from func.context import ScanContext, ScanStats
from func.factory import build_context
from func.net import HttpPool
from func.ping_site import ping_site
from func.print_proses import print_summary
from func.process_file import run_scan
from func.process_url import process_target
from func.proxy import ProxyPool, TorController, tor_proxies
from func.report import FIELDS, FORMATS, ReportWriter, generate_report
from func.save_to_file import BlockWriter as save_to_file
from func.waf import detect_waf, is_blocked, recheck

__all__ = [
    "probe_capabilities",
    "check_wp_credentials",
    "check_wp_version",
    "ScanContext",
    "ScanStats",
    "build_context",
    "HttpPool",
    "ping_site",
    "print_summary",
    "run_scan",
    "process_target",
    "ProxyPool",
    "TorController",
    "tor_proxies",
    "FIELDS",
    "FORMATS",
    "ReportWriter",
    "generate_report",
    "save_to_file",
    "detect_waf",
    "is_blocked",
    "recheck",
]
