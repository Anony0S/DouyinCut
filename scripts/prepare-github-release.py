"""Create the checksum sidecar required by the in-app GitHub release updater."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re


def prepare(installer: Path, spec: Path) -> Path:
    match = re.search(r"^- 版本：(\d+\.\d+\.\d+)\s*$", spec.read_text(encoding="utf-8"), re.M)
    if not match:
        raise ValueError("SPEC.md does not contain a valid application version")
    filename = f"DouyinCut-{match[1]}-setup.exe"
    if installer.name != filename:
        raise ValueError(f"Installer filename must match SPEC.md: {filename}")
    size = installer.stat().st_size
    if not 0 < size <= 2 * 1024**3:
        raise ValueError("Installer must be nonempty and no larger than 2 GiB")
    with installer.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    sidecar = installer.with_name(f"{filename}.sha256")
    sidecar.write_text(f"{digest}  {filename}\n", encoding="utf-8")
    return sidecar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    sidecar = prepare(args.installer, Path(__file__).resolve().parents[1] / "SPEC.md")
    print(f"Release checksum: {sidecar}")


if __name__ == "__main__":
    main()
