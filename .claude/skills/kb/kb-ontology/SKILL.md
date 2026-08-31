---
name: kb-ontology
description: Use when defining or validating the output/kb RDF/OWL ontology — IRI 발급, 법령·조례명 정규화, 기준 시점 결정, 결정론·확률론 층 분리, SHACL 게이트
---

# kb-ontology

## 상태 (deprecation)

`project-core M1`은 2026-08-25에 구현한 유일한 새 수직절단이다. 아래의 기존 9계통
스크립트·계약은 이전 구조를 전제하므로 Project Core의 정본이 아니다. 여기에는
`contract/ontology.json`도 포함된다. 기존 구조의 상태 정본은 `.claude/rules/계획규범요소-틀.md`다.

Project Core M1의 정본 구성요소는 다음과 같다.

- `contract/project_core.json`
- `contract/project_core_source.schema.json`
- `contract/procedure_profile.schema.json`
- `scripts/build_project_core.py`
- `scripts/ingest_project_core.py` — 역방향. 편집기가 고친 TTL 을 계약으로 되읽는다
- `scripts/build_project_core_pilot.py`
- `scripts/query_plan_version.py`
- `scripts/evaluate_project_conditions.py`
- `scripts/validate_project_core.py`

## 구성

- `scripts/mint_iri.py` — IRI 발급. 스펙 ②·③이 import 한다
- `scripts/normalize_law.py` — 법령·조례명 정규화. 해석 불가는 격리한다
- `scripts/resolve_asof.py` — District 시간 관측값 생성. 고시일 우선, 없으면 지구번호 연도 proxy. 적용 판본은 결정하지 않는다
- `scripts/build_vocab.py` — `taxonomy.json` → `vocab-concept.ttl` 생성. `--check` 로 어긋남만 확인
- `scripts/validate_ontology.py` — 게이트 1~7. `output/kb/` 만으로 돈다
- `scripts/verify_chain.py` — 게이트 8~14. 입력이 없으면 미검사로 집계한다
- `scripts/draw_ontology.py` — `ontology/*.ttl` → `ontology-map.{dot,svg,png}` 도식 생성 (멱등)
- `scripts/build_example.py` — 실지구 인스턴스 사례 조립 → `case/사례_강일_공개공지.ttl` 과 `ontology-instance-example.*`
- `scripts/build_boundary.py` — 지구별 법령 바운더리를 지침 인용이 아니라 법령→지구로 유도 → `graph/det/boundary.ttl`·`reports/_boundary_check.json`·`ontology-boundary-example.*` (멱등)
- `scripts/build_guideline_tree.py` — 지침 md 를 조문 트리로 세우고 용어 정의 진술을 매단다 → `graph/det/guideline.ttl`·`reports/_guideline_tree.json` (멱등)
- `scripts/build_plan_rule.py` — `norm_건축계획지표.csv` 한 행을 계획규정 하나로 발급 → `graph/det/plan-rule.ttl`·`reports/_plan_rule.json` (멱등)
- `scripts/build_plan_item.py` — 수립지침 절·항을 계획항목(L1) 축으로 옮긴다 → `ontology/vocab-plan-item.ttl`·`graph/det/plan-item.ttl`·`reports/_plan_item.json` (멱등). `--check` 로 어긋남만 확인
- `scripts/build_evidence.py` — 기존 계획규정과 수립지침 항의 표·본문·공식문서 근거를 독립 역발급 → `graph/det/evidence.ttl`·`reports/_evidence.json` (멱등). `--check` 로 어긋남만 확인
- `contract/ontology.json` — 클래스·술어 목록, IRI 패턴, det/prob 술어 배치, 지구단위계획 계통 발급 근거 요건(`planMinting`), 계획항목 축 발급 규약(`planItemAxis`)
- `contract/law_roots.json` — 법령 범위의 뿌리와 하강 규칙
- `contract/temporal.json` — 관측 시점 우선순위와 LawApplication 적용 기준 분리 규칙
- `case/사례_강일_공개공지.ttl` — 강일 도시개발구역의 공개공지 배선 사례. 적용일·판본 미확정으로 LawApplication을 발급하지 않는 것까지가 사례다
- `case/정규화_수작업.json` — 3단계 규칙으로 안 되는 항목의 처리 결정
- `references/common-mistakes.md` — 각 규칙을 없앴을 때 실제로 유입된 것

## 실행

Project Core M1 산출물은 다음 명령으로 생성하고 검증한다.

```bash
.venv/bin/python3 .claude/skills/kb/kb-ontology/scripts/build_project_core.py
.venv/bin/python3 .claude/skills/kb/kb-ontology/scripts/build_project_core_pilot.py
.venv/bin/python3 .claude/skills/kb/kb-ontology/scripts/validate_project_core.py
```

기존 9계통 산출물을 유지보수하는 경우에만 아래 두 기존 검증기를 돈다.

