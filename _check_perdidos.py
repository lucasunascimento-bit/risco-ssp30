from google.auth import default
from google.cloud import bigquery

creds, _ = default(scopes=['https://www.googleapis.com/auth/bigquery.readonly'])
client = bigquery.Client(project='meli-bi-data', credentials=creds)

ids = [
    '47186145045','47190258123','47161278266','47153427889','47136323446',
    '47172321233','47208465508','47128315746','47167535756','47161029115',
    '47104339370','47163119823','47168174514'
]
ids_str = ','.join(ids)

# 1. Status final via BT_SHP_SHIPMENTS (colunas confirmadas)
print("=== STATUS FINAL (BT_SHP_SHIPMENTS) ===")
q1 = f"""
SELECT
  CAST(SHP_SHIPMENT_ID AS STRING) AS shp_id,
  SHP_STATUS_ID                   AS status,
  SHP_SUBSTATUS_ID                AS substatus
FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS`
WHERE SHP_SHIPMENT_ID IN ({ids_str})
LIMIT 100
"""
try:
    rows1 = list(client.query(q1).result())
    for r in rows1:
        print(f"  {r['shp_id']} | {r['status']} / {r['substatus']}")
    nao_shp = [i for i in ids if i not in {r['shp_id'] for r in rows1}]
    if nao_shp:
        print(f"  NAO ENCONTRADOS: {nao_shp}")
except Exception as e:
    print(f"  ERRO: {e}")

# 2. Passagem por DC/Nex via BT_SHP_TRACKER_DELAY_CAUSE_DIT (sem filtro de status)
print("\n=== PASSAGEM POR DC/NEX/XPT (BT_SHP_TRACKER_DELAY_CAUSE_DIT) ===")
q2 = f"""
SELECT
  CAST(SHP_SHIPMENT_ID AS STRING)    AS shp_id,
  SHP_DESTINATION_FACILITY_ID        AS place_id,
  LM_DESTINATION_FACILITY_TYPE       AS tipo,
  LT_DELAY_CAUSE_L2                  AS causa,
  SHP_LG_SUB_STATUS                  AS sub_status,
  SHP_DATE_HANDLING_ID               AS data
FROM `meli-bi-data.WHOWNER.BT_SHP_TRACKER_DELAY_CAUSE_DIT`
WHERE SHP_SITE_ID = 'MLB'
  AND SHP_DATE_HANDLING_ID >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
  AND SHP_SHIPMENT_ID IN ({ids_str})
ORDER BY shp_id, data DESC
LIMIT 200
"""
try:
    rows2 = list(client.query(q2).result())
    if not rows2:
        print("  Nenhum registro encontrado em DIT (nem historico de DC/Nex nos ultimos 365 dias)")
    else:
        for r in rows2:
            flag = ' *** DC/NEX ***' if r['tipo'] in ('NEX', 'DC', 'XPT') else ''
            print(f"  {r['shp_id']} | place={r['place_id']} | tipo={r['tipo']} | causa={r['causa']} | sub_status={r['sub_status']} | data={r['data']}{flag}")
except Exception as e:
    print(f"  ERRO: {e}")

# 3. Tentar via LK_SHP_MISSING_MANAGEMENT_PACKAGES sem filtro de facility
print("\n=== HISTORICO MISSING (sem filtro SSP30) ===")
q3 = f"""
SELECT
  CAST(SHP_SHIPMENT_ID AS STRING) AS shp_id,
  SHP_TRAMO                       AS tramo,
  SHP_LG_FACILITY_ID              AS facility,
  SHP_DESTINATION_ID_LM           AS destination,
  ACTION_DETAIL                   AS action
FROM `meli-bi-data.WHOWNER.LK_SHP_MISSING_MANAGEMENT_PACKAGES`
WHERE SHP_SHIPMENT_ID IN ({ids_str})
LIMIT 100
"""
try:
    rows3 = list(client.query(q3).result())
    if not rows3:
        print("  Nenhum registro (ja removidos da tabela de ativos)")
    else:
        for r in rows3:
            flag = ' *** DC/NEX ***' if r['tramo'] in ('NEX', 'DC') else ''
            print(f"  {r['shp_id']} | tramo={r['tramo']} | facility={r['facility']} | dest={r['destination']} | action={r['action']}{flag}")
except Exception as e:
    print(f"  ERRO: {e}")
