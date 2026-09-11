-- AI 드라마 / AI 광고 레퍼런스 테이블 (v2)
-- Supabase SQL Editor에서 실행하세요.
--
-- ★ v1 테이블과 데이터를 통째로 갈아엎습니다.
--   v1의 510개는 "AI에 관한 영상"과 "AI로 만든 영상"이 섞여 있어 폐기합니다.
--
-- v2의 핵심 변화
--   1) is_ai_generated_likely / is_ai_topic_only_likely 를 분리해 기록
--      → 오염된 후보를 버리지 않고 "왜 아닌지"를 남긴다. 기준이 바뀌어도 재수집 불필요
--   2) 원시 지표 + 파생 지표 + quality_score 를 모두 저장
--      → scoring_version / score_breakdown 이 있어 가중치만 바꿔 재계산 가능
--   3) channel_subs 로 view_sub_ratio 계산 → 대형 채널 독식 방지

drop table if exists youtube_refs;

create table youtube_refs (
  video_id        text primary key,
  kind            text not null,              -- drama | ad
  lane            text,                       -- generated | engine | festival
  tier            text,                       -- primary | secondary (길이 기준 우선순위)
  search_keyword  text,

  title           text not null,
  channel         text,
  channel_id      text,
  channel_subs    bigint  default 0,
  published_at    date,

  duration_sec    int     default 0,
  is_vertical     boolean default false,

  -- 원시 지표 (합산하지 않고 그대로 둔다)
  views           bigint  default 0,
  likes           bigint  default 0,
  comments        bigint  default 0,
  age_days        int     default 0,

  -- 파생 지표
  like_rate       numeric default 0,          -- 좋아요 / 조회수
  comment_rate    numeric default 0,          -- 댓글 / 조회수
  view_sub_ratio  numeric default 0,          -- ★조회수 / 구독자 = 작은 채널의 대박
  views_per_day   numeric default 0,          -- 조회수 / 경과일

  -- ★핵심 판정: AI로 만든 것 vs AI에 관한 것
  is_ai_generated_likely       boolean default false,
  is_ai_topic_only_likely      boolean default false,
  is_tutorial_or_review_likely boolean default false,
  is_series_likely             boolean default false,
  reject_reason                text,

  -- 점수 (전부 후처리 재계산 가능)
  ai_signal_score      numeric default 0,
  pollution_risk_score numeric default 0,
  keyword_match_score  numeric default 0,
  quality_score        numeric default 0,
  scoring_version      text,
  score_breakdown      jsonb,

  -- 댓글 반응 (상위 후보에만 수행)
  comment_status  text default 'not_checked',  -- not_checked | ok | disabled | failed
  comment_signal  numeric default 0,

  engine          text,
  engine_raw      text,

  thumb           text,
  url             text,
  embed_url       text,
  embeddable      boolean default true,
  description     text,
  tags            text[],

  status          text default 'approved',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

create index youtube_refs_kind_idx      on youtube_refs (kind);
create index youtube_refs_quality_idx   on youtube_refs (quality_score desc);
create index youtube_refs_aigen_idx     on youtube_refs (is_ai_generated_likely);
create index youtube_refs_vsr_idx       on youtube_refs (view_sub_ratio desc);
create index youtube_refs_views_idx     on youtube_refs (views desc);
create index youtube_refs_engage_idx    on youtube_refs (like_rate desc);
create index youtube_refs_duration_idx  on youtube_refs (duration_sec);
create index youtube_refs_engine_idx    on youtube_refs (engine);

alter table youtube_refs enable row level security;

drop policy if exists "anon read youtube_refs" on youtube_refs;
create policy "anon read youtube_refs" on youtube_refs
  for select using (true);

drop policy if exists "anon insert youtube_refs" on youtube_refs;
create policy "anon insert youtube_refs" on youtube_refs
  for insert with check (true);

drop policy if exists "auth update youtube_refs" on youtube_refs;
create policy "auth update youtube_refs" on youtube_refs
  for update using (auth.role() = 'authenticated');

-- ※ DELETE 정책은 일부러 열지 않았습니다.
--   anon 키는 index.html에 공개되어 있어, 열면 누구나 테이블을 비울 수 있습니다.
--   삭제는 service_role 키 또는 이 SQL Editor로만 하세요.
