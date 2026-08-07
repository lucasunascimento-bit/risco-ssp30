"""
buyers.py — Análise de Buyers para o Dashboard de Fraude SSP30.
Consulta DM_LP_MELI_OPTIMIZADO e injeta tab-buyers em fraude.html.
Uso: python buyers.py
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
  CUS_NICKNAME_BUY                                           AS buyer,
  COUNT(DISTINCT SHIPMENT_ID)                                AS total_shps,
  COUNT(DISTINCT CUS_NICKNAME_SEL)                           AS sellers,
  ROUND(SUM(BPP_CASHOUT_USD), 2)                             AS bpp,
  APPROX_TOP_COUNT(Classification_LM, 1)[OFFSET(0)].value   AS classe_principal,
  COUNTIF(Classification_LM LIKE 'FRAUD%%'
       OR Classification_LM = 'STOLEN ON ROUTE'
       OR Classification_LM = 'PNR C'
       OR Classification_LM = 'EMPTY BOX')                  AS fraud_shps,
  COUNTIF(Classification_LM = 'PNR C')                      AS pnr_shps,
  MIN(FORMAT_DATE('%%Y-%%m', DATE_BPP))                      AS primeiro_mes,
  MAX(FORMAT_DATE('%%Y-%%m', DATE_BPP))                      AS ultimo_mes,
  COUNT(DISTINCT FORMAT_DATE('%%Y-%%m', DATE_BPP))           AS qtd_meses,
  ARRAY_AGG(CAST(SHIPMENT_ID AS STRING) ORDER BY BPP_CASHOUT_USD DESC LIMIT 20) AS shp_sample
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = '{facility}'
  AND DATE_BPP >= '{inicio}'
  AND DATE_BPP <= CURRENT_DATE()
  AND CUS_NICKNAME_BUY IS NOT NULL
GROUP BY 1
HAVING total_shps >= 2
ORDER BY fraud_shps DESC, bpp DESC
""".format(facility=FACILITY, inicio=INICIO)


def conectar():
    creds, project = default()
    return bigquery.Client(credentials=creds, project='meli-bi-data')


def carregar_dados():
    print('Conectando ao BigQuery...')
    client = conectar()
    print('Consultando buyers...')
    rows = list(client.query(QUERY).result())
    print(f'  {len(rows)} buyers retornados')

    buyers = []
    for row in rows:
        shps  = list(dict.fromkeys(str(s) for s in (row['shp_sample'] or []) if s))[:20]
        total = int(row['total_shps'])
        fraud = int(row['fraud_shps'])
        pnr   = int(row['pnr_shps'])
        buyers.append({
            'buyer':        row['buyer'] or '',
            'total':        total,
            'sellers':      int(row['sellers']),
            'bpp':          float(row['bpp'] or 0),
            'classe':       row['classe_principal'] or '',
            'fraud':        fraud,
            'pnr':          pnr,
            'primeiro_mes': row['primeiro_mes'] or '',
            'ultimo_mes':   row['ultimo_mes'] or '',
            'qtd_meses':    int(row['qtd_meses']),
            'shps':         shps,
        })

    return buyers


