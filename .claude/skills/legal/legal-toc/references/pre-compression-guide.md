---
name: legal-toc
description: Use when judging 편/장/절 계층 구조 of 시행지침 markdown, counting 최상위 단위, separating 목차 pages from 본문 headings, or classifying 구조 유형
---

# 구성 (스킬 폴더 기준 상대 경로)

- `scripts/verify_contract.py` — 산출물 계약 검증. 종료코드 0=충족, 1=위반
- `contract/columns.json` — 전수조사 CSV 열·값 도메인·행 제약
- `common-mistakes.md` — 규칙이 정상 케이스를 죽인 실패 사례
- `case/판정규칙.md` — 최상위 단위 판정 순서, 목차/본문 분리, 편 수 확정, 평문 표기 인식
- `case/문서실태.md` — 구조 유형 10종, 지역·시행자 경향, 함정 문서
- 구현체: `tools/analysis/` — `parse_toc.py`(헤딩 분리), `analyze.py`(계층 판정),
  `patterns.py`, `coverage.py`, `verify.py`. 각 파일 상단에 입력·출력·전제가 있다

# 실행

```bash
python3 scripts/verify_contract.py   # 산출물 계약 검증
```

- 판정 규칙의 정본은 `case/판정규칙.md` 이다. 각 절은 실측 반례에서 나왔으므로, 규칙을
  단순화하기 전에 해당 반례를 먼저 확인한다
- `contract/columns.json` 의 enum 은 문서 189건에서 관측한 값이다. 새 값이 나오면 판정
  로직이 바뀐 것이므로 계약을 먼저 갱신한다

# output path

- `output/legal/analysis/시행지침_목차구조_전수조사.{md,csv}`
