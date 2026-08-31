# -*- coding: utf-8 -*-
"""실측 어휘(조문 표제·정의 용어) → L1 계획항목(수립지침 절) 매핑.

정본 근거:
  - .claude/rules/계획규범요소-틀.md  (L1 정의, 부분문자열 정규식 금지)
  - output/legal/statute/수립지침_항구조.json  (L1 절 목록·항 본문)
산출: output/kb/norm/plan-item-map/{plan-item-map.json,_plan_item_residual.json,meta.json}
멱등: 입력이 같으면 산출이 같다. 정렬은 (출처, -출현지구수, 정규화형).
     `--out-dir` 로 다른 곳에 뽑아 해시를 견주면 정본을 건드리지 않고 멱등성을 잰다.

실행:
  python3 .claude/skills/kb/kb-norm/scripts/build_plan_item_map.py
  python3 .claude/skills/kb/kb-norm/scripts/build_plan_item_map.py --out-dir /tmp/chk --quiet
"""
import argparse
import json
import re
import unicodedata
import collections
import hashlib
import os
from pathlib import Path

ROOT = os.environ.get('LEGAL_PROJECT_ROOT', str(Path(__file__).resolve().parents[5]))

DEFAULTS = {
    'out_dir': 'output/kb/norm/plan-item-map',
    'hang': 'output/legal/statute/수립지침_항구조.json',
    'bldg': 'output/legal/건축부문/건축부문_수합.json',
    'term': 'output/legal/word/terms.json',
    # L1 SKOS 축은 kb-ontology 가 발급한다. 여기서는 조인만 하고 새로 발급하지 않는다
    'vocab': 'output/kb/ontology/vocab-plan-item.ttl',
}
VOCAB_BASE = 'https://w3id.org/lp/concept/계획항목/'

# ── 정규화 ────────────────────────────────────────────────────────────────
SEP_CHARS = 'ㆍ·⋅‧․･∙‥/|、,，'
TOKEN_SPLIT = re.compile(r'[^가-힣A-Za-z0-9]+')

JOSA = ('으로써', '에게', '에서', '으로', '로서', '로써', '에는', '에도', '과의', '와의',
        '등을', '등은', '등의', '등이', '등에',
        '의', '을', '를', '은', '는', '이', '가', '에', '와', '과', '도', '만', '로', '및', '등')

STOP = {'관한', '관련', '사항', '기준', '지침', '지침사항', '기타', '일반', '대한', '위한',
        '내용', '부문', '계획', '적용', '설치', '조성', '다음', '아래', '경우', '제한',
        '규정', '방법', '원칙', '세부', '또는', '이하', '이상', '해당', '전체', '공통',
        '기타사항', '개요', '목적', '정의', '용어', '총칙', '준수', '기타등', '조항',
        '건축물', '건축', '지구', '구역', '단지', '사업', '지역', '대상', '이용', '부분',
        '변경', '검토', '확보', '고려', '필요', '수립', '작성', '기본', '항목', '표기',
        # 문서 서식어와 L2(규범 형식) 어휘. L1 계획항목 축이 아니다
        '가이드라인', '매뉴얼', '체크리스트', '권장사항', '규제사항', '의무사항',
        '유의사항', '참고사항', '공통사항', '세부사항', '기타등등', '권장', '의무',
        # '주차장 설치기준' 의 '설치기준' 처럼 어느 항목에나 붙는 서식 접미. 두면
        # 3-5 본문 1개 항 출현이 3-6(주차장)과 가짜 경합을 만든다
        '설치기준', '산정기준', '적용기준', '계획기준', '수립기준', '작성기준',
        '일반기준', '세부기준', '운영기준', '설치방법', '계획방향', '추진방향',
        # 용언 활용형·수식어. 본문 색인에 섞여 표제와 맞으면 엉뚱한 절을 지지한다
        '하는', '되는', '있는', '없는', '사용하는', '의한', '따른', '따라', '안전한',
        '같은', '통하여', '위하여', '대하여', '관하여', '있어', '없어',
        # 위치 수식어. 적용대상 축이지 계획항목 축이 아니다
        '단지내', '단지안', '구역내', '구역안', '획지내', '가구내', '대지내', '대지안',
        '지구내', '내부', '외부', '주변', '인근'}

NONTITLE_TAIL = re.compile(r'(한다|하여야|하도록|없다|있다|된다|같다|아니한다|바란다|이다|였다)$')
NONTITLE_HEAD = re.compile(r'^(제\s*\d+\s*(조|항|호)|내지|다만|이 경우|아울러)')

NORM_STEPS = [
    '① 유니코드 NFC 정규화',
    '② 마크다운·인용부호(｢｣「」『』‘’“”\'")와 꺾쇠 제거',
    "③ 나열 구분자(%s)를 중점 '·' 하나로 통일하고 중점 앞뒤 공백을 없앤다" % SEP_CHARS,
    '④ 닫히지 않은 괄호는 여는 괄호 앞에서 절단하고 괄호절단 플래그를 남긴다',
    '⑤ 연속 공백을 한 칸으로 합치고 앞뒤 공백·중점을 제거',
    '⑥ 어절 분해는 한글/영숫자 이외 문자 경계로만 한다(부분문자열 정규식 금지)',
    '⑦ 어절은 원형을 먼저 대조하고, 원형이 어느 채널에도 안 걸릴 때만 조사 제거형(%s 등)을 대조한다. '
    '조사 제거를 먼저 하면 명사가 깎인다 — 바닥높이→바닥높, 가로등→가로' % '/'.join(JOSA[:6]),
    '⑧ 불용 어절(%d종)을 매핑 판정에서 제외한다' % len(STOP),
]


