"""Widget compartilhado do Diário de Bordo — CSS, HTML e JS para todos os dashboards."""


def diario_css():
    return """
  .mod-btn.m-diario{color:#10B981;background:rgba(16,185,129,.08);border-color:rgba(16,185,129,.2)}
  .db-section-lbl{font-size:10px;color:#374151;text-transform:uppercase;letter-spacing:.06em;margin:12px 0 6px;display:flex;align-items:center;gap:8px}
  .db-section-lbl::after{content:'';flex:1;border-top:0.5px solid #111827}
  .db-act-item{background:#080d19;border:1px solid #111827;border-radius:8px;padding:9px 12px;margin-bottom:6px;display:flex;align-items:flex-start;gap:10px;transition:border-color .15s}
  .db-act-item:hover{border-color:#1f2937}
  .db-act-item.db-done{opacity:.5}
  .db-check{width:18px;height:18px;border:1.5px solid #374151;border-radius:4px;flex-shrink:0;margin-top:1px;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:11px;color:#10B981;transition:all .15s;user-select:none}
  .db-check.db-done{background:rgba(16,185,129,.12);border-color:#10B981}
  .db-act-body{flex:1;min-width:0}
  .db-act-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
  .db-act-name{font-size:13px;font-weight:500;color:#F9FAFB}
  .db-act-item.db-done .db-act-name{text-decoration:line-through;color:#6B7280}
  .db-act-time{font-size:11px;color:#6B7280}
  .db-tipo{font-size:10px;padding:2px 6px;border-radius:20px;font-weight:500;white-space:nowrap}
  .db-t-analise{background:rgba(59,130,246,.1);color:#93C5FD}
  .db-t-gemba{background:rgba(245,158,11,.1);color:#FCD34D}
  .db-t-reuniao{background:rgba(234,88,12,.1);color:#FDBA74}
  .db-t-1a1{background:rgba(124,58,237,.1);color:#C4B5FD}
  .db-t-treinamento{background:rgba(5,150,105,.1);color:#6EE7B7}
  .db-t-extra{background:rgba(107,114,128,.1);color:#9CA3AF}
  .db-obs-txt{font-size:11px;color:#6B7280;margin-top:4px;font-style:italic}
  .db-obs-inp{margin-top:6px;width:100%;font-size:12px;padding:5px 8px;border-radius:4px;border:1px solid #1f2937;background:#060a14;color:#D1D5DB;outline:none;display:none;box-sizing:border-box;resize:vertical}
  .db-act-item:hover .db-obs-inp,.db-obs-inp:focus{display:block}
  .db-btn-add{width:100%;padding:8px;background:transparent;border:1px dashed #1f2937;border-radius:8px;font-size:12px;color:#6B7280;cursor:pointer;margin-top:8px;transition:border-color .15s,color .15s}
  .db-btn-add:hover{border-color:#10B981;color:#10B981}
  .db-modal-bg{display:none;position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.65);align-items:center;justify-content:center}
  .db-modal{background:#111827;border:1px solid #374151;border-radius:10px;padding:20px;width:360px;max-width:95vw}
  .db-modal-inp{width:100%;padding:7px 10px;background:#1f2937;border:1px solid #374151;border-radius:6px;color:#F9FAFB;font-size:13px;margin-bottom:8px;box-sizing:border-box}
  .db-modal-inp:focus{outline:none;border-color:#10B981}
"""


def diario_nav_btn():
    return """
      <button id="db-nav-btn" onclick="dbTogglePanel()" class="mod-btn m-diario" style="cursor:pointer;position:relative">
        <i data-lucide="book-open" width="12" height="12"></i> Diário
        <span id="db-nav-badge" style="display:none;position:absolute;top:-4px;right:-4px;background:#10B981;color:#fff;font-size:8px;padding:1px 4px;border-radius:10px;font-weight:700"></span>
      </button>"""


