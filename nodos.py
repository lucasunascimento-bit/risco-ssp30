"""
nodos.py — Análise de Nodos para o Dashboard de Fraude SSP30.
Consulta DM_LP_MELI_OPTIMIZADO e injeta tab-nodos em fraude.html.
Uso: python nodos.py
"""

import json
from datetime import datetime
from pathlib import Path

from google.cloud import bigquery
from google.auth import default

FACILITY  = 'Guarulhos Mega'
INICIO    = '2026-01-01'
FRAUDE_HTML = Path(__file__).parent / 'fraude.html'

QUERY = """
SELECT
  IFNULL(NODES_LM, 'Não Identificado')                      AS nodo,
  COUNT(DISTINCT SHIPMENT_ID)                                AS total_shps,
  COUNT(DISTINCT DRIVER_ID)                                  AS drivers,
  COUNT(DISTINCT CUS_NICKNAME_SEL)                           AS sellers,
  COUNT(DISTINCT CUS_NICKNAME_BUY)                           AS buyers,
  ROUND(SUM(BPP_CASHOUT_USD), 2)                             AS bpp,
  APPROX_TOP_COUNT(Classification_LM, 1)[OFFSET(0)].value   AS classe_principal,
  COUNTIF(Classification_LM LIKE 'FRAUD%%'
       OR Classification_LM = 'STOLEN ON ROUTE'
       OR Classification_LM = 'PNR C'
       OR Classification_LM = 'EMPTY BOX')                  AS fraud_shps,
  COUNTIF(Classification_LM LIKE 'DAMAGED%%')               AS damaged_shps,
  APPROX_TOP_COUNT(GEO_RCV_STATE_NAME, 1)[OFFSET(0)].value  AS estado_principal,
  APPROX_TOP_COUNT(GEO_RCV_CITY_NAME, 1)[OFFSET(0)].value   AS cidade_principal,
  ARRAY_AGG(DISTINCT FORMAT_DATE('%%Y-%%m', DATE_BPP)) AS meses,
  ARRAY_AGG(DISTINCT IFNULL(GEO_RCV_STATE_NAME,'?') LIMIT 5) AS estados,
  ARRAY_AGG(CAST(SHIPMENT_ID AS STRING) ORDER BY BPP_CASHOUT_USD DESC LIMIT 20) AS shp_sample
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = '{facility}'
  AND DATE_BPP >= '{inicio}'
  AND DATE_BPP <= CURRENT_DATE()
GROUP BY 1
ORDER BY bpp DESC
""".format(facility=FACILITY, inicio=INICIO)


def conectar():
    creds, project = default()
    return bigquery.Client(credentials=creds, project='meli-bi-data')


