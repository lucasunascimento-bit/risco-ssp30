"""
bloqueio.py v2 — Aba Bloqueios do Dashboard SSP30
Critério: BPP >= $300 E fraud SHPs >= 6 (desde jan/2026)
Injeta tab-bloqueios em fraude.html
"""

import json, re
from datetime import datetime
from pathlib import Path
from google.cloud import bigquery
from google.auth import default

MIN_BPP   = 300
MIN_FRAUD = 6
FACILITY  = 'Guarulhos Mega'
INICIO    = '2026-01-01'
HTML_OUT  = Path(__file__).parent / 'fraude.html'
LOG_URL   = 'https://logistics.mercadolibre.com.br/shipments/'
BO_DRIVER = 'https://shipping-bo.adminml.com/sauron/drivers/driver/'

_FC = (
    "Classification_LM LIKE 'FRAUD%' "
    "OR Classification_LM = 'STOLEN ON ROUTE' "
    "OR Classification_LM = 'PNR C' "
    "OR Classification_LM = 'EMPTY BOX'"
)

QUERY = f"""
SELECT
    SAFE_CAST(DRIVER_ID AS STRING)                              AS id,
    IFNULL(MAX(DRIVER_NAME), '')                               AS nome,
    IFNULL(MAX(MLP), '')                                       AS mlp,
    COUNT(DISTINCT SHIPMENT_ID)                                AS total,
    COUNT(DISTINCT CASE WHEN {_FC} THEN SHIPMENT_ID END)      AS fraud,
    ROUND(SUM(BPP_CASHOUT_USD), 2)                             AS bpp,
    APPROX_TOP_COUNT(Classification_LM, 1)[OFFSET(0)].value   AS classe,
    ARRAY_AGG(DISTINCT FORMAT_DATE('%Y-%m', date_bpp))         AS meses,
    ARRAY_AGG(CAST(SHIPMENT_ID AS STRING)
        ORDER BY BPP_CASHOUT_USD DESC LIMIT 30)                AS shp_ids_sample
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = '{FACILITY}'
  AND date_bpp >= '{INICIO}'
  AND date_bpp <= CURRENT_DATE()
  AND DRIVER_ID IS NOT NULL
GROUP BY 1
HAVING ROUND(SUM(BPP_CASHOUT_USD), 2) >= {MIN_BPP}
   AND COUNT(DISTINCT CASE WHEN {_FC} THEN SHIPMENT_ID END) >= {MIN_FRAUD}
ORDER BY bpp DESC
"""


def carregar_dados():
    creds, _ = default()
    client = bigquery.Client(credentials=creds, project='meli-bi-data')
    print(f'Consultando candidatos (BPP >= ${MIN_BPP}, fraud >= {MIN_FRAUD})...')
    rows = list(client.query(QUERY).result())
    print(f'  {len(rows)} candidatos encontrados')
    drivers = []
    for row in rows:
        total = int(row['total'])
        fraud = int(row['fraud'])
        pct   = round(fraud / total * 100, 1) if total else 0.0
        meses = sorted(m for m in (row['meses'] or []) if m)
        shps  = list(dict.fromkeys(str(s) for s in (row['shp_ids_sample'] or []) if s))[:30]
        drivers.append({
            'id':    row['id'],
            'nome':  row['nome'] or '',
            'mlp':   row['mlp'] or '',
            'total': total,
            'fraud': fraud,
            'pct':   pct,
            'bpp':   float(row['bpp'] or 0),
            'classe': row['classe'] or '',
            'meses': meses,
            'shps':  shps,
        })
    return drivers


