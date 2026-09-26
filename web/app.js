let sessionToken, data, model='all', selection, notifying=false;
const seen = new Set(), jobSeen = new Set();
const $ = id => document.getElementById(id);
const escapeHtml = x => String(x ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = n => '¥'+Number(n).toLocaleString('zh-CN');
const fresh = s => s.available && data.now-s.checked<=420;
const modelOf = p => /Duo/i.test(p.name)?'duo':/Max/i.test(p.name)?'max':'pro';
function toast(text){$('toast').textContent=text;$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,6000)}
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':sessionToken},body:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw Error(j.error||'操作未成功');return j}
function notify(title,body,key){if(seen.has(key))return;seen.add(key);if(notifying&&Notification.permission==='granted'){const n=new Notification(title,{body,tag:key});n.onclick=()=>{window.focus();n.close()}}}
function render(){
 const state=data.state, online=data.now-state.worker_seen<180;
 $('connection').textContent=online?'执行器在线':'执行器尚未连接';
 $('monitor').textContent=state.monitoring?'暂停监控':'开始监控';
 $('total').textContent=data.catalog.length;
 $('available').textContent=data.stock.filter(fresh).length;
 $('account-status').textContent=data.state.desktop_browser ? '使用本机 '+data.state.desktop_browser+' 专用窗口登录；密码和验证码只输入在 Apple 官网。' : '在执行器浏览器窗口登录 Apple 官网。';
 $('pickup-date').textContent=data.launch_date+' 门店取货';
 $('login').textContent='在监控浏览器打开账号页 ↗';
 $('notice').textContent=state.worker_message||'等待库存检查';
 $('notice').classList.toggle('good',online);
 const checked=new Set(data.stock.filter(s=>data.now-s.checked<300).map(s=>s.part));
 $('coverage').textContent=`最近 5 分钟已检查 ${checked.size} / ${data.catalog.length} 个配置 · ${online?'浏览器轮询中':'等待浏览器检查'} · 每条结果保留实际检查时间`;
 if(!$('store').dataset.loaded){const groups={};for(const s of data.stores||[])(groups[s.city]??=[]).push(s);$('store').innerHTML='<option value="all">全部全国直营店</option>'+Object.entries(groups).map(([city,stores])=>`<optgroup label="${escapeHtml(city)}">${stores.map(s=>`<option value="${escapeHtml(s.name)}">${escapeHtml(city+' · '+s.name)}</option>`).join('')}</optgroup>`).join('');$('store').dataset.loaded='1';$('store-count').textContent=(data.stores||[]).length;}
 const filter=$('store').value;
 const products=data.catalog.filter(p=>model==='all'||modelOf(p)===model).filter(p=>!$('only').checked||data.stock.some(s=>s.part===p.part&&fresh(s)&&(filter==='all'||s.store===filter)));
 $('cards').innerHTML=products.map(p=>{
  const rows=data.stock.filter(s=>s.part===p.part&&(filter==='all'||s.store===filter));
  const available=rows.filter(fresh);
  const details=rows.length?rows.map(s=>{const good=fresh(s),elapsed=Math.max(0,Math.round(data.now-s.checked));return `<div class="stock-row"><div><strong>${escapeHtml(s.store)}</strong><p>${good?escapeHtml(s.pickup):elapsed>420?'结果已过期':'目标日期暂无供应'} · ${elapsed<60?'刚刚':Math.floor(elapsed/60)+' 分钟前'}</p></div>${good?`<button class="primary" data-buy="${escapeHtml(p.part)}" data-store="${escapeHtml(s.store)}" ${online?'':'disabled'}>下单</button>`:''}</div>`}).join(''):'<div class="unknown">等待检查 · 暂无可确认的门店库存</div>';
  return `<article class="card ${available.length?'ready':''}"><div class="card-head"><span class="model">${modelOf(p)==='duo'?'IPHONE DUO':modelOf(p)==='max'?'IPHONE 18 PRO MAX':'IPHONE 18 PRO'}</span><span class="badge ${available.length?'green':''}">${available.length?'可供取货':rows.length?'暂无新鲜有货结果':'等待检查'}</span></div><h3>${escapeHtml(p.name.replace(/^iPhone\s+18\s+Pro\s*(Max)?\s*/i,'').replace(/^iPhone\s+Duo\s*/i,''))}</h3><div class="sku">${escapeHtml(p.part)}</div><div class="price">${money(p.price)}<small>官网目录价</small></div><div class="stores">${details}</div></article>`;
 }).join('')||'<div class="empty">当前筛选下没有可确认的有货选项。发现新库存后会显示在这里。</div>';
 const statuses={queued:'等待准备购物袋',checking:'正在复核目标日期库存',need_login:'请在官网登录',need_input:'请在官网完成下单',failed:'库存变化，未加入购物袋',cancelled:'已取消',completed:'购物袋已准备'};
 $('jobs').innerHTML=data.jobs.map(j=>`<article class="job"><div><strong>${escapeHtml(data.catalog.find(p=>p.part===j.part)?.name||j.part)} · ${escapeHtml(j.store)}</strong><p>${escapeHtml(statuses[j.status]||j.status)} · ${money(j.max_price)} · ${j.quantity} 台</p><p>${escapeHtml(j.message)}</p>${j.order_number?`<p>Apple 订单编号：${escapeHtml(j.order_number)}</p>`:''}</div><div>${j.status==='queued'?`<button data-cancel="${escapeHtml(j.id)}">撤回请求</button>`:''}${j.status==='awaiting_payment'&&j.payment_url?`<a href="${escapeHtml(j.payment_url)}" target="_blank" rel="noopener">前往手动付款 ↗</a>`:''}</div></article>`).join('')||'<div class="empty">尚未下单。库存出现后，你可以在商品卡片上选择门店并点击“下单”。</div>';
 for(const s of data.stock.filter(fresh)){const p=data.catalog.find(p=>p.part===s.part);notify('Apple 全国门店有货',`${p?.name} · ${s.store} · ${s.pickup}`,`${s.part}:${s.store}:${s.pickup}`)}
 for(const j of data.jobs){if(j.status==='awaiting_payment'&&!jobSeen.has(j.id)){jobSeen.add(j.id);notify('Apple 订单已创建，请手动付款',j.message,'order:'+j.id)}}
}
async function refresh(){try{const r=await fetch('/api/state');if(!r.ok)throw Error();data=await r.json();render()}catch{$('connection').textContent='页面服务连接中断';$('notice').textContent='无法连接本机服务，库存状态不可用。请重新启动页面服务。';document.querySelectorAll('[data-buy]').forEach(b=>b.disabled=true)}}
document.querySelectorAll('[data-model]').forEach(b=>b.onclick=()=>{model=b.dataset.model;document.querySelectorAll('[data-model]').forEach(x=>x.classList.toggle('selected',x===b));if(data)render()});
$('store').onchange=$('only').onchange=()=>{if(data)render()};
$('notify').onclick=async()=>{if(!('Notification'in window)){toast('此浏览器不支持系统通知，请保持页面打开。');return}const permission=await Notification.requestPermission();notifying=permission==='granted';$('notify').textContent=notifying?'到货通知已开启':'通知未获允许';toast(notifying?'新发现的有货选项将发送系统通知':'可在浏览器设置中允许通知')};
$('monitor').onclick=async()=>{try{await post('/api/monitor',{enabled:!data.state.monitoring});await refresh()}catch(e){toast(e.message)}};
$('refresh').onclick=async()=>{try{const r=await post('/api/refresh',{});toast(r.message)}catch(e){toast(e.message)}};
$('login').onclick=()=>{post('/api/account',{}).then(()=>toast('已请求监控浏览器打开 Apple 账号页。')).catch(e=>toast(e.message))};
$('cards').onclick=e=>{const b=e.target.closest('[data-buy]');if(!b)return;const s=data.stock.find(s=>s.part===b.dataset.buy&&s.store===b.dataset.store);if(!s||!fresh(s)){toast('库存已过期，请更新后再试');return}const p=data.catalog.find(p=>p.part===s.part);selection={...s,idempotencyKey:crypto.randomUUID()};$('review').innerHTML=[['商品',p.name],['直营店',s.store],['取货日期',s.pickup],['数量','1 台'],['最高总金额',money(s.price)]].map(([k,v])=>`<div class="review-row"><span>${escapeHtml(k)}</span><strong>${escapeHtml(v)}</strong></div>`).join('');$('order-error').textContent='';$('confirm').showModal()};
$('submit-order').onclick=async()=>{const b=$('submit-order');b.disabled=true;try{await post('/api/orders',{part:selection.part,store:selection.store,pickup:selection.pickup,price:selection.price,idempotencyKey:selection.idempotencyKey,confirmed:true});$('confirm').close();toast('已确认；脚本将复核目标日期库存并准备 Apple 购物袋。');await refresh()}catch(e){$('order-error').textContent=e.message}finally{b.disabled=false}};
$('jobs').onclick=async e=>{const b=e.target.closest('[data-cancel]');if(b){try{await post('/api/cancel',{id:b.dataset.cancel});await refresh()}catch(e){toast(e.message)}}};
fetch('/api/session').then(r=>r.json()).then(s=>{sessionToken=s.token;notifying='Notification'in window&&Notification.permission==='granted';refresh();setInterval(refresh,4000)}).catch(()=>toast('页面服务无法连接，请重新启动。'));
