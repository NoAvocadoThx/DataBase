# Layouts

One shared layout: `app/templates/base.html`. Every page `{% extends "base.html" %}` and fills `{% block body %}`.

Renders: left sidebar `<aside>` (brand mark + grouped nav 查找/录入/管理 with amber count badges + user footer) and `<main>` (flash `.msg` + page body). Nav groups are role-gated: intern → 查找 only; planner adds 录入; admin adds 管理. Sidebar sticky 100vh; under 900px becomes a wrapping top bar.

Context on every page: `sess` ({uid, role, name}), `can_sensitive`, `pending_docs`, `pending_dup`, `trash`, `msg`.

### `app/templates/base.html`

```html
<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>同写意专家智库</title>
<style>
:root{
  --ink:#13294B; --ink-2:#27406B; --ink-soft:#5B6B85; --line:#D8DEE8; --paper:#FFFFFF; --ground:#EEF2F6;
  --mark:#F6C445; --mark-ink:#5A4300; --ok:#1F7A6E; --ok-bg:#E3F3F0; --bad:#B3261E; --bad-bg:#FBE9E7; --info-bg:#E8EEF8;
  --serif:"Noto Serif CJK SC","Source Han Serif SC","Songti SC","STSong","SimSun",Georgia,serif;
  --sans:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC","Helvetica Neue",Arial,sans-serif;
  --mono:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;
}
*{box-sizing:border-box}
html{font-size:15px}
body{margin:0;font-family:var(--sans);color:var(--ink);background:var(--ground);line-height:1.5;-webkit-font-smoothing:antialiased}
a{color:var(--ink-2)}
h1,h2,h3,h4{margin:0 0 .6em;line-height:1.25;font-weight:600}
h2{font-size:1.35rem}h3{font-size:1.05rem}h4{font-size:.95rem;color:var(--ink-soft)}

/* 布局：左侧栏 + 内容 */
.shell{display:grid;grid-template-columns:216px 1fr;min-height:100vh}
aside{background:var(--ink);color:#fff;padding:22px 14px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:18px}
.brand{display:flex;align-items:center;gap:10px;padding:0 8px 16px;border-bottom:1px solid rgba(255,255,255,.12)}
.brand i{width:12px;height:12px;background:var(--mark);display:inline-block;border-radius:2px;flex:none}
.brand b{font-family:var(--serif);font-weight:600;font-size:1.1rem;letter-spacing:.04em}
.brand small{display:block;font-size:.7rem;color:rgba(255,255,255,.55);letter-spacing:.14em;font-weight:400}
.navg{display:flex;flex-direction:column;gap:2px}
.navg span.t{font-size:.68rem;letter-spacing:.16em;color:rgba(255,255,255,.45);padding:6px 10px 4px}
.navg a{color:rgba(255,255,255,.86);text-decoration:none;padding:7px 10px;border-radius:6px;display:flex;align-items:center;gap:8px;font-size:.93rem}
.navg a:hover{background:rgba(255,255,255,.08)}
.navg a.on{background:rgba(255,255,255,.12);color:#fff;box-shadow:inset 3px 0 0 var(--mark)}
.navg .n{margin-left:auto;background:var(--mark);color:var(--mark-ink);font-size:.7rem;font-weight:700;border-radius:10px;padding:0 7px;line-height:1.5}
.me{margin-top:auto;border-top:1px solid rgba(255,255,255,.12);padding-top:12px;font-size:.85rem}
.me a{color:#fff;text-decoration:none}.me .r{color:rgba(255,255,255,.55);font-size:.75rem}
.me .out{display:block;color:rgba(255,255,255,.6);margin-top:4px;font-size:.8rem}
main{padding:28px 32px;max-width:1320px;width:100%}

/* 卡片与消息 */
.card{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:18px 22px;margin-bottom:18px}
.msg{background:#FFF6D6;border-left:4px solid var(--mark);padding:10px 14px;margin-bottom:18px;border-radius:6px}
.msg.bad{background:var(--bad-bg);border-color:var(--bad)}
.muted{color:var(--ink-soft);font-size:.86rem}
.reason{color:var(--ok);font-size:.86rem}
.src{background:var(--ground);border-left:3px solid var(--line);padding:8px 12px;font-size:.82rem;color:var(--ink-2);white-space:pre-wrap;border-radius:0 6px 6px 0}

/* 表格 */
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);font-size:.9rem;vertical-align:top}
th{font-size:.74rem;letter-spacing:.08em;color:var(--ink-soft);font-weight:600;background:var(--paper);position:sticky;top:0}
th a{color:inherit;text-decoration:none}
th a.on{color:var(--ink);box-shadow:inset 0 -2px 0 var(--mark)}
th .dim{color:#B8C2D1}
tr:hover td{background:#F7F9FC}
td.num{font-family:var(--mono);font-size:.86rem;letter-spacing:.02em}
td a.name{font-family:var(--serif);font-size:1rem;font-weight:600;color:var(--ink);text-decoration:none;white-space:nowrap}
td a.name:hover{background:linear-gradient(transparent 55%,var(--mark) 55%)}

/* 标签、按钮、表单 */
.tag{display:inline-block;background:var(--info-bg);color:var(--ink-2);border-radius:4px;padding:1px 7px;font-size:.76rem;margin:1px 2px 1px 0;text-decoration:none}
.tag.warn{background:#FFF6D6;color:var(--mark-ink)}
input,select,textarea{padding:7px 10px;border:1px solid var(--line);border-radius:6px;font:inherit;font-size:.92rem;background:#fff;color:var(--ink)}
input:focus,select:focus,textarea:focus,button:focus-visible,a:focus-visible{outline:2px solid var(--mark);outline-offset:1px}
textarea{width:100%}label{font-size:.78rem;color:var(--ink-soft);display:block;margin-bottom:3px}
button,.btn{padding:7px 14px;background:var(--ink);color:#fff;border:0;border-radius:6px;cursor:pointer;font:inherit;font-size:.9rem;text-decoration:none;display:inline-block;line-height:1.4}
button:hover,.btn:hover{background:var(--ink-2)}
button.danger{background:var(--bad)}button.danger:hover{background:#8F1D17}
button.ghost,.btn.ghost{background:var(--ground);color:var(--ink);border:1px solid var(--line)}button.ghost:hover,.btn.ghost:hover{background:#E3E9F1}
button.mark{background:var(--mark);color:var(--mark-ink)}button.mark:hover{background:#E9B52E}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 16px}.grid .full{grid-column:1/-1}
.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;align-items:end}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.w{width:100%}

/* 胸牌式详情头 */
.badge{display:grid;grid-template-columns:1fr auto;gap:8px 24px;align-items:start;padding:22px 26px;border-radius:10px;background:var(--paper);border:1px solid var(--line);border-top:6px solid var(--ink);margin-bottom:18px}
.badge .nm{font-family:var(--serif);font-size:2.1rem;font-weight:600;line-height:1.1;letter-spacing:.02em}
.badge .org{font-size:1.02rem;color:var(--ink-2);margin-top:4px}
.badge .ttl{font-size:.86rem;color:var(--ink-soft);letter-spacing:.06em}
.badge .kv{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:.9rem;margin-top:14px}
.badge .kv b{color:var(--ink-soft);font-weight:500;font-size:.8rem;letter-spacing:.06em}
.badge .side{text-align:right;font-size:.8rem;color:var(--ink-soft)}
.mono{font-family:var(--mono)}

/* 审核页 */
.cand{background:var(--ground);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:12px}
.cand.new{border-left:4px solid var(--ok)}.cand.dup{border-left:4px solid var(--mark)}

/* 窄屏 */
@media (max-width:900px){
  .shell{grid-template-columns:1fr}
  aside{position:static;height:auto;flex-direction:row;flex-wrap:wrap;gap:8px 14px;padding:12px 14px;align-items:center}
  .brand{border:0;padding:0}.navg{flex-direction:row;flex-wrap:wrap}.navg span.t{display:none}.me{margin:0;border:0;padding:0}
  main{padding:16px}.grid{grid-template-columns:1fr}.badge{grid-template-columns:1fr}.badge .side{text-align:left}
}
@media (prefers-reduced-motion:no-preference){button,.btn,.navg a{transition:background .12s}}
</style></head><body>
{% set path = request.url.path %}
{% macro nav(href, label, n=0) -%}<a href="{{href}}" class="{{'on' if path == href or (href != '/' and path.startswith(href)) else ''}}">{{label}}{% if n %}<span class="n">{{n}}</span>{% endif %}</a>{%- endmacro %}
<div class="shell">
{% if sess %}<aside>
<div class="brand"><i></i><div><b>同写意专家智库</b><small>EXPERT DIRECTORY</small></div></div>
<nav class="navg"><span class="t">查找</span>{{nav('/', '专家列表')}}{{nav('/ask', '找专家')}}</nav>
{% if sess.role in ['admin','planner'] %}<nav class="navg"><span class="t">录入</span>{{nav('/documents', '资料录入', pending_docs)}}{{nav('/expert/new', '手工新增')}}{{nav('/history', '操作历史')}}</nav>{% endif %}
{% if sess.role=='admin' %}<nav class="navg"><span class="t">管理</span>{{nav('/duplicates', '疑似重复', pending_dup)}}{{nav('/trash', '回收站', trash)}}{{nav('/import', 'Excel 导入')}}<a href="/export">导出 Excel</a>{{nav('/users', '用户')}}</nav>{% endif %}
<div class="me"><a href="/account">{{sess.name}}</a> <span class="r">{{ {'admin':'管理员','planner':'策划','intern':'实习生'}[sess.role] }}</span><a class="out" href="/logout">退出登录</a></div>
</aside>{% endif %}
<main>{% if msg %}<div class="msg{% if '错误' in msg or '失败' in msg or '不一致' in msg or '无权限' in msg %} bad{% endif %}">{{msg}}</div>{% endif %}{% block body %}{% endblock %}</main>
</div></body></html>

```

