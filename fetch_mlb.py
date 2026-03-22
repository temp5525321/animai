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
    # deleted 포함 전체 post_id 조회 (삭제된 게시물 재수집 방지)
    # Supabase 기본 limit 1000 우회
    res = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_posts?select=post_id&limit=10000',
        headers=HEADERS
    )
    data = res.json()
    return set(item['post_id'] for item in data)

def get_latest_post_date():
    """Supabase에서 가장 최근 게시물의 post_date 조회 (deleted 제외)"""
    res = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_posts?select=post_date&status=neq.deleted&order=post_date.desc&limit=1',
        headers=HEADERS
    )
    data = res.json()
    if data and data[0].get('post_date'):
        date_str = data[0]['post_date'][:10]
        return datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=KST)
    return None

SEARCH_CATEGORIES = ['영화', '방송', '만화', 'IT', '유머', '짤방', '펌글', '아이돌']

TAG_MAP = {
    '영화': 'movie',
    '방송': 'broadcast',
    '만화': 'cartoon',
    'IT': 'it',
    '유머': 'humor',
    '짤방': 'jjal',
    '펌글': 'pmgl',
    '아이돌': 'idol'
}

def fetch_posts(session, cutoff_date):
    """말머리별 목록 페이지 순회로 cutoff_date 이후 게시물 수집"""
    posts = []
    seen_ids = set()
    KEYWORDS = ['ai', 'a.i', 'a,i']

    for category in SEARCH_CATEGORIES:
        print(f'\n[{category}] 목록 순회 시작')
        for page in range(1, 200):  # 최대 200페이지 (cutoff 도달 시 자동 중단)
            params = {
                'm': 'list',
                'b': 'bullpen',
                'select': 'spf',
                'query': category,
                'p': page
            }
            try:
                res = session.get(BOARD_URL, params=params, timeout=15)
                if res.status_code != 200:
                    print(f'  [{category}] 페이지 {page} 응답 오류: {res.status_code}')
                    break
            except Exception as e:
                print(f'  [{category}] 페이지 {page} 요청 오류: {e}')
                break

            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.select_one('table.tbl_type01')
            if not table:
                print(f'  [{category}] 페이지 {page} 테이블 없음, 중단')
                break

            rows = table.select('tbody tr') or table.select('tr')
            if not rows:
                print(f'  [{category}] 페이지 {page} 행 없음, 중단')
                break

            page_has_post = False
            reached_cutoff = False
            _today = datetime.now(KST)
            _today_str = _today.strftime('%Y-%m-%d')
            _cur_year = _today.year

            for row in rows:
                try:
                    first_td = row.select_one('td')
                    if first_td and first_td.get_text(strip=True) == '공지':
                        continue

                    # 날짜 추출 - m=list 날짜 형식 3가지:
                    # HH:MM (오늘글), MM-DD (올해글), YYYY-MM-DD (작년이전글)
                    post_date = None
                    date_str = ''
                    for td in row.select('td'):
                        txt = td.get_text(strip=True)
                        # YYYY-MM-DD
                        if len(txt) == 10 and txt.count('-') == 2:
                            try:
                                post_date = datetime.strptime(txt, '%Y-%m-%d').replace(tzinfo=KST)
                                date_str = txt
                                break
                            except:
                                pass
                        # MM-DD (올해)
                        elif len(txt) == 5 and txt.count('-') == 1:
                            try:
                                pd = datetime.strptime(f'{_cur_year}-{txt}', '%Y-%m-%d').replace(tzinfo=KST)
                                if pd > _today:  # 미래면 작년
                                    pd = pd.replace(year=_cur_year - 1)
                                post_date = pd
                                date_str = pd.strftime('%Y-%m-%d')
                                break
                            except:
                                pass
                        # HH:MM (오늘)
                        elif len(txt) == 5 and txt.count(':') == 1:
                            try:
                                datetime.strptime(txt, '%H:%M')
                                post_date = _today.replace(hour=0, minute=0, second=0, microsecond=0)
                                date_str = _today_str
                                break
                            except:
                                pass
                        # HH:MM:SS (오늘)
                        elif len(txt) == 8 and txt.count(':') == 2:
                            try:
                                datetime.strptime(txt, '%H:%M:%S')
                                post_date = _today.replace(hour=0, minute=0, second=0, microsecond=0)
                                date_str = _today_str
                                break
                            except:
                                pass

                    # cutoff보다 오래된 글이면 이 행 스킵
                    if post_date and post_date < cutoff_date:
                        reached_cutoff = True
                        continue

                    page_has_post = True

                    # 제목 링크
                    title_el = (row.select_one('a[href*="m=view"]') or
                               row.select_one('td.t_left a') or
                               row.select_one('a[href*="bullpen"]'))
                    if not title_el:
                        continue

                    title = title_el.get_text(strip=True)
                    if not title or len(title) < 2:
                        continue

                    # 키워드 필터 (제목에 ai/a.i/a,i 포함 여부)
                    title_lower = title.lower()
                    if not any(kw in title_lower for kw in KEYWORDS):
                        continue

                    href = title_el.get('href', '')
                    url = href if href.startswith('http') else (BASE_URL + href if href.startswith('/') else BASE_URL + '/' + href)

                    post_id = ''
                    if 'id=' in url:
                        post_id = url.split('id=')[1].split('&')[0]
                    if not post_id:
                        import hashlib
                        post_id = hashlib.md5(url.encode()).hexdigest()[:12]

                    if post_id in seen_ids:
                        continue
                    seen_ids.add(post_id)

                    # 작성자
                    author = ''
                    author_el = row.select_one('[data-unick]')
                    if author_el:
                        import urllib.parse
                        author = urllib.parse.unquote(author_el.get('data-unick', ''))
                    if not author:
                        td_author = row.select_one('td:nth-child(3) a') or row.select_one('td:nth-child(3)')
                        author = td_author.get_text(strip=True) if td_author else '익명'

                    # 썸네일
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
                        'category': category,
                        'post_date': date_str
                    })
                    print(f'  발견: [{category}] {title[:40]} ({date_str})')

                except Exception as e:
                    print(f'행 파싱 오류: {e}')
                    continue

            # cutoff 이전 글을 만났으면 이 카테고리 순회 종료 (목록은 최신순)
            if reached_cutoff:
                print(f'  [{category}] 페이지 {page}: cutoff 날짜 도달, 중단')
                break

            # 이 페이지에 게시물이 하나도 없으면 마지막 페이지
            if not page_has_post:
                print(f'  [{category}] 페이지 {page}: 게시물 없음, 중단')
                break

            print(f'  [{category}] 페이지 {page} 완료, 누적 {len(posts)}개')

    return posts

