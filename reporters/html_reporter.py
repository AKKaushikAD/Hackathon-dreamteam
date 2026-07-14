"""Self-contained HTML report generator (dark teal theme).

Produces a single standalone .html file with embedded CSS and JavaScript. The
full report is injected as a JSON blob; client-side JS renders:
  * header with an animated circular SVG progress ring
  * verdict banner (PASS/FAIL/CONDITIONAL)
  * an 8-axis metric radar chart (Chart.js via CDN — the only external dep)
  * color-coded playbook rules table
  * critical violation cards
  * per-metric horizontal score bars
  * expandable test-case accordions
  * Claude-generated executive summary + numbered recommendations
  * a collapsible step-by-step trace viewer with per-step-type icons
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from core.runner import EvaluationReport

_DATA_TOKEN = "/*__REPORT_DATA__*/"

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Agent Evaluation Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {
    --teal: #1ea599;
    --blue: #3f8cff;
    --amber: #f5a623;
    --danger: #e74c3c;
    --success: #2ecc71;
    --text: #e8f4f3;
    --muted: #7fa8a3;
    --card-bg: rgba(30, 165, 153, 0.08);
    --card-border: rgba(30, 165, 153, 0.25);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: linear-gradient(135deg, #020d0c, #010808);
    color: var(--text);
    min-height: 100vh;
  }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 32px 20px 80px; }
  h1, h2, h3 { font-weight: 600; letter-spacing: 0.3px; }
  h2 { color: var(--teal); border-bottom: 1px solid var(--card-border); padding-bottom: 8px; margin-top: 40px; }
  a { color: var(--blue); }
  .card {
    background: var(--card-bg); border: 1px solid var(--card-border);
    border-radius: 14px; padding: 20px; margin: 14px 0;
  }
  .header { display: flex; align-items: center; gap: 28px; flex-wrap: wrap; }
  .header .meta { flex: 1; min-width: 260px; }
  .header .meta h1 { margin: 0 0 6px; font-size: 26px; }
  .kv { color: var(--muted); font-size: 14px; line-height: 1.7; }
  .kv b { color: var(--text); font-weight: 600; }
  .ring-wrap { position: relative; width: 160px; height: 160px; }
  .ring-label {
    position: absolute; inset: 0; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
  }
  .ring-score { font-size: 34px; font-weight: 700; }
  .ring-grade { font-size: 14px; color: var(--muted); }
  .banner {
    text-align: center; font-size: 22px; font-weight: 700; letter-spacing: 2px;
    padding: 18px; border-radius: 14px; margin: 18px 0;
  }
  .banner.pass { background: rgba(46,204,113,0.14); border: 1px solid var(--success); color: var(--success); }
  .banner.cond { background: rgba(245,166,35,0.14); border: 1px solid var(--amber); color: var(--amber); }
  .banner.fail { background: rgba(231,76,60,0.14); border: 1px solid var(--danger); color: var(--danger); }
  .banner .sub { display:block; font-size: 13px; font-weight: 500; letter-spacing: 0.5px; color: var(--muted); margin-top: 6px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 820px) { .grid2 { grid-template-columns: 1fr; } }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid rgba(30,165,153,0.12); }
  th { color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 1px; }
  .pill { padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
  .pill.PASS { background: rgba(46,204,113,0.18); color: var(--success); }
  .pill.WARN { background: rgba(245,166,35,0.18); color: var(--amber); }
  .pill.FAIL { background: rgba(231,76,60,0.18); color: var(--danger); }
  .prio { font-size: 11px; color: var(--muted); }
  .bar-row { display:flex; align-items:center; gap: 12px; margin: 10px 0; }
  .bar-row .lbl { width: 170px; font-size: 13px; color: var(--text); }
  .bar-track { flex: 1; height: 12px; background: rgba(255,255,255,0.06); border-radius: 6px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 6px; transition: width 1s ease; }
  .bar-val { width: 48px; text-align: right; font-variant-numeric: tabular-nums; }
  .crit-card { border-left: 4px solid var(--danger); background: rgba(231,76,60,0.08); }
  .crit-card .rid { color: var(--amber); font-weight: 600; }
  .acc { border: 1px solid var(--card-border); border-radius: 12px; margin: 10px 0; overflow: hidden; }
  .acc-head {
    display:flex; align-items:center; gap: 12px; padding: 14px 16px; cursor: pointer;
    background: rgba(30,165,153,0.06);
  }
  .acc-head:hover { background: rgba(30,165,153,0.12); }
  .acc-head .grow { flex: 1; }
  .acc-body { display: none; padding: 16px; border-top: 1px solid var(--card-border); }
  .acc.open .acc-body { display: block; }
  .chev { transition: transform 0.2s; color: var(--muted); }
  .acc.open .chev { transform: rotate(90deg); }
  pre.out {
    background: #010e0d; border: 1px solid var(--card-border); border-radius: 8px;
    padding: 12px; overflow-x: auto; font-size: 12.5px; color: #cfeae7; white-space: pre-wrap;
  }
  .trace-step { display:flex; gap: 10px; padding: 8px 10px; border-radius: 8px; margin: 4px 0; background: rgba(255,255,255,0.03); }
  .trace-step .ico { width: 26px; text-align:center; }
  .trace-step .body { flex: 1; font-size: 13px; }
  .trace-step .st { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }
  .rec-card { display:flex; gap: 12px; align-items:flex-start; padding: 12px 14px; margin: 8px 0;
    background: rgba(30,165,153,0.10); border: 1px solid var(--card-border); border-radius: 10px; }
  .rec-num { color: var(--teal); font-weight: 700; font-size: 18px; }
  .muted { color: var(--muted); }
  .small { font-size: 12px; }
  .flex { display:flex; align-items:center; gap: 10px; flex-wrap: wrap; }
  .metric-mini { font-size: 12px; color: var(--muted); }
  footer { text-align:center; color: var(--muted); font-size: 12px; margin-top: 50px; }
</style>
</head>
<body>
<div class="wrap" id="app"></div>
<footer>Generated by the Agent Testing &amp; Evaluation Framework · LLM engine: Claude Code CLI</footer>
<script>
const REPORT = /*__REPORT_DATA__*/;

const COLORS = { teal:"#1ea599", blue:"#3f8cff", amber:"#f5a623", danger:"#e74c3c", success:"#2ecc71", muted:"#7fa8a3" };
function scoreColor(s){ if(s>=85)return COLORS.success; if(s>=70)return COLORS.amber; if(s>=60)return "#e8912a"; return COLORS.danger; }
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function ring(score, grade){
  const r=64, c=2*Math.PI*r, off=c*(1-score/100), col=scoreColor(score);
  return `<div class="ring-wrap">
    <svg width="160" height="160" viewBox="0 0 160 160">
      <circle cx="80" cy="80" r="${r}" stroke="rgba(255,255,255,0.08)" stroke-width="12" fill="none"/>
      <circle cx="80" cy="80" r="${r}" stroke="${col}" stroke-width="12" fill="none"
        stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${c}"
        transform="rotate(-90 80 80)">
        <animate attributeName="stroke-dashoffset" from="${c}" to="${off}" dur="1.2s" fill="freeze"
          calcMode="spline" keySplines="0.4 0 0.2 1" keyTimes="0;1"/>
      </circle>
    </svg>
    <div class="ring-label"><div class="ring-score" style="color:${col}">${score.toFixed(1)}</div>
    <div class="ring-grade">grade ${esc(grade)}</div></div>
  </div>`;
}

function header(){
  const s = REPORT.test_run_summary;
  return `<div class="card header">
    ${ring(s.overall_score, s.grade)}
    <div class="meta">
      <h1>Agent Evaluation Report</h1>
      <div class="kv">
        <div>Agent: <b>${esc(REPORT.agent_id)}</b></div>
        <div>Playbook version: <b>${esc(REPORT.playbook_version)}</b></div>
        <div>Generated: <b>${esc(REPORT.generated_at)}</b></div>
        <div>Report ID: <b>${esc(REPORT.report_id)}</b></div>
        <div>Test cases: <b>${s.total_test_cases}</b> · pass <b style="color:${COLORS.success}">${s.passed}</b>
          · warn <b style="color:${COLORS.amber}">${s.warnings}</b>
          · fail <b style="color:${COLORS.danger}">${s.failed}</b></div>
      </div>
    </div>
  </div>`;
}

function banner(){
  const v = REPORT.test_run_summary.verdict;
  const cls = v==="PASS"?"pass":(v==="FAIL"?"fail":"cond");
  const sub = {PASS:"Agent meets the playbook bar.", FAIL:"Blocking violations detected.",
    CONDITIONAL_PASS:"Passes with reservations — address findings before production."}[v]||"";
  return `<div class="banner ${cls}">${esc(v)}<span class="sub">${esc(sub)}</span></div>`;
}

function radar(){
  return `<div class="card"><h3>Metric Radar</h3><canvas id="radar" height="300"></canvas></div>`;
}

function bars(){
  let rows="";
  for(const [name,m] of Object.entries(REPORT.metric_scores)){
    const col=scoreColor(m.score);
    rows += `<div class="bar-row">
      <div class="lbl">${esc(name)} <span class="metric-mini">w=${m.weight}</span></div>
      <div class="bar-track"><div class="bar-fill" style="width:${m.score}%;background:${col}"></div></div>
      <div class="bar-val" style="color:${col}">${m.score.toFixed(1)}</div></div>`;
  }
  return `<div class="card"><h3>Score Breakdown</h3>${rows}</div>`;
}

function rulesTable(){
  let rows="";
  for(const r of REPORT.playbook_rule_results){
    let viol = r.violations.slice(0,3).map(v=>`<div class="small" style="color:${COLORS.danger}">• ${esc(v.description)} <span class="muted">(${esc(v.test_case_id)})</span></div>`).join("");
    rows += `<tr>
      <td><span class="pill ${r.status}">${r.status}</span></td>
      <td><b>${esc(r.rule_id)}</b><div class="prio">${esc(r.priority)}</div></td>
      <td>${esc(r.rule_name)}${viol?`<div style="margin-top:6px">${viol}</div>`:""}</td>
      <td style="text-align:right;color:${scoreColor(r.score)}">${r.score.toFixed(0)}</td>
    </tr>`;
  }
  return `<div class="card"><h3>Playbook Rules</h3><table>
    <thead><tr><th>Status</th><th>Rule</th><th>Name / findings</th><th style="text-align:right">Score</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function criticals(){
  const cv = REPORT.critical_violations;
  if(!cv.length) return `<div class="card"><h3>Critical Violations</h3><div class="muted">None — no blocking violations detected.</div></div>`;
  let cards = cv.map(v=>`<div class="card crit-card">
    <div class="rid">${esc(v.rule_id||"—")} · ${esc(v.test_case_id)}${v.trace_step!=null?` · step ${v.trace_step}`:""}</div>
    <div>${esc(v.description)}</div></div>`).join("");
  return `<h2>Critical Violations</h2>${cards}`;
}

const STEP_ICONS = { reasoning:"🧠", tool_call:"🔧", tool_result:"📥", memory_read:"📖", memory_write:"💾", output:"📤" };

function traceView(trace){
  if(!trace) return "";
  let steps = trace.steps.map(s=>{
    let extra="";
    if(s.tool_name) extra += `<div class="small muted">tool: ${esc(s.tool_name)} ${s.tool_params?esc(JSON.stringify(s.tool_params)):""}</div>`;
    if(s.tool_result!=null) extra += `<div class="small">→ ${esc(typeof s.tool_result==="string"?s.tool_result:JSON.stringify(s.tool_result)).slice(0,300)}</div>`;
    return `<div class="trace-step"><div class="ico">${STEP_ICONS[s.step_type]||"•"}</div>
      <div class="body"><div class="st">${esc(s.step_type)} · ${s.latency_ms.toFixed(0)}ms</div>
      <div>${esc(s.content)}</div>${extra}</div></div>`;
  }).join("");
  return `<div style="margin-top:10px"><div class="st muted small">Full trace (${trace.steps.length} steps · ${trace.total_latency_ms.toFixed(0)}ms${trace.error?` · ERROR`:""})</div>${steps}</div>`;
}

function caseAccordions(){
  let items = REPORT.test_case_results.map((r,i)=>{
    let metricRows = Object.entries(r.metrics).map(([n,m])=>
      `<div class="bar-row"><div class="lbl small">${esc(n)}</div>
       <div class="bar-track"><div class="bar-fill" style="width:${m.score}%;background:${scoreColor(m.score)}"></div></div>
       <div class="bar-val small" style="color:${scoreColor(m.score)}">${m.score.toFixed(0)}</div></div>`).join("");
    let viols = r.critical_violations.map(v=>`<div class="small" style="color:${COLORS.danger}">⚠ ${esc(v.description)}</div>`).join("");
    return `<div class="acc" id="acc${i}">
      <div class="acc-head" onclick="document.getElementById('acc${i}').classList.toggle('open')">
        <span class="chev">▶</span>
        <span class="pill ${r.status}">${r.status}</span>
        <span class="grow"><b>${esc(r.test_case_id)}</b> — ${esc(r.name)}</span>
        <span style="color:${scoreColor(r.weighted_score)}">${r.weighted_score.toFixed(1)}</span>
      </div>
      <div class="acc-body">
        <div class="small muted">Prompt</div><pre class="out">${esc(r.prompt)}</pre>
        <div class="small muted">Output</div><pre class="out">${esc(r.output)}</pre>
        ${viols?`<div style="margin:8px 0">${viols}</div>`:""}
        <div class="small muted" style="margin-top:10px">Per-metric</div>${metricRows}
        ${traceView(r.trace || (REPORT.traces||[]).find(t=>t.test_case_id===r.test_case_id))}
      </div></div>`;
  }).join("");
  return `<h2>Test Case Results</h2>${items}`;
}

function summaryBlock(){
  let recs = (REPORT.recommendations||[]).map((r,i)=>
    `<div class="rec-card"><div class="rec-num">${i+1}</div><div>${esc(r)}</div></div>`).join("");
  return `<h2>Executive Summary</h2>
    <div class="card">${esc(REPORT.executive_summary||"(none)")}</div>
    <h2>Recommendations</h2>${recs||'<div class="muted">None.</div>'}`;
}

function render(){
  document.getElementById("app").innerHTML =
    header() + banner() +
    `<div class="grid2">${radar()}${bars()}</div>` +
    rulesTable() + criticals() + caseAccordions() + summaryBlock();

  // radar chart
  const labels = Object.keys(REPORT.metric_scores);
  const values = labels.map(l=>REPORT.metric_scores[l].score);
  new Chart(document.getElementById("radar"), {
    type:"radar",
    data:{ labels, datasets:[{ label:"Score", data:values,
      backgroundColor:"rgba(30,165,153,0.25)", borderColor:COLORS.teal,
      pointBackgroundColor:COLORS.blue, borderWidth:2 }]},
    options:{ responsive:true, plugins:{legend:{display:false}},
      scales:{ r:{ min:0,max:100, angleLines:{color:"rgba(255,255,255,0.08)"},
        grid:{color:"rgba(255,255,255,0.08)"},
        pointLabels:{color:"#e8f4f3", font:{size:11}},
        ticks:{ color:"#7fa8a3", backdropColor:"transparent", stepSize:20 } } } }
  });
}
render();
</script>
</body>
</html>
"""


class HTMLReporter:
    def __init__(self, output_dir: Union[str, Path] = "reports") -> None:
        self.output_dir = Path(output_dir)

    def render(self, report: EvaluationReport) -> str:
        data = json.dumps(report.to_dict(), default=str)
        return HTML_TEMPLATE.replace(_DATA_TOKEN, data)

    def write(self, report: EvaluationReport, filename: str = "") -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        name = filename or f"{report.agent_id}_{report.report_id}.html"
        path = self.output_dir / name
        path.write_text(self.render(report), encoding="utf-8")
        return path
