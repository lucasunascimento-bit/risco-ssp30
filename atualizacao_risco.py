# ============================================================
# atualizacao_risco.py
#
# O QUE ESTE SCRIPT FAZ:
#   1. Consulta o BigQuery buscando pacotes em risco no SSP30
#   2. Compara com o que já está na sua planilha (evita duplicatas)
#   3. Adiciona automaticamente os pacotes novos
#   4. Corrige a coluna Situation de pacotes já existentes
#   5. Atualiza dados de CFTV (Sim/Não + responsável + status + link)
#
# COMO RODAR: python atualizacao_risco.py
# ============================================================

from google.cloud import bigquery
from google.auth import default
import gspread
from datetime import datetime
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

# Carrega .env local se existir (não usa python-dotenv para evitar dependência)
_env_file = Path(__file__).parent / '.env'
if _env_file.exists():
    for _line in _env_file.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S',
    stream=sys.stdout,
)
log = logging.getLogger('risco_ssp30')

# ============================================================
# CONFIGURAÇÕES
# ============================================================
FACILITY                  = 'SSP30'
GMV_MINIMO_USD            = 100   # mínimo para Possivel Lost (ON ROUTE e ON WAY) e >= 11 dias OW
GMV_MINIMO_PROCURAR_USD   = 350   # mínimo para Procurar Pacote (ON ROUTE)
GMV_MINIMO_OW_USD         = 500   # mínimo para pacotes < 11 dias OW (Procurar Pacote OW)

WEBHOOK_GCHAT        = os.environ.get('GCHAT_WEBHOOK', '')

PLANILHA_CONTROLE_ID = '1rFcUXxl53WVQf_ASRx3mhlEvFoJevcaiwjMZY1vso5Y'
PLANILHA_CFTV_ID     = '18isURInofILBi-RS9YrCQyYcnb6JeU_stNqnspxiqLM'

ABA_ON_ROUTE = 'Tratativas Risco On Route (HV) - Lucas'
ABA_ON_WAY   = 'Tratativas Risco On Way (HV) - Lucas'
ABA_CFTV      = 'Respostas ao formulário 2'
ABA_HISTORICO = 'Histórico'

MESES_PT = {1:'jan',2:'fev',3:'mar',4:'abr',5:'mai',6:'jun',
            7:'jul',8:'ago',9:'set',10:'out',11:'nov',12:'dez'}

ABA_SNAPSHOTS  = 'Snapshots'
GMV_ALERTA_USD = 1000  # limiar para alerta urgente de Possivel Lost

# ============================================================
# QUERY ON ROUTE
# Pacotes at_station / on_route, GMV >= $100, excluindo on_way
# Coluna K usa ULTIMO_SISTEMA_MOVIMENTACAO_SVC do BigQuery
# ============================================================
QUERY_ON_ROUTE = f"""
SELECT
  CAST(SHP_SHIPMENT_ID AS STRING)                                              AS SHP_SHIPMENT_ID,
  '{FACILITY}'                                                                 AS FACILITY,
  SHP_LG_STATUS,
  COALESCE(SHP_LG_SUB_STATUS, '')                                              AS SHP_LG_SUB_STATUS,
  COALESCE(CAST(SHP_LG_SHIPMENT_CHK_DT AS STRING), '')                        AS SHP_LG_SHIPMENT_CHK_DT,
  COALESCE(CAST(DAYS_EXPIRED_PROMISE AS STRING), '')                           AS DIAS_VENCIMENTO_PROMESSA,
  COALESCE(LAST_SYSTEM_HANDLING_SVC, '')                                       AS ULTIMO_SISTEMA,
  COALESCE(SHP_DESTINATION_ID_LM, '')                                          AS SHP_DESTINATION_ID,
  COALESCE(CAST(SHP_CANCEL_DT AS STRING), '')                                  AS DATA_CANCELAMENTO,
  COALESCE(CAST(LAST_HANDLING_SVC_DT AS STRING), '')                          AS DATA_ULTIMA_MOVIMENTACAO,
  COALESCE(SHP_STATUS_ID, '')                                                  AS SHP_STATUS_ID,
  COALESCE(SHP_SUBSTATUS_ID, '')                                               AS SHP_SUBSTATUS_ID,
  COALESCE(CAST(ATIVO_BUYER_DT AS STRING), '')                                 AS DATA_ATIVO_POC,
  COALESCE(CAST(DAYS_HANDLING_SVC AS STRING), '')                              AS DIAS_SEM_MOVIMENTACAO,
  COALESCE(SHP_FACILITY_ID_ORIGIN, '')                                         AS ORIGEN_FACILITY_ID,
  COALESCE(SHP_XD_FACILITY_ID_ON_WAY, '')                                     AS FACILITY_XD_OW,
  CAST(ROUND(SHP_ORDER_COST_USD, 2) AS STRING)                                 AS GMV_USD,
  CASE
    WHEN FLAG_BPP = true THEN 'Possivel Lost'
    ELSE 'Procurar Pacote'
  END                                                                          AS Situation
FROM `meli-bi-data.WHOWNER.LK_SHP_MISSING_MANAGEMENT_PACKAGES`
WHERE SHP_LG_FACILITY_ID = '{FACILITY}'
  AND SHP_LG_STATUS IN ('at_station', 'on_route')
  AND (DETAIL_SHIPMENT IS NULL OR DETAIL_SHIPMENT NOT LIKE '%ON WAY%')
  AND (
    (FLAG_BPP = true  AND SHP_ORDER_COST_USD >= {GMV_MINIMO_USD})
    OR (FLAG_BPP = false AND SHP_ORDER_COST_USD >= {GMV_MINIMO_PROCURAR_USD})
  )
ORDER BY SHP_ORDER_COST_USD DESC
"""

