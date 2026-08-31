#!/usr/bin/env python3
"""규범값 근거 계통을 산출한다.

이 계통은 사례 문서에서 뽑은 것이 아니다. 실측하면 국토계획법 제77·78조와
시행령 제84·85조는 189개 문서 전체에서 인용 0건이고, 제78조제4항이 단 1건
나온다(평택 브레인시티, 시행령 제85조제7항·평택시 도시계획조례 제64조와 함께).
시행지침은 위임 계통의 말단인 조례만 가리키므로, 계통 자체는 원문 법령에서
직접 세워야 한다.

정본은 `case/정본대조.json` 의 `규범값근거계통` 이며, 이 스크립트는 그것을
검증 가능한 산출물 형태로 옮긴다.

입력  .claude/skills/legal/legal-statute/case/정본대조.json
출력  output/legal/statute/norm_basis.json
"""

import argparse
import collections
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SEED = BASE / "case" / "정본대조.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import statute_common as sc  # noqa: E402


def build(seed_path, out_dir):
    seed = sc.read_json(seed_path)
    nb = seed["규범값근거계통"]
    out = {
        "meta": {
            "생성근거": "case/정본대조.json 의 규범값근거계통을 산출물로 옮긴다",
            "설명": nb["설명"],
            "왜필요한가": nb["왜필요한가"],
            "사례에서_뽑지_않는_이유": (
                "실측상 국토계획법 제77·78조와 시행령 제84·85조는 189개 문서에서 "
                "인용 0건이다. 제78조제4항만 1건 나온다. 시행지침은 위임 계통의 "
                "말단인 조례만 가리키므로 계통은 원문 법령에서 직접 세운다"
            ),
            "조문수": len(nb["articles"]),
            "관계수": len(nb["relations"]),
            "검증상태분포": dict(collections.Counter(
                a["검증상태"] for a in nb["articles"])),
            "대조일": seed.get("대조일"),
            "관계_의미": {
                "준용": "A 조문이 B 조문의 기준을 따르도록 정한다",
                "위임": "A 조문이 기준 결정을 하위 법령·조례에 맡긴다",
                "완화": "A 조문이 B 조문의 적용을 완화할 근거를 준다",
            },
            "스크립트": ".claude/skills/legal/legal-statute/scripts/build_norm_basis.py",
        },
        "articles": nb["articles"],
        "relations": nb["relations"],
    }
    od = Path(out_dir)
    od.mkdir(parents=True, exist_ok=True)
    sc.write_json(od / "norm_basis.json", out, final_newline=False)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=str(SEED))
    ap.add_argument("--out-dir", default="output/legal/statute")
    a = ap.parse_args()
    if not Path(a.seed).exists():
        print(f"입력 없음: {a.seed}", file=sys.stderr)
        return 1
    out = build(a.seed, a.out_dir)
    m = out["meta"]
    print(f"규범값 계통: 조문 {m['조문수']} / 관계 {m['관계수']} "
          f"({m['검증상태분포']}) → {a.out_dir}/norm_basis.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
