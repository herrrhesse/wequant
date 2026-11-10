import subprocess
import sys
from pathlib import Path

import wequant

CORE = """
import importlib, pkgutil, sys
import wequant

for info in pkgutil.iter_modules(wequant.__path__):
    if info.name in ("service", "cli"):
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


def test_the_core_never_imports_the_service_or_the_cli():
    loaded = run(CORE, ("pydantic", "cyclopts", "wequant.service", "wequant.cli"))
    root = Path(wequant.__file__).parent
    files = [p.relative_to(root).parts for p in root.rglob("*.py")]
    assert int(loaded) == sum(parts[0] not in ("service", "cli", "__init__.py") for parts in files)


def test_the_service_never_imports_the_cli():
    run(SERVICE, ("cyclopts", "wequant.cli"))