# ============================================================
# QUERY ON WAY
# Três categorias:
#   - Possivel Lost OW : FLAG_BPP = true, GMV >= $100
#   - >= 11 dias OW    : DETAIL_SHIPMENT = 'ON WAY >= 11 DIAS', GMV >= $100
#   - < 11 dias OW     : on_way sem DETAIL_SHIPMENT, GMV >= $500
# ============================================================
QUERY_ON_WAY = f"""
SELECT
  CAST(SHP_SHIPMENT_ID AS STRING)                                              AS SHP_SHIPMENT_ID,
  SHP_LG_STATUS,
  COALESCE(SHP_LG_SUB_STATUS, '')                                              AS SHP_LG_SUB_STATUS,
  COALESCE(CAST(SHP_LG_SHIPMENT_CHK_DT AS STRING), '')                        AS SHP_LG_SHIPMENT_CHK_DT,
  COALESCE(CAST(DAYS_EXPIRED_PROMISE AS STRING), '')                           AS DIAS_VENCIMENTO_PROMESSA,
  COALESCE(CAST(SHP_CANCEL_DT AS STRING), '')                                  AS DATA_CANCELAMENTO,
  CASE
    WHEN SHP_CANCEL_DT IS NOT NULL
    THEN CAST(DATE_DIFF(CURRENT_DATE(), DATE(SHP_CANCEL_DT), DAY) AS STRING)
    ELSE ''
  END                                                                          AS DIAS_CANCELAMENTO,
  COALESCE(SHP_STATUS_ID, '')                                                  AS SHP_STATUS_ID,
  COALESCE(SHP_SUBSTATUS_ID, '')                                               AS SHP_SUBSTATUS_ID,
  COALESCE(CAST(DAYS_ON_WAY AS STRING), '')                                    AS DIAS_ON_WAY,
  COALESCE(SHP_LG_CARRIER_NAME_LH, '')                                         AS CARRIER_NAME,
  COALESCE(CAST(SHP_LG_ROUTE_ID_LH AS STRING), '')                             AS ROUTE_ID,
  COALESCE(SHP_LG_VEHICLE_LICENSE_PLATE_LH, '')                                AS VEHICLE_PLATE,
  COALESCE(SHP_XD_LAST_FACILITY_ID, '')                                        AS FACILITY_XD_ANTERIOR,
  COALESCE(CAST(TMS_TR_PACKINGLIST_NUMBER AS STRING), '')                       AS TMS_PACKINGLIST,
  COALESCE(CAST(FLAG_RECEIVED_HU AS STRING), '')                               AS FLAG_HU_RECEBIDA,
  COALESCE(LAST_SYSTEM_HANDLING_SVC, '')                                        AS ULTIMO_SISTEMA_SVC,
  COALESCE(CAST(LAST_HANDLING_SVC_DT AS STRING), '')                           AS DATA_ULTIMA_SVC,
  CAST(ROUND(SHP_ORDER_COST_USD, 2) AS STRING)                                 AS GMV_USD,
  CASE
    WHEN FLAG_BPP = true                       THEN 'Possivel Lost'
    WHEN DETAIL_SHIPMENT = 'ON WAY >= 11 DIAS' THEN '>= 11 dias OW'
    ELSE '< 11 dias OW'
  END                                                                          AS Situation
FROM `meli-bi-data.WHOWNER.LK_SHP_MISSING_MANAGEMENT_PACKAGES`
WHERE SHP_LG_FACILITY_ID = '{FACILITY}'
  AND SHP_LG_STATUS = 'on_way'
  AND (
    (FLAG_BPP = true
     AND SHP_ORDER_COST_USD >= {GMV_MINIMO_USD})
    OR (FLAG_BPP = false
        AND DETAIL_SHIPMENT = 'ON WAY >= 11 DIAS'
        AND SHP_ORDER_COST_USD >= {GMV_MINIMO_USD})
    OR (FLAG_BPP = false
        AND DETAIL_SHIPMENT IS NULL
        AND SHP_ORDER_COST_USD >= {GMV_MINIMO_OW_USD})
  )
ORDER BY SHP_ORDER_COST_USD DESC
"""

# ============================================================
# FUNÇÕES
# ============================================================

def conectar_google():
    """Conecta ao BigQuery e Google Sheets usando credenciais do gcloud."""
    print("Conectando ao Google...")
    scopes = [
        'https://www.googleapis.com/auth/bigquery',
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/cloud-platform',
    ]
    credentials, _ = default(scopes=scopes)
    bq_client = bigquery.Client(credentials=credentials, project='meli-bi-data')
    gs_client  = gspread.authorize(credentials)
    print("Conectado!")
    return bq_client, gs_client


def buscar_bigquery(bq_client, query, nome):
    """Executa a query e devolve DataFrame."""
    print(f"\nBuscando {nome} no BigQuery...")
    df = bq_client.query(query).to_dataframe()
    print(f"  {len(df)} pacotes encontrados")
    return df


def verificar_entrega(bq_client, ids):
    """
    Consulta BT_SHP_LG_SHIPMENTS para saber o status atual dos pacotes removidos.
    Retorna dict: { shp_id_str: SHP_LG_STATUS }
    """
    if not ids:
        return {}
    ids_num = [str(int(i)) for i in ids if str(i).strip().isdigit()]
    if not ids_num:
        return {}
    ids_sql = ', '.join(ids_num)
    query = f"""
    SELECT CAST(SHP_SHIPMENT_ID AS STRING) AS SHP_SHIPMENT_ID,
           COALESCE(SHP_LG_SUB_STATUS, '') AS SHP_LG_SUB_STATUS
    FROM `meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS`
    WHERE SHP_SHIPMENT_ID IN ({ids_sql})
    """
    try:
        df = bq_client.query(query).to_dataframe()
        return dict(zip(df['SHP_SHIPMENT_ID'].astype(str), df['SHP_LG_SUB_STATUS'].astype(str)))
    except Exception as e:
        print(f"  ERRO verificar_entrega — status dos pacotes nao verificado, arquivamento pode ser incorreto: {e}")
        raise


