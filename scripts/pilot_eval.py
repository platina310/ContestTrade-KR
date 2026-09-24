"""
KTOP30 파일럿 Pass 2 평가기 — LLM 호출 없음

입력
  contest_trade/agents_workspace/reports/<agent>/<YYYY-MM-DD>_08-30-00.json  (backtest_runner 산출)
  data_collection/universe/ktop30.csv                                          (30종목)
  가격: FinanceDataReader(시가·종가) → data/pilot_prices.csv 캐시, 또는 --prices-csv

규칙 (docs/lookahead_rules.md, docs/DESIGN_CHANGELOG.md)
  - D 08:30 시그널 → D 시가 체결, 보유 수익률 r(i,D) = open(D+1)/open(D) − 1
  - 리서치 에이전트 성과 R(a,D) = 그날 buy 종목 동일가중 r의 평균 (buy 없으면 0 = 현금)
  - 콘테스트 on: û(a,D) = mean/std of R(a,d), d ∈ 최근 m개 & idx(d) ≤ idx(D)−2 (D-2 래그)
                 w_a ∝ max(0, û_a)  (논문 식 8). 이력 부족 시 동일가중, 전원 ≤0이면 --all-neg 규칙
  - 콘테스트 off: 시그널 낸 에이전트 동일가중 (단순 앙상블)
  - 체결: 시가가 상한가(전일 종가 +29.5% 이상)면 매수 불가, 하한가면 매도 불가 → 해당 종목 비중 유지
  - 비용: 차이분만 거래. 매수 = 수수료+슬리피지, 매도 = 수수료+거래세 0.20%+슬리피지
  - 5월 = m 선택용(학습), 6월 = 평가(테스트). 선택은 5월 콘테스트 on 순(net) Sharpe 기준

사용법
  python scripts/pilot_eval.py
  python scripts/pilot_eval.py --m 5            # m 고정
  python scripts/pilot_eval.py --all-neg cash   # 전원 음수면 현금 (기본 equal)
"""
import argparse
import csv
import glob
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

p = argparse.ArgumentParser()
p.add_argument("--reports", default=str(ROOT / "contest_trade/agents_workspace/reports"))
p.add_argument("--universe", default=str(ROOT / "data_collection/universe/ktop30.csv"))
p.add_argument("--prices-csv", default=str(ROOT / "data/pilot_prices.csv"))
p.add_argument("--train", default="2026-05-01:2026-05-31")
p.add_argument("--test", default="2026-06-01:2026-06-30")
p.add_argument("--hour", default="08-30-00")
p.add_argument("--m", type=int, default=0, help="0이면 5월에서 {3,5,10} 중 선택")
p.add_argument("--all-neg", choices=["equal", "cash"], default="equal")
p.add_argument("--commission", type=float, default=0.00015)
p.add_argument("--tax", type=float, default=0.0020)
p.add_argument("--slippage", type=float, default=0.0010)
p.add_argument("--out", default=str(ROOT / "data/pilot_eval"))
p.add_argument("--market-db", default=str(ROOT / "data/market.sqlite"),
               help="KTOP30 지수(index_prices 5600)·시가총액(prices.mktcap)")
p.add_argument("--mega", default="005930,000660", help="대형 반도체 노출 측정 종목")
p.add_argument("--bench-csv", default=str(ROOT / "data/benchmarks/benchmarks_open.csv"),
               help="build_benchmarks.py 산출 지수(시가 기준). 있으면 자체 지수로 평가")
a = p.parse_args()

# ------------------------------------------------------------------ 유니버스·시그널
universe = [r["ticker"].strip() for r in csv.DictReader(open(a.universe, encoding="utf-8-sig"))]
U = set(universe)
SIG = re.compile(r"<signal>(.*?)</signal>", re.S)


def tag(block, name):
    m = re.search(rf"<{name}>\s*(.*?)\s*</{name}>", block, re.S)
    return m.group(1).strip() if m else ""


def load_signals():
    """{agent: {date: [(ticker, action, prob)]}}  — 유니버스 밖 종목은 제외하고 개수 기록"""
    sig, dropped = defaultdict(dict), defaultdict(int)
    for f in sorted(glob.glob(os.path.join(a.reports, "*", f"*_{a.hour}.json"))):
        agent = Path(f).parent.name
        date = Path(f).name[:10]
        txt = json.load(open(f, encoding="utf-8")).get("final_result") or ""
        out = []
        for b in SIG.findall(txt):
            if tag(b, "has_opportunity").lower() == "no":
                continue
            code = re.sub(r"\D", "", tag(b, "symbol_code"))[:6]
            act = tag(b, "action").lower()
            try:
                prob = float(re.sub(r"[^\d.]", "", tag(b, "probability")) or 50) / 100
            except ValueError:
                prob = 0.5
            if code not in U:
                dropped[agent] += 1
                continue
            out.append((code, act, prob))
        sig[agent][date] = out
    return sig, dropped


