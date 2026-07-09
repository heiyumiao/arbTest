import os
import shutil
import sys
from typing import Optional


def resolve_python_executable(backend_dir: str) -> Optional[str]:
    """Resolve the Python used for background backend scripts."""
    candidates = [
        sys.executable,
        os.path.normpath(os.path.join(backend_dir, "..", "..", ".venv", "Scripts", "python.exe")),
        os.path.normpath(os.path.join(backend_dir, "..", "..", "..", ".venv", "Scripts", "python.exe")),
        os.path.normpath(os.path.join(backend_dir, "..", "..", "..", "Python311", "python.exe")),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return shutil.which("python")