def nfc(s):
    return unicodedata.normalize('NFC', s or '')


def normalize_title(raw):
    """원문 → (정규화형, 플래그목록). 원문은 보존하고 이 값은 파생값이다."""
    flags = []
    s = nfc(raw).strip()
    s = re.sub(r'\*\*|__', '', s)
    s = re.sub(r'[｢｣「」『』〈〉《》<>‘’“”\'"]', ' ', s)
    if '(' in s or '[' in s:
        op = s.count('(') + s.count('[')
        cl = s.count(')') + s.count(']')
        if op > cl:
            cut = min([i for i in (s.find('('), s.find('[')) if i >= 0])
            s = s[:cut]
            flags.append('괄호절단')
    for c in SEP_CHARS:
        s = s.replace(c, '·')
    s = re.sub(r'·+', '·', s)
    s = re.sub(r'\s*·\s*', '·', s)
    s = re.sub(r'\s+', ' ', s).strip(' ·\t')
    return s, flags


def stem(tok):
    """조사 제거형. 없으면 None. 원형을 먼저 쓰고 이 값은 대안으로만 쓴다."""
    for j in sorted(JOSA, key=len, reverse=True):
        if tok.endswith(j) and len(tok) - len(j) >= 2:
            return tok[:-len(j)]
    return None


def tokenize(s, keep_stop=False):
    """원형 어절 목록. 조사 제거는 채널 대조 단계에서 대안형으로만 시도한다."""
    out = []
    for t in TOKEN_SPLIT.split(s):
        if len(t) < 2:
            continue
        if not keep_stop and (t in STOP or (stem(t) or t) in STOP):
            continue
        out.append(t)
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def forms(tok):
    """대조 후보형. 원형이 먼저다."""
    s = stem(tok)
    return [tok] + ([s] if s and s != tok else [])


def is_title_form(norm):
    if len(norm) < 2 or len(norm) > 30:
        return False, '표제 길이 %d자 (2~30자 밖) — 본문 문장 혼입 의심' % len(norm)
    if NONTITLE_TAIL.search(norm.replace(' ', '')):
        return False, '서술어 종결 — 조문 본문이 표제로 잘려 들어온 것'
    if NONTITLE_HEAD.search(norm):
        return False, '조·항·호 인용으로 시작 — 표제가 아니라 본문 조각'
    if not re.search(r'[가-힣]{2}', norm):
        return False, '한글 어절이 없다 — 표·번호 잔재'
    return True, ''


# ── L1 절 축 ──────────────────────────────────────────────────────────────
# 3장(공통)과 4장(주거형)은 같은 계획항목을 층위만 달리 규정한다. 짝을 절군으로 묶고
# 3장 절을 대표로 삼는다. 4-1·4-7 은 3장 대응이 없어 스스로 대표다.
PAIR = {'3-4': '4-5', '3-5': '4-2', '3-6': '4-6', '3-7': '4-3', '3-16': '4-4'}
PAIR_REV = {v: k for k, v in PAIR.items()}

# 절제목 핵어. 각 값은 절제목 원문 어절이거나 그 등가표기다(근거를 meta 에 기록).
SECTION_HEADS = {
    '3-1': ['일반원칙'],
    '3-2': ['행위제한', '완화'],
    '3-3': ['용도지역', '용도지구'],
    '3-4': ['환경관리', '환경'],
    '3-5': ['기반시설'],
    '3-6': ['교통처리', '교통'],
    '3-7': ['가구', '획지'],
    '3-8': ['용도'],
    '3-9': ['건폐율', '용적률', '높이', '규모'],
    '3-10': ['배치', '건축선'],
    '3-11': ['형태', '색채'],
    '3-12': ['공동개발', '합벽건축'],
    '3-13': ['공개공지', '공지'],
    '3-14': ['공원', '녹지'],
    '3-15': ['특별계획구역'],
    '3-16': ['경관'],
    '3-17': ['기부채납'],
    '3-18': ['기존건축물', '특례'],
    '3-19': ['가설건축물'],
    '4-1': ['토지이용계획', '토지이용'],
    '4-7': ['보칙'],
}
# 어절이 통째로 같을 때만 인정하는 등가표기. 합성어 어말로는 쓰지 않는다
# ('공동주택용지'를 4-1 로 보내지 않기 위함 — 용지는 계획항목 축이 아니라 적용대상 축이다)
EXACT_ONLY_HEADS = {
    '용지': '4-1',
    '기본원칙': '3-1',
    '기본방향': '3-1',
    '일반사항': '3-1',
}
EXACT_ONLY_EVIDENCE = {
    '용지': "4-1-1 '구역내 토지는 주거용지ㆍ상업용지…로 구획한다' — 용지 구분은 토지이용계획이다. "
            "다만 '○○용지'는 적용대상 수식어라 어말 대조에서 제외한다(지구단위계획-법정구조.md '용지는 층위가 아니다')",
    '기본원칙': "3-1 절제목 '일반원칙'의 등가표기",
    '기본방향': "3-1 절제목 '일반원칙'의 등가표기",
    '일반사항': "3-1 절제목 '일반원칙'의 등가표기",
}
HEAD_EVIDENCE = {
    '3-4': "3-4 절제목 '환경관리' 어절 + 4-5 절제목 '환경'",
    '3-6': "3-6 절제목 '교통처리' 어절 + 4-6 절제목 '교통'",
    '3-8': "3-8 절제목 '건축물의 용도' 의 핵어 '용도'",
    '3-9': "3-9 절제목 '건폐율ㆍ용적률ㆍ높이 등 건축물의 규모' 의 어절",
    '3-10': "3-10 절제목 '건축물의 배치와 건축선' 의 어절",
    '3-11': "3-11 절제목 '건축물의 형태와 색채' 의 어절",
    '3-13': "3-13 절제목 '공개공지 등 대지내 공지' 의 어절 (공개공지·공지)",
    '3-17': "3-17 절제목 '기반시설 기부채납 운영기준' 의 변별 어절 '기부채납'",
    '3-18': "3-18 절제목 '기존 건축물의 특례에 관한 사항' 의 어절(기존건축물은 두 어절 결합)",
    '4-1': "4-1 절제목 '토지이용계획' 과 그 축약형",
}

