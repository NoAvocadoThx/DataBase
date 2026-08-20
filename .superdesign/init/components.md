# Components

No component framework — primitives are CSS classes in `base.html` (full CSS in theme.md):

- `.card` white panel, 1px border, radius 10 (tables sit in a `.card` with `padding:0 0 12px`)
- `.badge` "conference name-badge" header on expert detail + login: 6px navy top border, serif `.nm` 2.1rem, `.org`, `.ttl`, `.kv` grid, right `.side`
- `.tag` / `.tag.warn` chip; also used as `<a>` for tag filters
- Buttons `button`/`.btn` navy, `.ghost` light, `.danger` red, `.mark` amber primary CTA
- `.filters` auto-fit grid of labeled inputs + submit row
- `.row` flex wrap gap 8; `.w` width 100%
- `td.num` mono cells; `td a.name` serif name link, amber highlighter on hover
- Sortable `<th>` macro `th(key,label)` in index.html: ▲/▼ + amber underline when active, grey ⇅ inactive; asc→desc→default
- `.msg` / `.msg.bad` flash; `.cand.new` / `.cand.dup` review cards; `.src` quoted source text

## Partials

### `app/templates/_pager.html`

```html
{# 分页条。变量 page, pages, found, params(dict) #}
{% if pages > 1 %}{% set qs = params|urlencode %}
<div class="row" style="justify-content:flex-end;margin-top:10px">
<span class="muted">共 {{found}} 条 · 第 {{page}}/{{pages}} 页</span>
{% if page > 1 %}<a class="btn ghost" href="?{{qs}}&page=1">首页</a><a class="btn ghost" href="?{{qs}}&page={{page-1}}">上一页</a>{% endif %}
{% for p in range([1, page-3]|max, [pages, page+3]|min + 1) %}
  {% if p == page %}<span class="btn">{{p}}</span>{% else %}<a class="btn ghost" href="?{{qs}}&page={{p}}">{{p}}</a>{% endif %}
{% endfor %}
{% if page < pages %}<a class="btn ghost" href="?{{qs}}&page={{page+1}}">下一页</a><a class="btn ghost" href="?{{qs}}&page={{pages}}">末页</a>{% endif %}
<form class="row" style="gap:4px">{% for k, v in params.items() %}<input type="hidden" name="{{k}}" value="{{v}}">{% endfor %}
<input name="page" type="number" min="1" max="{{pages}}" value="{{page}}" style="width:64px" aria-label="页码"><button class="ghost">跳转</button></form>
</div>{% endif %}

```

### `app/templates/_log_rows.html`

```html
{# 复用：历史记录表格行。变量 logs, labels, show_expert #}
{% for c in logs %}<tr>
<td class="muted" style="white-space:nowrap">{{c.created_at.strftime('%Y-%m-%d %H:%M')}}</td>
<td>{{c.actor}}</td>
{% if show_expert %}<td><a href="/expert/{{c.expert_id}}">{{c.expert_name}}</a></td>{% endif %}
<td><span class="tag" style="background:{% if c.action in ['delete','purge'] %}#fee2e2;color:#991b1b{% elif c.action in ['create','restore'] or (c.action in ['import','approve'] and not c.is_diff) %}#dcfce7;color:#166534{% else %}#e0ecff;color:#1d4ed8{% endif %}">{{c.action_label}}</span></td>
<td>{% if c.summary %}<div class="muted">{{c.summary}}</div>{% endif %}
{% set d = c.diff %}
{% if c.action in ['meeting_add','meeting_del'] %}
  <div>{{d['会议']}}{% if d['年份'] %} {{d['年份']}}{% endif %}{% if d['角色'] %} · {{d['角色']}}{% endif %}{% if d['主题'] %} · {{d['主题']}}{% endif %}</div>
{% elif c.is_diff %}
  {% for k, v in d.items() %}<div><b>{{labels.get(k, k)}}</b>：<span style="color:#991b1b;text-decoration:line-through">{{v[0] or '（空）'}}</span> → <span style="color:#166534">{{v[1] or '（空）'}}</span></div>{% endfor %}
{% elif d %}
  <details><summary class="muted">当时的完整数据</summary>{% for k, v in d.items() %}{% if v %}<div><b>{{labels.get(k, k)}}</b>：{{v}}</div>{% endif %}{% endfor %}</details>
{% endif %}</td></tr>
{% else %}<tr><td colspan="{{ 5 if show_expert else 4 }}" class="muted">暂无记录</td></tr>{% endfor %}

```

