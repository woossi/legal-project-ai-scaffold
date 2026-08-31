---
name: legal-statute
description: Use when extracting 상위법령 인용 from 시행지침 정의문 into 법령 노드·인용 간선, when 법령 정본을 법제처 OPEN API 로 수집하여 정식명칭·법령ID·시행일을 확정할 때, or when 건폐율·용적률 규범값의 근거 계통을 다룰 때
---

# 구성 (스킬 폴더 기준 상대 경로)

- `scripts/build_citations.py` — 정의문에서 상위법령 인용을 추출해 간선을 만든다. 간선마다
  지구와 근거 발췌가 붙는다
- `scripts/build_guideline_article_scope.py` — 189개 시행지침의 일반 조문 본문 전체와,
  조문 헤딩이 유실된 문서의 비구조화 본문을 훑어 외부 규범 수집 범위를 확정한다
- `scripts/review_guideline_article_scope.py` — 원 Markdown 전체본문을 독립 경로로 다시
  스캔해 직접인용 누락·구조 오류를 적대 검수하고 공식 API/수동/격리 큐를 분리한다
- `scripts/collect_guideline_articles.py` — 검수 통과 큐의 현행 정본 전문을 캐시·checkpoint·
  속도제한·차단 회로와 함께 수집한다. 행정규칙은 상세 `ID`, 법령·조례는 `MST`를 쓴다
- `scripts/build_guideline_t4_scope.py` — 직접범위 정본 조문의 겹낫표 명시 cross-reference를
  T4 1단계 파생범위로 만들고 재귀 확장을 금지한다
- `scripts/verify_guideline_articles.py` — 직접범위·T4의 큐, 정본 master, 전문 corpus,
  파싱 상태, 적용판본 미확정 표지를 전건 대조한다. 수집 작업공간에서만
  `--require-cache`로 Git 제외 원응답 캐시의 실재까지 검사한다
- `scripts/collect_statutes.py` — 법제처 OPEN API 에서 법령 정본을 조회해 법령 노드를
  만든다. 법령·자치법규·행정규칙 세 대상을 꼬리로 가려 순서대로 시도한다
- `scripts/build_norm_basis.py` — 규범값 근거 계통을 산출물로 옮긴다
- `scripts/collect_articles.py` — 규범값 계통 조문의 본문을 법제처 API 에서 받는다
- `scripts/collect_history.py` — 정본대조 법령의 시행 이력을 받고, 지구번호 연도 proxy 는
  별도 신선도 관측으로 격리한다
- `scripts/verify_statute.py` — 계약 검증. `--full` 은 인용 표기가 근거 문장에 실재하는지
  대조한다
- `contract/outputs.json` — 산출물 목록·교차 제약·검증 루틴
- `contract/statute_citations.schema.json` — 인용 간선 구조 (JSON Schema)
- `contract/statute_master.schema.json` — 법령 정본 노드 구조 (JSON Schema)
- `case/정본대조.json` — 규범값 근거 계통, 명칭 변천, 표기 교정. 근거를 확인한 것만 담는다

# 실행

범위 감사 입력은 `output/legal/markdown/` 189개 전건이고, 기존 정의문 인용 간선의 입력은
`output/legal/word/terms.json` 이다. 갱신한 뒤에는 매번 검증한다.

```bash
python3 scripts/build_guideline_article_scope.py # 일반 조문 전수 범위 확정
python3 scripts/review_guideline_article_scope.py # 독립 전체본문 적대 검수 + 큐 분리
python3 scripts/collect_guideline_articles.py     # 직접범위 현행 정본 전문 (네트워크)
python3 scripts/build_guideline_t4_scope.py       # 정본 조문의 T4 1단계 범위
python3 scripts/collect_guideline_articles.py --review output/legal/statute/guideline_t4_scope.json --artifact-prefix guideline_t4
python3 scripts/verify_guideline_articles.py --require-t4-complete
python3 scripts/build_citations.py       # 인용 간선 추출
python3 scripts/collect_statutes.py      # 법령 정본 수집 (네트워크, 약 2분)
python3 scripts/build_norm_basis.py      # 규범값 근거 계통
python3 scripts/collect_articles.py      # 조문 본문 (네트워크)
python3 scripts/collect_history.py       # 법령 시행 이력 + 신선도 관측 (네트워크)
python3 scripts/verify_statute.py --full # 계약 검증
```

