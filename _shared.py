"""Constantes compartilhadas entre analise_fraude.py, gerar_dashboard.py e on_way_server.py."""

_SB_DRAG_JS = """
(function(){
var KEY='sb_order_'+(location.pathname.split('/').pop()||'idx');
var dragEl=null,sb=null;
function save(){
  var dc=0,order=Array.from(sb.children).map(function(el){
    if(el.classList.contains('sb-item'))return 'i:'+el.dataset.tab;
    if(el.classList.contains('sb-divider'))return 'd:'+(dc++);
    if(el.classList.contains('sb-section-header'))return 'h:'+el.textContent.trim();
    return null;
  }).filter(Boolean);
  try{localStorage.setItem(KEY,JSON.stringify(order));}catch(e){}
}
function restore(){
  try{
    var saved=JSON.parse(localStorage.getItem(KEY)||'null');
    if(!saved||!saved.length)return;
    var im={},hm={},da=[];
    Array.from(sb.children).forEach(function(el){
      if(el.classList.contains('sb-item'))im[el.dataset.tab]=el;
      else if(el.classList.contains('sb-section-header'))hm[el.textContent.trim()]=el;
      else if(el.classList.contains('sb-divider'))da.push(el);
    });
    var di=0;
    saved.forEach(function(e){
      var el=null;
      if(e.startsWith('i:'))el=im[e.slice(2)];
      else if(e.startsWith('h:'))el=hm[e.slice(2)];
      else if(e.startsWith('d:'))el=da[di++];
      if(el)sb.appendChild(el);
    });
  }catch(e){}
}
document.addEventListener('DOMContentLoaded',function(){
  sb=document.querySelector('.sidebar');
  if(!sb)return;
  restore();
  Array.from(sb.querySelectorAll('.sb-item')).forEach(function(el){
    el.setAttribute('draggable','true');
    var h=document.createElement('span');
    h.className='sb-drag-handle';h.textContent='⠿';
    el.insertBefore(h,el.firstChild);
  });
  sb.addEventListener('dragstart',function(e){
    var t=e.target.closest('.sb-item');
    if(!t)return;
    dragEl=t;setTimeout(function(){t.classList.add('sb-dragging');},0);
    e.dataTransfer.effectAllowed='move';
  });
  sb.addEventListener('dragend',function(){
    if(dragEl){dragEl.classList.remove('sb-dragging');dragEl=null;}
    sb.querySelectorAll('.sb-drop-before').forEach(function(el){el.classList.remove('sb-drop-before');});
    save();
  });
  sb.addEventListener('dragover',function(e){
    e.preventDefault();if(!dragEl)return;
    var t=e.target.closest('.sb-item');
    sb.querySelectorAll('.sb-drop-before').forEach(function(el){el.classList.remove('sb-drop-before');});
    if(t&&t!==dragEl){
      var r=t.getBoundingClientRect();
      if(e.clientY<r.top+r.height/2){sb.insertBefore(dragEl,t);t.classList.add('sb-drop-before');}
      else{sb.insertBefore(dragEl,t.nextSibling);}
    }
  });
  sb.addEventListener('drop',function(e){e.preventDefault();});
});
})();
"""

# Mapeamento de valor interno de finalização → label de exibição/histórico.
# Usado em on_way_server.py (_FINAL_MAP) e gerar_dashboard.py (_FINAL_HIST_MAP).
_FINAL_MAP = {
    'reversao':   'Retornou ao fluxo',
    'reversão':   'Retornou ao fluxo',
    'bpp':        'Perdido',
    'recuperado': 'Recuperado',
}
