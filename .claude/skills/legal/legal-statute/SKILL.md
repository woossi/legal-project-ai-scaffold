---
name: legal-statute
description: Use when extracting 상위법령 인용 from 시행지침 정의문 into 법령 노드·인용 간선, when 법령 정본을 법제처 OPEN API 로 수집하여 정식명칭·법령ID·시행일을 확정할 때, or when 건폐율·용적률 규범값의 근거 계통을 다룰 때
---

# legal-statute

## 구성

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
- `scripts/build_guideline_hang_structure.py` — `비조문형_전문` 훈령의 전문을 장·절·항
  단위로 자른다. 경계는 원문에 실재하는 표지로만 정하고 표·그림 채널과 유실의심을
  판정 필드로 분리한다
- `scripts/verify_hang_numbering.py` — 줄머리 번호 형식을 독립 경로로 검증한다.
  `--survey` 는 프로파일 없는 문서의 shape 인벤토리만 내고 역할은 비워 둔다
- `scripts/build_guideline_norm_units.py` — 항 본문에서 규범 단위를 뽑는다. 머리↔목의
  열거 관계, 문말 표현, 수치의 맥락, 교차참조를 단위마다 붙인다. 번호는 주소일 뿐이고
  지침이 말하려는 것은 본문에 있다
- `scripts/collect_statutes.py` — 법제처 OPEN API 에서 법령 정본을 조회해 법령 노드를
  만든다. 법령·자치법규·행정규칙 세 대상을 꼬리로 가려 순서대로 시도한다
- `scripts/build_norm_basis.py` — 규범값 근거 계통을 산출물로 옮긴다
- `scripts/collect_articles.py` — 규범값 계통 조문의 본문을 법제처 API 에서 받는다
- `scripts/collect_history.py` — 정본대조 법령의 시행 이력을 받고, 지구번호 연도 proxy 는
  별도 신선도 관측으로 격리한다
- `scripts/verify_statute.py` — 계약 검증. `--full` 은 인용 표기가 근거 문장에 실재하는지
  대조한다
- `contract/outputs.json` — 산출물 목록·교차 제약·검증 루틴
- `contract/hang_numbering.json` — 비조문형 전문의 줄머리 번호 형식 계약. shape 값
  도메인·게이트 9종·문서별 프로파일. 형식을 상수로 박지 않는 자리다
- `contract/statute_citations.schema.json` — 인용 간선 구조 (JSON Schema)
- `contract/statute_master.schema.json` — 법령 정본 노드 구조 (JSON Schema)
- `case/정본대조.json` — 규범값 근거 계통, 명칭 변천, 표기 교정, 범위 검증 표적 사례.
  근거를 확인한 것만 담는다
- `references/법정구조-출처.md` — 법정구조 대조 판본·조회 조문·원문 URL. 판본 근거를
  인용하거나 갱신하기 전에 읽는다
- `references/계획규범-내용구조.md` — 계획규범 한 건의 내부 구성 9축과 문헌 근거.
  국내 문헌 실물은 `output/legal/papers/`(서지 정본은 `_sources.json`)이고 3편은
  1쪽 미리보기뿐이다. 서양 정전은 저장소에 없으니 인용 전에 확인한다

## 실행

범위 감사 입력은 `output/legal/markdown/` 189개 전건이고, 기존 정의문 인용 간선의 입력은
`output/legal/word/terms.json` 이다. 갱신한 뒤에는 매번 검증한다.
범위 생성·수집·검증에서는 먼저 `case/정본대조.json`의 `옥외광고물_수집범위`를 읽는다.

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

### 비조문형 전문의 항 구조 — 번호 형식 검증 절차

`비조문형_전문` 문서는 조문(제N조) 체계가 아니라 줄머리 번호 체계를 쓴다. 그 형식은
문서마다 다르므로 **파싱 전에 형식을 먼저 판정한다.** 실측상 corpus 의 비조문형 9건 중
x-y-z 3단이 지배하는 것은 3건뿐이고, 도시·군관리계획수립지침은 4단이 359건,
도로안전시설 지침은 `N.` 319건과 가나다목 282건이다. 3단을 상수로 두면 대부분을 놓친다.

```bash
# 1. 형식 실측. 역할은 비운 채로 shape 인벤토리만 나온다
python3 scripts/verify_hang_numbering.py --survey

# 2. 대표 줄을 원문에서 열어 shape 마다 역할을 정하고
#    contract/hang_numbering.json 의 문서프로파일에 선언한다

# 3. 파싱
python3 scripts/build_guideline_hang_structure.py

# 4. 게이트 9종. 하나라도 붉으면 산출물을 쓰지 않는다
python3 scripts/verify_hang_numbering.py --document-key <키>

# 5. 규범 단위 추출. 구조가 아니라 지침이 말하는 내용이 여기 있다
python3 scripts/build_guideline_norm_units.py
```

2단계를 건너뛰지 않는다. `--survey` 가 역할을 비워 내는 것은 판정을 미룬 것이며,
최빈 shape 이 항이라는 보장이 없다. 검증기는 생성기를 import 하지 않고 corpus
원자료에서 shape 을 다시 산출한다 — 같은 스크립트가 만든 두 파일의 일치는 검증이 아니다.

게이트 정의와 실패 조건의 정본은 `contract/hang_numbering.json` 이다.

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

### 절대 규칙

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
10. **번호 형식을 상수로 박지 않는다.** 비조문형 전문의 줄머리 형식은 문서마다 다르다.
    `--survey` 로 shape 을 실측하고 프로파일에 선언한 뒤에 파싱한다. 미선언 shape 은
    빈도가 낮아도 버리지 않고 격리한다. 전각 하이픈·전각 마침표를 도메인에서 빼면
    `１－１－１．` 이 본문으로 위장해 조용히 사라진다

판정 규칙(정본 판본 선택·조문 범위·대조 상태)과 검증 게이트의 정본은
`references/판정규칙-검증게이트.md` 다. 규칙을 고치기 전에 읽는다.

## output path

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
- `output/legal/statute/수립지침_항구조.json` — 수립지침 훈령 전문의 장·절·항 구조
- `output/legal/statute/_수립지침_파싱_리포트.json` — 커버리지·연속성·채널·격리
- `output/legal/statute/_수립지침_번호형식_검증.json` — 번호 형식 게이트 9종 결과
- `output/legal/statute/_비조문형_번호형식_실측.json` — 비조문형 9건의 shape 인벤토리
- `output/legal/statute/수립지침_규범단위.json` — 항 본문의 규범 단위 893건
- `output/legal/statute/_수립지침_규범단위_리포트.json` — 서법 분포·값 맥락·커버리지

산출물 목록과 교차 제약의 정본은 `contract/outputs.json` 이다.
