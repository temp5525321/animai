"""incoming 폴더를 현재 판정 기준으로 재검수하고, 기준 미달을 걸러낸다.

★다운로드 직후에 한 번 더 돌린다.
  선별(pick)은 크롤 시점의 판정을 쓰는데, 규칙은 계속 고쳐진다.
  이 단계가 있으면 '규칙을 고친 뒤에도 옛 기준으로 들어온 것'이 남지 않는다.

기본은 격리(quarantine)다 — _rejected 폴더로 옮긴다. 되돌릴 수 있게.
  --delete 를 주면 실제로 지운다.

사용법:
  python verify_incoming.py [--dir <폴더>] [--delete] [--dry]
"""
import os
import io
import sys
import json
import glob
import shutil
import argparse
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = r'C:\AI_Production_System\reference_library\incoming'

# 판정 로직은 크롤러 것을 그대로 쓴다 (규칙이 한 곳에만 있게)
os.environ.setdefault('YOUTUBE_API_KEY', 'x')
os.environ.setdefault('REFS_DRY_RUN', '1')
_spec = importlib.util.spec_from_file_location('refs', os.path.join(HERE, 'fetch_refs.py'))
refs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(refs)


def judge_sidecar(d):
    """사이드카에 남은 제목·설명·태그로 다시 판정한다."""
    title = d.get('title', '')
    desc = d.get('description', '')
    tags = d.get('youtube_tags') or []
    engine = refs.detect_engine(f'{title} {desc} {" ".join(tags)}')
    kind = {'drama': 'drama', 'ad': 'ad_like'}.get(d.get('source_category_guess'), 'general')
    return refs.judge(kind, title, desc, tags, engine)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=DEFAULT_DIR)
    ap.add_argument('--delete', action='store_true', help='격리 대신 완전 삭제')
    ap.add_argument('--dry', action='store_true', help='판정만 출력')
    a = ap.parse_args()

    files = [f for f in glob.glob(os.path.join(a.dir, '*.json'))
             if not os.path.basename(f).startswith('_manifest')]
    if not files:
        print(f'{a.dir}: 검수할 사이드카가 없습니다.')
        return

    keep, drop = [], []
    skipped = 0
    for f in files:
        d = json.load(io.open(f, encoding='utf-8'))
        # ★Dan이 직접 지정한 것은 판정하지 않는다.
        #   사람이 이유가 있어 고른 것을 규칙으로 되돌리면 안 된다.
        #   실측 2026-09-15: 슈카월드 해설·아스트라 툴소개가 격리됐다. 둘 다 Dan 지정분.
        if d.get('manual_request'):
            keep.append((f, d, {'reject_reason': None}))
            skipped += 1
            continue
        j = judge_sidecar(d)
        bad = (not j['is_ai_generated_likely']) or j['pollution'] >= 0.35 \
            or j['is_tutorial_or_review_likely']
        (drop if bad else keep).append((f, d, j))

    print(f'검수 {len(files)}편 → 통과 {len(keep)} · 탈락 {len(drop)}'
          + (f' (수동 지정 {skipped}편은 판정 제외)' if skipped else ''))
    if drop:
        print('\n── 탈락 ──')
        for _, d, j in drop:
            print(f"  {d['title'][:52]}")
            print(f"     → {j['reject_reason'] or 'AI 제작 신호 없음'}")

    if a.dry or not drop:
        return

    qdir = os.path.join(a.dir, '_rejected')
    if not a.delete:
        os.makedirs(qdir, exist_ok=True)

    moved = 0
    for f, d, j in drop:
        stem = os.path.splitext(os.path.basename(f))[0]
        # 같은 stem 을 가진 파일 전부 (mp4 · jpg · vtt · json)
        for g in glob.glob(os.path.join(a.dir, glob.escape(stem) + '.*')):
            if a.delete:
                os.remove(g)
            else:
                shutil.move(g, os.path.join(qdir, os.path.basename(g)))
            moved += 1

    print(f"\n{'삭제' if a.delete else '격리'}: 파일 {moved}개 ({len(drop)}편)")
    if not a.delete:
        print(f'격리 위치: {qdir}')


if __name__ == '__main__':
    main()
