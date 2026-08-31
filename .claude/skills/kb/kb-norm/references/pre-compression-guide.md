---
name: kb-norm
description: Use when building or validating the 시행령 → 지자체 조례 위임구조 graph — 위임 사슬, 규범값 명제, 조례 계통 확정, 상대참조 해소
---

# 구성 (스킬 폴더 기준 상대 경로)

- `scripts/corpus.py` — corpus 두 개를 읽고 조례 계통을 확정한다. lcCode5 조회도 여기
- `scripts/parse_ordinance.py` — 조문 텍스트 파서. 순수 함수만 두고 파일을 읽지 않는다
- `scripts/mint_norm_iri.py` — zone·cond·norm IRI. statute·ordinance·gov 는 기존 mint_iri 를 쓴다
- `scripts/build_delegation.py` — 위임 사슬 → `graph/det/delegation.ttl` (멱등)
- `scripts/build_norm_values.py` — 건폐율·용적률 → `graph/det/norm-value.ttl` (멱등)
- `contract/jurisdiction_code.json` — 조례 주체 35개 ↔ lcCode5
- `contract/delegation.json` — 위임 축 16개·격리 사유·조례 주체 층위
- `contract/norm_value.json` — 용도지역 21종·조건 키 규약·교차 제약
- `common-mistakes.md` — 규칙을 없앴을 때 실제로 유입된 것

# 실행

```bash
.venv_kb/bin/python3 .claude/skills/kb/kb-norm/scripts/build_delegation.py
.venv_kb/bin/python3 .claude/skills/kb/kb-norm/scripts/build_norm_values.py
.venv_kb/bin/python3 -m pytest tools/tests/test_kb_norm_*.py -q
```

- **양방향 근거가 없으면 간선을 만들지 않는다.** 발신(상위 조문의 위임 문언)과
  수신(조례 조문의 지목 문언)이 둘 다 있어야 한다
- **조례 계통을 조례명으로 정하지 않는다.** 제1·2조가 지목한 법률로 정한다.
  이름은 표기 변이가 있고 법적 근거가 아니다
- **근거 항이 조건을 결정한다.** 영 제84조제1항은 기본값, 제6항은 방화지구 특례다.
  항을 구분하지 않으면 특례값이 기본값을 덮는다
- **가지조문(제84조의2 등)을 본조로 읽지 않는다.** `basis["number"]` 만 보면
  가지조문이 본조로 오인돼 완화값이 기본값과 충돌한다. `basis["branch"]` 를
  꼭 같이 본다. 실측 20건/19관할 — 지금은 `100분의` 표기라 값이 안 잡혀
  피해가 0이지만 그 파서를 지원하면 오염된다
- **천단위 쉼표를 떼고 읽는다.** `1,000퍼센트` 를 `[0-9]{1,4}` 로 읽으면 `000` 이 잡혀
  값이 0 이 된다. 실측 31건/17개 조례
- **괄호 조건부 값을 기본값과 섞지 않는다.** `20퍼센트 이하 (취락지구인 경우에는
  40퍼센트 이하)` 의 40 은 별도 명제다. 실측 98건/21개 조례
- **원문에 없는 비교연산자를 기본값으로 채우지 않는다.** `20퍼센트`(연산자 표기
  없음)를 "이하"로 채우면 원문이 "이상"인 경우까지 오염된다. 파서는 정직하게
  `None`을 낸다 — `lp:비교연산_미표기`로 드러내고 격리하지 않는다. 실측 869
  명시/170 미표기
- **조례 주체 층위는 계통마다 다르다.** 도시계획조례는 시·군(자치구 아님),
  건축조례는 자치구면 광역(건축법 제4조제5항), 주차장조례는 자치구도 주체다
- **현행 조례를 과거 지침에 귀속하지 않는다.** 조례 정본은 현행이고 지침은
  2002~2024년이다

# output path

- `output/kb/norm/ontology/norm-core.ttl` — TBox. `output/kb/ontology/core.ttl` 을 전제한다
- `output/kb/norm/graph/det/{delegation,norm-value}.ttl`
- `output/kb/norm/shapes/norm.shacl.ttl`
- `output/kb/norm/reports/_{delegation,norm_value}.json` — 그래프에 못 들어간 것과 사유
- `output/kb/` 의 **기존 산출물**(`graph/`·`ontology/`·`shapes/`·`reports/`)은 읽기만 한다.
  이 스킬이 쓰는 것은 `output/kb/norm/` 하위뿐이다

산출물 계약의 정본은 `contract/delegation.json` 과 `contract/norm_value.json` 이다.
