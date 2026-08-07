"""
lor_fraude.py — Análise LOR+Fraude por Driver para o Dashboard SSP30.
Consulta DM_LP_MELI_OPTIMIZADO e injeta tab-lor-fraud em fraude.html.
Uso: python lor_fraude.py
"""

import json
from datetime import datetime
from pathlib import Path

from google.cloud import bigquery
from google.auth import default

FACILITY    = 'Guarulhos Mega'
INICIO      = '2026-01-01'
FRAUDE_HTML = Path(__file__).parent / 'fraude.html'

# Classificações de LOR + Fraude
LOR_CLASSES = [
    'LOST ON ROUTE', 'LOST ON WAY', 'LOST AT STATION', 'LOST ENE',
    'FRAUD ON ROUTE', 'FRAUD AT STATION', 'FRAUD ENE',
    'STOLEN ON ROUTE', 'PNR C', 'EMPTY BOX',
]

QUERY = """
SELECT
  SAFE_CAST(DRIVER_ID AS STRING)                                          AS driver_id,
  IFNULL(MAX(DRIVER_NAME), '')                                            AS driver_name,
  IFNULL(MAX(MLP), '')                                                    AS mlp,
  COUNT(DISTINCT SHIPMENT_ID)                                             AS total_shps,
  ROUND(SUM(BPP_CASHOUT_USD), 2)                                          AS bpp,
  APPROX_TOP_COUNT(Classification_LM, 1)[OFFSET(0)].value                AS classe_principal,
  COUNTIF(Classification_LM LIKE 'FRAUD%%'
       OR Classification_LM = 'STOLEN ON ROUTE')                         AS fraud_shps,
  COUNTIF(Classification_LM LIKE 'LOST%%')                               AS lor_shps,
  COUNTIF(Classification_LM = 'PNR C')                                   AS pnr_shps,
  COUNTIF(Classification_LM = 'EMPTY BOX')                               AS empty_shps,
  MIN(FORMAT_DATE('%%Y-%%m', DATE_BPP))                                   AS primeiro_mes,
  MAX(FORMAT_DATE('%%Y-%%m', DATE_BPP))                                   AS ultimo_mes,
  COUNT(DISTINCT FORMAT_DATE('%%Y-%%m', DATE_BPP))                        AS qtd_meses,
  ARRAY_AGG(CAST(SHIPMENT_ID AS STRING) ORDER BY BPP_CASHOUT_USD DESC LIMIT 20) AS shp_sample
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = '{facility}'
  AND DATE_BPP >= '{inicio}'
  AND DATE_BPP <= CURRENT_DATE()
  AND DRIVER_ID IS NOT NULL
  AND Classification_LM IN (
    'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
    'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE',
    'STOLEN ON ROUTE','PNR C','EMPTY BOX'
  )
GROUP BY 1
ORDER BY bpp DESC
""".format(facility=FACILITY, inicio=INICIO)


def conectar():
    creds, project = default()
    return bigquery.Client(credentials=creds, project='meli-bi-data')


def carregar_dados():
    print('Conectando ao BigQuery...')
    client = conectar()
    print('Consultando drivers LOR+Fraude...')
    rows = list(client.query(QUERY).result())
    print(f'  {len(rows)} drivers retornados')

    drivers = []
    for row in rows:
        shps = list(dict.fromkeys(str(s) for s in (row['shp_sample'] or []) if s))[:20]
        drivers.append({
            'id':           row['driver_id'] or '',
            'nome':         row['driver_name'] or '',
            'mlp':          row['mlp'] or '',
            'total':        int(row['total_shps']),
            'bpp':          float(row['bpp'] or 0),
            'classe':       row['classe_principal'] or '',
            'fraud':        int(row['fraud_shps']),
            'lor':          int(row['lor_shps']),
            'pnr':          int(row['pnr_shps']),
            'empty':        int(row['empty_shps']),
            'primeiro_mes': row['primeiro_mes'] or '',
            'ultimo_mes':   row['ultimo_mes'] or '',
            'meses':        int(row['qtd_meses']),
            'shps':         shps,
        })
    return drivers


