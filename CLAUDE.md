# Claude Runtime Instructions

@AGENTS.md

Use `.claude/team-roster.yaml` as the declarative spawn bundle for project teams. For the KB bundle, create teammates in roster order and follow each role's body-level `REQUIRED SKILLS`.

Claude owns actual runtime team configuration under `~/.claude/teams/`. Project `.claude/teams/**` files are stale artifacts and must not be used as runtime config.
