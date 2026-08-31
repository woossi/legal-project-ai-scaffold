#!/usr/bin/env python3
# 건축부문 상당 블록 전수 스캔 — 1차 관측 수합
# 입력 정본: output/legal/markdown/**.md, 시행지침_목차구조_전수조사.csv,
#           legal-contrast/case/제52조-슬롯매핑.json, output/legal/시행지침/meta.json
import collections
import csv
import io
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(
    os.environ.get("LEGAL_PROJECT_ROOT", pathlib.Path(__file__).resolve().parents[5])
)
MD   = ROOT/'output/legal/markdown'
OUT  = ROOT/'output/legal/건축부문'
EX   = {}
PATS = []

H  = re.compile(r'^(#{1,6})\s+(.+?)\s*$')
PY = re.compile(r'제\s*(\d+)\s*편')
JA = re.compile(r'제\s*(\d+)\s*장')
JO = re.compile(r'제\s*(\d+)\s*조(?:의\s*(\d+))?')
PLAINJO = re.compile(r'^\s*(?:[-*>]\s*)?제\s*(\d+)\s*조(?:의\s*\d+)?\s*[\(（]([^)）]+)')
NUMPFX = re.compile(r'^(\d+[\.\)]|[가-힣][\.\)]|[①-⑳]|[IVXivx]+[\.\)])\s*')

R_GB   = re.compile(r'건축\s*부문')
R_YB   = re.compile(r'용지별')
R_YJ   = re.compile(r'(주택|시설|상업|업무|복합|근생|근린|학교|종교|자족|지원|기타|공공|판매|숙박|의료|연구|유보|산업|물류|유통|단독|공동)\S{0,8}용지')
R_BLDG = re.compile(r'건축물|건축\s*계획|도시건축|민간\s*부문')
PARENJO = re.compile(r'^\s*[\(（]([^)）]{1,30})[\)）]\s*$')
R_SCAPE= re.compile(r'경관|디자인')
# 마커 라벨 전체를 캡처한다 — 접두만 잡으면 '<표' 조각이 저장된다 (legal-xref 발견)
R_TBL  = re.compile(r'[<〈〔\[]\s*(?:별\s*)?표[^>〉〕\]\n]{0,40}[>〉〕\]]|별표\s*[0-9ⅠⅡⅢⅣⅤⅥ][0-9\-의.]*')
R_FIG  = re.compile(r'도면표시|[<〈〔\[]\s*그림|모식도|예시도')


def load_slotmap(path):
    with open(path, encoding='utf-8') as f:
        slotmap = json.load(f)
    return slotmap['slot_exact'], [
        (p['호'], re.compile(p['pattern'])) for p in slotmap['slot_patterns']
    ]

def nosp(s): return re.sub(r'[\s·ㆍ,\.]', '', s)

def slot_of(title):
    k = nosp(title)
    if k in EX: return EX[k], '표제일치'
    for h, r in PATS:
        if r.search(k): return h, '패턴'
    return '미판정', None

def jo_title(t):
    m = JO.search(t)
    rest = t[m.end():] if m else t
    mm = re.match(r'\s*[\.\)]?\s*[\(（]\s*([^)）]+)', rest)
    if mm: return mm.group(1).strip()
    rest = rest.strip(' .)-—:')
    if rest: return NUMPFX.sub('', rest).strip()
    return NUMPFX.sub('', t).strip()

