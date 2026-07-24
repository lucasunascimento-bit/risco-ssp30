"""
Script único para preencher a coluna AF com a data de ontem
nos pacotes que já estavam na planilha antes da automação.
Roda uma vez e pode ser deletado depois.
"""
from google.auth import default
import gspread
from datetime import datetime, timedelta

scopes = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/cloud-platform',
]
credentials, _ = default(scopes=scopes)
gs_client = gspread.authorize(credentials)

PLANILHA_CONTROLE_ID = '1rFcUXxl53WVQf_ASRx3mhlEvFoJevcaiwjMZY1vso5Y'
ABA_ON_ROUTE = 'Tratativas Risco On Route (HV) - Lucas'
ABA_ON_WAY   = 'Tratativas Risco On Way (HV) - Lucas'

data_ontem = (datetime.now() - timedelta(days=1)).strftime('%d/%m/%Y')
print(f"Data a preencher: {data_ontem}")

planilha = gs_client.open_by_key(PLANILHA_CONTROLE_ID)

for nome_aba in [ABA_ON_ROUTE, ABA_ON_WAY]:
    aba = planilha.worksheet(nome_aba)
    # expande a planilha para 32 colunas (até AF) se necessário
    planilha.batch_update({'requests': [{
        'updateSheetProperties': {
            'properties': {
                'sheetId': aba._properties['sheetId'],
                'gridProperties': {'columnCount': 32}
            },
            'fields': 'gridProperties.columnCount'
        }
    }]})

    col_c = aba.col_values(3)   # SHP_SHIPMENT_ID

    updates = []
    for i, shp_id in enumerate(col_c[1:], start=2):  # pula cabeçalho
        if not shp_id:
            continue
        updates.append({'range': f'AF{i}', 'values': [[data_ontem]]})

    if updates:
        aba.batch_update(updates)
        print(f"  '{nome_aba}': {len(updates)} linhas preenchidas com {data_ontem}")
    else:
        print(f"  '{nome_aba}': nenhuma linha para preencher")

print("\nConcluído!")