`collect_statutes.py` 는 법제처 OPEN API 를 쓴다. `--oc` 기본값은 `test` 이며, 대량 수집이나
운영에는 법제처에 등록한 자신의 ID 를 쓴다.

일반 조문 전문 수집기는 요청 간 최소 1초(기본 1.2초), 성공 응답 캐시, source별
checkpoint, 403/429·차단 페이지 즉시 중단, 연속 오류 회로를 강제한다. 프록시 회전,
User-Agent 회전, 캡차 우회, 차단 뒤 즉시 재시도는 금지한다.

```
법령     https://www.law.go.kr/DRF/lawSearch.do?target=law
자치법규  https://www.law.go.kr/DRF/lawSearch.do?target=ordin
행정규칙  https://www.law.go.kr/DRF/lawSearch.do?target=admrul
조문본문  https://www.law.go.kr/DRF/lawService.do?target=law&MST=…&JO=005600
          JO 는 조문번호 4자리 + 가지번호 2자리. 제56조 → 005600, 제27조의2 → 002702
시행이력  https://www.law.go.kr/DRF/lawSearch.do?target=eflaw
```

## 절대 규칙

0. **정의문 인용만으로 법령 범위를 닫지 않는다.** 먼저 189개 시행지침 일반 조문의
   문단·목록·표·주석·각주를 전수 스캔한다. h4 조문이 없는 문서는 본문 전체를
   `fallback_unsegmented_document` 로 보존하고, 조문 경계를 추정해서 만들지 않는다
1. **법령의 정본성은 사례 문서가 아니라 법제처가 공급한다.** 정식명칭·법령ID·시행일·소관은
   API 응답만 담고, 시행지침의 표기는 `실측표기` 필드에 격리한다. 섞으면 추정 명칭이 확정
   명칭처럼 쓰인다
2. **못 찾은 것을 찾은 것처럼 만들지 않는다.** 매칭 실패는 `검증상태: 미대조` 로 두고
   정식명칭·법령ID 를 비운다. 단일 후보라는 이유로 받으면 추측이다
3. **추측 간선을 만들지 않는다.** 상대참조(`동법`·`같은 법`)를 해소하지 못하면 간선을 버리고
   `_statute_report.json` 에만 남긴다. kb-plan 절대규칙 5와 같다
4. **간선마다 지구와 근거 발췌를 담는다.** 근거는 인용 위치 기준 발췌여야 한다 — 문장
   앞부분을 잘라 담으면 뒤쪽 인용이 근거 밖으로 밀려난다
5. **시행일 없이 조문을 쓰지 않는다.** 시행지침은 2002~2024년에 걸쳐 있어 현행 조문이 인용
   시점의 조문과 다를 수 있다
6. **지구번호 연도를 적용 시점으로 승격하지 않는다.** 이것은 지구 지정 연도이며 지침의
   작성일·고시일·인용일이 아니다. 신선도 우선순위 관측에만 쓰고 모든 행에
   `temporal_basis: 지구번호연도_proxy`, `적용판본_미확정: true` 를 둔다
7. **전체본문 독립 검수 전에는 크롤링하지 않는다.** 직접인용 누락과 구조 오류가 0이어야
   한다. 비법령 계획·내부 시행지침 편명·종류 불명 규칙·서술 잔여물은 공식 API 큐에서
   분리한다
8. **차단을 우회하지 않는다.** 403/429·캡차·차단페이지·연속 오류는 회로 중단 조건이다.
   성공 응답은 캐시해 재개 시 같은 요청을 반복하지 않는다
9. **T4는 한 단계만 확장한다.** 직접범위 정본의 `「자료명」+조문`을 파생범위로 수집하되,
   T4 정본의 인용을 다시 확장하지 않는다

## 판정 규칙

- 법령명 어휘는 2단으로 만든다. 1단은 통째로 법령명+조문참조인 인용에서 학습하고, 2단은
  학습한 어휘로 지저분한 문자열에서 최장 일치를 찾는다
