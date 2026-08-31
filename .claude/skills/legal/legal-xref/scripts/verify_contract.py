#!/usr/bin/env python3
"""output/legal/xref/ 계약 검증.

  기본    스키마 + 값 도메인 + 교차 제약 + 역인덱스 정합성
  --full  위 + 참조 표기가 근거 발췌와 원문 줄에 실재하는지 전건 대조

통과가 갱신 완료 조건이다. 실패 시 종료코드 1.

입력  output/legal/xref/{xref_index,xref_by_article,_xref_report}.json
      output/legal/markdown/ (--full 원문 대조)
      output/legal/statute/statute_master.json (정본 명칭 대조)
출력  표준출력 검증 결과
"""

import argparse
import collections
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import xref_common as X                                        # noqa: E402

BASE = os.path.dirname(HERE)
CONTRACT = os.path.join(BASE, "contract")

fails, warns = [], []
checked_schemas = set()


def fail(m):
    fails.append(m)


def warn(m):
    warns.append(m)


def load(p):
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        fail(f"파일 없음: {p}")
        return None
    except json.JSONDecodeError as exc:
        fail(f"JSON 파싱 실패: {p}:{exc.lineno}:{exc.colno} {exc.msg}")
        return None
    return data


def check_closed_schema(node, label, path=""):
    if isinstance(node, dict):
        is_object_schema = (
            node.get("type") == "object"
            or "properties" in node
            or "required" in node
        )
        if (
            is_object_schema
            and node.get("additionalProperties") is not False
        ):
            fail(f"스키마[{label}]: {path or '(root)'} 객체가 additionalProperties=false 로 닫히지 않았다")
        for key, value in node.items():
            if key in ("$comment", "description", "title"):
                continue
            child = f"{path}/{key}" if path else key
            check_closed_schema(value, label, child)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            check_closed_schema(value, label, f"{path}/{i}")


def load_schema(schema_file):
    p = os.path.join(CONTRACT, schema_file)
    if not os.path.exists(p):
        fail(f"스키마 파일 없음: {p}")
        return None
    schema = load(p)
    if schema is None:
        return None
    if p not in checked_schemas:
        check_closed_schema(schema, schema_file)
        checked_schemas.add(p)
    return schema


def check_schema(data, schema_name, label):
    try:
        import jsonschema
    except ImportError:
        warn("jsonschema 미설치 — 스키마 검증을 건너뛰었다 (pip install jsonschema)")
        return
    schema = load_schema(schema_name)
    if schema is None:
        return
    v = jsonschema.Draft7Validator(schema)
    errs = sorted(v.iter_errors(data), key=lambda e: list(e.path))
    for e in errs[:15]:
        loc = "/".join(str(x) for x in e.path) or "(root)"
        fail(f"스키마[{label}]: {loc} — {e.message}")
    if len(errs) > 15:
        fail(f"스키마[{label}]: 외 {len(errs) - 15}건")


def check_schema_ref(data, schema_file, def_name, label):
    """table_refs.schema.json 처럼 definitions 아래 여러 최상위 스키마를 담은 파일용.
    `{"$ref": "#/definitions/<def_name>", <스키마 파일 전체>}` 래퍼로 내부 참조를 그대로 살린다."""
    try:
        import jsonschema
    except ImportError:
        warn("jsonschema 미설치 — 스키마 검증을 건너뛰었다 (pip install jsonschema)")
        return
    full = load_schema(schema_file)
    if full is None:
        return
    wrapper = {"$ref": f"#/definitions/{def_name}",
               **{k: v for k, v in full.items() if k != "$id"}}
    v = jsonschema.Draft7Validator(wrapper)
    errs = sorted(v.iter_errors(data), key=lambda e: list(e.path))
    for e in errs[:15]:
        loc = "/".join(str(x) for x in e.path) or "(root)"
        fail(f"스키마[{label}]: {loc} — {e.message}")
    if len(errs) > 15:
        fail(f"스키마[{label}]: 외 {len(errs) - 15}건")