def fetch_post_detail(session, post):
    """게시물 상세 페이지에서 본문 전체, 이미지, 영상 URL 추출"""
    try:
        res = session.get(post['url'], timeout=15)
        if res.status_code != 200:
            return post

        soup = BeautifulSoup(res.text, 'html.parser')

        # 실제 MLB파크 본문 영역: div.view_context > div.ar_txt#contentDetail
        content_el = (soup.select_one('div.view_context div#contentDetail') or
                     soup.select_one('#contentDetail') or
                     soup.select_one('div.view_context .ar_txt') or
                     soup.select_one('div.ar_txt') or
                     soup.select_one('div.view_context'))

        if content_el:
            # 본문 텍스트 요약
            text = content_el.get_text(strip=True)
            post['summary'] = text

            # 본문 HTML 저장
            post['content_html'] = str(content_el)

            # 이미지 URL 수집 (본문 영역)
            images = []
            for img in content_el.select('img'):
                raw_src = img.get('src', '') or img.get('data-src', '')
                src = raw_src
                if src:
                    if src.startswith('//'):
                        src = 'https:' + src
                    elif not src.startswith('http'):
                        src = BASE_URL + src
                    if not any(x in src for x in ['btn', 'ico', 'arrow', 'blank', 'loading', 'profile', 'Profile', 'ugc/WWW']):
                        images.append(src)

            # 본문 외부 이미지도 체크 (view_context 등)
            view_ctx = soup.select_one('div.view_context') or soup.select_one('div.view_wrap')
            if view_ctx:
                for img in view_ctx.select('img'):
                    src = img.get('src', '') or img.get('data-src', '')
                    if src:
                        if src.startswith('//'):
                            src = 'https:' + src
                        elif not src.startswith('http'):
                            src = BASE_URL + src
                        if src not in images and not any(x in src for x in ['btn', 'ico', 'arrow', 'blank', 'loading', 'profile', 'Profile', 'ugc/WWW']):
                            images.append(src)

            post['images'] = images[:10]

            # 썸네일: 첫 번째 이미지
            if not post.get('thumb') and images:
                post['thumb'] = images[0]

            # 영상 URL 수집
            video_urls = []

            # video 태그 - 여러 속성 체크 (본문 영역)
            for v in content_el.select('video'):
                src = (v.get('src', '') or v.get('data-src', '') or
                       v.get('data-video', '') or v.get('data-url', ''))
                if not src:
                    source = v.select_one('source')
                    if source:
                        src = (source.get('src', '') or source.get('data-src', ''))
                if src:
                    if not src.startswith('http'):
                        src = BASE_URL + src
                    src = src.split('#')[0]
                    video_urls.append(src)
                poster = v.get('poster', '')
                if poster:
                    if not poster.startswith('http'):
                        poster = BASE_URL + poster
                    post['thumb'] = poster

            # 본문 영역 밖 video 태그도 체크 (MLB파크 자체 플레이어)
            for v in soup.select('video'):
                if content_el and v in content_el.descendants:
                    continue  # 이미 처리됨
                src = (v.get('src', '') or v.get('data-src', '') or
                       v.get('data-video', '') or v.get('data-url', ''))
                if not src:
                    source = v.select_one('source')
                    if source:
                        src = (source.get('src', '') or source.get('data-src', ''))
                if src:
                    if not src.startswith('http'):
                        src = BASE_URL + src
                    src = src.split('#')[0]
                    if src not in video_urls:
                        video_urls.append(src)
                        print(f'    본문 외부 video 발견: {src[:60]}')
                poster = v.get('poster', '')
                if poster and not post.get('thumb'):
                    if not poster.startswith('http'):
                        poster = BASE_URL + poster
                    post['thumb'] = poster

            # a 태그로 연결된 mp4/영상 파일
            for a in content_el.select('a[href]'):
                href = a.get('href', '')
                if any(href.lower().endswith(ext) for ext in ['.mp4', '.webm', '.mov', '.avi']):
                    if not href.startswith('http'):
                        href = BASE_URL + href
                    if href not in video_urls:
                        video_urls.append(href)

            # embed 태그 (X/Twitter 영상)
            for embed in content_el.select('embed'):
                src = embed.get('src', '')
                if 'twitter.com/i/videos' in src or 'x.com/i/videos' in src:
                    if src.startswith('//'):
                        src = 'https:' + src
                    video_urls.append(f'xvideo:{src}')

            # iframe (YouTube + 외부 영상 embed)
            for iframe in content_el.select('iframe'):
                src = iframe.get('src', '')
                if not src:
                    continue
                if 'youtube' in src or 'youtu.be' in src:
                    # YouTube: embed URL로 통일 (watch URL과 중복 방지)
                    vid = ''
                    if 'embed/' in src: vid = src.split('embed/')[1].split('?')[0]
                    elif 'v=' in src: vid = src.split('v=')[1].split('&')[0]
                    if vid:
                        embed_url = f'https://www.youtube.com/embed/{vid}'
                        video_urls.append(embed_url)
                else:
                    # 외부 영상 iframe (트위터, 기타)
                    video_urls.append(f'iframe:{src}')

            # YouTube watch 링크 (embed로 변환, 중복 방지)
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

            # 중복 제거
            seen = set()
            unique_urls = []
            for u in video_urls:
                if u not in seen:
                    seen.add(u)
                    unique_urls.append(u)
            post['video_urls'] = unique_urls

            # YouTube 영상이 있으면 썸네일 추출 (항상 덮어씌움)
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

    # 기존 게시물 ID 전체 조회 (deleted 포함, 중복/재수집 방지)
    existing = get_existing_post_ids()
    print(f'기존 게시물 수: {len(existing)}개')

    # 날짜 기준 결정
    # - DB에 데이터 있으면: 가장 최신 post_date 이후 신규만
    # - DB 비어있으면: 오늘 기준 30일 전부터
    latest_date = get_latest_post_date()
    if latest_date:
        cutoff_date = latest_date
        print(f'기준 날짜: {cutoff_date.strftime("%Y-%m-%d")} 이후 신규 전부 수집')
    else:
        cutoff_date = datetime.now(KST) - timedelta(days=30)
        print(f'기준 날짜: {cutoff_date.strftime("%Y-%m-%d")} 이후 30일치 전부 수집')

    # 목록 수집
    posts = fetch_posts(session, cutoff_date)
    print(f'\n총 게시물 발견: {len(posts)}개')

    # 기존에 없는 것만 필터링
    new_posts = [p for p in posts if p['post_id'] not in existing]
    print(f'신규 게시물: {len(new_posts)}개')

    if not new_posts:
        print('새로운 게시물이 없습니다.')
        return

    # 상세 크롤링 - 제한 없이 전부
    from collections import defaultdict
    category_posts = defaultdict(list)
    for p in new_posts:
        cat = p.get('category', '영화')
        category_posts[cat].append(p)

    enriched = []
    for category, cat_posts in category_posts.items():
        print(f'\n[{category}] 상세 크롤링 시작 ({len(cat_posts)}개)')
        cat_saved = 0
        for p in cat_posts:
            p = fetch_post_detail(session, p)
            video_urls = p.get('video_urls', [])

            if not video_urls:
                print(f'  영상 없음, 스킵: {p["title"][:30]}')
                continue

            enriched.append({
                'post_id': p['post_id'],
                'title': p['title'],
                'summary': p.get('summary', ''),
                'content_html': p.get('content_html', ''),
                'images': p.get('images', []),
                'video_urls': video_urls,
                'thumb': p.get('thumb', ''),
                'url': p['url'],
                'author': p['author'],
                'tag': TAG_MAP.get(category, category),
                'post_date': p.get('post_date') or None,
                'status': 'approved',
                'created_at': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S+09')
            })
            cat_saved += 1
        print(f'  [{category}] {cat_saved}개 저장 예정')

    if not enriched:
        print('저장할 게시물이 없습니다.')
        return

    success = insert_posts(enriched)
    if success:
        end_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
        print(f'[{end_kst}] {len(enriched)}개 게시물 저장 완료')
    else:
        print('저장 실패')

if __name__ == '__main__':
    main()
