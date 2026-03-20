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

# 여기만 변경됨
SEARCH_CATEGORIES = ['영화', '방송', '17금', '19금', '주번나']

def fetch_posts(session, pages=3):
    posts = []
    seen_ids = set()

    for category in SEARCH_CATEGORIES:
        print(f'\n[{category}] 말머리 검색 시작')
        for page in range(1, pages + 1):
            params = {
                'select': 'spf',
                'subselect': 'sct',
                'm': 'search',
                'b': 'bullpen',
                'search_select2': 'spf',
                'query': category,
                'search_select3': 'sct',
                'subquery': 'ai',
                'p': page
            }
            res = session.get(BOARD_URL, params=params)
            if res.status_code != 200:
                print(f'페이지 {page} 로드 실패: {res.status_code}')
                continue

            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.select_one('table.tbl_type01')
            if not table:
                print(f'페이지 {page}: 테이블 없음')
                continue

            rows = table.select('tbody tr') or table.select('tr')
            print(f'페이지 {page}: {len(rows)}개 행 발견')

            for row in rows:
                try:
                    first_td = row.select_one('td')
                    if first_td and first_td.get_text(strip=True) == '공지':
                        continue

                    title_el = (row.select_one('a[href*="m=view"]') or
                               row.select_one('td.t_left a') or
                               row.select_one('a[href*="bullpen"]'))
                    if not title_el:
                        continue

                    title = title_el.get_text(strip=True)
                    if not title or len(title) < 2:
                        continue

                    href = title_el.get('href', '')
                    url = href if href.startswith('http') else (BASE_URL + href if href.startswith('/') else BASE_URL + '/' + href)

                    post_id = ''
                    if 'id=' in url:
                        post_id = url.split('id=')[1].split('&')[0]
                    if not post_id:
                        import hashlib
                        post_id = hashlib.md5(url.encode()).hexdigest()[:12]

                    # 카테고리 간 중복 제거
                    if post_id in seen_ids:
                        continue
                    seen_ids.add(post_id)

                    # 작성자: data-unick 속성 또는 td 텍스트
                    author = ''
                    author_el = row.select_one('[data-unick]')
                    if author_el:
                        import urllib.parse
                        author = urllib.parse.unquote(author_el.get('data-unick', ''))
                    if not author:
                        td_author = row.select_one('td:nth-child(3) a') or row.select_one('td:nth-child(3)')
                        author = td_author.get_text(strip=True) if td_author else '익명'

                    thumb_el = row.select_one('img:not([src*="btn"]):not([src*="ico"])')
                    thumb = ''
                    if thumb_el:
                        src = thumb_el.get('src', '')
                        if src and not src.startswith('http'):
                            src = BASE_URL + src
                        if src and not any(x in src for x in ['Profile', 'profile', 'logo', 'btn', 'ico', 'ugc/WWW']):
                            thumb = src

                    posts.append({
                        'post_id': post_id,
                        'title': title,
                        'url': url,
                        'author': author,
                        'thumb': thumb,
                        'category': category  # 말머리 카테고리 저장
                    })
                    print(f'  발견: [{category}] {title[:40]}')

                except Exception as e:
                    print(f'행 파싱 오류: {e}')
                    continue

    return posts

