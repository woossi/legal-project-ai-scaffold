---
name: legal-textreform
description: Use when converting collected PDF/HWP documents to text or Markdown in bulk
---

# legal-textreform

## 구성

- `scripts/convert.py` — 변환 실행. 품질 게이트를 내장한다. pdftotext·hwp5txt 직접 호출 금지
- `scripts/verify.py` — 산출물 품질 게이트. convert.py 가 같은 판정기를 쓴다
- `scripts/merge_md.py` — 지구별 md 병합 → `output/legal/markdown/`
- `scripts/promote_jomun.py` — 미승격 조문 표제를 `####` 헤딩으로 승격. 기본은 보고만, `--apply` 로 적용
- `scripts/strip_definitions.py` — 용어 정의 조항 구간 제외. 기본은 보고만, `--apply` 로 적용
- `scripts/slim_md.py` — 정본이 다른 산출물에 있는 구간 절삭과 압축. 기본은 보고만, `--apply` 로 적용
- `scripts/verify_contract.py` — 병합 md 의 frontmatter 계약 검증
- `contract/frontmatter.json` — md frontmatter 필드·어휘·본문 마크다운 규칙
- `contract/markdown-corpus.json` — 승인된 코퍼스의 지구번호 기대 집합. 하류 문서 분모의 정본
- `references/common-mistakes.md` — 실패 유형·게이트 예외 규칙
- `case/_failed.json` — 실패·정상스킵·게이트예외·OCR 실제 사례. legal-ocr 스킬과 공용이다
- `case/변환품질-판정기준.md` — 품질 게이트 지표의 근거, 병합 md 의 알려진 부작용
- `case/재변환-판정기준.md` — 구조 소실 문서의 재변환 회수 가능성 판정 절차
- `case/정의조항제외.md` — 용어 정의 조항 구간의 경계 판정과 커버리지
- `case/절삭규칙.md` — 절삭 단계별 대상·정본, 표 블록 경계 판정, 검증 게이트

## 실행

```bash
python3 scripts/convert.py <입력> -o <출력>
python3 scripts/verify.py <txt디렉터리> --src-dir <원본디렉터리>   # 변환 품질 게이트
python3 scripts/merge_md.py                                     # 지구별 md 병합
python3 scripts/promote_jomun.py                                # 조문 표제 승격 (보고만)
python3 scripts/promote_jomun.py --apply --report <경로>         # 실제 승격
python3 scripts/slim_md.py                                      # 절삭·압축 (보고만)
python3 scripts/slim_md.py --apply --report <경로>               # 실제 절삭
python3 scripts/verify_contract.py                              # 병합 md 계약
python3 scripts/strip_definitions.py                            # 정의 조항 제외 (보고만)
python3 scripts/strip_definitions.py --apply -o <출력>           # 실제 제외
```

- 두 검증은 대상이 다르다. `verify.py` 는 변환 품질(글자 유실·조판 붕괴)을 보고,
  `verify_contract.py` 는 병합 산출물의 구조 계약을 본다. 둘 다 통과해야 완료된다
- `merge_md.py` 를 다시 실행하면 승격과 절삭이 모두 지워진다. 둘 다 병합 md 의
  후처리이므로 병합, 승격, 절삭, 계약검증 순서를 지킨다
- 임계값의 정본은 `scripts/verify.py` 상단 상수이다
- rc·파일 존재·글자수로 성공을 판정하지 않는다. 원본 도구는 실패할 때도 종료코드 0 을
  반환한다
- 게이트 예외를 적용할 때는 면제 지표명, 사유, 나머지 지표의 통과 수치를
  `case/_failed.json` 의 `게이트예외적용` 에 기록한다
- 스캔본 OCR 은 legal-ocr 스킬을 따른다. PDF 경로와 동일한 `fold_padding` 을 적용한다
- 재변환은 하지 않기로 확정하였다. 38건을 진단한 결과 기대 회수는 0건이다. 근거는
  `case/재변환-판정기준.md` 에 있다
- 용어 정의 조항 구간을 제외할 때의 경계 판정 규칙은 `case/정의조항제외.md` 에 있다
- 절삭 단계와 표 블록 경계 판정 규칙은 `case/절삭규칙.md` 에 있다

조문 표제 승격 규칙과 그 부작용은 `references/조문표제-승격.md`이 정본이다. 구현은 `scripts/promote_jomun.py` 다.

## output path

- `output/legal/markdown/<지역>/<지구명>.md`
- `output/legal/word/_definition_strip_report.json` — 정의 조항 제외 구간 보고
- `output/legal/analysis/조문표제_승격_리포트.json` — 승격·배제 판정 내역
- `output/legal/analysis/_md_slim_report.json` — 절삭 단계별 위치·행수·글자수. 내용은 담지 않는다
