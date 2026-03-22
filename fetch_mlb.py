import os
import sys
import requests
import urllib.parse
import hashlib
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from collections import defaultdict

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

SEARCH_CATEGORIES = ['영화', '방송', '만화', 'IT', '유머', '짤방', '펌글', '아이돌']
KEYWORDS = ['ai', 'a.i', 'a,i']
TAG_MAP = {
    '영화': 'movie', '방송': 'broadcast', '만화': 'cartoon', 'IT': 'it',
    '유머': 'humor', '짤방': 'jjal', '펌글': 'pmgl', '아이돌': 'idol'
}


def login():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://secure.donga.com/mlbpark/login.php',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9'
    })
    try:
        res = session.post(LOGIN_URL, data={
            'idsave_value': '', 'errorChk': '',
            'gourl': 'https://mlbpark.donga.com/mp',
            'bid': MLBPARK_ID, 'bpw': MLBPARK_PW
        }, allow_redirects=True, timeout=15)
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
        f'{SUPABASE_URL}/rest/v1/mlb_posts?select=post_id&limit=10000',
        headers=HEADERS
    )
    data = res.json()
    return set(item['post_id'] for item in data)


def get_latest_post_date():
    res = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_posts?select=post_date&status=neq.deleted&order=post_date.desc&limit=1',
        headers=HEADERS
    )
    data = res.json()
    if data and data[0].get('post_date'):
        return datetime.strptime(data[0]['post_date'][:10], '%Y-%m-%d').replace(tzinfo=KST)
    return None


def parse_date(txt, today, today_str):
    """목록 날짜 텍스트 파싱 (YYYY-MM-DD / MM-DD / HH:MM / HH:MM:SS)"""
    txt = txt.strip()
    if len(txt) == 10 and txt.count('-') == 2:
        try:
            return datetime.strptime(txt, '%Y-%m-%d').replace(tzinfo=KST), txt
        except:
            pass
    if len(txt) == 5 and txt.count('-') == 1:
        try:
            pd = datetime.strptime(f'{today.year}-{txt}', '%Y-%m-%d').replace(tzinfo=KST)
            if pd > today:
                pd = pd.replace(year=today.year - 1)
            return pd, pd.strftime('%Y-%m-%d')
        except:
            pass
    if (len(txt) == 5 and txt.count(':') == 1) or (len(txt) == 8 and txt.count(':') == 2):
        try:
            fmt = '%H:%M' if len(txt) == 5 else '%H:%M:%S'
            datetime.strptime(txt, fmt)
            return today.replace(hour=0, minute=0, second=0, microsecond=0), today_str
        except:
            pass
    return None, ''


