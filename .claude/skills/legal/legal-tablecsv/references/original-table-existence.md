# 원본 표 구조 실재 근거

이 문서는 `legal-tablecsv` 1층 추출기가 원본 HWP/PDF 바이너리에서 표 구조를 직접 회수할 수
있다는 Task 5 표본 근거다. 구조 개수는 정확도나 규범표 개수가 아니다. 예시도, 레이아웃
표, 목차 표도 원본 보존층에서는 구조 후보로 남을 수 있다.

## 확인 명령

```bash
/opt/homebrew/bin/python3 .claude/skills/legal/legal-tablecsv/scripts/extract_tables.py --root . --district '의정부우정 공공주택지구' --district '하남미사 공공주택지구' --district '남양주왕숙 공공주택지구' --output /private/tmp/legal-tablecsv-task5-cases-round1
```

결과: `tables=701`, `cells=12796`, `errors=0`.

해시:

| 파일 | SHA-256 |
|---|---|
| `tables.csv` | `80ad0cac5cef11dddbd4115c1724eea016c57f7bc2b51ddc739c3fa0947c133b` |
| `cells.csv` | `2ce160bcc3c45d5906c110856e6cf0a940ef078b43adf0e75b18cfc607c031ae` |
| `_extraction_report.json` | `4a7c952bfb644af88795f8e242853081e1698dcf99d94eb70221ae2e97571c8c` |

## 표본

| 지구 | 문서 | 형식 | `_table_loss.json` 소실표건수 | 구조 개수 | 셀 개수 | 대표 셀 |
|---|---|---|---:|---:|---:|---|
| 의정부우정 공공주택지구 | `지구단위계획 시행지침.hwp` | HWP | 193 | 190 | 1,637 | `41150LH2019001-0-0-12` (1,0) `공동주택용지`, (1,1) `35% 이상` |
| 하남미사 공공주택지구 | `지구단위계획 시행지침.hwp` | HWP | 181 | 146 | 2,937 | `41450KH2009001-0-0-13` (0,3) `R1-2`, (2,3) `60% 이하`, (2,4) `200% 이하` |
| 남양주왕숙 공공주택지구 | `[본단지]...4차 승인 .pdf` | PDF-in-ZIP | 291 | 296 | 7,029 | `41360MX2019001-0-6-0` (0,0) `구 분` |
| 남양주왕숙 공공주택지구 | `[기업이전단지]...2차 승인 .pdf` | PDF-in-ZIP | 291 | 69 | 1,193 | `41360MX2019001-1-7-0` (0,0) `③ ① 주체 ②...` |

## 해석 한계

- HWP `쪽=0`은 물리 쪽 미상 sentinel이며 오류가 아니다.
- PDF `쪽`은 원문 기준 1-기반이다.
- `_table_loss.json`의 소실표건수는 markdown 변환 산출물의 표 소실 증상이다. 원본 구조
  추출기의 구조 개수와 같은 분모로 비교하지 않는다.
- HTML `<table>` 태그 수나 pdfplumber 후보 수는 정확도, 완전성, 규범성의 근거가 아니다.
- 1층은 예시도와 규범성을 판정하지 않는다. 헤더 분류가 보수 규칙에 걸리지 않으면
  `법령계통=미분류`, `정규화대상=없음`으로 둔다.

## 표본별 재확인 명령

```bash
/opt/homebrew/bin/python3 .claude/skills/legal/legal-tablecsv/scripts/extract_tables.py --root . --district '의정부우정 공공주택지구' --output /private/tmp/legal-tablecsv-task5-uijeongbu
/opt/homebrew/bin/python3 .claude/skills/legal/legal-tablecsv/scripts/extract_tables.py --root . --district '하남미사 공공주택지구' --output /private/tmp/legal-tablecsv-task5-hanam
/opt/homebrew/bin/python3 .claude/skills/legal/legal-tablecsv/scripts/extract_tables.py --root . --district '남양주왕숙 공공주택지구' --output /private/tmp/legal-tablecsv-task5-namyangju
```

## 추출 판정

- HWP 셀은 `hwp5html`, PDF 셀은 `pdfplumber` 로 추출한다. `hwp5txt` 표 마커는 구조 추출
  입력이 아니다
- 이미지 표만 OCR 로 회수한다. **`추출경로`(`hwp5html|pdfplumber|OCR`)와 `품질등급`
  (`텍스트|OCR`)은 별도 축이다** — 한쪽으로 다른 쪽을 추론하지 않는다
- PDF `쪽` 은 1-기반이며 HWP 는 물리 쪽 미상 sentinel `0` 이다
- 표와 셀을 빠짐없이 보존하고 **병합 빈칸을 채우거나 줄바꿈을 추정하지 않는다**
- 캡션 원문·위치(`위|아래|없음`)·표 직전 heading 을 원문 그대로 기록한다. 캡션 열린
  구간은 가장 이른 heading·다음 캡션·조문 항·연속 빈 줄 둘에서 끝낸다. **고정된 이전
  N줄 창을 쓰지 않는다**