# ------------------------------------------------------------------ 가격
def load_prices(dates_needed):
    """{ticker: {date: (open, close)}} + 거래일 리스트. 캐시 없으면 FDR로 받아 저장."""
    lo, hi = min(dates_needed), max(dates_needed)
    if not os.path.exists(a.prices_csv):
        import FinanceDataReader as fdr
        import pandas as pd
        end = (pd.Timestamp(hi) + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        start = (pd.Timestamp(lo) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        rows = []
        for t in universe + ["KS11"]:
            df = fdr.DataReader(t, start, end)
            for d, r in df.iterrows():
                rows.append((d.strftime("%Y-%m-%d"), t, float(r["Open"]), float(r["Close"])))
        os.makedirs(os.path.dirname(a.prices_csv), exist_ok=True)
        with open(a.prices_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "ticker", "open", "close"])
            w.writerows(rows)
        print(f"가격 캐시 저장: {a.prices_csv} ({len(rows)}행)")
    px = defaultdict(dict)
    for r in csv.DictReader(open(a.prices_csv, encoding="utf-8")):
        px[r["ticker"]][r["date"]] = (float(r["open"]), float(r["close"]))
    cal = sorted(px["KS11"]) if "KS11" in px else sorted({d for t in px for d in px[t]})
    return px, cal


def ret(px, t, d, dn):
    o0, o1 = px[t].get(d, (None,))[0], px[t].get(dn, (None,))[0]
    return (o1 / o0 - 1) if o0 and o1 else 0.0


def limit_state(px, t, d, dp):
    """시가 기준 상·하한가 여부 (전일 종가 대비 ±29.5%)"""
    if dp not in px[t] or d not in px[t]:
        return 0
    chg = px[t][d][0] / px[t][dp][1] - 1
    return 1 if chg >= 0.295 else (-1 if chg <= -0.295 else 0)


# ------------------------------------------------------------------ 시뮬레이션
def rng(s):
    x, y = s.split(":")
    return x, y


def simulate(sig, px, cal, days, m, contest):
    agents = sorted(sig)
    idx = {d: i for i, d in enumerate(cal)}
    # 에이전트별 일일 성과 R(a,d)
    R = {ag: {} for ag in agents}
    for ag in agents:
        for d in cal[:-1]:
            buys = [c for c, act, _ in sig[ag].get(d, []) if act == "buy"]
            dn = cal[idx[d] + 1]
            R[ag][d] = sum(ret(px, c, d, dn) for c in buys) / len(buys) if buys else 0.0

    hold = {}                       # 현재 보유 비중(시가 직후 기준)
    rows = []
    for d in days:
        i = idx[d]
        if i + 1 >= len(cal):
            break
        dn, dp = cal[i + 1], cal[i - 1] if i > 0 else None
        active = [ag for ag in agents if any(act == "buy" for _, act, _ in sig[ag].get(d, []))]
        # ---- 에이전트 비중
        w = {}
        if active:
            if contest:
                hist = [cal[j] for j in range(max(0, i - 1 - m), i - 1)]      # idx ≤ i-2, 최근 m개
                u = {}
                for ag in active:
                    xs = [R[ag][h] for h in hist if h in R[ag] and h in sig[ag]]
                    if len(xs) >= max(3, m // 2):
                        mu = sum(xs) / len(xs)
                        sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)) or 1e-9
                        u[ag] = mu / sd
                if len(u) < len(active):                    # 이력 부족 → 동일가중
                    w = {ag: 1 / len(active) for ag in active}
                else:
                    pos = {ag: max(0.0, v) for ag, v in u.items()}
                    s = sum(pos.values())
                    if s > 0:
                        w = {ag: v / s for ag, v in pos.items() if v > 0}
                    elif a.all_neg == "equal":
                        w = {ag: 1 / len(active) for ag in active}
            else:
                w = {ag: 1 / len(active) for ag in active}
        # ---- 목표 종목 비중
        target = defaultdict(float)
        for ag, wa in w.items():
            buys = [c for c, act, _ in sig[ag].get(d, []) if act == "buy"]
            for c in buys:
                target[c] += wa / len(buys)
        # ---- 상하한가 제약 (현재 보유 비중에서 늘리기/줄이기 불가)
        for c in set(target) | set(hold):
            st = limit_state(px, c, d, dp) if dp else 0
            if st == 1 and target.get(c, 0) > hold.get(c, 0):
                target[c] = hold.get(c, 0)
            if st == -1 and target.get(c, 0) < hold.get(c, 0):
                target[c] = hold.get(c, 0)
        buy_to = sum(max(0, target.get(c, 0) - hold.get(c, 0)) for c in set(target) | set(hold))
        sell_to = sum(max(0, hold.get(c, 0) - target.get(c, 0)) for c in set(target) | set(hold))
        cost = buy_to * (a.commission + a.slippage) + sell_to * (a.commission + a.tax + a.slippage)
        gross = sum(wt * ret(px, c, d, dn) for c, wt in target.items())
        # ---- 다음날 시가 직전 비중으로 드리프트
        val = {c: wt * (1 + ret(px, c, d, dn)) for c, wt in target.items() if wt > 0}
        tot = sum(val.values()) + (1 - sum(target.values()))
        hold = {c: v / tot for c, v in val.items()} if tot > 0 else {}
        # ---- 종목 점수 (Rank IC용): Σ_a w_a × (buy:+p / sell:−p)
        score = defaultdict(float)
        for ag in agents:
            wa = w.get(ag, 0.0) if contest else (1 / len(agents))
            for c, act, pr in sig[ag].get(d, []):
                score[c] += wa * (pr if act == "buy" else -pr if act == "sell" else 0)
        rows.append({"date": d, "gross": gross, "net": gross - cost, "turnover": buy_to + sell_to,
                     "cost": cost, "n_names": sum(1 for v in target.values() if v > 0),
                     "mega_w": sum(target.get(c, 0) for c in MEGA),
                     "weights": {k: round(v, 3) for k, v in w.items()},
                     "ic": rank_ic(score, {c: ret(px, c, d, dn) for c in universe})})
    return rows


