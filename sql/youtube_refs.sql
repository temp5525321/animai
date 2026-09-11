-- AI 드라마 / AI 광고 레퍼런스 테이블
-- 목적: 제작 참고용 레퍼런스 수집 (페르소나 큐레이션과 성격이 다름 → 별도 테이블)
-- Supabase SQL Editor에서 실행하세요.

create table if not exists youtube_refs (
  video_id     text primary key,
  kind         text not null,               -- drama | ad
  lane         text,                        -- topic | festival | making (어느 갈래로 걸렸나)

  title        text not null,
  channel      text,
  channel_id   text,
  published_at date,

  -- 길이 / 형태 (쇼츠 판별 + 광고 초수 분석용)
  duration_sec int  default 0,              -- 영상 길이(초)
  is_vertical  boolean default false,       -- 세로 영상 = 쇼츠 계열
  is_short     boolean default false,       -- 최종 쇼츠 판정 결과

  -- 원시 지표 (합산 점수를 만들지 않는다. 고르는 축은 사용자가 정한다)
  views        bigint default 0,
  likes        bigint default 0,
  comments     bigint default 0,
  age_days     int    default 0,            -- 업로드 후 경과일 (수집 시점)
  engagement   numeric default 0,           -- (좋아요*0.6 + 댓글*0.4) / 조회수
  velocity     numeric default 0,           -- 조회수 / 경과일

  -- 어떤 엔진으로 만들었나 (하드코딩 목록에 의존하지 않는다)
  engine       text,                        -- 알려진 엔진과 매칭된 결과
  engine_raw   text,                        -- "made with ___" 류에서 통째로 캡처한 원문

  thumb        text,
  url          text,
  embed_url    text,
  embeddable   boolean default true,        -- 임베드 불가여도 분석 가치가 있어 저장은 한다
  keyword      text,                        -- 걸린 검색어
  description  text,
  tags         text[],

  status       text default 'approved',
  created_at   timestamptz default now(),
  updated_at   timestamptz default now()
);

create index if not exists youtube_refs_kind_idx     on youtube_refs (kind);
create index if not exists youtube_refs_views_idx    on youtube_refs (views desc);
create index if not exists youtube_refs_engage_idx   on youtube_refs (engagement desc);
create index if not exists youtube_refs_velocity_idx on youtube_refs (velocity desc);
create index if not exists youtube_refs_duration_idx on youtube_refs (duration_sec);
create index if not exists youtube_refs_engine_idx   on youtube_refs (engine);

-- RLS
alter table youtube_refs enable row level security;

-- 익명 읽기 (사이트 표시용)
drop policy if exists "anon read youtube_refs" on youtube_refs;
create policy "anon read youtube_refs" on youtube_refs
  for select using (true);

-- 익명 삽입 (크롤러가 anon key만 가진 경우의 폴백)
drop policy if exists "anon insert youtube_refs" on youtube_refs;
create policy "anon insert youtube_refs" on youtube_refs
  for insert with check (true);

-- 로그인 사용자 수정
drop policy if exists "auth update youtube_refs" on youtube_refs;
create policy "auth update youtube_refs" on youtube_refs
  for update using (auth.role() = 'authenticated');

-- ※ 조회수 갱신(upsert)은 service_role 키로 실행할 때만 동작합니다.
--   anon 키에 update 권한을 열면 사이트 방문자 누구나 행을 고쳐쓸 수 있어
--   (anon 키는 index.html에 공개되어 있음) 권장하지 않습니다.
--   크롤러는 SUPABASE_SERVICE_KEY가 있으면 upsert, 없으면 insert-only로 자동 폴백합니다.
