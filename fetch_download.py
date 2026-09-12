"""수집 결과 → mp4 + 썸네일 + 자막 + sidecar JSON → reference_library/incoming

크롤러(fetch_refs.py)가 남긴 JSON을 읽어, 갈래별 상위 N개를 내려받고
GPT 분석 세션이 쓸 부가 정보를 영상 옆에 같이 남긴다.

  영상      <제목> [<video_id>].mp4
  사이드카   <제목> [<video_id>].json      ← 메타데이터 전부
  썸네일     <제목> [<video_id>].jpg
  자막      <제목> [<video_id>].<lang>.vtt  (있을 때만)

★영상만 두면 GPT가 조회수·최신성·채널 신뢰도·AI 제작 여부를 알 수 없다.
  sidecar가 있어야 "조회수만 높은 오염 자료 제거"나 "Deep 분석 후보 선정"이 된다.

사용법:
  python fetch_download.py <수집JSON> [--dest <폴더>] [--drama 10] [--ad 30] [--general 20] [--dry]
"""
import os
import re
import sys
import csv
import json
import time
import argparse
import subprocess
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
DEST_DEFAULT = r'C:\AI_Production_System\reference_library\incoming'

# GPT 쪽 분류 힌트 — 우리 kind/tag를 그쪽 어휘로 옮긴다
CATEGORY = {'drama': 'drama', 'ad_like': 'ad', 'general': 'unknown'}

HASHTAG = re.compile(r'#([0-9A-Za-z가-힣_]+)')


def pick(rows, kind, n):
    """갈래별 상위 N. ★게이트를 먼저 통과시키고 점수로 정렬한다.
       (점수만으로 뽑으면 급등한 튜토리얼이 올라온다)"""
    pool = [r for r in rows
            if r.get('kind') == kind
            and r.get('is_ai_generated_likely')
            and (r.get('pollution_risk_score') or 0) < 0.35
            and not r.get('is_tutorial_or_review_likely')]
    pool.sort(key=lambda r: -(r.get('quality_score') or 0))
    return pool[:n]


def category_guess(r):
    tags = r.get('auto_tags') or []
    if '메이킹' in tags:
        return 'making_of'
    if r.get('is_tutorial_or_review_likely'):
        return 'review'
    if r.get('kind') == 'general' and (r.get('ad_likeness') or 0) >= 0.66:
        return 'ad'
    return CATEGORY.get(r.get('kind'), 'unknown')


def series_key(r):
    """시리즈 묶음 식별 — 같은 채널의 같은 시리즈를 한 그룹으로.

    ★회차 번호만 지우면 안 된다. 화마다 부제가 달라 따로 흩어진다(실측).
      CRAFT (1979): The Cove | Episode 2   → CRAFT1979TheCove
      CRAFT (1979): The Point | Episode 3  → CRAFT1979ThePoint   ← 다른 그룹이 됐다
      그래서 회차를 지운 뒤 구분자(: | ｜ -) 앞의 '시리즈명'만 남긴다.
    """
    if not r.get('is_series_likely'):
        return None
    t = r.get('title', '')
    t = re.sub(r'(?i)(ep\.?\s*\d+|episode\s*\d+|\d+\s*화|part\s*\d+|\[\d+/\d+\]|시즌\s*\d+)', '', t)
    t = re.split(r'[:\|｜\-–—]', t)[0]          # 시리즈명만
    t = re.sub(r'[^0-9A-Za-z가-힣]+', '', t)[:30]
    return f"{r.get('channel_id','')}::{t}" if len(t) >= 3 else None


