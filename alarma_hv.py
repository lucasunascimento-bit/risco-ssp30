# ============================================================
# alarma_hv.py
#
# O QUE ESTE SCRIPT FAZ:
#   1. Consulta BQ buscando pacotes HV (>= USD 350) em sorting
#      sem atrelamento na SSP30
#   2. Envia alerta no Google Chat com SHP IDs, valor e tempo parado
#   3. Registra na aba "HV Sorting" da planilha de controle
#   4. Gera relatório de fechamento do ciclo (eficiência)
#
# COMO RODAR:
#   python alarma_hv.py
#
# COMO FUNCIONA O CICLO:
#   - Cada execução é um ciclo
#   - Pacotes novos (não estavam no ciclo anterior) → alerta imediato
#   - Pacotes que saíram do sorting → marcados como resolvidos
#   - Eficiência = resolvidos / (total alertados no ciclo)
# ============================================================

from google.cloud import bigquery
from google.auth import default
import gspread
from datetime import datetime, timezone, timedelta
import json
import urllib.request

# ============================================================
# CONFIGURAÇÕES
# ============================================================
FACILITY         = 'SSP30'
FACILITY_NOME    = 'Guarulhos Mega'
GMV_HV_USD       = 350

WEBHOOK_GCHAT        = 'https://chat.googleapis.com/v1/spaces/AAQAJzYdVzU/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=vyL1G__Uu8Il-17-5Cz6DICamMChHaiZvOAIzOXFwgE'
PLANILHA_CONTROLE_ID = '1yMEH_kK5UCRB5FF13Yi6KKNPAZ49tN0lECWS4riQbI4'
ABA_HV           = 'raw'

BRT = timezone(timedelta(hours=-3))

# ============================================================
# QUERY HV SORTING — baseada no TOOLKIT SPISUL
#
# Tabela: BT_SHP_LG_SHIPMENTS (não LK_SHP_MISSING_MANAGEMENT_PACKAGES)
# "Sem atrelamento" = SHP_LG_SUB_STATUS = 'sorting'
# Inclui descrição do item via UNNEST(S.ITEMS)
# Exclui pacotes com container_removal em BT_PROBLEM_SOLVING_INCIDENT
#
# ⚠ AJUSTAR: CICLO_INICIO / CICLO_FIM conforme horário real do sorting SSP30
# ============================================================
CICLO_INICIO = '11:00:00'   # início do ciclo PM SSP30
CICLO_FIM    = '13:00:00'   # fim do ciclo PM SSP30

