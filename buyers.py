"""
buyers.py v2 — Aba Buyers do Dashboard SSP30
Sub-abas: Historico | Damaged | Fraudes | Suspeitos
"""

import json, re
from datetime import datetime
from pathlib import Path
from google.cloud import bigquery
from google.auth import default

FACILITY = 'Guarulhos Mega'
INICIO   = '2026-01-01'
HTML_OUT = Path(__file__).parent / 'fraude.html'

# ── Query 1: buyers com fraude OU damaged (LP relevante) ────────────────────
# CTE pré-deduplica por SHP antes de agregar — evita contar o mesmo pacote N vezes
Q_BUYERS = f"""
WITH shps AS (
  SELECT
    CUS_NICKNAME_BUY                                                     AS buyer,
    SHIPMENT_ID,
    MAX(TIPO_FRAUDE)                                                     AS tipo_fraude,
    MAX(CLASSIFICATION_LM)                                               AS classification_lm,
    MAX(TIPO_DAMAGED_LG)                                                 AS tipo_damaged_lg,
    MAX(DATE_BPP)                                                        AS date_bpp,
    MAX(BPP_CASHOUT_USD)                                                 AS bpp_cashout_usd
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = '{FACILITY}'
    AND DATE_BPP >= '{INICIO}'
    AND DATE_BPP <= CURRENT_DATE()
    AND CUS_NICKNAME_BUY IS NOT NULL
    AND CUS_NICKNAME_BUY != ''
  GROUP BY 1, 2
)
SELECT
  buyer,
  COUNT(*)                                                               AS total,
  ROUND(SUM(IFNULL(bpp_cashout_usd, 0)), 2)                             AS bpp,
  COUNTIF(tipo_fraude = 'FRAUDE BUYER')                                  AS n_fraude,
  COUNTIF(
    classification_lm LIKE 'DAMAGED%'
    OR tipo_damaged_lg IN (
      'DAMAGED','damaged_svc','damaged_on_route','damaged_seller','damaged','SELLER'
    )
  )                                                                      AS n_damaged,
  MIN(FORMAT_DATE('%Y-%m', date_bpp))                                    AS primeiro_mes,
  MAX(FORMAT_DATE('%Y-%m', date_bpp))                                    AS ultimo_mes,
  COUNT(DISTINCT FORMAT_DATE('%Y-%m', date_bpp))                        AS n_meses
FROM shps
GROUP BY 1
HAVING n_fraude > 0 OR n_damaged > 0
ORDER BY bpp DESC
"""

# ── Query 2: shipments FRAUDE BUYER (1 linha por SHP) ───────────────────────
Q_FRAUDE_BUYER = f"""
SELECT
  CUS_NICKNAME_BUY                                    AS buyer,
  CAST(SHIPMENT_ID AS STRING)                         AS sid,
  MAX(IFNULL(CLASSIFICATION_LM, ''))                  AS causa,
  MAX(IFNULL(TIPO_FRAUDE, ''))                        AS tf,
  FORMAT_DATE('%Y-%m', MAX(DATE_BPP))                 AS mes,
  ROUND(MAX(IFNULL(BPP_CASHOUT_USD, 0)), 2)           AS bpp
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = '{FACILITY}'
  AND DATE_BPP >= '{INICIO}'
  AND DATE_BPP <= CURRENT_DATE()
  AND CUS_NICKNAME_BUY IS NOT NULL
  AND TIPO_FRAUDE = 'FRAUDE BUYER'
GROUP BY 1, 2
ORDER BY bpp DESC
"""

