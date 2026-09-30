"""
파일럿 누적 수익률 그래프 — 발표 슬라이드용

입력  data/pilot_eval/contest_on_daily.csv, contest_off_daily.csv   (pilot_eval.py 산출)
      data/benchmarks/benchmarks_open.csv                           (build_benchmarks.py 산출)
출력  data/pilot_eval/cumret_june.png

모두 시가 기준(D 시가 → D+1 시가)이라 같은 시점끼리 비교됨.
전략 선: 순수익(실선), 콘테스트 on 총수익(점선) — 둘의 간격이 비용.

사용법
  python scripts/plot_pilot.py
  python scripts/plot_pilot.py --no-ktop30 --out data/pilot_eval/cumret.png
"""
import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import PercentFormatter

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--eval-dir", default=str(ROOT / "data/pilot_eval"))
p.add_argument("--bench", default=str(ROOT / "data/benchmarks/benchmarks_open.csv"))
p.add_argument("--start", default="2026-06-01")
p.add_argument("--end", default="2026-06-30")
p.add_argument("--no-ktop30", action="store_true", help="공식 KTOP30 참고선 숨기기")
p.add_argument("--out", default=str(ROOT / "data/pilot_eval/cumret_june.png"))
a = p.parse_args()

# 한글 폰트 (맥 AppleGothic → 윈도 Malgun Gothic → 나눔)
for f in ("AppleGothic", "Malgun Gothic", "NanumGothic", "Noto Sans CJK KR"):
    if any(f == x.name for x in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = f
        break
plt.rcParams["axes.unicode_minus"] = False

bench = list(csv.DictReader(open(a.bench, encoding="utf-8")))
dates = [r["date"] for r in bench]
lv = {r["date"]: r for r in bench}
test = [d for d in dates if a.start <= d <= a.end]
if not test:
    raise SystemExit("벤치마크 파일에 테스트 구간 날짜가 없습니다")
axis = test + [dates[dates.index(test[-1]) + 1]] if dates.index(test[-1]) + 1 < len(dates) else test


def strategy(name, col):
    """일별 수익률(D→D+1) → axis 날짜별 누적. axis[0]=0%"""
    rows = {r["date"]: float(r[col]) for r in csv.DictReader(open(Path(a.eval_dir) / f"{name}_daily.csv",
                                                                   encoding="utf-8"))}
    v, out = 1.0, [0.0]
    for d in axis[:-1]:
        v *= 1 + rows.get(d, 0.0)
        out.append(v - 1)
    return out


def index(col):
    base = float(lv[axis[0]][col])
    return [float(lv[d][col]) / base - 1 for d in axis]


series = [
    ("콘테스트 on (순수익)", strategy("contest_on", "net"), dict(color="#C0392B", lw=2.6)),
    ("콘테스트 on (총수익)", strategy("contest_on", "gross"), dict(color="#C0392B", lw=1.4, ls="--")),
    ("콘테스트 off (순수익)", strategy("contest_off", "net"), dict(color="#E67E22", lw=2.0)),
    ("시총가중 지수", index("CAP"), dict(color="#1F3A93", lw=2.2)),
    ("시총가중 상한25%", index("CAP_C"), dict(color="#5B7DB1", lw=1.6)),
    ("동일가중(월 리밸런싱)", index("EW_M"), dict(color="#7F8C8D", lw=1.6)),
]
if not a.no_ktop30:
    series.append(("KTOP30 공식(참고)", index("KTOP30"), dict(color="#BDC3C7", lw=1.2, ls=":")))

fig, ax = plt.subplots(figsize=(11, 6.4), dpi=200)
x = list(range(len(axis)))
for label, ys, style in series:
    ax.plot(x, ys, label=label, **style)
# 끝값 라벨 — 겹치지 않도록 세로 간격 확보
ends = sorted(((ys[-1], style["color"]) for _, ys, style in series), key=lambda t: t[0])
lo_y = min(min(ys) for _, ys, _ in series)
hi_y = max(max(ys) for _, ys, _ in series)
gap = (hi_y - lo_y) * 0.035
placed = []
for y, color in ends:
    yy = max(y, placed[-1] + gap) if placed else y
    placed.append(yy)
    ax.annotate(f"{y:+.1%}", (x[-1], y), xytext=(x[-1] + 0.4, yy), textcoords="data",
                va="center", fontsize=9, color=color,
                arrowprops=dict(arrowstyle="-", color=color, lw=0.5, alpha=0.6) if abs(yy - y) > gap * 0.3 else None)
ax.axhline(0, color="#333", lw=0.8)
step = max(1, len(axis) // 8)
ax.set_xticks(x[::step])
ax.set_xticklabels([d[5:].replace("-", "/") for d in axis[::step]])
ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
ax.set_xlim(0, x[-1] + 2)
ax.grid(alpha=0.25)
ax.set_title(f"KTOP30 구성 30종목 파일럿 — 누적 수익률 ({axis[0]} 시가 = 0%)", fontsize=13, loc="left")
ax.set_ylabel("누적 수익률 (시가 기준)")
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.07), fontsize=9, frameon=False, ncol=4)
fig.text(0.01, 0.005, "비용: 매도 거래세 0.20%·수수료 0.015%·슬리피지 0.10% (전략만 반영, 지수는 비용 없음). "
         "21거래일 개발 구간 — 파이프라인 검증용이며 통계적 결론의 근거 아님.",
         fontsize=7.5, color="#555")
