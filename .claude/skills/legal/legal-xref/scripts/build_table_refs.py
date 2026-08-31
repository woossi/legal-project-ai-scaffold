#!/usr/bin/env python3
"""건축부문 수합 조문의 표참조 마커를 output/legal/table/tables.csv 표 레코드에 해소한다.

`output/legal/건축부문/건축부문_수합.json` 의 `표참조` 필드는 상류 스캔 정규식이
여는 괄호+"표"만 캡처해 잘려 있다("<표Ⅱ-1-1>" → "<표"). 이 스크립트는 그 필드를
"이 조문에 표 신호가 있다"는 플래그로만 쓰고, 실제 마커는 같은 레코드의 `원문`
에서 직접 재추출한다 — 괄호형(`<표…>`·`〈별표…〉`·`[별표…]`)과 괄호 없는 bare
`별표N`. `tables.csv` 는 표 번호 전용 컬럼이 없어 `캡션원문`(예: `<표Ⅱ-1-1> 단독
주택용지의…`)의 마커 접두를 정규화해 매칭 키로 쓴다.

판정은 세 갈래다.
  1. 라벨에 숫자가 없으면(`> [표]` 류 md 변환 placeholder, "표계속"·"표 참조" 서술
     조각) 특정 표를 가리키는 표기가 아니라고 보고 매칭을 시도하지 않는다 — 표기해석불가.
  2. 괄호 없는 bare `별표N` 이고 마커 직전 어절이 법령류 접미어(법·시행령·시행규칙·
     조례·고시·지침·기준·규정)로 끝나면 외부 법령의 별표로 보고 매칭을 시도하지
     않는다 — 외부법령별표참조. 괄호형 `<별표N>`은 이 판정을 적용하지 않는다(이
     시행지침 자신의 별표로 tables.csv 에서 실제로 관측됨).
  3. 나머지는 (지구번호, 정규화라벨)로 tables.csv 를 찾는다. 후보가 여럿이고 md
     병합 경계 주석(`<!-- 원본: 파일명 -->`)으로 조문의 원본 파일을 알 수 있으면
     tables.csv `출처문서`의 파일명과 대조해 좁힌다(분책좁힘). 그래도 여럿이면
     확정하지 않고 후보 전체와 함께 격리한다(후보다중).

해소하지 못한 것은 `table_refs.json` 에 넣지 않고 `_table_refs_report.json` 에
사유와 함께 격리한다. legal-xref 절대규칙 1(못 찾은 것을 찾은 것처럼 만들지
않는다)과 같다.

입력  output/legal/건축부문/건축부문_수합.json
      output/legal/table/tables.csv
      output/legal/markdown/<지역>/<지구명>.md (병합 경계 주석 · frontmatter 구성문서수)
출력  output/legal/xref/table_refs.json
      output/legal/xref/_table_refs_report.json
"""

import argparse
import collections
import csv
import json
import os
import re
import sys

BR = re.compile(r'[<〈〔\[]\s*(표|별표)\s*([^<>〈〉〔〕\[\]]*?)\s*[>〉〕\]]')
BARE = re.compile(r'별표\s*(\d+(?:의\s*\d+)?(?:[-–]\d+)?)')
LAW_SUF = ('법령', '시행규칙', '시행령', '법률', '조례', '고시', '지침', '기준', '규정', '법')
ORIGIN_RE = re.compile(r'^<!--\s*원본:\s*(.+?)\s*-->\s*$')


def require_file(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"입력 없음: {path}")


def load_json_object(path):
    require_file(path)
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: JSON 루트가 객체가 아니다")
    return data


def norm_label(typ, content):
    content = re.sub(r'\s+', '', content)
    return typ + content


def last_word(pre):
    pre = pre.replace('\n', ' ')
    toks = re.split(r'\s+', pre.strip())
    w = toks[-1] if toks else ''
    return w.strip('「」｢｣()（）\'"‘’·,.')


def is_law_adjacent(text, start):
    pre = text[max(0, start - 25):start]
    return last_word(pre).endswith(LAW_SUF)


def basename_of(source_path):
    # "경기/화성동탄2 .../지구단위계획 시행지침.zip::05.시행지침-....hwp" -> "05.시행지침-....hwp"
    if '::' in source_path:
        return source_path.rsplit('::', 1)[-1]
    return os.path.basename(source_path)


