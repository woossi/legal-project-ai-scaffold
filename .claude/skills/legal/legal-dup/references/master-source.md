# BLS5_DSTRC_MASTER 원천

## 적용 범위

`BLS5_DSTRC_MASTER`는 지구단계정보를 의미한다. 이 파일은 지구지정번호, 지구명,
단계코드, 고시번호, 고시일자, 승인기관, 담당기관을 제공한다.

이 파일은 지구단위계획시행지침과 도면 첨부를 제공하지 않는다. 시행지침과 도면은
`collect.py index/fetch`의 지구 상세·첨부 다운로드 경로에서 계속 수집한다.

## 공식 경로

확인일은 2026-08-26이다.

1. 상세 페이지: `https://openapi.jigu.go.kr/down/detail.do?table=BLS5_DSTRC_MASTER`
2. 최신 기준월: `POST https://openapi.jigu.go.kr/down/title.json`
3. 파일 목록: `POST https://openapi.jigu.go.kr/api/list.json`
4. 파일 확인: `POST https://openapi.jigu.go.kr/openApi/fileExist.json`
5. 파일 다운로드: `GET https://openapi.jigu.go.kr/openApi/down.do`
6. 컬럼정의서: `https://openapi.jigu.go.kr/bls_Column_Info_lx.pdf`
7. 코드정의서: `https://openapi.jigu.go.kr/bls_Code_Info_v1.1.xlsx`

2026-08-26에 공식 페이지는 최근 생성일 2026-07-31, 최종 고시월 2026-06,
전국 CSV 행 수 3,056을 표시했다. 이 값은 기준값이 아니라 관측값이다. 다음 수집에서는
`discover` 결과와 새 스냅샷의 `manifest.json`을 다시 확인한다.

## 이용정보 JSON

공식 화면은 파일 다운로드 전에 직업, 기관명, 부서명, 활용 목적, 활용 분야를 요구한다.
자동 다운로드는 다음 구조의 로컬 JSON을 `--usage-profile`에 지정한다.

```json
{
  "userJobTp": "<공식 화면 코드>",
  "userJobCpNm": "<기관명>",
  "userJobClassNm": "<부서명>",
  "userUsePurpose": "<공식 화면 코드>",
  "field": ["<공식 화면 코드>"]
}
```

에이전트는 이용정보 값을 추정하지 않는다. 이용정보 JSON은 저장소에 추가하지 않는다.
수집기는 공식 폼의 허용 키만 요청에 사용한다. 알 수 없는 키는 전송하지 않는다.
수집기는 이용정보를 `manifest.json`과 오류 메시지에 저장하지 않는다.

## 실패 판정

- 응답이 HTML이면 다운로드 실패로 판정한다. HTML을 ZIP으로 저장하지 않는다.
- ZIP에 CSV가 없거나 두 개 이상이면 파일 구성이 바뀐 것으로 판정한다.
- CSV에 `DSTRC_APPN_NO` 또는 `DSTRC_NM`이 없으면 컬럼 계약 변경으로 판정한다.
- `stdrDe`가 8자리 날짜가 아니면 출력 경로를 만들지 않는다.
- ZIP 내부 CSV 파일명이 `stdrDe`를 포함하지 않으면 다른 판본으로 판정한다.
- 공식 목록의 `totcnt`와 CSV 행 수가 다르면 일부 자료 또는 다른 판본으로 판정한다.
- 같은 기준일의 바이트가 기존 스냅샷과 다르면 자동 덮어쓰지 않는다.
- `BLS5_DSTRC_MASTER`의 행 수와 고유 지구 수를 같은 값으로 간주하지 않는다. 한 지구가
  여러 단계 행을 가질 수 있다.
