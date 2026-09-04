"""
nodos.py — Análise de Nodos (Places/NEX/DC/PU/XPT) para o Dashboard de Fraude SSP30.
Consulta BT_SHP_PLACES_AND_NODES + DM_LP_MELI_OPTIMIZADO e injeta tab-nodos em fraude.html.
Uso: python nodos.py
"""

import json
from datetime import datetime
from pathlib import Path

from google.cloud import bigquery
from google.auth import default

FACILITY    = 'Guarulhos Mega'
INICIO      = '2026-01-01'
FRAUDE_HTML = Path(__file__).parent / 'fraude.html'

_FC = (
    "Classification_LM LIKE 'FRAUD%' "
    "OR Classification_LM = 'STOLEN ON ROUTE' "
    "OR Classification_LM = 'PNR C' "
    "OR Classification_LM = 'EMPTY BOX'"
)

QUERY = f"""
WITH shp_dedup AS (
  SELECT
    SAFE_CAST(SHIPMENT_ID AS STRING)                          AS sid,
    MAX(BPP_CASHOUT_USD)                                      AS bpp,
    MAX(IFNULL(Classification_LM, ''))                        AS classe,
    MAX(DRIVER_ID)                                             AS driver_id,
    MAX(CUS_NICKNAME_SEL)                                      AS seller,
    MAX(CUS_NICKNAME_BUY)                                      AS buyer,
    LOGICAL_OR({_FC})                                          AS is_fraud,
    LOGICAL_OR(Classification_LM LIKE 'DAMAGED%')             AS is_damaged,
    MAX(FORMAT_DATE('%Y-%m', date_bpp))                        AS mes
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = '{FACILITY}'
    AND date_bpp >= '{INICIO}'
    AND date_bpp <= CURRENT_DATE()
  GROUP BY 1
)
SELECT
  p.SHP_AGENCY_ID                                             AS place_id,
  p.SERVICE_TYPE                                              AS tipo,
  MAX(p.SHP_AGEN_DESC)                                        AS nome,
  MAX(p.SHP_AGEN_NODE_ID)                                     AS node_id,
  MAX(p.SHP_AGEN_STATE_NAME)                                  AS estado,
  MAX(p.SHP_AGEN_CITY_NAME)                                   AS cidade,
  COUNT(DISTINCT p.SHP_SHIPMENT_ID)                           AS total_shps,
  COUNT(DISTINCT f.driver_id)                                 AS drivers,
  COUNT(DISTINCT f.seller)                                    AS sellers,
  COUNT(DISTINCT f.buyer)                                     AS buyers,
  ROUND(SUM(f.bpp), 2)                                        AS bpp,
  COUNTIF(f.is_fraud)                                         AS fraud_shps,
  COUNTIF(f.is_damaged)                                       AS damaged_shps,
  APPROX_TOP_COUNT(f.classe, 1)[OFFSET(0)].value              AS classe_principal,
  ARRAY_AGG(DISTINCT f.mes IGNORE NULLS)                      AS meses,
  ARRAY_AGG(CAST(p.SHP_SHIPMENT_ID AS STRING)
      ORDER BY f.bpp DESC LIMIT 20)                           AS shp_sample
FROM `meli-bi-data.WHOWNER.BT_SHP_PLACES_AND_NODES` p
INNER JOIN shp_dedup f ON CAST(p.SHP_SHIPMENT_ID AS STRING) = f.sid
WHERE p.SERVICE_TYPE IN ('DO','NEX','DC','PU','XPT')
GROUP BY 1, 2
ORDER BY bpp DESC
"""

TIPO_LBL = {'DO': 'Place', 'NEX': 'NEX', 'DC': 'DC', 'PU': 'Pickup', 'XPT': 'XPT'}


def conectar():
    creds, project = default()
    return bigquery.Client(credentials=creds, project='meli-bi-data')


