# ============================================================
# analise_fraude.py — Dashboard de Análise de Fraude SSP30
# Como rodar: duplo clique em abrir_analise_fraude.bat
# ============================================================

import json, webbrowser, os
from datetime import datetime
from google.cloud import bigquery
from google.auth import default

FACILITY_NAME = 'Guarulhos Mega'   # SHP_LG_FACILITY_NAME em DM_LP_MELI_OPTIMIZADO
ANO_INICIO    = '2026-01-01'
OUTPUT     = os.path.join(os.path.dirname(__file__), 'fraude.html')

# ============================================================
# QUERIES
# ============================================================
QUERY_DRIVER_SCORE = f"""
-- Score combinado por driver — usa DRIVER_ID direto da tabela (sem join)
SELECT
    SAFE_CAST(DRIVER_ID AS STRING)                                           AS DRIVER_ID,
    COUNT(DISTINCT SHIPMENT_ID)                                              AS TOTAL_INCIDENTES,
    ROUND(SUM(BPP_CASHOUT_USD), 2)                                           AS TOTAL_BPP,
    COUNTIF(Classification_LM IN (
        'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
        'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE'))                     AS TOTAL_FRAUDE,
    COUNTIF(Classification_LM LIKE 'DAMAGED%')                              AS TOTAL_DAMAGED,
    COUNTIF(Classification_LM LIKE 'FRAUD%')                                AS FRAUD_CONFIRMADO
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND date_bpp >= '{ANO_INICIO}'
  AND date_bpp <= CURRENT_DATE()
  AND DRIVER_ID IS NOT NULL
GROUP BY 1
ORDER BY TOTAL_INCIDENTES DESC
LIMIT 60
"""

QUERY_DRIVER_SHIPMENTS = f"""
-- Todos os SHP IDs por driver para exibir no dashboard
SELECT
    SAFE_CAST(DRIVER_ID AS STRING)       AS DRIVER_ID,
    CAST(SHIPMENT_ID AS STRING)          AS SHP_ID,
    Classification_LM                    AS CLASSIFICACAO,
    ROUND(BPP_CASHOUT_USD, 2)            AS BPP,
    FORMAT_DATE('%d/%m/%Y', date_bpp)    AS DATA,
    FORMAT_DATE('%Y-W%V', date_bpp)      AS SEMANA
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND date_bpp >= '{ANO_INICIO}'
  AND date_bpp <= CURRENT_DATE()
  AND DRIVER_ID IS NOT NULL
ORDER BY SAFE_CAST(DRIVER_ID AS INT64), BPP_CASHOUT_USD DESC
"""

QUERY_DRIVER_PLACE = f"""
-- Driver x Place — usa DRIVER_ID direto (sem join com checkpoints)
WITH fraud_driver AS (
    SELECT
        SAFE_CAST(DRIVER_ID AS STRING)      AS DRIVER_ID,
        SAFE_CAST(SHIPMENT_ID AS STRING)    AS SHP_SHIPMENT_ID,
        Classification_LM,
        ROUND(BPP_CASHOUT_USD, 2)           AS BPP_CASHOUT_USD
    FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
    WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
      AND date_bpp >= '{ANO_INICIO}'
      AND date_bpp <= CURRENT_DATE()
      AND DRIVER_ID IS NOT NULL
)
SELECT
    fd.DRIVER_ID,
    p.SHP_AGENCY_ID,
    p.SHP_AGEN_DESC                                                              AS PLACE_NOME,
    COUNT(DISTINCT fd.SHP_SHIPMENT_ID)                                           AS INCIDENTES_EM_COMUM,
    COUNTIF(fd.Classification_LM LIKE 'LOST%' OR fd.Classification_LM LIKE 'FRAUD%') AS FRAUDES,
    COUNTIF(fd.Classification_LM LIKE 'DAMAGED%')                               AS DAMAGED,
    ROUND(SUM(fd.BPP_CASHOUT_USD), 2)                                            AS TOTAL_BPP
FROM fraud_driver fd
JOIN `meli-bi-data.WHOWNER.BT_SHP_PLACES_AND_NODES` p
    ON CAST(p.SHP_SHIPMENT_ID AS STRING) = fd.SHP_SHIPMENT_ID
GROUP BY 1, 2, 3
HAVING INCIDENTES_EM_COMUM >= 2
ORDER BY INCIDENTES_EM_COMUM DESC
LIMIT 80
"""

QUERY_PLACES = f"""
-- Ranking de places por fraudes (LOST + FRAUD apenas)
WITH fraudes AS (
    SELECT SAFE_CAST(SHIPMENT_ID AS STRING) AS SHP_SHIPMENT_ID,
           Classification_LM,
           ROUND(BPP_CASHOUT_USD, 2) AS BPP_CASHOUT_USD
    FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
    WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
      AND date_bpp >= '{ANO_INICIO}'
      AND date_bpp <= CURRENT_DATE()
      AND Classification_LM IN (
          'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
          'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE')
)
SELECT
    p.SHP_AGENCY_ID,
    p.SHP_AGEN_DESC                                             AS PLACE_NOME,
    COUNT(DISTINCT f.SHP_SHIPMENT_ID)                           AS TOTAL,
    ROUND(SUM(f.BPP_CASHOUT_USD), 2)                            AS TOTAL_BPP,
    COUNTIF(f.Classification_LM = 'LOST ON ROUTE')              AS LOST_ON_ROUTE,
    COUNTIF(f.Classification_LM = 'LOST ON WAY')                AS LOST_ON_WAY,
    COUNTIF(f.Classification_LM = 'LOST AT STATION')            AS LOST_AT_STATION,
    COUNTIF(f.Classification_LM = 'LOST ENE')                   AS LOST_ENE,
    COUNTIF(f.Classification_LM LIKE 'FRAUD%')                  AS FRAUD_CONFIRMADO
FROM fraudes f
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_PLACES_AND_NODES` p
    ON CAST(p.SHP_SHIPMENT_ID AS STRING) = f.SHP_SHIPMENT_ID
GROUP BY 1, 2
ORDER BY TOTAL DESC
LIMIT 50
"""

