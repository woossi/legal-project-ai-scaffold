---
name: legal-ocr
description: Use when converting collected OCR documents to text or Markdown in bulk
---

# 구성 (스킬 폴더 기준 상대 경로)

- `common-mistakes.md` — OCR 오인식 유형·패턴과 변형 규칙
- `../legal-textreform/case/_failed.json` — 실제 사례 (`OCR_적재`·`OCR_확인된_오인식`)
- `../legal-textreform/scripts/convert.py` — 변환 실행체. 이 스킬에는 스크립트 없음

# 실행

OCR 은 `tesseract 5.5.2` 와 `kor` 언어팩을 PSM 4 로 실행한다. 처리 속도는 쪽당 약 3초이며,
렌더링 1초와 OCR 2초로 나뉜다.

```bash
pdftoppm -r 300 -gray -png <입력.pdf> <출력접두사>
tesseract <쪽.png> <출력> -l kor --psm 4
```

- **시행 전** — 쪽당 글자수를 측정한다. 문서 앞·중간·뒤에서 균등하게 표본을 뽑는다
- **시행 중** — `추출품질: OCR` 플래그를 부착한다
- **시행 후** — 공백 정규화(`fold_padding`)를 적용한다. PDF 경로와 계약이 동일하다

문자 단위 검사는 읽기 순서 파손을 잡지 못한다. 실측 사례에서 오인식률 0% 인 문서가 2단
조판 좌우 열 뒤섞임으로 조문 순서가 파손되었다. 검사 통과를 안전 보증으로 보고하지 않는다.
검수 절차는 adversarial-review 스킬을 따른다.

# output path

- `output/legal/markdown/<지역>/<지구명>.md`
