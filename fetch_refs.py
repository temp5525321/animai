"""
AI 드라마 / AI 광고 레퍼런스 수집기 (v2)

v1 실측에서 드러난 문제
  조회수 상위 = 슈퍼볼 pre-roll 광고 (참여율 0.0000) + "AI 제품을 파는 광고"
  드라마 1위 = 84분짜리 AI 다큐멘터리 (AI로 만든 게 아님)
  → "AI에 관한 영상"과 "AI로 만든 영상"이 섞였다. 이게 핵심 오염원.

v2 설계
  1) is_ai_generated_likely / is_ai_topic_only_likely 를 분리 기록.
     오염 후보를 버리지 않고 "왜 아닌지"를 남긴다 → 기준이 바뀌어도 재수집 불필요.
  2) 합산 점수를 확정값처럼 박지 않는다.
     원시·파생 지표를 전부 저장하고 quality_score는 그 위에서 계산한 파생물일 뿐.
     scoring_version + score_breakdown 이 있어 가중치만 바꿔 재계산할 수 있다.
  3) view_sub_ratio(조회수/구독자)로 대형 채널 독식을 막는다.
  4) 댓글 분석은 전체가 아니라 1차 정렬 상위에만 (영상당 1유닛이라 전체는 낭비).
  5) 쇼츠 기준을 드라마와 광고에 다르게 적용한다.
     드라마 = 60초 초과 우선 / 광고 = 세로 쇼츠 적극 허용 (AI UGC 광고는 세로가 주류)
"""
import os
import re
import sys
import json
import math
import requests
from datetime import datetime, timezone, timedelta

SCORING_VERSION = 'v2-2026-09-11'

YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY_TEST') or os.environ.get('YOUTUBE_API_KEY', '')
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
SUPABASE_KEY = SUPABASE_SERVICE_KEY or os.environ.get('SUPABASE_KEY', '')
CAN_UPSERT = bool(SUPABASE_SERVICE_KEY)

KST = timezone(timedelta(hours=9))

