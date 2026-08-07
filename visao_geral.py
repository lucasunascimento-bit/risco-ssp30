"""
visao_geral.py — Dashboard Visão Geral de Drivers (Fraude & Lost) via BigQuery.
Dados desde 2026-01-01 — Guarulhos Mega / SSP30.
Uso: python visao_geral.py
Saída: visao_geral.html (abre automaticamente no browser)
"""

import json, os, webbrowser
from datetime import datetime
from pathlib import Path

from google.cloud import bigquery
from google.auth import default

FACILITY_NAME = 'Guarulhos Mega'
ANO_INICIO    = '2026-01-01'
OUTPUT        = Path(__file__).parent / 'visao_geral.html'

# URL base do backoffice logístico — ajuste se necessário
BO_SHP_URL    = 'https://shipping-bo.adminml.com/sauron/shipments/shipment/'
BO_DRIVER_URL = 'https://shipping-bo.adminml.com/sauron/drivers/driver/'

QUERY = """
SELECT
    SAFE_CAST(DRIVER_ID AS STRING)                                     AS id,
    IFNULL(MAX(DRIVER_NAME), '')                                       AS nome,
    IFNULL(MAX(MLP), '')                                               AS mlp,
    COUNT(DISTINCT SHIPMENT_ID)                                        AS total,
    COUNTIF(
        Classification_LM LIKE 'FRAUD%'
        OR Classification_LM = 'STOLEN ON ROUTE'
        OR Classification_LM = 'PNR C'
        OR Classification_LM = 'EMPTY BOX'
    )                                                                  AS fraud,
    ROUND(SUM(BPP_CASHOUT_USD), 2)                                     AS bpp,
    APPROX_TOP_COUNT(Classification_LM, 1)[OFFSET(0)].value           AS classe,
    ARRAY_AGG(DISTINCT FORMAT_DATE('%Y-%m', date_bpp))                 AS meses,
    ARRAY_AGG(CAST(SHIPMENT_ID AS STRING)
        ORDER BY BPP_CASHOUT_USD DESC LIMIT 30)                       AS shp_ids_sample
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = '{facility}'
  AND date_bpp >= '{inicio}'
  AND date_bpp <= CURRENT_DATE()
  AND DRIVER_ID IS NOT NULL
GROUP BY 1
ORDER BY total DESC
""".format(facility=FACILITY_NAME, inicio=ANO_INICIO)


def conectar():
    creds, project = default()
    return bigquery.Client(credentials=creds, project='meli-bi-data')


def carregar_dados():
    print('Conectando ao BigQuery...')
    client = conectar()
    print('Consultando DM_LP_MELI_OPTIMIZADO (desde 2026-01-01)...')
    rows = list(client.query(QUERY).result())
    print(f'  {len(rows)} drivers retornados pelo BQ')

    drivers = []
    for row in rows:
        meses  = sorted(m for m in (row['meses'] or []) if m)
        shps   = list(dict.fromkeys(str(s) for s in (row['shp_ids_sample'] or []) if s))[:30]
        lost   = int(row['total']) - int(row['fraud'])
        drivers.append({
            'id':    row['id'],
            'nome':  row['nome'] or '',
            'mlp':   row['mlp'] or '',
            'total': int(row['total']),
            'fraud': int(row['fraud']),
            'lost':  max(lost, 0),
            'bpp':   float(row['bpp'] or 0),
            'classe': row['classe'] or '',
            'meses': meses,
            'shps':  shps,
        })

    return drivers


def _mes_label(ym):
    try:
        d = datetime.strptime(ym, '%Y-%m')
        return d.strftime('%b/%y').capitalize()
    except Exception:
        return ym