# ── Query 3: shipments damaged com buyer (1 linha por SHP, top 3000) ─────────
Q_DAMAGED_BUYER = f"""
SELECT
  CUS_NICKNAME_BUY                                    AS buyer,
  CAST(SHIPMENT_ID AS STRING)                         AS sid,
  MAX(IFNULL(CLASSIFICATION_LM, ''))                  AS causa,
  MAX(IFNULL(TIPO_DAMAGED_LG, ''))                    AS td,
  FORMAT_DATE('%Y-%m', MAX(DATE_BPP))                 AS mes,
  ROUND(MAX(IFNULL(BPP_CASHOUT_USD, 0)), 2)           AS bpp
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = '{FACILITY}'
  AND DATE_BPP >= '{INICIO}'
  AND DATE_BPP <= CURRENT_DATE()
  AND CUS_NICKNAME_BUY IS NOT NULL
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

    print('Consultando buyers agregados (com fraude ou damaged)...')
    buyers = []
    for r in client.query(Q_BUYERS).result():
        buyers.append({
            'n': r['buyer'],
            't': int(r['total']),
            'b': float(r['bpp'] or 0),
            'f': int(r['n_fraude']),
            'd': int(r['n_damaged']),
            'p': r['primeiro_mes'] or '',
            'u': r['ultimo_mes'] or '',
            'm': int(r['n_meses']),
        })
    print(f'  {len(buyers):,} buyers')

    print('Consultando shipments FRAUDE BUYER...')
    shps_fraude = []
    for r in client.query(Q_FRAUDE_BUYER).result():
        shps_fraude.append({
            'n': r['buyer'],
            's': r['sid'],
            'c': r['causa'],
            'tf': r['tf'],
            'mes': r['mes'],
            'b': float(r['bpp']),
        })
    print(f'  {len(shps_fraude):,} SHPs fraude buyer')

    print('Consultando shipments damaged...')
    shps_damaged = []
    for r in client.query(Q_DAMAGED_BUYER).result():
        shps_damaged.append({
            'n': r['buyer'],
            's': r['sid'],
            'c': r['causa'],
            'td': r['td'],
            'mes': r['mes'],
            'b': float(r['bpp']),
        })
    print(f'  {len(shps_damaged):,} SHPs damaged')

    return buyers, shps_fraude, shps_damaged


def static_kpis(buyers, shps_fraude, shps_damaged):
    total_bpp = sum(b['b'] for b in buyers)
    suspeitos = [b for b in buyers if (b['f'] > 0 and b['d'] > 0) or b['f'] > 1]
    return {
        'buyers':    len(buyers),
        'bpp':       round(total_bpp, 2),
        'fraude':    len(shps_fraude),
        'damaged':   len(shps_damaged),
        'suspeitos': len(suspeitos),
    }


def gerar_tab(buyers, shps_fraude, shps_damaged, kpis):
    now          = datetime.now().strftime('%d/%m/%Y %H:%M')
    buyers_json  = json.dumps(buyers,       ensure_ascii=False)
    fraude_json  = json.dumps(shps_fraude,  ensure_ascii=False)
    damaged_json = json.dumps(shps_damaged, ensure_ascii=False)

    return f"""<div id="tab-buyers" class="content">