def scan_district(path):
    lines = path.read_text(encoding='utf-8').splitlines()
    heads = []
    for i, l in enumerate(lines):
        m = H.match(l)
        if m and not re.search(r'/\s*\d+\s*$', m.group(2)):
            heads.append({'lvl': len(m.group(1)), 'text': m.group(2), 'i': i})

    p_lv = min([h['lvl'] for h in heads if PY.search(h['text'])], default=None)
    j_lv = min([h['lvl'] for h in heads if JA.search(h['text']) and not PY.search(h['text'])], default=None)
    if p_lv: axis, blvl = '편', p_lv
    elif j_lv: axis, blvl = '장', j_lv
    elif heads: axis, blvl = '항목', min(h['lvl'] for h in heads)
    else: axis, blvl = None, None

    blocks, order = {}, []
    cur = None
    open_path = {}
    segstart = {}
    def close_seg(endline):
        if cur is not None and cur in segstart:
            blocks[cur]['segments'].append([segstart.pop(cur), endline])
    for h in heads:
        lvl, t = h['lvl'], h['text']
        open_path = {k: v for k, v in open_path.items() if k < lvl}
        open_path[lvl] = t
        if axis and lvl == blvl:
            if axis == '편':
                m = PY.search(t)
                mj = JA.search(t)
                title = t[:mj.start()].strip() if (m and mj and mj.start() > m.end()) else t
                # 번호 재사용(분책·별책마다 리셋)이 있어 번호+표제로 그룹핑한다
                key = ('편', m.group(1), nosp(title)[:16]) if m else ('기타', nosp(t)[:20])
            elif axis == '장':
                m = JA.search(t)
                key = ('장', m.group(1), nosp(t)[:16]) if m else ('기타', nosp(t)[:20])
                title = t
            else:
                key = ('항목', nosp(t)[:24]); title = t
            close_seg(h['i'] - 1)
            if key not in blocks:
                blocks[key] = {'key': key, 'titles': collections.Counter(),
                               'heads': [], 'segments': []}
                order.append(key)
            blocks[key]['titles'][title] += 1
            segstart[key] = h['i']
            cur = key
        elif cur is not None and lvl > blvl:
            blocks[cur]['heads'].append((h, [v for k, v in sorted(open_path.items()) if blvl < k < lvl]))
    close_seg(len(lines) - 1)

    if not blocks:  # 축 없음 — 문서 전체를 한 블록으로
        key = ('문서', '전체')
        blocks[key] = {'key': key, 'titles': collections.Counter({'(문서 전체)': 1}),
                       'heads': [(h, []) for h in heads], 'segments': [[0, len(lines) - 1]]}
        order.append(key)

    all_lvls = [h['lvl'] for b in blocks.values() for h, _ in b['heads']]
    leaf_lvl = max(all_lvls) if all_lvls else None

    out_blocks, out_articles = [], []
    for key in order:
        b = blocks[key]
        title = b['titles'].most_common(1)[0][0]
        # 잎 조문: 조번호 보유 헤딩 + 최심 레벨 헤딩
        seen, arts = set(), []
        for h, pth in b['heads']:
            is_jo = bool(JO.search(h['text']))
            if not (is_jo or (leaf_lvl and h['lvl'] == leaf_lvl)): continue
            if h['i'] in seen: continue
            seen.add(h['i'])
            arts.append((h, pth, is_jo, '헤딩'))
        if not arts:  # 평문 조문 폴백
            for s, e in b['segments']:
                for i in range(s, e + 1):
                    if PLAINJO.match(lines[i]):
                        arts.append(({'lvl': 99, 'text': lines[i].strip(), 'i': i}, [], True, '평문'))
        if not any(a[2] for a in arts):  # 조번호 헤딩이 전무하면 평문 괄호 표제도 수집 — (목적) 꼴
            taken = {a[0]['i'] for a in arts}
            for s, e in b['segments']:
                for i in range(s, e + 1):
                    if i not in taken and PARENJO.match(lines[i]):
                        arts.append(({'lvl': 99, 'text': lines[i].strip(), 'i': i}, [], False, '평문괄호'))
            arts.sort(key=lambda a: a[0]['i'])
        # 조문 span·슬롯
        head_idx = [h['i'] for h in heads]
        recs = []
        for h, pth, is_jo, origin in arts:
            end = len(lines) - 1
            if origin == '헤딩':
                for nh in heads:
                    if nh['i'] > h['i'] and nh['lvl'] <= h['lvl']:
                        end = nh['i'] - 1; break
            else:
                for i in range(h['i'] + 1, len(lines)):
                    if PLAINJO.match(lines[i]) or PARENJO.match(lines[i]) or i in head_idx:
                        end = i - 1; break
            t = jo_title(h['text'])
            s, basis = slot_of(t)
            m = JO.search(h['text'])
            jono = ('제%s조' % m.group(1) + ('의%s' % m.group(2) if m.group(2) else '')) if m else None
            span = lines[h['i']:end + 1]
            recs.append({'조번호': jono, '표제': t, 'slot': s, 'slot근거': basis,
                         '출처': origin, '경로': pth, '행': [h['i'] + 1, end + 1],
                         '표참조': sorted(set(R_TBL.findall('\n'.join(span))))[:8] or None,
                         '그림참조': bool(R_FIG.search('\n'.join(span))),
                         '_span': span})
        cnt = collections.Counter(r['slot'] for r in recs)
        # 질량 계산에서 정의 계열 조문은 제외한다 — 사전이지 규정이 아니다
        mass = [r for r in recs if '정의' not in r['표제']]
        mcnt = collections.Counter(r['slot'] for r in mass)
        judged = sum(v for k, v in mcnt.items() if k not in ('미판정', '총칙'))
        n45 = mcnt.get('4', 0) + mcnt.get('5', 0)
        reasons = []
        if R_GB.search(title): reasons.append('표제:건축부문')
        if R_YB.search(title): reasons.append('표제:용지별')
        elif R_YJ.search(title): reasons.append('표제:용지')
        if R_BLDG.search(title) and not R_SCAPE.search(title) and '표제:건축부문' not in reasons:
            reasons.append('표제:건축물')
        if judged >= 3 and n45 / judged >= 0.5:
            reasons.append('질량:%d/%d' % (n45, judged))
        flag = bool(reasons)
        out_blocks.append({'제목': title, 'axis': axis or '없음',
                           '표제변형': [t for t, _ in b['titles'].most_common()] if len(b['titles']) > 1 else None,
                           '세그먼트': [[s + 1, e + 1] for s, e in b['segments']],
                           '조문수': len(recs), '슬롯분포': dict(cnt),
                           '건축부문': flag, '판정근거': reasons or None,
                           '4·5호비중': round(n45 / judged, 3) if judged else None})
        if flag:
            for r in recs:
                r = dict(r); r['블록'] = title
                r['원문'] = '\n'.join(r.pop('_span'))
                out_articles.append(r)
        else:
            for r in recs: r.pop('_span')

    # 폴백 — 어떤 블록도 못 걸렸으면 문서 전체에서 조문을 다시 모아 질량 재판정.
    # 쓰레기 헤딩(지번 나열 등)으로 블록 축이 성립하지 않는 파일을 구제한다.
    if not any(b['건축부문'] for b in out_blocks):
        seen, arts = set(), []
        for h in heads:
            if JO.search(h['text']) and h['i'] not in seen:
                seen.add(h['i']); arts.append((h, '헤딩'))
        for i, l in enumerate(lines):
            if i in seen: continue
            if PLAINJO.match(l):
                seen.add(i); arts.append(({'lvl': 99, 'text': l.strip(), 'i': i}, '평문'))
            elif PARENJO.match(l):
                seen.add(i); arts.append(({'lvl': 99, 'text': l.strip(), 'i': i}, '평문괄호'))
        arts.sort(key=lambda a: a[0]['i'])
        recs = []
        for h, origin in arts:
            end = len(lines) - 1
            for j, _ in arts:
                if j['i'] > h['i']: end = j['i'] - 1; break
            t = jo_title(h['text'])
            s, basis = slot_of(t)
            m = JO.search(h['text'])
            jono = ('제%s조' % m.group(1) + ('의%s' % m.group(2) if m.group(2) else '')) if m else None
            span = lines[h['i']:end + 1]
            recs.append({'조번호': jono, '표제': t, 'slot': s, 'slot근거': basis,
                         '출처': origin, '경로': [], '행': [h['i'] + 1, end + 1],
                         '표참조': sorted(set(R_TBL.findall('\n'.join(span))))[:8] or None,
                         '그림참조': bool(R_FIG.search('\n'.join(span))),
                         '_span': span})
        mass = [r for r in recs if '정의' not in r['표제']]
        mcnt = collections.Counter(r['slot'] for r in mass)
        judged = sum(v for k, v in mcnt.items() if k not in ('미판정', '총칙'))
        n45 = mcnt.get('4', 0) + mcnt.get('5', 0)
        if judged >= 3 and n45 / judged >= 0.3:
            cnt = collections.Counter(r['slot'] for r in recs)
            out_blocks.append({'제목': '(문서 전체·폴백)', 'axis': '폴백',
                               '표제변형': None, '세그먼트': [[1, len(lines)]],
                               '조문수': len(recs), '슬롯분포': dict(cnt),
                               '건축부문': True, '판정근거': ['폴백질량:%d/%d' % (n45, judged)],
                               '4·5호비중': round(n45 / judged, 3)})
            for r in recs:
                r = dict(r); r['블록'] = '(문서 전체·폴백)'
                r['원문'] = '\n'.join(r.pop('_span'))
                out_articles.append(r)
    return axis, out_blocks, out_articles, len(heads)

