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
    'meme':      { 'label': '밈파고',          'keywords': ['"뇌절 밈" "AI"', '"meme" "AI"'] },
    # 'virtual':   { 'label': '시크릿AI',         'keywords': ['버추얼 인플루언서 AI', '룩북 AI'] },
    # 'cinema':    { 'label': '방구석놀란',        'keywords': ['시네마틱 AI', '영화 AI'] },
    # 'uncanny':   { 'label': '언캐니밸리',        'keywords': ['리미널 스페이스 AI', 'mystery AI'] },
    # 'cyberpunk': { 'label': '조선사이버펑크',    'keywords': ['조선 사이버펑크 AI', '조선 미래 AI'] },
    # 'healing':   { 'label': '픽셀테라피',        'keywords': ['로파이 AI', '힐링 AI'] },
    # 'music':     { 'label': '그루브생성기',      'keywords': ['jukebox AI', '댄스 AI'] },
    # 'anime':     { 'label': '2D프린터',          'keywords': ['애니메이션 AI', '애니 AI'] },
    # 'prompt':    { 'label': '프롬프트깎는장인',  'keywords': ['프롬프트 엔지니어링 AI', 'prompt AI'] },
    # 'trend':     { 'label': '루어픽',            'keywords': ['바이럴 AI', 'trend AI', 'viral AI'] },
    'lemae':     { 'label': '레매',              'keywords': ['"레깅스" "AI"', '"leggings" "AI"'] },
}

# AI 필터링 키워드 (제목에 하나라도 포함되어야 저장)
AI_FILTER_KEYWORDS = ['ai', 'a.i', 'a,i', '인공지능', 'chatgpt', 'sora', 'kling', 'midjourney', 'runway', '버추얼', 'virtual', 'grok', 'wan2.2']

# 제목 키워드로 페르소나 자동 분류
PERSONA_TITLE_KEYWORDS = {
    'meme':      ['밈', '웃긴', '개웃', 'ㅋㅋ', '뇌절', '병맛', '유머'],
    'virtual':   ['가상인간', '버추얼', '인플루언서', '룩북', '패션', '하이패션'],
    'cinema':    ['영화', '시네마', '단편', '감독', '트레일러', '시네마틱'],
    'uncanny':   ['소름', '공포', '무서운', '기괴', '불쾌', '리미널'],
    'cyberpunk': ['조선', '역사', '한국', '사이버펑크', '대체역사', '한복'],
    'healing':   ['힐링', '아트', '그림', '풍경', '로파이', 'lofi', '파스텔'],
    'music':     ['음악', '노래', '커버', '작곡', '비트', '댄스', '그루브'],
    'anime':     ['애니', '일러스트', '만화', '2D', '캐릭터', '오타쿠'],
    'prompt':    ['프롬프트', '사용법', '튜토리얼', '미드저니', '달리', '생성'],
    'trend':     ['트렌드', '최신', '2025', '신기한', '바이럴', '꿀잼'],
    'lemae':     ['레깅스', '홈트', '요가', '필라테스', '스트레칭', '운동'],
}

def classify_persona(title):
    """제목 키워드로 페르소나 자동 분류"""
    title_lower = title.lower()
    for persona, keywords in PERSONA_TITLE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in title_lower:
                return persona
    return 'trend'  # 분류 안 되면 루어픽으로

def calc_viral_score(views, likes, comments):
    """바이럴 점수 계산 (0~100)"""
    if not views or views == 0:
        return 0
    like_ratio = (likes / views) * 100      # 좋아요 비율
    comment_ratio = (comments / views) * 100  # 댓글 비율
    score = (like_ratio * 60) + (comment_ratio * 40)
    return round(min(score * 10, 100), 2)  # 100점 만점으로 정규화

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
    """YouTube API로 영상 검색 (최근 3개월, 한국어)"""
    # 3개월 전 날짜
    three_months_ago = (datetime.now(KST) - timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%SZ')

    params = {
        'part': 'snippet',
        'q': keyword,
        'type': 'video',
        'maxResults': max_results,
        'order': 'viewCount',
        'relevanceLanguage': 'ko',
        'regionCode': 'KR',
        'publishedAfter': three_months_ago,
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
        'part': 'statistics,snippet',
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
        stats[item['id']] = {
            'views': int(s.get('viewCount', 0)),
            'likes': int(s.get('likeCount', 0)),
            'comments': int(s.get('commentCount', 0)),
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

                # URL 제거 후 AI 필터링 (URL 속 .ai 도메인 오탐지 방지)
                description_no_url = re.sub(r'https?://\S+|www\.\S+', '', description)
                title_lower = title.lower()
                desc_lower = description_no_url.lower()
                if not any(kw in title_lower or kw in desc_lower for kw in AI_FILTER_KEYWORDS):
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
        batch = video_ids[i:i+50]
        batch_stats = get_video_stats(batch)
        stats.update(batch_stats)

    # 바이럴 점수 계산 + 필터링 (조회수 10만 이상)
    enriched = []
    for vid, data in all_videos.items():
        s = stats.get(vid, {})
        views = s.get('views', 0)
        likes = s.get('likes', 0)
        comments = s.get('comments', 0)

        if views < 100000:  # 10만 이하 스킵
            # 레매는 AI 레깅스 영상이 적으므로 1만 이상으로 낮춤
            if data['persona'] == 'lemae' and views < 10000:
                continue
            elif data['persona'] != 'lemae':
                continue

        viral_score = calc_viral_score(views, likes, comments)

        # 페르소나 재분류 하지 않고 검색한 페르소나 그대로 유지
        auto_persona = data['persona']

        enriched.append({
            'video_id': vid,
            'title': data['title'],
            'channel': data['channel'],
            'published_at': data['published_at'],
            'thumb': data['thumb'],
            'url': data['url'],
            'embed_url': data['embed_url'],
            'persona': auto_persona,
            'keyword': data['keyword'],
            'views': views,
            'likes': likes,
            'comments': comments,
            'viral_score': viral_score,
            'status': 'approved',
            'created_at': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S+09')
        })

    # 바이럴 점수 높은 순 정렬
    enriched.sort(key=lambda x: x['viral_score'], reverse=True)
    print(f'\n조회수 10만 이상 영상: {len(enriched)}개')

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
