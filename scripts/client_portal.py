"""Self-contained same-origin intake and proposal-review portal assets."""


def html():
    """Return markup with no inline executable content."""
    return b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Business record intake</title><link rel="stylesheet" href="/portal.css"><script src="/portal.js" defer></script></head>
<body><h1>Source intake and proposal review</h1><p>Originals and proposals are retained. Submission is not approval or publication.</p>
<section class="card"><label>OAuth access token (kept only in this page)<input id="token" type="password" autocomplete="off"></label>
<label>Source pages<input id="files" type="file" accept="image/png,image/jpeg" multiple></label><button id="upload">Create session and upload</button>
<label>Session ID<input id="session"></label><button id="review">Review session</button><button id="list">List my sessions</button></section>
<section class="grid"><div><h2>Source pages</h2><div id="pages"></div></div><div><h2>Receipts and proposals</h2><pre id="output"></pre></div></section>
<section class="card"><h2>Submit schema-mapped proposal</h2><p>Use the exact schema returned by the connector. Authorization fields are rejected.</p><textarea id="proposal" rows="14"></textarea><button id="submit">Retain proposal</button></section>
</body></html>"""


def css():
    """Return the portal's stylesheet, served same-origin."""
    return b"""body{font:16px system-ui;max-width:1100px;margin:auto;padding:1rem;color:#172033}label{display:block;margin:.7rem 0}input,textarea,button{font:inherit;padding:.5rem}input,textarea{width:100%;box-sizing:border-box}button{margin:.3rem}.grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.card{border:1px solid #ccd3df;border-radius:.5rem;padding:1rem}img{max-width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6fa;padding:.7rem}@media(max-width:700px){.grid{grid-template-columns:1fr}}"""


def javascript():
    """Return the portal's script, which calls only the authenticated intake API."""
    return b"""'use strict';
const $=id=>document.getElementById(id), key=()=>crypto.randomUUID();
async function call(name,body){const r=await fetch('/api/ingestion/'+name,{method:'POST',headers:{Authorization:'Bearer '+$('token').value,'Content-Type':'application/json'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(v.error||r.statusText);return v}
const show=v=>$('output').textContent=JSON.stringify(v,null,2);
$('upload').onclick=async()=>{try{const files=[...$('files').files];if(!files.length)throw Error('Select at least one image');const s=await call('create_ingestion_session',{idempotency_key:key(),expected_pages:files.length});$('session').value=s.session_id;const receipts=[];for(let i=0;i<files.length;i++){const data=await new Promise((ok,no)=>{const r=new FileReader;r.onload=()=>ok(r.result.split(',')[1]);r.onerror=no;r.readAsDataURL(files[i])});receipts.push(await call('upload_ingestion_page',{session_id:s.session_id,idempotency_key:key(),page_number:i+1,mime_type:files[i].type,data_base64:data}))}show({session:s,uploads:receipts})}catch(e){show({error:e.message})}};
async function review(){const id=$('session').value;const v=await call('get_ingestion_review_summary',{session_id:id});show(v);const s=await call('get_ingestion_status',{session_id:id});$('pages').replaceChildren();for(const p of s.pages){const image=await call('get_ingestion_page',{session_id:id,page_number:p.page_number});const card=document.createElement('div');card.className='card';const title=document.createElement('p');title.textContent='Page '+p.page_number+' - '+p.source_sha256;const img=document.createElement('img');img.alt='Retained source page '+p.page_number;img.src='data:'+image.mime_type+';base64,'+image.data;card.append(title,img);$('pages').append(card)}}
$('review').onclick=()=>review().catch(e=>show({error:e.message}));
$('list').onclick=()=>call('list_ingestion_sessions',{}).then(show).catch(e=>show({error:e.message}));
$('submit').onclick=()=>{let value;try{value=JSON.parse($('proposal').value)}catch(e){show({error:'Proposal is not valid JSON'});return}call('submit_record_proposal',{session_id:$('session').value,idempotency_key:key(),proposal:value}).then(show).catch(e=>show({error:e.message}))};
"""
