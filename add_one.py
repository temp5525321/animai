"""URL 하나를 받아 incoming 에 넣는다 (Dan 직접 지정용).

자동 수집과 달리 판정 게이트를 통과시키지 않는다.
Dan 이 "이거 받아줘" 하는 것은 이유가 있어서이므로 규칙으로 막지 않는다.
다만 sidecar 에 `manual_request: true` 와 사유를 남겨,
분석 세션이 자동 수집분과 구분할 수 있게 한다.

사용법:
  python add_one.py <URL> [--category drama|ad|making_of|commentary|unknown]
                          [--note "왜 받는지"] [--dest <폴더>]
"""
import os
import re
import io
import sys
import json
import glob
import argparse
import subprocess
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
DEST = r'C:\AI_Production_System\reference_library\incoming'
HASHTAG = re.compile(r'#([0-9A-Za-z가-힣_]+)')

FIELDS = ('id,title,channel,channel_id,channel_follower_count,upload_date,duration,'
          'view_count,like_count,comment_count,description,tags,thumbnail,webpage_url')


def probe(url):
    """메타데이터만 먼저 가져온다 (파일명·sidecar 를 만들기 위함)."""
    p = subprocess.run(
        ['yt-dlp', '--skip-download', '--no-warnings', '--dump-single-json', '--no-playlist', url],
        capture_output=True, text=True, encoding='utf-8', timeout=180)
    if p.returncode != 0:
        raise SystemExit(f'메타 조회 실패: {(p.stderr or "")[-300:]}')
    return json.loads(p.stdout)


def download(url, dest, stem):
    base = ['yt-dlp', '--no-playlist', '--windows-filenames', '--force-overwrites',
            '--retries', '10', '--fragment-retries', '10', '--no-warnings', '--quiet', '--no-progress']
    out = os.path.join(dest, stem + '.%(ext)s')
    # 403 은 간헐적이라 클라이언트를 바꿔 재시도하면 대개 풀린다
    for extra in ([], ['--extractor-args', 'youtube:player_client=tv_embedded,android_vr'],
                  ['--extractor-args', 'youtube:player_client=web_safari', '--http-chunk-size', '5M']):
        p = subprocess.run(base + extra + [
            '-f', 'bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/bv*[height<=1080]+ba/b',
            '--merge-output-format', 'mp4',
            '--write-thumbnail', '--convert-thumbnails', 'jpg',
            '-o', out, url], capture_output=True, text=True, timeout=1800)
        if p.returncode == 0:
            break
        print(f'  재시도… {(p.stderr or "")[:80]}')
    else:
        return False, (p.stderr or '')[:300]
    # 자막은 따로 (실패해도 영상은 건진다)
    try:
        subprocess.run(base + ['--skip-download', '--write-subs', '--write-auto-subs',
                               '--sub-langs', 'ko,en,ko-orig,en-orig', '--sub-format', 'vtt',
                               '-o', out, url], capture_output=True, text=True, timeout=300)
    except Exception:
        pass
    return True, ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('url')
    ap.add_argument('--category', default='unknown')
    ap.add_argument('--note', default='Dan 직접 지정')
    ap.add_argument('--dest', default=DEST)
    a = ap.parse_args()

    info = probe(a.url)
    vid = info['id']
    title = info.get('title', vid)
    dur = int(info.get('duration') or 0)
    views = int(info.get('view_count') or 0)
    subs = int(info.get('channel_follower_count') or 0)
    up = info.get('upload_date') or ''
    up_iso = f'{up[:4]}-{up[4:6]}-{up[6:8]}' if len(up) == 8 else None

    now = datetime.now(KST)
    age = 1
    if up_iso:
        try:
            age = max(1, (now - datetime.strptime(up_iso, '%Y-%m-%d').replace(tzinfo=timezone.utc)).days)
        except ValueError:
            pass

    safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:70].strip()
    stem = f'{safe} [{vid}]'
    m, s = divmod(dur, 60)
    print(f'{title[:60]}\n  {m}:{s:02d} · 조회 {views:,} · {info.get("channel")}')

    os.makedirs(a.dest, exist_ok=True)
    ok, err = download(a.url, a.dest, stem)
    desc = info.get('description') or ''

    meta = {
        'original_url': info.get('webpage_url') or a.url,
        'video_id': vid, 'title': title,
        'channel_name': info.get('channel'), 'channel_id': info.get('channel_id'),
        'channel_subscriber_count': subs,
        'upload_date': up_iso, 'crawl_date': now.strftime('%Y-%m-%d'),
        'duration_sec': dur, 'view_count': views,
        'like_count': int(info.get('like_count') or 0),
        'comment_count': int(info.get('comment_count') or 0),
        'views_per_day': round(views / age, 2), 'age_days': age,
        'view_subscriber_ratio': round(views / subs, 4) if subs else 0,
        'search_query': None, 'search_branch': 'manual', 'search_order': 'manual',
        'source_category_guess': a.category,
        # ★수동 지정분은 자동 판정을 적용하지 않는다. 사람이 이유가 있어 고른 것이다.
        'manual_request': True, 'keep_reason': a.note,
        'is_ai_generated_likely': None, 'is_ai_topic_only_likely': None,
        'detected_engine': 'unknown', 'quality_score': None, 'score_breakdown': None,
        'reject_reason': None,
        'local_filename': stem + '.mp4',
        'thumbnail_url': info.get('thumbnail'),
        'thumbnail_local_path': stem + '.jpg' if os.path.exists(os.path.join(a.dest, stem + '.jpg')) else None,
        'description': desc[:4000],
        'hashtags': sorted(set(HASHTAG.findall(f'{title} {desc}'))),
        'youtube_tags': (info.get('tags') or [])[:30],
        'auto_tags': ['수동지정'],
        'captions_files': sorted(f for f in os.listdir(a.dest)
                                 if f.startswith(stem) and f.endswith('.vtt')),
        'download_status': 'done' if ok else 'failed',
        'download_error': None if ok else err,
    }
    with io.open(os.path.join(a.dest, stem + '.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    if ok:
        mb = os.path.getsize(os.path.join(a.dest, stem + '.mp4')) / 1024 / 1024
        print(f'  ✓ {mb:.1f}MB'
              + (f" · 자막 {len(meta['captions_files'])}" if meta['captions_files'] else '')
              + (' · 썸네일' if meta['thumbnail_local_path'] else ''))
        print(f'  → {a.dest}')
    else:
        print(f'  ✗ 실패: {err[:120]}')


if __name__ == '__main__':
    main()
