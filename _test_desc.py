from google.cloud import bigquery
from google.auth import default
import gspread

creds, _ = default()
gc = gspread.authorize(creds)
pl = gc.open_by_key('1rFcUXxl53WVQf_ASRx3mhlEvFoJevcaiwjMZY1vso5Y')
rows = pl.worksheet('Tratativas Risco On Way (HV) - Lucas').get_all_values()
ids = list({r[2].strip() for r in rows[1:] if len(r) > 2 and r[2].strip()})[:10]
print('IDs:', ids)

client = bigquery.Client(project='meli-bi-data', credentials=creds)
ids_int = ','.join(ids)  # INT64, sem aspas
q = f"""
SELECT
  CAST(SHP_SHIPMENT_ID AS STRING) AS shp_id,
  (SELECT SHP_ITEM_DESC FROM UNNEST(ITEMS) LIMIT 1) AS item_desc
FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS`
WHERE SHP_SHIPMENT_ID IN ({ids_int})
LIMIT 20
"""
print("Executando query...")
for r in client.query(q).result():
    print(r['shp_id'], '->', r['item_desc'])