def fetch_post_detail(session, post):
    """게시물 상세 페이지에서 본문 전체, 이미지, 영상 URL 추출"""
    try:
        res = session.get(post['url'], timeout=15)
        if res.status_code != 200:
            return post

        soup = BeautifulSoup(res.text, 'html.parser')

        content_el = (soup.select_one('div.view_context div#contentDetail') or
                     soup.select_one('#contentDetail') or
                     soup.select_one('div.view_context .ar_txt') or
                     soup.select_one('div.ar_txt') or
                     soup.select_one('div.view_context'))

        if content_el:
            text = content_el.get_text(strip=True)
            post['summary'] = text
            post['content_html'] = str(content_el)

            images = []
            for img in content_el.select('img'):
                src = img.get('src', '') or img.get('data-src', '')
                if src:
                    if not src.startswith('http'):
                        src = BASE_URL + src
                    if not any(x in src for x in ['btn', 'ico', 'arrow', 'blank', 'loading']):
                        images.append(src)
            post['images'] = images[:10]

            if not post.get('thumb') and images:
                post['thumb'] = images[0]

            video_urls = []

            for v in content_el.select('video'):
                src = v.get('src', '')
                if not src:
                    source = v.select_one('source')
                    src = source.get('src', '') if source else ''
                if src:
                    if not src.startswith('http'):
                        src = BASE_URL + src
                    video_urls.append(src)
                poster = v.get('poster', '')
                if poster:
                    if not poster.startswith('http'):
                        poster = BASE_URL + poster
                    post['thumb'] = poster

            for iframe in content_el.select('iframe'):
                src = iframe.get('src', '')
                if not src:
                    continue
                if 'youtube' in src or 'youtu.be' in src:
                    vid = ''
                    if 'embed/' in src: vid = src.split('embed/')[1].split('?')[0]
                    elif 'v=' in src: vid = src.split('v=')[1].split('&')[0]
                    if vid:
                        embed_url = f'https://www.youtube.com/embed/{vid}'
                        video_urls.append(embed_url)
                else:
                    video_urls.append(f'iframe:{src}')

            for a in content_el.select('a'):
                href = a.get('href', '')
                if 'youtube.com/watch' in href or 'youtu.be/' in href:
                    vid = ''
                    if 'v=' in href: vid = href.split('v=')[1].split('&')[0]
                    elif 'youtu.be/' in href: vid = href.split('youtu.be/')[1].split('?')[0]
                    if vid:
                        embed_url = f'https://www.youtube.com/embed/{vid}'
                        if embed_url not in video_urls:
                            video_urls.append(embed_url)

            seen = set()
            unique_urls = []
            for u in video_urls:
                if u not in seen:
                    seen.add(u)
                    unique_urls.append(u)
            post['video_urls'] = unique_urls[:5]

            if not post.get('thumb'):
                for u in unique_urls:
                    vid = ''
                    if 'embed/' in u: vid = u.split('embed/')[1].split('?')[0]
                    if vid:
                        post['thumb'] = f'https://img.youtube.com/vi/{vid}/mqdefault.jpg'
                        break

            print(f'  상세: 이미지 {len(images)}개, 영상 {len(unique_urls)}개, 요약 {len(text)}자')

        else:
            post['summary'] = ''
            post['content_html'] = ''
            post['images'] = []
            post['video_urls'] = []
            print(f'  상세: 본문 영역 못 찾음')

    except Exception as e:
        print(f'상세 페이지 오류 ({post["url"]}): {e}')
        post.setdefault('summary', '')
        post.setdefault('content_html', '')
        post.setdefault('images', [])
        post.setdefault('video_urls', [])

    return post

def insert_posts(posts):
    headers = {**HEADERS, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    res = requests.post(
        f'{SUPABASE_URL}/rest/v1/mlb_posts',
        headers=headers,
        json=posts
    )
    print(f'저장 응답 status: {res.status_code}')
    if res.status_code not in (200, 201):
        print(f'저장 오류 내용: {res.text[:300]}')
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

    enriched = []
    for p in new_posts[:20]:
        p = fetch_post_detail(session, p)
        video_urls = p.get('video_urls', [])
        images = p.get('images', [])

        if not video_urls:
            print(f'  영상 없음, 스킵: {p["title"][:30]}')
            continue

        enriched.append({
            'post_id': p['post_id'],
            'title': p['title'],
            'summary': p.get('summary', ''),
            'content_html': p.get('content_html', ''),
            'images': images,
            'video_urls': video_urls,
            'thumb': p.get('thumb', ''),
            'url': p['url'],
            'author': p['author'],
            'tag': 'movie' if p.get('category') == '영화' else p.get('category', 'movie'),
            'status': 'approved',
            'created_at': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S+09')
        })

        if len(enriched) >= 10:
            break

    success = insert_posts(enriched)
    if success:
        end_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
        print(f'[{end_kst}] {len(enriched)}개 게시물 저장 완료')
    else:
        print('저장 실패')

if __name__ == '__main__':
    main()