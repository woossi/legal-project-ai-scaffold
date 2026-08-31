---
name: ppt-verify
description: Use when judging a built 발표 덱 — PPTX ZIP 무결성·슬라이드 수·제목 문체·본문 명사형 종결·fact_refs 해소·상류 해시·오버플로·미리보기를 검사하고 통과·실패·경고·미검사로 집계해 `03_qa` 를 낸다. 읽기 전용이며 상류를 고치지 않는다
---

# ppt-verify

## 구성

- `scripts/verify_deck.py` — PPTX 전용 검증기. 통과·실패·경고·미검사 4갈래로 집계한다
- `contract/qa.schema.json` — `03_qa.json` 의 구조 계약
- `contract/outputs.json` — 판정 조건·값 도메인·교차 제약
- `references/사각지대.md` — 이 검증기가 못 보는 것. 갱신 대상이다

## 왜 전용 검증기가 필요한가

공통 `contract-verify` 의 outputs 검사는 **파일 존재·topKeys·JSON Schema 대조** 셋만 본다.
PPTX 는 zip 바이너리라 그 셋으로는 내용이 보이지 않는다. 슬라이드가 0장이어도,
제목이 문장이어도, 수치가 근거 없이 박혀 있어도 전부 통과한다.

이 스킬은 그 구멍을 메운다. 반대로 **판정 규칙 자체의 구멍**은 이 스킬도 못 본다 —
그 몫은 `common/adversarial-review` 다. 둘 다 돌려야 완료다.

## 검사 항목

| 검사 | 내용 | 기본 판정 |
|---|---|---|
| `zip_integrity` | pptx 를 zip 으로 열어 손상 확인 | fail |
| `slide_count` | 실제 슬라이드 수가 `02_deck.json` 의 `slides[]` 길이와 일치 | fail |
| `slide_sequence` | 슬라이드 번호가 1부터 연속 증가하는지 | fail |
| `cover_role` | cover가 정확히 1개이며 첫 슬라이드인지 | fail |
| `title_style` | 제목에 서술형 종결 또는 명사화된 완결절(`함`·`됨`·`임` 등)이 없는지. 덱 제목과 슬라이드 제목 전건 | fail |
| `body_style` | 본문 항목의 명사형 종결 비율이 임계 이상인지 | warn |
| `bullet_hierarchy` | task의 각 1단계 판단 불릿 직후에 2단계 근거가 있는지 | fail |
| `fact_refs_present` | 숫자를 포함한 본문 항목에 `fact_refs` 가 있는지 | fail |
| `fact_refs_resolve` | `fact_refs` 의 각 id 가 `01_source.json` 에 실재하는지 | fail |
| `display_value_binding` | 본문·시각 캡션의 숫자 토큰과 claims[].display_value·fact.value가 일치하는지 | fail |
| `source_fact_integrity` | source·fact 내부 참조와 failed 원천 caveat를 확인 | fail |
| `fact_value_check` | `locator`를 사용해 facts[].value를 원천에서 재측정 | fail·unchecked |
| `denominator_pair` | 본문·시각 캡션의 비율 fact에 분모 수치 또는 함께 참조한 분모 fact가 있는지 | fail |
| `pptx_text_integrity` | 스펙의 제목·본문이 같은 번호의 PPTX 슬라이드에 수록됐는지 | fail |
| `artifact_identity` | source·deck·build의 deck_id와 전달 해시가 일치하는지 | fail |
| `upstream_hash` | `02_deck.json` 의 `source_sha256` 이 현재 `01_source.json` 해시와 일치 | fail |
| `spec_hash` | `_build_report.json` 의 `spec_sha256` 이 현재 `02_deck.json` 해시와 일치 | fail |
| `text_overflow` | 도형 크기 대비 줄 수 추정 | warn |
| `previews_present` | 고정 경로 `previews/sNN.png`가 유효한 PNG이며 슬라이드 수만큼 존재 | fail |
| `font_fallback` | `_build_report.json` 의 `font.fallback` 이 true 인지 | warn |
| `visual_check` | 렌더러가 육안확인을 기록했는지 | fail |
| `closing_on_limits` | `limitations[]` 가 있으면 `closing` 슬라이드가 있는지 | fail |

판정 등급은 `contract/outputs.json` 의 `값도메인` 에서 조정 가능한 **기본값**이다.
프로필이나 호출 시 지정으로 바꿀 수 있으며, 바꿨으면 `03_qa.json` 에 기록한다.

## 절대 규칙

### 1. 미검사를 통과로 세지 않는다

돌리지 못한 검사는 `unchecked` 로 따로 집계한다. 합계에 섞지 않는다.
`unchecked` 가 하나라도 있으면 `verdict` 를 `accept` 로 두지 않는다.

### 2. 읽기 전용이다

`01_source.json`·`02_deck.json`·`build/**` 를 **고치지 않는다.**
문제를 찾으면 소유 에이전트에게 반려한다. 검증자가 직접 고치면 그 결함이
어디서 왔는지 다음 실행에서 보이지 않는다.

| 결함 | 반려 대상 |
|---|---|
| 제목이 문장·수치에 근거 없음·본문 문체 | `ppt-writer` |
| fact 누락·분모 없음·해시 불일치 | `ppt-curator` |
| 빌드 손상·미리보기 없음·폰트 폴백 | `ppt-renderer` |

### 3. 사각지대를 적는다

검사를 다 통과해도 **못 본 것**이 있다. `blind_spots[]` 에 적는다.
비어 있는 `blind_spots` 는 그 자체로 의심 대상이다 — 한 번도 채워지지 않았다면
검증자가 자기 한계를 보고 있지 않다는 뜻이다.

### 4. 근거를 함께 남긴다

`fail` 은 **어느 슬라이드의 무엇인지**를 `slide_no` 와 `detail` 로 적는다.
"제목 문체 위반 3건" 만으로는 고칠 수 없다.

## 실행

1. `_build_report.json`·`02_deck.json`·`01_source.json` 을 읽는다. 하나라도 없으면
   그 사실을 `unchecked` 로 기록하고 해당 검사를 건너뛴다. 없는 것을 통과로 세지 않는다.
2. 원천 반박검증의 대상·범위·결과를 `--adversarial-review-note`로 넘겨
   `verify_deck.py`를 돌린다. 공통 runner를 쓸 때는 같은 내용을
   `PPT_ADVERSARIAL_REVIEW_NOTE` 환경변수로 넘긴다. 매 실행 기록이 없으면 `unchecked`다.
3. 실패 항목을 소유 에이전트별로 묶는다 (규칙 2 표).
4. `blind_spots[]` 를 채운다.
5. `verdict` 를 정한다. `fail` 이 0이고 `unchecked` 가 0이면 `accept`, 아니면 `reject`.
6. `03_qa.json` 과 사람이 읽을 `03_qa.md` 를 함께 낸다.
7. 보고: 통과·실패·경고·미검사 건수, 반려 대상별 목록, 사각지대.

## output path

`output/ppt/<deck-id>/03_qa.json` 과 `03_qa.md` 만 쓴다.
다른 어떤 파일도 쓰지 않는다.