def check_files(outdir, outputs):
    if not isinstance(outputs, dict) or not isinstance(outputs.get("files"), dict):
        fail("outputs.json: files 객체가 없다")
        return
    for name, spec in outputs["files"].items():
        p = os.path.join(outdir, name)
        if not os.path.exists(p):
            fail(f"산출물 없음: {p}")
            continue
        data = load(p)
        if not isinstance(data, dict):
            fail(f"{name}: JSON 루트가 객체가 아니다")
            continue
        for k in spec.get("topKeys", []):
            if k not in data:
                fail(f"{name}: 최상위 키 없음 — {k}")


def check_domains(recs, dom, label):
    for field, allowed in dom.items():
        if field.startswith("$") or isinstance(allowed, dict):
            continue
        seen = {r.get(field) for r in recs if r.get(field) is not None}
        extra = seen - set(allowed)
        if extra:
            fail(f"값 도메인[{label}.{field}]: 계약에 없는 값 {sorted(extra)} "
                 "— 판정 로직이 바뀐 것이므로 계약을 먼저 갱신한다")
    seen = {r.get("scope_basis") for r in recs if r.get("scope_basis")}
    extra = seen - set(dom["scope_basis"])
    if extra:
        fail(f"값 도메인[{label}.scope_basis]: 계약에 없는 값 {sorted(extra)}")
    mst = {r["target"].get("master_status") for r in recs
           if r.get("target", {}).get("master_status")}
    extra = mst - set(dom["master_status"])
    if extra:
        fail(f"값 도메인[{label}.target.master_status]: {sorted(extra)}")


def check_index(idx, rep):
    meta, xr = idx["meta"], idx["xrefs"]
    iso = rep["isolated"]
    if meta["참조수"] != len(xr):
        fail(f"meta.참조수 {meta['참조수']} != xrefs 길이 {len(xr)}")
    if meta["격리수"] != len(iso):
        fail(f"meta.격리수 {meta['격리수']} != isolated 길이 {len(iso)}")

    ids_i = [r["xref_id"] for r in xr]
    ids_r = [r["xref_id"] for r in iso]
    if len(set(ids_i)) != len(ids_i):
        fail("xref_id 중복 — xref_index")
    if set(ids_i) & set(ids_r):
        fail("xref_id 가 xref_index 와 _xref_report 에 동시에 있다")
    allids = sorted(set(ids_i) | set(ids_r))
    if len(allids) != len(xr) + len(iso):
        fail("xref_id 총수가 두 파일 합과 다르다")
    expect = [f"X{i:06d}" for i in range(1, len(allids) + 1)]
    if allids != expect:
        fail("xref_id 가 X000001 부터 빈 번호 없이 이어지지 않는다 — "
             "격리가 분류가 아니라 누락으로 보인다")

    def check_source_path(r):
        lb = r["xref_id"]
        src = r["source_file"]
        if os.path.isabs(src) or ".." in src.split(os.sep):
            fail(f"{lb}: source_file 이 상대 정본 경로가 아니다 — {src}")
        if os.path.normpath(src) != src or not src.startswith("output/legal/markdown/"):
            fail(f"{lb}: source_file 경로 규약 위반 — {src}")

    for r in xr:
        lb = r["xref_id"]
        check_source_path(r)
        if r["scope"] not in ("내부", "외부"):
            fail(f"{lb}: xref_index 에 scope={r['scope']} 이 있다 — 미판정은 격리한다")
        if r["resolution"] == "미해소":
            fail(f"{lb}: xref_index 에 미해소가 있다 — 격리한다")
        t = r["target"]
        if (t["후보수"] or 0) > 1 and t["article_iri"]:
            fail(f"{lb}: 후보수 {t['후보수']} 인데 article_iri 를 채웠다 — 추측이다")
        if t["master_status"] in ("미수록", "미대조") and t["statute_official"]:
            fail(f"{lb}: {t['master_status']}인데 statute_official 이 차 있다")
        if t["article_iri"] and not t["article_iri"].startswith(
                f"guidelineArticle/{r['dstrcAppnNo']}/"):
            fail(f"{lb}: target.article_iri 의 지구번호가 출처와 다르다")
        if r["article_origin"] == "조문헤딩" and not (
                r["article_iri"] and r["article_no"] and r["article_label"]):
            fail(f"{lb}: article_origin=조문헤딩 인데 조문 식별자가 비었다")
        if r["kind"] == "범위":
            if not r["range"]:
                fail(f"{lb}: kind=범위 인데 range 가 없다")
            elif r["resolution"] == "범위전개" and not r["range"]["expanded"]:
                fail(f"{lb}: resolution=범위전개 인데 expanded 가 비었다")
        if r["kind"] == "별표" and not r["target"]["annex"]:
            fail(f"{lb}: kind=별표 인데 target.annex 가 비었다")
        if r["kind"] == "별지·서식" and not r["target"]["form"]:
            fail(f"{lb}: kind=별지·서식 인데 target.form 이 비었다")
        if r["kind"] == "준용":
            fail(f"{lb}: kind=준용 은 대상 표기가 없는 준용 서술이므로 격리 대상이다")

    for r in iso:
        check_source_path(r)
        if r["scope"] in ("내부", "외부") and r["resolution"] != "미해소":
            fail(f"{r['xref_id']}: 해소된 레코드가 격리되어 있다")