def build_table_index(tables_csv):
    """tables.csv 색인: (지구번호, 정규화라벨) -> [행 dict, ...] (파일 순서 그대로)."""
    require_file(tables_csv)
    idx = collections.defaultdict(list)
    with open(tables_csv, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            cap = row['캡션원문']
            if not cap:
                continue
            m = BR.match(cap.strip()) or BR.search(cap)
            if not m:
                continue
            idx[(row['지구번호'], norm_label(m.group(1), m.group(2)))].append(row)
    return idx


def get_boundaries(md_dir, 지역, 지구명):
    """md 병합 파일의 원본 경계 주석: [(1-index 줄번호, 원본 파일명), ...]."""
    path = os.path.join(md_dir, 지역, 지구명 + '.md')
    marks = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for i, line in enumerate(f):
                m = ORIGIN_RE.match(line.rstrip('\n'))
                if m:
                    marks.append((i + 1, m.group(1)))
    return marks


def source_file_for_line(marks, line):
    fn = None
    for ln, name in marks:
        if ln <= line:
            fn = name
        else:
            break
    return fn


def is_multi(md_dir, 지역, 지구명):
    path = os.path.join(md_dir, 지역, 지구명 + '.md')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        txt = f.read()
    m = re.search(r'구성문서수:\s*(\d+)', txt)
    return int(m.group(1)) > 1 if m else None


def extract_occurrences(text):
    """조문 원문에서 표/별표 마커 발생을 char 위치 순서로 뽑는다."""
    occurrences = []
    span_used = []
    for m in BR.finditer(text):
        typ, content = m.group(1), m.group(2)
        span_used.append((m.start(), m.end()))
        occurrences.append({'유형': typ, '원문마커': m.group(0),
                             '정규화라벨': norm_label(typ, content),
                             'char': m.start(), '괄호형': True, '법령인접': False})
    for m in BARE.finditer(text):
        if any(s <= m.start() and m.end() <= e for s, e in span_used):
            continue
        occurrences.append({'유형': '별표', '원문마커': m.group(0),
                             '정규화라벨': norm_label('별표', m.group(1)),
                             'char': m.start(), '괄호형': False,
                             '법령인접': is_law_adjacent(text, m.start())})
    occurrences.sort(key=lambda o: o['char'])
    return occurrences


def run(coll_path, tables_csv, md_dir):
    table_idx = build_table_index(tables_csv)
    data = load_json_object(coll_path)
    if not isinstance(data.get('districts'), list):
        raise ValueError(f"{coll_path}: districts 배열이 없다")

    resolved, isolated, stat = [], [], collections.Counter()
    n_articles_with_flag = n_markers_total = 0
    boundary_cache = {}

    for d in data['districts']:
        지구번호, 지구명, 지역 = d['지구번호'], d['지구명'], d['지역']
        key = (지역, 지구명)
        if key not in boundary_cache:
            boundary_cache[key] = get_boundaries(md_dir, 지역, 지구명)
        marks = boundary_cache[key]

        for a in d.get('조문', []):
            if not a.get('표참조'):
                continue
            n_articles_with_flag += 1
            text = a['원문']
            start_line = a['행'][0] if a.get('행') else None
            src_file = source_file_for_line(marks, start_line) if start_line else None

            occurrences = extract_occurrences(text)
            n_markers_total += len(occurrences)

            # 조문 단위 중복 제거: (유형, 정규화라벨) 별로 대표 1건 + 원문마커 집합
            by_label = collections.OrderedDict()
            for occ in occurrences:
                k = (occ['유형'], occ['정규화라벨'])
                if k not in by_label:
                    by_label[k] = {'원문마커집합': set(), '괄호형존재': False,
                                    '법령인접': True, '건수': 0}
                agg = by_label[k]
                agg['원문마커집합'].add(occ['원문마커'])
                agg['건수'] += 1
                if occ['괄호형']:
                    agg['괄호형존재'] = True
                if not occ['법령인접']:
                    agg['법령인접'] = False  # 하나라도 비인접이면 내부 후보로 취급

            base = {'지구번호': 지구번호, '지구명': 지구명, '지역': 지역,
                    '조번호': a.get('조번호'), '조표제': a.get('표제'), '블록': a.get('블록'),
                    '행': a.get('행'), '슬롯': a.get('slot'), '원본파일': src_file}

            for (typ, label), agg in by_label.items():
                rec = dict(base)
                rec.update({'마커유형': typ, '정규화라벨': label,
                            '원문마커': sorted(agg['원문마커집합']), '언급횟수': agg['건수']})

                if not re.search(r'\d', label):
                    stat['표기해석불가'] += 1
                    isolated.append({**rec, '사유': '표기해석불가',
                                      '근거': "라벨에 표 번호(숫자)가 없다 — markdown 표 유실 "
                                              "placeholder(> [표]) 또는 '표계속·표 참조' 류 서술 "
                                              "조각으로 관측됨. 특정 표를 지시하는 표기가 아니라고 "
                                              "판단해 매칭을 시도하지 않았다"})
                    continue

                if typ == '별표' and agg['법령인접'] and not agg['괄호형존재']:
                    stat['외부법령별표참조'] += 1
                    isolated.append({**rec, '사유': '외부법령별표참조',
                                      '근거': '마커 직전 어절이 법령류 접미어로 끝남'
                                              '(법·시행령·시행규칙·조례·고시·지침·기준·규정)'})
                    continue

                cands = table_idx.get((지구번호, label), [])
                if not cands:
                    reason = '외부법령별표참조추정' if typ == '별표' else '대상없음'
                    stat[reason] += 1
                    isolated.append({**rec, '사유': reason,
                                      '근거': 'tables.csv 에 동일 (지구번호, 라벨) 표 레코드 없음'})
                    continue

                if len(cands) > 1 and src_file:
                    narrowed = [c for c in cands if basename_of(c['출처문서']) == src_file]
                    if len(narrowed) == 1:
                        c = narrowed[0]
                        resolved.append({**rec, '표ID': c['표ID'], '매칭방법': '분책좁힘',
                                          '매칭근거': {'캡션원문': c['캡션원문'],
                                                    '출처문서': c['출처문서'],
                                                    '원본파일_추정': src_file,
                                                    '전체후보수': len(cands)}})
                        stat['해소_분책좁힘'] += 1
                        continue
                    cands = narrowed if narrowed else cands

                if len(cands) == 1:
                    c = cands[0]
                    resolved.append({**rec, '표ID': c['표ID'], '매칭방법': '직접일치',
                                      '매칭근거': {'캡션원문': c['캡션원문'],
                                                '출처문서': c['출처문서']}})
                    stat['해소_직접일치'] += 1
                else:
                    stat['후보다중'] += 1
                    iso_rec = {**rec, '사유': '후보다중',
                               '근거': f"tables.csv 에 동일 (지구번호, 라벨) 표 레코드가 "
                                       f"{len(cands)}개 있어 하나로 확정하지 못함",
                               '후보': [{'표ID': c['표ID'], '캡션원문': c['캡션원문'],
                                       '출처문서': c['출처문서']} for c in cands]}
                    if src_file:
                        iso_rec['원본파일_추정'] = src_file
                    isolated.append(iso_rec)

    # 분책 여부별 해소·격리 건수(구성문서수 >= 2)
    multi_stat = collections.Counter()
    multi_cache = {}
    for rec in resolved + isolated:
        key = (rec['지역'], rec['지구명'])
        if key not in multi_cache:
            multi_cache[key] = is_multi(md_dir, *key)
    for rec in resolved:
        multi_stat[('해소', multi_cache[(rec['지역'], rec['지구명'])])] += 1
    for rec in isolated:
        multi_stat[('격리', multi_cache[(rec['지역'], rec['지구명'])])] += 1

    meta = {
        '설명': '건축부문 수합 조문의 표참조 마커를 output/legal/table/tables.csv 표 레코드에 해소',
        '스크립트': 'scripts/build_table_refs.py',
        '입력': ['output/legal/건축부문/건축부문_수합.json', 'output/legal/table/tables.csv',
               'output/legal/markdown/**.md (원본 경계 주석 · 구성문서수)'],
        '정규화규칙': "마커에서 대괄호/홑화살괄호/겹화살괄호(<>〈〉〔〕[])와 내부 공백을 제거하고 "
                  "'유형+숫자열' 형태로 정규화한다(예: '<표Ⅱ-4-1 >' -> '표Ⅱ-4-1', "
                  "'별표 20' -> '별표20'). 로마숫자는 원문 그대로(유니코드 Ⅰ-Ⅹ) 유지하며 "
                  "ASCII 로 바꾸지 않는다.",
        '분책좁힘규칙': "md 병합 파일의 '<!-- 원본: 파일명 -->' 경계 주석으로 조문 시작행이 속한 "
                    "원본 파일을 판정하고, tables.csv 출처문서의 '::' 이후 파일명과 대조해 "
                    "동일 (지구,라벨) 후보를 좁힌다.",
        '외부법령별표판정': "마커 직전 25자 내 마지막 어절이 법·법률·시행령·시행규칙·조례·고시·"
                      "지침·기준·규정으로 끝나는 괄호 없는(bare) '별표' 마커만 표 매칭을 시도하지 "
                      "않고 즉시 외부로 격리한다. 괄호형 '<별표N>'은 이 판정을 적용하지 않고 표 "
                      "매칭을 시도한다(이 시행지침 자신의 별표로 관측됨).",
        '모수': {'표참조_플래그_조문수': n_articles_with_flag,
               '추출마커_원발생수': n_markers_total,
               '조문단위_유니크마커간선후보수': len(resolved) + len(isolated),
               '해소': len(resolved), '격리': len(isolated)},
        '해소방법별': dict(stat),
        '분책여부별_건수': {'해소_분책': multi_stat[('해소', True)],
                      '해소_비분책': multi_stat[('해소', False)],
                      '격리_분책': multi_stat[('격리', True)],
                      '격리_비분책': multi_stat[('격리', False)],
                      '분책판정불가': multi_stat[('해소', None)] + multi_stat[('격리', None)]},
        '사각지대': [
            'tables.csv 에 캡션원문이 없는(빈 문자열) 표는 라벨 색인에서 제외되어 매칭 후보에서 원천적으로 빠진다.',
            '조문 원문 안의 괄호 없는 표 마커("표2-1과 같다" 류, 별표 아님)는 상류 표참조 신호(R_TBL)가 별표만 bare로 잡으므로 이 스캔에도 없다.',
            '그림 마커(<그림…>)는 표 채널이 아니므로 대상에서 제외했다 — 그림참조 불리언과는 별개 값이다.',
            '법령인접 판정은 마커 직전 어절만 보는 국소 휴리스틱이다. "관한 법률 시행령」제7조 및 별표1"처럼 법령명과 별표 사이에 조번호가 끼면 놓칠 수 있어 표기해석불가 대신 대상없음·외부법령별표참조추정으로만 분류했다(정본 판정 아님).',
            '조문의 원본파일 추정은 조문 시작행 기준이며, 표가 조문 span 내 다른(경계 넘은) 원본파일에 실려 있는 극소수 사례는 반영하지 못한다.'
        ]
    }
    return meta, resolved, isolated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--coll', default='output/legal/건축부문/건축부문_수합.json')
    ap.add_argument('--tables-csv', default='output/legal/table/tables.csv')
    ap.add_argument('--md-dir', default='output/legal/markdown')
    ap.add_argument('--out-dir', default='output/legal/xref')
    a = ap.parse_args()

    try:
        meta, resolved, isolated = run(a.coll, a.tables_csv, a.md_dir)
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"입력 계약 위반: {exc}", file=sys.stderr)
        return 1

    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, 'table_refs.json'), 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, '간선': resolved}, f, ensure_ascii=False, indent=1)

    report = {'meta': {k: meta[k] for k in
                        ['설명', '모수', '해소방법별', '분책여부별_건수', '사각지대']},
              '격리': isolated,
              '격리_사유별_집계': dict(collections.Counter(x['사유'] for x in isolated))}
    with open(os.path.join(a.out_dir, '_table_refs_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print(f"표참조 플래그 조문수: {meta['모수']['표참조_플래그_조문수']:,}")
    print(f"마커 원발생수: {meta['모수']['추출마커_원발생수']:,}")
    print(f"조문단위 유니크 마커후보: {meta['모수']['조문단위_유니크마커간선후보수']:,}")
    print(f"해소: {meta['모수']['해소']:,} {meta['해소방법별']}")
    print(f"격리: {meta['모수']['격리']:,} {report['격리_사유별_집계']}")
    print(f"분책 통계: {meta['분책여부별_건수']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
