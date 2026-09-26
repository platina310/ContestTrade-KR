"""
D6 — Factiva 뉴스 데이터 소스 (역할 B).

원본: data_collection/data/factiva/factiva_news.sqlite (factiva_ingest.py 산출)
종목 매핑 2단계: ① Factiva CO 코드 ↔ universe.csv의 factiva_co_code (정확 매칭)
              ② CO 매칭 실패 시 제목·본문의 종목명 문자열 매칭 (kr_telegram_research와 동일 규칙)

시각 규율: PD는 날짜만 제공(장중/장후 구분 불가) → **D+1 규칙(D45)**:
발행일 D의 기사는 D+1 00:00부터 사용 가능. DART 공시(kr_dart_disclosure)와 동일 원칙.

본문 절단: 팀 규칙 5 — 제목 + 본문 앞 300자 (한국어 토큰 비용 통제).

Web News(factiva_webnews, 2026.9.24 추가): 본문 없이 제목+요약만 있는 항목.
  - 연합인포맥스(source_code='WEBLINK'): 표시 시각이 KST(16시대 장마감 기사 집중, 08:15 뉴욕마켓워치 등)
    → **분 단위 규칙**: usable_from = pub_dt_kst + 1분.
    단, 시각이 00:00이거나 문서 ID 날짜(WC659120 뒤 8자리)가 표시일보다 늦으면 표시일을 신뢰하지 않고
    ID 날짜 D+1 00:00부터 사용 (표시 6/24인데 ID 7/24인 항목 등 미래 정보 유입 방지).
  - 그 밖의 Web News(한경·매경 요약형): PD 날짜만 있으므로 기사와 같은 D+1 규칙.
"""
import sqlite3
from datetime import timedelta
from pathlib import Path

import pandas as pd

from data_source.kr_data_source_base import KRDataSourceBase
from data_source.kr_telegram_research import tag_stock_codes

DB_PATH = Path(__file__).parents[2] / "data_collection" / "data" / "factiva" / "factiva_news.sqlite"

BODY_CHARS = 300  # 팀 규칙 5
LOOKBACK_DAYS = 3
INCLUDE_WEBNEWS = True   # False로 두면 기존(본문 기사만) 동작과 동일
WEBLINK_DELAY = timedelta(minutes=1)


def _webnews_usable_from(an: str, pub_date: str, pub_time, pub_dt_kst, source_code: str) -> pd.Timestamp:
    """Web News 한 건의 사용 가능 시각(KST). 모듈 docstring의 규칙."""
    d1 = pd.to_datetime(pub_date) + timedelta(days=1)
    if source_code != "WEBLINK":
        return d1
    id_date = None
    if an.startswith("WC") and len(an) >= 16 and an[8:16].isdigit():
        id_date = pd.to_datetime(an[8:16], format="%Y%m%d", errors="coerce")
    shown = pd.to_datetime(pub_dt_kst, errors="coerce") if pub_dt_kst else pd.NaT
    unreliable = (pd.isna(shown) or pub_time in (None, "", "00:00")
                  or (id_date is not None and not pd.isna(id_date) and id_date > pd.to_datetime(pub_date) + timedelta(days=1)))
    if unreliable:
        base = id_date if id_date is not None and not pd.isna(id_date) and id_date > pd.to_datetime(pub_date) else pd.to_datetime(pub_date)
        return base + timedelta(days=1)
    return shown + WEBLINK_DELAY


