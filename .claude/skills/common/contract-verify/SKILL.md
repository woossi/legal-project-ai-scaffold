---
name: contract-verify
description: Use when running or writing 계약 검증 for a skill's outputs — 선행조건·구조 계약(JSON Schema)·선언적 계약(값 도메인·교차 제약)·멱등성·불변식을 정해진 순서로 검사하고, 통과·실패·미검사 세 갈래로 집계할 때. Also use when a verifier's exit code or report format has to be decided.
---

# contract-verify

## 구성

- `scripts/run_verifiers.py` — 등록된 검증기를 순서대로 실행하고 통과·실패·미검사를
  집계해 리포트를 낸다. `--skill` 로 일부만, `--dry-run` 은 실행 계획만.
  디스크에 있으나 등록부에 없는 검증기도 함께 알린다
- `scripts/check_outputs_contract.py` — `contract/outputs.json` 형식의 선언적 계약에
  공통 검사를 건다. `outBase/files`, `output_root/outputs`, `산출물[].path` 형식의
  필수 파일 존재·topKeys 존재·JSON Schema 대조 셋만 본다. `schema.json#/pointer`,
  CSV 산출물, `output/ppt/<deck-id>/build/previews/sNN.png` 자리표시자를 해석한다
- `contract/verifier_registry.json` — 검증기 등록부. 스킬별 경로·실행 순서·선행조건·
  종료코드 실태를 담는다. 실행할 수 없는 진단기와 레거시 검증기는
  `ignoredVerifiers` 에 경로와 제외 사유를 함께 둔다. **등록부가 조용히 낡으므로
  `run_verifiers.py` 의 미등록 경고를 흘리지 않는다**
- `contract/report.schema.json` — 검증 리포트 구조 (JSON Schema)
- `case/검증기실태.md` — 검증기를 읽고 돌려 확인한 대조표. 서로 어긋나는 지점
- `references/common-mistakes.md` — 계약 검증에서 데인 곳
- **산출물 계약은 두지 않는다.** 각 팀 스킬의 `contract/` 가 그 산출물의 정본이며 이
  스킬은 정본을 만들지 않는다. 여기 있는 계약 둘은 이 스킬 자신의 것이다

## 실행

`adversarial-review` 와 대상이 다르다. 그쪽은 산출물을 **원자료와 대조해 반증**하는
관점 규칙이고, 이 스킬은 산출물의 **구조와 선언적 계약을 기계적으로 검사**하는 절차다.
이 스킬이 전건 통과해도 판정 규칙의 구멍은 하나도 잡히지 않는다 — 그 몫은
`adversarial-review` 다. 둘 다 돌려야 완료다.

### 검사 순서

앞 단계가 깨지면 뒤 단계의 결과는 읽을 수 없다. 순서를 바꾸지 않는다.

1. **선행조건** — 입력 파일과 모듈이 있는가. 없으면 실행하지 않고 미검사다
2. **구조 계약** — JSON Schema. 필드 존재와 타입
3. **선언적 계약** — 값 도메인, 교차 제약, 판정 파라미터
4. **멱등성** — 같은 입력에 같은 산출물인가
5. **불변식** — 고아 참조·dangling 없음. pytest 로 검사한다
6. **리포트** — 세 갈래 집계와 사각지대를 함께 적는다

```bash
python3 scripts/run_verifiers.py --dry-run          # 실행 계획과 미검사 예정 사유
python3 scripts/run_verifiers.py                    # 전건 실행 + 리포트
python3 scripts/run_verifiers.py --skill legal-term # 일부만 (반복 지정 가능)
python3 scripts/check_outputs_contract.py           # outputs.json 공통 검사
```

종료코드 규약과 보고 형식은 `references/사각지대와-보고.md` 의 「종료코드와 보고」 절에 있다.

### 절대 규칙

1. **자기 일관성은 검증이 아니다.** 같은 스크립트가 만든 두 파일이 맞는 것은 당연하다.
   산술이 완벽해도 판정 규칙의 구멍은 하나도 잡히지 않는다
2. **입력이 없는 것을 실패로 집계하지 않는다.** 거짓 경보가 나머지 검사를 덮는다
3. **건너뛴 검사를 통과로 보고하지 않는다.** 대조 상대가 없으면 그 사실을 출력한다.
   조용히 통과시키면 검증기가 살아 있는 것처럼 보이면서 아무것도 보지 않는다
4. **검사 결과를 보고할 때 그 검사가 보지 못하는 범위를 함께 적는다.** 검사 통과는
   안전 보증이 아니다
5. **계약을 고쳐 검사를 통과시키지 않는다.** 값 도메인에 새 값이 나온 것은 판정 로직이
   바뀌었다는 뜻이므로 계약을 먼저 갱신하고, 그 갱신이 옳은지를 따로 확인한다
6. **다른 스킬의 검증기를 이 스킬이 고치지 않는다.** 이 스킬은 감싸는 층이다.
   실패는 그대로 보고하고 담당 스킬로 넘긴다

이 검사가 보지 못하는 범위는 `references/사각지대와-보고.md` 의 「사각지대」 절이 정본이다.
통과를 안전 보증으로 보고하지 않는다.

## output path

- `output/kb/reports/_contract_verify.json` — 검증 리포트

팀 중립 산출물이지만 규약상 `output/<팀>/` 아래여야 하므로, 같은 성격의 리포트가 이미 있는
`output/kb/reports/` 에 둔다. 경로의 정본은 `contract/verifier_registry.json` 의 `reportPath`
이며 `--report` 로 덮어쓸 수 있다.

각 팀 스킬 산출물의 정본은 그 스킬의 `contract/` 이다. 이 스킬은 산출물 계약을 만들지 않는다.