def rank_ic(score, r):
    xs = [(score.get(c, 0.0), r[c]) for c in r]
    if len({s for s, _ in xs}) < 2:
        return None
    def rk(v):
        order = sorted(range(len(v)), key=lambda k: v[k])
        out = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2
            i = j + 1
        return out
    a1, b1 = rk([s for s, _ in xs]), rk([t for _, t in xs])
    ma, mb = sum(a1) / len(a1), sum(b1) / len(b1)
    num = sum((x - ma) * (y - mb) for x, y in zip(a1, b1))
    den = math.sqrt(sum((x - ma) ** 2 for x in a1) * sum((y - mb) ** 2 for y in b1))
    return num / den if den else None


def stats(rs):
    if not rs:
        return dict(CR=0, SR=0, MDD=0)
    v, peak, mdd = 1.0, 1.0, 0.0
    for x in rs:
        v *= 1 + x
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    mu = sum(rs) / len(rs)
    sd = math.sqrt(sum((x - mu) ** 2 for x in rs) / max(1, len(rs) - 1))
    return dict(CR=v - 1, SR=(mu / sd * math.sqrt(252)) if sd else 0.0, MDD=mdd)


def load_market():
    """KTOP30 지수 시가 {date: open}, 시가총액 {ticker: {date: mktcap}} — 없으면 빈 dict"""
    import sqlite3
    if not os.path.exists(a.market_db):
        return {}, {}
    con = sqlite3.connect(a.market_db)
    def num(x):   # pykrx의 numpy int64가 sqlite에 8바이트 BLOB로 저장된 경우 복원
        if isinstance(x, (bytes, bytearray)):
            return float(int.from_bytes(x, "little", signed=True))
        return float(x) if x is not None else None
    ktop = {d: num(o) for d, o in con.execute(
        "SELECT date, open FROM index_prices WHERE index_code='5600'")}
    ktop = {d: o for d, o in ktop.items() if o and o > 0}
    cap = defaultdict(dict)
    for t, d, mc in con.execute("SELECT ticker, date, mktcap FROM prices"):
        v = num(mc)
        if v and v > 0:
            cap[t.zfill(6)][d] = v
    return ktop, cap


