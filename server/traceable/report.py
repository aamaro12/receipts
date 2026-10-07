"""Single-file HTML report. Standard library only.

One page per folder: every question asked there (newest first), each with its answer, its verdict and the proof behind
every figure. A denied draft says so, and reloads itself until the final answer is written."""
from __future__ import annotations
import datetime
import html
import pathlib
import re, json
from urllib.parse import urlparse
from .ledger import Ledger
from .numbers import normalise

MAX_VIEWS = 12

_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#16181d;--muted:#5f6673;--line:#e3e6eb;--link:#1e5bd8;--ok:#0a7d33;--calc:#1e5bd8;--assume:#8e44ad;--bad:#c62828;--accent:#fa500f;--hl:rgba(255,196,0,.35);--okbg:#e7f5ec;--badbg:#fdecec;--calcbg:#e8effd}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e8eaee;--muted:#9aa3b2;--line:#2a2f3a;--link:#8ab4ff;--ok:#4cc27a;--calc:#8ab4ff;--assume:#c39bd3;--bad:#ff6b6b;--hl:rgba(255,196,0,.45);--okbg:#16301f;--badbg:#3a1b1b;--calcbg:#1b2740}}
*{box-sizing:border-box}
body{font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0}
.top{display:flex;align-items:baseline;gap:14px;padding:14px 28px;background:var(--card);border-bottom:1px solid var(--line)}
.brand{font-weight:700;font-size:18px;letter-spacing:-.01em}.brand b{color:var(--accent)}
.sub{color:var(--muted);font-size:13px}
.layout{display:grid;grid-template-columns:230px minmax(0,1.15fr) minmax(0,1fr);gap:20px;padding:20px 28px;align-items:start}
nav.questions,.view,#detail{background:var(--card);border:1px solid var(--line);border-radius:12px}
nav.questions{padding:12px;position:sticky;top:16px}
nav.questions h2,#detail h2,.view h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:4px 4px 10px}
.qitem,h1.question{overflow-wrap:anywhere}
.qitem{display:block;width:100%;text-align:left;background:none;border:0;border-radius:8px;padding:8px;color:var(--fg);font:inherit;font-size:13px;cursor:pointer}
.qitem:hover{background:var(--bg)}.qitem.active{background:var(--calcbg)}
.qitem .t{color:var(--muted);font-size:11px;display:block}
.qitem .qtext{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-weight:500;line-height:1.4}
.qitem{margin-bottom:2px}.qitem .t{margin-bottom:2px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot.verified{background:var(--ok)}.dot.denied{background:var(--bad)}.dot.notchecked{background:var(--muted)}
.view{padding:22px 24px}
.qlabel{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
h1.question{font-size:18px;line-height:1.4;margin:2px 0 4px;font-weight:650}
.qfull{font-size:12.5px;line-height:1.5;color:var(--muted);margin:0 0 4px}
.verdictbar{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;padding:10px 14px;border-radius:10px;margin:14px 0 18px}
.verdictbar.verified{background:var(--okbg)}.verdictbar.denied{background:var(--badbg)}.verdictbar.notchecked{background:var(--line)}
.verdictbar .verdict{padding:0;background:none;font-size:14px}
.srcrow{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:16px;padding-top:12px;border-top:1px solid var(--line)}
.srcrow .qlabel{margin-right:4px}

.verdict{font-weight:700;font-size:13px;padding:4px 10px;border-radius:999px}
.verdict.verified{background:var(--okbg);color:var(--ok)}.verdict.denied{background:var(--badbg);color:var(--bad)}.verdict.notchecked{background:var(--line);color:var(--muted)}
.summary{font-size:14px;color:var(--fg)}
details.meta{margin-top:12px;font-size:12px;color:var(--muted)}details.meta summary{cursor:pointer}
.sources{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 16px}
.src{display:inline-block;font-size:12px;line-height:1.5;padding:3px 9px;border-radius:8px;background:var(--bg);border:1px solid var(--line);max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.src svg{color:var(--muted);vertical-align:-2px;margin-right:6px}.src .domain{color:var(--muted);margin-left:5px}.src .domain:before{content:"· "}
.src em{font-size:10px;color:var(--bad);font-style:normal;margin-left:4px}
.src b{font-size:10px;letter-spacing:.05em;color:var(--muted);margin-right:4px}
.answer{font-size:15px;line-height:1.65}.answer p{margin:0 0 10px}.answer ul,.answer ol{margin:0 0 10px;padding-left:22px}
.answer h3,.answer h4{margin:14px 0 6px}
.figure-chip{padding:1px 5px;border-radius:5px;cursor:pointer;border:1px solid;font-weight:600;white-space:nowrap}
.figure-chip:hover{filter:brightness(.95);box-shadow:0 0 0 2px var(--hl)}
.status-traced{border-color:var(--ok);background:var(--okbg)} .status-calculated{border-color:var(--calc);background:var(--calcbg)}
.status-calculated_with_assumption{border-color:var(--assume);color:var(--assume)}
.status-from_question{border-color:var(--muted);border-style:dashed}
.status-untraced,.status-phantom,.status-mismatch,.status-derived_needs_calc,.status-unparsed{border-color:var(--bad);color:var(--bad);background:var(--badbg)}
.cite{font-size:10.5px;color:var(--muted);font-family:ui-monospace,Menlo,monospace;vertical-align:1px;margin-left:2px;cursor:pointer;border-bottom:1px dotted transparent}
.cite:hover{color:var(--link);border-bottom-color:var(--link)}
.dtitle{font-size:18px;margin:6px 0 4px;font-weight:650}
.webcard{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0}
.wsite{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)}.wtitle{font-weight:600;margin-top:4px}.wwhere{font-size:12px;color:var(--muted);margin-top:2px}
.passage{white-space:pre-wrap;font-size:13.5px;line-height:1.6;background:var(--bg);border-left:3px solid var(--line);border-radius:6px;padding:10px 12px;margin-top:8px}
mark{background:var(--hl);color:inherit;padding:0 2px;border-radius:3px}
.banner{padding:10px 14px;border-radius:8px;font-weight:600;margin-bottom:14px} .not-checked{background:var(--bad);color:#fff}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:var(--muted);margin-top:12px}
.legend span{display:inline-flex;align-items:center;gap:5px}.legend i{width:10px;height:10px;border-radius:3px;border:1px solid;display:inline-block}
#detail{padding:18px 20px;position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto}
table.ev{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}table.ev td{overflow-wrap:break-word}
table.ev th:first-child{width:22%}table.ev th:nth-child(2){width:17%}table.inputs th:first-child{width:34%}table.inputs th:nth-child(2){width:24%}
table.ev td,table.ev th{border:0;border-bottom:1px solid var(--line);padding:7px 6px;text-align:left;vertical-align:top}
table.ev tr[data-i]{cursor:pointer}table.ev tr[data-i]:hover{background:var(--bg)}
.hint{color:var(--muted);font-size:13px;margin:0 0 12px}.back{font-size:13px;cursor:pointer;color:var(--link);background:none;border:0;padding:0;margin-bottom:10px}
.calc{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0}
.calc code{font-size:13px;overflow-wrap:anywhere}.clabel{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:6px 0 2px}
.muted{color:var(--muted);font-size:12.5px}.nw{white-space:nowrap}.sic svg{vertical-align:-3px;margin-right:7px;width:16px;height:16px}
table.ev .muted .sic svg{width:13px;height:13px;vertical-align:-2px}
.k-PDF{color:#d64545}.k-WEB{color:#2f6fe0}.k-CARD{color:#8e5bd6}.k-LIBRARY{color:#c27c0e}.k-CALC{color:var(--calc)}.k-QUESTION{color:var(--muted)}
.src svg{vertical-align:-2px;margin-right:6px}table.ev tr[data-k]{cursor:pointer}table.ev tr[data-k]:hover{background:var(--bg)}
table.inputs td:first-child{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.page{position:relative;display:inline-block;max-width:100%;border:1px solid var(--line);border-radius:6px;overflow:hidden;margin-top:8px} .page img{max-width:100%;display:block}
.hl{position:absolute;background:var(--hl);outline:2px solid #f5a300} pre{white-space:pre-wrap;font-size:12px}
.grid td,.grid th{font-size:12px;font-family:ui-monospace,monospace} .cellhl{background:var(--hl);outline:2px solid #f5a300}
a{color:var(--link)} .blocktext{white-space:pre-wrap;font-size:12px;font-family:ui-monospace,monospace;background:var(--bg);border-radius:6px;padding:8px;margin-top:8px}
table{border-collapse:collapse;margin:6px 0;white-space:normal} td,th{border:1px solid var(--line);padding:4px 8px;text-align:left}
.answer table th{background:var(--bg)}
@media (max-width:1100px){.layout{grid-template-columns:1fr}nav.questions,#detail{position:static;max-height:none}}
"""

_LABELS = {"traced": "found in its source", "calculated": "calculated",
           "calculated_with_assumption": "calculated with an assumed input",
           "from_question": "from your question, not verified", "untraced": "no citation",
           "phantom": "cites a block or calculation that does not exist in this turn",
           "mismatch": "not in the cited source", "derived_needs_calc": "a derived or change figure without a calculation",
           "unparsed": "unparsed figure"}

_SHORT = {"traced": "In source", "calculated": "Calculated", "calculated_with_assumption": "Calculated (assumption)",
          "from_question": "From your question", "untraced": "Not verified: no citation", "phantom": "Not verified: phantom",
          "mismatch": "Not verified: not in source", "derived_needs_calc": "Not verified: needs a calculation",
          "unparsed": "Not verified"}

# Every dynamic string goes through esc(); bold_html and source_html are escaped on the server.
_JS = """
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
function pageView(id,bold){const b=D.blocks[id];if(!b)return'<p>No block '+esc(id)+'</p>';
if(b.kind==='web'||b.kind==='library')return b.source_html;
return `<div class="page"><img src="${esc(b.img)}" alt="page of ${esc(id)}" data-id="${esc(id)}" onload="place(this)"><div class="hl"></div></div><div class="blocktext">${bold||esc(b.text)}</div>`}
function place(img){const b=D.blocks[img.dataset.id],bb=b.bbox,s=img.clientWidth/(b.w||img.naturalWidth),h=img.nextElementSibling;
h.style.left=bb[0]*s+'px';h.style.top=bb[1]*s+'px';h.style.width=(bb[2]-bb[0])*s+'px';h.style.height=(bb[3]-bb[1])*s+'px'}
const brk=s=>{const t=String(s??''),i=t.lastIndexOf(' · ');const w=x=>esc(x).replace(/_/g,'_<wbr>');
const tail=t.slice(i+3);return i<0||tail.length>24?w(t):w(t.slice(0,i))+' · <span class="nw">'+esc(tail)+'</span>'};
const ic=k=>k&&D.icons[k]?`<span class="sic k-${esc(k)}">${D.icons[k]}</span>`:'';  // server-side constant SVGs
function markIn(box,raw){const p=box.querySelector('.passage'),n=String(raw).replace(/[^0-9.,]/g,'').replace(/^[.,]+|[.,]+$/g,'');
if(p&&n){const t=p.textContent;p.innerHTML=t.split(n).map(esc).join('<mark>'+esc(n)+'</mark>')}}
function overview(v){const w=D.views[v],d=document.getElementById('detail');
d.innerHTML='<h2>Evidence</h2><p class="hint">Every figure in the answer and where it comes from. Click one to see its proof.</p>'
+(w.figures.length?'<table class="ev"><tr><th>Figure</th><th>Status</th><th>Source</th></tr>'+w.figures.map((f,i)=>
`<tr data-i="${i}"><td><span class="figure-chip status-${esc(f.status)}">${esc(f.raw)}</span></td><td>${esc(f.short)}</td><td><b>${ic(f.src_kind)}${brk(f.src_name)}</b>${(f.src_lines||[f.src_where]).map((l,j)=>l?`<div class="muted">${f.src_lines?ic((f.src_line_kinds||[])[j]):''}${brk(l)}</div>`:'').join('')}</td></tr>`).join('')+'</table>'
:'<p class="hint">No figures in this answer.</p>');
d.querySelectorAll('tr[data-i]').forEach(r=>r.onclick=()=>show(v,+r.dataset.i))}
function show(v,i){const w=D.views[v],f=w.figures[i],d=document.getElementById('detail'),c=w.calcs[f.cite];
const top='<button class="back">&larr; All figures</button>';
const mark=()=>markIn(d,f.raw);
if(c){d.innerHTML=top+`<h3 class="dtitle">${esc(f.raw)}</h3><p class="hint">${esc(f.cite)}: ${esc(c.title)} · ${esc(f.label)}</p>`
+`<div class="calc"><div class="clabel">Formula</div><code>${esc(c.expression)}</code><div class="clabel">With the values</div><code>${esc(c.filled)}</code> = <b>${esc(c.display)}</b></div>`
+'<p class="hint">Inputs. Click one to see it in its source:</p><table class="ev inputs"><tr><th>Input</th><th>Value</th><th>Source</th></tr>'
+c.inputs.map((x,k)=>x.kind==='block'?`<tr data-k="${k}"><td>${esc(x.name)}</td><td>${esc(x.value)}</td><td><b>${ic(x.src_kind)}${brk(x.src_name)}</b><div class="muted">${brk(x.src_where)}${x.row?` · from row '${esc(x.row)}'`:''}</div></td></tr>`
:`<tr><td>${esc(x.name)}</td><td>${esc(x.value)}</td><td>${esc(x.label)}</td></tr>`).join('')+'</table><div id="inp"></div>';
d.querySelectorAll('tr[data-k]').forEach(r=>r.onclick=()=>{const x=c.inputs[+r.dataset.k],box=document.getElementById('inp');box.innerHTML=pageView(x.source_id,x.bold_html);markIn(box,x.value)})}
else if(D.blocks[f.cite]){d.innerHTML=top+`<h3 class="dtitle">${esc(f.raw)}</h3><p class="hint">${esc(f.label)} · ${esc(f.source_label)} · [${esc(f.cite)}]</p>`+pageView(f.cite,f.bold_html);mark()}
else d.innerHTML=top+`<h3 class="dtitle">${esc(f.raw)}</h3><p class="hint">${esc(f.label)}</p>`;
d.querySelector('.back').onclick=()=>overview(v)}
function select(v){document.querySelectorAll('section.view').forEach(s=>s.hidden=+s.dataset.v!==v);
document.querySelectorAll('.qitem').forEach(q=>q.classList.toggle('active',+q.dataset.v===v));overview(v)}
document.querySelectorAll('section.view').forEach(s=>{const v=+s.dataset.v;s.querySelectorAll('.figure-chip').forEach(el=>el.onclick=()=>show(v,+el.dataset.i))});
document.querySelectorAll('.qitem').forEach(q=>q.onclick=()=>select(+q.dataset.v));
document.querySelectorAll('section.view').forEach(s=>{const v=+s.dataset.v;s.querySelectorAll('.cite[data-cite]').forEach(el=>el.onclick=()=>{
const w=D.views[v],i=w.figures.findIndex(f=>f.cite===el.dataset.cite);if(i>=0)show(v,i)})});
const m=location.hash.match(/^#v(\\d+)(?:f(\\d+))?$/);select(m&&D.views[+m[1]]?+m[1]:0);
if(m&&m[2]!==undefined&&D.views[+m[1]]&&D.views[+m[1]].figures[+m[2]])show(+m[1],+m[2]);
"""


_ICON = {  # 14px line icons, currentColor
    "PDF": '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M4 1.5h5l3 3v10H4z"/><path d="M9 1.5v3h3"/></svg>',
    "WEB": '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="6.5"/><path d="M1.5 8h13M8 1.5c2 2 2 11 0 13M8 1.5c-2 2-2 11 0 13"/></svg>',
    "CARD": '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2 13.5h12M4 11V7M8 11V4M12 11V8.5"/></svg>',
    "CALC": '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3" y="1.5" width="10" height="13" rx="1.5"/><path d="M5.5 4.5h5M5.5 8h1M9.5 8h1M5.5 11h1M9.5 11h1"/></svg>',
    "QUESTION": '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2.5 3h11v8h-6l-3 2.5V11h-2z"/></svg>',
    "LIBRARY": '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2.5 2.5h4v11h-4zM6.5 2.5h3v11h-3zM10 3l3-.6 1.5 10.5-3 .6z"/></svg>',
}

def _domain(url: str | None) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host

def _safe_url(url: str | None) -> str | None:
    """A link the report may make clickable: http(s) only (a search result could carry a javascript: URL)."""
    u = (url or "").strip()
    return u if re.match(r"https?://", u, re.I) else None

def _bold(text: str, span: list[int] | None) -> str:
    """Escape the block text and bold the matched figure by its stored offsets."""
    if not span:
        return html.escape(text)
    s, e = span
    return html.escape(text[:s]) + "<b>" + html.escape(text[s:e]) + "</b>" + html.escape(text[e:])

_SEP = re.compile(r"^:?-{2,}:?$")

def _tables(markup: str) -> str:
    """Render markdown pipe tables inside already-escaped markup as HTML tables (chips and bold spans kept)."""
    out, rows = [], []

    def flush():
        if not rows:
            return
        cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
        head = len(cells) > 1 and all(_SEP.match(c) or not c for c in cells[1])
        body = [r for r in cells if not all(_SEP.match(c) or not c for c in r)]
        html_rows = []
        for i, r in enumerate(body):
            tag = "th" if head and i == 0 else "td"
            html_rows.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in r) + "</tr>")
        out.append("<table>" + "".join(html_rows) + "</table>")
        rows.clear()

    for line in markup.split("\n"):
        if line.lstrip().startswith("|"):
            rows.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)

def _headings(markup: str) -> str:
    """Markdown headings in already-escaped markup ('## Analysis') as HTML headings."""
    return re.sub(r"(?m)^(#{2,3}) +(.+)$", lambda m: f"<h{len(m.group(1)) + 1}>{m.group(2)}</h{len(m.group(1)) + 1}>", markup)

def _chips(text: str, figures: list[dict]) -> str:
    """Wrap each figure in a chip, scanning forward from a cursor so a repeated figure gets its own chip."""
    out, cur = [], 0
    for i, f in enumerate(figures):
        j = text.find(f["raw"], cur)
        if j < 0:
            continue
        out += [html.escape(text[cur:j]),
                f'<span class="figure-chip status-{html.escape(f["status"])}" data-i="{i}">{html.escape(f["raw"])}</span>']
        cur = j + len(f["raw"])
    out.append(html.escape(text[cur:]))
    return "".join(out)


_CITE_TAG = re.compile(r"\[((?:[DCWL]\d+[^\]\s<>]*)(?:\s*[,;]\s*[DCWL]\d+[^\]\s<>]*)*)\]")

def _markdown(markup: str) -> str:
    """Paragraphs, lists, bold and inline code in already-escaped markup (tables and headings are rendered first)."""
    markup = _CITE_TAG.sub(lambda m: f'<span class="cite" data-cite="{m.group(1).split(";")[0].split(",")[0].strip()}">{m.group(1)}</span>', markup)
    markup = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", markup)
    markup = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", markup)
    out, para, items, kind, pre = [], [], [], None, None

    def flush():
        nonlocal items, kind
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()
        if items:
            out.append(f"<{kind}>" + "".join(f"<li>{i}</li>" for i in items) + f"</{kind}>")
            items, kind = [], None

    for line in markup.split("\n"):
        s = line.strip()
        if pre is not None:
            if s.startswith("```"):
                out.append("<pre>" + "\n".join(pre) + "</pre>")
                pre = None
            else:
                pre.append(line)
            continue
        if s.startswith("```"):
            flush()
            pre = []
            continue
        m = re.match(r"^(?:[-*•]|(\d+)[.)])\s+(.*)$", s)
        if not s:
            flush()
        elif s.startswith(("<table", "<h3", "<h4")):
            flush()
            out.append(s)
        elif m:
            if para:
                out.append("<p>" + "<br>".join(para) + "</p>")
                para.clear()
            k = "ol" if m.group(1) else "ul"
            if kind and kind != k:
                flush()
            kind = k
            items.append(m.group(2))
        elif items and line[:1] in (" ", "\t"):
            items[-1] += "<br>" + s
        else:
            if items:
                flush()
            para.append(s)
    if pre is not None:
        out.append("<pre>" + "\n".join(pre) + "</pre>")
    flush()
    return "\n".join(out)


_EXT = r"\.(?:pdf|xlsx|xlsm|xls|csv|docx?)\b"
_SEG = rf"(?:(?!{_EXT})[^/\n])+"                    # a path segment (spaces allowed) that holds no file name
_PATH = re.compile(rf"(?:/{_SEG})+/({_SEG}{_EXT})", re.I)

def _short_paths(text: str) -> str:
    """'/Users/.../demo/live/relx_income_statement.pdf' -> 'relx_income_statement.pdf' (display only)."""
    return _PATH.sub(r"\1", text)


_FILE = r"[\w.\-&]+\.(?:pdf|xlsx|xlsm|xls|csv|docx?)"

def _nav_title(question: str) -> str:
    """The question without its file preamble ('Using a.pdf and b.pdf, ...', 'In x.xlsx: ...', 'The report I uploaded is
    x.pdf. ...'), for the question list."""
    q = re.sub(rf"^(?:using|in|from)\s+{_FILE}(?:\s*(?:,|and)\s+{_FILE})*\s*[:,]\s*", "", question.strip(), flags=re.I)
    q = re.sub(rf"^[^.?!]*\b{_FILE}\.\s+", "", q)
    return q[:1].upper() + q[1:]


def _source_text(check: dict, figures: list[dict]) -> str:
    """The answer as written (bold and lists kept) when every figure is found in it in order; else the checked text."""
    raw, cur = check.get("answer") or "", 0
    for f in figures:
        j = raw.find(f["raw"], cur)
        if j < 0:
            return check.get("text") or normalise(raw)
        cur = j + len(f["raw"])
    return raw


def _when(at) -> str:
    try:
        return datetime.datetime.fromtimestamp(float(at)).strftime("%d %b %H:%M")
    except (TypeError, ValueError):
        return ""


def write_report(ledger: Ledger, data: dict | None = None) -> str:
    """Write report.html: every question asked in this folder, newest first, each at its latest check. `data` replaces
    the ledger when it cannot be read. A NOT CHECKED run shows only itself: a report never shows an older answer as
    the current one."""
    from .check import MAX_ATTEMPTS
    data = ledger.load() if data is None else data
    checks = data["checks"] or [{"answer": "", "figures": []}]
    latest = checks[-1]
    if latest.get("decision") == "not_checked":
        chosen = [latest]
    else:
        chosen, seen = [], set()
        for c in reversed(checks):
            if c.get("decision") == "not_checked":
                continue
            key = c.get("question") or id(c)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(c)
            if len(chosen) == MAX_VIEWS:
                break

    pages, sources, names = {}, {}, {}
    for doc in data["documents"].values():
        if doc.get("doc_id"):
            names[doc["doc_id"]] = pathlib.Path(doc.get("path") or "").name or doc["doc_id"]
        if doc.get("kind") in ("web", "library"):
            for b in doc["blocks"]:
                sources[b["block_id"]] = b
            continue
        dims = {p["index"]: p.get("dimensions") or {} for p in doc["pages"]}
        for b in doc["blocks"]:
            pages[b["block_id"]] = (b, dims.get(b["page_index"], {}))
    by_id = {c["calc_id"]: c for c in data["calculations"]}

    def source_label(f: dict) -> str:
        cite, status = f.get("cite"), f["status"]
        if status == "from_question":
            return "your question"
        if not cite:
            return "no source"
        if cite in by_id and status != "phantom":
            return f"Calculation {cite} · {by_id[cite].get('title') or by_id[cite].get('expression', '')}"
        if cite in sources:
            b = sources[cite]
            title = b.get("title") or b.get("url") or cite
            if b.get("type") == "library":
                return f"Library · {title}" + (f" · page {b['page']}" if b.get("page") else "")
            return f"Web · {title} · {_domain(b.get('url'))}" if _safe_url(b.get("url")) else f"Mistral finance data · {title}"
        if cite in pages:
            return f"{names.get(cite.split(':')[0], cite.split(':')[0])} · page {pages[cite][0]['page_index'] + 1}"
        return f"{cite} (not found)"

    def locate(bid: str) -> tuple[str, str]:
        """(file or site, place in it) for a cited block."""
        doc = names.get(bid.split(":")[0], bid.split(":")[0])
        if bid in pages:
            return doc, f"page {pages[bid][0]['page_index'] + 1}"
        if bid in sources:
            b = sources[bid]
            title = b.get("title") or ""
            if b.get("type") == "library":
                return "Mistral Library", title + (f" · page {b['page']}" if b.get("page") else "")
            return (_domain(b.get("url")) if _safe_url(b.get("url")) else "Mistral finance data"), title
        return bid, ""

    def kind_of(bid: str) -> str:
        if bid in pages:
            return "PDF"
        if bid in sources:
            b = sources[bid]
            return "LIBRARY" if b.get("type") == "library" else ("WEB" if _safe_url(b.get("url")) else "CARD")
        return ""

    def calc_sources(c: dict) -> list:
        """'file · place, place; file · place' for a calculation's cited inputs, in input order."""
        grouped: dict[str, list[str]] = {}
        kinds: dict[str, str] = {}
        extra = 0
        for x in c["inputs"]:
            sid = x.get("source_id")
            if sid == "assumption":
                extra += 1
                continue
            name, place = locate(sid)
            kinds.setdefault(name, kind_of(sid))
            grouped.setdefault(name, [])
            if place and place not in grouped[name]:
                grouped[name].append(place)
        lines = [(kinds[n], f"{n} · {', '.join(p)}" if p else n) for n, p in grouped.items()]
        return lines + ([("", f"{extra} assumption{'' if extra == 1 else 's'}")] if extra else [])

    views, wanted_all = [], set()
    for check in chosen:
        figures = [dict(f) for f in check["figures"]]
        calcs = {f["cite"]: dict(by_id[f["cite"]]) for f in figures if f.get("cite") in by_id and f["status"] != "phantom"}
        wanted = {f.get("cite") for f in figures} | {x.get("source_id") for c in calcs.values() for x in c["inputs"]}
        wanted_all |= wanted
        views.append((check, figures, calcs, wanted))

    blocks = {}
    for bid in sorted(wanted_all & sources.keys()):             # a web or library source: site, title, link, passage
        b = sources[bid]
        url = _safe_url(b.get("url"))
        title = html.escape(b.get("title") or b.get("url") or "")
        if b.get("type") == "library":
            icon, site = _ICON["LIBRARY"], "Mistral Library"
            where = (f"page {b['page']} ({html.escape(b.get('page_note', 'best-effort'))})" if b.get("page")
                     else "page unknown (Mistral Libraries text has no page markers)")
        elif url:
            icon, site, where = _ICON["WEB"], html.escape(_domain(url)), ""
        else:
            icon, site, where = (_ICON["CARD"], "Mistral finance data: a data series from Mistral's web search, not a web page, "
                                 "so there is no link", "")
        head = (f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{title}</a>' if url else title)
        blocks[bid] = {"kind": b.get("type"), "text": normalise(b["text"]),
                       "source_html": f'<div class="webcard"><div class="wsite">{icon}<span class="domain">{site}</span></div>'
                                      f'<div class="wtitle">{head}</div>' + (f'<div class="wwhere">{where}</div>' if where else "")
                                      + f'</div><div class="passage">{html.escape(b.get("raw_text") or b["text"])}</div>'}
    for bid in sorted(wanted_all & pages.keys()):
        b, dims = pages[bid]
        blocks[bid] = {"bbox": b["bbox"], "text": normalise(b["text"]), "w": dims.get("width"),
                       "img": f"pages/{bid.split(':')[0]}-p{b['page_index'] + 1}.png"}

    models = sorted({d.get("model") for d in data["documents"].values() if d.get("model")})
    marker = ledger.root / "SYNTHETIC"
    named = set(re.findall(r"[\w.\-&]+\.(?:xlsx|xlsm|xls|csv|pdf|docx?)\b", marker.read_text())) if marker.exists() else set()
    synthetic = marker.exists() and not named               # a marker that names files flags only those
    payload_views, nav, sections = [], [], []
    legend = ('<div class="legend"><span><i class="status-traced"></i>found in its source</span>'
              '<span><i class="status-calculated"></i>calculated from cited inputs</span>'
              '<span><i class="status-from_question"></i>from your question</span>'
              '<span><i class="status-untraced"></i>not verified</span></div>')
    for v, (check, figures, calcs, wanted) in enumerate(views):
        for f in figures:
            f["label"] = _LABELS.get(f["status"], f["status"])
            f["short"] = _SHORT.get(f["status"], f["status"])
            f["source_label"] = source_label(f)
            cite = f.get("cite")
            f["src_kind"] = ""
            if f["status"] == "from_question":
                f["src_name"], f["src_where"], f["src_kind"] = "Your question", "not verified", "QUESTION"
            elif cite in calcs:
                k = len(calcs[cite]["inputs"])
                lines = calc_sources(calcs[cite])
                f["src_lines"], f["src_line_kinds"] = [t for _, t in lines], [kd for kd, _ in lines]
                f["src_name"], f["src_where"] = f"Calculated from {k} input{'' if k == 1 else 's'}", "; ".join(f["src_lines"])
                f["src_kind"] = "CALC"
            elif cite in blocks:
                f["src_name"], f["src_where"] = locate(cite)
                f["src_kind"] = kind_of(cite)
            else:
                f["src_name"], f["src_where"] = "No source", (cite or "")
            if f.get("cite") in blocks and blocks[f["cite"]].get("kind") not in ("web", "library"):
                f["bold_html"] = _tables(_bold(blocks[f["cite"]]["text"], f.get("span")))
        for c in calcs.values():
            c["inputs"] = [dict(x) for x in c["inputs"]]
            filled = c.get("expression") or ""
            for x in sorted(c["inputs"], key=lambda x: -len(x.get("name") or "")):
                if x.get("name"):
                    val = f"{x.get('value')}{x.get('scale') or ''}" if x.get("scale") in ("k", "m", "bn") else str(x.get("value"))
                    filled = re.sub(rf"\b{re.escape(x['name'])}\b", lambda _m, v=val: v, filled)
            c["filled"] = filled
            for x in c["inputs"]:
                if x.get("source_id") != "assumption":
                    x["kind"] = "block"                             # clickable to its own highlight
                    x["row"] = (x.get("evidence") or {}).get("row")  # the row it came from, so a misnamed input shows
                    if x.get("source_id") in sources:
                        x["row"] = None                                # a web or library passage has no table row
                    x["src_name"], x["src_where"] = locate(x["source_id"])
                    x["src_kind"] = kind_of(x["source_id"])
                elif (x.get("evidence") or {}).get("constant"):
                    x["kind"], x["label"] = "constant", "constant"
                else:
                    x["kind"], x["label"] = "assumption", f"assumption: {x.get('reason') or 'no reason given'}"
                if x.get("source_id") in blocks and blocks[x["source_id"]].get("kind") not in ("web", "library"):
                    x["bold_html"] = _tables(_bold(blocks[x["source_id"]]["text"], (x.get("evidence") or {}).get("span")))
        count = {s: sum(1 for f in figures if f["status"] == s) for s in _LABELS}
        calculated = count["calculated"] + count["calculated_with_assumption"]
        bad = len(figures) - count["traced"] - calculated - count["from_question"]
        denials = sum(1 for c in data["checks"] if check.get("question") and c.get("question") == check.get("question")
                      and c.get("decision") == "deny")
        parts = [f"{count['traced']} traced", f"{calculated} calculated", f"{bad} untraced"]
        if count["from_question"]:
            parts.append(f"{count['from_question']} from your question, not verified")
        empty = len(check.get("empty_citations") or [])
        if empty:
            parts.append(f"{empty} citation{'' if empty == 1 else 's'} without a figure")
        parts.append(f"denials for this question: {denials}")
        if models:
            parts.append("OCR model: " + ", ".join(models))
        if synthetic:
            parts.append("SYNTHETIC DOCUMENTS")
        n = len(figures)
        noun = "figure" if n == 1 else "figures"
        if not n:
            summary = "No figures in this answer."
        elif bad:
            summary = f"{bad} of {n} {noun} not verified."
        else:
            pieces = ([f"{count['traced']} found in {'its' if count['traced'] == 1 else 'their'} source"] if count["traced"] else []) \
                + ([f"{calculated} calculated from cited inputs"] if calculated else []) \
                + ([f"{count['from_question']} taken from your question"] if count["from_question"] else [])
            summary = f"{n - count['from_question']} of {n} {noun} verified: " + ", ".join(pieces) + "."
        if denials and check.get("decision") == "allow":
            summary += f" The check sent back {denials} draft{'' if denials == 1 else 's'} first."
        decision = check.get("decision")
        not_checked = decision == "not_checked"
        denied = decision == "deny"
        final_denied = denied and (check.get("attempt") or 0) >= MAX_ATTEMPTS
        if not_checked:
            state, word = "notchecked", "Not checked"
            banner = f'<div class="banner not-checked">NOT CHECKED: {html.escape(check.get("reason") or "")}</div>'
        elif final_denied:
            state, word = "denied", "Denied"
            banner = (f'<div class="banner not-checked">DENIED (attempt {check.get("attempt")} of {MAX_ATTEMPTS}): Vibe showed '
                      f'this answer after its last retry; figures marked red are not verified.</div>')
        elif denied:
            state, word = "denied", "Denied draft"
            banner = (f'<div class="banner not-checked">DENIED DRAFT (attempt {check.get("attempt", "?")}): the agent is rewriting '
                      f'it; this is not the final answer. This page reloads by itself.</div>')
        else:
            state, word, banner = "verified", "Verified", ""
        question = _short_paths((check.get("question_text") or "").strip())
        answer = "No answer was checked." if not_checked else _markdown(_headings(_tables(_chips(_source_text(check, figures),
                                                                                                        figures))))
        docs = []
        for w in sorted(wanted - {None}):
            if w in calcs or w in by_id:
                continue
            if w in sources:
                b = sources[w]
                url = _safe_url(b.get("url"))
                if b.get("type") == "library":
                    label = ("LIBRARY", b.get("title") or "", "Mistral Library")
                elif url:
                    label = ("WEB", b.get("title") or url, _domain(url))
                else:
                    label = ("CARD", "Mistral finance data", b.get("title") or "")
            elif w in pages:
                d = w.split(":")[0]
                label = ("PDF", names.get(d, d), "")
            else:
                continue
            if label not in docs:
                docs.append(label)
        src = "".join(f'<span class="src"><span class="k-{k}">{_ICON.get(k, "")}</span><span class="sname">{html.escape(nm)}</span>'
                      + (f'<span class="domain">{html.escape(extra)}</span>' if extra else "")
                      + (" <em>synthetic</em>" if nm in named else "") + "</span>" for k, nm, extra in docs)
        title = _nav_title(question) or "(question not recorded)"
        tick = {"verified": "&#10003;", "denied": "&#10005;", "notchecked": "&ndash;"}[state]
        sections.append(
            f'<section class="view" data-v="{v}"{" hidden" if v else ""}>{banner}'
            f'<div class="qlabel">Question {len(views) - v}</div>'
            f'<h1 class="question" title="{html.escape(question)}">{html.escape(title)}</h1>'
            + f'<div class="verdictbar {state}"><span class="verdict {state}">{tick} {word}</span>'
              f'<span class="summary">{html.escape(summary)}</span></div>'
            + f'<div class="answer">{answer}</div>'
            + (f'<div class="srcrow"><span class="qlabel">Sources</span>{src}</div>' if src else "")
            + f'{legend}<details class="meta"><summary>Check details</summary>{html.escape(" · ".join(parts))}</details></section>')
        nav.append(f'<button class="qitem" data-v="{v}"><span class="t"><span class="dot {state}"></span>Q{len(views) - v}'
                   f' · {_when(check.get("at"))} · {word}</span>'
                   f'<span class="qtext">{html.escape(_nav_title(question) or "(question not recorded)")}</span></button>')
        payload_views.append({"question": question, "decision": decision, "figures": figures, "calcs": calcs})

    first = payload_views[0]
    payload = json.dumps({"figures": first["figures"], "blocks": blocks, "calcs": first["calcs"], "views": payload_views,
                          "icons": _ICON},
                         default=str).replace("<", "\\u003c")
    refresh = latest.get("decision") == "deny" and (latest.get("attempt") or 0) < MAX_ATTEMPTS
    doc = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           + ('<meta http-equiv="refresh" content="3">' if refresh else "") + '\n'
           f"<title>Receipts report</title><style>{_CSS}</style></head><body>\n"
           '<header class="top"><div class="brand">Receipts<b>.</b></div>'
           '<div class="sub">Every figure in the answer, checked against its source</div></header>\n'
           f'<div class="layout"><nav class="questions"><h2>Questions</h2>{"".join(nav)}</nav>\n'
           f'<main>{"".join(sections)}</main>\n'
           '<aside id="detail"><p class="hint">Click a figure.</p></aside></div>\n'
           f"<script>const D={payload};\n{_JS}</script></body></html>")
    out = ledger.dir / "report.html"
    out.write_text(doc)
    return str(out.resolve())
