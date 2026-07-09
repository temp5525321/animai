import os
import sys
import time
import random
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

KST = timezone(timedelta(hours=9))

if not all([SUPABASE_URL, SUPABASE_KEY]):
    print('오류: 환경변수가 설정되지 않았습니다.')
    sys.exit(1)

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal'
}

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

BASE = 'https://arca.live'
BOARD = 'https://arca.live/b/aireal'
CATEGORY = 'Ai영상'   # 수집 대상 카테고리 (성인 필터 없음 = 전부 수집)

# 아카 영상은 CDN(namu.la) 서명 URL이 1시간 만료 → mp4/썸네일 URL은 저장하지 않는다.
# 저장은 안 죽는 것만: 글번호(post_id), 메타데이터, 아카 글 URL.
# 재생/썸네일은 Cloudflare 워커가 재생 시점에 아카 페이지를 다시 긁어 신선한 서명 URL로 처리.


def make_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9',
        'Referer': BOARD,
    })
    return s


def parse_count(text):
    """조회수/추천 파싱. '1.2만', '3천', '1,234' 등 처리."""
    if not text:
        return 0
    t = text.strip().replace(',', '')
    try:
        if '만' in t:
            return int(float(t.replace('만', '')) * 10000)
        if '천' in t:
            return int(float(t.replace('천', '')) * 1000)
        return int(re.sub(r'[^\d]', '', t) or 0)
    except Exception:
        return 0


def get_existing_post_ids():
    res = requests.get(
        f'{SUPABASE_URL}/rest/v1/arca_posts?select=post_id&limit=10000',
        headers=HEADERS
    )
    if res.status_code != 200:
        return set()
    return set(str(item['post_id']) for item in res.json())


def get_latest_post_date():
    """가장 최근 게시물 post_date 조회 (신규만 수집용 cutoff)."""
    res = requests.get(
        f'{SUPABASE_URL}/rest/v1/arca_posts?select=post_date&status=neq.deleted&order=post_date.desc&limit=1',
        headers=HEADERS
    )
    if res.status_code != 200:
        return None
    data = res.json()
    if data and data[0].get('post_date'):
        date_str = data[0]['post_date'][:10]
        try:
            return datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=KST)
        except Exception:
            return None
    return None


def fetch_list(session, cutoff_date, max_pages=60):
    """Ai영상 카테고리 목록을 페이지네이션하며 영상 글 수집.
    각 행에 ion-ios-videocam 아이콘이 있으면 영상 글로 판단.
    행 날짜가 cutoff보다 오래되면 중단.
    """
    posts = []
    seen = set()

    for page in range(1, max_pages + 1):
        params = {'category': CATEGORY, 'p': page}
        time.sleep(random.uniform(2.0, 4.0))

        res = None
        for attempt in range(3):
            try:
                res = session.get(BOARD, params=params, timeout=20)
                break
            except Exception as e:
                print(f'  요청 오류 (시도 {attempt+1}/3): {e}')
                time.sleep(random.uniform(3.0, 6.0))
        if res is None or res.status_code != 200:
            print(f'  [p={page}] 응답 실패, 중단')
            break

        soup = BeautifulSoup(res.text, 'html.parser')
        rows = [r for r in soup.select('a.vrow') if 'notice' not in (r.get('class') or [])]
        if not rows:
            print(f'  [p={page}] 행 없음, 중단')
            break

        page_valid = False
        stop = False

        for row in rows:
            try:
                href = row.get('href', '')
                m = re.search(r'/b/aireal/(\d+)', href)
                if not m:
                    continue
                post_id = m.group(1)

                # 날짜 (datetime 속성은 UTC) → KST
                time_el = row.select_one('.col-time time')
                dt_attr = time_el.get('datetime') if time_el else ''
                post_dt = None
                date_str = ''
                if dt_attr:
                    try:
                        utc_dt = datetime.strptime(dt_attr[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
                        post_dt = utc_dt.astimezone(KST)
                        date_str = post_dt.strftime('%Y-%m-%d')
                    except Exception:
                        pass

                if post_dt and post_dt < cutoff_date:
                    stop = True
                    break

                page_valid = True

                if post_id in seen:
                    continue
                seen.add(post_id)

                # 영상 아이콘 확인
                icon_classes = [c for mi in row.select('.media-icon') for c in (mi.get('class') or [])]
                has_video = 'ion-ios-videocam' in icon_classes
                if not has_video:
                    continue

                title_el = row.select_one('.col-title .title')
                title = ''
                if title_el:
                    # media-icon span 텍스트 제거
                    for mi in title_el.select('.media-icon'):
                        mi.extract()
                    title = title_el.get_text(strip=True)
                if not title or len(title) < 2:
                    continue

                author_el = row.select_one('.col-author [data-filter]') or row.select_one('.col-author')
                author = author_el.get_text(strip=True) if author_el else '익명'

                view_el = row.select_one('.col-view')
                rate_el = row.select_one('.col-rate')
                views = parse_count(view_el.get_text(strip=True) if view_el else '')
                rating = parse_count(rate_el.get_text(strip=True) if rate_el else '')

                posts.append({
                    'post_id': post_id,
                    'title': title,
                    'author': author,
                    'category': CATEGORY,
                    'post_date': date_str,
                    'views': views,
                    'rating': rating,
                    'url': f'{BASE}/b/aireal/{post_id}',
                })
                print(f'  발견: [{views}v/{rating}추] {title[:40]}')

            except Exception as e:
                print(f'행 파싱 오류: {e}')
                continue

        if stop:
            print(f'  [p={page}] cutoff 도달, 중단')
            break
        if not page_valid:
            print(f'  [p={page}] 유효 행 없음, 중단')
            break

    return posts


def insert_posts(posts):
    headers = {**HEADERS, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    res = requests.post(
        f'{SUPABASE_URL}/rest/v1/arca_posts',
        headers=headers,
        json=posts
    )
    print(f'저장 응답 status: {res.status_code}')
    if res.status_code not in (200, 201):
        print(f'저장 오류: {res.text[:300]}')
    return res.status_code in (200, 201)


def main():
    now_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
    print(f'[{now_kst}] 아카라이브 Ai영상 크롤링 시작')

    session = make_session()

    existing = get_existing_post_ids()
    print(f'기존 게시물 수: {len(existing)}개')

    latest_date = get_latest_post_date()
    if latest_date:
        cutoff_date = latest_date - timedelta(days=1)  # 경계 안전 여유
        print(f'기준 날짜: {cutoff_date.strftime("%Y-%m-%d")} 이후 (신규만)')
    else:
        cutoff_date = datetime.now(KST) - timedelta(days=30)
        print(f'첫 실행: {cutoff_date.strftime("%Y-%m-%d")} 이후 1달치 수집')

    posts = fetch_list(session, cutoff_date)
    print(f'\n총 영상 글 발견: {len(posts)}개')

    new_posts = [p for p in posts if p['post_id'] not in existing]
    print(f'신규 게시물: {len(new_posts)}개')

    if not new_posts:
        print('새로운 게시물이 없습니다.')
        return

    enriched = []
    for p in new_posts:
        enriched.append({
            **p,
            'post_date': p.get('post_date') or None,   # 빈 문자열이면 NULL (date 컬럼 보호)
            'has_video': True,
            'status': 'approved',
            'created_at': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S+09'),
        })

    success = insert_posts(enriched)
    if success:
        end_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
        print(f'[{end_kst}] {len(enriched)}개 게시물 저장 완료')
    else:
        print('저장 실패')


if __name__ == '__main__':
    main()
