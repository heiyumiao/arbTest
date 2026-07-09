# 轻量 VPS 数据采集器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `txy` VPS 上部署轻量日级数据采集器，并让本地 `daily_updater.py` 能通过 SSH key/agent 或密码从 VPS 拉取 JSON 数据。

**Architecture:** 新增 `deploy/` 目录，放本地部署脚本和 VPS 端采集脚本；VPS 只写 `/home/ubuntu/LOFarb/siphon_data/*.json`，本地继续负责 SFTP 同步、入库和估值。部署脚本生成基金代码列表、上传脚本、安装 cron，并保持幂等。

**Tech Stack:** Python 3.12 on VPS, Python on Windows, `requests`, `PyYAML`, `paramiko`, SSH host `txy`, cron, SQLite ingestion through existing `ArbDashboard/backend/scheduler/daily_updater.py`.

## Global Constraints

- 本地项目路径：`D:\something\arbTest`
- VPS Host：`txy`
- VPS 用户：`ubuntu`
- VPS 部署目录：`/home/ubuntu/LOFarb/`
- VPS 数据目录：`/home/ubuntu/LOFarb/siphon_data/`
- 不部署完整 ArbDashboard 到 VPS。
- 不在 VPS 上运行 QMT、IB、Futu OpenD 或券商交易逻辑。
- 不开放公网端口。
- 不把券商凭据、私钥或 git 内的明文 VPS 密码复制到 VPS。
- Woody 采集没有 VPS 本地 token 时保持禁用。
- 只修改本次任务需要的文件；保留工作树中已有的其他用户改动。

---

## File Structure

- Create `deploy/collector_common.py`: 共享日期、原子 JSON 写入、日志、基金代码格式化、cron 片段生成。
- Create `deploy/041_jsl_cloud_shares.py`: VPS 端场内份额采集脚本，输入 symbols 文件，输出 `shares_YYYY-MM-DD.json`。
- Create `deploy/cloud_siphon.py`: VPS 端公开汇率/期货采集脚本，输出 `fx_YYYY-MM-DD.json` 或 `futures_YYYY-MM-DD.json`；Woody token 未配置时跳过。
- Create `deploy/deploy_vps.py`: 本地部署脚本，生成 symbols、上传文件、创建 venv、安装依赖、安装 cron。
- Modify `ArbDashboard/backend/scheduler/daily_updater.py`: 允许无 `VPS_PASSWORD` 时使用 key/agent 认证同步。
- Create `tests/test_vps_deploy.py`: 测试 symbols 生成、cron 幂等块生成、SSH 认证配置判定。

---

### Task 1: 本地部署工具的纯函数和测试

**Files:**
- Create: `deploy/collector_common.py`
- Create: `deploy/deploy_vps.py`
- Create: `tests/test_vps_deploy.py`

**Interfaces:**
- Produces: `load_fund_symbols(config_path: Path) -> list[str]`
- Produces: `build_cron_block(project_dir: str, data_dir: str, python_bin: str) -> str`
- Produces: `replace_managed_cron(existing: str, block: str) -> str`
- Produces: `has_vps_auth(host, user, password=None, key_path=None, allow_agent=True) -> bool`

- [ ] **Step 1: Write failing tests for symbol extraction and cron idempotence**

Create `tests/test_vps_deploy.py`:

```python
from pathlib import Path

from deploy.deploy_vps import build_cron_block, load_fund_symbols, replace_managed_cron


def test_load_fund_symbols_formats_shanghai_and_shenzhen(tmp_path):
    config = tmp_path / "lof_config.yaml"
    config.write_text(
        """
funds:
  - code: "162411"
  - code: 501018
  - code: "159518"
  - code: ""
  - name: missing code
""",
        encoding="utf-8",
    )

    assert load_fund_symbols(config) == ["162411", "sh501018", "159518"]


def test_replace_managed_cron_is_idempotent():
    block = build_cron_block("/home/ubuntu/LOFarb", "/home/ubuntu/LOFarb/siphon_data", "/home/ubuntu/LOFarb/.venv/bin/python")
    existing = "SHELL=/bin/bash\n" + block + "\n"

    once = replace_managed_cron(existing, block)
    twice = replace_managed_cron(once, block)

    assert once == twice
    assert once.count("# BEGIN LOFARB COLLECTOR") == 1
    assert "041_jsl_cloud_shares.py" in once
    assert "cloud_siphon.py --kind fx" in once
    assert "cloud_siphon.py --kind futures" in once
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_vps_deploy.py -q
```

Expected: FAIL because `deploy.deploy_vps` does not exist.

- [ ] **Step 3: Implement pure functions**

