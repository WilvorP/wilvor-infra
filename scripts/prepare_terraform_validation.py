"""Prepare an isolated Terraform validation tree without real Lambda builds.

Copies only Terraform source and provider locks. Synthetic ZIPs contain only
a warning file, no executable handler. Never writes to real packaging paths,
copies state/tfvars, downloads providers, or invokes Terraform deployment.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import uuid
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
ZIP_REFERENCE = re.compile(r'"(?:\$\{path\.root\}/)?\.\./\.\./(functions/[^"\n]+\.zip)"')
WARNING = b"VALIDATION ONLY: synthetic archive with no Lambda handler. NEVER DEPLOY.\n"


def prepare() -> Path:
    destination = REPO_ROOT / "test-results" / "terraform-validation" / uuid.uuid4().hex
    destination.mkdir(parents=True)
    archives = set()
    for directory in ("envs", "modules"):
        for source in sorted((REPO_ROOT / directory).rglob("*.tf")):
            if ".terraform" in source.parts:
                continue
            target = destination / source.relative_to(REPO_ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            archives.update(ZIP_REFERENCE.findall(source.read_text(encoding="utf-8")))
    for root in ("dev", "dev-historical-data"):
        relative = Path("envs") / root / ".terraform.lock.hcl"
        shutil.copyfile(REPO_ROOT / relative, destination / relative)
    for name in sorted(archives):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "functions":
            raise ValueError(f"Unsafe validation ZIP path: {name}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Stable bytes across runs; no local paths, timestamps or provider data.
        entry = zipfile.ZipInfo("VALIDATION_ONLY.txt", date_time=(1980, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_STORED
        entry.external_attr = 0o644 << 16
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr(entry, WARNING)
    (destination / "VALIDATION_ONLY.txt").write_bytes(WARNING)
    print(json.dumps({"workspace": str(destination), "validation_archives": len(archives)}))
    return destination


if __name__ == "__main__":
    prepare()
