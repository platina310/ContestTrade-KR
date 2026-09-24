"""
Factiva RTF → SQLite 적재기 (파일럿 D11 경제지 뉴스 소스)

전제: Factiva에서 Display Option "Full Article/Report plus Indexing" + Article format RTF로
      100건씩 저장한 파일. striprtf 변환 후 각 필드가 "HD|내용|" 형태의 표 행으로 나온다.

출력 DB (기본 data/factiva_news.sqlite, 문서ID(AN) PK라 재실행해도 중복 없음)
  factiva_news     본문 있는 기사 (한경 ECODKO / 매경 MAEIKO / 서울경제 HAKSOL 등)
  factiva_company  기사 × Factiva 기업코드(CO) — 종목 매핑 기준
  factiva_webnews  본문 없는 Web News (연합인포맥스: 시각·요약만)
  articles (VIEW)  PR #1 어댑터(kr_factiva_news.py) 호환용 — 같은 DB를 그대로 읽을 수 있음

look-ahead: Factiva의 PD+ET는 실제 KST보다 9시간 늦게 찍혀 있다(ET_SHIFT_HOURS).
            검증 2026-09-24: 서울경제 "李대통령 삼성·SK 용인·서남권 팹…" 사이트 수정 2026-06-30 18:46 KST
            → Factiva PD 2026-07-01 / ET 03:46 (정확히 +9h, 날짜도 넘어감).
            - ET 있는 기사(서울경제 등): pub_dt_kst = PD+ET-9h → 어댑터는 "pub_dt_kst < D 08:30" 규칙
            - ET 없는 기사(한경·매경): PD만 → D+1 규칙. PD가 늦게 찍히는 쪽이라 누수 없이 보수적
            원본 pub_date·pub_time은 그대로 보존한다.

사용법
  python scripts/factiva_rtf_to_sqlite.py data/factiva/2026-05 [data/factiva/2026-06 ...] [--db PATH]
  python scripts/factiva_rtf_to_sqlite.py stats [--db PATH]
  python scripts/factiva_rtf_to_sqlite.py inspect data/factiva/2026-05/Factiva_May2026_0001-0100.rtf
  python scripts/factiva_rtf_to_sqlite.py fill-universe [--universe data/universe.csv] [--write]
"""
import argparse
import csv
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from striprtf.striprtf import rtf_to_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "factiva_news.sqlite"
DEFAULT_UNIVERSE = ROOT / "data" / "universe.csv"

# Factiva 인덱싱 필드 코드 (본문 속 대문자 단어 오인 방지용 화이트리스트)
FIELD_CODES = {
    "SE", "HD", "BY", "CR", "WC", "PD", "ET", "SN", "SC", "ED", "PG", "LA", "CY",
    "LP", "TD", "CT", "RF", "CO", "IN", "NS", "RE", "IPC", "IPD", "PUB", "AN", "ART",
}
FIELD_ROW = re.compile(r"^\s*([A-Z]{2,3})\s*\|(.*)$")
CODE_NAME = re.compile(r"([a-z0-9]{3,})\s*:\s*([^|]+)")

SCHEMA = """
CREATE TABLE IF NOT EXISTS factiva_news (
    an          TEXT PRIMARY KEY,   -- Factiva 문서 ID
    pub_date    TEXT NOT NULL,      -- YYYY-MM-DD (PD)
    pub_time    TEXT,               -- HH:MM (ET, 있을 때만. 서울경제 새벽 지면등록 시각 등)
    source      TEXT,               -- SN 매체명
    source_code TEXT,               -- SC (ECODKO/MAEIKO/HAKSOL ...)
    section     TEXT,               -- SE
    headline    TEXT,
    lead        TEXT,               -- LP
    body        TEXT,               -- TD
    word_count  INTEGER,            -- WC
    pub_dt_kst  TEXT,               -- 'YYYY-MM-DD HH:MM' 실제 KST (ET 있을 때만, PD+ET-9h)
    language    TEXT,               -- LA
    co_codes    TEXT,               -- 'sansel,hyunmo' 소문자 콤마 구분
    ns_codes    TEXT,               -- 뉴스유형 코드 'c151,ncat'
    in_codes    TEXT,               -- 산업 코드
    re_codes    TEXT,               -- 지역 코드
    src_file    TEXT,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_date ON factiva_news (pub_date);

CREATE TABLE IF NOT EXISTS factiva_company (
    an       TEXT NOT NULL,
    co_code  TEXT NOT NULL,
    co_name  TEXT,
    kind     TEXT NOT NULL,         -- 'news' | 'web'
    PRIMARY KEY (an, co_code)
);
CREATE INDEX IF NOT EXISTS idx_company_code ON factiva_company (co_code);

CREATE TABLE IF NOT EXISTS factiva_webnews (
    an          TEXT PRIMARY KEY,
    pub_date    TEXT NOT NULL,
    pub_time    TEXT,
    source      TEXT,
    source_code TEXT,
    headline    TEXT,
    summary     TEXT,               -- LP (Web News는 본문 없이 요약만)
    pub_dt_kst  TEXT,               -- PD+ET-9h (Web News에도 같은 보정 적용 — 별도 검증 전)
    co_codes    TEXT,
    ns_codes    TEXT,
    src_file    TEXT,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_date ON factiva_webnews (pub_date);

CREATE VIEW IF NOT EXISTS articles AS
    SELECT an, pub_date AS pd_date, source, headline,
           TRIM(COALESCE(lead, '') || char(10) || COALESCE(body, '')) AS body,
           co_codes, src_file, ingested_at
    FROM factiva_news;
"""

