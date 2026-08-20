"""
sellers.py v1 — Aba Sellers do Dashboard SSP30
Sub-abas: Histórico | Damaged | Fraudes | Suspeitos
"""

import json, re
from datetime import datetime
from pathlib import Path
from google.cloud import bigquery
from google.auth import default

FACILITY = 'Guarulhos Mega'
INICIO   = '2026-01-01'
HTML_OUT = Path(__file__).parent / 'fraude.html'
LOG_URL  = 'https://shipping-bo.adminml.com/sauron/shipments/shipment/'
ANALISTA = 'Lucas de Oliveira Nascimento'
CANCEL_THRESHOLD = 20.0  # % cancelamento (60d) considerado "alto" por padrão

# ── Query 1: sellers agregados (todos) ──────────────────────────────────────
# CTE pré-deduplica por SHP antes de agregar
Q_SELLERS = f"""
WITH shps AS (
  SELECT
    CUS_NICKNAME_SEL                                                    AS seller,
    IFNULL(REPUTACION, 'N/A')                                           AS reputacao,
    SHIPMENT_ID,
    MAX(TIPO_FRAUDE)                                                    AS tipo_fraude,
    MAX(CLASSIFICATION_LM)                                              AS classification_lm,
    MAX(TIPO_DAMAGED_LG)                                                AS tipo_damaged_lg,
    MAX(DATE_BPP)                                                       AS date_bpp,
    MAX(BPP_CASHOUT_USD)                                                AS bpp_cashout_usd,
    MAX(TOTALGMV)                                                       AS totalgmv
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = '{FACILITY}'
    AND DATE_BPP >= '{INICIO}'
    AND DATE_BPP <= CURRENT_DATE()
    AND CUS_NICKNAME_SEL IS NOT NULL
    AND CUS_NICKNAME_SEL != ''
  GROUP BY 1, 2, 3
)
SELECT
  seller,
  reputacao,
  COUNT(*)                                                              AS total,
  ROUND(SUM(IFNULL(bpp_cashout_usd, 0)), 2)                            AS bpp,
  ROUND(SUM(IFNULL(totalgmv, 0)), 2)                                   AS gmv,
  COUNTIF(tipo_fraude LIKE 'FRAUDE SELLER%')                           AS n_fraude,
  COUNTIF(
    classification_lm LIKE 'DAMAGED%'
    OR tipo_damaged_lg IN (
      'DAMAGED','damaged_svc','damaged_on_route','damaged_seller','damaged','SELLER'
    )
  )                                                                     AS n_damaged,
  COUNTIF(classification_lm = 'PNR C')                                 AS n_pnr,
  COUNTIF(classification_lm = 'EMPTY BOX')                             AS n_empty,
  MIN(FORMAT_DATE('%Y-%m', date_bpp))                                  AS primeiro_mes,
  MAX(FORMAT_DATE('%Y-%m', date_bpp))                                  AS ultimo_mes,
  COUNT(DISTINCT FORMAT_DATE('%Y-%m', date_bpp))                       AS n_meses
FROM shps
GROUP BY 1, 2
ORDER BY bpp DESC
"""

# ── Query 4: taxa de cancelamento por seller (60 dias, via BT_SHP_SHIPMENTS) ─
# DM_LP_MELI_OPTIMIZADO não tem status de pedido — mapeia nickname -> seller_id
# numérico (SHP_SENDER_ID) via SHIPMENT_ID, depois calcula cancelamento 60d.
# Filtro de partição em SHP_DATE_CREATED_ID é obrigatório (senão escaneia a
# tabela inteira — testado: 924s sem filtro vs poucos minutos com filtro).
Q_CANCELAMENTO = f"""
WITH seller_map AS (
  SELECT
    lp.CUS_NICKNAME_SEL AS nickname,
    s.SHP_SENDER_ID      AS seller_id,
    COUNT(*)              AS n
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO` lp
  INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` s
    ON CAST(s.SHP_SHIPMENT_ID AS STRING) = lp.SHIPMENT_ID
    AND s.SHP_DATE_CREATED_ID >= '{INICIO}'
  WHERE lp.SHP_LG_FACILITY_NAME = '{FACILITY}'
    AND lp.DATE_BPP >= '{INICIO}'
    AND lp.CUS_NICKNAME_SEL IS NOT NULL
  GROUP BY 1, 2
),
seller_best AS (
  SELECT nickname, seller_id FROM (
    SELECT nickname, seller_id, ROW_NUMBER() OVER (PARTITION BY nickname ORDER BY n DESC) AS rn
    FROM seller_map
  ) WHERE rn = 1
)
SELECT
  sb.nickname                                       AS seller,
  COUNT(*)                                           AS total_60d,
  COUNTIF(s.SHP_STATUS_ID = 'cancelled')             AS cancel_60d
FROM seller_best sb
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` s
  ON s.SHP_SENDER_ID = sb.seller_id
  AND s.SHP_DATE_CREATED_ID >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
GROUP BY 1
"""

# ── Query 2: shipments de fraude seller (1 linha por SHP) ───────────────────
Q_FRAUDE = f"""
SELECT
  CUS_NICKNAME_SEL                                   AS seller,
  CAST(SHIPMENT_ID AS STRING)                        AS sid,
  MAX(IFNULL(CLASSIFICATION_LM, ''))                 AS causa,
  MAX(IFNULL(TIPO_FRAUDE, ''))                       AS tf,
  MAX(CLAIM_ID)                                      AS claim_id,
  FORMAT_DATE('%Y-%m', MAX(DATE_BPP))                AS mes,
  ROUND(MAX(IFNULL(BPP_CASHOUT_USD, 0)), 2)          AS bpp
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = '{FACILITY}'
  AND DATE_BPP >= '{INICIO}'
  AND DATE_BPP <= CURRENT_DATE()
  AND CUS_NICKNAME_SEL IS NOT NULL
  AND TIPO_FRAUDE LIKE 'FRAUDE SELLER%'
GROUP BY 1, 2
ORDER BY bpp DESC
"""

# ── Query 3: shipments de damaged com seller (1 linha por SHP, top 3000) ────
Q_DAMAGED = f"""
SELECT
  CUS_NICKNAME_SEL                                   AS seller,
  CAST(SHIPMENT_ID AS STRING)                        AS sid,
  MAX(IFNULL(CLASSIFICATION_LM, ''))                 AS causa,
  MAX(IFNULL(TIPO_DAMAGED_LG, ''))                   AS td,
  MAX(CLAIM_ID)                                      AS claim_id,
  FORMAT_DATE('%Y-%m', MAX(DATE_BPP))                AS mes,
  ROUND(MAX(IFNULL(BPP_CASHOUT_USD, 0)), 2)          AS bpp
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = '{FACILITY}'
  AND DATE_BPP >= '{INICIO}'
  AND DATE_BPP <= CURRENT_DATE()
  AND CUS_NICKNAME_SEL IS NOT NULL
  AND (
    CLASSIFICATION_LM LIKE 'DAMAGED%'
    OR TIPO_DAMAGED_LG IN (
      'DAMAGED','damaged_svc','damaged_on_route','damaged_seller','damaged','SELLER'
    )
  )
GROUP BY 1, 2
ORDER BY bpp DESC
LIMIT 3000
"""


def carregar():
    creds, _ = default()
    client = bigquery.Client(credentials=creds, project='meli-bi-data')

    print('Consultando sellers agregados...')
    sellers = []
    for r in client.query(Q_SELLERS).result():
        sellers.append({
            'n': r['seller'],
            'r': r['reputacao'],
            't': int(r['total']),
            'b': float(r['bpp'] or 0),
            'g': float(r['gmv'] or 0),
            'f': int(r['n_fraude']),
            'd': int(r['n_damaged']),
            'pnr': int(r['n_pnr']),
            'eb': int(r['n_empty']),
            'p': r['primeiro_mes'] or '',
            'u': r['ultimo_mes'] or '',
            'm': int(r['n_meses']),
        })
    print(f'  {len(sellers):,} sellers')

    print('Consultando taxa de cancelamento (60 dias)...')
    cancel_map = {}
    for r in client.query(Q_CANCELAMENTO).result():
        total_60d  = int(r['total_60d'])
        cancel_60d = int(r['cancel_60d'])
        cancel_map[r['seller']] = {
            't60': total_60d,
            'c60': cancel_60d,
            'pc':  round(cancel_60d / total_60d * 100, 1) if total_60d else 0.0,
        }
    print(f'  {len(cancel_map):,} sellers com dado de cancelamento')
    for s in sellers:
        cm = cancel_map.get(s['n'])
        s['t60'] = cm['t60'] if cm else 0
        s['c60'] = cm['c60'] if cm else 0
        s['pc']  = cm['pc']  if cm else 0.0

    print('Consultando shipments de fraude...')
    shps_fraude = []
    for r in client.query(Q_FRAUDE).result():
        shps_fraude.append({
            'n': r['seller'],
            's': r['sid'],
            'c': r['causa'],
            'tf': r['tf'],
            'cl': r['claim_id'] or '',
            'mes': r['mes'],
            'b': float(r['bpp']),
        })
    print(f'  {len(shps_fraude):,} SHPs fraude')

    print('Consultando shipments de damaged...')
    shps_damaged = []
    for r in client.query(Q_DAMAGED).result():
        shps_damaged.append({
            'n': r['seller'],
            's': r['sid'],
            'c': r['causa'],
            'td': r['td'],
            'cl': r['claim_id'] or '',
            'mes': r['mes'],
            'b': float(r['bpp']),
        })
    print(f'  {len(shps_damaged):,} SHPs damaged')

    return sellers, shps_fraude, shps_damaged


