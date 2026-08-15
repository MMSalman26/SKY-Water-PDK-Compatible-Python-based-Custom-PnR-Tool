"""Best-effort process RSS helpers. Never raises into the pipeline."""

from __future__ import annotations

from typing import Optional


def current_rss_mb() -> Optional[float]:
    """Return current process RSS in megabytes, or ``None`` if unavailable."""
    try:
        import psutil  # type: ignore

        return float(psutil.Process().memory_info().rss) / (1024.0 * 1024.0)
    except Exception:
        pass

    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux: KB; macOS: bytes
        import sys

        if sys.platform == "darwin":
            return float(usage) / (1024.0 * 1024.0)
        return float(usage) / 1024.0
    except Exception:
        pass

    # Windows fallback via GetProcessMemoryInfo
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        )
        if ok:
            return float(counters.WorkingSetSize) / (1024.0 * 1024.0)
    except Exception:
        pass
    return None


class MemoryTracker:
    """Track peak RSS delta over a run; safe when RSS cannot be read."""

    def __init__(self) -> None:
        self._start = current_rss_mb()
        self._peak = self._start

    def sample(self) -> None:
        rss = current_rss_mb()
        if rss is None or self._peak is None:
            return
        if rss > self._peak:
            self._peak = rss

    def peak_delta_mb(self) -> Optional[float]:
        self.sample()
        if self._start is None or self._peak is None:
            return None
        return max(0.0, float(self._peak) - float(self._start))
