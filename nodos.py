"""
nodos.py — Análise de Nodos (Places/NEX/DC/PU/XPT) para o Dashboard de Fraude SSP30.
Consulta BT_SHP_PLACES_AND_NODES + DM_LP_MELI_OPTIMIZADO + LK_SHP_MISSING_MANAGEMENT_PACKAGES
e injeta tab-nodos em fraude.html.
Uso: python nodos.py
"""

import json
from datetime import datetime
from pathlib import Path

from google.cloud import bigquery
from google.auth import default

FACILITY       = 'Guarulhos Mega'
FACILITY_ID    = 'SSP30'
INICIO         = '2026-01-01'
DIAS_PARADO_MIN = 7
FRAUDE_HTML    = Path(__file__).parent / 'fraude.html'
LOG_URL        = 'https://shipping-bo.adminml.com/sauron/shipments/shipment/'

_FC = (
    "Classification_LM LIKE 'FRAUD%' "
    "OR Classification_LM = 'STOLEN ON ROUTE' "
    "OR Classification_LM = 'PNR C' "
    "OR Classification_LM = 'EMPTY BOX'"
)

# ── Query 1: agregado por nodo (place x tipo) ───────────────────────────────
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
  f.mes                                                       AS mes,
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
  COUNTIF(f.classe = 'EMPTY BOX')                             AS empty_box_shps,
  ROUND(SUM(IF(f.classe = 'EMPTY BOX', f.bpp, 0)), 2)         AS empty_box_bpp,
  COUNTIF(f.classe = 'PNR C')                                 AS pnr_shps,
  ROUND(SUM(IF(f.classe = 'PNR C', f.bpp, 0)), 2)             AS pnr_bpp,
  APPROX_TOP_COUNT(f.classe, 1)[OFFSET(0)].value              AS classe_principal,
  ARRAY_AGG(CAST(p.SHP_SHIPMENT_ID AS STRING)
      ORDER BY f.bpp DESC LIMIT 20)                           AS shp_sample
FROM `meli-bi-data.WHOWNER.BT_SHP_PLACES_AND_NODES` p
INNER JOIN shp_dedup f ON CAST(p.SHP_SHIPMENT_ID AS STRING) = f.sid
WHERE p.SERVICE_TYPE IN ('DO','NEX','DC','PU','XPT')
GROUP BY 1, 2, 3
ORDER BY bpp DESC
"""

# ── Query 2: evidencia (1 linha por SHP) para EMPTY BOX e PNR C ─────────────
QUERY_EVIDENCIA = f"""
WITH ev AS (
  SELECT
    CAST(SHIPMENT_ID AS STRING)                               AS sid,
    MAX(IFNULL(Classification_LM, ''))                        AS classe,
    MAX(IFNULL(CAUSA_BPP, ''))                                AS causa_bpp,
    MAX(BPP_CASHOUT_USD)                                      AS bpp,
    MAX(FORMAT_DATE('%Y-%m', date_bpp))                       AS mes
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = '{FACILITY}'
    AND date_bpp >= '{INICIO}'
    AND date_bpp <= CURRENT_DATE()
    AND Classification_LM IN ('EMPTY BOX', 'PNR C')
  GROUP BY 1
)
SELECT
  p.SHP_AGENCY_ID                                             AS place_id,
  p.SERVICE_TYPE                                              AS tipo,
  e.sid, e.classe, e.causa_bpp, e.bpp, e.mes