def gerar_html(drivers):
    all_meses   = sorted({m for d in drivers for m in d['meses']})
    all_classes = sorted({d['classe'] for d in drivers if d['classe']})

    CLASS_GROUPS = [
        ('Fraude confirmada',   ['FRAUD ON ROUTE', 'FRAUD AT STATION', 'FRAUD ENE', 'STOLEN ON ROUTE']),
        ('PNR / Suspeita',      ['PNR C', 'EMPTY BOX', 'DNR', 'BUYER']),
        ('LOR — perda na rota', ['LOST ON ROUTE', 'LOST AT STATION', 'LOST ENE', 'LOST']),
        ('Dano',                ['DAMAGED', 'DAMAGED ON ROUTE', 'DAMAGED AT STATION', 'DAMAGED ENE',
                                 'DAMAGED EMBALAJE', 'DAMAGED PRODUTO', 'DAMAGED SELLER']),
        ('Atraso',              ['DELAYED ON ROUTE', 'DELAYED AT STATION', 'DELAYED ENE']),
    ]

    all_classes_set = set(all_classes)
    cls_boxes_html  = ''
    assigned        = set()

    for grp_name, grp_items in CLASS_GROUPS:
        visible = [c for c in grp_items if c in all_classes_set]
        if visible:
            cls_boxes_html += f'<div class="ms-glabel">{grp_name}</div>'
            for c in visible:
                cls_boxes_html += (
                    f'<label class="ms-item">'
                    f'<input type="checkbox" class="ms-cb" value="{c}" onchange="msChg()"> {c}'
                    f'</label>'
                )
            assigned.update(visible)

    leftovers = [c for c in all_classes if c not in assigned]
    if leftovers:
        cls_boxes_html += '<div class="ms-glabel">Outros</div>'
        for c in leftovers:
            cls_boxes_html += (
                f'<label class="ms-item">'
                f'<input type="checkbox" class="ms-cb" value="{c}" onchange="msChg()"> {c}'
                f'</label>'
            )

    data_json   = json.dumps(drivers, ensure_ascii=False)
    bo_shp      = BO_SHP_URL
    bo_driver   = BO_DRIVER_URL
    agora       = datetime.now().strftime('%d/%m/%Y %H:%M')

    mes_opts = ''.join(
        f'<option value="{m}">{_mes_label(m)}</option>'
        for m in all_meses
    )

    return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visão Geral — Fraude SSP30</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🔍</text></svg>">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#080d19;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}}