def build_outputs(rows, meta):
    by_no = {d['dstrcAppnNo']: d for d in meta['districts']}
    districts, isolated = [], []

    for r in rows:
        no, nm, rg = r['지구번호'], r['지구명'], r['지역']
        md = MD/rg/(nm + '.md')
        base = {'지구번호': no, '지구명': nm, '지역': rg,
                '목차유형': r['유형'], '근거등급': r['근거등급']}
        d = by_no.get(no, {})
        base['특성'] = {'근거법령': d.get('lawordNm', r['근거법령']),
                      '시행자': d.get('opertnProfsNm'),
                      '면적': d.get('ar'), '신도시': d.get('newtownNm')}
        base['시점'] = {'사업기간': d.get('bsnsOpertnPd'), '단계': d.get('stepNm')}
        if not md.exists():
            isolated.append({**base, '사유': 'md 파일 없음'})
            continue
        axis, blocks, arts, nheads = scan_district(md)
        flagged = [b for b in blocks if b['건축부문']]
        base.update({'axis': axis or '없음', '헤딩수': nheads,
                     '블록': blocks, '건축부문블록수': len(flagged), '조문': arts})
        if not flagged:
            isolated.append({'지구번호': no, '지구명': nm, '지역': rg, '목차유형': r['유형'],
                             '사유': '건축부문 상당 블록 미검출 (헤딩수 %d)' % nheads})
        districts.append(base)

    return districts, isolated


