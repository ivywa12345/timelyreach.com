-- TimelyReach 论坛数据库结构（Supabase / Postgres）
-- 用法：Supabase 控制台 -> SQL Editor -> 粘贴整个文件 -> Run。可重复执行。
--
-- 设计：
--   * 用户注册（邮箱或 GitHub）后自动创建 profiles 记录。
--   * 用 GitHub 账号 ivywa12345 登录的用户自动成为站长（admin）。
--     （只认 GitHub 登录，邮箱注册不能冒充。改站长账号请改下面的 'ivywa12345'。）
--   * 积分：发主题 +5，跟帖 +1；等级由积分在前端计算。
--   * 新闻帖是静态页面，跟帖通过 thread_key 关联：'t:<日期/编号>'；网友帖为 'u:<id>'。
--   * 所有表开启行级安全（RLS），前端只用公开的 anon key。

-- ---------- 表 ----------
create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  username text unique not null,
  display_name text not null,
  avatar_url text,
  bio text default '',
  role text not null default 'member' check (role in ('member', 'moderator', 'admin')),
  points integer not null default 0,
  post_count integer not null default 0,
  comment_count integer not null default 0,
  banned boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.threads (
  id bigint generated always as identity primary key,
  board text not null check (board in ('chat', 'invest', 'career', 'life')),
  title text not null check (char_length(title) between 4 and 80),
  body text not null check (char_length(body) between 10 and 20000),
  author_id uuid not null default auth.uid() references public.profiles (id) on delete cascade,
  pinned boolean not null default false,
  locked boolean not null default false,
  deleted boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists threads_board_idx on public.threads (board, pinned desc, created_at desc) where not deleted;
create index if not exists threads_author_idx on public.threads (author_id, created_at desc);

create table if not exists public.comments (
  id bigint generated always as identity primary key,
  thread_key text not null check (thread_key ~ '^(t:[0-9/]+/[0-9a-f]{10}|u:[0-9]+)$'),
  author_id uuid not null default auth.uid() references public.profiles (id) on delete cascade,
  body text not null check (char_length(body) between 1 and 5000),
  reply_to bigint references public.comments (id) on delete set null,
  deleted boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists comments_thread_idx on public.comments (thread_key, id);

create table if not exists public.thread_stats (
  thread_key text primary key,
  view_count integer not null default 0,
  reply_count integer not null default 0,
  last_reply_at timestamptz
);
create index if not exists thread_stats_hot_idx on public.thread_stats (last_reply_at desc);

-- ---------- 工具函数 ----------
create or replace function public.is_moderator() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from profiles where id = auth.uid() and role in ('moderator', 'admin'));
$$;

create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from profiles where id = auth.uid() and role = 'admin');
$$;

create or replace function public.is_active_member() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from profiles where id = auth.uid() and not banned);
$$;

-- 防冒充：非管理员的昵称不能用站长账号名或"站长/版主/管理员/admin"等字样
create or replace function public.safe_display_name(wanted text, fallback text, staff boolean) returns text
language sql immutable as $$
  select case
    when wanted is null or char_length(trim(wanted)) < 1 then fallback
    when not staff and (lower(trim(wanted)) like '%ivywa12345%' or trim(wanted) ~* '(站长|版主|管理员|admin|moderator)') then fallback
    else left(trim(wanted), 20)
  end;
$$;

-- 新用户 -> profile
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  provider text := coalesce(new.raw_app_meta_data ->> 'provider', 'email');
  gh_login text := new.raw_user_meta_data ->> 'user_name';
  base_name text;
  final_name text;
begin
  if provider = 'github' and gh_login is not null then
    base_name := gh_login;
  else
    base_name := coalesce(nullif(new.raw_user_meta_data ->> 'nickname', ''), split_part(new.email, '@', 1), 'user');
  end if;
  base_name := left(regexp_replace(base_name, '[^A-Za-z0-9_一-龥-]', '', 'g'), 20);
  if char_length(base_name) < 2 then base_name := 'user'; end if;
  if not (provider = 'github' and lower(gh_login) = lower('ivywa12345'))
     and (lower(base_name) like '%ivywa12345%' or base_name ~* '(站长|版主|管理员|admin|moderator)') then
    base_name := 'user';
  end if;
  final_name := base_name;
  while exists (select 1 from profiles where username = final_name) loop
    final_name := base_name || floor(random() * 10000)::int;
  end loop;

  insert into profiles (id, username, display_name, avatar_url, role)
  values (
    new.id,
    final_name,
    public.safe_display_name(
      coalesce(nullif(new.raw_user_meta_data ->> 'nickname', ''), new.raw_user_meta_data ->> 'name', final_name),
      '网友' || substr(replace(new.id::text, '-', ''), 1, 6),
      provider = 'github' and lower(gh_login) = lower('ivywa12345')
    ),
    new.raw_user_meta_data ->> 'avatar_url',
    case when provider = 'github' and lower(gh_login) = lower('ivywa12345') then 'admin' else 'member' end
  );
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- 普通用户只能改昵称/签名/头像，不能给自己加积分或升级
create or replace function public.guard_profile_update() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  -- SQL 控制台（无登录用户）和积分触发器（设置了 tr.internal）可以改任何字段
  if auth.uid() is null or current_setting('tr.internal', true) = '1' then return new; end if;
  if not public.is_admin() then
    new.role := old.role;
    new.banned := old.banned;
    new.points := old.points;
    new.post_count := old.post_count;
    new.comment_count := old.comment_count;
  end if;
  new.id := old.id;
  new.username := old.username;
  new.created_at := old.created_at;
  new.display_name := public.safe_display_name(new.display_name, old.display_name, old.role = 'admin');
  new.bio := left(coalesce(new.bio, ''), 120);
  return new;
