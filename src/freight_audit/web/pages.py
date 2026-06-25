"""
Browser routes attached to the FastAPI app: marketing landing, signup, a self-serve
demo (real OCR -> match -> result on a committed sample, trial-limited), and a review
console served by the API (lists items needing review, accepts/corrects/rejects,
persisting through store.record_decision).
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Optional

from fastapi import Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..api import app, reviewer, _store, _payload
from ..cli import _bundled_samples
from ..secret_provider import get_secret

_HERE = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(_HERE, "static")), name="static")

# ---------------------------------------------------------------------------
# self-serve demo: trial metering (total per trial id)
# ---------------------------------------------------------------------------
_DEMO_COUNTS: dict[str, int] = {}


def _demo_allow(trial_id: str) -> bool:
    limit = int(get_secret("DEMO_TRIAL_LIMIT", "5"))
    used = _DEMO_COUNTS.get(trial_id, 0)
    if used >= limit:
        return False
    _DEMO_COUNTS[trial_id] = used + 1
    return True


def _load_sample(name: Optional[str]) -> dict:
    paths = _bundled_samples()
    chosen = next((p for p in paths if os.path.basename(p) == name), paths[0] if paths else None)
    if not chosen:
        raise HTTPException(status_code=404, detail="no bundled samples available")
    return json.loads(open(chosen).read())


@app.post("/demo/audit")
async def demo_audit(file: Optional[UploadFile] = File(default=None),
                     sample: Optional[str] = Form(default=None),
                     x_trial_id: str = Header(default="anon")) -> dict:
    """Run a trial audit with no API key. With a file: OCR -> match -> result.
    With a sample name: parse the committed bundle -> match -> result."""
    if not _demo_allow(x_trial_id):
        raise HTTPException(status_code=429, detail="trial limit reached -- sign up to continue")
    if file is not None:
        from ..pipeline import audit_documents
        data = await file.read()
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, os.path.basename(file.filename or "doc.png"))
        with open(path, "wb") as fh:
            fh.write(data)
        result = audit_documents(invoice_img=path)
        steps = ["ocr", "match", "result"]
    else:
        from .. import process_load
        bundle = _load_sample(sample or "load_002_overcharge_duplicate.json")
        result = process_load(bundle.get("rate_confirmation"),
                              bundle.get("invoice"), bundle.get("pod"))
        steps = ["parse", "match", "result"]
    return {"steps": steps, **_payload(result)}


# ---------------------------------------------------------------------------
# review console served by the API
# ---------------------------------------------------------------------------
@app.get("/review")
def review_list(tenant: str = Depends(reviewer)) -> dict:
    store = _store()
    items = []
    for row in store.all_loads(tenant):
        if row.get("auto_approvable"):
            continue
        if store.decisions_for(row["load_id"], tenant):
            continue
        items.append({
            "load_id": row["load_id"],
            "severity": row.get("severity"),
            "net_impact_cents": row.get("net_impact_cents"),
            "findings": json.loads(row.get("findings_json") or "[]"),
        })
    return {"items": items}


@app.post("/review/{load_id}")
def review_decide(load_id: str, body: dict, tenant: str = Depends(reviewer)) -> dict:
    decision = (body or {}).get("decision")
    mapping = {"accept": "approved", "correct": "disputed", "reject": "disputed"}
    if decision not in mapping:
        raise HTTPException(status_code=400, detail="decision must be accept|correct|reject")
    note = (body or {}).get("note") or ("correction" if decision == "correct" else None)
    _store().record_decision(load_id, mapping[decision], actor=tenant, note=note,
                             tenant_id=tenant)
    return {"load_id": load_id, "decision": mapping[decision]}


# ---------------------------------------------------------------------------
# HTML pages (real product copy; responsive)
# ---------------------------------------------------------------------------
_STYLE = """
<style>
 :root{--ink:#1c1a17;--mut:#5b554c;--bg:#fbf7f0;--card:#fff;--line:#e2d9c8;
   --amber:#c6781e;--teal:#0f7368;--red:#a02c25}
 *{box-sizing:border-box} body{margin:0;font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;
   color:var(--ink);background:var(--bg);line-height:1.5}
 a{color:var(--teal)} .wrap{max-width:960px;margin:0 auto;padding:24px}
 nav{display:flex;gap:18px;align-items:center;border-bottom:1px solid var(--line);
   padding:14px 24px;background:#fff;flex-wrap:wrap}
 nav .brand{font-weight:800;letter-spacing:.04em} nav .grow{flex:1}
 .btn{display:inline-block;background:var(--ink);color:#fff;border:0;border-radius:6px;
   padding:11px 18px;font:inherit;font-weight:700;cursor:pointer;text-decoration:none}
 .btn.alt{background:var(--teal)} .hero h1{font-size:34px;margin:.2em 0}
 .hero p.lead{font-size:19px;color:var(--mut);max-width:640px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin:26px 0}
 .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px}
 .card h3{margin:.1em 0 .3em} .steps{counter-reset:s;padding-left:0;list-style:none}
 .steps li{margin:10px 0;padding-left:42px;position:relative}
 .steps li:before{counter-increment:s;content:counter(s);position:absolute;left:0;top:-2px;
   width:28px;height:28px;border-radius:50%;background:var(--amber);color:#fff;
   font-weight:700;display:grid;place-items:center}
 input,select{font:inherit;padding:10px;border:1px solid var(--line);border-radius:6px;width:100%}
 label{font-weight:600;font-size:14px} .row{margin:12px 0} pre{background:#11100e;color:#e8e2d6;
   padding:14px;border-radius:8px;overflow:auto;font-size:13px} .muted{color:var(--mut)}
 .pill{font-size:12px;font-weight:700;padding:2px 8px;border-radius:20px}
 .pill.warn{background:#f7e9d4;color:var(--amber)} .pill.block{background:#f3dcd8;color:var(--red)}
 .pill.ok,.pill.info{background:#dde7d6;color:#3f6f3a}
 @media(max-width:560px){.hero h1{font-size:27px}}
</style>"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title>{_STYLE}</head><body>"
        f"<nav><span class=brand>freight-audit</span><span class=grow></span>"
        f"<a href='/'>Home</a><a href='/demo'>Demo</a><a href='/app/review'>Review</a>"
        f"<a class=btn href='/signup'>Start free</a></nav>{body}</body></html>")


@app.get("/", response_class=HTMLResponse)
def landing():
    body = """
<div class=wrap>
  <section class=hero>
    <h1>Stop paying freight invoices you don't owe.</h1>
    <p class=lead>freight-audit reads each carrier invoice against its rate
      confirmation and proof of delivery, then flags overcharges, unauthorized
      accessorials, and duplicate lines &mdash; and catches detention the POD proves
      but the carrier never billed, so you recover revenue too.</p>
    <p><a class='btn alt' href='/demo'>Try the live demo</a>
       <a class=btn href='/signup'>Start free</a></p>
  </section>
  <div class=grid>
    <div class=card><h3>Catch overbilling</h3><p class=muted>Total mismatches,
      over-cap and unauthorized accessorials, and duplicate charges &mdash; flagged
      with the exact dollar impact, in integer cents.</p></div>
    <div class=card><h3>Recover detention</h3><p class=muted>When the POD proves a
      long wait the carrier didn't bill, we surface the recoverable revenue you're
      leaving on the table.</p></div>
    <div class=card><h3>Auto-approve the clean ones</h3><p class=muted>Loads that
      match are marked safe to pay; only the exceptions reach a human, with a clear
      worklist and audit trail.</p></div>
  </div>
  <h2>How it works</h2>
  <ol class=steps>
    <li><b>Send three documents.</b> Rate confirmation, carrier invoice, and POD
      &mdash; as JSON, or as scans through the OCR pipeline.</li>
    <li><b>We OCR and match them.</b> Charges are normalized to canonical
      accessorials and checked against the agreement and the evidence.</li>
    <li><b>You get findings + impact.</b> Each load comes back with a severity, the
      money at stake, and a savings/ROI rollup &mdash; auto-approve or review.</li>
  </ol>
  <p><a class=btn href='/signup'>Create your account &rarr;</a></p>
</div>"""
    return _page("freight-audit — audit freight invoices before you pay", body)


@app.get("/signup", response_class=HTMLResponse)
def signup_page():
    body = """
<div class=wrap style='max-width:480px'>
  <h1>Start free</h1>
  <p class=muted>Create an account to get an API key. Your data is scoped to your
    tenant.</p>
  <div class=row><label for=email>Work email</label>
    <input id=email type=email placeholder='you@company.com' autocomplete=email></div>
  <button class=btn id=go>Create account</button>
  <div id=out class=row></div>
</div>
<script>
document.getElementById('go').onclick = async () => {
  const email = document.getElementById('email').value.trim();
  const out = document.getElementById('out');
  out.textContent = 'Creating…';
  const r = await fetch('/signup', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({email})});
  const d = await r.json();
  if (!r.ok) { out.innerHTML = '<p style="color:#a02c25">'+(d.detail||'error')+'</p>'; return; }
  out.innerHTML = '<p>Account created for <b>'+d.tenant+'</b> (role '+d.role+
    '). Save your API key &mdash; it is shown once:</p><pre>'+d.api_key+'</pre>'+
    '<p class=muted>Use it as the <code>X-API-Key</code> header on every request.</p>';
};
</script>"""
    return _page("Sign up — freight-audit", body)


@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    body = """
<div class=wrap>
  <h1>Live demo</h1>
  <p class=muted>Run a real audit with no signup. The sample invoice is a synthetic
    freight document; nothing you upload is stored.</p>
  <p>
    <button class=btn id=img>Audit the sample invoice (OCR &rarr; match &rarr; result)</button>
    <button class='btn alt' id=bundle>Run a sample load bundle (fast)</button>
  </p>
  <div id=steps class=row></div>
  <div id=out class=row></div>
</div>
<script>
const TRIAL = localStorage.getItem('fa_trial') ||
  (localStorage.setItem('fa_trial', 't-'+Math.random().toString(36).slice(2)),
   localStorage.getItem('fa_trial'));
const fmt = c => (c<0?'-':'')+'$'+Math.abs(Math.trunc(c/100)).toLocaleString()+'.'+
  String(Math.abs(c)%100).padStart(2,'0');
function render(d){
  if (d.detail){ document.getElementById('out').innerHTML =
    '<p style="color:#a02c25">'+d.detail+'</p>'; return; }
  document.getElementById('steps').innerHTML =
    d.steps.map(s=>'<span class="pill ok">'+s+'</span>').join(' → ');
  const rows = d.findings.map(f=>'<li><span class="pill '+f.severity+'">'+f.severity+
    '</span> '+f.message+' <b>'+(f.money_impact_cents?fmt(f.money_impact_cents):'')+'</b></li>').join('');
  document.getElementById('out').innerHTML = '<h3>Load '+d.load_id+' — '+
    d.severity.toUpperCase()+'</h3><ul>'+rows+'</ul>';
}
document.getElementById('bundle').onclick = async () => {
  const fd = new FormData(); fd.append('sample','load_002_overcharge_duplicate.json');
  const r = await fetch('/demo/audit',{method:'POST',headers:{'X-Trial-Id':TRIAL},body:fd});
  render(await r.json());
};
document.getElementById('img').onclick = async () => {
  document.getElementById('out').textContent = 'OCR running…';
  const blob = await (await fetch('/static/sample-invoice.png')).blob();
  const fd = new FormData(); fd.append('file', blob, 'sample-invoice.png');
  const r = await fetch('/demo/audit',{method:'POST',headers:{'X-Trial-Id':TRIAL},body:fd});
  render(await r.json());
};
</script>"""
    return _page("Demo — freight-audit", body)


@app.get("/app/review", response_class=HTMLResponse)
def review_console():
    body = """
<div class=wrap>
  <h1>Review console</h1>
  <p class=muted>Paste a reviewer or admin API key to load the loads that need a
    human decision.</p>
  <div class=row><input id=key type=password placeholder='X-API-Key'></div>
  <button class=btn id=load>Load review queue</button>
  <div id=queue class=row></div>
</div>
<script>
const fmt = c => (c<0?'-':'')+'$'+Math.abs(Math.trunc(c/100)).toLocaleString()+'.'+
  String(Math.abs(c)%100).padStart(2,'0');
let KEY='';
async function refresh(){
  const r = await fetch('/review',{headers:{'X-API-Key':KEY}});
  if(!r.ok){ document.getElementById('queue').innerHTML='<p style="color:#a02c25">'+
    ((await r.json()).detail||'error')+'</p>'; return; }
  const d = await r.json();
  if(!d.items.length){ document.getElementById('queue').innerHTML='<p>Nothing to review — all clear.</p>'; return; }
  document.getElementById('queue').innerHTML = d.items.map(it=>{
    const f = it.findings.map(x=>'<li><span class="pill '+x.severity+'">'+x.severity+
      '</span> '+x.message+' <b>'+(x.money_impact_cents?fmt(x.money_impact_cents):'')+'</b></li>').join('');
    return '<div class=card><h3>'+it.load_id+' <span class="pill '+it.severity+'">'+
      it.severity+'</span></h3><ul>'+f+'</ul>'+
      '<button class=btn data-a=accept data-id="'+it.load_id+'">Accept</button> '+
      '<button class=btn data-a=correct data-id="'+it.load_id+'">Correct</button> '+
      '<button class=btn data-a=reject data-id="'+it.load_id+'">Reject</button></div>';
  }).join('');
}
document.getElementById('load').onclick=()=>{KEY=document.getElementById('key').value.trim();refresh();};
document.addEventListener('click', async e=>{
  const b=e.target.closest('[data-a]'); if(!b) return;
  const note = b.dataset.a==='correct' ? (prompt('Correction note:')||'correction') : null;
  await fetch('/review/'+encodeURIComponent(b.dataset.id),{method:'POST',
    headers:{'X-API-Key':KEY,'Content-Type':'application/json'},
    body:JSON.stringify({decision:b.dataset.a, note})});
  refresh();
});
</script>"""
    return _page("Review — freight-audit", body)
