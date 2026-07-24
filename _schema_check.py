from google.cloud import bigquery
client = bigquery.Client(project='meli-bi-data')

# Checar FACILITY_ID que corresponde a SSP30/Guarulhos Mega
q = """
SELECT DISTINCT FACILITY_ID, COUNT(*) as cnt
FROM `meli-bi-data.WHOWNER.BT_SHP_CROWD_DASS_ASSIGNMENT`
WHERE DATE(CREATED_AT) >= '2026-01-01'
  AND CAST(DRIVER_ID AS STRING) IN (
    SELECT DISTINCT SAFE_CAST(DRIVER_ID AS STRING)
    FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
    WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
      AND date_bpp >= '2026-01-01'
      AND DRIVER_ID IS NOT NULL
  )
GROUP BY 1
ORDER BY cnt DESC
LIMIT 10
"""
print('=== FACILITY_IDs dos drivers SSP30 em DASS_ASSIGNMENT ===')
for row in client.query(q).result():
    print(f'  {row.FACILITY_ID!r:20s}  {row.cnt} registros')

# Teste: pegar placa mais recente de alguns drivers
q2 = """
SELECT
  CAST(DRIVER_ID AS STRING) AS DRIVER_ID,
  LICENCE_PLATE,
  MAX(DATE(CREATED_AT)) AS ultima_data
FROM `meli-bi-data.WHOWNER.BT_SHP_CROWD_DASS_ASSIGNMENT`
WHERE DATE(CREATED_AT) >= '2026-01-01'
  AND LICENCE_PLATE IS NOT NULL AND LICENCE_PLATE != ''
  AND CAST(DRIVER_ID AS STRING) IN (
    SELECT DISTINCT SAFE_CAST(DRIVER_ID AS STRING)
    FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
    WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
      AND date_bpp >= '2026-01-01'
      AND DRIVER_ID IS NOT NULL
  )
GROUP BY 1, 2
QUALIFY ROW_NUMBER() OVER (PARTITION BY DRIVER_ID ORDER BY MAX(DATE(CREATED_AT)) DESC) = 1
LIMIT 10
"""
print()
print('=== Amostra de placas por driver ===')
for row in client.query(q2).result():
    print(f'  Driver {row.DRIVER_ID:10s}  Placa: {row.LICENCE_PLATE}  Data: {row.ultima_data}')