def gerar_tab(drivers):
    if not drivers:
        return '<div id="tab-bloqueios" class="content"><div style="padding:40px;text-align:center;color:#6b7280">Nenhum candidato encontrado.</div></div>'

    now         = datetime.now().strftime('%d/%m/%Y %H:%M')
    n_drivers   = len(drivers)
    total_bpp   = sum(d['bpp'] for d in drivers)
    total_fraud = sum(d['fraud'] for d in drivers)
    avg_pct     = round(sum(d['pct'] for d in drivers) / n_drivers, 1)

    mlp_count = {}
    for d in drivers:
        k = d['mlp'] or 'Sem transportadora'
        mlp_count[k] = mlp_count.get(k, 0) + 1
    mlp_sorted   = sorted(mlp_count.items(), key=lambda x: -x[1])
    top_mlp      = mlp_sorted[0][0] if mlp_sorted else '—'
    top_mlp_n    = mlp_sorted[0][1] if mlp_sorted else 0
    top_mlp_short = (top_mlp[:18] + '…') if len(top_mlp) > 18 else top_mlp

    mlp_items  = mlp_sorted[:8]
    mlp_labels = json.dumps([x[0][:22] for x in mlp_items]).replace('</', '<\\/')
    mlp_vals   = json.dumps([x[1] for x in mlp_items])

    top10      = drivers[:10]
    top_labels = json.dumps([d['id'] for d in top10])
    top_vals   = json.dumps([round(d['bpp'], 2) for d in top10])

    all_classes = sorted({d['classe'] for d in drivers if d['classe']})
    all_meses   = sorted({m for d in drivers for m in d['meses']})
    all_mlps    = sorted({d['mlp'] for d in drivers if d['mlp']})

    def mes_label(ym):
        try:
            return datetime.strptime(ym, '%Y-%m').strftime('%b/%y').capitalize()
        except Exception:
            return ym

    mes_opts = ''.join(
        f'<option value="{m}">{mes_label(m)}</option>' for m in all_meses
    )
    mlp_cbs = ''.join(
        f'<label class="blq-ms-item"><input type="checkbox" class="blq-ms-cb blq-ms-cb-mlp" value="{m}" onchange="blqMsChg(\'mlp\')"> {m}</label>'
        for m in all_mlps
    )
    cls_cbs = ''.join(
        f'<label class="blq-ms-item"><input type="checkbox" class="blq-ms-cb blq-ms-cb-cls" value="{c}" onchange="blqMsChg(\'cls\')"> {c}</label>'
        for c in all_classes
    )

    data_json = json.dumps(drivers, ensure_ascii=False).replace('</', '<\\/')

    return f"""<div id="tab-bloqueios" class="content">
<style>
#tab-bloqueios .blq-crit{{background:#0d1017;border:1px solid rgba(239,68,68,.2);border-radius:6px;padding:8px 16px;font-size:11px;color:#6b7280;display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
#tab-bloqueios .blq-crit strong{{color:#f87171}}
#tab-bloqueios .blq-kpis{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}}
#tab-bloqueios .blq-s-ativo{{background:rgba(74,222,128,.08);color:#4ade80;border:1px solid rgba(74,222,128,.2)}}
#tab-bloqueios .blq-kpi{{background:#0d1321;border:1px solid #111827;border-radius:8px;padding:14px 16px}}
#tab-bloqueios .blq-kpi-l{{font-size:9px;text-transform:uppercase;letter-spacing:.7px;color:#4b5563;font-weight:700;margin-bottom:6px}}
#tab-bloqueios .blq-kpi-v{{font-size:22px;font-weight:800;color:#fff;line-height:1;letter-spacing:-1px}}
#tab-bloqueios .blq-kpi-s{{font-size:10px;color:#6b7280;margin-top:4px}}
#tab-bloqueios .blq-kpi.red .blq-kpi-v{{color:#f87171}}
#tab-bloqueios .blq-kpi.amb .blq-kpi-v{{color:#fbbf24}}
#tab-bloqueios .blq-kpi.blu .blq-kpi-v{{color:#60a5fa}}
#tab-bloqueios .blq-controls{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:10px 0;border-bottom:1px solid #111827}}
#tab-bloqueios .blq-controls select,#tab-bloqueios .blq-controls input{{background:#0d1321;border:1px solid #1f2937;color:#e2e8f0;font-size:12px;padding:4px 8px;border-radius:6px;height:30px}}
#tab-bloqueios .blq-btn-r{{background:transparent;border:1px solid #1f2937;color:#4b5563;font-size:11px;padding:4px 12px;border-radius:6px;cursor:pointer;height:30px}}
#tab-bloqueios .blq-btn-r:hover{{border-color:#374151;color:#9ca3af}}
#tab-bloqueios .blq-ms-wrap{{position:relative;display:inline-block}}
#tab-bloqueios .blq-ms-btn{{background:#0d1321;border:1px solid #1f2937;color:#e2e8f0;font-size:12px;padding:0 10px;border-radius:6px;height:30px;cursor:pointer;min-width:110px;text-align:left}}
#tab-bloqueios .blq-ms-btn.active{{border-color:#ef4444;color:#f87171}}
#tab-bloqueios .blq-ms-panel{{position:absolute;top:34px;left:0;z-index:200;background:#0d1321;border:1px solid #1f2937;border-radius:8px;padding:8px 0 6px;min-width:200px;max-height:280px;overflow-y:auto;box-shadow:0 8px 24px rgba(0,0,0,.7)}}
#tab-bloqueios .blq-ms-actions{{display:flex;gap:5px;padding:4px 10px 8px;border-bottom:1px solid #111827;margin-bottom:4px}}
#tab-bloqueios .blq-ms-actions button{{font-size:10px;color:#4b5563;background:transparent;border:1px solid #1f2937;border-radius:4px;padding:2px 8px;cursor:pointer}}
#tab-bloqueios .blq-ms-item{{display:flex;align-items:center;gap:8px;padding:4px 10px;font-size:12px;color:#e2e8f0;cursor:pointer}}
#tab-bloqueios .blq-ms-item:hover{{background:#111827}}
#tab-bloqueios .blq-ms-item input{{accent-color:#ef4444;cursor:pointer;width:13px;height:13px}}
#tab-bloqueios .blq-tbl-scr{{border-radius:8px;border:1px solid #111827;overflow-x:auto;margin-top:10px}}
#tab-bloqueios table{{width:100%;border-collapse:collapse;font-size:12px}}
#tab-bloqueios thead th{{background:#0b101e;padding:9px 12px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.7px;color:#374151;font-weight:700;border-bottom:1px solid #111827;white-space:nowrap;cursor:pointer;user-select:none}}
#tab-bloqueios thead th:hover{{color:#9ca3af}}
#tab-bloqueios thead th.blq-sorted{{color:#9ca3af}}
#tab-bloqueios thead th.blq-no-sort{{cursor:default}}
#tab-bloqueios thead th.blq-no-sort:hover{{color:#374151}}
#tab-bloqueios tbody tr.blq-dr-row{{border-bottom:1px solid #0b101e;transition:background .1s}}
#tab-bloqueios tbody tr.blq-dr-row:hover{{background:#0d1321}}
#tab-bloqueios tbody td{{padding:8px 12px;color:#e2e8f0;white-space:nowrap}}
#tab-bloqueios .blq-did-btn{{background:none;border:none;color:#f9fafb;font-weight:700;cursor:pointer;font-size:12px;padding:0;font-family:inherit;display:flex;align-items:center;gap:5px}}
#tab-bloqueios .blq-did-btn:hover{{color:#60a5fa}}
#tab-bloqueios .blq-chv{{font-size:9px;color:#374151;transition:transform .15s;display:inline-block}}
#tab-bloqueios .blq-chv.open{{transform:rotate(180deg);color:#60a5fa}}
#tab-bloqueios .blq-shp-row td{{background:#06090f;padding:10px 16px 14px 52px;border-bottom:1px solid #1f2937}}
#tab-bloqueios .blq-shp-meta{{display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap}}
#tab-bloqueios .blq-drv-link{{font-size:10px;color:#9ca3af;background:#111827;border:1px solid #1f2937;border-radius:4px;padding:3px 10px;text-decoration:none;white-space:nowrap}}
#tab-bloqueios .blq-drv-link:hover{{color:#e2e8f0;border-color:#374151}}
#tab-bloqueios .blq-shp-list{{display:flex;flex-wrap:wrap;gap:6px}}
#tab-bloqueios .blq-chip{{font-size:11px;color:#60a5fa;background:rgba(96,165,250,.08);border:1px solid rgba(96,165,250,.2);border-radius:4px;padding:3px 8px;text-decoration:none;font-variant-numeric:tabular-nums;white-space:nowrap}}
#tab-bloqueios .blq-chip:hover{{background:rgba(96,165,250,.18);border-color:rgba(96,165,250,.4)}}
#tab-bloqueios .blq-tag{{font-size:10px;color:#9ca3af;background:#111827;padding:2px 7px;border-radius:4px;max-width:120px;overflow:hidden;text-overflow:ellipsis;display:inline-block}}
#tab-bloqueios .blq-badge{{display:inline-block;font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px;cursor:pointer;transition:opacity .1s}}
#tab-bloqueios .blq-badge:hover{{opacity:.8}}
#tab-bloqueios .blq-s-mon{{background:rgba(74,222,128,.08);color:#4ade80;border:1px solid rgba(74,222,128,.2)}}
#tab-bloqueios .blq-s-inv{{background:rgba(251,191,36,.08);color:#fbbf24;border:1px solid rgba(251,191,36,.2)}}
#tab-bloqueios .blq-s-blq{{background:rgba(239,68,68,.08);color:#f87171;border:1px solid rgba(239,68,68,.2)}}
#tab-bloqueios .blq-mlp{{font-size:11px;color:#9ca3af;max-width:130px;overflow:hidden;text-overflow:ellipsis}}
</style>
<div style="padding:20px 32px">

  <!-- HEADER -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:16px;font-weight:700;color:#f9fafb">Candidatos a Bloqueio</div>
      <div style="font-size:11px;color:#6b7280;margin-top:2px">SSP30 · Guarulhos Mega · desde Jan/2026 · atualizado {now}</div>
    </div>
    <button onclick="blqExportCSV()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:5px 14px;font-size:11px;cursor:pointer">⬇ Exportar CSV</button>
  </div>

  <!-- CRITERIO -->
  <div class="blq-crit" style="margin-bottom:14px">
    <strong>Premissa de bloqueio:</strong>
    <span>BPP acumulado &gt; <strong>US$ {MIN_BPP}</strong></span>
    <span style="color:#1f2937">·</span>
    <span>Fraud SHPs &ge; <strong>{MIN_FRAUD} pacotes</strong></span>
    <span style="color:#1f2937">·</span>
    <span>Status salvo por browser (persiste entre sessões)</span>
  </div>

  <!-- KPIs -->
  <div class="blq-kpis" style="margin-bottom:14px">
    <div class="blq-kpi red">
      <div class="blq-kpi-l">Candidatos</div>
      <div class="blq-kpi-v" id="blq-k-total">{n_drivers}</div>
      <div class="blq-kpi-s">drivers no critério</div>
    </div>
    <div class="blq-kpi red">
      <div class="blq-kpi-l">BPP Acumulado</div>
      <div class="blq-kpi-v" id="blq-k-bpp">US$ {total_bpp:,.0f}</div>
      <div class="blq-kpi-s">total exposto</div>
    </div>
    <div class="blq-kpi">
      <div class="blq-kpi-l">Fraud SHPs</div>
      <div class="blq-kpi-v" id="blq-k-fraud">{total_fraud:,}</div>
      <div class="blq-kpi-s">pacotes comprometidos</div>
    </div>
    <div class="blq-kpi amb">
      <div class="blq-kpi-l">Média % Fraude</div>
      <div class="blq-kpi-v">{avg_pct}%</div>
      <div class="blq-kpi-s">dos SHPs por driver</div>
    </div>
    <div class="blq-kpi blu">
      <div class="blq-kpi-l">Top Transportadora</div>
      <div class="blq-kpi-v" style="font-size:13px;letter-spacing:0;padding-top:4px">{top_mlp_short}</div>
      <div class="blq-kpi-s">{top_mlp_n} candidatos</div>
    </div>
    <div class="blq-kpi" style="border-color:rgba(239,68,68,.3)">
      <div class="blq-kpi-l" style="color:#ef4444">Bloqueados</div>
      <div class="blq-kpi-v" id="blq-k-blq" style="color:#f87171">0</div>
      <div class="blq-kpi-s">no período filtrado</div>
    </div>
  </div>

  <!-- GRAFICOS -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Candidatos por Transportadora</div>
      <div style="position:relative;height:200px"><canvas id="blqCMlp"></canvas></div>
    </div>
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 por BPP Acumulado</div>
      <div style="position:relative;height:200px"><canvas id="blqCTop"></canvas></div>
    </div>
  </div>

  <!-- CONTROLES -->
  <div class="blq-controls">
    <span style="font-size:11px;color:#6b7280">Período</span>
    <select id="blq-de" onchange="blqRender()">
      <option value="">De...</option>
      {mes_opts}
    </select>
    <select id="blq-ate" onchange="blqRender()">
      <option value="">Até...</option>
      {mes_opts}
    </select>
    <span style="color:#1f2937;font-size:18px">|</span>
    <div class="blq-ms-wrap" id="blq-msw-mlp">
      <button class="blq-ms-btn" id="blq-msb-mlp" onclick="blqToggleMs('mlp')">Todas transp. ▾</button>
      <div class="blq-ms-panel" id="blq-msp-mlp" style="display:none">
        <div class="blq-ms-actions">
          <button onclick="blqMsAll('mlp')">Todas</button>
          <button onclick="blqMsNone('mlp')">Nenhuma</button>
        </div>
        {mlp_cbs}
      </div>
    </div>
    <div class="blq-ms-wrap" id="blq-msw-cls">
      <button class="blq-ms-btn" id="blq-msb-cls" onclick="blqToggleMs('cls')">Toda classe ▾</button>
      <div class="blq-ms-panel" id="blq-msp-cls" style="display:none">
        <div class="blq-ms-actions">
          <button onclick="blqMsAll('cls')">Todas</button>
          <button onclick="blqMsNone('cls')">Nenhuma</button>
        </div>
        {cls_cbs}
      </div>
    </div>
    <span style="color:#1f2937;font-size:18px">|</span>
    <span style="font-size:11px;color:#6b7280">Status</span>
    <select id="blq-status" onchange="blqRender()">
      <option value="">Todos</option>
      <option value="ativo">Ativos</option>
      <option value="blq">Bloqueados</option>
    </select>
    <input type="number" id="blq-pct" value="0" min="0" max="100" oninput="blqRender()"
      style="width:52px;text-align:center" placeholder="% min">
    <input type="search" id="blq-busca" placeholder="Driver ID..." oninput="blqRender()"
      style="width:130px">
    <button class="blq-btn-r" onclick="blqResetF()">&#x2715; Limpar</button>
    <span style="font-size:11px;color:#4b5563;margin-left:4px" id="blq-tbl-ct"></span>
  </div>

  <!-- TABELA -->
  <div class="blq-tbl-scr">
    <table>
      <thead>
        <tr>
          <th class="blq-no-sort">#</th>
          <th onclick="blqSortBy('bpp')" id="blq-th-bpp">BPP (USD) ↕</th>
          <th class="blq-no-sort">Driver ID</th>
          <th class="blq-no-sort">Transportadora</th>
          <th onclick="blqSortBy('fraud')" id="blq-th-fraud">Fraud SHPs ↕</th>
          <th onclick="blqSortBy('pct')" id="blq-th-pct">% Fraude ↕</th>
          <th onclick="blqSortBy('total')" id="blq-th-total">Total SHPs ↕</th>
          <th class="blq-no-sort">Classificacao</th>
          <th class="blq-no-sort">Meses ativo</th>
          <th class="blq-no-sort">Ação</th>
          <th class="blq-no-sort">Status</th>
        </tr>
      </thead>
      <tbody id="blq-tbody"></tbody>
    </table>
  </div>

</div>
<script>
(function(){{
var BLQ_DATA = {data_json};
var BLQ_LOG  = '{LOG_URL}';
var BLQ_DRV  = '{BO_DRIVER}';
var _blqSort = 'bpp', _blqDir = -1;

var _blqCMlp = null, _blqCTop = null;

function blqBuildCharts(){{
  var eMlp = document.getElementById('blqCMlp');
  var eTop = document.getElementById('blqCTop');
  if(eMlp){{
    var pw = eMlp.parentElement ? eMlp.parentElement.clientWidth : 0;
    if(pw > 10){{ eMlp.setAttribute('width', pw); eMlp.setAttribute('height', 200); }}
    if(_blqCMlp){{ try{{_blqCMlp.destroy();}}catch(ee){{}} _blqCMlp=null; }}
    try{{
      _blqCMlp = new Chart(eMlp, {{
        type:'bar',
        data:{{labels:{mlp_labels}, datasets:[{{data:{mlp_vals},backgroundColor:'rgba(239,68,68,0.65)',borderRadius:3,barThickness:16}}]}},
        options:{{indexAxis:'y',responsive:false,maintainAspectRatio:false,
          plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return ' '+c.parsed.x+' drivers';}}}}}}}},
          scales:{{x:{{grid:{{color:'#111827'}},ticks:{{color:'#6b7280',font:{{size:9}}}}}},y:{{grid:{{display:false}},ticks:{{color:'#9ca3af',font:{{size:9}}}}}}}}
        }}
      }});
    }}catch(ee){{console.error('blqCMlp:',ee);}}
  }}
  if(eTop){{
    var pw2 = eTop.parentElement ? eTop.parentElement.clientWidth : 0;
    if(pw2 > 10){{ eTop.setAttribute('width', pw2); eTop.setAttribute('height', 200); }}
    if(_blqCTop){{ try{{_blqCTop.destroy();}}catch(ee){{}} _blqCTop=null; }}
    try{{
      _blqCTop = new Chart(eTop, {{
        type:'bar',
        data:{{labels:{top_labels}, datasets:[{{data:{top_vals},backgroundColor:'rgba(251,191,36,0.65)',borderRadius:3,barThickness:16}}]}},
        options:{{indexAxis:'y',responsive:false,maintainAspectRatio:false,
          plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return ' $'+c.parsed.x.toLocaleString('en-US',{{minimumFractionDigits:2}});}}}}}}}},
          scales:{{x:{{grid:{{color:'#111827'}},ticks:{{color:'#6b7280',font:{{size:9}},callback:function(v){{return '$'+v.toLocaleString();}}}}}},y:{{grid:{{display:false}},ticks:{{color:'#9ca3af',font:{{size:9}}}}}}}}
        }}
      }});
    }}catch(ee){{console.error('blqCTop:',ee);}}
  }}
}}

function blqGetSt(id) {{
  try {{ return localStorage.getItem('blq_vg_'+id) || 'mon'; }} catch(e) {{ return 'mon'; }}
}}
function blqNextSt(id) {{
  var cycle = ['mon','inv','blq'];
  var cur; try {{ cur = localStorage.getItem('blq_vg_'+id) || 'mon'; }} catch(e) {{ cur = 'mon'; }}
  var next = cycle[(cycle.indexOf(cur)+1)%3];
  try {{ localStorage.setItem('blq_vg_'+id, next); }} catch(e) {{}}
  blqRender();
}}
function blqSortBy(k) {{
  if(_blqSort===k) _blqDir*=-1; else {{_blqSort=k;_blqDir=-1;}}
  blqRender();
}}
function blqToggleMs(id) {{
  var p = document.getElementById('blq-msp-'+id);
  p.style.display = p.style.display==='none' ? 'block' : 'none';
}}
function blqMsChg(id) {{ blqUpdMsBtn(id); blqRender(); }}
function blqUpdMsBtn(id) {{
  var n = document.querySelectorAll('.blq-ms-cb-'+id+':checked').length;
  var btn = document.getElementById('blq-msb-'+id);
  var lbl = id==='mlp' ? 'transp.' : 'classe(s)';
  var def = id==='mlp' ? 'Todas transp. ▾' : 'Toda classe ▾';
  btn.textContent = n ? n+' '+lbl+' ▾' : def;
  btn.className = n ? 'blq-ms-btn active' : 'blq-ms-btn';
}}
function blqMsAll(id) {{ document.querySelectorAll('.blq-ms-cb-'+id).forEach(function(e){{e.checked=true;}}); blqUpdMsBtn(id); blqRender(); }}
function blqMsNone(id) {{ document.querySelectorAll('.blq-ms-cb-'+id).forEach(function(e){{e.checked=false;}}); blqUpdMsBtn(id); blqRender(); }}

function blqRender() {{
  var de     = (document.getElementById('blq-de')||{{}}).value||'';
  var ate    = (document.getElementById('blq-ate')||{{}}).value||'';
  var stF    = (document.getElementById('blq-status')||{{}}).value||'';
  var minPct = parseFloat((document.getElementById('blq-pct')||{{}}).value)||0;
  var busca  = ((document.getElementById('blq-busca')||{{}}).value||'').trim();
  var mlpSel = new Set([].slice.call(document.querySelectorAll('.blq-ms-cb-mlp:checked')).map(function(e){{return e.value;}}));
  var clsSel = new Set([].slice.call(document.querySelectorAll('.blq-ms-cb-cls:checked')).map(function(e){{return e.value;}}));

  var rows = BLQ_DATA.filter(function(d) {{
    if(de||ate){{ var ok=d.meses.some(function(m){{return(!de||m>=de)&&(!ate||m<=ate);}});if(!ok)return false; }}
    if(mlpSel.size && !mlpSel.has(d.mlp||'')) return false;
    if(clsSel.size && !clsSel.has(d.classe))  return false;
    if(stF==='ativo' && blqGetSt(d.id)==='blq') return false;
    if(stF==='blq'   && blqGetSt(d.id)!=='blq') return false;
    if(minPct>0 && d.pct<minPct)              return false;
    if(busca && d.id.indexOf(busca)<0)        return false;
    return true;
  }});

  rows.sort(function(a,b){{return _blqDir*(a[_blqSort]-b[_blqSort]);}});

  ['bpp','fraud','pct','total'].forEach(function(k){{
    var el=document.getElementById('blq-th-'+k);
    if(el) el.className = _blqSort===k ? 'blq-sorted' : '';
  }});

  // Atualiza KPIs dinamicamente
  var filtBpp   = rows.reduce(function(s,d){{return s+d.bpp;}},0);
  var filtFraud = rows.reduce(function(s,d){{return s+d.fraud;}},0);
  var kT=document.getElementById('blq-k-total');
  if(kT) kT.textContent = rows.length;
  var kB=document.getElementById('blq-k-bpp');
  if(kB) kB.textContent = 'US$ '+filtBpp.toLocaleString('pt-BR',{{minimumFractionDigits:0,maximumFractionDigits:0}});
  var kF=document.getElementById('blq-k-fraud');
  if(kF) kF.textContent = filtFraud.toLocaleString('pt-BR');

  var inv = rows.filter(function(d){{return blqGetSt(d.id)==='inv';}}).length;
  var blk = rows.filter(function(d){{return blqGetSt(d.id)==='blq';}}).length;
  var kBL = document.getElementById('blq-k-blq');
  if(kBL) kBL.textContent = blk;
  var ct  = document.getElementById('blq-tbl-ct');
  if(ct) ct.textContent = rows.length+' drivers · '+inv+' em investigacao · '+blk+' bloqueados';

  var tbody = document.getElementById('blq-tbody');
  if(!tbody) return;
  if(!rows.length){{
    tbody.innerHTML='<tr><td colspan="11" style="text-align:center;padding:40px;color:#374151">Nenhum driver encontrado.</td></tr>';
    return;
  }}

  var ST_LBL = {{mon:'Monitorado',inv:'Em investigacao',blq:'Bloqueado'}};
  var ST_CLS = {{mon:'blq-s-mon',inv:'blq-s-inv',blq:'blq-s-blq'}};

  tbody.innerHTML = rows.map(function(d,i){{
    var st      = blqGetSt(d.id);
    var pctCol  = d.pct>=50?'#f87171':d.pct>=20?'#fbbf24':'#6b7280';
    var pctW    = d.pct>=50?'700':'400';
    var mLbl    = d.meses.length
      ? d.meses.slice(-3).map(function(m){{
          try{{var dt=new Date(m+'-15');return dt.toLocaleDateString('pt-BR',{{month:'short',year:'2-digit'}}).replace('. ','/');}}catch(e){{return m;}}
        }}).join(' · ')+(d.meses.length>3?' +':'')
      : '—';

    var mainRow =
      '<tr class="blq-dr-row">'+
      '<td style="color:#374151;font-size:11px">'+(i+1)+'</td>'+
      '<td style="color:#f87171;font-weight:700">US$ '+d.bpp.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</td>'+
      '<td><button class="blq-did-btn" onclick="blqToggleShps(\\''+d.id+'\\')">'+d.id+
        '<span class="blq-chv" id="blq-chv-'+d.id+'">&#9660;</span></button></td>'+
      '<td class="blq-mlp" title="'+(d.mlp||'')+'">'+(d.mlp||'—')+'</td>'+
      '<td style="color:#f87171">'+d.fraud+'</td>'+
      '<td style="color:'+pctCol+';font-weight:'+pctW+'">'+d.pct.toFixed(1)+'%</td>'+
      '<td>'+d.total+'</td>'+
      '<td><span class="blq-tag" title="'+(d.classe||'')+'">'+(d.classe||'—')+'</span></td>'+
      '<td style="font-size:10px;color:#6b7280">'+mLbl+'</td>'+
      '<td><span class="blq-badge '+ST_CLS[st]+'" onclick="blqNextSt(\\''+d.id+'\\')">'+ST_LBL[st]+'</span></td>'+
      '<td><span class="blq-badge '+(st==='blq'?'blq-s-blq':'blq-s-ativo')+'">'+(st==='blq'?'Bloqueado':'Ativo')+'</span></td>'+
      '</tr>';

    var shps = d.shps || [];
    var shpRow = '';
    if(shps.length){{
      var chips = shps.map(function(s){{
        return '<a href="'+BLQ_LOG+s+'" target="_blank" class="blq-chip">'+s+'</a>';
      }}).join('');
      var extra = d.total>shps.length ? ' <span style="color:#374151"> · +'+(d.total-shps.length)+' nao exibidos</span>' : '';
      shpRow =
        '<tr class="blq-shp-row" id="blq-shps-'+d.id+'" style="display:none">'+
        '<td colspan="11"><div class="blq-shp-meta">'+
          '<a href="'+BLQ_DRV+d.id+'" target="_blank" class="blq-drv-link">&#8599; Ver driver no backoffice</a>'+
          '<span style="font-size:10px;color:#4b5563">'+shps.length+' IDs (top BPP)'+extra+'</span>'+
        '</div><div class="blq-shp-list">'+chips+'</div></td></tr>';
    }}
    return mainRow + shpRow;
  }}).join('');
}}

window.blqToggleShps = function(id) {{
  var row = document.getElementById('blq-shps-'+id);
  var chv = document.getElementById('blq-chv-'+id);
  if(!row) return;
  var open = row.style.display!=='none';
  row.style.display = open ? 'none' : 'table-row';
  if(chv) chv.className = open ? 'blq-chv' : 'blq-chv open';
}};
window.blqNextSt    = blqNextSt;
window.blqSortBy    = blqSortBy;
window.blqMsChg     = blqMsChg;
window.blqToggleMs  = blqToggleMs;
window.blqMsAll     = blqMsAll;
window.blqMsNone    = blqMsNone;
window.blqResetF    = function() {{
  ['blq-de','blq-ate','blq-status'].forEach(function(id){{ var e=document.getElementById(id);if(e)e.value=''; }});
  var b=document.getElementById('blq-busca');if(b)b.value='';
  var p=document.getElementById('blq-pct');if(p)p.value='0';
  blqMsNone('mlp'); blqMsNone('cls');
  blqRender();
}};
window.blqExportCSV = function() {{
  var rows = [['Driver ID','Nome','Transportadora','Total','Fraud','% Fraude','BPP USD','Classificacao','Status']];
  BLQ_DATA.forEach(function(d) {{
    rows.push([d.id,d.nome,d.mlp,d.total,d.fraud,d.pct,d.bpp,d.classe,blqGetSt(d.id)]);
  }});
  var csv = rows.map(function(r){{
    return r.map(function(v){{return '"'+String(v).replace(/"/g,'""')+'"';}}).join(',');
  }}).join('\\n');
  var a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,﻿' + encodeURIComponent(csv);
  a.download = 'bloqueios_ssp30.csv';
  a.click();
}};
window.blqBuildCharts = blqBuildCharts;
window.blqRender      = blqRender;

// Badge: set synchronously + DOMContentLoaded (belt-and-suspenders)
(function() {{ var b=document.getElementById('tab-count-bloqueios'); if(b) b.textContent=BLQ_DATA.length; }})();

document.addEventListener('DOMContentLoaded', function() {{
  var b2=document.getElementById('tab-count-bloqueios'); if(b2) b2.textContent=BLQ_DATA.length;
  try {{ blqRender(); }} catch(e) {{ console.error('blqRender init error:', e); }}
  setTimeout(function() {{
    // Re-seta badge (sobrescreve qualquer reset do JS principal)
    var b3=document.getElementById('tab-count-bloqueios'); if(b3) b3.textContent=BLQ_DATA.length;
    // Constrói charts se a aba já estiver visível (restaurada do localStorage sem onclick)
    var tab=document.getElementById('tab-bloqueios');
    if(tab && getComputedStyle(tab).display!=='none') {{
      try {{ blqBuildCharts(); }} catch(e) {{ console.error('blqBuildCharts init:', e); }}
    }}
  }}, 500);
}});
}})();
</script>
</div>"""


