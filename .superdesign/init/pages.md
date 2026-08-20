# Pages — dependency trees

All pages: `base.html` (layout + all CSS) → page template; no further imports.

## index.html
Entry: `app/templates/index.html`
Dependencies:
- `app/templates/base.html`
- `app/templates/_pager.html`

### `app/templates/index.html`

```html
{% extends "base.html" %}{% block body %}
<div class="row" style="justify-content:space-between;margin-bottom:14px"><h2 style="margin:0">专家列表</h2>
{% if sess.role in ['admin','planner'] %}<a class="btn" href="/expert/new">+ 新增专家</a>{% endif %}</div>
<div class="card"><form class="filters">
<div style="grid-column:span 2"><label>关键词 <span class="muted">空格分隔多个，全部满足</span></label><input name="q" value="{{f.q}}" placeholder="姓名 / 单位 / 职务 / 方向 / 简介" class="w"></div>
<div><label>单位</label><input name="org" value="{{f.org}}" class="w"></div>
<div><label>职务 / 职称</label><input name="title" value="{{f.title}}" list="titles" class="w">
<datalist id="titles"><option>教授<option>副教授<option>主任医师<option>副主任医师<option>研究员<option>审评员<option>总裁<option>首席科学家</datalist></div>
<div><label>研究方向</label><input name="field" value="{{f.field}}" class="w"></div>
<div><label>标签 <span class="muted">逗号分隔，全部满足</span></label><input name="tag" value="{{f.tag}}" list="taglist" class="w">
<datalist id="taglist">{% for t in tags %}<option value="{{t.name}}">{% endfor %}</datalist></div>
<div><label>合作记录</label><select name="meeting" class="w"><option value="">不限</option><option value="yes" {% if f.meeting=='yes' %}selected{% endif %}>有合作</option><option value="no" {% if f.meeting=='no' %}selected{% endif %}>无合作</option></select></div>
<div><label>排序</label><select name="sort" class="w">
<option value="updated" {% if f.sort=='updated' %}selected{% endif %}>最近更新</option><option value="created" {% if f.sort=='created' %}selected{% endif %}>最近新增</option>
{% for k, v in [('name','姓名'),('org','单位'),('title','职务'),('field','研究方向'),('tags','标签'),('phone','手机'),('meetings','合作次数')] %}<option value="{{k}}" {% if f.sort==k %}selected{% endif %}>{{v}}</option>{% endfor %}</select>
<input type="hidden" name="dir" value="{{f.dir}}"></div>
<div class="row"><button>筛选</button><a class="btn ghost" href="/">清除</a></div>
</form>
<p class="muted" style="margin:12px 0 0">库中 {{total}} 位 · 符合条件 <b style="color:var(--ink)">{{found}}</b> 位</p></div>
{% macro th(key, label) -%}
{% set active = f.sort == key %}{% set first = 'desc' if key == 'meetings' else 'asc' %}{% set second = 'asc' if first == 'desc' else 'desc' %}
{% if not active %}{% set href = '?' ~ filters|urlencode ~ '&sort=' ~ key ~ '&dir=' ~ first %}{% set tip = '点击排序' %}
{% elif f.dir == first %}{% set href = '?' ~ filters|urlencode ~ '&sort=' ~ key ~ '&dir=' ~ second %}{% set tip = '再点反向' %}
{% else %}{% set href = '?' ~ filters|urlencode %}{% set tip = '再点恢复默认排序' %}{% endif %}
<th><a href="{{href}}" class="{{'on' if active}}" title="{{tip}}">{{label}}{% if active %} {{ '▲' if f.dir == 'asc' else '▼' }}{% else %} <span class="dim">⇅</span>{% endif %}</a></th>
{%- endmacro %}
<div class="card" style="padding:0 0 12px"><table><tr>{{th('name','姓名')}}{{th('org','单位')}}{{th('title','职务')}}{{th('field','研究方向')}}{{th('tags','标签')}}{{th('phone','手机')}}{{th('meetings','合作')}}</tr>
{% for e in experts %}<tr><td><a class="name" href="/expert/{{e.id}}">{{e.name}}</a></td><td>{{e.org}}</td><td>{{e.title}}</td>
<td>{{e.field}}</td><td>{% for t in e.tags %}<a class="tag" href="/?tag={{t.name}}">{{t.name}}</a>{% endfor %}</td><td class="num">{{e.phone}}</td><td>{{e.meetings|length}} 次</td></tr>
{% else %}<tr><td colspan="7" class="muted" style="padding:28px;text-align:center">暂无数据{% if params %}，试试放宽筛选条件{% elif sess.role=='admin' %}，先 <a href="/import">导入 Excel</a> 或 <a href="/documents">上传资料</a>{% endif %}</td></tr>{% endfor %}</table>
<div style="padding:0 14px">{% include "_pager.html" %}</div></div>
{% endblock %}

```