Create `deploy/collector_common.py`:

```python
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any


def today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def write_json_atomic(path: Path, payload: Any, force: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to overwrite")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)


def normalize_share_symbol(code: object) -> str | None:
    text = str(code or "").strip().lower()
    if not text:
        return None
    text = text.replace("sz", "").replace("sh", "")
    if not text.isdigit():
        return None
    text = text.zfill(6)
    if text.startswith("5"):
        return f"sh{text}"
    return text
```

Create the pure parts of `deploy/deploy_vps.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from collector_common import normalize_share_symbol

BEGIN_MARKER = "# BEGIN LOFARB COLLECTOR"
END_MARKER = "# END LOFARB COLLECTOR"


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


def has_vps_auth(host: str | None, user: str | None, password: str | None = None, key_path: str | None = None, allow_agent: bool = True) -> bool:
    return bool(host and user and (password or key_path or allow_agent))


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy LOFArb lightweight collector to VPS")
    parser.add_argument("--config", default="arbcore/config/lof_config.yaml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    symbols = load_fund_symbols(Path(args.config))
    print(f"loaded {len(symbols)} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_vps_deploy.py -q
```

Expected: PASS.

---

### Task 2: VPS 端采集脚本

**Files:**
- Modify: `deploy/041_jsl_cloud_shares.py`
- Modify: `deploy/cloud_siphon.py`
- Test: `tests/test_vps_deploy.py`

**Interfaces:**
- Consumes: `write_json_atomic(path: Path, payload: Any, force: bool = False) -> None`
- Produces: CLI `python 041_jsl_cloud_shares.py --file SYMBOLS --outdir OUTDIR [--date YYYY-MM-DD] [--force]`
- Produces: CLI `python cloud_siphon.py --kind fx|futures --outdir OUTDIR [--date YYYY-MM-DD] [--force]`

- [ ] **Step 1: Add parser tests for symbol file loading**

Append to `tests/test_vps_deploy.py`:

```python
from deploy.collector_common import normalize_share_symbol


def test_normalize_share_symbol():
    assert normalize_share_symbol("162411") == "162411"
    assert normalize_share_symbol("501018") == "sh501018"
    assert normalize_share_symbol("sh501018") == "sh501018"
    assert normalize_share_symbol("") is None
```

- [ ] **Step 2: Run targeted test**

Run:

```powershell
python -m pytest tests/test_vps_deploy.py::test_normalize_share_symbol -q
```

Expected: PASS after Task 1.

- [ ] **Step 3: Implement share collector**

Create `deploy/041_jsl_cloud_shares.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path
import random
import time

import requests

from collector_common import normalize_share_symbol, today_text, write_json_atomic

SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"
SSE_URL = "https://query.sse.com.cn/commonQuery.do"


def read_symbols(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    symbols: list[str] = []
    seen: set[str] = set()
    for raw in text.replace("\n", ",").split(","):
        symbol = normalize_share_symbol(raw)
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def fetch_szse_share(session: requests.Session, code: str) -> float | None:
    params = {
        "SHOWTYPE": "JSON",
        "CATALOGID": "1945",
        "TABKEY": "tab1",
        "txtDMorJC": code,
    }
    response = session.get(
        SZSE_URL,
        params=params,
        headers={
            "User-Agent": "Mozilla/5.0 LOFArb collector",
            "Referer": "https://www.szse.cn/disclosure/fund/currency/index.html",
            "Host": "www.szse.cn",
        },
        timeout=20,
        verify=False,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload[0].get("data", []) if isinstance(payload, list) and payload else []
    for row in rows:
        if str(row.get("zqdm") or row.get("dm") or "").zfill(6) == code:
            value = row.get("dqgm") or row.get("gm") or row.get("jjgm")
            return float(str(value).replace(",", ""))
    return None


def fetch_sse_shares(session: requests.Session) -> dict[str, float]:
    params = {
        "isPagination": "false",
        "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_L_NEW",
    }
    response = session.get(
        SSE_URL,
        params=params,
        headers={
            "User-Agent": "Mozilla/5.0 LOFArb collector",
            "Referer": "https://www.sse.com.cn/",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("result") or []
    result: dict[str, float] = {}
    for row in rows:
        code = str(row.get("FUND_ID") or row.get("SEC_CODE") or row.get("productid") or "").zfill(6)
        if not code or code == "000000":
            continue
        raw_value = row.get("INTERNAL_VOL") or row.get("TOTAL_SHARES") or row.get("VOL")
        if raw_value is None:
            continue
        result[f"sh{code}"] = float(str(raw_value).replace(",", ""))
    return result


def collect(symbols: list[str]) -> dict[str, float | None]:
    session = requests.Session()
    result: dict[str, float | None] = {}
    sh_symbols = [s for s in symbols if s.startswith("sh")]
    sz_symbols = [s for s in symbols if not s.startswith("sh")]
    if sh_symbols:
        try:
            sse_values = fetch_sse_shares(session)
            for symbol in sh_symbols:
                result[symbol] = sse_values.get(symbol)
        except Exception as exc:
            print(f"[WARN] SSE batch failed: {exc}")
            for symbol in sh_symbols:
                result[symbol] = None
    for symbol in sz_symbols:
        try:
            result[symbol] = fetch_szse_share(session, symbol)
        except Exception as exc:
            print(f"[WARN] SZSE {symbol} failed: {exc}")
            result[symbol] = None
        time.sleep(random.uniform(2.0, 4.0))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect LOF share data")
    parser.add_argument("--file", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--date", default=today_text())
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    symbols = read_symbols(Path(args.file))
    payload = collect(symbols)
    ok_count = sum(1 for value in payload.values() if value is not None)
    if ok_count < min(20, max(1, len(symbols) // 3)):
        print(f"[WARN] unusually few share values: {ok_count}/{len(symbols)}")
    out_path = Path(args.outdir) / f"shares_{args.date}.json"
    write_json_atomic(out_path, payload, force=args.force)
    print(f"wrote {out_path} with {ok_count}/{len(symbols)} values")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Implement public siphon**

Create `deploy/cloud_siphon.py`:

```python
from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests

