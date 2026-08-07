"""
sellers.py — Análise de Sellers para o Dashboard de Fraude SSP30.
Consulta DM_LP_MELI_OPTIMIZADO e injeta tab-sellers em fraude.html.
Uso: python sellers.py
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
  CUS_NICKNAME_SEL                                           AS seller,
  COUNT(DISTINCT SHIPMENT_ID)                                AS total_shps,
  COUNT(DISTINCT CUS_NICKNAME_BUY)                           AS buyers,
  ROUND(SUM(BPP_CASHOUT_USD), 2)                             AS bpp,
  APPROX_TOP_COUNT(Classification_LM, 1)[OFFSET(0)].value   AS classe_principal,
  COUNTIF(Classification_LM LIKE 'FRAUD%%'
       OR Classification_LM = 'STOLEN ON ROUTE'
       OR Classification_LM = 'PNR C'
       OR Classification_LM = 'EMPTY BOX')                  AS fraud_shps,
  COUNTIF(Classification_LM LIKE 'DAMAGED%%')               AS damaged_shps,
  MIN(FORMAT_DATE('%%Y-%%m', DATE_BPP))                      AS primeiro_mes,
  MAX(FORMAT_DATE('%%Y-%%m', DATE_BPP))                      AS ultimo_mes,
  ARRAY_AGG(DISTINCT FORMAT_DATE('%%Y-%%m', DATE_BPP) ORDER BY FORMAT_DATE('%%Y-%%m', DATE_BPP)) AS meses,
  ARRAY_AGG(CAST(SHIPMENT_ID AS STRING) ORDER BY BPP_CASHOUT_USD DESC LIMIT 20) AS shp_sample
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = '{facility}'
  AND DATE_BPP >= '{inicio}'
  AND DATE_BPP <= CURRENT_DATE()
  AND CUS_NICKNAME_SEL IS NOT NULL
GROUP BY 1
HAVING total_shps >= 2
ORDER BY bpp DESC
""".format(facility=FACILITY, inicio=INICIO)


def conectar():
    creds, project = default()
    return bigquery.Client(credentials=creds, project='meli-bi-data')


def carregar_dados():
    print('Conectando ao BigQuery...')
    client = conectar()
    print('Consultando sellers...')
    rows = list(client.query(QUERY).result())
    print(f'  {len(rows)} sellers retornados')

    sellers = []
    for row in rows:
        meses  = sorted(m for m in (row['meses'] or []) if m)
        shps   = list(dict.fromkeys(str(s) for s in (row['shp_sample'] or []) if s))[:20]
        total  = int(row['total_shps'])
        fraud  = int(row['fraud_shps'])
        dmg    = int(row['damaged_shps'])
        pct_fraud = round(fraud / total * 100, 1) if total else 0.0
        sellers.append({
            'seller':      row['seller'] or '',
            'total':       total,
            'buyers':      int(row['buyers']),
            'bpp':         float(row['bpp'] or 0),
            'classe':      row['classe_principal'] or '',
            'fraud':       fraud,
            'damaged':     dmg,
            'pct_fraud':   pct_fraud,
            'primeiro_mes': row['primeiro_mes'] or '',
            'ultimo_mes':  row['ultimo_mes'] or '',
            'meses':       meses,
            'shps':        shps,
        })

    return sellers