def montar_linha_on_route(row):
    """
    Colunas da aba ON ROUTE (A–W):
    A  Responsável        (vazio — preencher)
    B  Situation
    C  SHP_SHIPMENT_ID
    D  Ação               (vazio — preencher)
    E  TRAMO
    F  FACILITY
    G  SHP_LG_STATUS
    H  SHP_LG_SUB_STATUS
    I  SHP_LG_SHIPMENT_CHK_DT
    J  DIAS VENCIMENTO PROMESSA
    K  ULTIMO_SISTEMA_MOVIMENTACAO_SVC
    L  SHP_DESTINATION_ID
    M  DATA CANCELAMENTO
    N  DATA_ULTIMA_MOVIMENTACAO_SVC
    O  SHP_STATUS_ID
    P  SHP_SUBSTATUS_ID
    Q  DATA ATIVO POC
    R  DIAS_PEDIDO_CANCELAMENTO  (vazio — preencher)
    S  RETORNO POC               (vazio — preencher)
    T  DIAS_SEM_MOVIMENTACAO_SVC
    U  ORIGEN_FACILITY_ID
    V  FACILITY_XD_OW
    W  GMV USD
    (AF → Data de Entrada — preenchida separadamente pelo script)
    """
    return [
        '',                                              # A  Responsável
        str(row.get('Situation', '')),                   # B  Situation
        str(row.get('SHP_SHIPMENT_ID', '')),             # C  ID
        '',                                              # D  Ação
        FACILITY,                                        # E  TRAMO
        str(row.get('FACILITY', '')),                    # F  FACILITY
        str(row.get('SHP_LG_STATUS', '')),               # G  Status
        str(row.get('SHP_LG_SUB_STATUS', '')),           # H  Sub-status
        str(row.get('SHP_LG_SHIPMENT_CHK_DT', '')),     # I  Checkpoint
        str(row.get('DIAS_VENCIMENTO_PROMESSA', '')),    # J  Dias vencimento
        str(row.get('ULTIMO_SISTEMA', '')),              # K  Último sistema SVC
        str(row.get('SHP_DESTINATION_ID', '')),          # L  Destino
        str(row.get('DATA_CANCELAMENTO', '')),           # M  Data cancelamento
        str(row.get('DATA_ULTIMA_MOVIMENTACAO', '')),    # N  Última movimentação SVC
        str(row.get('SHP_STATUS_ID', '')),               # O  Status ID
        str(row.get('SHP_SUBSTATUS_ID', '')),            # P  Substatus ID
        str(row.get('DATA_ATIVO_POC', '')),              # Q  Data ativo POC
        '',                                              # R  Dias pedido cancelamento
        '',                                              # S  Retorno POC
        str(row.get('DIAS_SEM_MOVIMENTACAO', '')),       # T  Dias sem movimentação SVC
        str(row.get('ORIGEN_FACILITY_ID', '')),          # U  Origem
        str(row.get('FACILITY_XD_OW', '')),              # V  Facility XD OW
        str(row.get('GMV_USD', '')),                     # W  GMV USD
    ]


def montar_linha_on_way(row):
    """
    Colunas da aba ON WAY (A–V):
    A  Responsável              (vazio — preencher)
    B  Situation
    C  SHP_SHIPMENT_ID
    D  DIAS_PEDIDO_CANCELAMENTO
    E  SHP_CANCEL_DT
    F  SHP_LG_FACILITY_ID
    G  SHP_DIAS_VENCIMENTO_PROMESSA
    H  SHP_LG_STATUS
    I  SHP_LG_SUB_STATUS
    J  SHP_LG_SHIPMENT_CHK_DT
    K  SHP_STATUS_ID
    L  SHP_SUBSTATUS_ID
    M  SHP_DIAS_ON_WAY
    N  SHP_LG_CARRIER_NAME_LH
    O  SHP_LG_ROUTE_ID_LH
    P  SHP_LG_VEHICLE_LICENSE_PLATE_LH
    Q  FACILITY_XD_ANTERIOR
    R  TMS_TR_PACKINGLIST_NUMBER
    S  FLAG_HU_RECEBIDA
    T  ULTIMO_SISTEMA_MOVIMENTACAO_SVC
    U  DATA_ULTIMA_MOVIMENTACAO_SVC
    V  GMV USD
    (AF → Data de Entrada — preenchida separadamente pelo script)
    """
    return [
        '',                                              # A  Responsável
        str(row.get('Situation', '')),                   # B  Situation
        str(row.get('SHP_SHIPMENT_ID', '')),             # C  ID
        str(row.get('DIAS_CANCELAMENTO', '')),           # D  Dias pedido cancelamento
        str(row.get('DATA_CANCELAMENTO', '')),           # E  Data cancelamento
        FACILITY,                                        # F  SHP_LG_FACILITY_ID
        str(row.get('DIAS_VENCIMENTO_PROMESSA', '')),    # G  Dias vencimento
        str(row.get('SHP_LG_STATUS', '')),               # H  Status
        str(row.get('SHP_LG_SUB_STATUS', '')),           # I  Sub-status
        str(row.get('SHP_LG_SHIPMENT_CHK_DT', '')),     # J  Checkpoint
        str(row.get('SHP_STATUS_ID', '')),               # K  Status ID
        str(row.get('SHP_SUBSTATUS_ID', '')),            # L  Substatus ID
        str(row.get('DIAS_ON_WAY', '')),                 # M  Dias on way
        str(row.get('CARRIER_NAME', '')),                # N  Transportadora
        str(row.get('ROUTE_ID', '')),                    # O  Rota
        str(row.get('VEHICLE_PLATE', '')),               # P  Placa
        str(row.get('FACILITY_XD_ANTERIOR', '')),        # Q  Facility XD anterior
        str(row.get('TMS_PACKINGLIST', '')),             # R  TMS packing list
        str(row.get('FLAG_HU_RECEBIDA', '')),            # S  Flag HU recebida
        str(row.get('ULTIMO_SISTEMA_SVC', '')),          # T  Último sistema SVC
        str(row.get('DATA_ULTIMA_SVC', '')),             # U  Data última movimentação SVC
        str(row.get('GMV_USD', '')),                     # V  GMV USD
    ]