from collector_common import today_text, write_json_atomic


def collect_fx(target_date: str) -> dict:
    url = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/ccpr.json"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 LOFArb collector"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    records = payload.get("records") or []
    data = payload.get("data") or {}
    result = {"date": target_date, "source": "chinamoney_ccpr", "rates": {}}
    for record in records:
        name = record.get("vrtEName") or record.get("vrtName")
        price = record.get("price")
        if name in ("USD/CNY", "HKD/CNY") and price is not None:
            result["rates"][name] = float(price)
    if not result["rates"]:
        raise RuntimeError("no FX rates parsed")
    result["source_date"] = str(data.get("lastDate") or "")[:10]
    return result


def collect_futures(target_date: str) -> dict:
    symbols = ["nf_GC", "nf_CL", "nf_SI"]
    url = "https://hq.sinajs.cn/list=" + ",".join(symbols)
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 LOFArb collector", "Referer": "https://finance.sina.com.cn"}, timeout=20)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    data = []
    for chunk in response.text.splitlines():
        if "=\"" not in chunk:
            continue
        name = chunk.split("var hq_str_", 1)[1].split("=", 1)[0]
        parts = chunk.split("\"", 2)[1].split(",")
        if len(parts) > 8:
            data.append({"symbol": name.replace("nf_", ""), "close": _float(parts[3]), "settle": _float(parts[8]), "volume": _float(parts[14]) if len(parts) > 14 else None})
    return {"date": target_date, "source": "sina_futures", "data": data}


def collect_woody(target_date: str) -> dict:
    token = os.environ.get("WOODY_BOT_TOKEN") or os.environ.get("WOODY_API_TOKEN")
    if not token:
        return {"date": target_date, "source": "woody", "enabled": False, "message": "WOODY token not configured on VPS"}
    raise RuntimeError("Woody VPS collection requires endpoint confirmation before enabling")