def fetch_posts(session, cutoff_date):
    """
    말머리별 목록 1페이지(최신)부터 순회.
    cutoff_date 이전 날짜 발견 즉시 해당 카테고리 중단.
    제목에 ai/a.i/a,i 키워드 있는 글만 수집.
    """
    posts = []
    seen_ids = set()
    today = datetime.now(KST)
    today_str = today.strftime('%Y-%m-%d')

    for category in SEARCH_CATEGORIES:
        print(f'\n[{category}] 크롤링 시작')

        for page in range(1, 500):
            try:
                res = session.get(BOARD_URL, params={
                    'm': 'list', 'b': 'bullpen',
                    'select': 'spf', 'query': category, 'p': page
                }, timeout=15)
                if res.status_code != 200:
                    print(f'  페이지 {page} 오류({res.status_code}), 중단')
                    break
            except Exception as e:
                print(f'  페이지 {page} 요청 오류: {e}')
                break

            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.select_one('table.tbl_type01')
            if not table:
                print(f'  페이지 {page} 테이블 없음, 중단')
                break

            rows = table.select('tbody tr') or table.select('tr')
            if not rows:
                print(f'  페이지 {page} 행 없음, 중단')
                break

            found = 0
            stop = False

            for row in rows:
                try:
                    first_td = row.select_one('td')
                    if first_td and first_td.get_text(strip=True) == '공지':
                        continue

                    # 날짜 파싱
                    post_date, date_str = None, ''
                    for td in row.select('td'):
                        pd, ds = parse_date(td.get_text(strip=True), today, today_str)
                        if pd:
                            post_date, date_str = pd, ds
                            break

                    if not post_date:
                        continue

                    # cutoff 이전이면 즉시 중단
                    if post_date < cutoff_date:
                        print(f'  페이지 {page}: {date_str} < cutoff {cutoff_date.strftime("%Y-%m-%d")}, 중단')
                        stop = True
                        break

                    # 제목
                    title_el = (row.select_one('a[href*="m=view"]') or
                                row.select_one('td.t_left a') or
                                row.select_one('a[href*="bullpen"]'))
                    if not title_el:
                        continue

                    title = title_el.get_text(strip=True)
                    if not title or len(title) < 2:
                        continue

                    # 키워드 필터
                    if not any(kw in title.lower() for kw in KEYWORDS):
                        continue

                    # URL / post_id
                    href = title_el.get('href', '')
                    url = href if href.startswith('http') else (
                        BASE_URL + href if href.startswith('/') else BASE_URL + '/' + href)
                    post_id = url.split('id=')[1].split('&')[0] if 'id=' in url else \
                        hashlib.md5(url.encode()).hexdigest()[:12]

                    if post_id in seen_ids:
                        continue
                    seen_ids.add(post_id)

                    # 작성자
                    author = ''
                    author_el = row.select_one('[data-unick]')
                    if author_el:
                        author = urllib.parse.unquote(author_el.get('data-unick', ''))
                    if not author:
                        td_a = row.select_one('td:nth-child(3) a') or row.select_one('td:nth-child(3)')
                        author = td_a.get_text(strip=True) if td_a else '익명'

                    # 썸네일
                    thumb = ''
                    thumb_el = row.select_one('img:not([src*="btn"]):not([src*="ico"])')
                    if thumb_el:
                        src = thumb_el.get('src', '')
                        if src and not src.startswith('http'):
                            src = BASE_URL + src
                        if src and not any(x in src for x in ['Profile', 'profile', 'logo', 'btn', 'ico', 'ugc/WWW']):
                            thumb = src

                    posts.append({
                        'post_id': post_id, 'title': title, 'url': url,
                        'author': author, 'thumb': thumb,
                        'category': category, 'post_date': date_str
                    })
                    found += 1
                    print(f'  발견: {title[:40]} ({date_str})')

                except Exception as e:
                    print(f'  행 파싱 오류: {e}')
                    continue

            print(f'  페이지 {page}: {found}개 발견, 누적 {len(posts)}개')

            if stop:
                break

    return posts


def fetch_post_detail(session, post):
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
            post['summary'] = content_el.get_text(strip=True)
            post['content_html'] = str(content_el)

            images = []
            for img in content_el.select('img'):
                src = img.get('src', '') or img.get('data-src', '')
                if src:
                    if src.startswith('//'): src = 'https:' + src
                    elif not src.startswith('http'): src = BASE_URL + src
                    if not any(x in src for x in ['btn', 'ico', 'arrow', 'blank', 'loading', 'profile', 'Profile', 'ugc/WWW']):
                        images.append(src)
            post['images'] = images[:10]
            if not post.get('thumb') and images:
                post['thumb'] = images[0]

            video_urls = []

            for v in soup.select('video'):
                src = (v.get('src', '') or v.get('data-src', '') or
                       v.get('data-video', '') or v.get('data-url', ''))
                if not src:
                    s = v.select_one('source')
                    if s: src = s.get('src', '') or s.get('data-src', '')
                if src:
                    if not src.startswith('http'): src = BASE_URL + src
                    src = src.split('#')[0]
                    if src not in video_urls: video_urls.append(src)
                poster = v.get('poster', '')
                if poster:
                    if not poster.startswith('http'): poster = BASE_URL + poster
                    post['thumb'] = poster

            for a in content_el.select('a[href]'):
                href = a.get('href', '')
                if any(href.lower().endswith(ext) for ext in ['.mp4', '.webm', '.mov', '.avi']):
                    if not href.startswith('http'): href = BASE_URL + href
                    if href not in video_urls: video_urls.append(href)

            for embed in content_el.select('embed'):
                src = embed.get('src', '')
                if 'twitter.com/i/videos' in src or 'x.com/i/videos' in src:
                    if src.startswith('//'): src = 'https:' + src
                    video_urls.append(f'xvideo:{src}')

            for iframe in content_el.select('iframe'):
                src = iframe.get('src', '')
                if not src: continue
                if 'youtube' in src or 'youtu.be' in src:
                    vid = ''
                    if 'embed/' in src: vid = src.split('embed/')[1].split('?')[0]
                    elif 'v=' in src: vid = src.split('v=')[1].split('&')[0]
                    if vid:
                        eu = f'https://www.youtube.com/embed/{vid}'
                        if eu not in video_urls: video_urls.append(eu)
                else:
                    video_urls.append(f'iframe:{src}')

            for a in content_el.select('a'):
                href = a.get('href', '')
                if 'youtube.com/watch' in href or 'youtu.be/' in href:
                    vid = ''
                    if 'v=' in href: vid = href.split('v=')[1].split('&')[0]
                    elif 'youtu.be/' in href: vid = href.split('youtu.be/')[1].split('?')[0]
                    if vid:
                        eu = f'https://www.youtube.com/embed/{vid}'
                        if eu not in video_urls: video_urls.append(eu)

            for u in video_urls:
                if 'embed/' in u:
                    vid = u.split('embed/')[1].split('?')[0]
                    if vid:
                        post['thumb'] = f'https://img.youtube.com/vi/{vid}/mqdefault.jpg'
                        break

            post['video_urls'] = video_urls
            print(f'  상세: 이미지 {len(images)}개, 영상 {len(video_urls)}개')

        else:
            post.update({'summary': '', 'content_html': '', 'images': [], 'video_urls': []})
            print(f'  상세: 본문 못 찾음')

    except Exception as e:
        print(f'  상세 오류: {e}')
        post.setdefault('summary', '')
        post.setdefault('content_html', '')
        post.setdefault('images', [])
        post.setdefault('video_urls', [])

    return post


