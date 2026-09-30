"""
리서치 에이전트 시그널을 실행(v1/v2/v3…)·날짜·에이전트별로 한 파일에 모은다. LLM 호출 없음.

출력 (원문 근거가 들어가므로 data/ 아래에 저장 — git 커밋 금지)
  data/signals_all.csv      : 한 행 = 한 시그널 (실행, 날짜, 에이전트, 종목, 확률, 근거, 출처, 한계)
  data/signals_overlap.csv  : 날짜별 실행 쌍의 선택 겹침 (에이전트:종목 기준 Jaccard)

사용법 (저장소 루트에서)
  python scripts/export_signals.py
  python scripts/export_signals.py --date 2026-05-29
  python scripts/export_signals.py --run v1=data/pilot_2026-06_v1_workspace/reports --run v3=contest_trade/agents_workspace/reports
엑셀에서 바로 열리도록 UTF-8 BOM(utf-8-sig)으로 저장한다.
"""
import argparse
import csv
import glob
import itertools
import json
import os
import re
from collections import defaultdict

DEFAULT_RUNS = [
    "v1=data/pilot_2026-06_v1_workspace/reports",
    "v2=data/pilot_v2_workspace/reports",
    "v3=contest_trade/agents_workspace/reports",
]

p = argparse.ArgumentParser()
p.add_argument("--run", action="append", help="이름=reports 폴더 (여러 번 지정 가능)")
p.add_argument("--date", help="특정 날짜만 (YYYY-MM-DD). 생략하면 전체")
p.add_argument("--hour", default="08-30-00")
p.add_argument("--out", default="data/signals_all.csv")
p.add_argument("--overlap-out", default="data/signals_overlap.csv")
a = p.parse_args()

SIG = re.compile(r"<signal>(.*?)</signal>", re.S)


def tag(block, name):
    m = re.search(rf"<{name}>\s*(.*?)\s*</{name}>", block, re.S)
    return m.group(1).strip() if m else ""


def tags(block, name):
    return [s.strip() for s in re.findall(rf"<{name}>\s*(.*?)\s*</{name}>", block, re.S)]


def belief_label(text):
    if "이벤트 드리븐" in text:
        return "이벤트 드리븐(공시)"
    if "리서치를 추종" in text or "애널리스트" in text:
        return "리서치 추종"
    if "가격·수급" in text or "수급" in text:
        return "가격·수급"
    return text[:20]


runs = []
for spec in (a.run or DEFAULT_RUNS):
    name, _, path = spec.partition("=")
    if not os.path.isdir(path):
        print(f"[건너뜀] {name}: 폴더 없음 → {path}")
        continue
    runs.append((name, path))

rows = []
picks = defaultdict(lambda: defaultdict(set))  # picks[date][run] = {"agent_0:005930", ...}
present = defaultdict(lambda: defaultdict(set))  # present[date][run] = {"agent_0", ...} 파일이 있는 에이전트
pattern = f"{a.date}_{a.hour}.json" if a.date else f"*_{a.hour}.json"
for run, root in runs:
    files = sorted(glob.glob(os.path.join(root, "agent_*", pattern)))
    for f in files:
        agent = os.path.basename(os.path.dirname(f))
        date = os.path.basename(f)[:10]
        d = json.load(open(f, encoding="utf-8"))
        belief = belief_label(d.get("belief", ""))
        txt = d.get("final_result") or ""
        blocks = SIG.findall(txt)
        picks[date][run]  # 날짜·실행 키는 시그널이 없어도 만든다
        present[date][run].add(agent)
        rank = 0
        for b in blocks:
            if tag(b, "has_opportunity").lower() == "no":
                continue
            rank += 1
            code = re.sub(r"\D", "", tag(b, "symbol_code"))[:6]
            evid = tags(b, "evidence")
            rows.append({
                "run": run, "date": date, "agent": agent, "belief": belief, "rank": rank,
                "symbol_code": code, "symbol_name": tag(b, "symbol_name"),
                "action": tag(b, "action").lower(), "probability": tag(b, "probability"),
                "n_evidence": len(evid),
                "sources": ", ".join(sorted(set(tags(b, "from_source")))),
                "evidence": " || ".join(evid),
                "limitations": " || ".join(tags(b, "limitation")),
                "file": f,
            })
            picks[date][run].add(f"{agent}:{code}")
        if rank == 0:
            rows.append({"run": run, "date": date, "agent": agent, "belief": belief, "rank": 0,
                         "symbol_code": "", "symbol_name": "(시그널 없음)", "action": "",
                         "probability": "", "n_evidence": 0, "sources": "", "evidence": "",
                         "limitations": "", "file": f})

os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
cols = ["run", "date", "agent", "belief", "rank", "symbol_code", "symbol_name", "action",
        "probability", "n_evidence", "sources", "evidence", "limitations", "file"]
with open(a.out, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    w.writerows(sorted(rows, key=lambda r: (r["date"], r["agent"], r["run"], r["rank"])))

# 날짜별 실행 쌍 겹침
ov_rows = []
names = [r for r, _ in runs]
for date in sorted(picks):
    for r1, r2 in itertools.combinations(names, 2):
        if r1 not in picks[date] or r2 not in picks[date]:
            continue
        both = present[date][r1] & present[date][r2]   # 두 실행 모두 파일이 있는 에이전트만 비교
        s1 = {x for x in picks[date][r1] if x.split(":")[0] in both}
        s2 = {x for x in picks[date][r2] if x.split(":")[0] in both}
        union = s1 | s2
        ov_rows.append({"date": date, "pair": f"{r1}-{r2}", "n_a": len(s1), "n_b": len(s2),
                        "common": len(s1 & s2),
                        "jaccard": round(len(s1 & s2) / len(union), 3) if union else "",
                        "only_a": " ".join(sorted(s1 - s2)), "only_b": " ".join(sorted(s2 - s1))})
with open(a.overlap_out, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["date", "pair", "n_a", "n_b", "common", "jaccard", "only_a", "only_b"])
    w.writeheader()
    w.writerows(ov_rows)

n_sig = sum(1 for r in rows if r["rank"] > 0)
print(f"실행 {names} | 파일 {len({r['file'] for r in rows})}개 | 시그널 {n_sig}건 → {a.out}")
by_pair = defaultdict(list)
for r in ov_rows:
    if r["jaccard"] != "":
        by_pair[r["pair"]].append(r["jaccard"])
for pair, js in by_pair.items():
    print(f"  {pair}: {len(js)}일 평균 Jaccard {sum(js)/len(js):.2f} (최저 {min(js):.2f}, 최고 {max(js):.2f})")
print(f"날짜별 겹침 → {a.overlap_out}")
