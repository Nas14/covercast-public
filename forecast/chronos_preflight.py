"""F3 preflight: is this machine ready to run Chronos-2? Run it before run_chronos_local.py.

    python forecast/chronos_preflight.py            all checks, including reaching Hugging Face
    python forecast/chronos_preflight.py --offline  skip the network check (manual download route)

It downloads nothing. Every check prints OK, WARN or FAIL with what to do next
(section numbers refer to forecast/CHRONOS2_WINDOWS.md). Exit code 0 = ready.
"""
from __future__ import annotations

import argparse
import importlib
import os
import platform
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NEED_DISK_GB = 2.0      # 0.48 GB weights + headroom for the Hugging Face cache
RECOMMENDED_RAM_GB = 8.0
PINNED = {"chronos": "2.3.2"}
results: list[tuple[str, str, str]] = []


def report(status: str, what: str, hint: str = "") -> None:
    results.append((status, what, hint))
    print(f"[{status:4}] {what}" + (f"\n        -> {hint}" if hint else ""), flush=True)


def total_ram_gb() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return st.ullTotalPhys / 1024 ** 3
        with open("/proc/meminfo") as f:
            return int(f.readline().split()[1]) / 1024 ** 2
    except Exception:  # noqa: BLE001
        return None


def hf_cache_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def check_python() -> None:
    v = sys.version_info
    if v >= (3, 10):
        report("OK", f"Python {platform.python_version()} ({platform.machine()})")
    else:
        report("FAIL", f"Python {platform.python_version()}", "chronos-forecasting needs Python 3.10+ (section 1)")
    if sys.maxsize <= 2 ** 32:
        report("FAIL", "32-bit Python", "install 64-bit Python; torch has no 32-bit wheels (section 1)")


def check_packages() -> bool:
    ok = True
    for mod in ("numpy", "sklearn", "torch", "transformers", "huggingface_hub", "chronos"):
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "?")
            pinned = PINNED.get(mod)
            if pinned and ver != pinned:
                report("WARN", f"{mod} {ver}", f"the spec pins {pinned}; run: pip install -r requirements-chronos.txt")
            else:
                report("OK", f"{mod} {ver}")
        except Exception as e:  # noqa: BLE001
            ok = False
            report("FAIL", f"{mod} not importable ({e.__class__.__name__}: {e})",
                   "activate .venv-chronos, then: pip install -r requirements-chronos.txt (section 2)")
    if ok:
        import torch
        if torch.cuda.is_available():
            report("OK", "torch sees a GPU; the run still uses CPU as the spec says")
        else:
            report("OK", "torch will run on CPU (no GPU needed)")
    return ok


def check_resources() -> None:
    cache = hf_cache_dir()
    probe = cache if cache.exists() else cache.anchor or Path.home()
    free = shutil.disk_usage(probe).free / 1024 ** 3
    if free >= NEED_DISK_GB:
        report("OK", f"{free:.1f} GB free for the model cache ({cache})")
    else:
        report("FAIL", f"only {free:.1f} GB free at {cache}",
               f"free {NEED_DISK_GB} GB or set HF_HOME to another drive (section 4)")
    ram = total_ram_gb()
    if ram is None:
        report("WARN", "could not read total RAM", "8 GB or more is comfortable (section 3)")
    elif ram >= RECOMMENDED_RAM_GB:
        report("OK", f"{ram:.1f} GB RAM")
    else:
        report("WARN", f"{ram:.1f} GB RAM", "close other programs before running (section 3)")


def check_api() -> None:
    """Build a tiny random Chronos-2 in memory and call the exact API the run uses. No download."""
    try:
        import numpy as np
        import torch
        from chronos import Chronos2Pipeline
        from chronos.chronos2.config import Chronos2CoreConfig
        from chronos.chronos2.model import Chronos2Model

        from models.chronos2 import Chronos2Forecaster

        torch.manual_seed(0)
        q = [0.01, 0.05] + [round(0.1 * i, 1) for i in range(1, 10)] + [0.95, 0.99]
        cfg = Chronos2CoreConfig(
            d_model=32, d_kv=8, d_ff=64, num_layers=1, num_heads=4, architectures=["Chronos2Model"],
            chronos_config={"context_length": 256, "output_patch_size": 16, "input_patch_size": 16,
                            "input_patch_stride": 16, "quantiles": q, "use_reg_token": True,
                            "use_arcsinh": True, "max_output_patches": 4})
        fc = Chronos2Forecaster(Chronos2Pipeline(model=Chronos2Model(cfg).eval())).forecast(
            np.linspace(90_000, 150_000, 120), 119, 7)
        assert fc.p50.shape == (7,)
        report("OK", "Chronos-2 API works on this machine (tiny random model, not the real one)")
    except Exception as e:  # noqa: BLE001
        report("FAIL", f"Chronos-2 API check failed ({e.__class__.__name__}: {e})",
               "send this whole output back; it is a version problem, not a download problem (section 2)")


def check_network() -> None:
    import urllib.request
    url = "https://huggingface.co/amazon/chronos-2/resolve/main/config.json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=15) as r:
            report("OK", f"Hugging Face reachable (HTTP {r.status})")
    except Exception as e:  # noqa: BLE001
        report("FAIL", f"cannot reach Hugging Face ({e.__class__.__name__}: {e})",
               "check proxy/firewall, or use the manual download route (section 5)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="skip the Hugging Face network check")
    args = ap.parse_args(argv)
    print("CoverCast F3 preflight for Chronos-2\n")
    check_python()
    if check_packages():
        check_api()
    check_resources()
    if not args.offline:
        check_network()
    fails = [r for r in results if r[0] == "FAIL"]
    print(f"\n{'READY' if not fails else 'NOT READY'}: {len(fails)} fail, "
          f"{sum(r[0] == 'WARN' for r in results)} warn, {sum(r[0] == 'OK' for r in results)} ok")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
