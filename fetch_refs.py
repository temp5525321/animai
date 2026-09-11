"""
AI 드라마 / AI 광고 레퍼런스 수집기

목적이 기존 fetch_youtube.py와 다릅니다.
  fetch_youtube.py  주제별 큐레이션 (사이트에 보여주는 용도)
  fetch_refs.py     ★제작 레퍼런스 수집 (스토리·기획·연출 분석 용도)

설계 원칙
  1) 합산 점수를 만들지 않는다.
     조회수 / 참여율 / 속도 / 길이를 원시값 그대로 저장하고, 고르는 축은 사용자가 정한다.
     하나로 합치면 왜 위에 있는지 알 수 없게 되고, 축을 바꿀 때마다 재크롤링해야 한다.
  2) 엔진 이름을 검색어에 박지 않는다.
     엔진은 몇 달마다 갈린다. 박아두면 리스트가 썩고, 모르는 엔진은 영영 못 찾는다.
     → 주제어로 넓게 찾고, 찾은 영상의 설명에서 엔진명을 역으로 추출한다.
  3) 국내/해외 전체.
     regionCode·relevanceLanguage를 쓰지 않는다. 좋은 레퍼런스는 해외가 많다.
"""
import os
import re
import sys
import requests
from datetime import datetime, timezone, timedelta

YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY_TEST') or os.environ.get('YOUTUBE_API_KEY', '')
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
# service_role 키가 있으면 upsert(조회수 갱신)까지 가능, 없으면 anon으로 insert-only 폴백
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
SUPABASE_KEY = SUPABASE_SERVICE_KEY or os.environ.get('SUPABASE_KEY', '')
CAN_UPSERT = bool(SUPABASE_SERVICE_KEY)

KST = timezone(timedelta(hours=9))