def carregar_dados():
    print('Conectando ao BigQuery...')
    client = conectar()
    print('Consultando nodos (places/NEX/DC/PU/XPT)...')
    rows = list(client.query(QUERY).result())
    print(f'  {len(rows)} nodo(s) retornado(s)')

    nodos = []
    for row in rows:
        meses = sorted(m for m in (row['meses'] or []) if m)
        shps  = list(dict.fromkeys(str(s) for s in (row['shp_sample'] or []) if s))[:20]
        total = int(row['total_shps'])
        fraud = int(row['fraud_shps'])
        dmg   = int(row['damaged_shps'])
        pct_fraud = round(fraud / total * 100, 1) if total else 0.0
        tipo = row['tipo'] or ''
        nodos.append({
            'nodo':      row['nome'] or row['place_id'] or 'Não Identificado',
            'node_id':   row['node_id'] or '',
            'place_id':  row['place_id'] or '',
            'tipo':      tipo,
            'tipo_lbl':  TIPO_LBL.get(tipo, tipo),
            'total':     total,
            'drivers':   int(row['drivers']),
            'sellers':   int(row['sellers']),
            'buyers':    int(row['buyers']),
            'bpp':       float(row['bpp'] or 0),
            'classe':    row['classe_principal'] or '',
            'fraud':     fraud,
            'damaged':   dmg,
            'pct_fraud': pct_fraud,
            'estado':    row['estado'] or '',
            'cidade':    row['cidade'] or '',
            'meses':     meses,
            'shps':      shps,
        })

    return nodos


