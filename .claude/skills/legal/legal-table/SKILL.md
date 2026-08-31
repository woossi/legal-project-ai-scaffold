---
name: legal-table
description: Use when extracting 건폐율·용적률 values and table-loss evidence from 시행지침 markdown.
---

# legal-table

시행지침 md 189건에서 건폐율·용적률 값을 출처와 함께 뽑고, **뽑히지 않은 것이 왜 없는지를
지구 단위로 계량한다.** 값 추출과 소실 계량은 한 쌍이다 — 뒤가 없으면 앞의 커버리지가
실패로 읽힌다. 설계 정본은
`docs/adr/0013-값추출-재변환-금지.md` 이고
**값 도메인·교차 제약의 정본은 `contract/` 다.**

## 구성

| 파일 | 정본으로 담는 것 |
|---|---|
| `scripts/table_common.py` | md 파싱·조문 트리·표 참조 탐지 (라이브러리) |
| `scripts/scan_table_loss.py` | `_table_loss.json` |
| `scripts/extract_values.py` | `norm_values.json`·`_norm_value_report.json` |
| `scripts/value_relation.py` | 관계 필드 발급·미발급 판정표 (라이브러리) |
| `scripts/extract_gazette.py` | `gazette_refs.json` |
| `scripts/build_value_index.py` | `value_index.json` |
| `scripts/build_retransform_estimate.py` | 통합 재변환 견적서 |
| `scripts/analyze_subject_gap.py` | 주어 미상 원인·회수 견적 |
| `scripts/verify_contract.py` | 계약 검증 (구조 + 교차 제약 + 멱등성) |
| `contract/outputs.json` | 선행조건·멱등성·값 도메인·교차 제약 |
| `contract/*.schema.json` | `table_loss`·`norm_value`·`gazette_ref` 구조 |
| `references/판정-값도메인.md` | **판정 도메인·관계 필드 근거 요건의 정본** |
| `references/검증-게이트.md` | 게이트 58개가 무엇을 표적으로 삼는가 |
| `references/견적-절차.md` | 재변환 견적·주어 미상 견적 |
| `references/실측.md` | 회차별 실측과 예시 도해 |
| `references/common-mistakes.md` | 산출물에 실제로 들어왔던 오판정 |

`legal-xref/scripts/xref_common.py` 에 같은 성격의 파서가 있다. **규약은 따르되 import 하지
않는다** — 스킬 간 런타임 의존을 만들면 한쪽 갱신이 다른 쪽을 깨뜨린다.

## 실행

```bash
python3 .claude/skills/legal/legal-table/scripts/extract_values.py --root .
python3 .claude/skills/legal/legal-table/scripts/extract_gazette.py --root .
python3 .claude/skills/legal/legal-table/scripts/build_value_index.py --root .
python3 .claude/skills/legal/legal-table/scripts/scan_table_loss.py --root .
python3 .claude/skills/legal/legal-table/scripts/build_retransform_estimate.py --root .
python3 .claude/skills/legal/legal-table/scripts/analyze_subject_gap.py --root .
python3 .claude/skills/legal/legal-table/scripts/verify_contract.py --root .
```

**순서가 있다.** `build_value_index.py` 는 `norm_values.json` 을, `scan_table_loss.py`
는 `확정값수` 를 채우려고 `norm_values.json` 을 읽는다. 없으면 `확정값수` 는 전건
`null` 이 되며 **그 null 은 미산출이지 0 이 아니다** — 값 0 인 지구(표가 소실돼
회수된 규범값이 없다)와 구분해 읽는다.

**계약 검증 통과가 완료 조건이다.** 58개 게이트를 본다 — 표 소실 21, 값 추출 12, 예시 도해 2,
인용서술 2, 관계 필드 6, 통합 견적 8, 주어 미상 견적 7. 각 게이트의 표적과 위반을 심어
확인한 결과는 `references/검증-게이트.md` 가 정본이다. 공통 원칙 둘만 여기 남긴다.

- **줄번호·`surface`·인용문은 md 원문을 다시 열어 대조한다.** 같은 스크립트가 만든 두 필드가
  맞는 것은 자기 일관성이지 검증이 아니다
- **검증기는 위반을 만나면 실패를 보고해야지 죽으면 안 된다.** 게이트 14종에 위반을 심는
  시험에서 게이트 21이 `relation_basis` null 에 예외로 죽는 결함이 드러났다

값 추출 규약(비교표현·다중값·지표 귀속·용지 매핑·고시번호)은 `references/판정-값도메인.md` 가 정본이다.

### 줄번호 규약 — 파일 기준 1-based

**모든 `line` 은 md 파일의 물리적 줄번호다.** frontmatter 를 잘라낸 body 오프셋을
쓰지 않는다. frontmatter 길이는 원본구성 항목 수에 비례해 문서마다 다르므로, body
오프셋을 쓰면 **집계는 맞는데 개별 출처가 전부 어긋나는** 형태로 조용히 남는다.

`table_common.parse_document` 는 파일 전체를 줄 배열로 읽고 frontmatter 구간을
**건너뛰되 인덱스를 유지한다.** `split` 으로 제거하지 않는다.

커버리지 분모는 필드마다 다르다. 분모별 수와 포함관계는 `references/실측.md` 가 정본이며, **섞으면 커버리지가 두 배 넘게 부풀려진다.**

관계 필드는 값 추출과 근거 요건이 다르다 — 원문이 그렇게 적었을 때만 발급한다. 판정표는 `references/판정-값도메인.md` 다.

산출물에 실제로 들어왔던 오판정과 그것을 막는 규약 7개는 `references/common-mistakes.md` 가 정본이다. 규칙을 넓히기 전에 읽는다.

## output path

```
output/legal/table/
  _table_loss.json          표 소실 계량 · 재변환 견적서
  norm_values.json          확정 값 레코드
  _norm_value_report.json   격리 + 관계 필드. **하위 축은 반드시 함께 읽는다**
  _subject_gap.json    주어 미상 원인 분해 · 회수 견적
  _subject_gap.md      위 JSON 의 사람이 읽는 요약
```

`_` 접두는 리포트 파일 규약이다 — 그래프·산출물에 들어가지 못한 것과 그 사유를
남기는 자리이며 결손이 아니라 기록이다.

`_codex_article_integrity.json` 이 같은 경로에 놓이면 `scan_table_loss.py` 가
`dstrcAppnNo` 로 join 해 `article_integrity_class` 를 채운다. 없으면 `null` 이다.
