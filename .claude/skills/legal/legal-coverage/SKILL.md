---
name: legal-coverage
description: Use when reporting 커버리지·달성률 across 수집→변환→추출→그래프 파이프라인, when a 비율 needs its 모수 fixed, or when 집계가 바뀌어 전후 대조표와 결론 유지 여부를 밝혀야 할 때
---

# legal-coverage

## 구성

- `scripts/build_coverage.py` — 단계별 커버리지를 원자료에서 재계산해 리포트를 낸다.
  `--baseline <이전 리포트>` 는 전후 대조표를 함께 낸다
- `scripts/verify_contract.py` — 계약 검증. 단계 사슬·분모·값 도메인·정본 실재를 검사한다.
  `--strict` 는 어긋남이 남아 있으면 실패로 돌린다
- `contract/outputs.json` — 산출물 목록·선행조건·멱등성·값 도메인·교차 제약·검증 루틴
- `contract/coverage.schema.json` — 리포트 구조 (JSON Schema)
- `case/집계절차.md` — **어떻게 세는가.** 분자 계수, 제외 시 수치, 재계산 필드 취급과
  각 절대 규칙이 필요했던 실측 근거. 규칙을 고치기 전에 읽는다
- `case/모수왜곡.md` — 저장소에서 실제로 확인한 분모 왜곡과 그 교정
- `references/common-mistakes.md` — 집계에서 실제로 데인 것

**모수 정의의 정본은 이 스킬이 아니다.** ubiquity 분모, `definiation.json` 재계산 필드,
OCR 취급의 정본은 `.claude/skills/legal/legal-term/case/모수규약.md` 이다. 이 스킬은
**집계 절차만** 담는다 — 두 곳이 같은 값을 정하면 반드시 어긋난다.

## 실행

입력은 각 팀의 산출물이며 원자료를 직접 읽는다. 선행 산출물이 없는 단계는 `미집계` 로
기록하고 계속 진행한다.

```bash
python3 scripts/build_coverage.py                             # 리포트 생성
python3 scripts/build_coverage.py --baseline <이전 리포트>     # 전후 대조표 포함
python3 scripts/verify_contract.py                            # 계약 검증
```

단계별 모수와 각 단계에서 분모가 바뀌는 이유는 `references/단계별-모수.md`이 정본이다. **모수를 섞으면 개별 수치가 맞아도 분포가 틀린다.**

### 절대 규칙

1. **비율을 적을 때는 분모를 함께 적는다.** 분모 없는 비율은 쓰지 않는다.
   대상 밖과 결손을 가른다 — 첨부가 없는 지구는 수집 실패가 아니다
2. **분모를 바꿔가며 결론이 유지되는지 함께 제시한다.** 분모 하나로만 적으면
   결론의 견고함을 보일 수 없다
3. **저신뢰·OCR 유래 건은 확정분과 섞어 집계하지 않는다.** 포함했으면 건수를 밝히고
   **제외 시 수치를 함께 낸다**
4. **집계는 원자료에서 재계산한다.** 남이 만든 `meta` 요약을 그대로 옮기지 않는다.
   **다른 파일의 동명 필드를 다시 센 값은 재계산 필드이며 나란히 비교하지 않는다** —
   판정 규칙이 다르면 어느 쪽이 틀린 것이 아니다
5. **못 센 것을 0으로 적지 않는다.** 입력이 없어 못 센 단계는 `미집계` 이며 실패가
   아니다. 게이트의 `미검사(skipped)` 와 같은 취급이다
6. **전후 대조표를 남긴다.** 집계가 바뀌면 `--baseline` 으로 무엇이 왜 바뀌었는지 내고,
   **"핵심 결론이 뒤집히는지"에 한 줄로 답한다.** 그 판단은 사람이 한다

집계 절차 — **어떻게 세는가와 각 규칙이 필요했던 실측 근거는 `case/집계절차.md` 가 정본이다.**
분자를 세는 방법, 제외 시 수치의 계산, 재계산 필드 취급, 어긋남의 범위를 다룬다.
규칙을 고치기 전에 읽는다.

**실측 수치를 이 문서에 박지 않는다.** 재실행하면 낡는다 — 현재 값의 정본은
`output/legal/analysis/_coverage.json` 이다.

## output path

- `output/legal/analysis/_coverage.json` — 단계별 커버리지 집계

리포트 파일(`_*.json`)이다. 여기 실린 미집계·격리 건수는 기록이지 결손이 아니다.
산출물 목록과 교차 제약의 정본은 `contract/outputs.json` 이다.
