#!/usr/bin/env python3
"""변환 산출물 품질 게이트.

사용:
    verify.py out.txt --src orig.pdf          # 단건
    verify.py outdir/ --src-dir origdir/      # 일괄
    verify.py out.txt --src o.pdf --json      # 기계 판독

종료 코드: 0=전건 통과, 1=하나라도 실패(FAIL), 2=사용법 오류
"""
import argparse, json, os, re, subprocess, sys

# 실측 임계값 — 근거는 스킬 루트 case/변환품질-판정기준.md 참조
MIN_SOLID_RATIO = 0.05      # 공백·점선 제외 실질 문자 비율
MIN_CHARS_PER_PAGE = 200    # PDF: 쪽당 글자수
MIN_CHARS_PER_KB = 1.0      # HWP: 원본 KB당 글자수
MAX_MARKER_RATIO = 0.30     # <표>/<그림> 마커가 줄에서 차지하는 비율
MAX_LINE_BLANK = 200        # 한 줄에 이만큼 연속 공백이면 레이아웃 패딩
MAX_STUB_RATIO = 0.30       # 2자 이하 줄 비율 — 줄 파편화 탐지
MIN_AVG_LINE = 8            # 평균 줄 길이 하한 — 줄 파편화 탐지

FILLER = re.compile(r"[\s·.…∙‥\-_~]+")
MARKER = re.compile(r"^\s*<(표|그림|수식)>\s*$")


def pdf_pages(path):
    try:
        out = subprocess.run(["pdfinfo", path], capture_output=True,
                             text=True, timeout=60).stdout
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def check(txt_path, src_path=None):
    """산출물 1건 검사. (verdict, metrics, reasons) 반환."""
    r = {"txt": txt_path, "src": src_path}
    if not os.path.exists(txt_path):
        return "FAIL", r, ["산출 파일 없음"]
    text = open(txt_path, encoding="utf-8", errors="replace").read()
    total = len(text)
    solid = len(FILLER.sub("", text))
    lines = text.splitlines()
    nonblank = [l for l in lines if l.strip()]
    markers = sum(1 for l in nonblank if MARKER.match(l))

    r.update({"chars": total, "solid": solid,
              "solid_ratio": round(solid / total, 4) if total else 0.0,
              "lines": len(nonblank), "markers": markers,
              "marker_ratio": round(markers / len(nonblank), 4) if nonblank else 0.0,
              "max_blank_run": max((len(m) for m in
                                    re.findall(r"[ \t]{2,}", text)), default=0)})

    reasons = []
    if total == 0:
        reasons.append("빈 파일")
    if total and r["solid_ratio"] < MIN_SOLID_RATIO:
        reasons.append(f"실질 내용 {r['solid_ratio']*100:.1f}% "
                       f"(<{MIN_SOLID_RATIO*100:.0f}%) — 공백 패딩 의심")
    if r["max_blank_run"] >= MAX_LINE_BLANK:
        reasons.append(f"연속 공백 {r['max_blank_run']}자 — 레이아웃 패딩")
    if nonblank and r["marker_ratio"] > MAX_MARKER_RATIO:
        reasons.append(f"표·그림 마커 {r['marker_ratio']*100:.0f}% "
                       f"(>{MAX_MARKER_RATIO*100:.0f}%) — 셀 내용 유실 의심")

    # 줄 파편화 — 다른 4개 지표를 모두 통과하면서도 본문이 글자 단위로
    # 쪼개진 경우가 있다(HTML 인라인 태그를 개행으로 치환한 산출물).
    # 조문 헤더와 본문 연결이 끊겨 후속 파싱이 오염된다.
    if nonblank:
        stub = sum(1 for l in nonblank if len(l.strip()) <= 2)
        avg = sum(len(l.strip()) for l in nonblank) / len(nonblank)
        r["stub_ratio"] = round(stub / len(nonblank), 4)
        r["avg_line"] = round(avg, 1)
        if r["stub_ratio"] > MAX_STUB_RATIO:
            reasons.append(f"2자 이하 줄 {r['stub_ratio']*100:.0f}% "
                           f"(>{MAX_STUB_RATIO*100:.0f}%) — 줄 파편화")
        if avg < MIN_AVG_LINE:
            reasons.append(f"평균 줄 길이 {avg:.1f}자 (<{MIN_AVG_LINE}) — 줄 파편화")

    if src_path and os.path.exists(src_path):
        head = open(src_path, "rb").read(4)
        if head == b"%PDF":
            pg = pdf_pages(src_path)
            r["pages"] = pg
            if pg:
                per = solid / pg
                r["chars_per_page"] = round(per)
                if per < MIN_CHARS_PER_PAGE:
                    reasons.append(f"쪽당 {per:.0f}자 (<{MIN_CHARS_PER_PAGE}) "
                                   f"— 스캔본이거나 추출 실패")
        else:
            kb = os.path.getsize(src_path) / 1024
            r["src_kb"] = round(kb)
            if kb:
                per = solid / kb
                r["chars_per_kb"] = round(per, 1)
                if per < MIN_CHARS_PER_KB:
                    reasons.append(f"원본 KB당 {per:.1f}자 "
                                   f"(<{MIN_CHARS_PER_KB}) — 본문 유실 의심")

    return ("FAIL" if reasons else "PASS"), r, reasons


def main():
    ap = argparse.ArgumentParser(description="변환 산출물 품질 게이트")
    ap.add_argument("target", help="txt 파일 또는 디렉터리")
    ap.add_argument("--src", help="대응 원본 파일")
    ap.add_argument("--src-dir", help="원본 디렉터리(일괄 검사)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pairs = []
    if os.path.isdir(args.target):
        for root, _, files in os.walk(args.target):
            for f in sorted(files):
                if not f.endswith(".txt"):
                    continue
                t = os.path.join(root, f)
                s = None
                if args.src_dir:
                    stem = os.path.splitext(f)[0]
                    for ext in (".pdf", ".hwp", ".HWP", ".PDF"):
                        c = os.path.join(args.src_dir, stem + ext)
                        if os.path.exists(c):
                            s = c
                            break
                pairs.append((t, s))
    else:
        pairs.append((args.target, args.src))

    if not pairs:
        sys.exit("검사 대상 없음")

    out, nfail = [], 0
    for t, s in pairs:
        v, m, why = check(t, s)
        out.append({"verdict": v, **m, "reasons": why})
        if v == "FAIL":
            nfail += 1
        if not args.json:
            mark = "PASS" if v == "PASS" else "FAIL"
            print(f"[{mark}] {os.path.basename(t)}  "
                  f"{m.get('chars', 0):,}자 실질{m.get('solid_ratio', 0)*100:.0f}%"
                  + (f" 쪽당{m['chars_per_page']}" if "chars_per_page" in m else "")
                  + (f" KB당{m['chars_per_kb']}" if "chars_per_kb" in m else ""))
            for w in why:
                print(f"       └ {w}")

    if args.json:
        print(json.dumps({"total": len(out), "failed": nfail, "items": out},
                         ensure_ascii=False, indent=1))
    else:
        print(f"\n{len(out) - nfail}/{len(out)} 통과"
              + (f" — FAIL {nfail}건" if nfail else ""))
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