def gerar_tab_html(buyers):
    total_buyers = len(buyers)
    total_shps   = sum(b['total'] for b in buyers)
    total_bpp    = round(sum(b['bpp'] for b in buyers), 2)
    total_fraud  = sum(b['fraud'] for b in buyers)
    buyers_multi = sum(1 for b in buyers if b['total'] >= 3)
    top20_bpp    = sorted(buyers, key=lambda x: x['bpp'], reverse=True)[:20]
    top20_fraud  = sorted(buyers, key=lambda x: x['fraud'], reverse=True)[:20]

    buyers_json   = json.dumps(buyers, ensure_ascii=False)
    top20_bpp_json = json.dumps([{'buyer': b['buyer'], 'bpp': b['bpp']} for b in top20_bpp], ensure_ascii=False)
    top20_fr_json  = json.dumps([{'buyer': b['buyer'], 'fraud': b['fraud']} for b in top20_fraud if b['fraud'] > 0][:20], ensure_ascii=False)

    now = datetime.now().strftime('%d/%m/%Y %H:%M')

    html = f"""<div id="tab-buyers" class="content">
<div style="padding:20px 32px">

  <!-- Header -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:16px;font-weight:700;color:#f9fafb">Análise de Buyers</div>
      <div style="font-size:11px;color:#6b7280;margin-top:2px">SSP30 — Guarulhos Mega | desde Jan/2026 | atualizado {now}</div>
    </div>
    <div style="display:flex;gap:8px">
      <button onclick="exportCSVBuyers()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:5px 12px;font-size:11px;cursor:pointer">⬇ CSV</button>
    </div>
  </div>

  <!-- KPIs -->
  <div style="display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap">
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#38bdf8">{total_buyers:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">Buyers</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#e5e7eb">{total_shps:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">SHPs</div>
    </div>
    <div style="background:#1a0505;border:1px solid #7f1d1d;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#f87171">US$ {total_bpp:,.0f}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">BPP Total</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#fbbf24">{total_fraud:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">SHPs Fraude</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#a78bfa">{buyers_multi:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">Buyers 3+ SHPs</div>
    </div>
  </div>

  <!-- Charts -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px">
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:10px">Top 20 Buyers — BPP (USD)</div>
      <div style="position:relative;height:260px"><canvas id="buyChtBpp"></canvas></div>
    </div>
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:10px">Top 20 Buyers — SHPs Fraude</div>
      <div style="position:relative;height:260px"><canvas id="buyChtFraud"></canvas></div>
    </div>
  </div>

  <!-- Filtros -->
  <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center">
    <input id="buy-busca" type="text" placeholder="Buscar buyer..." oninput="filtrarBuyers()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px 12px;font-size:12px;flex:1;min-width:180px">
    <select id="buy-filtro" onchange="filtrarBuyers()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px 10px;font-size:12px">
      <option value="">Todos</option>
      <option value="fraud">Com fraude</option>
      <option value="pnr">Com PNR</option>
      <option value="multi">3+ SHPs</option>
    </select>
    <button onclick="limparFiltrosBuyers()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:6px 12px;font-size:11px;cursor:pointer">Limpar</button>
    <span style="font-size:11px;color:#6b7280">Exibindo <b id="buy-count" style="color:#e5e7eb">-</b> buyers</span>
  </div>

  <!-- Tabela -->
  <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead>
        <tr style="border-bottom:1px solid #374151">
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">#</th>
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Buyer</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">SHPs</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Sellers</th>
          <th style="text-align:right;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">BPP (USD)</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Fraude</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">PNR</th>
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Classe Principal</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Meses</th>
        </tr>
      </thead>
      <tbody id="buy-tbody"></tbody>
    </table>
  </div>

</div>

<script>
(function(){{
var BUYERS_DATA={buyers_json};
var BUY_TOP20_BPP={top20_bpp_json};
var BUY_TOP20_FR={top20_fr_json};
var buyChtBpp=null,buyChtFraud=null;

function buildBuyCharts(){{
  var eB=document.getElementById('buyChtBpp');
  var eF=document.getElementById('buyChtFraud');
  if(eB){{
    if(buyChtBpp)buyChtBpp.destroy();
    buyChtBpp=new Chart(eB,{{
      type:'bar',
      data:{{labels:BUY_TOP20_BPP.map(function(b){{return b.buyer.length>18?b.buyer.slice(0,17)+'…':b.buyer;}}),
             datasets:[{{data:BUY_TOP20_BPP.map(function(b){{return b.bpp;}}),backgroundColor:'#ef4444',borderRadius:3,barThickness:10}}]}},
      options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return'US$ '+Math.round(c.raw).toLocaleString();}}}}}}}},
        scales:{{x:{{ticks:{{color:'#6b7280',font:{{size:9}},callback:function(v){{return'US$ '+Math.round(v).toLocaleString();}}}},grid:{{color:'#111827'}}}},y:{{ticks:{{color:'#9ca3af',font:{{size:9}}}},grid:{{display:false}}}}}}
      }}
    }});
  }}
  if(eF&&BUY_TOP20_FR.length){{
    if(buyChtFraud)buyChtFraud.destroy();
    buyChtFraud=new Chart(eF,{{
      type:'bar',
      data:{{labels:BUY_TOP20_FR.map(function(b){{return b.buyer.length>18?b.buyer.slice(0,17)+'…':b.buyer;}}),
             datasets:[{{data:BUY_TOP20_FR.map(function(b){{return b.fraud;}}),backgroundColor:'#fbbf24',borderRadius:3,barThickness:10}}]}},
      options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return c.raw+' SHPs fraude';}}}}}}}},
        scales:{{x:{{ticks:{{color:'#6b7280',font:{{size:9}}}},grid:{{color:'#111827'}}}},y:{{ticks:{{color:'#9ca3af',font:{{size:9}}}},grid:{{display:false}}}}}}
      }}
    }});
  }}
}}

function filtrarBuyers(){{
  var q=((document.getElementById('buy-busca')||{{}}).value||'').toLowerCase().trim();
  var fl=(document.getElementById('buy-filtro')||{{}}).value||'';
  var dados=BUYERS_DATA.filter(function(b){{
    var okQ=!q||b.buyer.toLowerCase().indexOf(q)>=0;
    var okF=!fl||(fl==='fraud'&&b.fraud>0)||(fl==='pnr'&&b.pnr>0)||(fl==='multi'&&b.total>=3);
    return okQ&&okF;
  }});
  var tb=document.getElementById('buy-tbody');
  if(!tb)return;
  var ct=document.getElementById('buy-count');if(ct)ct.textContent=dados.length;
  tb.innerHTML=dados.slice(0,500).map(function(b,i){{
    var bppColor=b.bpp>5000?'#f87171':b.bpp>500?'#fbbf24':'#9ca3af';
    var fraudBg=b.fraud>0?'background:rgba(239,68,68,.1);color:#f87171':'color:#4b5563';
    var pnrBg=b.pnr>0?'background:rgba(251,191,36,.1);color:#fbbf24':'color:#4b5563';
    return '<tr style="border-bottom:1px solid #111827">'
      +'<td style="padding:7px 10px;color:#4b5563;font-size:10px">'+(i+1)+'</td>'
      +'<td style="padding:7px 10px;color:#a78bfa;font-weight:600">'+b.buyer+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#e5e7eb">'+b.total.toLocaleString()+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#9ca3af">'+b.sellers+'</td>'
      +'<td style="padding:7px 10px;text-align:right;color:'+bppColor+';font-weight:700">US$ '+b.bpp.toLocaleString(\'pt-BR\',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</td>'
      +'<td style="padding:7px 10px;text-align:center;'+fraudBg+';border-radius:4px">'+b.fraud+'</td>'
      +'<td style="padding:7px 10px;text-align:center;'+pnrBg+';border-radius:4px">'+b.pnr+'</td>'
      +'<td style="padding:7px 10px;color:#9ca3af;font-size:10px">'+b.classe+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#6b7280;font-size:10px">'+b.qtd_meses+'</td>'
      +'</tr>';
  }}).join('');
}}

window.exportCSVBuyers=function(){{
  var rows=[['Buyer','SHPs','Sellers','BPP USD','Fraude','PNR','Classe Principal','Primeiro Mês','Último Mês','Qtd Meses']];
  BUYERS_DATA.forEach(function(b){{rows.push([b.buyer,b.total,b.sellers,b.bpp,b.fraud,b.pnr,b.classe,b.primeiro_mes,b.ultimo_mes,b.qtd_meses]);}});
  var csv=rows.map(function(r){{return r.map(function(v){{return'"'+String(v).replace(/"/g,'""')+'"';}}).join(',');}}).join('\\n');
  var a=document.createElement('a');a.href='data:text/csv;charset=utf-8,\\uFEFF'+encodeURIComponent(csv);
  a.download='buyers_ssp30.csv';a.click();
}};

window.limparFiltrosBuyers=function(){{
  var b=document.getElementById('buy-busca');if(b)b.value='';
  var f=document.getElementById('buy-filtro');if(f)f.value='';
  filtrarBuyers();
}};

window.filtrarBuyers=filtrarBuyers;
window.buildBuyCharts=buildBuyCharts;

document.addEventListener('DOMContentLoaded',function(){{filtrarBuyers();}});
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
                # Skip optional newline after closing div
                if pos < len(content) and content[pos] == '\n':
                    end = pos + 1
                return content[:idx] + new_html + '\n' + content[end:], True

    return content, False


def injetar_no_fraude(tab_html, tab_id='tab-buyers'):
    print(f'Lendo {FRAUDE_HTML}...')
    content = FRAUDE_HTML.read_text(encoding='utf-8')
    content, ok = find_and_replace_tab(content, tab_id, tab_html)
    if ok:
        FRAUDE_HTML.write_text(content, encoding='utf-8')
        print(f'  Salvo: {FRAUDE_HTML}')
    return ok


def main():
    buyers = carregar_dados()
    tab_html = gerar_tab_html(buyers)
    if injetar_no_fraude(tab_html, 'tab-buyers'):
        print(f'\n✓ Tab Buyers injetada com {len(buyers)} buyers')
    else:
        print('\n✗ Falha na injeção')


if __name__ == '__main__':
    main()
