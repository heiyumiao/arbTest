from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests

try:
    from .collector_common import today_text, write_json_atomic
except ImportError:
    from collector_common import today_text, write_json_atomic


def collect_fx(target_date: str) -> dict:
    url = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/ccpr.json"
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 LOFArb collector"},
        timeout=20,
    )
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
    url = "http://hq.sinajs.cn/list=" + ",".join(symbols)
    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 LOFArb collector",
            "Referer": "https://finance.sina.com.cn",
        },
        timeout=20,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    data = []
    for chunk in response.text.splitlines():
        if '="' not in chunk:
            continue
        name = chunk.split("var hq_str_", 1)[1].split("=", 1)[0]
        parts = chunk.split('"', 2)[1].split(",")
        if len(parts) > 8:
            data.append(
                {
                    "symbol": name.replace("nf_", ""),
                    "close": _float(parts[3]),
                    "settle": _float(parts[8]),
                    "volume": _float(parts[14]) if len(parts) > 14 else None,
                }
            )
    return {"date": target_date, "source": "sina_futures", "data": data}


def collect_woody(target_date: str) -> dict:
    token = os.environ.get("WOODY_BOT_TOKEN") or os.environ.get("WOODY_API_TOKEN")
    if not token:
        return {
            "date": target_date,
            "source": "woody",
            "enabled": False,
            "message": "WOODY token not configured on VPS",
        }
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