def insert_posts(posts):
    headers = {**HEADERS, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    res = requests.post(f'{SUPABASE_URL}/rest/v1/mlb_posts', headers=headers, json=posts)
    print(f'저장 응답 status: {res.status_code}')
    if res.status_code not in (200, 201):
        print(f'저장 오류: {res.text[:300]}')
    return res.status_code in (200, 201)


def main():
    print(f'[{datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")}] MLB파크 크롤링 시작')

    session = login()
    if not session:
        print('로그인 실패, 비로그인으로 시도')
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0'})

    existing = get_existing_post_ids()
    print(f'기존 게시물: {len(existing)}개')

    latest_date = get_latest_post_date()
    if latest_date:
        cutoff_date = latest_date
        print(f'기준 날짜: {cutoff_date.strftime("%Y-%m-%d")} 이후 신규만')
    else:
        cutoff_date = datetime.now(KST) - timedelta(days=30)
        print(f'첫 실행: {cutoff_date.strftime("%Y-%m-%d")} 이후 30일치')

    posts = fetch_posts(session, cutoff_date)
    print(f'\n총 발견: {len(posts)}개')

    new_posts = [p for p in posts if p['post_id'] not in existing]
    print(f'신규: {len(new_posts)}개')

    if not new_posts:
        print('새로운 게시물 없음')
        return

    enriched = []
    cat_groups = defaultdict(list)
    for p in new_posts:
        cat_groups[p.get('category', '영화')].append(p)

    for category, cat_posts in cat_groups.items():
        print(f'\n[{category}] 상세 크롤링 ({len(cat_posts)}개)')
        saved = 0
        for p in cat_posts:
            p = fetch_post_detail(session, p)
            if not p.get('video_urls'):
                print(f'  영상 없음 스킵: {p["title"][:30]}')
                continue
            enriched.append({
                'post_id': p['post_id'],
                'title': p['title'],
                'summary': p.get('summary', ''),
                'content_html': p.get('content_html', ''),
                'images': p.get('images', []),
                'video_urls': p['video_urls'],
                'thumb': p.get('thumb', ''),
                'url': p['url'],
                'author': p['author'],
                'tag': TAG_MAP.get(category, category),
                'post_date': p.get('post_date') or None,
                'status': 'approved',
                'created_at': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S+09')
            })
            saved += 1
        print(f'  [{category}] {saved}개 저장 예정')

    if not enriched:
        print('저장할 게시물 없음')
        return

    if insert_posts(enriched):
        print(f'[{datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")}] {len(enriched)}개 저장 완료')
    else:
        print('저장 실패')


if __name__ == '__main__':
    main()