def gerar_tab_html(drivers):
    total_drivers = len(drivers)
    total_shps    = sum(d['total'] for d in drivers)
    total_bpp     = round(sum(d['bpp'] for d in drivers), 2)
    total_fraud   = sum(d['fraud'] for d in drivers)
    total_lor     = sum(d['lor'] for d in drivers)
    pct_fraud     = round(total_fraud / total_shps * 100, 1) if total_shps else 0

    top20_bpp   = sorted(drivers, key=lambda x: x['bpp'], reverse=True)[:20]
    top20_shps  = sorted(drivers, key=lambda x: x['total'], reverse=True)[:20]

    drivers_json      = json.dumps(drivers, ensure_ascii=False)
    top20_bpp_json    = json.dumps(
        [{'id': d['id'], 'mlp': d['mlp'], 'bpp': d['bpp']} for d in top20_bpp],
        ensure_ascii=False)
    top20_shps_json   = json.dumps(
        [{'id': d['id'], 'mlp': d['mlp'], 'total': d['total']} for d in top20_shps],
        ensure_ascii=False)

    now = datetime.now().strftime('%d/%m/%Y %H:%M')

    html = f"""<div id="tab-lor-fraud" class="content">
<div style="padding:20px 32px">

  <!-- Header -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:16px;font-weight:700;color:#f9fafb">LOR + Fraude — por Driver</div>
      <div style="font-size:11px;color:#6b7280;margin-top:2px">SSP30 — Guarulhos Mega | desde Jan/2026 | atualizado {now}</div>
    </div>
    <div style="display:flex;gap:8px">
      <button onclick="exportCSVLF()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:5px 12px;font-size:11px;cursor:pointer">⬇ CSV</button>
    </div>
  </div>

  <!-- KPIs -->
  <div style="display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap">
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#38bdf8">{total_drivers:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">Drivers</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#e5e7eb">{total_shps:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">SHPs LOR+Fraude</div>
    </div>
    <div style="background:#1a0505;border:1px solid #7f1d1d;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#f87171">US$ {total_bpp:,.0f}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">BPP Total</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#fbbf24">{total_fraud:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">Fraude Confirmada</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#a78bfa">{total_lor:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">LOR</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#34d399">{pct_fraud}%</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">% Fraude</div>
    </div>
  </div>

  <!-- Charts -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px">
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:10px">Top 20 Drivers — BPP (USD)</div>
      <div style="position:relative;height:280px"><canvas id="lfChtBpp"></canvas></div>
    </div>
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:10px">Top 20 Drivers — Qtd SHPs</div>
      <div style="position:relative;height:280px"><canvas id="lfChtShps"></canvas></div>
    </div>
  </div>

  <!-- Filtros -->
  <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center">
    <input id="lf-busca" type="text" placeholder="Buscar driver ID ou MLP..." oninput="filtrarLF()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px 12px;font-size:12px;flex:1;min-width:180px">
    <select id="lf-filtro" onchange="filtrarLF()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px 10px;font-size:12px">
      <option value="">Todos</option>
      <option value="fraud">Fraude confirmada</option>
      <option value="lor">LOR</option>
      <option value="pnr">PNR C</option>
      <option value="empty">Empty Box</option>
      <option value="multi">5+ SHPs</option>
    </select>
    <button onclick="limparFiltrosLF()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:6px 12px;font-size:11px;cursor:pointer">Limpar</button>
    <span style="font-size:11px;color:#6b7280">Exibindo <b id="lf-count" style="color:#e5e7eb">-</b> drivers</span>
  </div>

  <!-- Tabela -->
  <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead>
        <tr style="border-bottom:1px solid #374151">
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">#</th>
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Driver ID</th>
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">MLP</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">SHPs</th>
          <th style="text-align:right;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">BPP (USD)</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Fraude</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">LOR</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">PNR</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Empty</th>
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Classe Principal</th>
          <th style="text-align:center;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Meses</th>
          <th style="text-align:left;padding:8px 10px;color:#6b7280;font-weight:600;font-size:10px;text-transform:uppercase">Exemplos SHPs</th>
        </tr>
      </thead>
      <tbody id="lf-tbody"></tbody>
    </table>
  </div>

</div>

<script>
(function(){{
var LF_DATA={drivers_json};
var LF_TOP20_BPP={top20_bpp_json};
var LF_TOP20_SHPS={top20_shps_json};
var lfChtBpp=null,lfChtShps=null;

function buildLFCharts(){{
  var eB=document.getElementById('lfChtBpp');
  var eS=document.getElementById('lfChtShps');
  var labelFn=function(d){{
    var l=d.mlp||d.id||'';
    return l.length>18?l.slice(0,17)+'…':l;
  }};
  if(eB){{
    if(lfChtBpp)lfChtBpp.destroy();
    lfChtBpp=new Chart(eB,{{
      type:'bar',
      data:{{
        labels:LF_TOP20_BPP.map(labelFn),
        datasets:[{{data:LF_TOP20_BPP.map(function(d){{return d.bpp;}}),backgroundColor:'#ef4444',borderRadius:3,barThickness:10}}]
      }},
      options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return'US$ '+Math.round(c.raw).toLocaleString();}}}}}}}},
        scales:{{
          x:{{ticks:{{color:'#6b7280',font:{{size:9}},callback:function(v){{return'US$ '+Math.round(v).toLocaleString();}}}},grid:{{color:'#111827'}}}},
          y:{{ticks:{{color:'#9ca3af',font:{{size:9}}}},grid:{{display:false}}}}
        }}
      }}
    }});
  }}
  if(eS){{
    if(lfChtShps)lfChtShps.destroy();
    lfChtShps=new Chart(eS,{{
      type:'bar',
      data:{{
        labels:LF_TOP20_SHPS.map(labelFn),
        datasets:[{{data:LF_TOP20_SHPS.map(function(d){{return d.total;}}),backgroundColor:'#6366f1',borderRadius:3,barThickness:10}}]
      }},
      options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return c.raw+' SHPs';}}}}}}}},
        scales:{{
          x:{{ticks:{{color:'#6b7280',font:{{size:9}}}},grid:{{color:'#111827'}}}},
          y:{{ticks:{{color:'#9ca3af',font:{{size:9}}}},grid:{{display:false}}}}
        }}
      }}
    }});
  }}
}}

function filtrarLF(){{
  var q=((document.getElementById('lf-busca')||{{}}).value||'').toLowerCase().trim();
  var fl=(document.getElementById('lf-filtro')||{{}}).value||'';
  var dados=LF_DATA.filter(function(d){{
    var okQ=!q||d.id.toLowerCase().indexOf(q)>=0||d.mlp.toLowerCase().indexOf(q)>=0||d.nome.toLowerCase().indexOf(q)>=0;
    var okF=!fl
      ||(fl==='fraud'&&d.fraud>0)
      ||(fl==='lor'&&d.lor>0)
      ||(fl==='pnr'&&d.pnr>0)
      ||(fl==='empty'&&d.empty>0)
      ||(fl==='multi'&&d.total>=5);
    return okQ&&okF;
  }});
  var tb=document.getElementById('lf-tbody');
  if(!tb)return;
  var ct=document.getElementById('lf-count');if(ct)ct.textContent=dados.length;
  tb.innerHTML=dados.slice(0,500).map(function(d,i){{
    var bppClr=d.bpp>5000?'#f87171':d.bpp>500?'#fbbf24':'#9ca3af';
    var fraudBg=d.fraud>0?'background:rgba(239,68,68,.12);color:#f87171':'color:#4b5563';
    var lorBg=d.lor>0?'background:rgba(163,163,163,.1);color:#a3a3a3':'color:#4b5563';
    var pnrBg=d.pnr>0?'background:rgba(251,191,36,.1);color:#fbbf24':'color:#4b5563';
    var emptyBg=d.empty>0?'background:rgba(167,139,250,.1);color:#a78bfa':'color:#4b5563';
    var shpLinks=d.shps.slice(0,5).join(', ');
    return '<tr style="border-bottom:1px solid #111827">'
      +'<td style="padding:7px 10px;color:#4b5563;font-size:10px">'+(i+1)+'</td>'
      +'<td style="padding:7px 10px;color:#38bdf8;font-weight:600;font-family:monospace">'+d.id+'</td>'
      +'<td style="padding:7px 10px;color:#e5e7eb">'+d.mlp+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#e5e7eb">'+d.total.toLocaleString()+'</td>'
      +'<td style="padding:7px 10px;text-align:right;color:'+bppClr+';font-weight:700">US$ '+d.bpp.toLocaleString(\'pt-BR\',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</td>'
      +'<td style="padding:7px 10px;text-align:center;'+fraudBg+';border-radius:4px">'+d.fraud+'</td>'
      +'<td style="padding:7px 10px;text-align:center;'+lorBg+';border-radius:4px">'+d.lor+'</td>'
      +'<td style="padding:7px 10px;text-align:center;'+pnrBg+';border-radius:4px">'+d.pnr+'</td>'
      +'<td style="padding:7px 10px;text-align:center;'+emptyBg+';border-radius:4px">'+d.empty+'</td>'
      +'<td style="padding:7px 10px;color:#9ca3af;font-size:10px">'+d.classe+'</td>'
      +'<td style="padding:7px 10px;text-align:center;color:#6b7280;font-size:10px">'+d.meses+'</td>'
      +'<td style="padding:7px 10px;color:#6b7280;font-size:9px;font-family:monospace">'+shpLinks+'</td>'
      +'</tr>';
  }}).join('');
}}

window.exportCSVLF=function(){{
  var rows=[['Driver ID','MLP','Nome','SHPs','BPP USD','Fraude','LOR','PNR','Empty Box','Classe Principal','Primeiro Mês','Último Mês','Qtd Meses']];
  LF_DATA.forEach(function(d){{
    rows.push([d.id,d.mlp,d.nome,d.total,d.bpp,d.fraud,d.lor,d.pnr,d.empty,d.classe,d.primeiro_mes,d.ultimo_mes,d.meses]);
  }});
  var csv=rows.map(function(r){{return r.map(function(v){{return'"'+String(v).replace(/"/g,'""')+'"';}}).join(',');}}).join('\\n');
  var a=document.createElement('a');
  a.href='data:text/csv;charset=utf-8,\\uFEFF'+encodeURIComponent(csv);
  a.download='lor_fraude_drivers_ssp30.csv';
  a.click();
}};

window.limparFiltrosLF=function(){{
  var b=document.getElementById('lf-busca');if(b)b.value='';
  var f=document.getElementById('lf-filtro');if(f)f.value='';
  filtrarLF();
}};

window.filtrarLF=filtrarLF;
window.buildLFCharts=buildLFCharts;
window.lfRender=buildLFCharts;

document.addEventListener('DOMContentLoaded',function(){{
  filtrarLF();
  var badge=document.getElementById('tab-count-lor-fraud');
  if(badge)badge.textContent=LF_DATA.length;
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
    pos   = idx
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


def patch_badges(content, drivers):
    """Garante que o badge de sellers/buyers/nodos também atualiza via DOMContentLoaded."""
    return content


def main():
    drivers = carregar_dados()

    print(f'Gerando HTML do tab ({len(drivers)} drivers)...')
    tab_html = gerar_tab_html(drivers)

    print('Lendo fraude.html...')
    html = FRAUDE_HTML.read_text(encoding='utf-8')

    print('Injetando tab-lor-fraud...')
    html, ok = find_and_replace_tab(html, 'tab-lor-fraud', tab_html)
    if not ok:
        print('ERRO: tab-lor-fraud não encontrado no HTML!')
        return

    # Atualizar badge no sidebar via JS inline (backup se DOMContentLoaded não disparar)
    # O badge principal é atualizado dentro do IIFE no DOMContentLoaded

    print('Salvando fraude.html...')
    FRAUDE_HTML.write_text(html, encoding='utf-8')

    size_mb = FRAUDE_HTML.stat().st_size / 1024 / 1024
    print(f'Pronto! {size_mb:.1f} MB — {len(drivers)} drivers LOR+Fraude')


if __name__ == '__main__':
    main()
