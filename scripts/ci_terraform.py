"""Terraform CI setup/validation only; backend and provider locks stay protected."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.prepare_terraform_validation import prepare


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--profile", choices=("pr", "full"), required=True)
    parser.add_argument("--plugin-dir")
    args = parser.parse_args()
    report = ROOT / "test-results" / "ci" / "terraform"
    report.mkdir(parents=True, exist_ok=True)
    cli = report / "validation.tfrc"
    cli.write_text("# No user configuration, tokens or credentials.\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.update(TF_CLI_CONFIG_FILE=str(cli), CHECKPOINT_DISABLE="1", TF_IN_AUTOMATION="true",
                       AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull,
                       AWS_EC2_METADATA_DISABLED="true")
    for name in list(environment):
        if name.startswith("AWS_") and name not in {"AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_EC2_METADATA_DISABLED"}:
            environment.pop(name)
    if os.getenv("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write(f"TF_CLI_CONFIG_FILE={cli}\nCHECKPOINT_DISABLE=1\n")
    for directory in ("envs", "modules"):
        subprocess.run(["terraform", "fmt", "-recursive", "-check", "-no-color", directory], cwd=ROOT, env=environment, check=True)
    if args.profile == "pr":
        return
    staged = prepare()
    for name in ("dev", "dev-historical-data"):
        root = ROOT / "envs" / name
        lock = root / ".terraform.lock.hcl"
        before = hashlib.sha256(lock.read_bytes()).digest()
        command = ["terraform", f"-chdir={root}", "init", "-backend=false", "-input=false", "-lockfile=readonly", "-no-color"]
        if args.plugin_dir:
            command.append(f"-plugin-dir={Path(args.plugin_dir).resolve()}")
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
        if hashlib.sha256(lock.read_bytes()).digest() != before:
            raise RuntimeError(f"Provider lockfile changed: {lock}")
        staged_environment = environment | {"TF_DATA_DIR": str(root / ".terraform")}
        result = subprocess.run(["terraform", f"-chdir={staged / 'envs' / name}", "validate", "-json"],
                                cwd=ROOT, env=staged_environment, capture_output=True, text=True)
        data = json.loads(result.stdout)
        # Publish diagnostic summaries, not source snippets, state or configuration.
        safe = {key: data[key] for key in ("valid", "error_count", "warning_count")}
        safe["diagnostics"] = [{key: entry.get(key) for key in ("severity", "summary")} for entry in data.get("diagnostics", [])]
        (report / f"{name}.json").write_text(json.dumps(safe, indent=2), encoding="utf-8")
        print(name, json.dumps(safe))
        if result.returncode or not data["valid"]:
            raise RuntimeError(f"Terraform validation failed: {name}")


if __name__ == "__main__":
    main()
