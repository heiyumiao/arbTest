from __future__ import annotations

import argparse
from pathlib import Path
import random
import time

import requests

try:
    from .collector_common import normalize_share_symbol, today_text, write_json_atomic
except ImportError:
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
        "CATALOGID": "1945_LOF",
        "txtQueryKeyAndJC": code,
    }
    response = session.get(
        SZSE_URL,
        params=params,
        headers={
            "User-Agent": "Mozilla/5.0 LOFArb collector",
            "Referer": "https://www.szse.cn/market/product/fund/lof/index.html",
            "Host": "www.szse.cn",
        },
        timeout=20,
        verify=False,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload[0].get("data", []) if isinstance(payload, list) and payload else []
    for row in rows:
        row_text = " ".join(str(value) for value in row.values())
        if code in row_text:
            value = row.get("dqgm")
            return float(str(value).replace(",", ""))
    return None


def fetch_sse_shares(session: requests.Session) -> dict[str, float]:
    params = {
        "isPagination": "true",
        "sqlId": "COMMON_SSE_SJ_JJSJ_JJGM_LOFGMTJ_L",
        "PRODUCT_TYPE": "11,14,15",
        "SEARCH_DATE": "",
        "type": "inParams",
        "pageHelp.pageSize": "10000",
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
    rows = (payload.get("pageHelp") or {}).get("data") or payload.get("result") or []
    result: dict[str, float] = {}
    for row in rows:
        code = str(row.get("FUND_CODE") or row.get("FUND_ID") or row.get("SEC_CODE") or "").zfill(6)
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
    sh_symbols = [symbol for symbol in symbols if symbol.startswith("sh")]
    sz_symbols = [symbol for symbol in symbols if not symbol.startswith("sh")]
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
        raise RuntimeError(f"unusually few share values: {ok_count}/{len(symbols)}")
    out_path = Path(args.outdir) / f"shares_{args.date}.json"
    write_json_atomic(out_path, payload, force=args.force)
    print(f"wrote {out_path} with {ok_count}/{len(symbols)} values")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