def atualizar_aba(sheet, df, nome_aba, linha_fn, idx_gmv, bq_client, col_acao_lp):
    """
    Sincroniza a aba com o BigQuery:
      - Adiciona pacotes novos logo após o último registro
      - Corrige a coluna Situation dos existentes
      - Remove linhas que não aparecem mais nos resultados (e arquiva os dados)
    idx_gmv    : índice 0-based da coluna GMV (ON ROUTE=22, ON WAY=21)
    col_acao_lp: coluna da Ação de LP na planilha (ON ROUTE='X', ON WAY='W')
    """
    todos_dados = sheet.get_all_values()
    col_b = sheet.col_values(2)   # Situation
    col_c = sheet.col_values(3)   # SHP_SHIPMENT_ID

    # mapa: ID -> (linha na planilha, situation atual)
    existentes = {}
    for i, shp_id in enumerate(col_c[1:], start=2):
        if shp_id:
            situation_atual = col_b[i - 1] if i - 1 < len(col_b) else ''
            existentes[str(shp_id)] = (i, situation_atual)

    ja_existem   = set(existentes.keys())
    ids_bigquery = set(df['SHP_SHIPMENT_ID'].astype(str))
    print(f"  {len(ja_existem)} pacotes já existem na planilha")

    # adiciona novos
    novos = df[~df['SHP_SHIPMENT_ID'].isin(ja_existem)]
    print(f"  {len(novos)} pacotes novos para adicionar")

    if not novos.empty:
        linhas = [linha_fn(row) for _, row in novos.iterrows()]

        # encontra a última linha com dado na coluna C e escreve logo abaixo
        last_row = 1
        for i in range(len(col_c) - 1, -1, -1):
            if col_c[i]:
                last_row = i + 1
                break
        next_row = last_row + 1

        # garantir que a planilha tem linhas suficientes
        rows_needed = next_row + len(linhas) - 1
        sheet_props = sheet.spreadsheet.fetch_sheet_metadata()
        for s in sheet_props.get('sheets', []):
            if s['properties']['title'] == sheet.title:
                current_rows = s['properties']['gridProperties']['rowCount']
                if rows_needed > current_rows:
                    sheet.add_rows(rows_needed - current_rows + 50)
                break

        sheet.update(range_name=f'A{next_row}', values=linhas, value_input_option='USER_ENTERED')

        # registra a data de entrada em AF (após a última coluna AE)
        hoje = datetime.now().strftime('%d/%m/%Y')
        datas = [[hoje] for _ in linhas]
        sheet.update(range_name=f'AF{next_row}', values=datas, value_input_option='USER_ENTERED')

        print(f"  {len(linhas)} linhas adicionadas a partir da linha {next_row}!")

    # corrige Situation dos existentes que continuam no BigQuery
    updates = []
    for _, row in df[df['SHP_SHIPMENT_ID'].isin(ja_existem)].iterrows():
        shp_id = str(row['SHP_SHIPMENT_ID'])
        nova   = str(row.get('Situation', ''))
        linha, atual = existentes[shp_id]
        if nova != atual:
            updates.append({'range': f'B{linha}', 'values': [[nova]]})

    if updates:
        sheet.batch_update(updates)
        print(f"  {len(updates)} Situations corrigidas")
    else:
        print(f"  Situations já corretas")

    # coleta linhas para remover e arquiva os dados antes de deletar
    hoje = datetime.now().strftime('%d/%m/%Y')

    # verifica no BigQuery quais foram entregues (delivered)
    ids_para_remover = [sid for sid, (ln, _) in existentes.items() if sid not in ids_bigquery]
    print(f"  Verificando status de {len(ids_para_remover)} pacote(s) removido(s)...")
    status_bq = verificar_entrega(bq_client, ids_para_remover)

    arquivados       = []
    para_remover     = []
    recuperados      = 0
    updates_conclusao = []

    for shp_id, (linha, situation) in existentes.items():
        if shp_id not in ids_bigquery:
            para_remover.append(linha)
            sub          = status_bq.get(shp_id, '').lower()
            foi_entregue = sub.startswith('delivered') or sub == 'sorting'
            foi_perdido  = sub == 'lost'

            if foi_entregue:
                recuperados += 1

            if foi_entregue or foi_perdido:
                status_caso_novo = 'Concluído'
                acao_lp_novo     = ('Acompanhado - Pacote seguiu fluxo correto'
                                    if foi_entregue else
                                    'Acompanhado - Pacote confirmado perdido')
                updates_conclusao += [
                    {'range': f'AC{linha}',            'values': [[status_caso_novo]]},
                    {'range': f'{col_acao_lp}{linha}', 'values': [[acao_lp_novo]]},
                    {'range': f'AD{linha}',            'values': [['Recuperado' if foi_entregue else 'Perdido']]},
                ]

            if linha - 1 < len(todos_dados):
                row_data    = todos_dados[linha - 1]
                responsavel = row_data[0]       if len(row_data) > 0       else ''
                gmv         = row_data[idx_gmv] if len(row_data) > idx_gmv else ''
                if foi_entregue or foi_perdido:
                    status_caso = 'Concluído'
                    finalizacao = 'Seguiu fluxo correto' if foi_entregue else 'Perdido'
                    resp_final  = responsavel if responsavel.strip() else 'Lucas Nascimento'
                else:
                    status_caso = row_data[28] if len(row_data) > 28 else ''
                    finalizacao = row_data[29] if len(row_data) > 29 else ''
                    resp_final  = responsavel
                arquivados.append([hoje, nome_aba, shp_id, situation, gmv, resp_final, status_caso, finalizacao])

    # aplica os updates de conclusão antes de deletar as linhas
    if updates_conclusao:
        sheet.batch_update(updates_conclusao)
        print(f"  OK: {recuperados} pacote(s) marcado(s) como Concluido - seguiram fluxo")

    para_remover.sort(reverse=True)

    if para_remover:
        sheet_id = sheet.id
        requests = [
            {
                'deleteDimension': {
                    'range': {
                        'sheetId': sheet_id,
                        'dimension': 'ROWS',
                        'startIndex': linha - 1,  # 0-indexed
                        'endIndex':   linha,
                    }
                }
            }
            for linha in para_remover
        ]
        sheet.spreadsheet.batch_update({'requests': requests})
        print(f"  {len(para_remover)} linhas removidas (fora do critério atual)")
    else:
        print(f"  Nenhuma linha para remover")

    try:
        gmv_total = round(sum(float(str(v) or '0') for v in df['GMV_USD']), 2)
    except Exception:
        gmv_total = 0.0

    novos_criticos = []
    if not novos.empty:
        for _, row in novos.iterrows():
            try:
                if str(row['Situation']) == 'Possivel Lost' and float(str(row['GMV_USD'])) >= GMV_ALERTA_USD:
                    novos_criticos.append({'id': str(row['SHP_SHIPMENT_ID']), 'gmv': float(str(row['GMV_USD']))})
            except Exception:
                continue

    return {
        'novos':          len(novos),
        'removidos':      len(para_remover),
        'recuperados':    recuperados,
        'arquivados':     arquivados,
        'total':          len(ids_bigquery),
        'por_situation':  df['Situation'].value_counts().to_dict() if not df.empty else {},
        'top_id':         str(df.iloc[0]['SHP_SHIPMENT_ID']) if not df.empty else '-',
        'top_gmv':        str(df.iloc[0]['GMV_USD'])         if not df.empty else '-',
        'gmv_total':      gmv_total,
        'novos_criticos': novos_criticos,
    }


