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
  MIN(FORMAT_DATE('%Y-%m', date_bpp))                                  AS primeiro_mes,
  MAX(FORMAT_DATE('%Y-%m', date_bpp))                                  AS ultimo_mes,
  COUNT(DISTINCT FORMAT_DATE('%Y-%m', date_bpp))                       AS n_meses
FROM shps
GROUP BY 1, 2
ORDER BY bpp DESC
"""

# ── Query 2: shipments de fraude seller (1 linha por SHP) ───────────────────
Q_FRAUDE = f"""
SELECT
  CUS_NICKNAME_SEL                                   AS seller,
  CAST(SHIPMENT_ID AS STRING)                        AS sid,
  MAX(IFNULL(CLASSIFICATION_LM, ''))                 AS causa,
  MAX(IFNULL(TIPO_FRAUDE, ''))                       AS tf,
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
            'p': r['primeiro_mes'] or '',
            'u': r['ultimo_mes'] or '',
            'm': int(r['n_meses']),
        })
    print(f'  {len(sellers):,} sellers')

    print('Consultando shipments de fraude...')
    shps_fraude = []
    for r in client.query(Q_FRAUDE).result():
        shps_fraude.append({
            'n': r['seller'],
            's': r['sid'],
            'c': r['causa'],
            'tf': r['tf'],
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
            'mes': r['mes'],
            'b': float(r['bpp']),
        })
    print(f'  {len(shps_damaged):,} SHPs damaged')

    return sellers, shps_fraude, shps_damaged


def static_kpis(sellers, shps_fraude, shps_damaged):
    total_bpp = sum(s['b'] for s in sellers)
    untrusted = [s for s in sellers if s['r'] in ('SELLER NOT TRUSTED', 'BOTH NOT TRUSTED')]
    suspeitos = [
        s for s in sellers
        if s['r'] in ('SELLER NOT TRUSTED', 'BOTH NOT TRUSTED')
        or (s['f'] > 0 and s['m'] <= 2)
        or (s['t'] > 0 and s['f'] / s['t'] > 0.3)
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
      <input id="sel-dmg-busca" type="text" placeholder="Buscar seller ou SHP..."
        oninput="selDmgFiltrar()"
        style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
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
      <input id="sel-fr-busca" type="text" placeholder="Buscar seller ou SHP..."
        oninput="selFrFiltrar()"
        style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
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
      <b>Criterios de suspeicao:</b> Reputacao "NOT TRUSTED" <b>OU</b> seller com fraude em menos de 2 meses de historico <b>OU</b> mais de 30% dos SHPs sao fraude
    </div>
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px;margin-bottom:14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 Suspeitos - BPP <span style="font-weight:400;color:#374151">clique para filtrar</span></div>
      <div style="position:relative;height:220px"><canvas id="sel-cht-susp"></canvas></div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Sellers Suspeitos</span>
        <span id="sel-susp-count" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
      <input id="sel-susp-busca" type="text" placeholder="Buscar seller..."
        oninput="selSuspFiltrar()"
        style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
    </div>
    <div style="border:1px solid #78350f;border-radius:8px;overflow:hidden">
      <div style="overflow-y:auto;max-height:360px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#4b5563;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">#</th>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Seller</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Reputacao</th>
              <th style="padding:6px 8px;text-align:center;color:#e5e7eb;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Total SHPs</th>
              <th style="padding:6px 8px;text-align:center;color:#fbbf24;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Fraudes</th>
              <th style="padding:6px 8px;text-align:center;color:#a78bfa;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Damaged</th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Meses</th>
              <th style="padding:6px 8px;text-align:left;color:#f97316;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Motivo</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">BPP</th>
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
var LOG_URL    = 'https://logistics.mercadolibre.com.br/shipments/';

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

function isSuspeito(s){{
  return s.r === 'SELLER NOT TRUSTED' || s.r === 'BOTH NOT TRUSTED'
    || (s.f > 0 && s.m <= 2)
    || (s.t > 0 && s.f / s.t >= 0.3);
}}

function motivoSusp(s){{
  var m = [];
  if(s.r === 'SELLER NOT TRUSTED' || s.r === 'BOTH NOT TRUSTED') m.push('Not Trusted');
  if(s.f > 0 && s.m <= 2) m.push('Fraude em '+s.m+' mes(es)');
  if(s.t > 0 && s.f / s.t >= 0.3) m.push((s.f/s.t*100).toFixed(0)+'% fraude');
  return m.join(' | ') || '-';
}}

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
  var filtrado = lista.filter(function(s){{return !q || s.n.toLowerCase().indexOf(q) >= 0;}});
  var ct = document.getElementById('sel-susp-count');
  if(ct) ct.textContent = '(' + filtrado.length.toLocaleString('pt-BR') + ' sellers)';
  var el = document.getElementById('sel-susp-tbody'); if(!el) return;
  el.innerHTML = filtrado.map(function(s, i){{
    var sel = _selSusp === s.n;
    var hl = sel ? 'background:#1a0a00;border-left:3px solid #f97316;' : 'border-left:3px solid transparent;';
    return '<tr style="border-bottom:1px solid #0a0f1e;'+hl+'">'
      + '<td style="padding:4px 8px;color:#374151;font-size:9px">'+(i+1)+'</td>'
      + '<td style="padding:4px 8px;color:#60a5fa;font-size:10px;font-weight:700;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+s.n+'">'+s.n+'</td>'
      + '<td style="padding:4px 8px">'+repBadge(s.r)+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#e5e7eb;font-weight:700">'+s.t+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#fbbf24">'+s.f+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#a78bfa">'+s.d+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#4b5563">'+s.m+'</td>'
      + '<td style="padding:4px 8px;color:#f97316;font-size:10px">'+motivoSusp(s)+'</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#f87171;font-size:10px">$'+s.b.toFixed(0)+'</td>'
      + '</tr>';
  }}).join('');
}}

window.selSuspFiltrar = function(){{
  _qSusp = (document.getElementById('sel-susp-busca') || {{}}).value || '';
  var susp = _sellers.filter(isSuspeito).sort(function(a,b){{return b.b - a.b;}});
  renderSuspTbody(susp);
}};

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
    renderSuspCharts(susp); renderSuspTbody(susp);
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
  var rows = [['Seller','Reputacao','Total','Damaged','Fraude','Meses','BPP USD','GMV USD']];
  _sellers.forEach(function(s){{ rows.push([s.n, s.r, s.t, s.d, s.f, s.m, s.b, s.g]); }});
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
