"""Streaming multi-threaded scan engine.

Design notes (these are the fixes for the old *memory* and *thread* crashes):

* **Threads, not processes.** The workload is 100% network bound, so a thread
  pool gives the same or better throughput while using a fraction of the RAM
  (no fork, no re-import of requests/bs4 per worker).
* **Streaming input, streaming output.** Targets are parsed lazily from the
  file and every result is written to the report as soon as it is produced, so
  a 500 MB credential list no longer needs 500 MB of RAM.
* **Bounded in-flight work.** A dynamic semaphore caps how many requests are
  happening at once; the producer never queues more than ``workers * 2`` tasks.
* **Adaptive concurrency (``--auto-threads``).** A monitor thread samples the
  machine in real time and raises/lowers the ceiling, which prevents the
  freezing/OOM behaviour on low-end laptops, Raspberry Pis and Termux.
* **Graceful Ctrl-C.** Pending tasks are cancelled and the partial report is
  flushed instead of losing everything.
"""

from __future__ import annotations

import concurrent.futures
import os
import time

from func.context import ScanContext, ScanStats, STATUS_KEYS
from func.process_url import process_target
from func.report import ReportWriter
from func.save_to_file import BlockWriter
from utils.ansi import c
from utils.parse import count_targets, iter_targets
from utils.resources import AutoScaler

__all__ = ["run_scan", "ScanResult"]


class ScanResult:
    def __init__(self) -> None:
        self.stats = ScanStats()
        self.duration = 0.0
        self.total = 0
        self.interrupted = False
        self.stuck_workers = 0
        self.workers_final = 0
        self.workers_initial = 0
        self.scaler_events = 0
        self.scaler_summary = ""
        self.report_path = ""


def _worker(ctx: ScanContext, target):
    capacity = ctx.capacity
    if capacity is not None:
        if not capacity.acquire(cancel=ctx.cancel):
            return None
    try:
        if ctx.cancel.is_set():
            return None
        return process_target(ctx, target)
    except Exception as exc:  # a single bad target must never stop the scan
        record = {
            "url": target.url,
            "username": target.username,
            "password": target.password,
            "status": "Error",
            "error": f"worker exception: {exc.__class__.__name__}: {exc}"[:300],
            "elapsed": "",
            "wp_version": "",
            "role": "",
            "admin": "",
            "name": "",
            "capabilities": "",
            "plugin_install": "",
            "http_code": "",
            "waf": "",
        }
        return record
    finally:
        if capacity is not None:
            capacity.release()