def atualizar_cftv(planilha_controle, planilha_cftv):
    """
    Lê o formulário CFTV e atualiza nas abas de controle:
      Y  → 'Sim' se o pacote está no formulário, 'Não' se não está
      Z  → Responsável CFTV (col W do formulário)
      AA → Status (col Y do formulário)
      AB → Link  (col AE do formulário)
    """
    print("\nAtualizando CFTV...")
    cftv_counts = {}
    try:
        aba_cftv = planilha_cftv.worksheet(ABA_CFTV)
        dados    = aba_cftv.get_all_values()
        if len(dados) <= 1:
            print("  Sem dados de CFTV")
            return cftv_counts

        header = dados[0]
        def _hi(name, fallback):
            try: return header.index(name)
            except ValueError: return fallback
        idx_shp  = _hi('Shipment',    6)
        idx_resp = _hi('Responsável', 22)
        idx_stat = _hi('Status',      24)
        idx_link = _hi('Link',        30)

        ids_form = [str(r[idx_shp])  if len(r) > idx_shp  else '' for r in dados[1:]]
        col_w    = [r[idx_resp]      if len(r) > idx_resp  else '' for r in dados[1:]]
        col_y    = [r[idx_stat]      if len(r) > idx_stat  else '' for r in dados[1:]]
        col_ae   = [r[idx_link]      if len(r) > idx_link  else '' for r in dados[1:]]

        mapa = {ids_form[i]: (col_w[i], col_y[i], col_ae[i])
                for i in range(len(ids_form)) if ids_form[i]}

        for nome_aba in [ABA_ON_ROUTE, ABA_ON_WAY]:
            aba      = planilha_controle.worksheet(nome_aba)
            ids_ctrl = aba.col_values(3)   # coluna C
            updates  = []

            for i, shp_id in enumerate(ids_ctrl[1:], start=2):
                if not shp_id:
                    continue
                if str(shp_id) in mapa:
                    vw, vy, vae = mapa[str(shp_id)]
                    updates += [
                        {'range': f'Y{i}',  'values': [['Sim']]},
                        {'range': f'Z{i}',  'values': [[vw]]},
                        {'range': f'AA{i}', 'values': [[vy]]},
                        {'range': f'AB{i}', 'values': [[vae]]},
                    ]
                else:
                    updates.append({'range': f'Y{i}', 'values': [['Não']]})

            if updates:
                aba.batch_update(updates)
                sim_count = sum(1 for u in updates if u['values'] == [['Sim']])
                cftv_counts[nome_aba] = {
                    'sim': sim_count,
                    'total': len(ids_ctrl) - 1,
                }
                print(f"  CFTV '{nome_aba}': {sim_count} com CFTV, {len(ids_ctrl)-1-sim_count} sem")

    except Exception as e:
        print(f"  Aviso CFTV: {e}")

    return cftv_counts


def corrigir_historico(planilha_controle, bq_client):
    """
    Varre o Histórico procurando linhas sem Status e sem Finalização.
    Se o pacote foi entregue (SHP_LG_SUB_STATUS startswith 'delivered'),
    preenche Status='Concluído' e Finalização='Seguiu fluxo correto'.
    """
    print("\nCorrigindo Histórico (pacotes sem status)...")
    try:
        aba_hist = planilha_controle.worksheet(ABA_HISTORICO)
        dados    = aba_hist.get_all_values()
    except gspread.exceptions.WorksheetNotFound:
        print("  Aba Histórico não encontrada")
        return

    # { shp_id: {'linha': i, 'resp': responsavel} }
    ids_sem_status = {}
    for i, row in enumerate(dados[1:], start=2):
        shp_id = row[2] if len(row) > 2 else ''
        status = row[6] if len(row) > 6 else ''
        final  = row[7] if len(row) > 7 else ''
        resp   = row[5] if len(row) > 5 else ''
        if shp_id and not status.strip() and not final.strip():
            ids_sem_status[shp_id] = {'linha': i, 'resp': resp}

    if not ids_sem_status:
        print("  Nenhuma linha para corrigir")
        return

    print(f"  {len(ids_sem_status)} linha(s) sem status — verificando no BigQuery...")
    status_bq = verificar_entrega(bq_client, list(ids_sem_status.keys()))

    updates    = []
    corrigidos = 0
    for shp_id, info in ids_sem_status.items():
        linha = info['linha']
        resp  = info['resp']
        sub   = status_bq.get(shp_id, '').lower()

        foi_entregue = sub.startswith('delivered') or sub == 'sorting'
        foi_perdido  = sub == 'lost'

        if foi_entregue or foi_perdido:
            finalizacao = 'Seguiu fluxo correto' if foi_entregue else 'Perdido'
            updates += [
                {'range': f'G{linha}', 'values': [['Concluído']]},
                {'range': f'H{linha}', 'values': [[finalizacao]]},
            ]
            if not resp.strip():
                updates.append({'range': f'F{linha}', 'values': [['Lucas Nascimento']]})
            corrigidos += 1

    if updates:
        aba_hist.batch_update(updates)
        print(f"  {corrigidos} linha(s) corrigida(s) no Histórico")
    else:
        print(f"  Nenhum pacote encontrado para corrigir")