def diario_panel_html():
    return """
<div id="db-panel" style="display:none;position:fixed;top:60px;right:12px;width:350px;max-height:75vh;overflow-y:auto;background:#111827;border:1px solid #374151;border-radius:10px;z-index:9999;box-shadow:0 8px 32px rgba(0,0,0,.8)">
  <div style="padding:11px 14px;border-bottom:1px solid #1f2937;display:flex;align-items:center;gap:8px;flex-shrink:0;position:sticky;top:0;background:#111827;z-index:1">
    <span style="font-size:12px;font-weight:600;color:#F9FAFB">Diário de Bordo</span>
    <span id="db-date-lbl" style="font-size:11px;color:#6B7280;flex:1"></span>
    <span id="db-progress-wrap" style="display:flex;align-items:center;gap:5px;font-size:10px;color:#6B7280;margin-right:4px">
      <span id="db-progress-txt"></span>
      <div style="width:44px;height:4px;background:#0d1321;border-radius:2px;overflow:hidden">
        <div id="db-progress-bar" style="height:100%;background:#10B981;border-radius:2px;width:0%;transition:width .3s"></div>
      </div>
    </span>
    <span id="db-status" style="font-size:10px;color:#6B7280"></span>
    <button onclick="dbFecharPanel()" style="background:none;border:none;color:#6B7280;cursor:pointer;font-size:15px;padding:0;line-height:1;margin-left:4px">✕</button>
  </div>
  <div style="padding:10px 12px 12px">
    <div id="db-list"></div>
    <div class="db-section-lbl" style="margin-top:10px">Extras</div>
    <div id="db-extras"></div>
    <button class="db-btn-add" onclick="dbAbrirModal()">+ Atividade extra</button>
  </div>
</div>
<div id="db-modal-bg" class="db-modal-bg" onclick="if(event.target===this)dbFecharModal()">
  <div class="db-modal">
    <div style="font-size:14px;font-weight:600;color:#F9FAFB;margin-bottom:14px">Nova Atividade Extra</div>
    <input id="db-m-atv" class="db-modal-inp" placeholder="Descrição da atividade *">
    <div style="display:flex;gap:8px;margin-bottom:8px">
      <input id="db-m-ini" type="time" class="db-modal-inp" style="flex:1;margin-bottom:0">
      <input id="db-m-fim" type="time" class="db-modal-inp" style="flex:1;margin-bottom:0">
    </div>
    <textarea id="db-m-obs" class="db-modal-inp" rows="2" placeholder="Observações (opcional)" style="resize:vertical;margin-top:8px"></textarea>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
      <button onclick="dbFecharModal()" style="padding:6px 14px;background:transparent;border:1px solid #374151;border-radius:6px;color:#9CA3AF;font-size:12px;cursor:pointer">Cancelar</button>
      <button onclick="dbSalvarExtra()" style="padding:6px 14px;background:#10B981;border:none;border-radius:6px;color:#fff;font-size:12px;cursor:pointer;font-weight:500">Salvar</button>
    </div>
  </div>
</div>
"""