ET_SHIFT_HOURS = 9   # Factiva PD+ET − 실제 KST (위 docstring 검증 참고)
NEWS_COLS = ["an", "pub_date", "pub_time", "source", "source_code", "section", "headline", "lead",
             "body", "word_count", "pub_dt_kst", "language", "co_codes", "ns_codes", "in_codes",
             "re_codes", "src_file", "ingested_at"]
WEB_COLS = ["an", "pub_date", "pub_time", "source", "source_code", "headline", "summary",
            "pub_dt_kst", "co_codes", "ns_codes", "src_file", "ingested_at"]


# ---------------------------------------------------------------- 파싱
def read_rtf(path: Path) -> str:
    raw = path.read_bytes().decode("latin-1")          # RTF 자체는 ASCII, 한글은 \u 또는 \'hh 이스케이프
    m = re.search(r"\\ansicpg(\d+)", raw[:2000])
    enc = f"cp{m.group(1)}" if m else "cp1252"
    try:
        return rtf_to_text(raw, encoding=enc, errors="replace")
    except LookupError:
        return rtf_to_text(raw, errors="replace")


def iter_fields(text: str):
    """(코드, 내용) 순서대로. 셀 안 줄바꿈은 직전 필드에 이어 붙인다."""
    code, buf = None, []
    for line in text.splitlines():
        m = FIELD_ROW.match(line)
        if m and m.group(1) in FIELD_CODES:
            if code:
                yield code, _clean("\n".join(buf))
            code, buf = m.group(1), [m.group(2)]
        elif code:
            buf.append(line)
    if code:
        yield code, _clean("\n".join(buf))


def _clean(s: str) -> str:
    s = re.sub(r"\|\s*$", "", s.strip())               # 행 끝 셀 구분자
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def parse_date(s: str):
    s = s.strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def parse_time(s: str):
    if not s:
        return None
    s = s.strip().upper().replace("오전", "AM").replace("오후", "PM")
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", s)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if ap == "PM" and h < 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


def to_kst(pub_date, pub_time):
    if not pub_time:
        return None
    dt = datetime.strptime(f"{pub_date} {pub_time}", "%Y-%m-%d %H:%M") - timedelta(hours=ET_SHIFT_HOURS)
    return dt.strftime("%Y-%m-%d %H:%M")


def codes(field: str):
    return [(c.strip(), n.strip()) for c, n in CODE_NAME.findall(field or "")]


