---
name: kb-ontology
description: Use when defining or validating the output/kb RDF/OWL ontology — IRI 발급, 법령·조례명 정규화, 기준 시점 결정, 결정론·확률론 층 분리, SHACL 게이트
---

# 구성 (스킬 폴더 기준 상대 경로)

- `scripts/mint_iri.py` — IRI 발급. 스펙 ②·③이 import 한다
- `scripts/normalize_law.py` — 법령·조례명 정규화. 해석 불가는 격리한다
- `scripts/resolve_asof.py` — 기준 시점 결정. 고시일 우선, 없으면 지구번호 연도
- `scripts/build_vocab.py` — `taxonomy.json` → `vocab-concept.ttl` 생성. `--check` 로 어긋남만 확인
- `scripts/validate_ontology.py` — 게이트 1~7. `output/kb/` 만으로 돈다
- `scripts/verify_chain.py` — 게이트 8~13. 입력이 없으면 미검사로 집계한다
- `scripts/draw_ontology.py` — `ontology/*.ttl` → `ontology-map.{dot,svg,png}` 도식 생성 (멱등)
- `scripts/build_example.py` — 실지구 인스턴스 사례 조립 → `case/사례_강일_공개공지.ttl` 과 `ontology-instance-example.*`
- `scripts/build_boundary.py` — 지구별 법령 바운더리를 지침 인용이 아니라 법령→지구로 유도 → `graph/det/boundary.ttl`·`reports/_boundary_check.json`·`ontology-boundary-example.*` (멱등)
- `scripts/build_guideline_tree.py` — 지침 md 를 조문 트리로 세우고 용어 정의 진술을 매단다 → `graph/det/guideline.ttl`·`reports/_guideline_tree.json` (멱등)
- `contract/ontology.json` — 클래스·술어 목록, IRI 패턴, det/prob 술어 배치
- `contract/law_roots.json` — 법령 범위의 뿌리와 하강 규칙
- `contract/temporal.json` — 기준 시점 우선순위, 정밀도 도메인, 판본 IRI 규칙
- `case/사례_강일_공개공지.ttl` — 강일 도시개발구역의 공개공지 배선 사례. SHACL 이 판본 부재를 잡는 것까지가 사례다
- `case/정규화_수작업.json` — 3단계 규칙으로 안 되는 항목의 처리 결정
- `common-mistakes.md` — 각 규칙을 없앴을 때 실제로 유입된 것

# 실행

산출물을 갱신한 뒤에는 매번 두 검증기를 돈다.

```bash
python3 scripts/build_vocab.py                   # taxonomy 가 바뀐 경우만
python3 scripts/build_guideline_tree.py           # 조문 트리 (md·doc_definitions 가 바뀐 경우)
python3 scripts/validate_ontology.py             # 게이트 1~7
python3 scripts/verify_chain.py                  # 게이트 8~13
```

- **범위를 산출물로 정하지 않는다.** 법령 범위는 `contract/law_roots.json` 의 뿌리에서
  법령 체계를 타고 내려가 정한다. `cited_laws` 는 검증 재료이지 결정원이 아니다.
  실측상 법률 52종 중 하위법령이 함께 인용된 것은 6종뿐이라, 산출물로 정하면 46종의
  시행령이 빠진다
- **본법·시행령·시행규칙을 접지 않는다.** 접으면 위임 사슬의 중간 단계가 사라진다
- **해석 불가는 IRI 를 발급하지 않는다.** `동법시행령` 같은 문맥 의존 참조는 격리하고
  사유를 `reports/` 에 남긴다. 추측 관계를 넣지 않는다는 kb 절대규칙 5다
- **적용 사례는 근거가 있을 때만 만든다.** 누락·미상은 인스턴스가 아니라 리포트다
- 새 인용 표기가 나오면 `scripts/normalize_law.py` 가 아니라 `case/정규화_수작업.json`
  을 먼저 고친다
- 게이트 8~13 은 스펙 ②·③·용어-컴포넌트-연결의 산출물을 입력으로 받는다. 없으면
  실패가 아니라 미검사다
- **조문 노드를 평문 정규식으로 훑지 않는다.** 괄호만 있는 줄이 전부 조문으로 잡힌다.
  `doc_definitions.articles` 를 역인덱스로 써서 승격한다
- **`promoted` 는 평문만 뜻하지 않는다.** h4 조문 헤딩이 아니면서 현재 md 어딘가에
  표제가 있는 경우 전부다 — 평문 줄과 h4 가 아닌 헤딩(h1~h3·h5·h6)을 함께 담는다.
  `definition-restored` 는 "절삭으로 사라져 현재 md 에 없다" 만 뜻하도록 좁혀 둔다
- **용어 의미의 동일성을 판정하지 않는다.** 문자열 유사도로는 판정할 수 없다 —
  `lp:문언종수` 로 갈림의 정도만 기록한다

# output path

- `output/kb/ontology/{core,grounding,vocab-concept,vocab-relation,keys}.ttl`
- `output/kb/shapes/{det,prob}.shacl.ttl`
- `output/kb/{README.md,provenance.ttl}`
- `output/kb/graph/det/boundary.ttl` 은 `build_boundary.py` 가 만든다.
  `output/kb/graph/det/guideline.ttl`·`output/kb/reports/_guideline_tree.json` 은
  `build_guideline_tree.py` 가 만든다. 나머지 det 과 prob 는 스펙 ③이 채운다
- `output/kb/reports/` — 그래프에 들어가지 못한 것과 사유

산출물 계약의 정본은 `contract/ontology.json` 이다.