def summarize(districts, isolated):
    n_flag = sum(1 for d in districts if d.get('건축부문블록수', 0) > 0)
    n_arts = sum(len(d.get('조문', [])) for d in districts)
    titles = collections.Counter()
    slot_total = collections.Counter()
    undecided = collections.Counter()

    for d in districts:
        for b in d.get('블록', []):
            if b['건축부문']:
                titles[nosp(b['제목'])] += 1
        for a in d.get('조문', []):
            slot_total[a['slot']] += 1
            if a['slot'] == '미판정':
                undecided[a['표제'][:30]] += 1

    out = {'meta': {
        '설명': '건축부문 상당 블록 전수 스캔 1차 — 표제·질량 두 신호로 판정, 근거를 블록마다 기록',
        '생성스크립트': '.claude/skills/legal/legal-toc/scripts/scan_building.py',
        '입력정본': ['output/legal/markdown/**.md', 'output/legal/analysis/시행지침_목차구조_전수조사.csv',
                  '.claude/skills/legal/legal-contrast/case/제52조-슬롯매핑.json', 'output/legal/시행지침/meta.json'],
        '판정규칙': {'표제신호': '건축부문 | 용지별 | ○○용지 | 건축물(경관·디자인 표제 제외)',
                  '질량신호': '판정된 조문 중 제52조 4·5호 비중 ≥ 0.5 (판정 조문 ≥ 3)',
                  '슬롯판정': '제52조-슬롯매핑.json 의 exact→pattern 순서. 본문 인용 2차 판정은 미적용'},
        '모수': {'스캔지구': len(districts), '건축부문검출지구': n_flag,
               '수합조문': n_arts, '격리': len(isolated),
               '분모주의': '수집 원장은 190지구이나 41115MX2004001(광교)은 시행지침 md 부재로 목차 전수조사 CSV(189행)에 없다. 스캔 분모는 189다'},
        '수합원문_주의': '조문 원문은 md 정본의 사본이다. 행 번호가 정본 좌표다'},
        'districts': districts}

    report = {'meta': {'설명': '건축부문 전수 스캔 리포트 — 격리·미판정·표제 클러스터'},
              '격리': isolated,
              '건축부문_블록표제_클러스터': dict(titles.most_common()),
              '수합조문_슬롯분포': dict(slot_total.most_common()),
              '미판정표제_top40': dict(undecided.most_common(40))}
    return out, report, slot_total, titles


def write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def main():
    global EX, PATS
    EX, PATS = load_slotmap(
        ROOT/'.claude/skills/legal/legal-contrast/case/제52조-슬롯매핑.json')
    rows = list(csv.DictReader(io.open(
        ROOT/'output/legal/analysis/시행지침_목차구조_전수조사.csv',
        encoding='utf-8-sig')))
    with open(ROOT/'output/legal/시행지침/meta.json', encoding='utf-8') as f:
        meta = json.load(f)

    districts, isolated = build_outputs(rows, meta)
    out, report, slot_total, titles = summarize(districts, isolated)

    OUT.mkdir(exist_ok=True)
    write_json(OUT/'건축부문_수합.json', out)
    write_json(OUT/'_scan_report.json', report)

    sz = (OUT/'건축부문_수합.json').stat().st_size
    print('스캔지구 %d | 건축부문 검출 %d | 수합조문 %d | 격리 %d | 수합파일 %.1fMB'
          % (len(districts), out['meta']['모수']['건축부문검출지구'],
             out['meta']['모수']['수합조문'], len(isolated), sz / 1e6))
    print('슬롯분포:', dict(slot_total.most_common()))
    print('표제 클러스터 상위:', list(titles.most_common(12)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