QUERY_DRIVER_STATUS = f"""
-- Status e categoria de todos os drivers da nossa análise de fraude
WITH loyalty AS (
  SELECT
    crowd_driver_id AS driverid,
    CASE
      WHEN scenarios.last_mile_crowd.progress.value = 1 THEN 'Bronze'
      WHEN scenarios.last_mile_crowd.progress.value = 2 THEN 'Prata'
      WHEN scenarios.last_mile_crowd.progress.value = 3 THEN 'Ouro'
      WHEN scenarios.last_mile_crowd.progress.value = 4 THEN 'Platina'
      ELSE 'N/A'
    END AS lealdade
  FROM `meli-bi-data.WHOWNER.BT_SHP_MT_METRICS_LOYALTY` l
  LEFT JOIN UNNEST(l.player.profiles.last_mile_crowd) AS crowd_driver_id
  WHERE l.period_monthly = TRUE
    AND l.player.scenarios.is_last_mile_crowd = TRUE
    AND l.site_id = 'MLB'
    AND l.period_id = EXTRACT(YEAR FROM DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH)) * 100
                    + EXTRACT(MONTH FROM DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH))
),
status_driver AS (
  SELECT
    r.DRIVER_ID,
    s.SHP_CROWD_STATUS                AS STATUS,
    s.SHP_CROWD_SUBSTATUS             AS SUBSTATUS,
    DATE(MIN(r.CREATED_AT))           AS DATA_ATIVACAO,
    DATE_DIFF(CURRENT_DATE(), DATE(MIN(r.CREATED_AT)), DAY) AS DIAS_OPERACAO
  FROM `meli-bi-data.WHOWNER.BT_SHP_CROWD_TRACKER_REGIST` AS r
  LEFT JOIN `meli-bi-data.WHOWNER.BT_SHP_CROWD_DRIVER_REG_STATUS` AS ds
    ON r.DRIVER_ID = ds.SHP_CROWD_DRIVER_ID
  LEFT JOIN `meli-bi-data.WHOWNER.BT_SHP_CROWD_REG_STATUS` AS s
    ON ds.SHP_CROWD_STATUS_ID = s.SHP_CROWD_ID
  WHERE r.SITE = 'MLB'
  GROUP BY 1, 2, 3
),
fraud_drivers AS (
  SELECT DISTINCT SAFE_CAST(DRIVER_ID AS STRING) AS DRIVER_ID,
    MIN(date_bpp) AS PRIMEIRA_FRAUDE,
    MAX(date_bpp) AS ULTIMA_FRAUDE,
    COUNT(*) AS TOTAL_INCIDENTES_HIST
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
    AND date_bpp >= '{ANO_INICIO}'
    AND DRIVER_ID IS NOT NULL
  GROUP BY 1
)
SELECT
  sd.DRIVER_ID,
  sd.STATUS,
  sd.SUBSTATUS,
  sd.DATA_ATIVACAO,
  sd.DIAS_OPERACAO,
  loy.lealdade                        AS CATEGORIA,
  fd.PRIMEIRA_FRAUDE,
  fd.ULTIMA_FRAUDE,
  fd.TOTAL_INCIDENTES_HIST
FROM status_driver sd
INNER JOIN fraud_drivers fd ON CAST(sd.DRIVER_ID AS STRING) = fd.DRIVER_ID
LEFT JOIN loyalty loy ON CAST(sd.DRIVER_ID AS STRING) = CAST(loy.driverid AS STRING)
ORDER BY sd.STATUS, fd.TOTAL_INCIDENTES_HIST DESC
"""

QUERY_DRIVER_ROUTES = f"""
-- Última rota, transportadora e atividade dos drivers da análise de fraude
SELECT
    CAST(r.SHP_LG_DRIVER_ID AS STRING)                                          AS DRIVER_ID,
    c.SHP_COMPANY_NAME                                                           AS TRANSPORTADORA,
    MAX(DATE(r.SHP_LG_ROUTE_INIT_DATE))                                         AS ULTIMA_ROTA,
    DATE_DIFF(CURRENT_DATE(), MAX(DATE(r.SHP_LG_ROUTE_INIT_DATE)), DAY)         AS DIAS_SEM_ROTA,
    COUNT(DISTINCT r.SHP_LG_ROUTE_ID)                                            AS ROTAS_ANO
FROM `meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS_ROUTES` r
LEFT JOIN `meli-bi-data.WHOWNER.LK_SHP_COMPANIES` c
    ON r.SHP_COMPANY_ID = c.SHP_COMPANY_ID
WHERE r.SHP_LG_FACILITY_ID = 'SSP30'
  AND DATE(r.SHP_LG_ROUTE_INIT_DATE) >= '{ANO_INICIO}'
  AND CAST(r.SHP_LG_DRIVER_ID AS STRING) IN (
      SELECT DISTINCT SAFE_CAST(DRIVER_ID AS STRING)
      FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
      WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
        AND date_bpp >= '{ANO_INICIO}'
        AND DRIVER_ID IS NOT NULL
  )
GROUP BY 1, 2
ORDER BY DIAS_SEM_ROTA DESC
"""

QUERY_PLACE_SHIPMENTS = f"""
-- SHP IDs por place (LOST + FRAUD apenas)
SELECT
    p.SHP_AGENCY_ID                                                                    AS AGENCY_ID,
    REGEXP_REPLACE(p.SHP_AGEN_DESC, r'Ag[êe]ncia Mercado Livre - ', '')               AS PLACE_NOME,
    CAST(f.SHIPMENT_ID AS STRING)                                                       AS SHP_ID,
    SAFE_CAST(f.DRIVER_ID AS STRING)                                                    AS DRIVER_ID,
    f.Classification_LM                                                                 AS CLASSIFICACAO,
    ROUND(f.BPP_CASHOUT_USD, 2)                                                         AS BPP,
    FORMAT_DATE('%d/%m/%Y', f.date_bpp)                                                 AS DATA
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO` f
JOIN `meli-bi-data.WHOWNER.BT_SHP_PLACES_AND_NODES` p
    ON CAST(p.SHP_SHIPMENT_ID AS STRING) = CAST(f.SHIPMENT_ID AS STRING)
WHERE f.SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND f.date_bpp >= '{ANO_INICIO}'
  AND f.date_bpp <= CURRENT_DATE()
  AND f.Classification_LM IN (
      'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
      'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE')
ORDER BY p.SHP_AGEN_DESC, f.BPP_CASHOUT_USD DESC
"""

QUERY_DAMAGED = f"""
-- Damaged por driver — usa DRIVER_ID direto da tabela
SELECT
    SAFE_CAST(DRIVER_ID AS STRING)                           AS DRIVER_ID,
    COUNT(DISTINCT SHIPMENT_ID)                              AS TOTAL_DAMAGED,
    ROUND(SUM(BPP_CASHOUT_USD), 2)                           AS TOTAL_BPP,
    COUNTIF(Classification_LM = 'DAMAGED ON ROUTE')          AS DAMAGED_ON_ROUTE,
    COUNTIF(Classification_LM = 'DAMAGED AT STATION')        AS DAMAGED_AT_STATION,
    COUNTIF(Classification_LM = 'DAMAGED ENE')               AS DAMAGED_ENE
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND date_bpp >= '{ANO_INICIO}'
  AND date_bpp <= CURRENT_DATE()
  AND Classification_LM LIKE 'DAMAGED%'
  AND DRIVER_ID IS NOT NULL
GROUP BY 1
ORDER BY TOTAL_DAMAGED DESC
LIMIT 60
"""

# ============================================================
# CONEXÃO E CONSULTAS
# ============================================================
def norm_id(s):
    """Normaliza ID: '292999.0' → '292999'"""
    try:    return str(int(float(str(s).strip())))
    except: return str(s).strip()

def conectar():
    print("Conectando ao BigQuery...")
    scopes = ['https://www.googleapis.com/auth/bigquery',
              'https://www.googleapis.com/auth/cloud-platform']
    creds, _ = default(scopes=scopes)
    return bigquery.Client(credentials=creds, project='meli-bi-data')

def buscar(bq, query, nome):
    print(f"  Buscando {nome}...")
    df = bq.query(query).to_dataframe()
    print(f"  {len(df)} linhas")
    return df

# ============================================================
# PROCESSAMENTO
# ============================================================
def flt(v):
    try:    return float(v or 0)
    except: return 0.0

def prioridade(score, fraud_conf):
    if score >= 15 or fraud_conf >= 3: return 'PRIORIDADE MAXIMA'
    if score >= 8  or fraud_conf >= 2: return 'ALTA'
    if score >= 4:                     return 'MEDIA'
    return 'BAIXA'

