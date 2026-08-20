# 同写意专家智库 — Design System (Direction A「数据面板」, selected 2026-08-20)

## Product context
Internal expert directory for a Chinese pharma-industry conference organizer (同写意). Users: conference planners (admin / planner / intern), desktop-first, ~10 people, ~10,000 expert records. Jobs: find the right expert fast (facets, filters, natural-language ask), verify details, keep records clean (import, review AI extractions, merge duplicates), trust the history (field-level change log, recycle bin).

Key pages: 专家列表, 找专家, 专家详情, 资料录入/审核, 疑似重复, 操作历史, 回收站, 用户.

## Visual concept — dense power-user console
A graphite top bar, one teal accent, a left facet rail with counts, removable filter chips, and dense zebra tables. Engineering calm: nothing decorative, no illustration, no gradients, no shadows, no icon library (only a search glyph, simple arrows ▲▼⇅, × on chips, ▾ on dropdowns).

## Palette (use ONLY these)
- Graphite `#1F2A30` — top bar background, primary text on light surfaces uses `#1C2326`
- Teal `#0F8B8D` — the single accent: active nav underline, primary buttons, links, chip borders/text, sort arrows, recommendation reasons, "existing expert" notice
- Teal tint `#E6F4F4` — chip fill, info panels (e.g. 系统理解), selected facet background
- Surfaces: page `#F4F6F7`, cards white, zebra row `#F7F9FA`, facet rail `#F4F6F7`
- Text: `#1C2326` primary, `#6B7A80` muted/labels/table headers
- Borders `#E1E6E8`
- Success green `#2E7D4F` on `#E7F3EC` (new expert, restore); danger `#B3261E` on `#FBE9E7` (delete, deleted banner); diff: removed text red strike-through, added text green
- NO navy, NO amber/yellow, NO serif anywhere. Light mode only.

## Typography (system fonts only — no webfonts)
- Everything sans: "PingFang SC","Microsoft YaHei","Noto Sans CJK SC","Helvetica Neue",Arial,sans-serif. Names bold 600. Page title 1.35rem 700. Table header .74rem muted letter-spacing .06em.
- Mono for phone/email/years/usernames/rank numbers: Consolas, Menlo, monospace.
- Base 14–15px, line-height 1.5. Dense table rows 34–40px.

## Layout & components
- App shell: 56px graphite top bar — wordmark 同写意 专家智库 (teal 同写意 + white 专家智库, sans, wide tracking), horizontal nav 专家列表 / 找专家 / 资料录入 / 操作历史 / 管理 ▾ with a 2px teal underline on the active item; right: user name + 退出.
- 专家列表 has a 240px left facet rail (单位类型 / 常用标签 / 合作频次 with right-aligned mono counts). Other pages are full-width main (max ~1200px, padding 32px).
- Cards: white, 1px `#E1E6E8` border, radius 6, padding 20–24, no shadow.
- Buttons: teal primary (radius 4, 8/16 padding, white text), ghost (white, 1px border), danger (red). 
- Inputs: 1px border, radius 4, 36px tall, teal 2px focus ring. Labels small muted above.
- Chips: teal tint fill, teal text, 1px teal-ish border, radius 4, × to remove. Tag pills in tables: outlined, muted, radius 3.
- Tables: muted sticky header, zebra rows, hover tint, right-aligned mono numbers, bold names; sortable headers show ▲/▼ in teal when active and faint ⇅ when inactive.
- Expert header ("name card"): white card, bold 1.9rem sans name, org, title, then a 3-column key-value grid with small muted labels; right: teal 编辑资料 button + muted 来源/更新.
- Flash message: teal-tinted bar (red for errors). Empty states say what to do next.

## Copy
Chinese, plain. Buttons name the action (筛选 / 找专家 / 确认入库 / 恢复 / 编辑资料). No marketing language.
