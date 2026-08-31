---
name: legal-dup
description: Use when collecting BLS5_DSTRC_MASTER district metadata or 지구단위계획시행지침·도면 attachments from 택지정보시스템
---

# legal-dup

## 구성

- `scripts/collect_master.py` — `BLS5_DSTRC_MASTER` 전국 CSV 원본 스냅샷 수집·검증
- `scripts/collect.py` — 수집 실행. 지연·순차·백오프·재개 내장. API 직접 호출 금지
- `scripts/meta_store.py` — 통합 `meta.json` 의 로드·병합·집계
- `scripts/verify_contract.py` — 계약 검증. 종료코드 0=충족, 1=위반
- `contract/master-source.json` — 지구 마스터와 첨부 원장의 수집 경계·명령 계약
- `contract/master.schema.json` — 지구 마스터 `manifest.json` 구조
- `contract/commands.json` — 선행조건·멱등성·재개·저장 이름 규약
- `contract/{index,meta}.schema.json` — `_index.json`·`meta.json` 구조
- `references/master-source.md` — 공식 파일데이터 경로·이용정보·실패 판정
- `case/reference.md` — 엔드포인트·시도 코드·`fileRegistNo`·실측치

## 실행

```bash
python3 scripts/collect_master.py discover
python3 scripts/collect_master.py fetch --source-zip <공식화면에서_받은_zip>
python3 scripts/collect_master.py fetch --usage-profile <로컬_이용정보.json>
python3 scripts/collect_master.py verify

python3 scripts/collect.py index --region 서울                      # fetch의 선행 조건
python3 scripts/collect.py fetch --region 서울 --file-type 7         # 시행지침만
python3 scripts/collect.py fetch --region 경기 --file-type 2,4,5,7   # 도면 포함
python3 scripts/collect.py fetch --region 경기 --file-type 5 --dstrc <지구번호>  # 표본
python3 scripts/verify_contract.py [--region 인천]                   # 수집 후 검증
```

`BLS5_DSTRC_MASTER`는 지구단계정보 원장이다. 이 파일은 시행지침과 도면 첨부를
포함하지 않는다. 지구 마스터 스냅샷은 `collect_master.py`로 보존하고, 첨부 인덱스와
원문은 `collect.py index/fetch`로 수집한다. 두 원장을 합치지 않는다.

`fetch` 는 같은 지역에서 `total == indexed == items 길이`를 충족한 `index` 를 선행
조건으로 요구한다. 다운로드가 중단되면 같은 명령으로 재개된다. 산출물 구조를 바꿀 때는
`contract/` 를 함께 고친다. `verify_contract.py` 통과가 완료 조건이다. 나머지 규약은
`contract/commands.json` 이 정본이다.

## output path

- `output/legal/source/jigu/bls5_dstrc_master/<기준일>/` — 공식 ZIP·CSV·`manifest.json`
- `output/legal/시행지침/meta.json` — 전 지구 통합. `districts[]` 를 `dstrcAppnNo` 로 찾는다
- `output/legal/시행지침/<지역>/_index.json`
- `output/legal/시행지침/<지역>/<지구명>/<첨부어간>.<확장자>`

지구 디렉토리명은 `dstrcNm` 을 trim·치환한 값이며, 이 규약이 지구와 디렉토리를 잇는
유일한 연결이다. 첨부어간은 `fileRegistNo` 마다 다르다 — 어간표와 코드 7 불변 사유는
`contract/commands.json` 의 `assetStems`·`immutable` 이 정본이다.
