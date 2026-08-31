---
name: kb-axis-value
description: Use when extracting 규범값 for 위임축 3~16 from 지자체 조례 — 축별 주어·값 타입 선언, 축별 산출물, 못 뽑는 축의 사유 기록
---

# kb-axis-value

> **재설계 중 (2026-08-25).** `output/kb/` 산출물이 전부 삭제됐다. 축별 추출 절차와
> 계약은 유효하나 산출 경로는 새 3계층 체계에서 다시 정한다.
> 상태는 `.claude/rules/계획규범요소-틀.md`가 정본이다.

## 구성

- `scripts/axis_engine.py` — 주어·값 매처. 순수 함수만 두고 파일을 읽지 않는다
- `scripts/build_axis_values.py` — 축 루프. 축별 TTL·축별 격리·커버리지 (멱등)
- `contract/axis_spec.json` — **축 14개 명세의 정본.** 축을 더할 때 여기만 고친다
- `contract/axis_value.json` — 값 도메인·격리사유·교차제약·qa 검사항목
- `case/t_engine.py` — 실측 회귀 42건. 파서를 고치면 먼저 돌린다
- `references/README.md` — common-mistakes 색인. 축별 파일로 갈려 있다

건폐율·용적률은 이 스킬의 범위가 아니다 — `kb-norm` 이 정본이며 두 곳이 같은 값을
정하면 반드시 어긋난다. `corpus.py`·`parse_ordinance.py` 도 kb-norm 것을 그대로 쓴다.

## 실행

```bash
.venv/bin/python3 .claude/skills/kb/kb-axis-value/scripts/build_axis_values.py
.venv/bin/python3 .claude/skills/kb/kb-axis-value/scripts/build_axis_values.py --axis landscaping
.venv/bin/python3 .claude/skills/kb/kb-axis-value/case/t_engine.py
```

**축을 추가하는 것은 `axis_spec.json` 에 항목을 더하는 일이지 코드를 쓰는 일이 아니다.**
주어타입·값타입이 계약의 값 도메인 안에 있으면 엔진이 그대로 돌린다. 도메인 밖 타입이
필요하면 계약을 먼저 고치고 엔진에 매처를 더한다.

- **주어와 값 타입은 축이 아니라 (축, 근거항) 단위로 선언한다.** 공개공지 제1항은
  용도목록이고 제2항은 면적비율이다. 축 단위로 매기면 제1항의 열거가 값으로 둔갑한다
- **근거항이 없으면 조 단위로 구제하지 않는다.** kb-norm 은 단항 조문을 조 단위 판정으로
  구제하지만 여기서는 안 된다 — 항마다 값타입이 갈리기 때문이다
- **못 뽑는 축은 사유를 코드가 확인한다.** `별표결손_corpus` 는 그 축의 근거항을 인용하는
  **항에서** 별표 참조를 찾고, 그 별표가 corpus 에 없음을 확인해 근거 발췌를 남긴다.
  조문 전체를 훑거나 타법령 별표를 세면 근거가 무너진다
- **세분 주어를 군으로 접지 않는다.** `전용주거지역` 을 `주거지역` 으로 접으면 서로 다른
  규범이 한 명제로 합쳐진다. 실측 1건이 거짓 명제였다
- **원문에 없는 비교연산자를 채우지 않는다.** kb-norm 과 같은 규약이다 —
  `lp:비교연산` 과 `lp:비교연산_미표기` 는 배타적이다
- **값이 0건인 축에 빈 TTL 을 남기지 않는다.** 빈 그래프는 값이 0인 것과 축이 대상이
  아닌 것을 구분하지 못한다. 사유는 `_axis_coverage.json` 에 남는다
- **커버리지 분모를 하나만 적지 않는다.** 전체축 16 · 값가능축 · 명제산출축 셋을 각각 낸다

qa-verifier 가 검사할 항목은 `references/검사기-함정.md` 가 정본이다.

## output path

- `output/kb/norm/graph/det/norm-value/{슬러그}.ttl` — 값이 1건 이상인 축만
- `output/kb/norm/reports/_norm_value/{슬러그}.json` — 축별 격리와 별표결손
- `output/kb/norm/reports/_axis_coverage.json` — 축 14개 전부 + 값없음사유 + 분모 3종
- `output/kb/norm/graph/det/norm-value.ttl`(건폐율·용적률)은 **읽지도 쓰지도 않는다**

슬러그는 `axis_spec.json` 의 `슬러그` 필드가 정본이다. 코드가 축명에서 만들지 않는다 —
축 번호를 파일명에 넣지 않은 것은 축 순서가 바뀔 때 파일이 통째로 옮겨지지 않게 하기
위해서이며, 기존 2축이 나중에 갈릴 때도 이 레이아웃을 그대로 쓴다.

산출물 계약의 정본은 `contract/axis_spec.json` 과 `contract/axis_value.json` 이다.
