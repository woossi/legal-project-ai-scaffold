# legal-project AI scaffold

이 저장소는 `legal-project`의 AI 작업 골격을 별도로 보존한다.
대상은 Claude Code와 Codex가 공유하는 에이전트, 스킬, 규칙, 계약, 라우팅 어댑터다.

## 포함 범위

- `AGENTS.md`: 프로젝트 공통 운영 규칙
- `CLAUDE.md`: Claude 런타임 진입점
- `.claude/agents/`: 팀별 에이전트 정의
- `.claude/skills/`: 스킬 구현 정본
- `.agents/skills/`: Codex 라우팅 어댑터
- `.claude/rules/`, `.codex/rules/`: 프로젝트 규칙과 Codex 포인터
- `.claude/team-roster.yaml`: 팀 구성 선언
- `.claude/settings.json`, `.codex/config.toml`: 프로젝트 런타임 설정
- `tools/verify_scaffold.py`: 정본과 어댑터의 정합성 검증
- `tools/deepseek_router_proxy.py`: DeepSeek 모델 에이전트의 선택적 로컬 라우터

## 제외 범위

현재 프로젝트의 원문, 산출물, 작업 이력, 세션 상태는 이 저장소에 넣지 않는다.

- `output/`, `docs/ppt/`
- `.claude/agent-memory/`
- `.codex/logs/`
- `.claude/worktrees/`, `.worktrees/`
- `.mcp.json`, `.claude/settings.local.json`
- `.superpowers/`, 가상환경, 캐시

에이전트 메모리는 [`templates/agent-memory/MEMORY.md`](templates/agent-memory/MEMORY.md)를 복사해 만든다.
MCP 서버 설정은 [`.mcp.example.json`](.mcp.example.json)을 로컬 `.mcp.json`으로 복사한 뒤 엔드포인트를 수정한다.

## 선택적 DeepSeek 라우터

`legal-collector`와 `legal-adversary`는 `deepseek-v4-flash[1m]` 모델명을 사용한다.
두 에이전트를 그대로 사용할 때만 로컬 라우터를 실행한다.

```bash
export DEEPSEEK_API_KEY="<your-key>"
python3 tools/deepseek_router_proxy.py --port 8787
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
```

실제 키를 저장소 파일에 기록하지 않는다.
`DEEPSEEK_API_KEY` 환경변수가 없으면 라우터는 `~/.claude/.deepseek.env`를 읽는다.

## 정본 관계

`.claude/skills/<팀>/<스킬명>/`은 구현 정본이다.
`.agents/skills/<팀>/<스킬명>/SKILL.md`는 Codex가 정본을 찾도록 연결하는 라우팅 어댑터다.
규칙이나 절차를 바꾸면 구현 정본을 먼저 수정한다.

## 설치

이 저장소의 골격을 사용할 프로젝트 루트에 필요한 파일을 복사한다.
기존 `AGENTS.md`, `CLAUDE.md`, `.claude/`, `.agents/`, `.codex/`가 있으면 충돌 여부를 먼저 확인한다.
`.claude/settings.json`의 권한 목록은 적용할 프로젝트의 위험 범위를 기준으로 다시 검토한다.
현재 스냅샷은 `defaultMode: dontAsk`를 보존하므로 신뢰할 수 없는 저장소에 그대로 적용하지 않는다.

## 검증

```bash
python3 tools/verify_scaffold.py
```

스킬 내부의 실행 검증기는 `output/<팀>/` 입력이 있는 프로젝트에서 실행한다.
이 저장소는 AI 설정 골격만 보존하므로 원본 자료나 분석 산출물을 포함하지 않는다.
원본 저장소의 `tools/capability_context.py`와 압축 회귀 테스트는 과거 커밋 이력에
의존하므로 이력이 없는 골격 저장소에 포함하지 않는다.

## 출처

- 원본 저장소: `woossi/legal-project` (private)
- 기준 커밋: `e20bc1a2519a8365602c7148845309feebca58f4`
- 내보낸 날짜: `2026-08-31`
- 방식: 원본 Git 이력을 포함하지 않은 allowlist 작업 트리 스냅샷
