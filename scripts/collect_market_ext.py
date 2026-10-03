"""
수급 세분화·공매도·외국인 한도·밸류에이션 수집 (pykrx → market_main.sqlite 에 테이블 추가)

    python scripts/collect_market_ext.py --universe data/universe_main.csv --start 20241001 --end 20261002 --db data/market_main.sqlite

필요 env: KRX_ID / KRX_PW (pykrx 로그인). collect_market.py 와 같은 DB에 테이블만 추가하므로 먼저 collect_market.py 를 돌릴 것.

테이블 (모두 PK = ticker, date / 날짜는 YYYY-MM-DD / 금액은 원, 수량은 주)
  flows_detail   D13 투자자별 순매수 금액 세분화: fin_inv(금융투자) insurance(보험) trust(투신) private_fund(사모)
                 bank(은행) other_fin(기타금융) pension(연기금) other_corp(기타법인) indiv(개인)
                 foreign_(외국인) other_foreign(기타외국인) total(전체)
  short_trading  D4 공매도 거래: short_vol, buy_vol(전체 매수 거래량), short_vol_ratio(%), short_val, buy_val, short_val_ratio(%)
  short_balance  D4 공매도 잔고: bal_qty, listed_shares, bal_val, mktcap, bal_ratio(%)   ※ 잔고는 T+2 공표 → 어댑터에서 date+2 영업일 이후부터 사용
  foreign_limit  D13 외국인 보유·한도: listed_shares, held_qty, held_ratio(%), limit_qty, exhaustion(한도소진률 %)
  fundamentals   R3 저PBR 판정용: bps, per, pbr, eps, div(배당수익률 %), dps

주의
  - 공매도는 2023-11-06~2025-03-30 전면 금지(시장조성자·유동성공급자 예외) → 2025-01~03 구간 short_vol은 대부분 0.
    공매도 팩터(D4·R4)는 2025-03-31 재개 이후 구간에만 의미가 있음. 어댑터/변경로그에 기록할 것.
  - 대차잔고·신용잔고는 KRX가 아닌 금융투자협회(KOFIA) 자료라 pykrx에 없음. 필요하면 별도 소스.
  - 재실행하면 같은 (ticker,date)는 덮어씀(INSERT OR REPLACE). 종목·테이블 단위로 실패해도 다음으로 넘어가고 끝에 실패 목록 출력.
"""
import argparse
import csv
import sqlite3
import time

import pandas as pd
from pykrx import stock

SCHEMA = """
CREATE TABLE IF NOT EXISTS flows_detail (
    ticker TEXT, date TEXT, fin_inv REAL, insurance REAL, trust REAL, private_fund REAL, bank REAL,
    other_fin REAL, pension REAL, other_corp REAL, indiv REAL, foreign_ REAL, other_foreign REAL, total REAL,
    PRIMARY KEY (ticker, date));
CREATE TABLE IF NOT EXISTS short_trading (
    ticker TEXT, date TEXT, short_vol REAL, buy_vol REAL, short_vol_ratio REAL,
    short_val REAL, buy_val REAL, short_val_ratio REAL, PRIMARY KEY (ticker, date));
CREATE TABLE IF NOT EXISTS short_balance (
    ticker TEXT, date TEXT, bal_qty REAL, listed_shares REAL, bal_val REAL, mktcap REAL, bal_ratio REAL,
    PRIMARY KEY (ticker, date));
CREATE TABLE IF NOT EXISTS foreign_limit (
    ticker TEXT, date TEXT, listed_shares REAL, held_qty REAL, held_ratio REAL, limit_qty REAL, exhaustion REAL,
    PRIMARY KEY (ticker, date));
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker TEXT, date TEXT, bps REAL, per REAL, pbr REAL, eps REAL, div REAL, dps REAL,
    PRIMARY KEY (ticker, date));
"""


def f(x):
    try:
        return None if x is None or x != x else float(x)
    except (TypeError, ValueError):
        return None