QUERY_HV_SORTING = f"""
WITH DistinctShipments AS (
  SELECT
    L.SHP_LG_FACILITY_ID,
    S.SHP_SHIPMENT_ID         AS ID_ENVIO,
    S.SHP_ORDER_COST_USD      AS VALOR_USD,
    ANY_VALUE(ITE.SHP_ITEM_DESC) AS DESCRICAO_ITEM,
    L.SHP_LG_LAST_UPDATED
  FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` AS S
  LEFT JOIN UNNEST(S.ITEMS) AS ITE
  INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS` AS L
    ON S.SHP_SHIPMENT_ID = L.SHP_SHIPMENT_ID
  WHERE S.SIT_SITE_ID = 'MLB'
    AND L.SHP_LG_FACILITY_ID = '{FACILITY}'
    AND S.SHP_ORDER_COST_USD >= {GMV_HV_USD}
    AND L.SHP_LG_SUB_STATUS = 'sorting'
    AND L.SHP_LG_LAST_UPDATED >= DATETIME(CURRENT_DATE('America/Sao_Paulo'), '{CICLO_INICIO}')
    AND L.SHP_LG_LAST_UPDATED <= DATETIME(CURRENT_DATE('America/Sao_Paulo'), '{CICLO_FIM}')
  GROUP BY 1, 2, 3, 5
),
CalculatedData AS (
  SELECT
    *,
    (DATETIME_DIFF(CURRENT_DATETIME(), DATETIME(SHP_LG_LAST_UPDATED), MINUTE) - 240) AS MINUTOS_NA_FILA
  FROM DistinctShipments
),
ExcludeIds AS (
  SELECT DISTINCT
    SAFE_CAST(OBJECT_ID AS INT64) AS SHP_SHIPMENT_ID
  FROM `meli-bi-data.WHOWNER.BT_PROBLEM_SOLVING_INCIDENT`
  WHERE PROBLEM_TYPE = 'container_removal'
    AND DATE(CREATED_DATE) = CURRENT_DATE('America/Sao_Paulo')
    AND SAFE_CAST(OBJECT_ID AS INT64) IS NOT NULL
),
Filtered AS (
  SELECT *
  FROM CalculatedData CD
  WHERE NOT EXISTS (
    SELECT 1 FROM ExcludeIds E WHERE E.SHP_SHIPMENT_ID = CD.ID_ENVIO
  )
)
SELECT
  'PM'                      AS nome_ciclo,
  SHP_LG_FACILITY_ID        AS FACILITY_ID,
  CAST(ID_ENVIO AS STRING)  AS SHP_ID,
  ROUND(VALOR_USD, 2)       AS GMV_USD,
  COALESCE(DESCRICAO_ITEM, '') AS DESCRICAO_ITEM,
  CAST(SHP_LG_LAST_UPDATED AS STRING) AS LAST_UPDATED,
  MINUTOS_NA_FILA
FROM Filtered
ORDER BY VALOR_USD DESC
"""


# ============================================================
# CONEXÃO
# ============================================================

def conectar_google():
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


# ============================================================
# BUSCA BQ
# ============================================================

def buscar_hv_sorting(bq_client):
    print("\nBuscando pacotes HV em sorting no BigQuery...")
    df = bq_client.query(QUERY_HV_SORTING).to_dataframe()
    print(f"  {len(df)} pacotes HV encontrados (>= USD {GMV_HV_USD}, sem atrelamento)")
    return df


# ============================================================
# TEMPO PARADO
# ============================================================

