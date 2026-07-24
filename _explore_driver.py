from google.auth import default
from google.cloud import bigquery
creds, _ = default(scopes=['https://www.googleapis.com/auth/bigquery.readonly'])
client = bigquery.Client(project='meli-bi-data', credentials=creds)

shp_ids = [47176942580,47257737775,47238868500,47166311471,47239930133]

# 1. LK_SHP_ROUTE_SHIPMENT schema
print('=== LK_SHP_ROUTE_SHIPMENT — schema ===')
try:
    table = client.get_table('meli-bi-data.WHOWNER.LK_SHP_ROUTE_SHIPMENT')
    for f in table.schema:
        print(f'  {f.name} ({f.field_type})')
except Exception as e:
    print(f'  ERRO schema: {e}')

# 2. Amostrar LK_SHP_ROUTE_SHIPMENT para nossos IDs
print('\n=== LK_SHP_ROUTE_SHIPMENT — dados para SHP IDs ===')
q = """
SELECT *
FROM `meli-bi-data.WHOWNER.LK_SHP_ROUTE_SHIPMENT`
WHERE SHP_SHIPMENT_ID IN (47176942580,47257737775,47238868500,47166311471,47239930133)
LIMIT 30
"""
try:
    rows = list(client.query(q).result())
    if not rows:
        print('  Nenhum resultado')
    for r in rows:
        print(dict(r))
except Exception as e:
    print(f'  ERRO: {e}')

# 3. BT_SHP_ROUTE_SHIPMENT schema
print('\n=== BT_SHP_ROUTE_SHIPMENT — schema ===')
try:
    table2 = client.get_table('meli-bi-data.WHOWNER.BT_SHP_ROUTE_SHIPMENT')
    for f in table2.schema:
        print(f'  {f.name} ({f.field_type})')
except Exception as e:
    print(f'  ERRO schema: {e}')

# 4. Amostrar BT_SHP_ROUTE_SHIPMENT
print('\n=== BT_SHP_ROUTE_SHIPMENT — dados para SHP IDs ===')
q2 = """
SELECT *
FROM `meli-bi-data.WHOWNER.BT_SHP_ROUTE_SHIPMENT`
WHERE SHP_SHIPMENT_ID IN (47176942580,47257737775,47238868500,47166311471,47239930133)
LIMIT 30
"""
try:
    rows2 = list(client.query(q2).result())
    if not rows2:
        print('  Nenhum resultado')
    for r in rows2:
        print(dict(r))
except Exception as e:
    print(f'  ERRO: {e}')
