#!/usr/bin/env python3
"""terms.json 의 정의 조항 소속 정의문을 용어집(definiation.json)으로 재구성한다.

용도는 두 가지다.
1. 지침마다 반복되는 용어 정의의 정본 목록
2. 재추출 시 정의 조항을 건너뛰기 위한 조 표제 패턴 공급

입력  output/legal/word/terms.json
출력  output/legal/word/definiation.json
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

# ── 조 표제 정규화 ────────────────────────────────────────────────────────────
# 실측 270종의 표기를 조번호 + 주제로 분해한다. 조번호 없는 표기가 41% 이므로
# 조번호만으로 정의 조항을 식별해서는 안 된다.

ARTICLE_NO = re.compile(r"제\s*([0-9]+)\s*조")

# 정의 조항 표제 판정 — 4종을 인정한다. terms.json 의 in_definition_article 은
# 아래 ① 만 보므로 ②~④ 를 놓친다(실측 394 occurrence). 그래서 여기서 다시 판정한다.
DEF_TITLE_PATTERNS = [
    # ① `용어의 정의` / `용어정리` / `용어 정의` — 지배 패턴
    ("용어의정의", re.compile(r"용어\s*(?:의\s*)?(?:정의|정리)")),
    # ② 순수 `(정의)` — 부문별 지침(범죄예방·자전거·생태환경 등)의 정의 조항.
    #    표제 전체가 `정의` 뿐일 때만 인정한다. `건축물의 정의에 포함되는…` 같은
    #    본문 인용을 배제하기 위해 앵커를 건다.
    ("정의", re.compile(r"^\s*(?:제\s*\d+\s*조)?\s*[(<\[]?\s*정\s*의\s*[)>\]]?\s*$")),
    # ③ `…의 용어에 관한 사항`
    ("용어에관한사항", re.compile(r"용어에\s*관한\s*사항")),
    # ④ `…에 관한 용어` — 표제 끝에서만. `…에 관한 용어의 정의` 는 ① 이 먼저 잡는다.
    ("에관한용어", re.compile(r"에\s*관한\s*용어\s*[)>\]]?\s*$")),
]
# 주제 추출 시 잘라낼 꼬리말 (①③④ 공통)
_TITLE_TAIL = re.compile(
    r"용어\s*(?:의\s*)?(?:정의|정리)|용어에\s*관한\s*사항|에\s*관한\s*용어"
)

_STRIP_LEAD = re.compile(r"^[()<>\[\]「」『』\s:：.·,]+")
_STRIP_TAIL = re.compile(r"[()<>\[\]「」『』\s:：.·,]+$")
# `1-1-5 용어의 정의` 처럼 항목 번호가 앞에 붙는 형태
_ITEM_NO = re.compile(r"^[0-9]+(?:[-.][0-9]+)*\s+")

# 주제 카테고리. 앞에서부터 먼저 맞는 것을 채택하므로 순서가 곧 우선순위다.
# 좁은 주제를 위에, 넓은 주제를 아래에 둔다.
TOPIC_RULES = [
    ("건축선", r"건축선"),
    ("대지 내 공지", r"대지\s*내?\s*공지"),
    ("가구·획지", r"가구|획지"),
    ("건축물 용도", r"용도"),
    ("건축물 규모·높이", r"규모|높이|층수"),
    ("건축물 형태·색채", r"형태|외관|색채|디자인"),
    ("건축물 배치", r"배치"),
    ("교통처리", r"교통|보행|주차|차량"),
    ("환경·경관", r"저영향개발|친환경|환경|경관|조경|녹지"),
    ("공통", r"공통"),
]


def _clean(s):
    """표제 주변의 괄호·구두점·항목번호를 벗긴다."""
    s = _STRIP_LEAD.sub("", s)
    s = _STRIP_TAIL.sub("", s)
    s = _ITEM_NO.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    # `(용어의 정의) (변경)` → 조번호를 뗀 뒤 `용어의 정의) (변경` 이 남는다.
    # 닫는 괄호가 먼저 나오면 그 앞까지만 표제로 본다.
    s = re.sub(r"\)\s*\(.*$", "", s)
    return _STRIP_TAIL.sub("", _STRIP_LEAD.sub("", s))


def normalize_article(raw):
    """조 표제 원문 → (조번호, 주제, 정규화표제, 정의조항여부, 판정근거)."""
    raw = (raw or "").strip()
    m = ARTICLE_NO.search(raw)
    article_no = int(m.group(1)) if m else None

    rest = _clean(raw[m.end():] if m else raw)

    kind = None
    for name, pat in DEF_TITLE_PATTERNS:
        # ② 는 표제 전체를 봐야 하므로 원문에, 나머지는 어디에 있어도 되므로
        #    조번호를 뗀 나머지에 건다.
        target = raw if name == "정의" else rest
        if pat.search(target):
            kind = name
            break

    topic = None
    if kind == "정의":
        topic = "일반"
    elif kind:
        # 주제는 꼬리말 앞의 수식구에서만 찾는다.
        head = _TITLE_TAIL.split(rest, maxsplit=1)[0]
        topic = "일반"
        for name, pat in TOPIC_RULES:
            if re.search(pat, head):
                topic = name
                break
    return article_no, topic, rest, bool(kind), kind


# ── 집계 ─────────────────────────────────────────────────────────────────────

def build(terms_path, out_path):
    data = json.loads(Path(terms_path).read_text(encoding="utf-8"))
    src_meta = data.get("meta", {})
    terms = data["terms"]

    all_docs = set()
    def_docs = set()
    art5_docs = set()
    article_titles = collections.Counter()   # 원문 표기별 occurrence 수
    topic_counter = collections.Counter()
    articleno_counter = collections.Counter()
    kind_counter = collections.Counter()
    recovered = 0   # terms.json 이 정의조항으로 보지 않았는데 여기서 회수한 건수

    staged = []
    for t in terms:
        occ_def = []
        for o in t["occurrences"]:
            all_docs.add(o["dstrcAppnNo"])
            no, topic, norm, isdef, kind = normalize_article(o.get("article"))
            if not isdef:
                continue
            occ_def.append((o, no, topic, norm, kind))
            if not o.get("in_definition_article"):
                recovered += 1
            article_titles[(o.get("article") or "").strip()] += 1
            if topic:
                topic_counter[topic] += 1
            articleno_counter[no] += 1
            kind_counter[kind] += 1
            def_docs.add(o["dstrcAppnNo"])
            if no == 5:
                art5_docs.add(o["dstrcAppnNo"])

        if not occ_def:
            continue  # 정의 조항 밖에서만 나타난 용어는 용어집에 넣지 않는다

        arts = collections.Counter()
        for _o, no, topic, norm, _k in occ_def:
            arts[(no, topic, norm)] += 1
        art_list = [
            {
                "article_no": no,
                "topic": topic,
                "title_normalized": norm,
                "count": c,
            }
            for (no, topic, norm), c in arts.most_common()
        ]

        occs = [o for o, *_ in occ_def]
        def_districts = sorted({o["dstrcAppnNo"] for o in occs})
        regions = collections.Counter(o["region"] for o in occs)

        # 변이는 정의 조항 소속 occurrence 가 가리키는 variant 로 한정한다.
        # terms.json 의 variant.districts·count 는 전체 occurrence 기준이라
        # 그대로 쓰면 정의 조항 밖 지구가 섞인다. 여기서 다시 센다.
        v_dist = collections.defaultdict(set)
        v_count = collections.Counter()
        for o in occs:
            vi = o.get("variant_index")
            v_dist[vi].add(o["dstrcAppnNo"])
            v_count[vi] += 1
        variants = [v for v in t["variants"] if v.get("variant_index") in v_count]
        variants.sort(key=lambda v: -v_count[v.get("variant_index")])

        staged.append({
            "id": t["id"],
            "term": t["term"],
            "normalized": t["normalized"],
            "surface_forms": t.get("surface_forms", []),
            "definition_representative": t.get("definition_representative", ""),
            "definition_rule_part": t.get("definition_rule_part", ""),
            "definition_variance": t.get("definition_variance", ""),
            "variance_evidence": t.get("variance_evidence", ""),
            "proviso_divergence": t.get("proviso_divergence", False),
            "cited_laws": t.get("cited_laws", []),
            # terms.json 의 classification 을 통째로 옮기지 않는다. ubiquity·
            # source_article_* 는 이 파일이 자체 모수·자체 판정으로 다시 계산하므로
            # 그대로 두면 같은 이름의 값이 둘이 되어 오독을 부른다.
            "classification": {
                k: v for k, v in t.get("classification", {}).items()
                if k in (
                    "concept_type",
                    "concept_type_is_primary",
                    "classification_confidence",
                    "legal_status",
                    "legal_status_evidence",
                )
            },
            "doc_frequency": len(def_districts),
            "doc_frequency_all": t.get("doc_frequency", 0),
            "occurrence_count": len(occ_def),
            "regions": dict(regions),
            "articles": art_list,
            "in_article_5": any(a["article_no"] == 5 for a in art_list),
            "districts": def_districts,
            "variant_count": len(variants),
            "variants": [
                {
                    "variant_index": v.get("variant_index"),
                    "text": v.get("text", ""),
                    "text_core": v.get("text_core", ""),
                    "text_rule": v.get("text_rule", ""),
                    "districts": sorted(v_dist[v.get("variant_index")]),
                    "count": v_count[v.get("variant_index")],
                    "truncated": v.get("truncated", False),
                    "source_quality": v.get("source_quality", ""),
                }
                for v in variants
            ],
        })

    # 모수가 확정된 뒤에 비율·등급을 매긴다
    denom = len(def_docs)
    for e in staged:
        r = round(e["doc_frequency"] / denom, 4) if denom else 0.0
        e["district_ratio_def"] = r
        if e["doc_frequency"] == 1:
            e["ubiquity"] = "단일지구"
        elif r >= 0.5:
            e["ubiquity"] = "보편"
        elif r >= 0.1:
            e["ubiquity"] = "빈출"
        else:
            e["ubiquity"] = "산발"

    # 반복도 내림차순 — 용어집의 목적이 '반복되는 정의'의 식별이다
    staged.sort(key=lambda e: (-e["doc_frequency"], -e["occurrence_count"], e["term"]))

    skip_titles = sorted(
        ({"title": a, "count": c} for a, c in article_titles.items() if a),
        key=lambda x: (-x["count"], x["title"]),
    )
    ratio_ranks = collections.Counter(e["ubiquity"] for e in staged)

    out = {
        "meta": {
            "생성근거": "output/legal/word/terms.json 의 occurrence 를 조 표제로 재판정",
            "원본_생성일시": src_meta.get("생성일시"),
            "대상문서수": len(all_docs),
            "정의조항_보유문서수": denom,
            "제5조_보유문서수": len(art5_docs),
            "수집범위": (
                "조번호와 무관하게 정의 조항으로 판정된 모든 조항. 제5조는 그중 하나이며 "
                "in_article_5 로 구분한다. 실측상 정의 조항 표제의 41%에 조번호가 없고, "
                "제5조를 가진 문서는 정의 조항 보유 문서의 절반 수준이라 "
                "제5조만으로는 용어집이 완결되지 않는다."
            ),
            "정의조항_판정규칙": {
                "용어의정의": "표제에 '용어의 정의'·'용어정리'·'용어 정의' 포함 — 지배 패턴",
                "정의": "표제 전체가 '정의' 뿐 — 부문별 지침(범죄예방·자전거·생태환경 등)의 정의 조항",
                "용어에관한사항": "표제에 '…의 용어에 관한 사항' 포함",
                "에관한용어": "표제가 '…에 관한 용어' 로 끝남",
                "제외": (
                    "'…에 관한 사항'·'(목적)'·'(적용범위)'·'(대지의 조경)' 등 규정 조항. "
                    "'…에 관한 사항' 은 규정 조항 표제로도 쓰여 모호하므로 넣지 않는다."
                ),
            },
            "판정근거별_정의문수": dict(kind_counter.most_common()),
            "terms_json_대비_회수": {
                "회수_occurrence": recovered,
                "사유": (
                    "terms.json 의 in_definition_article 은 '용어의 정의' 표제만 인정한다. "
                    "순수 '(정의)'·'…의 용어에 관한 사항'·'…에 관한 용어' 표제의 "
                    "정의 조항이 누락되어 여기서 회수했다."
                ),
            },
            "용어수": len(staged),
            "정의문수": sum(e["occurrence_count"] for e in staged),
            "조번호분포": {
                (f"제{k}조" if k else "조번호없음"): v
                for k, v in sorted(articleno_counter.items(), key=lambda x: -x[1])
            },
            "주제분포": dict(topic_counter.most_common()),
            "반복도등급": dict(ratio_ranks),
            "반복도등급_기준": {
                "모수": "정의조항_보유문서수 (정의 조항이 없는 문서를 분모에서 뺀다)",
                "보편": "district_ratio_def >= 0.5",
                "빈출": "0.1 <= district_ratio_def < 0.5",
                "산발": "doc_frequency > 1 이면서 ratio < 0.1",
                "단일지구": "doc_frequency == 1",
            },
            "정의문_원문보존": (
                "정의문은 terms.json 의 저장 형태를 그대로 옮긴다. "
                "맞춤법 교정·요약·윤문을 하지 않는다."
            ),
            "필드_모수_주의": (
                "doc_frequency·occurrence_count·district_ratio_def·ubiquity·regions·"
                "articles·variants 는 정의 조항 소속 occurrence 만으로 다시 센 값이다. "
                "classification 과 cited_laws 는 terms.json 이 전체 occurrence 로 "
                "판정한 값을 그대로 옮긴 것이라 모수가 다르다. "
                "doc_frequency_all 은 비교용으로 남긴 terms.json 의 전체 기준 값이다."
            ),
            "스크립트": ".claude/skills/legal/legal-term/scripts/build_definitions.py",
        },
        "definition_articles": {
            "설명": "재추출 시 본문에서 건너뛸 정의 조항 표제 원문 목록",
            "고유표제수": len(skip_titles),
            "titles": skip_titles,
        },
        "definitions": staged,
    }

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", default="output/legal/word/terms.json")
    ap.add_argument("--out", default="output/legal/word/definiation.json")
    a = ap.parse_args()

    if not Path(a.terms).exists():
        print(f"입력 없음: {a.terms}", file=sys.stderr)
        return 1

    out = build(a.terms, a.out)
    m = out["meta"]
    print(f"용어 {m['용어수']}건 / 정의문 {m['정의문수']}건 → {a.out}")
    print(f"정의조항 보유문서 {m['정의조항_보유문서수']} (제5조 보유 {m['제5조_보유문서수']})")
    print(f"반복도: {m['반복도등급']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