<div style="padding:20px 32px">

  <!-- HEADER -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:16px;font-weight:700;color:#f9fafb">Buyers — Analise de Risco</div>
      <div style="font-size:11px;color:#6b7280;margin-top:2px">SSP30 · Guarulhos Mega · desde Jan/2026 · atualizado {now}</div>
    </div>
    <button onclick="buyExportCSV()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:5px 14px;font-size:11px;cursor:pointer">⬇ Exportar CSV</button>
  </div>

  <!-- PERIODO -->
  <div style="display:flex;gap:8px;margin-bottom:14px;align-items:center;flex-wrap:wrap;background:#0d1321;border:1px solid #1f2937;border-radius:8px;padding:10px 16px">
    <span style="font-size:11px;color:#6b7280;font-weight:600">Periodo:</span>
    <input id="buy-de" type="month" onchange="buyAplicar()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 8px;font-size:11px">
    <span style="font-size:11px;color:#4b5563">ate</span>
    <input id="buy-ate" type="month" onchange="buyAplicar()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 8px;font-size:11px">
    <button onclick="buyLimpar()" style="background:#1f2937;color:#6b7280;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:10px;cursor:pointer">Limpar</button>
    <span id="buy-periodo-label" style="font-size:10px;color:#38bdf8;margin-left:4px"></span>
  </div>

  <!-- KPIs -->
  <div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap">
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:10px 18px;flex:1;min-width:88px;text-align:center">
      <div id="buy-k-buyers" style="font-size:22px;font-weight:700;color:#38bdf8">{kpis['buyers']:,}</div>
      <div style="font-size:9px;color:#6b7280;margin-top:2px">Buyers c/ Ocorrencia</div>
    </div>
    <div style="background:#1a0505;border:1px solid #7f1d1d;border-radius:8px;padding:10px 18px;flex:1;min-width:88px;text-align:center">
      <div id="buy-k-bpp" style="font-size:22px;font-weight:700;color:#f87171">US$ {kpis['bpp']:,.0f}</div>
      <div style="font-size:9px;color:#6b7280;margin-top:2px">BPP Total</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:10px 18px;flex:1;min-width:88px;text-align:center">
      <div id="buy-k-fraude" style="font-size:22px;font-weight:700;color:#fbbf24">{kpis['fraude']:,}</div>
      <div style="font-size:9px;color:#6b7280;margin-top:2px">SHPs Fraude Buyer</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:10px 18px;flex:1;min-width:88px;text-align:center">
      <div id="buy-k-damaged" style="font-size:22px;font-weight:700;color:#a78bfa">{kpis['damaged']:,}</div>
      <div style="font-size:9px;color:#6b7280;margin-top:2px">SHPs Damaged</div>
    </div>
    <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:10px 18px;flex:1;min-width:88px;text-align:center">
      <div id="buy-k-suspeitos" style="font-size:22px;font-weight:700;color:#ef4444">{kpis['suspeitos']:,}</div>
      <div style="font-size:9px;color:#6b7280;margin-top:2px">Suspeitos</div>
    </div>
  </div>

  <!-- SUB-ABAS -->
  <div style="display:flex;gap:4px;margin-bottom:14px;border-bottom:1px solid #1f2937;padding-bottom:0">
    <button id="buytab-historico" onclick="buyTab('historico')"
      style="background:#1e3a5f;color:#38bdf8;border:none;border-radius:6px 6px 0 0;padding:7px 16px;font-size:11px;font-weight:600;cursor:pointer;border-bottom:2px solid #38bdf8">
      Historico
    </button>
    <button id="buytab-damaged" onclick="buyTab('damaged')"
      style="background:transparent;color:#6b7280;border:none;border-radius:6px 6px 0 0;padding:7px 16px;font-size:11px;font-weight:600;cursor:pointer;border-bottom:2px solid transparent">
      Damaged
    </button>
    <button id="buytab-fraudes" onclick="buyTab('fraudes')"
      style="background:transparent;color:#6b7280;border:none;border-radius:6px 6px 0 0;padding:7px 16px;font-size:11px;font-weight:600;cursor:pointer;border-bottom:2px solid transparent">
      Fraudes
    </button>
    <button id="buytab-suspeitos" onclick="buyTab('suspeitos')"
      style="background:transparent;color:#6b7280;border:none;border-radius:6px 6px 0 0;padding:7px 16px;font-size:11px;font-weight:600;cursor:pointer;border-bottom:2px solid transparent">
      Suspeitos
    </button>
  </div>

  <!-- HISTORICO -->
  <div id="buyc-historico">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 Buyers - BPP Total <span style="font-weight:400;color:#374151">clique para filtrar</span></div>
        <div style="position:relative;height:240px"><canvas id="buy-cht-hist-bpp"></canvas></div>
      </div>
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 Buyers - Qtd Fraudes</div>
        <div style="position:relative;height:240px"><canvas id="buy-cht-hist-fr"></canvas></div>
      </div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:6px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Buyers</span>
        <span id="buy-hist-count" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
      <div style="display:flex;gap:6px">
        <input id="buy-hist-busca" type="text" placeholder="Buscar buyer..."
          oninput="buyHistFiltrar()"
          style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
        <button onclick="buyHistClear()" style="background:#1f2937;color:#6b7280;border:1px solid #374151;border-radius:5px;padding:4px 9px;font-size:11px;cursor:pointer">X</button>
      </div>
    </div>
    <div style="border:1px solid #1f2937;border-radius:8px;overflow:hidden;margin-bottom:14px">
      <div style="overflow-y:auto;max-height:340px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#4b5563;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">#</th>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Buyer</th>
              <th style="padding:6px 8px;text-align:center;color:#e5e7eb;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Total SHPs</th>
              <th style="padding:6px 8px;text-align:center;color:#fbbf24;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Fraude</th>
              <th style="padding:6px 8px;text-align:center;color:#a78bfa;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Damaged</th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Meses</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">BPP</th>
            </tr>
          </thead>
          <tbody id="buy-hist-tbody"></tbody>
        </table>
      </div>
    </div>
    <!-- Painel SHPs do buyer selecionado -->
    <div id="buy-hist-shp-panel" style="display:none;margin-top:10px;border:1px solid #1e3a5f;border-radius:8px;overflow:hidden">
      <div style="background:#0a1628;padding:8px 14px;display:flex;align-items:center;justify-content:space-between">
        <span id="buy-hist-shp-title" style="font-size:11px;font-weight:700;color:#38bdf8"></span>
        <button onclick="buyHistClearPanel()" style="background:transparent;color:#4b5563;border:none;font-size:12px;cursor:pointer">X fechar</button>
      </div>
      <div style="overflow-y:auto;max-height:280px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Shipment ID</th>
              <th style="padding:6px 8px;text-align:left;color:#fbbf24;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Tipo</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Classificacao</th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Mes</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">BPP</th>
            </tr>
          </thead>
          <tbody id="buy-hist-shp-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- DAMAGED -->
  <div id="buyc-damaged" style="display:none">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 Buyers - Casos Damaged <span style="font-weight:400;color:#374151">clique para filtrar</span></div>
        <div style="position:relative;height:240px"><canvas id="buy-cht-dmg-rank"></canvas></div>
      </div>
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Tipos de Damaged</div>
        <div style="position:relative;height:240px"><canvas id="buy-cht-dmg-tipo"></canvas></div>
      </div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Shipments Damaged</span>
        <span id="buy-dmg-header" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
      <input id="buy-dmg-busca" type="text" placeholder="Buscar buyer ou SHP..."
        oninput="buyDmgFiltrar()"
        style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
    </div>
    <div style="border:1px solid #1f2937;border-radius:8px;overflow:hidden">
      <div style="overflow-y:auto;max-height:360px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Buyer</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Shipment ID</th>
              <th style="padding:6px 8px;text-align:left;color:#a78bfa;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Classificacao</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Tipo Damaged</th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Mes</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">BPP</th>
            </tr>
          </thead>
          <tbody id="buy-dmg-tbody"></tbody>
        </table>
      </div>
    </div>
    <div id="buy-dmg-note" style="font-size:10px;color:#374151;margin-top:5px;text-align:right"></div>
  </div>

  <!-- FRAUDES -->
  <div id="buyc-fraudes" style="display:none">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 Buyers - Fraudes <span style="font-weight:400;color:#374151">clique para filtrar</span></div>
        <div style="position:relative;height:240px"><canvas id="buy-cht-fr-rank"></canvas></div>
      </div>
      <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Classificacao das Fraudes Buyer</div>
        <div style="position:relative;height:240px"><canvas id="buy-cht-fr-tipo"></canvas></div>
      </div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Shipments Fraude Buyer</span>
        <span id="buy-fr-header" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
      <input id="buy-fr-busca" type="text" placeholder="Buscar buyer ou SHP..."
        oninput="buyFrFiltrar()"
        style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
    </div>
    <div style="border:1px solid #1f2937;border-radius:8px;overflow:hidden">
      <div style="overflow-y:auto;max-height:360px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Buyer</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Shipment ID</th>
              <th style="padding:6px 8px;text-align:left;color:#fbbf24;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Tipo Fraude</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Classificacao</th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Mes</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">BPP</th>
            </tr>
          </thead>
          <tbody id="buy-fr-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- SUSPEITOS -->
  <div id="buyc-suspeitos" style="display:none">
    <div style="background:#1a0505;border:1px solid #7f1d1d;border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:11px;color:#fca5a5">
      <b>Criterios de suspeicao:</b> Buyer com FRAUDE e DAMAGED simultaneamente <b>OU</b> com mais de 1 caso de Fraude Buyer
    </div>
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px;margin-bottom:14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 Suspeitos - BPP <span style="font-weight:400;color:#374151">clique para filtrar</span></div>
      <div style="position:relative;height:220px"><canvas id="buy-cht-susp"></canvas></div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Buyers Suspeitos</span>
        <span id="buy-susp-count" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
      <input id="buy-susp-busca" type="text" placeholder="Buscar buyer..."
        oninput="buySuspFiltrar()"
        style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
    </div>
    <div style="border:1px solid #7f1d1d;border-radius:8px;overflow:hidden">
      <div style="overflow-y:auto;max-height:360px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#4b5563;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">#</th>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Buyer</th>
              <th style="padding:6px 8px;text-align:center;color:#e5e7eb;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Total SHPs</th>
              <th style="padding:6px 8px;text-align:center;color:#fbbf24;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Fraudes</th>
              <th style="padding:6px 8px;text-align:center;color:#a78bfa;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Damaged</th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Meses</th>
              <th style="padding:6px 8px;text-align:left;color:#f97316;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Motivo</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">BPP</th>
            </tr>
          </thead>
          <tbody id="buy-susp-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

