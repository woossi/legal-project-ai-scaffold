---
name: legal-decmap
description: Use when collecting 결정도·도면 첨부(fileRegistNo 2·3·4·5) from 택지정보시스템 — 이름 충돌로 기존 코퍼스가 덮이는 것을 막고 받을 값이 있는지 먼저 판정한다
---

# legal-decmap

`meta.json`을 기준으로 코드 7 본문 190건과 코드 2·3·4·5 도면 첨부 전건을 받았다
(2026-08-26: 코드 2 270건, 코드 3 167건, 코드 4 242건, 코드 5 226건). 이 스킬은
도면 수집 절차와 **받을 값이 있는지의 판정**을 소유한다.

**실행 코드와 `meta.json` 의 소유자는 `legal-dup` 이다.** 이 스킬은 고치지 않고 호출한다.

## 구성

- `contract/decmap.json` — 파일코드·이름 규약·불변식·선행조건·판정 기준. **정본**
- `references/수집절차.md` — API 사슬·지연·재개·실행 위치
- `references/common-mistakes.md` — 실패 유형과 원인
- `case/첨부현황.md` — 첨부 실측

## 실행

표본 판정을 건너뛰면 헛수고한다.

```bash
# 1. 표본 5~8지구로 형식·픽셀을 재고 값하는지 판정한다
python3 <main절대경로>/.claude/skills/legal/legal-dup/scripts/collect.py \
        fetch --region 경기 --file-type 5
# 2. 판정이 서면 전수. 3. 검증
python3 <main절대경로>/.claude/skills/legal/legal-dup/scripts/verify_contract.py
```

### 절대 규칙

- **이미 받은 190건의 `savedAs` 를 바꾸지 마라.** `tables.csv` 의 `출처문서` 가 참조한다
- **`ROOT` 가 `__file__` 기준이다.** 워크트리 사본을 돌리면 코퍼스가 갈라진다 — main
  절대경로로 실행하라
- 수집 규칙을 바꾸지 마라 — 공식 API 만, 순차, 다운로드 간격 2초, 고정 UA, 지수 백오프
- 작업 전후로 원본을 세어 보고에 적어라. **어간 `지구단위계획 시행지침.*` 으로 센다** —
  확장자로 세면 도면의 `.pdf`·`.zip` 이 섞여 190 을 넘는다
- 판독 병목은 축척이 아니라 **지구 면적 대비 픽셀**이다. 판정 지표와 폐기된 선행실측은
  계약이 정본이다

## output path

- `output/legal/시행지침/<지역>/<지구명>/` — 원본 바이트. `legal-dup` 계약을 따른다
- `output/legal/lot-zone-audit/` — 판독 판정 산출물