class KrFactivaNews(KRDataSourceBase):
    def __init__(self, universe_names: dict | None = None, factiva_code_map: dict | None = None, cache_dir=None):
        """universe_names/factiva_code_map이 None이면 유니버스 CSV에서 자동 로드 (파이프라인 무인자 경로)."""
        super().__init__("kr_factiva_news", cache_dir=cache_dir)
        if universe_names is None or factiva_code_map is None:
            from utils.kr_universe import load_universe, load_factiva_code_map
            universe_names = universe_names or load_universe()
            factiva_code_map = factiva_code_map or load_factiva_code_map()
        self.universe_names = universe_names
        self.factiva_code_map = {k.lower(): v for k, v in factiva_code_map.items()}

    def _map_stocks(self, co_codes: str, text: str) -> list:
        # ① CO 코드 정확 매칭
        found = []
        for fc in (co_codes or "").split(","):
            code = self.factiva_code_map.get(fc.strip().lower())
            if code and code in self.universe_names and code not in found:
                found.append(code)
        # ② 폴백: 종목명 문자열 매칭 (factiva 코드 없는 종목 커버)
        if not found:
            found = tag_stock_codes(text, self.universe_names)
        return found

    def fetch_raw(self, trigger_time: str) -> pd.DataFrame:
        if not DB_PATH.exists():
            raise RuntimeError(
                f"Factiva DB가 없습니다: {DB_PATH}\n"
                "data_collection/factiva_ingest.py 를 먼저 실행하세요."
            )
        trigger_dt = pd.to_datetime(trigger_time)
        # D+1 규칙: trigger 당일 사용 가능한 최신 기사는 어제 발행분
        end_pd = (trigger_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        start_pd = (trigger_dt - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

        conn = sqlite3.connect(DB_PATH)
        try:
            raw = pd.read_sql_query(
                "SELECT an, pd_date, source, headline, body, co_codes FROM articles "
                "WHERE pd_date >= ? AND pd_date <= ?",
                conn, params=[start_pd, end_pd],
            )
            web = pd.DataFrame()
            if INCLUDE_WEBNEWS and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='factiva_webnews'").fetchone():
                # 분 단위·ID 날짜 보정 때문에 넉넉히 읽고 usable_from으로 거른다
                web = pd.read_sql_query(
                    "SELECT an, pub_date, pub_time, pub_dt_kst, source, source_code, headline, summary, co_codes "
                    "FROM factiva_webnews WHERE pub_date >= ? AND pub_date <= ?",
                    conn, params=[start_pd, trigger_dt.strftime("%Y-%m-%d")],
                )
        finally:
            conn.close()

        rows = []
        for _, r in raw.iterrows():
            codes = self._map_stocks(r["co_codes"], f"{r['headline']} {r['body'][:500]}")
            if not codes:
                continue
            usable_from = (pd.to_datetime(r["pd_date"]) + timedelta(days=1)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            names = ", ".join(self.universe_names[c] for c in codes)
            rows.append({
                "title": f"[{r['source']}] ({names}) {r['headline']}",
                "content": r["body"][:BODY_CHARS],
                "pub_time": usable_from,
                "url": f"factiva://{r['an']}",
            })

        window_start = trigger_dt - timedelta(days=LOOKBACK_DAYS)
        # 인포맥스는 같은 기사가 '05.' 번호나 'YYYY-MM-DD' 접두어만 바뀐 채 여러 번 실림 → 정규화 제목 기준 가장 이른 1건만
        if len(web):
            web["_key"] = web["headline"].fillna("").str.replace(
                r"^\s*(?:\d{1,2}\.\s*|\d{4}-\d{2}-\d{2}\s*)+", "", regex=True).str.replace(r"\W+", "", regex=True)
            web["_usable"] = [
                _webnews_usable_from(r.an, r.pub_date, r.pub_time, r.pub_dt_kst, r.source_code) for r in web.itertuples()]
            web = web.sort_values("_usable").drop_duplicates("_key", keep="first")
        for _, r in web.iterrows():
            usable = r["_usable"]
            if not (window_start <= usable < trigger_dt):   # 부모 as-of 필터와 같은 엄격 미만
                continue
            summary = r["summary"] or ""
            codes = self._map_stocks(r["co_codes"], f"{r['headline']} {summary[:500]}")
            if not codes:
                continue
            names = ", ".join(self.universe_names[c] for c in codes)
            rows.append({
                "title": f"[{r['source']}] ({names}) {r['headline']}",
                "content": summary[:BODY_CHARS],
                "pub_time": usable.strftime("%Y-%m-%d %H:%M:%S"),
                "url": f"factiva://{r['an']}",
            })
        return pd.DataFrame(rows, columns=["title", "content", "pub_time", "url"])


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from utils.kr_universe import load_universe, load_factiva_code_map

    src = KrFactivaNews(load_universe(), load_factiva_code_map())
    df = src.get_data_sync("2026-05-29 09:00:00")
    print(df.head(8).to_string(max_colwidth=60))
    print(f"\n{len(df)}건 / pub_time: {df['pub_time'].min()} ~ {df['pub_time'].max()}" if len(df) else "0건")