if not all([YOUTUBE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print('오류: 환경변수가 설정되지 않았습니다. (YOUTUBE_API_KEY / SUPABASE_URL / SUPABASE_KEY)')
    sys.exit(1)

SUPABASE_HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal',
}

YT_SEARCH_URL = 'https://www.googleapis.com/youtube/v3/search'
YT_VIDEOS_URL = 'https://www.googleapis.com/youtube/v3/videos'

SEARCH_MONTHS = 12       # 검색 대상 기간(개월). AI는 기술이 빨리 갈려 너무 길면 구식이 섞인다
MIN_VIEWS = 10000        # 하한. order=viewCount라 대개 걸리지 않지만 잡음 제거용

# ─────────────────────────────────────────────────────────────
# 검색어 — 세 갈래
#   topic     완성본을 넓게 건진다
#   festival  ★큐레이션이 이미 끝난 물건. 품질이 보장된다
#   making    ★기획·연출 분석이 목적이면 완성본보다 값지다 (왜 그렇게 했는지가 설명된다)
# ─────────────────────────────────────────────────────────────
SEARCH_PLAN = {
    'drama': {
        'label': 'AI 드라마',
        'lanes': {
            'topic': [
                'AI short film', 'AI 단편영화', 'AI generated film',
                'AI drama series', 'AI 드라마', 'AI cinematic short',
            ],
            'festival': [
                'AI Film Festival winner', 'AI film festival official selection', 'AI 영화제',
            ],
            'making': [
                'AI short film breakdown', 'AI film behind the scenes', 'AI 영상 제작 과정',
            ],
        },
    },
    'ad': {
        'label': 'AI 광고',
        'lanes': {
            'topic': [
                'AI commercial', 'AI 광고', 'AI generated commercial',
                'AI advertisement', 'AI brand film',
            ],
            'festival': [
                'AI commercial award', 'AI ad showcase', 'best AI commercial',
            ],
            'making': [
                'AI commercial breakdown', 'AI ad behind the scenes', 'AI 광고 제작 과정',
            ],
        },
    },
}

# 알려진 엔진 — 씨앗 목록일 뿐이다. 여기 없는 엔진은 아래 discover_engine()이 잡는다.
KNOWN_ENGINES = [
    ('Seedance', ['seedance', '시댄스', '即梦']),
    ('Veo', ['veo 3', 'veo3', 'google veo', ' veo ']),
    ('Sora', ['sora']),
    ('Kling', ['kling', '클링', '可灵']),
    ('Runway', ['runway', 'gen-3', 'gen-4', 'gen3', 'gen4']),
    ('Midjourney', ['midjourney', 'mj v']),
    ('Grok Imagine', ['grok imagine', 'grok']),
    ('Higgsfield', ['higgsfield']),
    ('Hailuo', ['hailuo', 'minimax', '해일루오']),
    ('Vidu', ['vidu']),
    ('Pika', ['pika labs', 'pika']),
    ('Luma', ['luma dream', 'dream machine', 'luma ai']),
    ('Wan', ['wan 2.', 'wan2.']),
    ('Nano Banana', ['nano banana']),
]

# "made with ___" 류 — 내가 모르는 엔진을 잡기 위한 장치
DISCOVER_PAT = re.compile(
    r'(?:made\s+(?:with|in|using)|created\s+(?:with|in|using)|generated\s+(?:with|in|by)'
    r'|powered\s+by|animated\s+with|rendered\s+(?:with|in)|사용\s*툴|제작\s*툴|으로\s*제작)'
    r'[:\s]*([A-Za-z0-9][A-Za-z0-9.\-\s]{1,24})',
    re.IGNORECASE,
)

ISO_DUR = re.compile(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')
EMBED_WH = re.compile(r'width="(\d+)".*?height="(\d+)"', re.DOTALL)


def parse_duration(iso):
    """ISO8601 (PT1H2M3S) → 초"""
    if not iso:
        return 0
    m = ISO_DUR.match(iso)
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def detect_engine(text):
    """알려진 엔진과 매칭. 없으면 None."""
    low = f' {text.lower()} '
    for name, aliases in KNOWN_ENGINES:
        for a in aliases:
            if a in low:
                return name
    return None


def discover_engine(text):
    """목록에 없는 엔진을 설명란 문구에서 통째로 캡처한다."""
    m = DISCOVER_PAT.search(text or '')
    if not m:
        return None
    raw = m.group(1).strip(' .-\n\t')
    # 너무 일반적인 단어가 잡히면 버린다
    if len(raw) < 2 or raw.lower() in ('ai', 'the', 'a', 'my', 'this', 'it'):
        return None
    return raw[:60]


def search_videos(keyword, published_after, max_results=50):
    """조회수 순 검색. ★regionCode / relevanceLanguage를 쓰지 않는다 = 국내·해외 전체."""
    params = {
        'part': 'snippet',
        'q': keyword,
        'type': 'video',
        'maxResults': max_results,
        'order': 'viewCount',
        'publishedAfter': published_after,
        'key': YOUTUBE_API_KEY,
    }
    res = requests.get(YT_SEARCH_URL, params=params, timeout=30)
    if res.status_code != 200:
        print(f'    검색 오류({res.status_code}): {res.text[:160]}')
        return []
    return res.json().get('items', [])


def get_video_details(video_ids):
    """상세 조회. part를 늘려도 비용은 1유닛 그대로라 필요한 걸 다 가져온다."""
    if not video_ids:
        return {}
    params = {
        'part': 'statistics,snippet,status,contentDetails,player',
        'id': ','.join(video_ids),
        'maxHeight': 720,          # 화면비를 알아내기 위해 지정 (세로 영상 판별용)
        'key': YOUTUBE_API_KEY,
    }
    res = requests.get(YT_VIDEOS_URL, params=params, timeout=30)
    if res.status_code != 200:
        print(f'    상세 조회 오류({res.status_code}): {res.text[:160]}')
        return {}

    out = {}
    for item in res.json().get('items', []):
        st = item.get('statistics', {})
        sn = item.get('snippet', {})
        cd = item.get('contentDetails', {})
        pl = item.get('player', {})

        dur = parse_duration(cd.get('duration', ''))

        # 화면비 → 세로면 쇼츠 계열
        vertical = False
        wh = EMBED_WH.search(pl.get('embedHtml', '') or '')
        if wh:
            w, h = int(wh.group(1)), int(wh.group(2))
            vertical = h > w

        out[item['id']] = {
            'views': int(st.get('viewCount', 0) or 0),
            'likes': int(st.get('likeCount', 0) or 0),
            'comments': int(st.get('commentCount', 0) or 0),
            'description': sn.get('description', ''),
            'tags': sn.get('tags', []) or [],
            'channel_id': sn.get('channelId', ''),
            'duration_sec': dur,
            'is_vertical': vertical,
            'embeddable': item.get('status', {}).get('embeddable', True),
        }
    return out


def is_shorts(title, description, tags, duration_sec, vertical):
    """쇼츠 판정.

    ★길이만으로 자르면 안 된다 — AI 광고는 15~60초라 쇼츠와 길이가 겹친다.
      구분되는 건 길이가 아니라 화면비다.
    """
    if '#shorts' in (title or '').lower() or '#shorts' in (description or '').lower():
        return True
    if any('short' == (t or '').lower() or 'shorts' == (t or '').lower() for t in tags):
        return True
    return bool(duration_sec and duration_sec <= 60 and vertical)


def keep_video(kind, duration_sec, vertical, shorts):
    """카테고리별 채택 기준.

    drama  60초 초과만  → 쇼츠가 자연히 빠진다
    ad     길이 제한 없음. 세로(쇼츠)만 제외 → 잘 만든 15초 가로 광고를 놓치지 않는다
    """
    if shorts:
        return False
    if kind == 'drama':
        return duration_sec > 60
    return True


def get_existing_ids():
    """이미 저장된 video_id (페이지네이션으로 전부)"""
    ids, offset, page = set(), 0, 1000
    while True:
        headers = {**SUPABASE_HEADERS, 'Range': f'{offset}-{offset + page - 1}'}
        res = requests.get(
            f'{SUPABASE_URL}/rest/v1/youtube_refs?select=video_id',
            headers=headers, timeout=30,
        )
        if res.status_code not in (200, 206):
            print(f'기존 목록 조회 실패({res.status_code}): {res.text[:160]}')
            break
        rows = res.json()
        if not rows:
            break
        ids.update(r['video_id'] for r in rows)
        if len(rows) < page:
            break
        offset += page
    return ids


def save(rows):
    """service_role 키가 있으면 upsert(조회수 갱신), 없으면 insert-only."""
    if not rows:
        return 0
    headers = dict(SUPABASE_HEADERS)
    url = f'{SUPABASE_URL}/rest/v1/youtube_refs'
    if CAN_UPSERT:
        headers['Prefer'] = 'return=minimal,resolution=merge-duplicates'
        url += '?on_conflict=video_id'

    res = requests.post(url, headers=headers, json=rows, timeout=60)
    if res.status_code in (200, 201, 204):
        print(f'저장 완료: {len(rows)}개 ({"upsert" if CAN_UPSERT else "insert"})')
        return len(rows)

    print(f'배치 저장 실패({res.status_code}) → 개별 전환: {res.text[:160]}')
    ok = 0
    for r in rows:
        rr = requests.post(url, headers=headers, json=[r], timeout=30)
        if rr.status_code in (200, 201, 204):
            ok += 1
    print(f'개별 저장: {ok}/{len(rows)}개')
    return ok


def main():
    now = datetime.now(KST)
    print(f'[{now:%Y-%m-%d %H:%M:%S} KST] AI 드라마/광고 레퍼런스 수집 시작')
    print(f'모드: {"upsert (조회수 갱신 O)" if CAN_UPSERT else "insert-only (조회수 갱신 X — service_role 키 없음)"}')

    published_after = (now - timedelta(days=SEARCH_MONTHS * 30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    existing = set() if CAN_UPSERT else get_existing_ids()
    print(f'기존 저장분: {len(existing)}개' if not CAN_UPSERT else '기존 저장분: (upsert 모드라 조회 생략)')

    candidates = {}   # video_id -> 수집 맥락
    searches = 0

    for kind, plan in SEARCH_PLAN.items():
        print(f'\n=== {plan["label"]} ===')
        for lane, keywords in plan['lanes'].items():
            for kw in keywords:
                items = search_videos(kw, published_after)
                searches += 1
                print(f'  [{lane}] "{kw}" → {len(items)}개')
                for it in items:
                    vid = it.get('id', {}).get('videoId', '')
                    if not vid or vid in candidates or vid in existing:
                        continue
                    sn = it.get('snippet', {})
                    candidates[vid] = {
                        'video_id': vid,
                        'kind': kind,
                        'lane': lane,
                        'keyword': kw,
                        'title': sn.get('title', ''),
                        'channel': sn.get('channelTitle', ''),
                        'published_at': sn.get('publishedAt', '')[:10],
                        'thumb': sn.get('thumbnails', {}).get('medium', {}).get('url', ''),
                        'url': f'https://www.youtube.com/watch?v={vid}',
                        'embed_url': f'https://www.youtube.com/embed/{vid}',
                    }

    print(f'\n검색 {searches}회 (약 {searches * 100 + max(1, len(candidates) // 50)}유닛) / 후보 {len(candidates)}개')
    if not candidates:
        print('신규 후보 없음. 종료.')
        return

    # 상세 조회 (50개씩)
    ids = list(candidates)
    details = {}
    for i in range(0, len(ids), 50):
        details.update(get_video_details(ids[i:i + 50]))

    rows, dropped_short, dropped_views = [], 0, 0
    for vid, base in candidates.items():
        d = details.get(vid)
        if not d:
            continue

        shorts = is_shorts(base['title'], d['description'], d['tags'], d['duration_sec'], d['is_vertical'])
        if not keep_video(base['kind'], d['duration_sec'], d['is_vertical'], shorts):
            dropped_short += 1
            continue
        if d['views'] < MIN_VIEWS:
            dropped_views += 1
            continue

        # 경과일 / 파생 지표 (합산하지 않고 각각 저장한다)
        try:
            pub = datetime.strptime(base['published_at'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
            age_days = max(1, (now - pub).days)
        except ValueError:
            age_days = 1

        views = d['views']
        engagement = round((d['likes'] * 0.6 + d['comments'] * 0.4) / views, 6) if views else 0
        velocity = round(views / age_days, 2)

        haystack = f"{base['title']} {d['description']} {' '.join(d['tags'])}"
        rows.append({
            **base,
            'channel_id': d['channel_id'],
            'duration_sec': d['duration_sec'],
            'is_vertical': d['is_vertical'],
            'is_short': shorts,
            'views': views,
            'likes': d['likes'],
            'comments': d['comments'],
            'age_days': age_days,
            'engagement': engagement,
            'velocity': velocity,
            'engine': detect_engine(haystack),
            'engine_raw': discover_engine(d['description']),
            'embeddable': d['embeddable'],
            'description': d['description'][:4000],
            'tags': d['tags'][:30],
        })

    print(f'쇼츠/길이 기준 제외: {dropped_short}개 · 조회수 미달 제외: {dropped_views}개')
    print(f'저장 대상: {len(rows)}개')

    by_kind = {}
    for r in rows:
        by_kind[r['kind']] = by_kind.get(r['kind'], 0) + 1
    for k, v in by_kind.items():
        print(f'  {SEARCH_PLAN[k]["label"]}: {v}개')

    # 모르는 엔진이 발견되면 눈에 띄게 찍는다 (검색어 유지보수 없이 판을 따라가기 위함)
    unknown = sorted({r['engine_raw'] for r in rows if r['engine_raw'] and not r['engine']})
    if unknown:
        print(f'\n★ 목록에 없는 엔진 후보: {", ".join(unknown[:20])}')

    save(rows)
    print(f'\n[{datetime.now(KST):%Y-%m-%d %H:%M:%S} KST] 완료')


if __name__ == '__main__':
    main()
