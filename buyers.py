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
FRAUDE_BUYERS_CACHE = Path(__file__).parent / '_fraude_buyers_conhecidos.json'


def _carregar_buyers_fraude_conhecidos():
    try:
        return set(json.loads(FRAUDE_BUYERS_CACHE.read_text(encoding='utf-8')).get('nicks', []))
    except Exception:
        return set()


def _salvar_buyers_fraude_conhecidos(nicks):
    try:
        FRAUDE_BUYERS_CACHE.write_text(
            json.dumps({'nicks': sorted(nicks), 'ts': datetime.now().isoformat()}, ensure_ascii=False),
            encoding='utf-8'
        )
    except Exception as e:
        print(f'  Aviso: não salvei cache de buyers fraude conhecidos: {e}')


# ── Query 1: buyers com fraude OU damaged (LP relevante) ────────────────────
# CTE pré-deduplica por SHP antes de agregar — evita contar o mesmo pacote N vezes
# Agrupado tambem por mes (igual sellers.py) pra filtro de periodo recalcular
# Historico/Suspeitos em vez de so filtrar quem aparece.
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
  FORMAT_DATE('%Y-%m', date_bpp)                                         AS mes,
  COUNT(*)                                                               AS total,
  ROUND(SUM(IFNULL(bpp_cashout_usd, 0)), 2)                             AS bpp,
  COUNTIF(tipo_fraude = 'FRAUDE BUYER')                                  AS n_fraude,
  COUNTIF(
    classification_lm LIKE 'DAMAGED%'
    OR tipo_damaged_lg IN (
      'DAMAGED','damaged_svc','damaged_on_route','damaged_seller','damaged','SELLER'
    )
  )                                                                      AS n_damaged
FROM shps
GROUP BY 1, 2
"""

# ── Query 1b: historico de compras do buyer (365d + janela de 14d) ─────────
# Mapeia nickname -> buyer_id via BT_SHP_SHIPMENTS (mesmo padrao do
# Q_CANCELAMENTO em sellers.py), depois busca TODO o historico de compra do
# buyer nos ultimos 365 dias (nao so os casos ja sinalizados pelo LP) pra
# detectar "comprador novo com valor alto" e "pico de compras em curto prazo".
Q_COMPRAS_BUYER = f"""
WITH buyer_map AS (
  SELECT
    lp.CUS_NICKNAME_BUY AS nickname,
    s.SHP_RECEIVER_ID AS buyer_id,
    COUNT(*) AS n
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO` lp
  INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` s
    ON CAST(s.SHP_SHIPMENT_ID AS STRING) = lp.SHIPMENT_ID
    AND s.SHP_DATE_CREATED_ID >= '{INICIO}'
  WHERE lp.SHP_LG_FACILITY_NAME = '{FACILITY}'
    AND lp.DATE_BPP >= '{INICIO}'
    AND lp.CUS_NICKNAME_BUY IS NOT NULL
  GROUP BY 1, 2
),
buyer_best AS (
  SELECT nickname, buyer_id FROM (
    SELECT nickname, buyer_id, ROW_NUMBER() OVER (PARTITION BY nickname ORDER BY n DESC) AS rn
    FROM buyer_map
  ) WHERE rn = 1
)
SELECT
  bb.nickname AS buyer,
  bb.buyer_id AS buyer_id,
  COUNT(*) AS total_365d,
  MIN(s.SHP_DATE_CREATED_ID) AS primeira_compra_365d,
  ROUND(SUM(s.SHP_ORDER_COST_USD),2) AS valor_365d,
  COUNTIF(s.SHP_DATE_CREATED_ID >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)) AS total_14d,
  ROUND(SUM(IF(s.SHP_DATE_CREATED_ID >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY), s.SHP_ORDER_COST_USD, 0)),2) AS valor_14d