def check_master(xr, master_path):
    if not os.path.exists(master_path):
        warn(f"statute_master.json 없음 — 정본 명칭 대조를 건너뛰었다")
        return 0, 0
    master = {s["statute_key"]: s for s in load(master_path)["statutes"]}
    ok = bad = 0
    for r in xr:
        t = r["target"]
        if not t["statute_key"]:
            continue
        st = master.get(t["statute_key"])
        if t["master_status"] == "정본대조":
            if not st:
                fail(f"{r['xref_id']}: 정본대조인데 statute_master 에 없다")
                bad += 1
                continue
            if t["statute_official"] != (st.get("정식명칭") or ""):
                fail(f"{r['xref_id']}: statute_official 이 정본과 다르다")
                bad += 1
                continue
            ok += 1
        elif t["master_status"] == "미대조":
            if not st:
                fail(f"{r['xref_id']}: 미대조인데 statute_master 에 없다")
                bad += 1
            elif st.get("검증상태") == "정본대조":
                fail(f"{r['xref_id']}: 미대조인데 statute_master 는 정본대조다")
                bad += 1
        elif st:
            fail(f"{r['xref_id']}: 미수록인데 statute_master 에 있다")
            bad += 1
    return ok, bad


def check_by_article(idx, bya):
    ids = {r["xref_id"] for r in idx["xrefs"]}
    if bya["meta"]["원본_참조수"] != len(ids):
        fail(f"by_article.meta.원본_참조수 {bya['meta']['원본_참조수']} != 참조수 {len(ids)}")
    if bya["meta"]["조문키수"] != len(bya["by_article"]):
        fail(f"by_article.meta.조문키수 {bya['meta']['조문키수']} != by_article 길이 {len(bya['by_article'])}")
    if bya["meta"]["대상키수"] != len(bya["by_target"]):
        fail(f"by_article.meta.대상키수 {bya['meta']['대상키수']} != by_target 길이 {len(bya['by_target'])}")

    seen = set()
    for a in bya["by_article"]:
        seen |= set(a["xref_ids"])
        if a["참조수"] != len(a["xref_ids"]):
            fail(f"by_article[{a['article_key']}]: 참조수 != xref_ids 길이")
    if seen != ids:
        fail(f"by_article 의 xref_id 합집합이 xref_index 와 다르다 "
             f"(빠짐 {len(ids - seen)} · 남음 {len(seen - ids)})")
    tot = sum(t["참조수"] for t in bya["by_target"])
    if tot + bya["meta"]["대상키_미발급"] != len(idx["xrefs"]):
        fail(f"by_target 참조수 합 {tot} + 미발급 "
             f"{bya['meta']['대상키_미발급']} != 참조수 {len(idx['xrefs'])}")
    for t in bya["by_target"]:
        if t["참조수"] != len(t["refs"]):
            fail(f"by_target[{t['target_key']}]: 참조수 != refs 길이")
    target_seen = {ref["xref_id"] for t in bya["by_target"] for ref in t["refs"]}
    if target_seen - ids:
        fail(f"by_target 이 xref_index 에 없는 xref_id 를 가리킨다 — {sorted(target_seen - ids)[:5]}")


