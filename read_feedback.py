"""Codex의 일일 인테이크 리포트를 읽어 '수집 조건을 어떻게 조일지'를 뽑아낸다.

★피드백 루프를 닫는 자리다.
  Codex가 reports/daily_intake/<날짜>/intake_report.json 에
  grade / grade_reason / search_branch / search_order 를 남기므로,
  그걸 읽으면 사람을 거치지 않고 다음 수집을 개선할 수 있다.

  S후보가 많이 나온 갈래  → 그 갈래 키워드를 늘린다
  A/Hold만 나오는 갈래    → 줄이거나 조건을 조인다
  항상 좋은 채널          → ★그 채널을 통째로 긁는 게 키워드 100개보다 낫다

채널별 성과표는 분석 세션 요청(2026-09-12)으로 추가했다.
intake_report 에는 채널 정보가 없어 우리 sidecar 를 video_id 로 이어 붙인다.

사용법:  python read_feedback.py [--days 7]
"""
import os
import io
import csv
import json
import glob
import argparse
from datetime import datetime
from collections import Counter, defaultdict

LIB = r'C:\AI_Production_System\reference_library'
REPORTS = os.path.join(LIB, 'reports', 'daily_intake')
EXCHANGE = os.path.join(LIB, '_exchange')
GRADE_RANK = {'S': 3, 'S_candidate': 3, 'A': 2, 'B': 1, 'Hold': 0, 'hold': 0}


def load_sidecar_channels():
    """라이브러리 전체 sidecar 에서 video_id → 채널 정보를 모은다."""
    out = {}
    for dirpath, _, files in os.walk(LIB):
        for f in files:
            if not f.endswith('.json') or f.startswith('_manifest'):
                continue
            try:
                d = json.load(io.open(os.path.join(dirpath, f), encoding='utf-8'))
            except Exception:
                continue
            if isinstance(d, dict) and d.get('video_id') and d.get('channel_name'):
                out[d['video_id']] = {'name': d['channel_name'], 'id': d.get('channel_id', '')}
    return out


def load(days):
    dirs = [d for d in sorted(glob.glob(os.path.join(REPORTS, '*')))
            if os.path.isdir(d)][-days:]
    rows = []
    for d in dirs:
        f = os.path.join(d, 'intake_report.json')
        if not os.path.exists(f):
            continue
        j = json.load(io.open(f, encoding='utf-8'))
        for r in j.get('processed', []):
            r['_date'] = j.get('date') or os.path.basename(d)
            rows.append(r)
    return rows, [os.path.basename(d) for d in dirs]


def pct(n, d):
    return f'{n / d * 100:.0f}%' if d else '-'


def breakdown(rows, key, label):
    print(f'\n── {label}별 성적 ──')
    by = defaultdict(list)
    for r in rows:
        by[r.get(key, '?')].append(GRADE_RANK.get(r.get('grade'), 0))
    print(f"  {'':<12}{'편수':>5}{'S후보':>7}{'평균등급':>9}")
    out = []
    for k, v in by.items():
        out.append((sum(v) / len(v), k, len(v), sum(1 for x in v if x >= 3)))
    for avg, k, n, s in sorted(out, reverse=True):
        print(f'  {k:<12}{n:>5}{s:>7}{avg:>9.2f}')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7)
    a = ap.parse_args()

    rows, dates = load(a.days)
    if not rows:
        print(f'{REPORTS} 에 인테이크 리포트가 없습니다.')
        return

    print(f'기간 {dates[0]} ~ {dates[-1]} · 총 {len(rows)}편\n')
    print('── 등급 분포 ──')
    for g, n in Counter(r.get('grade', '?') for r in rows).most_common():
        print(f'  {g:<14}{n:>4}  {pct(n, len(rows))}')

    br = breakdown(rows, 'search_branch', '검색 갈래')
    breakdown(rows, 'search_order', '수집 정렬')
    breakdown(rows, 'category', '분류')

    # ── 채널별 성과 (분석 세션 요청) ──
    meta = load_sidecar_channels()
    ch = defaultdict(lambda: defaultdict(int))
    for r in rows:
        m = meta.get(r.get('video_id'))
        if not m:
            continue
        g = r.get('grade', '?')
        key = (m['name'], m['id'])
        ch[key][g] += 1
        ch[key]['_n'] += 1
        ch[key]['_score'] += GRADE_RANK.get(g, 0)

    rep = [(v['_score'] / v['_n'], name, cid, v)
           for (name, cid), v in ch.items() if v['_n'] >= 2]

    print(f'\n── 채널별 성과 ({a.days}일 누적, 2편 이상) ──')
    print(f"  {'편수':>4}{'평균':>7}{'S후보':>6}{'A':>4}{'B':>4}{'Hold':>5}  채널")
    for avg, name, cid, v in sorted(rep, reverse=True)[:12]:
        s = v.get('S_candidate', 0) + v.get('S', 0)
        hold = v.get('Hold', 0) + v.get('hold', 0)
        print(f"  {v['_n']:>4}{avg:>7.2f}{s:>6}{v.get('A', 0):>4}"
              f"{v.get('B', 0):>4}{hold:>5}  {name[:30]}")
    if not rep:
        print('  (2편 이상인 채널이 아직 없습니다)')
    else:
        os.makedirs(EXCHANGE, exist_ok=True)
        out = os.path.join(EXCHANGE, 'channel_performance.csv')
        with io.open(out, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['channel_name', 'channel_id', 'videos', 'avg_grade',
                        'S_candidate', 'A', 'B', 'Hold', 'days_window', 'updated'])
            for avg, name, cid, v in sorted(rep, reverse=True):
                w.writerow([name, cid, v['_n'], f'{avg:.2f}',
                            v.get('S_candidate', 0) + v.get('S', 0), v.get('A', 0),
                            v.get('B', 0), v.get('Hold', 0) + v.get('hold', 0),
                            a.days, datetime.now().strftime('%Y-%m-%d %H:%M')])
        print(f'  → {out}')

    print('\n── 낮은 등급 사유 ──')
    low = [r for r in rows if GRADE_RANK.get(r.get('grade'), 0) <= 1]
    if low:
        for g, n in Counter(r.get('grade_reason', '?') for r in low).most_common(6):
            print(f'  {n:>4}  {g}')
    else:
        print('  (B/Hold 가 아직 없습니다)')

    print('\n══ 다음 수집에 반영할 것 ══')
    if br:
        best, worst = max(br), min(br)
        if best[0] > worst[0] + 0.3:
            print(f'  · "{best[1]}" 갈래가 가장 좋습니다(평균 {best[0]:.2f}) → 키워드를 늘리세요')
            print(f'  · "{worst[1]}" 갈래가 가장 나쁩니다(평균 {worst[0]:.2f}) → 줄이거나 조이세요')
        else:
            print('  · 갈래별 차이가 작습니다. 아직 조정할 근거가 부족합니다')
    good = [x for x in rep if x[0] >= 2.5 and x[3]['_n'] >= 2]
    for avg, name, cid, v in sorted(good, reverse=True)[:3]:
        print(f'  · ★"{name}" 채널 {v["_n"]}편 평균 {avg:.2f} → 채널 통째 수집 후보')


if __name__ == '__main__':
    main()
