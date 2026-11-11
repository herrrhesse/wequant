import subprocess
import sys
from pathlib import Path

import wequant

CORE = """
import importlib, pkgutil, sys
import wequant

for info in pkgutil.iter_modules(wequant.__path__):
    if info.name in ("service", "cli", "api"):
        continue
    module = importlib.import_module(f"wequant.{info.name}")
    if info.ispkg:
        for sub in pkgutil.walk_packages(module.__path__, prefix=f"wequant.{info.name}."):
            importlib.import_module(sub.name)
loaded = sorted(name for name in sys.modules if name.startswith(tuple(sys.argv[1:])))
assert not loaded, loaded
print(len([name for name in sys.modules if name.startswith("wequant.")]))
"""
SERVICE = """
import sys
import wequant.service

loaded = sorted(name for name in sys.modules if name.startswith(tuple(sys.argv[1:])))
assert not loaded, loaded
"""


def run(script: str, forbidden: tuple[str, ...]) -> str:
    done = subprocess.run([sys.executable, "-c", script, *forbidden], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


INTERFACES = ("service", "cli", "api")
WEB = ("fastapi", "starlette", "uvicorn", "wequant.api")


def test_the_core_never_imports_the_service_the_cli_or_the_api():
    loaded = run(CORE, ("pydantic", "cyclopts", "wequant.service", "wequant.cli", *WEB))
    root = Path(wequant.__file__).parent
    files = [p.relative_to(root).parts for p in root.rglob("*.py")]
    assert int(loaded) == sum(parts[0] not in (*INTERFACES, "__init__.py") for parts in files)


def test_the_service_never_imports_the_cli_or_the_api():
    run(SERVICE, ("cyclopts", "wequant.cli", *WEB))
