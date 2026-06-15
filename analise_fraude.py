# ============================================================
# analise_fraude.py — Dashboard de Análise de Fraude SSP30
# Como rodar: duplo clique em abrir_analise_fraude.bat
# ============================================================

import json, webbrowser, os
from datetime import datetime
from google.cloud import bigquery
from google.auth import default
import gspread

FACILITY_NAME  = 'Guarulhos Mega'
ANO_INICIO     = '2026-01-01'
OUTPUT         = os.path.join(os.path.dirname(__file__), 'fraude.html')
BLOCK_LIST_ID  = '1521Ek2wn8qYLj7g6dh0aBBMmpVYHjCp2hftGKNG9bO0'
ABA_BLOQUEIOS  = 'Drivers Bloqueados'
CFTV_SHEET_ID  = '18isURInofILBi-RS9YrCQyYcnb6JeU_stNqnspxiqLM'
CFTV_ABA       = 'Respostas ao formulário 2'

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
   AND p.SERVICE_TYPE = 'DO'
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
   AND p.SERVICE_TYPE = 'DO'
GROUP BY 1, 2
ORDER BY TOTAL DESC
LIMIT 50
"""

QUERY_DRIVER_STATUS = f"""
-- Status de todos os drivers: blocked, inactive/fraud_prevention, removed, active
-- Prioridade: blocked > inactive > removed > active
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
-- Todos os status distintos por driver (pode ter múltiplos)
status_raw AS (
  SELECT DISTINCT
    CAST(r.DRIVER_ID AS STRING)       AS DRIVER_ID,
    s.SHP_CROWD_STATUS                AS STATUS,
    s.SHP_CROWD_SUBSTATUS             AS SUBSTATUS
  FROM `meli-bi-data.WHOWNER.BT_SHP_CROWD_TRACKER_REGIST` AS r
  LEFT JOIN `meli-bi-data.WHOWNER.BT_SHP_CROWD_DRIVER_REG_STATUS` AS ds
    ON r.DRIVER_ID = ds.SHP_CROWD_DRIVER_ID
  LEFT JOIN `meli-bi-data.WHOWNER.BT_SHP_CROWD_REG_STATUS` AS s
    ON ds.SHP_CROWD_STATUS_ID = s.SHP_CROWD_ID
  WHERE r.SITE = 'MLB'
),
-- Pega o status mais grave por driver
status_priority AS (
  SELECT DISTINCT
    DRIVER_ID,
    FIRST_VALUE(STATUS) OVER (
      PARTITION BY DRIVER_ID
      ORDER BY CASE STATUS
        WHEN 'blocked'  THEN 1
        WHEN 'inactive' THEN 2
        WHEN 'removed'  THEN 3
        ELSE 4
      END
    ) AS STATUS,
    FIRST_VALUE(SUBSTATUS) OVER (
      PARTITION BY DRIVER_ID
      ORDER BY CASE STATUS
        WHEN 'blocked'  THEN 1
        WHEN 'inactive' THEN 2
        WHEN 'removed'  THEN 3
        ELSE 4
      END
    ) AS SUBSTATUS
  FROM status_raw
),
fraud_drivers AS (
  SELECT DISTINCT SAFE_CAST(DRIVER_ID AS STRING) AS DRIVER_ID
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
    AND date_bpp >= '{ANO_INICIO}'
    AND DRIVER_ID IS NOT NULL
)
SELECT
  sp.DRIVER_ID,
  sp.STATUS,
  sp.SUBSTATUS,
  loy.lealdade AS CATEGORIA
FROM status_priority sp
INNER JOIN fraud_drivers fd ON sp.DRIVER_ID = fd.DRIVER_ID
LEFT JOIN loyalty loy ON sp.DRIVER_ID = CAST(loy.driverid AS STRING)
ORDER BY
  CASE sp.STATUS WHEN 'blocked' THEN 1 WHEN 'inactive' THEN 2 WHEN 'removed' THEN 3 ELSE 4 END
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
   AND p.SERVICE_TYPE = 'DO'
WHERE f.SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND f.date_bpp >= '{ANO_INICIO}'
  AND f.date_bpp <= CURRENT_DATE()
  AND f.Classification_LM IN (
      'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
      'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE')
ORDER BY p.SHP_AGEN_DESC, f.BPP_CASHOUT_USD DESC
"""

QUERY_CRUZAMENTO = f"""
-- Sellers e Buyers ofensores cruzados com drivers de fraude
WITH fraudes AS (
  SELECT
    CAST(SHIPMENT_ID AS STRING)    AS SHP_ID,
    SAFE_CAST(DRIVER_ID AS STRING) AS DRIVER_ID
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = '{FACILITY_NAME}'
    AND date_bpp >= '{ANO_INICIO}'
    AND date_bpp <= CURRENT_DATE()
    AND Classification_LM IN (
      'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
      'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE'
    )
)
SELECT
  CAST(shp.SHP_SENDER_ID   AS STRING)     AS SELLER_ID,
  CAST(shp.SHP_RECEIVER_ID AS STRING)     AS BUYER_ID,
  COUNT(DISTINCT f.SHP_ID)                AS QTD_FRAUDES,
  STRING_AGG(DISTINCT f.DRIVER_ID, ',')   AS DRIVERS,
  STRING_AGG(DISTINCT f.SHP_ID, ',')      AS SHP_IDS
