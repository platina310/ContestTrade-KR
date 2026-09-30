## KR 시장 적용 (DFMBA 졸업 프로젝트, feat/SJ)

한국 시장(KTOP30 파일럿) 이식 작업 브랜치입니다. 원본 프레임워크 사용법은 위 절을 따르고, 아래는 KR 전용 추가 사항입니다.

### 환경

- Python 3.10, conda 환경 `contesttrade` (`conda create -n contesttrade python=3.10`)
- 프레임워크 의존성: `pip install -r requirements.txt`
- 수집 스크립트 의존성 고정본: `requirements-lock.txt` (`pip install -r requirements-lock.txt`). 수집용 로컬 venv에서 `pip freeze`한 결과이며, 프레임워크 패키지(langchain 등)는 포함하지 않습니다.
- 설정 파일: `config_kr.yaml.example`을 `config_kr.yaml`로 복사해 값 채우기. `config_kr.yaml`은 gitignore 대상입니다.

### 필요 환경 변수

| 변수 | 용도 | 발급처 |
| :-- | :-- | :-- |
| `DART_API_KEY` | OpenDART 공시목록·고유번호 조회 (`make_universe.py`, `collect_dart.py`) | https://opendart.fss.or.kr |
| `TG_API_ID`, `TG_API_HASH` | 텔레그램 증권사 채널 수집 (`collect_telegram.py`) | https://my.telegram.org |
| LLM 키 (`OPENAI_API_KEY` 등) | `config_kr.yaml`의 `llm` / `llm_thinking` / `vlm` api_key | 사용하는 LLM 제공자 |
| `CONTESTTRADE_DATA_DIR` (선택) | `data/` 대신 쓸 데이터 폴더 | - |

`.env`, `*.session`, `*.sqlite`, `data/` 는 모두 gitignore 대상입니다. 실데이터·키·텔레그램 세션 파일은 절대 커밋하지 마세요.

### 실행 순서

1. 유니버스 생성: `python scripts/make_universe.py --date 20260430 --out data/universe.csv`
   (마스터 사본은 `config/universe_ktop30_20260430.csv`, 없으면 이 파일을 `data/universe.csv`로 복사)
2. 백테스트용 데이터 수집
   - 시세·수급: `python scripts/collect_market.py --start 20260401 --end 20260630`
   - DART 공시: `python scripts/collect_dart.py --start 20260401 --end 20260630`
   - 텔레그램 4채널: `python scripts/collect_telegram.py` (채널 목록은 `docs/telegram_channels.md`)
   - Factiva RTF → SQLite: `python scripts/factiva_rtf_to_sqlite.py data/factiva/`
   - (실시간 전용) 네이버 종목뉴스: `python scripts/naver_news_collector.py`
3. 어댑터 단독 테스트: 각 KR data_source 어댑터를 단독 실행해 D-1 컷오프가 지켜지는지 확인 (`docs/lookahead_rules.md`)
4. 전체 실행: `python -m cli.main run` 후 KR 시장 선택

### 주의: universe.csv 는 엑셀로 열어 저장하지 마세요

`ticker`(6자리)와 `dart_corp_code`(8자리)는 앞자리 0이 있는 문자열입니다. 엑셀로 열어 저장하면 `005930` → `5930` 으로 0이 사라져 pykrx·DART 조회가 모두 깨집니다. 수정은 텍스트 편집기로만 하고, 읽을 때는 `dtype=str` 또는 `csv.DictReader`를 쓰세요.

### 관련 문서

- `docs/data_sources.md` : 백테스트용 / 실시간 전용 데이터 소스 구분
- `docs/lookahead_rules.md` : 시그널 확정 시각(D 08:30)과 소스별 look-ahead 컷오프
- `docs/telegram_channels.md` : 텔레그램 채널 판정표(채용 4채널·탈락 채널·저작권 원칙)

## 🌟 我们的愿景与路线图 (Vision & Roadmap)

我们坚信AGI时代即将到来，我们希望能够借助开源社区的力量，探索AGI时代下量化交易的新范式。

该项目致力于研发更完善的基础设施和更丰富的Agent，探索AI在金融交易的能力边界，打造一个稳定、可信、且能规模化扩展的Agent交易框架。

### 项目路线图 (Roadmap)

**V1.1 (已完成): 框架稳定性增强 & 核心体验优化**

- [✓] 核心数据源模块解耦，实现多数据源的adaptor (`data-provider` refactor)
- [✓] 优化CLI日志与交互体验

**V2.0 (已完成): 市场与功能拓展**

- [✓] 接入 **美股** 市场数据
- [✓] 引入更丰富的因子与信号来源

**未来规划:**

- [ ] 支持港股及其他市场
- [ ] 可视化回测与分析界面
- [ ] 支持更多Agent的scale up

## Contributing (贡献指南)

ContestTrade 是一个由社区驱动的开源项目，我们欢迎任何形式的贡献！

如果您是开发者，可以参考我们的 **[贡献指南 (CONTRIBUTING.md)](CONTRIBUTING.md)** 成为ContestTrade的贡献者。

我们同样珍视非代码形式的贡献，包括：

* **提出功能建议或反馈Bug:** [前往 Issues 页面](https://github.com/FinStep-AI/ContestTrade/issues)
* **反馈您的测试效果:** 包括测试结果、使用体验等。

## Star History（社区关注）

<div align="center">
  <a href="https://star-history.com/#FinStep-AI/ContestTrade&Date">
    <img src="https://api.star-history.com/svg?repos=FinStep-AI/ContestTrade&type=Date" alt="Star History Chart" style="width: 80%;">
  </a>
</div>

## Disclaimer（风险声明）

**重要声明:** 本项目 `ContestTrade` 是一个开源的量化交易Agent框架研究项目，仅供学术研究和教育目的使用。项目中包含的示例、数据和分析结果不构成任何形式的投资建议。

**风险提示:**

* **市场风险:** 本项目不构成任何形式的投资、财务、法律或税务建议。所有输出，包括交易信号和分析，均为基于历史数据的AI模型推演结果，不应被视为任何买卖操作的依据。
* **数据准确性:** 框架使用的数据源可能存在延迟、不准确或不完整的情况。我们不对数据的可靠性做任何保证。
* **模型幻觉:** AI模型（包括大型语言模型）存在固有的局限性和“幻觉”风险。我们不保证框架生成信息的准确性、完整性或及时性。
* **责任自负:**  开发者不对任何因使用或无法使用本框架而导致的直接或间接损失承担任何责任。投资有风险，入市需谨慎。

**在将本框架用于任何实际交易决策之前，请务必充分了解相关风险。**

## Citation (引用)

如果您在您的研究中使用了ContestTrade，请引用我们的论文：

```bibtex
@misc{zhao2025contesttrade,
      title={ContestTrade: A Multi-Agent Trading System Based on Internal Contest Mechanism}, 
      author={Li Zhao and Rui Sun and Zuoyou Jiang and Bo Yang and Yuxiao Bai and Mengting Chen and Xinyang Wang and Jing Li and Zuo Bai},
      year={2025},
      eprint={2508.00554},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR}
}
```

## License (许可证)

本项目采用 [Apache 2.0 License](LICENSE) 许可证。