def salvar_historico(planilha_controle, arquivados_route, arquivados_way):
    """
    Arquiva no Histórico as linhas removidas das abas ativas.
    Cria a aba automaticamente se não existir.
    Colunas: Data | Origem | SHP_SHIPMENT_ID | Situation | GMV USD | Responsável | Status Caso | Finalização
    """
    todos = arquivados_route + arquivados_way
    if not todos:
        print("\nHistórico: nenhuma linha para arquivar")
        return

    print(f"\nArquivando {len(todos)} linha(s) no Histórico...")
    try:
        aba_hist = planilha_controle.worksheet(ABA_HISTORICO)
    except gspread.exceptions.WorksheetNotFound:
        aba_hist = planilha_controle.add_worksheet(title=ABA_HISTORICO, rows=5000, cols=10)
        aba_hist.update(
            range_name='A1',
            values=[['Data', 'Origem', 'SHP_SHIPMENT_ID', 'Situation', 'GMV USD',
                     'Responsável', 'Status Caso', 'Finalização']],
            value_input_option='USER_ENTERED',
        )
        print("  Aba 'Histórico' criada")

    col_a = aba_hist.col_values(1)
    last_row = len(col_a)
    next_row = last_row + 1
    aba_hist.update(range_name=f'A{next_row}', values=todos, value_input_option='USER_ENTERED')
    print(f"  {len(todos)} linha(s) arquivada(s) (linha {next_row})")


def ler_stats_mensais(planilha_controle, aba_route, aba_way):
    """
    Retorna contagens para o relatório mensal:
      - concluidos_mes: linhas no Histórico do mês atual com Status = 'Conclu...'
      - em_andamento / pendente / sem_acompanhamento: de col AC das abas ativas
    """
    agora    = datetime.now()
    mes_ano  = agora.strftime('%m/%Y')  # "05/2026"
    mes_label = f"{MESES_PT[agora.month]}/{agora.year}"

    concluidos_mes = 0
    try:
        aba_hist   = planilha_controle.worksheet(ABA_HISTORICO)
        dados_hist = aba_hist.get_all_values()
        for row in dados_hist[1:]:
            data   = row[0] if len(row) > 0 else ''
            status = row[6] if len(row) > 6 else ''
            if mes_ano in data and 'conclu' in status.lower():
                concluidos_mes += 1
    except gspread.exceptions.WorksheetNotFound:
        pass

    em_andamento       = 0
    pendente           = 0
    sem_acompanhamento = 0
    for aba in [aba_route, aba_way]:
        col_ac = aba.col_values(29)  # coluna AC (1-based = 29)
        for val in col_ac[1:]:
            v = val.strip().lower()
            if 'andamento' in v:
                em_andamento += 1
            elif 'pendente' in v:
                pendente += 1
            elif v == '':
                sem_acompanhamento += 1

    return {
        'mes':                 mes_label,
        'concluidos_mes':      concluidos_mes,
        'em_andamento':        em_andamento,
        'pendente':            pendente,
        'sem_acompanhamento':  sem_acompanhamento,
    }


def enviar_alerta_urgente(novos_criticos_route, novos_criticos_way):
    """Envia mensagem urgente no Chat para cada novo Possivel Lost com GMV >= GMV_ALERTA_USD."""
    todos = [('ON ROUTE', p) for p in novos_criticos_route] + \
            [('ON WAY',   p) for p in novos_criticos_way]
    if not todos:
        log.info("Sem novos Possivel Lost de alto valor.")
        return
    for origem, pkg in todos:
        msg = (
            f"🚨 *ALERTA — Possível Lost Alto Valor | {origem}*\n"
            f"Novo pacote identificado agora:\n"
            f"📦 `{pkg['id']}`\n"
            f"💰 GMV: *${pkg['gmv']:,.2f}*\n"
            f"⚠️ Verificar imediatamente na planilha de controle."
        )
        try:
            payload = json.dumps({'text': msg}).encode('utf-8')
            req = urllib.request.Request(
                WEBHOOK_GCHAT, data=payload,
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req)
            log.info("Alerta urgente enviado: %s ($%.2f)", pkg['id'], pkg['gmv'])
        except Exception as e:
            log.warning("Falha no alerta urgente: %s", e)


def salvar_snapshot(planilha_controle, stats_route, stats_way):
    """Salva linha de snapshot diário na aba Snapshots."""
    def sit(stats, chave):
        return stats['por_situation'].get(chave, 0)
    try:
        try:
            aba = planilha_controle.worksheet(ABA_SNAPSHOTS)
        except gspread.exceptions.WorksheetNotFound:
            aba = planilha_controle.add_worksheet(title=ABA_SNAPSHOTS, rows=2000, cols=16)
            aba.update(
                range_name='A1',
                values=[['Data',
                         'OTR Total', 'OTR GMV ($)', 'OTR Poss.Lost', 'OTR Procurar',
                         'OTR Novos',  'OTR Recuperados',
                         'OW Total',   'OW GMV ($)',  'OW Poss.Lost',
                         'OW >=11d',   'OW <11d',
                         'OW Novos',   'OW Recuperados',
                         'GMV Total ($)']],
                value_input_option='USER_ENTERED',
            )
            log.info("Aba 'Snapshots' criada")
        hoje      = datetime.now().strftime('%d/%m/%Y')
        gmv_total = round(stats_route['gmv_total'] + stats_way['gmv_total'], 2)
        aba.append_rows([[
            hoje,
            stats_route['total'],    stats_route['gmv_total'],
            sit(stats_route, 'Possivel Lost'),
            sit(stats_route, 'Procurar Pacote'),
            stats_route['novos'],    stats_route['recuperados'],
            stats_way['total'],      stats_way['gmv_total'],
            sit(stats_way, 'Possivel Lost'),
            sit(stats_way, '>= 11 dias OW'),
            sit(stats_way, '< 11 dias OW'),
            stats_way['novos'],      stats_way['recuperados'],
            gmv_total,
        ]], value_input_option='USER_ENTERED')
        log.info("Snapshot: OTR=%d OW=%d GMV Total=$%.2f", stats_route['total'], stats_way['total'], gmv_total)
    except Exception as e:
        log.error("Falha ao salvar snapshot: %s", e)


