"""A dependency-free, thread-safe progress bar that plays nice with cmd/terminfo.

Features
--------
* adaptive width (works on 80 column cmd windows and wide terminals alike)
* unicode/ascii fallback, colour aware, truecolour independent
* live counters (valid / invalid / offline / blocked / errors), speed and ETA
* ``log()`` prints a message *above* the bar without breaking the rendering
* adaptive-thread indicator (``th 24/100``) when auto scaling is enabled
* disabled mode for non-tty output (``--progress none`` / redirected output)
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque

from utils import ansi

__all__ = ["ProgressBar"]


class ProgressBar:
    """Render a single, continuously updated status line."""

    COUNTER_ORDER = ("valid", "invalid", "offline", "blocked", "notwp", "error")
    COUNTER_STYLE = {
        "valid": ("bright_green", "✔", "V"),
        "invalid": ("red", "✘", "I"),
        "offline": ("yellow", "⚑", "O"),
        "blocked": ("bright_magenta", "⛔", "B"),
        "notwp": ("bright_blue", "◌", "N"),
        "error": ("bright_red", "!", "E"),
    }

    def __init__(
        self,
        total: int | None = None,
        title: str = "Scanning",
        enabled: bool = True,
        stream=None,
        refresh: float = 0.1,
        show_rate: bool = True,
        show_eta: bool = True,
        show_fraction: bool = True,
        show_counters: bool = True,
        show_threads: bool = True,
        color: bool | None = None,
    ) -> None:
        self.total = total if (total is None or total > 0) else None
        self.title = title
        self.enabled = bool(enabled)
        self.stream = stream or sys.stdout
        self.refresh = refresh
        self.show_rate = show_rate
        self.show_eta = show_eta
        self.show_fraction = show_fraction
        self.show_counters = show_counters
        self.show_threads = show_threads
        self.uni = ansi.unicode_enabled()
        if color is not None:
            ansi.enable_ansi(color)

        self.done = 0
        self.counters = {key: 0 for key in self.COUNTER_ORDER}
        self.threads_active = 0
        self.threads_capacity = 0
        self.scaled_down = 0
        self.start_time = time.time()
        self._last_render = 0.0
        self._line_len = 0
        self._printed_anything = False
        self._samples: deque[tuple[float, int]] = deque(maxlen=64)
        self._samples.append((self.start_time, 0))
        self._lock = threading.RLock()
        self._closed = False
        self._spinner_idx = 0

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def add(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + amount

    def advance(self, amount: int = 1, counter: str | None = None, **extra) -> None:
        with self._lock:
            self.done += amount
            if counter:
                self.counters[counter] = self.counters.get(counter, 0) + amount
            for key, value in extra.items():
                if value:
                    self.counters[key] = self.counters.get(key, 0) + value
            if self.total is None:
                self._samples.append((time.time(), self.done))
        self.refresh_bar()

    def set_total(self, total: int | None) -> None:
        with self._lock:
            self.total = total if (total is None or total > 0) else None
            self._samples.clear()
            self._samples.append((time.time(), self.done))
        self.refresh_bar(force=True)

    def set_threads(self, active: int, capacity: int, scaled_down: int = 0) -> None:
        with self._lock:
            self.threads_active = active
            self.threads_capacity = capacity
            self.scaled_down = scaled_down

    # ------------------------------------------------------------------ #
    # rendering
    # ------------------------------------------------------------------ #
    def _rate(self) -> float:
        now = time.time()
        if len(self._samples) >= 2:
            first_t, first_done = self._samples[0]
            span = now - first_t
            if span >= 0.75:
                speed = (self.done - first_done) / span
                if speed > 0:
                    return speed
        span = now - self.start_time
        return (self.done / span) if span > 0.5 and self.done else 0.0

    def _format_eta(self, seconds: float) -> str:
        if seconds != seconds or seconds in (float("inf"), float("-inf")) or seconds < 0:
            return "--:--"
        seconds = int(seconds)
        if seconds >= 3600:
            return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _bar(self, width: int, ratio: float) -> str:
        if self.uni:
            fill, empty = "█", "░"
            left, right = "▏", "▕"
        else:
            fill, empty = "#", "-"
            left = right = "|"
        filled = int(max(0.0, min(1.0, ratio)) * width)
        return left + (fill * filled) + (empty * (width - filled)) + right

    def _build_line(self, width: int) -> str:
        with self._lock:
            total, done = self.total, self.done
            ratio = (done / total) if total else 0.0
            percent = f"{ratio * 100:5.1f}%"

            if self.uni:
                glyphs = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if total is None else None
            else:
                glyphs = "|/-\\" if total is None else None

            head = ansi.c(f"[wp-checker]", "bright_blue", "bold")
            if glyphs:
                self._spinner_idx = (self._spinner_idx + 1) % len(glyphs)
                head = f"{head} {ansi.c(glyphs[self._spinner_idx], 'cyan')}"

            if total:
                fraction = f"{done:,}/{total:,}"
            else:
                fraction = f"{done:,}"

            counters = []
            if self.show_counters:
                for key in self.COUNTER_ORDER:
                    value = self.counters.get(key, 0)
                    if not value:
                        continue
                    style, sym, ascii_sym = self.COUNTER_STYLE[key]
                    label = sym if self.uni else ascii_sym
                    counters.append(ansi.c(f"{label} {value:,}", style))

            rate_txt = f"{self._rate():,.1f}/s" if self.show_rate else ""
            eta_txt = ""
            if self.show_eta and total and ratio > 0:
                remaining = total - done
                speed = self._rate()
                if speed > 0:
                    eta_txt = "ETA " + self._format_eta(remaining / speed)

            threads_txt = ""
            if self.show_threads and self.threads_capacity:
                color = "bright_yellow" if self.scaled_down else "cyan"
                threads_txt = ansi.c(
                    f"th {self.threads_active}/{self.threads_capacity}", color
                )

            head_parts = [head, percent]
            if self.show_fraction:
                head_parts.append(fraction)
            fixed = " │ ".join(head_parts)
            optional = []
            if counters:
                optional.append("  ".join(counters))
            optional.extend([rate_txt, eta_txt, threads_txt])

            bar_min = 10
            # drop optional pieces until everything fits
            while True:
                fixed_len = ansi.visible_len(fixed)
                tail = " │ ".join([p for p in optional if p])
                tail_len = ansi.visible_len(tail) + (3 if tail else 0)
                bar_width = max(bar_min, width - fixed_len - tail_len - 6)
                if fixed_len + tail_len + bar_width + 6 <= width or not optional:
                    break
                # remove the widest optional piece, right-most first
                removed = False
                for idx in range(len(optional) - 1, -1, -1):
                    if optional[idx]:
                        optional[idx] = ""
                        removed = True
                        break
                if not removed:
                    break

            bar = self._bar(bar_width, ratio or 0.0)
            bar = ansi.c(bar, "bright_green" if ratio >= 1 and total else "cyan")
            parts = [fixed, bar]
            tail = " │ ".join([p for p in optional if p])
            if tail:
                parts.append(tail)
            line = " │ ".join(parts)
            if ansi.visible_len(line) > width:
                line = ansi.strip_ansi(line)[: max(0, width - 1)]
            return line

    def refresh_bar(self, force: bool = False) -> None:
        if not self.enabled or self._closed:
            return
        now = time.time()
        if not force and (now - self._last_render) < self.refresh:
            return
        with self._lock:
            line = self._build_line(ansi.terminal_width() - 1)
            try:
                self.stream.write("\r\033[K" + line)
                self.stream.flush()
            except Exception:
                return
            self._line_len = ansi.visible_len(line)
            self._printed_anything = True
            self._last_render = now

    def clear(self) -> None:
        if not self.enabled or not self._printed_anything:
            return
        try:
            self.stream.write("\r\033[K")
            self.stream.flush()
        except Exception:
            pass
        self._printed_anything = False

    def log(self, message: str, end: str = "\n") -> None:
        """Print ``message`` above the bar (thread safe)."""
        with self._lock:
            if not self.enabled:
                try:
                    self.stream.write(message + end)
                    self.stream.flush()
                except Exception:
                    pass
                return
            self.clear()
            try:
                self.stream.write(message + end)
                self.stream.flush()
            except Exception:
                pass
            self._last_render = 0.0
            self.refresh_bar(force=True)

    def close(self, final_message: str | None = None) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if not self.enabled:
                if final_message:
                    try:
                        self.stream.write(final_message + "\n")
                        self.stream.flush()
                    except Exception:
                        pass
                return
            self._render_final()
            try:
                self.stream.write("\n")
                self.stream.flush()
            except Exception:
                pass

    def _render_final(self) -> None:
        self._closed = False
        self.enabled = True
        try:
            self.refresh_bar(force=True)
        finally:
            self._closed = True