def run_scan(args, ctx: ScanContext, bar=None, result: ScanResult | None = None) -> ScanResult:
    """Execute the scan described by ``args`` using ``ctx`` resources."""
    result = result or ScanResult()
    result.stats = ctx.stats

    # ------------------------------------------------------------------ #
    # sanity checks
    # ------------------------------------------------------------------ #
    path = args.file
    if not os.path.isfile(path):
        raise FileNotFoundError(f"targets file not found: {path}")
    if os.path.getsize(path) == 0:
        raise ValueError(f"targets file is empty: {path}")

    if bar is not None:
        bar.set_total(count_targets(path, dedupe=args.dedupe, limit=args.limit))
        bar.set_threads(0, ctx.capacity.capacity if ctx.capacity else args.threads)

    # ------------------------------------------------------------------ #
    # writers
    # ------------------------------------------------------------------ #
    valid_writer = BlockWriter(args.valid, append=args.append) if args.valid else None
    invalid_writer = BlockWriter(args.invalid, append=args.append) if args.invalid else None
    offline_writer = BlockWriter(args.offline, append=args.append) if args.offline else None
    blocked_writer = BlockWriter(args.blocked, append=args.append) if args.blocked else None

    output_file = f"{args.output}.{args.format}"
    if args.output_dir and args.output_dir != ".":
        os.makedirs(args.output_dir, exist_ok=True)
        output_file = os.path.join(args.output_dir, output_file)
    report = (
        ReportWriter(output_file, args.format)
        if not args.no_report
        else ReportWriter(None, "none")
    )
    result.report_path = report.path or ""

    scaler = None
    if ctx.capacity is not None and args.auto_threads:
        scaler = AutoScaler(
            ctx.capacity,
            max_cpu=args.max_cpu,
            max_mem=args.max_mem,
            log=ctx.log,
            verbose=args.verbose,
        )
        if scaler.sampler.backend == "none":
            if ctx.log:
                ctx.log(c("auto-threads: no resource backend (install psutil)", "yellow"))
            scaler = None
        else:
            scaler.start()
            result.workers_initial = ctx.capacity.capacity

    workers = ctx.capacity.maximum if ctx.capacity else max(1, args.threads)
    max_inflight = max(workers * 2, workers + 8)
    started = time.time()

    def consume(record: dict | None) -> None:
        if not record:
            return
        key = STATUS_KEYS.get(record.get("status", "Error"), "error")
        ctx.stats.add(key)
        if bar is not None:
            bar.advance(counter=key)
            bar.set_threads(
                ctx.capacity.in_use if ctx.capacity else 0,
                ctx.capacity.capacity if ctx.capacity else args.threads,
                scaled_down=bool(ctx.capacity and ctx.capacity.scaled_down),
            )
        report.write(record)

        status = record.get("status")
        if status == "Valid":
            if valid_writer:
                valid_writer.write(record)
            extras = []
            if record.get("role"):
                extras.append(f"role={record['role']}")
            if record.get("plugin_install"):
                extras.append(f"plugins={record['plugin_install']}")
            if record.get("wp_version"):
                extras.append(f"wp={record['wp_version']}")
            if record.get("waf"):
                extras.append(f"waf={record['waf']}")
            suffix = (" " + " ".join(extras)) if extras else ""
            line = c("VALID", "bright_green", "bold")
            message = f"  {line} {record['url']}  {record['username']}:{record['password']}{c(suffix, 'gray')}"
            if bar is not None:
                bar.log(message)
            else:
                print(message, flush=True)
        elif status == "Blocked":
            if blocked_writer:
                blocked_writer.write(record)
            if args.verbose and bar is not None:
                bar.log(c(f"  BLOCKED {record['url']} ({record.get('waf') or record.get('error', '')})", "bright_magenta"))
        elif status == "Invalid":
            if invalid_writer:
                invalid_writer.write(record)
        elif status == "Offline":
            if offline_writer:
                offline_writer.write(record)

    interrupted = False
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wpck")
    inflight: dict[concurrent.futures.Future, object] = {}
    try:
        targets = iter_targets(args.file, dedupe=args.dedupe, limit=args.limit)
        exhausted = False
        last_threads_update = 0.0

        while not exhausted or inflight:
            while not exhausted and len(inflight) < max_inflight:
                try:
                    target = next(targets)
                except StopIteration:
                    exhausted = True
                    break
                inflight[pool.submit(_worker, ctx, target)] = target

            if not inflight:
                break

            done, _pending = concurrent.futures.wait(
                inflight, timeout=0.25, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                inflight.pop(future, None)
                try:
                    record = future.result()
                except Exception as exc:  # pragma: no cover - safety net
                    record = {
                        "url": "",
                        "username": "",
                        "password": "",
                        "status": "Error",
                        "error": str(exc),
                    }
                consume(record)

            now = time.time()
            if bar is not None and now - last_threads_update > 0.5:
                last_threads_update = now
                bar.set_threads(
                    ctx.capacity.in_use if ctx.capacity else 0,
                    ctx.capacity.capacity if ctx.capacity else workers,
                    scaled_down=bool(ctx.capacity and ctx.capacity.scaled_down),
                )
                bar.refresh_bar()
    except KeyboardInterrupt:
        interrupted = True
        ctx.cancel.set()
        if bar is not None:
            bar.log(c("  interrupted - flushing partial results...", "yellow"))
        for future in list(inflight):
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)

        # Gently drain whatever was already running so those results are kept.
        # A second Ctrl-C abandons them immediately, and the grace period keeps
        # a hanging target from freezing the operator's terminal.
        grace = max(0.0, float(getattr(args, "interrupt_grace", 5.0)))
        deadline = time.time() + grace
        while inflight and time.time() < deadline:
            try:
                done, _pending = concurrent.futures.wait(
                    inflight, timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED
                )
            except KeyboardInterrupt:
                if bar is not None:
                    bar.log(c("  abandoning in-flight requests", "yellow"))
                break
            for future in done:
                inflight.pop(future, None)
                try:
                    consume(future.result())
                except Exception:
                    pass
        result.stuck_workers = len(inflight)
        if result.stuck_workers and bar is not None:
            bar.log(
                c(
                    f"  {result.stuck_workers} request(s) still running - results already saved",
                    "yellow",
                )
            )
    finally:
        ctx.cancel.set()
        if scaler is not None:
            scaler.stop()
        pool.shutdown(wait=False, cancel_futures=True)
        if bar is not None:
            bar.refresh_bar(force=True)
        report.close()
        for writer in (valid_writer, invalid_writer, offline_writer, blocked_writer):
            if writer:
                writer.close()
        if ctx.http is not None:
            ctx.http.close()

    result.duration = time.time() - started
    result.total = ctx.stats.snapshot().get("processed", 0)
    result.interrupted = interrupted
    result.workers_final = ctx.capacity.capacity if ctx.capacity else workers
    result.workers_initial = result.workers_initial or (ctx.capacity.initial if ctx.capacity else workers)
    if scaler is not None:
        result.scaler_events = scaler.events
        result.scaler_summary = scaler.summary()
    return result


# Keep a backwards compatible helper for anyone importing the old name.
def process_file(args, bar=None):  # pragma: no cover - legacy shim
    from func.factory import build_context

    ctx = build_context(args)
    return run_scan(args, ctx, bar=bar)
