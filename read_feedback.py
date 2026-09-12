"""Codex의 일일 인테이크 리포트를 읽어 '수집 조건을 어떻게 조일지'를 뽑아낸다.

★피드백 루프를 닫는 자리다.
  지금까지는 내가 보낸 것이 어떤 평가를 받았는지 몰랐다.
  Codex가 reports/daily_intake/<날짜>/intake_report.json 에
  grade / grade_reason / search_branch / search_order 를 남기고 있으므로,
  그걸 읽으면 사람을 거치지 않고 다음 수집을 개선할 수 있다.

  S후보가 많이 나온 갈래  → 그 갈래 키워드를 늘린다
  A/Hold만 나오는 갈래    → 그 갈래를 줄이거나 조건을 조인다
  항상 좋은 채널          → ★그 채널을 통째로 긁는 게 키워드 100개보다 낫다

사용법:  python read_feedback.py [--days 7]
"""
import os
import io
import json
import glob
import argparse
from collections import Counter, defaultdict

REPORTS = r'C:\AI_Production_System\reference_library\reports\daily_intake'
GRADE_RANK = {'S': 3, 'S_candidate': 3, 'A': 2, 'B': 1, 'Hold': 0, 'hold': 0}


def load(days):
    dirs = sorted(glob.glob(os.path.join(REPORTS, '*')))[-days:]
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

    def breakdown(key, label):
        print(f'\n── {label}별 성적 ──')
        by = defaultdict(list)
        for r in rows:
            by[r.get(key, '?')].append(GRADE_RANK.get(r.get('grade'), 0))
        print(f"  {'':<12}{'편수':>5}{'S후보':>7}{'평균등급':>9}")
        out = []
        for k, v in by.items():
            s = sum(1 for x in v if x >= 3)
            out.append((sum(v) / len(v), k, len(v), s))
        for avg, k, n, s in sorted(out, reverse=True):
            print(f'  {k:<12}{n:>5}{s:>7}{avg:>9.2f}')
        return out

    br = breakdown('search_branch', '검색 갈래')
    breakdown('search_order', '수집 정렬')
    breakdown('category', '분류')

    # 채널 단위 — 반복해서 좋은 등급을 받는 채널을 찾는다
    print('\n── 반복 등장 채널 (2편 이상) ──')
    ch = defaultdict(list)
    for r in rows:
        name = (r.get('channel_name') or r.get('stem', '')).split('[')[0].strip()[:34]
        ch[name].append(GRADE_RANK.get(r.get('grade'), 0))
    rep = [(sum(v) / len(v), k, len(v)) for k, v in ch.items() if len(v) >= 2]
    if rep:
        for avg, k, n in sorted(rep, reverse=True)[:8]:
            print(f'  {n}편 · 평균 {avg:.2f}  {k}')
    else:
        print('  (없음 — 채널명이 리포트에 없으면 stem으로 대체합니다)')

    print('\n── 탈락 사유 ──')
    for g, n in Counter(r.get('grade_reason', '?') for r in rows
                        if GRADE_RANK.get(r.get('grade'), 0) <= 1).most_common(6):
        print(f'  {n:>4}  {g}')

    # 권고
    print('\n══ 다음 수집에 반영할 것 ══')
    if br:
        best = max(br)
        worst = min(br)
        if best[0] > worst[0] + 0.3:
            print(f'  · "{best[1]}" 갈래가 가장 좋습니다(평균 {best[0]:.2f}) → 이 갈래 키워드를 늘리세요')
            print(f'  · "{worst[1]}" 갈래가 가장 나쁩니다(평균 {worst[0]:.2f}) → 줄이거나 조건을 조이세요')
        else:
            print('  · 갈래별 차이가 작습니다. 아직 조정할 근거가 부족합니다')
    if rep:
        top = max(rep)
        if top[0] >= 2.5 and top[2] >= 2:
            print(f'  · ★"{top[1]}" 채널이 반복해서 좋습니다({top[2]}편 평균 {top[0]:.2f})')
            print('    → 이 채널을 통째로 긁는 편이 키워드를 늘리는 것보다 효율이 높습니다')


if __name__ == '__main__':
    main()
