"use strict";
const $ = s => document.querySelector(s);
const state = {campaign: null, detail: null, judgments: [], omissions: [], dirty: false, busy: false, stop: false};
function node(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
function message(text, error=false) { $("#message").textContent = text; $("#message").className = error ? "error" : ""; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok || result.status === "error") throw new Error(typeof result.detail === "string" ? result.detail : result.message || JSON.stringify(result.detail));
  return result;
}
function query(cid, iid, extra={}) { return "/api/legal-review?" + new URLSearchParams({campaign_id:cid, ...(iid ? {item_id:iid} : {}), ...extra}); }
function cleanRequired() { if (state.dirty) throw new Error("Save your draft or discard unsaved edits before changing documents."); }
function dirty() { state.dirty = true; $("#save-state").textContent = "Unsaved edits"; $("#save-state").className = "dirty"; locks(); }
function locks() {
  $("#process").disabled = state.busy || !state.campaign || state.dirty;
  $("#stop").disabled = !state.busy;
  $("#campaign-select").disabled = state.busy;
  $("#export").disabled = state.busy || !state.campaign || state.dirty;
  $("#retry").disabled = state.busy || !state.detail || !!state.detail.revision_id || !!state.detail.error;
  $("#omit").disabled = state.busy || !state.detail?.revision_id;
  $("#save").disabled = state.busy || !state.detail?.revision_id;
  $("#discard").disabled = state.busy || !state.dirty;
  document.querySelectorAll("#create-form input, #create-form button, .doc, .review-pane input, .review-pane select, .review-pane textarea").forEach(e => e.disabled = state.busy);
}
async function action(fn) { try { await fn(); } catch(e) { message(e.message || String(e), true); } finally { locks(); } }
function button(text, fn) { const b = node("button", text); b.type = "button"; b.onclick = () => action(fn); return b; }
function label(text, control) { const l = node("label", text); control.setAttribute("aria-label",text); l.append(control); return l; }
function select(options, value, change) { const s = node("select"); options.forEach(([v,t]) => {const o=node("option",t);o.value=v;s.append(o);});s.value=value;s.onchange=()=>change(s.value);return s; }
const verdicts = [["unreviewed","Not reviewed"],["correct","Correct"],["incorrect","Incorrect"],["uncertain","Uncertain"]];
const kinds = ["fact","definition","right","obligation","prohibition","permission","amendment"];

