'use strict';
const $=id=>document.getElementById(id);
let token='', data={sources:[]}, busy=false;
function savedKey(){
  try{
    const endpoints=JSON.parse(localStorage.getItem('endpointList')||'[]');
    let selected=localStorage.getItem('selectedEndpoint')||'';try{selected=JSON.parse(selected)}catch{}
    const ep=endpoints.find(x=>x.id===selected&&new URL(x.url).origin===location.origin)||endpoints.find(x=>new URL(x.url).origin===location.origin);
    return ep?.secret||sessionStorage.getItem('subscription-secret')||'';
  }catch{return sessionStorage.getItem('subscription-secret')||''}
}
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);$('notice').hidden=false}
function formatDate(value){return value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'尚未更新'}
function intervalText(seconds){return seconds<3600?`每 ${Math.round(seconds/60)} 分钟`:`每 ${seconds/3600} 小时`}
function setBusy(value){busy=value;document.querySelectorAll('button').forEach(x=>x.disabled=value);$('status-line').textContent=value?'正在校验并应用，请稍候…':'已连接 · 修改已保存'}
async function request(path,body){
  const r=await fetch('/subscription-api/'+path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
  const d=await r.json();if(!r.ok){if(r.status===401){$('login').hidden=false;$('workspace').hidden=true}throw Error(d.error||'请求失败，请重试。')}return d;
}
async function load(){try{data=await request('sources');$('login').hidden=true;$('workspace').hidden=false;render()}catch(e){notice(e.message,true)}}
function render(){
  $('source-count').textContent=data.sources.length;$('total-badge').textContent=data.sources.length;
  $('node-count').textContent=data.sources.filter(s=>s.enabled).reduce((n,s)=>n+(s.node_count||0),0);
  $('automatic-count').textContent=data.sources.filter(s=>s.enabled).length;
  $('undo').disabled=!data.last_backup;
  const parent=$('sources');parent.replaceChildren();
  for(const s of data.sources){
    const card=el('article','source'),head=el('div','source-head'),info=el('div'),title=el('div','source-title');
    title.append(el('h3','',s.name),el('span','pill'+(s.enabled?'':' off'),s.enabled?'已启用':'已暂停'));info.append(title);
    let host='订阅地址';try{host=new URL(s.url).host}catch{}
    info.append(el('div','source-url',host+' / ••••••••'));head.append(info);
    const actions=el('div','actions');
    for(const [label,fn] of [['↻ 更新',()=>operate('refresh',{id:s.id})],['编辑',()=>edit(s)],['删除',()=>confirmAction('删除订阅',`删除“${s.name}”及其节点？现有分流规则会保留，也可撤销本次修改。`,()=>operate('delete',{id:s.id}))]]){
      const b=el('button',label==='删除'?'text-button':'ghost',label);b.onclick=fn;if(!s.enabled&&label==='↻ 更新')b.disabled=true;actions.append(b)
    }
    head.append(actions);card.append(head);
    const meta=el('div','source-meta');
    for(const [label,value] of [['节点',String(s.node_count||0)],['更新频率',intervalText(s.interval)],['最近成功',formatDate(s.updated_at)]]){const x=el('span');x.append(document.createTextNode(label+'  '),el('b','',value));meta.append(x)}
    if(s.excluded_count)meta.append(el('span','',`已排除 ${s.excluded_count} 个本机入口`));card.append(meta);
    if(s.last_error)card.append(el('div','source-error',s.last_error));
    const nodes=el('div','nodes');for(const n of (s.nodes||[]).slice(0,20))nodes.append(el('span','node',n.name));
    if((s.nodes||[]).length>20)nodes.append(el('span','node',`另有 ${s.nodes.length-20} 个节点`));card.append(nodes);parent.append(card)
  }
  if(!data.sources.length)parent.append(el('div','empty','还没有订阅，点击“添加订阅”开始。'));
}
function edit(s){
  $('source-form').reset();$('form-error').hidden=true;$('source-id').value=s?.id||'';
  $('source-name').value=s?.name||'';$('source-url').value=s?.url||'';
  $('source-interval').value=String(s?.interval||3600);
  if(!$('source-interval').value){const option=el('option','',intervalText(s.interval));option.value=s.interval;$('source-interval').append(option);$('source-interval').value=String(s.interval)}
  $('source-enabled').checked=s?.enabled??true;$('source-prefix').checked=s?.prefix??true;
  $('editor-title').textContent=s?'编辑订阅':'添加订阅';$('save').textContent=s?'保存并应用':'导入并应用';$('editor').showModal();$('source-name').focus();
}
async function operate(action,body){if(busy)return;setBusy(true);notice('正在下载、校验并应用订阅…');
  try{const d=await request(action,body);data=d.state;render();notice(d.message);return true}catch(e){notice(e.message,true);return false}finally{setBusy(false);render()}
}
function confirmAction(title,copy,fn){$('confirm-title').textContent=title;$('confirm-copy').textContent=copy;$('confirm-go').onclick=()=>{$('confirm').close();fn()};$('confirm').showModal()}
$('add').onclick=()=>edit();$('refresh-all').onclick=()=>operate('refresh-all',{});
$('undo').onclick=()=>confirmAction('撤销上次修改','恢复上次修改前的订阅和节点。当前分流规则保持不变。',()=>operate('rollback',{}));
$('confirm-cancel').onclick=()=>$('confirm').close();
for(const id of ['close-editor','cancel-editor'])$(id).onclick=()=>{if(!busy)$('editor').close()};
$('source-form').onsubmit=async event=>{event.preventDefault();if(busy)return;
  const id=$('source-id').value,body={id,name:$('source-name').value.trim(),url:$('source-url').value.trim(),interval:Number($('source-interval').value),enabled:$('source-enabled').checked,prefix:$('source-prefix').checked};
  const ok=await operate(id?'edit':'add',body);if(ok)$('editor').close();else{$('form-error').textContent=$('notice').textContent;$('form-error').hidden=false}
};
$('connect-form').onsubmit=async event=>{event.preventDefault();token=$('secret').value;await load();if(!$('workspace').hidden){sessionStorage.setItem('subscription-secret',token);$('secret').value=''}};
$('disconnect').onclick=()=>{token='';sessionStorage.removeItem('subscription-secret');$('workspace').hidden=true;$('login').hidden=false;notice('管理连接已断开，代理服务继续运行。')};
$('editor').addEventListener('cancel',event=>{if(busy)event.preventDefault()});
token=savedKey();if(token)load();else $('login').hidden=false;