def processar(df_score, df_dxp, df_places, df_damaged, df_shp, df_place_shp, df_status, df_routes):
    # ---- Drivers (score combinado) ----
    drivers = []
    for _, r in df_score.iterrows():
        fraude  = int(r.get('TOTAL_FRAUDE', 0) or 0)
        damaged = int(r.get('TOTAL_DAMAGED', 0) or 0)
        fraud_c = int(r.get('FRAUD_CONFIRMADO', 0) or 0)
        bpp     = flt(r.get('TOTAL_BPP', 0))
        score   = (fraude * 3) + (damaged * 1)
        drivers.append({
            'id':      str(r['DRIVER_ID']),
            'total':   int(r.get('TOTAL_INCIDENTES', 0) or 0),
            'fraude':  fraude, 'damaged': damaged,
            'fraud_c': fraud_c, 'bpp': bpp, 'score': score,
            'prio':    prioridade(score, fraud_c),
        })
    drivers.sort(key=lambda x: -x['score'])

    # ---- Driver × Place ----
    dxp = []
    for _, r in df_dxp.iterrows():
        nome = str(r.get('PLACE_NOME', '')).replace('Agncia Mercado Livre - ', '').replace('Agência Mercado Livre - ', '')
        dxp.append({
            'driver':   str(r['DRIVER_ID']),
            'place':    nome,
            'total':    int(r.get('INCIDENTES_EM_COMUM', 0) or 0),
            'fraudes':  int(r.get('FRAUDES', 0) or 0),
            'damaged':  int(r.get('DAMAGED', 0) or 0),
            'bpp':      flt(r.get('TOTAL_BPP', 0)),
        })

    # ---- Places ----
    places = []
    for _, r in df_places.iterrows():
        nome = str(r.get('PLACE_NOME', '')).replace('Agncia Mercado Livre - ', '').replace('Agência Mercado Livre - ', '')
        places.append({
            'nome':    nome,
            'total':   int(r.get('TOTAL', 0) or 0),
            'bpp':     flt(r.get('TOTAL_BPP', 0)),
            'route':   int(r.get('LOST_ON_ROUTE', 0) or 0),
            'way':     int(r.get('LOST_ON_WAY', 0) or 0),
            'station': int(r.get('LOST_AT_STATION', 0) or 0),
            'ene':     int(r.get('LOST_ENE', 0) or 0),
            'fraud':   int(r.get('FRAUD_CONFIRMADO', 0) or 0),
        })

    # ---- Damaged drivers ----
    damaged = []
    for _, r in df_damaged.iterrows():
        damaged.append({
            'id':      str(r['DRIVER_ID']),
            'total':   int(r.get('TOTAL_DAMAGED', 0) or 0),
            'bpp':     flt(r.get('TOTAL_BPP', 0)),
            'route':   int(r.get('DAMAGED_ON_ROUTE', 0) or 0),
            'station': int(r.get('DAMAGED_AT_STATION', 0) or 0),
            'ene':     int(r.get('DAMAGED_ENE', 0) or 0),
        })

    # ---- Rotas dos drivers (transportadora + última rota) ----
    routes_map = {}
    for _, r in df_routes.iterrows():
        did  = norm_id(r.get('DRIVER_ID', ''))
        dias = int(r.get('DIAS_SEM_ROTA', -1) or -1)
        if did:
            # mantém o registro com menos dias (mais recente)
            if did not in routes_map or dias < routes_map[did]['dias_sem_rota']:
                routes_map[did] = {
                'transportadora': str(r.get('TRANSPORTADORA', '') or 'N/A'),
                'ultima_rota':    str(r.get('ULTIMA_ROTA', '') or ''),
                'dias_sem_rota':  dias,
                'rotas_ano':      int(r.get('ROTAS_ANO', 0) or 0),
            }

    # ---- Status dos drivers ----
    status_map = {}   # {driver_id: {status, substatus, lealdade, data_ativacao, primeira_fraude, ultima_fraude}}
    bloqueados = []
    for _, r in df_status.iterrows():
        did = norm_id(r.get('DRIVER_ID', ''))
        if not did: continue
        info = {
            'status':          str(r.get('STATUS', '') or ''),
            'substatus':       str(r.get('SUBSTATUS', '') or ''),
            'lealdade':        str(r.get('CATEGORIA', '') or 'N/A'),
            'data_ativacao':   str(r.get('DATA_ATIVACAO', '') or ''),
            'dias_operacao':   int(r.get('DIAS_OPERACAO', 0) or 0),
            'primeira_fraude': str(r.get('PRIMEIRA_FRAUDE', '') or ''),
            'ultima_fraude':   str(r.get('ULTIMA_FRAUDE', '') or ''),
            'total_hist':      int(r.get('TOTAL_INCIDENTES_HIST', 0) or 0),
        }
        status_map[did] = info
        if info['status'] == 'blocked':
            bloqueados.append({'id': did, **info})

    # Enriquece drivers com status, transportadora e atividade
    drivers_ativos     = []
    drivers_bloqueados = []
    for d in drivers:
        st  = status_map.get(d['id'], {})
        rt  = routes_map.get(d['id'], {})
        dias = rt.get('dias_sem_rota', -1)

        d['lealdade']       = st.get('lealdade', 'N/A')
        d['data_ativacao']  = st.get('data_ativacao', '')
        d['transportadora'] = rt.get('transportadora', 'N/A')
        d['ultima_rota']    = rt.get('ultima_rota', '—')
        d['dias_sem_rota']  = dias
        d['rotas_ano']      = rt.get('rotas_ano', 0)

        # Determina atividade
        if st.get('status') == 'blocked':
            d['atividade']    = 'Bloqueado'
            d['ativ_cor']     = '#ef4444'
            drivers_bloqueados.append(d)
        elif dias < 0:
            d['atividade']    = 'Sem dados'
            d['ativ_cor']     = '#4b5563'
            drivers_ativos.append(d)
        elif dias <= 30:
            d['atividade']    = 'Ativo'
            d['ativ_cor']     = '#10b981'
            drivers_ativos.append(d)
        elif dias <= 90:
            d['atividade']    = 'Em observação'
            d['ativ_cor']     = '#f59e0b'
            drivers_ativos.append(d)
        else:
            d['atividade']    = 'Inativo'
            d['ativ_cor']     = '#ef4444'
            drivers_ativos.append(d)

    # ---- SHP IDs por place ----
    shp_por_place = {}
    for _, r in df_place_shp.iterrows():
        nome = str(r.get('PLACE_NOME', '')).replace('Agncia Mercado Livre - ', '').replace('Agência Mercado Livre - ', '')
        if not nome: continue
        if nome not in shp_por_place:
            shp_por_place[nome] = []
        shp_por_place[nome].append({
            'id':      str(r.get('SHP_ID', '')),
            'driver':  str(r.get('DRIVER_ID', '') or '—'),
            'class':   str(r.get('CLASSIFICACAO', '')),
            'bpp':     flt(r.get('BPP', 0)),
            'data':    str(r.get('DATA', '')),
        })

    # ---- SHP IDs por driver ----
    shp_por_driver = {}
    for _, r in df_shp.iterrows():
        did = str(r.get('DRIVER_ID', ''))
        if not did: continue
        if did not in shp_por_driver:
            shp_por_driver[did] = []
        shp_por_driver[did].append({
            'id':    str(r.get('SHP_ID', '')),
            'class': str(r.get('CLASSIFICACAO', '')),
            'bpp':   flt(r.get('BPP', 0)),
            'data':  str(r.get('DATA', '')),
            'semana':str(r.get('SEMANA', '')),
        })

    # Adiciona os SHP IDs a cada driver
    for d in drivers:
        d['shps'] = shp_por_driver.get(d['id'], [])

    # ---- Conjunto de IDs que aparecem nas duas análises ----
    ids_fraude   = {d['id'] for d in drivers if d['fraude'] > 0}
    ids_damaged  = {d['id'] for d in damaged}
    ids_cruzados = ids_fraude & ids_damaged

    # ---- Totais ----
    total_fraudes = sum(d['fraude']  for d in drivers)
    total_damaged = sum(d['damaged'] for d in drivers)
    total_bpp     = sum(d['bpp']     for d in drivers)
    criticos      = sum(1 for d in drivers if d['prio'] in ('PRIORIDADE MAXIMA', 'ALTA'))

    # ---- Dados para gráficos ----
    top10_labels = [d['id'] for d in drivers[:10]]
    top10_fraude = [d['fraude']  for d in drivers[:10]]
    top10_damage = [d['damaged'] for d in drivers[:10]]
    top10_fraud_c= [d['fraud_c'] for d in drivers[:10]]

    top10_places_labels = [p['nome'][:25]+'…' if len(p['nome'])>25 else p['nome'] for p in places[:10]]
    top10_places_vals   = [p['total'] for p in places[:10]]

    return {
        'gerado':    datetime.now().strftime('%d/%m/%Y %H:%M'),
        'ano':       ANO_INICIO[:4],
        'drivers':   drivers,
        'dxp':       dxp,
        'places':    places,
        'damaged':   damaged,
        'cruzados':          ids_cruzados,
        'shp_por_driver':    shp_por_driver,
        'shp_por_place':     shp_por_place,
        'drivers_ativos':    drivers_ativos,
        'drivers_bloqueados':drivers_bloqueados,
        'total_bloqueados':  len(drivers_bloqueados),
        # Totais
        'total_fraudes': total_fraudes,
        'total_damaged': total_damaged,
        'total_bpp':     total_bpp,
        'criticos':      criticos,
        'total_places':  len(places),
        # Charts
        'top10_labels':  top10_labels,
        'top10_fraude':  top10_fraude,
        'top10_damage':  top10_damage,
        'top10_fraud_c': top10_fraud_c,
        'top10_places_labels': top10_places_labels,
        'top10_places_vals':   top10_places_vals,
    }

