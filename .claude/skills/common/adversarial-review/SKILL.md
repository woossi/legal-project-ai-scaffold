---
name: adversarial-review
description: Use when verifying the accuracy of a large data artifact — an aggregate report, an extraction result, a classification dataset — and you need to falsify it rather than confirm it. Checks against source material instead of internal consistency, and targets negative claims and fabricated evidence first. Also use when you are the one being reviewed and need to tell a correct objection from a mistaken one.
---

# adversarial-review

## 구성

- `case/표적우선순위.md` — 표적 5종, 검사 사각지대 명시, 격리 파일 패턴
- `case/반박검증.md` — 검수자 오류 3종, 원문 기반 반박, 저신뢰 필드 취급
- `references/common-mistakes.md` — 검수에서 데인 곳
- 스크립트와 계약은 두지 않는다. 전 팀 공용 검수 관점 규칙만 둔다
- 검수 대상 산출물의 계약은 각 팀 스킬의 `contract/` 가 정본이다

## 실행

**자기 일관성은 검증이 아니다.** 보고서와 CSV 가 일치하는 것은 같은 스크립트가 두 파일을
만들었기 때문이다. 실측 사례에서 도수분포 13칸, 유형합, 교차표 행열합이 전부 일치하고
산술도 완벽했으나, 판정 규칙에 구멍이 5개 있었다. 산술 검증은 그중 하나도 잡지 못했다.

검증은 원자료 대조로 수행한다. 표적은 아래 순서로 고르며, 각 항목의 판정 절차는
`case/표적우선순위.md` 에 있다.

1. 부정 주장 — "0건"으로 집계된 칸을 전건 실측한다
2. 환각 전수 검사 — 인용문을 원본에서 substring 대조한다. 샘플링하지 않는다
3. 파생 필드의 자기모순 — 근거 필드와 판정 필드가 어긋나는 행을 찾는다
4. 모수 왜곡 — 분모에 부적격 건이 섞였는지 확인한다
5. 파일 간 정합성 — 고아 참조와 합계 불일치를 찾는다

- 검사 결과를 보고할 때는 그 검사가 보지 못하는 범위를 함께 적는다
- 지적을 제기하거나 수용하기 전에 `case/반박검증.md` 를 확인한다. 실측 반례 5건 중 3건이
  검수자 오류였다
- 확신이 없는 판정은 필드에 기록하지 않고 비운다

## output path

- 산출물을 만들지 않는다. 검수 대상 스킬의 경로를 따른다
