from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


INCLUDE_FILES = [
    "Dockerfile",
    ".dockerignore",
    "index.html",
    "scripts/merge_benchmark_rows.py",
    "scripts/generate_structure_benchmark_template.py",
    "scripts/collect_lifull_structure_benchmarks.py",
]

INCLUDE_DIRS = [
    "backend",
    "frontend",
    "spec_bundle_v0.1.2",
    "spec_bundle_v0.1.1",
    "agents/agent_D_benchmark_data/out",
    "benchmark_collection",
]

EXCLUDE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}

EXCLUDE_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
}


def _should_exclude(path: Path) -> bool:
    if path.name in EXCLUDE_DIR_NAMES:
        return True
    if path.suffix.lower() in EXCLUDE_FILE_SUFFIXES:
        return True
    return False


def _iter_files(rel_dir: Path) -> list[Path]:
    base = ROOT / rel_dir
    out: list[Path] = []
    for p in base.rglob("*"):
        if _should_exclude(p):
            continue
        if p.is_file():
            out.append(p)
    return out


def main(argv: list[str]) -> int:
    out_name = argv[1] if len(argv) >= 2 else "wh-cloudbuild.zip"
    out_path = (ROOT / out_name).resolve()

    missing: list[str] = []
    for f in INCLUDE_FILES:
        if not (ROOT / f).exists():
            missing.append(f)
    for d in INCLUDE_DIRS:
        if not (ROOT / d).exists():
            missing.append(d)
    if missing:
        raise SystemExit(f"Missing required paths: {', '.join(missing)}")

    files: list[Path] = []
    for f in INCLUDE_FILES:
        files.append((ROOT / f).resolve())
    for d in INCLUDE_DIRS:
        files.extend(_iter_files(Path(d)))

    # Stable ordering for reproducible zips.
    files = sorted(set(files), key=lambda p: str(p).replace(os.sep, "/"))

    if out_path.exists():
        out_path.unlink()

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            rel = p.relative_to(ROOT)
            # Ensure POSIX separators inside zip (Linux unzip/Docker build friendliness).
            arcname = str(rel).replace(os.sep, "/")
            z.write(p, arcname)

    size = out_path.stat().st_size
    print(f"Wrote {out_path} ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
