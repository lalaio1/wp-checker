"""Final summary rendering (works in cmd, powershell, xterm and Termux)."""

from __future__ import annotations

from utils import ansi

__all__ = ["print_proses", "print_summary"]


def _fmt_seconds(value: float) -> str:
    if value >= 3600:
        return f"{int(value // 3600)}h {int((value % 3600) // 60)}m {int(value % 60)}s"
    if value >= 60:
        return f"{int(value // 60)}m {int(value % 60)}s"
    return f"{value:.2f}s"


def print_summary(result, args, output_file: str, http_info: str = "") -> None:
    stats = result.stats.snapshot()
    duration = result.duration or 0.0
    processed = stats.get("processed", 0)
    rate = (processed / duration) if duration > 0 else 0.0

    def row(label: str, value: str) -> str:
        return f"{ansi.c(label.ljust(20), 'gray')} : {value}"

    lines = [
        row("Elapsed", _fmt_seconds(duration)),
        row("Targets processed", f"{processed:,} ({rate:,.1f}/s)"),
        row("Valid credentials", ansi.c(str(stats.get("valid", 0)), "bright_green", "bold")),
        row("Invalid credentials", f"{stats.get('invalid', 0):,}"),
        row("Offline / dead hosts", f"{stats.get('offline', 0):,}"),
    ]
    if stats.get("blocked"):
        lines.append(row("WAF blocked", ansi.c(str(stats["blocked"]), "bright_magenta")))
    if stats.get("error"):
        lines.append(row("Errors", ansi.c(str(stats["error"]), "bright_red")))
    if stats.get("notwp"):
        lines.append(row("Not WordPress", f"{stats['notwp']:,}"))

    if args.auto_threads:
        adjustments = result.scaler_events
        word = "adjustment" if adjustments == 1 else "adjustments"
        lines.append(
            row(
                "Threads (auto)",
                f"{result.workers_initial} → {result.workers_final} "
                f"({adjustments} {word}, {result.scaler_summary or 'n/a'})",
            )
        )
    else:
        lines.append(row("Threads", str(result.workers_final)))
    if http_info:
        lines.append(row("HTTP layer", http_info))

    box = ansi.box(lines, title="scan summary")
    print()
    print(ansi.gradient(box))

    files = []
    if result.report_path:
        files.append(f"report → {ansi.c(result.report_path, 'bright_cyan')}")
    for label, path in (
        ("valid", args.valid),
        ("invalid", args.invalid),
        ("offline", args.offline),
        ("blocked", args.blocked),
    ):
        if path:
            files.append(f"{label} → {ansi.c(path, 'bright_cyan')}")
    if files:
        print("  " + ansi.c("files:", "gray") + " " + "  |  ".join(files))
    if result.interrupted:
        print("  " + ansi.warn("scan interrupted by the user - partial report saved"))
    print()


def print_proses(results, start_time, end_time, output_file, args=None):  # legacy shim
    records = list(results) if not isinstance(results, dict) else []

    class _Stats:
        @staticmethod
        def snapshot():
            if isinstance(results, dict):
                return results
            return {
                "valid": sum(1 for r in records if r.get("status") == "Valid"),
                "invalid": sum(1 for r in records if r.get("status") == "Invalid"),
                "offline": sum(1 for r in records if r.get("status") == "Offline"),
                "blocked": sum(1 for r in records if r.get("status") == "Blocked"),
                "error": sum(1 for r in records if r.get("status") == "Error"),
                "processed": len(records),
            }

    class _Result:
        duration = end_time - start_time
        interrupted = False
        workers_initial = 0
        workers_final = 0
        scaler_events = 0
        scaler_summary = ""
        report_path = output_file
        stats = _Stats()

    class _Args:
        auto_threads = False
        valid = invalid = offline = blocked = None

    print_summary(_Result(), args or _Args(), output_file)
