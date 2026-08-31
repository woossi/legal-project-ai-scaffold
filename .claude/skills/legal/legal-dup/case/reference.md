# 택지정보시스템 API 참조

인증은 필요하지 않다. 호출은 지구 목록, 지구 상세(첨부 코드), 파일 다운로드의 3단계 체인으로
이루어진다.

## 엔드포인트

| 단계 | 메서드 · URL | 필수 파라미터 |
|------|--------------|---------------|
| ① 지구 목록 | `POST map.jigu.go.kr/search/moreResultDstrc.json` | `pageNum`, `scCtprvn`, `scSigngu=0`, 나머지 필터 `ALL` |
| ② 지구 상세 | `POST map.jigu.go.kr/dstrc/dstrcInfo.do` | `dstrcAppnNo`, `gubun=detailInfo` |
| ③ 파일 다운로드 | `GET openapi.jigu.go.kr/file/dstrcFileDownload.json` | `jobSe=dstrc`, `fileCode`, `fileRegistNo` |

③ 단계만 호스트가 `openapi` 이다. 브라우저 `User-Agent` 와
`Referer: https://map.jigu.go.kr/map.do` 를 누락하면 응답이 달라진다.

## 시도 코드 (scCtprvn)

서울은 `1100000000`, 인천은 `2800000000`, 경기는 `4100000000` 이다. 전체 목록은
`/search/searchDstrc.do` 응답의 `scCtprvn` select 에서 확인한다.

## 첨부 종류 (fileRegistNo)

실측으로 확인한 값은 `2` 위치도, `3` 광역교통계획도, `4` 토지이용계획도, `5` 지구단위계획도,
`7` 지구단위계획시행지침이다.

`1`, `6`, `8` 이상은 표본에서 관측되지 않았다. 라벨은 항상 응답 HTML 에서 파싱해 기록한다.
코드와 라벨의 매핑을 하드코딩해 신뢰하지 않는다.

## 실측 검증 (2026-08)

- 인천 65건 등 목록 조회가 정상 동작하였고, 페이지네이션도 확인되었다
- 광교지구(`41115MX2004001`)에서 `1.지구단위계획_시행지침.zip` 91MB 를 받았다. 실체는 EGG
  아카이브이다
- 광명역세권(`41210KH2003001`)에서 토지이용계획도 JPEG 6.7MB(4625×5841)를 받았다
- 순차 호출로 40건 이상 연속 조회하는 동안 차단과 오류는 0건이었다
- 첨부 보유율은 낮다. 표본 44건 중 첨부는 5건, 시행지침은 3건이다
