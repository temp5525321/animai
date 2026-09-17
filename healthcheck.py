"""파이프라인 건강검진 — 세션을 열 때 이것부터 돌린다.

★"조용히 잘못 도는 것"이 제일 위험하다.
  실측으로 겪은 것들:
    · 배치가 죽었는데 작업 스케줄러는 성공(0)으로 보고 (9/12)
    · 매일 같은 영상을 다시 받고 있었는데 겉보기엔 정상 (9/11~9/16)
    · 작업 3종이 임의로 Disabled 됐는데 아무 알림이 없었음 (9/16)
  전부 사람이 물어봐서 발견했다. 그래서 같은 항목을 매번 기계적으로 본다.

사용법:  python healthcheck.py
"""
import os
import io
import re
import json
import glob
import subprocess
from datetime import datetime, timezone, timedelta
from collections import defaultdict

KST = timezone(timedelta(hours=9))
PROJ = r'C:\DEV\ai gathering'
LIB = r'C:\AI_Production_System\reference_library'
TASKS = ['AI_Refs_Daily', 'AI_Refs_Process_Daily', 'AI_Refs_Report']
ID_IN_NAME = re.compile(r'\[([A-Za-z0-9_-]{11})\]')

ok, warn, bad = [], [], []


def check_tasks():
    """★상태와 결과를 둘 다 본다. Disabled 는 결과가 0이어도 안 도는 것이다."""
    names = ','.join(f"'{t}'" for t in TASKS)
    script = '\n'.join([
        f'foreach ($n in @({names})) {{',
        '  $t = Get-ScheduledTask -TaskName $n',
        '  $i = Get-ScheduledTaskInfo -TaskName $n',
        "  Write-Output ($n + '|' + $t.State + '|' + $i.LastRunTime + '|' +",
        "                $i.LastTaskResult + '|' + $i.NextRunTime)",
        '}',
        '',
    ])
    tmp = os.path.join(os.environ.get('TEMP', '.'), '_hc_tasks.ps1')
    io.open(tmp, 'w', encoding='utf-8').write(script)
    out = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', tmp],
                         capture_output=True, text=True, timeout=90).stdout
    found = 0
    for line in out.strip().splitlines():
        p = line.strip().split('|')
        if len(p) < 5:
            continue
        found += 1
        name, state, last, res, nxt = p[0], p[1], p[2], p[3], p[4]
        if state != 'Ready':
            bad.append(f'작업 {name} 상태가 {state} — 켜야 합니다')
        elif res not in ('0', ''):
            warn.append(f'작업 {name} 마지막 결과 {res} (0이 아님) · {last}')
        else:
            ok.append(f'{name:<22} Ready · 마지막 {last[:16]} · 다음 {nxt[:16]}')
    if not found:
        bad.append('작업 스케줄러 상태를 읽지 못했습니다 — 직접 확인 필요')


def check_today():
    day = datetime.now(KST).strftime('%Y-%m-%d')
    s = os.path.join(PROJ, 'logs', f'summary_{day}.md')
    if not os.path.exists(s):
        warn.append(f'오늘({day}) 요약 파일이 없습니다 — 아직 안 돌았거나 실패')
        return
    t = io.open(s, encoding='utf-8').read()
    head = t.splitlines()[0] if t else ''
    (bad if '🔴' in head else warn if '⚠️' in head else ok).append(head)
    for key in ('다운로드', '검수', '[분석]'):
        for ln in t.splitlines():
            if ln.startswith(key):
                ok.append('  ' + ln.strip())
                break


def check_dupes():
    """파일명의 [video_id] 로 중복을 센다. __dup 접미사도 잡히게 한다."""
    ids = defaultdict(list)
    for dp, _, fs in os.walk(LIB):
        for f in fs:
            if not f.lower().endswith('.mp4'):
                continue
            m = ID_IN_NAME.search(f)
            if m:
                ids[m.group(1)].append(f)
    dup = {k: v for k, v in ids.items() if len(v) > 1}
    if dup:
        bad.append(f'중복 영상 {len(dup)}편 — 정리 필요')
    else:
        ok.append(f'영상 {len(ids)}편 · 중복 0')
    return set(ids)


def check_new(have):
    """오늘 수집분에 '진짜 신규'가 남아 있는지. 0이면 후보가 마른 것이다."""
    src = os.path.join(PROJ, 'animai', 'refs_dryrun.json')
    if not os.path.exists(src):
        return
    rows = json.load(io.open(src, encoding='utf-8'))
    floor = {'drama': 60, 'ad_like': 50, 'general': 75}
    for k, lab in [('drama', '드라마'), ('ad_like', '광고'), ('general', '일반')]:
        n = sum(1 for r in rows
                if r.get('kind') == k and r.get('video_id') not in have
                and r.get('is_ai_generated_likely')
                and (r.get('pollution_risk_score') or 0) < 0.35
                and not r.get('is_tutorial_or_review_likely')
                and (r.get('quality_score') or 0) >= floor[k])
        line = f'  {lab} 신규 후보 {n}편'
        (bad if n == 0 else warn if n < 10 else ok).append(
            line + (' ★고갈' if n == 0 else ' (부족)' if n < 10 else ''))


def check_incoming():
    n = len(glob.glob(os.path.join(LIB, 'incoming', '*.mp4')))
    r = len(glob.glob(os.path.join(LIB, 'incoming', '_rejected', '*.mp4')))
    ok.append(f'incoming 대기 {n}편' + (f' · 격리 {r}편' if r else ''))
    if n > 150:
        warn.append(f'incoming 에 {n}편이 쌓여 있습니다 — 분석 세션이 안 가져가는 중일 수 있습니다')


def check_board():
    """상대 게시판에 내가 아직 안 본 글이 있는지."""
    ex = os.path.join(LIB, '_exchange')
    fa, fc = os.path.join(ex, 'from_analyst.md'), os.path.join(ex, 'from_crawler.md')
    if os.path.exists(fa) and os.path.exists(fc):
        if os.path.getmtime(fa) > os.path.getmtime(fc):
            todo = sum(1 for ln in io.open(fa, encoding='utf-8')
                       if ln.strip().startswith('- [ ]'))
            warn.append(f'★분석 세션이 새 글을 남겼습니다 (미처리 {todo}건) — 읽고 답할 것')
        else:
            ok.append('게시판: 새 글 없음')


def main():
    print(f'[{datetime.now(KST):%Y-%m-%d %H:%M} KST] 파이프라인 건강검진\n')
    check_tasks()
    check_today()
    have = check_dupes()
    check_new(have)
    check_incoming()
    check_board()

    for lab, items, mark in (('문제', bad, '🔴'), ('주의', warn, '⚠️'), ('정상', ok, '✅')):
        if items:
            print(f'{mark} {lab}')
            for i in items:
                print(f'   {i}')
            print()
    print('판정:', '🔴 조치 필요' if bad else '⚠️ 확인 필요' if warn else '✅ 이상 없음')


if __name__ == '__main__':
    main()