def sidecar(r, dest, filename, crawl_date, comments=None):
    """GPT 스펙에 맞춘 부가 정보. 영상 옆에 같은 이름으로 남긴다."""
    desc = r.get('description') or ''
    data = {
        # ── 필수
        'original_url': r.get('url'),
        'video_id': r.get('video_id'),
        'title': r.get('title'),
        'channel_name': r.get('channel'),
        'channel_id': r.get('channel_id'),
        'channel_subscriber_count': r.get('channel_subs'),
        'upload_date': r.get('published_at'),
        'crawl_date': crawl_date,
        'duration_sec': r.get('duration_sec'),
        'view_count': r.get('views'),
        'like_count': r.get('likes'),
        'comment_count': r.get('comments'),
        'views_per_day': r.get('views_per_day'),
        'view_subscriber_ratio': r.get('view_sub_ratio'),
        'search_query': r.get('search_keyword'),
        'search_branch': r.get('lane'),
        'source_category_guess': category_guess(r),
        'is_ai_generated_likely': r.get('is_ai_generated_likely'),
        'is_ai_topic_only_likely': r.get('is_ai_topic_only_likely'),
        'detected_engine': r.get('engine') or 'unknown',
        'quality_score': r.get('quality_score'),
        'score_breakdown': r.get('score_breakdown'),
        'keep_reason': None if r.get('reject_reason') else 'AI 제작 판정 · 오염 낮음 · 갈래별 상위',
        'reject_reason': r.get('reject_reason'),
        'local_filename': filename,
        'thumbnail_url': r.get('thumb'),
        'thumbnail_local_path': None,
        # ── 있으면 좋은 것
        'description': desc,
        'hashtags': sorted(set(HASHTAG.findall(f"{r.get('title','')} {desc}"))),
        'youtube_tags': r.get('tags') or [],
        'auto_tags': r.get('auto_tags') or [],
        'channel_recent_ai_ratio': r.get('channel_ai_signal'),
        'channel_videos_in_crawl': r.get('channel_video_count'),
        'ai_generated_confidence': r.get('ai_generated_confidence'),
        'niche_breakout': r.get('niche_breakout'),
        'ad_likeness': r.get('ad_likeness'),
        'is_series_likely': r.get('is_series_likely'),
        'duplicate_group_id': series_key(r),
        'is_vertical': r.get('is_vertical'),
        'search_order': r.get('search_order'),
        'pinned_comment': (comments or {}).get('pinned'),
        'top_comments_sample': (comments or {}).get('top'),
        'captions_files': [],
        'download_status': 'pending',
        'download_error': None,
        'scoring_version': r.get('scoring_version'),
    }
    return data