FROM ev e
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_PLACES_AND_NODES` p
  ON CAST(p.SHP_SHIPMENT_ID AS STRING) = e.sid
WHERE p.SERVICE_TYPE IN ('DO','NEX','DC','PU','XPT')
ORDER BY e.bpp DESC
"""

# ── Query 3: pacotes parados (sem movimentacao ou vencidos) por nodo ────────
QUERY_PARADOS = f"""
WITH stuck AS (
  SELECT
    CAST(SHP_SHIPMENT_ID AS STRING)                           AS sid,
    IFNULL(SHP_LG_STATUS, '')                                 AS status,
    IFNULL(SHP_LG_SUB_STATUS, '')                             AS substatus,
    IFNULL(DAYS_HANDLING_SVC, 0)                              AS dias_parado,
    IFNULL(DAYS_EXPIRED_PROMISE, 0)                           AS dias_vencido,
    ROUND(IFNULL(SHP_ORDER_COST_USD, 0), 2)                   AS gmv
  FROM `meli-bi-data.WHOWNER.LK_SHP_MISSING_MANAGEMENT_PACKAGES`
  WHERE SHP_LG_FACILITY_ID = '{FACILITY_ID}'
    AND (IFNULL(DAYS_HANDLING_SVC, 0) >= {DIAS_PARADO_MIN} OR IFNULL(DAYS_EXPIRED_PROMISE, 0) > 0)
)
SELECT
  p.SHP_AGENCY_ID                                             AS place_id,
  p.SERVICE_TYPE                                              AS tipo,
  s.sid, s.status, s.substatus, s.dias_parado, s.dias_vencido, s.gmv
FROM stuck s
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_PLACES_AND_NODES` p
  ON CAST(p.SHP_SHIPMENT_ID AS STRING) = s.sid