def gerar_tab_html(nodos):
    total_nodos  = len(nodos)
    total_shps   = sum(n['total'] for n in nodos)
    total_bpp    = round(sum(n['bpp'] for n in nodos), 2)
    total_fraud  = sum(n['fraud'] for n in nodos)
    top_bpp      = sorted(nodos, key=lambda x: x['bpp'], reverse=True)[:12]
    top_shps     = sorted(nodos, key=lambda x: x['total'], reverse=True)[:12]

    nodos_json    = json.dumps(nodos, ensure_ascii=False)
    top_bpp_json  = json.dumps([{'nodo': n['nodo']+' ('+n['tipo_lbl']+')', 'bpp': n['bpp']} for n in top_bpp], ensure_ascii=False)
    top_shps_json = json.dumps([{'nodo': n['nodo']+' ('+n['tipo_lbl']+')', 'total': n['total']} for n in top_shps], ensure_ascii=False)

    now = datetime.now().strftime('%d/%m/%Y %H:%M')

    html = f"""<div id="tab-nodos" class="content">
<div style="padding:20px 32px">

  <!-- Header -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:16px;font-weight:700;color:#f9fafb">Análise de Nodos — Places / NEX / DC / Pickup / XPT</div>
      <div style="font-size:11px;color:#6b7280;margin-top:2px">SSP30 — Guarulhos Mega | desde Jan/2026 | atualizado {now}</div>
    </div>
    <button onclick="exportCSVNodos()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:5px 12px;font-size:11px;cursor:pointer">⬇ CSV</button>
  </div>

  <div style="font-size:10px;color:#6b7280;background:#0d1321;border:1px solid #1f2937;border-radius:6px;padding:8px 12px;margin-bottom:14px">
    Um mesmo shipment pode passar por mais de um nó (ex: um NEX e depois um Place de entrega) — os valores de BPP/SHPs são por nó e não devem ser somados entre tipos diferentes.
  </div>

  <!-- KPIs -->
  <div style="display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap">
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#34d399">{total_nodos:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">Nodos (place x tipo)</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#e5e7eb">{total_shps:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">SHPs Totais</div>
    </div>
    <div style="background:#1a0505;border:1px solid #7f1d1d;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#f87171">US$ {total_bpp:,.0f}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">BPP Total</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#fbbf24">{total_fraud:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">SHPs Fraude</div>
    </div>
  </div>

  <!-- Charts -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px">
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:10px">Top 12 Nodos — BPP Total (USD)</div>
      <div style="position:relative;height:200px"><canvas id="nodChtBpp"></canvas></div>
    </div>
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:10px">Top 12 Nodos — SHPs Totais</div>
      <div style="position:relative;height:200px"><canvas id="nodChtShps"></canvas></div>
    </div>
  </div>

  <!-- Filtros -->
  <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center">
    <input id="nod-busca" type="text" placeholder="Buscar nodo, estado ou cidade..." oninput="filtrarNodos()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px 12px;font-size:12px;flex:1;min-width:180px">
    <select id="nod-tipo" onchange="filtrarNodos()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px 10px;font-size:12px">
      <option value="">Todos os tipos</option>
      <option value="DO">Place</option>
      <option value="NEX">NEX</option>
      <option value="DC">DC</option>
      <option value="PU">Pickup</option>
      <option value="XPT">XPT</option>
    </select>
    <select id="nod-filtro" onchange="filtrarNodos()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px 10px;font-size:12px">
      <option value="">Todos</option>
      <option value="fraud">Com fraude</option>
      <option value="top_bpp">Top BPP (&gt; US$ 1.000)</option>
    </select>
    <button onclick="limparFiltrosNodos()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:6px 12px;font-size:11px;cursor:pointer">Limpar</button>
    <span style="font-size:11px;color:#6b7280">Exibindo <b id="nod-count" style="color:#e5e7eb">-</b> nodos</span>
  </div>

  <!-- Tabela -->
  <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead>
        <tr style="border-bottom:1px solid #374151">
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Nodo</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Tipo</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">SHPs</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Drivers</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Sellers</th>
          <th style="text-align:right;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">BPP (USD)</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Fraude</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">% Fraude</th>
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Estado</th>
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Classe Principal</th>
        </tr>
      </thead>
      <tbody id="nod-tbody"></tbody>
    </table>
  </div>

</div>

<script>
(function(){{
var NODOS_DATA={nodos_json};
var NOD_TOP_BPP={top_bpp_json};
var NOD_TOP_SHPS={top_shps_json};
var nodChtBpp=null,nodChtShps=null;
var TIPO_CLR={{DO:'#34d399',NEX:'#60a5fa',DC:'#a78bfa',PU:'#fbbf24',XPT:'#f472b6'}};

function buildNodoCharts(){{
  var eB=document.getElementById('nodChtBpp');
  var eS=document.getElementById('nodChtShps');
  if(eB){{
    if(nodChtBpp)nodChtBpp.destroy();
    nodChtBpp=new Chart(eB,{{
      type:'bar',
      data:{{labels:NOD_TOP_BPP.map(function(n){{return n.nodo.length>26?n.nodo.slice(0,25)+'…':n.nodo;}}),
             datasets:[{{data:NOD_TOP_BPP.map(function(n){{return n.bpp;}}),backgroundColor:'#ef4444',borderRadius:3,barThickness:14}}]}},
      options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return'US$ '+Math.round(c.raw).toLocaleString();}}}}}}}},
        scales:{{x:{{ticks:{{color:'#6b7280',font:{{size:9}},callback:function(v){{return'US$ '+Math.round(v).toLocaleString();}}}},grid:{{color:'#111827'}}}},y:{{ticks:{{color:'#9ca3af',font:{{size:9}}}},grid:{{display:false}}}}}}
      }}
    }});
  }}
  if(eS){{
    if(nodChtShps)nodChtShps.destroy();
    nodChtShps=new Chart(eS,{{
      type:'bar',
      data:{{labels:NOD_TOP_SHPS.map(function(n){{return n.nodo.length>26?n.nodo.slice(0,25)+'…':n.nodo;}}),
             datasets:[{{data:NOD_TOP_SHPS.map(function(n){{return n.total;}}),backgroundColor:'#34d399',borderRadius:3,barThickness:14}}]}},
      options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return c.raw+' SHPs';}}}}}}}},
        scales:{{x:{{ticks:{{color:'#6b7280',font:{{size:9}}}},grid:{{color:'#111827'}}}},y:{{ticks:{{color:'#9ca3af',font:{{size:9}}}},grid:{{display:false}}}}}}
      }}
    }});
  }}
}}

function filtrarNodos(){{
  var q=((document.getElementById('nod-busca')||{{}}).value||'').toLowerCase().trim();
  var tp=(document.getElementById('nod-tipo')||{{}}).value||'';
  var fl=(document.getElementById('nod-filtro')||{{}}).value||'';
  var dados=NODOS_DATA.filter(function(n){{
    var okQ=!q||n.nodo.toLowerCase().indexOf(q)>=0||n.estado.toLowerCase().indexOf(q)>=0||n.cidade.toLowerCase().indexOf(q)>=0||(n.node_id||'').toLowerCase().indexOf(q)>=0||(n.place_id||'').toLowerCase().indexOf(q)>=0;
    var okT=!tp||n.tipo===tp;
    var okF=!fl||(fl==='fraud'&&n.fraud>0)||(fl==='top_bpp'&&n.bpp>1000);
    return okQ&&okT&&okF;
  }});
  var tb=document.getElementById('nod-tbody');
  if(!tb)return;
  var ct=document.getElementById('nod-count');if(ct)ct.textContent=dados.length;
  var truncado=dados.length>1000;
  var render=truncado?dados.slice(0,1000):dados;
  tb.innerHTML=render.map(function(n,i){{
    var bppColor=n.bpp>50000?'#f87171':n.bpp>10000?'#fbbf24':'#9ca3af';
    var fraudBg=n.fraud>0?'background:rgba(239,68,68,.1);color:#f87171':'color:#4b5563';
    var pctColor=n.pct_fraud>20?'#f87171':n.pct_fraud>5?'#fbbf24':'#86efac';
    var tipoClr=TIPO_CLR[n.tipo]||'#9ca3af';
    var idLbl=n.node_id?(n.tipo_lbl+' '+n.node_id):(n.place_id||'');
    return '<tr style="border-bottom:1px solid #111827">'
      +'<td style="padding:7px 10px;color:#34d399;font-weight:600">'+n.nodo+(idLbl?' <span style="color:#6b7280;font-weight:400;font-size:10px">('+idLbl+')</span>':'')+'</td>'
      +'<td style="padding:7px 10px;text-align:center"><span style="font-size:9px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,255,255,.06);color:'+tipoClr+'">'+n.tipo_lbl+'</span></td>'
      +'<td style="padding:7px 10px;text-align:center;color:#e5e7eb">'+n.total.toLocaleString()+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#9ca3af">'+n.drivers+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#9ca3af">'+n.sellers+'</td>'
      +'<td style="padding:7px 10px;text-align:right;color:'+bppColor+';font-weight:700">US$ '+n.bpp.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</td>'
      +'<td style="padding:7px 10px;text-align:center;'+fraudBg+';border-radius:4px">'+n.fraud+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:'+pctColor+'">'+n.pct_fraud+'%</td>'
      +'<td style="padding:7px 10px;color:#9ca3af;font-size:10px">'+n.estado+'</td>'
      +'<td style="padding:7px 10px;color:#9ca3af;font-size:10px">'+n.classe+'</td>'
      +'</tr>';
  }}).join('')+(truncado?'<tr><td colspan="10" style="padding:8px;text-align:center;color:#374151;font-size:10px">Mostrando 1000 de '+dados.length.toLocaleString('pt-BR')+' (ordenado por BPP). Use a busca ou o filtro de tipo pra refinar.</td></tr>':'');
}}

window.exportCSVNodos=function(){{
  var rows=[['Nodo','Node ID','Place ID','Tipo','SHPs','Drivers','Sellers','Buyers','BPP USD','Fraude','Damaged','% Fraude','Estado','Cidade','Classe Principal']];
  NODOS_DATA.forEach(function(n){{rows.push([n.nodo,n.node_id,n.place_id,n.tipo_lbl,n.total,n.drivers,n.sellers,n.buyers,n.bpp,n.fraud,n.damaged,n.pct_fraud,n.estado,n.cidade,n.classe]);}});
  var csv=rows.map(function(r){{return r.map(function(v){{return'"'+String(v).replace(/"/g,'""')+'"';}}).join(',');}}).join('\\n');
  var a=document.createElement('a');a.href='data:text/csv;charset=utf-8,\\uFEFF'+encodeURIComponent(csv);
  a.download='nodos_ssp30.csv';a.click();
}};

window.limparFiltrosNodos=function(){{
  var b=document.getElementById('nod-busca');if(b)b.value='';
  var t=document.getElementById('nod-tipo');if(t)t.value='';
  var f=document.getElementById('nod-filtro');if(f)f.value='';
  filtrarNodos();
}};

window.NODOS_DATA=NODOS_DATA;
window.filtrarNodos=filtrarNodos;
window.buildNodoCharts=buildNodoCharts;

document.addEventListener('DOMContentLoaded',function(){{
  filtrarNodos();
  var badge=document.getElementById('tab-count-nodos');
  if(badge)badge.textContent=NODOS_DATA.length;
}});
}})();
</script>
</div>
"""
    return html


