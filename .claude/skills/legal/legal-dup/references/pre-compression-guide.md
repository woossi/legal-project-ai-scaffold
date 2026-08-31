---
name: legal-dup
description: Use when collecting 지구단위계획시행지침, 토지이용계획도, 위치도, or other 택지개발지구 attachments from 택지정보시스템(map.jigu.go.kr)
---

# 구성 (스킬 폴더 기준 상대 경로)

- `scripts/collect.py` — 수집 실행. 지연·순차·백오프·재개 내장. API 직접 호출 금지
- `scripts/meta_store.py` — 통합 `meta.json` 의 로드·병합·집계. 수집과 검증이 함께 쓴다
- `scripts/verify_contract.py` — 산출물 계약 검증. 종료코드 0=충족, 1=위반
- `contract/commands.json` — 서브커맨드별 선행조건·멱등성·재개 규칙
- `contract/{index,meta}.schema.json` — `_index.json`·`meta.json` 구조 (JSON Schema)
- `common-mistakes.md` — 실패 유형과 대응
- `case/reference.md` — 엔드포인트·시도 코드·`fileRegistNo`·실측치

# 실행

```bash
python3 scripts/collect.py index --region 서울                     # fetch의 선행 조건
python3 scripts/collect.py fetch --region 서울 --file-type 7        # 시행지침만
python3 scripts/collect.py fetch --region 경기 --file-type 2,4,5,7  # 도면 포함
python3 scripts/verify_contract.py [--region 인천]                  # 수집 후 검증
```

- `fetch` 는 같은 지역의 `index` 실행을 선행 조건으로 요구한다. 중단된 경우 같은 명령을
  다시 실행하면 재개된다
- 산출물 구조를 변경할 때는 `contract/` 를 함께 수정한다. `verify_contract.py` 통과가
  완료 조건이다
- 스키마는 실측값으로 고정한다. 상류 응답에 필드가 늘면 검증이 실패하며, 그때 스키마를
  갱신한다
- 시행지침 원문은 `fileRegistNo=7` 에만 존재한다. 첨부를 보유한 지구가 소수이므로 목록
  건수와 수집 건수의 차이는 정상이다

# output path

- `output/legal/시행지침/meta.json` — 전 지구 통합. 지구는 `districts[]` 에 있고
  `dstrcAppnNo` 로 찾는다. 지구별 `meta.json` 은 두지 않는다
- `output/legal/시행지침/<지역>/_index.json`
- `output/legal/시행지침/<지역>/<지구명>/지구단위계획 시행지침.<확장자>`

지구 디렉토리명은 `dstrcNm` 을 trim·치환한 값이다. 통합 파일은 디렉토리명을 따로
담지 않으므로 이 규약이 곧 지구와 디렉토리를 잇는 유일한 연결이다.
