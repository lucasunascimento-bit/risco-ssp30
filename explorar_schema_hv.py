# ============================================================
# explorar_schema_hv.py
#
# Valida acesso às tabelas do TOOLKIT e confirma volume HV SSP30.
# Rodar antes da reunião: python explorar_schema_hv.py
# ============================================================

from google.cloud import bigquery
from google.auth import default

scopes = [
    'https://www.googleapis.com/auth/bigquery',
    'https://www.googleapis.com/auth/cloud-platform',
]
credentials, _ = default(scopes=scopes)
client = bigquery.Client(credentials=credentials, project='meli-bi-data')

FACILITY = 'SSP30'

# 1. Acesso às tabelas do TOOLKIT
print("=" * 60)
print("1. Validando acesso às tabelas do TOOLKIT")
print("=" * 60)
tabelas = [
    'meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS',
    'meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS',
    'meli-bi-data.WHOWNER.BT_PROBLEM_SOLVING_INCIDENT',
]
for t in tabelas:
    try:
        client.query(f"SELECT COUNT(*) FROM `{t}` WHERE FALSE").result()
        print(f"  OK  {t.split('.')[-1]}")
    except Exception as e:
        print(f"  ERRO  {t.split('.')[-1]} -- {e}")

# 2. Volume de pacotes com sub_status = 'sorting' no SSP30 hoje
print("\n" + "=" * 60)
print("2. Pacotes com sub_status='sorting' em SSP30 agora")
print("=" * 60)
q2 = f"""
SELECT
  COUNT(*) AS total,
  COUNTIF(S.SHP_ORDER_COST_USD >= 350) AS hv_350,
  ROUND(SUM(CASE WHEN S.SHP_ORDER_COST_USD >= 350 THEN S.SHP_ORDER_COST_USD ELSE 0 END), 2) AS gmv_hv_usd
FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` AS S
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS` AS L
  ON S.SHP_SHIPMENT_ID = L.SHP_SHIPMENT_ID
WHERE S.SIT_SITE_ID = 'MLB'
  AND L.SHP_LG_FACILITY_ID = '{FACILITY}'
  AND L.SHP_LG_SUB_STATUS = 'sorting'
"""
try:
    for row in client.query(q2).result():
        print(f"  Total em sorting: {row.total}")
        print(f"  HV >= USD 350   : {row.hv_350}")
        print(f"  GMV HV          : USD {row.gmv_hv_usd:,.2f}")
except Exception as e:
    print(f"  Erro: {e}")

# 3. Amostra de pacotes HV >= USD 350 em sorting
print("\n" + "=" * 60)
print("3. Amostra HV >= USD 350 em sorting (sem filtro de ciclo)")
print("=" * 60)
q3 = f"""
SELECT
  CAST(S.SHP_SHIPMENT_ID AS STRING) AS SHP_ID,
  ROUND(S.SHP_ORDER_COST_USD, 2) AS VALOR_USD,
  ANY_VALUE(ITE.SHP_ITEM_DESC) AS DESCRICAO_ITEM,
  L.SHP_LG_SUB_STATUS,
  CAST(L.SHP_LG_LAST_UPDATED AS STRING) AS LAST_UPDATED,
  DATETIME_DIFF(CURRENT_DATETIME(), DATETIME(L.SHP_LG_LAST_UPDATED), MINUTE) - 240 AS MINUTOS_NA_FILA
FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` AS S
LEFT JOIN UNNEST(S.ITEMS) AS ITE
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS` AS L
  ON S.SHP_SHIPMENT_ID = L.SHP_SHIPMENT_ID
WHERE S.SIT_SITE_ID = 'MLB'
  AND L.SHP_LG_FACILITY_ID = '{FACILITY}'
  AND S.SHP_ORDER_COST_USD >= 350
  AND L.SHP_LG_SUB_STATUS = 'sorting'
GROUP BY 1, 2, 4, 5, 6
ORDER BY VALOR_USD DESC
LIMIT 5
"""
try:
    rows = list(client.query(q3).result())
    if rows:
        for row in rows:
            print(f"\n  SHP: {row.SHP_ID}  |  USD {row.VALOR_USD}")
            print(f"  Item: {row.DESCRICAO_ITEM}")
            print(f"  Minutos na fila: {row.MINUTOS_NA_FILA}")
    else:
        print("  Nenhum pacote em sorting agora (fora do ciclo ou não há HV).")
        print("  → Normal se fora do horário de sorting do SSP30.")
except Exception as e:
    print(f"  Erro: {e}")

# 4. Descobrir horário real do ciclo de sorting SSP30
print("\n" + "=" * 60)
print("4. Distribuição de SHP_LG_LAST_UPDATED (horário do sorting)")
print("=" * 60)
q4 = f"""
SELECT
  EXTRACT(HOUR FROM DATETIME(L.SHP_LG_LAST_UPDATED, 'America/Sao_Paulo')) AS hora_brt,
  COUNT(*) AS qtd,
  COUNTIF(S.SHP_ORDER_COST_USD >= 350) AS hv_350
FROM `meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS` AS L
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` AS S
  ON S.SHP_SHIPMENT_ID = L.SHP_SHIPMENT_ID
WHERE L.SHP_LG_FACILITY_ID = '{FACILITY}'
  AND L.SHP_LG_SUB_STATUS = 'sorting'
  AND DATE(L.SHP_LG_LAST_UPDATED) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
GROUP BY 1
ORDER BY 1
"""
try:
    print("  hora_brt  | qtd  | hv_350")
    for row in client.query(q4).result():
        bar = '█' * min(int(row.qtd / 20), 30)
        print(f"  {row.hora_brt:02d}h       | {row.qtd:4d} | {row.hv_350:3d}  {bar}")
    print("\n  → Use as horas de pico para definir CICLO_INICIO e CICLO_FIM em alarma_hv.py")
except Exception as e:
    print(f"  Erro: {e}")

print("\n" + "=" * 60)
print("PRONTO — use os resultados acima na reunião.")
print("=" * 60)