PNR_EB_MIN_RECORRENCIA = 2  # 1 incidente isolado nao e padrao (validado em caso real 2026-08-20)


def static_kpis(sellers, shps_fraude, shps_damaged):
    total_bpp = sum(s['b'] for s in sellers)
    untrusted = [s for s in sellers if s['r'] in ('SELLER NOT TRUSTED', 'BOTH NOT TRUSTED')]
    suspeitos = [
        s for s in sellers
        if s['f'] > 0
        or s['pnr'] >= PNR_EB_MIN_RECORRENCIA
        or s['eb'] >= PNR_EB_MIN_RECORRENCIA
        or s['pc'] >= CANCEL_THRESHOLD
    ]
    return {
        'sellers':     len(sellers),
        'bpp':         round(total_bpp, 2),
        'fraude_sel':  len(shps_fraude),
        'damaged_sel': len(shps_damaged),
        'untrusted':   len(untrusted),
        'suspeitos':   len(suspeitos),
    }


def gerar_tab(sellers, shps_fraude, shps_damaged, kpis):
    now           = datetime.now().strftime('%d/%m/%Y %H:%M')
    sellers_json  = json.dumps(sellers,      ensure_ascii=False)
    fraude_json   = json.dumps(shps_fraude,  ensure_ascii=False)
    damaged_json  = json.dumps(shps_damaged, ensure_ascii=False)

    return f"""<div id="tab-sellers" class="content">
<div style="padding:20px 32px">

  <!-- HEADER -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:16px;font-weight:700;color:#f9fafb">Sellers — Análise de Risco</div>
      <div style="font-size:11px;color:#6b7280;margin-top:2px">SSP30 · Guarulhos Mega · desde Jan/2026 · atualizado {now}</div>
    </div>
    <button onclick="selExportCSV()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:5px 14px;font-size:11px;cursor:pointer">⬇ Exportar CSV</button>
  </div>

  <!-- PERÍODO -->
  <div style="display:flex;gap:8px;margin-bottom:14px;align-items:center;flex-wrap:wrap;background:#0d1321;border:1px solid #1f2937;border-radius:8px;padding:10px 16px">
    <span style="font-size:11px;color:#6b7280;font-weight:600">Período:</span>
    <input id="sel-de" type="month" onchange="selAplicar()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 8px;font-size:11px">
    <span style="font-size:11px;color:#4b5563">até</span>
    <input id="sel-ate" type="month" onchange="selAplicar()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 8px;font-size:11px">
    <button onclick="selLimpar()" style="background:#1f2937;color:#6b7280;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:10px;cursor:pointer">Limpar</button>
    <span id="sel-periodo-label" style="font-size:10px;color:#38bdf8;margin-left:4px"></span>
  </div>

  <!-- KPIs -->
  <div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap">
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:10px 18px;flex:1;min-width:88px;text-align:center">
      <div id="sel-k-sellers" style="font-size:22px;font-weight:700;color:#38bdf8">{kpis['sellers']:,}</div>
      <div style="font-size:9px;color:#6b7280;margin-top:2px">Sellers</div>
    </div>
    <div style="background:#1a0505;border:1px solid #7f1d1d;border-radius:8px;padding:10px 18px;flex:1;min-width:88px;text-align:center">
      <div id="sel-k-bpp" style="font-size:22px;font-weight:700;color:#f87171">US$ {kpis['bpp']:,.0f}</div>
      <div style="font-size:9px;color:#6b7280;margin-top:2px">BPP Total</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:10px 18px;flex:1;min-width:88px;text-align:center">
      <div id="sel-k-fraude" style="font-size:22px;font-weight:700;color:#fbbf24">{kpis['fraude_sel']:,}</div>
      <div style="font-size:9px;color:#6b7280;margin-top:2px">SHPs Fraude Seller</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:10px 18px;flex:1;min-width:88px;text-align:center">
      <div id="sel-k-damaged" style="font-size:22px;font-weight:700;color:#a78bfa">{kpis['damaged_sel']:,}</div>
      <div style="font-size:9px;color:#6b7280;margin-top:2px">SHPs Damaged</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:10px 18px;flex:1;min-width:88px;text-align:center">
      <div id="sel-k-untrusted" style="font-size:22px;font-weight:700;color:#f97316">{kpis['untrusted']:,}</div>
      <div style="font-size:9px;color:#6b7280;margin-top:2px">Reputação NOT TRUSTED</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:10px 18px;flex:1;min-width:88px;text-align:center">
      <div id="sel-k-suspeitos" style="font-size:22px;font-weight:700;color:#ef4444">{kpis['suspeitos']:,}</div>
      <div style="font-size:9px;color:#6b7280;margin-top:2px">Suspeitos</div>
    </div>
  </div>

  <!-- SUB-ABAS -->
  <div style="display:flex;gap:4px;margin-bottom:14px;border-bottom:1px solid #1f2937;padding-bottom:0">
    <button id="seltab-historico" onclick="selTab('historico')"
      style="background:#1e3a5f;color:#38bdf8;border:none;border-radius:6px 6px 0 0;padding:7px 16px;font-size:11px;font-weight:600;cursor:pointer;border-bottom:2px solid #38bdf8">
      Historico
    </button>
    <button id="seltab-damaged" onclick="selTab('damaged')"
      style="background:transparent;color:#6b7280;border:none;border-radius:6px 6px 0 0;padding:7px 16px;font-size:11px;font-weight:600;cursor:pointer;border-bottom:2px solid transparent">
      Damaged
    </button>
    <button id="seltab-fraudes" onclick="selTab('fraudes')"
      style="background:transparent;color:#6b7280;border:none;border-radius:6px 6px 0 0;padding:7px 16px;font-size:11px;font-weight:600;cursor:pointer;border-bottom:2px solid transparent">
      Fraudes
    </button>
    <button id="seltab-suspeitos" onclick="selTab('suspeitos')"
      style="background:transparent;color:#6b7280;border:none;border-radius:6px 6px 0 0;padding:7px 16px;font-size:11px;font-weight:600;cursor:pointer;border-bottom:2px solid transparent">
      Suspeitos
    </button>
  </div>

  <!-- HISTORICO -->
  <div id="selc-historico">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 Sellers - BPP Total <span style="font-weight:400;color:#374151">clique para filtrar</span></div>
        <div style="position:relative;height:240px"><canvas id="sel-cht-hist-bpp"></canvas></div>
      </div>
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 Sellers - Qtd SHPs</div>
        <div style="position:relative;height:240px"><canvas id="sel-cht-hist-shp"></canvas></div>
      </div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:6px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Sellers</span>
        <span id="sel-hist-count" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
      <div style="display:flex;gap:6px">
        <input id="sel-hist-busca" type="text" placeholder="Buscar seller..."
          oninput="selHistFiltrar()"
          style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
        <button onclick="selHistClear()" style="background:#1f2937;color:#6b7280;border:1px solid #374151;border-radius:5px;padding:4px 9px;font-size:11px;cursor:pointer">X</button>
      </div>
    </div>
    <div style="border:1px solid #1f2937;border-radius:8px;overflow:hidden;margin-bottom:14px">
      <div style="overflow-y:auto;max-height:340px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#4b5563;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">#</th>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Seller</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Reputacao</th>
              <th style="padding:6px 8px;text-align:center;color:#e5e7eb;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Total</th>
              <th style="padding:6px 8px;text-align:center;color:#a78bfa;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Damaged</th>
              <th style="padding:6px 8px;text-align:center;color:#fbbf24;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Fraude</th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Meses</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">BPP</th>
            </tr>
          </thead>
          <tbody id="sel-hist-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- DAMAGED -->
  <div id="selc-damaged" style="display:none">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 Sellers - Casos Damaged <span style="font-weight:400;color:#374151">clique para filtrar</span></div>
        <div style="position:relative;height:240px"><canvas id="sel-cht-dmg-rank"></canvas></div>
      </div>
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Tipos de Damaged</div>
        <div style="position:relative;height:240px"><canvas id="sel-cht-dmg-tipo"></canvas></div>
      </div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Shipments Damaged</span>
        <span id="sel-dmg-header" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <button id="sel-dmg-report-btn" onclick="selGerarRelatorioDamaged()" style="display:none;background:rgba(37,99,235,.12);border:1px solid rgba(37,99,235,.35);color:#93c5fd;font-size:11px;padding:5px 12px;border-radius:5px;cursor:pointer;font-family:inherit;white-space:nowrap">&#128196; Gerar relatório</button>
        <input id="sel-dmg-busca" type="text" placeholder="Buscar seller ou SHP..."
          oninput="selDmgFiltrar()"
          style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
      </div>
    </div>
    <div style="border:1px solid #1f2937;border-radius:8px;overflow:hidden">
      <div style="overflow-y:auto;max-height:360px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Seller</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Shipment ID</th>
              <th style="padding:6px 8px;text-align:left;color:#a78bfa;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Classificacao</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Tipo Damaged</th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Mes</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">BPP</th>
            </tr>
          </thead>
          <tbody id="sel-dmg-tbody"></tbody>
        </table>
      </div>
    </div>
    <div id="sel-dmg-note" style="font-size:10px;color:#374151;margin-top:5px;text-align:right"></div>
  </div>

  <!-- FRAUDES -->
  <div id="selc-fraudes" style="display:none">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 Sellers - Fraudes <span style="font-weight:400;color:#374151">clique para filtrar</span></div>
        <div style="position:relative;height:240px"><canvas id="sel-cht-fr-rank"></canvas></div>
      </div>
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Tipos de Fraude Seller</div>
        <div style="position:relative;height:240px"><canvas id="sel-cht-fr-tipo"></canvas></div>
      </div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Shipments Fraude Seller</span>
        <span id="sel-fr-header" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <button id="sel-fr-report-btn" onclick="selGerarRelatorioFraude()" style="display:none;background:rgba(37,99,235,.12);border:1px solid rgba(37,99,235,.35);color:#93c5fd;font-size:11px;padding:5px 12px;border-radius:5px;cursor:pointer;font-family:inherit;white-space:nowrap">&#128196; Gerar relatório</button>
        <input id="sel-fr-busca" type="text" placeholder="Buscar seller ou SHP..."
          oninput="selFrFiltrar()"
          style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
      </div>
    </div>
    <div style="border:1px solid #1f2937;border-radius:8px;overflow:hidden">
      <div style="overflow-y:auto;max-height:360px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Seller</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Shipment ID</th>
              <th style="padding:6px 8px;text-align:left;color:#fbbf24;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Tipo Fraude</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Classificacao</th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Mes</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">BPP</th>
            </tr>
          </thead>
          <tbody id="sel-fr-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- SUSPEITOS -->
  <div id="selc-suspeitos" style="display:none">
    <div style="background:#1a0a00;border:1px solid #78350f;border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:11px;color:#d97706">
      <b>Sinais de suspeicao (qualquer um marca o seller):</b>
      Historico fraudulento (Fraude Seller &gt; 0)
      <b> · </b> Pacote divergente (PNR C &ge; {PNR_EB_MIN_RECORRENCIA} casos — 1 caso isolado nao conta)
      <b> · </b> Caixa vazia (Empty Box &ge; {PNR_EB_MIN_RECORRENCIA} casos)
      <b> · </b> Alta taxa de cancelamento (&ge;
      <input id="sel-susp-cancel-min" type="number" value="{CANCEL_THRESHOLD:.0f}" min="0" max="100"
        onchange="selSuspFiltrar()"
        style="width:44px;background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:4px;padding:1px 4px;font-size:11px;text-align:center">
      % em 60 dias)
    </div>

    <!-- Overview status cards -->
    <div id="sel-susp-overview" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center">
      <div style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:6px;padding:7px 14px;text-align:center;min-width:80px">
        <div style="font-size:18px;font-weight:700;color:#f87171" id="sel-susp-ov-blq">0</div>
        <div style="font-size:10px;color:#9ca3af">Bloqueados</div>
      </div>
      <div style="background:rgba(251,191,36,.08);border:1px solid rgba(251,191,36,.25);border-radius:6px;padding:7px 14px;text-align:center;min-width:80px">
        <div style="font-size:18px;font-weight:700;color:#fbbf24" id="sel-susp-ov-sol">0</div>
        <div style="font-size:10px;color:#9ca3af">Solicitados</div>
      </div>
      <div style="background:rgba(156,163,175,.06);border:1px solid rgba(156,163,175,.15);border-radius:6px;padding:7px 14px;text-align:center;min-width:80px">
        <div style="font-size:18px;font-weight:700;color:#9ca3af" id="sel-susp-ov-ati">0</div>
        <div style="font-size:10px;color:#9ca3af">Ativos</div>
      </div>
    </div>

    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px;margin-bottom:14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 Suspeitos - BPP <span style="font-weight:400;color:#374151">clique para filtrar</span></div>
      <div style="position:relative;height:220px"><canvas id="sel-cht-susp"></canvas></div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:6px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Sellers Suspeitos</span>
        <span id="sel-susp-count" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
      <div style="display:flex;gap:6px;align-items:center">
        <select id="sel-susp-status" onchange="selSuspFiltrar()"
          style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 8px;font-size:11px">
          <option value="">Status: Todos</option>
          <option value="ati">Ativo</option>
          <option value="sol">Solicitado</option>
          <option value="blq">Bloqueado</option>
        </select>
        <input id="sel-susp-busca" type="text" placeholder="Buscar seller..."
          oninput="selSuspFiltrar()"
          style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
      </div>
    </div>
    <div style="border:1px solid #78350f;border-radius:8px;overflow:hidden">
      <div style="overflow-y:auto;max-height:360px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#4b5563;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">#</th>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Seller</th>
              <th style="padding:6px 8px;text-align:center;color:#e5e7eb;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Total SHPs</th>
              <th style="padding:6px 8px;text-align:center;color:#fbbf24;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Historico Fraudulento</th>
              <th style="padding:6px 8px;text-align:center;color:#f472b6;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Pacote Divergente (PNR C)</th>
              <th style="padding:6px 8px;text-align:center;color:#a78bfa;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Caixa Vazia</th>
              <th style="padding:6px 8px;text-align:center;color:#fb923c;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">% Cancelamento (60d)</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">BPP</th>
              <th style="padding:6px 8px;text-align:left;color:#9ca3af;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Status</th>
              <th style="padding:6px 8px;text-align:left;color:#93c5fd;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">PDF</th>
            </tr>
          </thead>
          <tbody id="sel-susp-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

</div>
<script>
(function(){{
var SEL_DATA   = {sellers_json};
var SHP_FRAUDE = {fraude_json};
var SHP_DAMAGE = {damaged_json};
var LOG_URL    = '{LOG_URL}';
var SEL_ANALISTA = '{ANALISTA}';

var _abaAtual = 'historico';
var _selSel   = null;
var _selSusp  = null;
var _qHist='', _qDmg='', _qFr='', _qSusp='';

var _sellers  = SEL_DATA;
var _fraudes  = SHP_FRAUDE;
var _damages  = SHP_DAMAGE;

function repBadge(r){{
  var cfg = {{
    'TRUSTED':           {{c:'#34d399',bg:'rgba(52,211,153,.12)'}},
    'SELLER NOT TRUSTED':{{c:'#ef4444',bg:'rgba(239,68,68,.12)'}},
    'BOTH NOT TRUSTED':  {{c:'#f97316',bg:'rgba(249,115,22,.12)'}},
    'BUYER NOT TRUSTED': {{c:'#fbbf24',bg:'rgba(251,191,36,.12)'}},
  }};
  var s = cfg[r] || {{c:'#6b7280',bg:'rgba(107,114,128,.10)'}};
  return '<span style="color:'+s.c+';background:'+s.bg+';border-radius:4px;padding:1px 7px;font-size:9px;font-weight:600;white-space:nowrap">'+r+'</span>';
}}

function mkChart(id, labels, data, color, onClick){{
  var cv = document.getElementById(id); if(!cv) return null;
  var ex = Chart.getChart(cv); if(ex) ex.destroy();
  return new Chart(cv, {{
    type: 'bar',
    data: {{labels: labels, datasets: [{{data: data, backgroundColor: color, borderRadius: 3, barThickness: 16}}]}},
    options: {{
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      onClick: onClick || undefined,
      plugins: {{legend: {{display: false}}, tooltip: {{callbacks: {{label: function(c){{return c.raw;}}}}}}}},
      scales: {{
        x: {{ticks: {{color:'#6b7280', font:{{size:9}}}}, grid: {{color:'#111827'}}}},
        y: {{ticks: {{color:'#9ca3af', font:{{size:9}}}}, grid: {{display:false}}}}
      }}
    }}
  }});
}}

function selCancelMin(){{
  var v = parseFloat((document.getElementById('sel-susp-cancel-min') || {{}}).value);
  return isNaN(v) ? {CANCEL_THRESHOLD} : v;
}}

var PNR_EB_MIN = {PNR_EB_MIN_RECORRENCIA};

function isSuspeito(s){{
  return s.f > 0 || s.pnr >= PNR_EB_MIN || s.eb >= PNR_EB_MIN || s.pc >= selCancelMin();
}}

// ── Fluxo de solicitação de bloqueio (status persistido no browser) ────────
function selGetSt(nick){{
  var valid = {{ati:1,sol:1,blq:1}};
  try {{
    var s = localStorage.getItem('sel_st_'+nick);
    if (s !== null) return valid[s] ? s : 'ati';
  }} catch(e) {{}}
  return 'ati';
}}
function selToggleSt(nick){{
  var cycle = ['ati','sol','blq'];
  var cur = selGetSt(nick);
  var next = cycle[(cycle.indexOf(cur)+1) % cycle.length];
  try {{ localStorage.setItem('sel_st_'+nick, next); }} catch(e) {{}}
  var susp = _sellers.filter(isSuspeito).sort(function(a,b){{return b.b - a.b;}});
  renderSuspTbody(susp);
  selUpdSuspOverview();
}}
function selUpdSuspOverview(){{
  var all = SEL_DATA.filter(isSuspeito);
  var ov = {{ati:0,sol:0,blq:0}};
  all.forEach(function(s){{ var st = selGetSt(s.n); if(ov[st]!==undefined) ov[st]++; else ov.ati++; }});
  ['ati','sol','blq'].forEach(function(k){{
    var el = document.getElementById('sel-susp-ov-'+k); if(el) el.textContent = ov[k];
  }});
}}
window.selGetSt = selGetSt;
window.selToggleSt = selToggleSt;

// HISTORICO
function renderHistCharts(){{
  var top10b = _sellers.slice(0, 10);
  var top10s = [..._sellers].sort(function(a,b){{return b.t - a.t;}}).slice(0, 10);
  var nb = top10b.map(function(s){{return s.n;}});
  mkChart('sel-cht-hist-bpp', nb, top10b.map(function(s){{return s.b;}}), '#ef4444',
    function(e,els){{if(els.length) selHistSel(nb[els[0].index]);}});
  mkChart('sel-cht-hist-shp', top10s.map(function(s){{return s.n;}}),
    top10s.map(function(s){{return s.t;}}), '#6366f1', null);
}}

function renderHistTbody(){{
  var q = _qHist.toLowerCase();
  var lista = _sellers.filter(function(s){{return !q || s.n.toLowerCase().indexOf(q) >= 0;}});
  var ct = document.getElementById('sel-hist-count');
  if(ct) ct.textContent = '(' + lista.length.toLocaleString('pt-BR') + ' sellers)';
  var el = document.getElementById('sel-hist-tbody'); if(!el) return;
  el.innerHTML = lista.slice(0, 1000).map(function(s, i){{
    var sel = _selSel === s.n;
    var hl = sel ? 'background:#0f2040;border-left:3px solid #38bdf8;' : 'border-left:3px solid transparent;';
    return '<tr data-nick="'+s.n+'" class="sel-hist-row" style="border-bottom:1px solid #0a0f1e;cursor:pointer;'+hl+'">'
      + '<td style="padding:4px 8px;color:#374151;font-size:9px">'+(i+1)+'</td>'
      + '<td style="padding:4px 8px;color:#60a5fa;font-size:10px;font-weight:700;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+s.n+'">'+s.n+'</td>'
      + '<td style="padding:4px 8px">'+repBadge(s.r)+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#e5e7eb;font-weight:700">'+s.t+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#a78bfa">'+s.d+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#fbbf24">'+s.f+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#4b5563">'+s.m+'</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#f87171;font-size:10px">$'+s.b.toFixed(0)+'</td>'
      + '</tr>';
  }}).join('');
  if(lista.length > 1000){{
    el.innerHTML += '<tr><td colspan="8" style="padding:8px;text-align:center;color:#374151;font-size:10px">Mostrando 1.000 de '+lista.length.toLocaleString('pt-BR')+'. Use o filtro para refinar.</td></tr>';
  }}
}}

function selHistSel(nick){{
  _selSel = (_selSel === nick) ? null : nick;
  renderHistTbody();
}}

window.selHistFiltrar = function(){{
  _qHist = (document.getElementById('sel-hist-busca') || {{}}).value || '';
  renderHistTbody();
}};
window.selHistClear = function(){{
  _selSel = null; _qHist = '';
  var b = document.getElementById('sel-hist-busca'); if(b) b.value = '';
  renderHistTbody();
}};

// DAMAGED
function renderDmgCharts(){{
  var byS = {{}};
  _damages.forEach(function(s){{ byS[s.n] = (byS[s.n] || 0) + 1; }});
  var top10 = Object.entries(byS).sort(function(a,b){{return b[1]-a[1];}}).slice(0,10);
  var nicks = top10.map(function(x){{return x[0];}});
  mkChart('sel-cht-dmg-rank', nicks, top10.map(function(x){{return x[1];}}), '#a78bfa',
    function(e,els){{if(els.length){{_selSel=nicks[els[0].index];renderDmgTbody();}}}});
  var byT = {{}};
  _damages.forEach(function(s){{ var k = s.td || s.c || 'Outro'; byT[k] = (byT[k] || 0) + 1; }});
  var topT = Object.entries(byT).sort(function(a,b){{return b[1]-a[1];}}).slice(0,8);
  mkChart('sel-cht-dmg-tipo', topT.map(function(x){{return x[0];}}), topT.map(function(x){{return x[1];}}), '#6366f1', null);
}}

function renderDmgTbody(){{
  var q = _qDmg.toLowerCase();
  var base = _selSel ? _damages.filter(function(s){{return s.n === _selSel;}}) : _damages;
  var filtrado = q ? base.filter(function(s){{return s.n.toLowerCase().indexOf(q)>=0||s.s.indexOf(q)>=0;}}) : base;
  var hdr = document.getElementById('sel-dmg-header');
  if(hdr) hdr.textContent = _selSel
    ? '- ' + _selSel + ' (' + filtrado.length + ' SHPs)'
    : '- ' + filtrado.length.toLocaleString('pt-BR') + ' shipments (top 3000 por BPP)';
  var note = document.getElementById('sel-dmg-note');
  if(note) note.textContent = filtrado.length > 2000 ? 'Mostrando 2.000 primeiros. Filtre por seller para ver todos.' : '';
  var btn = document.getElementById('sel-dmg-report-btn');
  if(btn) btn.style.display = _selSel ? '' : 'none';
  var el = document.getElementById('sel-dmg-tbody'); if(!el) return;
  el.innerHTML = filtrado.slice(0, 2000).map(function(s){{
    return '<tr style="border-bottom:1px solid #080c18">'
      + '<td style="padding:4px 8px;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><a href="https://www.mercadolivre.com.br/loja/'+s.n+'" target="_blank" style="color:#60a5fa;font-size:10px;font-weight:600;text-decoration:none" title="'+s.n+'">'+s.n+'</a></td>'
      + '<td style="padding:4px 8px"><a href="'+LOG_URL+s.s+'" target="_blank" style="color:#38bdf8;font-family:monospace;font-size:10px;font-weight:600;text-decoration:none">'+s.s+'</a></td>'
      + '<td style="padding:4px 8px;font-size:10px;color:#a78bfa">'+s.c+'</td>'
      + '<td style="padding:4px 8px;color:#6b7280;font-size:10px">'+s.td+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#4b5563;font-size:10px">'+s.mes+'</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#f87171;font-size:10px">$'+s.b.toFixed(2)+'</td>'
      + '</tr>';
  }}).join('');
}}

window.selDmgFiltrar = function(){{
  _qDmg = (document.getElementById('sel-dmg-busca') || {{}}).value || '';
  renderDmgTbody();
}};

// FRAUDES
function renderFrCharts(){{
  var byS = {{}};
  _fraudes.forEach(function(s){{ byS[s.n] = (byS[s.n] || 0) + 1; }});
  var top10 = Object.entries(byS).sort(function(a,b){{return b[1]-a[1];}}).slice(0,10);
  var nicks = top10.map(function(x){{return x[0];}});
  mkChart('sel-cht-fr-rank', nicks, top10.map(function(x){{return x[1];}}), '#ef4444',
    function(e,els){{if(els.length){{_selSel=nicks[els[0].index];renderFrTbody();}}}});
  var byT = {{}};
  _fraudes.forEach(function(s){{ var k = s.tf || 'Outro'; byT[k] = (byT[k] || 0) + 1; }});
  var topT = Object.entries(byT).sort(function(a,b){{return b[1]-a[1];}}).slice(0,6);
  mkChart('sel-cht-fr-tipo', topT.map(function(x){{return x[0];}}), topT.map(function(x){{return x[1];}}), '#fbbf24', null);
}}

function renderFrTbody(){{
  var q = _qFr.toLowerCase();
  var base = _selSel ? _fraudes.filter(function(s){{return s.n === _selSel;}}) : _fraudes;
  var filtrado = q ? base.filter(function(s){{return s.n.toLowerCase().indexOf(q)>=0||s.s.indexOf(q)>=0;}}) : base;
  var hdr = document.getElementById('sel-fr-header');
  if(hdr) hdr.textContent = _selSel
    ? '- ' + _selSel + ' (' + filtrado.length + ' SHPs)'
    : '- ' + filtrado.length.toLocaleString('pt-BR') + ' shipments';
  var btn = document.getElementById('sel-fr-report-btn');
  if(btn) btn.style.display = _selSel ? '' : 'none';
  var el = document.getElementById('sel-fr-tbody'); if(!el) return;
  el.innerHTML = filtrado.map(function(s){{
    return '<tr style="border-bottom:1px solid #080c18">'
      + '<td style="padding:4px 8px;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><a href="https://www.mercadolivre.com.br/loja/'+s.n+'" target="_blank" style="color:#60a5fa;font-size:10px;font-weight:600;text-decoration:none" title="'+s.n+'">'+s.n+'</a></td>'
      + '<td style="padding:4px 8px"><a href="'+LOG_URL+s.s+'" target="_blank" style="color:#38bdf8;font-family:monospace;font-size:10px;font-weight:600;text-decoration:none">'+s.s+'</a></td>'
      + '<td style="padding:4px 8px;font-size:10px;color:#fbbf24;font-weight:600">'+s.tf+'</td>'
      + '<td style="padding:4px 8px;color:#6b7280;font-size:10px">'+s.c+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#4b5563;font-size:10px">'+s.mes+'</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#f87171;font-size:10px">$'+s.b.toFixed(2)+'</td>'
      + '</tr>';
  }}).join('');
}}

window.selFrFiltrar = function(){{
  _qFr = (document.getElementById('sel-fr-busca') || {{}}).value || '';
  renderFrTbody();
}};

// SUSPEITOS
function renderSuspCharts(lista){{
  var top10 = lista.slice(0, 10);
  var nicks = top10.map(function(s){{return s.n;}});
  mkChart('sel-cht-susp', nicks, top10.map(function(s){{return s.b;}}), '#f97316',
    function(e,els){{if(els.length){{_selSusp=nicks[els[0].index];renderSuspTbody(lista);}}}});
}}

function renderSuspTbody(lista){{
  var q = _qSusp.toLowerCase();
  var stF = (document.getElementById('sel-susp-status') || {{}}).value || '';
  var filtrado = lista.filter(function(s){{
    if (stF && selGetSt(s.n) !== stF) return false;
    return !q || s.n.toLowerCase().indexOf(q) >= 0;
  }});
  var ct = document.getElementById('sel-susp-count');
  if(ct) ct.textContent = '(' + filtrado.length.toLocaleString('pt-BR') + ' sellers)';
  var el = document.getElementById('sel-susp-tbody'); if(!el) return;
  var ST_LBL = {{ati:'Ativo',sol:'Solicitado',blq:'Bloqueado'}};
  var ST_CLR = {{ati:'#9ca3af',sol:'#fbbf24',blq:'#f87171'}};
  var ST_BG  = {{ati:'rgba(156,163,175,.1)',sol:'rgba(251,191,36,.12)',blq:'rgba(239,68,68,.12)'}};
  var cancelMin = selCancelMin();
  var sinalCls = function(v, min){{ return v >= (min||1) ? 'color:#f87171;font-weight:700' : 'color:#374151'; }};
  el.innerHTML = filtrado.map(function(s, i){{
    var sel = _selSusp === s.n;
    var hl = sel ? 'background:#1a0a00;border-left:3px solid #f97316;' : 'border-left:3px solid transparent;';
    var st = selGetSt(s.n);
    var pcCls = s.pc >= cancelMin ? 'color:#f87171;font-weight:700' : 'color:#6b7280';
    return '<tr style="border-bottom:1px solid #0a0f1e;'+hl+'">'
      + '<td style="padding:4px 8px;color:#374151;font-size:9px">'+(i+1)+'</td>'
      + '<td style="padding:4px 8px;color:#60a5fa;font-size:10px;font-weight:700;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+s.n+'">'+s.n+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#e5e7eb;font-weight:700">'+s.t+'</td>'
      + '<td style="padding:4px 8px;text-align:center;'+sinalCls(s.f, 1)+'">'+s.f+'</td>'
      + '<td style="padding:4px 8px;text-align:center;'+sinalCls(s.pnr, PNR_EB_MIN)+'">'+s.pnr+'</td>'
      + '<td style="padding:4px 8px;text-align:center;'+sinalCls(s.eb, PNR_EB_MIN)+'">'+s.eb+'</td>'
      + '<td style="padding:4px 8px;text-align:center;'+pcCls+'" title="'+s.c60+' de '+s.t60+' SHPs (60d)">'+s.pc.toFixed(1)+'%</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#f87171;font-size:10px">$'+s.b.toFixed(0)+'</td>'
      + '<td style="padding:4px 8px"><span onclick="selToggleSt(\\''+s.n+'\\')" style="cursor:pointer;font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px;color:'+ST_CLR[st]+';background:'+ST_BG[st]+'">'+ST_LBL[st]+'</span></td>'
      + '<td style="padding:4px 8px"><button onclick="selGerarApresentacao(\\''+s.n+'\\')" style="background:rgba(37,99,235,.1);border:1px solid rgba(37,99,235,.25);color:#93c5fd;font-size:10px;padding:3px 9px;border-radius:5px;cursor:pointer;white-space:nowrap;font-family:inherit">&#9998; PDF</button></td>'
      + '</tr>';
  }}).join('');
}}

window.selSuspFiltrar = function(){{
  _qSusp = (document.getElementById('sel-susp-busca') || {{}}).value || '';
  var susp = _sellers.filter(isSuspeito).sort(function(a,b){{return b.b - a.b;}});
  renderSuspTbody(susp);
  selUpdSuspOverview();
}};

window.selGerarApresentacao = function(nick){{
  var s = SEL_DATA.find(function(x){{ return x.n === nick; }});
  if(!s) return;
  var hoje = new Date().toLocaleDateString('pt-BR',{{day:'2-digit',month:'2-digit',year:'numeric'}});
  var st = selGetSt(nick);
  var ST_LBL_MAP = {{ati:'Ativo',sol:'Solicitado',blq:'Bloqueado'}};
  var stLbl = ST_LBL_MAP[st] || 'Ativo';
  var isBloq = st === 'blq';
  var bppFmt = function(v){{ return '$ '+v.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}}); }};
  var evid = SHP_FRAUDE.filter(function(x){{ return x.n === nick; }})
    .concat(SHP_DAMAGE.filter(function(x){{ return x.n === nick; }}))
    .sort(function(a,b){{ return b.b - a.b; }})
    .slice(0, 200);
  var evRows = evid.map(function(e,i){{
    var causa = e.tf || e.td || e.c || '';
    return '<tr class="'+(i%2?'alt':'')+'">'
      +'<td class="cn">'+(i+1)+'</td>'
      +'<td style="color:#555;font-size:9.5px;white-space:nowrap">'+(e.mes||'—')+'</td>'
      +'<td>'+causa+'</td>'
      +'<td><a href="'+LOG_URL+e.s+'" style="color:#1d4ed8;text-decoration:none">'+e.s+'</a></td>'
      +'<td class="rn">'+bppFmt(e.b||0)+'</td></tr>';
  }}).join('');
  var motivoParts = [];
  if(s.f > 0)              motivoParts.push(s.f+' fraude(s)');
  if(s.pnr >= PNR_EB_MIN)   motivoParts.push(s.pnr+' pacote(s) divergente(s) (PNR C)');
  if(s.eb >= PNR_EB_MIN)    motivoParts.push(s.eb+' caixa(s) vazia(s)');
  if(s.pc >= selCancelMin()) motivoParts.push(s.pc.toFixed(1)+'% cancelamento (60d)');
  var motivo = motivoParts.join(' | ') || '—';
  var css=[
    '*{{box-sizing:border-box;margin:0;padding:0}}',
    'body{{font-family:Arial,sans-serif;background:#fff;color:#111;font-size:12px}}',
    '.page{{width:210mm;min-height:297mm;padding:16mm 20mm;margin:0 auto;display:flex;flex-direction:column;page-break-after:always}}',
    '.page:last-child{{page-break-after:auto}}',
    '.hdr{{background:#FFE600;padding:9px 16px;display:flex;align-items:center;justify-content:space-between;border-radius:5px;margin-bottom:18px}}',
    '.hdr-l{{font-weight:900;font-size:12px;letter-spacing:1px;color:#111}}',
    '.hdr-r{{font-size:9px;color:#555}}',
    '.ttl{{text-align:center;margin-bottom:16px}}',
    '.ttl h1{{font-size:20px;font-weight:900;letter-spacing:2px;text-transform:uppercase;color:#111}}',
    '.ttl h2{{font-size:10px;color:#555;font-weight:400;text-transform:uppercase;letter-spacing:1px;margin-top:3px}}',
    '.cid{{background:#111;color:#FFE600;font-size:22px;font-weight:900;text-align:center;padding:10px;border-radius:6px;letter-spacing:1px;margin-bottom:16px;word-break:break-all}}',
    '.igrid{{display:grid;grid-template-columns:1fr 1fr;gap:0;border:1px solid #ddd;border-radius:6px;overflow:hidden;margin-bottom:14px}}',
    '.ii{{padding:9px 12px;border-bottom:1px solid #eee}}',
    '.ii:nth-child(odd){{border-right:1px solid #eee}}',
    '.ilbl{{font-size:7.5px;text-transform:uppercase;letter-spacing:0.8px;color:#999;font-weight:700}}',
    '.ival{{font-size:13px;font-weight:700;color:#111;margin-top:2px}}',
    '.ival.red{{color:#dc2626}}',
    '.conc{{background:#f0fdf4;border:1.5px solid #16a34a;border-radius:5px;padding:12px 16px;margin-top:auto}}',
    '.conc .ct{{font-size:12px;font-weight:900;color:#15803d;margin-bottom:3px}}',
    '.conc .cs{{font-size:10px;color:#166534}}',
    '.fconf{{font-size:8.5px;color:#bbb;text-align:center;margin-top:12px;border-top:1px solid #eee;padding-top:7px}}',
    '.evh h2{{font-size:15px;font-weight:900;color:#111}}',
    '.evh .sub{{font-size:10px;color:#555;margin-top:2px;margin-bottom:10px}}',
    '.etbl{{width:100%;border-collapse:collapse;margin-bottom:8px}}',
    '.etbl th{{background:#111;color:#FFE600;font-size:8.5px;text-transform:uppercase;letter-spacing:0.7px;padding:6px 9px;text-align:left}}',
    '.etbl td{{padding:5px 9px;border-bottom:1px solid #eee;font-size:10.5px}}',
    '.etbl tr.alt td{{background:#f9fafb}}',
    '.etbl td.cn{{text-align:center;color:#aaa;width:28px}}',
    '.etbl td.rn{{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}}',
    '.efoot{{font-size:9.5px;color:#555;background:#f9fafb;border:1px solid #eee;border-radius:4px;padding:7px 10px;margin-top:4px}}',
    '.bkpg{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}}',
    '.bkpg .lp{{font-size:14px;font-weight:900;letter-spacing:3px;text-transform:uppercase}}',
    '.bkpg .bar{{background:#FFE600;width:50px;height:3px;margin:10px auto}}',
    '.bkpg .un{{font-size:10px;color:#555;margin-top:6px}}',
    '.bkpg .gn{{font-size:9px;color:#999;margin-top:18px;border-top:1px solid #eee;padding-top:12px;width:100%}}',
    '.pbtn{{position:fixed;bottom:18px;right:18px;background:#111;color:#FFE600;border:none;padding:9px 18px;border-radius:5px;font-size:12px;font-weight:700;cursor:pointer;letter-spacing:0.5px;z-index:99;box-shadow:0 4px 12px rgba(0,0,0,.3)}}',
    '.pbtn:hover{{background:#333}}',
    '@media print{{.pbtn{{display:none}}.page{{width:100%;margin:0}}}}'
  ].join('');
  var pg1='<div class="page">'+
    '<div class="hdr"><div class="hdr-l">LOSS PREVENTION</div><div class="hdr-r">SSP30 · Guarulhos Mega · Mercado Livre</div></div>'+
    '<div class="ttl"><h1>Solicitação de Bloqueio — Seller</h1><h2>Painel Operacional do Caso</h2></div>'+
    '<div class="cid">'+s.n+'</div>'+
    '<div class="igrid">'+
      '<div class="ii"><div class="ilbl">Seller</div><div class="ival">'+s.n+'</div></div>'+
      '<div class="ii"><div class="ilbl">Reputacao</div><div class="ival">'+s.r+'</div></div>'+
      '<div class="ii"><div class="ilbl">Total SHPs</div><div class="ival">'+s.t+'</div></div>'+
      '<div class="ii"><div class="ilbl">GMV Total</div><div class="ival">'+bppFmt(s.g)+'</div></div>'+
      '<div class="ii"><div class="ilbl">Motivo</div><div class="ival" style="font-size:11px">'+motivo+'</div></div>'+
      '<div class="ii"><div class="ilbl">Data da Solicitação</div><div class="ival">'+hoje+'</div></div>'+
      '<div class="ii"><div class="ilbl">Unidade Emissora</div><div class="ival">Guarulhos Mega</div></div>'+
      '<div class="ii"><div class="ilbl">Fraude / Damaged SHPs</div><div class="ival red">'+s.f+' / '+s.d+'</div></div>'+
      '<div class="ii"><div class="ilbl">BPP Total Acumulado</div><div class="ival red">'+bppFmt(s.b)+'</div></div>'+
    '</div>'+
    '<div class="conc">'+
      '<div class="ct">✓ Conclusão: '+(isBloq?'JÁ BLOQUEADO':'APTO PARA SOLICITAÇÃO')+'</div>'+
      '<div class="cs">O caso atinge os critérios de suspeição definidos pela política interna de Loss Prevention.</div>'+
    '</div>'+
    '<div class="fconf">CONFIDENCIAL — Uso Interno — Loss Prevention SSP30</div>'+
  '</div>';
  var pg2='<div class="page">'+
    '<div class="hdr"><div class="hdr-l">LOSS PREVENTION</div><div class="hdr-r">Evidências · Seller '+s.n+'</div></div>'+
    '<div class="evh"><h2>Evidências — Seller '+s.n+'</h2><div class="sub">Reputação: '+s.r+'</div></div>'+
    '<table class="etbl">'+
      '<thead><tr><th class="cn">#</th><th>Mês</th><th>Causa</th><th>Shipment ID</th><th class="rn">BPP (USD)</th></tr></thead>'+
      '<tbody>'+evRows+'</tbody>'+
    '</table>'+
    '<div class="efoot">Fraude SHPs: <strong>'+s.f+'</strong> · Damaged SHPs: <strong>'+s.d+'</strong> · BPP Total: <strong>'+bppFmt(s.b)+'</strong> &nbsp;|&nbsp; <strong>'+stLbl+'</strong></div>'+
    '<div class="fconf">CONFIDENCIAL — Uso Interno — Loss Prevention SSP30</div>'+
  '</div>';
  var pg3='<div class="page">'+
    '<div class="bkpg">'+
      '<div class="lp">Loss Prevention</div>'+
      '<div class="bar"></div>'+
      '<div class="un">Mercado Livre · SSP30 · Guarulhos Mega</div>'+
      '<div class="gn">Gerado em '+hoje+' &nbsp;|&nbsp; '+SEL_ANALISTA+'<br><br>CONFIDENCIAL — Uso Interno — Loss Prevention SSP30</div>'+
    '</div>'+
  '</div>';
  var full='<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><title>Bloqueio '+s.n+'</title>'+
    '<style>'+css+'</style></head><body>'+pg1+pg2+pg3+
    '<button class="pbtn" onclick="window.print()">&#128424; Imprimir / PDF</button>'+
    '</body></html>';
  var w=window.open('','_blank','width=1100,height=900,scrollbars=yes');
  if(!w){{ alert('Permita pop-ups para gerar a apresentação.'); return; }}
  w.document.write(full);
  w.document.close();
  w.focus();
}};

// Relatório de investigação — usado nas abas Fraudes e Damaged
// (neutro: "encaminhado para investigação", não implica bloqueio)
window.selGerarRelatorioSeller = function(nick, tipo){{
  if(!nick){{ alert('Selecione um seller no gráfico ou na busca primeiro.'); return; }}
  var s = SEL_DATA.find(function(x){{ return x.n === nick; }});
  if(!s) return;
  var fonte = tipo === 'damaged' ? SHP_DAMAGE : SHP_FRAUDE;
  var itens = fonte.filter(function(x){{ return x.n === nick; }}).sort(function(a,b){{ return b.b - a.b; }});
  if(!itens.length){{ alert('Nenhum shipment encontrado para este seller.'); return; }}
  var hoje = new Date().toLocaleDateString('pt-BR',{{day:'2-digit',month:'2-digit',year:'numeric'}});
  var bppFmt = function(v){{ return '$ '+v.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}}); }};
  var totalBpp = itens.reduce(function(a,e){{ return a+(e.b||0); }}, 0);
  var meses = itens.map(function(e){{ return e.mes; }}).filter(Boolean).sort();
  var periodo = meses.length ? meses[0]+' a '+meses[meses.length-1] : '—';
  var tipoLbl = tipo === 'damaged' ? 'Damaged' : 'Fraude Seller';
  var causaCnt = {{}};
  itens.forEach(function(e){{ var c = (tipo==='damaged'? e.td : e.tf) || 'Outro'; causaCnt[c]=(causaCnt[c]||0)+1; }});
  var causaTop = Object.entries(causaCnt).sort(function(a,b){{return b[1]-a[1];}})[0];
  var causaLbl = causaTop ? causaTop[0] : tipoLbl;
  var evRows = itens.slice(0, 200).map(function(e,i){{
    var causa = (tipo==='damaged'? e.td : e.tf) || e.c || '';
    var motivo = '<div style="font-weight:700;color:#111;margin-bottom:2px">'+causa+'</div>'
      +'<div style="color:#999;font-size:9px">Claim ID: '+(e.cl||'—')+' · '+(e.mes||'—')+' · motivo pendente (BT_CLA_CLAIM)</div>';
    return '<tr class="'+(i%2?'alt':'')+'">'
      +'<td class="cn">'+(i+1)+'</td>'
      +'<td><a href="'+LOG_URL+e.s+'" style="color:#1d4ed8;text-decoration:none">'+e.s+'</a></td>'
      +'<td>'+motivo+'</td>'
      +'<td class="rn">'+bppFmt(e.b||0)+'</td></tr>';
  }}).join('');
  var css=[
    '*{{box-sizing:border-box;margin:0;padding:0}}',
    'body{{font-family:Arial,sans-serif;background:#fff;color:#111;font-size:12px}}',
    '.page{{width:210mm;min-height:297mm;padding:16mm 20mm;margin:0 auto;display:flex;flex-direction:column;page-break-after:always}}',
    '.page:last-child{{page-break-after:auto}}',
    '.hdr{{background:#FFE600;padding:9px 16px;display:flex;align-items:center;justify-content:space-between;border-radius:5px;margin-bottom:18px}}',
    '.hdr-l{{font-weight:900;font-size:12px;letter-spacing:1px;color:#111}}',
    '.hdr-r{{font-size:9px;color:#555}}',
    '.ttl{{text-align:center;margin-bottom:16px}}',
    '.ttl h1{{font-size:19px;font-weight:900;letter-spacing:1px;text-transform:uppercase;color:#111}}',
    '.ttl h2{{font-size:10px;color:#555;font-weight:400;text-transform:uppercase;letter-spacing:1px;margin-top:3px}}',
    '.cid{{background:#111;color:#FFE600;font-size:20px;font-weight:900;text-align:center;padding:10px;border-radius:6px;margin-bottom:16px;word-break:break-all}}',
    '.igrid{{display:grid;grid-template-columns:1fr 1fr;gap:0;border:1px solid #ddd;border-radius:6px;overflow:hidden;margin-bottom:14px}}',
    '.ii{{padding:9px 12px;border-bottom:1px solid #eee}}',
    '.ii:nth-child(odd){{border-right:1px solid #eee}}',
    '.ilbl{{font-size:7.5px;text-transform:uppercase;letter-spacing:0.8px;color:#999;font-weight:700}}',
    '.ival{{font-size:13px;font-weight:700;color:#111;margin-top:2px}}',
    '.ival.red{{color:#dc2626}}',
    '.conc{{background:#eff6ff;border:1.5px solid #93c5fd;border-radius:5px;padding:12px 16px;margin-top:auto}}',
    '.conc .ct{{font-size:12px;font-weight:900;color:#1d4ed8;margin-bottom:3px}}',
    '.conc .cs{{font-size:10px;color:#1e3a5f}}',
    '.fconf{{font-size:8.5px;color:#bbb;text-align:center;margin-top:12px;border-top:1px solid #eee;padding-top:7px}}',
    '.etbl{{width:100%;border-collapse:collapse;margin-bottom:8px}}',
    '.etbl th{{background:#111;color:#FFE600;font-size:8.5px;text-transform:uppercase;letter-spacing:0.7px;padding:6px 9px;text-align:left}}',
    '.etbl td{{padding:5px 9px;border-bottom:1px solid #eee;font-size:10.5px}}',
    '.etbl tr.alt td{{background:#f9fafb}}',
    '.etbl td.cn{{text-align:center;color:#aaa;width:28px}}',
    '.etbl td.rn{{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}}',
    '.efoot{{font-size:9.5px;color:#555;background:#f9fafb;border:1px solid #eee;border-radius:4px;padding:7px 10px;margin-top:4px}}',
    '.pbtn{{position:fixed;bottom:18px;right:18px;background:#111;color:#FFE600;border:none;padding:9px 18px;border-radius:5px;font-size:12px;font-weight:700;cursor:pointer;letter-spacing:0.5px;z-index:99;box-shadow:0 4px 12px rgba(0,0,0,.3)}}',
    '.pbtn:hover{{background:#333}}',
    '@media print{{.pbtn{{display:none}}.page{{width:100%;margin:0}}}}'
  ].join('');
  var extra = itens.length > 200 ? ' <span style="color:#999">· +'+(itens.length-200)+' não exibidos (top BPP)</span>' : '';
  var pg1 = '<div class="page">'+
    '<div class="hdr"><div class="hdr-l">LOSS PREVENTION</div><div class="hdr-r">SSP30 · Guarulhos Mega · Mercado Livre</div></div>'+
    '<div class="ttl"><h1>Relatório de investigação — seller</h1><h2>Encaminhado para análise do time de fraude</h2></div>'+
    '<div class="cid">'+s.n+'</div>'+
    '<div class="igrid">'+
      '<div class="ii"><div class="ilbl">Total SHPs '+tipoLbl+'</div><div class="ival red">'+itens.length+' pacotes</div></div>'+
      '<div class="ii"><div class="ilbl">BPP total exposto</div><div class="ival red">'+bppFmt(totalBpp)+'</div></div>'+
      '<div class="ii"><div class="ilbl">Tipo predominante</div><div class="ival">'+causaLbl+'</div></div>'+
      '<div class="ii"><div class="ilbl">Período do histórico</div><div class="ival">'+periodo+'</div></div>'+
      '<div class="ii"><div class="ilbl">Unidade</div><div class="ival">Guarulhos Mega</div></div>'+
      '<div class="ii"><div class="ilbl">Data do relatório</div><div class="ival">'+hoje+'</div></div>'+
    '</div>'+
    '<div class="conc">'+
      '<div class="ct">Encaminhado para investigação</div>'+
      '<div class="cs">Volume e concentração de '+tipoLbl.toLowerCase()+' acima do padrão identificados nesta unidade. Solicitamos análise do time responsável.</div>'+
    '</div>'+
    '<div class="fconf">CONFIDENCIAL — Uso Interno — Loss Prevention SSP30</div>'+
  '</div>';
  var pg2 = '<div class="page">'+
    '<div class="hdr"><div class="hdr-l">LOSS PREVENTION</div><div class="hdr-r">Evidências · Seller '+s.n+'</div></div>'+
    '<div style="margin-bottom:10px"><div style="font-size:15px;font-weight:900;color:#111">Evidências — com motivo da reclamação (amostra)</div></div>'+
    '<table class="etbl">'+
      '<thead><tr><th class="cn">#</th><th>Shipment ID</th><th>Motivo da reclamação</th><th class="rn">BPP</th></tr></thead>'+
      '<tbody>'+evRows+'</tbody>'+
    '</table>'+
    '<div style="font-size:9px;color:#b45309;background:#fffbeb;border:1px solid #fde68a;border-radius:4px;padding:6px 10px;margin-bottom:8px">Coluna "Motivo da reclamação" depende de acesso ao BT_CLA_CLAIM (Shield) — Claim ID já disponível, texto do motivo pendente</div>'+
    '<div class="efoot">'+tipoLbl+' SHPs: <strong>'+itens.length+'</strong>'+extra+' · BPP Total: <strong>'+bppFmt(totalBpp)+'</strong></div>'+
    '<div class="fconf">CONFIDENCIAL — Uso Interno — Loss Prevention SSP30</div>'+
  '</div>';
  var full='<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><title>Relatório '+s.n+'</title>'+
    '<style>'+css+'</style></head><body>'+pg1+pg2+
    '<button class="pbtn" onclick="window.print()">&#128424; Imprimir / PDF</button>'+
    '</body></html>';
  var w2=window.open('','_blank','width=1100,height=900,scrollbars=yes');
  if(!w2){{ alert('Permita pop-ups para gerar o relatório.'); return; }}
  w2.document.write(full);
  w2.document.close();
  w2.focus();
}};
// Wrappers para os botões (onclick roda no escopo global; _selSel é interno ao closure)
window.selGerarRelatorioFraude  = function(){{ selGerarRelatorioSeller(_selSel, 'fraude'); }};
window.selGerarRelatorioDamaged = function(){{ selGerarRelatorioSeller(_selSel, 'damaged'); }};

// PERIODO E KPIs
function updKPIs(){{
  var bpp = _sellers.reduce(function(a, s){{return a + s.b;}}, 0);
  var untrusted = _sellers.filter(function(s){{return s.r==='SELLER NOT TRUSTED'||s.r==='BOTH NOT TRUSTED';}}).length;
  var susp = _sellers.filter(isSuspeito).length;
  function el(id, v){{ var e = document.getElementById(id); if(e) e.textContent = v; }}
  el('sel-k-sellers', _sellers.length.toLocaleString('pt-BR'));
  el('sel-k-bpp', 'US$ ' + Math.round(bpp).toLocaleString('pt-BR'));
  el('sel-k-fraude', _fraudes.length.toLocaleString('pt-BR'));
  el('sel-k-damaged', _damages.length.toLocaleString('pt-BR'));
  el('sel-k-untrusted', untrusted.toLocaleString('pt-BR'));
  el('sel-k-suspeitos', susp.toLocaleString('pt-BR'));
}}

function selAplicar(){{
  var pDe  = (document.getElementById('sel-de')  || {{}}).value || '';
  var pAte = (document.getElementById('sel-ate') || {{}}).value || '';
  var lb = document.getElementById('sel-periodo-label');
  if(lb) lb.textContent = (pDe || pAte) ? ('Periodo: ' + (pDe||'inicio') + ' a ' + (pAte||'hoje')) : '';

  _sellers = SEL_DATA.filter(function(s){{
    return (!pDe || s.u >= pDe) && (!pAte || s.p <= pAte);
  }});
  _fraudes = SHP_FRAUDE.filter(function(s){{
    return (!pDe || s.mes >= pDe) && (!pAte || s.mes <= pAte);
  }});
  _damages = SHP_DAMAGE.filter(function(s){{
    return (!pDe || s.mes >= pDe) && (!pAte || s.mes <= pAte);
  }});

  updKPIs();
  _selSel = null; _selSusp = null;
  renderAbas();
}}

function renderAbas(){{
  if(_abaAtual === 'historico'){{
    renderHistCharts(); renderHistTbody();
  }} else if(_abaAtual === 'damaged'){{
    renderDmgCharts(); renderDmgTbody();
  }} else if(_abaAtual === 'fraudes'){{
    renderFrCharts(); renderFrTbody();
  }} else if(_abaAtual === 'suspeitos'){{
    var susp = _sellers.filter(isSuspeito).sort(function(a,b){{return b.b - a.b;}});
    renderSuspCharts(susp); renderSuspTbody(susp); selUpdSuspOverview();
  }}
}}

window.selTab = function(aba){{
  ['historico','damaged','fraudes','suspeitos'].forEach(function(a){{
    var btn = document.getElementById('seltab-'+a);
    var sec = document.getElementById('selc-'+a);
    var ativo = a === aba;
    if(btn){{
      btn.style.background     = ativo ? '#1e3a5f' : 'transparent';
      btn.style.color          = ativo ? '#38bdf8' : '#6b7280';
      btn.style.borderBottom   = ativo ? '2px solid #38bdf8' : '2px solid transparent';
    }}
    if(sec) sec.style.display = ativo ? '' : 'none';
  }});
  _abaAtual = aba;
  _selSel = null;
  renderAbas();
}};

window.selAplicar = selAplicar;
window.selLimpar = function(){{
  ['sel-de','sel-ate'].forEach(function(id){{ var e = document.getElementById(id); if(e) e.value = ''; }});
  selAplicar();
}};

window.selExportCSV = function(){{
  var rows = [['Seller','Reputacao','Total','Damaged','Fraude','PNR C (Divergente)','Empty Box','% Cancelamento 60d','Meses','BPP USD','GMV USD','Status Solicitacao']];
  _sellers.forEach(function(s){{ rows.push([s.n, s.r, s.t, s.d, s.f, s.pnr, s.eb, s.pc, s.m, s.b, s.g, selGetSt(s.n)]); }});
  var csv = rows.map(function(r){{
    return r.map(function(v){{ return '"' + String(v).replace(/"/g, '""') + '"'; }}).join(',');
  }}).join('\\n');
  var a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,﻿' + encodeURIComponent(csv);
  a.download = 'sellers_ssp30.csv';
  a.click();
}};

document.addEventListener('click', function(e){{
  var row = e.target.closest('.sel-hist-row');
  if(row && row.dataset.nick) selHistSel(row.dataset.nick);
}});

document.addEventListener('DOMContentLoaded', function(){{
  selAplicar();
  selUpdSuspOverview();
  var badge = document.getElementById('tab-count-sellers');
  if(badge) badge.textContent = SEL_DATA.length;
}});
}})();
</script>
</div>"""