def parse_file(path: Path):
    """기사 dict 리스트와 스킵 건수. AN이 기사 종결자."""
    arts, skipped, cur = [], 0, {}
    for code, content in iter_fields(read_rtf(path)):
        cur[code] = (cur[code] + "\n" + content) if code in cur else content
        if code != "AN":
            continue
        an = re.sub(r"^Document\s+", "", cur.get("AN", "")).strip()
        pub_date = parse_date(cur.get("PD", ""))
        if not an or not pub_date:
            skipped += 1
            cur = {}
            continue
        co = codes(cur.get("CO"))
        wc = re.search(r"\d[\d,]*", cur.get("WC", ""))
        arts.append({
            "an": an,
            "pub_date": pub_date,
            "pub_time": parse_time(cur.get("ET", "")),
            "pub_dt_kst": to_kst(pub_date, parse_time(cur.get("ET", ""))),
            "source": cur.get("SN", ""),
            "source_code": cur.get("SC", "").strip(),
            "section": cur.get("SE", ""),
            "headline": cur.get("HD", ""),
            "lead": cur.get("LP", ""),
            "body": cur.get("TD", ""),
            "word_count": int(wc.group().replace(",", "")) if wc else None,
            "language": cur.get("LA", ""),
            "co": co,
            "co_codes": ",".join(c for c, _ in co),
            "ns_codes": ",".join(c for c, _ in codes(cur.get("NS"))),
            "in_codes": ",".join(c for c, _ in codes(cur.get("IN"))),
            "re_codes": ",".join(c for c, _ in codes(cur.get("RE"))),
            "src_file": path.name,
        })
        cur = {}
    return arts, skipped


