/* VELA UI: same-origin API only. No credentials or provider calls in the browser. */
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (s) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
const taskNames = {math:'数学 · 圆形装填',code:'代码 · Top-K 优化',paper:'论文 · 引文问答'};
const policies = {tierlab:'VELA 自适应协同',all_cloud:'全大模型',all_local:'纯小模型',cascade:'传统级联',static_split:'固定分工',no_advice_retry:'无顾问重试'};
const statuses = {queued:'排队中',running:'运行中',completed:'已完成',failed:'运行失败',interrupted:'服务重启后中断',unknown:'历史记录 · 状态未确认'};
const actions = {propose_local:'小模型候选',cloud_propose:'大模型候选',hint:'Hint · 方向提示',solve:'Solve · 完整解答',diagnose:'诊断',replan:'重规划',widen:'扩大并行',stop:'停止',final_eval:'独立最终评估',lesson_hit:'教训复用'};
const stops = {max_steps_reached:'达到最大轮数',consecutive_stagnation_stopped:'连续无提升',consultation_ladder_exhausted:'咨询阶梯已用完',cloud_budget_exceeded_exception:'预算达到上限',max_wall_clock_exceeded:'达到时间上限',low_consultation_roi_threshold:'咨询收益不足'};
let selected = null, entries = [], snapshot = null, timer, polling = null;
const num = (n, digits=4) => Number.isFinite(Number(n)) ? Number(n).toLocaleString('en-US',{maximumFractionDigits:digits}) : '—';
async function api(path, options={}) {
  const response = await fetch(new URL(path, location.href), {cache:'no-store',...options});
  const type = response.headers.get('content-type') || '';
  const body = type.includes('json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(typeof body === 'object' ? (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body)) : `请求失败 (${response.status})`);
  return body;
}
function notice(message) { $('notice').textContent = message; $('notice').hidden = !message; }
function safeRead() { try { return localStorage.getItem('vela-selected-run'); } catch { return null; } }
function remember(id) { try { localStorage.setItem('vela-selected-run',id); } catch {} }
async function history() {
  try {
    const data = await api('runs');
    $('connection').textContent = '● 服务已连接';
    $('history').innerHTML = data.runs.length ? data.runs.map(r => `<button class="history-item ${r.run_id===selected?'selected':''}" data-run="${esc(r.run_id)}"><span class="dot">·</span>${esc(taskNames[r.task] || r.task || '历史实验')}<small>${esc(policies[r.policy]||r.policy||r.run_id)} · ${esc(statuses[r.status]||r.status)}</small></button>`).join('') : '<p>还没有实验，开始第一次探索。</p>';
    if (!selected && data.runs.length) await choose(data.runs[0].run_id);
  } catch(e) { $('connection').textContent = '● 连接失败'; notice(e.message); }
}
async function choose(id) {
  clearTimeout(timer);
  if (polling) polling.abort();
  selected=id; remember(id); entries=[];
  document.querySelectorAll('[data-run]').forEach(el=>el.classList.toggle('selected',el.dataset.run===id));
  await poll();
}
async function poll() {
  const id=selected;
  if (!id) return;
  const control=new AbortController(); polling=control;
  try {
    const data=await api(`runs/${encodeURIComponent(id)}`, {signal:control.signal});
    const offset=Math.max(0,(data.ledger_count||0)-500);
    const ledger=await api(`runs/${encodeURIComponent(id)}/ledger?offset=${offset}&limit=500`,{signal:control.signal});
    if (selected!==id || control.signal.aborted) return;
    snapshot=data; entries=ledger.entries;
    render(data,ledger.total); notice('');
    if (['queued','running'].includes(data.status)) timer=setTimeout(poll,1500);
    else await history();
  } catch(e) {
    if (e.name==='AbortError') return;
    notice(`无法读取实验：${e.message}`);
    timer=setTimeout(poll,5000);
  }
}
function render(data,total) {
  const s=data.summary||{}, req=data.request||{};
  $('run-status').className=`status ${data.status}`;
  $('run-status').textContent=statuses[data.status]||data.status;
  $('run-title').textContent=taskNames[data.task]||data.task||data.run_id;
  $('run-meta').textContent=`${policies[data.policy]||s.policy||'历史实验'} · seed ${data.seed??s.seed??'—'} · ${data.run_id}`;
  $('score').textContent=data.summary?num(s.final_score):'—';
  $('score-unit').textContent=({math:'归一化半径和',code:'验证得分 / 性能加速比',paper:'答案正确率'})[data.task]||'任务原生得分';
  $('score-detail').textContent=data.status==='completed'?'包含最终评估；开发曲线另列':'来自验证器的当前记录';
  $('cost').textContent=data.summary?`¥${num(s.total_cloud_cny,6)}`:'—';
  $('cost-detail').textContent=`GPU忙时费用 ¥${num(s.total_gpu_cny||0,6)} · 租赁折算 ¥${num(s.total_gpu_lease_cny||0,6)}`;
  $('wall').textContent=data.summary?`${num(s.total_wall_s,1)} s`:'—';
  $('wall-detail').textContent=`其中验证 ${num(s.total_verify_s||0,2)} s`;
  $('calls').textContent=data.summary?`${s.cloud_calls??0} / ${s.local_calls??0}`:'—';
  $('stop-reason').textContent=data.status==='failed'?`失败：${data.error||'未知原因'}`:(stops[s.stop_reason]||s.stop_reason||statuses[data.status]);
  $('ba-auc').textContent=`BA-AUC ${num(s.ba_auc)}`;
  $('backend-note').textContent=data.backend==='mock'?'MOCK 演示 · 合成费用与结果，不代表真实模型效果。':data.backend==='real'?`REAL 实验 · 小模型后端：${req.weak_backend==='cloud_small'?'云端 API':'本地 vLLM'} · 按账本实际记录。`:'历史实验 · 执行模式未知，请结合原始记录解释。';
  $('report').hidden=data.status!=='completed';
  $('report').href=new URL(`runs/${encodeURIComponent(data.run_id)}/report`,location.href).href;
  $('export').disabled=!entries.length;
  $('ledger-caption').textContent=total>500?`共 ${total} 条，显示最近 500 条。点击一行查看完整证据。`:`共 ${total} 条决策。点击一行查看完整证据。`;
  const latest=entries.at(-1);
  $('feedback-action').textContent=latest?(actions[latest.action]||latest.action):'WAITING';
  if(latest){
    const ex=latest.extra||{};
    const feedback=ex.final_feedback||ex.feedback||ex.failure_detail||data.error||latest.trigger;
    $('feedback').innerHTML=`<h3>${esc(actions[latest.action]||latest.action)}</h3>${ex.failure_signature?`<span class="signature">${esc(ex.failure_signature)}</span>`:''}<p>${esc(feedback)}</p><p>得分 ${esc(num(latest.score_before))} → ${esc(num(latest.score_after))}</p>${latest.credited_to?`<p>建议归因：${esc(latest.credited_to)}</p>`:''}`;
  } else if(data.error) $('feedback').textContent=data.error;
  renderLedger(); renderChart();
}
function renderLedger(){
  const filter=$('action-filter').value;
  const rows=entries.map((r,i)=>({r,i})).filter(({r})=>filter==='all'||r.action===filter);
  $('ledger').innerHTML=rows.length?rows.map(({r,i})=>`<tr tabindex="0" data-index="${i}" aria-label="步骤 ${r.step}，${esc(actions[r.action]||r.action)}"><td>${r.step}</td><td>${esc(actions[r.action]||r.action)}<small>${esc(r.trigger)}</small></td><td>${esc(num(r.score_after))}</td><td>¥${esc(num(r.cloud_cny,6))}</td><td class="${r.verified?'verified':'rejected'}">${r.action==='stop'?'已停止':r.verified?'已验证':'无已验证提升'}</td></tr>`).join(''):'<tr><td colspan="5" class="empty">暂无符合条件的决策</td></tr>';
}
function renderChart(){
  const points=entries.filter(r=>!['final_eval','stop'].includes(r.action));
  if(!points.length){$('chart').innerHTML='<p>暂无开发阶段得分记录</p>';return;}
  const values=[Number(points[0].score_before)||0,...points.map(r=>Number(r.score_after)||0)];
  const max=Math.max(...values,0.01), min=Math.min(0,...values), span=max-min||1;
  const coords=values.map((v,i)=>[45+i/(values.length-1||1)*565,155-(v-min)/span*130]);
  const path=coords.map(([x,y],i)=>`${i?'L':'M'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
  $('chart').innerHTML=`<svg viewBox="0 0 640 190" role="img" aria-label="开发阶段得分随决策变化"><line x1="45" x2="610" y1="155" y2="155" stroke="#dce2d6"/><line x1="45" x2="610" y1="90" y2="90" stroke="#edf0e8"/><line x1="45" x2="610" y1="25" y2="25" stroke="#edf0e8"/><text x="0" y="29" font-size="10" fill="#78837b">${esc(num(max,2))}</text><text x="8" y="159" font-size="10" fill="#78837b">${esc(num(min,2))}</text><path d="${path}" stroke="#bd4d32" stroke-width="2" fill="none"/>${coords.map(([x,y],i)=>`<circle cx="${x}" cy="${y}" r="3" fill="#bd4d32"><title>决策 ${i}：${values[i]}</title></circle>`).join('')}<text x="45" y="181" font-size="10" fill="#78837b">开始</text><text x="563" y="181" font-size="10" fill="#78837b">最近决策</text></svg>`;
}
function detail(index){$('detail-json').textContent=JSON.stringify(entries[index],null,2);$('detail').showModal();}
$('new-run').onclick=()=>{$('form-error').textContent='';$('setup').showModal();};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
document.querySelectorAll('dialog').forEach(el=>el.addEventListener('click',e=>{if(e.target===el){const r=el.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)el.close();}}));
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{document.body.classList.toggle('ledger-only',b.dataset.view==='ledger');document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('active',el===b));$('breadcrumb').textContent=b.dataset.view==='ledger'?'决策账本':'概览';});
$('history').onclick=e=>{const el=e.target.closest('[data-run]');if(el)choose(el.dataset.run);};
$('ledger').onclick=e=>{const el=e.target.closest('[data-index]');if(el)detail(Number(el.dataset.index));};
$('ledger').onkeydown=e=>{if(['Enter',' '].includes(e.key)){const el=e.target.closest('[data-index]');if(el){e.preventDefault();detail(Number(el.dataset.index));}}};
$('action-filter').onchange=renderLedger;
$('refresh').onclick=history;
$('backend').onchange=()=>{const real=$('backend').value==='real';$('submit-run').textContent=real?'启动真实实验（会计费） →':'启动演示实验 →';$('mode-help').textContent=real?'真实模式使用服务端的模型与密钥配置，会产生 API 费用。默认配置中的占位单价必须先填写。本地 vLLM 需已启动。':'Mock 不调用真实模型，费用为合成数值，仅用于验证流程。';};
$('run-form').onsubmit=async(e)=>{
  e.preventDefault(); const data=Object.fromEntries(new FormData(e.target));
  for(const name of ['seed','k_init','max_steps','cloud_budget_cny','max_wall_clock_s','local_max_tokens','hint_max_tokens','local_patience','code_max_speedup'])data[name]=Number(data[name]);
  data.enable_lessons=e.target.elements.enable_lessons.checked;
  $('submit-run').disabled=true; $('form-error').textContent='';
  try{const result=await api('runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});$('setup').close();await choose(result.run_id);await history();}
  catch(error){$('form-error').textContent=error.message;}
  finally{$('submit-run').disabled=false;}
};
$('export').onclick=async()=>{
  try{
    const runId=selected;
    let offset=0,all=[];
    while(true){const batch=await api(`runs/${encodeURIComponent(runId)}/ledger?offset=${offset}&limit=500`);all.push(...batch.entries);if(batch.next_offset>=batch.total||batch.next_offset<=offset)break;offset=batch.next_offset;}
    const url=URL.createObjectURL(new Blob([all.map(r=>JSON.stringify(r)).join('\n')+'\n'],{type:'application/x-ndjson'}));
    const a=document.createElement('a');a.href=url;a.download=`${runId}-ledger.jsonl`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(e){notice(e.message);}
};
(async()=>{const old=safeRead();if(old)await choose(old);await history();})();