fig.tight_layout(rect=(0, 0.02, 1, 1))
Path(a.out).parent.mkdir(parents=True, exist_ok=True)
fig.savefig(a.out, bbox_inches="tight")
print(f"저장: {a.out}")

# ---- 초과수익 그림: 콘테스트 on - 시총가중 / - 상한25% / on - off  (누적 차이, %p)
names = {lab: ys for lab, ys, _ in series}
on_g, on_n = names["콘테스트 on (총수익)"], names["콘테스트 on (순수익)"]
fig2, ax2 = plt.subplots(figsize=(11, 4.2), dpi=200)
for label, ys, style in (
        ("on 총수익 - 시총가중", [g - c for g, c in zip(on_g, names["시총가중 지수"])], dict(color="#1F3A93", lw=2.2)),
        ("on 총수익 - 시총가중 상한25%", [g - c for g, c in zip(on_g, names["시총가중 상한25%"])], dict(color="#5B7DB1", lw=1.6)),
        ("on 순수익 - off 순수익 (콘테스트 효과)", [a_ - b_ for a_, b_ in zip(on_n, names["콘테스트 off (순수익)"])],
         dict(color="#C0392B", lw=2.0)),
        ("on 총수익 - on 순수익 (누적 비용)", [g - n for g, n in zip(on_g, on_n)], dict(color="#7F8C8D", lw=1.4, ls="--"))):
    ax2.plot(x, ys, label=label, **style)
    ax2.annotate(f"{ys[-1] * 100:+.1f}%p", (x[-1], ys[-1]), xytext=(6, 0), textcoords="offset points",
                 va="center", fontsize=9, color=style["color"])
ax2.axhline(0, color="#333", lw=0.8)
ax2.set_xticks(x[::step])
ax2.set_xticklabels([d[5:].replace("-", "/") for d in axis[::step]])
ax2.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
ax2.set_xlim(0, x[-1] + 2)
ax2.grid(alpha=0.25)
ax2.set_title("누적 초과수익 분해 (%p) — 벤치마크 대비 · 콘테스트 효과 · 비용", fontsize=12, loc="left")
ax2.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), fontsize=9, frameon=False, ncol=2)
fig2.tight_layout()
out2 = str(Path(a.out).with_name(Path(a.out).stem + "_excess.png"))
fig2.savefig(out2, bbox_inches="tight")
print(f"저장: {out2}")
for label, ys, _ in series:
    print(f"  {label:<18}{ys[-1]:+.2%}")