WHERE p.SERVICE_TYPE IN ('DO','NEX','DC','PU','XPT')
ORDER BY s.dias_parado DESC
"""

TIPO_LBL = {'DO': 'Place', 'NEX': 'NEX', 'DC': 'DC', 'PU': 'Pickup', 'XPT': 'XPT'}


def conectar():
    creds, project = default()
    return bigquery.Client(credentials=creds, project='meli-bi-data')


def _score(fraud, bpp, empty_box, pnr, parados, pct_fraud):
    s = (min(fraud, 20) + min(bpp / 300, 20) + min(empty_box * 3, 15)
         + min(pnr * 2, 15) + min(parados * 2, 15) + (15 if pct_fraud > 20 else 0))
    return round(s, 1)


def carregar_dados():
    print('Conectando ao BigQuery...')
    client = conectar()
    print('Disparando as 3 queries em paralelo (nodos, evidencia, parados)...')
    job_nodos    = client.query(QUERY)
    job_ev       = client.query(QUERY_EVIDENCIA)
    job_parados  = client.query(QUERY_PARADOS)

    print('Aguardando nodos agregados...')
    rows = list(job_nodos.result())
    print(f'  {len(rows):,} nodo(s) retornado(s)')

    print('Aguardando evidencia EMPTY BOX/PNR...')
    ev_rows = list(job_ev.result())
    print(f'  {len(ev_rows):,} SHP(s) EMPTY BOX/PNR C')

    print('Aguardando pacotes parados...')
    parados_rows = list(job_parados.result())
    print(f'  {len(parados_rows):,} pacote(s) parado(s) (>= {DIAS_PARADO_MIN}d sem mov. ou vencido)')

    # Evidencia EMPTY BOX/PNR por nodo
    evidencias = {}
    for row in ev_rows:
        key = (row['place_id'] or '', row['tipo'] or '')
        evidencias.setdefault(key, []).append({
            'sid':   row['sid'],
            'classe': row['classe'] or '',
            'causa': row['causa_bpp'] or row['classe'] or '',
            'bpp':   float(row['bpp'] or 0),
            'mes':   row['mes'] or '',
        })

    # Pacotes parados por nodo
    parados_por_nodo = {}
    for row in parados_rows:
        key = (row['place_id'] or '', row['tipo'] or '')
        parados_por_nodo.setdefault(key, []).append({
            'sid':          row['sid'],
            'status':       row['status'] or '',
            'substatus':    row['substatus'] or '',
            'dias':         int(row['dias_parado'] or 0),
            'dias_vencido': int(row['dias_vencido'] or 0),
            'gmv':          float(row['gmv'] or 0),
        })

    # Agrupa as linhas mensais em (place_id, tipo) -> breakdown por mes
    grouped = {}
    for row in rows:
        tipo = row['tipo'] or ''
        place_id = row['place_id'] or ''
        key = (place_id, tipo)
        g = grouped.setdefault(key, {
            'nome': '', 'node_id': '', 'estado': '', 'cidade': '',
            'classe_counts': {}, 'md': [], 'shps': [],
        })
        if row['nome']:     g['nome']    = row['nome']
        if row['node_id']:  g['node_id'] = row['node_id']
        if row['estado']:   g['estado']  = row['estado']
        if row['cidade']:   g['cidade']  = row['cidade']
        classe = row['classe_principal'] or ''
        total_m = int(row['total_shps'])
        if classe:
            g['classe_counts'][classe] = g['classe_counts'].get(classe, 0) + total_m
        g['md'].append({
            'mes': row['mes'] or '',
            't':   total_m,
            'b':   float(row['bpp'] or 0),
            'f':   int(row['fraud_shps']),
            'd':   int(row['damaged_shps']),
            'eb':  int(row['empty_box_shps']),
            'ebb': float(row['empty_box_bpp'] or 0),
            'pnr': int(row['pnr_shps']),
            'pnrb': float(row['pnr_bpp'] or 0),
            'drv': int(row['drivers']),
            'sel': int(row['sellers']),
            'buy': int(row['buyers']),
        })
        for s in (row['shp_sample'] or []):
            if s: g['shps'].append(str(s))

    nodos = []
    for (place_id, tipo), g in grouped.items():
        md = sorted(g['md'], key=lambda m: m['mes'])
        total = sum(m['t'] for m in md)
        bpp   = round(sum(m['b'] for m in md), 2)
        fraud = sum(m['f'] for m in md)
        dmg   = sum(m['d'] for m in md)
        empty_box_qtd = sum(m['eb'] for m in md)
        empty_box_bpp = round(sum(m['ebb'] for m in md), 2)
        pnr_qtd = sum(m['pnr'] for m in md)
        pnr_bpp = round(sum(m['pnrb'] for m in md), 2)
        pct_fraud = round(fraud / total * 100, 1) if total else 0.0
        classe_principal = max(g['classe_counts'].items(), key=lambda x: x[1])[0] if g['classe_counts'] else ''
        shps = list(dict.fromkeys(g['shps']))[:20]
        key = (place_id, tipo)
        parados_lista = parados_por_nodo.get(key, [])
        parados_qtd   = len(parados_lista)
        parados_gmv   = round(sum(p['gmv'] for p in parados_lista), 2)

        nodos.append({
            'nodo':          g['nome'] or place_id or 'Não Identificado',
            'node_id':       g['node_id'],
            'place_id':      place_id,
            'tipo':          tipo,
            'tipo_lbl':      TIPO_LBL.get(tipo, tipo),
            'total':         total,
            'drivers':       sum(m['drv'] for m in md),
            'sellers':       sum(m['sel'] for m in md),
            'buyers':        sum(m['buy'] for m in md),
            'bpp':           bpp,
            'classe':        classe_principal,
            'fraud':         fraud,
            'damaged':       dmg,
            'pct_fraud':     pct_fraud,
            'empty_box_qtd': empty_box_qtd,
            'empty_box_bpp': empty_box_bpp,
            'pnr_qtd':       pnr_qtd,
            'pnr_bpp':       pnr_bpp,
            'parados_qtd':   parados_qtd,
            'parados_gmv':   parados_gmv,
            'score':         _score(fraud, bpp, empty_box_qtd, pnr_qtd, parados_qtd, pct_fraud),
            'estado':        g['estado'],
            'cidade':        g['cidade'],
            'meses':         [m['mes'] for m in md if m['mes']],
            'shps':          shps,
            'md':            md,
        })

    return nodos, evidencias, parados_por_nodo


def gerar_tab_html(nodos, evidencias, parados_por_nodo):
    total_nodos  = len(nodos)
    total_shps   = sum(n['total'] for n in nodos)
    total_bpp    = round(sum(n['bpp'] for n in nodos), 2)
    total_fraud  = sum(n['fraud'] for n in nodos)
    total_empty  = sum(n['empty_box_qtd'] for n in nodos)
    total_pnr    = sum(n['pnr_qtd'] for n in nodos)
    total_parados = sum(n['parados_qtd'] for n in nodos)
    top_bpp      = sorted(nodos, key=lambda x: x['bpp'], reverse=True)[:12]
    top_shps     = sorted(nodos, key=lambda x: x['total'], reverse=True)[:12]

    nodos_json    = json.dumps(nodos, ensure_ascii=False)
    top_bpp_json  = json.dumps([{'nodo': n['nodo']+' ('+n['tipo_lbl']+')', 'bpp': n['bpp']} for n in top_bpp], ensure_ascii=False)
    top_shps_json = json.dumps([{'nodo': n['nodo']+' ('+n['tipo_lbl']+')', 'total': n['total']} for n in top_shps], ensure_ascii=False)

    # Evidencia/parados indexados por "place_id|tipo" (string, pra virar chave JS)
    ev_json = json.dumps(
        {f'{pid}|{tp}': v for (pid, tp), v in evidencias.items()},
        ensure_ascii=False,
    )
    parados_json = json.dumps(
        {f'{pid}|{tp}': v for (pid, tp), v in parados_por_nodo.items()},
        ensure_ascii=False,
    )

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
    Todos os nós abaixo têm ligação direta com o SSP30 — só entram na lista os que receberam ao menos um shipment que saiu do SSP30/Guarulhos Mega. Estados fora de SP são o destino final do comprador (Place/Pickup), não é erro. Um mesmo shipment pode passar por mais de um nó (ex: um NEX e depois um Place de entrega) — os valores de BPP/SHPs são por nó e não devem ser somados entre tipos diferentes.
  </div>

  <!-- Sub-abas -->
  <div style="display:flex;gap:6px;margin-bottom:16px;border-bottom:1px solid #1f2937">
    <button id="nodtab-todos" class="nod-subtab nod-subtab-active" onclick="nodMostrarSub('todos',this)"
      style="background:transparent;border:none;border-bottom:2px solid #34d399;color:#e5e7eb;font-size:12px;font-weight:700;padding:8px 14px;cursor:pointer">Todos os Nodos</button>
    <button id="nodtab-ofensores" class="nod-subtab" onclick="nodMostrarSub('ofensores',this)"
      style="background:transparent;border:none;border-bottom:2px solid transparent;color:#9ca3af;font-size:12px;font-weight:700;padding:8px 14px;cursor:pointer">Nodos Ofensores</button>
  </div>

  <!-- Periodo (afeta as duas sub-abas) -->
  <div style="display:flex;align-items:center;gap:6px;margin-bottom:14px;flex-wrap:wrap">
    <span style="font-size:11px;color:#9ca3af;font-weight:500;margin-right:4px">Período:</span>
    <button class="lf-btn" data-p="1m" onclick="nodPeriodo('1m',this)">1m</button>
    <button class="lf-btn" data-p="3m" onclick="nodPeriodo('3m',this)">3m</button>
    <button class="lf-btn" data-p="6m" onclick="nodPeriodo('6m',this)">6m</button>
    <button class="lf-btn lf-active" data-p="all" onclick="nodPeriodo('all',this)">Tudo</button>
    <span style="font-size:9px;color:#4b5563">Pacotes parados não têm data — sempre mostram o estado atual, independente do período</span>
  </div>

  <!-- VIEW: TODOS -->
  <div id="nod-view-todos">

  <!-- KPIs -->
  <div style="display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap">
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#34d399">{total_nodos:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">Nodos (place x tipo)</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div id="nod-kpi-shps" style="font-size:22px;font-weight:700;color:#e5e7eb">{total_shps:,}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">SHPs Totais</div>
    </div>
    <div style="background:#1a0505;border:1px solid #7f1d1d;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div id="nod-kpi-bpp" style="font-size:22px;font-weight:700;color:#f87171">US$ {total_bpp:,.0f}</div>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">BPP Total</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:110px;text-align:center">
      <div id="nod-kpi-fraud" style="font-size:22px;font-weight:700;color:#fbbf24">{total_fraud:,}</div>
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

  <!-- VIEW: OFENSORES -->
  <div id="nod-view-ofensores" style="display:none">
    <div style="display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap">
      <div style="background:#1a0505;border:1px solid #7f1d1d;border-radius:8px;padding:12px 20px;flex:1;min-width:120px;text-align:center">
        <div id="nod-of-kpi-fraud" style="font-size:22px;font-weight:700;color:#f87171">{total_fraud:,}</div>
        <div style="font-size:10px;color:#6b7280;margin-top:2px">SHPs Fraude Confirmada</div>
      </div>
      <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:120px;text-align:center">
        <div id="nod-of-kpi-empty" style="font-size:22px;font-weight:700;color:#a78bfa">{total_empty:,}</div>
        <div style="font-size:10px;color:#6b7280;margin-top:2px">SHPs Empty Box</div>
      </div>
      <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:120px;text-align:center">
        <div id="nod-of-kpi-pnr" style="font-size:22px;font-weight:700;color:#fbbf24">{total_pnr:,}</div>
        <div style="font-size:10px;color:#6b7280;margin-top:2px">SHPs PNR</div>
      </div>
      <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:12px 20px;flex:1;min-width:120px;text-align:center">
        <div id="nod-of-kpi-parados" style="font-size:22px;font-weight:700;color:#60a5fa">{total_parados:,}</div>
        <div style="font-size:10px;color:#6b7280;margin-top:2px">Pacotes Parados (&gt;= {DIAS_PARADO_MIN}d ou vencidos, não filtra por período)</div>
      </div>
    </div>

    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Nodos ofensores</span>
        <span style="font-size:10px;color:#374151;margin-left:6px">ordenado por score (clique na linha pra ver os shipments abaixo)</span>
        <span id="nod-of-header" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
      <input id="nod-of-busca" type="text" placeholder="Buscar nodo..." oninput="nodOfFiltrar()"
        style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
    </div>
    <div style="border:1px solid #1f2937;border-radius:8px;overflow:hidden;margin-bottom:14px">
      <div style="overflow-y:auto;max-height:340px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="nodOfSort('nodo')">Nodo</th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Tipo</th>
              <th style="padding:6px 8px;text-align:center;color:#fbbf24;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="nodOfSort('score')">Score</th>
              <th style="padding:6px 8px;text-align:center;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="nodOfSort('fraud')">Fraude</th>
              <th style="padding:6px 8px;text-align:center;color:#a78bfa;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="nodOfSort('empty_box_qtd')">Empty Box</th>
              <th style="padding:6px 8px;text-align:center;color:#fbbf24;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="nodOfSort('pnr_qtd')">PNR</th>
              <th style="padding:6px 8px;text-align:center;color:#60a5fa;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="nodOfSort('parados_qtd')">Parados</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="nodOfSort('bpp')">BPP</th>
            </tr>
          </thead>
          <tbody id="nod-of-tbody"></tbody>
        </table>
      </div>
    </div>

    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Shipments do nodo selecionado</span>
        <span id="nod-ev-header" style="font-size:10px;color:#4b5563;margin-left:6px">- selecione um nodo acima</span>
      </div>
    </div>
    <div style="border:1px solid #1f2937;border-radius:8px;overflow:hidden">
      <div style="overflow-y:auto;max-height:340px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Tipo</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Shipment ID</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Causa / Status</th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Mês / Dias parado</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">BPP / GMV</th>
            </tr>
          </thead>
          <tbody id="nod-ev-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

</div>

<script>
(function(){{
var NODOS_DATA={nodos_json};
var NOD_TOP_BPP={top_bpp_json};
var NOD_TOP_SHPS={top_shps_json};
var NOD_EV_DATA={ev_json};
var NOD_PARADOS_DATA={parados_json};
var LOG_URL='{LOG_URL}';
var nodChtBpp=null,nodChtShps=null;
var TIPO_CLR={{DO:'#34d399',NEX:'#60a5fa',DC:'#a78bfa',PU:'#fbbf24',XPT:'#f472b6'}};
var _nodOfQ='';
var _nodOfSortKey='score', _nodOfSortDir=-1;
var _nodOfSel=null;
var _nodPeriod='all';

function _nodPeriodRange(p){{
  if(p==='all') return {{de:null, ate:null}};
  var hoje=new Date();
  var meses=p==='1m'?1:(p==='3m'?3:6);
  var de=new Date(hoje.getFullYear(), hoje.getMonth()-meses, hoje.getDate());
  return {{de:de, ate:hoje}};
}}

function _nodScore(fraud,bpp,eb,pnr,parados,pct){{
  var s=Math.min(fraud,20)+Math.min(bpp/300,20)+Math.min(eb*3,15)+Math.min(pnr*2,15)+Math.min(parados*2,15)+(pct>20?15:0);
  return Math.round(s*10)/10;
}}

function _nodApplyPeriodo(n, de, ate){{
  var parados=n.parados_qtd||0;
  if(!de) return n;
  var t=0,b=0,f=0,d=0,eb=0,ebb=0,pnr=0,pnrb=0;
  (n.md||[]).forEach(function(m){{
    var p=(m.mes||'').split('-');
    var dt=p.length>=2?new Date(+p[0],+p[1]-1,1):null;
    if(dt && dt>=de && dt<=ate){{ t+=m.t;b+=m.b;f+=m.f;d+=m.d;eb+=m.eb;ebb+=m.ebb;pnr+=m.pnr;pnrb+=m.pnrb; }}
  }});
  var pct=t?Math.round(f/t*1000)/10:0;
  var out=Object.assign({{}}, n, {{
    total:t, bpp:Math.round(b*100)/100, fraud:f, damaged:d,
    empty_box_qtd:eb, empty_box_bpp:Math.round(ebb*100)/100,
    pnr_qtd:pnr, pnr_bpp:Math.round(pnrb*100)/100,
    pct_fraud:pct, score:_nodScore(f,b,eb,pnr,parados,pct),
  }});
  return out;
}}

function _nodDadosPeriodo(){{
  var rng=_nodPeriodRange(_nodPeriod);
  if(!rng.de) return NODOS_DATA;
  return NODOS_DATA.map(function(n){{ return _nodApplyPeriodo(n, rng.de, rng.ate); }});
}}

window.nodPeriodo=function(p, btn){{
  _nodPeriod=p;
  document.querySelectorAll('#tab-nodos .lf-btn').forEach(function(b){{ b.classList.remove('lf-active'); }});
  if(btn) btn.classList.add('lf-active');
  filtrarNodos();
  nodRenderOfensores();
  if(_nodOfSel) nodRenderEvidencia();
}};

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
  var todosPeriodo=_nodDadosPeriodo();
  var kShps=document.getElementById('nod-kpi-shps');
  var kBpp=document.getElementById('nod-kpi-bpp');
  var kFraud=document.getElementById('nod-kpi-fraud');
  if(kShps) kShps.textContent=todosPeriodo.reduce(function(s,n){{return s+n.total;}},0).toLocaleString('pt-BR');
  if(kBpp) kBpp.textContent='US$ '+Math.round(todosPeriodo.reduce(function(s,n){{return s+n.bpp;}},0)).toLocaleString('pt-BR');
  if(kFraud) kFraud.textContent=todosPeriodo.reduce(function(s,n){{return s+n.fraud;}},0).toLocaleString('pt-BR');

  var q=((document.getElementById('nod-busca')||{{}}).value||'').toLowerCase().trim();
  var tp=(document.getElementById('nod-tipo')||{{}}).value||'';
  var fl=(document.getElementById('nod-filtro')||{{}}).value||'';
  var dados=todosPeriodo.filter(function(n){{
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

window.nodMostrarSub=function(nome,btn){{
  document.getElementById('nod-view-todos').style.display=(nome==='todos')?'':'none';
  document.getElementById('nod-view-ofensores').style.display=(nome==='ofensores')?'':'none';
  document.querySelectorAll('.nod-subtab').forEach(function(b){{b.style.borderBottomColor='transparent';b.style.color='#9ca3af';}});
  if(btn){{btn.style.borderBottomColor='#34d399';btn.style.color='#e5e7eb';}}
  if(nome==='ofensores') nodRenderOfensores();
}};

function _nodOfensoresLista(){{
  return _nodDadosPeriodo().filter(function(n){{
    return n.fraud>0||n.empty_box_qtd>0||n.pnr_qtd>0||n.parados_qtd>0;
  }});
}}

window.nodOfSort=function(key){{
  if(_nodOfSortKey===key) _nodOfSortDir=-_nodOfSortDir;
  else {{ _nodOfSortKey=key; _nodOfSortDir=(key==='nodo')?1:-1; }}
  nodRenderOfensores();
}};

window.nodOfFiltrar=function(){{
  _nodOfQ=(document.getElementById('nod-of-busca')||{{}}).value||'';
  nodRenderOfensores();
}};

function nodRenderOfensores(){{
  var todosPeriodo=_nodDadosPeriodo();
  var kFraud=document.getElementById('nod-of-kpi-fraud');
  var kEmpty=document.getElementById('nod-of-kpi-empty');
  var kPnr=document.getElementById('nod-of-kpi-pnr');
  var kParados=document.getElementById('nod-of-kpi-parados');
  if(kFraud) kFraud.textContent=todosPeriodo.reduce(function(s,n){{return s+n.fraud;}},0).toLocaleString('pt-BR');
  if(kEmpty) kEmpty.textContent=todosPeriodo.reduce(function(s,n){{return s+n.empty_box_qtd;}},0).toLocaleString('pt-BR');
  if(kPnr) kPnr.textContent=todosPeriodo.reduce(function(s,n){{return s+n.pnr_qtd;}},0).toLocaleString('pt-BR');
  if(kParados) kParados.textContent=todosPeriodo.reduce(function(s,n){{return s+n.parados_qtd;}},0).toLocaleString('pt-BR');

  var q=_nodOfQ.toLowerCase();
  var lista=_nodOfensoresLista();
  if(q) lista=lista.filter(function(n){{return n.nodo.toLowerCase().indexOf(q)>=0;}});
  var key=_nodOfSortKey, dir=_nodOfSortDir;
  lista=lista.slice().sort(function(a,b){{
    if(key==='nodo') return a.nodo.localeCompare(b.nodo)*dir;
    return ((a[key]||0)-(b[key]||0))*dir;
  }});
  var hdr=document.getElementById('nod-of-header');
  if(hdr) hdr.textContent='- '+lista.length.toLocaleString('pt-BR')+' nodo(s) com sinal de risco';
  var el=document.getElementById('nod-of-tbody');
  if(!el) return;
  el.innerHTML=lista.map(function(n){{
    var key=n.place_id+'|'+n.tipo;
    var sel=_nodOfSel===key;
    var hl=sel?'background:#0f2040;border-left:3px solid #38bdf8;':'border-left:3px solid transparent;';
    var tipoClr=TIPO_CLR[n.tipo]||'#9ca3af';
    var scoreCls=n.score>=40?'color:#f87171;font-weight:700':(n.score>=20?'color:#fbbf24;font-weight:700':'color:#6b7280');
    var idLbl=n.node_id?(n.tipo_lbl+' '+n.node_id):(n.place_id||'');
    var nomeFull=n.nodo+(idLbl?' ('+idLbl+')':'');
    return '<tr style="border-bottom:1px solid #080c18;'+hl+'cursor:pointer" onclick="nodOfSelecionar(\\''+key+'\\')">'
      +'<td style="padding:4px 8px;color:#34d399;font-weight:600;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+nomeFull+'">'+n.nodo+(idLbl?' <span style="color:#6b7280;font-weight:400;font-size:10px">('+idLbl+')</span>':'')+'</td>'
      +'<td style="padding:4px 8px;text-align:center"><span style="font-size:9px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,255,255,.06);color:'+tipoClr+'">'+n.tipo_lbl+'</span></td>'
      +'<td style="padding:4px 8px;text-align:center;'+scoreCls+'">'+n.score+'</td>'
      +'<td style="padding:4px 8px;text-align:center;color:'+(n.fraud>0?'#f87171':'#374151')+'">'+n.fraud+'</td>'
      +'<td style="padding:4px 8px;text-align:center;color:'+(n.empty_box_qtd>0?'#a78bfa':'#374151')+'">'+n.empty_box_qtd+'</td>'
      +'<td style="padding:4px 8px;text-align:center;color:'+(n.pnr_qtd>0?'#fbbf24':'#374151')+'">'+n.pnr_qtd+'</td>'
      +'<td style="padding:4px 8px;text-align:center;color:'+(n.parados_qtd>0?'#60a5fa':'#374151')+'">'+n.parados_qtd+'</td>'
      +'<td style="padding:4px 8px;text-align:right;color:#f87171;font-weight:700">US$ '+n.bpp.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</td>'
      +'</tr>';
  }}).join('');
}}

window.nodOfSelecionar=function(key){{
  _nodOfSel=(_nodOfSel===key)?null:key;
  nodRenderOfensores();
  nodRenderEvidencia();
}};

function nodRenderEvidencia(){{
  var hdr=document.getElementById('nod-ev-header');
  var el=document.getElementById('nod-ev-tbody');
  if(!el) return;
  if(!_nodOfSel){{
    if(hdr) hdr.textContent='- selecione um nodo acima';
    el.innerHTML='';
    return;
  }}
  var rng=_nodPeriodRange(_nodPeriod);
  var evBase=NOD_EV_DATA[_nodOfSel]||[];
  if(rng.de){{
    evBase=evBase.filter(function(e){{
      var p=(e.mes||'').split('-');
      var dt=p.length>=2?new Date(+p[0],+p[1]-1,1):null;
      return dt && dt>=rng.de && dt<=rng.ate;
    }});
  }}
  var ev=evBase.map(function(e){{
    return {{tipo:e.classe==='EMPTY BOX'?'Empty Box':'PNR', sid:e.sid, info:e.causa, extra:e.mes, valor:e.bpp}};
  }});
  var parados=(NOD_PARADOS_DATA[_nodOfSel]||[]).map(function(p){{
    var motivo=p.dias>=7?(p.dias+'d parado'):('venc. '+p.dias_vencido+'d');
    return {{tipo:'Parado', sid:p.sid, info:(p.status||'')+(p.substatus?(' / '+p.substatus):''), extra:motivo, valor:p.gmv}};
  }});
  var combinado=ev.concat(parados).sort(function(a,b){{return b.valor-a.valor;}});
  var nodo=NODOS_DATA.find(function(n){{return (n.place_id+'|'+n.tipo)===_nodOfSel;}});
  var nodoLbl=_nodOfSel;
  if(nodo){{
    var idLbl2=nodo.node_id?(nodo.tipo_lbl+' '+nodo.node_id):(nodo.place_id||'');
    nodoLbl=nodo.nodo+(idLbl2?' ('+idLbl2+')':'');
  }}
  if(hdr) hdr.textContent='- '+nodoLbl+' — '+combinado.length.toLocaleString('pt-BR')+' shipment(s)';
  var TIPO_BG={{'Empty Box':'background:rgba(167,139,250,.12);color:#a78bfa','PNR':'background:rgba(251,191,36,.12);color:#fbbf24','Parado':'background:rgba(96,165,250,.12);color:#60a5fa'}};
  el.innerHTML=combinado.length?combinado.map(function(e){{
    return '<tr style="border-bottom:1px solid #080c18">'
      +'<td style="padding:4px 8px"><span style="font-size:9px;font-weight:700;padding:2px 7px;border-radius:4px;'+(TIPO_BG[e.tipo]||'')+'">'+e.tipo+'</span></td>'
      +'<td style="padding:4px 8px"><a href="'+LOG_URL+e.sid+'" target="_blank" style="color:#38bdf8;font-family:monospace;font-size:11px;font-weight:600;text-decoration:none">'+e.sid+'</a></td>'
      +'<td style="padding:4px 8px;color:#9ca3af;font-size:10px">'+(e.info||'—')+'</td>'
      +'<td style="padding:4px 8px;text-align:center;color:#6b7280;font-size:10px">'+(e.extra||'—')+'</td>'
      +'<td style="padding:4px 8px;text-align:right;color:#f87171">$'+e.valor.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</td>'
      +'</tr>';
  }}).join('') : '<tr><td colspan="5" style="text-align:center;color:#6b7280;padding:14px">Nenhum shipment encontrado.</td></tr>';
}}

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
    nodos, evidencias, parados_por_nodo = carregar_dados()
    tab_html = gerar_tab_html(nodos, evidencias, parados_por_nodo)
    if injetar_no_fraude(tab_html, nodos, 'tab-nodos'):
        print(f'\nOK - Tab Nodos injetada com {len(nodos)} nodos')
    else:
        print('\nERRO - Falha na injecao')


if __name__ == '__main__':
    main()
