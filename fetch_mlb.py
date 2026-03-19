import os
import sys
import requests
import random
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

MLBPARK_ID = os.environ.get('MLBPARK_ID', '')
MLBPARK_PW = os.environ.get('MLBPARK_PW', '')
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

KST = timezone(timedelta(hours=9))

if not all([MLBPARK_ID, MLBPARK_PW, SUPABASE_URL, SUPABASE_KEY]):
    print('오류: 환경변수가 설정되지 않았습니다.')
    sys.exit(1)

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal'
}

BASE_URL = 'https://mlbpark.donga.com'
LOGIN_URL = 'https://secure.donga.com/mlbpark/trans_exe.php'
BOARD_URL = f'{BASE_URL}/mp/b.php'

def login():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://secure.donga.com/mlbpark/login.php',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9'
    })
    data = {
        'idsave_value': '',
        'errorChk': '',
        'gourl': 'https://mlbpark.donga.com/mp',
        'bid': MLBPARK_ID,
        'bpw': MLBPARK_PW
    }
    try:
        res = session.post(LOGIN_URL, data=data, allow_redirects=True, timeout=15)
        print(f'로그인 응답 status: {res.status_code}')
        print(f'최종 URL: {res.url}')
        print(f'쿠키: {list(session.cookies.keys())}')
        print(f'응답 내용 (앞 500자): {res.text[:500]}')
        return session
    except Exception as e:
        print(f'로그인 예외 발생: {e}')
        return None

def get_existing_post_ids():
    res = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_posts?select=post_id',
        headers=HEADERS
    )
    data = res.json()
    return set(item['post_id'] for item in data)

def fetch_posts(session, pages=3):
    posts = []
    for page in range(1, pages + 1):
        params = {
            'b': 'bullpen',
            'search_select': 'sct',  # 말머리 검색
            'search_input': '영화',   # 말머리: 영화
            'page': page
        }
        res = session.get(BOARD_URL, params=params)
        if res.status_code != 200:
            print(f'페이지 {page} 로드 실패')
            continue

        soup = BeautifulSoup(res.text, 'html.parser')
        rows = soup.select('tr.list-item') or soup.select('table.board-list tbody tr')

        if not rows:
            # 대안 셀렉터 시도
            rows = soup.select('li.list-item') or soup.select('.bbs-list li')

        print(f'페이지 {page}: {len(rows)}개 행 발견')

        for row in rows:
            try:
                # 제목 추출
                title_el = row.select_one('td.title a') or row.select_one('.title a') or row.select_one('a.subject')
                if not title_el:
                    continue

                title = title_el.get_text(strip=True)

                # AI 키워드 필터링
                if 'AI' not in title and 'ai' not in title.lower():
                    continue

                # 링크
                href = title_el.get('href', '')
                if href.startswith('/'):
                    url = BASE_URL + href
                elif not href.startswith('http'):
                    url = BASE_URL + '/' + href
                else:
                    url = href

                # post_id 추출 (URL에서)
                post_id = ''
                if 'num=' in url:
                    post_id = url.split('num=')[1].split('&')[0]
                elif 'seq=' in url:
                    post_id = url.split('seq=')[1].split('&')[0]
                else:
                    post_id = href.replace('/', '_').replace('?', '_')

                if not post_id:
                    continue

                # 작성자
                author_el = row.select_one('td.name') or row.select_one('.author') or row.select_one('td.nick')
                author = author_el.get_text(strip=True) if author_el else '익명'

                # 날짜
                date_el = row.select_one('td.date') or row.select_one('.date') or row.select_one('td.time')
                date_str = date_el.get_text(strip=True) if date_el else ''

                # 썸네일 (있으면)
                thumb_el = row.select_one('img')
                thumb = thumb_el.get('src', '') if thumb_el else ''
                if thumb and not thumb.startswith('http'):
                    thumb = BASE_URL + thumb

                posts.append({
                    'post_id': post_id,
                    'title': title,
                    'url': url,
                    'author': author,
                    'date_str': date_str,
                    'thumb': thumb
                })

            except Exception as e:
                print(f'행 파싱 오류: {e}')
                continue

    return posts

def fetch_post_detail(session, post):
    """게시물 상세 페이지에서 본문 요약과 썸네일 추출"""
    try:
        res = session.get(post['url'], timeout=10)
        if res.status_code != 200:
            return post

        soup = BeautifulSoup(res.text, 'html.parser')

        # 본문 텍스트 요약
        content_el = soup.select_one('.board-content') or soup.select_one('.view-content') or soup.select_one('#board_content')
        if content_el:
            text = content_el.get_text(strip=True)
            post['summary'] = text[:200] + '...' if len(text) > 200 else text
        else:
            post['summary'] = ''

        # 썸네일 (본문 첫 번째 이미지)
        if not post['thumb']:
            img_el = soup.select_one('.board-content img') or soup.select_one('.view-content img')
            if img_el:
                src = img_el.get('src', '')
                if src and not src.startswith('http'):
                    src = BASE_URL + src
                post['thumb'] = src

    except Exception as e:
        print(f'상세 페이지 오류 ({post["url"]}): {e}')

    return post

def insert_posts(posts):
    res = requests.post(
        f'{SUPABASE_URL}/rest/v1/mlb_posts',
        headers=HEADERS,
        json=posts
    )
    return res.status_code in (200, 201)

def main():
    now_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
    print(f'[{now_kst}] MLB파크 크롤링 시작')

    session = login()
    if not session:
        print('세션 생성 실패, 비로그인으로 시도합니다.')
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'})

    existing = get_existing_post_ids()
    print(f'기존 게시물 수: {len(existing)}개')

    posts = fetch_posts(session, pages=3)
    print(f'AI 키워드 게시물 발견: {len(posts)}개')

    new_posts = [p for p in posts if p['post_id'] not in existing]
    print(f'신규 게시물: {len(new_posts)}개')

    if not new_posts:
        print('새로운 게시물이 없습니다.')
        return

    # 상세 페이지에서 본문 요약 + 썸네일 추출
    enriched = []
    for p in new_posts[:10]:  # 최대 10개
        p = fetch_post_detail(session, p)
        enriched.append({
            'post_id': p['post_id'],
            'title': p['title'],
            'summary': p.get('summary', ''),
            'thumb': p.get('thumb', ''),
            'url': p['url'],
            'author': p['author'],
            'tag': 'movie',
            'status': 'approved',
            'created_at': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S+09')
        })

    success = insert_posts(enriched)
    if success:
        end_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
        print(f'[{end_kst}] {len(enriched)}개 게시물 저장 완료')
    else:
        print('저장 실패')

if __name__ == '__main__':
    main()
