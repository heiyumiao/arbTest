from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile

import yaml

try:
    from .collector_common import normalize_share_symbol
except ImportError:
    from collector_common import normalize_share_symbol

BEGIN_MARKER = "# BEGIN LOFARB COLLECTOR"
END_MARKER = "# END LOFARB COLLECTOR"
PROJECT_DIR = "/home/ubuntu/LOFarb"
DATA_DIR = f"{PROJECT_DIR}/siphon_data"
PYTHON_BIN = f"{PROJECT_DIR}/.venv/bin/python"


def load_fund_symbols(config_path: Path) -> list[str]:
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    symbols: list[str] = []
    seen: set[str] = set()
    for fund in payload.get("funds", []):
        symbol = normalize_share_symbol(fund.get("code"))
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def build_cron_block(project_dir: str, data_dir: str, python_bin: str) -> str:
    log_dir = f"{project_dir}/logs"
    return "\n".join(
        [
            BEGIN_MARKER,
            f"0 6 * * 1-5 mkdir -p {log_dir} && cd {project_dir} && {python_bin} 041_jsl_cloud_shares.py --file {project_dir}/jsl_vps_symbols.txt --outdir {data_dir} >> {log_dir}/shares.log 2>&1",
            f"20 9 * * 1-5 mkdir -p {log_dir} && cd {project_dir} && {python_bin} cloud_siphon.py --kind fx --outdir {data_dir} >> {log_dir}/fx.log 2>&1",
            f"30 16 * * 1-5 mkdir -p {log_dir} && cd {project_dir} && {python_bin} cloud_siphon.py --kind futures --outdir {data_dir} >> {log_dir}/futures.log 2>&1",
            END_MARKER,
        ]
    )


def replace_managed_cron(existing: str, block: str) -> str:
    existing = existing.replace("\\r", "").replace("\r", "")
    lines = existing.splitlines()
    output: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == BEGIN_MARKER:
            skipping = True
            continue
        if line.strip() == END_MARKER:
            skipping = False
            continue
        if not skipping:
            output.append(line)
    while output and not output[-1].strip():
        output.pop()
    output.extend(["", block])
    return "\n".join(output).strip() + "\n"


def has_vps_auth(
    host: str | None,
    user: str | None,
    password: str | None = None,
    key_path: str | None = None,
    allow_agent: bool = True,
) -> bool:
    return bool(host and user and (password or key_path or allow_agent))


def run(command: list[str], input_text: str | None = None) -> str:
    input_bytes = input_text.encode("utf-8") if input_text is not None else None
    completed = subprocess.run(
        command,
        input=input_bytes,
        capture_output=True,
        check=True,
    )
    return completed.stdout.decode("utf-8", errors="replace")


def deploy(host: str, config_path: Path, dry_run: bool = False) -> None:
    symbols = load_fund_symbols(config_path)
    symbol_text = ",".join(symbols) + "\n"
    cron_block = build_cron_block(PROJECT_DIR, DATA_DIR, PYTHON_BIN)
    if dry_run:
        print(symbol_text)
        print(cron_block)
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        symbols_path = tmp_path / "jsl_vps_symbols.txt"
        symbols_path.write_text(symbol_text, encoding="utf-8")

        remote_setup = (
            f"mkdir -p {PROJECT_DIR} {DATA_DIR} {PROJECT_DIR}/logs "
            f"&& python3 -m venv {PROJECT_DIR}/.venv "
            f"&& {PYTHON_BIN} -m pip install --upgrade pip requests pyyaml"
        )
        run(["ssh", host, remote_setup])
        for local in [
            "deploy/collector_common.py",
            "deploy/041_jsl_cloud_shares.py",
            "deploy/cloud_siphon.py",
        ]:
            run(["scp", local, f"{host}:{PROJECT_DIR}/"])
        run(["scp", str(symbols_path), f"{host}:{PROJECT_DIR}/jsl_vps_symbols.txt"])

        existing = run(["ssh", host, "crontab -l 2>/dev/null || true"])
        updated = replace_managed_cron(existing, cron_block)
        run(["ssh", host, "crontab -"], input_text=updated)
        print(f"deployed {len(symbols)} symbols to {host}:{PROJECT_DIR}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy LOFArb lightweight collector to VPS")
    parser.add_argument("--config", default="arbcore/config/lof_config.yaml")
    parser.add_argument("--host", default="txy")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    deploy(args.host, Path(args.config), dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