def inject_sellers_sidebar(html):
    if 'data-tab="sellers"' in html:
        return html
    old = '<div class="sb-item" data-tab="bloqueios"'
    new = (
        '<div class="sb-item" data-tab="sellers" onclick="showTab(\'sellers\',this);'
        'setTimeout(function(){if(window.selAplicar)window.selAplicar();},80)">\n'
        '      <i data-lucide="store" width="14" height="14" class="ci"></i>\n'
        '      Sellers <span class="sb-badge" id="tab-count-sellers">0</span>\n'
        '    </div>\n'
        '    <div class="sb-item" data-tab="bloqueios"'
    )
    if old not in html:
        print('  WARNING: sellers sidebar — anchor data-tab="bloqueios" não encontrado')
        return html
    return html.replace(old, new, 1)


def find_and_replace_tab(content, tab_id, new_html):
    start = content.find(f'<div id="{tab_id}" class="content">')
    if start == -1:
        return content, False
    depth, pos = 0, start
    while pos < len(content):
        no = content.find('<div', pos)
        nc = content.find('</div>', pos)
        if no == -1 and nc == -1:
            break
        if no != -1 and (nc == -1 or no < nc):
            depth += 1; pos = no + 4
        else:
            depth -= 1; end = nc + 6; pos = end
            if depth == 0:
                if pos < len(content) and content[pos] == '\n':
                    end = pos + 1
                return content[:start] + new_html + '\n' + content[end:], True
    return content, False


