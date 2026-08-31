#!/usr/bin/env python3
"""지구별 md 병합 — 사업(지구)당 문서 1개.

ZIP으로 배포된 시행지침은 편별로 쪼개져 있다(목차·총론·용지별·경관·환경…).
같은 지구의 조각을 원문 편 순서대로 이어 붙여 하나로 만든다.

중복 제거가 아니라 조각 결합이다. 실측 33개 다중 지구 중 내용이 완전히
같은 파일은 0건이었다 — 함부로 버리면 본문이 사라진다.

결정조서는 시행지침과 성격이 다른 별도 문서라 본문 뒤에 부록으로 붙인다.

산출: output/legal/markdown/<지역>/<지구명>.md
"""
import argparse, json, os, re, shutil
from collections import defaultdict
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
OUT = os.path.join(ROOT, "output", "legal", "markdown")
SRC_INDEX = [("output/md/_index.json", "PDF"),
             ("output/md_hwp/_index.json", "HWP")]

# 원문 편 순서. 파일명에 이 말이 있으면 해당 순번으로 정렬한다.
ORDER = [
    (r"표지", 0, "표지"),
    (r"목\s*차|차\s*례", 1, "목차"),
    (r"총\s*론|총\s*칙|제?\s*1\s*편", 2, "총론"),
    (r"용지별|제?\s*2\s*편", 3, "용지별 시행지침"),
    (r"특별계획", 4, "특별계획구역"),
    (r"경관|공공부문|제?\s*3\s*편", 5, "경관 및 공공부문"),
    (r"생태|환경|제?\s*4\s*편", 6, "환경부문"),
    (r"에너지|친환경|신재생", 7, "에너지부문"),
    (r"민간부문", 8, "민간부문"),
    (r"결정\s*조서|조\s*서|결정도서", 90, "결정조서"),   # 부록
]


def classify(name):
    base = os.path.basename(name)
    for pat, rank, label in ORDER:
        if re.search(pat, base):
            return rank, label
    return 50, None


def split_fm(text):
    """frontmatter와 본문 분리."""
    if not text.startswith("---"):
        return {}, text
    end = text.index("---", 5)
    fm = {}
    for line in text[4:end].splitlines():
        m = re.match(r"([^:]+):\s*(.*)", line)
        if m:
            fm[m.group(1).strip()] = m.group(2).strip().strip('"')
    return fm, text[end + 4:].lstrip("\n")


def main():
    ap = argparse.ArgumentParser(description="지구별 md 병합")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    groups = defaultdict(list)
    for rel, tag in SRC_INDEX:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        for it in json.load(open(p, encoding="utf-8"))["items"]:
            md = os.path.join(ROOT, it["md"]) if not os.path.isabs(it["md"]) else it["md"]
            if not os.path.exists(md):
                continue
            key = (it["region"], it["dstrcAppnNo"], it["dstrcNm"].strip())
            groups[key].append({"md": md, "tag": tag,
                                "name": it.get("file") or it["source"],
                                "source": it["source"]})

    made, multi = 0, 0
    for (region, no, nm), parts in sorted(groups.items()):
        for p in parts:
            p["rank"], p["label"] = classify(p["name"])
        parts.sort(key=lambda p: (p["rank"], p["name"]))

        head_fm, bodies, srcs = None, [], []
        total_jo = total_tbl = 0
        for p in parts:
            fm, body = split_fm(open(p["md"], encoding="utf-8").read())
            if head_fm is None:
                head_fm = fm
            total_jo += int(fm.get("조문수", 0) or 0)
            total_tbl += int(fm.get("표", 0) or 0)
            srcs.append({"파일": p["name"], "구분": p["label"] or "본문",
                         "추출": fm.get("추출방식", p["tag"]), "글자": len(body)})
            if not body.strip():
                continue
            if len(parts) > 1:
                sec = p["label"] or os.path.splitext(os.path.basename(p["name"]))[0]
                bodies.append(f"<!-- 원본: {p['name']} -->\n\n# {sec}\n\n{body}")
            else:
                bodies.append(body)

        if len(parts) > 1:
            multi += 1
        merged = "\n\n---\n\n".join(bodies).strip() + "\n"

        fm = ["---",
              f'지구명: "{nm}"',
              f"지구번호: {no}",
              f"지역: {region}"]
        for k in ["사업단계", "면적", "시행자", "사업기간", "근거법령"]:
            if head_fm.get(k):
                fm.append(f'{k}: "{head_fm[k]}"')
        fm += [f"구성문서수: {len(parts)}",
               f"조문수: {total_jo}",
               f"표: {total_tbl}",
               "원본구성:"]
        for s in srcs:
            fm.append(f'  - 파일: "{s["파일"]}"')
            fm.append(f'    구분: "{s["구분"]}"')
            fm.append(f'    추출: "{s["추출"]}"')
            fm.append(f"    글자: {s['글자']}")
        fm += [f"병합일시: {datetime.now().isoformat(timespec='seconds')}",
               "---", ""]

        dst = os.path.join(OUT, region, f"{re.sub(r'[/\\\\]', '_', nm)}.md")
        if args.dry_run:
            print(f"  {region} {nm[:28]:<30} {len(parts)}개 → {len(merged):,}자")
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write("\n".join(fm) + merged)
        made += 1

    print(f"\n병합 {made}개 지구 (조각 결합 {multi}개)")
    if not args.dry_run:
        print(f"  → {OUT}")


if __name__ == "__main__":
    main()