def check_substring(recs, md_root, label, docs_cache):
    """환각 전수 검사 — 표기가 근거 발췌와 원문 줄에 실재하는가."""
    miss_q = miss_l = 0
    for r in recs:
        s = r["surface"]
        if s not in r["quote"]:
            miss_q += 1
            if miss_q <= 5:
                fail(f"{r['xref_id']}[{label}]: surface 가 quote 밖에 있다 "
                     f"— {s[:40]!r}")
        p = os.path.join(md_root, os.path.relpath(r["source_file"],
                                                  "output/legal/markdown"))
        if p not in docs_cache:
            if not os.path.exists(p):
                docs_cache[p] = None
            else:
                with open(p, encoding="utf-8", errors="replace") as f:
                    docs_cache[p] = f.read().split("\n")
        lines = docs_cache[p]
        if lines is None:
            fail(f"{r['xref_id']}: 원문 파일 없음 {p}")
            continue
        if not (1 <= r["line"] <= len(lines)):
            miss_l += 1
            continue
        line = lines[r["line"] - 1]
        m = X.HEADING_RE.match(line.rstrip())
        if m:
            line = m.group(2)
        if s not in line:
            miss_l += 1
            if miss_l <= 5:
                fail(f"{r['xref_id']}[{label}]: surface 가 원문 줄에 없다 "
                     f"— {s[:40]!r} @ {r['source_file']}:{r['line']}")
    if miss_q > 5:
        fail(f"[{label}] surface 가 quote 밖: 외 {miss_q - 5}건")
    if miss_l > 5:
        fail(f"[{label}] surface 가 원문 줄에 없음: 외 {miss_l - 5}건")
    return miss_q, miss_l


def check_range(recs, md_root, docs_cache):
    """범위 전개는 양 끝 조문이 대상 문서에 실재할 때만 한다."""
    bad = 0
    trees = {}
    for r in recs:
        if r["kind"] != "범위" or not (r["range"] or {}).get("expanded"):
            continue
        p = os.path.join(md_root, os.path.relpath(r["source_file"],
                                                  "output/legal/markdown"))
        if p not in trees:
            with open(p, encoding="utf-8", errors="replace") as f:
                trees[p] = X.parse_document(f.read())
        doc = trees[p]
        have = {a["조번호"] for a in doc["articles"]}
        for no in r["range"]["expanded"]:
            if no not in have:
                bad += 1
                fail(f"{r['xref_id']}: 전개한 {no} 이 문서 조문 트리에 없다")
    return bad


def check_table_refs_domains(edges, iso, dom):
    seen_mt = {r.get("마커유형") for r in edges + iso}
    extra = seen_mt - set(dom["table_refs.마커유형"])
    if extra:
        fail(f"값 도메인[table_refs.마커유형]: 계약에 없는 값 {sorted(extra)}")
    seen_mm = {r.get("매칭방법") for r in edges}
    extra = seen_mm - set(dom["table_refs.매칭방법"])
    if extra:
        fail(f"값 도메인[table_refs.매칭방법]: 계약에 없는 값 {sorted(extra)}")
    seen_reason = {r.get("사유") for r in iso}
    extra = seen_reason - set(dom["table_refs.격리사유"])
    if extra:
        fail(f"값 도메인[table_refs.격리사유]: 계약에 없는 값 {sorted(extra)}")