FROM buyer_best bb
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` s
  ON s.SHP_RECEIVER_ID = bb.buyer_id
  AND s.SHP_DATE_CREATED_ID >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
GROUP BY 1, 2
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
"""


def carregar():
    creds, _ = default()
    client = bigquery.Client(credentials=creds, project='meli-bi-data')

    print('Consultando buyers agregados (por mes)...')
    buyers_raw = {}
    for r in client.query(Q_BUYERS).result():
        key = r['buyer']
        if key not in buyers_raw:
            buyers_raw[key] = {'n': key, 'md': []}
        buyers_raw[key]['md'].append({
            'mes': r['mes'],
            't': int(r['total']),
            'b': float(r['bpp'] or 0),
            'f': int(r['n_fraude']),
            'd': int(r['n_damaged']),
        })

    buyers = []
    for key, obj in buyers_raw.items():
        md = sorted(obj['md'], key=lambda x: x['mes'])
        meses_list = [m['mes'] for m in md]
        n_fraude = sum(m['f'] for m in md)
        n_damaged = sum(m['d'] for m in md)
        if n_fraude <= 0 and n_damaged <= 0:
            continue
        buyers.append({
            'n':   key,
            't':   sum(m['t'] for m in md),
            'b':   sum(m['b'] for m in md),
            'f':   n_fraude,
            'd':   n_damaged,
            'p':   meses_list[0] if meses_list else '',
            'u':   meses_list[-1] if meses_list else '',
            'm':   len(meses_list),
            'md':  md,
        })
    buyers.sort(key=lambda b: b['b'], reverse=True)
    print(f'  {len(buyers):,} buyers')

    print('Consultando historico de compras (365d + 14d)...')
    compras_map = {}
    for r in client.query(Q_COMPRAS_BUYER).result():
        compras_map[r['buyer']] = {
            't365': int(r['total_365d']),
            'v365': float(r['valor_365d'] or 0),
            't14':  int(r['total_14d']),
            'v14':  float(r['valor_14d'] or 0),
            'bid':  str(r['buyer_id']) if r['buyer_id'] is not None else '',
        }
    print(f'  {len(compras_map):,} buyers com dado de compras')
    for b in buyers:
        cm = compras_map.get(b['n'])
        b['t365'] = cm['t365'] if cm else 0
        b['v365'] = cm['v365'] if cm else 0.0
        b['t14']  = cm['t14']  if cm else 0
        b['v14']  = cm['v14']  if cm else 0.0
        b['bid']  = cm['bid']  if cm else ''

    # ── Sinais de risco proativos (antes/alem de fraude ja confirmada) ──────
    # Comprador novo com valor alto: poucos pedidos no ano (<=3) e valor ja
    # alto. Pico de compras: concentracao de valor nos ultimos 14 dias.
    # Mantem no payload todo buyer ja flagueado (fraude/damaged) + qualquer
    # outro que bata em algum desses sinais, mesmo sem fraude confirmada
    # ainda — e o que permite pegar tendencia antes de virar caso.
    def _tem_sinal_compra(cm):
        if cm['t365'] > 0 and cm['t365'] <= 3 and cm['v365'] >= 300:
            return True
        if cm['v365'] > 0 and cm['v14'] >= 200 and (cm['v14'] / cm['v365']) >= 0.5:
            return True
        return False

    buyers_fraud_nicks = {b['n'] for b in buyers}
    compras = []
    for nick, cm in compras_map.items():
        if nick in buyers_fraud_nicks or _tem_sinal_compra(cm):
            compras.append({'n': nick, **cm})
    print(f'  {len(compras):,} buyers com sinal de risco (fraude/damaged ja confirmado ou padrao de compra suspeito)')

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

    nicks_fraude = {s['n'] for s in shps_fraude}
    nicks_conhecidos = _carregar_buyers_fraude_conhecidos()
    novos = nicks_fraude - nicks_conhecidos
    for s in shps_fraude:
        s['novo'] = s['n'] in novos
    print(f'  {len(novos)} buyer(s) NOVO(S) na fraude detectado(s)')
    _salvar_buyers_fraude_conhecidos(nicks_fraude)

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

    return buyers, shps_fraude, shps_damaged, compras


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


def gerar_tab(buyers, shps_fraude, shps_damaged, compras, kpis):
    now          = datetime.now().strftime('%d/%m/%Y %H:%M')
    buyers_json  = json.dumps(buyers,       ensure_ascii=False)
    fraude_json  = json.dumps(shps_fraude,  ensure_ascii=False)
    damaged_json = json.dumps(shps_damaged, ensure_ascii=False)
    compras_json = json.dumps(compras,      ensure_ascii=False)

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
    <div style="display:flex;gap:2px;background:#080d19;border-radius:6px;padding:2px">
      <button class="buy-period-chip" data-months="1" onclick="buySetPeriodChip(1)"
        style="padding:4px 9px;font-size:10px;color:#6b7280;border-radius:4px;cursor:pointer;background:transparent;border:none">1m</button>
      <button class="buy-period-chip" data-months="3" onclick="buySetPeriodChip(3)"
        style="padding:4px 9px;font-size:10px;color:#6b7280;border-radius:4px;cursor:pointer;background:transparent;border:none">3m</button>
      <button class="buy-period-chip" data-months="6" onclick="buySetPeriodChip(6)"
        style="padding:4px 9px;font-size:10px;color:#6b7280;border-radius:4px;cursor:pointer;background:transparent;border:none">6m</button>
      <button class="buy-period-chip active" data-months="0" onclick="buySetPeriodChip(0)"
        style="padding:4px 9px;font-size:10px;color:#04303a;background:#22d3ee;border-radius:4px;cursor:pointer;border:none;font-weight:700">Tudo</button>
    </div>
    <span style="color:#1f2937">|</span>
    <input id="buy-de" type="month" onchange="buyCustomPeriod()"
      style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 8px;font-size:11px">
    <span style="font-size:11px;color:#4b5563">ate</span>
    <input id="buy-ate" type="month" onchange="buyCustomPeriod()"
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
    <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap">
      <div style="background:#1a0505;border:1px solid #7f1d1d;border-radius:8px;padding:8px 16px;flex:1;min-width:150px;text-align:center">
        <div id="buy-fr-fraudconf" style="font-size:20px;font-weight:700;color:#f87171">0</div>
        <div style="font-size:9px;color:#6b7280;margin-top:2px">Fraude ja confirmada</div>
      </div>
      <div style="background:#1a0f00;border:1px solid #78350f;border-radius:8px;padding:8px 16px;flex:1;min-width:150px;text-align:center">
        <div id="buy-fr-novoalto" style="font-size:20px;font-weight:700;color:#fbbf24">0</div>
        <div style="font-size:9px;color:#6b7280;margin-top:2px">Comprador novo + valor alto</div>
      </div>
      <div style="background:#0a0f1e;border:1px solid #1f2937;border-radius:8px;padding:8px 16px;flex:1;min-width:150px;text-align:center">
        <div id="buy-fr-pico" style="font-size:20px;font-weight:700;color:#a78bfa">0</div>
        <div style="font-size:9px;color:#6b7280;margin-top:2px">Pico recente de compras</div>
      </div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Buyers ofensores</span>
        <span style="font-size:10px;color:#374151;margin-left:6px">ordenado por score (clique nos cabecalhos pra mudar) · clique na linha pra ver os shipments abaixo</span>
        <span id="buy-fr-ofens-header" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
      <input id="buy-fr-busca" type="text" placeholder="Buscar buyer ou SHP..."
        oninput="buyFrFiltrar()"
        style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:11px;width:200px">
    </div>
    <div style="border:1px solid #1f2937;border-radius:8px;overflow:hidden;margin-bottom:14px">
      <div style="overflow-y:auto;max-height:320px;background:#060a14">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead style="position:sticky;top:0;z-index:2;background:#0d1321">
            <tr>
              <th style="padding:6px 8px;text-align:left;color:#38bdf8;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="buyFrSort('n')">Buyer <span id="buy-fr-sort-n"></span></th>
              <th style="padding:6px 8px;text-align:center;color:#fbbf24;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" title="Probabilidade de fraude: fraude confirmada + comprador novo com valor alto + pico de compras" onclick="buyFrSort('score')">Score <span id="buy-fr-sort-score"></span></th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="buyFrSort('qtd')">Qtd Fraude <span id="buy-fr-sort-qtd"></span></th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Sinal predominante</th>
              <th style="padding:6px 8px;text-align:right;color:#f87171;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="buyFrSort('b')">BPP <span id="buy-fr-sort-b"></span></th>
              <th style="padding:6px 8px;text-align:center;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="buyFrSort('t365')" title="Pedidos nos ultimos 365 dias">Pedidos 365d <span id="buy-fr-sort-t365"></span></th>
              <th style="padding:6px 8px;text-align:right;color:#4ade80;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937;cursor:pointer" onclick="buyFrSort('v365')" title="Valor comprado nos ultimos 365 dias (USD)">Valor 365d <span id="buy-fr-sort-v365"></span></th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Status</th>
              <th style="padding:6px 8px;text-align:left;color:#6b7280;font-size:9px;text-transform:uppercase;border-bottom:1px solid #1f2937">Acompanhamento</th>
            </tr>
          </thead>
          <tbody id="buy-fr-ofens-tbody"></tbody>
        </table>
      </div>
    </div>
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px;margin-bottom:14px;max-width:420px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Classificacao das Fraudes Buyer</div>
      <div style="position:relative;height:200px"><canvas id="buy-cht-fr-tipo"></canvas></div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <span style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px">Shipments Fraude Buyer</span>
        <span id="buy-fr-header" style="font-size:10px;color:#4b5563;margin-left:6px"></span>
      </div>
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
var BUY_DATA    = {buyers_json};
var SHP_FRAUDE  = {fraude_json};
var SHP_DAMAGE  = {damaged_json};
var COMPRAS_DATA = {compras_json};
var LOG_URL    = 'https://shipping-bo.adminml.com/sauron/shipments/shipment/';

var _abaAtual = 'historico';
var _buySel   = null;
var _buySusp  = null;
var _qHist='', _qDmg='', _qFr='', _qSusp='';

var _buyers  = BUY_DATA;
var _fraudes = SHP_FRAUDE;
var _damages = SHP_DAMAGE;

var BUY_ID_MAP = {{}}, BUY_HIST_MAP = {{}};
COMPRAS_DATA.forEach(function(c){{
  if(c.bid) BUY_ID_MAP[c.n] = c.bid;
  BUY_HIST_MAP[c.n] = {{t365:c.t365||0, v365:c.v365||0, t14:c.t14||0, v14:c.v14||0}};
}});
function buyIdSuffix(nick){{
  var bid = BUY_ID_MAP[nick];
  return bid ? ' <span style="color:#4b5563;font-weight:400">(ID '+bid+')</span>' : '';
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
// ── Status de investigacao + Acompanhamento (historico de acoes) ───────────
var FR_ST_CYCLE = ['ati','inv','enc','res'];
var FR_ST_LBL = {{ati:'Ativo',inv:'Em investigacao',enc:'Encaminhado',res:'Resolvido'}};
var FR_ST_CLR = {{ati:'#9ca3af',inv:'#fbbf24',enc:'#f87171',res:'#4ade80'}};
var FR_ST_BG  = {{ati:'rgba(156,163,175,.1)',inv:'rgba(251,191,36,.12)',enc:'rgba(239,68,68,.12)',res:'rgba(74,222,128,.12)'}};
function buyGetFrSt(nick){{
  try {{
    var s = localStorage.getItem('buyfr_st_'+nick);
    if (s !== null) return FR_ST_LBL[s] ? s : 'ati';
  }} catch(e) {{}}
  return 'ati';
}}
function buyToggleFrSt(nick){{
  var cur = buyGetFrSt(nick);
  var next = FR_ST_CYCLE[(FR_ST_CYCLE.indexOf(cur)+1) % FR_ST_CYCLE.length];
  try {{ localStorage.setItem('buyfr_st_'+nick, next); }} catch(e) {{}}
  renderFrOfensores();
}}
window.buyToggleFrSt = buyToggleFrSt;

var _buyFrHistOpen = null;
function buyGetFrHist(nick){{
  try {{
    var raw = localStorage.getItem('buyfr_hist_'+nick);
    if (raw) return JSON.parse(raw);
  }} catch(e) {{}}
  return [];
}}
function _buySaveFrHist(nick, hist){{
  try {{ localStorage.setItem('buyfr_hist_'+nick, JSON.stringify(hist)); }} catch(e) {{}}
}}
window.buyToggleFrHist = function(nick){{
  _buyFrHistOpen = (_buyFrHistOpen === nick) ? null : nick;
  renderFrOfensores();
}};
window.buyAddFrHist = function(nick){{
  var el = document.getElementById('buyfr-hist-input-'+nick);
  if(!el) return;
  var text = el.value.trim();
  if(!text) return;
  var hist = buyGetFrHist(nick);
  hist.push({{ts: new Date().toISOString(), text: text}});
  _buySaveFrHist(nick, hist);
  renderFrOfensores();
}};
function _buyFrHistPanelHtml(nick){{
  var hist = buyGetFrHist(nick);
  var items = hist.map(function(h){{
    var d = new Date(h.ts);
    var ds = isNaN(d) ? '' : d.toLocaleString('pt-BR',{{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}});
    return '<div style="display:flex;gap:10px;margin-bottom:8px">'
      + '<div style="width:6px;display:flex;flex-direction:column;align-items:center"><div style="width:6px;height:6px;border-radius:50%;background:#38bdf8;margin-top:4px"></div></div>'
      + '<div style="flex:1"><div style="font-size:9px;color:#4b5563">'+ds+'</div><div style="font-size:11px;color:#e5e7eb">'+String(h.text).replace(/</g,'&lt;')+'</div></div>'
      + '</div>';
  }}).join('');
  return '<div style="background:#0a0f1e;border-top:1px solid #1f2937;border-bottom:1px solid #1f2937;padding:12px 20px">'
    + '<div style="font-size:9px;font-weight:700;color:#6b7280;text-transform:uppercase;margin-bottom:8px">Historico — '+nick+'</div>'
    + (items || '<div style="font-size:10px;color:#374151;margin-bottom:8px">Nenhum registro ainda.</div>')
    + '<div style="display:flex;gap:6px;margin-top:4px">'
    + '<input id="buyfr-hist-input-'+nick+'" type="text" placeholder="Adicionar novo registro..." onkeydown="if(event.key===\\'Enter\\'){{buyAddFrHist(\\''+nick+'\\');}}" style="flex:1;background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:6px 10px;font-size:11px">'
    + '<button onclick="buyAddFrHist(\\''+nick+'\\')" style="background:rgba(37,99,235,.15);border:1px solid rgba(37,99,235,.4);color:#93c5fd;font-size:11px;padding:6px 14px;border-radius:5px;cursor:pointer;font-family:inherit">+ Adicionar</button>'
    + '</div></div>';
}}

// ── Score de risco e ordenacao ──────────────────────────────────────────
var _buyFrSortKey = 'score';
var _buyFrSortDir = -1;
window.buyFrSort = function(key){{
  if(_buyFrSortKey === key) _buyFrSortDir = -_buyFrSortDir;
  else {{ _buyFrSortKey = key; _buyFrSortDir = key === 'n' ? 1 : -1; }}
  renderFrOfensores();
}};

function _buyScore(qtd, dQtd, hist){{
  var novoAltoValor = (hist.t365 > 0 && hist.t365 <= 3) ? Math.min(hist.v365 / 10, 40) : 0;
  var pico = hist.v365 > 0 ? Math.min((hist.v14 / hist.v365) * 25, 25) : 0;
  var fraudeConfirmada = Math.min(qtd * 15, 45) + Math.min(dQtd * 5, 15);
  return Math.round((novoAltoValor + pico + fraudeConfirmada) * 10) / 10;
}}
function _buySinal(qtd, dQtd, hist){{
  if(qtd > 0) return 'Fraude confirmada';
  if(hist.t365 > 0 && hist.t365 <= 3 && hist.v365 >= 300) return 'Novo + alto valor';
  if(hist.v365 > 0 && hist.v14 >= 200 && (hist.v14/hist.v365) >= 0.5) return 'Pico recente de compras';
  if(dQtd > 0) return 'Damaged';
  return 'Outro';
}}

function _buyFrAgg(){{
  var byBuyer = {{}};
  _fraudes.forEach(function(s){{
    if(!byBuyer[s.n]) byBuyer[s.n] = {{qtd:0, b:0, novo:false}};
    byBuyer[s.n].qtd++;
    byBuyer[s.n].b += (s.b||0);
    if(s.novo) byBuyer[s.n].novo = true;
  }});
  Object.keys(BUY_HIST_MAP).forEach(function(nick){{
    if(!byBuyer[nick]) byBuyer[nick] = {{qtd:0, b:0, novo:false}};
  }});
  var buyByNick = {{}};
  BUY_DATA.forEach(function(b){{ buyByNick[b.n] = b; }});
  var lista = Object.keys(byBuyer).map(function(nick){{
    var o = byBuyer[nick];
    var hist = BUY_HIST_MAP[nick] || {{t365:0,v365:0,t14:0,v14:0}};
    var bfull = buyByNick[nick];
    var dQtd = bfull ? bfull.d : 0;
    return {{
      n: nick, qtd: o.qtd, b: o.b, novo: o.novo,
      t365: hist.t365, v365: hist.v365, t14: hist.t14, v14: hist.v14,
      score: _buyScore(o.qtd, dQtd, hist),
      sinal: _buySinal(o.qtd, dQtd, hist),
    }};
  }});
  var key = _buyFrSortKey, dir = _buyFrSortDir;
  lista.sort(function(a,b){{
    if(key === 'n') return a.n.localeCompare(b.n) * dir;
    var av = a[key]==null ? -1 : a[key], bv = b[key]==null ? -1 : b[key];
    return (av-bv) * dir;
  }});
  return lista;
}}

function renderFrCharts(){{
  var byC = {{}};
  _fraudes.forEach(function(s){{ var k = s.c || 'Outro'; byC[k] = (byC[k] || 0) + 1; }});
  var topC = Object.entries(byC).sort(function(a,b){{return b[1]-a[1];}}).slice(0,8);
  mkChart('buy-cht-fr-tipo', topC.map(function(x){{return x[0];}}), topC.map(function(x){{return x[1];}}), '#fbbf24', null);
}}

function renderFrOfensores(){{
  var q = _qFr.toLowerCase();
  var lista = _buyFrAgg();
  if(q) lista = lista.filter(function(s){{return s.n.toLowerCase().indexOf(q)>=0;}});
  var hdr = document.getElementById('buy-fr-ofens-header');
  if(hdr) hdr.textContent = '- ' + lista.length.toLocaleString('pt-BR') + ' buyers';
  ['n','score','qtd','b','t365','v365'].forEach(function(k){{
    var e = document.getElementById('buy-fr-sort-'+k);
    if(e) e.textContent = (_buyFrSortKey===k) ? (_buyFrSortDir===1?'▲':'▼') : '';
  }});
  var fConf = 0, fNovo = 0, fPico = 0;
  lista.forEach(function(s){{
    if(s.sinal === 'Fraude confirmada') fConf++;
    else if(s.sinal === 'Novo + alto valor') fNovo++;
    else if(s.sinal === 'Pico recente de compras') fPico++;
  }});
  var elC = document.getElementById('buy-fr-fraudconf'); if(elC) elC.textContent = fConf.toLocaleString('pt-BR');
  var elN = document.getElementById('buy-fr-novoalto'); if(elN) elN.textContent = fNovo.toLocaleString('pt-BR');
  var elP = document.getElementById('buy-fr-pico'); if(elP) elP.textContent = fPico.toLocaleString('pt-BR');

  var el = document.getElementById('buy-fr-ofens-tbody'); if(!el) return;
  var truncado = lista.length > 500;
  var listaRender = truncado ? lista.slice(0, 500) : lista;
  el.innerHTML = listaRender.map(function(s){{
    var sel = _buySel === s.n;
    var hl = sel ? 'background:#0f2040;border-left:3px solid #38bdf8;' : 'border-left:3px solid transparent;';
    var st = buyGetFrSt(s.n);
    var sinalCls = s.sinal === 'Fraude confirmada' ? 'color:#f87171' : (s.sinal === 'Novo + alto valor' ? 'color:#fbbf24' : (s.sinal === 'Pico recente de compras' ? 'color:#a78bfa' : 'color:#6b7280'));
    var scoreCls = s.score >= 40 ? 'color:#f87171;font-weight:700' : (s.score >= 20 ? 'color:#fbbf24;font-weight:700' : 'color:#6b7280');
    var novoBadge = s.novo ? '<span style="display:inline-block;font-size:8px;font-weight:800;padding:1px 5px;border-radius:10px;margin-left:5px;vertical-align:middle;background:#fbbf24;color:#1a1a1a;letter-spacing:.4px">NOVO</span>' : '';
    var histOpen = _buyFrHistOpen === s.n;
    var hist = buyGetFrHist(s.n);
    var histStyle = hist.length ? 'background:rgba(37,99,235,.12);border:1px solid rgba(37,99,235,.35);color:#93c5fd;' : 'background:#1f2937;border:1px solid #374151;color:#6b7280;';
    var histLabel = hist.length ? (hist.length + ' registro' + (hist.length>1?'s':'')) : 'sem registros';
    var row = '<tr style="border-bottom:1px solid #080c18;'+hl+'cursor:pointer" onclick="buyFrSelecionar(\\''+s.n+'\\')">'
      + '<td style="padding:4px 8px;color:#60a5fa;font-weight:700;max-width:190px;white-space:nowrap"><span style="display:inline-block;max-width:'+(s.novo?'135px':'175px')+';overflow:hidden;text-overflow:ellipsis;vertical-align:middle" title="'+s.n+'">'+s.n+buyIdSuffix(s.n)+'</span>'+novoBadge+'</td>'
      + '<td style="padding:4px 8px;text-align:center;'+scoreCls+'">'+s.score+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#e5e7eb;font-weight:700">'+s.qtd+'</td>'
      + '<td style="padding:4px 8px;font-size:10px;'+sinalCls+'">'+s.sinal+'</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#f87171">$'+s.b.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</td>'
      + '<td style="padding:4px 8px;text-align:center;color:#9ca3af">'+s.t365+'</td>'
      + '<td style="padding:4px 8px;text-align:right;color:#4ade80">$'+s.v365.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</td>'
      + '<td style="padding:4px 8px" onclick="event.stopPropagation()"><span onclick="buyToggleFrSt(\\''+s.n+'\\')" style="cursor:pointer;font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px;color:'+FR_ST_CLR[st]+';background:'+FR_ST_BG[st]+'">'+FR_ST_LBL[st]+'</span></td>'
      + '<td style="padding:4px 8px" onclick="event.stopPropagation()"><button onclick="buyToggleFrHist(\\''+s.n+'\\')" style="'+histStyle+'font-size:10px;padding:4px 10px;border-radius:5px;cursor:pointer;font-family:inherit;white-space:nowrap">&#128337; '+histLabel+' '+(histOpen?'▲':'▼')+'</button></td>'
      + '</tr>';
    if(histOpen){{
      row += '<tr onclick="event.stopPropagation()" style="cursor:default"><td colspan="9" style="padding:0">'+_buyFrHistPanelHtml(s.n)+'</td></tr>';
    }}
    return row;
  }}).join('') + (truncado ? '<tr><td colspan="9" style="padding:8px;text-align:center;color:#374151;font-size:10px">Mostrando 500 de '+lista.length.toLocaleString('pt-BR')+' (ordenado por '+_buyFrSortKey+'). Use a busca ou ordene por outra coluna pra refinar.</td></tr>' : '');
}}

window.buyFrSelecionar = function(nick){{
  _buySel = (_buySel === nick) ? null : nick;
  renderFrTbody();
  renderFrOfensores();
}};

function renderFrTbody(){{
  var q = _qFr.toLowerCase();
  var base = _buySel ? _fraudes.filter(function(s){{return s.n === _buySel;}}) : _fraudes;
  var filtrado = q ? base.filter(function(s){{return s.n.toLowerCase().indexOf(q)>=0||s.s.indexOf(q)>=0;}}) : base;
  var hdr = document.getElementById('buy-fr-header');
  if(hdr) hdr.innerHTML = _buySel
    ? '- ' + _buySel + ' (' + filtrado.length + ' SHPs) <span onclick="buyFrSelecionar(\\''+_buySel+'\\')" style="cursor:pointer;color:#60a5fa;margin-left:6px">&times; limpar selecao</span>'
    : '- ' + filtrado.length.toLocaleString('pt-BR') + ' shipments';
  var el = document.getElementById('buy-fr-tbody'); if(!el) return;
  el.innerHTML = filtrado.map(function(s){{
    return '<tr style="border-bottom:1px solid #080c18">'
      + '<td style="padding:4px 8px;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#60a5fa;font-size:10px;font-weight:600" title="'+s.n+'">'+s.n+buyIdSuffix(s.n)+'</td>'
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
  renderFrOfensores();
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

// Recalcula t/b/f/d do buyer somando so os meses dentro do periodo filtrado
// (em vez de usar o acumulado jan-ago inteiro) — mesma logica de sellers.py.
function _somaPeriodoBuy(b, pDe, pAte){{
  if(!pDe && !pAte) return b;
  var md = (b.md||[]).filter(function(m){{ return (!pDe||m.mes>=pDe) && (!pAte||m.mes<=pAte); }});
  var t=0,bpp=0,f=0,d=0;
  md.forEach(function(m){{ t+=m.t; bpp+=m.b; f+=m.f; d+=m.d; }});
  return Object.assign({{}}, b, {{t:t, b:bpp, f:f, d:d, m: md.length}});
}}

function buySetPeriodChip(months){{
  var deEl = document.getElementById('buy-de');
  var ateEl = document.getElementById('buy-ate');
  if(!deEl || !ateEl) return;
  if(months === 0){{
    deEl.value = ''; ateEl.value = '';
  }} else {{
    var now = new Date();
    var ateY = now.getFullYear(), ateM = now.getMonth()+1;
    var d = new Date(now.getFullYear(), now.getMonth() - (months-1), 1);
    var deY = d.getFullYear(), deM = d.getMonth()+1;
    ateEl.value = ateY + '-' + String(ateM).padStart(2,'0');
    deEl.value  = deY  + '-' + String(deM).padStart(2,'0');
  }}
  document.querySelectorAll('.buy-period-chip').forEach(function(b) {{
    var isActive = b.dataset.months == months;
    b.classList.toggle('active', isActive);
    b.style.background = isActive ? '#22d3ee' : 'transparent';
    b.style.color = isActive ? '#04303a' : '#6b7280';
    b.style.fontWeight = isActive ? '700' : '400';
  }});
  buyAplicar();
}}
function buyCustomPeriod(){{
  document.querySelectorAll('.buy-period-chip').forEach(function(b) {{
    b.classList.remove('active'); b.style.background = 'transparent'; b.style.color = '#6b7280'; b.style.fontWeight = '400';
  }});
  buyAplicar();
}}

function buyAplicar(){{
  var pDe  = (document.getElementById('buy-de')  || {{}}).value || '';
  var pAte = (document.getElementById('buy-ate') || {{}}).value || '';
  var lb = document.getElementById('buy-periodo-label');
  if(lb) lb.textContent = (pDe || pAte) ? ('Periodo: ' + (pDe||'inicio') + ' a ' + (pAte||'hoje')) : '';

  _buyers  = BUY_DATA.filter(function(b){{
    return (!pDe || b.u >= pDe) && (!pAte || b.p <= pAte);
  }}).map(function(b){{ return _somaPeriodoBuy(b, pDe, pAte); }});
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
    renderFrCharts(); renderFrOfensores(); renderFrTbody();
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
window.buySetPeriodChip = buySetPeriodChip;
window.buyCustomPeriod  = buyCustomPeriod;
window.buyLimpar = function(){{
  ['buy-de','buy-ate'].forEach(function(id){{ var e = document.getElementById(id); if(e) e.value = ''; }});
  buySetPeriodChip(0);
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
    buyers, shps_fraude, shps_damaged, compras = carregar()
    kpis = static_kpis(buyers, shps_fraude, shps_damaged)
    print(f'  Suspeitos: {kpis["suspeitos"]:,}')

    print('Gerando HTML...')
    tab_html = gerar_tab(buyers, shps_fraude, shps_damaged, compras, kpis)

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
