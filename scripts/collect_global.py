"""
D12 글로벌 반도체 오버나이트 — 미국 지수·반도체 종목·환율 일별 시세 (yfinance → market_main.sqlite)

    pip install yfinance
    python scripts/collect_global.py --start 20241001 --end 20261002 --db data/market_main.sqlite

테이블 global_prices (PK symbol, date)
  symbol  ^SOX(필라델피아 반도체) ^IXIC(나스닥) ^GSPC(S&P500) NVDA MU TSM(ADR) AMD ASML KRW=X(원/달러)
  date    미국 현지 거래일 (YYYY-MM-DD). 뉴욕 정규장 마감 16:00 ET = KST 다음날 05:00(서머타임)/06:00
          → 미국 D일 종가는 한국 D+1 08:30 시그널에 사용 가능. 어댑터에서 kr_signal_date = 다음 한국 영업일 로 매핑.
  open high low close volume

주의: yfinance는 비공식 API라 간헐적으로 빈 결과를 돌려줌 → 요약에서 심볼별 건수 확인. KRW=X는 거래량 없음(0).
"""
import argparse
import sqlite3

import pandas as pd
import yfinance as yf

SYMBOLS = ["^SOX", "^IXIC", "^GSPC", "NVDA", "MU", "TSM", "AMD", "ASML", "KRW=X"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS global_prices (
    symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, date));
"""


def f(x):
    return None if x is None or x != x else float(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20241001")
    ap.add_argument("--end", default="20261002")
    ap.add_argument("--db", default="data/market_main.sqlite")
    ap.add_argument("--symbols", nargs="*", default=SYMBOLS)
    args = ap.parse_args()

    s = pd.to_datetime(args.start).strftime("%Y-%m-%d")
    e = (pd.to_datetime(args.end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")  # yfinance end는 배타적
    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)

    for sym in args.symbols:
        df = yf.download(sym, start=s, end=e, interval="1d", auto_adjust=False, progress=False)
        if df.empty:
            print(f"{sym}: 0 (빈 결과 — 재시도 필요)")
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        rows = [(sym, d.strftime("%Y-%m-%d"), f(r["Open"]), f(r["High"]), f(r["Low"]), f(r["Close"]), f(r.get("Volume")))
                for d, r in df.iterrows()]
        con.executemany("INSERT OR REPLACE INTO global_prices VALUES (?,?,?,?,?,?,?)", rows)
        con.commit()
        print(f"{sym}: {len(rows)} days ({rows[0][1]} ~ {rows[-1][1]})")

    print("\n요약:", con.execute("SELECT COUNT(DISTINCT symbol), MIN(date), MAX(date), COUNT(*) FROM global_prices").fetchone())


if __name__ == "__main__":
    main()