# ============================================================
# HELPERS HTML
# ============================================================
def prio_badge(p):
    cores = {
        'PRIORIDADE MAXIMA': ('#7f1d1d','#fca5a5'),
        'ALTA':              ('#7c2d12','#fdba74'),
        'MEDIA':             ('#713f12','#fde68a'),
        'BAIXA':             ('#1f2937','#9ca3af'),
    }
    bg, fg = cores.get(p, ('#1f2937','#9ca3af'))
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{p}</span>'

MELI_URL = 'https://envios.adminml.com/logistics/package-management/package'

def lealdade_badge(l):
    cores = {'Bronze':'#cd7f32','Prata':'#9ca3af','Ouro':'#f59e0b','Platina':'#a78bfa','N/A':'#374151'}
    cor = cores.get(l, '#374151')
    return f'<span style="background:{cor};color:#fff;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:600">{l}</span>'

def rows_drivers(drivers, cruzados):
    out = ''
    for d in drivers:
        cruz   = '⚠️' if d['id'] in cruzados else ''
        row_id = f'dr_{d["id"]}'
        # linhas dos SHP IDs
        shp_rows = ''
        for s in d.get('shps', []):
            cls_cor = '#ef4444' if 'FRAUD' in s['class'] else '#f59e0b' if 'DAMAGED' in s['class'] else '#94a3b8'
            shp_rows += f'''<tr style="background:#060c1a">
                <td colspan="2" style="padding:6px 16px 6px 40px;font-family:monospace;font-size:12px">
                  <a href="{MELI_URL}/{s["id"]}" target="_blank" style="color:#60a5fa;text-decoration:none">{s["id"]}</a>
                </td>
                <td style="color:{cls_cor};font-size:11px">{s["class"]}</td>
                <td style="color:#10b981;font-size:12px">${s["bpp"]:,.2f}</td>
                <td style="color:#6b7280;font-size:11px">{s["data"]}</td>
                <td colspan="2" style="color:#4b5563;font-size:11px">{s["semana"]}</td>
            </tr>'''
        toggle = f'onclick="toggleDriver(\'{row_id}\')" style="cursor:pointer"' if d['shps'] else ''
        seta   = f' <span id="arrow_{row_id}" style="font-size:10px;color:#4b5563">▶ {len(d["shps"])} pacotes</span>' if d['shps'] else ''
        dias     = d.get('dias_sem_rota', -1)
        dias_str = f'{dias}d' if dias >= 0 else '—'
        dias_cor = '#10b981' if 0 <= dias <= 30 else '#f59e0b' if dias <= 90 else '#ef4444' if dias > 0 else '#4b5563'
        ativ_cor = d.get('ativ_cor', '#4b5563')
        leal = d.get('lealdade','N/A')
        leal_html = lealdade_badge(leal) if leal not in ('N/A','') else '<span style="color:#374151;font-size:10px">Não se enquadra</span>'
        out += f'''<tr {toggle}
            data-id="{d["id"]}"
            data-transp="{d.get("transportadora","").lower()}"
            data-ativ="{d.get("atividade","").lower()}">
            <td style="font-weight:700;color:#f9fafb">{d["id"]}{seta} {cruz}</td>
            <td>{prio_badge(d["prio"])}</td>
            <td style="font-size:11px;color:#9ca3af">{d.get("transportadora","—")}</td>
            <td>{leal_html}</td>
            <td>
              <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{ativ_cor};margin-right:5px"></span>
              <span style="font-size:11px;color:{ativ_cor}">{d.get("atividade","—")}</span>
              <span style="font-size:10px;color:{dias_cor};margin-left:4px">({dias_str})</span>
            </td>
            <td style="font-size:11px;color:#6b7280">{d.get("ultima_rota","—")}</td>
            <td style="text-align:center;font-weight:700;color:#f9fafb">{d["score"]}</td>
            <td style="text-align:center;color:#ef4444;font-weight:600">{d["fraude"]}</td>
            <td style="text-align:center;color:#f59e0b">{d["damaged"]}</td>
            <td style="text-align:center;color:#ef4444">{d["fraud_c"]}</td>
            <td style="color:#10b981;font-weight:600">${d["bpp"]:,.2f}</td>
        </tr>
        <tbody id="{row_id}" style="display:none">{shp_rows}</tbody>'''
    return out

def rows_historico_bloqueios(bloqueados):
    if not bloqueados:
        return ''
    rows = ''
    for b in bloqueados:
        rows += f'''<tr style="background:#051505">
            <td style="font-weight:700;color:#4ade80">{b["id"]}</td>
            <td>{lealdade_badge(b.get("lealdade","N/A"))}</td>
            <td style="color:#6b7280;font-size:11px">{b.get("substatus","")}</td>
            <td style="color:#9ca3af;font-size:12px">{b.get("primeira_fraude","")}</td>
            <td style="color:#9ca3af;font-size:12px">{b.get("ultima_fraude","")}</td>
            <td style="text-align:center;font-weight:700;color:#f9fafb">{b.get("fraude",0) + b.get("damaged",0)}</td>
            <td style="color:#10b981">${b.get("bpp",0):,.2f}</td>
        </tr>'''
    return f'''<div class="tbl-wrap" style="border-color:#166534">
    <div class="tbl-title" style="color:#4ade80">Drivers Bloqueados — Removidos do Mercado ({len(bloqueados)})</div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th>Driver ID</th><th>Categoria</th><th>Substatus</th>
        <th>1ª Fraude</th><th>Última Fraude</th><th>Total Incidentes</th><th>BPP Total</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
  </div>'''