def inject_bloqueios_sidebar(html):
    if 'data-tab="bloqueios"' in html:
        return html
    old = '<div class="sb-item" data-tab="nodos"'
    new = (
        '<div class="sb-item" data-tab="bloqueios" onclick="showTab(\'bloqueios\',this);'
        'setTimeout(function(){if(window.blqBuildCharts)window.blqBuildCharts();'
        'if(window.blqRender)window.blqRender();},250)">\n'
        '      <i data-lucide="shield-x" width="14" height="14" class="ci"></i>\n'
        '      Bloqueios <span class="sb-badge" id="tab-count-bloqueios">0</span>\n'
        '    </div>\n'
        '    <div class="sb-item" data-tab="nodos"'
    )
    return html.replace(old, new, 1)


def find_and_replace_tab(content, tab_id, new_html):
    start_tag = f'<div id="{tab_id}" class="content">'
    start = content.find(start_tag)
    if start == -1:
        return content, False
    after = start + len(start_tag)
    # Usa '\n<' como prefixo para evitar falsos positivos dentro de <script>
    candidates = []
    for marker in ['\n<div id="tab-', '\n</main>', '\n</body>']:
        idx = content.find(marker, after)
        if idx != -1:
            candidates.append(idx + 1)  # +1: aponta para '<', não '\n'
    if not candidates:
        return content, False
    end = min(candidates)
    return content[:start] + new_html + '\n' + content[end:], True


def main():
    drivers = carregar_dados()

    print('Gerando HTML...')
    tab_html = gerar_tab(drivers)

    print('Lendo fraude.html...')
    html = HTML_OUT.read_text(encoding='utf-8')

    html = inject_bloqueios_sidebar(html)

    html, ok = find_and_replace_tab(html, 'tab-bloqueios', tab_html)
    if not ok:
        ins = html.rfind('</main>')
        if ins == -1:
            ins = html.rfind('</body>')
        if ins > 0:
            html = html[:ins] + tab_html + '\n' + html[ins:]
            ok = True
    print(f'  tab-bloqueios {"atualizada" if ok else "ERRO"}')

    html = re.sub(
        r'<span class="ver-badge">v[\d.]+</span>',
        '<span class="ver-badge">v4.6</span>',
        html, count=1
    )

    HTML_OUT.write_text(html, encoding='utf-8')
    mb = HTML_OUT.stat().st_size / 1024 / 1024
    print(f'Pronto! {mb:.1f} MB — v4.4')


if __name__ == '__main__':
    main()
