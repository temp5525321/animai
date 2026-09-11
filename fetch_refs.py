"""
AI 드라마 / AI 영상 레퍼런스 수집기 (v3)

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

SCORING_VERSION = 'v3-2026-09-11'

# ★키를 fetch_youtube.py와 일부러 다르게 집는다.
#   두 크롤러가 같은 키를 쓰면 10,000을 나눠 쓰지만,
#   갈라놓으면 각자 10,000을 통째로 쓴다 (테스트 키는 별도 프로젝트 = 별도 할당량).
#   fetch_youtube.py → YOUTUBE_API_KEY_TEST 우선
#   fetch_refs.py    → YOUTUBE_API_KEY(운영) 우선   ← 그동안 놀던 쪽
_KEY_SOURCE = next((n for n in ('YOUTUBE_API_KEY_REFS', 'YOUTUBE_API_KEY', 'YOUTUBE_API_KEY_TEST')
                    if os.environ.get(n)), '(없음)')
YOUTUBE_API_KEY = os.environ.get(_KEY_SOURCE, '')
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

# ─────────────────────────────────────────────────────────────
# 실험 손잡이 — 코드를 고치지 않고 환경변수로 조절한다.
#   ★할당량을 먹는 건 (키워드 수 × 페이지 수) 뿐이다.
#     검색 기간(REFS_MONTHS)은 결과만 거를 뿐 비용은 그대로다.
#   기본값 = 실험 모드(싸게, 자주). 본격 수집 때만 페이지·댓글을 올린다.
#     실험  1페이지 + 댓글끔 = 약 2,400유닛 → 하루에 세 번 더 돌릴 수 있음
#     본수집 2페이지 + 댓글100 = 약 5,000유닛
# ─────────────────────────────────────────────────────────────
SEARCH_MONTHS = int(os.environ.get('REFS_MONTHS', 0))        # 0 = 갈래별 기본값 사용. 값을 주면 전부 덮어쓴다
PAGES_PER_KEYWORD = int(os.environ.get('REFS_PAGES', 1))     # 키워드당 페이지 (1페이지=50개, 100유닛)
COMMENT_TOP_N = int(os.environ.get('REFS_COMMENT_TOP', 0))   # 갈래별 상위 N개만 댓글 분석. 0=끔
MIN_VIEWS = int(os.environ.get('REFS_MIN_VIEWS', 1000))
DRY_RUN = os.environ.get('REFS_DRY_RUN', '') == '1'          # 저장하지 않고 결과만 본다

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
# ⚠️ Runway는 '활주로·패션쇼 런웨이', Sora는 일반 단어와 겹쳐 오폭이 심했다 → 검색어에서 제외.
#    탐지용 ENGINES에는 그대로 남아 있어 결과물에서는 계속 잡힌다.
SEARCH_ENGINES = ['Seedance', 'Veo', 'Kling']

# ─────────────────────────────────────────────────────────────
# 검색 계획 (v3) — 갈래마다 기간과 정렬을 다르게 둔다
#
#   ★order=viewCount는 누적 조회수라 구조적으로 오래된 영상만 올린다.
#     실측: 경과일 중앙값 99일, 이번 달 업로드는 50건 중 4건뿐.
#     → order=date를 병행해 최신작을 따로 들여오고, 순위는 내부 점수로 다시 매긴다.
#     즉 YouTube의 정렬은 "수집용"이고 실제 순위는 우리가 정한다.
#
#   ★kind를 drama / general / ad_like 로 재편했다.
#     광고를 검색 단계에서 맞히려는 시도는 3번 다 실패했다(상위 15개 중 쓸 만한 게 3개).
#     유튜브에서 commercial은 실제광고·스펙광고·쇼릴·스킷·논평 다섯 가지로 쓰여
#     검색어로는 분리가 안 된다. → 넓게 모아 두고 저장 단계에서 태그로 가른다.
# ─────────────────────────────────────────────────────────────
SEARCH_PLAN = {
    'drama': {
        'label': 'AI 드라마',
        'lanes': {
            # 잘 작동 중인 갈래라 건드리지 않는다
            'generated': {'days': 180, 'orders': ['viewCount', 'date'], 'kw': [
                'AI로 만든 드라마', 'AI generated short film',
                'made with AI short film', 'AI generated series episode']},
            'engine':    {'days': 180, 'orders': ['date'], 'kw':
                [f'{e} short film' for e in SEARCH_ENGINES]},
            # 공모전·영화제는 시즌 이벤트라 오래된 것도 가치가 있다 → 1년
            'festival':  {'days': 365, 'orders': ['relevance'], 'kw': ['AI Film Festival winner']},
            # 축소 — 순도 39%지만 「독배」같은 한국 웹드라마를 여기서만 건졌다
            'broad':     {'days': 180, 'orders': ['viewCount'], 'kw': ['AI 웹드라마', 'AI 단편영화']},
        },
    },
    'general': {
        'label': 'AI 영상 일반',
        'lanes': {
            # 광고 자리를 대체한다. 최신 흐름을 보는 게 목적이라 date 중심 · 90일
            'generated': {'days': 90, 'orders': ['date'], 'kw': [
                'AI로 만든 영상', 'AI generated video',
                'generative AI video', 'AI cinematic video']},
            'engine':    {'days': 90, 'orders': ['date'], 'kw':
                [f'{e} AI video' for e in SEARCH_ENGINES]},
        },
    },
    'ad_like': {
        'label': '광고성 후보',
        'lanes': {
            # ★"확정 광고"가 아니라 "광고성 AI 영상 후보"로만 취급한다.
            #   AI 제품/서비스/앱 광고 계열과 'Runway commercial' 단독은 오폭이 심해 전부 뺐다.
            'generated': {'days': 90, 'orders': ['date'], 'kw': [
                'AI generated commercial', 'AI generated spec ad',
                'made with AI commercial', 'AI UGC ad', 'AI brand film']},
        },
    },
}

# "AI로 만들었다"는 신호
AI_GEN_PHRASES = [
    'ai generated', 'ai-generated', 'generated with ai', 'made with ai', 'made using ai',
    'created with ai', 'created using ai', 'generative ai', 'ai animation', 'ai filmmaking',
    'ai로 만든', 'ai로 제작', 'ai 생성', 'ai 제작', '생성형 ai', 'ai로 만들었',
    # ★한국 AI 드라마 제작자는 "AI로 만들었다"고 안 쓰고 장르명처럼 쓴다.
    #   실측: 보류 120개 중 상당수가 이것 때문에 빠졌다
    #   (「[AI 웹드라마] 내 여자친구는 로봇입니다」, 「AI 로판 회귀물」 등)
    'ai 드라마', 'ai드라마', 'ai 웹드라마', 'ai웹드라마', 'ai 숏폼', 'ai숏폼',
    'ai 로판', 'ai로판', 'ai 단편', 'ai단편', 'ai 영화', 'ai영화', 'ai 영상', 'ai영상',
    'ai 애니', 'ai애니', 'ai 광고', 'ai광고',
    # ★해시태그는 붙여 쓴다 — #aigenerated, #aifilm.
    #   실측: 「Hero Dog Saves Pregnant Woman #aigenerated」가 보류로 빠졌다
    'aigenerated', 'aivideo', 'aifilm', 'aidrama', 'aishortfilm', 'madewithai',
    'aicinema', 'generativeai', 'aianimation',
]

# "AI 제품을 파는 광고" = 오염. v1 실측에서 실제로 걸린 것들
AI_PRODUCT_BRANDS = [
    'alexa', 'chatgpt', 'copilot', 'gemini', 'base44', 'albert',
    'perplexity', 'claude', 'notion ai', 'salesforce', 'ai assistant',
    'ai financial', 'ai agent', 'siri',
]
BROADCAST_MARKERS = ['super bowl', 'superbowl', 'big game', 'official commercial', 'tv commercial']

# 제작 해설·리뷰·뉴스 = 레퍼런스가 아님
# ⚠️ 'vs '는 뺐다 — 「천군｜99명의 한국군 vs 왜군 10만｜AI 단편영화」처럼
#    진짜 작품이 잘려나갔다. 대신 reaction/comparison 같은 구체어로 좁힌다.
TUTORIAL_MARKERS = [
    'tutorial', 'how to', 'how i made', 'step by step', 'beginner', 'course', 'masterclass',
    'review', 'reaction', 'react to', 'news', 'explained', 'breakdown', 'compilation',
    'comparison', 'best of',
    # ↓ 실측에서 상위를 먹었던 튜토리얼·랭킹 패턴
    'top 3', 'top 5', 'top 10', 'free ai tool', 'ai tools', 'in 2 minutes', 'in 5 minutes',
    'create a ', 'make ai', 'better than 99', 'ranking:', 'try not to', 'the most ',
    '만드는 법', '만드는법', '강의', '강좌', '리뷰', '리액션', '뉴스', '모음', '정리', '비교',
    '따라하기', '초보', '꿀팁',
]

# 광고성 판정용 어휘 (태그 계산에 쓴다)
AD_WORDS = ['commercial', ' ad ', 'advert', 'brand', 'campaign', 'product', 'launch', 'ugc',
            'spec ad', '광고', '제품', '브랜드', '캠페인', '출시']
MAKING_WORDS = ['making of', 'behind the scenes', 'workflow', 'pipeline', 'process', 'prompt',
                '제작 과정', '메이킹', '워크플로우']

SERIES_MARKERS = ['ep.', 'ep ', 'episode', 'season', '시즌', '시리즈', '화 ', '1화', '2화', '3화', 'part ']

# 댓글 반응 — 갈래별로 다른 말이 나온다
COMMENT_POS = {
    'drama': ['다음화', '다음 화', '몰입', '스토리', '세계관', '영화 같', '드라마 같', '퀄리티',
              'next episode', 'story', 'immersive', 'cinematic', 'masterpiece', 'goosebumps'],
    'ad_like': ['어디서 사', '링크', '가격', '사고 싶', '써보고 싶', '광고인데',
                'where to buy', 'link', 'price', 'want this', 'actually watched'],
    'general': ['미쳤다', '실화', '퀄리티', '어떻게 만든', 'insane', 'incredible', 'how did you'],
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


_PUNCT = re.compile(r'[^0-9a-z가-힣#]+')

def hits(text, markers):
    """★기호를 공백으로 바꿔 정규화한 뒤 찾는다.
       「[AI] 드라마 선덕여왕」처럼 괄호가 끼면 'ai 드라마'가 매칭되지 않았다.
       해시태그(#)는 남겨 둔다 — #aigenerated 류를 따로 잡기 때문."""
    low = _PUNCT.sub(' ', text.lower())
    return [m for m in markers if m in low or m in text.lower()]


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
    if kind == 'ad_like':
        # 광고성 후보 — 세로 쇼츠를 막지 않는다 (AI UGC 광고는 세로가 주류)
        if 10 <= sec <= 120:
            return 'primary'
        if 120 < sec <= 300:
            return 'secondary'
        return None
    # AI 영상 일반 — 넓게 받되 극단만 자른다
    if 30 <= sec <= 900:
        return 'primary'
    if 15 <= sec < 30 or 900 < sec <= 1800:
        return 'secondary'
    return None


# ─────────────────────────────────────────────────────────────
# YouTube API
# ─────────────────────────────────────────────────────────────
def search_videos(keyword, published_after, order='viewCount', pages=PAGES_PER_KEYWORD):
    """★order는 '수집용' 정렬일 뿐, 실제 순위는 우리 점수로 다시 매긴다.
       viewCount = 누적이라 오래된 것에 유리 / date = 최신작 유입.
       regionCode·relevanceLanguage를 쓰지 않아 국내·해외 전체를 본다."""
    out, token = [], None
    for _ in range(pages):
        params = {
            'part': 'snippet', 'q': keyword, 'type': 'video', 'maxResults': 50,
            'order': order, 'publishedAfter': published_after, 'key': YOUTUBE_API_KEY,
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

    # ★오염을 두 갈래로 나눈다 — 성격이 다르기 때문이다.
    #   ①"AI 제품 광고인가"  → AI로 만들었다고 명시하면 의심을 낮춰도 된다
    #   ②"튜토리얼·뉴스인가"  → ★AI로 만들었든 아니든 레퍼런스가 아니다. 깎아주면 안 된다
    #   실측: 「중국 휩쓴 AI 드라마 / JTBC 뉴스룸」이 AI 신호 덕에 감점을 면하고 통과했다
    pol_ad = 0.5 * bool(brand_hits) + 0.35 * bool(bcast_hits)
    if ai_signal >= 0.5:
        pol_ad = max(0.0, pol_ad - 0.3)
    pol_tut = 0.45 * bool(tut_hits)
    pollution = min(1.0, pol_ad + pol_tut)

    ai_gen = ai_signal >= 0.45
    topic_only = (not ai_gen) and (pollution >= 0.35 or bool(brand_hits))

    # ★사유는 AI 제작 판정과 무관하게 남긴다.
    #   "AI로 만든 뉴스 리포트"는 AI 제작이면서 동시에 레퍼런스로는 쓸모없다.
    reason = None
    if tut_hits:
        reason = f'해설·리뷰·뉴스로 보임: {", ".join(tut_hits[:3])}'
    elif topic_only and brand_hits:
        reason = f'AI 제품 광고로 보임 (AI로 만든 것이 아님): {", ".join(brand_hits[:3])}'
    elif topic_only and bcast_hits:
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
    elif kind == 'ad_like':
        words = ['ad', 'ads', 'commercial', 'brand', 'product', 'campaign', 'launch', 'ugc',
                 '광고', '제품', '브랜드', '캠페인', '출시']
        bonus = 0.0
    else:
        words = ['ai', 'generated', 'cinematic', 'video', 'film', 'animation',
                 '영상', '생성', '제작']
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
    print(f'모드: {"upsert" if CAN_UPSERT else "insert-only (service_role 키 없음)"}'
          + (' · DRY RUN' if DRY_RUN else ''))
    n_kw = sum(len(l['kw']) for p in SEARCH_PLAN.values() for l in p['lanes'].values())
    n_call = sum(len(l['kw']) * len(l['orders']) for p in SEARCH_PLAN.values() for l in p['lanes'].values())
    est = n_call * PAGES_PER_KEYWORD * 100 + COMMENT_TOP_N * len(SEARCH_PLAN) + 50
    print(f'설정: 키워드 {n_kw}개 → 검색 {n_call}회 × {PAGES_PER_KEYWORD}페이지 '
          f'· 댓글 상위 {COMMENT_TOP_N} → 예상 약 {est:,}유닛')
    print(f'기간: 갈래별 (드라마 180일 / 일반·광고성 90일 / 영화제 365일)'
          + (f' ← REFS_MONTHS={SEARCH_MONTHS}로 덮어씀' if SEARCH_MONTHS else ''))
    print(f'API 키: {_KEY_SOURCE}  (fetch_youtube.py는 YOUTUBE_API_KEY_TEST를 쓴다 — 할당량 분리)')

    existing = set() if CAN_UPSERT else get_existing_ids()
    print(f'기존 저장분: {len(existing)}개')

    cand, searches = {}, 0
    for kind, plan in SEARCH_PLAN.items():
        print(f'\n=== {plan["label"]} ===')
        for lane, cfg in plan['lanes'].items():
            # 기간은 갈래마다 다르다 — 드라마 180일 / 일반·광고성 90일 / 영화제 365일
            days = SEARCH_MONTHS * 30 if SEARCH_MONTHS else cfg['days']
            after = (now - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
            for kw in cfg['kw']:
                for order in cfg['orders']:
                    items = search_videos(kw, after, order)
                    searches += 1
                    new = 0
                    for it in items:
                        vid = it.get('id', {}).get('videoId', '')
                        if not vid or vid in cand or vid in existing:
                            continue
                        sn = it.get('snippet', {})
                        cand[vid] = {
                            'video_id': vid, 'kind': kind, 'lane': lane, 'search_keyword': kw,
                            'search_order': order,
                            'title': sn.get('title', ''), 'channel': sn.get('channelTitle', ''),
                            'published_at': sn.get('publishedAt', '')[:10],
                            'thumb': sn.get('thumbnails', {}).get('medium', {}).get('url', ''),
                            'url': f'https://www.youtube.com/watch?v={vid}',
                            'embed_url': f'https://www.youtube.com/embed/{vid}',
                        }
                        new += 1
                    print(f'  [{lane:<9}|{order:<9}|{days:>3}일] "{kw}" → {len(items)}개 (신규 {new})')

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
        # 경과일을 먼저 구한다 — 조회수 하한을 나이에 비례시키기 위함
        try:
            pub0 = datetime.strptime(base['published_at'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
            age0 = max(1, (now - pub0).days)
        except ValueError:
            age0 = 1
        # ★하루 50회 페이스를 기준으로, 신작에는 낮은 문턱을 적용한다.
        #   고정 1,000으로 자르면 order=date가 물어온 신작이 통째로 날아간다(실측 735/1108).
        floor = min(MIN_VIEWS, 50 * age0)
        if d['views'] < floor:
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

    # ─────────────────────────────────────────────────────────────
    # ★채널 단위 AI 신호 — 엔진 미탐지 62% 문제의 해법
    #   같은 채널의 다른 영상에서 엔진이 잡혔다면, 안 밝힌 영상도 AI 제작일 확률이 높다.
    #   ★이미 수집한 데이터 안에서 집계하므로 추가 API 비용이 0이다.
    # ─────────────────────────────────────────────────────────────
    by_ch = {}
    for r in rows:
        by_ch.setdefault(r['channel_id'], []).append(r)
    for items in by_ch.values():
        n = len(items)
        n_eng = sum(1 for x in items if x['engine'])
        n_gen = sum(1 for x in items if x['is_ai_generated_likely'])
        sig = min(1.0, (n_eng * 0.6 + n_gen * 0.4) / n + (0.25 if n_eng else 0))
        for x in items:
            x['channel_ai_signal'] = round(sig, 3)
            x['channel_video_count'] = n

    rescued = 0
    for r in rows:
        # 영상 신호 70% + 채널 신호 30%
        conf = round(min(1.0, r['ai_signal_score'] * 0.7 + r['channel_ai_signal'] * 0.3), 3)
        r['ai_generated_confidence'] = conf
        # 본인은 안 밝혔지만 채널이 AI 제작 채널이면 구제한다.
        # ⚠️ conf 임계값으로 걸면 산술적으로 불가능하다 — ai_signal=0일 때 conf 최대가 0.3이라
        #    0.45를 못 넘어 구제가 0개였다(v3 첫 실행 실측). 채널 신호만으로 판단한다.
        if (not r['is_ai_generated_likely']) and r['channel_ai_signal'] >= 0.5                 and r['pollution_risk_score'] < 0.35:
            r['is_ai_generated_likely'] = True
            r['reject_reason'] = None
            r['rescued_by_channel'] = True
            rescued += 1

        # 작은 채널에서 튄 정도 — 절대 조회수보다 이게 실력에 가깝다
        r['niche_breakout'] = round(norm(r['view_sub_ratio'], 10) * norm(r['views_per_day'], 20000) * 100, 2)

        hay = f"{r['title']} {r['description']}".lower()
        ad_hits = sum(1 for w in AD_WORDS if w in hay)
        r['ad_likeness'] = round(min(1.0, ad_hits / 3), 3)

        # ⚠️ 메타데이터로 실제 판정 가능한 것만 붙인다.
        #   화면을 봐야 아는 것(제품 노출·브랜드 등장·영상 품질)은 태그로 만들지 않는다.
        t = []
        if r['is_series_likely']: t.append('시리즈')
        if r['is_vertical']: t.append('세로')
        if r['engine']: t.append('엔진명시')
        if r['lane'] == 'festival': t.append('영화제')
        if r['is_tutorial_or_review_likely']: t.append('해설·리뷰')
        if any(w in hay for w in MAKING_WORDS): t.append('메이킹')
        if r['ad_likeness'] >= 0.66: t.append('광고성')
        if any('가' <= c <= '힣' for c in r['title']): t.append('한국어')
        r['auto_tags'] = t

    print(f'채널 단위 신호로 구제: {rescued}개 (본인은 안 밝혔지만 채널이 AI 제작 채널)')


    # 댓글 분석 — 갈래별 상위 N개에만 (영상당 1유닛이라 전체는 낭비)
    checked = 0
    for kind in SEARCH_PLAN if COMMENT_TOP_N else []:
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
    print(f'댓글 분석: {checked}개' + ('' if COMMENT_TOP_N else ' (꺼짐 — REFS_COMMENT_TOP=0)'))

    ages = sorted(r['age_days'] for r in rows) or [0]
    print(f"\n경과일: 중앙값 {ages[len(ages)//2]}일 · 30일 이내 {sum(1 for a in ages if a<=30)}개 "
          f"· 90일 이내 {sum(1 for a in ages if a<=90)}개")
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

    if DRY_RUN:
        # ★DRY RUN의 목적은 숫자가 아니라 "목록이 쓸 만한가"를 눈으로 보는 것이다.
        # ★게이트 정렬 — views_per_day만 쓰면 급등한 튜토리얼이 올라온다.
        #   ①AI 제작 확신 ②오염 낮음 을 먼저 통과시킨 뒤 점수로 정렬한다.
        def gate(r):
            return (r['is_ai_generated_likely'],
                    r['pollution_risk_score'] < 0.35,
                    r['quality_score'])
        for k, p in SEARCH_PLAN.items():
            top = sorted([r for r in rows if r['kind'] == k], key=gate, reverse=True)[:15]
            print(f'\n──── {p["label"]} 상위 15 (quality_score 순) ────')
            for i, r in enumerate(top, 1):
                mark = 'AI제작' if r['is_ai_generated_likely'] else ('오염' if r['is_ai_topic_only_likely'] else '보류')
                mm, ss = divmod(r['duration_sec'], 60)
                print(f"{i:>2}. [{r['quality_score']:>5.1f}] {mark:<5} 확신{r['ai_generated_confidence']:.2f} "
                      f"{mm}:{ss:02d} 조회{r['views']:>9,} 구독대비{r['view_sub_ratio']:>6.1f} "
                      f"돌풍{r['niche_breakout']:>5.1f} {r['age_days']:>3}일 "
                      f"{(r['engine'] or '-'):<10} | {r['title'][:40]}")
                if r['auto_tags']:
                    print(f"      태그 {' · '.join(r['auto_tags'])}")
                print(f"      {r['url']}  | 채널 {r['channel'][:22]} | 검색어 \"{r['search_keyword']}\"")
        # ★갈래별 성적 — 넓은 키워드가 실제로 값을 하는지 비교하기 위함
        print('\n──── 갈래별 성적 ────')
        print(f"{'갈래':<12}{'후보':>6}{'AI제작':>8}{'오염':>6}{'순도':>7}   상위30 진입")
        for k, p in SEARCH_PLAN.items():
            kr = sorted([r for r in rows if r['kind'] == k],
                        key=lambda r: r['quality_score'], reverse=True)
            top30 = {r['video_id'] for r in kr[:30]}
            print(f'  [{p["label"]}]')
            for lane in p['lanes']:
                lr = [r for r in kr if r['lane'] == lane]
                if not lr:
                    continue
                g = sum(1 for r in lr if r['is_ai_generated_likely'])
                po = sum(1 for r in lr if r['is_ai_topic_only_likely'])
                intop = sum(1 for r in lr if r['video_id'] in top30)
                print(f"  {lane:<12}{len(lr):>6}{g:>8}{po:>6}{g/max(len(lr),1)*100:>6.0f}%{intop:>10}개")
        polluted = [r for r in rows if r['is_ai_topic_only_likely']]
        if polluted:
            print(f'\n──── 오염 판정 {len(polluted)}개 (사유별) ────')
            for r in polluted[:10]:
                print(f"  {r['title'][:44]} → {r['reject_reason']}")
        # 전체 결과를 파일로 남긴다 — 로그는 상위 15개만 보여 전부 훑을 수 없다
        with open('refs_dryrun.json', 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False)
        print(f'\n[DRY RUN] 저장하지 않음. 전체 {len(rows)}개를 refs_dryrun.json 으로 남김')
    else:
        print(f'\n저장 완료: {save(rows)}/{len(rows)}개')
    print(f'[{datetime.now(KST):%Y-%m-%d %H:%M:%S} KST] 완료')


if __name__ == '__main__':
    main()