def run_ytdlp(url, dest, stem):
    """★영상과 자막을 따로 부른다.
       한 번에 부르면 자막에서 429가 나도 영상까지 통째로 실패한다(실측).
       자막은 '있으면 좋은 것'이라 실패해도 영상은 건진다."""
    out = os.path.join(dest, stem + '.%(ext)s')
    base = ['yt-dlp', '--no-playlist', '--windows-filenames', '--force-overwrites',
            '--retries', '10', '--fragment-retries', '10',
            '--no-warnings', '--quiet', '--no-progress']

    # ① 영상 + 썸네일 (필수)
    #    ★403은 유튜브가 간헐적으로 내는 것이라 재시도하면 대개 풀린다(실측).
    #      1차는 기본 클라이언트, 2차는 고화질을 내주는 클라이언트로 바꿔 시도한다.
    attempts = [
        [],
        ['--extractor-args', 'youtube:player_client=tv_embedded,android_vr'],
        ['--extractor-args', 'youtube:player_client=web_safari', '--http-chunk-size', '5M'],
    ]
    last = ''
    for n, extra in enumerate(attempts, 1):
        p1 = subprocess.run(base + extra + [
            '-f', 'bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/bv*[height<=1080]+ba/b',
            '--merge-output-format', 'mp4',
            '--write-thumbnail', '--convert-thumbnails', 'jpg',
            '-o', out, url,
        ], capture_output=True, text=True, timeout=900)
        if p1.returncode == 0:
            break
        last = (p1.stderr or '').strip()[:300]
        if n < len(attempts):
            print(f'        재시도 {n+1}/{len(attempts)} — {last[:70]}')
            time.sleep(5)
    else:
        return p1.returncode, last

    # ② 자막 (선택 — 실패해도 무시)
    try:
        subprocess.run(base + [
            '--skip-download',
            '--write-subs', '--write-auto-subs',
            '--sub-langs', 'ko,en,ko-orig,en-orig', '--sub-format', 'vtt',
            '-o', out, url,
        ], capture_output=True, text=True, timeout=240)
    except Exception:
        pass
    return 0, ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source')
    ap.add_argument('--dest', default=DEST_DEFAULT)
    ap.add_argument('--drama', type=int, default=10)
    ap.add_argument('--ad', type=int, default=30)
    ap.add_argument('--general', type=int, default=20)
    ap.add_argument('--dry', action='store_true', help='받지 않고 대상만 출력')
    a = ap.parse_args()

    now = datetime.now(KST)
    crawl_date = now.strftime('%Y-%m-%d')
    rows = json.load(open(a.source, encoding='utf-8'))

    targets = (pick(rows, 'drama', a.drama)
               + pick(rows, 'ad_like', a.ad)
               + pick(rows, 'general', a.general))

    print(f'[{now:%Y-%m-%d %H:%M} KST] 다운로드 대상 {len(targets)}개')
    for k, lab, n in [('drama', '드라마', a.drama), ('ad_like', '광고성', a.ad), ('general', '일반', a.general)]:
        got = sum(1 for r in targets if r['kind'] == k)
        print(f'  {lab:<5} {got:>3}/{n}  ' + ('(후보 부족)' if got < n else ''))
    est = sum(r['duration_sec'] for r in targets) * 147 / 493
    print(f'예상 용량 약 {est:,.0f}MB\n')

    if a.dry:
        for r in targets:
            m, s = divmod(r['duration_sec'], 60)
            print(f"  [{r['quality_score']:>5.1f}] {r['kind']:<8} {m}:{s:02d} {r['title'][:52]}")
        return

    os.makedirs(a.dest, exist_ok=True)
    ok = fail = 0
    manifest = []

    for i, r in enumerate(targets, 1):
        vid = r['video_id']
        safe = re.sub(r'[\\/:*?"<>|]', '_', (r['title'] or vid))[:70].strip()
        stem = f'{safe} [{vid}]'
        print(f'[{i}/{len(targets)}] {r["kind"]:<8} {r["title"][:46]}')

        if i > 1:
            time.sleep(2)          # 429 회피 — 실측에서 연속 요청 시 자막이 막혔다
        code, err = run_ytdlp(r['url'], a.dest, stem)
        mp4 = os.path.join(a.dest, stem + '.mp4')
        meta = sidecar(r, a.dest, stem + '.mp4', crawl_date)

        if code == 0 and os.path.exists(mp4):
            meta['download_status'] = 'done'
            thumb = os.path.join(a.dest, stem + '.jpg')
            if os.path.exists(thumb):
                meta['thumbnail_local_path'] = stem + '.jpg'
            meta['captions_files'] = sorted(
                f for f in os.listdir(a.dest) if f.startswith(stem) and f.endswith('.vtt'))
            size = os.path.getsize(mp4) / 1024 / 1024
            print(f'        ✓ {size:.1f}MB'
                  + (f" · 자막 {len(meta['captions_files'])}" if meta['captions_files'] else '')
                  + (' · 썸네일' if meta['thumbnail_local_path'] else ''))
            ok += 1
        else:
            meta['download_status'] = 'failed'
            meta['download_error'] = err or f'exit {code}'
            print(f'        ✗ 실패: {meta["download_error"][:90]}')
            fail += 1

        with open(os.path.join(a.dest, stem + '.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        manifest.append(meta)

    # ★수집 JSON에 '보냈음'을 되써넣는다.
    #   그래야 페이지에서 "넘긴 것 / 안 넘긴 것"을 구분해 볼 수 있고,
    #   Dan이 안 넘어간 것 중에서 직접 골라 추가로 보낼 수 있다.
    sent = {m['video_id']: m for m in manifest if m['download_status'] == 'done'}
    for r in rows:
        if r['video_id'] in sent:
            r['sent_to_incoming'] = True
            r['sent_at'] = crawl_date
            r['sent_filename'] = sent[r['video_id']]['local_filename']
        else:
            r.setdefault('sent_to_incoming', False)
    with open(a.source, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False)
    print(f'수집 JSON 갱신: 보낸 것 {len(sent)}개 표시')

    # 하루치 매니페스트 — GPT 쪽에서 한 번에 훑을 수 있게
    mpath = os.path.join(a.dest, f'_manifest_{crawl_date}.csv')
    cols = ['video_id', 'source_category_guess', 'title', 'channel_name', 'duration_sec',
            'view_count', 'views_per_day', 'view_subscriber_ratio', 'quality_score',
            'detected_engine', 'upload_date', 'search_branch', 'duplicate_group_id',
            'local_filename', 'download_status', 'original_url']
    with open(mpath, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(manifest)

    print(f'\n완료: 성공 {ok} · 실패 {fail}')
    print(f'매니페스트: {mpath}')


if __name__ == '__main__':
    main()