def gerar_analise_claude(stats_route, stats_way, cftv, stats_mensais):
    """Gera parágrafo de análise em português via Claude API. Retorna '' se indisponível."""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        log.warning("ANTHROPIC_API_KEY não configurada — análise IA pulada")
        return ''
    try:
        import anthropic
    except ImportError:
        log.warning("Pacote 'anthropic' não instalado — análise IA pulada")
        return ''

    def sit(stats, chave):
        return stats['por_situation'].get(chave, 0)

    cftv_r = cftv.get(ABA_ON_ROUTE, {})
    cftv_w = cftv.get(ABA_ON_WAY,   {})

    contexto = (
        f"ON ROUTE — {stats_route['total']} pacotes\n"
        f"  Procurar Pacote: {sit(stats_route,'Procurar Pacote')} | Possível Lost: {sit(stats_route,'Possivel Lost')}\n"
        f"  Novos: +{stats_route['novos']} | Removidos: -{stats_route['removidos']} | Recuperados: {stats_route['recuperados']}\n"
        f"  Top GMV: ${stats_route['top_gmv']}\n"
        f"  CFTV: {cftv_r.get('sim',0)}/{cftv_r.get('total',0)}\n\n"
        f"ON WAY — {stats_way['total']} pacotes\n"
        f"  Possível Lost: {sit(stats_way,'Possivel Lost')} | >=11 dias: {sit(stats_way,'>= 11 dias OW')} | <11 dias: {sit(stats_way,'< 11 dias OW')}\n"
        f"  Novos: +{stats_way['novos']} | Removidos: -{stats_way['removidos']} | Recuperados: {stats_way['recuperados']}\n"
        f"  Top GMV: ${stats_way['top_gmv']}\n"
        f"  CFTV: {cftv_w.get('sim',0)}/{cftv_w.get('total',0)}\n\n"
        f"Visibilidade {stats_mensais['mes']}:\n"
        f"  Concluídos: {stats_mensais['concluidos_mes']} | Em andamento: {stats_mensais['em_andamento']}\n"
        f"  Pendente: {stats_mensais['pendente']} | Sem acompanhamento: {stats_mensais['sem_acompanhamento']}"
    )

    # Verifica se a API key tem só chars ASCII (problema comum de copy-paste)
    api_key_non_ascii = [(i, hex(ord(c))) for i, c in enumerate(api_key) if ord(c) >= 128]
    if api_key_non_ascii:
        log.error("ANTHROPIC_API_KEY contem chars nao-ASCII: pos=%s — recadastre a key no GitHub Secrets",
                  api_key_non_ascii[:5])
        return ''

    try:
        log.info("Gerando analise com Claude (claude-opus-4-8)...")
        client = anthropic.Anthropic(api_key=api_key)
        # Normaliza para ASCII puro (remove acentos) para evitar UnicodeEncodeError
        # no SDK/httpx — a analise retornada pelo modelo continua em portugues correto
        import unicodedata
        def _ascii(s):
            return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii')

        contexto_ascii = (
            f"ON ROUTE - {stats_route['total']} pacotes\n"
            f"  Procurar Pacote: {sit(stats_route,'Procurar Pacote')} | Possivel Lost: {sit(stats_route,'Possivel Lost')}\n"
            f"  Novos: +{stats_route['novos']} | Removidos: -{stats_route['removidos']} | Recuperados: {stats_route['recuperados']}\n"
            f"  Top GMV: ${_ascii(stats_route['top_gmv'])}\n"
            f"  CFTV: {cftv_r.get('sim',0)}/{cftv_r.get('total',0)}\n\n"
            f"ON WAY - {stats_way['total']} pacotes\n"
            f"  Possivel Lost: {sit(stats_way,'Possivel Lost')} | >=11 dias: {sit(stats_way,'>= 11 dias OW')} | <11 dias: {sit(stats_way,'< 11 dias OW')}\n"
            f"  Novos: +{stats_way['novos']} | Removidos: -{stats_way['removidos']} | Recuperados: {stats_way['recuperados']}\n"
            f"  Top GMV: ${_ascii(stats_way['top_gmv'])}\n"
            f"  CFTV: {cftv_w.get('sim',0)}/{cftv_w.get('total',0)}\n\n"
            f"Visibilidade {stats_mensais['mes']}:\n"
            f"  Concluidos: {stats_mensais['concluidos_mes']} | Em andamento: {stats_mensais['em_andamento']}\n"
            f"  Pendente: {stats_mensais['pendente']} | Sem acompanhamento: {stats_mensais['sem_acompanhamento']}"
        )
        prompt = (
            "Voce e analista de Loss Prevention do SSP30 (MercadoLivre, Guarulhos). "
            "Com base nos dados abaixo, escreva 2 a 3 frases de analise em portugues "
            "destacando o ponto mais relevante do dia: tendencia de risco, pacotes criticos, "
            "recuperos expressivos ou alertas. Seja direto e objetivo, sem titulos nem bullet points.\n\n"
            + contexto_ascii
        )
        resp = client.messages.create(
            model='claude-opus-4-8',
            max_tokens=1024,
            messages=[{'role': 'user', 'content': prompt}],
        )
        for block in resp.content:
            if hasattr(block, 'text'):
                texto = block.text.strip()
                log.info("Analise IA gerada (%d chars)", len(texto))
                return texto
        return ''
    except Exception as e:
        import traceback as _tb
        log.error("Falha na Claude API: %s\n%s", e, _tb.format_exc())
        return ''


