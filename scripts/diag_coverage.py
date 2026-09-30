"""
종목별 Factiva 커버리지 진단 — 수집 누락(Company 필터가 못 잡은 종목) 찾기

  태깅   : CO 코드로 태깅된 기사 수
  제목   : 제목에 종목명/약칭이 나온 기사 수 (태깅 여부 무관)
  제목∩태깅: 제목 언급 기사 중 해당 CO 코드가 붙은 비율 → 낮으면 Factiva가 그 종목을 잘 태깅하지 않음
  본문   : 본문에 대표 표기(body_key)가 나온 기사 수
  제목/본문: 본문에는 자주 나오는데 제목 기사가 거의 없으면 → 그 종목 '주인공' 기사가 다운로드되지 않은 것(수집 누락 의심)

사용법: python scripts/diag_coverage.py [--db data/factiva_news.sqlite]
        [--universe config/universe_ktop30_20260430.csv] [--aliases config/aliases_ktop30.csv]
"""
import argparse
import csv
import sqlite3

p = argparse.ArgumentParser()
p.add_argument("--db", default="data/factiva_news.sqlite")
p.add_argument("--universe", default="config/universe_ktop30_20260430.csv")
p.add_argument("--aliases", default="config/aliases_ktop30.csv")
p.add_argument("--start", default="2026-05-01")
p.add_argument("--end", default="2026-06-30")
a = p.parse_args()

codes = {r["ticker"]: r["factiva_co_code"]
         for r in csv.DictReader(open(a.universe, encoding="utf-8-sig"))}
c = sqlite3.connect(a.db)
rng = (a.start, a.end)

rows = []
for r in csv.DictReader(open(a.aliases, encoding="utf-8-sig")):
    t, code = r["ticker"], codes.get(r["ticker"], "")
    keys = [k.strip() for k in r["aliases"].split(";") if k.strip()]
    cond = " OR ".join(["n.headline LIKE ?"] * len(keys))
    like = [f"%{k}%" for k in keys]
    tagged = c.execute(
        "SELECT COUNT(DISTINCT n.an) FROM factiva_news n JOIN factiva_company fc ON fc.an=n.an "
        "WHERE fc.co_code=? AND n.pub_date BETWEEN ? AND ?", (code, *rng)).fetchone()[0]
    head = c.execute(
        f"SELECT COUNT(*) FROM factiva_news n WHERE ({cond}) AND n.pub_date BETWEEN ? AND ?",
        (*like, *rng)).fetchone()[0]
    head_tag = c.execute(
        f"SELECT COUNT(DISTINCT n.an) FROM factiva_news n JOIN factiva_company fc ON fc.an=n.an "
        f"WHERE fc.co_code=? AND ({cond}) AND n.pub_date BETWEEN ? AND ?",
        (code, *like, *rng)).fetchone()[0]
    body = c.execute(
        "SELECT COUNT(*) FROM factiva_news n WHERE n.body LIKE ? AND n.pub_date BETWEEN ? AND ?",
        (f"%{r['body_key']}%", *rng)).fetchone()[0]
    ratio = head / body if body else 0
    flag = ""
    if body >= 30 and ratio < 0.10:
        flag = "◀ 수집 누락 의심"
    elif head >= 5 and head_tag / head < 0.5:
        flag = "◀ 태깅 약함(이름 매칭 필요)"
    rows.append((t, r["name_kr"], code, tagged, head, head_tag, body, ratio, flag))

print(f"{'ticker':<7}{'종목':<12}{'code':<8}{'태깅':>6}{'제목':>6}{'제목∩태깅':>9}{'본문':>6}{'제목/본문':>9}")
for t, k, code, tg, h, ht, b, rt, fl in sorted(rows, key=lambda x: x[7]):
    print(f"{t:<7}{k:<12}{code:<8}{tg:>6}{h:>6}{ht:>9}{b:>6}{rt:>9.2f}  {fl}")