end $$;

drop trigger if exists profiles_guard on public.profiles;
create trigger profiles_guard before update on public.profiles
  for each row execute function public.guard_profile_update();

-- 防刷屏：每分钟最多 5 条跟帖，每 10 分钟最多 3 个主题
create or replace function public.rate_limit() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if tg_table_name = 'comments' then
    if (select count(*) from comments where author_id = new.author_id and created_at > now() - interval '1 minute') >= 5 then
      raise exception '跟帖太快了，请稍后再试';
    end if;
    new.deleted := false;
  else
    if (select count(*) from threads where author_id = new.author_id and created_at > now() - interval '10 minutes') >= 3 then
      raise exception '发帖太频繁了，请稍后再试';
    end if;
    if not public.is_moderator() then
      new.pinned := false;
      new.locked := false;
    end if;
    new.deleted := false;
  end if;
  return new;
end $$;

drop trigger if exists comments_rate on public.comments;
create trigger comments_rate before insert on public.comments for each row execute function public.rate_limit();
drop trigger if exists threads_rate on public.threads;
create trigger threads_rate before insert on public.threads for each row execute function public.rate_limit();

-- 作者只能改标题/正文，置顶/锁帖/删除只有版主能改（作者可删自己的帖）
create or replace function public.guard_thread_update() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if not public.is_moderator() then
    new.pinned := old.pinned;
    new.locked := old.locked;
    if old.deleted then new.deleted := true; end if;
  end if;
  new.author_id := old.author_id;
  new.board := case when public.is_moderator() then new.board else old.board end;
  new.created_at := old.created_at;
  new.updated_at := now();
  return new;
end $$;

drop trigger if exists threads_guard on public.threads;
create trigger threads_guard before update on public.threads
  for each row execute function public.guard_thread_update();

create or replace function public.guard_comment_update() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  -- 跟帖只能被"删除"（作者或版主），不能修改内容，避免改楼
  new.body := old.body;
  new.thread_key := old.thread_key;
  new.author_id := old.author_id;
  new.created_at := old.created_at;
  if old.deleted then new.deleted := true; end if;
  return new;
end $$;

drop trigger if exists comments_guard on public.comments;
create trigger comments_guard before update on public.comments
  for each row execute function public.guard_comment_update();

-- 积分与统计
create or replace function public.on_thread_insert() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  perform set_config('tr.internal', '1', true);
  update profiles set points = points + 5, post_count = post_count + 1 where id = new.author_id;
  insert into thread_stats (thread_key, last_reply_at) values ('u:' || new.id, new.created_at)
    on conflict (thread_key) do nothing;
  perform set_config('tr.internal', '0', true);
  return new;
end $$;

drop trigger if exists threads_after_insert on public.threads;
create trigger threads_after_insert after insert on public.threads
  for each row execute function public.on_thread_insert();

create or replace function public.on_comment_insert() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  perform set_config('tr.internal', '1', true);
  update profiles set points = points + 1, comment_count = comment_count + 1 where id = new.author_id;
  insert into thread_stats (thread_key, reply_count, last_reply_at) values (new.thread_key, 1, new.created_at)
    on conflict (thread_key) do update set reply_count = thread_stats.reply_count + 1, last_reply_at = excluded.last_reply_at;
  perform set_config('tr.internal', '0', true);
  return new;
end $$;

drop trigger if exists comments_after_insert on public.comments;
create trigger comments_after_insert after insert on public.comments
  for each row execute function public.on_comment_insert();

-- 浏览数 +1（任何人可调用）
create or replace function public.bump_view(key text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if key !~ '^(t:[0-9/]+/[0-9a-f]{10}|u:[0-9]+)$' then return; end if;
  insert into thread_stats (thread_key, view_count) values (key, 1)
    on conflict (thread_key) do update set view_count = thread_stats.view_count + 1;
end $$;
grant execute on function public.bump_view(text) to anon, authenticated;

-- ---------- 行级安全 ----------
alter table public.profiles enable row level security;
alter table public.threads enable row level security;
alter table public.comments enable row level security;
alter table public.thread_stats enable row level security;

drop policy if exists "profiles readable" on public.profiles;
create policy "profiles readable" on public.profiles for select using (true);
drop policy if exists "profiles self update" on public.profiles;
create policy "profiles self update" on public.profiles for update
  using (id = auth.uid() or public.is_admin());

drop policy if exists "threads readable" on public.threads;
create policy "threads readable" on public.threads for select using (not deleted or public.is_moderator());
drop policy if exists "threads insert" on public.threads;
create policy "threads insert" on public.threads for insert to authenticated
  with check (author_id = auth.uid() and public.is_active_member());
drop policy if exists "threads update" on public.threads;
create policy "threads update" on public.threads for update to authenticated
  using ((author_id = auth.uid() and public.is_active_member()) or public.is_moderator());

drop policy if exists "comments readable" on public.comments;
create policy "comments readable" on public.comments for select using (not deleted or public.is_moderator());
drop policy if exists "comments insert" on public.comments;
create policy "comments insert" on public.comments for insert to authenticated
  with check (
    author_id = auth.uid()
    and public.is_active_member()
    and (
      thread_key like 't:%'
      or exists (select 1 from threads t where 'u:' || t.id = thread_key and not t.locked and not t.deleted)
    )
  );
drop policy if exists "comments update" on public.comments;
create policy "comments update" on public.comments for update to authenticated
  using (author_id = auth.uid() or public.is_moderator());

drop policy if exists "stats readable" on public.thread_stats;
create policy "stats readable" on public.thread_stats for select using (true);