def rows_dxp(dxp, shp_por_driver):
    out = ''
    for i, r in enumerate(dxp):
        alert  = r['total'] >= 5
        bg     = 'background:#1a0a0a' if alert else ''
        row_id = f'dxp_{i}'
        # SHP IDs do driver
        shps     = shp_por_driver.get(r['driver'], [])
        shp_rows = ''
        for s in shps:
            cls_cor = '#ef4444' if 'FRAUD' in s['class'] else '#f59e0b' if 'DAMAGED' in s['class'] else '#94a3b8'
            shp_rows += f'''<tr style="background:#060c1a">
                <td colspan="2" style="padding:6px 16px 6px 40px;font-family:monospace;font-size:12px">
                  <a href="{MELI_URL}/{s["id"]}" target="_blank" style="color:#60a5fa;text-decoration:none">{s["id"]}</a>
                </td>
                <td style="color:{cls_cor};font-size:11px" colspan="2">{s["class"]}</td>
                <td style="color:#10b981;font-size:12px">${s["bpp"]:,.2f}</td>
                <td style="color:#6b7280;font-size:11px">{s["data"]}</td>
            </tr>'''
        seta   = f' <span id="arrow_dxp_{i}" style="font-size:10px;color:#4b5563">▶ {len(shps)} pacotes</span>' if shps else ''
        toggle = f'onclick="toggleDriver(\'dxp_{i}\')" style="cursor:pointer"' if shps else ''
        out += f'''<tr style="{bg}" {toggle}>
            <td style="font-weight:700;color:{"#fca5a5" if alert else "#f9fafb"}">{r["driver"]}{seta}</td>
            <td>{r["place"]}</td>
            <td style="text-align:center;font-weight:800;color:{"#ef4444" if alert else "#f9fafb"}">{r["total"]}</td>
            <td style="text-align:center;color:#ef4444">{r["fraudes"]}</td>
            <td style="text-align:center;color:#f59e0b">{r["damaged"]}</td>
            <td style="color:#10b981">${r["bpp"]:,.2f}</td>
        </tr>
        <tbody id="dxp_{i}" style="display:none">{shp_rows}</tbody>'''
    return out

def rows_places(places, shp_por_place):
    out = ''
    for i, p in enumerate(places):
        row_id = f'pl_{i}'
        shps   = shp_por_place.get(p['nome'], [])
        shp_rows = ''
        for s in shps:
            cls_cor = '#ef4444' if 'FRAUD' in s['class'] else '#94a3b8'
            shp_rows += f'''<tr style="background:#060c1a">
                <td style="padding:6px 16px 6px 36px;font-family:monospace;font-size:12px">
                  <a href="{MELI_URL}/{s["id"]}" target="_blank" style="color:#60a5fa;text-decoration:none">{s["id"]}</a>
                </td>
                <td style="color:#6b7280;font-size:11px">Driver: {s["driver"]}</td>
                <td style="color:{cls_cor};font-size:11px" colspan="2">{s["class"]}</td>
                <td style="color:#10b981;font-size:12px">${s["bpp"]:,.2f}</td>
                <td style="color:#6b7280;font-size:11px" colspan="3">{s["data"]}</td>
            </tr>'''
        seta   = f' <span id="arrow_pl_{i}" style="font-size:10px;color:#4b5563">▶ {len(shps)} ids</span>' if shps else ''
        toggle = f'onclick="toggleDriver(\'pl_{i}\')" style="cursor:pointer"' if shps else ''
        out += f'''<tr {toggle}>
            <td style="font-weight:600;color:#f9fafb">{p["nome"]}{seta}</td>
            <td style="text-align:center;font-weight:700;color:#f9fafb">{p["total"]}</td>
            <td style="color:#10b981">${p["bpp"]:,.2f}</td>
            <td style="text-align:center;color:#ef4444">{p["route"]}</td>
            <td style="text-align:center;color:#60a5fa">{p["way"]}</td>
            <td style="text-align:center;color:#a78bfa">{p["station"]}</td>
            <td style="text-align:center;color:#94a3b8">{p["ene"]}</td>
            <td style="text-align:center;color:#f87171;font-weight:700">{p["fraud"]}</td>
        </tr>
        <tbody id="pl_{i}" style="display:none">{shp_rows}</tbody>'''
    return out

def rows_damaged(damaged, cruzados_fraude, shp_por_driver):
    out = ''
    for i, d in enumerate(damaged):
        cruz   = ' ⚠️' if d['id'] in cruzados_fraude else ''
        row_id = f'dmg_{i}'
        # filtra só DAMAGED
        shps   = [s for s in shp_por_driver.get(d['id'], []) if 'DAMAGED' in s['class']]
        shp_rows = ''
        for s in shps:
            cls_cor = '#f59e0b'
            shp_rows += f'''<tr style="background:#060c1a">
                <td style="padding:6px 16px 6px 36px;font-family:monospace;font-size:12px" colspan="2">
                  <a href="{MELI_URL}/{s["id"]}" target="_blank" style="color:#60a5fa;text-decoration:none">{s["id"]}</a>
                </td>
                <td style="color:{cls_cor};font-size:11px" colspan="2">{s["class"]}</td>
                <td style="color:#10b981;font-size:12px">${s["bpp"]:,.2f}</td>
                <td style="color:#6b7280;font-size:11px">{s["data"]}</td>
            </tr>'''
        seta   = f' <span id="arrow_dmg_{i}" style="font-size:10px;color:#4b5563">▶ {len(shps)} ids</span>' if shps else ''
        toggle = f'onclick="toggleDriver(\'dmg_{i}\')" style="cursor:pointer"' if shps else ''
        out += f'''<tr {toggle}>
            <td style="font-weight:700;color:#f9fafb">{d["id"]}{seta}{cruz}</td>
            <td style="text-align:center;font-weight:700;color:#f59e0b">{d["total"]}</td>
            <td style="color:#10b981">${d["bpp"]:,.2f}</td>
            <td style="text-align:center">{d["route"]}</td>
            <td style="text-align:center">{d["station"]}</td>
            <td style="text-align:center">{d["ene"]}</td>
        </tr>
        <tbody id="dmg_{i}" style="display:none">{shp_rows}</tbody>'''
    return out

