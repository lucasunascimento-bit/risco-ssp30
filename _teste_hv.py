from google.cloud import bigquery
from google.auth import default
import json, urllib.request

scopes = [
    'https://www.googleapis.com/auth/bigquery',
    'https://www.googleapis.com/auth/cloud-platform',
]
credentials, _ = default(scopes=scopes)
client = bigquery.Client(credentials=credentials, project='meli-bi-data')

q = """
WITH DistinctShipments AS (
  SELECT
    L.SHP_LG_FACILITY_ID,
    S.SHP_SHIPMENT_ID AS ID_ENVIO,
    S.SHP_ORDER_COST_USD AS VALOR_USD,
    ANY_VALUE(ITE.SHP_ITEM_DESC) AS DESCRICAO_ITEM,
    L.SHP_LG_LAST_UPDATED
  FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` AS S
  LEFT JOIN UNNEST(S.ITEMS) AS ITE
  INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS` AS L
    ON S.SHP_SHIPMENT_ID = L.SHP_SHIPMENT_ID
  WHERE S.SIT_SITE_ID = 'MLB'
    AND L.SHP_LG_FACILITY_ID = 'SSP30'
    AND S.SHP_ORDER_COST_USD >= 350
    AND L.SHP_LG_SUB_STATUS = 'sorting'
  GROUP BY 1, 2, 3, 5
)
SELECT CAST(ID_ENVIO AS STRING) AS SHP_ID, ROUND(VALOR_USD,2) AS GMV_USD,
       COALESCE(DESCRICAO_ITEM,'') AS ITEM,
       CAST(SHP_LG_LAST_UPDATED AS STRING) AS LAST_UPDATED
FROM DistinctShipments
ORDER BY VALOR_USD DESC
LIMIT 3
"""

print("Buscando pacotes HV em sorting SSP30...")
df = client.query(q).to_dataframe()
print(f"Pacotes encontrados: {len(df)}")
for _, r in df.iterrows():
    print(f"  {r.SHP_ID} | USD {r.GMV_USD} | {str(r.ITEM)[:60]}")

WEBHOOK = 'https://chat.googleapis.com/v1/spaces/AAQAJzYdVzU/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=vyL1G__Uu8Il-17-5Cz6DICamMChHaiZvOAIzOXFwgE'

if not df.empty:
    gmv = df['GMV_USD'].sum()
    linhas = []
    for _, r in df.iterrows():
        linhas.append(f"ID: {r.SHP_ID} | USD {r.GMV_USD:,.0f}\n   {str(r.ITEM)[:60]}")
    msg = (
        f"*TESTE — ALARMA HV SSP30 (>= USD 350)*\n\n"
        f"Qtde: *{len(df)}* | GMV: *USD {gmv:,.0f}*\n\n"
        + "\n\n".join(linhas)
    )
else:
    msg = "TESTE Alarma HV SSP30 - BQ OK, sem pacotes em sorting agora (fora do ciclo 11h-13h)."

payload = json.dumps({'text': msg}).encode('utf-8')
req = urllib.request.Request(WEBHOOK, data=payload, headers={'Content-Type': 'application/json'})
urllib.request.urlopen(req)
print("Mensagem enviada ao Chat Alerta HV_SSP30!")
