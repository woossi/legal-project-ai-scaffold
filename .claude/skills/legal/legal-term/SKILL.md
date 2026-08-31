---
name: legal-term
description: Use when extracting 용어 정의 from 시행지침 markdown into 용어집 (terms.json·definiation.json·doc_definitions.json 외 7종), or when judging 정의 조항 귀속·정의문 충돌·인용법령·ubiquity 등급
---

# legal-term

## 구성

- `scripts/verify_contract.py` — 계약 검증. `--full` 은 정의문 원문 대조까지 수행한다
- `scripts/build_definitions.py` — `terms.json` 에서 정의 조항을 수집해 `definiation.json` 을 만든다
- `scripts/canonicalize.py` — `definiation.json` 을 1용어 1정의 구조로 재구성한다
- `scripts/verify_definitions.py` — `definiation.json` 계약 검증. `--full` 은 원문 대조까지 수행한다
- `scripts/build_doc_definitions.py` — `terms.json` 에서 문서 단위 정의 레코드(`doc_definitions.json`)를 만든다. 정의는 표제어 도입부와 정의 술어를 절단해 요약한다
- `scripts/verify_doc_definitions.py` — `doc_definitions.json` 계약 검증. `--full` 은 원문 대조(정의조항 절삭 이전 md 포함)까지 수행한다
- `contract/outputs.json` — `output/legal/word/` 산출물 목록·교차 제약·검증 루틴
- `contract/terms.schema.json` — `terms.json` 구조 (JSON Schema)
- `contract/definitions.schema.json` — `definiation.json` 구조 (JSON Schema)
- `contract/doc_definitions.schema.json` — `doc_definitions.json` 구조 (JSON Schema)
- `case/정의문패턴.md` — 매칭 모드, 정의부/규정부 분리, 미도입 레이아웃
- `case/정의조항판정.md` — 조 표제 형태, 인정 표제 4종, 배제 규칙
- `case/정의통합.md` — 수치·요건 서명, 갈래 판정, 대표 정의문 선정
- `case/모수규약.md` — ubiquity 분모, `definiation.json` 재계산 필드, cited_laws, OCR 취급
- `references/common-mistakes.md` — 각 규칙을 제거했을 때 실제로 유입된 것. 인용부호·법령명 파싱·수치 판정

## 실행

입력은 `output/legal/markdown/` 의 지구별 병합 md 이다. 산출물을 갱신한 뒤에는 매번 검증한다.

```bash
python3 scripts/verify_contract.py --full        # terms.json 외 7종
python3 scripts/build_definitions.py             # definiation.json 수집
python3 scripts/canonicalize.py                  # 1용어 1정의로 통합
python3 scripts/verify_definitions.py --full     # definiation.json 계약
python3 scripts/build_doc_definitions.py         # doc_definitions.json 수집
python3 scripts/verify_doc_definitions.py --full # doc_definitions.json 계약
```

- `--full` 은 스키마, 교차 제약, 정의문 원문 대조를 한 번에 검사한다. 검증이 모두
  통과해야 갱신이 완료된다
- `build_definitions.py` 를 실행한 뒤 `canonicalize.py` 를 실행한다. 앞의 것만 실행하면
  통합 전 구조(`variants`)가 남아 계약을 통과하지 못한다
- 세 빌드 스크립트는 `terms.json` 만 읽는다. `terms.json` 을 갱신한 경우 셋을 다시 실행한다
- `definiation.json` 은 용어 축(1용어 1정의), `doc_definitions.json` 은 문서 축이다 —
  레코드마다 원본 문서(`source_file`)를 담고, 용어의 class 는 '법률 용어' 로 고정한다
  (사용자 규약 2026-08-11). 정의 요약은 정의부 원문의 연속 부분문자열이며 재작성하지 않는다
- 판정 규칙의 정본은 `case/` 에 있다. 각 규칙은 실측 반례에서 나왔으므로, 규칙을 단순화하기
  전에 `references/common-mistakes.md` 의 해당 반례를 먼저 확인한다
- 자동 검증이 불가능한 항목은 무작위 30건 육안 검수 하나이다. 지역별 10건을 원문에서 다시
  파싱해 정의문, 표제어, 조 표제 귀속을 확인한다

## output path

- `output/legal/word/{terms,core_terms,taxonomy,concept_relations}.json`
- `output/legal/word/definiation.json` — 정의 조항 용어집. 파일명 오탈자는 확정 경로라 유지한다
- `output/legal/word/doc_definitions.json` — 문서 단위 정의 레코드. 용어 class 는 '법률 용어' 고정
- `output/legal/word/_{extraction_report,low_confidence_review,ocr_only_terms}.json`

산출물 목록과 교차 제약의 정본은 `contract/outputs.json` 이다.
