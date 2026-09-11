-- 'AI 로판' 페르소나 전면 제거 (2026-09-11)
-- 코드(fetch_youtube.py / index.html)에서는 이미 제거됨. 이 파일은 DB 정리용.
--
-- ⚠️ 사이트에 박힌 anon key로는 DELETE가 안 됩니다 (RLS에 delete 정책 없음).
--    Supabase 대시보드 → SQL Editor에서 실행하세요.

-- 1) 지우기 전에 개수 확인
select count(*) as lopan_count from youtube_posts where persona = 'lopan';
-- 2026-09-11 기준 예상: 417개

-- 2) 삭제
delete from youtube_posts where persona = 'lopan';

-- 3) 남은 페르소나 확인 (meme / asmr / military 3개만 남아야 정상)
select persona, count(*) from youtube_posts group by persona order by count desc;