```bash
python3 scripts/build_vocab.py                   # taxonomy 가 바뀐 경우만
python3 scripts/build_guideline_tree.py           # 조문 트리 (md·doc_definitions 가 바뀐 경우)
python3 scripts/build_plan_rule.py                # 계획규정 (csv 가 바뀐 경우)
python3 scripts/build_plan_item.py                # 계획항목 축 (수립지침_항구조.json 이 바뀐 경우)
python3 scripts/build_evidence.py                 # 계획규정·계획항목 근거 레코드
python3 scripts/validate_ontology.py             # 게이트 1~7
python3 scripts/verify_chain.py                  # 게이트 8~14
```

- **범위를 산출물로 정하지 않는다.** 법령 범위는 `contract/law_roots.json` 의 뿌리에서
  법령 체계를 타고 내려가 정한다. `cited_laws` 는 검증 재료이지 결정원이 아니다.
  실측상 법률 52종 중 하위법령이 함께 인용된 것은 6종뿐이라, 산출물로 정하면 46종의
  시행령이 빠진다
- **본법·시행령·시행규칙을 접지 않는다.** 접으면 위임 사슬의 중간 단계가 사라진다
- **해석 불가는 IRI 를 발급하지 않는다.** `동법시행령` 같은 문맥 의존 참조는 격리하고
  사유를 `reports/` 에 남긴다. 추측 관계를 넣지 않는다는 kb 절대규칙 5다
- **적용 사례는 근거가 있을 때만 만든다.** 누락·미상은 인스턴스가 아니라 리포트다
- **지구번호 연도는 적용 판본의 asOf가 아니다.** `observation*` 품질 관측값으로만
  쓰고, 실제 고시일·인용일·문서작성일과 ArticleVersion이 함께 확인되기 전에는
  `LawApplication`을 만들지 않는다
- 새 인용 표기가 나오면 `scripts/normalize_law.py` 가 아니라 `case/정규화_수작업.json`
  을 먼저 고친다
- 게이트 8~14 는 스펙 ②·③·용어-컴포넌트-연결의 산출물을 입력으로 받는다. 없으면
  실패가 아니라 미검사다
- **조문 노드를 평문 정규식으로 훑지 않는다.** 괄호만 있는 줄이 전부 조문으로 잡힌다.
  `doc_definitions.articles` 를 역인덱스로 써서 승격한다
- **`promoted` 는 평문만 뜻하지 않는다.** h4 조문 헤딩이 아니면서 현재 md 어딘가에
  표제가 있는 경우 전부다 — 평문 줄과 h4 가 아닌 헤딩(h1~h3·h5·h6)을 함께 담는다.
  `definition-restored` 는 "절삭으로 사라져 현재 md 에 없다" 만 뜻하도록 좁혀 둔다
- **용어 의미의 동일성을 판정하지 않는다.** 문자열 유사도로는 판정할 수 없다 —
  `lp:문언종수` 로 갈림의 정도만 기록한다
- **훈령의 항을 조문으로 타이핑하지 않는다.** 수립지침 항은 `lp:AdminRuleClause` 다.
  `lp:ArticleWork` 로 두면 위임 사슬(게이트 8)의 뿌리 도달 대상이 되고 판본까지
  발급된다. `lp:inSource`(domain 이 `ArticleWork`) 대신 `lp:inAdminRule` 을 쓰는 것도
  같은 이유다
- **계획항목의 제52조 호를 정규식으로 정하지 않는다.** 근거원은 호법문·`slot_exact`·
  법정구조표 셋뿐이다. 미확정 절은 호를 붙이지 않고 사유만 리포트에 남긴다
- **두 evidence 술어를 합치지 않는다.** `lp:evidence`는 `LawApplication`에서 기존
  규정·출현을 가리킨다. `lp:hasEvidenceRecord`는 계획규정·수립지침 항에서 신규
  `lp:EvidenceRecord`를 가리킨다
- **공식문서 이름을 추정하지 않는다.** 계획규정의 공식문서는 `tables.csv` 출처문서
  basename과 같은 지구 `meta.json`의 `originalName|savedAs`가 정확히 일치할 때만
  발급한다. 부분일치와 부모 attachment 일반화는 사용하지 않는다

## output path

- Project Core M1: `output/kb/ontology/project-core.ttl`,
  `output/kb/shapes/project-core.shacl.ttl`, `output/kb/graph/det/shinnae2-2008-773.ttl`,
  `output/kb/{README.md,provenance.ttl}`

- `output/kb/ontology/{core,grounding,vocab-concept,vocab-relation,vocab-plan-item,keys}.ttl`
- `output/kb/shapes/{det,prob}.shacl.ttl`
- `output/kb/{README.md,provenance.ttl}`
- `graph/det/`에는 `boundary.ttl`·`guideline.ttl`·`plan-rule.ttl`·`plan-item.ttl`·
  `evidence.ttl`을 둔다. 각 `build_*.py`는 같은 이름의 리포트를 만든다.
  `build_plan_item.py`만 `ontology/`에도 낸다 — 절은 어휘, 항은 det이라 한 원천이
  두 층으로 갈린다.
  나머지 det 과 prob 는 스펙 ③이 채운다
- `output/kb/reports/` — 그래프에 들어가지 못한 것과 사유

기존 9계통 산출물 계약의 정본은 `contract/ontology.json` 이다. 이 계약은 Project Core M1에 적용하지 않는다.