def find_and_replace_tab(content, tab_id, new_html):
    """Substitui um tab div completo rastreando profundidade de divs."""
    start_marker = f'<div id="{tab_id}" class="content">'
    idx = content.find(start_marker)
    if idx == -1:
        print(f'  WARNING: {tab_id} não encontrado')
        return content, False

    depth = 0
    pos = idx
    while pos < len(content):
        next_open  = content.find('<div', pos)
        next_close = content.find('</div>', pos)
        if next_open == -1 and next_close == -1:
            break
        if next_open != -1 and (next_close == -1 or next_open < next_close):
            depth += 1
            pos = next_open + 4
        else:
            depth -= 1
            end = next_close + 6
            pos = end
            if depth == 0:
                if pos < len(content) and content[pos] == '\n':
                    end = pos + 1
                return content[:idx] + new_html + '\n' + content[end:], True

    return content, False


def inject_nodos_sidebar(html):
    if 'data-tab="nodos"' in html:
        return html
    old = '<div class="sb-item" data-tab="buyers"'
    new = (
        '<div class="sb-item" data-tab="nodos" onclick="showTab(\'nodos\',this);'
        'setTimeout(function(){if(window.buildNodoCharts)window.buildNodoCharts();'
        'if(window.filtrarNodos)window.filtrarNodos();},80)">\n'
        '      <i data-lucide="map-pin" width="14" height="14" class="ci"></i>\n'
        '      Nodos <span class="sb-badge" id="tab-count-nodos">0</span>\n'
        '    </div>\n'
        '    <div class="sb-item" data-tab="buyers"'
    )
    return html.replace(old, new, 1)


def injetar_no_fraude(tab_html, nodos, tab_id='tab-nodos'):
    print(f'Lendo {FRAUDE_HTML}...')
    content = FRAUDE_HTML.read_text(encoding='utf-8')

    content = inject_nodos_sidebar(content)

    content, ok = find_and_replace_tab(content, tab_id, tab_html)
    if not ok:
        ins = content.rfind('</main>')
        if ins == -1:
            ins = content.rfind('</body>')
        if ins > 0:
            content = content[:ins] + tab_html + '\n' + content[ins:]
            ok = True

    if ok:
        FRAUDE_HTML.write_text(content, encoding='utf-8')
        mb = FRAUDE_HTML.stat().st_size / 1024 / 1024
        print(f'  Salvo: {FRAUDE_HTML.name} ({mb:.1f} MB)')
    return ok


def main():
    nodos = carregar_dados()
    tab_html = gerar_tab_html(nodos)
    if injetar_no_fraude(tab_html, nodos, 'tab-nodos'):
        print(f'\nOK - Tab Nodos injetada com {len(nodos)} nodos')
    else:
        print('\nERRO - Falha na injecao')


if __name__ == '__main__':
    main()