def enviar_gchat(stats_route, stats_way, cftv, stats_mensais, duracao, data_hora):
    """Monta e envia o report diário para o Google Chat."""

    analise_ia = gerar_analise_claude(stats_route, stats_way, cftv, stats_mensais)

    def sit(stats, chave):
        return stats['por_situation'].get(chave, 0)

    cftv_r = cftv.get(ABA_ON_ROUTE, {})
    cftv_w = cftv.get(ABA_ON_WAY,   {})

    sm = stats_mensais
    msg = (
        f"*🔔 Report Risco SSP30 — {data_hora}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"*📦 ON ROUTE* — {stats_route['total']} pacotes\n"
        f"  Procurar Pacote : *{sit(stats_route, 'Procurar Pacote')}*\n"
        f"  Possivel Lost   : *{sit(stats_route, 'Possivel Lost')}*\n"
        f"  Novos hoje      : +{stats_route['novos']}  |  Removidos: -{stats_route['removidos']}\n"
        f"  ✅ Recuperados  : *{stats_route['recuperados']}* (Acompanhado fluxo correto)\n"
        f"  CFTV solicitado : *{cftv_r.get('sim', 0)}/{cftv_r.get('total', 0)}*\n"
        f"  💰 Top GMV: *${stats_route['top_gmv']}* · `{stats_route['top_id']}`\n"
        f"\n"
        f"*🚛 ON WAY* — {stats_way['total']} pacotes\n"
        f"  Possivel Lost    : *{sit(stats_way, 'Possivel Lost')}*\n"
        f"  >= 11 dias OW    : *{sit(stats_way, '>= 11 dias OW')}*\n"
        f"  < 11 dias OW     : *{sit(stats_way, '< 11 dias OW')}*\n"
        f"  Novos hoje       : +{stats_way['novos']}  |  Removidos: -{stats_way['removidos']}\n"
        f"  ✅ Recuperados   : *{stats_way['recuperados']}* (Acompanhado fluxo correto)\n"
        f"  CFTV solicitado  : *{cftv_w.get('sim', 0)}/{cftv_w.get('total', 0)}*\n"
        f"  💰 Top GMV: *${stats_way['top_gmv']}* · `{stats_way['top_id']}`\n"
        f"\n"
        f"*📊 Visibilidade {sm['mes']}*\n"
        f"  ✅ Concluídos no mês  : *{sm['concluidos_mes']}*\n"
        f"  🔄 Em andamento       : *{sm['em_andamento']}*\n"
        f"  ⏳ Pendente           : *{sm['pendente']}*\n"
        f"  📭 Sem acompanhamento : *{sm['sem_acompanhamento']}*\n"
        f"\n"
        + (f"\n🤖 *Análise IA*\n_{analise_ia}_\n" if analise_ia else '')
        + f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Atualizado em {duracao}s"
    )

    try:
        payload = json.dumps({'text': msg}).encode('utf-8')
        req = urllib.request.Request(
            WEBHOOK_GCHAT,
            data=payload,
            headers={'Content-Type': 'application/json'},
        )
        urllib.request.urlopen(req)
        print("\nReport enviado ao Google Chat!")
    except Exception as e:
        print(f"\nAviso: não foi possível enviar ao Google Chat — {e}")


# ============================================================
# EXECUÇÃO
# ============================================================
if __name__ == '__main__':
    inicio = datetime.now()
    print("=" * 55)
    print(f"Atualização de Risco SSP30 — {inicio.strftime('%d/%m/%Y %H:%M')}")
    print("=" * 55)

    bq_client, gs_client = conectar_google()

    planilha_controle = gs_client.open_by_key(PLANILHA_CONTROLE_ID)
    planilha_cftv     = gs_client.open_by_key(PLANILHA_CFTV_ID)

    print("\n--- ON ROUTE ---")
    df_route    = buscar_bigquery(bq_client, QUERY_ON_ROUTE, 'ON ROUTE')
    aba_route   = planilha_controle.worksheet(ABA_ON_ROUTE)
    stats_route = atualizar_aba(aba_route, df_route, ABA_ON_ROUTE, montar_linha_on_route,
                                idx_gmv=22, bq_client=bq_client, col_acao_lp='X')

    print("\n--- ON WAY ---")
    df_way    = buscar_bigquery(bq_client, QUERY_ON_WAY, 'ON WAY')
    aba_way   = planilha_controle.worksheet(ABA_ON_WAY)
    stats_way = atualizar_aba(aba_way, df_way, ABA_ON_WAY, montar_linha_on_way,
                              idx_gmv=21, bq_client=bq_client, col_acao_lp='W')

    print("\n--- Alertas urgentes ---")
    enviar_alerta_urgente(stats_route['novos_criticos'], stats_way['novos_criticos'])

    salvar_historico(planilha_controle, stats_route['arquivados'], stats_way['arquivados'])
    corrigir_historico(planilha_controle, bq_client)

    cftv = atualizar_cftv(planilha_controle, planilha_cftv)

    stats_mensais = ler_stats_mensais(planilha_controle, aba_route, aba_way)

    salvar_snapshot(planilha_controle, stats_route, stats_way)

    fim     = datetime.now()
    duracao = (fim - inicio).seconds
    print(f"\n{'=' * 55}")
    print(f"Concluído em {duracao}s | On Route: +{stats_route['novos']} | On Way: +{stats_way['novos']}")
    print("=" * 55)

    enviar_gchat(stats_route, stats_way, cftv, stats_mensais, duracao, inicio.strftime('%d/%m/%Y %H:%M'))