.hdr{{background:#080d19;padding:14px 28px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #7f1d1d;position:sticky;top:0;z-index:10}}
.hbrand{{display:flex;align-items:center;gap:10px}}
.hacc{{width:3px;height:26px;background:#ef4444;border-radius:2px}}
.lp{{width:32px;height:32px;background:#FFE600;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:900;color:#080d19;flex-shrink:0}}
.htitle{{font-size:15px;font-weight:700;color:#fff}}
.hsub{{font-size:10px;color:#6b7280;margin-top:1px}}
.hinfo{{font-size:10px;color:#374151}}
.controls{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:10px 28px;background:#060a14;border-bottom:1px solid #111827;position:sticky;top:56px;z-index:9}}
.controls select,.controls input{{background:#0d1321;border:1px solid #1f2937;color:#e2e8f0;font-size:12px;padding:4px 8px;border-radius:6px;height:30px}}
.controls select:focus,.controls input:focus{{outline:none;border-color:#374151}}
.sep{{color:#1f2937;font-size:18px}}
.btn-r{{background:transparent;border:1px solid #1f2937;color:#4b5563;font-size:11px;padding:4px 12px;border-radius:6px;cursor:pointer;height:30px;transition:all .15s}}
.btn-r:hover{{border-color:#374151;color:#9ca3af}}
.lbl{{font-size:11px;color:#6b7280;white-space:nowrap}}
.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;padding:16px 28px}}
.kpi{{background:#0d1321;border:1px solid #111827;border-radius:8px;padding:14px 16px}}
.kpi-l{{font-size:9px;text-transform:uppercase;letter-spacing:.7px;color:#4b5563;font-weight:700;margin-bottom:6px}}
.kpi-v{{font-size:26px;font-weight:800;color:#fff;line-height:1;letter-spacing:-1px}}
.kpi-s{{font-size:10px;color:#6b7280;margin-top:4px}}
.kpi.red .kpi-v{{color:#f87171}}
.kpi.amb .kpi-v{{color:#fbbf24}}
.kpi.grn .kpi-v{{color:#34d399}}
.tbl-hdr{{display:flex;align-items:center;justify-content:space-between;padding:10px 28px 6px}}
.tbl-title{{font-size:13px;font-weight:600;color:#e2e8f0}}
.tbl-ct{{font-size:11px;color:#4b5563}}
.tbl-scr{{margin:0 28px 28px;border-radius:8px;border:1px solid #111827;overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
thead th{{background:#0b101e;padding:9px 12px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.7px;color:#374151;font-weight:700;border-bottom:1px solid #111827;white-space:nowrap;cursor:pointer;user-select:none}}
thead th:hover{{color:#9ca3af}}
thead th.sorted{{color:#9ca3af}}
thead th.no-sort{{cursor:default}}
thead th.no-sort:hover{{color:#374151}}
tbody tr.dr-row{{border-bottom:1px solid #0b101e;transition:background .1s}}
tbody tr.dr-row:hover{{background:#0d1321}}
tbody td{{padding:8px 12px;color:#e2e8f0;white-space:nowrap}}
/* Driver ID button */
.did-btn{{background:none;border:none;color:#f9fafb;font-weight:700;font-variant-numeric:tabular-nums;cursor:pointer;font-size:12px;padding:0;font-family:inherit;display:flex;align-items:center;gap:5px}}
.did-btn:hover{{color:#60a5fa}}
.did-chevron{{font-size:9px;color:#374151;transition:transform .15s;display:inline-block}}
.did-chevron.open{{transform:rotate(180deg);color:#60a5fa}}
/* SHP expandable row */
.shp-row td{{background:#06090f;padding:10px 16px 14px 52px;border-bottom:1px solid #1f2937}}
.shp-meta{{display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap}}
.shp-driver-link{{font-size:10px;color:#9ca3af;background:#111827;border:1px solid #1f2937;border-radius:4px;padding:3px 10px;text-decoration:none;white-space:nowrap}}
.shp-driver-link:hover{{color:#e2e8f0;border-color:#374151}}
.shp-count{{font-size:10px;color:#4b5563}}
.shp-list{{display:flex;flex-wrap:wrap;gap:6px}}
.shp-chip{{font-size:11px;color:#60a5fa;background:rgba(96,165,250,.08);border:1px solid rgba(96,165,250,.2);border-radius:4px;padding:3px 8px;text-decoration:none;font-variant-numeric:tabular-nums;white-space:nowrap;transition:background .1s}}
.shp-chip:hover{{background:rgba(96,165,250,.18);border-color:rgba(96,165,250,.4)}}
/* Other cells */
.rank{{color:#374151;font-size:11px;text-align:right}}
.num{{font-variant-numeric:tabular-nums}}
.mlp-cell{{font-size:11px;color:#9ca3af;max-width:130px;overflow:hidden;text-overflow:ellipsis}}
.bar-wrap{{display:flex;gap:5px;align-items:center}}
.bar-bg{{width:56px;height:4px;background:#1f2937;border-radius:99px;overflow:hidden;flex-shrink:0}}
.bar-fill{{height:100%;border-radius:99px;background:#ef4444}}
.badge-s{{display:inline-block;font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px;cursor:pointer;user-select:none;transition:opacity .1s}}
.badge-s:hover{{opacity:.8}}
.s-mon{{background:rgba(74,222,128,.08);color:#4ade80;border:1px solid rgba(74,222,128,.2)}}
.s-inv{{background:rgba(251,191,36,.08);color:#fbbf24;border:1px solid rgba(251,191,36,.2)}}
.s-blq{{background:rgba(239,68,68,.08);color:#f87171;border:1px solid rgba(239,68,68,.2)}}
.tag{{font-size:10px;color:#9ca3af;background:#111827;padding:2px 7px;border-radius:4px}}
.empty{{text-align:center;padding:48px;color:#374151;font-size:13px}}
.tip{{font-size:10px;color:#374151;padding:6px 28px 0;text-align:right}}
/* Multi-select */
.ms-wrap{{position:relative;display:inline-block}}
.ms-btn{{background:#0d1321;border:1px solid #1f2937;color:#e2e8f0;font-size:12px;padding:0 10px;border-radius:6px;height:30px;cursor:pointer;white-space:nowrap;min-width:130px;text-align:left;transition:border-color .15s}}
.ms-btn:hover{{border-color:#374151}}
.ms-btn.active{{border-color:#ef4444;color:#f87171}}
.ms-panel{{position:absolute;top:34px;left:0;z-index:200;background:#0d1321;border:1px solid #1f2937;border-radius:8px;padding:8px 0 6px;min-width:240px;max-height:380px;overflow-y:auto;box-shadow:0 8px 24px rgba(0,0,0,.7)}}
.ms-actions{{display:flex;gap:5px;padding:4px 10px 8px;border-bottom:1px solid #111827;margin-bottom:4px;flex-wrap:wrap}}
.ms-actions button{{font-size:10px;color:#4b5563;background:transparent;border:1px solid #1f2937;border-radius:4px;padding:2px 8px;cursor:pointer;white-space:nowrap;line-height:1.6}}
.ms-actions button:hover{{color:#9ca3af;border-color:#374151}}
.ms-preset-lorf{{color:#f87171 !important;border-color:rgba(239,68,68,.3) !important}}
.ms-preset-lorf:hover{{color:#fca5a5 !important;border-color:rgba(239,68,68,.5) !important}}
.ms-glabel{{font-size:9px;font-weight:700;letter-spacing:.7px;text-transform:uppercase;color:#374151;padding:6px 10px 3px}}
.ms-item{{display:flex;align-items:center;gap:8px;padding:4px 10px;font-size:12px;color:#e2e8f0;cursor:pointer;user-select:none}}
.ms-item:hover{{background:#111827}}
.ms-item input[type=checkbox]{{accent-color:#ef4444;cursor:pointer;width:13px;height:13px;flex-shrink:0}}
</style>
</head>
<body>

<div class="hdr">
  <div class="hbrand">
    <div class="hacc"></div>
    <div class="lp">LP</div>
    <div>
      <div class="htitle">Visão Geral — Drivers Monitorados <span style="font-size:10px;font-weight:700;background:rgba(239,68,68,.15);color:#f87171;border:1px solid rgba(239,68,68,.3);border-radius:4px;padding:2px 7px;margin-left:6px;vertical-align:middle">SSP30</span></div>
      <div class="hsub">Guarulhos Mega · Fraude &amp; Lost · dados desde jan/2026</div>
    </div>
  </div>
  <div class="hinfo">Atualizado: {agora}</div>
</div>

<div class="controls">
  <span class="lbl">Período</span>
  <select id="f-de" onchange="render()">
    <option value="">De...</option>
    {mes_opts}
  </select>
  <select id="f-ate" onchange="render()">
    <option value="">Até...</option>
    {mes_opts}
  </select>
  <span class="sep">|</span>
  <select id="f-causa" onchange="render()">
    <option value="">Toda causa</option>
    <option value="FRAUD">FRAUD</option>
    <option value="LOST">LOST</option>
  </select>
  <div class="ms-wrap" id="ms-wrap">
    <button class="ms-btn" id="ms-btn" onclick="toggleMs()">Toda classe ▾</button>
    <div class="ms-panel" id="ms-panel" style="display:none">
      <div class="ms-actions">
        <button onclick="msAll()">Todas</button>
        <button onclick="msNone()">Nenhuma</button>
        <button class="ms-preset-lorf" onclick="msPreset(PRESET_LORF)">LOR+Fraude</button>
        <button onclick="msPreset(PRESET_FRAUD)">Só Fraude</button>
        <button onclick="msPreset(PRESET_LOR)">Só LOR</button>
        <button onclick="msPreset(PRESET_PNR)">PNR/Suspeita</button>
      </div>
      {cls_boxes_html}
    </div>
  </div>
  <select id="f-status" onchange="render()">
    <option value="">Todos os status</option>
    <option value="mon">Monitorado</option>
    <option value="inv">Em investigação</option>
    <option value="blq">Bloqueado</option>
  </select>
  <span class="sep">|</span>
  <span class="lbl">Mín. SHPs</span>
  <input type="number" id="f-min" value="1" min="1" max="9999" oninput="render()" style="width:58px;text-align:center">
  <input type="search" id="f-busca" placeholder="Driver ID..." oninput="render()" style="width:130px">
  <button class="btn-r" onclick="resetF()">&#x2715; Limpar</button>
</div>

<div class="kpis">
  <div class="kpi">
    <div class="kpi-l">Drivers monitorados</div>
    <div class="kpi-v" id="kv-tot">—</div>
    <div class="kpi-s" id="ks-tot">desde jan/2026</div>
  </div>
  <div class="kpi amb">
    <div class="kpi-l">Em investigação</div>
    <div class="kpi-v" id="kv-inv">—</div>
    <div class="kpi-s">clique no badge p/ alterar</div>
  </div>
  <div class="kpi red">
    <div class="kpi-l">Bloqueados</div>
    <div class="kpi-v" id="kv-blq">—</div>
    <div class="kpi-s">confirmados</div>
  </div>
  <div class="kpi">
    <div class="kpi-l">Total SHPs</div>
    <div class="kpi-v" id="kv-shp">—</div>
    <div class="kpi-s">FRAUD + LOST</div>
  </div>
  <div class="kpi grn">
    <div class="kpi-l">BPP acumulado</div>
    <div class="kpi-v" id="kv-bpp">—</div>
    <div class="kpi-s">USD cashout</div>
  </div>
</div>

<div class="tip">&#9432; Status salvo localmente. Clique no badge para ciclar: Monitorado → Em investigação → Bloqueado. Clique no Driver ID para ver os SHPs.</div>

<div class="tbl-hdr">
  <span class="tbl-title">Ranking de drivers</span>
  <span class="tbl-ct" id="tbl-ct"></span>
</div>
<div class="tbl-scr">
  <table>
    <thead>
      <tr>
        <th class="no-sort">#</th>
        <th onclick="sortBy('total')" id="th-total">Total SHPs ↕</th>
        <th class="no-sort">Driver ID</th>
        <th class="no-sort">Transportadora</th>
        <th onclick="sortBy('fraud')" id="th-fraud">FRAUD ↕</th>
        <th onclick="sortBy('lost')" id="th-lost">LOST ↕</th>
        <th class="no-sort">Distribuição</th>
        <th class="no-sort">Classificação</th>
        <th onclick="sortBy('bpp')" id="th-bpp">BPP (USD) ↕</th>
        <th class="no-sort">Meses</th>
        <th class="no-sort">Status</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<script>
const DATA = {data_json};
const BO_SHP    = '{bo_shp}';
const BO_DRIVER = '{bo_driver}';

const ST_CYCLE = ['mon','inv','blq'];
const ST_LBL = {{mon:'Monitorado', inv:'Em investigação', blq:'Bloqueado'}};
const ST_CLS = {{mon:'s-mon', inv:'s-inv', blq:'s-blq'}};

const PRESET_LORF  = ['FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE','STOLEN ON ROUTE',
                      'PNR C','EMPTY BOX','DNR','LOST ON ROUTE','LOST ENE','LOST AT STATION','LOST'];
const PRESET_FRAUD = ['FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE','STOLEN ON ROUTE'];
const PRESET_LOR   = ['LOST ON ROUTE','LOST AT STATION','LOST ENE','LOST'];
const PRESET_PNR   = ['PNR C','EMPTY BOX','DNR','BUYER'];

let _sortKey = 'total', _sortDir = -1;

// ── Multi-select ────────────────────────────────────────────────────
function toggleMs() {{
  const p = document.getElementById('ms-panel');
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
}}
function msChg() {{ updateMsBtn(); render(); }}
function updateMsBtn() {{
  const n = document.querySelectorAll('.ms-cb:checked').length;
  const btn = document.getElementById('ms-btn');
  btn.textContent = n ? n + ' classe(s) ▾' : 'Toda classe ▾';
  btn.className   = n ? 'ms-btn active' : 'ms-btn';
}}
function msAll()  {{ document.querySelectorAll('.ms-cb').forEach(e => e.checked = true);  updateMsBtn(); render(); }}
function msNone() {{ document.querySelectorAll('.ms-cb').forEach(e => e.checked = false); updateMsBtn(); render(); }}
function msPreset(vals) {{
  const s = new Set(vals);
  document.querySelectorAll('.ms-cb').forEach(e => e.checked = s.has(e.value));
  updateMsBtn(); render();
}}
document.addEventListener('click', e => {{
  const wrap = document.getElementById('ms-wrap');
  if (wrap && !wrap.contains(e.target))
    document.getElementById('ms-panel').style.display = 'none';
}});

// ── SHP dropdown ────────────────────────────────────────────────────
function toggleShps(id) {{
  const row = document.getElementById('shps-' + id);
  const chv = document.getElementById('chv-' + id);
  if (!row) return;
  const isOpen = row.style.display !== 'none';
  row.style.display = isOpen ? 'none' : 'table-row';
  if (chv) chv.className = isOpen ? 'did-chevron' : 'did-chevron open';
}}

// ── Status & sort ───────────────────────────────────────────────────
function getSt(id)  {{ return localStorage.getItem('vg_' + id) || 'mon'; }}
function nextSt(id) {{
  const cur  = getSt(id);
  const next = ST_CYCLE[(ST_CYCLE.indexOf(cur) + 1) % 3];
  localStorage.setItem('vg_' + id, next);
  render();
}}
function sortBy(k) {{
  if (_sortKey === k) _sortDir *= -1; else {{ _sortKey = k; _sortDir = -1; }}
  render();
}}

// ── Render ──────────────────────────────────────────────────────────
function render() {{
  const de       = document.getElementById('f-de').value;
  const ate      = document.getElementById('f-ate').value;
  const causa    = document.getElementById('f-causa').value;
  const stF      = document.getElementById('f-status').value;
  const busca    = document.getElementById('f-busca').value.trim();
  const minShps  = parseInt(document.getElementById('f-min').value) || 1;
  const classeSel = new Set([...document.querySelectorAll('.ms-cb:checked')].map(e => e.value));

  let rows = DATA.filter(d => {{
    if (d.total < minShps) return false;
    if (de || ate) {{
      const ok = d.meses.some(m => (!de || m >= de) && (!ate || m <= ate));
      if (!ok) return false;
    }}
    if (causa === 'FRAUD' && d.fraud === 0) return false;
    if (causa === 'LOST'  && d.lost  === 0) return false;
    if (classeSel.size && !classeSel.has(d.classe)) return false;
    if (stF && getSt(d.id) !== stF) return false;
    if (busca && !d.id.includes(busca)) return false;
    return true;
  }});

  rows.sort((a, b) => _sortDir * (a[_sortKey] - b[_sortKey]));

  const inv  = rows.filter(d => getSt(d.id) === 'inv').length;
  const blq  = rows.filter(d => getSt(d.id) === 'blq').length;
  const shps = rows.reduce((s, d) => s + d.total, 0);
  const bpp  = rows.reduce((s, d) => s + d.bpp, 0);

  document.getElementById('kv-tot').textContent = rows.length.toLocaleString('pt-BR');
  document.getElementById('ks-tot').textContent = rows.length === DATA.length ? 'total monitorados' : 'de ' + DATA.length.toLocaleString('pt-BR') + ' monitorados';
  document.getElementById('kv-inv').textContent = inv.toLocaleString('pt-BR');
  document.getElementById('kv-blq').textContent = blq.toLocaleString('pt-BR');
  document.getElementById('kv-shp').textContent = shps.toLocaleString('pt-BR');
  document.getElementById('kv-bpp').textContent = '$' + Math.round(bpp).toLocaleString('en-US');
  document.getElementById('tbl-ct').textContent = rows.length + ' drivers';

  ['total','fraud','lost','bpp'].forEach(k => {{
    const el = document.getElementById('th-' + k);
    if (el) el.className = _sortKey === k ? 'sorted' : '';
  }});

  const body = document.getElementById('tbody');
  if (!rows.length) {{
    body.innerHTML = '<tr><td colspan="11" class="empty">Nenhum driver encontrado.</td></tr>';
    return;
  }}

  body.innerHTML = rows.map((d, i) => {{
    const st     = getSt(d.id);
    const fraudW = Math.round(d.total ? d.fraud / d.total * 100 : 0);
    const mLbl   = d.meses.length
      ? d.meses.slice(-3).map(m => {{
          const dt = new Date(m + '-15');
          return dt.toLocaleDateString('pt-BR', {{month:'short', year:'2-digit'}}).replace('. ', '/');
        }}).join(' · ') + (d.meses.length > 3 ? ' +' : '')
      : '—';

    const mainRow = '<tr class="dr-row">' +
      '<td class="rank num">' + (i+1) + '</td>' +
      '<td class="num"><strong>' + d.total.toLocaleString('pt-BR') + '</strong></td>' +
      '<td><button class="did-btn" onclick="toggleShps(' + d.id + ')">' +
        d.id + '<span class="did-chevron" id="chv-' + d.id + '">▼</span>' +
      '</button></td>' +
      '<td class="mlp-cell" title="' + (d.mlp||'') + '">' + (d.mlp || '—') + '</td>' +
      '<td class="num" style="color:#f87171">' + d.fraud + '</td>' +
      '<td class="num" style="color:#fbbf24">' + d.lost + '</td>' +
      '<td><div class="bar-wrap"><div class="bar-bg"><div class="bar-fill" style="width:' + fraudW + '%"></div></div>' +
        '<span style="font-size:10px;color:#4b5563">' + fraudW + '%F</span></div></td>' +
      '<td><span class="tag">' + (d.classe || '—') + '</span></td>' +
      '<td class="num" style="color:#34d399">$' + d.bpp.toLocaleString('en-US', {{minimumFractionDigits:2, maximumFractionDigits:2}}) + '</td>' +
      '<td style="font-size:10px;color:#6b7280;max-width:140px;overflow:hidden;text-overflow:ellipsis">' + mLbl + '</td>' +
      '<td><span class="badge-s ' + ST_CLS[st] + '" onclick="nextSt(' + d.id + ')">' + ST_LBL[st] + '</span></td>' +
      '</tr>';

    const shps = d.shps || [];
    let shpRow = '';
    if (shps.length) {{
      const chips = shps.map(s =>
        '<a href="' + BO_SHP + s + '" target="_blank" class="shp-chip">' + s + '</a>'
      ).join('');
      const extra = d.total > shps.length
        ? ' <span style="color:#374151"> · +' + (d.total - shps.length) + ' não exibidos</span>' : '';
      shpRow = '<tr class="shp-row" id="shps-' + d.id + '" style="display:none">' +
        '<td colspan="11">' +
        '<div class="shp-meta">' +
          '<a href="' + BO_DRIVER + d.id + '" target="_blank" class="shp-driver-link">↗ Ver driver no backoffice</a>' +
          '<span class="shp-count">' + shps.length + ' IDs (top BPP)' + extra + '</span>' +
        '</div>' +
        '<div class="shp-list">' + chips + '</div>' +
        '</td></tr>';
    }}

    return mainRow + shpRow;
  }}).join('');
}}

function resetF() {{
  ['f-de','f-ate','f-causa','f-status'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('f-busca').value = '';
  document.getElementById('f-min').value  = '1';
  msNone();
}}

render();
</script>
</body>
</html>'''


if __name__ == '__main__':
    print('=' * 55)
    print('Visão Geral — Fraude SSP30 (BigQuery)')
    print('=' * 55)
    drivers = carregar_dados()
    print(f'{len(drivers)} drivers carregados')
    html = gerar_html(drivers)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'Salvo: {OUTPUT}')
    webbrowser.open(OUTPUT.as_uri())