def calcular_tempo_parado(last_handling_dt_str):
    """Retorna string 'Xh Ymin' com base em LAST_HANDLING_SVC_DT."""
    if not last_handling_dt_str:
        return 'tempo desconhecido'
    try:
        dt = datetime.fromisoformat(last_handling_dt_str.replace(' UTC', '+00:00'))
        agora = datetime.now(timezone.utc)
        delta = agora - dt
        horas = int(delta.total_seconds() // 3600)
        minutos = int((delta.total_seconds() % 3600) // 60)
        return f"{horas}h {minutos}min"
    except Exception:
        return 'tempo desconhecido'


# ============================================================
# GOOGLE SHEETS — ABA HV SORTING
# ============================================================

CABECALHO_HV = [
    'Data_Hora_Alerta', 'Ciclo', 'SHP_ID', 'GMV_USD',
    'Sub_Status', 'Packinglist', 'Carrier', 'Route_ID',
    'Tempo_Parado', 'Status'
]

def garantir_aba_hv(planilha):
    """Cria a aba HV Sorting se não existir, com cabeçalho."""
    abas = [ws.title for ws in planilha.worksheets()]
    if ABA_HV not in abas:
        print(f"  Criando aba '{ABA_HV}'...")
        ws = planilha.add_worksheet(title=ABA_HV, rows=1000, cols=len(CABECALHO_HV))
        ws.append_row(CABECALHO_HV)
    return planilha.worksheet(ABA_HV)


def carregar_ids_ciclo_anterior(ws):
    """Carrega SHP_IDs alertados no ciclo anterior (últimas 4h) que ainda estão sem status final."""
    try:
        dados = ws.get_all_records()
        agora = datetime.now(BRT)
        ids_ativos = set()
        for row in dados:
            if row.get('Status') == 'Alerta_Enviado':
                try:
                    dt = datetime.fromisoformat(row['Data_Hora_Alerta'])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=BRT)
                    if (agora - dt).total_seconds() < 4 * 3600:
                        ids_ativos.add(str(row['SHP_ID']))
                except Exception:
                    pass
        return ids_ativos
    except Exception as e:
        print(f"  Aviso ao carregar ciclo anterior: {e}")
        return set()


def registrar_pacotes(ws, df, ciclo_label):
    """Registra os pacotes alertados na planilha."""
    agora_str = datetime.now(BRT).strftime('%Y-%m-%d %H:%M')
    linhas = []
    for _, row in df.iterrows():
        tempo = calcular_tempo_parado(str(row.get('LAST_HANDLING_DT', '')))
        linhas.append([
            agora_str,
            ciclo_label,
            str(row['SHP_ID']),
            str(row.get('GMV_USD', '')),
            str(row.get('SHP_LG_SUB_STATUS', '')),
            str(row.get('PACKINGLIST', '')),
            str(row.get('CARRIER_NAME', '')),
            str(row.get('ROUTE_ID', '')),
            tempo,
            'Alerta_Enviado',
        ])
    if linhas:
        ws.append_rows(linhas)
        print(f"  {len(linhas)} pacotes registrados na aba '{ABA_HV}'")


def marcar_resolvidos(ws, ids_resolvidos):
    """Marca como 'Resolvido' os pacotes que saíram do sorting."""
    if not ids_resolvidos:
        return
    try:
        dados = ws.get_all_records()
        for i, row in enumerate(dados, start=2):
            if str(row.get('SHP_ID', '')) in ids_resolvidos and row.get('Status') == 'Alerta_Enviado':
                ws.update_cell(i, CABECALHO_HV.index('Status') + 1, 'Resolvido')
        print(f"  {len(ids_resolvidos)} pacotes marcados como Resolvidos")
    except Exception as e:
        print(f"  Aviso ao marcar resolvidos: {e}")


# ============================================================
# GOOGLE CHAT — ALERTA
# ============================================================

def enviar_alerta_gchat(df, ciclo_label):
    """Envia alerta para o Google Chat com os pacotes HV encontrados."""
    if df.empty:
        return

    gmv_total = df['GMV_USD'].sum()
    linhas_pacotes = []
    for _, row in df.iterrows():
        tempo = calcular_tempo_parado(str(row.get('LAST_HANDLING_DT', '')))
        shp_id = str(row['SHP_ID'])
        gmv    = float(row.get('GMV_USD', 0))
        linhas_pacotes.append(f"🔴 ID: {shp_id} | USD {gmv:,.0f}\n   Tempo: {tempo}")

    msg = (
        f"*RELATÓRIO SORTING {ciclo_label} (>= USD {GMV_HV_USD})*\n"
        f"*{FACILITY_NOME} — SSP30*\n\n"
        f"⚠ Atenção Time. Detectamos pacotes de alto valor sem ATRELAMENTO no sorting.\n"
        f"Precisamos fazer a busca dos pacotes imediatamente.\n\n"
        f"Qtde de Pacotes: *{len(df)}*\n"
        f"GMV Total: *USD {gmv_total:,.0f}*\n\n"
        + "\n\n".join(linhas_pacotes)
    )

    _postar_gchat(msg)
    print(f"  Alerta enviado: {len(df)} pacotes, USD {gmv_total:,.0f}")


def enviar_relatorio_ciclo(df_atual, ids_anteriores, ciclo_label):
    """Envia relatório de fechamento do ciclo."""
    ids_atuais = set(df_atual['SHP_ID'].astype(str).tolist()) if not df_atual.empty else set()

    total_reportados  = len(ids_anteriores) if ids_anteriores else len(ids_atuais)
    ids_resolvidos    = ids_anteriores - ids_atuais
    ids_pendentes     = ids_anteriores & ids_atuais if ids_anteriores else set()

    gmv_atual         = df_atual['GMV_USD'].sum() if not df_atual.empty else 0
    gmv_pendente      = df_atual[df_atual['SHP_ID'].astype(str).isin(ids_pendentes)]['GMV_USD'].sum() if ids_pendentes and not df_atual.empty else 0
    gmv_salvo         = gmv_atual - gmv_pendente if ids_anteriores else 0

    eficiencia = (len(ids_resolvidos) / total_reportados * 100) if total_reportados > 0 else 0

    msg = (
        f"📊 *FECHAMENTO {ciclo_label} SSP30*\n"
        f"RELATÓRIO DO CICLO (report: {datetime.now(BRT).strftime('%Y-%m-%d %H:%M')})\n\n"
        f"✅ *MONITORAMENTO TOTAL:*\n"
        f"· IDs REPORTADAS: {total_reportados}\n"
        f"· GMV MONITORADO: USD {gmv_atual:,.2f}\n\n"
        f"⚠ *PENDENTES (ÚLTIMO REPORT):*\n"
        f"· IDs PENDENTES: {len(ids_pendentes)}\n"
        f"· GMV PENDENTE: USD {gmv_pendente:,.2f}\n\n"
        f"💰 *RESULTADO:*\n"
        f"· VALOR SALVO: USD {gmv_salvo:,.2f}\n"
        f"· EFICIÊNCIA: {eficiencia:.2f}%"
    )

    _postar_gchat(msg)
    print(f"  Relatório de ciclo enviado (eficiência: {eficiencia:.1f}%)")

    return ids_resolvidos


def _postar_gchat(msg):
    try:
        payload = json.dumps({'text': msg}).encode('utf-8')
        req = urllib.request.Request(
            WEBHOOK_GCHAT,
            data=payload,
            headers={'Content-Type': 'application/json'},
        )
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"  Aviso Google Chat: {e}")