def gerar_tab_html(sellers):
    total_sellers  = len(sellers)
    total_shps     = sum(s['total'] for s in sellers)
    total_bpp      = round(sum(s['bpp'] for s in sellers), 2)
    total_fraud    = sum(s['fraud'] for s in sellers)
    top20_bpp      = sorted(sellers, key=lambda x: x['bpp'], reverse=True)[:20]

    sellers_json = json.dumps(sellers, ensure_ascii=False)
    top20_json   = json.dumps([{'seller': s['seller'], 'bpp': s['bpp']} for s in top20_bpp], ensure_ascii=False)

    now = datetime.now().strftime('%d/%m/%Y %H:%M')

    html = f"""<div id="tab-sellers" class="content">
<div style="padding:20px 32px">

  <!-- Header -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:16px;font-weight:700;color:#f9fafb">Análise de Sellers</div>
      <div style="font-size:11px;color:#6b7280;margin-top:2px">SSP30 — Guarulhos Mega | desde Jan/2026 | atualizado {now}</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <button onclick="exportCSVSellers()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:5px 12px;font-size:11px;cursor:pointer">⬇ CSV</button>
    </div>
  </div>

  <!-- KPI cards -->
  <div style="display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap">
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#38bdf8">{total_sellers:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">Sellers</div>
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
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:10px">Top 20 Sellers — BPP (USD)</div>
      <div style="position:relative;height:260px"><canvas id="selChtBpp"></canvas></div>
    </div>
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:10px">Top 20 Sellers — SHPs Totais</div>
      <div style="position:relative;height:260px"><canvas id="selChtShps"></canvas></div>
    </div>
  </div>

  <!-- Filtros -->
  <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center">
    <input id="sel-busca" type="text" placeholder="Buscar seller..." oninput="filtrarSellers()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px 12px;font-size:12px;flex:1;min-width:180px">
    <select id="sel-classe" onchange="filtrarSellers()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px 10px;font-size:12px">
      <option value="">Todas as classes</option>
      <option value="fraud">Fraude / PNR / Roubo</option>
      <option value="damaged">Damaged</option>
    </select>
    <button onclick="limparFiltrosSellers()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:6px 12px;font-size:11px;cursor:pointer">Limpar</button>
    <span style="font-size:11px;color:#6b7280">Exibindo <b id="sel-count" style="color:#e5e7eb">-</b> sellers</span>
  </div>

  <!-- Tabela -->
  <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead>
        <tr style="border-bottom:1px solid #374151">
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">#</th>
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Seller</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">SHPs</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Buyers</th>
          <th style="text-align:right;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">BPP (USD)</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Fraude</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">% Fraude</th>
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Classe Principal</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Meses Ativos</th>
        </tr>
      </thead>
      <tbody id="sel-tbody"></tbody>
    </table>
  </div>

</div>

<script>
(function(){{
var SELLERS_DATA={sellers_json};
var SEL_TOP20={top20_json};
var selChtBpp=null,selChtShps=null;

function buildSelCharts(){{
  var elB=document.getElementById('selChtBpp');
  var elS=document.getElementById('selChtShps');
  if(elB){{
    if(selChtBpp)selChtBpp.destroy();
    selChtBpp=new Chart(elB,{{
      type:'bar',
      data:{{labels:SEL_TOP20.map(function(s){{return s.seller.length>18?s.seller.slice(0,17)+'…':s.seller;}}),
             datasets:[{{data:SEL_TOP20.map(function(s){{return s.bpp;}}),backgroundColor:'#ef4444',borderRadius:3,barThickness:10}}]}},
      options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return'US$ '+Math.round(c.raw).toLocaleString();}}}}}}}},
        scales:{{x:{{ticks:{{color:'#6b7280',font:{{size:9}},callback:function(v){{return'US$ '+Math.round(v).toLocaleString();}}}},grid:{{color:'#111827'}}}},y:{{ticks:{{color:'#9ca3af',font:{{size:9}}}},grid:{{display:false}}}}}}
      }}
    }});
  }}
  var topShps=SELLERS_DATA.slice().sort(function(a,b){{return b.total-a.total;}}).slice(0,20);
  if(elS){{
    if(selChtShps)selChtShps.destroy();
    selChtShps=new Chart(elS,{{
      type:'bar',
      data:{{labels:topShps.map(function(s){{return s.seller.length>18?s.seller.slice(0,17)+'…':s.seller;}}),
             datasets:[{{data:topShps.map(function(s){{return s.total;}}),backgroundColor:'#3b82f6',borderRadius:3,barThickness:10}}]}},
      options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return c.raw+' SHPs';}}}}}}}},
        scales:{{x:{{ticks:{{color:'#6b7280',font:{{size:9}}}},grid:{{color:'#111827'}}}},y:{{ticks:{{color:'#9ca3af',font:{{size:9}}}},grid:{{display:false}}}}}}
      }}
    }});
  }}
}}

function filtrarSellers(){{
  var q=((document.getElementById('sel-busca')||{{}}).value||'').toLowerCase().trim();
  var cl=(document.getElementById('sel-classe')||{{}}).value||'';
  var dados=SELLERS_DATA.filter(function(s){{
    var okQ=!q||s.seller.toLowerCase().indexOf(q)>=0;
    var okC=!cl||(cl==='fraud'&&s.fraud>0)||(cl==='damaged'&&s.damaged>0);
    return okQ&&okC;
  }});
  var tb=document.getElementById('sel-tbody');
  if(!tb)return;
  var ct=document.getElementById('sel-count');
  if(ct)ct.textContent=dados.length;
  tb.innerHTML=dados.slice(0,500).map(function(s,i){{
    var pctColor=s.pct_fraud>30?'#f87171':s.pct_fraud>10?'#fbbf24':'#86efac';
    var bppColor=s.bpp>10000?'#f87171':s.bpp>1000?'#fbbf24':'#9ca3af';
    var fraudBg =s.fraud>0?'background:rgba(239,68,68,.1);color:#f87171':'color:#4b5563';
    return '<tr style="border-bottom:1px solid #111827">'
      +'<td style="padding:7px 10px;color:#4b5563;font-size:10px">'+(i+1)+'</td>'
      +'<td style="padding:7px 10px;color:#38bdf8;font-weight:600">'+s.seller+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#e5e7eb">'+s.total.toLocaleString()+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#9ca3af">'+s.buyers.toLocaleString()+'</td>'
      +'<td style="padding:7px 10px;text-align:right;color:'+bppColor+';font-weight:700">US$ '+s.bpp.toLocaleString(\'pt-BR\',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</td>'
      +'<td style="padding:7px 10px;text-align:center;'+fraudBg+';border-radius:4px">'+s.fraud+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:'+pctColor+'">'+s.pct_fraud+'%</td>'
      +'<td style="padding:7px 10px;color:#9ca3af;font-size:10px">'+s.classe+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#6b7280;font-size:10px">'+s.meses.length+'</td>'
      +'</tr>';
  }}).join('');
}}

window.exportCSVSellers=function(){{
  var rows=[['Seller','SHPs','Buyers','BPP USD','Fraude','Damaged','% Fraude','Classe Principal','Primeiro Mês','Último Mês']];
  SELLERS_DATA.forEach(function(s){{rows.push([s.seller,s.total,s.buyers,s.bpp,s.fraud,s.damaged,s.pct_fraud,s.classe,s.primeiro_mes,s.ultimo_mes]);}});
  var csv=rows.map(function(r){{return r.map(function(v){{return'"'+String(v).replace(/"/g,'""')+'"';}}).join(',');}}).join('\\n');
  var a=document.createElement('a');a.href='data:text/csv;charset=utf-8,\\uFEFF'+encodeURIComponent(csv);
  a.download='sellers_ssp30.csv';a.click();
}};

window.limparFiltrosSellers=function(){{
  var b=document.getElementById('sel-busca');if(b)b.value='';
  var c=document.getElementById('sel-classe');if(c)c.value='';
  filtrarSellers();
}};

window.filtrarSellers=filtrarSellers;
window.buildSelCharts=buildSelCharts;

// Init quando a aba for ativada
document.addEventListener('DOMContentLoaded',function(){{
  filtrarSellers();
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


def injetar_no_fraude(tab_html):
    print(f'Lendo {FRAUDE_HTML}...')
    content = FRAUDE_HTML.read_text(encoding='utf-8')
    content, ok = find_and_replace_tab(content, 'tab-sellers', tab_html)
    if ok:
        FRAUDE_HTML.write_text(content, encoding='utf-8')
        print(f'  Salvo: {FRAUDE_HTML}')
    return ok


def main():
    sellers = carregar_dados()
    tab_html = gerar_tab_html(sellers)
    if injetar_no_fraude(tab_html):
        print(f'\n✓ Tab Sellers injetada com {len(sellers)} sellers')
    else:
        print('\n✗ Falha na injeção')


if __name__ == '__main__':
    main()