## detail.html
Entry: `app/templates/detail.html`
Dependencies:
- `app/templates/base.html`
- `app/templates/_log_rows.html`

### `app/templates/detail.html`

```html
{% extends "base.html" %}{% block body %}
{% if deleted %}<div class="msg bad">该专家已于 {{deleted.strftime('%Y-%m-%d %H:%M')}} 删除，目前在回收站中。
{% if sess.role=='admin' %}<form method="post" action="/expert/{{e.id}}/restore" style="display:inline;margin-left:8px"><button class="ghost">恢复</button></form>{% endif %}</div>{% endif %}

<div class="badge">
<div><div class="nm">{{e.name}}</div><div class="org">{{e.org or '单位未填写'}}</div><div class="ttl">{{e.title}}</div>
<div class="kv">
<b>研究方向</b><span>{{e.field or '—'}}</span>
<b>手机</b><span class="mono">{{e.phone or '—'}}</span>
<b>邮箱</b><span class="mono">{{e.email or '—'}}</span>
<b>微信</b><span class="mono">{{e.wechat or '—'}}</span>
<b>标签</b><span>{% for t in e.tags %}<a class="tag" href="/?tag={{t.name}}">{{t.name}}</a>{% else %}—{% endfor %}</span>
</div></div>
<div class="side">{% if sess.role in ['admin','planner'] and not deleted %}<a class="btn" href="/expert/{{e.id}}/edit">编辑资料</a><br><br>{% endif %}
来源 {{e.source or '—'}}<br>更新 {{e.updated_at.strftime('%Y-%m-%d %H:%M') if e.updated_at else ''}}</div>
</div>

<div class="card"><h3>简介</h3><p style="margin:0 0 10px">{{e.bio or '（暂无）'}}</p>
{% if can_sensitive and e.note %}<h4>内部备注</h4><p style="margin:0 0 10px">{{e.note}}</p>{% endif %}
{% if e.source_text %}<h4>录入时的原文</h4><div class="src">{{e.source_text}}</div>{% endif %}</div>

<div class="card"><h3>合作历史 <span class="muted">{{e.meetings|length}} 次</span></h3>
<table><tr><th>会议</th><th>年份</th><th>角色</th><th>报告主题</th><th></th></tr>
{% for m in e.meetings %}<tr><td>{{m.meeting}}</td><td class="num">{{m.year or ''}}</td><td>{{m.role}}</td><td>{{m.topic}}</td>
<td style="text-align:right">{% if sess.role in ['admin','planner'] %}<form method="post" action="/meeting/{{m.id}}/delete" onsubmit="return confirm('删除这条合作记录？（操作历史里会保留）')"><button class="ghost">删除</button></form>{% endif %}</td></tr>
{% else %}<tr><td colspan="5" class="muted">暂无合作记录</td></tr>{% endfor %}</table>
{% if sess.role in ['admin','planner'] and not deleted %}<form method="post" action="/expert/{{e.id}}/meeting" class="row" style="margin-top:12px">
<input name="meeting" placeholder="会议名称" required><input name="year" placeholder="年份" style="width:80px">
<input name="mrole" placeholder="角色（主席 / 报告人）"><input name="topic" placeholder="报告主题" style="width:240px"><button class="ghost">添加合作记录</button></form>{% endif %}</div>

{% if can_sensitive %}<div class="card"><h3>修改历史</h3><table><tr><th>时间</th><th>操作人</th><th>操作</th><th>内容</th></tr>
{% with show_expert=false %}{% include "_log_rows.html" %}{% endwith %}</table></div>{% endif %}
{% if sess.role=='admin' and not deleted %}<form method="post" action="/expert/{{e.id}}/delete" onsubmit="return confirm('删除后进入回收站，可以恢复。确定删除 {{e.name}}？')"><button class="danger">删除专家</button></form>{% endif %}
{% endblock %}

```

