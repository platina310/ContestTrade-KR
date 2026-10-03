"""
KTOP30 point-in-time 구성종목 이력 + 수집용 합집합 유니버스 (본 실험, 2025-01~)

    python scripts/make_universe_history.py --start 20241201 --end 20261002

필요 env: KRX_ID/KRX_PW (pykrx 로그인), DART_API_KEY

출력
  data/ktop30_membership.csv   asof(월말 영업일), ticker, name_kr   ← 백테스트 시점별 유니버스(생존편향 제거용)
  data/universe_main.csv       기간 중 한 번이라도 편입된 종목 합집합 (make_universe.py와 같은 컬럼)
                               → collect_market.py / collect_dart.py --universe 로 넘김
엑셀로 열어 저장 금지(앞자리 0 소실).
"""
import argparse
import csv
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

sys.path.insert(0, os.path.dirname(__file__))
from make_universe import NAME_EN, load_dart_corp_map  # noqa: E402

INDEX = "5600"  # KTOP30


def month_ends(start, end):
    for d in pd.date_range(start, end, freq="ME"):
        yield d
    if pd.Timestamp(end) > pd.Timestamp(end).to_period("M").start_time:  # 마지막 달(진행 중)은 end일 기준
        yield pd.Timestamp(end)


def members_asof(day):
    """휴장일이면 최대 7일 후퇴해 직전 영업일 구성종목."""
    d = day.to_pydatetime() if hasattr(day, "to_pydatetime") else day
    for _ in range(8):
        t = stock.get_index_portfolio_deposit_file(INDEX, d.strftime("%Y%m%d"))
        if t:
            return d.strftime("%Y-%m-%d"), list(t)
        d -= timedelta(days=1)
    return None, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20241201")
    ap.add_argument("--end", default=datetime.today().strftime("%Y%m%d"))
    ap.add_argument("--membership", default="data/ktop30_membership.csv")
    ap.add_argument("--out", default="data/universe_main.csv")
    args = ap.parse_args()

    key = os.environ.get("DART_API_KEY") or sys.exit("환경변수 DART_API_KEY 필요")

    snaps, prev = [], None
    for me in sorted(set(month_ends(args.start, args.end))):
        asof, tickers = members_asof(me)
        if not tickers:
            print(f"[경고] {me.date()} 구성종목 없음")
            continue
        s = set(tickers)
        if prev is not None and s != prev:
            print(f"{asof} 변경: 편입 {sorted(s - prev)} / 제외 {sorted(prev - s)}")
        if len(s) != 30:
            print(f"[주의] {asof} 종목 수 {len(s)}")
        snaps.append((asof, sorted(s)))
        prev = s

    names = {}
    with open(args.membership, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["asof", "ticker", "name_kr"])
        for asof, ts in snaps:
            for t in ts:
                names.setdefault(t, stock.get_market_ticker_name(t))
                w.writerow([asof, t, names[t]])

    dart = load_dart_corp_map(key)
    first = {}
    for asof, ts in snaps:
        for t in ts:
            first.setdefault(t, asof)
    rows = [dict(ticker=t, name_kr=names[t], market="KOSPI", dart_corp_code=dart.get(t, ""),
                 name_en=NAME_EN.get(t, ""), factiva_co_code="", asof=first[t]) for t in sorted(first)]
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"\n스냅샷 {len(snaps)}개 ({snaps[0][0]} ~ {snaps[-1][0]}), 합집합 {len(rows)}종목 → {args.out}")
    miss = [r["name_kr"] for r in rows if not r["dart_corp_code"]]
    if miss:
        print("DART corp_code 미매칭:", miss)
    print("영문명 수기 필요:", [r["name_kr"] for r in rows if not r["name_en"]])


if __name__ == "__main__":
    main()
