-- 아카라이브 Ai영상 테이블
-- Supabase SQL Editor에서 실행하세요.

create table if not exists arca_posts (
  post_id     text primary key,           -- 아카 글번호 (예: 176375278)
  title       text not null,
  author      text,
  category    text default 'Ai영상',
  post_date   date,                        -- 작성일 (KST 기준 날짜)
  views       int  default 0,              -- 조회수
  rating      int  default 0,              -- 추천
  url         text,                        -- https://arca.live/b/aireal/<post_id>
  has_video   boolean default true,
  status      text default 'approved',     -- approved | deleted
  created_at  timestamptz default now()
);

create index if not exists arca_posts_date_idx on arca_posts (post_date desc);
create index if not exists arca_posts_status_idx on arca_posts (status);

-- RLS
alter table arca_posts enable row level security;

-- 익명 읽기 (사이트 표시용)
drop policy if exists "anon read arca_posts" on arca_posts;
create policy "anon read arca_posts" on arca_posts
  for select using (true);

-- 익명 삽입 (크롤러가 anon key로 저장)
drop policy if exists "anon insert arca_posts" on arca_posts;
create policy "anon insert arca_posts" on arca_posts
  for insert with check (true);

-- 로그인 사용자 수정 (관리자 삭제 = status 변경)
drop policy if exists "auth update arca_posts" on arca_posts;
create policy "auth update arca_posts" on arca_posts
  for update using (auth.role() = 'authenticated');
