"""
KTOP30 구성 30종목 자체 벤치마크 지수 산출 (가격지수, 비용·배당 미반영)

왜: 공식 KTOP30은 주가평균식(다우식)이라 고가주 비중이 커서, 시가총액에 비례해
    노출되는 포트폴리오의 벤치마크로 부적합 → 같은 30종목으로 두 지수를 직접 만든다.

지수 (기준일 종가/시가 = 1000)
  CAP     시가총액가중. 상장주식수(=시총/종가, 직전 거래일 값)를 고정하고 주가만 반영
          → 코스피 등 시총식 지수와 같은 방식. 유동비율 미반영(데이터 없음, 한계로 명시)
  CAP_C   시가총액가중 + 종목당 상한(--cap, 기본 25%). 월초 리밸런싱 후 드리프트
  EW_M    동일가중, 월초 1/30로 리밸런싱 후 드리프트 (일반적인 동일가중 지수 방식)
  EW_D    동일가중, 매일 1/30 리밸런싱 (평가기 EW30과 동일, 참고용)
  KTOP30  공식 지수(주가평균식) — 비교 참고

두 가지 기준으로 산출
  open   시가→다음날 시가  (파일럿 체결 가정과 일치 — 평가용)
  close  종가→다음날 종가  (일반적인 지수 표기 — 보고용)

입력  data/market.sqlite (prices: open/close/mktcap, index_prices: 5600)
      data_collection/universe/ktop30.csv (2026-04-30 기준 구성, 기간 중 고정)
출력  data/benchmarks/benchmarks_open.csv, benchmarks_close.csv (일별 지수 레벨)

사용법
  python scripts/build_benchmarks.py
  python scripts/build_benchmarks.py --base 2026-04-30 --cap 0.25
"""
import argparse
import csv
import math
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--db", default=str(ROOT / "data/market.sqlite"))
p.add_argument("--universe", default=str(ROOT / "data_collection/universe/ktop30.csv"))
p.add_argument("--base", default="2026-04-30", help="기준일(지수 1000)")
p.add_argument("--cap", type=float, default=0.25, help="CAP_C 종목당 비중 상한")
p.add_argument("--out", default=str(ROOT / "data/benchmarks"))
p.add_argument("--report", default="2026-06-01:2026-06-30", help="요약 표 구간")
a = p.parse_args()


def num(x):  # pykrx numpy int64가 8바이트 BLOB로 저장된 경우 복원
    if isinstance(x, (bytes, bytearray)):
        return float(int.from_bytes(x, "little", signed=True))
    return float(x) if x is not None else None


uni = [r["ticker"].strip().zfill(6) for r in csv.DictReader(open(a.universe, encoding="utf-8-sig"))]
con = sqlite3.connect(a.db)
px = defaultdict(dict)                      # px[t][d] = (open, close, mktcap)
for t, d, o, c, mc in con.execute("SELECT ticker, date, open, close, mktcap FROM prices"):
    t = t.zfill(6)
    if t in uni:
        px[t][d] = (num(o), num(c), num(mc))
ktop = {d: (num(o), num(c)) for d, o, c in con.execute(
    "SELECT date, open, close FROM index_prices WHERE index_code='5600'")}
cal = sorted(set.intersection(*[set(px[t]) for t in uni]))
missing = [t for t in uni if not px[t]]
if missing:
    raise SystemExit(f"시세 없는 종목: {missing}")
cal = [d for d in cal if d >= a.base]
if not cal or cal[0] != a.base:
    raise SystemExit(f"기준일 {a.base}가 거래일이 아니거나 데이터 없음 (첫 거래일 {cal[0] if cal else None})")


def shares(t, d):
    o, c, mc = px[t][d]
    return mc / c if mc and c else None


