# API 호출

| 실수 | 결과 · 대응 |
|---|---|
| `gubun=info` 로 상세 호출 | 첨부 필드 자체가 없다. `gubun=detailInfo` 를 쓴다 |
| 파라미터를 `dstrcNo` 로 씀 | 404 가 반환된다. 올바른 이름은 `dstrcAppnNo` 이다 |
| 다운로드를 `map.jigu.go.kr` 로 요청 | 404 가 반환된다. 호스트는 `openapi.jigu.go.kr` 이다 |

# 파일 판별

| 실수 | 결과 · 대응 |
|---|---|
| `Content-Type` 으로 확장자 결정 | 서버가 `application/x-msdownload` 를 반환한다. `Content-Disposition` 의 filename 을 URL 디코딩해 쓴다 |
| `.zip` 이니 unzip 되겠지 | 시행지침이 EGG 아카이브(매직 `EGGA`)인데 이름만 `.zip` 인 경우가 있다. 매직바이트로 판별한다 |

# 등록 상태 가정

| 실수 | 결과 · 대응 |
|---|---|
| 모든 지구에 첨부가 있다고 가정 | 대부분 미등록 상태이다. 표본 44건 중 첨부 보유는 5건, 시행지침은 3건이다 |
| 라벨을 코드에서 유추 | 지구마다 등록 상태가 다르다. HTML 라벨을 파싱해 `meta.json` 에 남긴다 |

후속 처리 실패 사례(광교지구 EGG 아카이브 등)는 legal-textreform 스킬의 `case/_failed.json`
에 있다.