def main():
    sellers, shps_fraude, shps_damaged = carregar()
    kpis = static_kpis(sellers, shps_fraude, shps_damaged)
    print(f'  Suspeitos: {kpis["suspeitos"]:,} | Untrusted: {kpis["untrusted"]:,}')

    print('Gerando HTML...')
    tab_html = gerar_tab(sellers, shps_fraude, shps_damaged, kpis)

    print('Lendo fraude.html...')
    html = HTML_OUT.read_text(encoding='utf-8')

    # Injeta sidebar item de sellers (se ainda não existir)
    html = inject_sellers_sidebar(html)

    # Injeta/atualiza aba sellers
    html, ok = find_and_replace_tab(html, 'tab-sellers', tab_html)
    if not ok:
        # Insere antes do primeiro fechamento de </main> ou no final do body
        ins = html.rfind('</main>')
        if ins == -1:
            ins = html.rfind('</body>')
        if ins > 0:
            html = html[:ins] + tab_html + '\n' + html[ins:]
            ok = True
    print(f'  tab-sellers {"atualizada" if ok else "ERRO - nao encontrada"}')

    HTML_OUT.write_text(html, encoding='utf-8')
    mb = HTML_OUT.stat().st_size / 1024 / 1024
    print(f'Pronto! {mb:.1f} MB')


if __name__ == '__main__':
    main()
