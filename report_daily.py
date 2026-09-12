"""일일 수집 결과 요약 → 파일 + 텔레그램.

★자동 실행(06:00)에는 Claude 세션이 없다.
  로그만 남겨두면 사람이 직접 열어봐야 하므로, 요약을 만들어
  ① logs/summary_YYYY-MM-DD.md 로 남기고 ② 텔레그램으로 보낸다.
  세션을 열었을 때 Claude 도 이 파일만 읽으면 바로 상황을 안다.

사용법:  python report_daily.py [수집JSON] [로그파일]
"""
import os
import re
import io
import sys
import csv
import json
import glob
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
PROJ = r'C:\DEV\ai gathering'
LIB = r'C:\AI_Production_System\reference_library'
TG_CONF = r'C:\DEV\grok_auto\telegram_config.json'


def tail_reason(log_text):
    """실패 사유를 로그에서 뽑는다."""
    for pat in (r'\[FAIL\].*', r'할당량 초과가 심합니다.*', r'오류:.*'):
        m = re.findall(pat, log_text)
        if m:
            return m[-1][:180]
    return None


def main():
    now = datetime.now(KST)
    day = now.strftime('%Y-%m-%d')
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, 'animai', 'refs_dryrun.json')
    logp = sys.argv[2] if len(sys.argv) > 2 else os.path.join(PROJ, 'logs', f'refs_{day}.log')

    log = io.open(logp, encoding='utf-8', errors='replace').read() if os.path.exists(logp) else ''
    ok = 'DONE =====' in log
    reason = tail_reason(log)
    # 신·구 코드가 쓰는 문구가 다르므로 둘 다 센다
    q429 = log.count('할당량 초과(429)') + log.count('검색 오류(429)')

    # 수집 결과
    rows = json.load(io.open(src, encoding='utf-8')) if os.path.exists(src) else []
    sent = [r for r in rows if r.get('sent_to_incoming')]
    kinds = {}
    for r in sent:
        kinds[r.get('kind', '?')] = kinds.get(r.get('kind', '?'), 0) + 1

    # 오늘 받은 파일 (라이브러리 전체에서 오늘 날짜)
    today_files, today_mb = 0, 0.0
    for dirpath, _, files in os.walk(LIB):
        for f in files:
            if not f.lower().endswith('.mp4'):
                continue
            fp = os.path.join(dirpath, f)
            if datetime.fromtimestamp(os.path.getmtime(fp), KST).strftime('%Y-%m-%d') == day:
                today_files += 1
                today_mb += os.path.getsize(fp) / 1024 / 1024

    dl_ok = len(re.findall(r'✓ [\d.]+MB', log))
    dl_fail = log.count('✗ 실패')
    m = re.search(r'검수 (\d+)편 → 통과 (\d+) · 탈락 (\d+)', log)
    verify = (int(m.group(2)), int(m.group(3))) if m else None

    # ★429가 있으면 '정상'이라고 하지 않는다. 수집이 불완전하기 때문이다.
    empty_kind = [lab for k, lab in [('drama', '드라마'), ('ad_like', '광고'), ('general', '일반')]
                  if kinds.get(k, 0) == 0]
    if not ok or reason:
        head = '🔴 실패'
    elif q429 or empty_kind:
        head = '⚠️ 부분 수집'
    else:
        head = '✅ 정상'
    lines = [
        f'{head} · AI 레퍼런스 수집 {day}',
        '',
        f'수집 후보      {len(rows)}건',
        f'다운로드       성공 {dl_ok} · 실패 {dl_fail}',
        f'오늘 받은 파일  {today_files}편 · {today_mb:,.0f}MB',
        f'갈래별         드라마 {kinds.get("drama",0)} · 광고 {kinds.get("ad_like",0)} · 일반 {kinds.get("general",0)}',
    ]
    if verify:
        lines.append(f'검수           통과 {verify[0]} · 탈락 {verify[1]} (격리됨)')
    if q429:
        lines.append(f'⚠️ 할당량 429  {q429}회 — 수집 불완전 (리셋 KST 17시)')
    if empty_kind:
        lines.append(f'⚠️ 0건 갈래   {" · ".join(empty_kind)}')
    if reason:
        lines.append(f'⚠️ {reason}')
    lines += ['', f'로그: {logp}', '페이지: https://temp5525321.github.io/animai/refs.html']
    text = '\n'.join(lines)

    out = os.path.join(PROJ, 'logs', f'summary_{day}.md')
    io.open(out, 'w', encoding='utf-8').write(text + '\n')
    print(text)
    print(f'\n요약 저장: {out}')

    # 텔레그램
    try:
        import requests
        c = json.load(io.open(TG_CONF, encoding='utf-8'))
        r = requests.post(
            f'https://api.telegram.org/bot{c["bot_token"]}/sendMessage',
            json={'chat_id': c['chat_id'], 'text': text, 'disable_web_page_preview': True},
            timeout=20)
        print('텔레그램 전송:', '성공' if r.status_code == 200 else f'실패 {r.status_code}')
    except Exception as e:
        print('텔레그램 전송 실패:', type(e).__name__, e)


if __name__ == '__main__':
    main()