W_EXACT = 20.0      # 매칭키가 절제목과 같다
W_HEAD = 10.0       # 어절이 절제목 핵어와 같다
W_HEAD_TAIL = 8.0   # 어절이 절제목 핵어로 끝난다(한국어 핵어말 합성)
W_HEAD_PLAN = 8.0   # 어절이 절제목 핵어 + 계획류 접미다(경관계획 → 경관)
W_EQUIV = 5.0       # 절제목 등가표기. 실제 주제어(W_HEAD)에 밀리도록 낮춘다
W_BODY = 5.0        # 어절이 절 본문에 어절로 등장한다(절군별 항 수의 상대몫)
W_BODY_TAIL = 3.0   # 어절이 변별적 본문 어절로 끝난다

# 어두 핵어 + 이 접미일 때만 어두 대조를 허용한다. '경관계획'의 분류어는 앞머리 '경관'이다
PLAN_SUFFIX = ('계획', '계획의', '계획은', '상세계획', '기본계획', '관리계획',
               '설계', '계획도', '구상')
MIN_SCORE = 5.0
DOMINANCE = 1.5
BODY_MAX_GROUPS = 4   # 이보다 많은 절군에 걸치는 어절은 변별력이 없다
BODY_MIN_SHARE = 0.25


def build(args):
    SRC_HANG, SRC_BLDG = args.hang, args.bldg
    SRC_TERM, SRC_VOCAB = args.term, args.vocab
    OUT = args.out_dir

    def rel(p):
        """meta 에는 저장소 상대경로를 적는다. 절대경로는 기계마다 다르다"""
        try:
            return os.path.relpath(os.path.abspath(p), ROOT)
        except ValueError:
            return p

    vocab_iris = set()
    if os.path.exists(SRC_VOCAB):
        vocab_iris = set(re.findall(re.escape(VOCAB_BASE) + r'[0-9]+-[0-9]+',
                                    open(SRC_VOCAB, encoding='utf-8').read()))

    def item_iri(g):
        iri = VOCAB_BASE + g
        return iri if iri in vocab_iris else None

    hang = json.load(open(SRC_HANG, encoding='utf-8'))
    sec_title = {}
    for s in hang['절목록']:
        sec_title['%d-%d' % (s['장번호'], s['절번호'])] = s['절제목']

    groups = [k for k in SECTION_HEADS]  # 대표 절키 21개
    group_of = {}
    for g in groups:
        group_of[g] = g
    for a, b in PAIR.items():
        group_of[b] = a

    # 본문 어절 색인 (3·4장 = L1 대상, 그 밖의 장 = 진단용)
    body34 = collections.defaultdict(collections.Counter)
    body_other = collections.defaultdict(collections.Counter)
    for h in hang['항목록']:
        key = '%s-%s' % (h['장번호'], h['절번호'])
        tgt34 = h['장번호'] in (3, 4)
        for raw_tok in tokenize(nfc(h['본문']), keep_stop=True):
            for t in forms(raw_tok):
                if tgt34:
                    body34[t][group_of.get(key, key)] += 1
                else:
                    body_other[t][key] += 1

    # 변별적 본문 어절: 불용어 제외, 절군 수 제한
    body_raw = {t: c for t, c in body34.items() if t not in STOP and len(t) >= 2}
    body_disc = {}
    for t, cnt in body34.items():
        if t in STOP or len(t) < 2:
            continue
        if len(cnt) > BODY_MAX_GROUPS:
            continue
        tot = sum(cnt.values())
        shares = {g: c / tot for g, c in cnt.items() if c / tot >= BODY_MIN_SHARE}
        if shares:
            body_disc[t] = (shares, dict(cnt))

    head_of = {}   # 핵어 → 절군
    for g, hs in SECTION_HEADS.items():
        for h in hs:
            head_of[h] = g
    head_list = sorted(head_of, key=len, reverse=True)
    # 어말 대조에 쓸 본문 핵어. 한 절의 3개 항 이상에, 점유율 0.6 이상으로 몰린 어절만
    # 쓴다('층수' → 3-9). 그보다 성긴 어절은 어말 대조에서 약한 근거로만 다룬다.
    # 세는 단위는 '항의 수'다 — tokenize 가 항 안에서 중복 어절을 접기 때문에
    # 한 항에 같은 어절이 여러 번 나와도 1로 센다
    body_tail_strong, body_tail_list = [], []
    for t, (shares, cnt) in body_disc.items():
        if len(t) < 2:
            continue
        body_tail_list.append(t)
        top = max(cnt.values())
        if top >= 3 and top / sum(cnt.values()) >= 0.6:
            body_tail_strong.append(t)
    body_tail_list.sort(key=len, reverse=True)
    body_tail_strong.sort(key=len, reverse=True)

    exact_key = {}
    for g in groups:
        for key in [g] + ([PAIR[g]] if g in PAIR else []):
            title = sec_title.get(key)
            if not title:
                continue
            mk = ''.join(tokenize(*normalize_title(title)[:1], keep_stop=True))
            if mk:
                exact_key.setdefault(mk, g)

    def match(norm, tokens):
        """→ (절군점수, 근거리스트). 어절마다 채널 하나만 채택한다(먼저 걸리는 것)."""
        score = collections.defaultdict(float)
        ev = []
        firm = set()   # 절제목 채널 또는 본문 2회 이상으로 뒷받침된 절군
        full_mk = ''.join(tokenize(norm, keep_stop=True))
        for mk in (full_mk, ''.join(tokens)):
            if mk and mk in exact_key:
                g = exact_key[mk]
                score[g] += W_EXACT
                ev.append(('정확일치', mk, g, W_EXACT))
                firm.add(g)
                break
        # 채널이 바깥, 어절 변이형이 안쪽이다. 순서를 뒤집으면 '용지의' 가 본문 채널에서
        # 먼저 걸려 절제목 등가표기 채널이 영영 안 돌아간다
        for t in tokens:
            fs = forms(t)
            g = next((EXACT_ONLY_HEADS[f] for f in fs if f in EXACT_ONLY_HEADS), None)
            if g:
                score[g] += W_EQUIV
                ev.append(('절제목등가표기', t, g, W_EQUIV))
                firm.add(g)
                continue
            g = next((head_of[f] for f in fs if f in head_of), None)
            if g:
                score[g] += W_HEAD
                ev.append(('절제목핵어', t, g, W_HEAD))
                firm.add(g)
                continue
            tail = next((h for f in fs for h in head_list if len(f) > len(h) and f.endswith(h)), None)
            if tail:
                g = head_of[tail]
                score[g] += W_HEAD_TAIL
                ev.append(('절제목핵어_어말:%s' % tail, t, g, W_HEAD_TAIL))
                firm.add(g)
                continue
            lead = next((h for f in fs for h in head_list
                         if f.startswith(h) and f[len(h):] in PLAN_SUFFIX), None)
            if lead:
                g = head_of[lead]
                score[g] += W_HEAD_PLAN
                ev.append(('절제목핵어_어두:%s+계획류' % lead, t, g, W_HEAD_PLAN))
                firm.add(g)
                continue
            cnt = collections.Counter()
            for f in fs:
                if f in body_raw:
                    cnt.update(body_raw[f])
            if cnt and len(cnt) <= BODY_MAX_GROUPS:
                tot = sum(cnt.values())
                # 점유율이 낮은 절군을 떨군 뒤 남은 것끼리 다시 정규화한다.
                # 떨구기만 하면 '주차장'(3-6 에 11회, 3-2 에 2회)처럼 한 절에 몰린
                # 어절이 점수를 손해 본다
                # 점유율이 낮은 절군을 떨군 뒤 남은 것끼리 다시 정규화한다. 최다 절에
                # 만점을 주면 1회씩 갈린 어절('외벽' 3-11:1·3-16:1)까지 만점 동점이 돼
                # 가짜 경합이 쏟아진다 — 실측으로 다중후보가 102→298 로 부풀었다
                keep = {gg: c for gg, c in cnt.items() if c / tot >= BODY_MIN_SHARE}
                ktot = sum(keep.values()) or 1
                for gg, c in keep.items():
                    sh = c / ktot
                    score[gg] += W_BODY * sh
                    ev.append(('본문어절x%d' % c, t, gg, round(W_BODY * sh, 2)))
                    if c >= 2:
                        firm.add(gg)
                continue
            strong = next((b for f in fs for b in body_tail_strong
                           if len(f) > len(b) and f.endswith(b)), None)
            if strong:
                bcnt = body_disc[strong][1]
                btot = sum(bcnt.values())
                keep = {gg: c for gg, c in bcnt.items() if c / btot >= BODY_MIN_SHARE}
                ktot = sum(keep.values()) or 1
                for gg, c in keep.items():
                    sh = c / ktot
                    score[gg] += W_BODY * sh
                    ev.append(('본문핵어_어말:%s(x%d)' % (strong, c), t, gg, round(W_BODY * sh, 2)))
                    firm.add(gg)
                continue
            btail = next((b for f in fs for b in body_tail_list
                          if len(f) > len(b) and f.endswith(b)), None)
            if btail:
                shares, bcnt = body_disc[btail]
                for gg, sh in shares.items():
                    score[gg] += W_BODY_TAIL * sh
                    ev.append(('본문어절_어말:%s' % btail, t, gg, round(W_BODY_TAIL * sh, 2)))
        weak = set(score) - firm
        return score, ev, weak

    def sec_label(g):
        if g in PAIR:
            return '%s %s (주거형 대응 %s %s)' % (g, sec_title[g], PAIR[g], sec_title[PAIR[g]])
        return '%s %s' % (g, sec_title.get(g, ''))

    # 나열 표지. '높이와 배치'처럼 붙여 쓴 접속조사도 잡는다
    ENUM = re.compile(r'·|,|및|\S+[와과]\s')

    def decide(raw, norm, tokens):
        score, ev, weak = match(norm, tokens)
        if not score:
            return None
        rank = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
        best, bs = rank[0]
        second, ss = (rank[1] if len(rank) > 1 else (None, 0.0))
        by_g = collections.defaultdict(list)
        for ch, tok, g, w in ev:
            by_g[g].append((ch, tok, w))
        out = {'score': rank, 'ev': ev, 'by_g': by_g, 'weak': weak}
        if bs < MIN_SCORE:
            out['판정'] = '판정불가'
            return out
        # 나열 표제는 우세비보다 먼저 본다. 성분이 서로 다른 어절이고 각각 문턱을 넘으면
        # 하나의 조문이 두 절을 함께 규정하는 것이지 절이 다투는 것이 아니다.
        reach = [g for g, s in rank if s >= MIN_SCORE]
        firm = [g for g in reach if g not in weak]
        if len(firm) >= 2 and ENUM.search(norm):
            toks_of = {g: {t for ch, t, w in by_g[g]} for g in firm}
            disjoint = all(not (toks_of[a] & toks_of[b])
                           for i, a in enumerate(firm) for b in firm[i + 1:])
            if disjoint:
                out['판정'] = '복합나열'
                out['절군'] = firm
                return out
        if second is None or bs >= DOMINANCE * ss:
            out['판정'] = '확정'
            out['절군'] = [best]
            return out
        out['판정'] = '경합'
        out['절군'] = reach or [best, second]
        return out

    def reason(res):
        gs = res['절군']
        parts = []
        for g in gs:
            chans = res['by_g'][g]
            chans = sorted(chans, key=lambda x: -x[2])[:3]
            toks = ', '.join("'%s'(%s)" % (t, ch) for ch, t, w in chans)
            parts.append('%s ← %s' % (sec_label(g), toks))
        return ' / '.join(parts)

    def method_of(res):
        gs = res['절군']
        chans = [ch for g in gs for ch, t, w in res['by_g'][g]]
        if any(c.startswith('정확일치') for c in chans):
            return '정확일치'
        if res['판정'] == '복합나열':
            return '규칙-복합나열'
        if any(c == '절제목핵어' for c in chans):
            return '규칙-절제목핵어'
        if any(c == '절제목등가표기' for c in chans):
            return '규칙-절제목등가표기'
        if any(c.startswith('절제목핵어_어말') for c in chans):
            return '규칙-절제목핵어_어말'
        if any(c.startswith('절제목핵어_어두') for c in chans):
            return '규칙-절제목핵어_어두'
        if any(c.startswith('본문어절x') for c in chans):
            return '규칙-본문어절'
        if any(c.startswith('본문핵어_어말') for c in chans):
            return '규칙-본문핵어_어말'
        return '규칙-본문어절_어말'

    CONF = {'정확일치': '높음', '규칙-절제목핵어': '높음', '규칙-복합나열': '보통',
            '규칙-절제목등가표기': '보통', '규칙-절제목핵어_어말': '보통',
            '규칙-절제목핵어_어두': '보통', '규칙-본문어절': '보통',
            '규칙-본문핵어_어말': '보통', '규칙-본문어절_어말': '낮음'}

    # ── 입력 1: 조문 표제 ────────────────────────────────────────────────
    bldg = json.load(open(SRC_BLDG, encoding='utf-8'))
    t_cnt = collections.Counter()
    t_dist = collections.defaultdict(set)
    t_slot = collections.defaultdict(collections.Counter)
    for d in bldg['districts']:
        for a in d.get('조문', []):
            raw = nfc(a.get('표제') or '').strip()
            t_cnt[raw] += 1
            t_dist[raw].add(d['지구번호'])
            t_slot[raw][str(a.get('slot'))] += 1

    # ── 입력 2: 정의 용어 ────────────────────────────────────────────────
    terms = json.load(open(SRC_TERM, encoding='utf-8'))

    items, residual, contest, nontitle = [], [], [], []

    def emit(raw, src, ndist, extra):
        norm, flags = normalize_title(raw)
        ok, why = is_title_form(norm)
        toks = tokenize(norm)
        base = {'원문': raw, '정규화형': norm, '출처': src, '출현지구수': ndist,
                '어절': toks, '정규화플래그': flags}
        base.update(extra)
        if not ok:
            rec = dict(base); rec['사유'] = why; rec['사유구분'] = '표제형식_아님'
            nontitle.append(rec)
            return
        if not toks:
            dropped = [t for t in TOKEN_SPLIT.split(norm) if len(t) >= 2]
            rec = dict(base)
            rec['사유'] = ('계획항목 축이 아닌 총칙·서식 어휘만 남는다 (불용 어절: %s)'
                           % ', '.join(dropped[:6]))
            rec['사유구분'] = '판정불가'
            rec['수립지침_그밖의장'] = {}
            residual.append(rec)
            return
        res = decide(raw, norm, toks)
        if res is None or res['판정'] == '판정불가':
            other = {}
            for t in toks:
                if t in body_other:
                    other[t] = dict(body_other[t])
            rec = dict(base)
            rec['사유'] = ('수립지침 3·4장 절제목·본문 어휘와 어절이 대응하지 않는다'
                           if not other else
                           '수립지침 3·4장 밖(총칙·구역지정·타 유형 장)에만 어절이 나타난다')
            rec['사유구분'] = '판정불가'
            rec['수립지침_그밖의장'] = other
            rec['부분점수'] = [[g, round(s, 2)] for g, s in (res['score'][:3] if res else [])]
            residual.append(rec)
            return
        if res['판정'] == '경합':
            rec = dict(base)
            rec['사유구분'] = '다중후보'
            rec['후보절'] = [{'절군': g, '절제목': sec_label(g), '점수': round(s, 2),
                             '계획항목IRI': item_iri(g)}
                             for g, s in res['score'] if s > 0][:4]
            rec['사유'] = '하나의 개념이 두 절 이상을 같은 강도로 지지한다 — 확정하지 않는다'
            rec['근거'] = reason(res)
            contest.append(rec)
            return
        m = method_of(res)
        gs = res['절군']
        conf = CONF[m]
        if conf != '낮음' and all(g in res['weak'] for g in gs):
            conf = '낮음'   # 근거가 수립지침 본문 1개 항 출현뿐이다
        rec = dict(base)
        rec['L1절'] = gs
        rec['L1절제목'] = [sec_title.get(g, '') for g in gs]
        rec['계획항목IRI'] = [item_iri(g) for g in gs]
        rec['주거형대응절'] = [PAIR[g] for g in gs if g in PAIR]
        rec['주거형대응IRI'] = [item_iri(PAIR[g]) for g in gs if g in PAIR]
        rec['매핑방법'] = m
        rec['신뢰'] = conf
        rec['매핑근거'] = reason(res)
        rec['점수'] = [[g, round(s, 2)] for g, s in res['score'][:3]]
        items.append(rec)

    for raw, c in t_cnt.items():
        emit(raw, '조문표제', len(t_dist[raw]),
             {'출현조문수': c, '참고_slot분포': dict(t_slot[raw])})
    for t in terms['terms']:
        cl = t.get('classification') or {}
        emit(nfc(t['term']), '정의용어', t.get('doc_frequency') or 0,
             {'용어id': t['id'], '참고_concept_type': cl.get('concept_type'),
              '참고_source_topic': cl.get('source_topic')})

    key = lambda r: (r['출처'], -r.get('출현지구수', 0), r['정규화형'])
    items.sort(key=key); residual.sort(key=key); contest.sort(key=key); nontitle.sort(key=key)

    # ── 모수·매핑률 ─────────────────────────────────────────────────────
    def bysrc(rows, s):
        return [r for r in rows if r['출처'] == s]

    stat = {}
    for s in ('조문표제', '정의용어'):
        total = len(bysrc(items, s)) + len(bysrc(residual, s)) + len(bysrc(contest, s)) + len(bysrc(nontitle, s))
        judged = total - len(bysrc(nontitle, s))
        stat[s] = {
            '고유항목수': total,
            '표제형식_아님': len(bysrc(nontitle, s)),
            '판정모수': judged,
            '매핑확정': len(bysrc(items, s)),
            '다중후보_격리': len(bysrc(contest, s)),
            '판정불가_잔차': len(bysrc(residual, s)),
            '매핑률_판정모수기준': round(len(bysrc(items, s)) / judged, 4) if judged else 0,
            '매핑률_고유항목기준': round(len(bysrc(items, s)) / total, 4) if total else 0,
        }
    dist_weighted = {}
    for s in ('조문표제',):
        num = sum(r['출현조문수'] for r in bysrc(items, s))
        den = sum(r.get('출현조문수', 0) for r in items + residual + contest + nontitle if r['출처'] == s)
        dist_weighted['조문건수_가중매핑률'] = round(num / den, 4) if den else 0
        dist_weighted['조문건수_분자'] = num
        dist_weighted['조문건수_분모'] = den

    # 교차 확인: terms.json 의 source_topic(관행 조 표제 계열)과 이번 L1 판정을 맞대본다.
    # 매핑 채널이 아니라 검증 신호다 — 같은 관행 표제 축에서 나온 값이라 근거로 쓰지 않는다
    TOPIC_L1 = {'건축물의 배치': '3-10', '건축선': '3-10', '건축물의 형태·색채': '3-11',
                '건축물의 용도': '3-8', '건축물의 규모·높이': '3-9', '대지 내 공지': '3-13',
                '가구·획지': '3-7', '교통처리': '3-6', '경관': '3-16', '친환경·에너지': '3-4'}
    xa, xd, xdet = 0, 0, collections.Counter()
    for r in items:
        if r['출처'] != '정의용어':
            continue
        exp = TOPIC_L1.get(r.get('참고_source_topic'))
        if not exp:
            continue
        if exp in r['L1절']:
            xa += 1
        else:
            xd += 1
            xdet['%s → %s' % (exp, '·'.join(r['L1절']))] += 1
    cross = {'대조가능': xa + xd, '일치': xa, '불일치': xd,
             '일치율': round(xa / (xa + xd), 4) if (xa + xd) else 0,
             '불일치_상위': [{'source_topic→판정': k, '건수': v} for k, v in xdet.most_common(8)],
             '주의': 'source_topic 은 관행 조 표제에서 나온 값이라 L1 정답이 아니다. 갈림의 정도만 본다'}

    near = [x for x in residual if x.get('부분점수') and x['부분점수'][0][1] >= 3.0]

    sec_dist = collections.Counter()
    for r in items:
        for g in r['L1절']:
            sec_dist[g] += 1
    sec_dist_w = collections.Counter()
    for r in items:
        if r['출처'] != '조문표제':
            continue
        for g in r['L1절']:
            sec_dist_w[g] += r['출현조문수']

    def sha(p):
        h = hashlib.sha256()
        with open(p, 'rb') as f:
            for b in iter(lambda: f.read(1 << 20), b''):
                h.update(b)
        return h.hexdigest()

    meta = {
        '설명': '시행지침 실측 어휘(조문 표제·정의 용어) → L1 계획항목(수립지침 절) 매핑. 전부 판정 산출물이다',
        '생성근거': {
            '틀정본': '.claude/rules/계획규범요소-틀.md — L1 = 수립지침 제3장 19개 절 + 제4장(주거형) 7개 절',
            'L1정본': 'output/legal/statute/수립지침_항구조.json (admrul:2100000241690, 2024-05-29 시행)',
            '입력': [
                {'경로': rel(SRC_BLDG), 'sha256': sha(SRC_BLDG)},
                {'경로': rel(SRC_TERM), 'sha256': sha(SRC_TERM)},
                {'경로': rel(SRC_HANG), 'sha256': sha(SRC_HANG)},
            ],
            '입력해시_갱신': '2026-08-25 — 건축부문_수합.json 이 재생성돼(생성기 legal 스킬 승격) '
                             'sha256 이 edd646bb… → b3927f98… 로 바뀌었다. 조문·표제 집합은 불변이라 '
                             '매핑 내용은 그대로이고 선언값만 실측치로 맞췄다',
            '생성스크립트': '.claude/skills/kb/kb-norm/scripts/build_plan_item_map.py '
                            '(--out-dir 로 다른 곳에 뽑아 정본을 건드리지 않고 멱등성을 잰다)',
            '관측값_확정값': '이 파일의 L1절은 관측 어휘에 대한 판정이며 확정값이 아니다. 신뢰 필드로 구분한다',
        },
        '정규화규칙': NORM_STEPS,
        '매핑채널': [
            {'채널': '정확일치', '가중치': W_EXACT, '신뢰': '높음',
             '뜻': '표제 매칭키가 절제목 매칭키와 같다'},
            {'채널': '절제목핵어', '가중치': W_HEAD, '신뢰': '높음',
             '뜻': '어절이 절제목 핵어와 같다'},
            {'채널': '절제목핵어_어말', '가중치': W_HEAD_TAIL, '신뢰': '보통',
             '뜻': '어절이 절제목 핵어로 끝난다(한국어 핵어말 합성: 허용용도→용도). '
                   '어중 부분문자열은 쓰지 않는다 — 계[획지]침·적[용지]역 오탐을 막는 자리다'},
            {'채널': '절제목핵어_어두', '가중치': W_HEAD_PLAN, '신뢰': '보통',
             '뜻': "어절이 '절제목 핵어 + 계획류 접미'다(경관계획→경관). 접미 목록은 %s"
                   % '·'.join(PLAN_SUFFIX)},
            {'채널': '절제목등가표기', '가중치': W_EQUIV, '신뢰': '보통',
             '뜻': '어절이 절제목 등가표기와 완전히 같다. 실제 주제어(절제목핵어)에 밀리도록 낮게 준다'},
            {'채널': '본문어절', '가중치': W_BODY, '신뢰': '보통',
             '뜻': '어절이 그 절 항 본문에 어절로 등장한다. 점유율 %s 미만 절군을 떨군 뒤 '
                   '남은 절군끼리 재정규화한 몫을 준다' % BODY_MIN_SHARE},
            {'채널': '본문핵어_어말', '가중치': W_BODY, '신뢰': '보통',
             '뜻': '어절이 강한 본문 핵어(한 절의 3개 항 이상·점유율 0.6 이상)로 끝난다(최고층수→층수)'},
            {'채널': '본문어절_어말', '가중치': W_BODY_TAIL, '신뢰': '낮음',
             '뜻': '어절이 그보다 성긴 본문 어절로 끝난다. 단독으로는 문턱을 넘지 못한다'},
        ],
        '본문근거_세는단위': {
            '단위': '항(項)의 수. 출현 횟수가 아니다',
            '이유': '본문 색인은 항마다 어절을 뽑고 항 안에서 중복을 접는다. 한 항에 같은 '
                    '어절이 세 번 나와도 1로 센다',
            '읽는법': "매핑근거의 '본문어절x3' 은 그 어절이 그 절군의 서로 다른 3개 항에 "
                      "나왔다는 뜻이다. '본문핵어_어말:층수(x3)' 도 같다",
            '영향받는_값': ['매핑근거의 본문어절xN', '매핑근거의 본문핵어_어말:어절(xN)',
                            '본문핵어 판정의 3개 항 문턱', '신뢰하향규칙의 1개 항'],
        },
        '신뢰하향규칙': "절군을 받친 근거가 모두 '본문 1개 항 출현'이면 신뢰를 '낮음'으로 내린다",
        '판정파라미터': {'최소점수': MIN_SCORE, '우세비': DOMINANCE,
                        '본문_최대절군수': BODY_MAX_GROUPS, '본문_최소share': BODY_MIN_SHARE},
        '절군규약': {
            '뜻': '3장(공통)과 4장(주거형)이 같은 계획항목을 층위만 달리 규정하는 짝은 한 절군으로 묶고 3장 절을 대표로 둔다',
            '짝': PAIR,
            '독립': ['4-1 토지이용계획', '4-7 보칙'],
            '주의': '이 병합은 3장/4장 구조 중복을 다루는 것이고, 하나의 개념이 두 절을 다투는 다중후보와 다르다',
        },
        '절제목핵어': {g: {'핵어': hs, '근거': HEAD_EVIDENCE.get(g, "%s 절제목 '%s' 의 어절" % (g, sec_title.get(g, '')))}
                      for g, hs in SECTION_HEADS.items()},
        '절제목등가표기': {t: {'절군': g, '근거': EXACT_ONLY_EVIDENCE[t], '대조': '어절 완전일치만'}
                          for t, g in EXACT_ONLY_HEADS.items()},
        '불용어절': sorted(STOP),
        '모수': stat,
        '조문건수가중': dist_weighted,
        '신뢰분포': dict(collections.Counter(r['신뢰'] for r in items)),
        '매핑방법분포': dict(collections.Counter(r['매핑방법'] for r in items)),
        '근접잔차': {'뜻': '판정불가지만 최고 부분점수가 3.0 이상인 항목. 문턱(5.0)만 못 넘겼다',
                    '건수': len(near),
                    '표본': [{'원문': x['원문'], '출현지구수': x['출현지구수'],
                             '부분점수': x['부분점수']} for x in near[:15]]},
        '교차확인_source_topic': cross,
        'L1절별_매핑분포_항목수': [{'절군': g, '절제목': sec_title.get(g, ''), '항목수': c,
                                   '주거형대응절': PAIR.get(g)}
                                  for g, c in sorted(sec_dist.items(), key=lambda kv: -kv[1])],
        'L1절별_매핑분포_조문건수': [{'절군': g, '절제목': sec_title.get(g, ''), '조문건수': c}
                                    for g, c in sorted(sec_dist_w.items(), key=lambda kv: -kv[1])],
        '사각지대': [
            '표제만 본다. 조문 본문·표·그림 채널은 보지 않으므로 표제가 실제 규정 내용과 다른 조문은 오매핑된다',
            '수립지침 어휘에 없는 실측 개념(생태면적률 등)은 잔차로 남는다. 잔차는 결손이 아니라 관행 확장 후보다',
            '어말 핵어 채널은 한국어 핵어말 합성을 전제한다. 외래어·약어 합성은 판정하지 못한다',
            '조문표제 2,917종 중 상당수가 1개 지구에만 나타난다 — 매핑률은 지구 커버리지가 아니라 어휘 커버리지다',
        ],
        'L1축_조인': {
            '정본': 'output/kb/ontology/vocab-plan-item.ttl (발급은 kb-ontology 의 build_plan_item.py)',
            '계약': '.claude/skills/kb/kb-ontology/contract/ontology.json planItemAxis',
            'base': VOCAB_BASE,
            '조인가능_concept수': len(vocab_iris),
            '주의': '이 스크립트는 IRI 를 발급하지 않는다. 정본 ttl 에 있는 IRI 만 되쓰고 없으면 null 을 낸다',
        },
        '미해결': [
            '이 매핑은 어휘(표제·용어) 단위다. 지구별 조문 인스턴스를 L1 에 붙이려면 '
            '지구×조문 단위로 다시 돌려야 한다 — 같은 표제가 지구마다 다른 내용을 담을 수 있다',
            '매핑 산출 스키마의 계약(JSON Schema)이 아직 없다. 이 meta 가 임시 정본이다 — kb-planner 결정 사항',
            '생성 스크립트를 스킬 scripts/ 로 승격할지 정해야 한다 — kb-planner 결정 사항',
        ],
    }

    os.makedirs(OUT, exist_ok=True)
    dump = lambda p, o: json.dump(o, open(os.path.join(OUT, p), 'w', encoding='utf-8'),
                                  ensure_ascii=False, indent=1, sort_keys=False)
    dump('meta.json', meta)
    dump('plan-item-map.json', {
        'meta': {'설명': meta['설명'], '정본': 'output/kb/norm/plan-item-map/meta.json',
                 '모수': stat, '정렬': '(출처, -출현지구수, 정규화형)'},
        '항목': items})
    dump('_plan_item_residual.json', {
        'meta': {'설명': '매핑에 들어가지 못한 것과 사유. 결손이 아니라 기록이다',
                 '정본': 'output/kb/norm/plan-item-map/meta.json',
                 '구분': {'판정불가': '어느 절에도 안착하지 않음 — 관행 확장 후보',
                          '다중후보': '두 절 이상을 같은 강도로 지지 — 확정하지 않고 격리',
                          '표제형식_아님': '조문 본문·표 잔재가 표제로 들어온 것 — 상류(건축부문 수합) 품질 문제'},
                 '모수': stat},
        '판정불가': residual, '다중후보': contest, '표제형식_아님': nontitle})
    if not args.quiet:
        print(json.dumps(stat, ensure_ascii=False, indent=1))
        print('조문건수가중', dist_weighted)
        print('상위절', [(g, sec_title.get(g), c) for g, c in sec_dist.most_common(10)])
    return OUT


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description='실측 어휘(조문 표제·정의 용어) → L1 계획항목(수립지침 절) 매핑')
    ap.add_argument('--out-dir', default=DEFAULTS['out_dir'],
                    help='산출 폴더. 기본 %(default)s. 다른 곳을 주면 정본을 건드리지 않고 '
                         '두 번 뽑아 해시를 견줄 수 있다')
    ap.add_argument('--hang', default=DEFAULTS['hang'], help='수립지침 항구조 (기본 %(default)s)')
    ap.add_argument('--bldg', default=DEFAULTS['bldg'], help='건축부문 수합 (기본 %(default)s)')
    ap.add_argument('--term', default=DEFAULTS['term'], help='정의 용어 (기본 %(default)s)')
    ap.add_argument('--vocab', default=DEFAULTS['vocab'],
                    help='L1 SKOS 축 ttl. 조인만 하고 발급하지 않는다 (기본 %(default)s)')
    ap.add_argument('--quiet', action='store_true', help='요약 출력을 접는다')
    args = ap.parse_args(argv)
    for k in ('out_dir', 'hang', 'bldg', 'term', 'vocab'):
        v = getattr(args, k)
        if not os.path.isabs(v):
            setattr(args, k, os.path.join(ROOT, v))
    return args


if __name__ == '__main__':
    build(parse_args())
