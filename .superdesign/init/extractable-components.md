# Extractable components

## Sidebar
- Source: `app/templates/base.html` (`<aside>` + `.brand/.navg/.me` CSS)
- Category: layout
- Description: navy left sidebar — brand mark (amber square + serif 同写意专家智库 / EXPERT DIRECTORY), three labeled nav groups, amber count badges, user footer
- Extractable props: activeItem (string, "专家列表"), role (admin|planner|intern, "admin"), pendingDocs (number, 0), pendingDup (number, 0), trash (number, 0), userName (string, "admin")
- Hardcoded: nav labels, group titles, CSS, brand text

## NameBadge
- Source: `app/templates/detail.html` (`.badge`)
- Category: basic
- Description: conference-badge expert header: serif name, org, title, kv grid, right meta + edit button
- Extractable props: name, org, title, field, phone, email, wechat, tags[], source, updated

## FilterBar
- Source: `app/templates/index.html` (`.filters`)
- Category: basic
- Description: auto-fit grid of labeled inputs + 筛选/清除 + result count

## Pager
- Source: `app/templates/_pager.html`
- Category: basic
- Description: count + first/prev/numbers/next/last ghost buttons + jump form
