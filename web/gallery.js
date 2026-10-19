'use strict';
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
let collections=[], rows=new Map(), selected=new Set(), current='', revision=-1, cursor=null, detailId=null;
let syncing=false, again=false, generation=0, retry=500, query='', pageLimit=40, connected=false;
const subtitle = card => `${card.set} · ${card.number}${card.total ? '/'+card.total : ''}`;
const date = time => new Date(time).toLocaleString();
function report(error) {$('error').textContent=error.message || String(error);$('error').hidden=false;}
async function api(path, data) {
  const response=await fetch(path,data ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)} : {cache:'no-store'});
  const value=await response.json();if(!response.ok)throw Error(typeof value.detail==='string' ? value.detail : 'Desktop request failed');return value;
}
async function action(value) {const result=await api('/api/library/action',value);$('error').hidden=true;await sync();return result;}
function figure(title,url) {
  const f=el('figure');f.append(el('figcaption',title));const box=el('div',undefined,'image-box');f.append(box);
  const placeholder=el('span',url?'Loading image…':title==='Scanned Image'?'No saved photo':'No reference image','placeholder');box.append(placeholder);
  if(url){const im=el('img');im.alt=title;im.loading='lazy';im.decoding='async';im.onload=()=>placeholder.hidden=true;im.onerror=()=>{im.remove();placeholder.textContent='Image unavailable';};im.src=url;box.append(im);}return f;
}
function priceText(p) {if(!p || p.min_cents===null)return p?.status==='loading'?'Pricing…':'Price unavailable';const money=n=>'$'+(n/100).toFixed(2);return `Est. ${money(p.min_cents)}${p.max_cents!==p.min_cents?'–'+money(p.max_cents):''} USD${p.stale?' · Older data':''}`;}
function renderDetail(row) {
  $('detail-name').textContent=row.card.name;$('detail-info').textContent=`${subtitle(row.card)} · ${date(row.time)}`;
  $('detail-images').replaceChildren(figure('Scanned Image',row.scanned_image),figure('Reference Image',row.reference_image));
  $('detail-price').replaceChildren(el('p',priceText(row.price),'price'));
  if(row.price?.min_cents!==null && row.price?.min_cents!==undefined){
    for(const q of row.price.quotes)$('detail-price').append(el('p',`${q.label} · $${(q.cents/100).toFixed(2)}`,'muted'));
    $('detail-price').append(el('p','TCGplayer market via TCGdex · Condition and finish affect value.','muted'));
    if(row.price.updated)$('detail-price').append(el('p','Source updated '+date(row.price.updated),'muted'));
  }
}
function openDetail(id) {detailId=id;renderDetail(rows.get(id));$('detail').showModal();}
function updateSelection(){
  $('selection-count').textContent=`${selected.size} selected`;
  $('bulk-delete').disabled=$('bulk-move').disabled=!selected.size;
  $('select-all').textContent=rows.size && selected.size===rows.size?'Clear selection':'Select loaded scans';
}
function render(data) {
  collections=data.collections;revision=data.revision;cursor=data.next;
  const group=data.collection;
  if(current && !group){location.hash='';return;}
  $('page-title').textContent=group?.name || 'Collections';
  $('description').textContent=group?'Tap a scan to compare images or edit its details.':'Choose a collection on your phone or here, then start scanning.';
  $('count').textContent=group?`${data.total} scans`:`${collections.length} collections`;
  $('active').textContent='Scanning into: '+(collections.find(c=>c.id===data.active_collection)?.name || 'Choose a collection');
  for(const id of ['back','continue','export','rename','delete-collection','scan-tools'])$(id).hidden=!group;
  $('continue').textContent=data.active_collection===current?'Selected for scanning':'Scan into this collection';
  $('continue').disabled=data.active_collection===current;
  $('gallery').setAttribute('aria-label',group?'Scanned cards':'Collections');
  rows=new Map(data.rows.map(r=>[r.id,r]));selected=new Set([...selected].filter(id=>rows.has(id)));updateSelection();
  $('gallery').replaceChildren();
  if(!group){for(const c of collections){
    const card=el('article',undefined,'card collection');const b=el('button',undefined,'compare');b.append(el('h2',c.name),el('p',`${c.count} scans`,'muted'),el('p',c.last_scan?'Last scan '+date(c.last_scan):'Ready for your first scan','muted'));
    if(c.id===data.active_collection)b.append(el('p','Selected for scanning','price'));
    b.onclick=()=>location.hash=encodeURIComponent(c.id);card.append(b);$('gallery').append(card);
  }}else{for(const row of rows.values()){
    const article=el('article',undefined,'card');article.dataset.id=row.id;
    const label=el('label',undefined,'scan-select'),checkbox=el('input');checkbox.type='checkbox';checkbox.checked=selected.has(row.id);checkbox.setAttribute('aria-label','Select '+row.card.name);checkbox.onchange=()=>{checkbox.checked?selected.add(row.id):selected.delete(row.id);updateSelection();};label.append(checkbox,el('span','Select scan'));article.append(label);
    const b=el('button',undefined,'compare');b.setAttribute('aria-label','Open '+row.card.name);b.append(figure('Scanned Image',row.scanned_thumbnail));b.onclick=()=>openDetail(row.id);article.append(b);
    const meta=el('div',undefined,'meta');meta.append(el('h2',row.card.name),el('p',subtitle(row.card),'muted'),el('p',date(row.time),'muted'),el('p',priceText(row.price),'price'));article.append(meta);$('gallery').append(article);
  }}
  $('empty').hidden=group?rows.size>0:collections.length>0;
  $('empty').textContent=group?(query?'No matching scans.':'No scans yet. Select this collection and present a card to your phone.'):'Create your first collection to start scanning.';
  $('more').hidden=!cursor;
  if($('detail').open && !$('form-dialog').open){const row=rows.get(detailId);if(row)renderDetail(row);else{$('detail').close();detailId=null;}}
}
async function sync() {
  if(syncing){again=true;return;}syncing=true;
  try{do{again=false;const gen=generation;let before='', data, all=[];
    do {data=await api(`/api/library?collection_id=${encodeURIComponent(current)}&query=${encodeURIComponent(query)}&before=${encodeURIComponent(before)}&limit=40`);
      if(gen!==generation)break;
      if(all.length && data.revision!==all[0].snapshot){again=true;break;}
      all.push({snapshot:data.revision,rows:data.rows});before=data.next;
    }while(before && all.reduce((n,p)=>n+p.rows.length,0)<pageLimit);
    if(gen!==generation){again=true;continue;}if(again)continue;
    render({...data,rows:all.flatMap(p=>p.rows)});
  }while(again);}catch(e){report(e);}finally{syncing=false;}
}
function versions(ids){return Object.fromEntries(ids.map(id=>[id,rows.get(id)?.revision]));}
function field(name,label,value,options) {
  const wrap=el('label',label),input=el(options?'select':'input');input.name=name;
  if(options){for(const item of options){const o=el('option',item.name);o.value=item.id;input.append(o);}}else{input.value=value??'';input.maxLength=name==='name'||name==='set'?200:32;}
  if(name==='total'){input.type='number';input.min='1';input.max='100000';input.step='1';}
  if(['name','set','number'].includes(name))input.required=true;
  wrap.append(input);return wrap;
}
let submitForm=null;
function form(title,fields,submit){$('form-title').textContent=title;$('fields').replaceChildren(...fields);$('form-error').textContent='';submitForm=submit;$('form-dialog').showModal();$('fields').querySelector('input,select')?.focus();}
$('form').onsubmit=async event=>{event.preventDefault();$('save').disabled=true;try{await submitForm(Object.fromEntries(new FormData(event.target)));$('form-dialog').close();await sync();}catch(e){$('form-error').textContent=e.message;}finally{$('save').disabled=false;}};
$('cancel').onclick=()=>$('form-dialog').close();
$('create').onclick=()=>{const id=crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);form('New collection',[field('name','Collection name','')],async data=>{await action({action:'create',id,name:data.name});location.hash=id;});};
$('rename').onclick=()=>form('Rename collection',[field('name','Collection name',collections.find(c=>c.id===current)?.name)],data=>action({action:'rename',id:current,name:data.name}));
$('export').onclick=()=>{
  const link=el('a');link.href='/api/library/export?collection_id='+encodeURIComponent(current);
  link.download='collection-'+current+'.zip';document.body.append(link);link.click();link.remove();
};
$('continue').onclick=()=>action({action:'select',id:current}).catch(report);
$('delete-collection').onclick=async()=>{const c=collections.find(c=>c.id===current);if(!confirm(`Delete “${c.name}” and all ${c.count} scans in it? This cannot be undone.`))return;try{await action({action:'delete_collection',id:current});location.hash='';}catch(e){report(e);}};
function move(ids){const options=collections.filter(c=>c.id!==current);if(!options.length){report(Error('Create another collection first.'));return;}const v=versions(ids);form(`Move ${ids.length} scan${ids.length===1?'':'s'}`,[field('collection_id','Destination collection','',options)],async data=>{await action({action:'move',ids,versions:v,collection_id:data.collection_id});selected.clear();$('detail').close();});}
async function remove(ids){if(!confirm(`Delete ${ids.length} scan${ids.length===1?'':'s'} and their saved photos? This cannot be undone.`))return;try{await action({action:'delete_scans',ids,versions:versions(ids)});selected.clear();updateSelection();}catch(e){report(e);}}
$('bulk-move').onclick=()=>move([...selected]);$('bulk-delete').onclick=()=>remove([...selected]);
$('move').onclick=()=>move([detailId]);$('delete').onclick=()=>remove([detailId]);
$('select-all').onclick=()=>{selected=selected.size===rows.size?new Set():new Set(rows.keys());for(const box of $('gallery').querySelectorAll('input[type=checkbox]'))box.checked=selected.has(box.closest('.card').dataset.id);updateSelection();};
$('edit').onclick=()=>{const row=rows.get(detailId),id=row.id, v=versions([id]);form('Edit card details',[
  field('name','Card name',row.card.name),field('set','Set name',row.card.set),field('number','Card number',row.card.number),field('code','Set code (optional)',row.card.code),field('total','Cards in set (optional)',row.card.total),el('p','Custom details without a catalog match clear the old reference image and price. Your scanned photo stays saved.','muted')
],data=>action({action:'edit',ids:[id],versions:v,fields:{...data,total:data.total?Number(data.total):null}}));};
$('close').onclick=()=>$('detail').close();$('back').onclick=()=>location.hash='';
$('more').onclick=()=>{pageLimit+=40;sync();};let searchTimer;$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{query=$('search').value;pageLimit=40;generation++;sync();},200);};
function navigate(){current=decodeURIComponent(location.hash.slice(1));selected.clear();query='';$('search').value='';pageLimit=40;generation++;$('detail').close();sync();}
addEventListener('hashchange',navigate);
function connect(){const ws=new WebSocket(`ws://${location.host}/history/events`);ws.onopen=()=>{connected=true;retry=500;$('status').textContent='Live · local network';$('status').className='';sync();};ws.onmessage=e=>{const value=JSON.parse(e.data);if(value.revision!==revision)sync();};ws.onerror=()=>ws.close();ws.onclose=()=>{connected=false;$('status').textContent='Disconnected · reconnecting…';$('status').className='offline';setTimeout(connect,retry);retry=Math.min(8000,retry*2);};}
let pendingSignature='';
async function pendingScans(){
  try {
    const data=await api('/api/library/pending'), signature=JSON.stringify(data.rows);
    if(signature===pendingSignature)return;pendingSignature=signature;
    const host=$('pending-scans');host.replaceChildren();host.hidden=!data.rows.length;
    for(const row of data.rows){
      const panel=el('article',undefined,'card');panel.append(el('h2','Confirm this card'),el('p','The number and artwork did not give a single clear match. Choose the correct printing before saving.'));
      const preview=el('img');preview.src=`/api/library/pending/${encodeURIComponent(row.request_id)}/image`;preview.alt='Card awaiting confirmation';preview.style.maxWidth='160px';panel.append(preview);
      for(const match of row.matches){const b=el('button',`${match.card.name} · ${subtitle(match.card)}${match.ocr_support?' · Number matches':''}`);
        b.onclick=async()=>{b.disabled=true;try{await api('/api/library/confirm',{request_id:row.request_id,card_id:match.card.id});pendingSignature='';await pendingScans();await sync();}catch(e){report(e);b.disabled=false;}};panel.append(b);}
      const dismiss=el('button','Skip this scan');dismiss.onclick=async()=>{try{await api('/api/library/confirm',{request_id:row.request_id,card_id:null});pendingSignature='';await pendingScans();}catch(e){report(e);}};panel.append(dismiss);host.append(panel);
    }
  }catch(e){/* Connection status is handled by the existing collection stream. */}
}
navigate();connect();pendingScans();setInterval(()=>{if(!document.hidden)pendingScans();},2000);setInterval(()=>{if(connected && !document.hidden)sync();},60000);