# ---------------------------------------------------------------- 적재
def connect(db: Path):
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    # 기존 DB 마이그레이션: pub_dt_kst 컬럼 추가 + 채우기 (재적재 불필요)
    for tbl in ("factiva_news", "factiva_webnews"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")}
        if "pub_dt_kst" not in cols:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN pub_dt_kst TEXT")
        conn.execute(
            f"UPDATE {tbl} SET pub_dt_kst = strftime('%Y-%m-%d %H:%M', pub_date || ' ' || pub_time, "
            f"'-{ET_SHIFT_HOURS} hours') WHERE pub_time IS NOT NULL AND pub_dt_kst IS NULL")
    conn.commit()
    return conn


def ingest(folders, db: Path):
    conn = connect(db)
    now = datetime.now(timezone.utc).isoformat()
    n_files = n_news = n_web = n_skip = 0
    for folder in folders:
        folder = Path(folder)
        files = sorted(folder.rglob("*.rtf")) if folder.is_dir() else [folder]
        if not files:
            print(f"[경고] RTF 없음: {folder}")
        for f in files:
            arts, skipped = parse_file(f)
            n_files += 1
            n_skip += skipped
            for a in arts:
                is_web = not a["body"]            # Web News(인포맥스)는 TD 본문이 없음
                if is_web:
                    row = dict(a, summary=a["lead"], ingested_at=now)
                    conn.execute(
                        f"INSERT OR IGNORE INTO factiva_webnews ({','.join(WEB_COLS)}) "
                        f"VALUES ({','.join('?' * len(WEB_COLS))})", [row[c] for c in WEB_COLS])
                    n_web += 1
                else:
                    row = dict(a, ingested_at=now)
                    conn.execute(
                        f"INSERT OR IGNORE INTO factiva_news ({','.join(NEWS_COLS)}) "
                        f"VALUES ({','.join('?' * len(NEWS_COLS))})", [row[c] for c in NEWS_COLS])
                    n_news += 1
                conn.executemany(
                    "INSERT OR IGNORE INTO factiva_company VALUES (?,?,?,?)",
                    [(a["an"], c, nm, "web" if is_web else "news") for c, nm in a["co"]])
            print(f"  {f.name}: {len(arts)}건" + (f" (스킵 {skipped})" if skipped else ""))
    conn.commit()
    print(f"\n파일 {n_files}개 파싱 → 기사 {n_news}건 / Web News {n_web}건 / 스킵 {n_skip}건 "
          f"(중복 AN은 무시되므로 DB 증가분은 이보다 작을 수 있음)")
    print_stats(conn)
    conn.close()


def print_stats(conn):
    print("\n[DB 현황]")
    for tbl in ("factiva_news", "factiva_webnews"):
        n, lo, hi = conn.execute(f"SELECT COUNT(*), MIN(pub_date), MAX(pub_date) FROM {tbl}").fetchone()
        print(f"  {tbl}: {n}건 | {lo} ~ {hi}")
    tagged = conn.execute("SELECT COUNT(*) FROM factiva_news WHERE co_codes != ''").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM factiva_news").fetchone()[0]
    timed = conn.execute("SELECT COUNT(*) FROM factiva_news WHERE pub_time IS NOT NULL").fetchone()[0]
    print(f"  CO 태깅 {tagged}/{total} ({tagged / max(total, 1):.0%}), 시각(ET) 있음 {timed}건 "
          f"(pub_dt_kst = PD+ET-{ET_SHIFT_HOURS}h)")
    print("  월×매체:")
    for ym, src, n in conn.execute(
            "SELECT substr(pub_date,1,7), COALESCE(NULLIF(source_code,''), source), COUNT(*) "
            "FROM factiva_news GROUP BY 1,2 ORDER BY 1,2"):
        print(f"    {ym}  {src:<20} {n}")


# ---------------------------------------------------------------- 점검·보완
def inspect(path: Path):
    """첫 기사의 필드 코드와 앞부분을 출력 — 파싱이 0건일 때 형식 확인용."""
    text = read_rtf(path)
    print(f"[변환 텍스트 앞 40줄]\n" + "\n".join(text.splitlines()[:40]))
    print("\n[인식된 필드 — 첫 기사]")
    for code, content in iter_fields(text):
        print(f"  {code}: {content[:80].replace(chr(10), ' ')}")
        if code == "AN":
            break
    arts, skipped = parse_file(path)
    print(f"\n이 파일: 기사 {len(arts)}건, 스킵 {skipped}건")


def _norm(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\b(co|corp|corporation|company|ltd|limited|inc|holdings?|group|the)\b", " ", name)
    return re.sub(r"[^a-z0-9가-힣]", "", name)


def fill_universe(db: Path, universe: Path, write: bool):
    """universe.csv의 빈 factiva_co_code를 DB의 CO 이름과 name_en/name_kr 일치로 채운다."""
    conn = sqlite3.connect(db)
    names = {}
    for code, nm, n in conn.execute(
            "SELECT co_code, co_name, COUNT(*) FROM factiva_company GROUP BY co_code, co_name"):
        names.setdefault(_norm(nm or ""), []).append((code, n))
    conn.close()

    with universe.open(encoding="utf-8-sig", newline="") as fh:   # csv 모듈: 종목코드 앞자리 0 보존
        reader = csv.DictReader(fh)
        fields, rows = reader.fieldnames, list(reader)

    filled, unmatched = 0, []
    for r in rows:
        if r.get("factiva_co_code", "").strip():
            continue
        cands = names.get(_norm(r.get("name_en", ""))) or names.get(_norm(r.get("name_kr", ""))) or []
        codes_ = sorted({c for c, _ in cands})
        if len(codes_) == 1:
            r["factiva_co_code"] = codes_[0]
            filled += 1
            print(f"  {r['ticker']} {r.get('name_kr', '')}: {codes_[0]}")
        else:
            unmatched.append((r["ticker"], r.get("name_kr", ""), r.get("name_en", ""), codes_))

    print(f"\n채움 {filled}개, 미매칭 {len(unmatched)}개")
    for t, kr, en, c in unmatched:
        print(f"  {t} {kr} ({en}) 후보: {c or '없음'}")
    if unmatched:
        print("  → 미매칭은 아래로 후보 확인 후 수동 입력:\n"
              "    sqlite3 data/factiva_news.sqlite \"SELECT co_code, co_name, COUNT(*) FROM factiva_company "
              "WHERE co_name LIKE '%키워드%' GROUP BY 1,2;\"")
    if write and filled:
        shutil.copy(universe, universe.with_suffix(".csv.bak"))
        with universe.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        print(f"\n저장 완료: {universe} (백업 {universe.with_suffix('.csv.bak').name})")
    elif filled:
        print("\n미리보기만 했습니다. 반영하려면 --write")


# ---------------------------------------------------------------- CLI
def main():
    argv = sys.argv[1:]
    mode = argv[0] if argv and argv[0] in {"stats", "inspect", "fill-universe"} else "ingest"
    p = argparse.ArgumentParser(description="Factiva RTF → SQLite")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    if mode == "ingest":
        p.add_argument("folders", nargs="+", help="RTF 폴더(하위 폴더 포함) 또는 파일")
        a = p.parse_args(argv)
        ingest(a.folders, a.db)
    elif mode == "stats":
        a = p.parse_args(argv[1:])
        conn = connect(a.db)
        print_stats(conn)
        conn.close()
    elif mode == "inspect":
        p.add_argument("file", type=Path)
        a = p.parse_args(argv[1:])
        inspect(a.file)
    else:
        p.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
        p.add_argument("--write", action="store_true")
        a = p.parse_args(argv[1:])
        fill_universe(a.db, a.universe, a.write)


if __name__ == "__main__":
    main()