def fmt(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    return df


def chunked(fn, s, e, months=3):
    """KRX 공매도 화면은 긴 조회기간을 거부(응답에 output 없음) → 3개월 단위로 나눠 받아 합친다."""
    parts, cur = [], pd.to_datetime(s)
    end = pd.to_datetime(e)
    while cur <= end:
        nxt = min(cur + pd.DateOffset(months=months) - pd.Timedelta(days=1), end)
        df = None
        for attempt, wait in enumerate((0, 5, 20, 60)):   # KRX가 연결을 끊으면 점점 길게 쉬고 재시도
            if wait:
                time.sleep(wait)
            try:
                df = fn(cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d"))
                break
            except Exception:  # noqa: BLE001
                if attempt == 3:
                    raise
        if df is not None and len(df):
            parts.append(df)
        cur = nxt + pd.Timedelta(days=1)
        time.sleep(0.7)
    if not parts:
        raise RuntimeError("빈 응답 — 조회기간을 더 줄이거나 KRX 화면 변경 여부 확인")
    return pd.concat(parts)[lambda d: ~d.index.duplicated()]


def flows_detail(con, t, s, e):
    df = fmt(stock.get_market_trading_value_by_date(s, e, t, detail=True))
    cols = ["금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금", "기타법인", "개인", "외국인", "기타외국인", "전체"]
    rows = [(t, d, *[f(r.get(c)) for c in cols]) for d, r in df.iterrows()]
    con.executemany("INSERT OR REPLACE INTO flows_detail VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def short_trading(con, t, s, e):
    v = fmt(chunked(lambda a, b: stock.get_shorting_volume_by_date(a, b, t), s, e))
    m = fmt(chunked(lambda a, b: stock.get_shorting_value_by_date(a, b, t), s, e))
    rows = []
    for d, r in v.iterrows():
        mv = m.loc[d] if d in m.index else {}
        rows.append((t, d, f(r.get("공매도")), f(r.get("매수")), f(r.get("비중")),
                     f(mv.get("공매도")), f(mv.get("매수")), f(mv.get("비중"))))
    con.executemany("INSERT OR REPLACE INTO short_trading VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def short_balance(con, t, s, e):
    df = fmt(chunked(lambda a, b: stock.get_shorting_balance_by_date(a, b, t), s, e))
    rows = [(t, d, f(r.get("공매도잔고")), f(r.get("상장주식수")), f(r.get("공매도금액")), f(r.get("시가총액")), f(r.get("비중")))
            for d, r in df.iterrows()]
    con.executemany("INSERT OR REPLACE INTO short_balance VALUES (?,?,?,?,?,?,?)", rows)
    return len(rows)


def foreign_limit(con, t, s, e):
    df = fmt(stock.get_exhaustion_rates_of_foreign_investment_by_date(s, e, t))
    rows = [(t, d, f(r.get("상장주식수")), f(r.get("보유수량")), f(r.get("지분율")), f(r.get("한도수량")), f(r.get("한도소진률")))
            for d, r in df.iterrows()]
    con.executemany("INSERT OR REPLACE INTO foreign_limit VALUES (?,?,?,?,?,?,?)", rows)
    return len(rows)


def fundamentals(con, t, s, e):
    df = fmt(stock.get_market_fundamental_by_date(s, e, t))
    rows = [(t, d, f(r.get("BPS")), f(r.get("PER")), f(r.get("PBR")), f(r.get("EPS")), f(r.get("DIV")), f(r.get("DPS")))
            for d, r in df.iterrows()]
    con.executemany("INSERT OR REPLACE INTO fundamentals VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


JOBS = [("flows_detail", flows_detail), ("short_trading", short_trading), ("short_balance", short_balance),
        ("foreign_limit", foreign_limit), ("fundamentals", fundamentals)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="data/universe_main.csv")
    ap.add_argument("--start", default="20241001")
    ap.add_argument("--end", default="20261002")
    ap.add_argument("--db", default="data/market_main.sqlite")
    ap.add_argument("--only", nargs="*", default=None, help="일부 테이블만: flows_detail short_trading short_balance foreign_limit fundamentals")
    ap.add_argument("--tickers", nargs="*", default=None, help="일부 종목만(실패분 재실행용), 6자리 코드")
    args = ap.parse_args()

    uni = list(csv.DictReader(open(args.universe, encoding="utf-8-sig")))
    if args.tickers:
        want = {t.zfill(6) for t in args.tickers}
        uni = [r for r in uni if str(r["ticker"]).strip().zfill(6) in want]
    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)
    jobs = [(n, fn) for n, fn in JOBS if not args.only or n in args.only]

    failed = []
    for r in uni:
        t = str(r["ticker"]).strip().zfill(6)
        out = []
        for name, fn in jobs:
            try:
                out.append(f"{name}={fn(con, t, args.start, args.end)}")
            except Exception as ex:  # noqa: BLE001
                failed.append((t, name, repr(ex)[:120]))
                out.append(f"{name}=FAIL")
            con.commit()
            time.sleep(1.0)
        print(f"{t} {r['name_kr']}: " + " ".join(out))

    print("\n요약:")
    for name, _ in jobs:
        print(" ", name, con.execute(f"SELECT COUNT(DISTINCT ticker), MIN(date), MAX(date), COUNT(*) FROM {name}").fetchone())
    for label, cond in (("재개 전(금지 기간)", "date <  '2025-03-31'"), ("재개 후", "date >= '2025-03-31'")):
        r = con.execute(f"SELECT AVG(short_vol), AVG(short_vol_ratio) FROM short_trading WHERE {cond}").fetchone()
        print(f"  공매도 {label}: 일평균 {r[0] or 0:,.0f}주, 매수 대비 {r[1] or 0:.2f}%")
    if failed:
        print("\n실패 목록 (재실행: --only <테이블>):")
        for x in failed:
            print("  ", x)


if __name__ == "__main__":
    main()