if not all([YOUTUBE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print('오류: 환경변수가 설정되지 않았습니다.')
    sys.exit(1)

SUPABASE_HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal',
}

YT_SEARCH = 'https://www.googleapis.com/youtube/v3/search'
YT_VIDEOS = 'https://www.googleapis.com/youtube/v3/videos'
YT_CHANNELS = 'https://www.googleapis.com/youtube/v3/channels'
YT_COMMENTS = 'https://www.googleapis.com/youtube/v3/commentThreads'

SEARCH_MONTHS = 12
PAGES_PER_KEYWORD = 2      # 키워드당 페이지 수 (1페이지=50개, 100유닛)
COMMENT_TOP_N = 100        # 갈래별 상위 몇 개에만 댓글 분석할지
MIN_VIEWS = 1000

# ─────────────────────────────────────────────────────────────
# ★ 엔진 목록 — 유지보수는 여기 한 곳만 하면 된다.
#   검색어에도 쓰이고 탐지에도 쓰인다. 새 엔진이 나오면 여기에만 추가.
#   단, 여기 없는 엔진도 discover_engine()이 설명란에서 자동으로 잡아낸다.
# ─────────────────────────────────────────────────────────────
ENGINES = {
    'Seedance':   ['seedance', '시댄스', '即梦'],
    'Veo':        ['veo 3', 'veo3', 'veo 2', 'google veo'],
    'Sora':       ['sora'],
    'Kling':      ['kling', '클링', '可灵'],
    'Runway':     ['runway', 'gen-3', 'gen-4'],
    'Higgsfield': ['higgsfield'],
    'Midjourney': ['midjourney'],
    'Hailuo':     ['hailuo', 'minimax'],
    'Grok':       ['grok imagine'],
    'Pika':       ['pika labs'],
    'Luma':       ['dream machine', 'luma ai'],
    'Wan':        ['wan 2.', 'wan2.'],
}
# 검색어에 쓸 엔진 (전부 쓰면 예산 초과 → 현재 판에서 결과물이 많은 것만)
SEARCH_ENGINES = ['Seedance', 'Veo', 'Sora', 'Kling', 'Runway', 'Higgsfield']

# ─────────────────────────────────────────────────────────────
# 검색어 — 총 24개 (24 × 2페이지 × 100유닛 = 4,800유닛)
#   generated  "AI로 만든"을 직접 노리는 어법. 엔진이 바뀌어도 안 썩는다
#   engine     엔진명 + 장르. 정밀도가 가장 높다
#   festival   큐레이션이 이미 끝난 물건
#   ※ making(제작과정) 갈래는 뺐다 — v1에서 브이로그·해설이 대량 유입됐다
# ─────────────────────────────────────────────────────────────
SEARCH_PLAN = {
    'drama': {
        'label': 'AI 드라마',
        'lanes': {
            'generated': [
                'AI로 만든 드라마', 'AI 생성 단편영화',
                'AI generated short film', 'made with AI short film',
                'generative AI film', 'AI generated series episode',
            ],
            'engine': [f'{e} short film' for e in SEARCH_ENGINES],
            'festival': ['AI Film Festival winner'],
        },
    },
    'ad': {
        'label': 'AI 광고',
        'lanes': {
            'generated': [
                'AI로 만든 광고', 'AI 생성 광고',
                'AI generated commercial', 'AI generated ad',
                'made with AI commercial', 'AI generated UGC ad',
            ],
            'engine': [f'{e} commercial' for e in SEARCH_ENGINES],
            'festival': ['best AI generated commercial'],
        },
    },
}

# "AI로 만들었다"는 신호
AI_GEN_PHRASES = [
    'ai generated', 'ai-generated', 'generated with ai', 'made with ai', 'made using ai',
    'created with ai', 'created using ai', 'generative ai', 'ai animation', 'ai filmmaking',
    'ai로 만든', 'ai로 제작', 'ai 생성', 'ai 제작', '생성형 ai', 'ai로 만들었',
]

# "AI 제품을 파는 광고" = 오염. v1 실측에서 실제로 걸린 것들
AI_PRODUCT_BRANDS = [
    'alexa', 'chatgpt', 'copilot', 'gemini', 'base44', 'albert',
    'perplexity', 'claude', 'notion ai', 'salesforce', 'ai assistant',
    'ai financial', 'ai agent', 'siri',
]
BROADCAST_MARKERS = ['super bowl', 'superbowl', 'big game', 'official commercial', 'tv commercial']

# 제작 해설·리뷰·뉴스 = 레퍼런스가 아님
TUTORIAL_MARKERS = [
    'tutorial', 'how to', 'how i made', 'step by step', 'beginner', 'course', 'masterclass',
    'review', 'reaction', 'react', 'news', 'explained', 'breakdown', 'compilation',
    'top 10', 'best of', 'vs ', 'comparison',
    '만드는 법', '만드는법', '강의', '강좌', '리뷰', '리액션', '뉴스', '모음', '정리', '비교',
]

SERIES_MARKERS = ['ep.', 'ep ', 'episode', 'season', '시즌', '시리즈', '화 ', '1화', '2화', '3화', 'part ']

# 댓글 반응 — 갈래별로 다른 말이 나온다
COMMENT_POS = {
    'drama': ['다음화', '다음 화', '몰입', '스토리', '세계관', '영화 같', '드라마 같', '퀄리티',
              'next episode', 'story', 'immersive', 'cinematic', 'masterpiece', 'goosebumps'],
    'ad': ['어디서 사', '링크', '가격', '사고 싶', '써보고 싶', '광고인데',
           'where to buy', 'link', 'price', 'want this', 'actually watched'],
}
COMMENT_NEG = ['어색', 'ai 티', '낚시', '시간 아깝', '별로', 'creepy', 'uncanny', 'soulless',
               'clickbait', 'waste of time', 'slop']

ISO_DUR = re.compile(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')
EMBED_WH = re.compile(r'width="(\d+)".*?height="(\d+)"', re.DOTALL)
DISCOVER_PAT = re.compile(
    r'(?:made\s+with|created\s+with|generated\s+with|powered\s+by|사용\s*툴|제작\s*툴)'
    r'[:\s]*([A-Za-z][A-Za-z0-9.\-]{1,18}(?:\s+[A-Z0-9][A-Za-z0-9.\-]{0,12})?)',
)


def parse_duration(iso):
    m = ISO_DUR.match(iso or '')
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def detect_engine(text):
    low = f' {text.lower()} '
    for name, aliases in ENGINES.items():
        if any(a in low for a in aliases):
            return name
    return None


def discover_engine(desc):
    """목록에 없는 엔진을 설명란에서 잡는다."""
    m = DISCOVER_PAT.search(desc or '')
    if not m:
        return None
    raw = m.group(1).strip(' .-\n\t,')
    if len(raw) < 3 or raw.lower() in ('ai', 'the', 'my', 'this', 'love', 'care'):
        return None
    return raw[:40]


def hits(text, markers):
    low = text.lower()
    return [m for m in markers if m in low]


def norm(v, cap):
    """0..1로 정규화"""
    if cap <= 0:
        return 0.0
    return max(0.0, min(float(v) / cap, 1.0))


def duration_tier(kind, sec, series):
    """길이 기준 우선순위. 드라마와 광고에 다른 기준을 쓴다."""
    if kind == 'drama':
        if 60 < sec <= 900:
            return 'primary'
        if 30 <= sec <= 60:
            return 'secondary'
        if sec < 30 and not series:
            return None                       # 30초 미만 + 시리즈 신호 없음 → 탈락
        if 900 < sec <= 1800:
            return 'secondary'
        return None                            # 30분 초과 → 탈락 (v1의 84분 다큐를 막는다)
    # 광고 — 세로 쇼츠를 막지 않는다
    if 10 <= sec <= 120:
        return 'primary'
    if 120 < sec <= 300:
        return 'secondary'
    return None


# ─────────────────────────────────────────────────────────────
# YouTube API
# ─────────────────────────────────────────────────────────────
def search_videos(keyword, published_after, pages=PAGES_PER_KEYWORD):
    """조회수 순. regionCode·relevanceLanguage를 쓰지 않아 국내·해외 전체를 본다."""
    out, token = [], None
    for _ in range(pages):
        params = {
            'part': 'snippet', 'q': keyword, 'type': 'video', 'maxResults': 50,
            'order': 'viewCount', 'publishedAfter': published_after, 'key': YOUTUBE_API_KEY,
        }
        if token:
            params['pageToken'] = token
        r = requests.get(YT_SEARCH, params=params, timeout=30)
        if r.status_code != 200:
            print(f'    검색 오류({r.status_code}): {r.text[:140]}')
            break
        data = r.json()
        out.extend(data.get('items', []))
        token = data.get('nextPageToken')
        if not token:
            break
    return out


def get_video_details(ids):
    out = {}
    for i in range(0, len(ids), 50):
        params = {
            'part': 'statistics,snippet,status,contentDetails,player',
            'id': ','.join(ids[i:i + 50]), 'maxHeight': 720, 'key': YOUTUBE_API_KEY,
        }
        r = requests.get(YT_VIDEOS, params=params, timeout=30)
        if r.status_code != 200:
            print(f'    상세 오류({r.status_code}): {r.text[:140]}')
            continue
        for it in r.json().get('items', []):
            st, sn, cd, pl = (it.get('statistics', {}), it.get('snippet', {}),
                              it.get('contentDetails', {}), it.get('player', {}))
            vertical = False
            wh = EMBED_WH.search(pl.get('embedHtml', '') or '')
            if wh:
                vertical = int(wh.group(2)) > int(wh.group(1))
            out[it['id']] = {
                'views': int(st.get('viewCount', 0) or 0),
                'likes': int(st.get('likeCount', 0) or 0),
                'comments': int(st.get('commentCount', 0) or 0),
                'description': sn.get('description', ''),
                'tags': sn.get('tags', []) or [],
                'channel_id': sn.get('channelId', ''),
                'duration_sec': parse_duration(cd.get('duration', '')),
                'is_vertical': vertical,
                'embeddable': it.get('status', {}).get('embeddable', True),
            }
    return out


def get_channel_subs(channel_ids):
    """★조회수/구독자 비율용. 50개당 1유닛으로 거의 공짜."""
    subs = {}
    ids = [c for c in set(channel_ids) if c]
    for i in range(0, len(ids), 50):
        params = {'part': 'statistics', 'id': ','.join(ids[i:i + 50]), 'key': YOUTUBE_API_KEY}
        r = requests.get(YT_CHANNELS, params=params, timeout=30)
        if r.status_code != 200:
            continue
        for it in r.json().get('items', []):
            subs[it['id']] = int(it.get('statistics', {}).get('subscriberCount', 0) or 0)
    return subs


def analyse_comments(video_id, kind):
    """상위 후보에만 실행. 댓글이 꺼져 있어도 후보를 버리지 않는다."""
    params = {'part': 'snippet', 'videoId': video_id, 'maxResults': 50,
              'order': 'relevance', 'textFormat': 'plainText', 'key': YOUTUBE_API_KEY}
    r = requests.get(YT_COMMENTS, params=params, timeout=30)
    if r.status_code == 403:
        return 'disabled', 0.0
    if r.status_code != 200:
        return 'failed', 0.0
    pos = neg = 0
    for it in r.json().get('items', []):
        txt = it['snippet']['topLevelComment']['snippet'].get('textDisplay', '').lower()
        if any(w in txt for w in COMMENT_POS.get(kind, [])):
            pos += 1
        if any(w in txt for w in COMMENT_NEG):
            neg += 1
    total = pos + neg
    if total == 0:
        return 'ok', 0.0
    return 'ok', round((pos - neg) / total, 4)


# ─────────────────────────────────────────────────────────────
# 판정 & 점수
# ─────────────────────────────────────────────────────────────
def judge(kind, title, desc, tags, engine):
    """AI로 만든 것인가, AI에 관한 것인가."""
    hay = f'{title} {desc} {" ".join(tags)}'
    gen_hits = hits(hay, AI_GEN_PHRASES)
    brand_hits = hits(hay, AI_PRODUCT_BRANDS)
    bcast_hits = hits(hay, BROADCAST_MARKERS)
    tut_hits = hits(title, TUTORIAL_MARKERS)
    series = bool(hits(title, SERIES_MARKERS))

    ai_signal = min(1.0, 0.55 * bool(gen_hits) + 0.45 * bool(engine) + 0.1 * (len(gen_hits) > 1))
    pollution = min(1.0, 0.5 * bool(brand_hits) + 0.35 * bool(bcast_hits) + 0.4 * bool(tut_hits))
    if ai_signal >= 0.5:
        pollution = max(0.0, pollution - 0.3)   # 제작 방식이 AI라고 명시되면 오염 의심을 낮춘다

    ai_gen = ai_signal >= 0.45
    topic_only = (not ai_gen) and (pollution >= 0.35 or bool(brand_hits))

    reason = None
    if topic_only:
        if brand_hits:
            reason = f'AI 제품 광고로 보임 (AI로 만든 것이 아님): {", ".join(brand_hits[:3])}'
        elif tut_hits:
            reason = f'해설·리뷰·튜토리얼로 보임: {", ".join(tut_hits[:3])}'
        elif bcast_hits:
            reason = f'일반 방송 광고로 보임: {", ".join(bcast_hits[:3])}'
    elif not ai_gen:
        reason = 'AI 제작 신호 없음 (엔진명·생성 어법 모두 미검출)'

    return {
        'ai_signal': round(ai_signal, 3),
        'pollution': round(pollution, 3),
        'is_ai_generated_likely': ai_gen,
        'is_ai_topic_only_likely': topic_only,
        'is_tutorial_or_review_likely': bool(tut_hits),
        'is_series_likely': series,
        'reject_reason': reason,
    }


def keyword_match(kind, title, desc, tags, series):
    """제목·설명·태그가 그 갈래답게 생겼는지."""
    hay = f'{title} {desc} {" ".join(tags)}'.lower()
    if kind == 'drama':
        words = ['drama', 'film', 'story', 'cinematic', 'short', 'episode',
                 '드라마', '영화', '스토리', '단편', '웹드라마']
        bonus = 0.2 if series else 0.0
    else:
        words = ['ad', 'ads', 'commercial', 'brand', 'product', 'campaign', 'launch', 'ugc',
                 '광고', '제품', '브랜드', '캠페인', '출시']
        bonus = 0.0
    found = sum(1 for w in words if w in hay)
    return round(min(1.0, found / 3 + bonus), 3)


def score(kind, m, j, kw_match, comment_signal):
    """quality_score = 확정값이 아니라 파생물.
    breakdown을 같이 저장해 가중치만 바꿔 재계산할 수 있게 한다."""
    parts = {
        'views':       15 * norm(math.log10(max(m['views'], 1)), math.log10(10_000_000)),
        'like_rate':   20 * norm(m['like_rate'], 0.08),
        'comment_rate':15 * norm(m['comment_rate'], 0.01),
        'view_sub':    20 * norm(m['view_sub_ratio'], 10),
        'freshness':   10 * max(0.0, 1 - m['age_days'] / 365),
        'keyword':     15 * kw_match,
        'clarity':     10 * (1.0 if (j['is_series_likely'] if kind == 'drama' else kw_match > 0.5) else 0.3),
        'ai_signal':   15 * j['ai_signal'],
        'comments_fb': 10 * max(0.0, comment_signal),
        'pollution':  -40 * j['pollution'],
    }
    total = round(sum(parts.values()), 2)
    return max(0.0, total), {k: round(v, 2) for k, v in parts.items()}


# ─────────────────────────────────────────────────────────────
def get_existing_ids():
    ids, offset = set(), 0
    while True:
        h = {**SUPABASE_HEADERS, 'Range': f'{offset}-{offset + 999}'}
        r = requests.get(f'{SUPABASE_URL}/rest/v1/youtube_refs?select=video_id', headers=h, timeout=30)
        if r.status_code not in (200, 206):
            break
        rows = r.json()
        if not rows:
            break
        ids.update(x['video_id'] for x in rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return ids


def save(rows):
    if not rows:
        return 0
    h = dict(SUPABASE_HEADERS)
    url = f'{SUPABASE_URL}/rest/v1/youtube_refs'
    if CAN_UPSERT:
        h['Prefer'] = 'return=minimal,resolution=merge-duplicates'
        url += '?on_conflict=video_id'
    ok = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        r = requests.post(url, headers=h, json=chunk, timeout=90)
        if r.status_code in (200, 201, 204):
            ok += len(chunk)
        else:
            print(f'  배치 실패({r.status_code}) → 개별 전환: {r.text[:140]}')
            for one in chunk:
                if requests.post(url, headers=h, json=[one], timeout=30).status_code in (200, 201, 204):
                    ok += 1
    return ok


def main():
    now = datetime.now(KST)
    print(f'[{now:%Y-%m-%d %H:%M:%S} KST] AI 드라마/광고 레퍼런스 수집 v2 ({SCORING_VERSION})')
    print(f'모드: {"upsert" if CAN_UPSERT else "insert-only (service_role 키 없음)"}')

    after = (now - timedelta(days=SEARCH_MONTHS * 30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    existing = set() if CAN_UPSERT else get_existing_ids()
    print(f'기존 저장분: {len(existing)}개')

    cand, searches = {}, 0
    for kind, plan in SEARCH_PLAN.items():
        print(f'\n=== {plan["label"]} ===')
        for lane, kws in plan['lanes'].items():
            for kw in kws:
                items = search_videos(kw, after)
                searches += 1
                new = 0
                for it in items:
                    vid = it.get('id', {}).get('videoId', '')
                    if not vid or vid in cand or vid in existing:
                        continue
                    sn = it.get('snippet', {})
                    cand[vid] = {
                        'video_id': vid, 'kind': kind, 'lane': lane, 'search_keyword': kw,
                        'title': sn.get('title', ''), 'channel': sn.get('channelTitle', ''),
                        'published_at': sn.get('publishedAt', '')[:10],
                        'thumb': sn.get('thumbnails', {}).get('medium', {}).get('url', ''),
                        'url': f'https://www.youtube.com/watch?v={vid}',
                        'embed_url': f'https://www.youtube.com/embed/{vid}',
                    }
                    new += 1
                print(f'  [{lane:<9}] "{kw}" → {len(items)}개 (신규 {new})')

    print(f'\n검색 {searches}회 / 신규 후보 {len(cand)}개')
    if not cand:
        print('신규 후보 없음. 종료.')
        return

    details = get_video_details(list(cand))
    subs = get_channel_subs([d['channel_id'] for d in details.values()])

    rows, drop_dur, drop_view = [], 0, 0
    for vid, base in cand.items():
        d = details.get(vid)
        if not d:
            continue
        if d['views'] < MIN_VIEWS:
            drop_view += 1
            continue

        hay_title = base['title']
        engine = detect_engine(f"{hay_title} {d['description']} {' '.join(d['tags'])}")
        j = judge(base['kind'], hay_title, d['description'], d['tags'], engine)

        tier = duration_tier(base['kind'], d['duration_sec'], j['is_series_likely'])
        if tier is None:
            drop_dur += 1
            continue

        try:
            pub = datetime.strptime(base['published_at'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
            age = max(1, (now - pub).days)
        except ValueError:
            age = 1

        v = d['views']
        sub = subs.get(d['channel_id'], 0)
        m = {
            'views': v, 'age_days': age,
            'like_rate': round(d['likes'] / v, 6) if v else 0,
            'comment_rate': round(d['comments'] / v, 6) if v else 0,
            'view_sub_ratio': round(v / sub, 4) if sub else 0,
            'views_per_day': round(v / age, 2),
        }
        kw_m = keyword_match(base['kind'], hay_title, d['description'], d['tags'], j['is_series_likely'])
        q, breakdown = score(base['kind'], m, j, kw_m, 0.0)

        rows.append({
            **base, 'tier': tier,
            'channel_id': d['channel_id'], 'channel_subs': sub,
            'duration_sec': d['duration_sec'], 'is_vertical': d['is_vertical'],
            'likes': d['likes'], 'comments': d['comments'], **m,
            'is_ai_generated_likely': j['is_ai_generated_likely'],
            'is_ai_topic_only_likely': j['is_ai_topic_only_likely'],
            'is_tutorial_or_review_likely': j['is_tutorial_or_review_likely'],
            'is_series_likely': j['is_series_likely'],
            'reject_reason': j['reject_reason'],
            'ai_signal_score': j['ai_signal'], 'pollution_risk_score': j['pollution'],
            'keyword_match_score': kw_m,
            'quality_score': q, 'scoring_version': SCORING_VERSION,
            'score_breakdown': breakdown,
            'engine': engine, 'engine_raw': discover_engine(d['description']),
            'embeddable': d['embeddable'],
            'description': d['description'][:4000], 'tags': d['tags'][:30],
        })

    print(f'길이 기준 제외 {drop_dur}개 · 조회수 미달 제외 {drop_view}개 → 후보 {len(rows)}개')

    # 댓글 분석 — 갈래별 상위 N개에만 (영상당 1유닛이라 전체는 낭비)
    checked = 0
    for kind in SEARCH_PLAN:
        top = sorted([r for r in rows if r['kind'] == kind],
                     key=lambda r: r['quality_score'], reverse=True)[:COMMENT_TOP_N]
        for r in top:
            st, sig = analyse_comments(r['video_id'], kind)
            r['comment_status'], r['comment_signal'] = st, sig
            checked += 1
            if sig:
                q, bd = score(kind, r, {
                    'ai_signal': r['ai_signal_score'], 'pollution': r['pollution_risk_score'],
                    'is_series_likely': r['is_series_likely'],
                }, r['keyword_match_score'], sig)
                r['quality_score'], r['score_breakdown'] = q, bd
    print(f'댓글 분석: {checked}개 (상위 {COMMENT_TOP_N}개/갈래)')

    gen = sum(1 for r in rows if r['is_ai_generated_likely'])
    topic = sum(1 for r in rows if r['is_ai_topic_only_likely'])
    print(f'\n★ AI로 만든 것: {gen}개 / AI에 관한 것(오염): {topic}개 / 판정 보류: {len(rows)-gen-topic}개')
    for k, p in SEARCH_PLAN.items():
        kr = [r for r in rows if r['kind'] == k]
        kg = sum(1 for r in kr if r['is_ai_generated_likely'])
        print(f'  {p["label"]}: 전체 {len(kr)}개 · AI 제작 {kg}개')

    unknown = sorted({r['engine_raw'] for r in rows if r['engine_raw'] and not r['engine']})
    if unknown:
        print(f'\n목록에 없는 엔진 후보: {", ".join(unknown[:15])}')

    saved = save(rows)
    print(f'\n저장 완료: {saved}/{len(rows)}개')
    print(f'[{datetime.now(KST):%Y-%m-%d %H:%M:%S} KST] 완료')


if __name__ == '__main__':
    main()
