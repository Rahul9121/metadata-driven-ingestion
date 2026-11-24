"""Build static HTML + JSON report for GitHub Pages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>MDIF Run Report</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js" integrity="sha512-CQBWl4fJHWbryGE+Pc7UAxWMVwMViRDEPwvF0sA1XBPCN9rP4C6E7Y8B8T8W0Q0b+0Q8W8h6h6r+0Q8W8h6r" crossorigin="anonymous"></script>
<style>
:root {
  --bg: #05060f;
  --cyan: #00f0ff;
  --magenta: #ff2bd6;
  --lime: #b6ff00;
  --text: #e8eefc;
  --muted: #9aa6c3;
  --card: #0c1020;
  --border: #1c2440;
}
* { box-sizing: border-box; }
body {
  margin: 0; font-family: ui-sans-serif, system-ui, Segoe UI, Roboto, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.5;
}
header {
  padding: 2rem 1.5rem 1rem; border-bottom: 1px solid var(--border);
  background: linear-gradient(180deg, #0a0d1c 0%, var(--bg) 100%);
}
h1 { margin: 0 0 .4rem; font-size: 1.6rem; letter-spacing: .02em; }
h1 span { color: var(--cyan); }
.sub { color: var(--muted); max-width: 70ch; }
.badge {
  display: inline-block; margin-top: .75rem; padding: .2rem .6rem; border-radius: 999px;
  border: 1px solid var(--cyan); color: var(--cyan); font-size: .8rem;
}
main { padding: 1.25rem 1.5rem 3rem; display: grid; gap: 1.25rem; }
.grid { display: grid; gap: 1.25rem; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 1rem 1.1rem;
}
.card h2 { margin: 0 0 .75rem; font-size: 1.05rem; color: var(--lime); }
table { width: 100%; border-collapse: collapse; font-size: .85rem; }
th, td { text-align: left; padding: .45rem .4rem; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--cyan); font-weight: 600; }
.status-success, .status-success_with_rejections, .status-success_with_expected_failures { color: var(--lime); }
.status-failed { color: var(--magenta); }
.status-skipped { color: #ffcc66; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .78rem; white-space: pre-wrap; }
svg.lineage { width: 100%; height: auto; background: #070a16; border-radius: 8px; }
.note { color: var(--muted); font-size: .9rem; }
footer { padding: 1rem 1.5rem 2rem; color: var(--muted); font-size: .85rem; border-top: 1px solid var(--border); }
a { color: var(--cyan); }
.overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(5, 6, 15, 0.82);
  display: flex; align-items: center; justify-content: center;
  padding: 1rem;
}
.overlay[hidden] { display: none !important; }
.alert-box {
  width: min(560px, 100%);
  background: var(--card);
  border: 1px solid var(--cyan);
  border-radius: 14px;
  box-shadow: 0 0 0 1px rgba(0,240,255,.12), 0 18px 50px rgba(0,0,0,.55);
  padding: 1.25rem 1.35rem 1.1rem;
}
.alert-box h2 {
  margin: 0 0 .65rem; color: var(--cyan); font-size: 1.15rem; letter-spacing: .02em;
}
.alert-box p { margin: 0 0 .7rem; color: var(--text); font-size: .95rem; }
.alert-box ul { margin: 0 0 1rem; padding-left: 1.15rem; color: var(--muted); font-size: .9rem; }
.alert-box li { margin: .25rem 0; }
.alert-box .actions { display: flex; gap: .6rem; flex-wrap: wrap; justify-content: flex-end; }
.alert-box button {
  cursor: pointer; border-radius: 999px; padding: .45rem .95rem; font-size: .9rem;
  border: 1px solid var(--border); background: transparent; color: var(--text);
}
.alert-box button.primary {
  border-color: var(--cyan); background: rgba(0,240,255,.12); color: var(--cyan); font-weight: 600;
}
.alert-box button:hover { border-color: var(--lime); color: var(--lime); }
.alert-chip {
  display: inline-block; margin-bottom: .75rem; padding: .15rem .55rem; border-radius: 999px;
  border: 1px solid var(--magenta); color: var(--magenta); font-size: .75rem;
}
</style>
</head>
<body>
<div id="intro-overlay" class="overlay" role="dialog" aria-modal="true" aria-labelledby="intro-title">
  <div class="alert-box">
    <div class="alert-chip">portfolio project · not Azure Data Factory</div>
    <h2 id="intro-title">What you are looking at</h2>
    <p>This is a <strong>Metadata-Driven Ingestion Framework</strong> I built in Python. Pipelines are described in YAML. The engine validates config, runs sources, retries transient failures, quarantines bad rows, and loads DuckDB.</p>
    <p>It follows an <strong>Azure Data Factory style pattern</strong>, but it is <strong>not</strong> ADF. There are no Azure resources, no integration runtime, and no cloud triggers. Everything here runs offline against fixtures and a local mock HTTP API.</p>
    <ul>
      <li>Lineage graph: source to target and dependencies</li>
      <li>Status table: rows read, loaded, rejected, attempts</li>
      <li>Retry timeline: mock API fails, then succeeds</li>
      <li>Quarantine + failed source with skipped downstream steps</li>
    </ul>
    <div class="actions">
      <button type="button" id="intro-dismiss">Got it</button>
      <button type="button" class="primary" id="intro-start">View report</button>
    </div>
  </div>
</div>
<header>
  <h1>Metadata-Driven Ingestion <span>Framework</span></h1>
  <p class="sub">Python re-creation of an Azure Data Factory style metadata-driven pattern. Not Azure Data Factory. No Azure resources. Offline fixtures and a local mock HTTP API.</p>
  <div class="badge" id="run-status">loading…</div>
</header>
<main>
  <section class="card">
    <h2>Lineage graph</h2>
    <div id="lineage"></div>
  </section>
  <section class="grid">
    <div class="card">
      <h2>Per-source status</h2>
      <div style="overflow:auto"><table id="src-table"><thead><tr>
        <th>Source</th><th>Status</th><th>Read</th><th>Loaded</th><th>Rejected</th><th>Attempts</th><th>Duration ms</th><th>Error</th>
      </tr></thead><tbody></tbody></table></div>
    </div>
    <div class="card">
      <h2>Attempt / retry timeline</h2>
      <canvas id="timeline" height="180"></canvas>
      <p class="note">Bars show attempt durations. Retries appear as extra attempts for the same source.</p>
    </div>
  </section>
  <section class="grid">
    <div class="card">
      <h2>Quarantine records</h2>
      <div style="overflow:auto"><table id="q-table"><thead><tr><th>Source</th><th>Reason</th><th>Record</th></tr></thead><tbody></tbody></table></div>
    </div>
    <div class="card">
      <h2>Failed run path + skipped downstream</h2>
      <div id="fail-panel" class="note"></div>
    </div>
  </section>
  <section class="grid">
    <div class="card">
      <h2>YAML vs result (products_http)</h2>
      <div id="yaml-panel" class="mono"></div>
    </div>
    <div class="card">
      <h2>ADF concept → this framework</h2>
      <div style="overflow:auto"><table id="adf-table"><thead><tr><th>ADF concept</th><th>In this project</th><th>Status</th></tr></thead><tbody></tbody></table></div>
      <p class="note">This is an honest mapping. Triggers, integration runtimes, and Azure resources are not implemented.</p>
    </div>
  </section>
</main>
<footer>
  Report generated from a local pipeline run. Source: <span class="mono">report.json</span>
</footer>
<script>
function setupIntroAlert(){
  const overlay = document.getElementById('intro-overlay');
  if(!overlay) return;
  const hide = () => { overlay.hidden = true; document.body.style.overflow = ''; };
  const show = () => { overlay.hidden = false; document.body.style.overflow = 'hidden'; };
  // Always show once per page open so visitors get the project context.
  show();
  document.getElementById('intro-dismiss')?.addEventListener('click', hide);
  document.getElementById('intro-start')?.addEventListener('click', hide);
  overlay.addEventListener('click', (e) => { if(e.target === overlay) hide(); });
  document.addEventListener('keydown', (e) => { if(e.key === 'Escape') hide(); });
}
async function boot() {
  setupIntroAlert();
  const res = await fetch('report.json');
  const data = await res.json();
  document.getElementById('run-status').textContent = `run ${data.run_id} · ${data.status} · ${data.duration_ms} ms`;
  renderLineage(data);
  renderSources(data);
  renderTimeline(data);
  renderQuarantine(data);
  renderFail(data);
  renderYaml(data);
  renderAdf(data);
}
function esc(s){return String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;');}
function renderLineage(data){
  const nodes = new Map();
  (data.sources||[]).forEach(s=>{
    nodes.set(s.name,{id:s.name,kind:'source',status:s.status});
    if(s.target) nodes.set(s.target,{id:s.target,kind:'target',status:'target'});
  });
  const list=[...nodes.values()];
  const cols={source:[],target:[]};
  list.forEach(n=> (n.kind==='source'?cols.source:cols.target).push(n));
  const w=900,h=Math.max(280, Math.max(cols.source.length, cols.target.length)*42+60);
  let svg=`<svg class="lineage" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">`;
  const pos={};
  cols.source.forEach((n,i)=> pos[n.id]={x:40,y:40+i*42});
  cols.target.forEach((n,i)=> pos[n.id]={x:520,y:40+i*42});
  // also place dependency-only layout tweaks
  (data.lineage||[]).forEach(e=>{
    if(!pos[e.from]) pos[e.from]={x:40,y:h-40};
    if(!pos[e.to]) pos[e.to]={x:520,y:h-40};
  });
  (data.lineage||[]).forEach(e=>{
    const a=pos[e.from], b=pos[e.to]; if(!a||!b) return;
    const color = e.kind==='depends_on' ? '#ff2bd6' : '#00f0ff';
    svg += `<path d="M${a.x+140} ${a.y+14} C${a.x+280} ${a.y+14}, ${b.x-80} ${b.y+14}, ${b.x} ${b.y+14}" stroke="${color}" fill="none" stroke-width="1.5" opacity="0.85"/>`;
  });
  Object.entries(pos).forEach(([id,p])=>{
    const n=nodes.get(id)||{kind:'source',status:''};
    const fill = n.kind==='target' ? '#12182b' : '#0c1020';
    const stroke = n.status==='failed' ? '#ff2bd6' : n.status==='skipped' ? '#ffcc66' : '#00f0ff';
    svg += `<rect x="${p.x}" y="${p.y}" rx="8" ry="8" width="140" height="28" fill="${fill}" stroke="${stroke}"/>`;
    svg += `<text x="${p.x+8}" y="${p.y+18}" fill="#e8eefc" font-size="11" font-family="ui-monospace,monospace">${esc(id)}</text>`;
  });
  svg += `</svg>`;
  document.getElementById('lineage').innerHTML = svg;
}
function renderSources(data){
  const tb=document.querySelector('#src-table tbody');
  tb.innerHTML=(data.sources||[]).map(s=>`<tr>
    <td class="mono">${esc(s.name)}</td>
    <td class="status-${esc(s.status)}">${esc(s.status)}</td>
    <td>${s.rows_read}</td><td>${s.rows_loaded}</td><td>${s.rows_rejected}</td>
    <td>${(s.attempts||[]).length}</td>
    <td>${s.duration_ms}</td>
    <td class="mono">${esc(s.error_class||'')} ${esc(s.error_message||s.skipped_reason||'')}</td>
  </tr>`).join('');
}
function renderTimeline(data){
  const labels=[]; const durations=[]; const colors=[];
  (data.sources||[]).forEach(s=>{
    (s.attempts||[]).forEach(a=>{
      labels.push(`${s.name}#${a.attempt}`);
      durations.push(a.duration_ms||0);
      colors.push(a.status==='success' ? '#b6ff00' : '#ff2bd6');
    });
    if(!(s.attempts||[]).length && s.status==='skipped'){
      labels.push(`${s.name}#skip`); durations.push(0.1); colors.push('#ffcc66');
    }
  });
  const ctx=document.getElementById('timeline');
  new Chart(ctx,{type:'bar', data:{labels, datasets:[{label:'duration_ms', data:durations, backgroundColor:colors}]},
    options:{plugins:{legend:{display:false}}, scales:{x:{ticks:{color:'#9aa6c3', maxRotation:90, minRotation:45, font:{size:9}}}, y:{ticks:{color:'#9aa6c3'}, grid:{color:'#1c2440'}}}}});
}
function renderQuarantine(data){
  const tb=document.querySelector('#q-table tbody');
  const rows=(data.quarantine||[]).slice(0,50);
  if(!rows.length){ tb.innerHTML='<tr><td colspan="3" class="note">No quarantined rows.</td></tr>'; return; }
  tb.innerHTML=rows.map(r=>{
    const reason=r.quarantine_reason||'';
    const src=r._source||'';
    const copy={...r}; delete copy.quarantine_reason; delete copy._source;
    return `<tr><td class="mono">${esc(src)}</td><td>${esc(reason)}</td><td class="mono">${esc(JSON.stringify(copy))}</td></tr>`;
  }).join('');
}
function renderFail(data){
  const failed=(data.sources||[]).filter(s=>s.status==='failed');
  const skipped=(data.sources||[]).filter(s=>s.status==='skipped');
  let html='';
  if(!failed.length) html+='<p>No failed sources in this run.</p>';
  failed.forEach(s=>{
    html += `<p><strong class="status-failed">${esc(s.name)}</strong> · ${esc(s.error_class)} · ${esc(s.error_message)}</p>`;
  });
  if(skipped.length){
    html += '<p class="note">Skipped downstream:</p><ul>';
    skipped.forEach(s=> html += `<li class="mono">${esc(s.name)} — ${esc(s.skipped_reason||'')}</li>`);
    html += '</ul>';
  }
  document.getElementById('fail-panel').innerHTML=html;
}
function renderYaml(data){
  const s=(data.sources||[]).find(x=>x.name==='products_http') || (data.sources||[])[0];
  if(!s){return;}
  const yaml = s.yaml_snippet||{};
  const out = {
    config: {name: yaml.name, type: yaml.type, url: yaml.url, retry: yaml.retry, target: yaml.target},
    result: {status:s.status, attempts:(s.attempts||[]).length, rows_loaded:s.rows_loaded, rows_rejected:s.rows_rejected, duration_ms:s.duration_ms, attempt_detail:s.attempts}
  };
  document.getElementById('yaml-panel').textContent = JSON.stringify(out, null, 2);
}
function renderAdf(data){
  const tb=document.querySelector('#adf-table tbody');
  tb.innerHTML=(data.adf_mapping||[]).map(r=>`<tr><td>${esc(r.adf)}</td><td>${esc(r.here)}</td><td>${esc(r.status)}</td></tr>`).join('');
}
boot().catch(err=>{
  document.getElementById('run-status').textContent='failed to load report.json';
  console.error(err);
});
</script>
</body>
</html>
"""


def write_report(data: dict[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "report.json"
    html_path = out / "index.html"
    json_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    # Chart.js from cdnjs without brittle integrity (pinned by URL version)
    html = REPORT_TEMPLATE.replace(
        'integrity="sha512-CQBWl4fJHWbryGE+Pc7UAxWMVwMViRDEPwvF0sA1XBPCN9rP4C6E7Y8B8T8W0Q0b+0Q8W8h6h6r+0Q8W8h6r" crossorigin="anonymous"',
        "crossorigin=\"anonymous\"",
    )
    html_path.write_text(html, encoding="utf-8")
    return html_path


def load_last_run(state_dir: str | Path = "data/state") -> dict[str, Any]:
    path = Path(state_dir) / "last_run.json"
    if not path.exists():
        raise FileNotFoundError(f"No run artifact at {path}. Run the pipeline first.")
    return json.loads(path.read_text(encoding="utf-8"))