## ask.html
Entry: `app/templates/ask.html`
Dependencies:
- `app/templates/base.html`

### `app/templates/ask.html`

```html
{% extends "base.html" %}{% block body %}
<h2>找专家</h2>
<div class="card"><form class="row"><input name="q" value="{{q}}" style="flex:1;font-size:1.05rem;padding:10px 14px" placeholder="用一句话描述，例如：做 ADC 药物临床研究、参加过我们会议的专家" autofocus><button class="mark" style="padding:10px 18px">找专家</button></form>
<p class="muted" style="margin:8px 0 0">{% if llm_on %}大模型已启用，会先理解你的问题再检索{% else %}未配置大模型，按关键词和标签匹配{% endif %}。结果附带推荐依据。</p></div>
{% if parsed %}<div class="card" style="background:#FFF6D6;border-color:#F1D98A"><b>系统理解：</b>{{parsed.explain}}
<div class="muted" style="margin-top:4px">关键词 {{parsed.keywords|join(' / ') or '—'}} · 标签 {{parsed.tags|join(' / ') or '—'}}{% if parsed.org %} · 单位 {{parsed.org}}{% endif %}{% if parsed.need_meeting %} · 要求有合作历史{% endif %}</div></div>
<div class="card" style="padding:0 0 6px"><table><tr><th style="width:40px">#</th><th>专家</th><th>单位 / 职务</th><th>研究方向</th><th>推荐依据</th></tr>
{% for e, pts, reasons in results %}<tr><td class="num">{{loop.index}}</td><td><a class="name" href="/expert/{{e.id}}">{{e.name}}</a></td>
<td>{{e.org}}<br><span class="muted">{{e.title}}</span></td><td>{{e.field}}</td><td class="reason">{{reasons|join('；')}}</td></tr>
{% else %}<tr><td colspan="5" class="muted" style="padding:28px;text-align:center">没有匹配的专家。换个说法，或者去 <a href="/">专家列表</a> 按条件筛选。</td></tr>{% endfor %}</table></div>{% endif %}
{% endblock %}

```

## review.html
Entry: `app/templates/review.html`
Dependencies:
- `app/templates/base.html`

### `app/templates/review.html`

```html
{% extends "base.html" %}{% block body %}
<h2>审核 <span class="muted" style="font-weight:400">{{doc.filename}} · {{cands|length}} 位候选 · {{ '待审核' if doc.status=='pending' else '已入库' }}</span></h2>
<form method="post" action="/documents/{{doc.id}}/approve"><input type="hidden" name="count" value="{{cands|length}}">
<div class="card"><div class="row"><div><label>本资料对应会议 <span class="muted">可选，填写后为勾选的专家添加合作记录</span></label>
<div class="row"><input name="meeting" placeholder="会议名称" style="width:320px"><input name="year" placeholder="年份" style="width:90px"></div></div></div></div>
{% for c in cands %}{% set i = loop.index0 %}<div class="cand {{'dup' if c.existing else 'new'}}">
<div class="row" style="justify-content:space-between"><label style="font-size:.95rem;color:var(--ink);margin:0"><input type="checkbox" name="accept_{{i}}" checked> <b>入库</b></label>
{% if c.existing %}<span class="tag warn">库中已有同名：{{c.existing|join('、')}}（单位一致则更新，否则新建并进疑似重复）</span>{% else %}<span class="tag" style="background:var(--ok-bg);color:var(--ok)">新专家</span>{% endif %}</div>
<div class="grid" style="margin-top:10px">
<div><label>姓名</label><input name="name_{{i}}" value="{{c.name}}" class="w" style="font-family:var(--serif);font-weight:600"></div>
<div><label>单位</label><input name="org_{{i}}" value="{{c.org}}" class="w"></div>
<div><label>职务</label><input name="title_{{i}}" value="{{c.title}}" class="w"></div>
<div><label>研究方向</label><input name="field_{{i}}" value="{{c.field}}" class="w"></div>
<div><label>邮箱</label><input name="email_{{i}}" value="{{c.email}}" class="w mono"></div>
<div><label>手机</label><input name="phone_{{i}}" value="{{c.phone}}" class="w mono"></div>
<div><label>报告主题</label><input name="topic_{{i}}" value="{{c.topic}}" class="w"></div>
<div><label>会议角色</label><input name="role_{{i}}" placeholder="报告人 / 主持 / 嘉宾" class="w"></div>
<div class="full"><label>标签 <span class="muted">逗号分隔，留空则不改</span></label><input name="tags_{{i}}" class="w"></div>
<div class="full"><label>来源原文</label><input name="source_text_{{i}}" value="{{c.source_text}}" class="w" style="font-size:.82rem;color:var(--ink-2)"></div></div></div>
{% else %}<div class="card muted">没有提取到候选专家。展开下方原文核对，或 <a href="/expert/new">手工新增</a>。</div>{% endfor %}
{% if cands and doc.status=='pending' %}<div class="card row"><button class="mark">确认入库勾选的专家</button><span class="muted">入库后可在专家详情的“修改历史”里看到来源</span></div>{% endif %}</form>
<div class="card"><details><summary class="muted">查看提取的原文</summary><div class="src" style="max-height:400px;overflow:auto;margin-top:8px">{{doc.text}}</div></details></div>
{% endblock %}

```