</div>
<script>
(function(){{
var BUY_DATA   = {buyers_json};
var SHP_FRAUDE = {fraude_json};
var SHP_DAMAGE = {damaged_json};
var LOG_URL    = 'https://logistics.mercadolibre.com.br/shipments/';

var _abaAtual = 'historico';
var _buySel   = null;
var _buySusp  = null;
var _qHist='', _qDmg='', _qFr='', _qSusp='';

var _buyers  = BUY_DATA;
var _fraudes = SHP_FRAUDE;
var _damages = SHP_DAMAGE;

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

function isSuspeito(b){{
  return (b.f > 0 && b.d > 0) || b.f > 1;
}}

function motivoSusp(b){{
  var m = [];
  if(b.f > 1) m.push(b.f + ' fraudes');
  if(b.f > 0 && b.d > 0) m.push('Fraude + Damaged');
  return m.join(' | ') || '-';
}}

// HISTORICO
function renderHistCharts(){{
  var top10b = _buyers.slice(0, 10);
  var top10f = [..._buyers].sort(function(a,b){{return b.f - a.f;}}).slice(0, 10);
  var nb = top10b.map(function(b){{return b.n;}});
  mkChart('buy-cht-hist-bpp', nb, top10b.map(function(b){{return b.b;}}), '#ef4444',
    function(e,els){{if(els.length) buyHistSel(nb[els[0].index]);}});
  mkChart('buy-cht-hist-fr', top10f.map(function(b){{return b.n;}}),
    top10f.map(function(b){{return b.f;}}), '#fbbf24', null);
}}

function renderHistTbody(){{
  var q = _qHist.toLowerCase();
  var lista = _buyers.filter(function(b){{return !q || b.n.toLowerCase().indexOf(q) >= 0;}});
  var ct = document.getElementById('buy-hist-count');
  if(ct) ct.textContent = '(' + lista.length.toLocaleString('pt-BR') + ' buyers)';
  var el = document.getElementById('buy-hist-tbody'); if(!el) return;
  el.innerHTML = lista.slice(0, 1000).map(function(b, i){{
    var sel = _buySel === b.n;
    var hl = sel ? 'background:#0f2040;border-left:3px solid #38bdf8;' : 'border-left:3px solid transparent;';
    return '<tr data-nick="'+b.n+'" class="buy-hist-row" style="border-bottom:1px solid #0a0f1e;cursor:pointer;'+hl+'">'
      + '<td style="padding:4px 8px;color:#374151;font-size:9px">'+(i+1)+'</td>'
      + '<td style="padding:4px 8px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
      + '<span style="color:#60a5fa;font-size:10px;font-weight:700" title="'+b.n+'">'+b.n+'</span>'
      + '</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#e5e7eb;font-weight:700">'+b.t+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#fbbf24">'+b.f+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#a78bfa">'+b.d+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#4b5563">'+b.m+'</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#f87171;font-size:10px">$'+b.b.toFixed(0)+'</td>'
      + '</tr>';
  }}).join('');
  if(lista.length > 1000){{
    el.innerHTML += '<tr><td colspan="7" style="padding:8px;text-align:center;color:#374151;font-size:10px">Mostrando 1.000 de '+lista.length.toLocaleString('pt-BR')+'. Use o filtro para refinar.</td></tr>';
  }}
}}

function renderHistShpPanel(){{
  var panel = document.getElementById('buy-hist-shp-panel');
  var tbody = document.getElementById('buy-hist-shp-tbody');
  var title = document.getElementById('buy-hist-shp-title');
  if(!panel || !tbody) return;
  if(!_buySel){{ panel.style.display='none'; return; }}
  var shps = [];
  SHP_FRAUDE.filter(function(s){{return s.n === _buySel;}}).forEach(function(s){{
    shps.push({{sid:s.s, tipo:'FRAUDE', sub:s.tf, causa:s.c, mes:s.mes, bpp:s.b}});
  }});
  SHP_DAMAGE.filter(function(s){{return s.n === _buySel;}}).forEach(function(s){{
    shps.push({{sid:s.s, tipo:'DAMAGED', sub:s.td, causa:s.c, mes:s.mes, bpp:s.b}});
  }});
  shps.sort(function(a,b){{return b.bpp - a.bpp;}});
  if(title) title.textContent = _buySel + ' — ' + shps.length + ' SHPs (Fraude + Damaged)';
  tbody.innerHTML = shps.map(function(s){{
    var cor = s.tipo === 'FRAUDE' ? '#fbbf24' : '#a78bfa';
    return '<tr style="border-bottom:1px solid #080c18">'
      + '<td style="padding:4px 8px"><a href="'+LOG_URL+s.sid+'" target="_blank" style="color:#38bdf8;font-family:monospace;font-size:10px;font-weight:600;text-decoration:none">'+s.sid+'</a></td>'
      + '<td style="padding:4px 8px;font-size:9px;font-weight:700;color:'+cor+'">'+s.tipo+'</td>'
      + '<td style="padding:4px 8px;color:#6b7280;font-size:10px">'+s.causa+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#4b5563;font-size:10px">'+s.mes+'</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#f87171;font-size:10px">$'+s.bpp.toFixed(2)+'</td>'
      + '</tr>';
  }}).join('');
  panel.style.display = '';
}}

function buyHistSel(nick){{
  _buySel = (_buySel === nick) ? null : nick;
  renderHistTbody();
  renderHistShpPanel();
}}

window.buyHistFiltrar = function(){{
  _qHist = (document.getElementById('buy-hist-busca') || {{}}).value || '';
  renderHistTbody();
}};
window.buyHistClear = function(){{
  _buySel = null; _qHist = '';
  var b = document.getElementById('buy-hist-busca'); if(b) b.value = '';
  renderHistTbody(); renderHistShpPanel();
}};
window.buyHistClearPanel = function(){{
  _buySel = null; renderHistTbody(); renderHistShpPanel();
}};

// DAMAGED
function renderDmgCharts(){{
  var byB = {{}};
  _damages.forEach(function(s){{ byB[s.n] = (byB[s.n] || 0) + 1; }});
  var top10 = Object.entries(byB).sort(function(a,b){{return b[1]-a[1];}}).slice(0,10);
  var nicks = top10.map(function(x){{return x[0];}});
  mkChart('buy-cht-dmg-rank', nicks, top10.map(function(x){{return x[1];}}), '#a78bfa',
    function(e,els){{if(els.length){{_buySel=nicks[els[0].index];renderDmgTbody();}}}});
  var byT = {{}};
  _damages.forEach(function(s){{ var k = s.td || s.c || 'Outro'; byT[k] = (byT[k] || 0) + 1; }});
  var topT = Object.entries(byT).sort(function(a,b){{return b[1]-a[1];}}).slice(0,8);
  mkChart('buy-cht-dmg-tipo', topT.map(function(x){{return x[0];}}), topT.map(function(x){{return x[1];}}), '#6366f1', null);
}}

function renderDmgTbody(){{
  var q = _qDmg.toLowerCase();
  var base = _buySel ? _damages.filter(function(s){{return s.n === _buySel;}}) : _damages;
  var filtrado = q ? base.filter(function(s){{return s.n.toLowerCase().indexOf(q)>=0||s.s.indexOf(q)>=0;}}) : base;
  var hdr = document.getElementById('buy-dmg-header');
  if(hdr) hdr.textContent = _buySel
    ? '- ' + _buySel + ' (' + filtrado.length + ' SHPs)'
    : '- ' + filtrado.length.toLocaleString('pt-BR') + ' shipments (top 3000 por BPP)';
  var note = document.getElementById('buy-dmg-note');
  if(note) note.textContent = filtrado.length > 2000 ? 'Mostrando 2.000 primeiros. Filtre por buyer para ver todos.' : '';
  var el = document.getElementById('buy-dmg-tbody'); if(!el) return;
  el.innerHTML = filtrado.slice(0, 2000).map(function(s){{
    return '<tr style="border-bottom:1px solid #080c18">'
      + '<td style="padding:4px 8px;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#60a5fa;font-size:10px;font-weight:600" title="'+s.n+'">'+s.n+'</td>'
      + '<td style="padding:4px 8px"><a href="'+LOG_URL+s.s+'" target="_blank" style="color:#38bdf8;font-family:monospace;font-size:10px;font-weight:600;text-decoration:none">'+s.s+'</a></td>'
      + '<td style="padding:4px 8px;font-size:10px;color:#a78bfa">'+s.c+'</td>'
      + '<td style="padding:4px 8px;color:#6b7280;font-size:10px">'+s.td+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#4b5563;font-size:10px">'+s.mes+'</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#f87171;font-size:10px">$'+s.b.toFixed(2)+'</td>'
      + '</tr>';
  }}).join('');
}}

window.buyDmgFiltrar = function(){{
  _qDmg = (document.getElementById('buy-dmg-busca') || {{}}).value || '';
  renderDmgTbody();
}};

// FRAUDES
function renderFrCharts(){{
  var byB = {{}};
  _fraudes.forEach(function(s){{ byB[s.n] = (byB[s.n] || 0) + 1; }});
  var top10 = Object.entries(byB).sort(function(a,b){{return b[1]-a[1];}}).slice(0,10);
  var nicks = top10.map(function(x){{return x[0];}});
  mkChart('buy-cht-fr-rank', nicks, top10.map(function(x){{return x[1];}}), '#ef4444',
    function(e,els){{if(els.length){{_buySel=nicks[els[0].index];renderFrTbody();}}}});
  var byC = {{}};
  _fraudes.forEach(function(s){{ var k = s.c || 'Outro'; byC[k] = (byC[k] || 0) + 1; }});
  var topC = Object.entries(byC).sort(function(a,b){{return b[1]-a[1];}}).slice(0,8);
  mkChart('buy-cht-fr-tipo', topC.map(function(x){{return x[0];}}), topC.map(function(x){{return x[1];}}), '#fbbf24', null);
}}

function renderFrTbody(){{
  var q = _qFr.toLowerCase();
  var base = _buySel ? _fraudes.filter(function(s){{return s.n === _buySel;}}) : _fraudes;
  var filtrado = q ? base.filter(function(s){{return s.n.toLowerCase().indexOf(q)>=0||s.s.indexOf(q)>=0;}}) : base;
  var hdr = document.getElementById('buy-fr-header');
  if(hdr) hdr.textContent = _buySel
    ? '- ' + _buySel + ' (' + filtrado.length + ' SHPs)'
    : '- ' + filtrado.length.toLocaleString('pt-BR') + ' shipments';
  var el = document.getElementById('buy-fr-tbody'); if(!el) return;
  el.innerHTML = filtrado.map(function(s){{
    return '<tr style="border-bottom:1px solid #080c18">'
      + '<td style="padding:4px 8px;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#60a5fa;font-size:10px;font-weight:600" title="'+s.n+'">'+s.n+'</td>'
      + '<td style="padding:4px 8px"><a href="'+LOG_URL+s.s+'" target="_blank" style="color:#38bdf8;font-family:monospace;font-size:10px;font-weight:600;text-decoration:none">'+s.s+'</a></td>'
      + '<td style="padding:4px 8px;font-size:10px;color:#fbbf24;font-weight:600">'+s.tf+'</td>'
      + '<td style="padding:4px 8px;color:#6b7280;font-size:10px">'+s.c+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#4b5563;font-size:10px">'+s.mes+'</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#f87171;font-size:10px">$'+s.b.toFixed(2)+'</td>'
      + '</tr>';
  }}).join('');
}}

window.buyFrFiltrar = function(){{
  _qFr = (document.getElementById('buy-fr-busca') || {{}}).value || '';
  renderFrTbody();
}};

// SUSPEITOS
function renderSuspCharts(lista){{
  var top10 = lista.slice(0, 10);
  var nicks = top10.map(function(b){{return b.n;}});
  mkChart('buy-cht-susp', nicks, top10.map(function(b){{return b.b;}}), '#f97316',
    function(e,els){{if(els.length){{_buySusp=nicks[els[0].index];renderSuspTbody(lista);}}}});
}}

function renderSuspTbody(lista){{
  var q = _qSusp.toLowerCase();
  var filtrado = lista.filter(function(b){{return !q || b.n.toLowerCase().indexOf(q) >= 0;}});
  var ct = document.getElementById('buy-susp-count');
  if(ct) ct.textContent = '(' + filtrado.length.toLocaleString('pt-BR') + ' buyers)';
  var el = document.getElementById('buy-susp-tbody'); if(!el) return;
  el.innerHTML = filtrado.map(function(b, i){{
    var sel = _buySusp === b.n;
    var hl = sel ? 'background:#1a0505;border-left:3px solid #ef4444;' : 'border-left:3px solid transparent;';
    return '<tr style="border-bottom:1px solid #0a0f1e;'+hl+'">'
      + '<td style="padding:4px 8px;color:#374151;font-size:9px">'+(i+1)+'</td>'
      + '<td style="padding:4px 8px;color:#60a5fa;font-size:10px;font-weight:700;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+b.n+'">'+b.n+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#e5e7eb;font-weight:700">'+b.t+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#fbbf24">'+b.f+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#a78bfa">'+b.d+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#4b5563">'+b.m+'</td>'
      + '<td style="padding:4px 8px;color:#f97316;font-size:10px">'+motivoSusp(b)+'</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#f87171;font-size:10px">$'+b.b.toFixed(0)+'</td>'
      + '</tr>';
  }}).join('');
}}

window.buySuspFiltrar = function(){{
  _qSusp = (document.getElementById('buy-susp-busca') || {{}}).value || '';
  var susp = _buyers.filter(isSuspeito).sort(function(a,b){{return b.b - a.b;}});
  renderSuspTbody(susp);
}};

// KPIs e PERIODO
function updKPIs(){{
  var bpp = _buyers.reduce(function(a, b){{return a + b.b;}}, 0);
  var susp = _buyers.filter(isSuspeito).length;
  function el(id, v){{ var e = document.getElementById(id); if(e) e.textContent = v; }}
  el('buy-k-buyers',    _buyers.length.toLocaleString('pt-BR'));
  el('buy-k-bpp',       'US$ ' + Math.round(bpp).toLocaleString('pt-BR'));
  el('buy-k-fraude',    _fraudes.length.toLocaleString('pt-BR'));
  el('buy-k-damaged',   _damages.length.toLocaleString('pt-BR'));
  el('buy-k-suspeitos', susp.toLocaleString('pt-BR'));
}}

function buyAplicar(){{
  var pDe  = (document.getElementById('buy-de')  || {{}}).value || '';
  var pAte = (document.getElementById('buy-ate') || {{}}).value || '';
  var lb = document.getElementById('buy-periodo-label');
  if(lb) lb.textContent = (pDe || pAte) ? ('Periodo: ' + (pDe||'inicio') + ' a ' + (pAte||'hoje')) : '';

  _buyers  = BUY_DATA.filter(function(b){{
    return (!pDe || b.u >= pDe) && (!pAte || b.p <= pAte);
  }});
  _fraudes = SHP_FRAUDE.filter(function(s){{
    return (!pDe || s.mes >= pDe) && (!pAte || s.mes <= pAte);
  }});
  _damages = SHP_DAMAGE.filter(function(s){{
    return (!pDe || s.mes >= pDe) && (!pAte || s.mes <= pAte);
  }});

  updKPIs();
  _buySel = null; _buySusp = null;
  renderAbas();
}}

function renderAbas(){{
  if(_abaAtual === 'historico'){{
    renderHistCharts(); renderHistTbody(); renderHistShpPanel();
  }} else if(_abaAtual === 'damaged'){{
    renderDmgCharts(); renderDmgTbody();
  }} else if(_abaAtual === 'fraudes'){{
    renderFrCharts(); renderFrTbody();
  }} else if(_abaAtual === 'suspeitos'){{
    var susp = _buyers.filter(isSuspeito).sort(function(a,b){{return b.b - a.b;}});
    renderSuspCharts(susp); renderSuspTbody(susp);
  }}
}}

window.buyTab = function(aba){{
  ['historico','damaged','fraudes','suspeitos'].forEach(function(a){{
    var btn = document.getElementById('buytab-'+a);
    var sec = document.getElementById('buyc-'+a);
    var ativo = a === aba;
    if(btn){{
      btn.style.background   = ativo ? '#1e3a5f' : 'transparent';
      btn.style.color        = ativo ? '#38bdf8' : '#6b7280';
      btn.style.borderBottom = ativo ? '2px solid #38bdf8' : '2px solid transparent';
    }}
    if(sec) sec.style.display = ativo ? '' : 'none';
  }});
  _abaAtual = aba;
  _buySel = null;
  renderAbas();
}};

window.buyAplicar = buyAplicar;
window.buyLimpar = function(){{
  ['buy-de','buy-ate'].forEach(function(id){{ var e = document.getElementById(id); if(e) e.value = ''; }});
  buyAplicar();
}};

window.buyExportCSV = function(){{
  var rows = [['Buyer','Total','Fraude','Damaged','Meses','BPP USD']];
  _buyers.forEach(function(b){{ rows.push([b.n, b.t, b.f, b.d, b.m, b.b]); }});
  var csv = rows.map(function(r){{
    return r.map(function(v){{ return '"' + String(v).replace(/"/g, '""') + '"'; }}).join(',');
  }}).join('\\n');
  var a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,﻿' + encodeURIComponent(csv);
  a.download = 'buyers_ssp30.csv';
  a.click();
}};

document.addEventListener('click', function(e){{
  var row = e.target.closest('.buy-hist-row');
  if(row && row.dataset.nick) buyHistSel(row.dataset.nick);
}});

document.addEventListener('DOMContentLoaded', function(){{
  buyAplicar();
  var badge = document.getElementById('tab-count-buyers');
  if(badge) badge.textContent = BUY_DATA.length;
}});
}})();
</script>
</div>"""


def inject_buyers_sidebar(html):
    if 'id="tab-count-buyers"' in html:
        return html
    old = '<div class="sb-item" data-tab="sellers"'
    new = (
        '<div class="sb-item" data-tab="buyers" onclick="showTab(\'buyers\',this);'
        'setTimeout(function(){if(window.buyAplicar)window.buyAplicar();},80)">\n'
        '      <i data-lucide="user" width="14" height="14" class="ci"></i>\n'
        '      Buyers <span class="sb-badge" id="tab-count-buyers">0</span>\n'
        '    </div>\n'
        '    <div class="sb-item" data-tab="sellers"'
    )
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
    buyers, shps_fraude, shps_damaged = carregar()
    kpis = static_kpis(buyers, shps_fraude, shps_damaged)
    print(f'  Suspeitos: {kpis["suspeitos"]:,}')

    print('Gerando HTML...')
    tab_html = gerar_tab(buyers, shps_fraude, shps_damaged, kpis)

    print('Lendo fraude.html...')
    html = HTML_OUT.read_text(encoding='utf-8')

    html = inject_buyers_sidebar(html)

    html, ok = find_and_replace_tab(html, 'tab-buyers', tab_html)
    if not ok:
        ins = html.rfind('</main>')
        if ins == -1:
            ins = html.rfind('</body>')
        if ins > 0:
            html = html[:ins] + tab_html + '\n' + html[ins:]
            ok = True
    print(f'  tab-buyers {"atualizada" if ok else "ERRO - nao encontrada"}')

    HTML_OUT.write_text(html, encoding='utf-8')
    mb = HTML_OUT.stat().st_size / 1024 / 1024
    print(f'Pronto! {mb:.1f} MB')


if __name__ == '__main__':
    main()