def _float(value: str):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect public daily data")
    parser.add_argument("--kind", choices=["fx", "futures", "woody"], required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--date", default=today_text())
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    collectors = {"fx": collect_fx, "futures": collect_futures, "woody": collect_woody}
    payload = collectors[args.kind](args.date)
    out_path = Path(args.outdir) / f"{args.kind}_{args.date}.json"
    write_json_atomic(out_path, payload, force=args.force)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Syntax check**

Run:

```powershell
python -m py_compile deploy/041_jsl_cloud_shares.py deploy/cloud_siphon.py deploy/collector_common.py deploy/deploy_vps.py
```

Expected: no output and exit 0.

---

### Task 3: 本地 SFTP 认证兼容

**Files:**
- Modify: `ArbDashboard/backend/scheduler/daily_updater.py`
- Test: `tests/test_vps_deploy.py`

**Interfaces:**
- Produces: `has_vps_auth(...)` in `deploy/deploy_vps.py` for tests and mirrored logic in `daily_updater.py`.

- [ ] **Step 1: Add auth predicate tests**

Append to `tests/test_vps_deploy.py`:

```python
from deploy.deploy_vps import has_vps_auth


def test_has_vps_auth_allows_agent_without_password():
    assert has_vps_auth("101.34.73.38", "ubuntu", password=None, key_path=None, allow_agent=True)


def test_has_vps_auth_rejects_missing_host_or_user():
    assert not has_vps_auth(None, "ubuntu", allow_agent=True)
    assert not has_vps_auth("101.34.73.38", None, allow_agent=True)
```

- [ ] **Step 2: Run tests**

Run:

```powershell
python -m pytest tests/test_vps_deploy.py -q
```

Expected: PASS after Task 1.

- [ ] **Step 3: Patch `daily_updater.py` auth guard and connection flow**

Modify `_try_sync_all_from_vps()`:

```python
        if not all([VPS_HOST, VPS_USER]) or not (VPS_PASSWORD or VPS_KEY_PATH or True):
            return []
```

Replace with clearer helper inside the method:

```python
        if not (VPS_HOST and VPS_USER):
            return []
```

Then update connection order:

```python
            connected = False
            if VPS_KEY_PATH and os.path.exists(VPS_KEY_PATH):
                try:
                    pkey = paramiko.Ed25519Key.from_private_key_file(VPS_KEY_PATH, password=VPS_KEY_PASSWORD)
                    ssh.connect(VPS_HOST, port=VPS_PORT, username=VPS_USER, pkey=pkey, timeout=10)
                    connected = True
                    self.logger.info("[VPS] SSH 私钥认证成功")
                except Exception as key_err:
                    self.logger.info(f"[VPS] 私钥认证失败 ({key_err})，继续尝试其他认证方式")
            if not connected:
                try:
                    ssh.connect(VPS_HOST, port=VPS_PORT, username=VPS_USER, timeout=10)
                    connected = True
                    self.logger.info("[VPS] SSH agent/key 认证成功")
                except Exception as agent_err:
                    self.logger.info(f"[VPS] SSH agent/key 认证失败 ({agent_err})")
            if not connected and VPS_PASSWORD:
                ssh.connect(
                    VPS_HOST,
                    port=VPS_PORT,
                    username=VPS_USER,
                    password=VPS_PASSWORD,
                    timeout=10,
                    look_for_keys=False,
                    allow_agent=False,
                )
                connected = True
                self.logger.info("[VPS] SSH 密码认证成功")
            if not connected:
                self.logger.warning("[VPS] 没有可用 SSH 认证方式")
                return []
```

- [ ] **Step 4: Run focused tests and syntax check**

Run:

```powershell
python -m pytest tests/test_vps_deploy.py -q
python -m py_compile ArbDashboard/backend/scheduler/daily_updater.py
```

Expected: tests PASS; compile exits 0.

---

### Task 4: 完成本地部署脚本并部署到 `txy`

**Files:**
- Modify: `deploy/deploy_vps.py`
- Runtime target: `txy:/home/ubuntu/LOFarb`

**Interfaces:**
- Consumes: Task 1 pure functions.
- Produces: CLI `python deploy/deploy_vps.py --host txy`.

- [ ] **Step 1: Implement SSH/SCP deployment commands**

Extend `deploy/deploy_vps.py`:

```python
import subprocess
import tempfile

PROJECT_DIR = "/home/ubuntu/LOFarb"
DATA_DIR = f"{PROJECT_DIR}/siphon_data"
PYTHON_BIN = f"{PROJECT_DIR}/.venv/bin/python"


def run(command: list[str], input_text: str | None = None) -> str:
    completed = subprocess.run(command, input=input_text, text=True, capture_output=True, check=True)
    return completed.stdout


def deploy(host: str, config_path: Path, dry_run: bool = False) -> None:
    symbols = load_fund_symbols(config_path)
    symbol_text = ",".join(symbols) + "\n"
    if dry_run:
        print(symbol_text)
        print(build_cron_block(PROJECT_DIR, DATA_DIR, PYTHON_BIN))
        return
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        symbols_path = tmp_path / "jsl_vps_symbols.txt"
        symbols_path.write_text(symbol_text, encoding="utf-8")
        run(["ssh", host, f"mkdir -p {PROJECT_DIR} {DATA_DIR} {PROJECT_DIR}/logs && python3 -m venv {PROJECT_DIR}/.venv && {PYTHON_BIN} -m pip install --upgrade pip requests pyyaml"])
        for local in ["deploy/collector_common.py", "deploy/041_jsl_cloud_shares.py", "deploy/cloud_siphon.py"]:
            run(["scp", local, f"{host}:{PROJECT_DIR}/"])
        run(["scp", str(symbols_path), f"{host}:{PROJECT_DIR}/jsl_vps_symbols.txt"])
        existing = run(["ssh", host, "crontab -l 2>/dev/null || true"])
        block = build_cron_block(PROJECT_DIR, DATA_DIR, PYTHON_BIN)
        updated = replace_managed_cron(existing, block)
        run(["ssh", host, "crontab -"], input_text=updated)
        print(f"deployed {len(symbols)} symbols to {host}:{PROJECT_DIR}")
```

Update `main()` arguments:

```python
    parser.add_argument("--host", default="txy")
    ...
    deploy(args.host, Path(args.config), dry_run=args.dry_run)
```

- [ ] **Step 2: Dry run**

Run:

```powershell
python deploy/deploy_vps.py --host txy --dry-run
```

Expected: prints generated symbols and one managed cron block.

- [ ] **Step 3: Deploy**

Run:

```powershell
python deploy/deploy_vps.py --host txy
```

Expected: prints `deployed N symbols to txy:/home/ubuntu/LOFarb`.

- [ ] **Step 4: Verify remote files and cron**

Run:

```powershell
ssh txy "ls -la /home/ubuntu/LOFarb; ls -la /home/ubuntu/LOFarb/siphon_data; crontab -l"
```

Expected:

- `041_jsl_cloud_shares.py`, `cloud_siphon.py`, `collector_common.py`, `jsl_vps_symbols.txt` exist.
- cron contains one `# BEGIN LOFARB COLLECTOR` block.

---

### Task 5: 手动采集和本地同步验证

**Files:**
- Modify private config only if needed: `arbcore/config/account_private.py`
- Runtime target: `txy:/home/ubuntu/LOFarb/siphon_data`

**Interfaces:**
- Consumes: collector scripts deployed by Task 4.
- Consumes: `daily_updater.py` VPS sync.

- [ ] **Step 1: Run remote collectors manually**

Run:

```powershell
ssh txy "cd /home/ubuntu/LOFarb && .venv/bin/python cloud_siphon.py --kind fx --outdir /home/ubuntu/LOFarb/siphon_data --force && .venv/bin/python cloud_siphon.py --kind futures --outdir /home/ubuntu/LOFarb/siphon_data --force"
```

Expected: writes `fx_YYYY-MM-DD.json` and `futures_YYYY-MM-DD.json`.

Then run shares:

```powershell
ssh txy "cd /home/ubuntu/LOFarb && .venv/bin/python 041_jsl_cloud_shares.py --file /home/ubuntu/LOFarb/jsl_vps_symbols.txt --outdir /home/ubuntu/LOFarb/siphon_data --force"
```

Expected: writes `shares_YYYY-MM-DD.json`. This may take several minutes because SZSE requests are paced.

- [ ] **Step 2: Inspect remote JSON counts**

Run:

```powershell
ssh txy "python3 - <<'PY'
import json, glob
for path in sorted(glob.glob('/home/ubuntu/LOFarb/siphon_data/*.json')):
    data=json.load(open(path, encoding='utf-8'))
    print(path, len(data) if isinstance(data, dict) else type(data).__name__)
PY"
```

Expected: `shares_*.json` reports a meaningful dict size; FX and futures files exist.

- [ ] **Step 3: Update local private VPS config**

Edit `arbcore/config/account_private.py` to include:

```python
VPS_HOST = "101.34.73.38"
VPS_PORT = 22
VPS_USER = "ubuntu"
VPS_PASSWORD = None
VPS_DATA_DIR = "/home/ubuntu/LOFarb/siphon_data"
VPS_KEY_PATH = None
VPS_KEY_PASSWORD = None
```

Do not commit this private file.

- [ ] **Step 4: Run local sync**

Run:

```powershell
python ArbDashboard/backend/scheduler/daily_updater.py --refresh-morning
```

Expected: log contains VPS sync lines and files appear under `ArbDashboard/data/vps_sync/`.

- [ ] **Step 5: Final verification**

Run:

```powershell
Get-ChildItem -LiteralPath "ArbDashboard\data\vps_sync" -Filter "*.json" | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name,Length,LastWriteTime
python -m pytest tests/test_vps_deploy.py -q
```

Expected: recent `shares_`, `fx_`, or `futures_` files are present; tests pass.

---

## Self-Review

- Spec coverage: Tasks cover deploy scripts, VPS paths, cron, local sync auth, manual collector run, local SFTP ingestion, and rollback-safe behavior.
- Placeholder scan: no unfinished placeholder wording or unspecified implementation steps are intentionally left.
- Type consistency: plan uses `Path`, `list[str]`, `str`, and CLI interfaces consistently across tasks.