def carregar_dados():
    print('Conectando ao BigQuery...')
    client = conectar()
    print('Consultando nodos...')
    rows = list(client.query(QUERY).result())
    print(f'  {len(rows)} nodos retornados')

    nodos = []
    for row in rows:
        meses  = sorted(m for m in (row['meses'] or []) if m)
        estados = [e for e in (row['estados'] or []) if e and e != '?']
        shps   = list(dict.fromkeys(str(s) for s in (row['shp_sample'] or []) if s))[:20]
        total  = int(row['total_shps'])
        fraud  = int(row['fraud_shps'])
        dmg    = int(row['damaged_shps'])
        pct_fraud = round(fraud / total * 100, 1) if total else 0.0
        nodos.append({
            'nodo':      row['nodo'] or 'Não Identificado',
            'total':     total,
            'drivers':   int(row['drivers']),
            'sellers':   int(row['sellers']),
            'buyers':    int(row['buyers']),
            'bpp':       float(row['bpp'] or 0),
            'classe':    row['classe_principal'] or '',
            'fraud':     fraud,
            'damaged':   dmg,
            'pct_fraud': pct_fraud,
            'estado':    row['estado_principal'] or '',
            'cidade':    row['cidade_principal'] or '',
            'estados':   estados,
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
    top_bpp_json  = json.dumps([{'nodo': n['nodo'], 'bpp': n['bpp']} for n in top_bpp], ensure_ascii=False)
    top_shps_json = json.dumps([{'nodo': n['nodo'], 'total': n['total']} for n in top_shps], ensure_ascii=False)

    now = datetime.now().strftime('%d/%m/%Y %H:%M')

    html = f"""<div id="tab-nodos" class="content">
<div style="padding:20px 32px">

  <!-- Header -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:16px;font-weight:700;color:#f9fafb">Análise de Nodos</div>
      <div style="font-size:11px;color:#6b7280;margin-top:2px">SSP30 — Guarulhos Mega | desde Jan/2026 | atualizado {now}</div>
    </div>
    <button onclick="exportCSVNodos()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:5px 12px;font-size:11px;cursor:pointer">⬇ CSV</button>
  </div>

  <!-- KPIs -->
  <div style="display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap">
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#34d399">{total_nodos}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">Nodos</div>
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
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:10px">Nodos — BPP Total (USD)</div>
      <div style="position:relative;height:200px"><canvas id="nodChtBpp"></canvas></div>
    </div>
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:10px">Nodos — SHPs Totais</div>
      <div style="position:relative;height:200px"><canvas id="nodChtShps"></canvas></div>
    </div>
  </div>

  <!-- Filtros -->
  <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center">
    <input id="nod-busca" type="text" placeholder="Buscar nodo..." oninput="filtrarNodos()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px 12px;font-size:12px;flex:1;min-width:180px">
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
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">SHPs</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Drivers</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Sellers</th>
          <th style="text-align:right;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">BPP (USD)</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Fraude</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">% Fraude</th>
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Estado Principal</th>
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

function buildNodoCharts(){{
  var eB=document.getElementById('nodChtBpp');
  var eS=document.getElementById('nodChtShps');
  if(eB){{
    if(nodChtBpp)nodChtBpp.destroy();
    nodChtBpp=new Chart(eB,{{
      type:'bar',
      data:{{labels:NOD_TOP_BPP.map(function(n){{return n.nodo.length>20?n.nodo.slice(0,19)+'…':n.nodo;}}),
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
      data:{{labels:NOD_TOP_SHPS.map(function(n){{return n.nodo.length>20?n.nodo.slice(0,19)+'…':n.nodo;}}),
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
  var fl=(document.getElementById('nod-filtro')||{{}}).value||'';
  var dados=NODOS_DATA.filter(function(n){{
    var okQ=!q||n.nodo.toLowerCase().indexOf(q)>=0||n.estado.toLowerCase().indexOf(q)>=0||n.cidade.toLowerCase().indexOf(q)>=0;
    var okF=!fl||(fl==='fraud'&&n.fraud>0)||(fl==='top_bpp'&&n.bpp>1000);
    return okQ&&okF;
  }});
  var tb=document.getElementById('nod-tbody');
  if(!tb)return;
  var ct=document.getElementById('nod-count');if(ct)ct.textContent=dados.length;
  tb.innerHTML=dados.map(function(n,i){{
    var bppColor=n.bpp>50000?'#f87171':n.bpp>10000?'#fbbf24':'#9ca3af';
    var fraudBg=n.fraud>0?'background:rgba(239,68,68,.1);color:#f87171':'color:#4b5563';
    var pctColor=n.pct_fraud>20?'#f87171':n.pct_fraud>5?'#fbbf24':'#86efac';
    return '<tr style="border-bottom:1px solid #111827">'
      +'<td style="padding:7px 10px;color:#34d399;font-weight:600">'+n.nodo+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#e5e7eb">'+n.total.toLocaleString()+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#9ca3af">'+n.drivers+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#9ca3af">'+n.sellers+'</td>'
      +'<td style="padding:7px 10px;text-align:right;color:'+bppColor+';font-weight:700">US$ '+n.bpp.toLocaleString(\'pt-BR\',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</td>'
      +'<td style="padding:7px 10px;text-align:center;'+fraudBg+';border-radius:4px">'+n.fraud+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:'+pctColor+'">'+n.pct_fraud+'%</td>'
      +'<td style="padding:7px 10px;color:#9ca3af;font-size:10px">'+n.estado+'</td>'
      +'<td style="padding:7px 10px;color:#9ca3af;font-size:10px">'+n.classe+'</td>'
      +'</tr>';
  }}).join('');
}}

window.exportCSVNodos=function(){{
  var rows=[['Nodo','SHPs','Drivers','Sellers','Buyers','BPP USD','Fraude','Damaged','% Fraude','Estado Principal','Cidade Principal','Classe Principal']];
  NODOS_DATA.forEach(function(n){{rows.push([n.nodo,n.total,n.drivers,n.sellers,n.buyers,n.bpp,n.fraud,n.damaged,n.pct_fraud,n.estado,n.cidade,n.classe]);}});
  var csv=rows.map(function(r){{return r.map(function(v){{return'"'+String(v).replace(/"/g,'""')+'"';}}).join(',');}}).join('\\n');
  var a=document.createElement('a');a.href='data:text/csv;charset=utf-8,\\uFEFF'+encodeURIComponent(csv);
  a.download='nodos_ssp30.csv';a.click();
}};

window.limparFiltrosNodos=function(){{
  var b=document.getElementById('nod-busca');if(b)b.value='';
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
        print(f'\n✓ Tab Nodos injetada com {len(nodos)} nodos')
    else:
        print('\n✗ Falha na injeção')


if __name__ == '__main__':
    main()