# ============================================================
# GERAÇÃO DO HTML
# ============================================================
def gerar_html(d):
    j = lambda x: json.dumps(x, ensure_ascii=False)

    cruzados_list = sorted(d['cruzados'])
    rows_cruzados = ''
    for did in cruzados_list:
        drv = next((x for x in d['drivers'] if x['id'] == did), None)
        dam = next((x for x in d['damaged'] if x['id'] == did), None)
        if drv and dam:
            score = drv['score']
            rows_cruzados += f'''<tr style="background:#160a0a">
                <td style="font-weight:800;color:#fca5a5">{did}</td>
                <td>{prio_badge(drv["prio"])}</td>
                <td style="text-align:center;color:#ef4444;font-weight:700">{drv["fraude"]}</td>
                <td style="text-align:center;color:#f59e0b;font-weight:700">{dam["total"]}</td>
                <td style="text-align:center;font-weight:800;color:#f9fafb">{score}</td>
                <td style="color:#10b981">${drv["bpp"]:,.2f}</td>
            </tr>'''

    return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fraude SSP30 — Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🔍</text></svg>">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#080d19;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
  .header{{background:#080d19;padding:16px 32px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #7f1d1d}}
  .header-brand{{display:flex;align-items:center;gap:10px}}
  .header-accent{{width:3px;height:28px;background:#ef4444;border-radius:2px}}
  .header-title{{font-size:16px;font-weight:700;color:#ffffff}}
  .header-sub{{font-size:11px;color:#374151;margin-top:2px}}
  .tabs{{background:#080d19;border-bottom:1px solid #111827;padding:0 32px;display:flex;gap:0;overflow-x:auto}}
  .tab{{padding:14px 20px;cursor:pointer;font-size:12px;font-weight:500;color:#6b7280;border-bottom:2px solid transparent;transition:all .3s ease;white-space:nowrap}}
  .tab:hover{{color:#f9fafb}}
  .tab.active{{color:#ffffff;border-bottom-color:#ef4444;font-weight:600}}
  .content{{display:none;padding:28px 32px;max-width:1480px;margin:0 auto}}
  .content.active{{display:block}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px}}
  .card{{background:#0d1321;border-radius:8px;padding:18px 20px;border:1px solid #111827;transition:all .3s ease;display:flex;flex-direction:column;min-height:90px}}
  .card:hover{{border-color:#1f2937;transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.5)}}
  .card.c-red{{border-color:#450a0a;background:#0f0606}}
  .card-header{{display:flex;align-items:center;gap:7px;margin-bottom:12px}}
  .ci{{color:#374151;flex-shrink:0}}
  .cl{{font-size:10px;color:#4b5563;text-transform:uppercase;letter-spacing:.7px;font-weight:600}}
  .cv{{font-size:28px;font-weight:800;color:#ffffff;line-height:1;letter-spacing:-1px}}
  .cv.red{{color:#ef4444}}
  .cv.amber{{color:#f59e0b}}
  .cv.green{{color:#10b981}}
  .cd{{font-size:11px;color:#374151;margin-top:auto;padding-top:6px}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}}
  @media(max-width:900px){{.grid2{{grid-template-columns:1fr}}}}
  .box{{background:#0d1321;border-radius:8px;padding:20px;border:1px solid #111827;margin-bottom:14px}}
  .bt{{font-size:10px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.7px;margin-bottom:16px}}
  .tbl-wrap{{background:#0d1321;border-radius:8px;overflow:hidden;margin-bottom:20px;border:1px solid #111827}}
  .tbl-title{{padding:14px 24px;font-size:10px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.7px;border-bottom:1px solid #111827}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{background:#080d19;padding:10px 16px;text-align:left;font-size:10px;font-weight:600;color:#374151;text-transform:uppercase;letter-spacing:.6px}}
  td{{padding:10px 16px;border-bottom:1px solid #0d1321;color:#d1d5db}}
  tr:hover td{{background:#111827!important}}
  tr:last-child td{{border-bottom:none}}
  .tbl-scroll{{overflow-x:auto}}
  /* FILTROS */
  .filter-bar{{display:flex;gap:8px;padding:12px 20px;flex-wrap:wrap;border-bottom:1px solid #111827;align-items:center;background:#080d19}}
  .filter-input{{background:#0d1321;border:1px solid #1f2937;border-radius:6px;padding:8px 14px;color:#e2e8f0;font-size:12px;flex:1;min-width:180px;outline:none;transition:border-color .3s ease}}
  .filter-input:focus{{border-color:#374151}}
  .filter-input::placeholder{{color:#374151}}
  .filter-select{{background:#0d1321;border:1px solid #1f2937;border-radius:6px;padding:8px 14px;color:#9ca3af;font-size:12px;outline:none;cursor:pointer;appearance:none;-webkit-appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%236b7280'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center;padding-right:28px;transition:border-color .3s ease}}
  .filter-select:focus{{border-color:#374151;color:#e2e8f0}}
  .filter-label{{font-size:10px;color:#4b5563;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap}}
  /* NAV */
  .nav-wrap{{position:relative}}
  .nav-btn{{background:#111827;color:#9ca3af;border:1px solid #1f2937;border-radius:6px;padding:7px 14px;font-size:11px;font-weight:500;cursor:pointer;display:flex;align-items:center;gap:6px;transition:all .3s ease}}
  .nav-btn:hover{{background:#1f2937;color:#e2e8f0}}
  .nav-dropdown{{display:none;position:absolute;right:0;top:calc(100% + 6px);background:#0d1321;border:1px solid #1f2937;border-radius:8px;overflow:hidden;min-width:220px;z-index:999;box-shadow:0 8px 24px rgba(0,0,0,.6)}}
  .nav-wrap:hover .nav-dropdown{{display:block}}
  .nav-dropdown a{{display:flex;align-items:center;gap:10px;padding:11px 16px;font-size:12px;color:#d1d5db;text-decoration:none;transition:background .2s;border-bottom:1px solid #111827}}
  .nav-dropdown a:last-child{{border-bottom:none}}
  .nav-dropdown a:hover{{background:#1f2937;color:#ffffff}}
  .nav-dropdown a.nav-active{{color:#ef4444;font-weight:600}}
  .alerta-box{{background:#160a0a;border:1px solid #7f1d1d;border-radius:8px;padding:16px 20px;margin-bottom:20px;display:flex;align-items:center;gap:14px}}
  .alerta-box .num{{font-size:28px;font-weight:800;color:#fca5a5}}
  .alerta-box .txt{{color:#fca5a5;font-size:13px}}
</style>
</head>
<body>

<div class="header">
  <div class="header-brand">
    <div class="header-accent"></div>
    <div>
      <div class="header-title">Análise de Fraude — SSP30</div>
      <div class="header-sub">Base {d["ano"]} · Gerado em {d["gerado"]}</div>
    </div>
  </div>
  <div class="nav-wrap">
    <button class="nav-btn">⊞ Dashboards ▾</button>
    <div class="nav-dropdown">
      <a href="./index.html">🔔 Risco SSP30 — ON ROUTE / ON WAY</a>
      <a href="./fraude.html" class="nav-active">🔍 Análise de Fraude SSP30</a>
    </div>
  </div>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('geral',this)">Visão Geral</div>
  <div class="tab" onclick="showTab('drivers',this)">Risco por Driver ({len(d["drivers"])})</div>
  <div class="tab" onclick="showTab('dxp',this)">Driver × Place ({len(d["dxp"])})</div>
  <div class="tab" onclick="showTab('places',this)">Ofensores Places ({d["total_places"]})</div>
  <div class="tab" onclick="showTab('damaged',this)">Damaged ({len(d["damaged"])})</div>
</div>

<!-- VISÃO GERAL -->
<div id="tab-geral" class="content active">
  <div class="cards">
    <div class="card c-red">
      <div class="card-header"><i data-lucide="alert-triangle" class="ci" width="14" height="14" style="color:#7f1d1d"></i><span class="cl">Drivers Críticos</span></div>
      <div class="cv red">{d["criticos"]}</div>
      <div class="cd">Prioridade Alta ou Máxima</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="package-x" class="ci" width="14" height="14"></i><span class="cl">Total Fraudes/Lost</span></div>
      <div class="cv">{d["total_fraudes"]}</div>
      <div class="cd">{d["ano"]}</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="box" class="ci" width="14" height="14"></i><span class="cl">Total Damaged</span></div>
      <div class="cv amber">{d["total_damaged"]}</div>
      <div class="cd">{d["ano"]}</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="dollar-sign" class="ci" width="14" height="14"></i><span class="cl">BPP Total</span></div>
      <div class="cv green">${d["total_bpp"]:,.2f}</div>
      <div class="cd">Cashout {d["ano"]}</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="map-pin" class="ci" width="14" height="14"></i><span class="cl">Places Suspeitos</span></div>
      <div class="cv">{d["total_places"]}</div>
      <div class="cd">Com fraude/lost</div>
    </div>
    <div class="card c-red">
      <div class="card-header"><i data-lucide="zap" class="ci" width="14" height="14" style="color:#7f1d1d"></i><span class="cl">Cruzados F+D</span></div>
      <div class="cv red">{len(d["cruzados"])}</div>
      <div class="cd">Fraude + Damaged</div>
    </div>
    <div class="card card-ok">
      <div class="card-header"><i data-lucide="shield-check" class="ci" width="14" height="14" style="color:#064e3b"></i><span class="cl">Drivers Bloqueados</span></div>
      <div class="cv val-ok">{d["total_bloqueados"]}</div>
      <div class="card-delta">Autônomos removidos do mercado</div>
    </div>
  </div>

  {"" if not d["cruzados"] else f'''
  <div class="alerta-box">
    <div class="num">{len(d["cruzados"])}</div>
    <div class="txt"><strong>drivers aparecem em AMBAS as análises (Fraude + Damaged)</strong><br>
    Estes são os principais alvos para investigação e bloqueio.</div>
  </div>
  <div class="tbl-wrap">
    <div class="tbl-title">Drivers com Fraude + Damaged (maior risco)</div>
    <div class="tbl-scroll"><table>
      <thead><tr><th>Driver ID</th><th>Prioridade</th><th>Fraudes</th><th>Damaged</th><th>Score</th><th>BPP Total</th></tr></thead>
      <tbody>''' + rows_cruzados + '''</tbody></table></div>
  </div>'''}

  <div class="grid2">
    <div class="box"><div class="bt">Top 10 Drivers — Fraude vs Damaged</div><canvas id="cDrivers" height="280"></canvas></div>
    <div class="box"><div class="bt">Top 10 Places com mais Fraudes</div><canvas id="cPlaces" height="280"></canvas></div>
  </div>
</div>

<!-- RISCO POR DRIVER -->
<div id="tab-drivers" class="content">

  <!-- Banner de bloqueados -->
  {f'''<div class="alerta-box" style="background:#0a1f0a;border-color:#166534">
    <div class="num" style="color:#4ade80">{d["total_bloqueados"]}</div>
    <div class="txt" style="color:#4ade80"><strong>Drivers Bloqueados</strong> — identificados na sua análise e removidos do mercado.<br>
    Não aparecem mais no ranking ativo.</div>
  </div>''' if d["total_bloqueados"] > 0 else ''}

  <div class="tbl-wrap">
    <div class="tbl-title">Ranking Ativo — Drivers em Atuação ({len(d["drivers_ativos"])})</div>
    <!-- Filtros -->
    <div class="filter-bar">
      <span class="filter-label">Filtrar:</span>
      <input type="text" id="busca_driver" placeholder="Driver ID..." oninput="filtrarDrivers()" class="filter-input" style="max-width:160px">
      <select id="filtro_transp" onchange="filtrarDrivers()" class="filter-select">
        <option value="">Transportadora</option>
        {''.join(f'<option value="{t.lower()}">{t}</option>' for t in sorted(t for t in set(r.get("transportadora","") for r in d["drivers_ativos"]) if t and t not in ("N/A","—","")))}
      </select>
      <select id="filtro_ativ" onchange="filtrarDrivers()" class="filter-select">
        <option value="">Atividade</option>
        <option value="ativo">Ativo</option>
        <option value="em observação">Em observação</option>
        <option value="inativo">Inativo</option>
        <option value="sem dados">Sem dados</option>
      </select>
      <button onclick="document.getElementById('busca_driver').value='';document.getElementById('filtro_transp').value='';document.getElementById('filtro_ativ').value='';filtrarDrivers()" style="background:#1f2937;color:#6b7280;border:1px solid #374151;border-radius:6px;padding:7px 12px;font-size:11px;cursor:pointer">Limpar</button>
    </div>
    <div class="tbl-scroll"><table id="tbl_drivers">
      <thead><tr>
        <th>Driver ID</th><th>Prioridade</th><th>Transportadora</th><th>Categoria</th>
        <th>Atividade</th><th>Última Rota</th><th>Score</th>
        <th>Fraudes</th><th>Damaged</th><th>Fraud Confirm.</th><th>BPP Total</th>
      </tr></thead>
      <tbody>{rows_drivers(d["drivers_ativos"], d["cruzados"])}</tbody>
    </table></div>
  </div>

  <!-- Histórico de bloqueados -->
  {rows_historico_bloqueios(d["drivers_bloqueados"])}
</div>

<!-- DRIVER × PLACE -->
<div id="tab-dxp" class="content">
  <div class="tbl-wrap">
    <div class="tbl-title">Driver × Place — Combinações com 2+ Fraudes em Comum</div>
    <div class="tbl-scroll"><table>
      <thead><tr><th>Driver ID</th><th>Place</th><th>Total</th><th>Fraudes</th><th>Damaged</th><th>BPP Total</th></tr></thead>
      <tbody>{rows_dxp(d["dxp"], d["shp_por_driver"])}</tbody>
    </table></div>
  </div>
</div>

<!-- PLACES / OFENSORES -->
<div id="tab-places" class="content">

  <!-- Top 3 cards -->
  <div class="cards" style="grid-template-columns:repeat(3,1fr);margin-bottom:20px">
    {''.join(f"""
    <div class="card {'c-red' if i==0 else ''}">
      <div class="card-header"><i data-lucide="map-pin" class="ci" width="14" height="14" {'style="color:#7f1d1d"' if i==0 else ''}></i>
        <span class="cl">#{i+1} Ofensor</span></div>
      <div class="cv {'red' if i==0 else ''}" style="font-size:14px;font-weight:700">{p["nome"][:30]}{"…" if len(p["nome"])>30 else ""}</div>
      <div class="cd">{p["total"]} incidentes · ${p["bpp"]:,.2f} BPP</div>
    </div>""" for i, p in enumerate(d["places"][:3]))}
  </div>

  <div class="box mb16"><div class="bt">Top 15 Places por Total de Fraudes</div><canvas id="cPlacesBar" height="300"></canvas></div>

  <div class="tbl-wrap">
    <div class="tbl-title">Ranking completo — Places Ofensores</div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th>Place</th><th>Total</th><th>BPP</th>
        <th>Lost Route</th><th>Lost Way</th><th>Lost Station</th><th>Lost ENE</th><th>Fraud Confirm.</th>
      </tr></thead>
      <tbody>{rows_places(d["places"], d["shp_por_place"])}</tbody>
    </table></div>
  </div>
</div>

<!-- DAMAGED -->
<div id="tab-damaged" class="content">
  <div class="tbl-wrap">
    <div class="tbl-title">Damaged por Driver — ⚠️ indica driver que também tem fraudes</div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th>Driver ID</th><th>Total Damaged</th><th>BPP Total</th>
        <th>On Route</th><th>At Station</th><th>ENE</th>
      </tr></thead>
      <tbody>{rows_damaged(d["damaged"], d["cruzados"], d["shp_por_driver"])}</tbody>
    </table></div>
  </div>
</div>

<script>
// Filtro da tabela de drivers
function filtrarDrivers() {{
  const busca  = (document.getElementById('busca_driver')?.value || '').toLowerCase();
  const transp = (document.getElementById('filtro_transp')?.value || '').toLowerCase();
  const ativ   = (document.getElementById('filtro_ativ')?.value || '').toLowerCase();
  document.querySelectorAll('#tbl_drivers > tbody > tr[data-id]').forEach(tr => {{
    const id    = (tr.dataset.id    || '').toLowerCase();
    const tp    = (tr.dataset.transp|| '').toLowerCase();
    const at    = (tr.dataset.ativ  || '').toLowerCase();
    const ok = (!busca  || id.includes(busca))
            && (!transp || tp.includes(transp))
            && (!ativ   || at.includes(ativ));
    tr.style.display = ok ? '' : 'none';
    // esconde também o tbody expandido do driver quando filtrado
    const nextSibling = tr.nextElementSibling;
    if (nextSibling && nextSibling.tagName === 'TBODY' && !ok) nextSibling.style.display = 'none';
  }});
}}

// Expandir/recolher SHP IDs do driver
function toggleDriver(id) {{
  const el = document.getElementById(id);
  const ar = document.getElementById('arrow_' + id);
  if (!el) return;
  const open = el.style.display === 'none';
  el.style.display = open ? '' : 'none';
  if (ar) ar.textContent = ar.textContent.replace(open ? '▶' : '▼', open ? '▼' : '▶');
}}

const TAB_ORDER = ['geral','drivers','dxp','places','damaged'];
function showTab(name, el) {{
  document.querySelectorAll('.content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(e => e.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  el.classList.add('active');
  history.replaceState(null,'','#'+name);
}}
window.addEventListener('load', () => {{
  const h = window.location.hash.replace('#','');
  const i = TAB_ORDER.indexOf(h);
  if (i >= 0) showTab(h, document.querySelectorAll('.tab')[i]);
}});

Chart.defaults.plugins.tooltip.backgroundColor = '#0d1321';
Chart.defaults.plugins.tooltip.titleColor      = '#f9fafb';
Chart.defaults.plugins.tooltip.bodyColor       = '#9ca3af';
Chart.defaults.plugins.tooltip.borderColor     = '#1f2937';
Chart.defaults.plugins.tooltip.borderWidth     = 1;
Chart.defaults.plugins.tooltip.cornerRadius    = 6;
Chart.defaults.plugins.tooltip.padding         = 10;

new Chart(document.getElementById('cDrivers'), {{
  type: 'bar',
  data: {{
    labels: {j(d["top10_labels"])},
    datasets: [
      {{ label:'Fraudes', data:{j(d["top10_fraude"])}, backgroundColor:'rgba(239,68,68,0.8)', borderRadius:4 }},
      {{ label:'Damaged', data:{j(d["top10_damage"])}, backgroundColor:'rgba(245,158,11,0.8)', borderRadius:4 }},
      {{ label:'Fraud Confirmado', data:{j(d["top10_fraud_c"])}, backgroundColor:'rgba(168,85,247,0.8)', borderRadius:4 }},
    ]
  }},
  options: {{
    responsive:true,
    plugins:{{ legend:{{ labels:{{ color:'#94a3b8', font:{{size:11}} }} }} }},
    scales:{{
      x:{{ stacked:false, ticks:{{color:'#8a8a8a'}}, grid:{{color:'#1e293b'}} }},
      y:{{ ticks:{{color:'#8a8a8a'}}, grid:{{color:'#334155'}} }}
    }}
  }}
}});

new Chart(document.getElementById('cPlaces'), {{
  type: 'bar',
  data: {{
    labels: {j(d["top10_places_labels"])},
    datasets: [{{ data:{j(d["top10_places_vals"])}, backgroundColor:'rgba(239,68,68,0.7)', borderRadius:4 }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    plugins: {{ legend:{{display:false}} }},
    scales: {{
      x:{{ ticks:{{color:'#8a8a8a'}}, grid:{{color:'#334155'}} }},
      y:{{ ticks:{{color:'#8a8a8a', font:{{size:11}}}}, grid:{{display:false}} }}
    }}
  }}
}});

// Gráfico de Places Ofensores
new Chart(document.getElementById('cPlacesBar'), {{
  type: 'bar',
  data: {{
    labels: {j([p["nome"][:28]+"…" if len(p["nome"])>28 else p["nome"] for p in d["places"][:15]])},
    datasets: [
      {{ label:'Fraudes/Lost', data:{j([p["total"]-p["fraud"] for p in d["places"][:15]])},
         backgroundColor:'rgba(239,68,68,0.75)', borderRadius:4 }},
      {{ label:'Fraud Confirmado', data:{j([p["fraud"] for p in d["places"][:15]])},
         backgroundColor:'rgba(168,85,247,0.75)', borderRadius:4 }},
    ]
  }},
  options: {{
    indexAxis:'y', responsive:true,
    plugins:{{ legend:{{ labels:{{ color:'#94a3b8',font:{{size:11}} }} }} }},
    scales:{{
      x:{{ stacked:true, ticks:{{color:'#8a8a8a'}}, grid:{{color:'#334155'}} }},
      y:{{ stacked:true, ticks:{{color:'#8a8a8a',font:{{size:10}}}}, grid:{{display:false}} }}
    }}
  }}
}});

lucide.createIcons();
</script>
</body>
</html>'''

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("="*55)
    print(f"Análise de Fraude SSP30 — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("="*55)

    bq = conectar()

    print("\nConsultando BigQuery...")
    df_score     = buscar(bq, QUERY_DRIVER_SCORE,    'Score por Driver')
    df_shp       = buscar(bq, QUERY_DRIVER_SHIPMENTS,'SHP IDs por Driver')
    df_status    = buscar(bq, QUERY_DRIVER_STATUS,   'Status dos Drivers')
    df_routes    = buscar(bq, QUERY_DRIVER_ROUTES,   'Rotas dos Drivers')
    df_dxp       = buscar(bq, QUERY_DRIVER_PLACE,    'Driver x Place')
    df_places    = buscar(bq, QUERY_PLACES,           'Places')
    df_place_shp = buscar(bq, QUERY_PLACE_SHIPMENTS, 'SHP IDs por Place')
    df_damaged   = buscar(bq, QUERY_DAMAGED,           'Damaged por Driver')

    print("\nProcessando...")
    dados = processar(df_score, df_dxp, df_places, df_damaged, df_shp, df_place_shp, df_status, df_routes)

    print("Gerando dashboard...")
    html = gerar_html(dados)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Salvo em: {OUTPUT}")

    if not os.environ.get('CI'):
        webbrowser.open(f'file:///{OUTPUT.replace(chr(92),"/")}')
        print("Abrindo no navegador!")

    print(f"\n{'='*55}")
    print(f"Drivers criticos : {dados['criticos']}")
    print(f"Total fraudes    : {dados['total_fraudes']}")
    print(f"Total damaged    : {dados['total_damaged']}")
    print(f"BPP Total        : ${dados['total_bpp']:,.2f}")
    print(f"Cruzados F+D     : {len(dados['cruzados'])}")
    print("="*55)
