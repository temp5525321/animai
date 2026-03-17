import os
import requests
import random
from datetime import datetime

YOUTUBE_API_KEY = os.environ['YOUTUBE_API_KEY']
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_KEY']

KEYWORDS = [
    '애니메이션 실사화 AI',
    'anime live action AI',
    'anime to real life AI',
    'AI anime realistic',
    'Sora anime live action',
    'Runway anime real',
    'Kling anime realistic',
    'AI anime girl real',
    'anime character real AI video',
]

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal'
}

def guess_tag(title):
    t = title.lower()
    if 'sora' in t: return 'sora'
    if 'runway' in t: return 'runway'
    if 'kling' in t: return 'kling'
    if 'cosplay' in t or '코스프레' in t: return 'cosplay'
    return 'anime'

def get_existing_yt_ids():
    res = requests.get(
        f'{SUPABASE_URL}/rest/v1/videos?select=yt_id',
        headers=HEADERS
    )
    data = res.json()
    return set(item['yt_id'] for item in data)

def search_youtube(keyword, max_results=10):
    url = 'https://www.googleapis.com/youtube/v3/search'
    params = {
        'part': 'snippet',
        'q': keyword,
        'type': 'video',
        'maxResults': max_results,
        'order': 'date',
        'key': YOUTUBE_API_KEY
    }
    res = requests.get(url, params=params)
    data = res.json()
    if 'error' in data:
        print(f'YouTube API 오류: {data["error"]["message"]}')
        return []
    return data.get('items', [])

def get_video_stats(video_ids):
    url = 'https://www.googleapis.com/youtube/v3/videos'
    params = {
        'part': 'statistics',
        'id': ','.join(video_ids),
        'key': YOUTUBE_API_KEY
    }
    res = requests.get(url, params=params)
    data = res.json()
    stats = {}
    for item in data.get('items', []):
        stats[item['id']] = item.get('statistics', {})
    return stats

def insert_videos(videos):
    res = requests.post(
        f'{SUPABASE_URL}/rest/v1/videos',
        headers=HEADERS,
        json=videos
    )
    return res.status_code in (200, 201)

def main():
    print(f'[{datetime.now()}] 영상 수집 시작')

    # 기존 yt_id 목록 가져오기
    existing = get_existing_yt_ids()
    print(f'기존 영상 수: {len(existing)}개')

    # 랜덤 키워드 2개 선택
    selected_keywords = random.sample(KEYWORDS, min(2, len(KEYWORDS)))
    print(f'검색 키워드: {selected_keywords}')

    new_videos = []
    seen = set()

    for keyword in selected_keywords:
        items = search_youtube(keyword, max_results=10)
        if not items:
            continue

        video_ids = [item['id']['videoId'] for item in items]
        stats = get_video_stats(video_ids)

        for item in items:
            vid = item['id']['videoId']
            if vid in existing or vid in seen:
                continue
            seen.add(vid)

            sn = item['snippet']
            st = stats.get(vid, {})
            published = sn.get('publishedAt', '')[:10]
            thumb = sn.get('thumbnails', {}).get('medium', {}).get('url', f'https://img.youtube.com/vi/{vid}/mqdefault.jpg')

            new_videos.append({
                'yt_id': vid,
                'title': sn.get('title', ''),
                'channel': sn.get('channelTitle', ''),
                'published_at': published or None,
                'thumb': thumb,
                'url': f'https://www.youtube.com/watch?v={vid}',
                'views': int(st.get('viewCount', 0)),
                'tag': guess_tag(sn.get('title', '')),
                'status': 'approved'
            })

    if not new_videos:
        print('새로운 영상이 없습니다.')
        return

    print(f'새 영상 {len(new_videos)}개 발견, Supabase에 저장 중...')

    # 10개씩 나눠서 insert
    chunk_size = 10
    total_added = 0
    for i in range(0, len(new_videos), chunk_size):
        chunk = new_videos[i:i+chunk_size]
        success = insert_videos(chunk)
        if success:
            total_added += len(chunk)
            print(f'{len(chunk)}개 저장 완료')
        else:
            print(f'저장 실패 (청크 {i//chunk_size + 1})')

    print(f'[완료] 총 {total_added}개 새 영상 추가됨')

if __name__ == '__main__':
    main()