## login.html
Entry: `app/templates/login.html`
Dependencies:
- `app/templates/base.html`

### `app/templates/login.html`

```html
{% extends "base.html" %}{% block body %}
<div style="max-width:380px;margin:10vh auto 0">
<div class="badge" style="grid-template-columns:1fr;padding:28px 30px">
<div style="display:flex;align-items:center;gap:10px;margin-bottom:18px"><i style="width:12px;height:12px;background:var(--mark);display:inline-block;border-radius:2px"></i><span class="muted" style="letter-spacing:.14em">EXPERT DIRECTORY</span></div>
<div class="nm" style="font-size:1.7rem">同写意专家智库</div>
<div class="ttl" style="margin-bottom:22px">内部系统 · 请使用分配的账号登录</div>
<form method="post" style="display:grid;gap:10px"><input type="hidden" name="next" value="{{next}}">
<div><label for="u">用户名</label><input id="u" name="username" required class="w" autofocus></div>
<div><label for="p">密码</label><input id="p" name="password" type="password" required class="w"></div>
<button class="w" style="margin-top:6px">登录</button></form>
</div>
<p class="muted" style="text-align:center">忘记密码请联系管理员重置</p></div>
{% endblock %}

```

## history.html
Entry: `app/templates/history.html`
Dependencies:
- `app/templates/base.html`
- `app/templates/_pager.html`
- `app/templates/_log_rows.html`

### `app/templates/history.html`

```html
{% extends "base.html" %}{% block body %}
<h2>操作历史</h2>
<div class="card"><form class="filters">
<div><label>操作人</label><select name="actor" class="w"><option value="">全部</option>{% for a in actors %}<option {% if a==f.actor %}selected{% endif %}>{{a}}</option>{% endfor %}</select></div>
<div><label>操作类型</label><select name="action" class="w"><option value="">全部</option>{% for k, v in actions.items() %}<option value="{{k}}" {% if k==f.action %}selected{% endif %}>{{v}}</option>{% endfor %}</select></div>
<div><label>专家姓名</label><input name="name" value="{{f.name}}" class="w"></div>
<div><label>开始日期</label><input name="date_from" type="date" value="{{f.date_from}}" class="w"></div>
<div><label>结束日期</label><input name="date_to" type="date" value="{{f.date_to}}" class="w"></div>
<div class="row"><button>筛选</button><a class="btn ghost" href="/history">清除</a></div></form>
<p class="muted" style="margin:12px 0 0">符合条件 <b style="color:var(--ink)">{{found}}</b> 条</p></div>
<div class="card" style="padding:0 0 12px"><table><tr><th>时间</th><th>操作人</th><th>专家</th><th>操作</th><th>内容</th></tr>
{% with show_expert=true %}{% include "_log_rows.html" %}{% endwith %}</table>
<div style="padding:0 14px">{% include "_pager.html" %}</div></div>
{% endblock %}

```

