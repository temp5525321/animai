import os
import sys
import re
import requests
from datetime import datetime, timezone, timedelta

YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY_TEST') or os.environ.get('YOUTUBE_API_KEY', '')
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

KST = timezone(timedelta(hours=9))

if not all([YOUTUBE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print('오류: 환경변수가 설정되지 않았습니다.')
    sys.exit(1)

SUPABASE_HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal'
}

YT_SEARCH_URL = 'https://www.googleapis.com/youtube/v3/search'
YT_VIDEOS_URL = 'https://www.googleapis.com/youtube/v3/videos'

# 페르소나별 키워드 정의
PERSONAS = {
    'meme': {
        'label': '밈파고',
        'keywords': ['"뇌절 밈" "AI"', '"meme" "AI"'],
        'min_views': 100000,
        'ai_filter': True,   # AI 키워드 필터링 적용
    },
    # 'virtual':   { 'label': '시크릿AI',         'keywords': ['버추얼 인플루언서 AI', '룩북 AI'] },
    # 'cinema':    { 'label': '방구석놀란',        'keywords': ['시네마틱 AI', '영화 AI'] },
    # 'uncanny':   { 'label': '언캐니밸리',        'keywords': ['리미널 스페이스 AI', 'mystery AI'] },
    # 'cyberpunk': { 'label': '조선사이버펑크',    'keywords': ['조선 사이버펑크 AI', '조선 미래 AI'] },
    # 'healing':   { 'label': '픽셀테라피',        'keywords': ['로파이 AI', '힐링 AI'] },
    # 'music':     { 'label': '그루브생성기',      'keywords': ['jukebox AI', '댄스 AI'] },
    # 'anime':     { 'label': '2D프린터',          'keywords': ['애니메이션 AI', '애니 AI'] },
    # 'prompt':    { 'label': '프롬프트깎는장인',  'keywords': ['프롬프트 엔지니어링 AI', 'prompt AI'] },
    # 'trend':     { 'label': '루어픽',            'keywords': ['바이럴 AI', 'trend AI', 'viral AI'] },
    'asmr': {
        'label': 'AI ASMR',
        'keywords': ['AI ASMR', '인공지능 ASMR', 'AI asmr roleplay'],
        'min_views': 10000,
        'ai_filter': False,  # 키워드 자체가 AI 포함 → 필터 불필요
    },
    'military': {
        'label': 'AI 군대',
        'keywords': ['AI 군대', 'AI 밀리터리', '인공지능 전쟁', 'AI 군사'],
        'min_views': 10000,
        'ai_filter': False,
    },
    'lopan': {
        'label': 'AI 로판',
        'keywords': ['AI 로판', 'AI 로맨스판타지', '인공지능 로판', 'AI 웹툰'],
        'min_views': 10000,
        'ai_filter': False,
    },
}

# AI 필터링 키워드 (ai_filter: True인 페르소나에만 적용)
AI_FILTER_KEYWORDS = ['ai', 'a.i', 'a,i', '인공지능', 'chatgpt', 'sora', 'kling', 'midjourney', 'runway', '버추얼', 'virtual', 'grok', 'wan2.2']

def has_ai_keyword(text):
    for kw in AI_FILTER_KEYWORDS:
        if re.search(r'(?<![a-zA-Z])' + re.escape(kw) + r'(?![a-zA-Z])', text, re.IGNORECASE):
            return True
    return False

def calc_viral_score(views, likes, comments):
    """바이럴 점수 계산 (0~100) - 참여율 × 조회수 규모 보정"""
    import math
    if not views or views == 0:
        return 0
    like_ratio = likes / views        # 좋아요 비율
    comment_ratio = comments / views  # 댓글 비율
    engagement = (like_ratio * 0.6) + (comment_ratio * 0.4)  # 참여율
    # 조회수 규모 보정 (로그 스케일: 10만=1.0, 100만=1.25, 1000만=1.5)
    view_boost = math.log10(max(views, 1)) / math.log10(100000)
    score = engagement * view_boost * 1000
    return round(min(score, 100), 2)

def get_existing_video_ids():
    """이미 저장된 영상 ID 조회"""
    res = requests.get(
        f'{SUPABASE_URL}/rest/v1/youtube_posts?select=video_id&limit=10000',
        headers=SUPABASE_HEADERS
    )
    if res.status_code != 200:
        return set()
    return set(item['video_id'] for item in res.json())

def search_videos(keyword, max_results=50):
    """YouTube API로 영상 검색 (최근 6개월, 조회수 순)"""
    six_months_ago = (datetime.now(KST) - timedelta(days=180)).strftime('%Y-%m-%dT%H:%M:%SZ')

    params = {
        'part': 'snippet',
        'q': keyword,
        'type': 'video',
        'maxResults': max_results,
        'order': 'viewCount',
        'relevanceLanguage': 'ko',
        'regionCode': 'KR',
        'publishedAfter': six_months_ago,
        'key': YOUTUBE_API_KEY
    }
    res = requests.get(YT_SEARCH_URL, params=params)
    if res.status_code != 200:
        print(f'검색 오류: {res.text[:200]}')
        return []
    data = res.json()
    return data.get('items', [])

def get_video_stats(video_ids):
    """영상 상세 통계 조회"""
    if not video_ids:
        return {}
    params = {
        'part': 'statistics,snippet,status',
        'id': ','.join(video_ids),
        'key': YOUTUBE_API_KEY
    }
    res = requests.get(YT_VIDEOS_URL, params=params)
    if res.status_code != 200:
        print(f'통계 조회 오류: {res.text[:200]}')
        return {}
    stats = {}
    for item in res.json().get('items', []):
        s = item.get('statistics', {})
        snippet = item.get('snippet', {})
        status = item.get('status', {})
        if not status.get('embeddable', True):
            print(f'  임베드 불가 스킵: {snippet.get("title", "")[:40]}')
            continue
        stats[item['id']] = {
            'views': int(s.get('viewCount', 0)),
            'likes': int(s.get('likeCount', 0)),
            'comments': int(s.get('commentCount', 0)),
            'description': snippet.get('description', ''),
        }
    return stats

def insert_videos(videos):
    """Supabase에 영상 저장"""
    headers = {**SUPABASE_HEADERS, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    res = requests.post(
        f'{SUPABASE_URL}/rest/v1/youtube_posts',
        headers=headers,
        json=videos
    )
    print(f'저장 응답 status: {res.status_code}')
    if res.status_code not in (200, 201):
        print(f'저장 오류: {res.text[:300]}')
    return res.status_code in (200, 201)

def main():
    now_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
    print(f'[{now_kst}] 유튜브 크롤링 시작')

    existing_ids = get_existing_video_ids()
    print(f'기존 영상 수: {len(existing_ids)}개')

    all_videos = {}  # video_id: 데이터

    for persona_key, persona in PERSONAS.items():
        print(f'\n[{persona["label"]}] 크롤링 시작')
        for keyword in persona['keywords']:
            print(f'  키워드: "{keyword}"')
            items = search_videos(keyword, max_results=50)
            print(f'  검색 결과: {len(items)}개')

            for item in items:
                vid = item['id'].get('videoId', '')
                if not vid or vid in existing_ids or vid in all_videos:
                    continue
                snippet = item.get('snippet', {})
                title = snippet.get('title', '')
                description = snippet.get('description', '')

                # AI 필터링 (페르소나별 설정에 따라)
                if persona.get('ai_filter', False):
                    description_no_url = re.sub(r'https?://\S+|www\.\S+', '', description)
                    if not has_ai_keyword(title.lower()) and not has_ai_keyword(description_no_url.lower()):
                        continue

                all_videos[vid] = {
                    'video_id': vid,
                    'title': title,
                    'channel': snippet.get('channelTitle', ''),
                    'published_at': snippet.get('publishedAt', '')[:10],
                    'thumb': snippet.get('thumbnails', {}).get('medium', {}).get('url', ''),
                    'url': f'https://www.youtube.com/watch?v={vid}',
                    'embed_url': f'https://www.youtube.com/embed/{vid}',
                    'persona': persona_key,
                    'keyword': keyword,
                }

    print(f'\n총 신규 영상: {len(all_videos)}개 → 통계 조회 중...')

    # 통계 조회 (50개씩 배치)
    video_ids = list(all_videos.keys())
    stats = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        batch_stats = get_video_stats(batch)
        stats.update(batch_stats)

    # 바이럴 점수 계산 + 페르소나별 조회수 필터링
    enriched = []
    for vid, data in all_videos.items():
        s = stats.get(vid, {})
        views = s.get('views', 0)
        likes = s.get('likes', 0)
        comments = s.get('comments', 0)
        description = s.get('description', '')

        persona_key = data['persona']
        min_views = PERSONAS[persona_key]['min_views']

        if views < min_views:
            continue

        viral_score = calc_viral_score(views, likes, comments)

        enriched.append({
            'video_id': vid,
            'title': data['title'],
            'channel': data['channel'],
            'published_at': data['published_at'],
            'thumb': data['thumb'],
            'url': data['url'],
            'embed_url': data['embed_url'],
            'persona': persona_key,
            'keyword': data['keyword'],
            'views': views,
            'likes': likes,
            'comments': comments,
            'viral_score': viral_score,
            'description': description,
            'status': 'approved',
            'created_at': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S+09')
        })

    # 바이럴 점수 높은 순 정렬
    enriched.sort(key=lambda x: x['viral_score'], reverse=True)
    print(f'\n조회수 기준 통과 영상: {len(enriched)}개')

    if not enriched:
        print('저장할 영상이 없습니다.')
        return

    # 페르소나별 상위 10개만 저장
    from collections import defaultdict
    persona_count = defaultdict(int)
    final = []
    for v in enriched:
        p = v['persona']
        if persona_count[p] < 10:
            final.append(v)
            persona_count[p] += 1

    print(f'최종 저장 대상: {len(final)}개')
    for p, cnt in persona_count.items():
        print(f'  {PERSONAS.get(p, {}).get("label", p)}: {cnt}개')

    success = insert_videos(final)
    if success:
        end_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
        print(f'[{end_kst}] {len(final)}개 영상 저장 완료')
    else:
        print('저장 실패')

if __name__ == '__main__':
    main()