def bench(px, cal, days, kind):
    idx = {d: i for i, d in enumerate(cal)}
    out = []
    for d in days:
        i = idx[d]
        if i + 1 >= len(cal):
            break
        dn, dp = cal[i + 1], cal[i - 1]
        if kind == "EW30":
            out.append(sum(ret(px, c, d, dn) for c in universe) / len(universe))
        elif kind == "KS11":
            out.append(ret(px, "KS11", d, dn))
        elif kind == "KTOP30":                       # 공식 지수(주가가중), 시가→시가
            if d not in KTOP or dn not in KTOP:
                return None
            out.append(KTOP[dn] / KTOP[d] - 1)
        elif kind == "CAP30":                        # 30종목 시총가중, 비중은 D-1 종가 시총(D 08:30에 알 수 있는 값)
            w = {c: CAP[c].get(dp) for c in universe if CAP.get(c, {}).get(dp)}
            if len(w) < len(universe) * 0.8:
                return None
            tot = sum(w.values())
            out.append(sum(v / tot * ret(px, c, d, dn) for c, v in w.items()))
        elif kind == "MEGA2":                        # 삼성전자·SK하이닉스 50:50
            out.append(sum(ret(px, c, d, dn) for c in MEGA) / len(MEGA))
    return out


# ------------------------------------------------------------------ 실행
MEGA = [c.strip() for c in a.mega.split(",") if c.strip()]
KTOP, CAP = load_market()
sig, dropped = load_signals()
if not sig:
    raise SystemExit(f"시그널 파일이 없습니다: {a.reports}/*/*_{a.hour}.json")
all_dates = sorted({d for ag in sig for d in sig[ag]})
px, cal = load_prices(all_dates)
tr, te = rng(a.train), rng(a.test)
train_days = [d for d in cal if tr[0] <= d <= tr[1] and any(d in sig[ag] for ag in sig)]
test_days = [d for d in cal if te[0] <= d <= te[1] and any(d in sig[ag] for ag in sig)]
missing = [d for d in cal if te[0] <= d <= te[1] and d not in test_days]
print(f"에이전트 {sorted(sig)} | 시그널 파일 {sum(len(v) for v in sig.values())}개 | "
      f"학습 {len(train_days)}일 · 테스트 {len(test_days)}일")
if missing:
    print(f"[경고] 테스트 구간 시그널 없는 거래일: {missing}")
if any(dropped.values()):
    print(f"[참고] 유니버스 밖 시그널 제외: {dict(dropped)}")

# m 선택 (5월, 콘테스트 on, net Sharpe)
if a.m:
    m = a.m
else:
    print("\n[학습 5월] m 선택 — 콘테스트 on, 순수익 Sharpe")
    best = None
    for mm in (3, 5, 10):
        s = stats([r["net"] for r in simulate(sig, px, cal, train_days, mm, True)])
        print(f"  m={mm:<3} CR {s['CR']:+.2%}  SR {s['SR']:+.2f}  MDD {s['MDD']:.2%}")
        if best is None or s["SR"] > best[1]:
            best = (mm, s["SR"])
    m = best[0]
print(f"→ m = {m} 고정 (6월에 변경 금지)")

# 6월 평가 — 5월부터 이어서 시뮬레이션해 보유 상태·이력을 유지한 뒤 6월 구간만 집계
full = sorted(set(train_days) | set(test_days))
res = {}
for name, contest in (("contest_on", True), ("contest_off", False)):
    rows = [r for r in simulate(sig, px, cal, full, m, contest) if te[0] <= r["date"] <= te[1]]
    res[name] = rows
print(f"\n[테스트 6월] {len(test_days)}거래일 — 비용: 매도 거래세 {a.tax:.2%}, 수수료 {a.commission:.3%}, 슬리피지 {a.slippage:.2%}")
print(f"{'':<14}{'CR(net)':>9}{'SR(net)':>9}{'MDD':>8}{'CR(gross)':>11}{'회전율/일':>10}{'RankIC':>8}{'ICIR':>7}")
for name, rows in res.items():
    n, g = stats([r["net"] for r in rows]), stats([r["gross"] for r in rows])
    ics = [r["ic"] for r in rows if r["ic"] is not None]
    mic = sum(ics) / len(ics) if ics else float("nan")
    sdic = math.sqrt(sum((x - mic) ** 2 for x in ics) / max(1, len(ics) - 1)) if len(ics) > 1 else float("nan")
    to = sum(r["turnover"] for r in rows) / max(1, len(rows))
    print(f"{name:<14}{n['CR']:>+9.2%}{n['SR']:>9.2f}{n['MDD']:>8.2%}{g['CR']:>+11.2%}{to:>10.2f}"
          f"{mic:>8.3f}{(mic / sdic if sdic else float('nan')):>7.2f}")
