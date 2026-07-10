"""아카 썸네일(poster) 로컬 다운로드기.
아카가 데이터센터 IP를 차단하므로 집 IP(로컬 PC)에서 실행해야 한다.
Supabase의 arca_posts를 읽어 각 글 상세페이지에서 poster를 받아 thumbs/<post_id>.webp 로 저장.
이미 있는 파일은 건너뛴다(재개 가능). 저장된 이미지는 GitHub Pages로 서빙.
"""
import os
import sys
import time
import random
import re
import html as htmlmod
import requests
from bs4 import BeautifulSoup

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')
if not all([SUPABASE_URL, SUPABASE_KEY]):
    print('오류: SUPABASE_URL / SUPABASE_KEY 환경변수 필요')
    sys.exit(1)

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
THUMB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'thumbs')
os.makedirs(THUMB_DIR, exist_ok=True)

SB_HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}


def get_posts():
    res = requests.get(
        f'{SUPABASE_URL}/rest/v1/arca_posts?select=post_id,url&status=eq.approved&order=post_date.desc&limit=10000',
        headers=SB_HEADERS, timeout=20)
    return res.json() if res.status_code == 200 else []


def make_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9',
        'Referer': 'https://arca.live/b/aireal',
    })
    return s


def fetch_poster(session, url):
    for attempt in range(3):
        try:
            r = session.get(url, timeout=20)
            if r.status_code != 200:
                return None
            m = re.search(r'poster="(//[a-z0-9.-]*namu\.la/[^"]+)"', r.text, re.I)
            if m:
                # HTML 속성의 &amp; 를 & 로 디코딩해야 서명(key)이 유효 (안 하면 403)
                return htmlmod.unescape('https:' + m.group(1))
            # video 없는 글일 수 있음
            return None
        except Exception as e:
            print(f'  상세 요청 오류 (시도 {attempt+1}/3): {e}')
            time.sleep(random.uniform(3.0, 5.0))
    return None


def main():
    posts = get_posts()
    print(f'대상 글: {len(posts)}개')
    session = make_session()
    saved = skipped = failed = 0

    for i, p in enumerate(posts, 1):
        pid = str(p['post_id'])
        out = os.path.join(THUMB_DIR, f'{pid}.webp')
        if os.path.exists(out) and os.path.getsize(out) > 0:
            skipped += 1
            continue

        time.sleep(random.uniform(1.2, 2.5))
        poster = fetch_poster(session, p['url'])
        if not poster:
            failed += 1
            print(f'[{i}/{len(posts)}] {pid} poster 없음/실패')
            continue
        try:
            img = requests.get(poster, headers={'User-Agent': UA}, timeout=20)
            if img.status_code == 200 and img.content:
                with open(out, 'wb') as f:
                    f.write(img.content)
                saved += 1
                if saved % 20 == 0:
                    print(f'[{i}/{len(posts)}] 진행 — 저장 {saved}, 스킵 {skipped}, 실패 {failed}')
            else:
                failed += 1
        except Exception as e:
            failed += 1
            print(f'[{i}/{len(posts)}] {pid} 이미지 다운 오류: {e}')

    print(f'\n완료 — 저장 {saved}, 스킵(기존) {skipped}, 실패 {failed}')


if __name__ == '__main__':
    main()
