#!/usr/bin/env python3
"""문서 변환 — 게이트 내장

사용:
    convert.py orig.pdf                    # → orig.txt
    convert.py orig.hwp -o out/            # 출력 디렉터리 지정
    convert.py indir/ -o out/              # 일괄
    convert.py indir/ -o out/ --json       # 기계 판독

종료 코드: 0=전건 통과, 1=하나라도 실패
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify import check  # noqa: E402  게이트는 같은 판정기를 쓴다

TIMEOUT = 600
HWP5_MAGIC = b"\xd0\xcf\x11\xe0"

# 지구 디렉터리에는 시행지침 본문 말고 도면 첨부(fileRegistNo=2·4·5)도 들어온다.
# 확장자만 보면 도면 .pdf·.zip 이 본문으로 오인돼 코퍼스에 섞인다. 어간으로 좁힌다.
# 어간표 정본은 legal-dup/contract/commands.json 의 assetStems 이고 본문은 코드 7이다.
BODY_STEM = "지구단위계획 시행지침"


def _run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, **kw)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _tool(name):
    """pyhwp 실행 파일 탐색: 환경변수 → PATH → 흔한 venv 위치."""
    env = os.environ.get("HWP5TXT")
    if env:
        c = os.path.join(os.path.dirname(env), name)
        if os.path.exists(c):
            return c
    return shutil.which(name)


# ── PDF 경로 ────────────────────────────────────────────────────────────
def pdf_layout(src):
    r = _run(["pdftotext", "-layout", "-enc", "UTF-8", src, "-"])
    return r.stdout.decode("utf-8", "replace") if r and r.returncode == 0 else ""


def fold_padding(text):
    """행 중간의 과도한 공백만 접는다.

    행 선두 들여쓰기는 조문 계층 정보라 8칸까지 보존한다. 전부 접으면
    편·장·조 구조가 뭉개진다.
    """
    text = text.replace("\x0c", "\n\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"(?m)^[ \t]{9,}", " " * 8, text)
    text = re.sub(r"(?<=\S)[ \t]{9,}(?=\S)", " " * 4, text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{4,}", "\n\n\n", text).strip() + "\n"


def pdf_raw(src):
    r = _run(["pdftotext", "-enc", "UTF-8", src, "-"])
    return r.stdout.decode("utf-8", "replace") if r and r.returncode == 0 else ""


# ── HWP 경로 ────────────────────────────────────────────────────────────
def hwp_txt(src):
    t = _tool("hwp5txt")
    if not t:
        return ""
    r = _run([t, src])
    return r.stdout.decode("utf-8", "replace") if r and r.returncode == 0 else ""


def hwp_html(src):
    """표 셀 내용까지 회수. hwp5txt는 표를 <표> 마커로만 내보낸다."""
    t = _tool("hwp5html")
    if not t:
        return ""
    with tempfile.TemporaryDirectory() as td:
        r = _run([t, "--output", td, src])
        page = os.path.join(td, "index.xhtml")
        if not r or r.returncode != 0 or not os.path.exists(page):
            return ""
        raw = open(page, encoding="utf-8", errors="replace").read()
    raw = re.sub(r"(?s)<(style|script)\b.*?</\1>", " ", raw)
    # 인라인 태그는 공백으로, 블록 태그만 개행으로. 전부 개행으로 바꾸면
    # 문장 중간의 <span> 하나가 줄바꿈이 되어 본문이 글자 단위로 파편화된다
    # (실측: 2자 이하 줄 39%, 불릿 '•'이 111회 독립 줄로 분리).
    raw = re.sub(r"(?is)</?(p|div|tr|br|li|h[1-6]|table|tbody|thead)\b[^>]*>",
                 "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw).replace("&#13;", "\n")
    raw = raw.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    lines = []
    for l in raw.splitlines():
        l = re.sub(r"[ \t]{2,}", " ", l).strip()
        if not l:
            continue
        # 불릿·가운뎃점만 남은 줄은 다음 줄 머리에 붙인다
        if re.fullmatch(r"[•·․∙‧⋅°※]+", l):
            lines.append(("\x00", l))
        else:
            lines.append(("", l))
    out = []
    pend = ""
    for kind, l in lines:
        if kind == "\x00":
            pend = l + " "
            continue
        out.append(pend + l)
        pend = ""
    if pend:
        out.append(pend.strip())
    return "\n".join(out)


CJK_OK = re.compile(r"[가-힣0-9A-Za-z\s.,()（）·․…\-~%㎡㎞№「」『』‘’“”:;/+±×°①-⑳㉠-㉻]")


def hwp_proc(src):
    """pyhwp가 XML 생성 중 죽는 문서용. 레코드 헤더 오독 잡음을 걸러낸다."""
    t = _tool("hwp5proc")
    if not t:
        return ""
    r = _run([t, "ls", src])
    if not r or r.returncode != 0:
        return ""
    secs = [l for l in r.stdout.decode("utf-8", "replace").split()
            if l.startswith("BodyText/")]
    out = []
    for s in secs:
        rr = _run([t, "cat", src, s])
        if not rr or rr.returncode != 0:
            continue
        u = re.sub(r"[\x00-\x1f-]+", "\n", rr.stdout.decode("utf-16-le", "ignore"))
        for line in u.splitlines():
            line = line.strip()
            if len(line) < 4 or not re.search(r"[가-힣]", line):
                continue
            if sum(1 for ch in line if CJK_OK.match(ch)) / len(line) < 0.9:
                continue
            out.append(line)
    return "\n".join(out)


# ── HWPX 경로 ───────────────────────────────────────────────────────────
HWPX_PARA_NS = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HWPX_SECTION_RE = re.compile(r"Contents/section\d+\.xml$")


def is_hwpx(src):
    """HWPX 는 ZIP 이라 매직이 PK 다. 일반 ZIP 과 가르는 신호는 section XML 이다."""
    try:
        with zipfile.ZipFile(src) as zf:
            return any(HWPX_SECTION_RE.fullmatch(n) for n in zf.namelist())
    except Exception:
        return False


def _hwpx_text_of(el):
    """hp:t 아래 텍스트를 전부 모은다.

    t.text 만 읽으면 안 된다. hp:t 안에 markpen·글자겹침 같은 인라인 요소가
    들어가면 그 뒤 텍스트가 요소의 tail 로 빠져 문장이 조용히 잘린다. 글자 수는
    그럴듯하게 남으므로 길이 검사로는 드러나지 않는다.
    """
    return "".join("".join(t.itertext()) for t in el.iter(HWPX_PARA_NS + "t"))


def _hwpx_cell(tc):
    return _hwpx_text_of(tc).strip()


def _hwpx_emit(el, out):
    """문서 순서를 지키며 문단과 표를 뽑는다.

    표는 hwp5txt 와 같은 <표> 마커를 남기되 셀 텍스트도 잇는다. 마커만 남기면
    표에 든 규범값이 md 에서 사라지고, 셀만 남기면 표 경계를 잃는다.
    """
    for child in el:
        tag = child.tag
        if tag == HWPX_PARA_NS + "tbl":
            out.append("<표>")
            rows = child.findall(HWPX_PARA_NS + "tr") or list(child.iter(HWPX_PARA_NS + "tr"))
            for tr in rows:
                cells = tr.findall(HWPX_PARA_NS + "tc") or list(tr.iter(HWPX_PARA_NS + "tc"))
                row = " | ".join(c for c in (_hwpx_cell(tc) for tc in cells) if c)
                if row:
                    out.append(row)
        elif tag == HWPX_PARA_NS + "p":
            # 표를 품은 문단은 재귀해야 순서가 지켜진다. 아니면 문단 텍스트에
            # 표 셀이 섞여 들어가 같은 값이 두 번 나온다.
            if child.find(".//" + HWPX_PARA_NS + "tbl") is not None:
                _hwpx_emit(child, out)
            else:
                text = _hwpx_text_of(child).strip()
                if text:
                    out.append(text)
        else:
            _hwpx_emit(child, out)


def hwpx_text(src):
    """HWPX(ZIP + HWPML 2011 XML)에서 본문을 뽑는다.

    hwp5 계열 도구는 이 포맷을 열지 못하므로 hwp5txt·hwp5html 로 대체할 수 없다.
    외부 도구 없이 표준 라이브러리만 쓴다.
    """
    out = []
    with zipfile.ZipFile(src) as zf:
        for name in sorted(n for n in zf.namelist() if HWPX_SECTION_RE.fullmatch(n)):
            try:
                root = ET.fromstring(zf.read(name))
            except ET.ParseError:
                continue
            _hwpx_emit(root, out)
    return "\n".join(line for line in out if line.strip())


def routes(src):
    """(이름, 추출함수) 순서대로 시도. 앞이 실패하면 뒤로 넘어간다."""
    with open(src, "rb") as fh:
        magic = fh.read(4)
    if magic[:2] == b"PK" and is_hwpx(src):
        return [("HWPX(section XML)", hwpx_text)]
    if magic == b"%PDF":
        return [("pdftotext -layout", lambda p: fold_padding(pdf_layout(p))),
                ("pdftotext (raw)", pdf_raw)]
    if magic == HWP5_MAGIC:
        return [("hwp5txt", hwp_txt),
                ("hwp5html(표포함)", hwp_html),
                ("hwp5proc(레코드)", hwp_proc)]
    if magic[:4] == b"<?xm":
        return [("hwpml", lambda p: re.sub(
            r"\n{2,}", "\n",
            re.sub(r"<[^>]+>", "\n",
                   open(p, encoding="utf-8", errors="replace").read())).strip())]
    return []


def convert(src, dst):
    """게이트를 통과할 때까지 경로를 바꿔가며 시도.

    반환: (성공여부, 사용경로, 지표, 시도기록)
    모든 경로 실패 시 산출물을 쓰지 않는다.
    """
    rs = routes(src)
    if not rs:
        return False, None, {}, [("(형식 미지원)", "매직바이트 불일치")]
    tried = []
    for name, fn in rs:
        try:
            text = fn(src)
        except Exception as e:
            tried.append((name, f"예외: {e}"))
            continue
        if not text or not text.strip():
            tried.append((name, "빈 결과"))
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(text)
            tmp = tf.name
        verdict, metrics, reasons = check(tmp, src)
        os.unlink(tmp)
        if verdict == "PASS":
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(text)
            return True, name, metrics, tried
        tried.append((name, "; ".join(reasons)))
    return False, None, {}, tried


def main():
    ap = argparse.ArgumentParser(description="문서 변환 (게이트 내장)")
    ap.add_argument("target", help="파일 또는 디렉터리")
    ap.add_argument("-o", "--outdir", help="출력 디렉터리 (기본: 원본 옆)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stem", default=BODY_STEM,
                    help=f"디렉터리 일괄에서 고를 파일 어간 (기본: {BODY_STEM})")
    ap.add_argument("--any-stem", action="store_true",
                    help="어간을 보지 않고 확장자만으로 고른다. 도면 첨부가 섞인다")
    args = ap.parse_args()

    srcs, skipped = [], []
    if os.path.isdir(args.target):
        for root, _, files in os.walk(args.target):
            for f in sorted(files):
                if not f.lower().endswith((".pdf", ".hwp", ".hwpml", ".hwpx", ".zip")):
                    continue
                p = os.path.join(root, f)
                if args.any_stem or os.path.splitext(f)[0] == args.stem:
                    srcs.append(p)
                else:
                    skipped.append(p)
    else:
        # 파일을 직접 지목한 것은 명시적 의사이므로 어간으로 거르지 않는다.
        srcs = [args.target]
    if skipped:
        # 잘라낸 것은 반드시 말한다. 조용히 줄면 전수를 돈 것처럼 읽힌다.
        print(f"[SKIP] 어간 불일치 {len(skipped)}건 — 도면 첨부로 본다 "
              f"(어간 '{args.stem}'. 포함하려면 --any-stem)", file=sys.stderr)
        for p in skipped[:5]:
            print(f"         {os.path.basename(p)}", file=sys.stderr)
        if len(skipped) > 5:
            print(f"         … 외 {len(skipped) - 5}건", file=sys.stderr)
    if not srcs:
        sys.exit("변환 대상 없음")

    # ZIP 내부의 문서를 펼친다. 확장자만 보고 거르면 ZIP 안에 편별로 쪼개져
    # 들어간 HWP를 통째로 놓친다(실측: 단일 88건 대비 ZIP 내부 150건).
    expanded, tmpdirs = [], []
    for s in srcs:
        with open(s, "rb") as fh:
            if fh.read(4)[:2] != b"PK":
                expanded.append((s, None))
                continue
        try:
            zf = zipfile.ZipFile(s)
        except Exception as e:
            print(f"[FAIL] {os.path.basename(s)} — zip 열기 실패: {e}")
            continue
        names = [n for n in zf.namelist()
                 if n.lower().endswith((".pdf", ".hwp", ".hwpml", ".hwpx"))
                 and not n.endswith("/")]
        if not names:
            print(f"[SKIP] {os.path.basename(s)} — zip 내부에 문서 없음")
            zf.close()
            continue
        td = tempfile.mkdtemp()
        tmpdirs.append(td)
        for n in names:
            flat = re.sub(r"[\x00-\x1f/\\]", "_", n)
            p = os.path.join(td, flat)
            with zf.open(n) as zi, open(p, "wb") as fo:
                shutil.copyfileobj(zi, fo)
            stem = os.path.splitext(os.path.basename(s))[0]
            expanded.append((p, f"{stem}__{os.path.splitext(flat)[0]}"))
        zf.close()
    srcs = expanded

    ok, bad = [], []
    for src, override in srcs:
        stem = override or os.path.splitext(os.path.basename(src))[0]
        dst = os.path.join(args.outdir or os.path.dirname(src) or ".", stem + ".txt")
        good, route, m, tried = convert(src, dst)
        rec = {"src": src, "txt": dst if good else None, "route": route,
               "chars": m.get("chars"), "solid_ratio": m.get("solid_ratio"),
               "attempts": [{"route": r, "result": w} for r, w in tried]}
        if good:
            ok.append(rec)
            if not args.json:
                print(f"[PASS] {os.path.basename(src)} → {m['chars']:,}자 "
                      f"실질{m['solid_ratio']*100:.0f}% [{route}]")
                for r, w in tried:
                    print(f"       └ {r} 실패: {w}")
        else:
            bad.append(rec)
            if not args.json:
                print(f"[FAIL] {os.path.basename(src)} — 모든 경로 실패")
                for r, w in tried:
                    print(f"       └ {r}: {w}")

    if args.json:
        print(json.dumps({"converted": len(ok), "failed": len(bad),
                          "ok": ok, "failed_items": bad},
                         ensure_ascii=False, indent=1))
    else:
        print(f"\n변환 {len(ok)} / 실패 {len(bad)}")
        if bad:
            print("실패분은 산출물을 만들지 않았다. 사유는 위 참조.")
    for td in tmpdirs:
        shutil.rmtree(td, ignore_errors=True)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