def build(field):
    """field: 0=open, 1=close. 기간 [d_i → d_{i+1}] 수익률로 지수 레벨 생성"""
    lv = {k: [1000.0] for k in ("CAP", "CAP_C", "EW_M", "EW_D", "KTOP30")}
    wd = {"CAP_C": {}, "EW_M": {}}          # 드리프트 중인 비중
    for i in range(len(cal) - 1):
        d, dn = cal[i], cal[i + 1]
        r = {t: px[t][dn][field] / px[t][d][field] - 1 for t in uni}
        # 비중을 정할 때 쓰는 주식수: open 기준이면 D-1 종가 시점, close 기준이면 D 종가 시점 값
        ref = cal[i - 1] if (field == 0 and i > 0) else d
        sh = {t: shares(t, ref) for t in uni}
        capv = {t: sh[t] * px[t][d][field] for t in uni}
        tot = sum(capv.values())
        lv["CAP"].append(lv["CAP"][-1] * (1 + sum(capv[t] / tot * r[t] for t in uni)))
        # 월초(또는 첫날) 리밸런싱
        if i == 0 or d[:7] != cal[i - 1][:7]:
            w = {t: capv[t] / tot for t in uni}
            for _ in range(50):             # 상한 초과분을 나머지에 비례 재배분
                over = {t: v for t, v in w.items() if v > a.cap}
                if not over:
                    break
                excess = sum(v - a.cap for v in over.values())
                rest = sum(v for t, v in w.items() if t not in over)
                w = {t: (a.cap if t in over else v + excess * v / rest) for t, v in w.items()}
            wd["CAP_C"] = w
            wd["EW_M"] = {t: 1 / len(uni) for t in uni}
        for k in ("CAP_C", "EW_M"):
            rt = sum(wd[k][t] * r[t] for t in uni)
            lv[k].append(lv[k][-1] * (1 + rt))
            wd[k] = {t: wd[k][t] * (1 + r[t]) / (1 + rt) for t in uni}
        lv["EW_D"].append(lv["EW_D"][-1] * (1 + sum(r.values()) / len(uni)))
        kt = (ktop.get(dn, (None, None))[field] or 0) / (ktop.get(d, (None, None))[field] or 1) - 1 \
            if ktop.get(d) and ktop.get(dn) else 0.0
        lv["KTOP30"].append(lv["KTOP30"][-1] * (1 + kt))
    return lv


def stats(levels, dates, lo, hi, field):
    idx = [i for i, d in enumerate(dates) if lo <= d <= hi]
    if len(idx) < 2:
        return None
    if field == 0:   # 시가 기준: 구간 첫날 시가 → 구간 마지막날 다음 거래일 시가 (평가기와 동일)
        seg = levels[idx[0]: idx[-1] + 2]
    else:            # 종가 기준: 직전월 마지막 종가 → 구간 마지막날 종가 (일반 월수익률)
        seg = levels[max(0, idx[0] - 1): idx[-1] + 1]
    rs = [seg[j + 1] / seg[j] - 1 for j in range(len(seg) - 1)]
    v, pk, mdd = 1.0, 1.0, 0.0
    for x in rs:
        v *= 1 + x
        pk = max(pk, v)
        mdd = max(mdd, 1 - v / pk)
    mu = sum(rs) / len(rs)
    sd = math.sqrt(sum((x - mu) ** 2 for x in rs) / max(1, len(rs) - 1))
    return v - 1, (mu / sd * math.sqrt(252) if sd else 0.0), mdd


os.makedirs(a.out, exist_ok=True)
lo, hi = a.report.split(":")
names = {"CAP": "시총가중", "CAP_C": f"시총가중(상한{a.cap:.0%})", "EW_M": "동일가중(월리밸)",
         "EW_D": "동일가중(일리밸)", "KTOP30": "KTOP30 공식(주가평균)"}
for field, tag in ((0, "open"), (1, "close")):
    lv = build(field)
    path = os.path.join(a.out, f"benchmarks_{tag}.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date"] + list(lv))
        for i, d in enumerate(cal):
            w.writerow([d] + [round(lv[k][i], 4) for k in lv])
    print(f"\n[{tag} 기준] {path}  (기준일 {a.base} = 1000, {len(cal)}거래일)")
    print(f"  {lo}~{hi}:  {'CR':>8}{'SR':>7}{'MDD':>8}")
    for k in lv:
        s = stats(lv[k], cal, lo, hi, field)
        if s:
            print(f"  {names[k]:<18}{s[0]:>+8.2%}{s[1]:>7.2f}{s[2]:>8.2%}")

# 비중 스냅샷 — 상위 5종목 (기준일)
sh0 = {t: shares(t, cal[0]) * px[t][cal[0]][1] for t in uni}
tot0 = sum(sh0.values())
top = sorted(sh0.items(), key=lambda kv: -kv[1])[:5]
print("\n[기준일 시총 비중 상위 5] " + ", ".join(f"{t} {v / tot0:.1%}" for t, v in top))
print("한계: 상장주식수 기준(유동비율 미반영), 가격지수(배당 제외), 구성종목 기간 중 고정(4/30 기준)")