- **어휘는 인용부호로 시작하는 인용에서만 학습한다.** 법령명은 관례상 「」로 묶이고,
  묶이지 않은 인용에는 앞 문장의 서술이 붙어 들어온다
- 인용부호 없는 인용은, 1단 어휘로 스캔해 **0번이 아닌 위치에서 아는 법령이 나오면**
  버린다. 그 앞부분이 서술 잔여물이라는 뜻이다(`말하여 이때 건축법`). 아는 법령이
  없으면 새 법령이므로 학습한다(`도로법`). 어미 목록으로 거르는 방식은 `위하여`·
  `높이는`·`정해진` 을 놓쳐 서술 잔여물 8종이 법령 노드로 올라왔다
- 정의문 도입부(`…라 함은`)가 든 문자열은 법령명이 아니다
- 조례에 지자체명이 없으면 근거 문장 바로 앞에서 되살린다. 같은 문장이 약칭을 정의한
  경우(`인천시 도시계획조례(이하 도시계획조례라 한다)`)는 앞선 조례로 잇는다
- **정본 대조가 어휘 결함의 검출기다.** 서술 잔여물은 API 에서 결과가 나오지 않으므로
  미대조로 드러난다. 미대조 목록을 보고 어휘 학습을 고친다
- 조회 대상은 표기 꼬리로 가른다. `조례` → 자치법규, `지침·고시·예규·훈령·기준` →
  행정규칙 우선, 그 밖에는 법령 우선이며 실패하면 행정규칙을 시도한다
- 자치법규 검색은 지자체 가나다순으로 대량 반환한다. `display` 를 작게 두면 본청 조례를
  놓친다(`서울특별시 건축 조례` 는 123건 중 뒤쪽)
- 자치법규는 정식 행정구역명만 받는다. `서울시 건축조례` 는 0건이므로 약칭을 편다
- 중점 표기는 원본마다 다르다(`ㆍ · ‧ ․ ･ ・ ∙`). 쉼표로 적은 문서도 있다. 대조 키에서
  접고 본문 매칭에서 다시 허용한다. `case/정본대조.json` 의 키도 같은 폭으로 접힌다
- `기법`·`공법`·`수법` 으로 끝나는 것은 `법` 으로 끝나도 법령이 아니다
- 명칭 변천은 `case/정본대조.json` 이 확인한 것만 받는다. 단일 후보라는 이유로 받지 않는다

# output path

- `output/legal/statute/statute_citations.json` — 시행지침 → 상위법령 인용 간선
- `output/legal/statute/guideline_article_scope.json` — 일반 조문 전수 인용 범위와 근거 전건
- `output/legal/statute/guideline_article_scope.md` — 범위·크롤링 tier·표적 사례 요약
- `output/legal/statute/guideline_source_article_corpus.jsonl.gz` — 원 시행지침 조문 전문·줄범위·해시
- `output/legal/statute/guideline_article_scope_review.json` — 독립 적대 검수와 분리 큐
- `output/legal/statute/guideline_statute_master.json` — 직접범위 현행 정본 대조
- `output/legal/statute/guideline_article_corpus.jsonl.gz` — 직접범위 정본 전문·전 조문
- `output/legal/statute/guideline_t4_scope.json` — 정본 조문의 T4 1단계 파생범위
- `output/legal/statute/guideline_t4_statute_master.json` — T4 현행 정본 대조
- `output/legal/statute/guideline_t4_article_corpus.jsonl.gz` — T4 정본 전문·전 조문
- `output/legal/statute/statute_master.json` — 법령 정본 노드
- `output/legal/statute/norm_basis.json` — 규범값 근거 계통 (조문 요지·관계)
- `output/legal/statute/article_master.json` — 규범값 계통 조문의 본문 정본
- `output/legal/statute/statute_history.json` — 정본대조 법령의 시행 이력 사실
- `output/legal/statute/_freshness_observations.json` — 지구번호 연도 proxy 신선도 관측
- `output/legal/statute/_statute_report.json` — 미해소 인용, 어휘 미포착
- `output/legal/statute/_collect_report.json` — 정본 대조 실패 목록

산출물 목록과 교차 제약의 정본은 `contract/outputs.json` 이다.
