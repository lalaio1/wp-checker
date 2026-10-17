"""Real-time resource monitoring and adaptive concurrency control.

The goal is simple: never let the scanner freeze the machine it runs on.  When
``--auto-threads`` is enabled a monitor thread samples CPU and RAM usage and
grows/shrinks the number of in-flight requests (AIMD: additive increase,
multiplicative decrease) inside the user provided window.

``psutil`` is used when available, otherwise a zero dependency ``/proc``
fallback keeps the feature working on Linux boxen without extra packages.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from utils.ansi import c

__all__ = ["Sample", "ResourceSampler", "DynamicCapacity", "AutoScaler"]


@dataclass
class Sample:
    cpu: float | None
    mem: float | None
    ts: float

    @property
    def usable(self) -> bool:
        return self.cpu is not None or self.mem is not None


class ResourceSampler:
    """Return CPU (0-100) and RAM (0-100) usage with graceful degradation."""

    def __init__(self) -> None:
        self.backend = "none"
        self._psutil = None
        self._prev_cpu: tuple[float, float] | None = None  # (total, idle)
        try:  # preferred backend
            import psutil  # type: ignore

            self._psutil = psutil
            self.backend = "psutil"
            psutil.cpu_percent(interval=None)  # prime the internal delta
        except Exception:
            import os

            if os.path.exists("/proc/stat"):
                self.backend = "proc"

    def sample(self) -> Sample:
        now = time.time()
        cpu = self._cpu()
        mem = self._mem()
        return Sample(cpu=cpu, mem=mem, ts=now)

    # ------------------------------------------------------------------ #
    def _cpu(self) -> float | None:
        if self._psutil is not None:
            try:
                return float(self._psutil.cpu_percent(interval=None))
            except Exception:
                return None
        if self.backend == "proc":
            return self._cpu_proc()
        return None

    def _cpu_proc(self) -> float | None:
        try:
            with open("/proc/stat", "r") as handle:
                fields = handle.readline().split()[1:]
            values = [float(v) for v in fields]
            idle = values[3] + (values[4] if len(values) > 4 else 0.0)
            total = sum(values)
        except Exception:
            return None
        if self._prev_cpu is None:
            self._prev_cpu = (total, idle)
            return None
        prev_total, prev_idle = self._prev_cpu
        self._prev_cpu = (total, idle)
        delta_total = total - prev_total
        delta_idle = idle - prev_idle
        if delta_total <= 0:
            return None
        return max(0.0, min(100.0, (1.0 - delta_idle / delta_total) * 100.0))

    def _mem(self) -> float | None:
        if self._psutil is not None:
            try:
                return float(self._psutil.virtual_memory().percent)
            except Exception:
                return None
        if self.backend == "proc":
            try:
                info = {}
                with open("/proc/meminfo", "r") as handle:
                    for line in handle:
                        key, _, rest = line.partition(":")
                        info[key.strip()] = float(rest.strip().split()[0])
                total = info.get("MemTotal") or 0.0
                available = info.get("MemAvailable")
                if available is None:
                    available = info.get("MemFree", 0.0) + info.get("Buffers", 0.0) + info.get("Cached", 0.0)
                if total <= 0:
                    return None
                return max(0.0, min(100.0, (1.0 - available / total) * 100.0))
            except Exception:
                return None
        return None


class DynamicCapacity:
    """Semaphore whose ceiling can be changed while workers are blocked on it."""

    def __init__(self, initial: int, minimum: int = 1, maximum: int | None = None) -> None:
        self._cond = threading.Condition()
        self._capacity = max(1, int(initial))
        self._minimum = max(1, int(minimum))
        self._maximum = max(self._capacity, int(maximum)) if maximum else max(self._capacity, 1)
        self._in_use = 0
        self._warning = False
        self.initial = self._capacity

    # ------------------------------------------------------------------ #
    @property
    def capacity(self) -> int:
        with self._cond:
            return self._capacity

    @property
    def in_use(self) -> int:
        with self._cond:
            return self._in_use

    @property
    def maximum(self) -> int:
        with self._cond:
            return self._maximum

    @property
    def minimum(self) -> int:
        with self._cond:
            return self._minimum

    @property
    def scaled_down(self) -> bool:
        with self._cond:
            return self._capacity < self.initial

    def set_capacity(self, value: int) -> int:
        with self._cond:
            new_value = int(value)
            if self._maximum:
                new_value = min(new_value, self._maximum)
            new_value = max(self._minimum, new_value)
            if new_value != self._capacity:
                self._capacity = new_value
                self._cond.notify_all()
            return self._capacity

    def acquire(self, cancel: "threading.Event | None" = None) -> bool:
        with self._cond:
            while self._in_use >= self._capacity:
                if cancel is not None and cancel.is_set():
                    return False
                self._cond.wait(timeout=0.5)
            self._in_use += 1
            return True

    def release(self) -> None:
        with self._cond:
            self._in_use = max(0, self._in_use - 1)
            self._cond.notify()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class AutoScaler(threading.Thread):
    """Background monitor that keeps CPU/RAM below the configured limits."""

    def __init__(
        self,
        capacity: DynamicCapacity,
        max_cpu: float = 85.0,
        max_mem: float = 85.0,
        interval: float = 1.0,
        log=None,
        verbose: bool = False,
    ) -> None:
        super().__init__(name="wp-checker-autoscaler", daemon=True)
        self.capacity = capacity
        self.max_cpu = float(max_cpu)
        self.max_mem = float(max_mem)
        self.interval = max(0.25, float(interval))
        self._stop = threading.Event()
        self._log = log
        self.verbose = verbose
        self.sampler = ResourceSampler()
        self.last = Sample(cpu=None, mem=None, ts=time.time())
        self.events = 0
        self._cooldown_until = 0.0
        self._down_events = 0
        self._floor_notice = False

    # ------------------------------------------------------------------ #
    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return not self._stop.is_set()

    def run(self) -> None:  # pragma: no cover - timing dependent
        if self.sampler.backend == "none":
            if self._log:
                self._log(c("auto-threads disabled: no resource backend available", "yellow"))
            return
        # sample right away so even very short scans get a reading
        self.step()
        while not self._stop.wait(self.interval):
            self.step()

    def step(self) -> None:
        """Single control iteration (exposed for tests)."""
        sample = self.sampler.sample()
        self.last = sample
        if not sample.usable:
            return

        cpu = sample.cpu if sample.cpu is not None else 0.0
        mem = sample.mem if sample.mem is not None else 0.0
        overloaded = cpu >= self.max_cpu or mem >= self.max_mem
        relaxed = cpu <= self.max_cpu * 0.65 and mem <= self.max_mem * 0.65

        current = self.capacity.capacity
        if overloaded:
            new_value = max(1, int(current * 0.7))
            if new_value < current:
                self._apply(new_value, f"cpu {cpu:.0f}% / mem {mem:.0f}% -> throttling")
            elif current <= self.capacity.minimum and self.verbose and self._log:
                # already as low as the operator allows - say it once, not every tick
                if not getattr(self, "_floor_notice", False):
                    self._floor_notice = True
                    self._log(
                        c(
                            f"[auto] holding at the minimum ({current} threads): "
                            f"cpu {cpu:.0f}% / mem {mem:.0f}%",
                            "yellow",
                        )
                    )
            self._cooldown_until = time.time() + max(2.0, self.interval * 3)
            return

        if relaxed and time.time() >= self._cooldown_until:
            step = max(1, int(current * 0.25))
            new_value = current + step
            if new_value > current:
                self._apply(new_value, f"cpu {cpu:.0f}% / mem {mem:.0f}% -> ramping up")

    def _apply(self, value: int, reason: str) -> None:
        before = self.capacity.capacity
        applied = self.capacity.set_capacity(value)
        if applied == before:
            return  # clamped by --min-threads/--max-threads: not a real change
        self.events += 1
        if applied < self.capacity.initial:
            self._down_events += 1
        if self._log and self.verbose:
            self._log(c(f"[auto] threads -> {applied} ({reason})", "yellow"))

    def summary(self) -> str:
        parts = []
        if self.last.cpu is not None:
            parts.append(f"cpu {self.last.cpu:.0f}%")
        if self.last.mem is not None:
            parts.append(f"mem {self.last.mem:.0f}%")
        return ", ".join(parts) if parts else "no samples"