# ============================================================
# EXECUÇÃO PRINCIPAL
# ============================================================

def main():
    inicio = datetime.now(BRT)
    ciclo_label = inicio.strftime('PM %H:%M')
    print(f"\n{'=' * 55}")
    print(f"ALARMA HV SSP30 — {inicio.strftime('%d/%m/%Y %H:%M')} BRT")
    print(f"{'=' * 55}")

    bq_client, gs_client = conectar_google()

    # Busca pacotes HV em sorting
    df = buscar_hv_sorting(bq_client)

    # Carrega planilha
    planilha = gs_client.open_by_key(PLANILHA_CONTROLE_ID)
    ws       = garantir_aba_hv(planilha)

    # Carrega IDs do ciclo anterior (para calcular eficiência)
    ids_anteriores = carregar_ids_ciclo_anterior(ws)
    print(f"  IDs no ciclo anterior: {len(ids_anteriores)}")

    if df.empty:
        print("\n✅ Nenhum pacote HV em sorting sem atrelamento. Nada a alertar.")
        if ids_anteriores:
            ids_resolvidos = enviar_relatorio_ciclo(df, ids_anteriores, ciclo_label)
            marcar_resolvidos(ws, ids_resolvidos)
    else:
        # Filtra apenas pacotes novos (não estavam no ciclo anterior)
        ids_atuais  = set(df['SHP_ID'].astype(str).tolist())
        ids_novos   = ids_atuais - ids_anteriores
        df_novos    = df[df['SHP_ID'].astype(str).isin(ids_novos)] if ids_novos else df

        print(f"  Pacotes novos neste ciclo: {len(df_novos)}")

        # Envia alerta para pacotes novos
        if not df_novos.empty:
            enviar_alerta_gchat(df_novos, ciclo_label)
            registrar_pacotes(ws, df_novos, ciclo_label)

        # Envia relatório de fechamento e marca resolvidos
        if ids_anteriores:
            ids_resolvidos = enviar_relatorio_ciclo(df, ids_anteriores, ciclo_label)
            marcar_resolvidos(ws, ids_resolvidos)

    fim = datetime.now(BRT)
    print(f"\nConcluído em {(fim - inicio).seconds}s")
    print(f"{'=' * 55}\n")


if __name__ == '__main__':
    main()
