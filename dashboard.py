# ============================================================
# dashboard.py — Dashboard de Risco SSP30
# Como rodar: duplo clique em abrir_dashboard.bat
# ============================================================

import streamlit as st
from google.auth import default
import gspread
import pandas as pd
import plotly.express as px
from datetime import datetime

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Risco SSP30",
    page_icon="🔔",
    layout="wide"
)

PLANILHA_CONTROLE_ID = '1rFcUXxl53WVQf_ASRx3mhlEvFoJevcaiwjMZY1vso5Y'
ABA_ON_ROUTE  = 'Tratativas Risco On Route (HV) - Lucas'
ABA_ON_WAY    = 'Tratativas Risco On Way (HV) - Lucas'
ABA_HISTORICO = 'Histórico'
MESES_PT = {1:'jan',2:'fev',3:'mar',4:'abr',5:'mai',6:'jun',
            7:'jul',8:'ago',9:'set',10:'out',11:'nov',12:'dez'}

# ============================================================
# CARREGAMENTO DE DADOS (cache 5 minutos)
# ============================================================
@st.cache_data(ttl=300)
def carregar_dados():
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/cloud-platform',
    ]
    creds, _ = default(scopes=scopes)
    gc       = gspread.authorize(creds)
    planilha = gc.open_by_key(PLANILHA_CONTROLE_ID)

    def ler_aba(nome):
        dados = planilha.worksheet(nome).get_all_values()
        if len(dados) <= 1:
            return pd.DataFrame()
        header = list(dados[0])
        # renomeia colunas duplicadas
        seen = {}
        for i, h in enumerate(header):
            if h in seen:
                seen[h] += 1
                header[i] = f"{h}_{seen[h]}"
            else:
                seen[h] = 0
        df = pd.DataFrame(dados[1:], columns=header)
        return df[df.iloc[:, 2] != ''].reset_index(drop=True)

    df_route = ler_aba(ABA_ON_ROUTE)
    df_way   = ler_aba(ABA_ON_WAY)

    try:
        df_hist = ler_aba(ABA_HISTORICO)
    except Exception:
        df_hist = pd.DataFrame()

    return df_route, df_way, df_hist


def para_float(valor):
    try:
        return float(str(valor).replace(',', '.'))
    except Exception:
        return 0.0


def gmv_serie(df, idx):
    if df.empty or len(df.columns) <= idx:
        return pd.Series(dtype=float)
    return df.iloc[:, idx].apply(para_float)


# ============================================================
# CARREGA E PREPARA
# ============================================================
st.title("🔔 Risco SSP30 — Dashboard")

with st.spinner("Carregando dados da planilha..."):
    df_route, df_way, df_hist = carregar_dados()

agora     = datetime.now()
mes_label = f"{MESES_PT[agora.month]}/{agora.year}"
hoje_str  = agora.strftime('%d/%m/%Y')
mes_ano   = agora.strftime('%m/%Y')

# GMV
gmv_route = gmv_serie(df_route, 22)   # col W (índice 22)
gmv_way   = gmv_serie(df_way,   21)   # col V (índice 21)

# Situation
sit_route = df_route['Situation'].value_counts().to_dict() if not df_route.empty else {}
sit_way   = df_way['Situation'].value_counts().to_dict()   if not df_way.empty   else {}

# CFTV — col Y (índice 24)
cftv_route = int((df_route.iloc[:, 24] == 'Sim').sum()) if not df_route.empty and len(df_route.columns) > 24 else 0
cftv_way   = int((df_way.iloc[:, 24]   == 'Sim').sum()) if not df_way.empty   and len(df_way.columns) > 24   else 0

# Novos hoje — col AF (índice 31)
novos_route = int((df_route.iloc[:, 31] == hoje_str).sum()) if not df_route.empty and len(df_route.columns) > 31 else 0
novos_way   = int((df_way.iloc[:, 31]   == hoje_str).sum()) if not df_way.empty   and len(df_way.columns) > 31   else 0

# Status Caso — col AC (índice 28)
def contar_status(df):
    if df.empty or len(df.columns) <= 28:
        return {}
    return df.iloc[:, 28].value_counts().to_dict()

status_route = contar_status(df_route)
status_way   = contar_status(df_way)

# Junta status das duas abas
status_total = {}
for d in [status_route, status_way]:
    for k, v in d.items():
        status_total[k] = status_total.get(k, 0) + v

em_andamento       = sum(v for k, v in status_total.items() if 'andamento' in k.lower())
pendente           = sum(v for k, v in status_total.items() if 'pendente' in k.lower())
sem_acompanhamento = sum(
    int((df.iloc[:, 28].str.strip() == '').sum())
    for df in [df_route, df_way]
    if not df.empty and len(df.columns) > 28
)

# Histórico do mês
concluidos_mes = 0
removidos_mes  = 0
if not df_hist.empty and len(df_hist.columns) > 6:
    mask_mes  = df_hist.iloc[:, 0].str.contains(mes_ano, na=False)
    mask_conc = df_hist.iloc[:, 6].str.lower().str.contains('conclu', na=False)
    concluidos_mes = int((mask_mes & mask_conc).sum())
    removidos_mes  = int(mask_mes.sum())