def check_table_refs(tr, rep, tables_csv):
    meta_tr, edges = tr["meta"], tr["간선"]
    meta_rep, iso, agg = rep["meta"], rep["격리"], rep["격리_사유별_집계"]
    counts = meta_tr["모수"]

    if counts["해소"] != len(edges):
        fail(f"table_refs.meta.모수.해소 {counts['해소']} != 간선 길이 {len(edges)}")
    if counts["격리"] != len(iso):
        fail(f"table_refs.meta.모수.격리 {counts['격리']} != _table_refs_report.격리 길이 {len(iso)}")
    if counts["조문단위_유니크마커간선후보수"] != len(edges) + len(iso):
        fail("table_refs.meta.모수.조문단위_유니크마커간선후보수 "
             f"{counts['조문단위_유니크마커간선후보수']} != 간선+격리 {len(edges) + len(iso)}")
    if counts["추출마커_원발생수"] < counts["조문단위_유니크마커간선후보수"]:
        fail("table_refs.meta.모수.추출마커_원발생수 가 유니크 후보수보다 작다")
    if meta_tr["모수"] != meta_rep["모수"]:
        fail("table_refs.meta.모수 와 _table_refs_report.meta.모수 가 다르다 — 같은 실행 산출물이 아니다")

    if sum(agg.values()) != len(iso):
        fail(f"격리_사유별_집계 합 {sum(agg.values())} != 격리 길이 {len(iso)}")

    if not os.path.exists(tables_csv):
        warn(f"tables.csv 없음 — 표ID 실재 대조를 건너뛰었다: {tables_csv}")
        table_ids = None
    else:
        table_ids = set()
        with open(tables_csv, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                table_ids.add(row["표ID"])

    def check_id(tid, dstrc, where):
        if table_ids is not None and tid not in table_ids:
            fail(f"{where}: 표ID {tid} 가 tables.csv 에 없다")
        if not tid.startswith(dstrc + "-"):
            fail(f"{where}: 표ID {tid} 의 지구번호 접두가 {dstrc} 와 다르다")

    edge_required = {"지구번호", "조번호", "행", "마커유형", "정규화라벨", "표ID"}
    iso_required = {"지구번호", "조번호", "행", "마커유형", "정규화라벨", "사유"}

    seen_keys = collections.Counter()
    for r in edges:
        missing = edge_required - set(r)
        if missing:
            fail(f"table_refs 간선 필수키 없음: {sorted(missing)}")
            continue
        loc = f"간선[{r['지구번호']}/{r.get('조번호')}/{r['정규화라벨']}]"
        check_id(r["표ID"], r["지구번호"], loc)
        if r["매칭방법"] == "분책좁힘":
            mg = r["매칭근거"]
            if "원본파일_추정" not in mg or "전체후보수" not in mg:
                fail(f"{loc}: 매칭방법=분책좁힘 인데 매칭근거에 원본파일_추정·전체후보수가 없다")
            elif mg["전체후보수"] < 2:
                fail(f"{loc}: 분책좁힘의 전체후보수가 {mg['전체후보수']} — 2 이상이어야 한다")
        k = (r["지구번호"], r.get("조번호"), tuple(r["행"]) if r.get("행") else None,
             r["마커유형"], r["정규화라벨"])
        seen_keys[k] += 1

    for r in iso:
        missing = iso_required - set(r)
        if missing:
            fail(f"table_refs 격리 필수키 없음: {sorted(missing)}")
            continue
        loc = f"격리[{r['지구번호']}/{r.get('조번호')}/{r['정규화라벨']}]"
        has_digit = bool(re.search(r"\d", r["정규화라벨"]))
        if not has_digit and r["사유"] != "표기해석불가":
            fail(f"{loc}: 라벨에 숫자가 없는데 사유가 {r['사유']} — 표기해석불가여야 한다")
        if has_digit and r["사유"] == "표기해석불가":
            fail(f"{loc}: 라벨에 숫자가 있는데 사유가 표기해석불가다")
        if r["사유"] == "후보다중":
            cands = r.get("후보", [])
            if len(cands) < 2:
                fail(f"{loc}: 사유=후보다중 인데 후보가 {len(cands)}개다")
            for c in cands:
                check_id(c["표ID"], r["지구번호"], loc)
        k = (r["지구번호"], r.get("조번호"), tuple(r["행"]) if r.get("행") else None,
             r["마커유형"], r["정규화라벨"])
        seen_keys[k] += 1

    dup = {k: v for k, v in seen_keys.items() if v > 1}
    if dup:
        fail(f"table_refs: (지구,조,행,유형,라벨) 조합 중복 {len(dup)}건 "
             f"— 예 {list(dup.items())[:3]}")


def check_table_refs_full(edges, iso, coll_path, md_dir):
    """환각 전수 검사 — 원문마커가 건축부문_수합.json 조문 원문에 실재하는지,
    원본파일 판정이 md 병합 경계 주석과 일치하는지 대조한다."""
    if not os.path.exists(coll_path):
        warn(f"건축부문_수합.json 없음 — table_refs 원문 대조를 건너뛰었다: {coll_path}")
        return 0, 0

    art_text = {}
    boundary_cache = {}
    with open(coll_path, encoding="utf-8") as f:
        coll = json.load(f)
    for d in coll["districts"]:
        for a in d.get("조문", []):
            key = (d["지구번호"], tuple(a["행"]) if a.get("행") else None)
            art_text[key] = a["원문"]

    def boundaries(지역, 지구명):
        key = (지역, 지구명)
        if key in boundary_cache:
            return boundary_cache[key]
        p = os.path.join(md_dir, 지역, 지구명 + ".md")
        marks = []
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for i, line in enumerate(f):
                    m = re.match(r"^<!--\s*원본:\s*(.+?)\s*-->\s*$", line.rstrip("\n"))
                    if m:
                        marks.append((i + 1, m.group(1)))
        boundary_cache[key] = marks
        return marks

    def expect_file(마크, line):
        fn = None
        for ln, name in 마크:
            if ln <= line:
                fn = name
            else:
                break
        return fn

    miss_marker = miss_src = 0
    for r in edges + iso:
        key = (r["지구번호"], tuple(r["행"]) if r.get("행") else None)
        text = art_text.get(key)
        if text is None:
            miss_marker += 1
            if miss_marker <= 5:
                fail(f"{key}: 건축부문_수합.json 에 해당 조문(지구·행)이 없다")
            continue
        for marker in r["원문마커"]:
            if marker not in text:
                miss_marker += 1
                if miss_marker <= 5:
                    fail(f"{key}: 원문마커 {marker!r} 가 조문 원문에 없다 — 환각")

        marks = boundaries(r["지역"], r["지구명"])
        line0 = r["행"][0] if r.get("행") else None
        expected = expect_file(marks, line0) if line0 else None
        if expected != r.get("원본파일"):
            miss_src += 1
            if miss_src <= 5:
                fail(f"{key}: 원본파일 {r.get('원본파일')!r} 이 md 경계 재계산값 "
                     f"{expected!r} 과 다르다")
    if miss_marker > 5:
        fail(f"table_refs 원문마커 대조 실패: 외 {miss_marker - 5}건")
    if miss_src > 5:
        fail(f"table_refs 원본파일 재계산 불일치: 외 {miss_src - 5}건")
    return miss_marker, miss_src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="output/legal/xref")
    ap.add_argument("--md-dir", default="output/legal/markdown")
    ap.add_argument("--statute-dir", default="output/legal/statute")
    ap.add_argument("--tables-csv", default="output/legal/table/tables.csv")
    ap.add_argument("--coll", default="output/legal/건축부문/건축부문_수합.json")
    ap.add_argument("--full", action="store_true",
                    help="표기가 근거 발췌·원문 줄에 실재하는지 전건 대조")
    a = ap.parse_args()

    outputs = load(os.path.join(CONTRACT, "outputs.json"))
    if outputs is None:
        print("계약 검증 실패")
        for m in fails:
            print("  ✗", m)
        return 1
    check_files(a.dir, outputs)
    if fails:
        print("계약 검증 실패")
        for m in fails:
            print("  ✗", m)
        return 1

    idx = load(os.path.join(a.dir, "xref_index.json"))
    rep = load(os.path.join(a.dir, "_xref_report.json"))
    bya = load(os.path.join(a.dir, "xref_by_article.json"))
    if not all(isinstance(x, dict) for x in (idx, rep, bya)):
        print("계약 검증 실패")
        for m in fails:
            print("  ✗", m)
        return 1

    check_schema(idx, "xref.schema.json", "xref_index")
    check_schema({"meta": {**idx["meta"], **rep["meta"]},
                  "xrefs": rep["isolated"]}, "xref.schema.json", "격리")
    check_domains(idx["xrefs"], outputs["값도메인"], "xref_index")
    check_domains(rep["isolated"], outputs["값도메인"], "격리")
    check_index(idx, rep)
    ok, bad = check_master(idx["xrefs"],
                           os.path.join(a.statute_dir, "statute_master.json"))
    check_by_article(idx, bya)

    tr = load(os.path.join(a.dir, "table_refs.json"))
    rep_tr = load(os.path.join(a.dir, "_table_refs_report.json"))
    if not all(isinstance(x, dict) for x in (tr, rep_tr)):
        print("계약 검증 실패")
        for m in fails:
            print("  ✗", m)
        return 1
    check_schema_ref(tr, "table_refs.schema.json", "table_refs", "table_refs")
    check_schema_ref(rep_tr, "table_refs.schema.json", "table_refs_report", "_table_refs_report")
    check_table_refs_domains(tr["간선"], rep_tr["격리"], outputs["값도메인"])
    check_table_refs(tr, rep_tr, a.tables_csv)

    cache = {}
    subs = rngs = None
    tr_full = None
    if a.full:
        mq1, ml1 = check_substring(idx["xrefs"], a.md_dir, "index", cache)
        mq2, ml2 = check_substring(rep["isolated"], a.md_dir, "격리", cache)
        subs = (mq1 + mq2, ml1 + ml2)
        rngs = check_range(idx["xrefs"], a.md_dir, cache)
        tr_full = check_table_refs_full(tr["간선"], rep_tr["격리"], a.coll, a.md_dir)

    print(f"검사 대상 — 참조 {len(idx['xrefs']):,} · 격리 {len(rep['isolated']):,} "
          f"· 조문키 {len(bya['by_article']):,} · 대상키 {len(bya['by_target']):,}")
    print(f"table_refs — 간선 {len(tr['간선']):,} · 격리 {len(rep_tr['격리']):,}")
    print(f"정본 명칭 대조 {ok:,}건 일치 / {bad}건 불일치")
    if a.full:
        print(f"환각 전수 검사 — 표기가 근거 발췌 밖 {subs[0]}건 · "
              f"원문 줄에 없음 {subs[1]}건")
        print(f"범위 전개 실재 검사 — 트리에 없는 전개 조문 {rngs}건")
        print(f"table_refs 원문 대조 — 원문마커 환각 {tr_full[0]}건 · "
              f"원본파일 재계산 불일치 {tr_full[1]}건")
    else:
        print("환각 전수 검사 미실행 — --full 로 돈다")
    for m in warns:
        print("  !", m)
    if fails:
        print(f"\n계약 검증 실패 {len(fails)}건")
        for m in fails[:40]:
            print("  ✗", m)
        if len(fails) > 40:
            print(f"  … 외 {len(fails) - 40}건")
        return 1
    print("\n계약 검증 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
