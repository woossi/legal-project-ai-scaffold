---
name: legal-tablecsv
description: 이 스킬은 원본 바이너리에서 표 구조를 뽑는다. legal-table은 md에서 값을 뽑는다. Use when extracting table structure, captions, headings, and cells from original HWP or PDF binaries into first-layer CSV preservation outputs
---

# legal-tablecsv

원본 HWP·PDF의 표·빈 셀·캡션·상위표제를 1층에 보존하고, 2층의 `norm_건축계획지표.csv`
계약을 검증한다. **예시도 표 후보의 규범성 판단은 2층만 소유한다.**

경계 — md 기반 건폐율·용적률 값 추출은 `legal-table` 이 정본이다. CSV 수용 뒤 KB 그래프
구성은 KB 소유 스킬이다. **1층에서 예시도·규범 표를 분류하거나 누락 캡션을 복원하지 않는다.**

## 구성

| 자산 | 정본으로 담는 것 |
|---|---|
| `scripts/normalize_indicators.py` | 2층 생성기. 판정을 갖지 않고 계약을 컴파일해 집행 |
| `scripts/verify_contract.py` | 계약 검증. 정상 0 · 위반 1 · 검증불가 입력 2 |
| `scripts/extract_tables.py` | 1층 표·셀 추출 |
| `scripts/hwp5html_recover.py` | HWP 셀 회수 |
| `contract/outputs.json` | 산출물·값 도메인·`판정파라미터.2층생성규칙`·`게이트6_면제` |
| `contract/norm_건축계획지표.schema.json` | 2층 구조 계약 |
| `case/norm_건축계획지표.json` | 비규범 캡션 판정 사례. **verifier 에 하드코딩하지 않는다** |
| `case/original-table-samples.json` | 원표 실측 표본 |
| `references/original-table-existence.md` | 원본 표 실재 근거 |
| `references/common-mistakes.md` | 산출물에 실제로 들어왔던 오판정 |
| 설계 정본 | `docs/adr/0003-표-csv-2층-구조.md` |

입력 모수 — 수집 원장 190개 지구(직접 HWP 90, 직접 PDF 59, archive 41), 원본문서 접근
가능 **189개 지구**(HWP 124, PDF 71, 양쪽 6). 광교 `EGG_미해제` 만 EGG 도구가 준비될
때까지 제외한다. 1층 산출은 `output/legal/table/tables.csv`·`cells.csv` 다.

## 실행

```bash
.venv/bin/python .claude/skills/legal/legal-tablecsv/scripts/normalize_indicators.py
.venv/bin/python .claude/skills/legal/legal-tablecsv/scripts/verify_contract.py
.venv/bin/python -m pytest tools/tests/test_legal_tablecsv_verify_contract.py -q
.venv/bin/python -m pytest tools/tests/test_legal_tablecsv_normalize.py -q
```

repository root 에서 순서대로 실행한다.

추출 판정 기준은 `references/original-table-existence.md` 가 정본이다.

### 2층 생성

`normalize_indicators.py` 는 판정을 갖지 않는다. 판정의 정본은 계약의
`판정파라미터.2층생성규칙` 이고 스크립트는 그것을 컴파일해 집행한다. **규칙을 고칠 때는
계약을 먼저 고친다.**

표는 세 방식으로 읽는다. 순서가 곧 우선순위이며 한 표에 한 방식만 쓴다.

| 순서 | 방식 | 조건 |
|---|---|---|
| 1 | 열표제 | 지표명이 열 표제에 있고 주체 열이 있다 |
| 2 | 전치 | 1이 행을 하나도 못 냈다. 지표명이 셀 라벨이고 값이 오른쪽에 있다 |
| 3 | 열표제_주체미표기 | 1·2가 모두 실패했고 지표 열은 있으나 주체 표기가 없다. 주체는 `미표기` |

셋 다 실패하면 격리하고 `_norm_indicator_report.json` 에 사유와 함께 남긴다. 게이트 6은
계약의 `게이트6_면제` 에 걸리는 표만 면제하며, 면제 두 갈래(`단일행표`·`수치셀없음`)는
verifier 가 `tables.csv`·`cells.csv` 에서 **독립 재계산한다.**

선언된 `norm_건축계획지표.csv` 필드만 정규화하고 `값_원문`과 `값_수치`, 괄호 조건의
`단서` 를 분리 보존한다.

### 검증

정확한 CSV 열과 선언된 값 도메인을 요구한다. `표ID·지구번호·추출경로·품질등급` 을
`tables.csv` 와 다시 대조하고, 각 `값_원문` 을 같은 표의 `cells.csv` 값과 다시 대조한다.
수치/파싱사유 배타성, 지표/단위 매핑, 조건 단서 보존을 검사한다. **case regex 는
`캡션원문` 에만 적용한다** — 헤더·셀 키워드는 캡션 근거가 아니다.

생성기의 판정과 2회 생성 바이트 동일성은 별도 테스트가 검사한다. **verifier 는 산출물만
보므로 멱등성을 통과로 보고하지 않는다.**

산출물에 들어왔던 오판정과 그 규약은 `references/common-mistakes.md` 가 정본이다.

## output path

```
output/legal/table/
  tables.csv                    표 1개당 1행. 예시도 후보와 캡션 근거 포함
  cells.csv                     빈 셀을 포함한 원본 셀 1개당 1행
  norm_건축계획지표.csv          2층 정규화 값
  _norm_indicator_report.json   2층 격리표·읽기모드·분포
```

보고서와 기타 파생물도 `output/legal/table/` 아래에 둔다.
