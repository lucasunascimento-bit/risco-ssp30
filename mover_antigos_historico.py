# mover_antigos_historico.py
# Move os 10 casos ON ROUTE com 30+ dias parados para o Histórico.
# Executar: python mover_antigos_historico.py

import os, sys
from datetime import datetime
from google.auth import default
from google.oauth2 import service_account as _sa_module
import gspread

PLANILHA_CONTROLE_ID = '1rFcUXxl53WVQf_ASRx3mhlEvFoJevcaiwjMZY1vso5Y'
ABA_ON_ROUTE  = 'Tratativas Risco On Route (HV) - Lucas'
ABA_HISTORICO = 'Histórico'

# SHP IDs a mover + finalização baseada no sub-status BQ
CASOS = {
    '47069669118': 'Recuperado',       # 84d, claimed
    '47238868500': 'Recuperado',       # 65d, claimed → BRNSP354
    '47239930133': 'Recuperado',       # 65d, claimed → BRNSP140
    '47237982431': 'Perdido',          # 67d, stale, BPP=true
    '47244375952': 'Retornou ao fluxo',# 65d, stale
    '47443949948': 'Perdido',          # 30d, stale, BPP=true
    '47488480891': 'Recuperado',       # 31d, claimed
    '47375871869': 'Recuperado',       # 46d, claimed
    '47274929612': 'Retornou ao fluxo',# 33d, stale
    '47443810349': 'Retornou ao fluxo',# 35d, stale
}

def main():
    hoje = datetime.now().strftime('%d/%m/%Y')

    # Auth
    _sa_file = os.path.join(os.path.dirname(__file__), 'google_credentials.json')
    scopes = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive']
    if os.path.exists(_sa_file):
        creds = _sa_module.Credentials.from_service_account_file(_sa_file, scopes=scopes)
    else:
        creds, _ = default(scopes=scopes)

    gc = gspread.authorize(creds)
    pl = gc.open_by_key(PLANILHA_CONTROLE_ID)
    rt_ws   = pl.worksheet(ABA_ON_ROUTE)
    hist_ws = pl.worksheet(ABA_HISTORICO)

    # Lê ON ROUTE
    rows = rt_ws.get_all_values()
    if len(rows) <= 1:
        print("Planilha ON ROUTE vazia.")
        return

    header = rows[0]
    data   = [(i + 2, r) for i, r in enumerate(rows[1:]) if len(r) > 2 and r[2].strip()]

    encontrados = []
    for sheet_row, r in data:
        shp_id = r[2].strip()
        if shp_id in CASOS:
            encontrados.append((sheet_row, r, CASOS[shp_id]))

    if not encontrados:
        print("Nenhum dos SHP IDs encontrado na planilha. Verifique se já foram movidos.")
        return

    print(f"\n{len(encontrados)} caso(s) encontrado(s) para mover:\n")
    for row_num, r, final in encontrados:
        gmv  = r[22] if len(r) > 22 else ''
        sit  = r[1]  if len(r) > 1  else ''
        print(f"  Linha {row_num}: {r[2]} | {sit} | GMV: {gmv} | Finalização: {final}")

    confirm = input("\nConfirmar mover para Histórico? (s/n): ").strip().lower()
    if confirm != 's':
        print("Cancelado.")
        return

    # Monta linhas para Histórico
    novas_hist = []
    for _, r, final in encontrados:
        while len(r) < 35:
            r.append('')
        novas_hist.append([
            hoje,
            'ON ROUTE',
            r[2],   # SHP_ID
            r[1],   # Situation
            r[22],  # GMV
            r[0] or 'Lucas Nascimento',  # Responsável
            r[28],  # Status
            final,  # Finalização
        ])

    # Append ao Histórico
    hist_ws.append_rows(novas_hist, value_input_option='RAW')
    print(f"\n✓ {len(novas_hist)} linha(s) adicionada(s) ao Histórico.")

    # Deleta do ON ROUTE (ordem reversa para não deslocar índices)
    rows_to_delete = sorted({row_num for row_num, _, _ in encontrados}, reverse=True)
    for row_num in rows_to_delete:
        rt_ws.delete_rows(row_num)
        print(f"  Deletado linha {row_num} do ON ROUTE.")

    print(f"\n✅ Concluído. {len(encontrados)} caso(s) movido(s) para Histórico.")

if __name__ == '__main__':
    main()
