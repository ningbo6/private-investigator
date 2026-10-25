'use strict';
const $ = id => document.getElementById(id);
const HEADERS = {'Content-Type': 'application/json', 'X-Company-Detective': '1'};
let report = null, busy = false, ocrAvailable = false, ocrAttempted = false;
const reportActions = ['copy', 'download', 'markdown', 'watch', 'share'];
const compareReports = new Map();

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
}
function notice(text) { $('notice').textContent = text; }
function setBusy(value, message = '') {
  busy = value;
  document.querySelectorAll('button, input, select, textarea').forEach(el => {
    if (!el.classList.contains('tab')) el.disabled = value;
  });
  syncActions();
  if (message) notice(message);
}
function syncActions() {
  $('ocrbtn').disabled = busy || !ocrAvailable;
  $('ocrconfirm').disabled = busy || !$('ocrcompany').value.trim() || !$('ocrtext').value;
  reportActions.forEach(id => $(id).disabled = busy || !report);
  document.querySelectorAll('[data-follow]').forEach(b => b.disabled = busy || !report);
  if (report && report.meta.status === 'failed') $('watch').disabled = true;
}
async function api(path, body) {
  const options = body === undefined ? {} : {method: 'POST', headers: HEADERS, body: JSON.stringify(body)};
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok || !data.ok) throw Error(data.error || '服务未完成请求');
  return data;
}
async function action(work, message) {
  if (busy) return;
  setBusy(true, message);
  try {
    await work();
    if ($('notice').textContent === message) notice('');
  }
  catch (e) { notice(e.message); }
  finally { setBusy(false); await refreshStats(); }
}
function setProgress(r) {
  const ids = r?.dimensions?.map(d=>d.id) || (r ? ['identity','technology','business','product','quality','compliance','employer','sustainability'] : ['product','business','technology','risk','organization']);
  const base = r ? ids.map(id => r.routes.filter(x => (x.dimension_id || x.id) === id && !x.id.startsWith('followup_'))) : [];
  $('progress').innerHTML = base.map(routes => {
    const ok = routes.some(x => x.ok && x.matched_count > 0);
    const failed = routes.length > 0 && routes.every(x => !x.ok);
    return `<i class="${ok ? 'done' : failed ? 'failed' : ''}" title="${ok ? '已找到名称相关线索' : failed ? '检索失败' : routes.length ? '已查未命中' : '未调查'}"></i>`;
  }).join('');
}
function evidence(p) {
  const tags = p.review_terms?.length ? `标题含“${p.review_terms.join('、')}”（仅待核）` : p.relation;
  return `<article class="ev ${p.related ? '' : 'unmatched'}"><div class="evtags">${esc(tags)}</div>
    <a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>
    <details><summary>查看元宝搜索摘要</summary><p>${esc(p.passage || '未提供摘要')}</p></details>
    <small>${esc(p.domain)} · ${esc(p.source_type)} · 来源日期 ${esc(p.date)}<br>${esc(p.relation)} · ${p.requested_site_match===false?'不属于指定权威站点 · ':p.requested_site_match===true?'指定站点命中 · ':''}${esc(p.verification)}</small></article>`;
}
function renderRawRoutes() {
  if (!report) return;
  const mode = $('filter').value, keyword = $('keyword').value.trim().toLowerCase();
  $('rawroutes').innerHTML = report.routes.map(r => {
    const pages = r.pages.filter(p => {
      if (mode === 'related' && !p.related) return false;
      if (mode === 'primary' && (!p.related || !['政府公开信息', '交易所/披露平台'].includes(p.source_type))) return false;
      if (mode === 'company' && (!p.related || !p.source_type.startsWith('用户指定'))) return false;
      return !keyword || `${p.title} ${p.passage}`.toLowerCase().includes(keyword);
    }).sort((a, b) => Number(b.related) - Number(a.related));
    return `<section class="route" id="route-${r.id}"><div class="routetop"><h3>${esc(r.name)}</h3>
      <span class="badge ${!r.ok ? 'bad' : r.matched_count ? 'warn' : 'neutral'}">${esc(r.status)}</span></div>
      <p class="muted">${esc(r.measure)}<br><b>待核：</b>${esc(r.check)}</p>
      <details class="trace"><summary>检索凭据 · ${r.ok ? (r.meta.cached ? '缓存' : '实时') : '失败'} · ${r.pages.length} 条 / 名称相关 ${r.matched_count} 条</summary>
      <p>元宝检索词：${esc(r.query_used)}<br>站点参数：${esc(r.site_filter || '未限制')}<br>取回时间：${esc(r.meta.fetched_at || '失败')}<br>RequestId：${esc(r.meta.request_id || '未提供')}<br>本次耗时：${r.meta.elapsed} 秒</p></details>
      ${!r.ok ? `<p class="error">${esc(r.error)}</p>` : ''}
      ${pages.map(evidence).join('') || '<p class="muted">当前筛选下没有可展示线索。可切换“全部”查看未匹配候选；不要据此认定公司不存在或没有风险。</p>'}</section>`;
  }).join('');
}
const FIVE_DIMENSIONS = [
  {id:'product',name:'业务与产品'}, {id:'business',name:'经营与财务'},
  {id:'technology',name:'技术与创新'}, {id:'risk',name:'风险与合规'},
  {id:'organization',name:'组织与责任'}
];
function reportDimensions(r) {
  return r.dimensions ? FIVE_DIMENSIONS : [
    {id:'identity',name:'主体治理'}, {id:'technology',name:'专利研发'},
    {id:'business',name:'经营财务'}, {id:'product',name:'产品客户'},
    {id:'quality',name:'质量安全'}, {id:'compliance',name:'合规司法'},
    {id:'employer',name:'组织雇主'}, {id:'sustainability',name:'环境责任'}
  ];
}
function sourceKey(p) {
  // Keep query parameters: they can identify different documents.
  try { const url=new URL(p.url);url.hash='';return url.href; }
  catch { return p.id || p.url; }
}
function groupEvidence(routes) {
  const groups=new Map();
  for(const route of routes) for(const p of route.pages||[]) {
    const key=sourceKey(p);
    if(!groups.has(key))groups.set(key,{page:p,variants:[],routes:new Set(),ids:new Set()});
    const g=groups.get(key);g.ids.add(p.id);g.routes.add(route.name||route.id);
    if(!g.variants.some(v=>v.title===p.title&&v.passage===p.passage))g.variants.push(p);
    if(p.related&&!g.page.related)g.page=p;
  }
  const clusters=new Map();
  for(const g of groups.values()){
    const normalized=(g.page.passage||'').replace(/\s/g,'');
    const key=normalized.length>=100 ? normalized : sourceKey(g.page);
    if(!clusters.has(key))clusters.set(key,[]);
    clusters.get(key).push(g);
  }
  return [...clusters.values()];
}
function renderRoutes() {
  if(!report)return;
  const mode=$('filter').value, keyword=$('keyword').value.trim().toLowerCase();
  const claims=new Map((report.briefing?.claims||[]).map(c=>[c.topic,c]));
  $('routes').innerHTML='<h3>调查方向 · 结论与证据</h3><p class="muted">已引用材料优先，其次为监管披露和补充材料。同一网址合并，完全相同的长摘要折叠；不将转载当作独立印证。仅据搜索摘要，未自动核验全文。</p>'+reportDimensions(report).map(d=>{
    const routes=report.routes.filter(x=>(x.dimension_id||x.id)===d.id);
    const claim=claims.get(d.id);
    const cited=new Set((claim?.citations||[]).map(c=>c.evidence_id));
    const visible=p=>(mode!=='related'||p.related)&&(mode!=='primary'||(p.related&&['政府公开信息','交易所/披露平台'].includes(p.source_type)))&&(mode!=='company'||(p.related&&p.source_type.startsWith('用户指定')))&&(!keyword||`${p.title} ${p.passage}`.toLowerCase().includes(keyword));
    const clusters=groupEvidence(routes).map(cluster=>cluster.filter(g=>g.variants.some(visible))).filter(c=>c.length);
    const rank=g=>[...g.ids].some(id=>cited.has(id))?0:['政府公开信息','交易所/披露平台'].includes(g.page.source_type)?1:g.page.related?2:3;
    clusters.forEach(c=>c.sort((a,b)=>rank(a)-rank(b)));
    clusters.sort((a,b)=>rank(a[0])-rank(b[0]));
    const show=g=>`${evidence(g.page)}<small>检索路径：${esc([...g.routes].join(' / '))}</small>${g.variants.length>1?`<details><summary>同一来源的其他摘要（${g.variants.length-1}）</summary>${g.variants.filter(v=>v!==g.page).map(evidence).join('')}</details>`:''}`;
    const assessment=report.credibility?.assessments?.find(a=>a.task_id===d.id);
    return `<section class="route dimension-evidence" id="dimension-${d.id}"><h3>${esc(d.name)}</h3><p>${esc(claim?.answer||'暂无可引用结论；缺证不代表公司没有相关业务或风险。')}</p>${(claim?.citations||[]).map(c=>`<blockquote>${esc(c.quote)}<br><a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">查看结论引用 ${esc(c.evidence_id)}</a></blockquote>`).join('')}<p class="muted">${routes.length?'已检索 '+routes.length+' 路，其中 '+routes.filter(x=>!x.ok).length+' 路失败':'未调查'} · 当前筛选 ${clusters.reduce((n,c)=>n+c.length,0)} 个去重来源 / ${clusters.length} 组内容</p>${d.id==='technology'?renderPatentDetails(assessment?.patent_details):''}${clusters.map(c=>`<div class="source-group">${show(c[0])}${c.length>1?`<details><summary>相同摘要的其他来源（${c.length-1}，非独立印证）</summary>${c.slice(1).map(show).join('')}</details>`:''}</div>`).join('')||'<p class="muted">当前筛选下没有可展示证据。</p>'}</section>`;
  }).join('');
  renderRawRoutes();
}
function arrangeReport(r) {
  const parent=$('summary').parentElement;
  if(!$('identitycheck')){const el=document.createElement('section');el.id='identitycheck';el.className='card';parent.append(el);}
  const identity=r.routes.find(x=>x.id==='identity');
  $('identitycheck').innerHTML=`<h3>公司是谁 · ${esc(r.company)}</h3><p>${esc(r.identity_status||'主体仍待核验')}</p><small>${!identity?'主体未检查':!identity.ok?'主体检索失败':identity.matched_count?'找到名称线索，名称匹配不等于工商认证':'未找到名称明确线索'} · 主营业务见简报；同名和上下文不足材料单独隔离。</small>`;
  const auxiliary=document.querySelector('.auxiliary');
  const grid=parent.closest('.grid');
  grid.classList.add('report-ready');
  grid.append(auxiliary);
  if(!auxiliary.dataset.positioned){auxiliary.open=true;auxiliary.dataset.positioned='1';}
  if(!$('rawroutes')){const el=document.createElement('details');el.id='rawdetails';el.innerHTML='<summary>完整检索记录与原始返回（含重复候选）</summary><div id="rawroutes"></div>';parent.append(el);}
  if(!$('processdetails')){const el=document.createElement('details');el.id='processdetails';el.innerHTML='<summary>查看自动拆解、轮次和权威来源核对过程</summary>';parent.append(el);el.append($('deepreport'));}
  [$('identitycheck'),$('summary'),$('briefing'),$('casework'),$('filters'),$('routes'),$('unattributed'),$('processdetails'),$('rawdetails')].forEach(el=>parent.append(el));
  requestAnimationFrame(sizeWorkspace);
}
function sizeWorkspace() {
  const grid=document.querySelector('#investigate .grid');
  if(!grid||grid.closest('.hidden'))return;
  const top=grid.getBoundingClientRect().top+window.scrollY;
  grid.style.setProperty('--workspace-top', `${Math.round(top)}px`);
}
window.addEventListener('resize',sizeWorkspace);
document.fonts.ready.then(sizeWorkspace);
function render(r) {
  report = r;
  arrangeReport(r);
  const state = {complete:'搜索完成', partial:'部分路线失败', failed:'全部检索失败'}[r.meta.status];
  $('title').textContent = `${r.company} · ${state}`;
  $('live').textContent = `● ${r.meta.success_count}/${r.meta.route_count} 路成功`;
  $('context').textContent = `报告 ${r.id} ｜ ${r.created_at} ｜ 去重 ${r.meta.unique_count} 条 ｜ 名称相关 ${r.meta.matched_count} 条 ｜ 缓存 ${r.meta.cached_routes} 路 ｜ ${r.identity_status}`;
  $('summary').textContent = r.summary;
  $('filters').classList.remove('hidden');
  $('watchstate').textContent = '';
  $('watch').textContent = '加入复查'; $('copy').textContent = '复制摘要';
  renderCases(r); renderBriefing(r); renderRadar(r); renderDeep(r); setProgress(r); renderRoutes(); syncActions();
}
function resetReport() {
  if (busy) return;
  report = null;
  document.querySelector('#investigate .grid').classList.remove('report-ready');
  $('title').textContent = '等待投递公司线索';
  $('live').textContent = '● 元宝搜索待命';
  $('context').textContent = '深度模式自动拆解、追查、交叉核对，最多 12 次元宝搜索；24 小时内相同参数优先复用缓存。';
  $('summary').textContent = '请输入公司名称；别名只用于候选匹配，不代表关联主体已经认证。';
  $('casework').replaceChildren(); $('unattributed').replaceChildren(); $('briefing').replaceChildren(); $('scores').replaceChildren(); $('routes').replaceChildren(); $('deepreport').replaceChildren(); $('radar').replaceChildren(); $('filters').classList.add('hidden'); $('watchstate').textContent = '';
  for(const id of ['identitycheck','rawroutes'])if($(id))$(id).replaceChildren();
  $('error').textContent = ''; setProgress(); syncActions();
}
function fillForm(cfg) {
  $('company').value = cfg.company; $('aliases').value = cfg.aliases.join('，');
  $('domain').value = cfg.official_domain; $('freshness').value = cfg.freshness; $('question').value=cfg.question||''; $('mode').value=cfg.mode||'deep';
}
async function openReport(id) {
  const data = await api('/api/report/' + id);
  fillForm(data.report.config); showTab('investigate'); render(data.report);
}
function showTab(name) {
  document.querySelectorAll('.tab').forEach(el => el.classList.toggle('active', el.dataset.tab === name));
  document.querySelectorAll('.tabpage').forEach(el => el.classList.toggle('hidden', el.id !== name));
  if (name === 'history') history().catch(e => notice(e.message));
  if (name === 'watches') { watches().catch(e => notice(e.message)); refreshAlerts(); }
}
async function investigate() {
  const company = $('company').value.trim();
  if (company.length < 2) return $('error').textContent = '请输入至少两个字符的公司名称';
  resetReport();
  await action(async () => {
    $('title').textContent = $('mode').value==='deep'?'正在检索五维公司资料并发现待核疑点…':'正在执行主体前置检查与五维元宝搜索…';
    const data = await api('/api/search', {company, aliases: $('aliases').value.split(/[,，]/).map(s => s.trim()).filter(Boolean), official_domain: $('domain').value.trim(), freshness: $('freshness').value, fresh:false, question:$('question').value.trim(), mode:$('mode').value});
    render(data.report);
    notice(data.report.meta.status === 'failed' ? '检索全部失败，不是“没有风险”。' : '调查已保存。请展开摘要并点回原文复核。');
  }, '正在进行公司调查，请勿重复提交；深度调查最多 12 次元宝搜索。');
  if (!report) { $('title').textContent = '调查未完成'; $('live').textContent = '● 请求失败'; }
}
function download(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], {type}));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function history() {
  const data = await api('/api/history');
  $('historylist').innerHTML = data.items.map(r => `<div class="historyrow"><div><b>${esc(r.company)}</b><small>${esc(r.created_at)} · ${r.meta.success_count}/${r.meta.route_count} 路成功 · 名称相关 ${r.meta.matched_count} 条</small><p class="muted">${esc(r.conclusion)}</p></div><button data-open="${r.id}">打开</button></div>`).join('') || '<p class="muted">尚无报告。</p>';
}
async function watches() {
  const data = await api('/api/watches');
  $('watchlist').innerHTML = data.items.map(w => `<div class="historyrow"><div><b>${esc(w.company)}</b><small>基线：${w.last_report_id} ｜ ${w.enabled?'每日 '+w.schedule_time+' 自动复查':'自动复查未开启'}<br>下次：${esc(w.next_run||'未安排')} ｜ 最近状态：${esc(w.last_status)}</small></div><div class="row"><button data-open="${w.last_report_id}">基线报告</button><button data-recheck="${w.id}">立即实时复查</button><button data-schedule="${w.id}" data-enabled="${w.enabled?'0':'1'}">${w.enabled?'暂停每日复查':'开启每日复查'}</button><button data-unwatch="${w.id}">移出清单</button></div></div>`).join('') || '<p class="muted">先打开一份报告，点击“加入复查”。</p>';
  if (busy) $('watchlist').querySelectorAll('button').forEach(b => b.disabled = true);
}
function renderDiff(delta) {
  $('diff').innerHTML = `<h3>搜索快照变化</h3><p class="muted">${esc(delta.note)}${delta.comparable ? '' : ' 本次有失败路线，差异不完整，未更新基线。'}</p>
    <div class="row"><button data-open="${delta.new_id}">打开本次报告</button></div>` + [['added','新增返回'], ['missing','本次未返回'], ['changed','摘要或日期变化']].map(([key,label]) => `<details><summary>${label}：${delta[key].length} 条</summary>${delta[key].map(evidence).join('') || '<p class="muted">无</p>'}</details>`).join('');
}
async function compare() {
  const a = $('a').value.trim(), b = $('b').value.trim();
  if (!a || !b || a === b) return notice('请输入两个不同的公司名称。');
  await action(async () => {
    $('compareresult').textContent = '正在分别检索，两家公司最多调用 12 次元宝搜索。';
    const data = await api('/api/compare', {a:{company:a,mode:'overview'}, b:{company:b,mode:'overview'}});
    data.reports.forEach(r => compareReports.set(r.id, r));
    const [left,right] = data.reports;
    $('compareresult').innerHTML = `<div class="tablewrap"><table><caption>本次搜索覆盖对比（不是实力排名）</caption><thead><tr><th>调查维度</th><th>${esc(left.company)}</th><th>${esc(right.company)}</th></tr></thead><tbody>` + left.routes.map((r,i) => `<tr><th>${esc(r.name)}</th>${[r,right.routes.find(y=>y.id===r.id)].map(x => `<td>${x?.ok ? `${x.matched_count} 条名称相关线索<br><span class="muted">尚待原文核验</span>` : '检索失败'}</td>`).join('')}</tr>`).join('') + `</tbody></table></div><div class="row">${data.reports.map(r => `<button data-open="${r.id}">打开 ${esc(r.company)} 的完整证据</button>`).join('')}</div><p class="muted">公司大小、公开披露义务、别名覆盖和搜索召回会影响数量；不得用数量比较公司好坏。</p>`;
    notice('对比完成，可分别打开原始报告。');
  }, '正在比较两家公司。');
}
async function refreshStats() {
  try {
    const data = await api('/api/status');
    $('callstats').textContent = `${data.scheduler?.running?'每日调度运行中':'每日调度未运行'} ｜ 服务调用尝试 ${data.stats.attempts} 次 / 成功 ${data.stats.success} 次（非账单）；证据提供方：${data.provider}`;
    ocrAvailable = Boolean(data.ocr);
    syncActions();
    if (data.ocr && !ocrAttempted) $('ocrstatus').textContent = `服务端 OCR 已就绪（${data.ocr_info?.engine || '本地引擎'} · 中英文）。识别后请核对，不会自动搜索。`;
    if (!data.ocr) $('ocrstatus').textContent = '服务端未启用 OCR，请参考 README 或手动输入。';
  } catch { ocrAvailable = false; syncActions(); $('callstats').textContent = '服务暂不可用'; }
}
async function ocr() {
  const file = $('image').files[0];
  if (!file || !['image/png','image/jpeg'].includes(file.type) || file.size > 5000000) return notice('请选择 5 MB 以下的 PNG/JPEG 截图。');
  await action(async () => {
    ocrAttempted = true;
    $('ocrtext').value = '';
    $('ocrcompany').value = ''; $('ocrcandidates').replaceChildren(); $('ocrconfirmation').classList.add('hidden');
    $('ocrstatus').textContent = '正在服务端识别截图…';
    const dataUrl = await new Promise((resolve,reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); });
    let data;
    try { data = await api('/api/ocr', {image: dataUrl.split(',')[1]}); }
    catch (e) { $('ocrstatus').textContent = `识别未完成：${e.message}`; throw e; }
    $('ocrtext').value = data.text;
    const companies=data.companies||[];
    $('ocrcandidates').innerHTML='<option value="">选择识别到的公司候选</option>'+companies.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('');
    $('ocrcompany').value=companies[0]||'';
    $('ocrconfirmation').classList.toggle('hidden', !data.text);
    $('ocrstatus').textContent = data.text ? (companies.length?'请核对下方公司名称；多家公司可切换，确认后直接开始调查。':'未可靠提取公司名称，请在下方补充后确认调查。') : '未识别出文字，请换清晰截图或手动输入。';
    notice('识别完成，尚未搜索；点击“确认公司并开始调查”才会调用元宝搜索。');
  }, '服务端 OCR 识别中…');
}
async function confirmOcrCompany() {
  if(busy||!$('ocrtext').value)return;
  const company=$('ocrcompany').value.trim();
  if(company.length<2)return notice('请核对并填写至少两个字符的公司名称。');
  // Clear the previous company's context instead of submitting stale aliases/questions.
  fillForm({company,aliases:[],official_domain:'',freshness:'',question:'',mode:'deep'});
  $('ocrconfirmation').classList.add('hidden');
  await investigate();
}
function shareImage() {
  if (!report) return;
  const c = document.createElement('canvas'); c.width = 1200; c.height = 1450;
  const x = c.getContext('2d'); x.fillStyle = '#f3f0e8'; x.fillRect(0,0,c.width,c.height);
  x.fillStyle = '#18363b'; x.fillRect(0,0,1200,16);
  x.font = 'bold 32px "PingFang SC", sans-serif'; x.fillText('公司侦探 / 元宝搜索证据卡',65,90);
  function wrap(text, y, font, color, maxLines=6) {
    x.font=font; x.fillStyle=color; let line='', n=0;
    for(const char of text) { if(x.measureText(line+char).width>1070){x.fillText(line,65,y);y+=44;line='';n++;if(n>=maxLines)return y;}line+=char; }
    if(line){x.fillText(line,65,y);y+=44;} return y;
  }
  let y=wrap(report.company,175,'bold 46px "PingFang SC", sans-serif','#18363b',3);
  y=wrap(report.conclusion,y+35,'28px "PingFang SC", sans-serif','#c65530',4);
  y+=25;
  report.routes.filter(r=>!r.id.startsWith('followup_')).forEach(r=>{x.font='26px "PingFang SC", sans-serif';x.fillStyle='#202628';x.fillText(r.name,65,y);x.fillStyle='#69716e';x.fillText(r.ok?`${r.matched_count} 条名称相关线索 · 待核`:'搜索失败',510,y);y+=52;});
  y=wrap('只表示本次公开检索覆盖，不是公司评分。摘要未作原文事实核验；搜索不到不表示不存在或没有风险。',y+30,'24px "PingFang SC", sans-serif','#69716e',4);
  y=wrap(`报告 ${report.id} | ${report.created_at}`,y+20,'21px "PingFang SC", sans-serif','#69716e',2);
  wrap('#元宝搜索 #WaytoAGI #AI切磋大会 #1024超能Coding周末',y+35,'23px "PingFang SC", sans-serif','#18363b',3);
  c.toBlob(blob=>{if(blob)download(`公司侦探-${report.id}.png`,blob,'image/png');},'image/png');
}

