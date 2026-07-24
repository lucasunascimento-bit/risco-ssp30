"""
Script único para corrigir o Histórico agora.
Roda corrigir_historico() + preenche responsável vazio nos Concluídos.
Pode deletar depois.
"""
from google.auth import default
from google.cloud import bigquery
import gspread
from atualizacao_risco import verificar_entrega, ABA_HISTORICO, PLANILHA_CONTROLE_ID

scopes = [
    'https://www.googleapis.com/auth/bigquery',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/cloud-platform',
]
creds, _ = default(scopes=scopes)
bq_client  = bigquery.Client(credentials=creds, project='meli-bi-data')
gs_client  = gspread.authorize(creds)

planilha   = gs_client.open_by_key(PLANILHA_CONTROLE_ID)
aba_hist   = planilha.worksheet(ABA_HISTORICO)
dados      = aba_hist.get_all_values()

print(f"Total de linhas no Histórico: {len(dados)-1}")

# Coleta linhas para corrigir
ids_para_verificar = {}   # {shp_id: {'linha': i, 'resp': r, 'status': s, 'final': f}}
for i, row in enumerate(dados[1:], start=2):
    shp_id = row[2] if len(row) > 2 else ''
    resp   = row[5] if len(row) > 5 else ''
    status = row[6] if len(row) > 6 else ''
    final  = row[7] if len(row) > 7 else ''
    # sem status E sem finalização → precisa verificar no BQ
    # OU tem status mas não tem responsável
    if shp_id and (not status.strip() or not resp.strip()):
        ids_para_verificar[shp_id] = {
            'linha': i, 'resp': resp,
            'status': status, 'final': final
        }

print(f"Linhas para verificar: {len(ids_para_verificar)}")

if not ids_para_verificar:
    print("Nada para corrigir!")
    exit()

# Consulta BigQuery
print("Consultando BigQuery...")
status_bq = verificar_entrega(bq_client, list(ids_para_verificar.keys()))

updates    = []
corrigidos = 0

for shp_id, info in ids_para_verificar.items():
    linha  = info['linha']
    resp   = info['resp']
    status = info['status']
    final  = info['final']
    sub    = status_bq.get(shp_id, '').lower()

    foi_entregue = sub.startswith('delivered') or sub == 'sorting'
    foi_perdido  = sub == 'lost'

    # preenche status e finalização se estiverem vazios
    if not status.strip() and not final.strip():
        if foi_entregue or foi_perdido:
            updates += [
                {'range': f'G{linha}', 'values': [['Concluído']]},
                {'range': f'H{linha}', 'values': [['Seguiu fluxo correto' if foi_entregue else 'Perdido']]},
            ]
            corrigidos += 1

    # preenche responsável se vazio e tem status concluído
    status_atual = status.strip() if status.strip() else ('Concluído' if (foi_entregue or foi_perdido) else '')
    if not resp.strip() and 'conclu' in status_atual.lower():
        updates.append({'range': f'F{linha}', 'values': [['Lucas Nascimento']]})

if updates:
    print(f"Aplicando {len(updates)} atualizações...")
    # batch em grupos de 500
    for i in range(0, len(updates), 500):
        aba_hist.batch_update(updates[i:i+500])
    print(f"✓ {corrigidos} registros com status corrigido")
    print(f"✓ Responsável preenchido onde estava vazio")
else:
    print("Nada para corrigir no Histórico")

print("\nConcluído! Agora rode o dashboard para atualizar o site.")