async function loadCampaigns(selected) {
  const result = await api("/api/legal-review");
  const s = $("#campaign-select"); s.replaceChildren(new Option("Choose a campaign", ""));
  for (const c of result.campaigns) s.append(new Option(`${c.created_at.slice(0,16).replace("T"," ")} · ${c.sample_size} documents · ${c.model}`, c.campaign_id));
  s.value = selected || "";
  return result.campaigns;
}
function renderCampaign(result) {
  state.campaign = result;
  $("#overview").hidden = $("#workbench").hidden = false;
  const summary = result.summary, c = result.campaign;
  const cards = [[summary.population_documents,"JSON files in frozen population"],[summary.sample_documents,"Documents in this sample"],
    [`${summary.extracted_documents} / ${summary.sample_documents}`,"Complete extractions"],[`${summary.completed_document_reviews} / ${summary.sample_documents}`,"Full-source reviews completed"]];
  $("#metrics").replaceChildren(...cards.map(([v,t])=>{const d=node("div",null,"metric");d.append(node("strong",v),node("span",t));return d;}));
  $("#sample-label").textContent = `${c.model} · seed ${c.seed} · ${c.corpus_path}`;
  const table=node("table"), head=node("tr");["Judgment","Correct","Incorrect","Uncertain","Unreviewed"].forEach(t=>head.append(node("th",t)));table.append(head);
  for (const [k,v] of Object.entries(summary.claim_judgments)) { const tr=node("tr");tr.append(node("td",k));for(const s of ["correct","incorrect","uncertain","unreviewed"])tr.append(node("td",v[s]));table.append(tr); }
  $("#scores").replaceChildren(table,node("p",`${summary.reported_omissions} source-anchored omissions reported. ${summary.unprocessed_documents} sampled documents still lack complete extraction.`));
  $("#documents").replaceChildren(...result.items.map(item=>{
    const b=button("",()=>openItem(item.item_id));b.className="doc"+(state.detail?.item_id===item.item_id?" active":"");
    b.append(node("strong",item.file),node("span",`${item.complete?"Reviewed":item.extraction_status} · ${item.claim_count == null ? "awaiting claims" : item.claim_count+" claims"}`));b.title=item.title||item.file;return b;
  }));
  locks();
}
async function refreshCampaign() { renderCampaign(await api(query(state.campaign.campaign.campaign_id))); }
async function openCampaign(cid) {
  cleanRequired(); state.detail=null;
  renderCampaign(await api(query(cid)));
  $("#campaign-select").value=cid; $("#setup").open=false;
  const params=new URLSearchParams(location.search);const iid=params.get("campaign")===cid?params.get("item"):null;
  const chosen=state.campaign.items.find(i=>i.item_id===iid)||state.campaign.items.find(i=>i.revision_id&&!i.complete)||state.campaign.items[0];
  if(chosen)await openItem(chosen.item_id);
}
function highlight(span) {
  const text=Array.from(state.detail.source_text), pre=$("#source");
  const mark=node("mark",text.slice(span.start,span.end).join(""));
  pre.replaceChildren(document.createTextNode(text.slice(0,span.start).join("")),mark,document.createTextNode(text.slice(span.end).join("")));
  mark.scrollIntoView({block:"center",behavior:"smooth"});
}
function renderOmissions() {
  const host=$("#omissions");host.replaceChildren(node("h3",`Missed rules · ${state.omissions.length}`));
  state.omissions.forEach((m,i)=>{const box=node("div",null,"omission"), notes=node("textarea");notes.value=m.note||"";notes.oninput=()=>{m.note=notes.value;dirty();};
    box.append(node("blockquote",m.quote),label("What was missed?",notes),button("Show passage",()=>highlight(m)),button("Remove",()=>{state.omissions.splice(i,1);dirty();renderOmissions();}));host.append(box);});
}
function renderDetail(detail) {
  state.detail=detail;state.dirty=false;
  state.omissions=structuredClone(detail.review?.omissions||[]);
  state.judgments=(detail.receipt?.claims||[]).map(c=>structuredClone(detail.review?.judgments.find(j=>j.claim_id===c.claim_id)||{claim_id:c.claim_id,kind:"unreviewed",subject:"unreviewed",qualifications:"unreviewed",note:""}));
  $("#source-title").textContent=detail.title||detail.file;
  $("#source-meta").textContent=`${detail.original_path} · ${Array.from(detail.source_text).length.toLocaleString()} characters · ${detail.extraction_status}`;
  $("#source").textContent=detail.source_text;
  $("#claim-heading").textContent=detail.receipt ? `${detail.receipt.claims.length} claims to review` : "Extraction not complete";
  $("#save-state").textContent=detail.version ? `Saved revision ${detail.version}` : "Not reviewed";$("#save-state").className="muted";
  $("#complete").checked=!!detail.review?.complete;
  $("#review-footer").hidden=!detail.receipt;
  const host=$("#claims");host.replaceChildren();
  if (!detail.receipt) {
    host.append(node("p",detail.extraction_error || "Process this document to see its proposals. Review remains unavailable until its complete extraction is saved.","muted"));
    if(detail.progress)host.append(node("pre",JSON.stringify(detail.progress,null,2)));
  }
  (detail.receipt?.claims||[]).forEach((c,i)=>{
    const j=state.judgments[i], card=node("article",null,"claim");
    card.append(node("h3",`${i+1}. ${c.kind} · ${c.provision||"Source statement"}`),node("blockquote",c.statement),node("p",`Subject: ${c.subject||"Unresolved"}`,"subject"));
    if(c.review_issues?.length)card.append(node("p",c.review_issues.join("; "),"issues"));
    const evidence=button("Show evidence",()=>highlight(c.evidence[0]));card.append(evidence);
    if(c.context_evidence?.length)card.append(button("Show provision context",()=>highlight(c.context_evidence[c.context_evidence.length-1])));
    const qualifiers=node("details");qualifiers.append(node("summary","Extracted qualifications"));
    for(const key of ["conditions","exceptions","dates","scope"])qualifiers.append(node("p",`${key}: ${(c[key]||[]).join(" · ")||"None explicitly recorded"}`,"small"));
    qualifiers.append(node("p",`Scope resolution: ${c.scope_resolution||"unresolved"}. Check against the source and provision context.`,"muted small"));card.append(qualifiers);
    const v=node("div",null,"verdicts");for(const [key,title] of [["kind","Rule type"],["subject","Complete subject"],["qualifications","Conditions & scope"]])v.append(label(title,select(verdicts,j[key],value=>{j[key]=value;dirty();})));card.append(v);
    const details=node("details");details.append(node("summary","Correction or reviewer note"));
    details.append(label("Proposed corrected type",select([["","No correction"],...kinds.map(k=>[k,k])],j.corrected_kind||"",value=>{j.corrected_kind=value;dirty();})));
    const subject=node("input");subject.value=j.corrected_subject||"";subject.oninput=()=>{j.corrected_subject=subject.value;dirty();};
    details.append(label("Corrected subject (copy the exact source phrase)",subject));
    const note=node("textarea");note.value=j.note||"";note.maxLength=3000;note.oninput=()=>{j.note=note.value;dirty();};details.append(label("Reason / ambiguity / missing qualification",note));card.append(details);host.append(card);
  });
  renderOmissions();locks();
}
async function openItem(iid) {
  cleanRequired();const cid=state.campaign.campaign.campaign_id;
  renderDetail(await api(query(cid,iid)));renderCampaign(state.campaign);
  history.replaceState(null,"",`/legal-review?${new URLSearchParams({campaign:cid,item:iid})}`);
  message(state.detail.extraction_error || (state.detail.review ? `Loaded saved review revision ${state.detail.version}.` : state.detail.receipt ? "Read the full source, assess each claim, and record any missed rules. No human judgments are prefilled." : "This document is frozen and waiting for complete extraction."),!!state.detail.extraction_error&&state.detail.extraction_status!=="partial");
}
async function processItems(single=false) {
  cleanRequired();const budget=single?1:Number($("#budget").value);
  if(!Number.isInteger(budget)||budget<1||budget>50)throw new Error("Choose a batch budget from 1 to 50.");
  const cid=state.campaign.campaign.campaign_id;state.busy=true;state.stop=false;locks();
  let calls=0;const failed=new Set();
  try {
    for(;calls<budget&&!state.stop;calls++) {
      const item=single?state.campaign.items.find(i=>i.item_id===state.detail.item_id):state.campaign.items.find(i=>!failed.has(i.item_id)&&["pending","partial"].includes(i.extraction_status));
      if(!item)break;
      message(`Processing ${item.file} · batch ${calls+1} of ${budget}. Stop takes effect after this request.`);
      try {
        const detail=await api(`/api/legal-review/${cid}/${item.item_id}/process`,{});
        if(state.detail?.item_id===item.item_id)renderDetail(detail);
        if(!["ready","partial","pending"].includes(detail.extraction_status))failed.add(item.item_id);
      } catch(e) { failed.add(item.item_id);message(e.message,true); }
      await refreshCampaign();
    }
    message(`${state.stop?"Stopped":"Processing finished"} after ${calls} batch requests. ${failed.size?failed.size+" document(s) need attention. ":""}Completed extractions are ready for your review.`,failed.size>0);
  } finally {state.busy=false;locks();}
}
$("#create-form").onsubmit=e=>{e.preventDefault();action(async()=>{
  cleanRequired();state.busy=true;locks();message("Hashing the corpus and freezing the selected documents…");
  try {const r=await api("/api/legal-review",{path:$("#corpus").value,sample_size:Number($("#sample-size").value),seed:$("#seed").value||null,model:$("#model").value});
    await loadCampaigns(r.campaign.campaign_id);await openCampaign(r.campaign.campaign_id);
  } finally {state.busy=false;locks();}
});};
$("#campaign-select").onchange=()=>action(async()=>{const cid=$("#campaign-select").value;try{if(cid)await openCampaign(cid);}catch(e){$("#campaign-select").value=state.campaign?.campaign.campaign_id||"";throw e;}});
$("#process").onclick=()=>action(()=>processItems());$("#retry").onclick=()=>action(()=>processItems(true));
$("#stop").onclick=()=>{state.stop=true;message("Stopping after the current batch. Already completed batches remain saved.");};
$("#reviewer").value=localStorage.getItem("grap-reviewer")||"";
$("#reviewer").oninput=()=>{localStorage.setItem("grap-reviewer",$("#reviewer").value);if(state.detail?.receipt)dirty();};
$("#complete").onchange=dirty;
$("#save").onclick=()=>action(async()=>{
  const cid=state.campaign.campaign.campaign_id,d=state.detail;
  state.busy=true;locks();
  try {
  const saved=await api(`/api/legal-review/${cid}/${d.item_id}/save`,{revision_id:d.revision_id,expected_version:d.version,reviewer:$("#reviewer").value,judgments:state.judgments,omissions:state.omissions,complete:$("#complete").checked});
  renderDetail(saved);await refreshCampaign();message(`Review saved as revision ${saved.version}. The model receipt remains unchanged.`);
  } finally {state.busy=false;locks();}
});
$("#discard").onclick=()=>action(async()=>{renderDetail(await api(query(state.campaign.campaign.campaign_id,state.detail.item_id)));message("Unsaved edits discarded.");});
$("#omit").onclick=()=>action(async()=>{
  const selection=window.getSelection(),pre=$("#source");
  if(!selection.rangeCount||selection.isCollapsed)throw new Error("Select the missed passage in the full source first.");
  const range=selection.getRangeAt(0);if(!pre.contains(range.commonAncestorContainer))throw new Error("Select a passage inside the source document.");
  const prefix=range.cloneRange();prefix.selectNodeContents(pre);prefix.setEnd(range.startContainer,range.startOffset);
  const quote=range.toString(),start=Array.from(prefix.toString()).length,end=start+Array.from(quote).length;
  if(!quote.trim())throw new Error("Select source text, not only whitespace.");
  state.omissions.push({start,end,quote,note:""});dirty();renderOmissions();message("Missed passage added to your draft. Add a note and save the review.");
});
$("#export").onclick=()=>action(async()=>{
  cleanRequired();const cid=state.campaign.campaign.campaign_id,result=await api(query(cid,null,{export:"true"}));
  const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:"application/json"}));const a=node("a");a.href=url;a.download=`grap-legal-review-${cid}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message("Export includes the frozen manifest, source text, receipts, latest judgments and review history.");
});
window.addEventListener("beforeunload",e=>{if(state.dirty){e.preventDefault();e.returnValue="";}});
action(async()=>{
  const health=await api("/api/health");$("#corpus").value=health.repo_root+"\\real-data-law-ro";
  const campaigns=await loadCampaigns(),requested=new URLSearchParams(location.search).get("campaign");
  const chosen=campaigns.find(c=>c.campaign_id===requested)||campaigns[0];
  if(chosen)await openCampaign(chosen.campaign_id);else{$("#setup").open=true;message("Start with a random document sample. Sampling does not load an LLM.");}
});
