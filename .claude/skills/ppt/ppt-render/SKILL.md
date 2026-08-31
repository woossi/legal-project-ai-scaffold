---
name: ppt-render
description: Use when building `02_deck.json` into deck.pptx — python-pptx 렌더, 미리보기 PNG·PDF 산출, 빌드 리포트. 빌드까지만 소유하며 합격 판정은 하지 않는다. 검증은 ppt-verify 가 맡는다
---

# ppt-render

## 구성

- `scripts/build_deck.py` — `02_deck.json` → `build/deck.pptx`.
  `build(spec_path, out_dir) -> report` 와 `main()` 을 분리한다
- `contract/build.schema.json` — `_build_report.json` 의 구조 계약
- `contract/outputs.json` — `build/` 산출물의 선행조건·멱등성·값 도메인·교차 제약
- `references/렌더-함정.md` — python-pptx 에서 데인 곳
- **검증기를 두지 않는다.** 합격·불합격 판정은 `ppt-verify` 의 소유다.
  이 스킬은 빌드 사실(무엇을 만들었고 폰트가 대체됐는지)만 자기보고한다

## 절대 규칙

### 1. import 부작용을 만들지 않는다

모듈 최상위에서 `Presentation()` 을 만들거나 슬라이드를 그리거나 파일을 저장하지 않는다.
**전부 함수 안이다.** 최상위에는 상수와 `def` 만 둔다.

이 저장소의 `tools/viz/build_intro_ppt.py` 가 반대 사례다 — 135행에서 최상위
`prs = Presentation()` 후 곧장 슬라이드를 그리며 `__main__` 가드가 없다.
**import 하는 순간 실행된다. 재사용하지 않는다.** 그 파일의 헬퍼(`tb`·`rect`·
`stat_tile`·`chip`·`pic_fit`)는 발상만 참고하고 옮겨 쓰려면 새로 짠다.

### 2. 색·폰트·좌표를 하드코딩하지 않는다

프로필에서 읽는다. `02_deck.json` 의 `profile` 이 `docs-ppt-20260813` 이면
`docs/ppt/design-tokens.json` 을, `none` 이면 렌더러 기본 프로필을 쓴다.
새 강조색을 임의로 추가하지 않는다.

### 3. 한글 폰트를 확인한다

폰트가 없으면 한글이 네모(□)로 깨진다. 지정 폰트가 시스템에 없으면
**폴백 사실을 `_build_report.json` 의 `font.fallback` 에 남긴다. 조용히 대체하지 않는다.**

`KoPubWorldDotum` 미설치 시 폴백 후보는 `Apple SD Gothic Neo`·`NanumGothic`.

### 4. 렌더 후 실제로 열어 본다

빌드가 오류 없이 끝나는 것과 글자가 보이는 것은 다르다. 미리보기 PNG 를 만들고
**적어도 표지와 본문 한 장을 눈으로 확인한다.** 확인 결과를 `visual_check` 에 적는다.
자동 검사와 다른 층이며 `ppt-verify` 가 대신해 주지 않는다.

### 5. 상류를 고치지 않는다

`02_deck.json` 의 텍스트가 길어 넘치면 **스펙을 줄이지 말고 반려한다.**
넘침은 `ppt-writer` 가 해결할 문제다. 렌더러가 임의로 잘라내면 근거가 소리 없이 사라진다.

### 6. 합격을 선언하지 않는다

빌드 성공은 합격이 아니다. `_build_report.json` 에 통과·실패 집계를 두지 않으며
`03_qa.*` 를 쓰지 않는다. 빌드가 끝나면 `ppt-verify` 로 넘긴다.

## 실행

1. `02_deck.json` 을 `ppt-deck/contract/deck.schema.json` 으로 먼저 검사한다.
   깨져 있으면 빌드하지 않고 반려한다. 이 검사는 **빌드 가능성 확인**이지 합격 판정이 아니다.
2. `build_deck.py` 로 빌드한다.
   표 자산은 머리글을 포함해 11행·6열 이하만 허용한다. 초과 표는 자르지 않고 반려한다.
   `slides[].stage` 가 있으면 기준 덱의 6단계 여정 레일을 같은 순서와 색 의미로 렌더한다.
   `level: 1`의 `body[].kind`는 `• <kind> — <text>`로 렌더하고, `level: 2`는 들여쓴
   `◦ <text>`로 렌더한다. 표지는 기본 파랑 색면을 사용한다. 본문은 배경 박스 없이
   제목 아래의 문단으로 직접 배치한다.
3. 미리보기 PNG 를 슬라이드 수만큼 낸다.
4. PDF 를 낸다. LibreOffice(`soffice`) 가 없으면 PDF 를 만들지 않고
   `artifacts.pdf` 를 `null` 로 두며 사유를 `notes` 에 남긴다.
5. 눈으로 확인한다 (규칙 4). `visual_check` 를 채운다.
6. 보고: 슬라이드 수, 폰트 폴백 여부, PDF 생성 여부와 사유, 실제로 본 슬라이드 번호.
   **합격 여부는 말하지 않는다.**

## output path

`output/ppt/<deck-id>/build/` 아래만 쓴다.

```
build/deck.pptx · deck.pdf · previews/sNN.png · _build_report.json
```

`01_source.json`·`02_deck.json`·`03_qa.*` 를 건드리지 않는다.
`docs/ppt/` 정본은 **읽기 전용**이다 — 사용자가 최종 반영을 따로 지시할 때만 갱신한다.
