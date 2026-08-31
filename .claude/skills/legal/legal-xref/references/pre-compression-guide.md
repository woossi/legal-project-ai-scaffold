---
name: legal-xref
description: Use when searching or resolving 조문 단위 외부 참조 in 시행지침 markdown — 내부·외부 참조 구분, 별표·별지·부칙 참조, 범위 참조 전개, 준용 관계, 조문→참조 역인덱스와 검색 CLI
---

# 구성 (스킬 폴더 기준 상대 경로)

- `scripts/xref_common.py` — 표기 정규화·문서 파싱 규약. 인용부호 집합, `canon_key`,
  조문·별표·범위·상대참조 표기, 조문 트리와 편·장·절 문맥을 여기서 만든다
- `scripts/extract_xref.py` — 지침 md 전건을 조문 단위로 순회해 참조를 뽑는다.
  `xref_index.json` 과 `_xref_report.json` 을 만든다 (정렬 순회 · 멱등)
- `scripts/build_article_index.py` — 조문 → 참조, 대상 → 조문 두 방향 역인덱스
- `scripts/search_xref.py` — 검색 CLI. 결과 건수와 사각지대를 항상 함께 낸다
- `scripts/verify_contract.py` — 계약 검증. `--full` 은 참조 표기가 근거 발췌와
  원문 줄에 실재하는지 전건 대조한다
- `contract/xref.schema.json` — 참조 레코드 구조 (JSON Schema)
- `contract/outputs.json` — 산출물 목록·선행조건·멱등성·값 도메인·교차 제약·검증 루틴
- `case/판정규칙.md` — **어떻게 가르는가.** 조문 앵커·어휘·편장절·준용·표기 변이·근거 발췌의
  판정 절차와 그 근거 수치. 규칙을 고치기 전에 읽는다
- `case/참조표기.md` — 실측 표기 유형별 실제 사례와 그 판정
- `case/내부외부판정.md` — 내부·외부가 갈린 경계 사례와 판정 근거. `scope_basis` 별 분포표
- `common-mistakes.md` — 개발 중 실제로 산출물에 들어왔던 오판정

# 실행

입력은 `output/legal/markdown/<지역>/<지구명>.md` 189건이다. 갱신한 뒤에는 매번 검증한다.

```bash
python3 scripts/extract_xref.py           # 참조 추출 (약 10초)
python3 scripts/build_article_index.py    # 역인덱스
python3 scripts/verify_contract.py --full # 계약 검증 + 환각 전수 검사
python3 scripts/search_xref.py --target 주차장법          # 역방향 질의
python3 scripts/search_xref.py --district 하남교산 --article 제3조 --scope 외부
python3 scripts/search_xref.py --unresolved --kind 준용   # 격리된 것
```

`legal-statute` 와의 경계 — **정의문의 상위법령 인용 간선은 `legal-statute` 가 정본이고,
조문 본문의 참조는 `legal-xref` 가 정본이다.** 저쪽은 `terms.json` 의 `variants[].text`
(정의문)만 훑고 산출은 법령 노드와 인용 간선이다. 이쪽은 조문 본문 전체를 훑고
내부·외부 구분, 별표·별지·부칙, 범위 전개, 준용 관계, 조문 역인덱스를 담는다.
모수가 다르므로 두 산출물의 건수를 나란히 비교하지 않는다.

법령 정본은 어느 쪽도 만들지 않는다. `statute_master.json`(법제처 API)이 정본이고
이쪽은 `master_status` 로 대조 상태만 관측한다.

## 절대 규칙

1. **못 찾은 것을 찾은 것처럼 만들지 않는다.** 해소 실패는 `xref_index.json` 에서 빼고
   `_xref_report.json` 에 사유와 함께 격리한다. 미판정·미해소·범위전개 실패·후보 다중이
   전부 여기로 간다. kb-plan 절대규칙 5(추측 관계 금지)와 같다
2. **법령명 없는 조문 참조를 외부로 승격하지 않는다.** 선행 법령이 **인접해** 있을 때만
   외부다. 없으면 판정 근거(`본지침명시`·`문서조문실재`·`현재조문내`·`편장절한정`·
   `문서표제일치`)를 담아 내부로 판정하고, 근거가 없으면 `미판정` 이다. 문장 안에 법령이
   있으나 인접하지 않으면(`선행법령_비인접`) 승격도 내부 판정도 하지 않는다
3. **인용부호로 묶였다는 이유만으로 법령으로 만들지 않는다.** 같은 문서의 표목·조문 표제와
   대조해 내부 표목이면 `내부표목` 이다. 표기만으로 외부 문서인지 이 지침이 속한 계획의
   구성 항목인지 갈리지 않으면(`「가구 및 획지계획」`) `미판정` 이다
4. **범위 전개는 양 끝 조문이 대상 문서에 실재할 때만 한다.** 실재하지 않으면 전개하지 않고
   원문 표기만 남긴 채 격리한다. 외부 법령의 범위는 그 법령의 조문 목록이 없으므로
   전개하지 않는다
5. **원문 표기와 해소값의 필드를 나눈다.** `surface`·`name_surface`·`quote` 가 원문이고
   `target`·`range` 가 해소값이다. 섞으면 원문 복원이 불가능해진다
6. **값 도메인은 실측 관측값으로 계약에 고정한다.** 새 값이 나오면 판정 로직이 바뀐 것이므로
   `contract/outputs.json` 을 먼저 갱신한다. `verify_contract.py` 가 도메인 이탈을 잡는다
7. **문맥 의존 참조에 식별자를 발급하지 않는다.** `「동법 시행령」` 을 어휘로 학습하면
   `동법시행령` 이 법령 노드가 된다. 상대참조는 같은 문장의 선행 법령으로 해소하고,
   해소 못 하면 격리한다. kb-ontology 와 같은 규칙이다
8. **관측값을 확정값으로 승격하지 않는다.** `legal-statute` 가 미대조로 둔 법령을
   정본대조로 올리지 않는다. `master_status` 는 `정본대조`·`미대조`·`미수록` 세 값이며
   정본대조가 아니면 `statute_official` 을 비운다

판정 절차 — **어떻게 가르는가는 `case/판정규칙.md` 가 정본이다.** 조문 앵커와 후보 다중,
법령명 어휘 학습, 편·장·절, 준용, 표기 변이, 근거 발췌를 다룬다. 규칙을 고치기 전에 읽는다.

**실측 수치를 이 문서에 박지 않는다.** 재실행하면 낡는다 — 현재 값의 정본은 산출물의 `meta`
와 `_xref_report.json` 이고, 회차별 상태는 `.claude/agent-memory/legal-xref/MEMORY.md` 다.

# output path

- `output/legal/xref/xref_index.json` — 지침 조문 → 참조 레코드
- `output/legal/xref/xref_by_article.json` — 조문 → 참조, 대상 → 조문 역인덱스
- `output/legal/xref/_xref_report.json` — 미해소·미판정·범위전개 실패·후보 다중·법령정본 미수록

산출물 목록과 교차 제약의 정본은 `contract/outputs.json` 이다.
