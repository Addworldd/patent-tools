# 云端批注表结构

## 表：`annotations`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | bigint / uuid | 主键，自增即可 |
| `doc_id` | text | 文档标识，如 `US20160183596A1`，同一环境下可存多份文档 |
| `mark_key` | text | **唯一键**，格式 `段号\|侧\|起\|止`，如 `0054\|en\|31\|207` |
| `para_no` | text | 段号（四位），如 `0054` |
| `side` | text | `en` 或 `zh` |
| `start_off` | int4 | 起始偏移（纯文本字符偏移） |
| `end_off` | int4 | 结束偏移 |
| `sel_text` | text | 被选中的文字（冗余存一份，脱离原文也能读） |
| `note` | text | 批注内容 |
| `author` | text | 批注人昵称，可匿名 |
| `created` | timestamptz | 默认 `now()` |
| `updated` | timestamptz | 每次写入更新 |

## 建表 SQL

```sql
create table if not exists annotations (
  id          bigserial primary key,
  doc_id      text        not null,
  mark_key    text        not null,
  para_no     text        not null,
  side        text        not null check (side in ('en','zh')),
  start_off   int4        not null,
  end_off     int4        not null,
  sel_text    text        default '',
  note        text        default '',
  author      text        default '',
  created     timestamptz default now(),
  updated     timestamptz default now(),
  unique (mark_key)
);

create index if not exists idx_anno_doc_para on annotations (doc_id, para_no);
create index if not exists idx_anno_doc_updated on annotations (doc_id, updated desc);
```

## 关键设计

### 为什么用 `mark_key` 而不用自增 id 当业务键

页面新增高亮时是「先本地渲染、后异步写入」。如果等云端返回 id，界面会卡住。
用 `段号|侧|起|止` 作为业务唯一键，前端可以立即生成 key 并渲染，
写入时用 `upsert(onConflict: mark_key)` 幂等处理，重复提交不会产生两条。

### 为什么用纯文本偏移而非 HTML 偏移

页面渲染会为每个高亮插入 `<mark>` 标签。若存 HTML 偏移，
新增一条高亮会让它后面所有高亮的偏移值全部失效（差了 `<mark>` 那几十个字符）。
用**纯文本字符偏移**，位置与高亮数量无关，增删批注互不影响。

### 访问控制（重要）

本 skill 默认表对匿名用户开放读写，适合「发给同事内部评审」。
若文档涉及客户机密，必须收紧：

- 方案 A：开启云服务 Auth，要求登录后读，`author` 取自登录身份
- 方案 B：给文档加 `access_token` 字段，URL 带 token 才能访问对应 `doc_id`

未做访问控制时**不要**把链接发到公开渠道。

## 权限（RLS）

若启用行级安全，最小可用策略：

```sql
-- 读：默认放开
create policy anno_read on annotations for select using (true);

-- 写：只允许 insert / update 自己的行，需配合 auth.uid()
create policy anno_write on annotations for insert with check (true);
create policy anno_update on annotations for update using (true);
```

简化部署时可先全放开，交付前按上述说明收紧。