FROM fraudes f
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` shp
  ON CAST(shp.SHP_SHIPMENT_ID AS STRING) = f.SHP_ID
GROUP BY 1, 2
HAVING COUNT(DISTINCT f.SHP_ID) >= 2
ORDER BY 3 DESC
LIMIT 200
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

def _ym(d):
    """'dd/mm/yyyy' → 'yyyy-mm'"""
    try: return d[6:10]+'-'+d[3:5] if len(str(d)) >= 10 else ''
    except: return ''

def conectar():
    print("Conectando ao BigQuery e Google Sheets...")
    scopes = [
        'https://www.googleapis.com/auth/bigquery',
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/cloud-platform',
    ]
    creds, _ = default(scopes=scopes)
    bq = bigquery.Client(credentials=creds, project='meli-bi-data')
    gs = gspread.authorize(creds)
    return bq, gs

def carregar_block_list(gs):
    print("  Lendo Block List...")
    try:
        pl    = gs.open_by_key(BLOCK_LIST_ID)
        dados = pl.worksheet(ABA_BLOQUEIOS).get_all_values()
        if len(dados) <= 1:
            return []
        header = dados[0]
        rows   = []
        for r in dados[1:]:
            if not any(r): continue
            row = dict(zip(header, r))
            ano = row.get('Ano','').strip()
            if ano == '2026' or not ano:
                rows.append(row)
        print(f"  {len(rows)} registros na Block List")
        return rows
    except Exception as e:
        print(f"  Aviso Block List: {e}")
        return []

def carregar_cftv(gs):
    print("  Lendo planilha CFTV...")
    try:
        pl   = gs.open_by_key(CFTV_SHEET_ID)
        data = pl.worksheet(CFTV_ABA).get_all_values()
        if len(data) <= 1:
            return []
        header = data[0]
        rows   = [dict(zip(header, r)) for r in data[1:] if any(r)]
        print(f"  {len(rows)} solicitações CFTV")
        return rows
    except Exception as e:
        print(f"  Aviso CFTV: {e}")
        return []

def sincronizar_status_block_list(gs, bq, bl_rows):
    """Consulta BQ e atualiza status na planilha para drivers Solicitado/Monitorado."""
    ATUALIZAR = {'solicitado', 'monitorado'}
    def _st(r): return r.get('Status', r.get('status', '')).strip().lower()
    def _did(r): return str(r.get('Driver ID', r.get('driver_id', ''))).strip()
    pendentes = {_did(r) for r in bl_rows if _st(r) in ATUALIZAR and _did(r)}
    if not pendentes:
        print("  Nenhum driver pendente para sincronizar.")
        return

    print(f"  Sincronizando status de {len(pendentes)} drivers com BQ...")

    def col_letter(n):
        s = ''
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    query = """
    SELECT DISTINCT DRIVER_ID, DRIVER_STATUS
    FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
    WHERE DATE_BPP >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
      AND DRIVER_ID IN UNNEST(@ids)
    QUALIFY ROW_NUMBER() OVER (PARTITION BY DRIVER_ID ORDER BY DATE_BPP DESC) = 1
    """
    job_cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter('ids', 'STRING', list(pendentes))
    ])
    try:
        status_bq = {str(r['DRIVER_ID']): r['DRIVER_STATUS']
                     for r in bq.query(query, job_config=job_cfg).result()}
    except Exception as e:
        print(f"  Aviso BQ status: {e}")
        return

    def mapear(s):
        if s == 'blocked':                             return 'Bloqueado'
        if s == 'active':                              return 'Monitorado'
        if s in ('inactive', 'removed', 'disabled'):  return 'Inativo'
        return None

    try:
        pl       = gs.open_by_key(BLOCK_LIST_ID)
        ws       = pl.worksheet(ABA_BLOQUEIOS)
        all_vals = ws.get_all_values()
        header   = all_vals[0] if all_vals else []
        col_id     = header.index('Driver ID') + 1 if 'Driver ID' in header else None
        col_status = header.index('Status')    + 1 if 'Status'    in header else None
        if not col_id or not col_status:
            print("  Colunas não encontradas na planilha.")
            return

        updates     = []
        status_memo = {}
        for i, row_vals in enumerate(all_vals[1:], start=2):
            did     = row_vals[col_id - 1].strip()     if len(row_vals) >= col_id     else ''
            current = row_vals[col_status - 1].strip() if len(row_vals) >= col_status else ''
            if did not in status_bq:
                continue
            novo = mapear(status_bq[did])
            if novo and novo != current:
                updates.append({'range': f'{col_letter(col_status)}{i}', 'values': [[novo]]})
                status_memo[did] = novo

        if updates:
            ws.batch_update(updates)
            print(f"  {len(updates)} status atualizados na planilha.")
            for r in bl_rows:
                did = _did(r)
                if did in status_memo:
                    # atualiza tanto a chave bruta quanto a processada
                    if 'Status' in r:    r['Status']    = status_memo[did]
                    if 'status' in r:    r['status']    = status_memo[did]
        else:
            print("  Nenhuma alteração de status necessária.")
    except Exception as e:
        print(f"  Aviso ao atualizar planilha: {e}")


def processar_block_list(rows):
    if not rows:
        return {
            'total':0,'bloqueados':0,'solicitados':0,
            'monitorados':0,'recusados':0,'gmv_protegido':0.0,
            'por_transp':{},'rows':[],'por_status':{}
        }
    def flt(v):
        try: return float(str(v).replace('$','').replace(',','.').strip() or 0)
        except: return 0.0
    def norm_status(s):
        s = s.strip().lower()
        if 'bloqueado' in s: return 'Bloqueado'
        if 'solicitado' in s: return 'Solicitado'
        if 'sendo' in s or 'monit' in s: return 'Monitorado'
        if 'recusado' in s: return 'Recusado'
        return 'Inativo'

    total = len(rows)
    bloqueados  = sum(1 for r in rows if 'bloqueado' in r.get('Status','').lower())
    solicitados = sum(1 for r in rows if 'solicitado' in r.get('Status','').lower())
    monitorados = sum(1 for r in rows if 'sendo' in r.get('Status','').lower() or 'monit' in r.get('Status','').lower())
    recusados   = sum(1 for r in rows if 'recusado' in r.get('Status','').lower())
    gmv_protegido = sum(flt(r.get('USD$','0')) for r in rows if 'bloqueado' in r.get('Status','').lower())

    por_transp = {}
    por_status = {}
    rows_out   = []
    for r in rows:
        mlp    = r.get('MLP','').strip() or 'N/A'
        status = norm_status(r.get('Status',''))
        por_transp[mlp]    = por_transp.get(mlp, 0) + 1
        por_status[status] = por_status.get(status, 0) + 1
        rows_out.append({
            'driver_id':  r.get('Driver ID','').strip(),
            'nome':       r.get('Nome','').strip(),
            'mlp':        mlp,
            'placa':      r.get('Placa','').strip(),
            'shp':        r.get('SHP','').strip(),
            'usd':        flt(r.get('USD$','0')),
            'semana':     r.get('Semana','').strip(),
            'data':       r.get('Data Solicitação','').strip(),
            'status':     status,
            'motivo':     r.get('Motivo','').strip(),
        })
    # Remove Recusado do gráfico (status descontinuado)
    por_status.pop('Recusado', None)
    # Agrupar por driver_id para histórico de solicitações
    from collections import defaultdict as _dd
    from datetime import datetime as _dt2
    _PRIO = {'Bloqueado':0,'Monitorado':1,'Solicitado':2,'Inativo':3,'Recusado':4}
    def _parse_dt(d):
        try: return _dt2.strptime(d, '%d/%m/%Y').timestamp()
        except: return 0.0
    grupos = _dd(list)
    for r in rows_out:
        grupos[r['driver_id'] or ''].append(r)
    final_rows = []
    for did, entries in grupos.items():
        entries_s = sorted(entries, key=lambda x: (_PRIO.get(x['status'],9), -_parse_dt(x['data'])))
        main = entries_s[0].copy()
        main['historico']      = entries_s
        main['n_solicitacoes'] = len(entries_s)
        final_rows.append(main)
    final_rows.sort(key=lambda x: (_PRIO.get(x['status'],9), -x['usd']))
    return {
        'total': total, 'bloqueados': bloqueados,
        'solicitados': solicitados, 'monitorados': monitorados,
        'recusados': recusados, 'gmv_protegido': gmv_protegido,
        'por_transp': por_transp, 'por_status': por_status,
        'rows': final_rows,
    }

def processar_cruzamento(df):
    if df is None or df.empty:
        return {'sellers':[],'buyers':[],'pares':[],
                'total_sellers':0,'total_buyers':0,'total_pares':0,'total_drivers':0}
    rows = df.to_dict('records')
    def _drivers(raw):
        if not raw or str(raw) in ('None','nan',''): return set()
        return {d.strip() for d in str(raw).split(',') if d.strip() and d.strip() != 'None'}

    seller_map, buyer_map = {}, {}
    for r in rows:
        sid = str(r['SELLER_ID']); bid = str(r['BUYER_ID']); qtd = int(r['QTD_FRAUDES'])
        drv = _drivers(r.get('DRIVERS',''))
        if sid not in seller_map: seller_map[sid] = {'seller_id':sid,'qtd':0,'buyers':set(),'drivers':set()}
        seller_map[sid]['qtd'] += qtd; seller_map[sid]['buyers'].add(bid); seller_map[sid]['drivers'] |= drv
        if bid not in buyer_map:  buyer_map[bid]  = {'buyer_id':bid, 'qtd':0,'sellers':set(),'drivers':set()}
        buyer_map[bid]['qtd']  += qtd; buyer_map[bid]['sellers'].add(sid); buyer_map[bid]['drivers']  |= drv

    sellers = sorted([{**v,'buyers':len(v['buyers']),'drivers':len(v['drivers'])}
                      for v in seller_map.values()], key=lambda x:-x['qtd'])
    buyers  = sorted([{**v,'sellers':len(v['sellers']),'drivers':len(v['drivers'])}
                      for v in buyer_map.values()],  key=lambda x:-x['qtd'])
    def _clean(v): return str(v) if v and str(v) not in ('None','nan','') else ''
    pares = []
    for r in rows:
        shp_raw = _clean(r.get('SHP_IDS',''))
        shp_ids = [s.strip() for s in shp_raw.split(',') if s.strip()] if shp_raw else []
        pares.append({'seller_id':str(r['SELLER_ID']),'buyer_id':str(r['BUYER_ID']),
                      'qtd':int(r['QTD_FRAUDES']),
                      'drivers':_clean(r.get('DRIVERS','')) or '—',
                      'shp_ids':shp_ids})
    all_drv = set(); [all_drv.update(_drivers(r.get('DRIVERS',''))) for r in rows]
    return {'sellers':sellers,'buyers':buyers,'pares':pares,
            'total_sellers':len(seller_map),'total_buyers':len(buyer_map),
            'total_pares':len(pares),'total_drivers':len(all_drv)}

def processar_cftv(rows):
    def _valor(v):
        try:
            return float(str(v).replace('R$','').replace('\xa0','').replace('.','').replace(',','.').strip() or 0)
        except:
            return 0.0
    def _status(s):
        s = s.strip().lower()
        if 'conclu' in s: return 'Concluído'
        if 'expira' in s or 'expid' in s: return 'SLA Vencido'
        return 'Em Andamento'

    out = []
    for r in rows:
        ts       = r.get('Carimbo de data/hora', '')
        data     = ts.split(' ')[0] if ts else ''
        data_iso = ''
        if data and len(data) == 10:
            try: data_iso = f"{data[6:]}-{data[3:5]}-{data[:2]}"
            except: pass
        status = _status(r.get('Status', ''))
        out.append({
            'data':          data,
            'data_iso':      data_iso,
            'week':          str(r.get('Week', '')).strip(),
            'solicitante':   r.get('Solicitante', '').strip(),
            'operacao':      r.get('Operação', '').strip(),
            'shp':           str(r.get('Shipment', '')).strip(),
            'produto':       str(r.get('Informe a descrição do ID', '')).strip()[:60],
            'valor':         _valor(r.get('Valor em R$', '')),
            'prioridade':    r.get('Nivel de Prioridade', '').strip(),
            'status':        status,
            'data_inicio':   r.get('Data Inicio', '').strip(),
            'data_conclusao':r.get('Data Conclusão', '').strip(),
            'sla':           str(r.get('SLA', '') or '').strip(),
            'responsavel':   r.get('Responsável', '').strip(),
            'conclusao':     r.get('Conclusão', '').strip(),
            'driver':        str(r.get('Driver', '') or '').strip(),
            'placa':         str(r.get('Placa', '') or '').strip(),
            'mlp':           str(r.get('MLP', '') or '').strip(),
        })
    out.sort(key=lambda x: x['data_iso'], reverse=True)
    total      = len(out)
    concluidos = sum(1 for r in out if r['status'] == 'Concluído')
    sla_venc   = sum(1 for r in out if r['status'] == 'SLA Vencido')
    em_and     = total - concluidos - sla_venc
    return {
        'total': total, 'concluidos': concluidos,
        'em_andamento': em_and, 'sla_vencido': sla_venc,
        'rows': out,
    }

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
            'driver':   norm_id(r['DRIVER_ID']),
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
    status_map = {}
    bloqueados = []
    for _, r in df_status.iterrows():
        did = norm_id(r.get('DRIVER_ID', ''))
        if not did: continue
        status    = str(r.get('STATUS',    '') or '')
        substatus = str(r.get('SUBSTATUS', '') or '')
        lealdade  = str(r.get('CATEGORIA', '') or 'N/A')
        # Considera "removido do mercado" se: blocked, inactive/fraud_prevention, ou removed
        removido = (
            status == 'blocked' or
            (status == 'inactive' and 'fraud' in substatus.lower()) or
            status == 'removed'
        )
        info = {
            'status':    status,
            'substatus': substatus,
            'lealdade':  lealdade,
            'removido':  removido,
        }
        status_map[did] = info
        if removido:
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
        if st.get('removido', False):
            # Label conforme substatus
            sub = st.get('substatus','')
            if st.get('status') == 'blocked':
                d['atividade'] = 'Bloqueado'
            elif 'fraud' in sub.lower():
                d['atividade'] = 'Inativo por Fraude'
            else:
                d['atividade'] = 'Removido'
            d['ativ_cor'] = '#ef4444'
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

    # ---- SHP IDs por (driver, place) — para Driver × Place correto ----
    shp_dxp = {}   # {(driver_id, place_nome): [shps]}
    for _, r in df_place_shp.iterrows():
        did  = norm_id(r.get('DRIVER_ID', ''))
        nome = str(r.get('PLACE_NOME', '')).replace('Agncia Mercado Livre - ','').replace('Agência Mercado Livre - ','')
        if not did or not nome: continue
        key = (did, nome)
        if key not in shp_dxp:
            shp_dxp[key] = []
        shp_dxp[key].append({
            'id':    str(r.get('SHP_ID', '')),
            'class': str(r.get('CLASSIFICACAO', '')),
            'bpp':   flt(r.get('BPP', 0)),
            'data':  str(r.get('DATA', '')),
        })

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

    # Meses ativos por entidade (para filtro por período cross-tab)
    for d in drivers:
        d['months'] = ' '.join(sorted({_ym(s['data']) for s in d['shps'] if s.get('data')}))
    for r in dxp:
        r['months'] = ' '.join(sorted({_ym(s['data']) for s in shp_dxp.get((r['driver'], r['place']), []) if s.get('data')}))
    for p in places:
        p['months'] = ' '.join(sorted({_ym(s['data']) for s in shp_por_place.get(p['nome'], []) if s.get('data')}))
    for dmg in damaged:
        dmg['months'] = ' '.join(sorted({_ym(s['data']) for s in shp_por_driver.get(dmg['id'], []) if s.get('data') and 'DAMAGED' in s.get('class','')}))

    # Agrega KPIs por mês — mesmo escopo dos top-60 (consistente com os cards)
    ids_top60   = {d['id'] for d in drivers}
    monthly_agg = {}
    for did, shps in shp_por_driver.items():
        if did not in ids_top60:
            continue
        for s in shps:
            ym = _ym(s['data'])
            if not ym:
                continue
            if ym not in monthly_agg:
                monthly_agg[ym] = {'fraudes': 0, 'damaged': 0, 'bpp': 0.0, 'total': 0}
            cls = s.get('class', '')
            monthly_agg[ym]['total'] += 1
            monthly_agg[ym]['bpp']   += s.get('bpp', 0.0)
            if any(x in cls for x in ('LOST', 'FRAUD')):
                monthly_agg[ym]['fraudes'] += 1
            elif 'DAMAGED' in cls:
                monthly_agg[ym]['damaged'] += 1

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
        'shp_dxp':           shp_dxp,
        'drivers_ativos':    drivers_ativos,
        'drivers_bloqueados':drivers_bloqueados,
        'total_bloqueados':  len(drivers_bloqueados),
        'bl':                {},  # preenchido no main após carregar_block_list
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
        'monthly_agg':         monthly_agg,
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

MELI_URL = 'https://shipping-bo.adminml.com/sauron/shipments/shipment'

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
            data-ativ="{d.get("atividade","").lower()}"
            data-months="{d.get("months","")}"
            data-prio="{d.get("prio","").lower()}"
            data-cruzado="{'1' if d['id'] in cruzados else '0'}">
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

def status_bl_badge(s):
    cores = {'Bloqueado':('#064e3b','#4ade80'), 'Solicitado':('#1e3a5f','#60a5fa'),
             'Monitorado':('#713f12','#fde68a'), 'Recusado':('#7f1d1d','#fca5a5'),
             'Inativo':('#1f2937','#6b7280')}
    bg, fg = cores.get(s, ('#1f2937','#9ca3af'))
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{s}</span>'

def rows_block_list(rows):
    from datetime import datetime as _dtbl
    def _iso(d):
        try: return _dtbl.strptime(d.strip(), '%d/%m/%Y').strftime('%Y-%m-%d')
        except: return ''

    out = ''
    for idx, r in enumerate(rows):
        did      = r['driver_id']
        link     = f'https://shipping-bo.adminml.com/sauron/shipments/shipment/{did}' if did else '#'
        data_iso = _iso(r["data"]) if r["data"] else ''
        n_sol    = r.get('n_solicitacoes', 1)
        row_id   = f'blh_{idx}'

        # badge de contagem de solicitações
        badge_hist = ''
        if n_sol > 1:
            badge_hist = f' <span title="Ver histórico" style="background:#1e3a5f;color:#60a5fa;font-size:10px;font-weight:700;padding:1px 6px;border-radius:10px;cursor:pointer" onclick="toggleBl(\'{row_id}\')">{n_sol}x</span>'

        # célula do driver: com ou sem expand
        if n_sol > 1:
            driver_cell = f'''<td style="font-weight:700;cursor:pointer" onclick="toggleBl('{row_id}')">
              <span id="{row_id}_arrow" style="color:#4b5563;margin-right:3px;font-size:10px">▶</span>
              <a href="{link}" target="_blank" style="color:#60a5fa;text-decoration:none;font-family:monospace;font-size:12px" onclick="event.stopPropagation()">{did or "—"}</a>{badge_hist}
            </td>'''
        else:
            driver_cell = f'''<td style="font-weight:700">
              <a href="{link}" target="_blank" style="color:#60a5fa;text-decoration:none;font-family:monospace;font-size:12px">{did or "—"}</a>
            </td>'''

        search_txt = f'{did} {r["nome"]}'.lower()
        out += f'''<tr class="bl-row" data-data="{data_iso}" data-status="{r["status"]}" data-transp="{r["mlp"]}" data-usd="{r["usd"]}" data-search="{search_txt}">
            {driver_cell}
            <td style="font-size:12px;color:#d1d5db">{r["nome"] or "—"}</td>
            <td style="font-size:11px;color:#9ca3af">{r["mlp"]}</td>
            <td style="font-size:11px;color:#6b7280">{r["placa"] or "—"}</td>
            <td style="text-align:center">{r["shp"] or "—"}</td>
            <td style="color:#10b981;font-weight:600">${r["usd"]:,.2f}</td>
            <td>{status_bl_badge(r["status"])}</td>
            <td style="font-size:11px;color:#9ca3af">{r["motivo"] or "—"}</td>
            <td style="font-size:11px;color:#6b7280">{r["data"] or "—"}</td>
            <td style="font-size:11px;color:#6b7280">Sem {r["semana"]}</td>
        </tr>'''

        # subrow com histórico completo
        if n_sol > 1:
            hist_rows = ''
            for i, h in enumerate(r.get('historico', []), 1):
                hist_rows += f'''<tr style="background:#060c1a">
                    <td style="padding:4px 8px;font-size:11px;color:#6b7280;text-align:center">#{i}</td>
                    <td style="padding:4px 8px;font-size:11px;color:#9ca3af">{h["data"] or "—"}</td>
                    <td style="padding:4px 8px;font-size:11px">Sem {h["semana"]}</td>
                    <td style="padding:4px 8px">{status_bl_badge(h["status"])}</td>
                    <td style="padding:4px 8px;font-size:11px;color:#6b7280">{h["motivo"] or "—"}</td>
                    <td style="padding:4px 8px;font-size:11px;color:#10b981">${h["usd"]:,.2f}</td>
                </tr>'''
            out += f'''<tr id="{row_id}" class="bl-hist-row" style="display:none">
                <td colspan="10" style="padding:0 0 6px 32px;background:#07111e">
                    <table style="width:100%;border-collapse:collapse;border:1px solid #1e3a5f;border-radius:4px">
                        <thead><tr style="background:#0a1929">
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:center;width:32px">#</th>
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:left">Data</th>
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:left">Semana</th>
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:left">Status</th>
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:left">Motivo</th>
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:left">USD$</th>
                        </tr></thead>
                        <tbody>{hist_rows}</tbody>
                    </table>
                </td>
            </tr>'''
    return out

def rows_cftv(rows):
    STATUS_COR  = {'Concluído':'#10b981','Em Andamento':'#3b82f6','SLA Vencido':'#ef4444'}
    PRIO_COR    = {'Alto':'#ef4444','Moderado':'#f59e0b'}
    CONCL_COR   = {'Conclusivo':'#10b981','Inconclusivo':'#ef4444'}
    out = ''
    for r in rows:
        st_cor  = STATUS_COR.get(r['status'], '#9ca3af')
        pr_cor  = PRIO_COR.get(r['prioridade'], '#9ca3af')
        co_cor  = CONCL_COR.get(r['conclusao'], '#6b7280')
        shp_link = (f'<a href="{MELI_URL}/{r["shp"]}" target="_blank" '
                    f'style="color:#60a5fa;text-decoration:none;font-family:monospace;font-size:12px">{r["shp"]}</a>'
                    if r['shp'] else '—')
        search_txt = f'{r["shp"]} {r["driver"]} {r["solicitante"]} {r["produto"]}'.lower()
        prod_esc   = r['produto'].replace('"', '&quot;')
        out += f'''<tr class="cftv-row" data-operacao="{r["operacao"]}" data-status="{r["status"]}" data-prio="{r["prioridade"]}" data-search="{search_txt}">
          <td style="font-size:11px;color:#9ca3af;white-space:nowrap">{r["data"]}</td>
          <td style="font-size:11px;color:#6b7280">W{r["week"]}</td>
          <td><span style="background:#1f2937;color:#e2e8f0;border-radius:4px;padding:2px 6px;font-size:10px;font-weight:600">{r["operacao"]}</span></td>
          <td>{shp_link}</td>
          <td style="font-size:11px;color:#d1d5db;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{prod_esc}">{r["produto"]}</td>
          <td style="color:#10b981;font-size:12px;text-align:right;white-space:nowrap">R${r["valor"]:,.2f}</td>
          <td><span style="color:{pr_cor};font-size:11px;font-weight:600">{r["prioridade"] or "—"}</span></td>
          <td><span style="color:{st_cor};font-size:11px;font-weight:600">{r["status"]}</span></td>
          <td style="font-size:11px;color:#9ca3af;text-align:center">{r["sla"] or "—"}</td>
          <td style="font-size:11px;color:#d1d5db">{r["responsavel"] or "—"}</td>
          <td><span style="color:{co_cor};font-size:11px">{r["conclusao"] or "—"}</span></td>
          <td style="font-size:11px;color:#9ca3af">{r["driver"] or "—"}</td>
        </tr>'''
    return out

def rows_historico_bloqueios(bloqueados):
    if not bloqueados:
        return ''
    rows = ''
    for b in bloqueados:
        rows += f'''<tr style="background:#051505">
            <td style="font-weight:700;color:#4ade80">{b["id"]}</td>
            <td>{lealdade_badge(b.get("lealdade","N/A"))}</td>
            <td style="color:#6b7280;font-size:11px">{b.get("atividade",b.get("substatus",""))}</td>
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

def rows_dxp(dxp, shp_por_driver, shp_dxp):
    out = ''
    for i, r in enumerate(dxp):
        alert  = r['total'] >= 5
        bg     = 'background:#1a0a0a' if alert else ''
        row_id = f'dxp_{i}'
        # SHP IDs que cruzam especificamente este driver + este place
        shps = shp_dxp.get((r['driver'], r['place']), [])
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
        out += f'''<tr style="{bg}" {toggle} data-months="{r.get("months","")}">
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
        out += f'''<tr {toggle} data-months="{p.get("months","")}">
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
        out += f'''<tr {toggle} data-months="{d.get("months","")}">
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
            rows_cruzados += f'''<tr style="background:#160a0a" data-months="{drv.get("months","")}">
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
  body{{background:#080d19;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:flex;flex-direction:column;height:100vh;overflow:hidden}}
  .header{{background:#080d19;padding:16px 32px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #7f1d1d;flex-shrink:0}}
  .header-brand{{display:flex;align-items:center;gap:10px}}
  .header-accent{{width:3px;height:28px;background:#ef4444;border-radius:2px}}
  .header-title{{font-size:16px;font-weight:700;color:#ffffff}}
  .header-sub{{font-size:11px;color:#374151;margin-top:2px}}
  .app-body{{display:flex;flex:1;overflow:hidden}}
  .sidebar{{width:220px;flex-shrink:0;background:#060a14;border-right:1px solid #111827;overflow-y:auto;padding:12px 0;display:flex;flex-direction:column}}
  .sb-divider{{height:1px;background:#111827;margin:8px 0;flex-shrink:0}}
  .sb-section-header{{padding:10px 16px 4px;font-size:9px;text-transform:uppercase;letter-spacing:1px;color:#374151;font-weight:700;flex-shrink:0}}
  .sb-item{{display:flex;align-items:center;gap:9px;padding:9px 16px;font-size:12px;color:#6b7280;cursor:pointer;transition:all .2s;border-left:2px solid transparent;white-space:nowrap;flex-shrink:0}}
  .sb-item:hover{{background:#0d1321;color:#e2e8f0}}
  .sb-item.active{{background:#0d1321;color:#ffffff;border-left-color:#ef4444;font-weight:600}}
  .main-content{{flex:1;overflow-y:auto}}
  .content{{display:none;padding:28px 32px}}
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
  /* PERIOD BUTTONS */
  .pbtn{{background:#0d1321;border:1px solid #1f2937;border-radius:20px;padding:5px 14px;color:#6b7280;font-size:11px;cursor:pointer;transition:all .2s ease;white-space:nowrap}}
  .pbtn:hover{{background:#111827;color:#e2e8f0;border-color:#374151}}
  .pbtn.ativo{{background:#ef4444;border-color:#ef4444;color:#fff;font-weight:600}}
  /* DATE PICKER */
  input[type="date"],input[type="month"]{{background:#0d1321;border:1px solid #1f2937;border-radius:6px;padding:7px 12px;color:#9ca3af;font-size:12px;outline:none;cursor:pointer;transition:border-color .3s ease;color-scheme:dark}}
  input[type="date"]:focus,input[type="month"]:focus{{border-color:#374151;color:#e2e8f0}}
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
  .nav-dropdown.open{{display:block}}
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
      <div class="header-sub">Base {d["ano"]} · <span id="upd-badge">Gerado em {d["gerado"]}</span><span id="upd-ts" data-ts="{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}" style="display:none"></span></div>
    </div>
  </div>
  <a href="https://github.com/lucasunascimento-bit/risco-ssp30/actions/workflows/update-dashboard.yml"
     target="_blank" title="Atualizar dados"
     style="position:fixed;bottom:20px;right:20px;background:#064e3b;color:#4ade80;border:1px solid #166534;border-radius:50%;width:38px;height:38px;display:flex;align-items:center;justify-content:center;text-decoration:none;font-size:18px;z-index:999;box-shadow:0 2px 8px rgba(0,0,0,.5);transition:all .2s"
     onmouseover="this.style.background='#065f46';this.style.transform='scale(1.1)'" onmouseout="this.style.background='#064e3b';this.style.transform='scale(1)'">↻</a>
  <div class="nav-wrap">
    <button class="nav-btn" onclick="toggleNav(event)">⊞ Dashboards ▾</button>
    <div class="nav-dropdown">
      <a href="./index.html">🔔 Risco SSP30 — ON ROUTE / ON WAY</a>
      <a href="./fraude.html" class="nav-active">🔍 Análise de Fraude SSP30</a>
    </div>
  </div>
</div>

<div class="app-body">
<nav class="sidebar">
  <div class="sb-item active" data-tab="geral" onclick="showTab('geral',this)">
    <i data-lucide="bar-chart-2" width="14" height="14" class="ci"></i> Visão Geral
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Análise de Risco</div>
  <div class="sb-item" data-tab="drivers" onclick="showTab('drivers',this)">
    <i data-lucide="user" width="14" height="14" class="ci"></i>
    Por Driver (<span id="tab-count-drivers">{len(d["drivers_ativos"])}</span>)
  </div>
  <div class="sb-item" data-tab="dxp" onclick="showTab('dxp',this)">
    <i data-lucide="map-pin" width="14" height="14" class="ci"></i>
    Driver × Place (<span id="tab-count-dxp">{len(d["dxp"])}</span>)
  </div>
  <div class="sb-item" data-tab="places" onclick="showTab('places',this)">
    <i data-lucide="building-2" width="14" height="14" class="ci"></i>
    Ofensores Places (<span id="tab-count-places">{d["total_places"]}</span>)
  </div>
  <div class="sb-item" data-tab="damaged" onclick="showTab('damaged',this)">
    <i data-lucide="package-x" width="14" height="14" class="ci"></i>
    Damaged (<span id="tab-count-damaged">{len(d["damaged"])}</span>)
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Block List</div>
  <div class="sb-item" data-tab="bloqueios" onclick="showTab('bloqueios',this)" style="color:#4ade80">
    <i data-lucide="shield" width="14" height="14" class="ci"></i>
    Bloqueios (<span id="tab-count-bloqueios">{d["bl"]["total"]}</span>)
  </div>
  <div class="sb-item" data-tab="cruzamento" onclick="showTab('cruzamento',this)" style="color:#f59e0b">
    <i data-lucide="git-merge" width="14" height="14" class="ci"></i>
    BSD ({d["crz"]["total_pares"]})
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Investigações</div>
  <div class="sb-item" data-tab="cftv" onclick="showTab('cftv',this)" style="color:#a78bfa">
    <i data-lucide="camera" width="14" height="14" class="ci"></i>
    CFTV ({d["cftv"]["total"]})
  </div>
</nav>
<main class="main-content">

<!-- BARRA DE PERÍODO — sempre visível em todas as abas -->
<div style="background:#080d19;border-bottom:1px solid #1f2937;padding:10px 32px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
  <span class="filter-label">Período:</span>
  <span style="font-size:11px;color:#6b7280">De</span>
  <input type="month" id="pd_de" onchange="setPeriodo()" min="{d["ano"]}-01" max="{d["ano"]}-12" style="max-width:150px">
  <span style="font-size:11px;color:#6b7280">Até</span>
  <input type="month" id="pd_ate" onchange="setPeriodo()" min="{d["ano"]}-01" max="{d["ano"]}-12" style="max-width:150px">
  <button onclick="resetPeriodo()" style="background:#1f2937;color:#6b7280;border:1px solid #374151;border-radius:6px;padding:6px 12px;font-size:11px;cursor:pointer">Limpar</button>
  <span id="pd_label" style="font-size:12px;font-weight:600;color:#60a5fa"></span>
</div>

<!-- VISÃO GERAL -->
<div id="tab-geral" class="content active">

  <div class="cards">
    <div class="card c-red">
      <div class="card-header"><i data-lucide="alert-triangle" class="ci" width="14" height="14" style="color:#7f1d1d"></i><span class="cl">Drivers Críticos</span></div>
      <div class="cv red" id="cv-criticos">{d["criticos"]}</div>
      <div class="cd" id="sub-criticos">Prioridade Alta ou Máxima</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="package-x" class="ci" width="14" height="14"></i><span class="cl">Total Fraudes/Lost</span></div>
      <div class="cv" id="cv-fraudes">{d["total_fraudes"]}</div>
      <div class="cd" id="sub-fraudes">{d["ano"]}</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="box" class="ci" width="14" height="14"></i><span class="cl">Total Damaged</span></div>
      <div class="cv amber" id="cv-damaged">{d["total_damaged"]}</div>
      <div class="cd" id="sub-damaged">{d["ano"]}</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="dollar-sign" class="ci" width="14" height="14"></i><span class="cl">BPP Total</span></div>
      <div class="cv green" id="cv-bpp">${d["total_bpp"]:,.2f}</div>
      <div class="cd" id="sub-bpp">Cashout {d["ano"]}</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="map-pin" class="ci" width="14" height="14"></i><span class="cl">Places Suspeitos</span></div>
      <div class="cv" id="cv-places">{d["total_places"]}</div>
      <div class="cd">Com fraude/lost</div>
    </div>
    <div class="card c-red">
      <div class="card-header"><i data-lucide="zap" class="ci" width="14" height="14" style="color:#7f1d1d"></i><span class="cl">Cruzados F+D</span></div>
      <div class="cv red" id="cv-cruzados">{len(d["cruzados"])}</div>
      <div class="cd">Fraude + Damaged</div>
    </div>
    <div class="card card-ok card-link" onclick="irPara('bloqueios')">
      <div class="card-header"><i data-lucide="shield-check" class="ci" width="14" height="14" style="color:#064e3b"></i><span class="cl">Bloqueios Confirmados</span></div>
      <div class="cv val-ok">{d["bl"]["bloqueados"]}</div>
      <div class="card-delta">de {d["bl"]["total"]} sol. · ${d["bl"]["gmv_protegido"]:,.0f} protegido</div>
    </div>
  </div>

  {"" if not d["cruzados"] else f'''
  <div class="alerta-box" id="alerta-cruzados">
    <div class="num" id="num-cruzados-alert">{len(d["cruzados"])}</div>
    <div class="txt"><strong><span id="num-cruzados-txt">{len(d["cruzados"])}</span> drivers aparecem em AMBAS as análises (Fraude + Damaged)</strong><br>
    Estes são os principais alvos para investigação e bloqueio.</div>
  </div>
  <div class="tbl-wrap" id="wrap-cruzados">
    <div class="tbl-title">Drivers com Fraude + Damaged (maior risco)</div>
    <div class="tbl-scroll"><table id="tbl_cruzados">
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
    <div class="tbl-title">Ranking Ativo — Drivers em Atuação (<span id="count-drivers-ativos">{len(d["drivers_ativos"])}</span>)</div>
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
    <div class="tbl-scroll"><table id="tbl_dxp">
      <thead><tr><th>Driver ID</th><th>Place</th><th>Total</th><th>Fraudes</th><th>Damaged</th><th>BPP Total</th></tr></thead>
      <tbody>{rows_dxp(d["dxp"], d["shp_por_driver"], d["shp_dxp"])}</tbody>
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

  <div class="box mb16"><div class="bt">Top 8 Places — Total de Fraudes/Lost</div><canvas id="cPlacesBar" height="200"></canvas></div>

  <div class="tbl-wrap">
    <div class="tbl-title">Ranking completo — Places Ofensores</div>
    <div class="tbl-scroll"><table id="tbl_places">
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
    <div class="tbl-scroll"><table id="tbl_damaged">
      <thead><tr>
        <th>Driver ID</th><th>Total Damaged</th><th>BPP Total</th>
        <th>On Route</th><th>At Station</th><th>ENE</th>
      </tr></thead>
      <tbody>{rows_damaged(d["damaged"], d["cruzados"], d["shp_por_driver"])}</tbody>
    </table></div>
  </div>
</div>

<script>
// Filtro da aba Bloqueios — usa período global _periodDe/_periodAte
function filtrarBloqueios() {{
  const status = document.getElementById('bl_status')?.value || '';
  const transp = document.getElementById('bl_transp')?.value || '';
  const search = (document.getElementById('bl_search')?.value || '').toLowerCase();
  document.querySelectorAll('.bl-row').forEach(tr => {{
    const d   = tr.dataset.data   || '';
    const ym  = d.substring(0,7);
    const st  = tr.dataset.status || '';
    const tp  = tr.dataset.transp || '';
    const src = tr.dataset.search || '';
    const ok  = (!_periodDe || ym >= _periodDe)
             && (!_periodAte || ym <= _periodAte)
             && (!status || st === status)
             && (!transp || tp === transp)
             && (!search || src.includes(search));
    tr.style.display = ok ? '' : 'none';
    const nx = tr.nextElementSibling;
    if (nx && nx.classList.contains('bl-hist-row') && !ok) nx.style.display = 'none';
  }});
  updateBloqueiosCards();
}}

// Exportar linhas visíveis como CSV
function exportBlCSV() {{
  const cols = ['Driver ID','Nome','Transportadora','Placa','SHP','USD$','Status','Motivo','Data','Semana'];
  const rows = [cols.join(',')];
  document.querySelectorAll('.bl-row').forEach(tr => {{
    if (tr.style.display === 'none') return;
    const tds = [...tr.querySelectorAll('td')];
    const vals = tds.map(td => '"' + td.textContent.trim().replace(/"/g,'""') + '"');
    rows.push(vals.join(','));
  }});
  const blob = new Blob([rows.join('\\n')], {{type:'text/csv;charset=utf-8;'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'block_list_ssp30.csv';
  a.click();
}}

// Ordenação das colunas
let _blSortCol = null, _blSortDir = 1;
function sortBl(col) {{
  if (_blSortCol === col) _blSortDir *= -1; else {{ _blSortCol = col; _blSortDir = 1; }}
  ['did','usd','status','data'].forEach(c => {{
    const el = document.getElementById('bl-sort-' + c);
    if (el) el.textContent = c === col ? (_blSortDir === 1 ? ' ↑' : ' ↓') : '';
  }});
  const tbody = document.getElementById('bl-tbody');
  if (!tbody) return;
  const all = [...tbody.children];
  const units = [];
  let i = 0;
  while (i < all.length) {{
    const main = all[i];
    const nx   = all[i+1];
    if (nx && nx.classList.contains('bl-hist-row')) {{ units.push([main, nx]); i += 2; }}
    else {{ units.push([main]); i++; }}
  }}
  const getVal = (tr) => {{
    if (col === 'usd')    return parseFloat(tr.dataset.usd || '0');
    if (col === 'status') return tr.dataset.status || '';
    if (col === 'data')   return tr.dataset.data   || '';
    if (col === 'did')    return (tr.dataset.search || '').split(' ')[0];
    return '';
  }};
  units.sort((a,b) => {{
    const va = getVal(a[0]), vb = getVal(b[0]);
    return (va < vb ? -1 : va > vb ? 1 : 0) * _blSortDir;
  }});
  units.forEach(u => u.forEach(tr => tbody.appendChild(tr)));
}}

// Badge "atualizado há X min"
function _updBadge() {{
  const ts = document.getElementById('upd-ts')?.dataset.ts;
  const badge = document.getElementById('upd-badge');
  if (!ts || !badge) return;
  const diff = Math.round((Date.now() - new Date(ts).getTime()) / 60000);
  badge.textContent = diff < 1 ? 'Atualizado agora' : `Atualizado há ${{diff}} min`;
}}
_updBadge();
setInterval(_updBadge, 60000);

function toggleBl(id) {{
  const el = document.getElementById(id);
  const ar = document.getElementById(id + '_arrow');
  if (!el) return;
  const show = el.style.display === 'none';
  el.style.display = show ? '' : 'none';
  if (ar) ar.textContent = show ? '▼' : '▶';
}}

function updateBloqueiosCards() {{
  const set = (id, v) => {{ const e = document.getElementById(id); if(e) e.textContent = v; }};
  const counts = {{}};
  const byTransp = {{}};
  let total = 0, gmv = 0;
  document.querySelectorAll('.bl-row').forEach(tr => {{
    if (tr.style.display !== 'none') {{
      total++;
      const st  = tr.dataset.status || '';
      const tp  = tr.dataset.transp || '';
      const usd = parseFloat(tr.dataset.usd || '0') || 0;
      counts[st]  = (counts[st]  || 0) + 1;
      byTransp[tp] = (byTransp[tp] || 0) + 1;
      if (st === 'Bloqueado') gmv += usd;
    }}
  }});
  set('bl-cv-total',      total);
  set('bl-cv-bloqueados', counts['Bloqueado']  || 0);
  set('bl-cv-solicitados',counts['Solicitado'] || 0);
  set('bl-cv-monitorados',counts['Monitorado'] || 0);
  set('bl-cv-gmv', '$' + gmv.toLocaleString('en-US', {{minimumFractionDigits:2,maximumFractionDigits:2}}));
  if (_chartBlStatus) {{
    const labels = Object.keys(counts).filter(k => BL_COLORS[k]);
    _chartBlStatus.data.labels = labels;
    _chartBlStatus.data.datasets[0].data = labels.map(k => counts[k]);
    _chartBlStatus.data.datasets[0].backgroundColor = _blColors(labels);
    _chartBlStatus.update();
  }}
  if (_chartBlTransp) {{
    const sorted = Object.entries(byTransp).sort((a,b) => b[1]-a[1]);
    _chartBlTransp.data.labels = sorted.map(e => e[0]);
    _chartBlTransp.data.datasets[0].data = sorted.map(e => e[1]);
    _chartBlTransp.update();
  }}
}}

// Menu Dashboards — abre/fecha com click, fecha ao clicar fora
function toggleNav(e) {{
  e.stopPropagation();
  const dd = document.querySelector('.nav-dropdown');
  dd.classList.toggle('open');
}}
document.addEventListener('click', () => {{
  document.querySelectorAll('.nav-dropdown').forEach(d => d.classList.remove('open'));
}});

// Filtro da tabela de drivers
function filtrarDrivers() {{
  const busca  = (document.getElementById('busca_driver')?.value || '').toLowerCase();
  const transp = (document.getElementById('filtro_transp')?.value || '').toLowerCase();
  const ativ   = (document.getElementById('filtro_ativ')?.value || '').toLowerCase();
  document.querySelectorAll('#tbl_drivers > tbody > tr[data-id]').forEach(tr => {{
    const id    = (tr.dataset.id    || '').toLowerCase();
    const tp    = (tr.dataset.transp|| '').toLowerCase();
    const at    = (tr.dataset.ativ  || '').toLowerCase();
    const periodOk = (!_periodDe && !_periodAte) || (tr.dataset.months||'').split(' ').some(m => (!_periodDe || m >= _periodDe) && (!_periodAte || m <= _periodAte));
    const ok = periodOk
            && (!busca  || id.includes(busca))
            && (!transp || tp.includes(transp))
            && (!ativ   || at.includes(ativ));
    tr.style.display = ok ? '' : 'none';
    const nextSibling = tr.nextElementSibling;
    if (nextSibling && nextSibling.tagName === 'TBODY' && !ok) nextSibling.style.display = 'none';
  }});
  updateCountCards();
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

const ALL_TABS = ['geral','drivers','dxp','places','damaged','bloqueios','cruzamento','cftv'];
function showTab(name, el) {{
  _currentTab = name;
  document.querySelectorAll('.content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.sb-item').forEach(e => e.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  el.classList.add('active');
  history.replaceState(null,'','#'+name);
  applyPeriodoToTab(name);
  if (name === 'bloqueios') initBlCharts();
}}
window.addEventListener('load', () => {{
  const h = window.location.hash.replace('#','');
  if (ALL_TABS.includes(h)) {{
    const el = document.querySelector(`.sb-item[data-tab="${{h}}"]`);
    if (el) showTab(h, el);
  }}
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
    labels: {j([p["nome"][:22]+"…" if len(p["nome"])>22 else p["nome"] for p in d["places"][:8]])},
    datasets: [
      {{
        label: 'Fraudes/Lost',
        data: {j([p["total"] for p in d["places"][:8]])},
        backgroundColor: [
          'rgba(239,68,68,0.9)','rgba(239,68,68,0.82)','rgba(239,68,68,0.74)',
          'rgba(239,68,68,0.66)','rgba(239,68,68,0.58)','rgba(239,68,68,0.50)',
          'rgba(239,68,68,0.42)','rgba(239,68,68,0.34)'
        ],
        borderRadius: 6,
        barThickness: 18,
      }}
    ]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: ctx => ` ${{ctx.parsed.x}} incidentes`
        }}
      }}
    }},
    scales: {{
      x: {{ ticks: {{color:'#6b7280',font:{{size:10}}}}, grid: {{color:'#1e293b'}} }},
      y: {{ ticks: {{color:'#d1d5db',font:{{size:11}}}}, grid: {{display:false}} }}
    }}
  }}
}});

// Mapa fixo de cores por status — evita troca de cor ao atualizar dinamicamente
const BL_COLORS = {{Bloqueado:'#10b981',Solicitado:'#3b82f6',Monitorado:'#f59e0b',Inativo:'#6b7280'}};
function _blColors(labels) {{ return labels.map(l => BL_COLORS[l] || '#94a3b8'); }}

// Gráficos de Bloqueios — criados na 1ª vez que a aba abre
let _blDone = false, _chartBlStatus = null, _chartBlTransp = null;
function initBlCharts() {{
  if (_blDone) return;
  _blDone = true;
  const _stLabels = {j([k for k in d["bl"]["por_status"].keys() if k in ('Bloqueado','Monitorado','Solicitado','Inativo')])};
  const _stData   = {j([v for k, v in d["bl"]["por_status"].items() if k in ('Bloqueado','Monitorado','Solicitado','Inativo')])};
  _chartBlStatus = new Chart(document.getElementById('cBlStatus'), {{
    type: 'doughnut',
    data: {{
      labels: _stLabels,
      datasets: [{{ data: _stData,
        backgroundColor: _blColors(_stLabels), borderWidth:0 }}]
    }},
    options: {{ responsive:true, plugins:{{ legend:{{ labels:{{ color:'#94a3b8',font:{{size:11}} }} }} }}, cutout:'40%' }}
  }});
  _chartBlTransp = new Chart(document.getElementById('cBlTransp'), {{
    type: 'bar',
    data: {{
      labels: {j(list(d["bl"]["por_transp"].keys()))},
      datasets: [{{ data: {j(list(d["bl"]["por_transp"].values()))},
        backgroundColor: 'rgba(74,222,128,0.75)', borderRadius:4 }}]
    }},
    options: {{
      responsive:true, plugins:{{ legend:{{display:false}} }},
      scales:{{ x:{{ ticks:{{color:'#8a8a8a'}}, grid:{{color:'#1e293b'}} }},
                y:{{ ticks:{{color:'#8a8a8a'}}, grid:{{color:'#334155'}} }} }}
    }}
  }});
}}

// ---- Filtro de período ----
const MONTHLY = {j(d.get("monthly", []))};
const ANNUAL  = {{
  fraudes: MONTHLY.reduce((s,m)=>s+m.fraudes,0),
  damaged: MONTHLY.reduce((s,m)=>s+m.damaged,0),
  bpp:     MONTHLY.reduce((s,m)=>s+m.bpp,0)
}};
let _periodDe = '', _periodAte = '';
let _currentTab = 'geral';

// Aplica filtro de período apenas na aba indicada
function applyPeriodoToTab(name) {{
  const filterByMonths = (tblId) => {{
    const tbl = document.getElementById(tblId);
    if (!tbl) return;
    tbl.querySelectorAll('tbody > tr[data-months]').forEach(tr => {{
      const months = (tr.dataset.months||'').split(' ');
      const show = (!_periodDe && !_periodAte) ? true
        : months.some(m => (!_periodDe || m >= _periodDe) && (!_periodAte || m <= _periodAte));
      tr.style.display = show ? '' : 'none';
      const nx = tr.nextElementSibling;
      if (nx && nx.tagName === 'TBODY' && !show) nx.style.display = 'none';
    }});
  }};
  if      (name === 'geral')    {{ filterByMonths('tbl_cruzados'); updateCountCards(); }}
  else if (name === 'drivers')  {{ filtrarDrivers(); }}
  else if (name === 'dxp')      {{ filterByMonths('tbl_dxp'); }}
  else if (name === 'places')   {{ filterByMonths('tbl_places'); }}
  else if (name === 'damaged')  {{ filterByMonths('tbl_damaged'); }}
  else if (name === 'bloqueios')   {{ filtrarBloqueios(); }}
  // cruzamento: sem filtro de período (dados estáticos do BQ)
}}

function setPeriodo() {{
  const de  = document.getElementById('pd_de').value;
  const ate = document.getElementById('pd_ate').value;
  _periodDe  = de;
  _periodAte = ate;

  const meses = MONTHLY.filter(m => (!de || m.key >= de) && (!ate || m.key <= ate));
  const dt = (!de && !ate) ? ANNUAL
    : meses.length > 0
      ? {{ fraudes: meses.reduce((s,m)=>s+m.fraudes,0), damaged: meses.reduce((s,m)=>s+m.damaged,0), bpp: meses.reduce((s,m)=>s+m.bpp,0) }}
      : {{ fraudes:0, damaged:0, bpp:0 }};

  document.getElementById('cv-fraudes').textContent = dt.fraudes.toLocaleString('pt-BR');
  document.getElementById('cv-damaged').textContent = dt.damaged.toLocaleString('pt-BR');
  document.getElementById('cv-bpp').textContent = '$' + dt.bpp.toLocaleString('en-US', {{minimumFractionDigits:2, maximumFractionDigits:2}});

  const lbl_de  = de  ? (MONTHLY.find(m=>m.key===de)?.label  || de)  : '';
  const lbl_ate = ate ? (MONTHLY.find(m=>m.key===ate)?.label || ate) : '';
  const lbl = (!de && !ate) ? '{d["ano"]}'
    : (lbl_de && lbl_ate) ? lbl_de + ' → ' + lbl_ate
    : lbl_de || lbl_ate;
  document.getElementById('sub-fraudes').textContent = lbl;
  document.getElementById('sub-damaged').textContent = lbl;
  document.getElementById('sub-bpp').textContent = 'Cashout ' + lbl;
  document.getElementById('pd_label').textContent = (!de && !ate) ? '' : '📅 ' + lbl;

  applyPeriodoToTab(_currentTab);
  _updateAllTabCounts();
}}

// Atualiza contadores de todas as abas sem mexer nas linhas das abas inativas
function _updateAllTabCounts() {{
  const _set = (id, v) => {{ const e = document.getElementById(id); if (e) e.textContent = v; }};
  const countMonths = (tblId) => {{
    let n = 0;
    const tbl = document.getElementById(tblId);
    if (!tbl) return 0;
    tbl.querySelectorAll('tbody > tr[data-months]').forEach(tr => {{
      const months = (tr.dataset.months||'').split(' ');
      if ((!_periodDe && !_periodAte) || months.some(m => (!_periodDe||m>=_periodDe)&&(!_periodAte||m<=_periodAte))) n++;
    }});
    return n;
  }};
  _set('tab-count-drivers', countMonths('tbl_drivers'));
  _set('tab-count-dxp',     countMonths('tbl_dxp'));
  _set('tab-count-places',  countMonths('tbl_places'));
  _set('tab-count-damaged', countMonths('tbl_damaged'));
  let blCount = 0;
  document.querySelectorAll('.bl-row').forEach(tr => {{
    const ym = (tr.dataset.data||'').substring(0,7);
    if ((!_periodDe||ym>=_periodDe)&&(!_periodAte||ym<=_periodAte)) blCount++;
  }});
  _set('tab-count-bloqueios', blCount);
}}

function resetPeriodo() {{
  const first = MONTHLY.length > 0 ? MONTHLY[0].key                   : '';
  const last  = MONTHLY.length > 0 ? MONTHLY[MONTHLY.length-1].key   : '';
  document.getElementById('pd_de').value  = first;
  document.getElementById('pd_ate').value = last;
  setPeriodo();
}}

function updateCountCards() {{
  function vis(sel) {{
    let n = 0;
    document.querySelectorAll(sel).forEach(tr => {{ if (tr.style.display !== 'none') n++; }});
    return n;
  }}
  function set(id, v) {{ const e = document.getElementById(id); if (e) e.textContent = v; }}

  // Drivers críticos e ranking ativo
  let criticos = 0, ativos = 0;
  document.querySelectorAll('#tbl_drivers > tbody > tr[data-prio]').forEach(tr => {{
    if (tr.style.display !== 'none') {{
      ativos++;
      if (tr.dataset.prio === 'prioridade maxima' || tr.dataset.prio === 'alta') criticos++;
    }}
  }});
  set('cv-criticos', criticos);
  set('count-drivers-ativos', ativos);
  set('tab-count-drivers', ativos);

  // Places
  const placesN = vis('#tbl_places > tbody > tr[data-months]');
  set('cv-places', placesN);
  set('tab-count-places', placesN);

  // DxP
  set('tab-count-dxp', vis('#tbl_dxp > tbody > tr[data-months]'));

  // Damaged
  set('tab-count-damaged', vis('#tbl_damaged > tbody > tr[data-months]'));

  // Cruzados F+D — filtra tabela e alerta
  let cruzados = 0;
  document.querySelectorAll('#tbl_cruzados > tbody > tr[data-months]').forEach(tr => {{
    if (tr.style.display !== 'none') cruzados++;
  }});
  set('cv-cruzados', cruzados);
  set('num-cruzados-alert', cruzados);
  set('num-cruzados-txt', cruzados);
  const alerta = document.getElementById('alerta-cruzados');
  const wrapCruz = document.getElementById('wrap-cruzados');
  if (alerta)   alerta.style.display   = cruzados > 0 ? '' : 'none';
  if (wrapCruz) wrapCruz.style.display = cruzados > 0 ? '' : 'none';
}}

// Inicializa: intervalo completo disponível
(function() {{
  const first = MONTHLY.length > 0 ? MONTHLY[0].key                 : '';
  const last  = MONTHLY.length > 0 ? MONTHLY[MONTHLY.length-1].key : '';
  document.getElementById('pd_de').value  = first;
  document.getElementById('pd_ate').value = last;
  setPeriodo();
}})();

lucide.createIcons();

function filtrarCftv() {{
  const op     = document.getElementById('cftv_op')?.value     || '';
  const status = document.getElementById('cftv_status')?.value || '';
  const prio   = document.getElementById('cftv_prio')?.value   || '';
  const search = (document.getElementById('cftv_search')?.value || '').toLowerCase();
  document.querySelectorAll('.cftv-row').forEach(tr => {{
    const ok = (!op     || tr.dataset.operacao === op)
            && (!status || tr.dataset.status   === status)
            && (!prio   || tr.dataset.prio     === prio)
            && (!search || (tr.dataset.search || '').includes(search));
    tr.style.display = ok ? '' : 'none';
  }});
}}
</script>

<!-- ABA BLOQUEIOS -->
<div id="tab-bloqueios" class="content">
  <div class="cards">
    <div class="card">
      <div class="card-header"><i data-lucide="list" class="ci" width="14" height="14"></i><span class="cl">Total Solicitações</span></div>
      <div class="cv" id="bl-cv-total">{d["bl"]["total"]}</div><div class="cd">2026</div>
    </div>
    <div class="card card-ok">
      <div class="card-header"><i data-lucide="shield-check" class="ci" width="14" height="14" style="color:#064e3b"></i><span class="cl">Bloqueados</span></div>
      <div class="cv val-ok" id="bl-cv-bloqueados">{d["bl"]["bloqueados"]}</div><div class="cd">Confirmados</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="clock" class="ci" width="14" height="14"></i><span class="cl">Solicitados</span></div>
      <div class="cv" style="color:#60a5fa" id="bl-cv-solicitados">{d["bl"]["solicitados"]}</div><div class="cd">Aguardando</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="eye" class="ci" width="14" height="14"></i><span class="cl">Monitorados</span></div>
      <div class="cv val-warn" id="bl-cv-monitorados">{d["bl"]["monitorados"]}</div><div class="cd">Em acompanhamento</div>
    </div>
    <div class="card card-ok">
      <div class="card-header"><i data-lucide="dollar-sign" class="ci" width="14" height="14" style="color:#064e3b"></i><span class="cl">GMV Protegido</span></div>
      <div class="cv val-ok" id="bl-cv-gmv">${d["bl"]["gmv_protegido"]:,.2f}</div><div class="cd">Bloqueados confirmados</div>
    </div>
  </div>

  <div class="grid2 mb16">
    <div class="box"><div class="bt">Por Status</div><canvas id="cBlStatus" height="220"></canvas></div>
    <div class="box"><div class="bt">Por Transportadora</div><canvas id="cBlTransp" height="220"></canvas></div>
  </div>

  <div class="tbl-wrap">
    <div class="tbl-title">Lista Completa — Block List 2026</div>
    <div class="filter-bar">
      <input id="bl_search" type="text" oninput="filtrarBloqueios()" class="filter-select" placeholder="🔍 Driver ID ou Nome..." style="width:180px">
      <select id="bl_status" onchange="filtrarBloqueios()" class="filter-select">
        <option value="">Todos os status</option>
        <option value="Bloqueado">Bloqueado</option>
        <option value="Solicitado">Solicitado</option>
        <option value="Monitorado">Monitorado</option>
      </select>
      <select id="bl_transp" onchange="filtrarBloqueios()" class="filter-select">
        <option value="">Todas as transportadoras</option>
        {''.join(f'<option value="{t}">{t}</option>' for t in sorted(t for t in set(r["mlp"] for r in d["bl"]["rows"]) if t and t not in ("N/A","")))}
      </select>
      <button onclick="document.getElementById('bl_status').value='';document.getElementById('bl_transp').value='';document.getElementById('bl_search').value='';filtrarBloqueios()" style="background:#1f2937;color:#6b7280;border:1px solid #374151;border-radius:6px;padding:7px 12px;font-size:11px;cursor:pointer">Limpar</button>
      <button onclick="exportBlCSV()" style="background:#1e3a5f;color:#60a5fa;border:1px solid #1e40af;border-radius:6px;padding:7px 12px;font-size:11px;cursor:pointer;margin-left:auto">⬇ Exportar CSV</button>
    </div>
    <div class="tbl-scroll"><table id="bl-table">
      <thead><tr>
        <th onclick="sortBl('did')" style="cursor:pointer">Driver ID <span id="bl-sort-did"></span></th>
        <th>Nome</th><th>Transportadora</th><th>Placa</th><th>SHP</th>
        <th onclick="sortBl('usd')" style="cursor:pointer">USD$ <span id="bl-sort-usd"></span></th>
        <th onclick="sortBl('status')" style="cursor:pointer">Status <span id="bl-sort-status"></span></th>
        <th>Motivo</th>
        <th onclick="sortBl('data')" style="cursor:pointer">Data <span id="bl-sort-data"></span></th>
        <th>Semana</th>
      </tr></thead>
      <tbody id="bl-tbody">{rows_block_list(d["bl"]["rows"])}</tbody>
    </table></div>
  </div>
</div>

<!-- ===== ABA BSD (Buyer Seller Driver) ===== -->
<div id="tab-cruzamento" class="content">
  <div class="cards-grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card">
      <div class="card-header"><i data-lucide="store" class="ci" width="14" height="14"></i><span class="cl">Sellers Ofensores</span></div>
      <div class="cv" style="color:#f59e0b">{d["crz"]["total_sellers"]}</div><div class="cd">Vendedores c/ ≥2 fraudes</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="user" class="ci" width="14" height="14"></i><span class="cl">Buyers Ofensores</span></div>
      <div class="cv" style="color:#60a5fa">{d["crz"]["total_buyers"]}</div><div class="cd">Compradores c/ ≥2 fraudes</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="git-merge" class="ci" width="14" height="14"></i><span class="cl">Pares Seller×Buyer</span></div>
      <div class="cv">{d["crz"]["total_pares"]}</div><div class="cd">Combinações suspeitas</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="truck" class="ci" width="14" height="14"></i><span class="cl">Drivers Conectados</span></div>
      <div class="cv" style="color:#4ade80">{d["crz"]["total_drivers"]}</div><div class="cd">Motoristas envolvidos</div>
    </div>
  </div>

  <div class="grid2 mb16">
    <div class="tbl-wrap">
      <div class="tbl-title" style="color:#f59e0b">Top Sellers Ofensores</div>
      <div class="tbl-scroll"><table>
        <thead><tr><th>Seller ID</th><th>Fraudes</th><th>Buyers</th><th>Drivers</th></tr></thead>
        <tbody>
          {''.join(f"""<tr style="background:{'#1a100a' if i==0 else ''}">
            <td style="font-family:monospace;font-size:12px;color:#f59e0b">{s["seller_id"]}</td>
            <td style="font-weight:700;color:#ef4444;text-align:center">{s["qtd"]}</td>
            <td style="text-align:center;color:#9ca3af">{s["buyers"]}</td>
            <td style="text-align:center;color:#4ade80">{s["drivers"]}</td>
          </tr>""" for i,s in enumerate(d["crz"]["sellers"][:30]))}
        </tbody>
      </table></div>
    </div>
    <div class="tbl-wrap">
      <div class="tbl-title" style="color:#60a5fa">Top Buyers Ofensores</div>
      <div class="tbl-scroll"><table>
        <thead><tr><th>Buyer ID</th><th>Fraudes</th><th>Sellers</th><th>Drivers</th></tr></thead>
        <tbody>
          {''.join(f"""<tr style="background:{'#0a0f1a' if i==0 else ''}">
            <td style="font-family:monospace;font-size:12px;color:#60a5fa">{b["buyer_id"]}</td>
            <td style="font-weight:700;color:#ef4444;text-align:center">{b["qtd"]}</td>
            <td style="text-align:center;color:#9ca3af">{b["sellers"]}</td>
            <td style="text-align:center;color:#4ade80">{b["drivers"]}</td>
          </tr>""" for i,b in enumerate(d["crz"]["buyers"][:30]))}
        </tbody>
      </table></div>
    </div>
  </div>

  <div class="tbl-wrap">
    <div class="tbl-title">BSD — Buyer × Seller × Driver × Pacotes</div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th>#</th><th>Seller ID</th><th>Buyer ID</th><th>Fraudes</th><th>Drivers</th><th>Pacotes (SHP IDs)</th>
      </tr></thead>
      <tbody>
        {''.join(f"""<tr>
          <td style="color:#4b5563;font-size:11px;text-align:center">{i+1}</td>
          <td style="font-family:monospace;font-size:12px;color:#f59e0b">{p["seller_id"]}</td>
          <td style="font-family:monospace;font-size:12px;color:#60a5fa">{p["buyer_id"]}</td>
          <td style="font-weight:700;color:#ef4444;text-align:center">{p["qtd"]}</td>
          <td style="font-size:11px;color:#9ca3af">{p["drivers"]}</td>
          <td style="font-size:11px">{' '.join(f'<a href="{MELI_URL}/{s}" target="_blank" style="color:#60a5fa;text-decoration:none;font-family:monospace;margin-right:4px">{s}</a>' for s in p["shp_ids"])}</td>
        </tr>""" for i,p in enumerate(d["crz"]["pares"]))}
      </tbody>
    </table></div>
  </div>
</div>

<!-- ===== ABA CFTV ===== -->
<div id="tab-cftv" class="content">
  <div class="cards-grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card">
      <div class="card-header"><i data-lucide="camera" class="ci" width="14" height="14"></i><span class="cl">Solicitações</span></div>
      <div class="cv">{d["cftv"]["total"]}</div><div class="cd">Total de solicitações</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="check-circle" class="ci" width="14" height="14"></i><span class="cl">Concluídos</span></div>
      <div class="cv green">{d["cftv"]["concluidos"]}</div><div class="cd">Investigações encerradas</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="clock" class="ci" width="14" height="14"></i><span class="cl">Em Andamento</span></div>
      <div class="cv" style="color:#3b82f6">{d["cftv"]["em_andamento"]}</div><div class="cd">Aguardando conclusão</div>
    </div>
    <div class="card c-red">
      <div class="card-header"><i data-lucide="alert-triangle" class="ci" width="14" height="14"></i><span class="cl">SLA Vencido</span></div>
      <div class="cv red">{d["cftv"]["sla_vencido"]}</div><div class="cd">Prazo expirado</div>
    </div>
  </div>

  <div class="tbl-wrap">
    <div class="filter-bar">
      <span class="filter-label">Operação</span>
      <select id="cftv_op" class="filter-select" onchange="filtrarCftv()">
        <option value="">Todas</option>
        <option value="SSP30">SSP30</option>
        <option value="XSP10">XSP10</option>
      </select>
      <span class="filter-label">Status</span>
      <select id="cftv_status" class="filter-select" onchange="filtrarCftv()">
        <option value="">Todos</option>
        <option value="Concluído">Concluído</option>
        <option value="Em Andamento">Em Andamento</option>
        <option value="SLA Vencido">SLA Vencido</option>
      </select>
      <span class="filter-label">Prioridade</span>
      <select id="cftv_prio" class="filter-select" onchange="filtrarCftv()">
        <option value="">Todas</option>
        <option value="Alto">Alto</option>
        <option value="Moderado">Moderado</option>
      </select>
      <input id="cftv_search" type="text" oninput="filtrarCftv()" class="filter-select" placeholder="🔍 SHP / Driver / Solicitante..." style="width:220px">
    </div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th>Data</th><th>Wk</th><th>Op</th><th>Shipment</th><th>Produto</th>
        <th style="text-align:right">Valor R$</th><th>Prioridade</th><th>Status</th>
        <th>SLA</th><th>Responsável</th><th>Conclusão</th><th>Driver</th>
      </tr></thead>
      <tbody id="cftv-tbody">{rows_cftv(d["cftv"]["rows"])}</tbody>
    </table></div>
  </div>
</div>

</main>
</div>

</body>
</html>'''

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("="*55)
    print(f"Análise de Fraude SSP30 — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("="*55)

    bq, gs = conectar()

    print("\nConsultando BigQuery...")
    df_score     = buscar(bq, QUERY_DRIVER_SCORE,    'Score por Driver')
    df_shp       = buscar(bq, QUERY_DRIVER_SHIPMENTS,'SHP IDs por Driver')
    df_status    = buscar(bq, QUERY_DRIVER_STATUS,   'Status dos Drivers')
    df_routes    = buscar(bq, QUERY_DRIVER_ROUTES,   'Rotas dos Drivers')
    df_dxp       = buscar(bq, QUERY_DRIVER_PLACE,    'Driver x Place')
    df_places    = buscar(bq, QUERY_PLACES,           'Places')
    df_place_shp = buscar(bq, QUERY_PLACE_SHIPMENTS, 'SHP IDs por Place')
    df_damaged      = buscar(bq, QUERY_DAMAGED,      'Damaged por Driver')
    df_cruzamento   = buscar(bq, QUERY_CRUZAMENTO,   'Sellers/Buyers Ofensores')

    bl_rows   = carregar_block_list(gs)
    cftv_rows = carregar_cftv(gs)
    sincronizar_status_block_list(gs, bq, bl_rows)

    print("\nProcessando...")
    dados = processar(df_score, df_dxp, df_places, df_damaged, df_shp, df_place_shp, df_status, df_routes)
    dados['bl']   = processar_block_list(bl_rows)
    dados['crz']  = processar_cruzamento(df_cruzamento)
    dados['cftv'] = processar_cftv(cftv_rows)

    MONTHS_PT = {1:'Jan',2:'Fev',3:'Mar',4:'Abr',5:'Mai',6:'Jun',
                 7:'Jul',8:'Ago',9:'Set',10:'Out',11:'Nov',12:'Dez'}
    monthly = []
    for ym in sorted(dados.get('monthly_agg', {})):
        agg = dados['monthly_agg'][ym]
        mo, yr = int(ym[5:7]), int(ym[:4])
        monthly.append({
            'key':     ym,
            'label':   f"{MONTHS_PT[mo]}/{yr}",
            'fraudes': agg['fraudes'],
            'damaged': agg['damaged'],
            'bpp':     round(agg['bpp'], 2),
            'total':   agg['total'],
        })
    dados['monthly'] = monthly

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
