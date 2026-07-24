import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from google.cloud import bigquery
from datetime import datetime, timedelta

client = bigquery.Client(project='meli-bi-data')
ANO_INICIO = '2026-01-01'
cutoff = datetime.now() - timedelta(days=90)

# 1. Buscar status dos drivers
STATUS_QUERY = f"""
WITH status_raw AS (
  SELECT DISTINCT
    CAST(r.DRIVER_ID AS STRING) AS DRIVER_ID,
    s.SHP_CROWD_STATUS  AS STATUS,
    s.SHP_CROWD_SUBSTATUS AS SUBSTATUS
  FROM `meli-bi-data.WHOWNER.BT_SHP_CROWD_TRACKER_REGIST` AS r
  LEFT JOIN `meli-bi-data.WHOWNER.BT_SHP_CROWD_DRIVER_REG_STATUS` AS ds
    ON r.DRIVER_ID = ds.SHP_CROWD_DRIVER_ID
  LEFT JOIN `meli-bi-data.WHOWNER.BT_SHP_CROWD_REG_STATUS` AS s
    ON ds.SHP_CROWD_STATUS_ID = s.SHP_CROWD_ID
  WHERE r.SITE = 'MLB'
),
status_priority AS (
  SELECT DISTINCT DRIVER_ID,
    FIRST_VALUE(STATUS) OVER (
      PARTITION BY DRIVER_ID
      ORDER BY CASE STATUS WHEN 'blocked' THEN 1 WHEN 'inactive' THEN 2 WHEN 'removed' THEN 3 ELSE 4 END
    ) AS STATUS
  FROM status_raw
),
fraud_drivers AS (
  SELECT DISTINCT SAFE_CAST(DRIVER_ID AS STRING) AS DRIVER_ID
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
    AND date_bpp >= '{ANO_INICIO}'
    AND DRIVER_ID IS NOT NULL
)
SELECT sp.DRIVER_ID, sp.STATUS
FROM status_priority sp
INNER JOIN fraud_drivers fd ON sp.DRIVER_ID = fd.DRIVER_ID
"""

# 2. Buscar SHPs por driver
SHP_QUERY = f"""
SELECT
  SAFE_CAST(DRIVER_ID AS STRING)  AS DRIVER_ID,
  CAST(SHIPMENT_ID AS STRING)     AS SHP_ID,
  Classification_LM               AS CLASSE,
  ROUND(BPP_CASHOUT_USD, 2)       AS BPP,
  FORMAT_DATE('%d/%m/%Y', date_bpp) AS DATA
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND date_bpp >= '{ANO_INICIO}'
  AND DRIVER_ID IS NOT NULL
  AND BPP_CASHOUT_USD > 0
"""

print("Buscando status dos drivers...")
df_status = client.query(STATUS_QUERY).to_dataframe()
status_map = {r['DRIVER_ID']: r['STATUS'] for _, r in df_status.iterrows()}

print("Buscando SHPs...")
df_shp = client.query(SHP_QUERY).to_dataframe()

# Montar shp_por_driver
from collections import defaultdict
shp_por_driver = defaultdict(list)
for _, r in df_shp.iterrows():
    did = str(r['DRIVER_ID']).strip()
    try:
        dt = datetime.strptime(str(r['DATA']), '%d/%m/%Y')
    except:
        continue
    shp_por_driver[did].append({
        'class': str(r['CLASSE'] or ''),
        'bpp':   float(r['BPP'] or 0),
        'data':  str(r['DATA']),
    })

# Simular processar_acumulo_bloqueio SEM o fix (status sempre '')
STATUS_NAO_BLOQ = {'inactive','inativo','bloqueado','blocked','suspendido','suspended'}
CLASSES_VALIDAS = ('FRAUD', 'LOST ON ROUTE')

sem_fix = []
com_fix = []

for did, shps_all in shp_por_driver.items():
    shps = [s for s in shps_all
            if float(s.get('bpp', 0) or 0) > 0
            and any(k in str(s.get('class','')) for k in CLASSES_VALIDAS)]
    if len(shps) < 1:
        continue
    meses = set()
    for s in shps:
        try:
            dr = datetime.strptime(s['data'], '%d/%m/%Y')
            if dr >= cutoff:
                meses.add(f'{dr.month:02d}/{dr.year}')
        except: pass
    if len(meses) < 3:
        continue
    # Passou os critérios básicos — driver seria candidato
    st_bq = status_map.get(did, 'active')
    sem_fix.append({'id': did, 'status': st_bq})
    if st_bq.lower() not in STATUS_NAO_BLOQ:
        com_fix.append({'id': did, 'status': st_bq})

print(f"\n{'='*55}")
print(f"Candidatos ANTES do fix (status ignorado): {len(sem_fix)}")
print(f"Candidatos DEPOIS do fix (blocked/inactive excluídos): {len(com_fix)}")
print(f"\nDrivers que VÃO SUMIR do Acúmulo após o fix:")
removidos = [d for d in sem_fix if d['id'] not in {x['id'] for x in com_fix}]
for d in removidos:
    print(f"  Driver {d['id']:12s}  status BQ: {d['status']}")
print(f"{'='*55}")
