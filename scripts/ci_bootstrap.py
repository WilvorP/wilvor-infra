"""Setup-only download of the pinned, checksum-verified actionlint binary."""

import hashlib
from pathlib import Path
import urllib.request
import zipfile

VERSION = "1.7.7"
SHA256 = "7f12f1801bca3d480d67aaf7774f4c2a6359a3ca8eebe382c95c10c9704aa731"


def main():
    directory = Path(__file__).resolve().parents[1] / "test-results" / "ci-tools" / "actionlint"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "actionlint.zip"
    urllib.request.urlretrieve(
        f"https://github.com/rhysd/actionlint/releases/download/v{VERSION}/actionlint_{VERSION}_windows_amd64.zip",
        archive,
    )
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("actionlint archive checksum mismatch")
    with zipfile.ZipFile(archive) as zipped:
        (directory / "actionlint.exe").write_bytes(zipped.read("actionlint.exe"))
    print(directory / "actionlint.exe")


if __name__ == "__main__":
    main()