# ============================================================
# MÉTRICAS PRINCIPAIS
# ============================================================
st.subheader("📊 Visão Geral")

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("📦 ON ROUTE",          len(df_route),              f"+{novos_route} hoje")
c2.metric("🚛 ON WAY",            len(df_way),                f"+{novos_way} hoje")
c3.metric("💰 GMV ON ROUTE",      f"${gmv_route.sum():,.0f}")
c4.metric("💰 GMV ON WAY",        f"${gmv_way.sum():,.0f}")
c5.metric("📹 CFTV Solicitado",   f"{cftv_route + cftv_way}", f"Route {cftv_route} | Way {cftv_way}")
c6.metric(f"✅ Concluídos {mes_label}", concluidos_mes,        f"{removidos_mes} removidos no mês")

st.divider()

# ============================================================
# GRÁFICOS — SITUATION
# ============================================================
col_esq, col_dir = st.columns(2)

CORES_SITUATION = {
    'Possivel Lost':  '#EF4444',
    'Procurar Pacote':'#F97316',
    '>= 11 dias OW':  '#FBBF24',
    '< 11 dias OW':   '#60A5FA',
}

with col_esq:
    st.subheader("📦 ON ROUTE por Situation")
    if sit_route:
        fig = px.pie(
            values=list(sit_route.values()),
            names=list(sit_route.keys()),
            color=list(sit_route.keys()),
            color_discrete_map=CORES_SITUATION,
            hole=0.35,
        )
        fig.update_traces(textinfo='label+value')
        fig.update_layout(showlegend=False, margin=dict(t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem dados ON ROUTE")

with col_dir:
    st.subheader("🚛 ON WAY por Situation")
    if sit_way:
        fig = px.pie(
            values=list(sit_way.values()),
            names=list(sit_way.keys()),
            color=list(sit_way.keys()),
            color_discrete_map=CORES_SITUATION,
            hole=0.35,
        )
        fig.update_traces(textinfo='label+value')
        fig.update_layout(showlegend=False, margin=dict(t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem dados ON WAY")

st.divider()

# ============================================================
# STATUS DO MÊS
# ============================================================
st.subheader(f"📋 Status dos Casos — {mes_label}")

cs1, cs2, cs3, cs4 = st.columns(4)
cs1.metric("✅ Concluídos no mês", concluidos_mes)
cs2.metric("🔄 Em andamento",      em_andamento)
cs3.metric("⏳ Pendente",           pendente)
cs4.metric("📭 Sem acompanhamento", sem_acompanhamento)

st.divider()

# ============================================================
# TOP 10 GMV
# ============================================================
st.subheader("💰 Top 10 Pacotes por GMV")

top_rows = []
for df, origem, idx_gmv in [(df_route, 'ON ROUTE', 22), (df_way, 'ON WAY', 21)]:
    if df.empty:
        continue
    for _, row in df.iterrows():
        gmv = para_float(row.iloc[idx_gmv])
        if gmv > 0:
            top_rows.append({
                'Origem':          origem,
                'SHP_SHIPMENT_ID': row.iloc[2],
                'Situation':       row.iloc[1],
                'GMV USD':         gmv,
                'Responsável':     row.iloc[0],
                'CFTV':            row.iloc[24] if len(row) > 24 else '',
                'Status Caso':     row.iloc[28] if len(row) > 28 else '',
                'Data Entrada':    row.iloc[31] if len(row) > 31 else '',
            })

if top_rows:
    df_top = (
        pd.DataFrame(top_rows)
        .sort_values('GMV USD', ascending=False)
        .head(10)
        .reset_index(drop=True)
    )
    df_top.index += 1
    df_top['GMV USD'] = df_top['GMV USD'].apply(lambda x: f"${x:,.2f}")
    st.dataframe(df_top, use_container_width=True)
else:
    st.info("Sem dados disponíveis")

# ============================================================
# HISTÓRICO DO MÊS
# ============================================================
if not df_hist.empty and removidos_mes > 0:
    st.divider()
    st.subheader(f"📁 Histórico — Removidos em {mes_label}")

    df_hist_mes = df_hist[df_hist.iloc[:, 0].str.contains(mes_ano, na=False)].copy()
    if not df_hist_mes.empty:
        df_hist_mes.columns = ['Data', 'Origem', 'SHP_SHIPMENT_ID', 'Situation',
                                'GMV USD', 'Responsável', 'Status Caso', 'Finalização'][: len(df_hist_mes.columns)]
        st.dataframe(df_hist_mes.reset_index(drop=True), use_container_width=True, hide_index=True)

# ============================================================
# RODAPÉ
# ============================================================
st.divider()
col_btn, col_info = st.columns([1, 5])
with col_btn:
    if st.button("🔄 Atualizar dados"):
        st.cache_data.clear()
        st.rerun()
with col_info:
    st.caption(
        f"Fonte: Planilha de Controle SSP30 · "
        f"Cache: 5 min · "
        f"Carregado em: {agora.strftime('%d/%m/%Y %H:%M')}"
    )
