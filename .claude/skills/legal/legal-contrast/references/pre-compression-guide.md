---
name: legal-contrast
description: Use when grouping 시행지침 into 대조쌍 by 시행시기·자치구·사업주체, measuring 규범 문장 변이 across 지구, or judging which 규칙 is 결정론적 versus 지구 재량
---

# 구성 (스킬 폴더 기준 상대 경로)

- `scripts/build_facets.py` — 지구별 축을 정규화하고 대조쌍을 만든다
- `scripts/extract_norms.py` — 시행지침 본문에서 규칙 슬롯과 규범 문장을 뽑아 군집한다
- `scripts/build_contrast.py` — 변이를 축에 귀속시키고 결정론 등급을 판정한다
- `scripts/verify_contract.py` — 계약 검증. `--full` 은 판정 재계산까지 수행한다
- `contract/outputs.json` — 산출물 목록·값 도메인·교차 제약·판정 파라미터
- `contract/contrast.schema.json` — 산출물 구조 (JSON Schema)
- `case/제52조-슬롯매핑.json` — 슬롯을 국토계획법 제52조 각 호에 매핑. 근거 판정의 정본
- `case/축설계.md` — 축을 고른 근거, Tier 정의, 기준선의 한계
- `common-mistakes.md` — 각 규칙을 제거했을 때 실제로 유입된 것

# 실행

입력은 `output/legal/markdown/` 의 지구별 병합 md 와 `output/legal/analysis/` 의
목차구조 전수조사 CSV 다. 순서를 지킨다 — `build_contrast.py` 가 `slots.json` 을
덮어쓰므로 `extract_norms.py` 를 먼저 돌린다.

```bash
python3 scripts/build_facets.py        # facets.json · pairs.json
python3 scripts/extract_norms.py       # slots.json · norms.json
python3 scripts/build_contrast.py      # determinism.json · variation.json · _extraction_gap.json
python3 scripts/verify_contract.py --full
```

- 대조쌍은 **대상축 하나만 다르고 통제축은 모두 같은** 지구 두 개다. 이 불변식이
  깨지면 변이를 축에 귀속시킨 결과 전체가 무의미해진다. 검증의 첫 항목이다
- Tier 는 통제 강도다. A 가 가장 엄격하고 아래로 갈수록 표본이 넓어진다. 대상축이
  자치구인 경우만 통제축 목록이 뒤집힌다 — 자치구를 광역으로 "완화" 하면 같은 광역
  안에서만 비교하게 되어 오히려 통제가 강해지기 때문이다
- 한 규범의 축별 불일치율은 **네 축이 같은 tier 를 써야** 견줄 수 있다. 같은 tier 로
  최소 유효 쌍을 함께 채우지 못하면 귀속을 판정하지 않고 `표본부족` 으로 둔다
- 규범 문장 군집은 슬롯을 가리지 않고 전역으로 묶는다. 같은 규범이 지구마다 다른
  조문 아래 놓이므로 슬롯별로 나눠 세면 공유 지구 수가 과소평가된다
- 군집 병합은 star 방식이다. 연결요소로 묶으면 A~B, B~C 가 유사하다는 이유만으로
  A 와 C 가 한 군집에 들어가 서로 다른 규범이 뭉개진다
- 문언 동일성 기준은 `legal-term` 의 `definition_variance` 와 같다 — 수치 서명이
  같고 유사도 0.70 이상이면 같은 규범의 표현차이로 본다. 두 산출물이 다른 기준을
  쓰면 결과를 나란히 놓을 수 없다

# 판정 구조

등급은 두 축의 교차다. 하나로 합치면 "법정 필수인데 값은 지구마다 다른 것"을 놓을
자리가 없어진다 — 건폐율·용적률이 그렇다.

| | 축귀속변이 | 축무관변이 | 지구고유 |
|---|---|---|---|
| **법령근거** | 그 축이 규정을 가른다 | 법정 사항이나 문언은 재량 | 해당 지구 고유 규정 |
| **지침총칙** | 지침 관행이 축에 따라 갈림 | 관행 재량 | — |
| **근거없음** | 축 종속 관행 | 순수 재량 | — |

세로축은 `case/제52조-슬롯매핑.json` 으로 1차 판정하고, 매핑되지 않으면 본문 인용
법령으로 2차 판정한다. 가로축은 축별 불일치율을 서로 견줘 판정한다.

**보유율에 임계를 걸어 "불변" 등급을 만들지 않는다.** 실측상 최다 규범이 105/181
(0.580) 이므로 전 지구 보편 규범은 존재하지 않는다 — 임계 0.75 인 `준보편` 구간은
비어 있다. 구간(`준보편`·`다수`·`소수`·`희소`)은 그대로 기록한다.

이 실측치는 정의조항 절삭 후 md 기준이다. 절삭 전에는 최다가 144/181 (0.796) 이었다.
차이는 `제N조(용어의 정의)` 구간이 통째로 걷히면서 그 안에 있던 해석규칙 문장까지
함께 사라진 결과다 — 정의문이 아니라서 이 스크립트의 정의문 제외 규칙에는 걸리지
않던 문장이다.

법정 필수인 제2호·제4호의 미검출은 `_extraction_gap.json` 에 남긴다.
`.claude/rules/지구단위계획-법정구조.md` 가 정한 대로, 규정이 없는 것이 아니라 추출이
실패한 것으로 본다. 갭 판정은 **호 수준**에서 한다 — 슬롯 단위로 세면 한 지구에만
있는 세부 조문이 전부 누락으로 잡힌다.

# output path

- `output/legal/contrast/{facets,pairs,slots,norms,determinism,variation}.json`
- `output/legal/contrast/_extraction_gap.json` — 법정 필수 호 미검출 지구

산출물 목록과 교차 제약의 정본은 `contract/outputs.json` 이다.