$('search').onclick = investigate;
$('clear').onclick = () => { if(busy)return; ['company','aliases','domain','question'].forEach(id=>$(id).value=''); resetReport(); };
['company','aliases','domain','freshness','question','mode'].forEach(id=>$(id).addEventListener('input',resetReport));
$('filter').onchange=renderRoutes; $('keyword').oninput=renderRoutes;
$('refresh').onclick=()=>history().catch(e=>notice(e.message));
$('refreshwatches').onclick=()=>watches().catch(e=>notice(e.message));
$('comparebtn').onclick=compare; $('ocrbtn').onclick=ocr; $('share').onclick=shareImage;
$('ocrconfirm').onclick=confirmOcrCompany;
$('ocrcompany').oninput=syncActions;
$('ocrcandidates').onchange=()=>{if($('ocrcandidates').value)$('ocrcompany').value=$('ocrcandidates').value;syncActions();};
$('image').onchange=()=>{$('ocrtext').value='';$('ocrcompany').value='';$('ocrcandidates').replaceChildren();$('ocrconfirmation').classList.add('hidden');$('ocrstatus').textContent='请先识别新截图。';syncActions();};
$('copy').onclick=async()=>{if(!report)return;try{await navigator.clipboard.writeText(report.summary);notice('摘要已复制。');}catch{notice('浏览器拒绝剪贴板，请手动复制摘要。');}};
$('download').onclick=()=>{if(report)download(`公司侦探-${report.id}.json`,JSON.stringify(report,null,2),'application/json');};
$('markdown').onclick=()=>action(async()=>{const res=await fetch(`/api/report/${report.id}/markdown`);if(!res.ok)throw Error('导出失败');download(`公司侦探-${report.id}.md`,await res.text(),'text/markdown;charset=utf-8');notice('Markdown 报告已导出。');});
$('watch').onclick=()=>action(async()=>{await api('/api/watches',{id:report.id});$('watchstate').textContent='已保存基线；在“复查清单”中选择时间并开启每日自动复查。';notice('已加入复查清单，不会自动消耗额度。');});
document.addEventListener('click', e=>{
  const b=e.target.closest('button');if(!b||b.disabled)return;
  if(b.dataset.tab)showTab(b.dataset.tab);
  if(b.dataset.company&&!busy){resetReport();fillForm({company:b.dataset.company,aliases:[],official_domain:'',freshness:''});notice('已填入示例，请点击开始调查。');}
  if(b.dataset.open)action(()=>openReport(b.dataset.open),'正在读取报告…');
  if(b.dataset.follow&&report)action(async()=>{const data=await api('/api/followup',{id:report.id,kind:b.dataset.follow});render(data.report);notice('补充检索已保存为新报告；来源仍为元宝搜索。');},'正在执行一条补充元宝搜索…');
  if(b.dataset.recheck)action(async()=>{const data=await api('/api/recheck',{id:b.dataset.recheck});renderDiff(data.diff);await watches();notice('实时复查完成，请查看快照变化与完整性提示。');},'实时复查中，按原报告模式强制执行最多 12 次元宝搜索…');
  if(b.dataset.schedule)action(async()=>{const data=await api('/api/schedule',{id:b.dataset.schedule,enabled:b.dataset.enabled==='1',at:$('schedule-at').value});await watches();notice(data.enabled?'已开启每日自动复查：'+data.next_run:'每日复查已暂停');});
  if(b.dataset.ack)action(async()=>{await api('/api/alerts/read',{id:b.dataset.ack});await refreshAlerts();notice('提醒已标为已读');});
  if(b.dataset.unwatch)action(async()=>{await api('/api/unwatch',{id:b.dataset.unwatch});await watches();notice('已移出复查清单，历史报告保留。');});
});
resetReport(); refreshStats(); refreshAlerts(); setInterval(refreshAlerts,30000);