def diario_js():
    # Raw string — backslashes are literal (JS template literals preserved as-is)
    return r"""
let _dbTodayStr = '', _dbData = null, _dbAcabouAbrir = false;

function _dbSlug(s) { return (s||'').toLowerCase().replace(/[^a-z0-9]/g,''); }
function _dbTipoCls(tipo) {
  const m = {'Análise':'analise','Gemba':'gemba','Reunião':'reuniao','1:1':'1a1','Treinamento':'treinamento'};
  return 'db-tipo db-t-'+(m[tipo]||'extra');
}
function _dbItemHtml(item, isExtra) {
  const dc=item.feito?'db-done':'', ck=item.feito?'✓':'';
  const time=(item.hora_ini&&item.hora_fim)?`${item.hora_ini}–${item.hora_fim}`:(item.hora_ini||'');
  const aEsc=(item.atividade||'').replace(/'/g,"\\'");
  const oEsc=(item.obs||'').replace(/"/g,'&quot;');
  const delBtn=isExtra?`<button onclick="dbDeletarExtra('${aEsc}')" style="background:none;border:none;color:#6B7280;font-size:11px;cursor:pointer;padding:0 2px;margin-left:auto">✕</button>`:'';
  const obsTxt=item.obs?`<div class="db-obs-txt">${item.obs}</div>`:'';
  return `<div class="db-act-item ${dc}" id="db-i-${_dbSlug(item.atividade)}">
  <div class="db-check ${dc}" onclick="dbToggle('${aEsc}','${item.hora_ini||''}','${item.hora_fim||''}','${item.tipo||''}')">${ck}</div>
  <div class="db-act-body"><div class="db-act-row">
    <span class="db-act-name">${item.atividade}</span>
    ${time?`<span class="db-act-time">${time}</span>`:''}
    <span class="${_dbTipoCls(item.tipo)}">${item.tipo}</span>${delBtn}
  </div>${obsTxt}
  <input class="db-obs-inp" value="${oEsc}" placeholder="Observação…"
    onblur="dbSalvarObs('${aEsc}','${item.hora_ini||''}','${item.hora_fim||''}','${item.tipo||''}',this.value)"
    onkeydown="if(event.key==='Enter')this.blur()">
  </div></div>`;
}
function _dbRender(data) {
  const list=document.getElementById('db-list'), xtra=document.getElementById('db-extras');
  if(!list||!xtra) return;
  const items=data.items||[], extras=data.extras||[];
  const done=items.filter(i=>i.feito).length, tot=items.length;
  const ptxt=document.getElementById('db-progress-txt'), pbar=document.getElementById('db-progress-bar');
  if(ptxt) ptxt.textContent=`${done}/${tot} feitas`;
  if(pbar) pbar.style.width=tot?`${Math.round(done/tot*100)}%`:'0%';
  const manha=items.filter(i=>parseInt((i.hora_ini||'00').split(':')[0])<12);
  const tarde=items.filter(i=>parseInt((i.hora_ini||'00').split(':')[0])>=12);
  let html='';
  if(manha.length){html+='<div class="db-section-lbl">Manhã</div>';manha.forEach(i=>{html+=_dbItemHtml(i,false);});}
  if(tarde.length){html+='<div class="db-section-lbl">Tarde</div>';tarde.forEach(i=>{html+=_dbItemHtml(i,false);});}
  list.innerHTML=html;
  xtra.innerHTML=extras.map(i=>_dbItemHtml(i,true)).join('');
}
function _dbSetStatus(e) {
  const el=document.getElementById('db-status');
  if(!el) return;
  if(e==='ativo'){el.style.color='#10B981';el.textContent='🟢';}
  else{el.style.color='#f87171';el.textContent='🔴 offline';}
}
async function carregarDiario() {
  _dbTodayStr=new Date().toISOString().slice(0,10);
  const dias=['Dom','Seg','Ter','Qua','Qui','Sex','Sáb'], now=new Date();
  const lbl=document.getElementById('db-date-lbl');
  if(lbl) lbl.textContent=`${dias[now.getDay()]}, ${now.getDate()}/${(now.getMonth()+1).toString().padStart(2,'0')}`;
  try {
    _dbData = await api('diario');
    if(_dbData) _dbRender(_dbData);
  } catch(e){}
}
function dbTogglePanel() {
  const panel=document.getElementById('db-panel');
  if(!panel) return;
  if(panel.style.display!=='none'){panel.style.display='none';return;}
  panel.style.display='block';
  _dbAcabouAbrir=true; setTimeout(()=>{_dbAcabouAbrir=false;},150);
  if(!_dbData) carregarDiario();
}
function dbFecharPanel(){const p=document.getElementById('db-panel');if(p)p.style.display='none';}
document.addEventListener('click',function(ev){
  if(_dbAcabouAbrir) return;
  const panel=document.getElementById('db-panel');
  if(!panel||panel.style.display==='none') return;
  const modal=document.getElementById('db-modal-bg');
  if(modal&&modal.style.display!=='none') return;
  if(!panel.contains(ev.target)) panel.style.display='none';
});
async function dbToggle(atividade,hi,hf,tipo){
  const el=document.getElementById('db-i-'+_dbSlug(atividade));
  const ck=el?.querySelector('.db-check');
  if(!el||!ck) return;
  const agora=!ck.classList.contains('db-done');
  ck.classList.toggle('db-done',agora); ck.textContent=agora?'✓':'';
  el.classList.toggle('db-done',agora);
  if(_dbData){
    const item=_dbData.items.find(i=>i.atividade===atividade);
    if(item) item.feito=agora;
    const done=_dbData.items.filter(i=>i.feito).length, tot=_dbData.items.length;
    const ptxt=document.getElementById('db-progress-txt'), pbar=document.getElementById('db-progress-bar');
    if(ptxt) ptxt.textContent=`${done}/${tot} feitas`;
    if(pbar) pbar.style.width=tot?`${Math.round(done/tot*100)}%`:'0%';
  }
  try{await api('diario_toggle',{data:_dbTodayStr,atividade,hora_ini:hi,hora_fim:hf,tipo,feito:agora},'POST');}catch(e){}
}
const _dbObsTmr={};
function dbSalvarObs(atividade,hi,hf,tipo,obs){
  clearTimeout(_dbObsTmr[atividade]);
  _dbObsTmr[atividade]=setTimeout(async()=>{
    try{await api('diario_obs',{data:_dbTodayStr,atividade,hora_ini:hi,hora_fim:hf,tipo,obs},'POST');}catch(e){}
  },800);
}
function dbAbrirModal(){const bg=document.getElementById('db-modal-bg');if(bg){bg.style.display='flex';document.getElementById('db-m-atv').focus();}}
function dbFecharModal(){const bg=document.getElementById('db-modal-bg');if(bg)bg.style.display='none';}
async function dbSalvarExtra(){
  const atividade=document.getElementById('db-m-atv').value.trim();
  const m=document.getElementById('db-m-atv');
  if(!atividade){m.style.borderColor='#ef4444';m.focus();return;}
  m.style.borderColor='';
  const hi=document.getElementById('db-m-ini').value, hf=document.getElementById('db-m-fim').value, obs=document.getElementById('db-m-obs').value;
  try{
    const r=await api('diario_extra',{data:_dbTodayStr,atividade,hora_ini:hi,hora_fim:hf,obs},'POST');
    if(r&&r.ok){
      dbFecharModal();
      ['db-m-atv','db-m-ini','db-m-fim','db-m-obs'].forEach(id=>{document.getElementById(id).value='';});
      if(_dbData){_dbData.extras=_dbData.extras||[];_dbData.extras.push({hora_ini:hi,hora_fim:hf,atividade,tipo:'Extra',feito:false,obs,extra:true});document.getElementById('db-extras').innerHTML=_dbData.extras.map(i=>_dbItemHtml(i,true)).join('');}
    }
  }catch(e){}
}
async function dbDeletarExtra(atividade){
  if(!confirm(`Remover "${atividade}"?`)) return;
  try{
    await api('diario_delete_extra',{data:_dbTodayStr,atividade},'POST');
    if(_dbData&&_dbData.extras){_dbData.extras=_dbData.extras.filter(i=>i.atividade!==atividade);document.getElementById('db-extras').innerHTML=_dbData.extras.map(i=>_dbItemHtml(i,true)).join('');}
  }catch(e){}
}
"""
