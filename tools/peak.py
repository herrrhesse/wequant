"""Peak resident memory and load average for the reports: Linux's getrusage and getloadavg, Windows' peak working
set; NaN for what Windows does not keep."""

from __future__ import annotations

import math
import os

try:
    import resource
except ImportError:  # Windows
    resource = None


def peak_mib(children: bool = False) -> float:
    """Peak RSS of this process (or of its finished children) in MiB; NaN where it cannot be read."""
    if resource is None:
        return math.nan if children else _windows_peak()
    who = resource.RUSAGE_CHILDREN if children else resource.RUSAGE_SELF
    return resource.getrusage(who).ru_maxrss / 1024  # Linux reports KiB


def _windows_peak() -> float:
    """PeakWorkingSetSize of this process, through psapi's GetProcessMemoryInfo."""
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    current, info = ctypes.windll.kernel32.GetCurrentProcess, ctypes.windll.psapi.GetProcessMemoryInfo
    current.restype = wintypes.HANDLE  # a pointer-sized pseudo handle: the default int would cut it on 64 bits
    info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not info(current(), ctypes.byref(counters), counters.cb):
        return math.nan
    return counters.PeakWorkingSetSize / 2**20


def load_average() -> tuple[float, float, float]:
    """The system's 1, 5 and 15 minute load averages; NaN on Windows, which keeps none."""
    return os.getloadavg() if hasattr(os, "getloadavg") else (math.nan,) * 3