def bench_file(col):
    """build_benchmarks.py 지수 레벨 → 테스트일별 시가→다음 시가 수익률"""
    if not os.path.exists(a.bench_csv):
        return None
    lv = {r["date"]: float(r[col]) for r in csv.DictReader(open(a.bench_csv, encoding="utf-8"))}
    idx = {d: i for i, d in enumerate(cal)}
    out = []
    for d in test_days:
        i = idx[d]
        if i + 1 >= len(cal):
            break
        if d not in lv or cal[i + 1] not in lv:
            return None
        out.append(lv[cal[i + 1]] / lv[d] - 1)
    return out


labels = {"KTOP30": "KTOP30지수", "CAP30": "30종목시총가중", "EW30": "30종목동일가중",
          "MEGA2": "삼전·하닉50:50", "KS11": "KOSPI(참고)",
          "F:CAP": "[자체]시총가중지수", "F:CAP_C": "[자체]시총가중(상한)", "F:EW_M": "[자체]동일가중(월)"}
use_file = bench_file("CAP") is not None
kinds = (("F:CAP", "F:CAP_C", "F:EW_M", "KTOP30", "MEGA2", "KS11") if use_file
         else ("KTOP30", "CAP30", "EW30", "MEGA2", "KS11"))
if not use_file:
    print(f"  (자체 지수 파일 없음 — scripts/build_benchmarks.py 실행 시 시총가중·동일가중 지수로 대체)")
for kind in kinds:
    rs = bench_file(kind[2:]) if kind.startswith("F:") else bench(px, cal, test_days, kind)
    if rs is None:
        print(f"{labels[kind]:<14}  (데이터 없음 — {a.market_db} 확인)")
        continue
    s = stats(rs)
    print(f"{labels[kind]:<14}{s['CR']:>+9.2%}{s['SR']:>9.2f}{s['MDD']:>8.2%}{'(비용 없음)':>11}")

# 대형 반도체 노출 — 성과가 기울기로 설명되는지
print(f"\n[노출] 6월 평균 {'+'.join(MEGA)} 비중:", end="")
for name, rows in res.items():
    print(f"  {name} {sum(r['mega_w'] for r in rows) / max(1, len(rows)):.0%}", end="")
cw = []
for d in test_days:
    i = cal.index(d)
    dp = cal[i - 1]
    w = {c: CAP[c].get(dp) for c in universe if CAP.get(c, {}).get(dp)}
    if w:
        cw.append(sum(w.get(c, 0) for c in MEGA) / sum(w.values()))
print(f"  | 30종목 시총 내 비중 {sum(cw) / len(cw):.0%}" if cw else "")

# 에이전트별 성과(6월, 비용 전)와 콘테스트 평균 비중
print("\n[에이전트별 6월] 매수종목 동일가중, 비용 전")
idx_ = {d: i for i, d in enumerate(cal)}
wavg = defaultdict(float)
for r in res["contest_on"]:
    for ag, v in r["weights"].items():
        wavg[ag] += v / len(res["contest_on"])
print(f"{'agent':<10}{'CR':>9}{'SR':>7}{'매수일':>7}{'평균종목':>8}{'대형반도체':>10}{'콘테스트비중':>12}")
for ag in sorted(sig):
    rs, npick, mega, days_on = [], 0, 0, 0
    for d in test_days:
        i = idx_[d]
        if i + 1 >= len(cal):
            continue
        buys = [c for c, act, _ in sig[ag].get(d, []) if act == "buy"]
        rs.append(sum(ret(px, c, d, cal[i + 1]) for c in buys) / len(buys) if buys else 0.0)
        if buys:
            days_on += 1
            npick += len(buys)
            mega += sum(1 for c in buys if c in MEGA)
    s = stats(rs)
    print(f"{ag:<10}{s['CR']:>+9.2%}{s['SR']:>7.2f}{days_on:>7}{npick / max(1, days_on):>8.1f}"
          f"{mega / max(1, npick):>10.0%}{wavg.get(ag, 0):>12.0%}")

os.makedirs(a.out, exist_ok=True)
for name, rows in res.items():
    with open(os.path.join(a.out, f"{name}_daily.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["date"])
        w.writeheader()
        for r in rows:
            w.writerow({**r, "weights": json.dumps(r["weights"], ensure_ascii=False)})
print(f"\n일별 결과: {a.out}/contest_on_daily.csv, contest_off_daily.csv")
print("주의: 테스트 약 21거래일 — 수치는 파이프라인 검증용이며 통계적 결론의 근거가 아님")