function renderCases(r) {
  const source=(id)=>{const p=r.evidence.find(x=>x.id===id);return p?`<a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>`:esc(id);};
  $('casework').innerHTML=(r.cases?.length?`<section class="card"><div class="label">从疑点到结论</div><h3>具体疑点调查记录</h3><p class="muted">每条记录展示发现、追查和判断。流程完成不等于事实已确认，新增网页不等于独立印证。</p>${r.cases.map(c=>`<article class="ev case-record"><h4>${esc(c.id)} · ${esc(c.question)}</h4><b>1 · 发现了什么</b><blockquote>${esc(c.initial_claim)}</blockquote>${c.initial_evidence_ids.map(source).join('<br>')}<p class="muted">为什么查：${esc(c.trigger)}</p><details><summary>2 · 如何继续追查（${c.steps.length} 次）</summary>${c.steps.map(s=>`<p><b>${esc(s.query)}</b><br>${esc(s.reason)}<br>${esc(s.status)}<br>父证据：${s.parent_evidence_ids.map(source).join(' / ')||'缺证复核'}</p>`).join('')}</details><details><summary>3 · 新增名称明确线索 ${c.new_evidence_ids.length} 条（非独立来源计数）</summary>${c.new_evidence_ids.map(source).join('<br>')||'未获得新增线索，不能据此强化判断。'}</details><h4>4 · 追查结论：${esc(c.status)}</h4><p>${esc(c.conclusion)}</p>${c.citations.map(x=>`<blockquote>${esc(x.quote)}<br>${source(x.evidence_id)}</blockquote>`).join('')}<details><summary>仍未查明</summary><ul>${c.remaining.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details></article>`).join('')}</section>`:'');
  const items=r.unattributed_evidence||[];
  $('unattributed').innerHTML=`<section class="card"><h3>主体不明／上下文不足的线索（${items.length} 个来源）</h3><p class="muted">以下片段没有明确目标名称或用户别名，已从模型结论输入中隔离，不作为公司的风险或业绩事实。名称同句出现也仅是线索，不是工商主体认证。</p><details><summary>单独查看隔离片段</summary>${items.map(x=>`<article class="ev">${source(x.evidence_id)}${x.excerpts.map(t=>`<blockquote>${esc(t)}</blockquote>`).join('')}<small>${esc(x.reason)}</small></article>`).join('')||'<p>本次未发现此类片段，不代表已全面核验主体。</p>'}</details></section>`;
}
function renderBriefing(r) {
  const b=r.briefing;
  if(!b){$('briefing').innerHTML='<p class="muted">历史报告未生成新版结论与证据清单，请重新调查。</p>';return;}
  const scopedUnknown=(u)=>{const a=r.credibility?.assessments?.find(x=>x.limitations?.includes(u));return a?(a.name||a.task_id)+'（仅本方向所选材料）：'+u:u;};
  const link=(c)=>{const p=r.evidence.find(e=>e.id===c.evidence_id);return `<blockquote>${esc(c.quote)}<br><a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(p?.title||'查看来源')}</a><br><small>${esc(p?.domain||'')} · 页面时间 ${esc(p?.date||'未提供')} · 摘要证据 ${esc(c.evidence_id)}</small></blockquote>`;};
  $('briefing').innerHTML=`<section class="card answer-first"><div class="label">${b.company_mode?'公司简报':'回答你的问题'}</div><h3>${esc(b.question)}</h3><h4>一分钟简报 · 结论节选</h4>${b.claims.length?b.claims.map(c=>`<article class="ev"><b>${esc(c.title)} · ${esc(c.status)}</b><p>${esc(c.answer.length>180?c.answer.slice(0,180)+'…':c.answer)}</p><a href="#dimension-${esc(c.topic)}">查看完整结论与证据 →</a></article>`).join(''):`<p>${esc(b.headline)}</p><p>未形成可引用结论；可在下方查看检索过程与原始候选来源。</p>`}<h4>争议与不同说法</h4>${b.disputes.length?b.disputes.map(d=>`<article class="ev"><b>${esc(d.label)}</b><p>${esc(d.reason)}</p><p class="muted">不同材料并列待核，不预设哪方正确；需核对主体、期间和口径。</p><div class="conflict-grid">${d.evidence.map((c,i)=>`<section><b>材料 ${i+1}</b>${link(c)}</section>`).join('')}</div></article>`).join(''):`<p class="muted">${esc(b.dispute_note)}</p>`}<h4>优先待核事项</h4><ul>${b.unknowns.slice(0,3).map(u=>`<li>${esc(scopedUnknown(u))}</li>`).join('')||'<li>未列出不表示已全面核实。</li>'}</ul><details><summary>全部尚未查明（${b.unknowns.length} 项）</summary><ul>${b.unknowns.map(u=>`<li>${esc(scopedUnknown(u))}</li>`).join('')||'<li>未列出其他缺口不代表已全面核实。</li>'}</ul></details><p class="muted">${esc(b.boundary)}</p></section>${r.gap_checks?.length?`<section class="card"><h3>针对缺口，继续追查了什么？</h3>${r.gap_checks.map(g=>`<article class="ev"><b>${esc(g.question)}</b><p>触发：${esc(g.trigger)}</p><p>结果：${esc(g.status)}</p><p class="muted">仍需核对：${esc(g.remaining)}</p></article>`).join('')}<p>预算最多 12 次元宝检索。未解决的问题保留为未知，不强行下结论。</p></section>`:''}`;
}
function renderDeep(r) {
  $('scores').innerHTML=renderCredibility(r);
  if(!r.plan){$('deepreport').replaceChildren();return;}
  $('deepreport').innerHTML=`<section class="card"><h3>自动调查：${esc(r.plan.question)}</h3><p class="muted">${esc(r.plan.planner)} · 上限 ${r.plan.max_searches} 次检索。${r.plan.omitted_topics?'另有 '+r.plan.omitted_topics+' 个方向未深入追查，可分批提问。':''}</p>
    <h4>调查轨迹（后轮由前轮证据驱动）</h4>${r.rounds.map(x=>`<details><summary>第 ${x.number} 轮 · ${esc(x.purpose)}</summary>${r.routes.filter(y=>y.round===x.number).map(y=>`<p class="muted">${esc(y.query_used)}<br>原因：${esc(y.reason)}<br>父证据：${(y.parent_evidence_ids||[]).map(id=>{const p=r.evidence.find(e=>e.id===id);return p?`<a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>`:esc(id)}).join(' / ')||'问题输入'} ｜ ${esc(y.status)}</p>`).join('')}</details>`).join('')}
    <h4>权威来源核对</h4>${(r.authority_checks||[]).map(a=>`<p class="muted">${esc(a.target_site)}：${a.initial_status==='failed'?'请求失败':a.initial_status==='matched'?'站内名称相关线索已找到':'限制检索未命中'}；放宽追查 ${esc(a.fallback_route_id||'未执行')}，找到站内名称相关线索 ${a.fallback_matched_sources} 条。零命中不是已证实没有该事实。</p>`).join('')}<h4>规则抽取结果（不代表最终可信度）</h4>${r.plan.tasks.map(t=>`<p><b>${t.id} ${esc(t.question)}</b><br>${esc(t.answer||t.status)}</p>`).join('')}
    <h4>规则抽取核对（未识别不代表内容不可信）</h4>${r.findings.length?r.findings.map(f=>`<details><summary>${esc(f.status)} · ${f.source_count} 个链接 / ${f.candidate_source_clusters} 个候选来源簇</summary><p class="muted">${esc(f.reason)}</p>${f.evidence.map(e=>`<blockquote>${esc(e.quote)}<br><a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.domain)}</a> · 指标 ${esc(e.metric||'未抽取')} · 期间 ${esc(e.period||'未明确')} · 数值 ${esc(e.value||'未抽取')}${esc(e.unit||'')}</blockquote>`).join('')}</details>`).join(''):'<p class="muted">规则未抽取到可比较陈述；请查看模型内容评估及原始材料，不表示来源不可信。</p>'}
    <p class="muted">停止原因：${esc(r.stop_reason)}</p></section>`;
}
function renderRadar(r) {
  const names=reportDimensions(r).map(d=>esc(d.name));
  const ids=reportDimensions(r).map(d=>d.id);
  const n=ids.length, angle=2*Math.PI/n;
  const focused=new Set((r.plan?.tasks||[]).map(t=>t.topic));
  const grouped=Object.fromEntries(ids.map(id=>[id,r.routes.filter(x=>(x.dimension_id||x.id)===id&&!x.id.startsWith('followup_'))]));
  const states=ids.map(id=>{
    const routes=grouped[id];
    if(!routes.length)return {value:null,count:null,label:'未调查',className:'missing'};
    const successful=routes.filter(x=>x.ok);
    const failed=routes.length-successful.length;
    // Count distinct related evidence IDs across all routes for this
    // dimension. This is a quantity, not a quality or probability score.
    const evidence=new Set(successful.flatMap(x=>(x.pages||[]).filter(p=>p.related).map(p=>p.id)));
    const count=evidence.size;
    if(!successful.length)return {value:null,count:null,label:'检索失败',className:'failed'};
    if(!count)return {value:0,count:0,label:failed?'部分失败 · 0 条':'已查未命中',className:failed?'failed':'empty'};
    return {value:count,count,label:failed?`部分失败 · ${count} 条`:`已查有线索 · ${count} 条`,className:failed?'partial':'covered'};
  });
  // Join by stable dimension ID, never array order or clue count.
  const assessments=new Map((r.credibility?.assessments||[]).map(a=>[a.task_id,a]));
  const values=ids.map(id=>{
    const a=assessments.get(id);
    return a && ['assessed','mixed'].includes(a.status) && Number.isFinite(a.score) && a.score>=0 && a.score<=100 ? a.score : null;
  });
  const scoreLabels=ids.map((id,i)=>{
    if(values[i]!==null)return `${values[i]}/100`;
    const a=assessments.get(id);
    const reason={citation_invalid:'引用待核',insufficient:'证据不足',failed:'评分失败',not_configured:'模型未配置',no_evidence:'无可评估证据'}[a?.status||r.credibility?.status];
    return '暂无评分'+(reason?`（${reason}）`:'');
  });
  const maxCount=100;
  const c=160,rad=118,pts=[];for(let i=0;i<n;i++){const a=-Math.PI/2+i*angle;pts.push([c+rad*Math.cos(a),c+rad*Math.sin(a)]);}
  const poly=(scale)=>scale.map((v,i)=>{const a=-Math.PI/2+i*angle,rr=rad*v/maxCount;return `${c+rr*Math.cos(a)},${c+rr*Math.sin(a)}`;}).join(' ');
  const rings=[.2,.4,.6,.8,1].map(v=>`<polygon points="${poly(Array(n).fill(maxCount*v))}" class="radar-ring"/>`).join('');
  const axes=pts.map(p=>`<line x1="${c}" y1="${c}" x2="${p[0]}" y2="${p[1]}" class="radar-axis"/>`).join('');
  const labels=pts.map((p,i)=>{const a=-Math.PI/2+i*angle,x=c+(rad+25)*Math.cos(a),y=c+(rad+25)*Math.sin(a);return `<text x="${x}" y="${y}" class="radar-label" text-anchor="${Math.abs(Math.cos(a))<.2?'middle':Math.cos(a)>0?'start':'end'}" dominant-baseline="middle">${names[i]}<tspan x="${x}" dy="20">${values[i]===null?'暂无评分':values[i]+'/100'}</tspan></text>`}).join('');
  // Axes form a cycle: connect every valid adjacent pair, including last -> first.
  // Missing dimensions remain gaps rather than invented zero scores.
  const paths=[];
  const point=(v,i)=>{const a=-Math.PI/2+i*angle,rr=rad*v/maxCount;return `${c+rr*Math.cos(a)},${c+rr*Math.sin(a)}`;};
  if(values.every(v=>v!==null))paths.push(`<polygon points="${poly(values)}" class="radar-data"/>`);
  else values.forEach((v,i)=>{
    const next=(i+1)%n;
    if(v!==null&&values[next]!==null)paths.push(`<polyline points="${point(v,i)} ${point(values[next],next)}" class="radar-data" data-from="${esc(ids[i])}" data-to="${esc(ids[next])}"/>`);
  });
  const dots=values.map((v,i)=>v===null?'':`<circle cx="${point(v,i).split(',')[0]}" cy="${point(v,i).split(',')[1]}" r="5" class="radar-dot" data-dimension="${esc(ids[i])}" data-score="${v}"><title>${names[i]}：${v}/100</title></circle>`).join('');
  $('radar').innerHTML=`<section class="radar-card"><div><div class="label">AI 可信度画像</div><h3>${n===5?'五个调查方向':'历史八维画像'}</h3><p class="muted">雷达图直接展示 AI 对各方向所选证据的内容可信度评分，与下方评分卡片一致；不是公司实力或好坏评级。去重线索数量仅作辅助信息，不参与分数换算。</p><p class="muted">固定量表：0–100 分，每圈 20 分。无有效评分或引用待核的方向留空，不按 0 分绘制。</p></div><svg class="radar" viewBox="-100 -40 520 400" role="img" aria-label="${n===5?'五个调查方向':'历史八维画像'}AI 内容可信度评分雷达图">${rings}${axes}${paths.join('')}${dots}${labels}</svg><div class="radar-legend">${names.map((n,i)=>`<span class="radar-state ${states[i].className}"><i></i>${n} · ${scoreLabels[i]} · ${states[i].label} · ${focused.has(ids[i])?'重点追查':'基础检索'}</span>`).join('')}</div></section>`;
}
function renderCredibility(r) {
  const c=r.credibility;
  if(!c)return '<section class="card"><h3>内容可信度</h3><p>此历史报告尚未进行大模型评分，请重新调查。</p></section>';
  const labels={relevance:'问题相关性',directness:'直接支持',source_quality:'来源质量',specificity:'细节口径',consistency:'一致与独立性'};
  const states={failed:'评分失败，未生成分数',not_configured:'模型未配置',no_evidence:'无可评估证据',complete:'评分完成'};
  return `<section class="card"><h3>AI · 内容可信度</h3><p class="muted">${esc(states[c.status]||c.status)} ${c.cached?'· 复用相同证据评分':''}<br>${esc(c.boundary)}</p>${(c.assessments||[]).map(a=>`<article class="ev"><h3>${esc(a.name||a.task_id)} · ${a.status==='citation_invalid'?'引用待核':a.score===null?'未评分':a.score+'/100'}</h3><details class="assessment-answer"><summary>完整评估说明（与正文对应）</summary><p>${esc(a.answer)}</p></details><p class="muted">${Object.entries(a.dimensions).map(([k,v])=>esc(labels[k]||k)+' '+(v===null?'—':v+'/20')).join(' ｜ ')}</p><details><summary>评分依据与局限</summary><p>${a.reasons.map(esc).join('<br>')}</p><p>${a.limitations.map(esc).join('<br>')}</p>${a.citations.map(x=>`<blockquote>${esc(x.quote)}${x.quote_fallback?'（模型摘句未对齐，已降级为证据标题）':''}<br><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">查看引用原文 ${esc(x.evidence_id)}</a></blockquote>`).join('')}</details></article>`).join('')}<small>评估请求 ${esc(c.request_id||'无')} · 量表 ${esc(c.rubric)}</small></section>`;
}
function renderPatentDetails(items) {
  const steps=[['申请人是否匹配','patent_applicant'],['专利名称、公开号与类型','patent_identifier'],['法律状态与日期','patent_legal'],['技术内容与主营业务关系','patent_relevance'],['应用与转化证据','patent_application']];
  const mapped=new Map((items||[]).map(a=>[a.task_id,a]));
  return `<section class="patent-analysis"><h4>专利证据专项核对</h4><p class="muted">权属 → 身份 → 法律状态 → 技术关系 → 实际应用。摘要不能替代专利登记簿、权利要求书或转化证明；专项分数不替代技术方向评分。</p>${steps.map(([name,id],i)=>{const a=mapped.get(id);return `<article class="ev"><h4>${i+1} · ${name}</h4>${a?`<p>${esc(a.answer)}</p><details><summary>证据依据与专项评分：${a.score==null||a.status==='citation_invalid'?'暂无有效评分':esc(a.score)+'/100'}</summary><p>${(a.reasons||[]).map(esc).join('<br>')}</p>${(a.citations||[]).map(x=>`<blockquote>${esc(x.quote)}<br><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">核对原文</a></blockquote>`).join('')}</details><p class="muted">未知与缺口：${(a.limitations||[]).map(esc).join('；')||'未列出额外缺口，不代表已完整核验。'}</p>`:'<p class="muted">尚无本项分析，不能从数量或品牌推断。</p>'}</article>`;}).join('')}</section>`;
}
const alerted=new Set();
async function refreshAlerts(){
  try{const d=await api('/api/alerts');$('alerts').innerHTML=d.items.map(a=>`<div class="historyrow"><div><b>${a.read?'已读':'未读'} · ${esc(a.title)}</b><p class="muted">${esc(a.created_at)} · ${esc(a.payload.note||'')}${a.payload.counts?' ｜ '+esc(JSON.stringify(a.payload.counts)):''}</p></div><div class="row">${a.report_id?`<button data-open="${a.report_id}">查看证据报告</button>`:''}${!a.read?`<button data-ack="${a.id}">标为已读</button>`:''}</div></div>`).join('')||'<p class="muted">暂无变化或失败提醒；正常无变化保持安静。</p>';
    for(const a of d.items){if(!a.read&&!alerted.has(a.id)){alerted.add(a.id);if('Notification' in window&&Notification.permission==='granted')new Notification(a.title,{body:a.payload.note||'打开公司侦探提醒中心查看证据',tag:a.id});}}
  }catch{/* Durable inbox remains on the server; retry on next poll. */}
}
$('notify-permission').onclick=async()=>{if(!('Notification' in window))return notice('当前浏览器不支持桌面通知；提醒仍会保存。');const p=await Notification.requestPermission();notice(p==='granted'?'浏览器通知已开启（需保持页面打开）':'通知权限未开启；请使用提醒中心。');};

const linkedReport=new URLSearchParams(location.search).get('report');
if(linkedReport && /^[a-f0-9]{16}$/.test(linkedReport))action(()=>openReport(linkedReport),'正在打开指定报告…');
