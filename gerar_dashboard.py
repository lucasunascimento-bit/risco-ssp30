# ============================================================
# gerar_dashboard.py — Gera o Dashboard HTML do Risco SSP30
# Como rodar: duplo clique em abrir_dashboard_html.bat
# ============================================================

import json, webbrowser, os, unicodedata, re, html as _html
from datetime import datetime
from google.auth import default
from google.cloud import bigquery
import gspread
from google.oauth2 import service_account as _sa_module
from _shared import _SB_DRAG_JS, _FINAL_MAP as _FINAL_HIST_MAP

# ============================================================
# CONFIGURAÇÃO
# ============================================================
PLANILHA_CONTROLE_ID = '1rFcUXxl53WVQf_ASRx3mhlEvFoJevcaiwjMZY1vso5Y'
ABA_ON_ROUTE  = 'Tratativas Risco On Route (HV) - Lucas'
ABA_ON_WAY    = 'Tratativas Risco On Way (HV) - Lucas'
ABA_HISTORICO = 'Histórico'
ABA_SNAPSHOTS = 'Snapshots'
OUTPUT        = os.path.join(os.path.dirname(__file__), 'index.html')
_FRAUDE_HTML  = os.path.join(os.path.dirname(__file__), 'fraude.html')

def _fraude_acumulo_ids():
    """Lê os IDs de drivers que existem no fraude.html (acumulo)."""
    if not os.path.isfile(_FRAUDE_HTML):
        return set()
    with open(_FRAUDE_HTML, 'r', encoding='utf-8', errors='ignore') as f:
        return set(re.findall(r'id="acbl_(\w+)"', f.read()))

PLACES_QUERY = """
SELECT
  CAST(SHP_SHIPMENT_ID AS STRING)                                        AS SHP_SHIPMENT_ID,
  SHP_TRAMO,
  ACTION_DETAIL,
  RISK_CLASIFICATION,
  COALESCE(DAYS_HANDLING_SVC, 0)                                         AS DAYS_HANDLING_SVC,
  COALESCE(DAYS_EXPIRED_PROMISE, 0)                                      AS DAYS_EXPIRED_PROMISE,
  COALESCE(CAST(SHP_ORDER_COST_USD AS FLOAT64), 0)                       AS SHP_ORDER_COST_USD,
  FLAG_BPP,
  FLAG_SYSTEMS_CANCEL,
  FORMAT_DATETIME('%d/%m/%Y %H:%M', SHP_LG_SHIPMENT_CHK_DT)             AS SHP_LG_SHIPMENT_CHK_DT,
  COALESCE(SHP_COMPANY_NAME_LM, SHP_LG_CARRIER_NAME_LH)                 AS CARRIER,
  CAST(SHP_LG_ROUTE_ID_LM AS STRING)                                     AS ROTA_ID,
  SHP_DESTINATION_ID_LM                                                   AS SHP_DESTINATION_ID,
  ATIVO_BUYER_ASSET_RETURN                                                AS RETORNO_ATIVO
FROM `meli-bi-data.WHOWNER.LK_SHP_MISSING_MANAGEMENT_PACKAGES`
WHERE SHP_LG_FACILITY_ID = 'SSP30'
  AND SHP_TRAMO IN ('NEX', 'DC')
ORDER BY SHP_ORDER_COST_USD DESC
"""
AT_STATION_QUERY = """
SELECT
  CAST(SHP_SHIPMENT_ID AS STRING)                                        AS SHP_SHIPMENT_ID,
  SHP_TRAMO,
  COALESCE(ACTION_DETAIL, '')                                            AS ACTION_DETAIL,
  COALESCE(RISK_CLASIFICATION, '')                                       AS RISK_CLASIFICATION,
  COALESCE(CAST(DAYS_HANDLING_SVC AS INT64), 0)                         AS DAYS_HANDLING_SVC,
  COALESCE(CAST(SHP_ORDER_COST_USD AS FLOAT64), 0)                      AS SHP_ORDER_COST_USD,
  COALESCE(CAST(FLAG_BPP AS BOOL), FALSE)                               AS FLAG_BPP,
  COALESCE(SHP_DESTINATION_ID_LM, '')                                   AS SHP_DESTINATION_ID
FROM `meli-bi-data.WHOWNER.LK_SHP_MISSING_MANAGEMENT_PACKAGES`
WHERE SHP_LG_FACILITY_ID = 'SSP30'
  AND SHP_TRAMO = 'AT STATION'
ORDER BY SHP_ORDER_COST_USD DESC
"""
DIT_QUERY = """
WITH dit_dedup AS (
  SELECT
    SHP_SHIPMENT_ID,
    SHP_DESTINATION_FACILITY_ID    AS place_id,
    LM_DESTINATION_FACILITY_TYPE   AS tipo,
    LT_DELAY_CAUSE_L2              AS causa,
    SHP_LG_SUB_STATUS              AS sub_status,
    DATE_DIFF(CURRENT_DATE(), SHP_DATE_HANDLING_ID, DAY) AS dias_parado
  FROM (
    SELECT *,
      ROW_NUMBER() OVER (PARTITION BY SHP_SHIPMENT_ID ORDER BY AUD_UPD_DTTM DESC) AS rn
    FROM `meli-bi-data.WHOWNER.BT_SHP_TRACKER_DELAY_CAUSE_DIT`
    WHERE SHP_SITE_ID = 'MLB'
      AND SHP_DATE_HANDLING_ID >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
      AND SHP_STATUS_ID NOT IN ('delivered','cancelled','not_delivered')
      AND LM_DESTINATION_FACILITY_TYPE IN ('NEX','XPT','DC')
      AND SHP_DESTINATION_FACILITY_ID IS NOT NULL
  )
  WHERE rn = 1
),
missing_ids AS (
  SELECT DISTINCT SHP_SHIPMENT_ID
  FROM `meli-bi-data.WHOWNER.LK_SHP_MISSING_MANAGEMENT_PACKAGES`
  WHERE SIT_SITE_ID = 'MLB'
)
SELECT
  d.place_id,
  d.tipo,
  COUNT(*)                                             AS dit_total,
  COUNTIF(m.SHP_SHIPMENT_ID IS NULL)                  AS dit_blind_spot,
  ROUND(AVG(d.dias_parado), 1)                        AS avg_dias_dit,
  COUNTIF(d.sub_status = 'delivered_place'
          AND m.SHP_SHIPMENT_ID IS NULL)              AS stuck_in_place,
  ARRAY_AGG(
    CASE WHEN m.SHP_SHIPMENT_ID IS NULL THEN CAST(d.SHP_SHIPMENT_ID AS STRING) END
    IGNORE NULLS LIMIT 50
  )                                                    AS blind_ids
FROM dit_dedup d
LEFT JOIN missing_ids m USING (SHP_SHIPMENT_ID)
GROUP BY 1, 2
HAVING COUNT(*) >= 3
"""

QUERY_BRIEFING = """
WITH carrier_map AS (
  SELECT
    CAST(r.SHP_LG_DRIVER_ID AS STRING) AS driver_id,
    MAX(c.SHP_COMPANY_NAME)            AS transportadora
  FROM `meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS_ROUTES` r
  LEFT JOIN `meli-bi-data.WHOWNER.LK_SHP_COMPANIES` c
    ON r.SHP_COMPANY_ID = c.SHP_COMPANY_ID
  WHERE r.SHP_LG_FACILITY_ID = 'SSP30'
    AND DATE(r.SHP_LG_ROUTE_INIT_DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
    AND c.SHP_COMPANY_NAME IS NOT NULL
    AND TRIM(c.SHP_COMPANY_NAME) != ''
  GROUP BY 1
)
SELECT
    CAST(f.SHIPMENT_ID AS STRING)        AS shp_id,
    f.DATE_BPP,
    f.SUBSTATUS_CLASIFICATION            AS tipo_fraude,
    f.CULPABILITY_STD                    AS culpabilidade,
    COALESCE(f.BPP_CASHOUT_USD, 0)       AS bpp_usd,
    COALESCE(f.GMV, 0)                   AS gmv,
    CAST(f.DRIVER_ID AS STRING)          AS driver_id,
    COALESCE(cm.transportadora, '')      AS driver_nome,
    f.DRIVER_STATUS                      AS driver_status,
    f.NODES_LM                           AS place_tipo,
    f.NODE_ID                            AS place_id
FROM `meli-bi-data.WHOWNER.DM_LP_LM_MELI_CAUSE` f
LEFT JOIN carrier_map cm ON CAST(f.DRIVER_ID AS STRING) = cm.driver_id
WHERE f.SHP_LG_FACILITY_ID = 'SSP30'
  AND f.DATE_BPP >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
  AND CAST(f.ISFRAUD AS STRING) = '1'
ORDER BY f.DATE_BPP DESC
LIMIT 10000
"""

MESES_PT      = {1:'jan',2:'fev',3:'mar',4:'abr',5:'mai',6:'jun',
                 7:'jul',8:'ago',9:'set',10:'out',11:'nov',12:'dez'}

# _SB_DRAG_JS importado de _shared.py

# ============================================================
# LEITURA DA PLANILHA
# ============================================================
def carregar():
    scopes = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive',
              'https://www.googleapis.com/auth/cloud-platform']
    _sa_file = os.path.join(os.path.dirname(__file__), 'google_credentials.json')
    if os.path.exists(_sa_file):
        creds = _sa_module.Credentials.from_service_account_file(_sa_file, scopes=scopes)
        print("  Usando service account local (google_credentials.json)")
    else:
        creds, _ = default(scopes=scopes)
    gc = gspread.authorize(creds)
    pl = gc.open_by_key(PLANILHA_CONTROLE_ID)

    def ler(nome):
        try:
            ws = pl.worksheet(nome)
        except Exception:
            abas = [w.title for w in pl.worksheets()]
            raise Exception(f"Aba '{nome}' não encontrada. Abas disponíveis: {abas}")
        rows = ws.get_all_values()
        if len(rows) <= 1:
            return [], []
        # preserva número da linha na planilha (1=header, 2=primeira linha de dados)
        out = []
        for i, r in enumerate(rows[1:], start=2):
            if len(r) > 2 and r[2].strip():
                r = list(r)
                while len(r) < 35:
                    r.append('')
                r.append(i)  # índice -1 = número da linha na planilha
                out.append(r)
        return rows[0], out

    h_rt, rt = ler(ABA_ON_ROUTE)
    h_wy, wy = ler(ABA_ON_WAY)
    try:
        h_hi, hi = ler(ABA_HISTORICO)
    except Exception:
        h_hi, hi = [], []
    try:
        snap_rows = pl.worksheet(ABA_SNAPSHOTS).get_all_values()
        snaps = snap_rows[1:] if len(snap_rows) > 1 else []
    except Exception:
        snaps = []
    return rt, wy, hi, snaps, creds

# ============================================================
# PROCESSAMENTO
# ============================================================
def flt(v):
    try:    return float(str(v).replace(',','.'))
    except: return 0.0

def _processar_snapshots(snaps):
    """Converte linhas brutas da aba Snapshots em séries prontas para o Chart.js."""
    rows = []
    for r in snaps:
        if not r or not r[0].strip():
            continue
        try:
            rows.append({
                'data':      r[0].strip(),
                'gmv_otr':   flt(r[2])  if len(r) > 2  else 0.0,
                'gmv_ow':    flt(r[8])  if len(r) > 8  else 0.0,
                'gmv_total': flt(r[14]) if len(r) > 14 else 0.0,
                'otr_total': flt(r[1])  if len(r) > 1  else 0.0,
                'ow_total':  flt(r[7])  if len(r) > 7  else 0.0,
            })
        except Exception:
            continue
    def _parse_dt(s):
        for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
            try: return datetime.strptime(s.strip(), fmt)
            except: pass
        return datetime.min
    rows.sort(key=lambda r: _parse_dt(r['data']))
    def fmt_eixo(d):
        try: return datetime.strptime(d, '%d/%m/%Y').strftime('%d/%m')
        except: return d
    return {
        'labels':    [fmt_eixo(r['data']) for r in rows],
        'gmv_total': [round(r['gmv_total'], 2) for r in rows],
        'gmv_otr':   [round(r['gmv_otr'],   2) for r in rows],
        'gmv_ow':    [round(r['gmv_ow'],    2) for r in rows],
        'otr_total': [int(r['otr_total']) for r in rows],
        'ow_total':  [int(r['ow_total'])  for r in rows],
    }

def calc_dias(entrada_str):
    try:
        dt = datetime.strptime(entrada_str.strip(), '%d/%m/%Y')
        return (datetime.now() - dt).days
    except:
        return -1

def processar(rt, wy, hi, descricoes=None, cftv_map=None, entregues=None):
    agora      = datetime.now()
    hoje       = agora.strftime('%d/%m/%Y')
    mes_ano    = agora.strftime('%m/%Y')
    mes_lbl    = f"{MESES_PT[agora.month]}/{agora.year}"
    descricoes = descricoes or {}
    cftv_map   = cftv_map   or {}
    entregues  = entregues  or set()

    # ---- ON ROUTE ----
    r_total = len(rt)
    r_gmv   = sum(flt(r[22]) for r in rt if len(r) > 22)
    r_sit   = {}
    r_cftv  = 0
    r_novos = 0
    r_rows  = []
    for r in rt:
        sit = r[1] if len(r) > 1 else ''
        r_sit[sit] = r_sit.get(sit, 0) + 1
        if len(r) > 24 and r[24] == 'Sim': r_cftv += 1
        if len(r) > 31 and r[31] == hoje:  r_novos += 1
        entrada = r[31] if len(r) > 31 else ''
        shp_id_rt = r[2] if len(r) > 2 else ''
        r_rows.append({
            'id':            shp_id_rt,
            'sit':           r[1]  if len(r) > 1  else '',
            'gmv':           flt(r[22]) if len(r) > 22 else 0,
            'resp':          (r[0] if len(r) > 0 else '') or 'Lucas Nascimento',
            'cftv':          r[24] if len(r) > 24 else '',
            'cftv_inv':      cftv_map.get(shp_id_rt),
            'status':        r[28] if len(r) > 28 else '',
            'acao_lp':       r[23] if len(r) > 23 else '',
            'finalizacao':   r[29] if len(r) > 29 else '',
            'cobrar_otr':    r[32] if len(r) > 32 else '',
            'entrada':       entrada,
            'dias_carteira': calc_dias(entrada),
            'descricao':     descricoes.get(shp_id_rt, ''),
            'entregue':      shp_id_rt in entregues,
        })
    r_rows.sort(key=lambda x: -x['gmv'])

    # ---- ON WAY ----
    w_total = len(wy)
    w_gmv   = sum(flt(r[21]) for r in wy if len(r) > 21)
    w_sit   = {}
    w_cftv  = 0
    w_novos = 0
    w_rows  = []
    for r in wy:
        sit = r[1] if len(r) > 1 else ''
        w_sit[sit] = w_sit.get(sit, 0) + 1
        if len(r) > 24 and r[24] == 'Sim': w_cftv += 1
        if len(r) > 31 and r[31] == hoje:  w_novos += 1
        entrada = r[31] if len(r) > 31 else ''
        shp_id_wy = r[2] if len(r) > 2 else ''
        w_rows.append({
            'id':            shp_id_wy,
            'sit':           r[1]  if len(r) > 1  else '',
            'gmv':           flt(r[21]) if len(r) > 21 else 0,
            'dias_ow':       r[12] if len(r) > 12 else '',
            'carrier':       r[13] if len(r) > 13 else '',
            'resp':          (r[0] if len(r) > 0 else '') or 'Lucas Nascimento',
            'cftv':          r[24] if len(r) > 24 else '',
            'cftv_inv':      cftv_map.get(shp_id_wy),
            'status':        r[28] if len(r) > 28 else '',
            'entrada':       entrada,
            'dias_carteira': calc_dias(entrada),
            'acao_lp':       r[22] if len(r) > 22 else '',
            'link_email':    r[23] if len(r) > 23 else '',
            'finalizacao':   r[29] if len(r) > 29 else '',
            'sheet_row':     r[-1] if r else 0,
            'descricao':     descricoes.get(shp_id_wy, ''),
            'entregue':      shp_id_wy in entregues,
        })
    w_rows.sort(key=lambda x: -x['gmv'])
    _car_cnt = {}
    for _r in w_rows:
        _c = (_r.get('carrier') or '').strip()
        if _c and _c.lower() not in ('none', 'nan', ''):
            if _c not in _car_cnt: _car_cnt[_c] = {'n': 0, 'gmv': 0.0}
            _car_cnt[_c]['n']   += 1
            _car_cnt[_c]['gmv'] += _r['gmv']
    carrier_ranking_wy = sorted(_car_cnt.items(), key=lambda x: -x[1]['n'])[:8]

    # ---- Dias médio na carteira (pacotes ativos) ----
    dias_validos = [r['dias_carteira'] for r in r_rows + w_rows if r['dias_carteira'] >= 0]
    dias_medio = round(sum(dias_validos) / len(dias_validos), 1) if dias_validos else 0

    # ---- Status dos casos ----
    status_cnt = {'Em andamento': 0, 'Pendente': 0, 'Sem acomp.': 0}
    for r in rt + wy:
        v = r[28].strip() if len(r) > 28 else ''
        if   'andamento' in v.lower(): status_cnt['Em andamento'] += 1
        elif 'pendente'  in v.lower(): status_cnt['Pendente'] += 1
        else:                          status_cnt['Sem acomp.'] += 1

    # ---- Top GMV (combinado) ----
    top_all = []
    for r in r_rows: top_all.append({'origem': 'ON ROUTE', **r})
    for r in w_rows: top_all.append({'origem': 'ON WAY',   **r})
    top_all.sort(key=lambda x: -x['gmv'])
    top15 = top_all[:15]

    # ---- Críticos (pontuação de risco) ----
    def score_critico(r):
        pts = []
        if r['sit'] in ('Possivel Lost', '>= 11 dias OW'): pts.append('🔴 Possivel Lost / +11d OW')
        if r['gmv'] > 500:                                  pts.append('💰 GMV alto')
        if r['dias_carteira'] > 7:                          pts.append('⏰ +7 dias na carteira')
        return pts

    criticos = []
    for r in top_all:
        motivos = score_critico(r)
        if len(motivos) >= 2:
            criticos.append({**r, 'motivos': motivos})
    criticos.sort(key=lambda x: (-len(x['motivos']), -x['gmv']))

    # ---- Histórico do mês ----
    hist_mes    = [r for r in hi if len(r) > 0 and mes_ano in r[0]]
    concluidos  = sum(1 for r in hist_mes if len(r) > 6 and 'conclu' in r[6].lower())
    def _recuperado(final): f=final.lower(); return any(k in f for k in ('fluxo','revers','localizado','recuperado'))
    def _perdido(final):    f=final.lower(); return any(k in f for k in ('perdido','bpp'))
    recuperados = sum(1 for r in hist_mes if len(r) > 7 and _recuperado(r[7]))
    removidos   = len(hist_mes)
    hist_rows   = [{'data': r[0], 'origem': r[1], 'id': r[2],
                    'sit':  r[3], 'gmv':  r[4],   'resp': r[5],
                    'status': r[6] if len(r) > 6 else '',
                    'final':  r[7] if len(r) > 7 else ''}
                   for r in hist_mes]

    # Histórico completo (todas as datas) para a aba com filtro de mês
    def mes_de(data_str):
        try:
            s = data_str.strip()
            if '-' in s and len(s) == 10:   # YYYY-MM-DD
                return f"{s[5:7]}/{s[:4]}"  # MM/YYYY
            p = s.split('/')
            return f"{p[1]}/{p[2]}"         # DD/MM/YYYY → MM/YYYY
        except: return ''

    hist_todos = []
    for r in hi:
        if not (len(r) > 0 and r[0].strip()): continue
        # Coluna I (índice 8) = "Mês Ref" — preenchida manualmente para lançamentos em bloco
        mes_ref = r[8].strip() if len(r) > 8 else ''
        # Valida formato MM/YYYY; senão usa a Data da coluna A
        if mes_ref and re.match(r'^\d{2}/\d{4}$', mes_ref):
            m = mes_ref
        else:
            m = mes_de(r[0])
        hist_todos.append({
            'data':   r[0], 'origem': r[1] if len(r) > 1 else '',
            'id':     r[2] if len(r) > 2 else '',
            'sit':    r[3] if len(r) > 3 else '',
            'gmv':    r[4] if len(r) > 4 else '',
            'resp':   r[5] if len(r) > 5 else '',
            'status': r[6] if len(r) > 6 else '',
            'final':  r[7] if len(r) > 7 else '',
            'mes':    m,
        })
    # ordena do mais recente para o mais antigo
    def _parse_data(s):
        if not s:
            return datetime.min
        for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(s.strip(), fmt)
            except ValueError:
                pass
        return datetime.min
    hist_todos.sort(key=lambda r: _parse_data(r['data']), reverse=True)
    # meses disponíveis em ordem cronológica
    def lbl_mes(m):
        try:
            dt = datetime.strptime('01/' + m, '%d/%m/%Y')
            return f"{MESES_PT[dt.month]}/{dt.year}"
        except: return m
    meses_hist = [{'val': m, 'lbl': lbl_mes(m)}
                  for m in sorted(set(r['mes'] for r in hist_todos if r['mes']),
                                  key=lambda m: datetime.strptime('01/'+m, '%d/%m/%Y'))]

    # Meta mensal de recupero
    META_RECUPERO = 20

    # Taxa de recupero, GMV recuperado e GMV perdido
    taxa_recupero  = round(recuperados / removidos * 100, 1) if removidos > 0 else 0
    gmv_recuperado = sum(flt(r['gmv']) for r in hist_rows if _recuperado(r['final']))
    gmv_perdido    = sum(flt(r['gmv']) for r in hist_rows if _perdido(r['final']))

    # ---- Comparativo hoje (novos - removidos hoje) ----
    rem_hoje_rt = sum(1 for r in hist_rows if r['data'] == hoje and 'Route' in r['origem'])
    rem_hoje_wy = sum(1 for r in hist_rows if r['data'] == hoje and 'Way'   in r['origem'])
    net_rt = r_novos - rem_hoje_rt
    net_wy = w_novos - rem_hoje_wy

    # ---- Heatmap por dia da semana (ativos + histórico do mês) ----
    dias_labels = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    heatmap = [0] * 7
    # pacotes ativos
    for r in rt + wy:
        entrada = r[31] if len(r) > 31 else ''
        try:
            heatmap[datetime.strptime(entrada.strip(), '%d/%m/%Y').weekday()] += 1
        except: pass
    # histórico do mês
    for r in hist_mes:
        data = r[0] if len(r) > 0 else ''
        try:
            for _fmt in ('%d/%m/%Y', '%Y-%m-%d'):
                try:
                    heatmap[datetime.strptime(data.strip(), _fmt).weekday()] += 1
                    break
                except ValueError:
                    pass
        except: pass

    # ---- Evolução por data de entrada (qtd + GMV) ----
    datas_rt, datas_wy   = {}, {}
    gmv_rt_dt, gmv_wy_dt = {}, {}
    for r in rt:
        d = r[31] if len(r) > 31 else ''
        g = flt(r[22]) if len(r) > 22 else 0
        if d:
            datas_rt[d]  = datas_rt.get(d, 0) + 1
            gmv_rt_dt[d] = gmv_rt_dt.get(d, 0) + g
    for r in wy:
        d = r[31] if len(r) > 31 else ''
        g = flt(r[21]) if len(r) > 21 else 0
        if d:
            datas_wy[d]  = datas_wy.get(d, 0) + 1
            gmv_wy_dt[d] = gmv_wy_dt.get(d, 0) + g
    # ordena cronologicamente (não lexicograficamente)
    datas_todas = sorted(
        set(list(datas_rt.keys()) + list(datas_wy.keys())),
        key=lambda d: datetime.strptime(d, '%d/%m/%Y') if d else datetime.min
    )
    # labels sem o ano para o eixo X (01/06 em vez de 01/06/2026)
    def fmt_eixo(d):
        try: return datetime.strptime(d, '%d/%m/%Y').strftime('%d/%m')
        except: return d
    evo_labels_fmt = [fmt_eixo(d) for d in datas_todas]

    return {
        'gerado':      agora.strftime('%d/%m/%Y %H:%M'),
        'mes_lbl':     mes_lbl,
        'hoje':        hoje,
        # ON ROUTE
        'r_total': r_total, 'r_gmv': r_gmv, 'r_sit': r_sit,
        'r_cftv':  r_cftv,  'r_novos': r_novos, 'r_rows': r_rows,
        # ON WAY
        'w_total': w_total, 'w_gmv': w_gmv, 'w_sit': w_sit,
        'w_cftv':  w_cftv,  'w_novos': w_novos, 'w_rows': w_rows,
        'carrier_ranking_wy': carrier_ranking_wy,
        'w_entregues': sum(1 for r in w_rows if r['entregue']),
        # Geral
        'gmv_total':  r_gmv + w_gmv,
        'cftv_total': r_cftv + w_cftv,
        'status_cnt': status_cnt,
        'top15':      top15,
        'dias_medio': dias_medio,
        # Histórico
        'concluidos': concluidos, 'recuperados': recuperados,
        'removidos':  removidos,  'hist_rows': hist_rows,
        'hist_todos': hist_todos, 'meses_hist': meses_hist,
        'mes_ano':    mes_ano,
        'taxa_recupero': taxa_recupero, 'gmv_recuperado': gmv_recuperado,
        'gmv_perdido': gmv_perdido, 'meta_recupero': META_RECUPERO,
        # Heatmap
        'heatmap_labels': dias_labels, 'heatmap': heatmap,
        # Críticos
        'criticos': criticos,
        # Comparativo
        'net_rt': net_rt, 'net_wy': net_wy,
        # Evolução
        'evo_labels':  evo_labels_fmt,
        'evo_rt':      [datas_rt.get(d, 0)      for d in datas_todas],
        'evo_wy':      [datas_wy.get(d, 0)      for d in datas_todas],
        'evo_gmv_rt':  [round(gmv_rt_dt.get(d, 0), 2) for d in datas_todas],
        'evo_gmv_wy':  [round(gmv_wy_dt.get(d, 0), 2) for d in datas_todas],
    }

# ============================================================
# HELPERS HTML
# ============================================================
def trend(net):
    if net > 0: return f'<span style="color:#EF4444;font-weight:700">▲ +{net} vs ontem</span>'
    if net < 0: return f'<span style="color:#10B981;font-weight:700">▼ {net} vs ontem</span>'
    return '<span style="color:#94a3b8">➡ estável hoje</span>'

def pill(sit):
    cores = {
        'Possivel Lost':   ('#7f1d1d','#fca5a5'),
        'Procurar Pacote': ('#7c3c14','#fdba74'),
        '>= 11 dias OW':   ('#713f12','#fde68a'),
        '< 11 dias OW':    ('#1e3a5f','#93c5fd'),
    }
    bg, fg = cores.get(sit, ('#1f2937','#9ca3af'))
    return f'<span class="pill" style="background:{bg};color:{fg}">{sit}</span>'

def pill_status(s):
    s = s.strip()
    if   not s:                    return '<span class="pill" style="background:#374151;color:#9CA3AF">—</span>'
    elif 'conclu'   in s.lower():  return f'<span class="pill" style="background:#10B981;color:#fff">{s}</span>'
    elif 'andamento'in s.lower():  return f'<span class="pill" style="background:#3B82F6;color:#fff">{s}</span>'
    elif 'pendente' in s.lower():  return f'<span class="pill" style="background:#F59E0B;color:#fff">{s}</span>'
    else:                          return f'<span class="pill" style="background:#6B7280;color:#fff">{s}</span>'

def dias_badge(d):
    if d < 0:   return '<span style="color:#64748b;font-size:11px">—</span>'
    if d >= 8:  cor = '#EF4444'
    elif d >= 4:cor = '#F97316'
    else:       cor = '#10B981'
    return f'<span style="color:{cor};font-weight:700">{d}d</span>'

def row_bg(status, dias, acao=''):
    s = (status or '').strip()
    a = (acao or '').strip()
    if not s:               return 'border-left:3px solid #BA7517;background:rgba(186,117,23,0.06)'
    if dias >= 8 and not a: return 'border-left:3px solid #E24B4A;background:rgba(226,75,74,0.07)'
    return 'border-left:3px solid transparent'

MELI_PKG_URL = 'https://envios.adminml.com/logistics/package-management/package'

def id_link(shp_id):
    return f'<a href="{MELI_PKG_URL}/{shp_id}" target="_blank" class="shp-link">{shp_id}</a>'

CFTV_ST_COR = {
    'Concluído':    '#10b981',
    'Em Andamento': '#3b82f6',
    'SLA Vencido':  '#ef4444',
}

def cftv_cell_merged(cftv_solicitado, cftv_inv):
    """Célula unificada: ícone CFTV + status investigação como subtexto."""
    if cftv_solicitado != 'Sim':
        return '<td style="text-align:center;font-size:13px;color:#6b7280">❌</td>'
    if cftv_inv is None:
        sub = '<div style="font-size:9px;color:#6b7280;margin-top:2px">Não concluída</div>'
    else:
        st  = cftv_inv['status']
        cor = CFTV_ST_COR.get(st, '#9ca3af')
        cl  = cftv_inv.get('conclusao', '')
        cl_html = (f'<div style="font-size:8px;color:#6b7280">{cl}</div>' if cl else '')
        sub = f'<div style="font-size:9px;color:{cor};margin-top:2px">{st}{cl_html}</div>'
    return f'<td style="text-align:center;line-height:1.3">✅{sub}</td>'

def desc_sub(desc):
    if not desc:
        return ''
    short = desc[:45] + '…' if len(desc) > 45 else desc
    return f'<div style="font-size:10px;color:#6b7280;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:160px" title="{desc}">{short}</div>'

def rows_table_rt(rows):
    out = ''
    for r in rows:
        g   = f'${r["gmv"]:,.2f}' if r['gmv'] else '—'
        bg  = row_bg(r.get('status', ''), r['dias_carteira'], r.get('acao_lp', ''))
        out += f'''<tr style="{bg}" class="data-row"
            data-id="{r["id"].lower()}"
            data-sit="{r["sit"].lower()}"
            data-status="{r["status"].lower()}"
            data-resp="{r["resp"].lower()}"
            data-dias="{r["dias_carteira"]}">
            <td style="font-family:monospace;font-size:12px">{id_link(r["id"])}{desc_sub(r.get("descricao",""))}</td>
            <td>{pill(r["sit"])}</td>
            <td style="font-weight:700;color:#10B981">{g}</td>
            <td>{r["resp"] or "—"}</td>
            {cftv_cell_merged(r["cftv"], r.get("cftv_inv"))}
            <td>{ow_status_select(r, tab="rt")}</td>
            <td style="text-align:center">{dias_badge(r["dias_carteira"])}</td>
            <td style="font-size:11px;color:#9CA3AF">{r["entrada"] or "—"}</td>
            <td>{rt_acao_select(r)}</td>
            <td>{ow_final_select(r, tab="rt")}</td>
            <td>{rt_cobrar_select(r)}</td>
        </tr>'''
    return out

OW_STATUS_OPTS = ['', 'Em andamento', 'Concluído', 'Sem acompanhamento', 'Pendente']

def _ow_norm(s):
    """Normaliza string para comparação: minúsculo + remove acentos."""
    s = (s or '').strip().lower()
    return unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode('ascii')
OW_FINAL_OPTS  = ['', 'BPP', 'Reversão', 'Recuperado']
OW_ACAO_SUGEST = ['Cobrado Origem','Aguardando Retorno da Origem','Escalonado para Supervisão',
                  'Sem Retorno da Origem','Pacote Localizado','Em Investigação','BPP Solicitado']
RT_ACAO_OPTS   = ['', 'Cobrar MLP', 'Cobrado MLP', 'Aguardando Retorno MLP',
                  'Escalonado Supervisão', 'BPP Solicitado', 'Em Investigação']
RT_COBRAR_OPTS = ['', 'Aguardando Retorno', 'Cobrado', 'Sem Retorno']

def ow_status_select(r, tab='wy'):
    cur = _ow_norm(r.get('status'))
    display = next((o for o in OW_STATUS_OPTS if o and _ow_norm(o) == cur), None) or r.get('status') or '— Status —'
    opts = ''.join(f'<option value="{o}"{"selected" if _ow_norm(o)==cur else ""}>{o or "— Status —"}</option>' for o in OW_STATUS_OPTS)
    return (f'<div class="ow-edit-wrap"><div class="ow-fake-sel">'
            f'<span class="ow-fake-val">{display}</span><span style="color:#6b7280;font-size:13px;flex-shrink:0">⌄</span>'
            f'<select class="ow-edit ow-real-sel" data-shp="{r["id"]}" data-tab="{tab}" data-col="29" '
            f'onchange="owSalvarSelect(this)" title="Salva automaticamente">{opts}</select>'
            f'</div></div>')

def ow_final_select(r, tab='wy'):
    cur = _ow_norm(r.get('finalizacao'))
    display = next((o for o in OW_FINAL_OPTS if o and _ow_norm(o) == cur), None) or r.get('finalizacao') or '— Final —'
    opts = ''.join(f'<option value="{o}"{"selected" if _ow_norm(o)==cur else ""}>{o or "— Final —"}</option>' for o in OW_FINAL_OPTS)
    return (f'<div class="ow-edit-wrap"><div class="ow-fake-sel">'
            f'<span class="ow-fake-val">{display}</span><span style="color:#6b7280;font-size:13px;flex-shrink:0">⌄</span>'
            f'<select class="ow-edit ow-real-sel" data-shp="{r["id"]}" data-tab="{tab}" data-col="30" '
            f'onchange="owSalvarSelect(this)" title="Salva automaticamente">{opts}</select>'
            f'</div></div>')

def rt_acao_select(r):
    cur = _ow_norm(r.get('acao_lp'))
    display = next((o for o in RT_ACAO_OPTS if o and _ow_norm(o) == cur), None) or r.get('acao_lp') or '— Ação —'
    opts = ''.join(f'<option value="{o}"{"selected" if _ow_norm(o)==cur else ""}>{o or "— Ação —"}</option>' for o in RT_ACAO_OPTS)
    return (f'<div class="ow-edit-wrap"><div class="ow-fake-sel">'
            f'<span class="ow-fake-val">{display}</span><span style="color:#6b7280;font-size:13px;flex-shrink:0">⌄</span>'
            f'<select class="ow-edit ow-real-sel" data-shp="{r["id"]}" data-tab="rt" data-col="24" '
            f'onchange="owSalvarSelect(this)" title="Salva automaticamente">{opts}</select>'
            f'</div></div>')

def rt_cobrar_select(r):
    cur = _ow_norm(r.get('cobrar_otr', ''))
    display = next((o for o in RT_COBRAR_OPTS if o and _ow_norm(o) == cur), None) or r.get('cobrar_otr') or '— Cobrar OTR —'
    opts = ''.join(f'<option value="{o}"{"selected" if _ow_norm(o)==cur else ""}>{o or "— Cobrar OTR —"}</option>' for o in RT_COBRAR_OPTS)
    return (f'<div class="ow-edit-wrap"><div class="ow-fake-sel">'
            f'<span class="ow-fake-val">{display}</span><span style="color:#6b7280;font-size:13px;flex-shrink:0">⌄</span>'
            f'<select class="ow-edit ow-real-sel" data-shp="{r["id"]}" data-tab="rt" data-col="33" '
            f'onchange="owSalvarSelect(this)" title="Salva automaticamente">{opts}</select>'
            f'</div></div>')

def carrier_ranking_html(ranking):
    if not ranking: return ''
    max_n = max(s['n'] for _, s in ranking)
    rows  = ''
    for carrier, s in ranking:
        pct = int(s['n'] / max_n * 100) if max_n else 0
        rows += (
            f'<div style="display:flex;align-items:center;gap:10px;padding:5px 0;border-bottom:1px solid #0f172a">'
            f'<div style="width:130px;font-size:11px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="{carrier}">{carrier}</div>'
            f'<div style="flex:1;background:#0f172a;border-radius:3px;height:8px">'
            f'<div style="width:{pct}%;background:#f97316;height:8px;border-radius:3px"></div></div>'
            f'<div style="width:28px;text-align:right;font-size:11px;font-weight:700;color:#fb923c">{s["n"]}</div>'
            f'<div style="width:70px;text-align:right;font-size:10px;color:#64748b">${s["gmv"]:,.0f}</div>'
            f'</div>'
        )
    return (
        f'<div style="background:#0d1526;border-radius:8px;padding:14px 16px;margin-bottom:14px">'
        f'<div style="font-size:12px;font-weight:700;color:#94a3b8;margin-bottom:10px;letter-spacing:.5px">TRANSPORTADORA OFENSORA — ON WAY</div>'
        f'{rows}'
        f'</div>'
    )

def rows_table_wy(rows):
    import json as _json
    sugest_js = _json.dumps(OW_ACAO_SUGEST, ensure_ascii=False)
    out = ''
    for r in rows:
        g    = f'${r["gmv"]:,.2f}' if r['gmv'] else '—'
        bg   = row_bg(r.get('status', ''), r['dias_carteira'], r.get('acao_lp', ''))
        srow = r.get('sheet_row', 0)
        acao = (r.get('acao_lp') or '').replace('"', '&quot;')
        link = (r.get('link_email') or '').replace('"', '&quot;')
        link_btn = (f'<a href="{r["link_email"]}" target="_blank" class="ow-link-btn" title="Abrir email">↗</a>'
                    if r.get('link_email') else '')
        out += f'''<tr style="{bg}" class="data-row"
            data-id="{r["id"].lower()}"
            data-sit="{r["sit"].lower()}"
            data-status="{r["status"].lower()}"
            data-resp="{r["resp"].lower()}"
            data-dias="{r["dias_carteira"]}">
            <td style="font-family:monospace;font-size:12px">{id_link(r["id"])}{desc_sub(r.get("descricao",""))}</td>
            <td>{pill(r["sit"])}</td>
            <td style="font-weight:700;color:#10B981">{g}</td>
            <td style="text-align:center;font-weight:700;color:#FBBF24">{r["dias_ow"] or "—"}</td>
            <td>{r["carrier"] or "—"}</td>
            {cftv_cell_merged(r["cftv"], r.get("cftv_inv"))}
            <td>{ow_status_select(r, tab='wy')}</td>
            <td style="text-align:center">{dias_badge(r["dias_carteira"])}</td>
            <td style="font-size:11px;color:#9CA3AF">{r["entrada"] or "—"}</td>
            <td>
              <div class="ow-edit-wrap">
                <input class="ow-edit ow-text" type="text" value="{acao}"
                  placeholder="Ação ou escolha ⌄"
                  data-shp="{r['id']}" data-tab="wy" data-col="23"
                  autocomplete="off"
                  oninput="owSugest(this);owAgendar(this)"
                  onblur="owFecharSugest(this);owSalvarImediato(this)"
                  onfocus="owSugest(this)">
                <span class="ow-dd-btn" onclick="owToggleSugest(this.previousElementSibling)">⌄</span>
                <div class="ow-sugest" data-for="{r['id']}">
                  {''.join(f'<div class="ow-sugest-item" onmousedown="owEscolher(event)">{s}</div>' for s in OW_ACAO_SUGEST)}
                </div>
              </div>
            </td>
            <td>
              <div class="ow-edit-wrap">
                <input class="ow-edit ow-text" type="text" value="{link}"
                  placeholder="https://..."
                  data-shp="{r['id']}" data-tab="wy" data-col="24"
                  onblur="owSalvarImediato(this)"
                  oninput="owAgendar(this);owAtualizarLink(this)"
                  style="padding-right:22px">
                {link_btn}
              </div>
            </td>
            <td>{ow_final_select(r, tab='wy')}</td>
        </tr>'''
    return out

def rows_table_top(rows):
    out = ''
    for i, r in enumerate(rows, 1):
        g        = f'${r["gmv"]:,.2f}' if r['gmv'] else '—'
        orig_bg  = '#1D4ED8' if r['origem'] == 'ON ROUTE' else '#065F46'
        bg       = row_bg(r.get('status', ''), r['dias_carteira'], r.get('acao_lp', ''))
        out += f'''<tr style="{bg}">
            <td style="text-align:center;font-weight:700;color:#FFE600">{i}</td>
            <td><span style="background:{orig_bg};color:#fff;padding:2px 7px;border-radius:10px;font-size:11px">{r["origem"]}</span></td>
            <td style="font-family:monospace;font-size:12px">{id_link(r["id"])}{desc_sub(r.get("descricao",""))}</td>
            <td>{pill(r["sit"])}</td>
            <td style="font-weight:700;color:#10B981;font-size:14px">{g}</td>
            <td>{r["resp"] or "—"}</td>
            <td>{pill_status(r["status"])}</td>
            <td style="text-align:center">{dias_badge(r["dias_carteira"])}</td>
        </tr>'''
    return out

def _iso_date(s):
    s = s.strip()
    if '-' in s and len(s) == 10: return s
    try: p = s.split('/'); return f'{p[2]}-{p[1]}-{p[0]}'
    except: return ''

def rows_table_hist(rows):
    out = ''
    for r in rows:
        orig_bg = '#1D4ED8' if 'Route' in r['origem'] else '#065F46'
        g       = f'${flt(r["gmv"]):,.2f}' if r['gmv'] else '—'
        mes     = r.get('mes', '')
        out += f'''<tr class="hist-row" data-mes="{mes}" data-data="{_iso_date(r['data'])}">
            <td style="font-size:12px;color:#9CA3AF">{r["data"]}</td>
            <td><span style="background:{orig_bg};color:#fff;padding:2px 7px;border-radius:4px;font-size:11px">{"ON ROUTE" if "Route" in r["origem"] else "ON WAY"}</span></td>
            <td style="font-family:monospace;font-size:12px">{id_link(r["id"]) if r["id"] else "—"}</td>
            <td>{pill(r["sit"])}</td>
            <td style="color:#10B981;font-weight:600">{g}</td>
            <td>{r["resp"] or "—"}</td>
            <td>{pill_status(r["status"])}</td>
            <td style="font-size:11px;color:#9CA3AF">{r["final"] or "—"}</td>
        </tr>'''
    return out

# ============================================================
# DESCRIÇÕES DE ITEM — BigQuery
# ============================================================
def carregar_descricoes(creds, shp_ids):
    """Busca SHP_ITEM_DESC para cada SHP ID via BT_SHP_SHIPMENTS."""
    clean = [sid for sid in shp_ids if sid and str(sid).strip().isdigit()]
    if not clean:
        return {}
    ids_str = ','.join(clean)
    client  = bigquery.Client(project='meli-bi-data', credentials=creds)
    q = f"""
    SELECT
        CAST(SHP_SHIPMENT_ID AS STRING) AS shp_id,
        (SELECT SHP_ITEM_DESC FROM UNNEST(ITEMS) LIMIT 1) AS item_desc
    FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS`
    WHERE SHP_SHIPMENT_ID IN ({ids_str})
      AND _PARTITIONDATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)
    """
    try:
        return {r['shp_id']: str(r['item_desc'] or '').strip()
                for r in client.query(q).result()}
    except Exception as e:
        print(f"  [AVISO] Descrições falhou: {e}")
        return {}

# ============================================================
# DETECÇÃO DE PACOTES ENTREGUES — BigQuery
# ============================================================
def carregar_entregues(creds, shp_ids):
    """Retorna set de SHP IDs que já foram entregues (status delivered) no BQ."""
    clean = [sid for sid in shp_ids if sid and str(sid).strip().isdigit()]
    if not clean:
        return set()
    ids_str = ','.join(clean)
    client  = bigquery.Client(project='meli-bi-data', credentials=creds)
    q = f"""
    SELECT CAST(SHP_SHIPMENT_ID AS STRING) AS shp_id
    FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS`
    WHERE SHP_SHIPMENT_ID IN ({ids_str})
      AND SHP_STATUS_ID = 'delivered'
      AND _PARTITIONDATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)
    """
    try:
        return {r['shp_id'] for r in client.query(q).result()}
    except Exception as e:
        print(f"  [AVISO] Entregues BQ falhou: {e}")
        return set()

def atualizar_entregues_planilha(creds, wy_raw, entregues):
    """Preenche Status=Concluído, AçãoLP=Pacote Localizado, Link e Final=Reversão
    na planilha ON WAY para pacotes entregues que ainda não estejam como Concluído.
    Também atualiza as linhas in-memory (wy_raw) para o passo seguinte."""
    pendentes = [r for r in wy_raw
                 if len(r) > 2 and r[2] in entregues
                 and _ow_norm(r[28] if len(r) > 28 else '') != 'concluido']
    if not pendentes:
        return 0
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(PLANILHA_CONTROLE_ID).worksheet(ABA_ON_WAY)
    updates = []
    for r in pendentes:
        row  = r[-1]  # número da linha na planilha
        shp_id = r[2]
        link = f'{MELI_PKG_URL}/{shp_id}'
        updates += [
            {'range': f'W{row}', 'values': [['Pacote Localizado']]},
            {'range': f'X{row}', 'values': [[link]]},
            {'range': f'AC{row}', 'values': [['Concluído']]},
            {'range': f'AD{row}', 'values': [['Recuperado']]},
        ]
        # atualiza in-memory para o passo de mover para histórico
        while len(r) < 35:
            r.append('')
        r[22] = 'Pacote Localizado'
        r[23] = link
        r[28] = 'Concluído'
        r[29] = 'Recuperado'
    if updates:
        ws.batch_update(updates, value_input_option='RAW')
    return len(pendentes)

def mover_concluidos_historico(creds, wy_raw, hoje_str):
    """Move para a aba Histórico todas as linhas do ON WAY com Status=Concluído
    e Finalização preenchida; deleta do ON WAY; retorna (n, novas_hi_rows).
    Mapeia 'Reversão'→'Retornou ao fluxo' e 'BPP'→'Perdido' para bater com
    a lógica de contagem do processar()."""
    para_mover = [r for r in wy_raw
                  if _ow_norm(r[28] if len(r) > 28 else '') == 'concluido'
                  and (r[29] if len(r) > 29 else '').strip()]
    if not para_mover:
        return 0, []
    gc      = gspread.authorize(creds)
    pl      = gc.open_by_key(PLANILHA_CONTROLE_ID)
    hist_ws = pl.worksheet(ABA_HISTORICO)
    ow_ws   = pl.worksheet(ABA_ON_WAY)
    novas_sheet = []
    novas_hi    = []
    for r in para_mover:
        final_orig = (r[29] if len(r) > 29 else '').strip()
        final_hist = _FINAL_HIST_MAP.get(final_orig.lower(), final_orig)
        linha = [
            hoje_str,
            'ON WAY',
            r[2]  if len(r) > 2  else '',
            r[1]  if len(r) > 1  else '',
            r[21] if len(r) > 21 else '',
            r[0]  if len(r) > 0  else '',
            r[28] if len(r) > 28 else '',
            final_hist,
        ]
        novas_sheet.append(linha)
        # constrói linha no formato de hi (lista com padding + sheet_row=0)
        hi_row = list(linha)
        while len(hi_row) < 35:
            hi_row.append('')
        hi_row.append(0)
        novas_hi.append(hi_row)
    hist_ws.append_rows(novas_sheet, value_input_option='RAW')
    for row_idx in sorted({r[-1] for r in para_mover}, reverse=True):
        ow_ws.delete_rows(row_idx)
    return len(para_mover), novas_hi


def mover_concluidos_historico_rt(creds, rt_raw, hoje_str):
    """Mesmo que mover_concluidos_historico mas para ON ROUTE (GMV em r[22])."""
    para_mover = [r for r in rt_raw
                  if _ow_norm(r[28] if len(r) > 28 else '') == 'concluido'
                  and (r[29] if len(r) > 29 else '').strip()]
    if not para_mover:
        return 0, []
    gc      = gspread.authorize(creds)
    pl      = gc.open_by_key(PLANILHA_CONTROLE_ID)
    hist_ws = pl.worksheet(ABA_HISTORICO)
    rt_ws   = pl.worksheet(ABA_ON_ROUTE)
    novas_sheet = []
    novas_hi    = []
    for r in para_mover:
        final_orig = (r[29] if len(r) > 29 else '').strip()
        final_hist = _FINAL_HIST_MAP.get(final_orig.lower(), final_orig)
        linha = [
            hoje_str,
            'ON ROUTE',
            r[2]  if len(r) > 2  else '',
            r[1]  if len(r) > 1  else '',
            r[22] if len(r) > 22 else '',
            r[0]  if len(r) > 0  else '',
            r[28] if len(r) > 28 else '',
            final_hist,
        ]
        novas_sheet.append(linha)
        hi_row = list(linha)
        while len(hi_row) < 35:
            hi_row.append('')
        hi_row.append(0)
        novas_hi.append(hi_row)
    hist_ws.append_rows(novas_sheet, value_input_option='RAW')
    for row_idx in sorted({r[-1] for r in para_mover}, reverse=True):
        rt_ws.delete_rows(row_idx)
    return len(para_mover), novas_hi


def atualizar_devolvidos_rt(creds, rt_raw, hoje_str):
    """Detecta linhas ON ROUTE com step 'Devolvido', preenche automaticamente
    Status=Concluído, Finalização=Recuperado, AçãoLP=Seguiu fluxo correto
    na planilha e in-memory (para que mover_concluidos_historico_rt as mova)."""
    pendentes = [r for r in rt_raw
                 if 'devolvido' in _ow_norm(r[1] if len(r) > 1 else '')
                 and _ow_norm(r[28] if len(r) > 28 else '') != 'concluido']
    if not pendentes:
        return 0
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(PLANILHA_CONTROLE_ID).worksheet(ABA_ON_ROUTE)
    updates = []
    for r in pendentes:
        row = r[-1]
        updates += [
            {'range': f'X{row}',  'values': [['Seguiu fluxo correto']]},
            {'range': f'AC{row}', 'values': [['Concluído']]},
            {'range': f'AD{row}', 'values': [['Recuperado']]},
        ]
        while len(r) < 35:
            r.append('')
        r[23] = 'Seguiu fluxo correto'
        r[28] = 'Concluído'
        r[29] = 'Recuperado'
    if updates:
        ws.batch_update(updates, value_input_option='RAW')
    return len(pendentes)

# ============================================================
# PLACES — BigQuery
# ============================================================
def carregar_cftv_status(creds):
    """Retorna dict SHP_ID → {status, conclusao} a partir da planilha CFTV."""
    try:
        gc   = gspread.authorize(creds)
        pl   = gc.open_by_key('18isURInofILBi-RS9YrCQyYcnb6JeU_stNqnspxiqLM')
        data = pl.worksheet('Respostas ao formulário 2').get_all_values()
        if len(data) <= 1:
            return {}
        header = data[0]
        result = {}
        for row in data[1:]:
            r   = dict(zip(header, row))
            shp = str(r.get('Shipment', '')).strip()
            if not shp:
                continue
            s = r.get('Status', '').strip().lower()
            if 'conclu' in s:
                status = 'Concluído'
            elif any(k in s for k in ('expira', 'expid', 'vencid', 'vencido')):
                status = 'SLA Vencido'
            else:
                status = 'Em Andamento'
            result[shp] = {
                'status':    status,
                'conclusao': r.get('Conclusão', '').strip(),
            }
        return result
    except Exception as e:
        print(f"  [AVISO] CFTV status falhou: {e}")
        return {}

def carregar_places(creds):
    client = bigquery.Client(project='meli-bi-data', credentials=creds)
    job    = client.query(PLACES_QUERY)
    return [dict(r) for r in job.result()]

def carregar_at_station(creds):
    client = bigquery.Client(project='meli-bi-data', credentials=creds)
    job    = client.query(AT_STATION_QUERY)
    return [dict(r) for r in job.result()]

def carregar_dit(creds):
    client = bigquery.Client(project='meli-bi-data', credentials=creds)
    job    = client.query(DIT_QUERY)
    return {r['place_id']: dict(r) for r in job.result()}

def carregar_briefing(creds):
    client = bigquery.Client(project='meli-bi-data', credentials=creds)
    job    = client.query(QUERY_BRIEFING)
    rows   = [dict(r) for r in job.result()]
    return rows

def processar_briefing(bq_rows, wy):
    from datetime import date as _date
    hoje    = _date.today()
    hoje_str = hoje.isoformat()
    yr_h, wk_h, _ = hoje.isocalendar()
    current_week   = f'W{wk_h:02d}/{yr_h}'

    def get_week(row):
        dbpp = row.get('DATE_BPP')
        if dbpp is None: return None, None, None
        try:
            d  = dbpp if hasattr(dbpp, 'isocalendar') else _date.fromisoformat(str(dbpp)[:10])
            yr, wk, _ = d.isocalendar()
            return f'W{wk:02d}/{yr}', yr, wk
        except: return None, None, None

    week_data = {}
    for r in bq_rows:
        lbl, yr, wk = get_week(r)
        if lbl is None: continue
        did  = str(r.get('driver_id')    or '').strip(); did  = '' if did  in ('None','nan','') else did
        dnm  = str(r.get('driver_nome')  or '').strip(); dnm  = '' if dnm  in ('None','nan','') else dnm
        dst  = str(r.get('driver_status')or '').strip()
        pt   = str(r.get('place_tipo')   or '').strip()
        if pt in ('None','nan','','ON ROUTE','on_route'): pt = ''
        pid  = str(r.get('place_id')     or '').strip(); pid  = '' if pid  in ('None','nan','') else pid
        bpp  = max(0.0, float(r.get('bpp_usd') or 0))
        shp  = str(r.get('shp_id') or '').strip()

        if lbl not in week_data:
            try:
                mon = _date.fromisocalendar(yr, wk, 1)
                sun = _date.fromisocalendar(yr, wk, 7)
                dr  = f'{mon.day:02d} {MESES_PT[mon.month]}–{sun.day:02d} {MESES_PT[sun.month]}'
            except: dr = lbl
            week_data[lbl] = {'label':lbl,'yr':yr,'wk':wk,'date_range':dr,
                              'n_casos':0,'gmv':0.0,'drv_map':{},'plc_map':{},'shp_set':set()}
        wd = week_data[lbl]
        # n_casos conta SHPs únicos na semana
        if shp and shp not in wd['shp_set']:
            wd['n_casos'] += 1
            wd['gmv']     += bpp
            wd['shp_set'].add(shp)
        elif not shp:
            wd['n_casos'] += 1
            wd['gmv']     += bpp
        if did:
            if did not in wd['drv_map']:
                wd['drv_map'][did] = {'id':did,'nome':dnm,'status':dst,'total':0,'gmv':0.0,'shps':[],'seen':set()}
            else:
                if dnm and not wd['drv_map'][did]['nome']:
                    wd['drv_map'][did]['nome'] = dnm
                if dst and not wd['drv_map'][did]['status']:
                    wd['drv_map'][did]['status'] = dst
            dm = wd['drv_map'][did]
            if shp and shp not in dm['seen']:
                dm['total'] += 1
                dm['gmv']   += bpp
                dm['shps'].append(shp)
                dm['seen'].add(shp)
            elif not shp:
                dm['total'] += 1
                dm['gmv']   += bpp
        if pt:
            key = f'{pt}|{pid}'
            if key not in wd['plc_map']:
                wd['plc_map'][key] = {'tipo':pt,'id':pid,'total':0,'gmv':0.0,'shps':[],'seen':set()}
            pm = wd['plc_map'][key]
            if shp and shp not in pm['seen']:
                pm['total'] += 1
                pm['gmv']   += bpp
                pm['shps'].append(shp)
                pm['seen'].add(shp)
            elif not shp:
                pm['total'] += 1
                pm['gmv']   += bpp

    weeks_sorted = sorted(week_data.values(), key=lambda x: (x['yr'], x['wk']))
    by_week = {}
    for i, wd in enumerate(weeks_sorted):
        lbl     = wd['label']
        top_drv = sorted(wd['drv_map'].values(), key=lambda x: (-x['total'],-x['gmv']))[:8]
        for d in top_drv:
            d['gmv'] = round(d['gmv'], 2)
            d['shps'] = d['shps'][:30]
            d.pop('seen', None)
        top_plc = sorted(wd['plc_map'].values(), key=lambda x: (-x['total'],-x['gmv']))[:8]
        for p in top_plc:
            p['gmv'] = round(p['gmv'], 2)
            p['shps'] = p['shps'][:30]
            p.pop('seen', None)
        prev         = weeks_sorted[i-1] if i > 0 else None
        delta_casos  = wd['n_casos'] - prev['n_casos'] if prev else 0
        delta_gmv    = round(wd['gmv'] - prev['gmv'], 2) if prev else 0.0
        by_week[lbl] = {
            'label':lbl,'date_range':wd['date_range'],
            'n_casos':wd['n_casos'],'gmv':round(wd['gmv'],2),
            'n_drivers':len(wd['drv_map']),'n_places':len(wd['plc_map']),
            'delta_casos':delta_casos,'delta_gmv':delta_gmv,
            'top_drivers':top_drv,'max_drv':top_drv[0]['total'] if top_drv else 1,
            'top_places':top_plc,'max_plc':top_plc[0]['total'] if top_plc else 1,
        }

    casos_hoje = sorted(
        [r for r in bq_rows if str(r.get('DATE_BPP',''))[:10] == hoje_str],
        key=lambda x: -(x.get('bpp_usd') or 0))[:10]

    alto_ow = sorted(
        [{'id':r[2] if len(r)>2 else '','sit':r[1] if len(r)>1 else '',
          'gmv':flt(r[21] if len(r)>21 else 0),'carrier':r[13] if len(r)>13 else '',
          'dias':calc_dias(r[31] if len(r)>31 else '')}
         for r in wy if flt(r[21] if len(r)>21 else 0) > 0],
        key=lambda x: -x['gmv'])

    # ---- Drivers para bloqueio: lê do JSON exportado por analise_fraude.py ----
    # Fonte única de verdade: processar_acumulo_bloqueio() usa DM_LP_MELI_OPTIMIZADO
    # filtrado FRAUD+LOST ON ROUTE — mesmos dados do detalhe em fraude.html.
    _acumulo_json = os.path.join(os.path.dirname(__file__), '_acumulo_bloqueio.json')
    drivers_bloqueio = []
    _json_ok = False
    if os.path.exists(_acumulo_json):
        try:
            with open(_acumulo_json, 'r', encoding='utf-8') as _f:
                _raw = json.load(_f)
            # suporte ao novo formato {periodo: [...]} e ao formato antigo [...]
            _lista = _raw.get('90', _raw) if isinstance(_raw, dict) else _raw
            for c in _lista:
                drivers_bloqueio.append({
                    'id':      str(c.get('id', '')),
                    'nome':    str(c.get('transportadora', '') or c.get('nome', '')),
                    'status':  str(c.get('status', '')),
                    'n_meses': int(c.get('n_meses', 0)),
                    'total':   int(c.get('n_pkgs', 0)),
                    'gmv':     float(c.get('total_bpp', 0)),
                })
            drivers_bloqueio = sorted(drivers_bloqueio, key=lambda x: (-x['n_meses'], -x['gmv']))[:10]
            _json_ok = True
            print(f"  Acúmulo bloqueio: {len(drivers_bloqueio)} drivers (fonte: _acumulo_bloqueio.json)")
        except Exception as _e:
            print(f"  Aviso: falha ao ler _acumulo_bloqueio.json ({_e}), usando fallback BQ")
    if not _json_ok:
        # Fallback: calcula direto do BQ (menos preciso — inclui todos os tipos ISFRAUD=1)
        _drv_acc = {}
        _STATUS_NAO_BLOQ = {'inactive','inativo','bloqueado','blocked','suspendido','suspended'}
        _CLASSES_VALIDAS_BRF = ('FRAUD', 'LOST ON ROUTE')
        for r in bq_rows:
            did = str(r.get('driver_id') or '').strip()
            if not did or did in ('None','nan',''): continue
            cls = str(r.get('tipo_fraude') or '').upper()
            if not any(k in cls for k in _CLASSES_VALIDAS_BRF): continue
            dbpp = r.get('DATE_BPP')
            if dbpp is None: continue
            try:
                d_ref = dbpp if hasattr(dbpp, 'month') else _date.fromisoformat(str(dbpp)[:10])
                mes_key = f'{d_ref.month:02d}/{d_ref.year}'
            except Exception:
                continue
            dnm = str(r.get('driver_nome') or '').strip()
            if dnm in ('None','nan',''): dnm = ''
            dst = str(r.get('driver_status') or '').strip()
            if dst in ('None','nan',''): dst = ''
            bpp_val = max(0.0, float(r.get('bpp_usd') or 0))
            shp = str(r.get('shp_id') or '').strip()
            if did not in _drv_acc:
                _drv_acc[did] = {'id':did,'nome':dnm,'status':dst,'meses':set(),'total':0,'gmv':0.0,'seen':set(),'max_bpp':0.0}
            dm = _drv_acc[did]
            if dnm and not dm['nome']: dm['nome'] = dnm
            if dst and not dm['status']: dm['status'] = dst
            dm['meses'].add(mes_key)
            if shp and shp not in dm['seen']:
                dm['total'] += 1; dm['gmv'] += bpp_val; dm['seen'].add(shp)
                if bpp_val > dm['max_bpp']: dm['max_bpp'] = bpp_val
            elif not shp:
                dm['total'] += 1; dm['gmv'] += bpp_val
                if bpp_val > dm['max_bpp']: dm['max_bpp'] = bpp_val
        drivers_bloqueio = sorted(
            [{'id':v['id'],'nome':v['nome'],'status':v['status'],
              'n_meses':len(v['meses']),'total':v['total'],'gmv':round(v['gmv'],2)}
             for v in _drv_acc.values()
             if len(v['meses']) >= 3
             and v['total'] >= 5
             and (v['gmv'] - v['max_bpp']) >= 300
             and v['status'].lower() not in _STATUS_NAO_BLOQ],
            key=lambda x: (-x['n_meses'], -x['gmv'])
        )[:10]

    cur = by_week.get(current_week, {})
    return {
        'casos_hoje':  casos_hoje,'n_hoje':len(casos_hoje),
        'alto_ow':     alto_ow,
        'by_week':     by_week,
        'weeks':       [w['label'] for w in weeks_sorted],
        'current_week':current_week,
        'total_sem':   cur.get('n_casos',0),'gmv_sem':cur.get('gmv',0.0),
        'n_drivers':   cur.get('n_drivers',0),'n_places':cur.get('n_places',0),
        'drivers_bloqueio': drivers_bloqueio,
    }

def processar_dit_summary(dit_data, place_ids_tracked):
    """Retorna métricas agregadas de DIT apenas para places que monitoramos."""
    total_blind = sum(
        v['dit_blind_spot'] for pid, v in dit_data.items()
        if pid in place_ids_tracked
    )
    total_dit   = sum(
        v['dit_total'] for pid, v in dit_data.items()
        if pid in place_ids_tracked
    )
    total_stuck = sum(
        v['stuck_in_place'] for pid, v in dit_data.items()
        if pid in place_ids_tracked
    )
    return {'blind': total_blind, 'total': total_dit, 'stuck': total_stuck}

def gerar_otr_list(dit_data, place_ids_tracked):
    result = []
    for pid, d in dit_data.items():
        if pid not in place_ids_tracked:
            continue
        blind = int(d.get('dit_blind_spot') or 0)
        avg   = float(d.get('avg_dias_dit') or 0)
        stuck = int(d.get('stuck_in_place') or 0)
        tipo  = str(d.get('tipo') or '')
        if (blind >= 50 and avg >= 7) or stuck >= 20:
            nivel = 'IMEDIATO'
        elif blind >= 20 and avg >= 5:
            nivel = 'MONITORAMENTO'
        elif blind >= 10 or stuck >= 10:
            nivel = 'OBSERVAR'
        else:
            continue
        ids = list(d.get('blind_ids') or [])
        result.append({'place_id': pid, 'tipo': tipo, 'blind': blind,
                       'avg': avg, 'stuck': stuck, 'nivel': nivel, 'ids': ids})
    ordem = {'IMEDIATO': 0, 'MONITORAMENTO': 1, 'OBSERVAR': 2}
    return sorted(result, key=lambda x: (ordem[x['nivel']], -x['blind']))

def rows_otr_section(otr_list):
    if not otr_list:
        return '<p style="padding:16px;color:#4b5563;font-size:12px">Nenhum place com alerta DIT no momento.</p>'
    out = ''
    nivel_cfg = {
        'IMEDIATO':     ('Imediato',     '#7f1d1d', '#fca5a5', '#1a0808'),
        'MONITORAMENTO':('Monitorar',    '#713f12', '#fde68a', '#160f04'),
        'OBSERVAR':     ('Observar',     '#1f2937', '#9ca3af', ''),
    }
    for p in otr_list:
        nivel    = p['nivel']
        lbl, cl, bg_txt, row_bg_color = nivel_cfg[nivel]
        safe_pid = p['place_id'].replace(' ', '_')
        badge    = (f'<span style="background:{cl};color:{bg_txt};font-size:10px;'
                    f'font-weight:700;padding:2px 8px;border-radius:4px">{lbl}</span>')
        avg_txt  = f'{p["avg"]:.1f}d'
        stuck_cell = (f'<span style="color:#f87171;font-weight:600">{p["stuck"]}</span>'
                      if p['stuck'] else '<span style="color:#374151">—</span>')
        tipo_color = '#60a5fa' if p['tipo'] == 'NEX' else '#a78bfa'
        ids = p.get('ids', [])
        id_chips = ''.join(
            f'<a href="{MELI_PKG_URL}/{sid}" target="_blank" '
            f'style="font-family:monospace;font-size:11px;color:#60a5fa;background:#0d1321;'
            f'padding:3px 8px;border-radius:4px;border:1px solid #1f2937;text-decoration:none">'
            f'{sid}</a>'
            for sid in ids
        )
        cap_note = '  (até 50 exibidos)' if len(ids) >= 50 else ''
        detail_row = (
            f'<tr id="otr-d-{safe_pid}" style="display:none">'
            f'<td colspan="7" style="padding:8px 16px 12px;background:#060c18;border-bottom:1px solid #1f2937">'
            f'<div style="font-size:10px;color:#4b5563;margin-bottom:8px;font-weight:600;'
            f'text-transform:uppercase;letter-spacing:.5px">'
            f'{len(ids)} SHP ID(s) sem flag · {p["place_id"]}{cap_note}'
            f'</div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:6px">{id_chips}</div>'
            f'</td></tr>'
        )
        row_style = f'background:{row_bg_color}' if row_bg_color else ''
        out += (
            f'<tr style="{row_style};cursor:pointer" onclick="toggleOtrRow(\'{safe_pid}\')">'
            f'<td>{badge}</td>'
            f'<td style="font-family:monospace;font-size:12px;color:#e2e8f0;font-weight:600">{p["place_id"]}</td>'
            f'<td style="color:{tipo_color};font-size:11px;font-weight:600">{p["tipo"]}</td>'
            f'<td style="text-align:center;font-weight:700;color:#fbbf24">{p["blind"]}</td>'
            f'<td style="text-align:center;color:#94a3b8">{avg_txt}</td>'
            f'<td style="text-align:center">{stuck_cell}</td>'
            f'<td id="otr-btn-{safe_pid}" style="color:#374151;font-size:16px;text-align:right;'
            f'padding-right:10px;user-select:none">›</td>'
            f'</tr>{detail_row}'
        )
    return out

def gerar_otr_txt(otr_list, hoje):
    imediatos = [p for p in otr_list if p['nivel'] == 'IMEDIATO']
    monitora  = [p for p in otr_list if p['nivel'] == 'MONITORAMENTO']
    lines = [f'OTR - Places com acumulo DIT ({hoje})', '']
    if imediatos:
        lines.append('ACAO IMEDIATA:')
        for p in imediatos:
            lines.append(f'  {p["place_id"]} ({p["tipo"]}) - {p["blind"]} pcts / {p["avg"]:.1f}d avg'
                         + (f' / {p["stuck"]} presos' if p["stuck"] else ''))
            ids = p.get('ids', [])
            if ids:
                preview = ', '.join(ids[:10])
                suffix = '...' if len(ids) > 10 else ''
                lines.append(f'  IDs: {preview}{suffix}')
    if monitora:
        lines.append('')
        lines.append('MONITORAMENTO:')
        for p in monitora:
            lines.append(f'  {p["place_id"]} ({p["tipo"]}) - {p["blind"]} pcts / {p["avg"]:.1f}d avg')
            ids = p.get('ids', [])
            if ids:
                preview = ', '.join(ids[:5])
                suffix = '...' if len(ids) > 5 else ''
                lines.append(f'  IDs: {preview}{suffix}')
    lines += ['', 'Solicito verificacao de status e retorno com motivo do atraso.']
    return '\n'.join(lines)

RISK_LABEL = {'CRITICO': 'Crítico', 'ALTO': 'Alto', 'MODERADO': 'Moderado'}
def norm_risk(val):
    v = str(val or '').strip().upper()
    return RISK_LABEL.get(v, val.capitalize() if val else '—')

def extract_acao(action_detail):
    if not action_detail:
        return '—'
    parts = str(action_detail).split('|')
    return parts[-1].strip()

def processar_places(rows, dit_data=None):
    dit_data = dit_data or {}
    total    = len(rows)
    gmv_tot  = sum(float(r.get('SHP_ORDER_COST_USD') or 0) for r in rows)
    nex_rows = [r for r in rows if r.get('SHP_TRAMO') == 'NEX']
    dc_rows  = [r for r in rows if r.get('SHP_TRAMO') == 'DC']

    risk_cnt, acao_cnt, acao_gmv = {}, {}, {}
    for r in rows:
        rk = norm_risk(r.get('RISK_CLASIFICATION') or '')
        risk_cnt[rk] = risk_cnt.get(rk, 0) + 1
        a  = extract_acao(r.get('ACTION_DETAIL') or '')
        acao_cnt[a] = acao_cnt.get(a, 0) + 1
        acao_gmv[a] = acao_gmv.get(a, 0) + float(r.get('SHP_ORDER_COST_USD') or 0)

    ranking   = processar_places_ranking(rows, dit_data)
    place_ids = {p['place_id'] for p in ranking}
    dit_sum   = processar_dit_summary(dit_data, place_ids)
    otr_list  = gerar_otr_list(dit_data, place_ids)
    hoje      = datetime.now().strftime('%d/%m/%Y')

    return {
        'total': total, 'gmv_total': round(gmv_tot, 2),
        'nex': len(nex_rows), 'dc':  len(dc_rows),
        'gmv_nex': round(sum(float(r.get('SHP_ORDER_COST_USD') or 0) for r in nex_rows), 2),
        'gmv_dc':  round(sum(float(r.get('SHP_ORDER_COST_USD') or 0) for r in dc_rows),  2),
        'critico':  risk_cnt.get('Crítico',  0),
        'alto':     risk_cnt.get('Alto',     0),
        'moderado': risk_cnt.get('Moderado', 0),
        'acao_cnt': acao_cnt,
        'acao_gmv': {k: round(v, 2) for k, v in acao_gmv.items()},
        'dit_blind': dit_sum['blind'],
        'dit_total': dit_sum['total'],
        'dit_stuck': dit_sum['stuck'],
        'otr_list':  otr_list,
        'otr_txt':   gerar_otr_txt(otr_list, hoje),
        'otr_imediato': sum(1 for p in otr_list if p['nivel'] == 'IMEDIATO'),
        'rows': rows,
        'ranking': ranking,
    }

def processar_at_station(rows):
    total   = len(rows)
    gmv_tot = sum(float(r.get('SHP_ORDER_COST_USD') or 0) for r in rows)
    criticos = sum(1 for r in rows if int(r.get('DAYS_HANDLING_SVC') or 0) > 10)
    bpp_ct   = sum(1 for r in rows if r.get('FLAG_BPP'))

    buckets_def = [
        ('≤3d',   lambda d: d <= 3),
        ('4-7d',  lambda d: 4 <= d <= 7),
        ('8-10d', lambda d: 8 <= d <= 10),
        ('11-30d',lambda d: 11 <= d <= 30),
        ('31d+',  lambda d: d >= 31),
    ]
    aging = []
    for label, fn in buckets_def:
        matched = [r for r in rows if fn(int(r.get('DAYS_HANDLING_SVC') or 0))]
        gmv_b = sum(float(r.get('SHP_ORDER_COST_USD') or 0) for r in matched)
        aging.append({'label': label, 'count': len(matched), 'gmv': round(gmv_b, 2)})

    acao_cnt, acao_gmv = {}, {}
    for r in rows:
        a = extract_acao(r.get('ACTION_DETAIL') or '') or '—'
        acao_cnt[a] = acao_cnt.get(a, 0) + 1
        acao_gmv[a] = acao_gmv.get(a, 0) + float(r.get('SHP_ORDER_COST_USD') or 0)
    acao = sorted(
        [{'label': k, 'count': v, 'gmv': round(acao_gmv[k], 2)} for k, v in acao_cnt.items()],
        key=lambda x: -x['count']
    )

    risk_cnt, risk_gmv = {}, {}
    for r in rows:
        rk = norm_risk(r.get('RISK_CLASIFICATION') or '') or '—'
        risk_cnt[rk] = risk_cnt.get(rk, 0) + 1
        risk_gmv[rk] = risk_gmv.get(rk, 0) + float(r.get('SHP_ORDER_COST_USD') or 0)
    risk = sorted(
        [{'label': k, 'count': v, 'gmv': round(risk_gmv[k], 2)} for k, v in risk_cnt.items()],
        key=lambda x: -x['count']
    )

    tramo_cnt, tramo_gmv = {}, {}
    for r in rows:
        t = r.get('SHP_TRAMO') or '—'
        tramo_cnt[t] = tramo_cnt.get(t, 0) + 1
        tramo_gmv[t] = tramo_gmv.get(t, 0) + float(r.get('SHP_ORDER_COST_USD') or 0)
    tramos = sorted(
        [{'label': k, 'count': v, 'gmv': round(tramo_gmv[k], 2)} for k, v in tramo_cnt.items()],
        key=lambda x: -x['count']
    )

    top_pkgs = [
        {
            'shp_id': r['SHP_SHIPMENT_ID'],
            'tramo': r.get('SHP_TRAMO') or '—',
            'acao': extract_acao(r.get('ACTION_DETAIL') or '') or '—',
            'risk': norm_risk(r.get('RISK_CLASIFICATION') or '') or '—',
            'dias': int(r.get('DAYS_HANDLING_SVC') or 0),
            'usd': float(r.get('SHP_ORDER_COST_USD') or 0),
        }
        for r in rows[:50]
    ]

    return {
        'total':    total,
        'gmv_total': round(gmv_tot, 2),
        'criticos': criticos,
        'bpp_ct':   bpp_ct,
        'aging':    aging,
        'acao':     acao,
        'risk':     risk,
        'tramos':   tramos,
        'top_pkgs': top_pkgs,
    }

def processar_places_ranking(rows, dit_data=None):
    dit_data = dit_data or {}
    places = {}
    for r in rows:
        pid   = str(r.get('SHP_DESTINATION_ID') or 'N/A')
        tramo = str(r.get('SHP_TRAMO') or '')
        if pid not in places:
            places[pid] = {'tramo': tramo, 'qtd': 0, 'gmv': 0.0, 'dias': [], 'pkgs': []}
        places[pid]['qtd']  += 1
        places[pid]['gmv']  += float(r.get('SHP_ORDER_COST_USD') or 0)
        places[pid]['dias'].append(int(r.get('DAYS_HANDLING_SVC') or 0))
        places[pid]['pkgs'].append({
            'id':   str(r.get('SHP_SHIPMENT_ID') or ''),
            'gmv':  float(r.get('SHP_ORDER_COST_USD') or 0),
            'acao': extract_acao(r.get('ACTION_DETAIL') or ''),
            'dias': int(r.get('DAYS_HANDLING_SVC') or 0),
            'risk': norm_risk(r.get('RISK_CLASIFICATION') or ''),
        })
    result = []
    for pid, d in places.items():
        qtd     = d['qtd']
        gmv     = round(d['gmv'], 2)
        max_d   = max(d['dias']) if d['dias'] else 0
        avg_d   = round(sum(d['dias']) / len(d['dias']), 1) if d['dias'] else 0
        gmv_pkg = round(gmv / qtd, 2) if qtd else 0
        pkgs_sorted = sorted(d['pkgs'], key=lambda x: x['gmv'], reverse=True)
        dit = dit_data.get(pid, {})
        result.append({
            'place_id':      pid,
            'tramo':         d['tramo'],
            'qtd':           qtd,
            'gmv':           gmv,
            'gmv_pkg':       gmv_pkg,
            'max_dias':      max_d,
            'avg_dias':      avg_d,
            'pkgs':          pkgs_sorted,
            'dit_blind':     int(dit.get('dit_blind_spot', 0)),
            'dit_avg_dias':  float(dit.get('avg_dias_dit', 0) or 0),
            'dit_stuck':     int(dit.get('stuck_in_place', 0)),
        })
    return sorted(result, key=lambda x: x['gmv'], reverse=True)

ROTA_URL_PLACES = 'https://envios.adminml.com/logistics/monitoring-distribution/detail/{id}?site=MLB'

def gerar_report_places_txt(dados):
    p        = dados['places']
    ranking  = p['ranking']
    today    = datetime.now().strftime('%d/%m/%Y')
    lines    = [
        f"📍 PLACES SSP30 — {today}",
        "",
        f"📦 {p['total']} SHP parados (NEX + XPT/DC)",
        f"💰 GMV em risco: ${p['gmv_total']:,.2f}",
        f"🔴 Crítico: {p['critico']}  |  🟡 Alto: {p['alto']}  |  Moderado: {p['moderado']}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🏆 TOP PLACES OFENSORES",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for i, pl in enumerate(ranking[:15]):
        tramo_lbl = pl['tramo'] if pl['tramo'] == 'NEX' else 'XPT/DC'
        alert = '  🚨 ALTO VALOR' if pl['gmv_pkg'] >= 300 else ('  ⏰ LONGA ESPERA' if pl['max_dias'] >= 20 else '')
        lines.append(
            f"{i+1}. {pl['place_id']} ({tramo_lbl}) — "
            f"{pl['qtd']} shp  |  ${pl['gmv']:,.2f}{alert}"
        )
    return '\n'.join(lines)

def rows_ranking_places(ranking):
    out = ''
    for i, p in enumerate(ranking):
        tramo     = p['tramo']
        tramo_lbl = tramo if tramo == 'NEX' else 'XPT/DC'
        tramo_bg  = 'rgba(96,165,250,.15)'  if tramo == 'NEX' else 'rgba(167,139,250,.15)'
        tramo_cl  = '#60a5fa'               if tramo == 'NEX' else '#a78bfa'
        tramo_pill = f'<span style="background:{tramo_bg};color:{tramo_cl};padding:1px 7px;border-radius:4px;font-size:11px;font-weight:600">{tramo_lbl}</span>'

        alert = ''
        if p['gmv_pkg'] >= 300:
            alert = '<span style="color:#f87171;font-weight:700">● ALTO VALOR</span>'
        elif p['max_dias'] >= 20:
            alert = '<span style="color:#fbbf24;font-weight:700">● LONGA ESPERA</span>'

        row_bg   = 'background:#1a0808' if p['gmv_pkg'] >= 300 else ('background:#160f04' if p['max_dias'] >= 20 else '')
        safe_pid = p['place_id'].replace(' ', '_')
        rank_badge = f'<span style="background:#1f2937;color:#9ca3af;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px">#{i+1}</span>'

        pkg_chips = ''
        for pkg in p['pkgs']:
            rk_low = pkg['risk'].lower()
            if 'cr' in rk_low:
                risk_bg, risk_cl = '#7f1d1d', '#fca5a5'
            elif 'alt' in rk_low:
                risk_bg, risk_cl = '#713f12', '#fde68a'
            else:
                risk_bg, risk_cl = '#1f2937', '#9ca3af'
            dias_bg = '#7f1d1d' if pkg['dias'] >= 20 else ('#713f12' if pkg['dias'] >= 10 else '#1f2937')
            dias_cl = '#fca5a5' if pkg['dias'] >= 20 else ('#fde68a' if pkg['dias'] >= 10 else '#9ca3af')
            pkg_chips += f'''<div style="display:inline-flex;align-items:center;gap:7px;padding:5px 10px;background:#0d1321;border-radius:6px;border:1px solid #1f2937">
                {id_link(pkg["id"])}
                <span style="color:#10B981;font-size:11px;font-weight:600">${pkg["gmv"]:,.2f}</span>
                <span style="background:{risk_bg};color:{risk_cl};font-size:10px;padding:1px 5px;border-radius:3px">{pkg["risk"]}</span>
                <span style="background:{dias_bg};color:{dias_cl};font-size:10px;padding:1px 5px;border-radius:3px">{pkg["dias"]}d</span>
            </div>'''

        # DIT blind spot cell
        dit_b = p['dit_blind']
        dit_d = p['dit_avg_dias']
        dit_s = p['dit_stuck']
        if dit_b >= 200:
            dit_cl = '#f87171'; dit_bg_row = ';background:#1a0808' if not row_bg else ''
        elif dit_b >= 50:
            dit_cl = '#fbbf24'; dit_bg_row = ''
        else:
            dit_cl = '#4b5563'; dit_bg_row = ''
        if dit_b > 0:
            stuck_txt = f' <span style="color:#94a3b8;font-size:10px">({dit_s} preso)</span>' if dit_s else ''
            dit_cell  = (f'<span style="color:{dit_cl};font-weight:700">{dit_b}</span>'
                         f'<br><span style="color:#6b7280;font-size:10px">{dit_d}d avg</span>'
                         f'{stuck_txt}')
        else:
            dit_cell = '<span style="color:#1f2937">—</span>'

        if row_bg:
            final_bg = row_bg
        elif dit_b >= 200:
            final_bg = 'background:#120a1a'
        else:
            final_bg = ''

        detail_row = f'''<tr id="pdr-{safe_pid}" style="display:none">
            <td colspan="12" style="padding:8px 16px 12px;background:#060c18;border-bottom:1px solid #1f2937">
              <div style="font-size:10px;color:#4b5563;margin-bottom:8px;font-weight:600;text-transform:uppercase;letter-spacing:.5px">
                {p["qtd"]} pacote(s) · {p["place_id"]} · ordenados por GMV
              </div>
              <div style="display:flex;flex-wrap:wrap;gap:6px">{pkg_chips}</div>
            </td>
        </tr>'''

        out += f'''<tr style="{final_bg};cursor:pointer" class="rank-row" onclick="togglePlaceRow('{safe_pid}')">
            <td style="text-align:center">{rank_badge}</td>
            <td style="font-family:monospace;font-size:12px;color:#a78bfa;font-weight:600">{p["place_id"]}</td>
            <td>{tramo_pill}</td>
            <td style="text-align:center;font-weight:700;color:#e2e8f0">{p["qtd"]}</td>
            <td style="font-weight:700;color:#10B981">${p["gmv"]:,.2f}</td>
            <td style="font-weight:700;color:#{"f87171" if p["gmv_pkg"]>=300 else "fbbf24" if p["gmv_pkg"]>=100 else "9ca3af"}">${p["gmv_pkg"]:,.2f}</td>
            <td style="text-align:center">{dias_badge(p["max_dias"])}</td>
            <td style="text-align:center">{dias_badge(int(p["avg_dias"]))}</td>
            <td style="text-align:center;line-height:1.3">{dit_cell}</td>
            <td style="font-size:11px">{alert}</td>
            <td id="pbtn-{safe_pid}" style="color:#374151;font-size:16px;padding-right:10px;text-align:right;user-select:none">›</td>
        </tr>{detail_row}'''
    return out

def rows_table_places(rows):
    out = ''
    for r in rows:
        shp_id     = str(r.get('SHP_SHIPMENT_ID') or '')
        tramo      = str(r.get('SHP_TRAMO') or '')
        acao       = extract_acao(r.get('ACTION_DETAIL') or '')
        risk       = norm_risk(r.get('RISK_CLASIFICATION') or '')
        dias       = int(r.get('DAYS_HANDLING_SVC') or 0)
        gmv        = float(r.get('SHP_ORDER_COST_USD') or 0)
        carrier    = str(r.get('CARRIER') or '—')
        rota_id    = str(r.get('ROTA_ID') or '')
        chk_dt     = str(r.get('SHP_LG_SHIPMENT_CHK_DT') or '—')
        bpp        = r.get('FLAG_BPP', False)
        place_id   = str(r.get('SHP_DESTINATION_ID') or '—')
        retorno    = str(r.get('RETORNO_ATIVO') or '—')
        gmv_fmt    = f'${gmv:,.2f}' if gmv else '—'

        tramo_label = tramo if tramo == 'NEX' else 'XPT/DC'
        tramo_bg  = 'rgba(96,165,250,.15)'  if tramo == 'NEX' else 'rgba(167,139,250,.15)'
        tramo_cl  = '#60a5fa'               if tramo == 'NEX' else '#a78bfa'
        tramo_pill = f'<span style="background:{tramo_bg};color:{tramo_cl};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{tramo_label}</span>'

        ret_low = retorno.upper()
        if ret_low == 'SEM RETORNO':
            retorno_cell = f'<span style="color:#f87171;font-size:11px;font-weight:600">{retorno}</span>'
        elif ret_low == 'NÃO RECEBIDO':
            retorno_cell = f'<span style="color:#fbbf24;font-size:11px;font-weight:600">{retorno}</span>'
        elif ret_low == 'RECEBIDO':
            retorno_cell = f'<span style="color:#4ade80;font-size:11px;font-weight:600">{retorno}</span>'
        else:
            retorno_cell = f'<span style="color:#6b7280;font-size:11px">{retorno}</span>'

        rk = risk.lower()
        if 'cr' in rk:
            risk_pill = f'<span class="pill" style="background:#7f1d1d;color:#fca5a5">{risk}</span>'
            row_bg    = 'background:#1a0808'
        elif 'alt' in rk:
            risk_pill = f'<span class="pill" style="background:#713f12;color:#fde68a">{risk}</span>'
            row_bg    = 'background:#160f04'
        else:
            risk_pill = f'<span class="pill" style="background:#1f2937;color:#9ca3af">{risk}</span>'
            row_bg    = ''

        if rota_id and rota_id not in ('None', ''):
            rota_cell = f'<a href="{ROTA_URL_PLACES.format(id=rota_id)}" target="_blank" style="color:#4ade80;text-decoration:none;font-family:monospace;font-size:11px">{rota_id}</a>'
        else:
            rota_cell = '—'

        carrier_cell = 'BPP ✓' if bpp else (carrier if carrier not in ('None', '') else '—')

        out += f'''<tr style="{row_bg}" class="pl-row"
            data-id="{shp_id}"
            data-tramo="{tramo.lower()}"
            data-acao="{acao.lower()}"
            data-risk="{rk}">
            <td style="font-family:monospace;font-size:12px">{id_link(shp_id)}</td>
            <td>{tramo_pill}</td>
            <td style="font-family:monospace;font-size:11px;color:#a78bfa">{place_id}</td>
            <td style="font-size:12px;color:#d1d5db">{acao}</td>
            <td>{risk_pill}</td>
            <td style="text-align:center">{dias_badge(dias)}</td>
            <td style="font-weight:700;color:#10B981">{gmv_fmt}</td>
            <td style="text-align:center">{retorno_cell}</td>
            <td style="font-size:11px;color:#9ca3af">{carrier_cell}</td>
            <td>{rota_cell}</td>
            <td style="font-size:10px;color:#64748b">{chk_dt}</td>
        </tr>'''
    return out

# ============================================================
# GERAÇÃO DO HTML
# ============================================================
def filtros_html(tab_id, sits):
    opts_sit = ''.join(f'<option value="{s.lower()}">{s}</option>' for s in sits)
    return f'''
    <div class="filter-bar">
      <input type="text" id="busca_{tab_id}" placeholder="🔍 Buscar por SHP ID ou responsável..."
             oninput="filtrar('{tab_id}')" class="filter-input">
      <select id="sit_{tab_id}" onchange="filtrar('{tab_id}')" class="filter-select">
        <option value="">Todas as Situations</option>{opts_sit}
      </select>
      <select id="status_{tab_id}" onchange="filtrar('{tab_id}')" class="filter-select">
        <option value="">Todos os Status</option>
        <option value="andamento">Em andamento</option>
        <option value="pendente">Pendente</option>
        <option value="conclu">Concluído</option>
        <option value="__sem__">Sem acompanhamento</option>
      </select>
      <button onclick="exportCSV('{tab_id}', 'ssp30_{tab_id}.csv')" class="btn-export">⬇ Exportar CSV</button>
    </div>'''

def _painel_dia_html(b, on_route, on_way):
    from datetime import date as _date
    hoje = _date.today().strftime('%d/%m/%Y')
    urgentes = sorted(
        [r for r in (on_route or []) if r.get('dias_carteira',0) >= 8 and not (r.get('acao_lp') or '').strip()],
        key=lambda x: -x.get('dias_carteira', 0)
    )[:8]
    ow_sem = sorted(
        [r for r in (on_way or []) if not (r.get('status') or '').strip()],
        key=lambda x: -x.get('dias_carteira', 0)
    )[:6]
    cw = b['current_week']
    wd = b['by_week'].get(cw, {})

    # Places: apenas DC / NEX / XPT (exclui categorias de rota como ON WAY, MELI EXTRA)
    top_plc = [p for p in wd.get('top_places', [])
               if p.get('tipo','').upper() in ('DC','NEX','XPT')][:4]

    # Drivers para bloqueio vem pré-calculado do processar_briefing (dados brutos, não top-N por semana)
    bloqueio = b.get('drivers_bloqueio', [])

    def _item(uid, label, sub, tag_txt, tag_col, tab_id=None, ext_href=None):
        tag_style = {
            'r': 'background:#FCEBEB;color:#A32D2D',
            'a': 'background:#FAEEDA;color:#633806',
            'b': 'background:#E6F1FB;color:#0C447C',
        }.get(tag_col, '')
        tag = (f'<span style="{tag_style};font-size:10px;padding:1px 7px;border-radius:8px;white-space:nowrap">'
               f'{tag_txt}</span>') if tag_txt else ''
        _nav_style = ('font-size:10px;color:#60a5fa;cursor:pointer;white-space:nowrap;flex-shrink:0;'
                      'padding:2px 8px;border:1px solid #1f3050;border-radius:6px;text-decoration:none')
        if ext_href:
            nav = f'<a href="{ext_href}" style="{_nav_style}">Ver →</a>'
        elif tab_id:
            nav = f'<span onclick="pdGoTab(\'{tab_id}\')" style="{_nav_style}">Ver →</span>'
        else:
            nav = ''
        _label_txt = _html.escape(re.sub(r'<[^>]+>', '', label))
        return (f'<div class="pd-item">'
                f'<div class="pd-cb" data-id="{uid}" data-label="{_label_txt}" onclick="pdToggle(this)" '
                f'style="width:16px;height:16px;border:1.5px solid #374151;border-radius:4px;flex-shrink:0;'
                f'cursor:pointer;display:flex;align-items:center;justify-content:center"></div>'
                f'<div class="pd-it" style="flex:1;min-width:0">'
                f'<div class="pd-tl" style="font-size:12px;color:#f9fafb;display:flex;align-items:center;'
                f'gap:6px;flex-wrap:wrap">{label} {tag}</div>'
                f'<div style="font-size:11px;color:#6b7280;margin-top:1px">{sub}</div>'
                f'</div>{nav}</div>')

    def _section(icon, title, badge_txt, badge_col, items_html):
        bc = {'r':'#7f1d1d;color:#fca5a5','a':'#78350f;color:#fcd34d','b':'#1e3a5f;color:#93c5fd'}.get(badge_col,'#1f2937;color:#9ca3af')
        return f'''<div style="border:0.5px solid #1a2035;border-radius:10px;margin-bottom:10px;overflow:hidden">
          <div style="display:flex;align-items:center;gap:8px;padding:9px 14px;background:#0d1526;border-bottom:0.5px solid #1a2035">
            <span style="font-size:15px">{icon}</span>
            <span style="font-size:13px;font-weight:600;color:#f9fafb;flex:1">{title}</span>
            <span style="background:{bc};padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500">{badge_txt}</span>
          </div>
          {items_html if items_html else '<div style="padding:10px 14px;font-size:12px;color:#6b7280">Nenhum item pendente</div>'}
        </div>'''

    MELI = 'https://envios.adminml.com/logistics/package-management/package'
    items_rt = ''.join(_item(
        f'rt_{r["id"]}',
        f'<a href="{MELI}/{r["id"]}" target="_blank" style="color:#60a5fa;font-family:monospace">{r["id"]}</a>'
        f' — ${r["gmv"]:,.0f}',
        f'{r.get("sit","—")} · {r.get("descricao","") or "—"}',
        f'{r["dias_carteira"]}d sem ação', 'r' if r['dias_carteira'] >= 12 else 'a',
        tab_id='route')
        for r in urgentes)
    items_ow = ''.join(_item(
        f'ow_{r["id"]}',
        f'<a href="{MELI}/{r["id"]}" target="_blank" style="color:#60a5fa;font-family:monospace">{r["id"]}</a>'
        f' — ${r["gmv"]:,.0f}',
        f'{r.get("sit","—")} · {r.get("carrier","") or "—"}'
        + (f'<div style="margin-top:3px;color:#9ca3af;font-size:10px">{r["descricao"]}</div>'
           if r.get('descricao') else ''),
        f'{r["dias_carteira"]}d', 'r' if r['dias_carteira'] >= 8 else 'a',
        tab_id='way')
        for r in ow_sem)
    _acumulo_ids = _fraude_acumulo_ids()
    items_blq = ''.join(_item(
        f'blq_{d["id"]}',
        f'Driver {d["id"]} — {d["nome"] or "—"}',
        f'{d["n_meses"]} meses · {d["total"]} casos · ${d["gmv"]:,.0f} BPP acumulado',
        f'{d["n_meses"]} meses', 'r' if d['n_meses'] >= 4 else 'a',
        ext_href=f'./fraude.html#acumulo__{d["id"]}' if str(d["id"]) in _acumulo_ids else './fraude.html#acumulo')
        for d in bloqueio)
    items_plc = ''
    for p in top_plc:
        ids_html = (
            '<div style="margin-top:5px;line-height:2">'
            + ' &nbsp;'.join(
                f'<a href="{MELI}/{s}" target="_blank" '
                f'style="color:#60a5fa;font-family:monospace;font-size:10px;text-decoration:none">{s}</a>'
                for s in p['shps']
            )
            + '</div>'
        ) if p['shps'] else ''
        sub_plc = f'{p["total"]} passagens · ${p["gmv"]:,.0f} BPP{ids_html}'
        items_plc += _item(f'plc_{p["id"]}', f'{p["tipo"]} {p["id"]}', sub_plc, f'{p["total"]} casos', 'b',
                           ext_href='./fraude.html#places')

    n_tot = len(urgentes) + len(ow_sem) + len(bloqueio) + len(top_plc)
    sec_rt  = _section('📦', 'ON ROUTE sem Ação LP', f'{len(urgentes)} urgentes',
                        'r' if urgentes else 'a', items_rt)
    sec_ow  = _section('🚚', 'ON WAY sem status', f'{len(ow_sem)} pendentes',
                        'a' if ow_sem else 'b', items_ow)
    sec_blq = _section('🔒', 'Drivers para bloqueio — BPP 3+ meses', f'{len(bloqueio)} drivers',
                        'r' if bloqueio else 'a', items_blq)
    sec_plc = _section('🏭', f'Places DC/NEX com BPP — {cw}', f'{len(top_plc)} places', 'b', items_plc)

    js = '''<script>
const PD_TODAY = new Date().toLocaleDateString('pt-BR');
const PD_KEY = 'pd_' + PD_TODAY;
function pdLoad() {
  const sv = JSON.parse(localStorage.getItem(PD_KEY)||'{}');
  document.querySelectorAll('.pd-cb').forEach(cb => {
    if (sv[cb.dataset.id]) pdMark(cb, true);
  });
  pdProg();
}
function pdMark(cb, on) {
  if (on) {
    cb.style.background='#3B6D11'; cb.style.borderColor='#3B6D11';
    cb.innerHTML='<svg width="9" height="7" viewBox="0 0 9 7"><polyline points="1,3.5 3.5,6 8,1" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  } else {
    cb.style.background=''; cb.style.borderColor='#374151'; cb.innerHTML='';
  }
  const tl = cb.nextElementSibling?.querySelector('.pd-tl');
  if (tl) tl.style.opacity = on ? '0.4' : '1';
}
function pdToggle(cb) {
  const sv = JSON.parse(localStorage.getItem(PD_KEY)||'{}');
  const done = !sv[cb.dataset.id];
  sv[cb.dataset.id] = done;
  localStorage.setItem(PD_KEY, JSON.stringify(sv));
  pdMark(cb, done);
  pdProg();
  const lbl = cb.dataset.label || cb.dataset.id;
  const agora = new Date().toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
  if (done) {
    api('diario_extra', {data: PD_TODAY, atividade: lbl, hora_ini: agora, hora_fim: agora, obs:''}, 'POST').catch(()=>{});
  } else {
    api('diario_delete_extra', {data: PD_TODAY, atividade: lbl}, 'POST').catch(()=>{});
  }
}
function pdProg() {
  const all = document.querySelectorAll('.pd-cb').length;
  const done = [...document.querySelectorAll('.pd-cb')].filter(c=>c.style.background==='rgb(59, 109, 17)').length;
  const pct = all ? Math.round(done/all*100) : 0;
  const bar = document.getElementById('pd-prog'); if(bar) bar.style.width=pct+'%';
  const lbl = document.getElementById('pd-pct'); if(lbl) lbl.textContent=done+' de '+all+' revisados';
}
function pdGoTab(tabName) {
  const el = document.querySelector('.sb-item[data-tab="'+tabName+'"]');
  if (el) { showTab(tabName, el); window.scrollTo({top:0, behavior:'smooth'}); }
}
document.addEventListener('DOMContentLoaded', pdLoad);
</script>'''

    return f'''<div style="margin-bottom:20px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
    <div style="font-size:15px;font-weight:600;color:#f9fafb">Painel do Dia</div>
    <div style="display:flex;align-items:center;gap:10px">
      <span id="pd-pct" style="font-size:12px;color:#6b7280">0 de {n_tot} revisados</span>
      <span style="font-size:12px;color:#4b5563">{hoje}</span>
    </div>
  </div>
  <div style="height:3px;background:#1a2035;border-radius:2px;margin-bottom:14px">
    <div id="pd-prog" style="height:100%;background:#3B6D11;border-radius:2px;width:0%;transition:width .3s"></div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px">
    <div style="background:#0d1526;border-radius:8px;padding:10px 12px"><div style="font-size:20px;font-weight:600;color:#E24B4A">{len(urgentes)}</div><div style="font-size:11px;color:#6b7280;margin-top:2px">ON ROUTE sem ação</div></div>
    <div style="background:#0d1526;border-radius:8px;padding:10px 12px"><div style="font-size:20px;font-weight:600;color:#BA7517">{len(ow_sem)}</div><div style="font-size:11px;color:#6b7280;margin-top:2px">ON WAY sem status</div></div>
    <div style="background:#0d1526;border-radius:8px;padding:10px 12px"><div style="font-size:20px;font-weight:600;color:#E24B4A">{len(bloqueio)}</div><div style="font-size:11px;color:#6b7280;margin-top:2px">Drivers para bloqueio</div></div>
    <div style="background:#0d1526;border-radius:8px;padding:10px 12px"><div style="font-size:20px;font-weight:600;color:#185FA5">{len(top_plc)}</div><div style="font-size:11px;color:#6b7280;margin-top:2px">Places DC/NEX c/ BPP</div></div>
  </div>
  {sec_rt}{sec_ow}{sec_blq}{sec_plc}
  <hr style="border:none;border-top:1px solid #1a2035;margin:18px 0 14px">
</div>
{js}'''

def _briefing_html(b, on_route=None, on_way=None):
    import json as _json
    MELI_URL = 'https://envios.adminml.com/logistics/package-management/package'

    by_week_json = _json.dumps(b['by_week'], ensure_ascii=False, default=str)
    current_week = b['current_week']

    week_opts = ''
    for w in b['weeks']:
        dr  = b['by_week'][w].get('date_range', '')
        sel = 'selected' if w == current_week else ''
        atual = ' — atual' if w == current_week else ''
        week_opts += f'<option value="{w}" {sel}>{w} ({dr}){atual}</option>\n'

    # --- Casos hoje (static) ---
    tipo_cor = {
        'FRAUD':     ('#7f1d1d','#fca5a5'),
        'EMPTY BOX': ('#78350f','#fcd34d'),
        'DAMAGED':   ('#713f12','#fde68a'),
    }
    if b['casos_hoje']:
        c_rows = ''
        for r in b['casos_hoje']:
            tp  = str(r.get('tipo_fraude','') or '').upper()
            cb  = str(r.get('culpabilidade','') or '')
            bg, fg = tipo_cor.get(tp, ('#1f2937','#9ca3af'))
            shp = r.get('shp_id','')
            bpv = float(r.get('bpp_usd') or 0)
            c_rows += (
                f'<tr style="border-top:1px solid #1a2035">'
                f'<td style="padding:6px 10px;font-family:monospace;font-size:12px">'
                f'<a href="{MELI_URL}/{shp}" target="_blank" style="color:#60a5fa;text-decoration:none">{shp}</a></td>'
                f'<td style="padding:6px 10px"><span style="background:{bg};color:{fg};padding:2px 7px;border-radius:10px;font-size:10px">{tp or "—"}</span></td>'
                f'<td style="padding:6px 10px;font-size:11px;color:#9ca3af">{cb}</td>'
                f'<td style="padding:6px 10px;font-weight:700;color:#10b981;text-align:right">${bpv:,.2f}</td>'
                f'</tr>'
            )
        box_hoje = (
            f'<div class="tbl-wrap">'
            f'<div class="tbl-title" style="color:#ef4444">'
            f'<i data-lucide="zap" width="14" height="14" style="color:#ef4444;margin-right:6px;vertical-align:middle"></i>'
            f'BPP Hoje — {b["n_hoje"]} registro(s)</div>'
            f'<div class="tbl-scroll"><table>'
            f'<thead><tr><th>SHP ID</th><th>Tipo</th><th>Culpa</th><th style="text-align:right">BPP USD</th></tr></thead>'
            f'<tbody>{c_rows}</tbody></table></div></div>'
        )
    else:
        box_hoje = '<div class="tbl-wrap"><div style="text-align:center;padding:24px;color:#10b981;font-size:14px">Nenhum BPP registrado hoje ✓</div></div>'

    # --- ON WAY (static) ---
    if b['alto_ow']:
        ow_rows = ''
        for r in b['alto_ow']:
            dc = r['dias']
            dc_cor = '#EF4444' if dc >= 8 else ('#F97316' if dc >= 4 else '#10B981')
            cr = str(r.get('carrier') or '')
            ctxt = cr if cr and cr.lower() not in ('null','none','') else '—'
            ow_rows += (
                f'<tr style="border-top:1px solid #1a2035">'
                f'<td style="padding:6px 10px;font-family:monospace;font-size:12px">'
                f'<a href="{MELI_URL}/{r["id"]}" target="_blank" style="color:#60a5fa;text-decoration:none">{r["id"]}</a></td>'
                f'<td style="padding:6px 10px;font-size:11px;color:#9ca3af">{r["sit"]}</td>'
                f'<td style="padding:6px 10px;font-size:11px;color:#9ca3af">{ctxt}</td>'
                f'<td style="padding:6px 10px;text-align:center"><span style="color:{dc_cor};font-weight:700">{dc}d</span></td>'
                f'<td style="padding:6px 10px;font-weight:700;color:#10b981;text-align:right">${r["gmv"]:,.2f}</td>'
                f'</tr>'
            )
        box_ow = (
            f'<div class="tbl-wrap">'
            f'<div class="tbl-title" style="color:#f97316">'
            f'<i data-lucide="alert-triangle" width="14" height="14" style="color:#f97316;margin-right:6px;vertical-align:middle"></i>'
            f'ON WAY — {len(b["alto_ow"])} pacote(s)</div>'
            f'<div class="tbl-scroll"><table>'
            f'<thead><tr><th>SHP ID</th><th>Situação</th><th>Transportadora</th><th style="text-align:center">Dias</th><th style="text-align:right">GMV</th></tr></thead>'
            f'<tbody>{ow_rows}</tbody></table></div></div>'
        )
    else:
        box_ow = '<div class="tbl-wrap"><div style="text-align:center;padding:24px;color:#10b981;font-size:14px">Nenhum pacote ON WAY</div></div>'

    bpp_hoje_cor = '#EF4444' if b['n_hoje'] else '#10B981'

    return (
        _painel_dia_html(b, on_route, on_way) +
        f'<script>\n'
        f'const BRF_DATA = {by_week_json};\n\n'
        f'function brfDelta(d, isGmv) {{\n'
        f'  if (!d) return \'<span style="color:#4b5563;font-size:10px">—</span>\';\n'
        f'  var up = d > 0;\n'
        f'  var color = up ? \'#ef4444\' : \'#10b981\';\n'
        f'  var arrow = up ? \'▲\' : \'▼\';\n'
        f'  var val = isGmv ? \'$\' + Math.abs(d).toFixed(0) : Math.abs(d);\n'
        f'  return \'<span style="color:\' + color + \';font-size:10px">\' + arrow + \' \' + val + \'</span>\';\n'
        f'}}\n\n'
        f'function brfWeekChange(wk) {{ renderBrfWeek(wk); }}\n\n'
        f'function renderBrfWeek(wk) {{\n'
        f'  var d = BRF_DATA[wk];\n'
        f'  if (!d) return;\n'
        f'  function set(id, v) {{ var el = document.getElementById(id); if (el) el.innerHTML = v; }}\n'
        f'  set(\'brf-c-casos\', d.n_casos);\n'
        f'  set(\'brf-c-gmv\', \'$\' + Number(d.gmv).toLocaleString(\'en-US\', {{maximumFractionDigits:0}}));\n'
        f'  set(\'brf-c-drv\', d.n_drivers);\n'
        f'  set(\'brf-c-plc\', d.n_places);\n'
        f'  set(\'brf-d-casos\', brfDelta(d.delta_casos, false));\n'
        f'  set(\'brf-d-gmv\', brfDelta(d.delta_gmv, true));\n\n'
        f'  var maxDrv = d.max_drv || 1;\n'
        f'  var dh = \'\';\n'
        f'  var meli_url = \'{MELI_URL}\';\n'
        f'  (d.top_drivers || []).forEach(function(drv, i) {{\n'
        f'    var pct = Math.round(drv.total / maxDrv * 100);\n'
        f'    var stCor = drv.status === \'active\' ? \'#10b981\' : \'#6b7280\';\n'
        f'    var uid = \'bdrv\' + i + \'_\' + wk.replace(/[^a-z0-9]/gi, \'\');\n'
        f'    var shpLinks = (drv.shps || []).map(function(s) {{\n'
        f'      return \'<a href="\' + meli_url + \'/\' + s + \'" target="_blank" style="font-family:monospace;font-size:11px;color:#60a5fa;background:#0d1321;padding:2px 7px;border-radius:4px;border:1px solid #1f2937;text-decoration:none">\' + s + \'</a>\';\n'
        f'    }}).join(\'\');\n'
        f'    dh += \'<tr style="border-top:1px solid #1a2035;cursor:pointer" onclick="toggleBrfRow(this)">\'\n'
        f'        + \'<td style="padding:5px 8px;text-align:center;color:#6b7280;font-size:11px">\' + (i+1) + \'</td>\'\n'
        f'        + \'<td style="padding:5px 8px;font-family:monospace;font-size:12px;color:#e2e8f0">\' + (drv.id||\'—\') + \'</td>\'\n'
        f'        + \'<td style="padding:5px 8px;font-size:11px;color:#9ca3af;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="\' + (drv.nome||\'\') + \'">\' + (drv.nome||\'—\') + \'</td>\'\n'
        f'        + \'<td style="padding:5px 8px"><div style="display:flex;align-items:center;gap:5px"><div style="flex:1;background:#1a2035;border-radius:3px;height:5px;overflow:hidden"><div style="background:#ef4444;height:100%;width:\' + pct + \'%"></div></div><span style="font-size:12px;font-weight:700;color:#f9fafb;min-width:18px">\' + drv.total + \'</span></div></td>\'\n'
        f'        + \'<td style="padding:5px 8px;text-align:right;font-size:11px;color:#10b981">$\' + drv.gmv.toFixed(0) + \'</td>\'\n'
        f'        + \'<td style="padding:5px 8px;text-align:center"><span style="color:\' + stCor + \';font-size:10px">●</span> <span style="color:#374151;font-size:10px">›</span></td>\'\n'
        f'        + \'</tr>\'\n'
        f'        + \'<tr class="brf-expand" style="display:none"><td colspan="6" style="padding:8px 14px 12px;background:#060c18;border-bottom:1px solid #1f2937">\'\n'
        f'        + \'<div style="font-size:10px;color:#4b5563;margin-bottom:6px;font-weight:600;text-transform:uppercase">\' + (drv.shps||[]).length + \' SHP(s)</div>\'\n'
        f'        + \'<div style="display:flex;flex-wrap:wrap;gap:4px">\' + shpLinks + \'</div></td></tr>\';\n'
        f'  }});\n'
        f'  set(\'brf-drv-body\', dh || \'<tr><td colspan="6" style="padding:16px;text-align:center;color:#6b7280">Sem drivers</td></tr>\');\n\n'
        f'  var maxPlc = d.max_plc || 1;\n'
        f'  var plcCor = {{"NEx":"#f59e0b","MELI EXTRA":"#a78bfa","DELIVERY CELL":"#60a5fa"}};\n'
        f'  var ph = \'\';\n'
        f'  (d.top_places || []).forEach(function(p, i) {{\n'
        f'    var pct = Math.round(p.total / maxPlc * 100);\n'
        f'    var pc = plcCor[p.tipo] || \'#9ca3af\';\n'
        f'    var plcLinks = (p.shps || []).map(function(s) {{\n'
        f'      return \'<a href="\' + meli_url + \'/\' + s + \'" target="_blank" style="font-family:monospace;font-size:11px;color:#60a5fa;background:#0d1321;padding:2px 7px;border-radius:4px;border:1px solid #1f2937;text-decoration:none">\' + s + \'</a>\';\n'
        f'    }}).join(\'\');\n'
        f'    ph += \'<tr style="border-top:1px solid #1a2035;cursor:pointer" onclick="toggleBrfRow(this)">\'\n'
        f'        + \'<td style="padding:5px 8px;text-align:center;color:#6b7280;font-size:11px">\' + (i+1) + \'</td>\'\n'
        f'        + \'<td style="padding:5px 8px"><span style="color:\' + pc + \';font-size:10px;font-weight:700">\' + p.tipo + \'</span></td>\'\n'
        f'        + \'<td style="padding:5px 8px;font-family:monospace;font-size:11px;color:#6b7280">\' + (p.id||\'—\') + \'</td>\'\n'
        f'        + \'<td style="padding:5px 8px"><div style="display:flex;align-items:center;gap:5px"><div style="flex:1;background:#1a2035;border-radius:3px;height:5px;overflow:hidden"><div style="background:\' + pc + \';height:100%;width:\' + pct + \'%"></div></div><span style="font-size:12px;font-weight:700;color:#f9fafb;min-width:18px">\' + p.total + \'</span></div></td>\'\n'
        f'        + \'<td style="padding:5px 8px;text-align:right;font-size:11px;color:#10b981">$\' + p.gmv.toFixed(0) + \' <span style="color:#374151;font-size:10px">›</span></td>\'\n'
        f'        + \'</tr>\'\n'
        f'        + \'<tr class="brf-expand" style="display:none"><td colspan="5" style="padding:8px 14px 12px;background:#060c18;border-bottom:1px solid #1f2937">\'\n'
        f'        + \'<div style="font-size:10px;color:#4b5563;margin-bottom:6px;font-weight:600;text-transform:uppercase">\' + (p.shps||[]).length + \' SHP(s)</div>\'\n'
        f'        + \'<div style="display:flex;flex-wrap:wrap;gap:4px">\' + plcLinks + \'</div></td></tr>\';\n'
        f'  }});\n'
        f'  set(\'brf-plc-body\', ph || \'<tr><td colspan="5" style="padding:16px;text-align:center;color:#6b7280">Sem places</td></tr>\');\n'
        f'}}\n\n'
        f'function toggleBrfRow(tr) {{\n'
        f'  var next = tr.nextElementSibling;\n'
        f'  if (next && next.classList.contains(\'brf-expand\')) {{\n'
        f'    next.style.display = next.style.display === \'none\' ? \'table-row\' : \'none\';\n'
        f'  }}\n'
        f'}}\n\n'
        f'document.addEventListener(\'DOMContentLoaded\', function() {{ renderBrfWeek(\'{current_week}\'); }});\n'
        f'</script>\n\n'
        f'<div class="cards" style="margin-bottom:14px">\n'
        f'  <div class="card">\n'
        f'    <div class="card-header"><i data-lucide="zap" class="card-icon" width="14" height="14"></i><span class="card-label">BPP Hoje</span></div>\n'
        f'    <div class="card-value" style="color:{bpp_hoje_cor}">{b["n_hoje"]}</div>\n'
        f'    <div class="card-delta">caso(s) com BPP hoje</div>\n'
        f'  </div>\n'
        f'  <div class="card">\n'
        f'    <div class="card-header"><i data-lucide="calendar" class="card-icon" width="14" height="14"></i><span class="card-label">Casos Semana</span></div>\n'
        f'    <div class="card-value" style="color:#f87171" id="brf-c-casos">—</div>\n'
        f'    <div class="card-delta" id="brf-d-casos" style="min-height:14px"></div>\n'
        f'  </div>\n'
        f'  <div class="card">\n'
        f'    <div class="card-header"><i data-lucide="dollar-sign" class="card-icon" width="14" height="14"></i><span class="card-label">BPP Semana</span></div>\n'
        f'    <div class="card-value" style="color:#10b981;font-size:18px" id="brf-c-gmv">—</div>\n'
        f'    <div class="card-delta" id="brf-d-gmv" style="min-height:14px"></div>\n'
        f'  </div>\n'
        f'  <div class="card">\n'
        f'    <div class="card-header"><i data-lucide="user-x" class="card-icon" width="14" height="14"></i><span class="card-label">Drivers</span></div>\n'
        f'    <div class="card-value" style="color:#a78bfa" id="brf-c-drv">—</div>\n'
        f'    <div class="card-delta">identificados na semana</div>\n'
        f'  </div>\n'
        f'  <div class="card">\n'
        f'    <div class="card-header"><i data-lucide="map-pin" class="card-icon" width="14" height="14"></i><span class="card-label">Places</span></div>\n'
        f'    <div class="card-value" style="color:#f59e0b" id="brf-c-plc">—</div>\n'
        f'    <div class="card-delta">nodes com ocorrências</div>\n'
        f'  </div>\n'
        f'</div>\n\n'
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;background:#0d1321;border:1px solid #1f2937;border-radius:8px;padding:10px 14px">\n'
        f'  <i data-lucide="calendar-days" width="14" height="14" style="color:#6b7280"></i>\n'
        f'  <span style="font-size:12px;color:#6b7280">Semana:</span>\n'
        f'  <select onchange="brfWeekChange(this.value)" style="background:#0d1321;color:#e2e8f0;border:1px solid #374151;border-radius:5px;padding:4px 10px;font-size:12px;cursor:pointer">\n'
        f'    {week_opts}'
        f'  </select>\n'
        f'  <span style="font-size:10px;color:#4b5563;margin-left:4px">▲ piorou &nbsp; ▼ melhorou vs semana anterior</span>\n'
        f'</div>\n\n'
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px">\n'
        f'  <div>{box_hoje}</div>\n'
        f'  <div>{box_ow}</div>\n'
        f'</div>\n\n'
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">\n'
        f'  <div class="tbl-wrap">\n'
        f'    <div class="tbl-title" style="color:#a78bfa"><i data-lucide="user-x" width="14" height="14" style="color:#a78bfa;margin-right:6px;vertical-align:middle"></i>Drivers — Semana</div>\n'
        f'    <div class="tbl-scroll"><table>\n'
        f'      <thead><tr><th>#</th><th>Driver ID</th><th>Transportadora</th><th>Casos</th><th style="text-align:right">BPP</th><th></th></tr></thead>\n'
        f'      <tbody id="brf-drv-body"><tr><td colspan="6" style="padding:16px;text-align:center;color:#6b7280">Carregando...</td></tr></tbody>\n'
        f'    </table></div>\n'
        f'  </div>\n'
        f'  <div class="tbl-wrap">\n'
        f'    <div class="tbl-title" style="color:#f59e0b"><i data-lucide="map-pin" width="14" height="14" style="color:#f59e0b;margin-right:6px;vertical-align:middle"></i>Places — Semana</div>\n'
        f'    <div class="tbl-scroll"><table>\n'
        f'      <thead><tr><th>#</th><th>Tipo</th><th>Node ID</th><th>Casos</th><th style="text-align:right">BPP</th></tr></thead>\n'
        f'      <tbody id="brf-plc-body"><tr><td colspan="5" style="padding:16px;text-align:center;color:#6b7280">Carregando...</td></tr></tbody>\n'
        f'    </table></div>\n'
        f'  </div>\n'
        f'</div>'
    )

def _tab_json(rows_rt, rows_wy):
    """Serializa r_rows e w_rows para JSON usado pelo Tabulator."""
    import json as _json

    def _rt(r):
        return {
            'id': r.get('id',''),
            'sit': r.get('sit',''),
            'gmv': round(r.get('gmv',0), 2),
            'resp': r.get('resp',''),
            'status': r.get('status',''),
            'acao_lp': r.get('acao_lp',''),
            'finalizacao': r.get('finalizacao',''),
            'cftv': r.get('cftv',''),
            'cobrar_otr': r.get('cobrar_otr',''),
            'entrada': r.get('entrada',''),
            'dias_carteira': r.get('dias_carteira',-1),
            'entregue': r.get('entregue', False),
            'nota': r.get('nota',''),
        }

    def _wy(r):
        return {
            'id': r.get('id',''),
            'sit': r.get('sit',''),
            'gmv': round(r.get('gmv',0), 2),
            'carrier': r.get('carrier',''),
            'dias_ow': r.get('dias_ow',''),
            'resp': r.get('resp',''),
            'status': r.get('status',''),
            'acao_lp': r.get('acao_lp',''),
            'finalizacao': r.get('finalizacao',''),
            'cftv': r.get('cftv',''),
            'entrada': r.get('entrada',''),
            'dias_carteira': r.get('dias_carteira',-1),
            'entregue': r.get('entregue', False),
        }

    return (
        _json.dumps([_rt(r) for r in rows_rt], ensure_ascii=False),
        _json.dumps([_wy(r) for r in rows_wy], ensure_ascii=False)
    )


def gerar_html(d):
    j = lambda x: json.dumps(x, ensure_ascii=False)
    if 'snapshots' not in d:
        d['snapshots'] = {'labels': [], 'gmv_total': [], 'gmv_otr': [], 'gmv_ow': [], 'otr_total': [], 'ow_total': []}

    # --- Estatísticas mensais para o filtro de período da Visão Geral ---
    def _rec_h(f): fl = f.lower(); return any(k in fl for k in ('fluxo','revers','localizado'))
    def _perd_h(f): fl = f.lower(); return any(k in fl for k in ('perdido','bpp'))
    def _flt_h(v):
        try: return float(str(v).replace(',','.').strip() or 0)
        except: return 0.0
    _monthly_stats = {}
    for _h in d.get('hist_todos', []):
        _m = _h.get('mes', '')
        if not _m: continue
        if _m not in _monthly_stats:
            _monthly_stats[_m] = {'r': 0, 'gmv_r': 0.0, 'gmv_p': 0.0, 'rem': 0,
                                   'rt': 0, 'wy': 0, 'gmv_rt': 0.0, 'gmv_wy': 0.0}
        _f   = _h.get('final', '')
        _org = _h.get('origem', '').lower()
        _g   = _flt_h(_h.get('gmv', '0'))
        _monthly_stats[_m]['rem'] += 1
        if 'route' in _org:
            _monthly_stats[_m]['rt']     += 1
            _monthly_stats[_m]['gmv_rt'] += _g
        elif 'way' in _org:
            _monthly_stats[_m]['wy']     += 1
            _monthly_stats[_m]['gmv_wy'] += _g
        if _rec_h(_f):
            _monthly_stats[_m]['r']     += 1
            _monthly_stats[_m]['gmv_r'] += _g
        elif _perd_h(_f):
            _monthly_stats[_m]['gmv_p'] += _g
    _meses_btn = d.get('meses_hist', [])[-4:]
    _btn_meses = ''.join(
        f'<button class="mes-btn vg-mes-btn{" mes-ativo" if m["val"] == d["mes_ano"] else ""}" '
        f'data-mes="{m["val"]}" onclick="filtrarVisaoMes(\'{m["val"]}\')">{m["lbl"]}</button>'
        for m in _meses_btn
    )
    _meses_lbl_js = j({m['val']: m['lbl'] for m in d.get('meses_hist', [])})

    sit_rt_labels = j(list(d['r_sit'].keys()))
    sit_rt_values = j(list(d['r_sit'].values()))
    sit_wy_labels = j(list(d['w_sit'].keys()))
    sit_wy_values = j(list(d['w_sit'].values()))

    st_labels = j(list(d['status_cnt'].keys()))
    st_values = j(list(d['status_cnt'].values()))

    CORES_SIT = {'Possivel Lost':  'rgba(181,64,64,0.85)',
                 'Procurar Pacote':'rgba(176,112,64,0.85)',
                 '>= 11 dias OW': 'rgba(157,133,48,0.85)',
                 '< 11 dias OW':  'rgba(61,110,168,0.85)'}
    rt_colors = j([CORES_SIT.get(k,'#9CA3AF') for k in d['r_sit'].keys()])
    wy_colors = j([CORES_SIT.get(k,'#9CA3AF') for k in d['w_sit'].keys()])

    sits_rt = list(d['r_sit'].keys())
    sits_wy = list(d['w_sit'].keys())

    d['tab_rt_json'], d['tab_wy_json'] = _tab_json(d['r_rows'], d['w_rows'])
    d['tab_hist_json'] = json.dumps([{
        'data': r.get('data',''), 'origem': r.get('origem',''), 'id': r.get('id',''),
        'sit': r.get('sit',''), 'gmv': r.get('gmv',''), 'resp': r.get('resp',''),
        'status': r.get('status',''), 'final': r.get('final','')
    } for r in d.get('hist_todos', [])], ensure_ascii=False)

    # --- JSON para Tabulator: Críticos ---
    d['tab_criticos_json'] = json.dumps([{
        'id':            r.get('id',''),
        'sit':           r.get('sit',''),
        'origem':        r.get('origem',''),
        'gmv':           round(r.get('gmv',0), 2),
        'dias_carteira': r.get('dias_carteira',-1),
        'status':        r.get('status',''),
        'resp':          r.get('resp',''),
        'motivos':       ', '.join(r.get('motivos',[])),
    } for r in d.get('criticos',[])], ensure_ascii=False)

    # --- JSON para Tabulator: Places ranking ---
    d['tab_places_rank_json'] = json.dumps([{
        'rank':      i + 1,
        'place_id':  p.get('place_id',''),
        'tramo':     p.get('tramo','') if p.get('tramo','') == 'NEX' else 'XPT/DC',
        'qtd':       p.get('qtd',0),
        'gmv':       round(p.get('gmv',0), 2),
        'gmv_pkg':   round(p.get('gmv_pkg',0), 2),
        'max_dias':  p.get('max_dias',0),
        'avg_dias':  float(p.get('avg_dias',0)),
        'dit_blind': p.get('dit_blind',0),
        'pkgs_ids':  ', '.join(pk.get('id','') for pk in p.get('pkgs',[])),
    } for i, p in enumerate(d.get('places',{}).get('ranking',[]))], ensure_ascii=False)

    # --- JSON para Tabulator: Places pacotes individuais ---
    d['tab_places_json'] = json.dumps([{
        'shp_id':   str(r.get('SHP_SHIPMENT_ID') or ''),
        'tramo':    str(r.get('SHP_TRAMO') or '') if str(r.get('SHP_TRAMO') or '') == 'NEX' else 'XPT/DC',
        'place_id': str(r.get('SHP_DESTINATION_ID') or '—'),
        'acao':     extract_acao(r.get('ACTION_DETAIL') or ''),
        'risk':     norm_risk(r.get('RISK_CLASIFICATION') or ''),
        'dias':     int(r.get('DAYS_HANDLING_SVC') or 0),
        'gmv':      round(float(r.get('SHP_ORDER_COST_USD') or 0), 2),
        'retorno':  str(r.get('RETORNO_ATIVO') or '—'),
        'carrier':  'BPP' if r.get('FLAG_BPP') else (str(r.get('CARRIER') or '') or '—'),
        'rota_id':  str(r.get('ROTA_ID') or ''),
        'chk_dt':   str(r.get('SHP_LG_SHIPMENT_CHK_DT') or '—'),
    } for r in d.get('places',{}).get('rows',[])], ensure_ascii=False)

    # --- JSON para Tabulator: AT STATION top packages ---
    d['tab_as_pkgs_json'] = json.dumps([{
        'shp_id': str(p.get('shp_id','')),
        'tramo':  p.get('tramo',''),
        'acao':   p.get('acao',''),
        'risk':   p.get('risk',''),
        'dias':   p.get('dias',0),
        'usd':    round(p.get('usd',0), 2),
    } for p in d.get('at_station',{}).get('top_pkgs',[])], ensure_ascii=False)

    hist_table = (
        "<div class='tbl-scroll'><table id='tbl_hist'><thead><tr>"
        "<th>Data</th><th>Origem</th><th>SHP ID</th><th>Situation</th>"
        "<th>GMV USD</th><th>Responsável</th><th>Status</th><th>Finalização</th>"
        "</tr></thead><tbody>" + rows_table_hist(d["hist_rows"]) + "</tbody></table></div>"
        if d["hist_rows"] else
        '<p style="padding:24px;color:#64748b;text-align:center">Nenhum registro arquivado este mês ainda.</p>'
    )

    _meta_rec  = d.get('meta_recupero', 20)
    _pct_meta  = min(100.0, round(d['recuperados'] / _meta_rec * 100, 1)) if _meta_rec > 0 else 0.0
    _cor_meta  = '#10b981' if _pct_meta >= 100 else '#FFE600' if _pct_meta >= 50 else '#f97316'

    return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#080d19">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Risco SSP30 — Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🔔</text></svg>">
<link rel="manifest" href="/manifest.json">
<script src="config.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/fuse.js@7.0.0/dist/fuse.umd.min.js"></script>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/tabulator-tables/6.3.1/css/tabulator_midnight.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/tabulator-tables/6.3.1/js/tabulator.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#080d19;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;flex-direction:column;height:100vh;overflow:hidden}}
  /* HEADER */
  .header{{background:#080d19;padding:16px 32px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1f2937;flex-shrink:0}}
  .header-brand{{display:flex;align-items:center;gap:12px}}
  .header-accent{{width:3px;height:28px;background:#FFE600;border-radius:2px}}
  .lp-badge{{width:36px;height:36px;background:#FFE600;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:900;color:#080d19;flex-shrink:0;letter-spacing:-.5px}}
  .header-title{{font-size:16px;font-weight:700;color:#ffffff;letter-spacing:-.3px}}
  .header-sub{{font-size:11px;color:#6b7280;margin-top:2px}}
  .ver-badge{{background:rgba(255,230,0,.12);color:#FFE600;border:1px solid rgba(255,230,0,.3);border-radius:4px;font-size:9px;font-weight:700;padding:2px 6px;letter-spacing:.5px;vertical-align:middle}}
  .hdr-stat{{background:#111827;border:1px solid #1f2937;border-radius:6px;padding:4px 10px;font-size:10px;color:#6b7280;white-space:nowrap}}
  .hdr-stat b{{font-weight:700;color:#e2e8f0}}
  .header-right{{display:flex;align-items:center;gap:10px}}
  .status-dot{{width:7px;height:7px;border-radius:50%;background:#FFE600;animation:pulse 2.5s infinite}}
  @keyframes pulse{{0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(255,230,0,.4)}}50%{{opacity:.6;box-shadow:0 0 0 5px rgba(255,230,0,0)}}}}
  @keyframes spin{{from{{transform:rotate(0deg)}}to{{transform:rotate(360deg)}}}}
  .countdown-txt{{font-size:11px;color:#4b5563}}
  /* SIDEBAR */
  .app-body{{display:flex;flex:1;overflow:hidden}}
  .sidebar{{width:220px;flex-shrink:0;background:#060a14;border-right:1px solid #111827;overflow-y:auto;padding:6px 0;display:flex;flex-direction:column}}
  .sb-divider{{height:1px;background:#111827;margin:6px 0;flex-shrink:0}}
  .sb-section-header{{padding:10px 16px 4px;font-size:9px;text-transform:uppercase;letter-spacing:1px;color:#374151;font-weight:700;flex-shrink:0}}
  .sb-item{{display:flex;align-items:center;gap:9px;padding:9px 16px;font-size:12px;color:#6b7280;cursor:pointer;transition:all .2s;border-left:2px solid transparent;white-space:nowrap;flex-shrink:0}}
  .sb-item:hover{{background:#0d1321;color:#e2e8f0}}
  .sb-item.active{{background:linear-gradient(90deg,rgba(255,230,0,.12),transparent);color:#ffffff;border-left-color:#FFE600;font-weight:600}}
  .sb-item.sb-alert{{color:#ef4444!important}}
  .sb-drag-handle{{opacity:0;cursor:grab;margin-right:5px;color:#374151;font-size:14px;flex-shrink:0;user-select:none;transition:opacity .15s}}
  .sb-item:hover .sb-drag-handle{{opacity:1}}
  .sb-item.sb-dragging{{opacity:.35}}
  .sb-item.sb-drop-before{{border-top:2px solid #FFE600!important}}
  .sb-badge{{margin-left:auto;background:rgba(255,230,0,.15);color:#FFE600;font-size:9px;font-weight:700;padding:2px 6px;border-radius:3px;flex-shrink:0}}
  .sb-badge.red{{background:rgba(239,68,68,.2);color:#f87171}}
  .ci{{flex-shrink:0}}
  .main-content{{flex:1;overflow-y:auto}}
  /* CONTENT */
  .content{{display:none;padding:28px 32px}}
  .content.active{{display:block}}
  /* CARDS */
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px;margin-bottom:24px;align-items:stretch}}
  .card{{background:#0d1321;border-radius:8px;padding:18px 20px;border:1px solid #111827;transition:background-color .3s ease,color .3s ease,border-color .3s ease,box-shadow .3s ease,transform .2s ease;cursor:default;display:flex;flex-direction:column;min-height:96px}}
  .card:hover{{border-color:#374151;transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,0.5)}}
  .card-delta{{margin-top:auto;padding-top:6px}}
  .card.card-alert{{border-color:#450a0a;background:#0f0606;padding-left:22px}}
  .card.card-ok{{border-color:#022c22;background:#060f0d;padding-left:22px}}
  .card-header{{display:flex;align-items:center;gap:7px;margin-bottom:14px}}
  .card-icon{{color:#374151;flex-shrink:0}}
  .card-label{{font-size:10px;color:#4b5563;text-transform:uppercase;letter-spacing:.8px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .card-value{{font-size:28px;font-weight:800;color:#ffffff;line-height:1;letter-spacing:-1px}}
  .card-value.val-alert{{color:#ef4444}}
  .card-value.val-ok{{color:#10b981}}
  .card-value.val-warn{{color:#f59e0b}}
  .card-delta{{font-size:11px;color:#374151;margin-top:6px;line-height:1.4}}
  .card .label{{font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.8px;font-weight:600;margin-bottom:10px}}
  .card .value{{font-size:28px;font-weight:800;line-height:1;letter-spacing:-1px;color:#fff}}
  .card.yellow{{border-color:#78350f;background:#1a1005}}.card.yellow .value{{color:#fbbf24}}
  .card.green{{border-color:#022c22;background:#060f0d}}.card.green .value{{color:#10b981}}
  .card.red{{border-color:#450a0a;background:#0f0606}}.card.red .value{{color:#f87171}}
  .card.orange{{border-color:#7c2d12;background:#150a04}}.card.orange .value{{color:#fb923c}}
  .card.blue{{border-color:#1e3a5f;background:#060c18}}.card.blue .value{{color:#60a5fa}}
  /* PAINEL DO DIA */
  .pd-item{{display:flex;align-items:flex-start;gap:10px;padding:8px 14px;border-bottom:1px solid #0f1728}}
  .pd-item:last-child{{border-bottom:none}}
  /* GRIDS */
  .cards-grid{{display:grid;gap:14px;margin-bottom:18px}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}}
  @media(max-width:900px){{.grid2{{grid-template-columns:1fr}}}}
  /* CHART BOX */
  .box{{background:#0d1321;border-radius:8px;padding:20px 20px;border:1px solid #111827}}
  .box-title{{font-size:10px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.7px;margin-bottom:16px}}
  /* TABLE */
  .tbl-wrap{{background:#0d1321;border-radius:8px;overflow:hidden;margin-bottom:20px;border:1px solid #111827}}
  .tbl-title{{padding:14px 24px;font-size:10px;font-weight:600;color:#374151;text-transform:uppercase;letter-spacing:.7px;border-bottom:1px solid #111827}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{background:#080d19;padding:10px 16px;text-align:left;font-size:10px;font-weight:600;color:#374151;text-transform:uppercase;letter-spacing:.6px}}
  td{{padding:11px 16px;border-bottom:1px solid #0d1321;color:#d1d5db}}
  tr:hover td{{background:#111827!important}}
  tr:last-child td{{border-bottom:none}}
  .tbl-scroll{{overflow-x:auto}}
  /* PILL */
  .pill{{padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap}}
  /* LEGEND */
  .legend{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px;font-size:11px;padding:0;margin-left:0}}
  .legend-item{{display:flex;align-items:center;gap:5px;color:#4b5563}}
  .legend-dot{{width:7px;height:7px;border-radius:50%;flex-shrink:0}}
  /* FILTER BAR */
  .filter-bar{{display:flex;gap:8px;padding:12px 24px;flex-wrap:wrap;border-bottom:1px solid #111827;align-items:center}}
  .filter-input{{background:#080d19;border:1px solid #111827;border-radius:6px;padding:7px 12px;color:#e2e8f0;font-size:12px;flex:1;min-width:200px;transition:background-color .3s ease,color .3s ease,border-color .3s ease,box-shadow .3s ease,transform .2s ease}}
  .filter-input:focus{{outline:none;border-color:#1f2937}}
  .filter-input::placeholder{{color:#374151}}
  .filter-select{{background:#080d19;border:1px solid #111827;border-radius:6px;padding:7px 12px;color:#9ca3af;font-size:12px;transition:background-color .3s ease,color .3s ease,border-color .3s ease,box-shadow .3s ease,transform .2s ease}}
  .filter-select:focus{{outline:none;border-color:#1f2937}}
  .btn-export{{background:#111827;color:#6b7280;border:1px solid #1f2937;border-radius:6px;padding:7px 14px;font-size:11px;font-weight:500;cursor:pointer;white-space:nowrap;transition:background-color .3s ease,color .3s ease,border-color .3s ease,box-shadow .3s ease,transform .2s ease;display:flex;align-items:center;gap:5px}}
  .btn-export:hover{{background:#1f2937;color:#e2e8f0}}
  /* LINKS */
  .shp-link{{color:#60a5fa;text-decoration:none;font-weight:500;font-family:monospace;font-size:12px}}
  .shp-link:hover{{color:#93c5fd}}
  /* SORTABLE */
  th.sortable{{cursor:pointer;user-select:none}}
  th.sortable:hover{{color:#6b7280}}
  th.sort-asc::after{{content:" ↑";color:#FFE600}}
  th.sort-desc::after{{content:" ↓";color:#FFE600}}
  /* DIVIDER */
  .divider{{height:1px;background:#111827;margin:20px 0}}
  /* mb utils */
  .mb16{{margin-bottom:16px}}
  /* MODULE NAV */
  .mod-nav{{display:flex;gap:4px;align-items:center}}
  .mod-btn{{padding:6px 14px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid #1f2937;text-decoration:none;transition:all .2s;color:#9ca3af;background:#0d1321;display:flex;align-items:center;gap:6px}}
  .mod-btn:hover{{background:#1f2937;color:#e2e8f0;border-color:#374151}}
  .mod-btn.m-fraude{{color:#ef4444;background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.3)}}
  .mod-btn.m-risco{{color:#FFE600;background:rgba(255,230,0,.08);border-color:rgba(255,230,0,.2)}}
  .mod-btn.m-isca{{color:#4ade80;background:rgba(74,222,128,.08);border-color:rgba(74,222,128,.2)}}
  .mod-btn.m-cftv{{color:#60a5fa;background:rgba(96,165,250,.08);border-color:rgba(96,165,250,.2)}}
  .mod-btn.m-sinistros{{color:#f97316;background:rgba(249,115,22,.08);border-color:rgba(249,115,22,.2)}}
  .mod-btn.m-disabled{{opacity:.35;cursor:not-allowed;pointer-events:none}}
  .mod-btn.m-diario{{color:#10B981;background:rgba(16,185,129,.08);border-color:rgba(16,185,129,.2)}}
  /* CARD CLICÁVEL */
  .card-link{{cursor:pointer;position:relative}}
  .card-link::after{{content:'↗';position:absolute;top:14px;right:14px;font-size:10px;color:#1f2937;transition:color .3s ease}}
  .card-link:hover::after{{color:#6b7280}}
  /* ON WAY editable fields */
  .ow-edit-wrap{{position:relative;display:flex;align-items:center;gap:3px;min-width:160px}}
  .ow-text{{background:#080d19;border:1px solid #1f2937;border-radius:5px;padding:5px 8px;color:#e2e8f0;font-size:12px;width:100%;outline:none;transition:border-color .2s}}
  .ow-text:focus{{border-color:#374151}}
  .ow-text.ow-saving{{border-color:#FBBF24!important}}
  .ow-text.ow-saved{{border-color:#10B981!important}}
  .ow-text.ow-err{{border-color:#ef4444!important}}
  .ow-fake-sel{{position:relative;background:#080d19;border:1px solid #1f2937;border-radius:5px;padding:5px 24px 5px 8px;color:#e2e8f0;font-size:12px;width:100%;display:flex;align-items:center;justify-content:space-between;min-height:30px;box-sizing:border-box;cursor:pointer;transition:border-color .2s}}
  .ow-fake-sel:focus-within{{border-color:#374151}}
  .ow-fake-val{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .ow-real-sel{{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%;border:none}}
  .ow-dd-btn{{color:#6b7280;cursor:pointer;font-size:14px;padding:0 2px;user-select:none;flex-shrink:0}}
  .ow-dd-btn:hover{{color:#e2e8f0}}
  .ow-sugest{{display:none;position:absolute;top:calc(100% + 2px);left:0;min-width:200px;background:#111827;border:1px solid #1f2937;border-radius:6px;z-index:999;box-shadow:0 4px 16px #000a;overflow:hidden}}
  .ow-sugest-item{{padding:7px 12px;font-size:12px;color:#d1d5db;cursor:pointer}}
  .ow-sugest-item:hover{{background:#1f2937;color:#fff}}
  .ow-link-btn{{color:#60a5fa;font-size:13px;text-decoration:none;flex-shrink:0;padding:2px 4px}}
  .ow-link-btn:hover{{color:#93c5fd}}
  /* SELETOR DE MÊS */
  .mes-selector{{display:flex;gap:8px;flex-wrap:wrap;padding:14px 20px;border-bottom:1px solid #111827;align-items:center}}
  .mes-btn{{background:#0d1321;color:#4b5563;border:1px solid #1f2937;border-radius:20px;padding:5px 14px;font-size:11px;font-weight:500;cursor:pointer;transition:background-color .3s ease,color .3s ease,border-color .3s ease,box-shadow .3s ease,transform .2s ease}}
  .mes-btn:hover{{color:#e2e8f0;border-color:#374151}}
  .mes-btn.mes-ativo{{background:#1f2937;color:#ffffff;border-color:#374151}}
  /* RESPONSIVO */
  @media(max-width:1024px){{
    .content{{padding:20px 20px}}
    .header{{padding:14px 20px}}
    .tabs{{padding:0 16px}}
    .filter-bar{{padding:10px 16px}}
    .tbl-title{{padding:12px 16px}}
  }}
  @media(max-width:640px){{
    .content{{padding:14px 12px}}
    .cards{{grid-template-columns:repeat(2,1fr)}}
    .grid2{{grid-template-columns:1fr}}
    .header-title{{font-size:14px}}
  }}
  /* EMPTY STATE */
  .chart-wrap{{position:relative}}
  .empty-msg{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#374151;pointer-events:none}}
  /* DIÁRIO DE BORDO */
  .db-section-lbl{{font-size:10px;color:#374151;text-transform:uppercase;letter-spacing:.06em;margin:12px 0 6px;display:flex;align-items:center;gap:8px}}
  .db-section-lbl::after{{content:'';flex:1;border-top:0.5px solid #111827}}
  .db-act-item{{background:#080d19;border:1px solid #111827;border-radius:8px;padding:9px 12px;margin-bottom:6px;display:flex;align-items:flex-start;gap:10px;transition:border-color .15s}}
  .db-act-item:hover{{border-color:#1f2937}}
  .db-act-item.db-done{{opacity:.5}}
  .db-check{{width:18px;height:18px;border:1.5px solid #374151;border-radius:4px;flex-shrink:0;margin-top:1px;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:11px;color:#10B981;transition:all .15s;user-select:none}}
  .db-check.db-done{{background:rgba(16,185,129,.12);border-color:#10B981}}
  .db-act-body{{flex:1;min-width:0}}
  .db-act-row{{display:flex;align-items:center;gap:6px;flex-wrap:wrap}}
  .db-act-name{{font-size:13px;font-weight:500;color:#F9FAFB}}
  .db-act-item.db-done .db-act-name{{text-decoration:line-through;color:#6B7280}}
  .db-act-time{{font-size:11px;color:#6B7280}}
  .db-tipo{{font-size:10px;padding:2px 6px;border-radius:20px;font-weight:500;white-space:nowrap}}
  .db-t-analise{{background:rgba(59,130,246,.1);color:#93C5FD}}
  .db-t-gemba{{background:rgba(245,158,11,.1);color:#FCD34D}}
  .db-t-reuniao{{background:rgba(234,88,12,.1);color:#FDBA74}}
  .db-t-1a1{{background:rgba(124,58,237,.1);color:#C4B5FD}}
  .db-t-treinamento{{background:rgba(5,150,105,.1);color:#6EE7B7}}
  .db-t-extra{{background:rgba(107,114,128,.1);color:#9CA3AF}}
  .db-obs-txt{{font-size:11px;color:#6B7280;margin-top:4px;font-style:italic}}
  .db-obs-inp{{margin-top:6px;width:100%;font-size:12px;padding:5px 8px;border-radius:4px;border:1px solid #1f2937;background:#060a14;color:#D1D5DB;outline:none;display:none;box-sizing:border-box;resize:vertical}}
  .db-act-item:hover .db-obs-inp,.db-obs-inp:focus{{display:block}}
  .db-btn-add{{width:100%;padding:8px;background:transparent;border:1px dashed #1f2937;border-radius:8px;font-size:12px;color:#6B7280;cursor:pointer;margin-top:8px;transition:border-color .15s,color .15s}}
  .db-btn-add:hover{{border-color:#10B981;color:#10B981}}
  .db-modal-bg{{display:none;position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.65);align-items:center;justify-content:center}}
  .db-modal{{background:#111827;border:1px solid #374151;border-radius:10px;padding:20px;width:360px;max-width:95vw}}
  .db-modal-inp{{width:100%;padding:7px 10px;background:#1f2937;border:1px solid #374151;border-radius:6px;color:#F9FAFB;font-size:13px;margin-bottom:8px;box-sizing:border-box}}
  .db-modal-inp:focus{{outline:none;border-color:#10B981}}
</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <div class="header-brand">
    <div class="lp-badge">LP</div>
    <div>
      <div style="display:flex;align-items:center;gap:8px">
        <div class="header-title">Risco SSP30</div>
        <span class="ver-badge">v2.8</span>
      </div>
      <div class="header-sub">Planilha de Controle · Gerado em {d["gerado"]}</div>
    </div>
  </div>
  <div style="display:flex;align-items:center;gap:14px">
    <span class="status-dot"></span>
    <span class="countdown-txt" id="countdown">Calculando...</span>
    <div class="mod-nav">
      <a href="./fraude.html" class="mod-btn">
        <i data-lucide="shield-alert" width="12" height="12"></i> Fraude
      </a>
      <a href="./index.html" class="mod-btn m-risco">
        <i data-lucide="truck" width="12" height="12"></i> Risco
      </a>
      <a href="./isca.html" class="mod-btn">
        <i data-lucide="fish" width="12" height="12"></i> Isca
      </a>
      <a href="./cftv.html" class="mod-btn">
        <i data-lucide="camera" width="12" height="12"></i> CFTV
      </a>
      <a href="./sinistros.html" class="mod-btn m-sinistros">
        <i data-lucide="alert-triangle" width="12" height="12"></i> Sinistros
      </a>
      <button id="db-nav-btn" onclick="dbTogglePanel()" class="mod-btn m-diario" style="cursor:pointer;position:relative">
        <i data-lucide="book-open" width="12" height="12"></i> Diário
        <span id="db-nav-badge" style="display:none;position:absolute;top:-4px;right:-4px;background:#10B981;color:#fff;font-size:8px;padding:1px 4px;border-radius:10px;font-weight:700"></span>
      </button>
    </div>
  </div>
</div>

<!-- PAINEL DIÁRIO DE BORDO (dropdown flutuante) -->
<div id="db-panel" style="display:none;position:fixed;top:60px;right:12px;width:350px;max-height:75vh;overflow-y:auto;background:#111827;border:1px solid #374151;border-radius:10px;z-index:9999;box-shadow:0 8px 32px rgba(0,0,0,.8)">
  <!-- cabeçalho do painel -->
  <div style="padding:11px 14px;border-bottom:1px solid #1f2937;display:flex;align-items:center;gap:8px;flex-shrink:0;position:sticky;top:0;background:#111827;z-index:1">
    <span style="font-size:12px;font-weight:600;color:#F9FAFB"><i data-lucide="book-open" width="13" height="13" style="vertical-align:-2px"></i> Diário de Bordo</span>
    <span id="db-date-lbl" style="font-size:11px;color:#6B7280;flex:1"></span>
    <span id="db-progress-wrap" style="display:flex;align-items:center;gap:5px;font-size:10px;color:#6B7280;margin-right:4px">
      <span id="db-progress-txt"></span>
      <div style="width:44px;height:4px;background:#0d1321;border-radius:2px;overflow:hidden">
        <div id="db-progress-bar" style="height:100%;background:#10B981;border-radius:2px;width:0%;transition:width .3s"></div>
      </div>
    </span>
    <span id="db-status" style="font-size:10px;color:#6B7280"></span>
    <button onclick="dbFecharPanel()" style="background:none;border:none;color:#6B7280;cursor:pointer;font-size:15px;padding:0;line-height:1;margin-left:4px">✕</button>
  </div>
  <!-- corpo -->
  <div style="padding:10px 12px 12px">
    <div id="db-list"></div>
    <div class="db-section-lbl" style="margin-top:10px">Extras</div>
    <div id="db-extras"></div>
    <button class="db-btn-add" onclick="dbAbrirModal()">+ Atividade extra</button>
  </div>
</div>

<div class="app-body">
<nav class="sidebar">
  <div class="sb-item active" data-tab="briefing" onclick="showTab('briefing',this)">
    <i data-lucide="sun" width="14" height="14" class="ci"></i> Briefing
    {'<span class="sb-badge red">' + str(d["briefing"]["n_hoje"]) + ' hoje</span>' if d["briefing"]["n_hoje"] else ''}
  </div>
  <div class="sb-item" data-tab="geral" onclick="showTab('geral',this)">
    <i data-lucide="bar-chart-2" width="14" height="14" class="ci"></i> Visão Geral
  </div>
  <div class="sb-item {'sb-alert' if d['criticos'] else ''}" data-tab="criticos" onclick="showTab('criticos',this)">
    <i data-lucide="alert-triangle" width="14" height="14" class="ci"></i>
    Críticos {'<span class="sb-badge red">' + str(len(d["criticos"])) + '</span>' if d["criticos"] else ''}
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Monitoramento</div>
  <div class="sb-item" data-tab="route" onclick="showTab('route',this)">
    <i data-lucide="package" width="14" height="14" class="ci"></i>
    ON ROUTE <span class="sb-badge">{d["r_total"]}</span>
  </div>
  <div class="sb-item" data-tab="way" onclick="showTab('way',this)">
    <i data-lucide="truck" width="14" height="14" class="ci"></i>
    ON WAY <span class="sb-badge">{d["w_total"]}</span>
  </div>
  <div class="sb-item" data-tab="at_station" onclick="showTab('at_station',this)">
    <i data-lucide="warehouse" width="14" height="14" class="ci"></i>
    AT STATION <span class="sb-badge">{d["at_station"]["total"]}</span>
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Places</div>
  <div class="sb-item" data-tab="places" onclick="showTab('places',this)">
    <i data-lucide="map-pin" width="14" height="14" class="ci"></i>
    NEX / DC <span class="sb-badge">{d["places"]["total"]}</span>
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Análise</div>
  <div class="sb-item" data-tab="hist" onclick="showTab('hist',this)">
    <i data-lucide="clock" width="14" height="14" class="ci"></i> Histórico
    <span class="sb-badge">{len(d["hist_todos"])}</span>
  </div>
</nav>
<main class="main-content">

<!-- ===================== ABA 0: BRIEFING MATINAL ===================== -->
<div id="tab-briefing" class="content active">
  <div style="font-size:18px;font-weight:700;color:#f9fafb;margin-bottom:14px">
    Briefing Matinal <span style="color:#6b7280;font-weight:400;font-size:13px">SSP30 · últimos 90 dias</span>
  </div>
  {_briefing_html(d["briefing"], d.get("r_rows",[]), d.get("w_rows",[]))}
</div>

<!-- ===================== ABA 1: VISÃO GERAL ===================== -->
<div id="tab-geral" class="content">
  <div style="display:flex;align-items:center;gap:8px;padding:16px 24px 0;flex-wrap:wrap">
    <span style="font-size:11px;color:#6b7280;font-weight:600">Período:</span>
    {_btn_meses}
  </div>
  <div class="cards">
    <div class="card card-link" onclick="irPara('route')">
      <div class="card-header"><i data-lucide="package" class="card-icon" width="14" height="14"></i><span class="card-label">ON ROUTE</span></div>
      <div class="card-value" id="cv-rt">{d["r_total"]}</div>
      <div class="card-delta" id="cd-rt">+{d["r_novos"]} novos · {trend(d["net_rt"])}</div>
    </div>
    <div class="card card-link" onclick="irPara('way')">
      <div class="card-header"><i data-lucide="truck" class="card-icon" width="14" height="14"></i><span class="card-label">ON WAY</span></div>
      <div class="card-value" id="cv-wy">{d["w_total"]}</div>
      <div class="card-delta" id="cd-wy">+{d["w_novos"]} novos · {trend(d["net_wy"])}</div>
    </div>
    <div class="card card-link" onclick="irPara('route')">
      <div class="card-header"><i data-lucide="dollar-sign" class="card-icon" width="14" height="14"></i><span class="card-label">GMV ON ROUTE</span></div>
      <div class="card-value" id="cv-gmv-rt">${d["r_gmv"]:,.0f}</div>
    </div>
    <div class="card card-link" onclick="irPara('way')">
      <div class="card-header"><i data-lucide="dollar-sign" class="card-icon" width="14" height="14"></i><span class="card-label">GMV ON WAY</span></div>
      <div class="card-value" id="cv-gmv-wy">${d["w_gmv"]:,.0f}</div>
    </div>
    <div class="card card-alert card-link" onclick="irPara('gmv')">
      <div class="card-header"><i data-lucide="alert-triangle" class="card-icon" width="14" height="14" style="color:#7f1d1d"></i><span class="card-label">GMV EM RISCO</span></div>
      <div class="card-value val-alert" id="cv-gmv-risco">${d["gmv_total"]:,.0f}</div>
    </div>
    <div class="card card-link" onclick="irPara('route')">
      <div class="card-header"><i data-lucide="camera" class="card-icon" width="14" height="14"></i><span class="card-label">CFTV Solicitado</span></div>
      <div class="card-value val-warn">{d["cftv_total"]}</div>
      <div class="card-delta">Route {d["r_cftv"]} · Way {d["w_cftv"]}</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="clock" class="card-icon" width="14" height="14"></i><span class="card-label">Dias médio carteira</span></div>
      <div class="card-value">{d["dias_medio"]}</div>
      <div class="card-delta">pacotes ativos</div>
    </div>
    <div class="card card-ok card-link" onclick="irPara('hist')">
      <div class="card-header"><i data-lucide="award" class="card-icon" width="14" height="14" style="color:#064e3b"></i><span class="card-label">Recuperados</span></div>
      <div class="card-value val-ok" id="cv-rec">{d["recuperados"]}</div>
      <div class="card-delta" id="cd-rec">{d["mes_lbl"]} · Seguiram fluxo correto</div>
    </div>
    <div class="card card-ok card-link" onclick="irPara('hist')">
      <div class="card-header"><i data-lucide="trending-up" class="card-icon" width="14" height="14" style="color:#064e3b"></i><span class="card-label">GMV do Mês</span></div>
      <div class="card-value val-ok" id="cv-gmv-mes">${d["gmv_recuperado"]:,.0f}</div>
      <div class="card-delta" id="cd-gmv-mes">
        <span style="color:#10b981">↑ ${d["gmv_recuperado"]:,.0f} recuperado</span><br>
        <span style="color:#ef4444">↓ ${d["gmv_perdido"]:,.0f} perdido</span>
      </div>
    </div>
    <div class="card card-link" onclick="irPara('hist')">
      <div class="card-header"><i data-lucide="percent" class="card-icon" width="14" height="14"></i><span class="card-label">Taxa de Recupero</span></div>
      <div class="card-value" id="cv-taxa">{d["taxa_recupero"]}%</div>
      <div class="card-delta" id="cd-taxa">{d["recuperados"]} de {d["removidos"]} removidos</div>
    </div>
  </div>

  <!-- Meta Mensal de Recupero -->
  <div style="background:#0d1321;border-radius:8px;border:1px solid #111827;padding:20px 24px;margin-bottom:16px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px">
      <div style="display:flex;align-items:center;gap:8px">
        <i data-lucide="target" width="14" height="14" style="color:#FFE600"></i>
        <span style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">Meta Mensal de Recupero</span>
      </div>
      <span style="font-size:13px;font-weight:700;color:#f9fafb">{d["recuperados"]} <span style="color:#94a3b8;font-weight:400">/ {_meta_rec} pacotes</span></span>
    </div>
    <div style="background:#1f2937;border-radius:99px;height:10px;overflow:hidden">
      <div style="background:linear-gradient(90deg,{_cor_meta},{_cor_meta}cc);width:{_pct_meta}%;height:100%;border-radius:99px;transition:width .6s ease"></div>
    </div>
    <div style="margin-top:8px;display:flex;justify-content:space-between;align-items:center">
      <span style="font-size:12px;font-weight:600;color:{_cor_meta}">{_pct_meta}% atingido</span>
      <span style="font-size:11px;color:#64748b">{d["mes_lbl"]}</span>
    </div>
  </div>

  <div class="legend">
    <span class="legend-item"><span class="legend-dot" style="background:#10B981"></span> 0–3 dias (ok)</span>
    <span class="legend-item"><span class="legend-dot" style="background:#F97316"></span> 4–7 dias (atenção)</span>
    <span class="legend-item"><span class="legend-dot" style="background:#EF4444"></span> 8+ dias (crítico)</span>
  </div>

  <div class="grid2 mb16">
    <div class="box"><div class="box-title">ON ROUTE por Situation</div><canvas id="cSitRt" height="220"></canvas></div>
    <div class="box"><div class="box-title">ON WAY por Situation</div><canvas id="cSitWy" height="220"></canvas></div>
  </div>
  <div class="grid2 mb16">
    <div class="box"><div class="box-title">Status dos Casos</div><canvas id="cStatus" height="220"></canvas></div>
    <div class="box"><div class="box-title">GMV em Risco por Data de Entrada</div><canvas id="cGmvEvo" height="220"></canvas></div>
  </div>
  <div class="box mb16"><div class="box-title">Volume de Entradas por Data</div><canvas id="cEvo" height="180"></canvas></div>
  <div class="box mb16"><div class="box-title">Entradas por Dia da Semana</div><canvas id="cHeatmap" height="160"></canvas></div>
  <div class="box mb16">
    <div class="box-title" style="display:flex;align-items:center;justify-content:space-between">
      <span>Evolução do GMV em Risco — Snapshots Diários</span>
      <button onclick="if(window._snapChart)_snapChart.resetZoom()" title="Resetar zoom"
        style="background:#1e293b;border:1px solid #334155;color:#94a3b8;font-size:10px;padding:3px 10px;border-radius:4px;cursor:pointer;transition:all .15s"
        onmouseover="this.style.borderColor='#6b7280';this.style.color='#e2e8f0'"
        onmouseout="this.style.borderColor='#334155';this.style.color='#94a3b8'">↺ Reset zoom</button>
    </div>
    <div id="snap-no-data" style="display:none;text-align:center;padding:32px;color:#64748b;font-size:13px">Dados insuficientes — gráfico disponível após acúmulo de snapshots diários</div>
    <canvas id="cGmvSnap" height="180"></canvas>
  </div>
</div>

<!-- ===================== ABA 2: CRÍTICOS ===================== -->
<div id="tab-criticos" class="content">
  {'<div style="background:#7f1d1d;border:1px solid #EF4444;border-radius:12px;padding:20px;margin-bottom:24px;display:flex;align-items:center;gap:16px"><span style="font-size:32px">🚨</span><div><div style="font-size:16px;font-weight:800;color:#FCA5A5">'+str(len(d["criticos"]))+' pacotes precisam de atenção urgente</div><div style="color:#FCA5A5;opacity:0.8;font-size:13px;margin-top:4px">Critérios: Possivel Lost / +11d OW + GMV alto + muitos dias na carteira (2 ou mais fatores)</div></div></div>' if d['criticos'] else '<div style="text-align:center;padding:48px;color:#64748b"><div style="font-size:48px">✅</div><div style="font-size:18px;margin-top:12px">Nenhum pacote crítico no momento!</div></div>'}
  <div class="tbl-wrap">
    <div id="tabulator-criticos"></div>
    <script>
    (function(){{
      var dataCrit = {d['tab_criticos_json']};
      new Tabulator("#tabulator-criticos", {{
        data: dataCrit,
        layout: "fitDataStretch",
        pagination: "local",
        paginationSize: 30,
        paginationSizeSelector: [10, 30, 50],
        movableColumns: true,
        initialSort: [{{column:"gmv", dir:"desc"}}],
        rowFormatter: function(row){{
          row.getElement().style.borderLeft = "3px solid #e05252";
        }},
        columns: [
          {{title:"SHP ID", field:"id", headerFilter:"input", width:130, formatter:function(cell){{
            var v = cell.getValue();
            return '<a href="https://envios.adminml.com/logistics/package-management/package/'+v+'" target="_blank" style="font-family:monospace;font-size:12px;color:#60a5fa;text-decoration:none;font-weight:700">'+v+'</a>';
          }}}},
          {{title:"Situação", field:"sit", headerFilter:"select", headerFilterParams:{{values:true}}, width:160}},
          {{title:"Origem", field:"origem", headerFilter:"select", headerFilterParams:{{values:true}}, width:110}},
          {{title:"GMV (USD)", field:"gmv", sorter:"number", formatter:"money", formatterParams:{{precision:2, symbol:"$"}}, width:120}},
          {{title:"Dias Cart.", field:"dias_carteira", sorter:"number", width:90, formatter:function(cell){{
            var v = cell.getValue();
            if(v > 7) return '<span style="color:#e05252;font-weight:600">'+v+'d</span>';
            return v >= 0 ? v+'d' : '—';
          }}}},
          {{title:"Status", field:"status", headerFilter:"select", headerFilterParams:{{values:true}}, width:140}},
          {{title:"Responsável", field:"resp", headerFilter:"select", headerFilterParams:{{values:true}}, width:150}},
          {{title:"Motivos", field:"motivos", headerFilter:"input", width:360}},
        ],
      }});
    }})();
    </script>
  </div>
</div>

<!-- ===================== ABA: ON ROUTE ===================== -->
<div id="tab-route" class="content">
  {'<div style="background:#064e3b;border:1px solid #10b981;border-radius:6px;padding:8px 12px;margin:0 0 14px;font-size:12px;color:#6ee7b7"><strong>✓ ' + str(d["r_devolvidos"]) + ' pacote(s) detectado(s) como DEVOLVIDO</strong> — movidos automaticamente para Histórico como Recuperado.</div>' if d.get("r_devolvidos") else ''}
  <div id="rt-kpis" style="display:flex;gap:10px;margin-bottom:18px;flex-wrap:wrap"></div>
  <div style="display:flex;gap:14px;margin-bottom:18px;align-items:flex-start">
    <div style="flex:1.1;display:flex;flex-direction:column;gap:14px">
      <div style="border:1px solid #2a2a2a;border-radius:8px;overflow:hidden">
        <div style="background:#FFD700;color:#000;font-weight:600;padding:6px 12px;font-size:12px">Situação</div>
        <table id="rt-tbl-sit" style="width:100%;border-collapse:collapse;background:#161616"></table>
      </div>
      <div style="border:1px solid #2a2a2a;border-radius:8px;overflow:hidden">
        <div style="background:#FFD700;color:#000;font-weight:600;padding:6px 12px;font-size:12px">Dias na carteira</div>
        <table id="rt-tbl-dias" style="width:100%;border-collapse:collapse;background:#161616"></table>
      </div>
    </div>
    <div style="flex:1">
      <div style="border:1px solid #2a2a2a;border-radius:8px;overflow:hidden">
        <div style="background:#FFD700;color:#000;font-weight:600;padding:6px 12px;font-size:12px">Distribuição — dias na carteira</div>
        <div id="rt-chart" style="background:#161616;padding:12px"></div>
      </div>
    </div>
  </div>
  <div style="display:flex;justify-content:flex-end;margin-bottom:10px">
    <a href="https://docs.google.com/spreadsheets/d/{PLANILHA_CONTROLE_ID}/edit"
       target="_blank"
       style="background:#1a3a5c;color:#5ba3d9;border:1px solid #2a5a8c;padding:5px 14px;border-radius:6px;font-size:12px;text-decoration:none">
      ✏ Editar na planilha
    </a>
  </div>
  <div style="border:1px solid #2a2a2a;border-radius:8px;overflow:hidden">
    <div style="background:#FFD700;color:#000;font-weight:600;padding:6px 12px;font-size:12px">Casos individuais — Ação LP · Status · Conclusão · Nota</div>
    <div id="rt-list" style="background:#161616"></div>
  </div>
  <style>
  .rt-row{{transition:background .12s}}
  .rt-row:hover{{background:#1c1c1c}}
  .rt-urg{{background:#160404}}
  .rt-urg:hover{{background:#1e0606}}
  .rt-st,.rt-co{{padding:2px 6px;border-radius:3px;font-size:9px;border:1px solid #252525;background:#0d0d0d;color:#444;cursor:pointer;height:22px;white-space:nowrap}}
  .rt-sel{{background:#0d0d0d;border:1px solid #252525;color:#bbb;padding:2px 5px;border-radius:4px;font-size:10px;height:22px;width:130px;flex-shrink:0;outline:none}}
  .rt-sel:focus,.rt-nota:focus{{border-color:#FFD700;outline:none}}
  .rt-nota{{background:#0d0d0d;border:1px solid #252525;color:#bbb;padding:2px 6px;border-radius:4px;font-size:10px;height:22px;flex:1;min-width:60px;outline:none}}
  .rt-nota::placeholder{{color:#333}}
  </style>
  <script>
  (function(){{
    var SCRIPT_URL = '';
    var data = {d['tab_rt_json']};

    function fmtG(v){{ return '$' + Math.round(Number(v)||0).toLocaleString('pt-BR'); }}
    function pct(a,t){{ return t ? (a/t*100).toFixed(1)+'%' : '0%'; }}

    var urgentes   = data.filter(function(r){{ return r.dias_carteira >= 8 && !(r.acao_lp||'').trim(); }});
    var novos      = data.filter(function(r){{ return r.dias_carteira >= 0 && r.dias_carteira <= 2; }});
    var urgIds     = urgentes.map(function(r){{ return r.id; }});
    var novosIds   = novos.map(function(r){{ return r.id; }});
    var andamento  = data.filter(function(r){{ return urgIds.indexOf(r.id)<0 && novosIds.indexOf(r.id)<0; }});
    var gmvTotal   = data.reduce(function(s,r){{ return s+(r.gmv||0); }},0);
    var gmvUrg     = urgentes.reduce(function(s,r){{ return s+(r.gmv||0); }},0);
    var gmvAnd     = andamento.reduce(function(s,r){{ return s+(r.gmv||0); }},0);
    var gmvNov     = novos.reduce(function(s,r){{ return s+(r.gmv||0); }},0);

    /* ---- KPI cards ---- */
    var kpisEl = document.getElementById('rt-kpis');
    [
      {{v:data.length,    l:'Total ativo',  c:'#FFD700'}},
      {{v:fmtG(gmvTotal), l:'GMV em risco', c:'#4caf50'}},
      {{v:urgentes.length,l:'Urgentes ≥8d', c:'#ef5350'}},
      {{v:andamento.length,l:'Em andamento',c:'#ffb74d'}},
      {{v:novos.length,   l:'Novos ≤2d',   c:'#81c784'}}
    ].forEach(function(k){{
      var d2=document.createElement('div');
      d2.style.cssText='flex:1;background:#161616;border-radius:8px;padding:11px 13px;border:1px solid #2a2a2a;border-top:3px solid '+k.c;
      d2.innerHTML='<div style="font-size:20px;font-weight:600;margin-bottom:3px;color:'+k.c+'">'+k.v+'</div><div style="font-size:10px;color:#666">'+k.l+'</div>';
      kpisEl.appendChild(d2);
    }});

    /* ---- Tabela Situação ---- */
    var sitMap={{}};
    data.forEach(function(r){{
      var s=r.sit||'Sem situação';
      if(!sitMap[s]) sitMap[s]={{cnt:0,gmv:0}};
      sitMap[s].cnt++; sitMap[s].gmv+=(r.gmv||0);
    }});
    var sitEl=document.getElementById('rt-tbl-sit');
    var thS='<tr><td style="background:#1e1e1e;color:#777;font-size:10px;padding:5px 10px;border-bottom:1px solid #252525;text-transform:uppercase">Situação</td><td style="background:#1e1e1e;color:#777;font-size:10px;padding:5px 10px;border-bottom:1px solid #252525;text-align:right;text-transform:uppercase">Qtd</td><td style="background:#1e1e1e;color:#777;font-size:10px;padding:5px 10px;border-bottom:1px solid #252525;text-align:right;text-transform:uppercase">GMV</td><td style="background:#1e1e1e;color:#777;font-size:10px;padding:5px 10px;border-bottom:1px solid #252525;text-align:right;text-transform:uppercase">%</td></tr>';
    Object.keys(sitMap).forEach(function(s){{
      var lost=s.indexOf('Lost')>=0;
      thS+='<tr><td style="padding:7px 10px;border-bottom:1px solid #1e1e1e;color:#ccc;font-size:12px">'+s+'</td><td style="padding:7px 10px;border-bottom:1px solid #1e1e1e;text-align:right;font-size:12px">'+sitMap[s].cnt+'</td><td style="padding:7px 10px;border-bottom:1px solid #1e1e1e;text-align:right;font-size:12px">'+fmtG(sitMap[s].gmv)+'</td><td style="padding:7px 10px;border-bottom:1px solid #1e1e1e;text-align:right;font-size:12px;'+(lost?'color:#ef5350;font-weight:600':'')+'">'+(lost?'<strong>':'')+pct(sitMap[s].cnt,data.length)+(lost?'</strong>':'')+'</td></tr>';
    }});
    thS+='<tr><td style="padding:7px 10px;font-weight:600;color:#fff;border-top:1px solid #333;font-size:12px">Total</td><td style="padding:7px 10px;font-weight:600;color:#fff;border-top:1px solid #333;text-align:right;font-size:12px">'+data.length+'</td><td style="padding:7px 10px;font-weight:600;color:#fff;border-top:1px solid #333;text-align:right;font-size:12px">'+fmtG(gmvTotal)+'</td><td style="padding:7px 10px;font-weight:600;color:#fff;border-top:1px solid #333;text-align:right;font-size:12px">100%</td></tr>';
    sitEl.innerHTML=thS;

    /* ---- Tabela Dias na carteira ---- */
    var diasEl=document.getElementById('rt-tbl-dias');
    var thD='<tr><td style="background:#1e1e1e;color:#777;font-size:10px;padding:5px 10px;border-bottom:1px solid #252525;text-transform:uppercase">Faixa</td><td style="background:#1e1e1e;color:#777;font-size:10px;padding:5px 10px;border-bottom:1px solid #252525;text-align:right;text-transform:uppercase">Qtd</td><td style="background:#1e1e1e;color:#777;font-size:10px;padding:5px 10px;border-bottom:1px solid #252525;text-align:right;text-transform:uppercase">GMV</td><td style="background:#1e1e1e;color:#777;font-size:10px;padding:5px 10px;border-bottom:1px solid #252525;text-align:right;text-transform:uppercase">%</td></tr>';
    [
      {{lbl:'≤ 2d — Novos',    rows:novos,    gmv:gmvNov, c:'#81c784'}},
      {{lbl:'3–7d — Andamento',rows:andamento, gmv:gmvAnd, c:'#ffb74d'}},
      {{lbl:'≥ 8d — Urgentes', rows:urgentes,  gmv:gmvUrg, c:'#ef5350'}}
    ].forEach(function(g){{
      var isUrg=g.c==='#ef5350';
      thD+='<tr><td style="padding:7px 10px;border-bottom:1px solid #1e1e1e;color:'+g.c+';font-size:12px">'+g.lbl+'</td><td style="padding:7px 10px;border-bottom:1px solid #1e1e1e;text-align:right;font-size:12px">'+g.rows.length+'</td><td style="padding:7px 10px;border-bottom:1px solid #1e1e1e;text-align:right;font-size:12px">'+fmtG(g.gmv)+'</td><td style="padding:7px 10px;border-bottom:1px solid #1e1e1e;text-align:right;font-size:12px;'+(isUrg?'color:#ef5350;font-weight:600':'')+'">'+(isUrg?'<strong>':'')+pct(g.rows.length,data.length)+(isUrg?'</strong>':'')+'</td></tr>';
    }});
    thD+='<tr><td style="padding:7px 10px;font-weight:600;color:#fff;border-top:1px solid #333;font-size:12px">Total</td><td style="padding:7px 10px;font-weight:600;color:#fff;border-top:1px solid #333;text-align:right;font-size:12px">'+data.length+'</td><td style="padding:7px 10px;font-weight:600;color:#fff;border-top:1px solid #333;text-align:right;font-size:12px">'+fmtG(gmvTotal)+'</td><td style="padding:7px 10px;font-weight:600;color:#fff;border-top:1px solid #333;text-align:right;font-size:12px">100%</td></tr>';
    diasEl.innerHTML=thD;

    /* ---- Gráfico de barras ---- */
    var daysMap={{}};
    data.forEach(function(r){{ if(r.dias_carteira>=0) daysMap[r.dias_carteira]=(daysMap[r.dias_carteira]||0)+1; }});
    var daysArr=Object.keys(daysMap).map(Number).sort(function(a,b){{return a-b;}});
    var maxCnt=Math.max.apply(null,daysArr.map(function(d3){{return daysMap[d3];}}));
    var chartEl=document.getElementById('rt-chart');
    var chartH='';
    daysArr.forEach(function(d3){{
      var cnt=daysMap[d3], pw=Math.round(cnt/maxCnt*90);
      var bg=d3<=2?'#81c784':d3<=7?'#ffb74d':d3<=10?'#ef5350':'#b71c1c';
      var tc=d3<=7?'#000':'#fff';
      chartH+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:7px"><div style="font-size:10px;color:#777;width:26px;text-align:right;flex-shrink:0">'+d3+'d</div><div style="flex:1;height:20px;background:#1e1e1e;border-radius:3px;overflow:hidden"><div style="width:'+pw+'%;height:100%;background:'+bg+';display:flex;align-items:center;padding:0 6px;font-size:10px;font-weight:600;color:'+tc+';border-radius:3px;min-width:20px">'+cnt+'</div></div></div>';
    }});
    chartEl.innerHTML=chartH||'<div style="color:#555;font-size:11px;padding:20px;text-align:center">Sem dados</div>';

    /* ---- Helpers para botões ---- */
    function stStyle(btn, selected){{
      var t=btn.textContent.trim();
      if(selected){{
        var map={{'Pendente':'rgba(255,183,77,.15)|#ffb74d','Andando':'rgba(100,181,246,.15)|#64b5f6','Concluído':'rgba(129,199,132,.15)|#81c784'}};
        var p=(map[t]||'').split('|');
        btn.style.background=p[0]||''; btn.style.borderColor=p[1]||''; btn.style.color=p[1]||'';
      }} else {{
        btn.style.background='#0d0d0d'; btn.style.borderColor='#252525'; btn.style.color='#444';
      }}
    }}
    function coStyle(btn, selected){{
      var t=btn.textContent.trim();
      if(selected){{
        var isBpp=t==='BPP';
        btn.style.background=isBpp?'rgba(239,83,80,.15)':'rgba(129,199,132,.15)';
        btn.style.borderColor=isBpp?'#ef5350':'#81c784';
        btn.style.color=isBpp?'#ef5350':'#81c784';
      }} else {{
        btn.style.background='#0d0d0d'; btn.style.borderColor='#252525'; btn.style.color='#444';
      }}
    }}

    /* ---- Lista de casos ---- */
    var ACOES=['Solicitado apoio','Investigação em andamento','Solicitado medida'];
    var sorted=urgentes.concat(andamento).concat(novos);
    var listEl=document.getElementById('rt-list');
    if(!sorted.length){{
      listEl.innerHTML='<div style="text-align:center;color:#555;padding:30px;font-size:12px">Nenhum caso ativo.</div>';
    }}

    sorted.forEach(function(r){{
      var isUrg=urgIds.indexOf(r.id)>=0, isNov=novosIds.indexOf(r.id)>=0;
      var dotC=isUrg?'#ef5350':(isNov?'#81c784':'#ffb74d');
      var diasC=r.dias_carteira>=8?'#ef5350':(r.dias_carteira<=2?'#81c784':'#ffb74d');
      var isLost=(r.sit||'').indexOf('Lost')>=0;

      var row=document.createElement('div');
      row.className='rt-row'+(isUrg?' rt-urg':'');
      row.style.cssText='display:flex;align-items:center;gap:7px;padding:7px 10px;border-bottom:1px solid #1e1e1e;min-height:40px';

      var dot=document.createElement('div');
      dot.style.cssText='width:7px;height:7px;border-radius:50%;background:'+dotC+';flex-shrink:0';
      row.appendChild(dot);

      var shp=document.createElement('div');
      shp.style.cssText='font-family:monospace;font-size:10px;color:#64b5f6;width:90px;flex-shrink:0;cursor:pointer';
      shp.textContent=r.id; shp.title='Clique para copiar';
      shp.onclick=function(){{ navigator.clipboard&&navigator.clipboard.writeText(this.textContent); }};
      row.appendChild(shp);

      var gmvEl=document.createElement('div');
      gmvEl.style.cssText='font-size:11px;font-weight:600;width:54px;flex-shrink:0';
      gmvEl.textContent=fmtG(r.gmv);
      row.appendChild(gmvEl);

      var sit=document.createElement('span');
      sit.style.cssText='font-size:9px;padding:2px 5px;border-radius:3px;background:'+(isLost?'rgba(239,83,80,.2)':'rgba(255,183,77,.12)')+';color:'+(isLost?'#ef9a9a':'#ffcc80')+';flex-shrink:0';
      sit.textContent=isLost?'Poss.Lost':'Procurar';
      row.appendChild(sit);

      var dc=document.createElement('span');
      dc.style.cssText='font-size:10px;font-weight:700;width:22px;flex-shrink:0;color:'+diasC;
      dc.textContent=(r.dias_carteira>=0?r.dias_carteira:'—')+'d';
      row.appendChild(dc);

      var div1=document.createElement('div');
      div1.style.cssText='width:1px;height:18px;background:#252525;flex-shrink:0';
      row.appendChild(div1);

      var sel=document.createElement('select');
      sel.className='rt-sel';
      ['','Solicitado apoio','Investigação em andamento','Solicitado medida'].forEach(function(a){{
        var opt=document.createElement('option');
        opt.value=a; opt.textContent=a||'— sem ação';
        if(a===r.acao_lp) opt.selected=true;
        sel.appendChild(opt);
      }});
      row.appendChild(sel);

      var stDiv=document.createElement('div');
      stDiv.style.cssText='display:flex;gap:3px;flex-shrink:0';
      [['Pendente','Pendente'],['Andando','Em andamento'],['Concluído','Concluído']].forEach(function(pair){{
        var btn=document.createElement('button');
        btn.className='rt-st'; btn.textContent=pair[0];
        stStyle(btn, r.status===pair[1]);
        btn.onclick=function(){{
          stDiv.querySelectorAll('.rt-st').forEach(function(b){{ stStyle(b,false); }});
          stStyle(btn,true);
        }};
        stDiv.appendChild(btn);
      }});
      row.appendChild(stDiv);

      var coDiv=document.createElement('div');
      coDiv.style.cssText='display:flex;gap:3px;flex-shrink:0';
      ['BPP','Revertido'].forEach(function(lbl){{
        var btn=document.createElement('button');
        btn.className='rt-co'; btn.textContent=lbl;
        coStyle(btn, r.finalizacao===lbl);
        btn.onclick=function(){{
          coDiv.querySelectorAll('.rt-co').forEach(function(b){{ coStyle(b,false); }});
          coStyle(btn,true);
        }};
        coDiv.appendChild(btn);
      }});
      row.appendChild(coDiv);

      var nota=document.createElement('input');
      nota.className='rt-nota'; nota.type='text'; nota.placeholder='Nota...';
      nota.value=r.nota||'';
      row.appendChild(nota);

      if((r.cftv||'').toLowerCase()==='sim'){{
        var cftvBadge=document.createElement('span');
        cftvBadge.style.cssText='font-size:9px;padding:2px 5px;border-radius:3px;background:rgba(100,181,246,.15);color:#64b5f6;flex-shrink:0';
        cftvBadge.textContent='CFTV';
        row.appendChild(cftvBadge);
      }}

      var saveBtn=document.createElement('button');
      saveBtn.style.cssText='background:#FFD700;color:#000;border:none;padding:2px 9px;border-radius:4px;font-size:10px;font-weight:700;cursor:pointer;height:22px;flex-shrink:0';
      saveBtn.textContent='Salvar';
      var okSpan=document.createElement('span');
      okSpan.style.cssText='font-size:10px;color:#81c784;flex-shrink:0;display:none';
      okSpan.textContent='✓';
      saveBtn.onclick=function(){{
        var stSel=''; stDiv.querySelectorAll('.rt-st').forEach(function(b){{
          if(b.style.color&&b.style.color!=='rgb(68,68,68)'&&b.style.color!=='#444'){{
            var map={{'Pendente':'Pendente','Andando':'Em andamento','Concluído':'Concluído'}};
            stSel=map[b.textContent.trim()]||'';
          }}
        }});
        var coSel=''; coDiv.querySelectorAll('.rt-co').forEach(function(b){{
          if(b.style.color&&b.style.color!=='rgb(68,68,68)'&&b.style.color!=='#444') coSel=b.textContent.trim();
        }});
        var payload=JSON.stringify({{shp_id:r.id,acao_lp:sel.value,status:stSel,conclusao:coSel,nota:nota.value}});
        if(SCRIPT_URL){{
          fetch(SCRIPT_URL,{{method:'POST',body:payload}}).then(function(){{
            okSpan.style.display='inline';
            setTimeout(function(){{okSpan.style.display='none';}},2000);
          }}).catch(function(){{ okSpan.textContent='✗'; okSpan.style.color='#ef5350'; okSpan.style.display='inline'; setTimeout(function(){{okSpan.style.display='none';okSpan.textContent='✓';okSpan.style.color='#81c784';}},2000); }});
        }} else {{
          okSpan.style.display='inline';
          setTimeout(function(){{okSpan.style.display='none';}},2000);
        }}
      }};
      row.appendChild(saveBtn);
      row.appendChild(okSpan);
      listEl.appendChild(row);
    }});
  }})();
  </script>
</div>

<!-- ===================== ABA 3: ON WAY ===================== -->
<div id="tab-way" class="content">
  <div class="cards">
    <div class="card yellow"><div class="label">Total</div><div class="value">{d["w_total"]}</div></div>
    <div class="card green"><div class="label">GMV Total</div><div class="value">${d["w_gmv"]:,.0f}</div></div>
    <div class="card red"><div class="label">Possivel Lost</div><div class="value">{d["w_sit"].get("Possivel Lost",0)}</div></div>
    <div class="card orange"><div class="label">&gt;= 11 dias OW</div><div class="value">{d["w_sit"].get(">= 11 dias OW",0)}</div></div>
    <div class="card blue"><div class="label">&lt; 11 dias OW</div><div class="value">{d["w_sit"].get("< 11 dias OW",0)}</div></div>
    <div class="card orange"><div class="label">CFTV Solicitado</div><div class="value">{d["w_cftv"]}</div></div>
  </div>
  {carrier_ranking_html(d["carrier_ranking_wy"])}
  <div class="tbl-wrap">
    {'<div style="background:#064e3b;border:1px solid #10b981;border-radius:6px;padding:8px 12px;margin-bottom:8px;font-size:12px;color:#6ee7b7"><strong>✓ ' + str(d["w_entregues"]) + ' pacote(s) detectado(s) como ENTREGUE no sistema</strong> — verifique e mova para Histórico como recupero.</div>' if d["w_entregues"] else ''}
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <span style="font-size:13px;color:#8899aa">{d["w_total"]} casos · USD {d["w_gmv"]:,.2f}</span>
      <a href="https://docs.google.com/spreadsheets/d/{PLANILHA_CONTROLE_ID}/edit"
         target="_blank"
         style="background:#1a3a5c;color:#5ba3d9;border:1px solid #2a5a8c;padding:5px 14px;border-radius:6px;font-size:12px;text-decoration:none">
        ✏ Editar na planilha
      </a>
    </div>
    <div id="tabulator-way"></div>
    <script>
    (function(){{
      var dataWY = {d['tab_wy_json']};
      new Tabulator("#tabulator-way", {{
        data: dataWY,
        layout: "fitDataStretch",
        pagination: "local",
        paginationSize: 30,
        paginationSizeSelector: [20, 30, 50, 100],
        movableColumns: true,
        initialSort: [{{column:"gmv", dir:"desc"}}],
        rowFormatter: function(row){{
          var data = row.getData();
          if(data.entregue) row.getElement().style.opacity = "0.5";
          if(data.sit && (data.sit.indexOf("Lost") >= 0 || data.sit.indexOf("11 dias") >= 0))
            row.getElement().style.borderLeft = "3px solid #e05252";
        }},
        columns: [
          {{title:"SHP ID", field:"id", headerFilter:"input", width:130, formatter:function(cell){{
            return '<span style="font-family:monospace;font-size:12px;color:#aac4e0">'+cell.getValue()+'</span>';
          }}}},
          {{title:"Situação", field:"sit", headerFilter:"select", headerFilterParams:{{values:true}}, width:160}},
          {{title:"GMV (USD)", field:"gmv", sorter:"number", formatter:"money", formatterParams:{{precision:2, symbol:"$"}}, width:110}},
          {{title:"Transportadora", field:"carrier", headerFilter:"input", width:140}},
          {{title:"Dias OW", field:"dias_ow", width:90}},
          {{title:"Ação LP", field:"acao_lp", headerFilter:"select", headerFilterParams:{{values:true}}, width:130}},
          {{title:"Status", field:"status", headerFilter:"select", headerFilterParams:{{values:true}}, width:140}},
          {{title:"Finalização", field:"finalizacao", headerFilter:"select", headerFilterParams:{{values:true}}, width:130}},
          {{title:"Dias Cart.", field:"dias_carteira", sorter:"number", width:90, formatter:function(cell){{
            var v = cell.getValue();
            if(v > 7) return '<span style="color:#e05252;font-weight:600">'+v+'d</span>';
            return v+'d';
          }}}},
          {{title:"CFTV", field:"cftv", width:80}},
          {{title:"Entrada", field:"entrada", width:100}},
          {{title:"Responsável", field:"resp", headerFilter:"select", headerFilterParams:{{values:true}}, width:150}},
        ],
      }});
    }})();
    </script>
  </div>
</div>

<!-- ===================== ABA AT STATION ===================== -->
<div id="tab-at_station" class="content">
  <div class="cards">
    <div class="card">
      <div class="card-header"><i data-lucide="warehouse" class="card-icon" width="14" height="14"></i><span class="card-label">Total At Station</span></div>
      <div class="card-value">{d["at_station"]["total"]}</div>
      <div class="card-delta">todos os tramos · SSP30</div>
    </div>
    <div class="card card-alert" style="border-color:#022c22;background:#060f0d">
      <div class="card-header"><i data-lucide="dollar-sign" class="card-icon" width="14" height="14" style="color:#064e3b"></i><span class="card-label">GMV Total</span></div>
      <div class="card-value val-ok">${d["at_station"]["gmv_total"]:,.0f}</div>
      <div class="card-delta">USD</div>
    </div>
    <div class="card card-alert">
      <div class="card-header"><i data-lucide="clock" class="card-icon" width="14" height="14" style="color:#7f1d1d"></i><span class="card-label">Críticos &gt;10 dias</span></div>
      <div class="card-value val-alert">{d["at_station"]["criticos"]}</div>
      <div class="card-delta">{"%.0f%%" % (d["at_station"]["criticos"]/d["at_station"]["total"]*100) if d["at_station"]["total"] else "0%"} do total</div>
    </div>
    <div class="card" style="border-color:rgba(251,191,36,.2);background:#0d0c00">
      <div class="card-header"><i data-lucide="shield-alert" class="card-icon" width="14" height="14" style="color:#fbbf24"></i><span class="card-label">Flag BPP</span></div>
      <div class="card-value" style="color:#fbbf24">{d["at_station"]["bpp_ct"]}</div>
      <div class="card-delta">{"%.0f%%" % (d["at_station"]["bpp_ct"]/d["at_station"]["total"]*100) if d["at_station"]["total"] else "0%"} com BPP</div>
    </div>
  </div>

  <div class="grid2 mb16">

    <!-- Aging buckets -->
    <div class="tbl-wrap" style="margin-bottom:0">
      <div class="tbl-title">Aging — Dias Estacado</div>
      <div class="tbl-scroll">
      <table id="tbl_as_aging">
        <thead><tr><th>Bucket</th><th>Qtd</th><th>%</th><th>GMV USD</th></tr></thead>
        <tbody>
          {"".join(
            f'<tr style="{"background:rgba(239,68,68,.08)" if i["label"] in ("11-30d","31d+") else ""}">'
            f'<td><b>{i["label"]}</b></td>'
            f'<td>{i["count"]}</td>'
            f'<td style="color:#9ca3af">{("%.1f%%" % (i["count"]/d["at_station"]["total"]*100)) if d["at_station"]["total"] else "—"}</td>'
            f'<td>${i["gmv"]:,.0f}</td></tr>'
            for i in d["at_station"]["aging"]
          )}
        </tbody>
      </table>
      </div>
    </div>

    <!-- Tramo breakdown -->
    <div class="tbl-wrap" style="margin-bottom:0">
      <div class="tbl-title">Distribuição por Tramo</div>
      <div class="tbl-scroll">
      <table id="tbl_as_tramo">
        <thead><tr><th>Tramo</th><th>Qtd</th><th>%</th><th>GMV USD</th></tr></thead>
        <tbody>
          {"".join(
            f'<tr><td><b>{i["label"]}</b></td>'
            f'<td>{i["count"]}</td>'
            f'<td style="color:#9ca3af">{("%.1f%%" % (i["count"]/d["at_station"]["total"]*100)) if d["at_station"]["total"] else "—"}</td>'
            f'<td>${i["gmv"]:,.0f}</td></tr>'
            for i in d["at_station"]["tramos"]
          )}
        </tbody>
      </table>
      </div>
    </div>

  </div>

  <div class="grid2 mb16">

    <!-- Ação LP -->
    <div class="tbl-wrap" style="margin-bottom:0">
      <div class="tbl-title">Ação LP</div>
      <div class="tbl-scroll">
      <table id="tbl_as_acao">
        <thead><tr><th>Ação</th><th>Qtd</th><th>%</th><th>GMV USD</th></tr></thead>
        <tbody>
          {"".join(
            f'<tr><td>{i["label"]}</td>'
            f'<td>{i["count"]}</td>'
            f'<td style="color:#9ca3af">{("%.1f%%" % (i["count"]/d["at_station"]["total"]*100)) if d["at_station"]["total"] else "—"}</td>'
            f'<td>${i["gmv"]:,.0f}</td></tr>'
            for i in d["at_station"]["acao"]
          )}
        </tbody>
      </table>
      </div>
    </div>

    <!-- Risk Classification -->
    <div class="tbl-wrap" style="margin-bottom:0">
      <div class="tbl-title">Classificação de Risco (BPP)</div>
      <div class="tbl-scroll">
      <table id="tbl_as_risk">
        <thead><tr><th>Risco</th><th>Qtd</th><th>%</th><th>GMV USD</th></tr></thead>
        <tbody>
          {"".join(
            f'<tr><td>{i["label"]}</td>'
            f'<td>{i["count"]}</td>'
            f'<td style="color:#9ca3af">{("%.1f%%" % (i["count"]/d["at_station"]["total"]*100)) if d["at_station"]["total"] else "—"}</td>'
            f'<td>${i["gmv"]:,.0f}</td></tr>'
            for i in d["at_station"]["risk"]
          )}
        </tbody>
      </table>
      </div>
    </div>

  </div>

  <!-- Top pacotes por GMV -->
  <div class="tbl-wrap">
    <div class="tbl-title">Top 50 Pacotes — At Station (por GMV)</div>
    <div id="tabulator-as-pkgs"></div>
    <script>
    (function(){{
      var dataAS = {d['tab_as_pkgs_json']};
      new Tabulator("#tabulator-as-pkgs", {{
        data: dataAS,
        layout: "fitDataStretch",
        pagination: "local",
        paginationSize: 50,
        paginationSizeSelector: [20, 50, 100],
        movableColumns: true,
        initialSort: [{{column:"usd", dir:"desc"}}],
        rowFormatter: function(row){{
          var data = row.getData();
          if(data.dias > 10) row.getElement().style.borderLeft = "3px solid #ef4444";
        }},
        columns: [
          {{title:"SHP ID", field:"shp_id", headerFilter:"input", width:140,
            formatter:function(cell){{
              var v = cell.getValue();
              return '<a href="https://www.mercadolivre.com.br/envios/pacote/'+v+'" target="_blank" style="color:#60a5fa;text-decoration:none;font-family:monospace">'+v+'</a>';
            }}}},
          {{title:"Tramo", field:"tramo", headerFilter:"select", headerFilterParams:{{values:true}}, width:90}},
          {{title:"Ação LP", field:"acao", headerFilter:"select", headerFilterParams:{{values:true}}, width:160}},
          {{title:"Risco", field:"risk", headerFilter:"select", headerFilterParams:{{values:true}}, width:100}},
          {{title:"Dias", field:"dias", sorter:"number", width:80, hozAlign:"center",
            formatter:function(cell){{
              var v = cell.getValue();
              if(v > 10) return '<span style="color:#ef4444;font-weight:700">'+v+'d</span>';
              return '<span style="color:#9ca3af">'+v+'d</span>';
            }}}},
          {{title:"GMV (USD)", field:"usd", sorter:"number", formatter:"money", formatterParams:{{precision:0, symbol:"$"}}, width:120}},
        ],
      }});
    }})();
    </script>
  </div>
</div>

<!-- ===================== ABA 5: HISTÓRICO ===================== -->
<div id="tab-hist" class="content">
  <div class="cards">
    <div class="card card-ok">
      <div class="card-header"><i data-lucide="award" class="card-icon" width="14" height="14" style="color:#064e3b"></i><span class="card-label">Recuperados {d["mes_lbl"]}</span></div>
      <div class="card-value val-ok">{d["recuperados"]}</div>
      <div class="card-delta">Seguiram fluxo correto</div>
    </div>
    <div class="card card-ok card-link" onclick="irPara('hist')">
      <div class="card-header"><i data-lucide="trending-up" class="card-icon" width="14" height="14" style="color:#064e3b"></i><span class="card-label">GMV do Mês</span></div>
      <div class="card-value val-ok">${d["gmv_recuperado"]:,.0f}</div>
      <div class="card-delta">
        <span style="color:#10b981">↑ ${d["gmv_recuperado"]:,.0f} recuperado</span><br>
        <span style="color:#ef4444">↓ ${d["gmv_perdido"]:,.0f} perdido</span>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="percent" class="card-icon" width="14" height="14"></i><span class="card-label">Taxa de Recupero</span></div>
      <div class="card-value">{d["taxa_recupero"]}%</div>
      <div class="card-delta">{d["recuperados"]} de {d["removidos"]} removidos</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="archive" class="card-icon" width="14" height="14"></i><span class="card-label">Total no Histórico</span></div>
      <div class="card-value">{len(d["hist_todos"])}</div>
      <div class="card-delta">todos os meses</div>
    </div>
  </div>

  <!-- Seletor de mês -->
  <div class="mes-selector">
    <button class="mes-btn" data-mes="" onclick="filtrarMes('')">Todos</button>
    {''.join(f'<button class="mes-btn" data-mes="{m["val"]}" onclick="filtrarMes(\'{m["val"]}\')">{m["lbl"]}</button>' for m in d["meses_hist"])}
  </div>

  <!-- Filtro por intervalo de datas -->
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">
    <span style="font-size:12px;color:#94a3b8">Período:</span>
    <input type="date" id="hist-de"  onchange="filtrarHistRange()" style="background:#1e293b;border:1px solid #334155;border-radius:6px;padding:4px 8px;color:#e2e8f0;font-size:12px">
    <span style="font-size:12px;color:#64748b">até</span>
    <input type="date" id="hist-ate" onchange="filtrarHistRange()" style="background:#1e293b;border:1px solid #334155;border-radius:6px;padding:4px 8px;color:#e2e8f0;font-size:12px">
    <button onclick="limparFiltroHist()" style="background:transparent;border:1px solid #334155;border-radius:6px;padding:4px 10px;color:#94a3b8;font-size:11px;cursor:pointer">✕ Limpar</button>
    <span id="hist-count" style="font-size:11px;color:#64748b"></span>
  </div>

  <div class="tbl-wrap">
    <div id="tabulator-hist"></div>
    <script>
    (function(){{
      var dataHist = {d['tab_hist_json']};
      new Tabulator("#tabulator-hist", {{
        data: dataHist,
        layout: "fitDataStretch",
        pagination: "local",
        paginationSize: 50,
        paginationSizeSelector: [20, 50, 100, 200],
        initialSort: [{{column:"data", dir:"desc"}}],
        columns: [
          {{title:"Data", field:"data", width:100}},
          {{title:"Origem", field:"origem", headerFilter:"select", headerFilterParams:{{values:true}}, width:100}},
          {{title:"SHP ID", field:"id", headerFilter:"input", width:130}},
          {{title:"Situação", field:"sit", headerFilter:"select", headerFilterParams:{{values:true}}, width:160}},
          {{title:"GMV", field:"gmv", width:100}},
          {{title:"Responsável", field:"resp", headerFilter:"select", headerFilterParams:{{values:true}}, width:150}},
          {{title:"Status", field:"status", headerFilter:"select", headerFilterParams:{{values:true}}, width:140}},
          {{title:"Finalização", field:"final", headerFilter:"select", headerFilterParams:{{values:true}}, width:130}},
        ],
      }});
    }})();
    </script>
  </div>
</div>

<!-- ===================== ABA: PLACES (NEX/DC) ===================== -->
<div id="tab-places" class="content">
  <div class="cards">
    <div class="card">
      <div class="card-header"><i data-lucide="map-pin" class="card-icon" width="14" height="14"></i><span class="card-label">Total Places</span></div>
      <div class="card-value">{d["places"]["total"]}</div>
      <div class="card-delta">NEX + DC · SSP30</div>
    </div>
    <div class="card" style="border-color:rgba(96,165,250,.2)">
      <div class="card-header"><i data-lucide="navigation" class="card-icon" width="14" height="14" style="color:#60a5fa"></i><span class="card-label">NEX</span></div>
      <div class="card-value" style="color:#60a5fa">{d["places"]["nex"]}</div>
      <div class="card-delta">${d["places"]["gmv_nex"]:,.0f} USD</div>
    </div>
    <div class="card" style="border-color:rgba(167,139,250,.2)">
      <div class="card-header"><i data-lucide="building-2" class="card-icon" width="14" height="14" style="color:#a78bfa"></i><span class="card-label">DC</span></div>
      <div class="card-value" style="color:#a78bfa">{d["places"]["dc"]}</div>
      <div class="card-delta">${d["places"]["gmv_dc"]:,.0f} USD</div>
    </div>
    <div class="card card-alert">
      <div class="card-header"><i data-lucide="alert-triangle" class="card-icon" width="14" height="14" style="color:#7f1d1d"></i><span class="card-label">Crítico</span></div>
      <div class="card-value val-alert">{d["places"]["critico"]}</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="alert-circle" class="card-icon" width="14" height="14" style="color:#f59e0b"></i><span class="card-label">Alto</span></div>
      <div class="card-value val-warn">{d["places"]["alto"]}</div>
    </div>
    <div class="card card-alert" style="border-color:#022c22;background:#060f0d">
      <div class="card-header"><i data-lucide="dollar-sign" class="card-icon" width="14" height="14" style="color:#064e3b"></i><span class="card-label">GMV Total</span></div>
      <div class="card-value val-ok">${d["places"]["gmv_total"]:,.0f}</div>
    </div>
    <div class="card" style="border-color:rgba(251,191,36,.2);background:#0d0c00">
      <div class="card-header"><i data-lucide="eye-off" class="card-icon" width="14" height="14" style="color:#fbbf24"></i><span class="card-label">DIT Blind Spot</span></div>
      <div class="card-value" style="color:#fbbf24">{d["places"]["dit_blind"]}</div>
      <div class="card-delta">{d["places"]["dit_stuck"]} presos no place</div>
    </div>
  </div>

  <div class="grid2 mb16">
    <div class="box"><div class="box-title">Pacotes por Ação</div><div style="position:relative;height:220px"><canvas id="cPlAcao"></canvas></div></div>
    <div class="box"><div class="box-title">Distribuição NEX / DC</div><div style="position:relative;height:220px"><canvas id="cPlTramo"></canvas></div></div>
  </div>

  <script>
    const PLACES_REPORT = {json.dumps(gerar_report_places_txt(d), ensure_ascii=False)};
    const OTR_TXT = {json.dumps(d["places"]["otr_txt"], ensure_ascii=False)};
  </script>

  <!-- SECAO OTR -->
  <div class="tbl-wrap" style="margin-bottom:18px;border-color:{'rgba(239,68,68,.3)' if d['places']['otr_imediato'] else 'rgba(251,191,36,.2)'}">
    <div class="tbl-title" style="display:flex;align-items:center;justify-content:space-between;background:{'rgba(127,29,29,.25)' if d['places']['otr_imediato'] else 'rgba(113,63,18,.15)'}">
      <span style="display:flex;align-items:center;gap:8px">
        {'<span style="width:8px;height:8px;background:#ef4444;border-radius:50%;display:inline-block;animation:pulse 1.5s infinite"></span>' if d['places']['otr_imediato'] else ''}
        Lista OTR — Places com acumulo DIT
        {f'<span style="background:#7f1d1d;color:#fca5a5;font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px">{d["places"]["otr_imediato"]} IMEDIATO</span>' if d['places']['otr_imediato'] else ''}
      </span>
      <button id="btn-otr-copy" onclick="copiarOTR()"
        style="background:rgba(251,191,36,.1);border:1px solid rgba(251,191,36,.3);color:#fbbf24;
               border-radius:6px;padding:5px 14px;cursor:pointer;font-size:11px;font-weight:600;
               display:flex;align-items:center;gap:6px">
        Copiar para OTR
      </button>
    </div>
    <div class="tbl-scroll">
    <table id="tbl_otr" style="min-width:500px">
      <thead><tr>
        <th>Nivel</th><th>Place ID</th><th>Tipo</th>
        <th class="sortable" onclick="sortTable('tbl_otr',3)">DIT s/ flag</th>
        <th class="sortable" onclick="sortTable('tbl_otr',4)">Avg dias</th>
        <th>Preso no place</th>
        <th style="width:24px"></th>
      </tr></thead>
      <tbody>{rows_otr_section(d["places"]["otr_list"])}</tbody>
    </table>
    </div>
  </div>

  <div class="tbl-wrap" style="margin-bottom:18px">
    <div class="tbl-title" style="display:flex;align-items:center;justify-content:space-between">
      <span>Places Ofensores — Ranking por GMV (SSP30)</span>
      <button id="btn-report-places" onclick="copiarReportPlaces()"
        style="background:rgba(255,230,0,.1);border:1px solid rgba(255,230,0,.3);color:#FFE600;
               border-radius:6px;padding:5px 14px;cursor:pointer;font-size:11px;font-weight:600;
               display:flex;align-items:center;gap:6px">
        📋 Copiar Relatório
      </button>
    </div>
    <div id="tabulator-places-rank"></div>
    <script>
    (function(){{
      var dataPlRank = {d['tab_places_rank_json']};
      new Tabulator("#tabulator-places-rank", {{
        data: dataPlRank,
        layout: "fitDataStretch",
        pagination: "local",
        paginationSize: 30,
        movableColumns: true,
        initialSort: [{{column:"gmv", dir:"desc"}}],
        rowFormatter: function(row){{
          var data = row.getData();
          if(data.gmv_pkg >= 300) row.getElement().style.background = '#1a0808';
          else if(data.max_dias >= 20) row.getElement().style.background = '#160f04';
        }},
        columns: [
          {{title:"#", field:"rank", width:50, hozAlign:"center", sorter:"number",
            formatter:function(cell){{ return '<span style="background:#1f2937;color:#9ca3af;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px">#'+cell.getValue()+'</span>'; }}}},
          {{title:"Place ID", field:"place_id", headerFilter:"input", width:140,
            formatter:function(cell){{ return '<span style="color:#a78bfa;font-family:monospace;font-size:12px;font-weight:600">'+cell.getValue()+'</span>'; }}}},
          {{title:"Tramo", field:"tramo", headerFilter:"select", headerFilterParams:{{values:true}}, width:90}},
          {{title:"SHP", field:"qtd", sorter:"number", width:70, hozAlign:"center"}},
          {{title:"GMV Total", field:"gmv", sorter:"number", formatter:"money", formatterParams:{{precision:2, symbol:"$"}}, width:120}},
          {{title:"GMV/pkg", field:"gmv_pkg", sorter:"number", formatter:"money", formatterParams:{{precision:2, symbol:"$"}}, width:110,
            formatter:function(cell){{
              var v = cell.getValue();
              var cl = v >= 300 ? '#f87171' : (v >= 100 ? '#fbbf24' : '#9ca3af');
              return '<span style="color:'+cl+';font-weight:700">$'+v.toFixed(2)+'</span>';
            }}}},
          {{title:"Max Dias", field:"max_dias", sorter:"number", width:90, hozAlign:"center",
            formatter:function(cell){{
              var v = cell.getValue();
              if(v > 7) return '<span style="color:#e05252;font-weight:600">'+v+'d</span>';
              return v+'d';
            }}}},
          {{title:"Avg Dias", field:"avg_dias", sorter:"number", width:90, hozAlign:"center",
            formatter:function(cell){{
              var v = cell.getValue();
              if(v > 7) return '<span style="color:#e05252;font-weight:600">'+v.toFixed(1)+'d</span>';
              return v.toFixed(1)+'d';
            }}}},
          {{title:"DIT s/ flag", field:"dit_blind", sorter:"number", width:100, hozAlign:"center",
            formatter:function(cell){{
              var v = cell.getValue();
              if(!v) return '<span style="color:#1f2937">—</span>';
              var cl = v >= 200 ? '#f87171' : (v >= 50 ? '#fbbf24' : '#9ca3af');
              return '<span style="color:'+cl+';font-weight:700">'+v+'</span>';
            }}}},
          {{title:"SHP IDs", field:"pkgs_ids", headerFilter:"input", width:200,
            formatter:function(cell){{ return '<span style="font-size:10px;color:#6b7280">'+cell.getValue()+'</span>'; }}}},
        ],
      }});
    }})();
    </script>
  </div>

  <div class="tbl-wrap">
    <div class="tbl-title" style="display:flex;align-items:center;justify-content:space-between">
      <span>📍 Pacotes nos Places (NEX + DC) — SSP30 · ordenados por GMV</span>
      <button onclick="window._tabPlaces && window._tabPlaces.download('csv', 'places_ssp30.csv')" class="btn-export">⬇ Exportar CSV</button>
    </div>
    <div id="tabulator-places"></div>
    <script>
    (function(){{
      var dataPl = {d['tab_places_json']};
      window._tabPlaces = new Tabulator("#tabulator-places", {{
        data: dataPl,
        layout: "fitDataStretch",
        pagination: "local",
        paginationSize: 50,
        paginationSizeSelector: [20, 50, 100, 200],
        movableColumns: true,
        initialSort: [{{column:"gmv", dir:"desc"}}],
        rowFormatter: function(row){{
          var data = row.getData();
          var rk = (data.risk || '').toLowerCase();
          if(rk.indexOf('cr') >= 0) row.getElement().style.background = '#1a0808';
          else if(rk.indexOf('alt') >= 0) row.getElement().style.background = '#160f04';
        }},
        columns: [
          {{title:"SHP ID", field:"shp_id", headerFilter:"input", width:130,
            formatter:function(cell){{
              return '<span style="font-family:monospace;font-size:12px;color:#aac4e0">'+cell.getValue()+'</span>';
            }}}},
          {{title:"Tramo", field:"tramo", headerFilter:"select", headerFilterParams:{{values:true}}, width:90}},
          {{title:"Place ID", field:"place_id", headerFilter:"input", width:130,
            formatter:function(cell){{ return '<span style="color:#a78bfa;font-size:11px;font-family:monospace">'+cell.getValue()+'</span>'; }}}},
          {{title:"Ação", field:"acao", headerFilter:"select", headerFilterParams:{{values:true}}, width:160}},
          {{title:"Risco", field:"risk", headerFilter:"select", headerFilterParams:{{values:true}}, width:100}},
          {{title:"Dias S/ Mov", field:"dias", sorter:"number", width:100, hozAlign:"center",
            formatter:function(cell){{
              var v = cell.getValue();
              if(v > 7) return '<span style="color:#e05252;font-weight:600">'+v+'d</span>';
              return v >= 0 ? v+'d' : '—';
            }}}},
          {{title:"GMV (USD)", field:"gmv", sorter:"number", formatter:"money", formatterParams:{{precision:2, symbol:"$"}}, width:120}},
          {{title:"Retorno Ativo", field:"retorno", headerFilter:"select", headerFilterParams:{{values:true}}, width:130}},
          {{title:"Transportadora", field:"carrier", headerFilter:"input", width:130}},
          {{title:"Rota ID", field:"rota_id", headerFilter:"input", width:110,
            formatter:function(cell){{
              var v = cell.getValue();
              if(!v || v === 'None' || v === '') return '—';
              return '<a href="https://envios.adminml.com/logistics/monitoring-distribution/detail/'+v+'?site=MLB" target="_blank" style="color:#4ade80;text-decoration:none;font-family:monospace;font-size:11px">'+v+'</a>';
            }}}},
          {{title:"Último Status", field:"chk_dt", width:130,
            formatter:function(cell){{ return '<span style="font-size:10px;color:#64748b">'+cell.getValue()+'</span>'; }}}},
        ],
      }});
    }})();
    </script>
  </div>
</div>

<!-- Modal nova atividade extra -->
<div id="db-modal-bg" class="db-modal-bg" onclick="if(event.target===this)dbFecharModal()">
  <div class="db-modal">
    <div style="font-size:14px;font-weight:600;color:#F9FAFB;margin-bottom:14px">Nova Atividade Extra</div>
    <input id="db-m-atv" class="db-modal-inp" placeholder="Descrição da atividade *">
    <div style="display:flex;gap:8px;margin-bottom:8px">
      <input id="db-m-ini" type="time" class="db-modal-inp" style="flex:1;margin-bottom:0" placeholder="Início">
      <input id="db-m-fim" type="time" class="db-modal-inp" style="flex:1;margin-bottom:0" placeholder="Fim">
    </div>
    <textarea id="db-m-obs" class="db-modal-inp" rows="2" placeholder="Observações (opcional)" style="resize:vertical;margin-top:8px"></textarea>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
      <button onclick="dbFecharModal()" style="padding:6px 14px;background:transparent;border:1px solid #374151;border-radius:6px;color:#9CA3AF;font-size:12px;cursor:pointer">Cancelar</button>
      <button onclick="dbSalvarExtra()" style="padding:6px 14px;background:#10B981;border:none;border-radius:6px;color:#fff;font-size:12px;cursor:pointer;font-weight:500">Salvar</button>
    </div>
  </div>
</div>

<!-- ===================== SCRIPTS ===================== -->
<script>
// Troca de abas + atualiza URL hash para link direto
const TAB_ORDER = ['geral','criticos','route','way','places','gmv','hist'];
function showTab(name, el) {{
  document.querySelectorAll('.content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.sb-item').forEach(e => e.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  el.classList.add('active');
  history.replaceState(null, '', '#' + name);
  if (name === 'places') initPlCharts();
  if (name === 'way' || name === 'route') carregarValoresOW();
}}

// Abre aba pelo hash da URL (ex: #criticos)
window.addEventListener('load', () => {{
  const hash = window.location.hash.replace('#','');
  if (TAB_ORDER.includes(hash)) {{
    const el = document.querySelector(`.sb-item[data-tab="${{hash}}"]`);
    if (el) showTab(hash, el);
  }}
  if (!hash || hash === 'way' || hash === 'route') carregarValoresOW();
}});

// Navega para uma aba ao clicar num card
function irPara(tabName) {{
  const el = document.querySelector(`.sb-item[data-tab="${{tabName}}"]`);
  if (el) {{ showTab(tabName, el); window.scrollTo({{top:0, behavior:'smooth'}}); }}
}}

// Copia lista OTR para clipboard
function copiarOTR() {{
  if (!navigator.clipboard) return;
  navigator.clipboard.writeText(OTR_TXT).then(() => {{
    const btn = document.getElementById('btn-otr-copy');
    if (!btn) return;
    const orig = btn.innerHTML;
    btn.innerHTML = 'Copiado!';
    btn.style.background = 'rgba(74,222,128,.15)';
    btn.style.borderColor = 'rgba(74,222,128,.4)';
    btn.style.color = '#4ade80';
    setTimeout(() => {{ btn.innerHTML = orig; btn.style = ''; }}, 2000);
  }});
}}

// Copia relatório de places para clipboard
function copiarReportPlaces() {{
  if (!navigator.clipboard) return;
  navigator.clipboard.writeText(PLACES_REPORT).then(() => {{
    const btn = document.getElementById('btn-report-places');
    if (!btn) return;
    const orig = btn.innerHTML;
    btn.innerHTML = '✅ Copiado!';
    btn.style.background = 'rgba(74,222,128,.15)';
    btn.style.borderColor = 'rgba(74,222,128,.4)';
    btn.style.color = '#4ade80';
    setTimeout(() => {{ btn.innerHTML = orig; btn.style.background = ''; btn.style.borderColor = ''; btn.style.color = '#FFE600'; }}, 2500);
  }});
}}

// Carrega valores atuais da planilha e preenche campos editáveis da aba ON WAY
async function carregarValoresOW() {{
  try {{
    const [valsWy, valsRt] = await Promise.all([
      api('ow_values', {{tab:'wy'}}),
      api('ow_values', {{tab:'rt'}}),
    ]);

    // ON WAY — cols 23(acao) 24(link) 29(status) 30(final)
    document.querySelectorAll('#tbl_way .ow-edit').forEach(el => {{
      const shp = el.dataset.shp, col = String(el.dataset.col);
      const v = valsWy[shp];
      if (!v) return;
      let val = col === '23' ? v.acao : col === '24' ? v.link : col === '29' ? v.status : col === '30' ? v.final : undefined;
      if (val !== undefined && val !== null) {{
        el.value = val;
        if (el.tagName === 'SELECT') owUpdateFake(el);
      }}
    }});
    // Atualiza botões de link (↗)
    document.querySelectorAll('#tbl_way input[data-col="24"]').forEach(inp => {{
      const wrap = inp.closest('.ow-edit-wrap');
      if (!wrap) return;
      let btn = wrap.querySelector('.ow-link-btn');
      if (inp.value) {{
        if (!btn) {{ btn = document.createElement('a'); btn.className = 'ow-link-btn'; btn.title = 'Abrir email'; btn.textContent = '↗'; btn.target = '_blank'; wrap.appendChild(btn); }}
        btn.href = inp.value;
      }} else if (btn) {{ btn.remove(); }}
    }});

    // ON ROUTE — cols 24(acao) 29(status) 30(final)
    document.querySelectorAll('#tbl_route .ow-edit').forEach(el => {{
      const shp = el.dataset.shp, col = String(el.dataset.col);
      const v = valsRt[shp];
      if (!v) return;
      let val = col === '24' ? v.acao : col === '29' ? v.status : col === '30' ? v.final : undefined;
      if (val !== undefined && val !== null) {{
        el.value = val;
        if (el.tagName === 'SELECT') owUpdateFake(el);
      }}
    }});
  }} catch(e) {{ /* servidor offline — silencioso */ }}
}}

// Expand/collapse IDs de um place na tabela OTR
function toggleOtrRow(pid) {{
  const row = document.getElementById('otr-d-' + pid);
  const btn = document.getElementById('otr-btn-' + pid);
  if (!row) return;
  const open = row.style.display !== 'none';
  row.style.display = open ? 'none' : 'table-row';
  if (btn) {{ btn.textContent = open ? '›' : '⌄'; btn.style.color = open ? '#374151' : '#a78bfa'; }}
}}

// Expand/collapse pacotes de um place no ranking
function togglePlaceRow(pid) {{
  const row = document.getElementById('pdr-' + pid);
  const btn = document.getElementById('pbtn-' + pid);
  if (!row) return;
  const open = row.style.display !== 'none';
  row.style.display = open ? 'none' : 'table-row';
  if (btn) {{ btn.textContent = open ? '›' : '⌄'; btn.style.color = open ? '#374151' : '#a78bfa'; }}
}}

// Filtro por mês no Histórico
function filtrarMes(mes) {{
  const de  = document.getElementById('hist-de');
  const ate = document.getElementById('hist-ate');
  if (de)  de.value  = '';
  if (ate) ate.value = '';
  let count = 0;
  document.querySelectorAll('.hist-row').forEach(tr => {{
    const show = (!mes || tr.dataset.mes === mes);
    tr.style.display = show ? '' : 'none';
    if (show) count++;
  }});
  document.querySelectorAll('#tab-hist .mes-btn').forEach(btn => {{
    btn.classList.toggle('mes-ativo', btn.dataset.mes === mes);
  }});
  const cnt = document.getElementById('hist-count');
  if (cnt) cnt.textContent = count + ' registros';
}}

function filtrarHistRange() {{
  const de  = (document.getElementById('hist-de')?.value  || '').trim();
  const ate = (document.getElementById('hist-ate')?.value || '').trim();
  if (!de && !ate) {{
    const ativo = document.querySelector('#tab-hist .mes-btn.mes-ativo');
    filtrarMes(ativo ? ativo.dataset.mes : '');
    return;
  }}
  document.querySelectorAll('#tab-hist .mes-btn').forEach(b => b.classList.remove('mes-ativo'));
  const todos = document.querySelector('#tab-hist .mes-btn[data-mes=""]');
  if (todos) todos.classList.add('mes-ativo');
  let count = 0;
  document.querySelectorAll('.hist-row').forEach(tr => {{
    const d = tr.dataset.data || '';
    const show = (!de || d >= de) && (!ate || d <= ate);
    tr.style.display = show ? '' : 'none';
    if (show) count++;
  }});
  const cnt = document.getElementById('hist-count');
  if (cnt) cnt.textContent = count + ' registros';
}}

function limparFiltroHist() {{
  const de  = document.getElementById('hist-de');
  const ate = document.getElementById('hist-ate');
  if (de)  de.value  = '';
  if (ate) ate.value = '';
  const cnt = document.getElementById('hist-count');
  if (cnt) cnt.textContent = '';
  filtrarMes('{d["mes_ano"]}');
}}

// Abre no mês atual por padrão
filtrarMes('{d["mes_ano"]}');

// Filtro de período — Visão Geral
const _MONTHLY_STATS  = {j(_monthly_stats)};
const _MESES_LBL      = {_meses_lbl_js};
function fmtUSD(v) {{ return '$' + Math.round(v).toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ','); }}
const _MES_ATUAL = '{d["mes_ano"]}';
// Captura valores ao vivo do mês vigente ao carregar a página
const _LIVE = {{}};
document.addEventListener('DOMContentLoaded', () => {{
  ['cv-rt','cd-rt','cv-wy','cd-wy','cv-gmv-rt','cv-gmv-wy','cv-gmv-risco'].forEach(id => {{
    const el = document.getElementById(id);
    if (el) _LIVE[id] = el.innerHTML;
  }});
}});
function filtrarVisaoMes(mes) {{
  const s    = _MONTHLY_STATS[mes] || {{r:0, gmv_r:0, gmv_p:0, rem:0, rt:0, wy:0, gmv_rt:0, gmv_wy:0}};
  const taxa = s.rem > 0 ? (s.r / s.rem * 100).toFixed(1) : '0.0';
  const lbl  = _MESES_LBL[mes] || mes;
  const g = document.getElementById.bind(document);

  // Cards históricos
  const elRec = g('cv-rec'), elCRec = g('cd-rec');
  const elGmv = g('cv-gmv-mes'), elCGmv = g('cd-gmv-mes');
  const elTax = g('cv-taxa'), elCTax = g('cd-taxa');
  if (elRec)  elRec.textContent  = s.r;
  if (elCRec) elCRec.textContent = lbl + ' · Seguiram fluxo correto';
  if (elGmv)  elGmv.textContent  = fmtUSD(s.gmv_r);
  if (elCGmv) elCGmv.innerHTML   = '<span style="color:#10b981">↑ ' + fmtUSD(s.gmv_r) + ' recuperado</span><br><span style="color:#ef4444">↓ ' + fmtUSD(s.gmv_p) + ' perdido</span>';
  if (elTax)  elTax.textContent  = taxa + '%';
  if (elCTax) elCTax.textContent = s.r + ' de ' + s.rem + ' removidos';

  // Cards de volume
  const elRt = g('cv-rt'), elCRt = g('cd-rt');
  const elWy = g('cv-wy'), elCWy = g('cd-wy');
  const elGrt = g('cv-gmv-rt'), elGwy = g('cv-gmv-wy'), elGrisk = g('cv-gmv-risco');
  if (mes === _MES_ATUAL) {{
    ['cv-rt','cd-rt','cv-wy','cd-wy','cv-gmv-rt','cv-gmv-wy','cv-gmv-risco'].forEach(id => {{
      const el = g(id); if (el && _LIVE[id] !== undefined) el.innerHTML = _LIVE[id];
    }});
  }} else {{
    if (elRt)    elRt.textContent   = s.rt;
    if (elCRt)   elCRt.textContent  = 'casos finalizados em ' + lbl;
    if (elWy)    elWy.textContent   = s.wy;
    if (elCWy)   elCWy.textContent  = 'casos finalizados em ' + lbl;
    if (elGrt)   elGrt.textContent  = fmtUSD(s.gmv_rt);
    if (elGwy)   elGwy.textContent  = fmtUSD(s.gmv_wy);
    if (elGrisk) elGrisk.textContent = fmtUSD(s.gmv_rt + s.gmv_wy);
  }}
  document.querySelectorAll('.vg-mes-btn').forEach(b => b.classList.toggle('mes-ativo', b.dataset.mes === mes));
}}

// Filtros das tabelas
function filtrar(tabId) {{
  const busca  = (document.getElementById('busca_'  + tabId)?.value || '').toLowerCase();
  const sit    = (document.getElementById('sit_'    + tabId)?.value || '').toLowerCase();
  const status = (document.getElementById('status_' + tabId)?.value || '').toLowerCase();
  document.querySelectorAll('#tbl_' + tabId + ' .data-row').forEach(tr => {{
    const id   = tr.dataset.id    || '';
    const rs   = tr.dataset.sit   || '';
    const st   = tr.dataset.status|| '';
    const resp = tr.dataset.resp  || '';
    const matchBusca  = !busca  || id.includes(busca)  || resp.includes(busca);
    const matchSit    = !sit    || rs.includes(sit);
    const matchStatus = !status || (status === '__sem__' ? !st.trim() : st.includes(status));
    tr.style.display = (matchBusca && matchSit && matchStatus) ? '' : 'none';
  }});
}}

// Exportar CSV
function exportCSV(tabId, filename) {{
  const rows = document.querySelectorAll('#tbl_' + tabId + ' tr');
  let csv = '';
  rows.forEach(row => {{
    if (row.style.display === 'none') return;
    const cols = Array.from(row.querySelectorAll('th,td'))
      .map(c => '"' + c.textContent.trim().replace(/"/g,'""') + '"').join(',');
    csv += cols + '\\n';
  }});
  const blob = new Blob(['\\uFEFF' + csv], {{type:'text/csv;charset=utf-8;'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}}

// Ordenação de colunas
const sortState = {{}};
function sortTable(tblId, colIdx) {{
  const tbl  = document.getElementById(tblId);
  const tbody= tbl.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const key  = tblId + '_' + colIdx;
  const asc  = sortState[key] !== true;
  sortState[key] = asc;

  // atualiza ícones
  tbl.querySelectorAll('th').forEach((th,i) => {{
    th.classList.remove('sort-asc','sort-desc');
    if (i === colIdx) th.classList.add(asc ? 'sort-asc' : 'sort-desc');
  }});

  rows.sort((a, b) => {{
    const ta = a.cells[colIdx]?.textContent.trim() || '';
    const tb = b.cells[colIdx]?.textContent.trim() || '';
    // tenta numérico (remove $, d, ,)
    const na = parseFloat(ta.replace(/[$,d]/g,''));
    const nb = parseFloat(tb.replace(/[$,d]/g,''));
    if (!isNaN(na) && !isNaN(nb)) return asc ? na-nb : nb-na;
    return asc ? ta.localeCompare(tb,'pt-BR') : tb.localeCompare(ta,'pt-BR');
  }});
  rows.forEach(r => tbody.appendChild(r));
}}

// Countdown para próxima atualização (08:00 BRT = 11:00 UTC)
function updateCountdown() {{
  const now  = new Date();
  let next   = new Date();
  next.setUTCHours(11, 0, 0, 0);
  if (now >= next) next.setUTCDate(next.getUTCDate() + 1);
  const diff = next - now;
  const h    = Math.floor(diff / 3600000);
  const m    = Math.floor((diff % 3600000) / 60000);
  document.getElementById('countdown').textContent =
    h > 0 ? `Próx. atualização em ${{h}}h ${{m}}min` : `Próx. atualização em ${{m}}min`;
}}
setInterval(updateCountdown, 60000);
updateCountdown();

// Tooltip global dark
Chart.defaults.plugins.tooltip.backgroundColor = '#0d1321';
Chart.defaults.plugins.tooltip.titleColor      = '#f9fafb';
Chart.defaults.plugins.tooltip.bodyColor       = '#9ca3af';
Chart.defaults.plugins.tooltip.borderColor     = '#1f2937';
Chart.defaults.plugins.tooltip.borderWidth     = 1;
Chart.defaults.plugins.tooltip.cornerRadius    = 6;
Chart.defaults.plugins.tooltip.padding         = 10;
Chart.defaults.plugins.tooltip.titleFont       = {{size:12, weight:'600'}};
Chart.defaults.plugins.tooltip.bodyFont        = {{size:12}};

// Empty state: mostra msg se todos os valores forem zero
function checkEmpty(canvasId, chart) {{
  const allZero = chart.data.datasets.every(ds => ds.data.every(v => !v || v === 0));
  if (allZero) {{
    const el = document.getElementById(canvasId);
    const wrap = el.parentElement;
    wrap.classList.add('chart-wrap');
    if (!wrap.querySelector('.empty-msg')) {{
      wrap.insertAdjacentHTML('beforeend',
        '<div class="empty-msg">Nenhum registro para exibir</div>');
    }}
  }}
}}

// Opções padrão dos gráficos
const defOpts = {{
  responsive: true,
  plugins: {{ legend: {{ labels: {{ color:'#94a3b8', font:{{ size:12 }} }} }} }},
}};

// Clique no gráfico → filtra tabela e muda de aba
function onClickChart(evt, elements, chart, tabId, sitSelectId) {{
  if (!elements.length) return;
  const label = chart.data.labels[elements[0].index];
  const _el = document.querySelector(`.sb-item[data-tab="${{tabId}}"]`); if (_el) showTab(tabId, _el);
  const sel = document.getElementById(sitSelectId);
  if (sel) {{ sel.value = label.toLowerCase(); filtrar(tabId); }}
}}

// Doughnut ON ROUTE
new Chart(document.getElementById('cSitRt'), {{
  type: 'doughnut',
  data: {{ labels:{sit_rt_labels}, datasets:[{{ data:{sit_rt_values}, backgroundColor:{rt_colors}, borderWidth:0 }}] }},
  options: {{ ...defOpts, cutout:'40%',
    onClick: (evt,els,chart) => onClickChart(evt,els,chart,'route','sit_route'),
    plugins: {{ ...defOpts.plugins, tooltip:{{ callbacks:{{ label: ctx => ` ${{ctx.label}}: ${{ctx.raw}} pacotes — clique para filtrar` }} }} }}
  }}
}});

// Doughnut ON WAY
new Chart(document.getElementById('cSitWy'), {{
  type: 'doughnut',
  data: {{ labels:{sit_wy_labels}, datasets:[{{ data:{sit_wy_values}, backgroundColor:{wy_colors}, borderWidth:0 }}] }},
  options: {{ ...defOpts, cutout:'40%',
    onClick: (evt,els,chart) => onClickChart(evt,els,chart,'way','sit_way'),
    plugins: {{ ...defOpts.plugins, tooltip:{{ callbacks:{{ label: ctx => ` ${{ctx.label}}: ${{ctx.raw}} pacotes — clique para filtrar` }} }} }}
  }}
}});

// Status dos casos
const cStatus = new Chart(document.getElementById('cStatus'), {{
  type: 'bar',
  data: {{ labels:{st_labels}, datasets:[{{ data:{st_values},
    backgroundColor:['#3B82F6','#F59E0B','#9CA3AF'], borderRadius:6 }}] }},
  options: {{ ...defOpts, plugins:{{ legend:{{ display:false }} }},
    scales:{{ x:{{ ticks:{{ color:'#8a8a8a' }}, grid:{{ color:'#1e293b' }} }},
              y:{{ ticks:{{ color:'#8a8a8a' }}, grid:{{ color:'#334155' }} }} }} }}
}});
checkEmpty('cStatus', cStatus);

// GMV em risco por data de entrada
new Chart(document.getElementById('cGmvEvo'), {{
  type: 'bar',
  data: {{
    labels: {j(d["evo_labels"])},
    datasets: [
      {{ label:'GMV ON ROUTE', data:{j(d["evo_gmv_rt"])}, backgroundColor:'#3B82F6', borderRadius:4 }},
      {{ label:'GMV ON WAY',   data:{j(d["evo_gmv_wy"])}, backgroundColor:'#10B981', borderRadius:4 }},
    ]
  }},
  options: {{ ...defOpts,
    scales:{{ x:{{ stacked:true, ticks:{{ color:'#8a8a8a', maxRotation:45 }}, grid:{{ color:'#1e293b' }} }},
              y:{{ stacked:true, ticks:{{ color:'#8a8a8a', callback: v=>'$'+v.toLocaleString('pt-BR') }}, grid:{{ color:'#334155' }} }} }},
    plugins:{{ ...defOpts.plugins, tooltip:{{ callbacks:{{ label: ctx=>' $'+ctx.raw.toLocaleString('pt-BR',{{minimumFractionDigits:2}}) }} }} }}
  }}
}});

// Qtd pacotes por data de entrada
new Chart(document.getElementById('cEvo'), {{
  type: 'bar',
  data: {{
    labels: {j(d["evo_labels"])},
    datasets: [
      {{ label:'ON ROUTE', data:{j(d["evo_rt"])}, backgroundColor:'rgba(59,130,246,0.7)', borderRadius:4 }},
      {{ label:'ON WAY',   data:{j(d["evo_wy"])}, backgroundColor:'rgba(16,185,129,0.7)', borderRadius:4 }},
    ]
  }},
  options: {{ ...defOpts,
    scales:{{ x:{{ stacked:true, ticks:{{ color:'#8a8a8a', maxRotation:45 }}, grid:{{ color:'#1e293b' }} }},
              y:{{ stacked:true, ticks:{{ color:'#8a8a8a' }}, grid:{{ color:'#334155' }} }} }}
  }}
}});

// Heatmap dias da semana
const hmColors = {j(d["heatmap"])}.map(v => {{
  const max = Math.max(...{j(d["heatmap"])});
  const ratio = max > 0 ? v / max : 0;
  const r = Math.round(59  + (239-59)  * ratio);
  const g = Math.round(130 + (68-130)  * ratio);
  const b = Math.round(246 + (68-246)  * ratio);
  return `rgba(${{r}},${{g}},${{b}},0.85)`;
}});
new Chart(document.getElementById('cHeatmap'), {{
  type: 'bar',
  data: {{ labels: {j(d["heatmap_labels"])}, datasets:[{{ data:{j(d["heatmap"])},
    backgroundColor: hmColors, borderRadius:8 }}] }},
  options: {{
    responsive:true,
    plugins:{{ legend:{{ display:false }},
      tooltip:{{ callbacks:{{ label: ctx=>`${{ctx.raw}} pacotes entraram na ${{ctx.label}}` }} }} }},
    scales:{{ x:{{ ticks:{{ color:'#94a3b8', font:{{size:13, weight:'bold'}} }}, grid:{{ display:false }} }},
              y:{{ ticks:{{ color:'#8a8a8a' }}, grid:{{ color:'#334155' }} }} }}
  }}
}});

// Evolução GMV Snapshots
(function() {{
  const snapLabels    = {j(d["snapshots"]["labels"])};
  const snapGmvTotal  = {j(d["snapshots"]["gmv_total"])};
  const snapGmvOtr    = {j(d["snapshots"]["gmv_otr"])};
  const snapGmvOw     = {j(d["snapshots"]["gmv_ow"])};
  if (snapLabels.length < 2) {{
    document.getElementById('snap-no-data').style.display = 'block';
    document.getElementById('cGmvSnap').style.display     = 'none';
  }} else {{
    window._snapChart = new Chart(document.getElementById('cGmvSnap'), {{
      type: 'line',
      data: {{
        labels: snapLabels,
        datasets: [
          {{ label:'GMV Total',  data: snapGmvTotal,  borderColor:'#FBBF24', backgroundColor:'rgba(251,191,36,0.10)',  borderWidth:2.5, pointRadius:4, pointBackgroundColor:'#FBBF24',  fill:false, tension:0.3 }},
          {{ label:'GMV OTR',    data: snapGmvOtr,    borderColor:'#3B82F6', backgroundColor:'rgba(59,130,246,0.08)',  borderWidth:2,   pointRadius:3, pointBackgroundColor:'#3B82F6',  fill:false, tension:0.3 }},
          {{ label:'GMV OW',     data: snapGmvOw,     borderColor:'#10B981', backgroundColor:'rgba(16,185,129,0.08)', borderWidth:2,   pointRadius:3, pointBackgroundColor:'#10B981', fill:false, tension:0.3 }},
        ]
      }},
      options: {{ ...defOpts,
        scales: {{
          x: {{ ticks:{{ color:'#8a8a8a', maxRotation:45 }}, grid:{{ color:'#1e293b' }} }},
          y: {{ ticks:{{ color:'#8a8a8a', callback: v=>'$'+v.toLocaleString('pt-BR') }}, grid:{{ color:'#334155' }} }}
        }},
        plugins: {{ ...defOpts.plugins,
          tooltip: {{ callbacks: {{ label: ctx => ctx.dataset.label+': $'+ctx.raw.toLocaleString('pt-BR',{{minimumFractionDigits:2}}) }} }},
          zoom: {{ zoom:{{ wheel:{{ enabled:true }}, pinch:{{ enabled:true }}, mode:'x' }}, pan:{{ enabled:true, mode:'x' }} }}
        }}
      }}
    }});
  }}
}})();

// ---- Places: filtro ----
function filtrarPlaces() {{
  const busca = (document.getElementById('busca_places')?.value || '').toLowerCase();
  const tramo = (document.getElementById('tramo_places')?.value || '').toLowerCase();
  const acao  = (document.getElementById('acao_places')?.value  || '').toLowerCase();
  const risk  = (document.getElementById('risk_places')?.value  || '').toLowerCase();
  document.querySelectorAll('#tbl_places .pl-row').forEach(tr => {{
    const id  = tr.dataset.id    || '';
    const tr_ = tr.dataset.tramo || '';
    const ac  = tr.dataset.acao  || '';
    const rk  = tr.dataset.risk  || '';
    const ok  = (!busca || id.includes(busca))
             && (!tramo || tr_.includes(tramo))
             && (!acao  || ac.includes(acao))
             && (!risk  || rk.includes(risk));
    tr.style.display = ok ? '' : 'none';
  }});
}}

// ---- Places: gráficos ----
let _plDone = false;
function initPlCharts() {{
  if (_plDone) return; _plDone = true;
  setTimeout(function() {{
    const _acaoLabels = {j(list(d["places"]["acao_cnt"].keys()))};
    const _acaoVals   = {j(list(d["places"]["acao_cnt"].values()))};
    new Chart(document.getElementById('cPlAcao'), {{
      type: 'bar',
      data: {{ labels: _acaoLabels, datasets: [{{ data: _acaoVals,
        backgroundColor: 'rgba(239,68,68,0.7)', borderRadius: 4 }}] }},
      options: {{ responsive:true, maintainAspectRatio:false,
        plugins:{{ legend:{{ display:false }} }},
        scales:{{ x:{{ ticks:{{ color:'#8a8a8a', maxRotation:35, font:{{size:10}} }}, grid:{{ color:'#1e293b' }} }},
                  y:{{ ticks:{{ color:'#8a8a8a' }}, grid:{{ color:'#334155' }} }} }} }}
    }});
    new Chart(document.getElementById('cPlTramo'), {{
      type: 'doughnut',
      data: {{ labels: ['NEX','DC'],
               datasets: [{{ data: [{d["places"]["nex"]},{d["places"]["dc"]}],
                 backgroundColor: ['rgba(96,165,250,.75)','rgba(167,139,250,.75)'],
                 borderWidth: 0 }}] }},
      options: {{ responsive:true, maintainAspectRatio:false, cutout:'40%',
        plugins:{{ legend:{{ labels:{{ color:'#94a3b8',font:{{size:12}} }} }} }} }}
    }});
  }}, 0);
}}

// Gráficos Places já inicializados dentro de showTab

// ---- On Way: salvar campos editáveis ----
const _owTimers = {{}};

async function owPost(shp_id, tab, col, value) {{
  try {{
    await api('update', {{shp_id, tab, col, value}}, 'POST');
  }} catch(e) {{
    throw new Error('OFFLINE');
  }}
}}

function owVerificarArquivar(shp_id, tab) {{
  const statusSel = document.querySelector(`select.ow-edit[data-shp="${{shp_id}}"][data-col="29"]`);
  const finalSel  = document.querySelector(`select.ow-edit[data-shp="${{shp_id}}"][data-col="30"]`);
  if (!statusSel || !finalSel) return;
  if (!statusSel.value.toLowerCase().includes('conclu') || !finalSel.value.trim()) return;
  const hoje = new Date().toLocaleDateString('pt-BR');
  api('mover_historico', {{shp_id, tab: tab || 'wy', hoje}}, 'POST').then(data => {{
    if (data.ok) {{
      const tr = statusSel.closest('tr');
      if (tr) {{
        tr.style.transition = 'opacity 0.6s';
        tr.style.opacity = '0';
        setTimeout(() => tr.remove(), 650);
      }}
    }}
  }}).catch(() => {{}});
}}

function owUpdateFake(sel) {{
  const fake = sel.closest('.ow-fake-sel');
  if (!fake) return;
  const valEl = fake.querySelector('.ow-fake-val');
  if (!valEl) return;
  const opt = sel.options[sel.selectedIndex];
  const placeholders = {{'29':'— Status —','30':'— Final —','24':'— Ação —'}};
  valEl.textContent = (opt && opt.value) ? opt.text : (placeholders[sel.dataset.col] || '—');
}}

function updateRowBg(tr) {{
  if (!tr) return;
  const dias = parseInt(tr.dataset.dias || '0', 10);
  const statusSel = tr.querySelector('select[data-col="29"]');
  const status = statusSel ? statusSel.value.trim() : '';
  const acaoSel = tr.querySelector('select[data-col="24"]');
  const acaoInp = tr.querySelector('input[data-col="23"]');
  const acao = (acaoSel ? acaoSel.value : (acaoInp ? acaoInp.value : '')).trim();
  if (!status) {{
    tr.style.borderLeft = '3px solid #BA7517';
    tr.style.background = 'rgba(186,117,23,0.06)';
  }} else if (dias >= 8 && !acao) {{
    tr.style.borderLeft = '3px solid #E24B4A';
    tr.style.background = 'rgba(226,75,74,0.07)';
  }} else {{
    tr.style.borderLeft = '3px solid transparent';
    tr.style.background = '';
  }}
}}

function owSalvarSelect(el) {{
  const shp_id = el.dataset.shp, tab = el.dataset.tab || 'wy', col = +el.dataset.col;
  const prev = el.dataset.prev ?? el.value;
  el.dataset.prev = el.value;
  owUpdateFake(el);
  const tr = el.closest('tr');
  owPost(shp_id, tab, col, el.value)
    .then(() => {{ updateRowBg(tr); owVerificarArquivar(shp_id, tab); }})
    .catch(err => {{
      if (err.message === 'NOT_FOUND') {{
        alert('SHP ' + shp_id + ' não esta na planilha ativa. Recarregue o dashboard (F5) para sincronizar.');
      }} else {{
        alert('Erro ao salvar. Verifique sua conexão e tente novamente.');
        el.value = prev;
        owUpdateFake(el);
      }}
    }});
}}

function owAgendar(el) {{
  const key = el.dataset.shp + '_' + el.dataset.col;
  clearTimeout(_owTimers[key]);
  _owTimers[key] = setTimeout(() => owSalvarImediato(el), 1500);
}}

function owSalvarImediato(el) {{
  const key = el.dataset.shp + '_' + el.dataset.col;
  clearTimeout(_owTimers[key]);
  const shp_id = el.dataset.shp, tab = el.dataset.tab || 'wy', col = +el.dataset.col;
  if (!shp_id) return;
  const tr = el.closest('tr');
  el.classList.add('ow-saving');
  owPost(shp_id, tab, col, el.value)
    .then(() => {{ el.classList.remove('ow-saving'); el.classList.add('ow-saved');
                   setTimeout(() => el.classList.remove('ow-saved'), 1200);
                   updateRowBg(tr); }})
    .catch(err => {{
      el.classList.remove('ow-saving'); el.classList.add('ow-err');
      setTimeout(() => el.classList.remove('ow-err'), 2000);
      if (err.message === 'NOT_FOUND')
        alert('SHP ' + shp_id + ' não esta na planilha ativa. Recarregue o dashboard (F5) para sincronizar.');
    }});
}}

function owSugest(el) {{
  const q = el.value.trim().toLowerCase();
  const wrap = el.parentElement;
  const dd = wrap.querySelector('.ow-sugest');
  if (!dd) return;
  dd.querySelectorAll('.ow-sugest-item').forEach(item => {{
    item.style.display = (!q || item.textContent.toLowerCase().includes(q)) ? '' : 'none';
  }});
  dd.style.display = 'block';
}}

function owFecharSugest(el) {{
  setTimeout(() => {{
    const dd = el.parentElement?.querySelector('.ow-sugest');
    if (dd) dd.style.display = 'none';
  }}, 150);
}}

function owToggleSugest(inp) {{
  const dd = inp.parentElement?.querySelector('.ow-sugest');
  if (!dd) return;
  if (dd.style.display === 'block') {{ dd.style.display = 'none'; }}
  else {{ dd.querySelectorAll('.ow-sugest-item').forEach(i => i.style.display = ''); dd.style.display = 'block'; inp.focus(); }}
}}

function owEscolher(ev) {{
  ev.preventDefault();
  const item = ev.currentTarget;
  const wrap = item.closest('.ow-edit-wrap');
  const inp = wrap?.querySelector('.ow-text');
  if (!inp) return;
  inp.value = item.textContent;
  wrap.querySelector('.ow-sugest').style.display = 'none';
  owSalvarImediato(inp);
}}

function owAtualizarLink(el) {{
  const wrap = el.parentElement;
  let btn = wrap.querySelector('.ow-link-btn');
  const url = el.value.trim();
  if (url.startsWith('http')) {{
    if (!btn) {{ btn = document.createElement('a'); btn.className = 'ow-link-btn'; btn.textContent = '↗'; wrap.appendChild(btn); }}
    btn.href = url; btn.target = '_blank';
  }} else if (btn) {{ btn.remove(); }}
}}

// ---- Diário de Bordo ----
let _dbTodayStr = '';
let _dbData     = null;

function _dbSlug(s) {{
  return (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}}

function _dbTipoCls(tipo) {{
  const m = {{'Análise':'analise','Gemba':'gemba','Reunião':'reuniao','1:1':'1a1','Treinamento':'treinamento'}};
  return 'db-tipo db-t-' + (m[tipo] || 'extra');
}}

function _dbItemHtml(item, isExtra) {{
  const dc   = item.feito ? 'db-done' : '';
  const ck   = item.feito ? '✓' : '';
  const time = (item.hora_ini && item.hora_fim) ? `${{item.hora_ini}}–${{item.hora_fim}}` : (item.hora_ini || '');
  const aEsc = (item.atividade || '').replace(/'/g, "\\\\'");
  const oEsc = (item.obs || '').replace(/"/g,'&quot;');
  const delBtn = isExtra
    ? `<button onclick="dbDeletarExtra('${{aEsc}}')" style="background:none;border:none;color:#6B7280;font-size:11px;cursor:pointer;padding:0 2px;margin-left:auto" title="Remover">✕</button>`
    : '';
  const obsTxt = item.obs ? `<div class="db-obs-txt">${{item.obs}}</div>` : '';
  return `<div class="db-act-item ${{dc}}" id="db-i-${{_dbSlug(item.atividade)}}">
  <div class="db-check ${{dc}}" onclick="dbToggle('${{aEsc}}','${{item.hora_ini||''}}','${{item.hora_fim||''}}','${{item.tipo||''}}')">${{ck}}</div>
  <div class="db-act-body">
    <div class="db-act-row">
      <span class="db-act-name">${{item.atividade}}</span>
      ${{time ? `<span class="db-act-time">${{time}}</span>` : ''}}
      <span class="${{_dbTipoCls(item.tipo)}}">${{item.tipo}}</span>
      ${{delBtn}}
    </div>
    ${{obsTxt}}
    <input class="db-obs-inp" value="${{oEsc}}" placeholder="Observação…"
      onblur="dbSalvarObs('${{aEsc}}','${{item.hora_ini||''}}','${{item.hora_fim||''}}','${{item.tipo||''}}',this.value)"
      onkeydown="if(event.key==='Enter')this.blur()">
  </div></div>`;
}}

function _dbRender(data) {{
  const list = document.getElementById('db-list');
  const xtra = document.getElementById('db-extras');
  if (!list || !xtra) return;
  const items  = data.items  || [];
  const extras = data.extras || [];
  const done = items.filter(i => i.feito).length;
  const tot  = items.length;
  document.getElementById('db-progress-txt').textContent = `${{done}}/${{tot}} feitas`;
  document.getElementById('db-progress-bar').style.width = tot ? `${{Math.round(done/tot*100)}}%` : '0%';
  const manha = items.filter(i => parseInt((i.hora_ini||'00').split(':')[0]) < 12);
  const tarde = items.filter(i => parseInt((i.hora_ini||'00').split(':')[0]) >= 12);
  let html = '';
  if (manha.length) {{ html += '<div class="db-section-lbl">Manhã</div>'; manha.forEach(i => {{ html += _dbItemHtml(i, false); }}); }}
  if (tarde.length) {{ html += '<div class="db-section-lbl">Tarde</div>'; tarde.forEach(i => {{ html += _dbItemHtml(i, false); }}); }}
  list.innerHTML = html;
  xtra.innerHTML = extras.map(i => _dbItemHtml(i, true)).join('');
}}

function _dbSetStatus(estado) {{
  const el = document.getElementById('db-status');
  if (!el) return;
  if (estado === 'ativo')   {{ el.style.color = '#10B981'; el.textContent = '🟢 servidor ativo'; }}
  else if (estado === 'offline') {{ el.style.color = '#f87171'; el.textContent = '🔴 servidor offline'; }}
}}

async function carregarDiario() {{
  _dbTodayStr = new Date().toISOString().slice(0,10);
  const dias  = ['Dom','Seg','Ter','Qua','Qui','Sex','Sáb'];
  const meses = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];
  const now   = new Date();
  const lbl   = document.getElementById('db-date-lbl');
  if (lbl) lbl.textContent = `${{dias[now.getDay()]}}, ${{now.getDate()}}/${{(now.getMonth()+1).toString().padStart(2,'0')}}`;
  try {{
    _dbData = await api('diario');
    if (_dbData) _dbRender(_dbData);
  }} catch(e) {{}}
}}

let _dbAcabouAbrir = false;
function dbTogglePanel() {{
  const panel = document.getElementById('db-panel');
  if (!panel) return;
  if (panel.style.display !== 'none') {{
    panel.style.display = 'none';
    return;
  }}
  panel.style.display = 'block';
  _dbAcabouAbrir = true;
  setTimeout(() => {{ _dbAcabouAbrir = false; }}, 150);
  if (!_dbData) carregarDiario();
}}

function dbFecharPanel() {{
  const panel = document.getElementById('db-panel');
  if (panel) panel.style.display = 'none';
}}

document.addEventListener('click', function(ev) {{
  if (_dbAcabouAbrir) return;
  const panel = document.getElementById('db-panel');
  if (!panel || panel.style.display === 'none') return;
  const modal = document.getElementById('db-modal-bg');
  if (modal && modal.style.display !== 'none') return;
  if (!panel.contains(ev.target)) panel.style.display = 'none';
}});

async function dbToggle(atividade, hi, hf, tipo) {{
  const el = document.getElementById('db-i-' + _dbSlug(atividade));
  const ck = el?.querySelector('.db-check');
  if (!el || !ck) return;
  const agora = !ck.classList.contains('db-done');
  ck.classList.toggle('db-done', agora); ck.textContent = agora ? '✓' : '';
  el.classList.toggle('db-done', agora);
  if (_dbData) {{
    const item = _dbData.items.find(i => i.atividade === atividade);
    if (item) item.feito = agora;
    const done = _dbData.items.filter(i => i.feito).length;
    const tot  = _dbData.items.length;
    document.getElementById('db-progress-txt').textContent = `${{done}}/${{tot}} feitas`;
    document.getElementById('db-progress-bar').style.width = tot ? `${{Math.round(done/tot*100)}}%` : '0%';
  }}
  try {{
    await api('diario_toggle', {{data:_dbTodayStr,atividade,hora_ini:hi,hora_fim:hf,tipo,feito:agora}}, 'POST');
  }} catch(e) {{}}
}}

const _dbObsTmr = {{}};
function dbSalvarObs(atividade, hi, hf, tipo, obs) {{
  clearTimeout(_dbObsTmr[atividade]);
  _dbObsTmr[atividade] = setTimeout(async () => {{
    try {{
      await api('diario_obs', {{data:_dbTodayStr,atividade,hora_ini:hi,hora_fim:hf,tipo,obs}}, 'POST');
    }} catch(e) {{}}
  }}, 800);
}}

function dbAbrirModal() {{
  const bg = document.getElementById('db-modal-bg');
  if (bg) {{ bg.style.display = 'flex'; document.getElementById('db-m-atv').focus(); }}
}}
function dbFecharModal() {{
  const bg = document.getElementById('db-modal-bg');
  if (bg) bg.style.display = 'none';
}}

async function dbSalvarExtra() {{
  const atividade = document.getElementById('db-m-atv').value.trim();
  const m = document.getElementById('db-m-atv');
  if (!atividade) {{ m.style.borderColor='#ef4444'; m.focus(); return; }}
  m.style.borderColor = '';
  const hi  = document.getElementById('db-m-ini').value;
  const hf  = document.getElementById('db-m-fim').value;
  const obs = document.getElementById('db-m-obs').value;
  try {{
    const r = await api('diario_extra', {{data:_dbTodayStr,atividade,hora_ini:hi,hora_fim:hf,obs}}, 'POST');
    if (r && r.ok) {{
      dbFecharModal();
      ['db-m-atv','db-m-ini','db-m-fim','db-m-obs'].forEach(id => {{ document.getElementById(id).value = ''; }});
      if (_dbData) {{
        _dbData.extras = _dbData.extras || [];
        _dbData.extras.push({{hora_ini:hi,hora_fim:hf,atividade,tipo:'Extra',feito:false,obs,extra:true}});
        document.getElementById('db-extras').innerHTML = _dbData.extras.map(i => _dbItemHtml(i,true)).join('');
      }}
    }}
  }} catch(e) {{}}
}}

async function dbDeletarExtra(atividade) {{
  if (!confirm(`Remover "${{atividade}}"?`)) return;
  try {{
    await api('diario_delete_extra', {{data:_dbTodayStr,atividade}}, 'POST');
    if (_dbData && _dbData.extras) {{
      _dbData.extras = _dbData.extras.filter(i => i.atividade !== atividade);
      document.getElementById('db-extras').innerHTML = _dbData.extras.map(i => _dbItemHtml(i,true)).join('');
    }}
  }} catch(e) {{}}
}}

// Inicializa ícones Lucide
lucide.createIcons();
{_SB_DRAG_JS}

</script>
</main>
</div>
</body>
</html>'''

# ============================================================
# ABAS ESPELHO PARA GRID SDK (tabs sem parênteses no nome)
# ============================================================
def escrever_abas_grid(creds, rt, wy, at_station_rows):
    """Escreve grid_rota, grid_ow e grid_station no Controle para que o Grid SDK possa ler."""
    print("Escrevendo abas Grid (grid_rota / grid_ow / grid_station)...")
    try:
        gc = gspread.authorize(creds)
        pl = gc.open_by_key(PLANILHA_CONTROLE_ID)

        N = 36  # colunas A..AJ
        HEADER = [''] * N
        HEADER[0]  = 'Responsavel'; HEADER[1]  = 'Situacao';    HEADER[2]  = 'SHP_ID'
        HEADER[12] = 'Dias_OW';     HEADER[13] = 'Carrier';     HEADER[21] = 'GMV_OW'
        HEADER[22] = 'GMV_USD';     HEADER[23] = 'Acao_LP';     HEADER[24] = 'CFTV'
        HEADER[28] = 'Status';      HEADER[29] = 'Finalizacao'; HEADER[31] = 'Entrada'
        HEADER[32] = 'Cobrar_OTR'

        def _ensure(name):
            try:
                ws = pl.worksheet(name); ws.clear(); return ws
            except gspread.exceptions.WorksheetNotFound:
                return pl.add_worksheet(title=name, rows=5000, cols=N)

        def _write(ws, rows):
            pad = lambda r: (list(r) + [''] * N)[:N]
            data = [HEADER] + [pad(r) for r in rows]
            ws.update('A1', data, value_input_option='RAW')
            print(f"  {ws.title}: {len(rows)} linhas")

        _write(_ensure('grid_rota'), rt)
        _write(_ensure('grid_ow'),   wy)

        as_rows = []
        for r in at_station_rows:
            row = [''] * N
            row[2]  = str(r.get('SHP_SHIPMENT_ID') or '')
            row[1]  = str(r.get('RISK_CLASIFICATION') or '')
            row[22] = str(r.get('SHP_ORDER_COST_USD') or 0)
            row[23] = str(r.get('ACTION_DETAIL') or '')
            row[24] = 'SIM' if r.get('FLAG_BPP') else ''
            row[28] = str(r.get('SHP_TRAMO') or '')
            dias    = r.get('DAYS_HANDLING_SVC')
            row[31] = (str(dias) + 'd') if dias else ''
            as_rows.append(row)
        _write(_ensure('grid_station'), as_rows)

    except Exception as e:
        print(f"  [AVISO] Falha ao escrever abas Grid: {e}")

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("Lendo planilha...")
    rt, wy, hi, snaps, creds = carregar()
    print(f"  ON ROUTE: {len(rt)} | ON WAY: {len(wy)} | Histórico: {len(hi)} | Snapshots: {len(snaps)}")
    print("Lendo Places (BigQuery)...")
    try:
        places_rows = carregar_places(creds)
        print(f"  Places: {len(places_rows)} pacotes (NEX+DC)")
    except Exception as e:
        print(f"  [AVISO] Places BQ falhou: {e}")
        places_rows = []
    print("Lendo At Station (BigQuery)...")
    try:
        at_station_rows = carregar_at_station(creds)
        print(f"  At Station: {len(at_station_rows)} pacotes (todos os tramos SSP30)")
    except Exception as e:
        print(f"  [AVISO] At Station BQ falhou: {e}")
        at_station_rows = []
    print("Lendo DIT Blind Spot (BigQuery)...")
    try:
        dit_data = carregar_dit(creds)
        print(f"  DIT: {len(dit_data)} places com dados de delay")
    except Exception as e:
        print(f"  [AVISO] DIT BQ falhou: {e}")
        dit_data = {}
    print("Lendo status CFTV (Google Sheets)...")
    cftv_map = carregar_cftv_status(creds)
    print(f"  CFTV: {len(cftv_map)} investigacoes encontradas")
    print("Lendo descricoes dos pacotes (BigQuery)...")
    try:
        all_ids = [r[2] for r in rt + wy if len(r) > 2 and r[2].strip()]
        descricoes = carregar_descricoes(creds, all_ids)
        print(f"  Descrições: {len(descricoes)} pacotes")
    except Exception as e:
        print(f"  [AVISO] Descrições BQ falhou: {e}")
        descricoes = {}
    print("Verificando pacotes entregues no ON WAY (BigQuery)...")
    try:
        # Exclui pacotes já Concluídos — evita re-detectar os mesmos IDs a cada run
        wy_ids = [r[2] for r in wy if len(r) > 2 and r[2].strip()
                  and _ow_norm(r[28] if len(r) > 28 else '') != 'concluido']
        entregues = carregar_entregues(creds, wy_ids)
        print(f"  Entregues detectados: {len(entregues)}")
    except Exception as e:
        print(f"  [AVISO] Entregues BQ falhou: {e}")
        entregues = set()
    if entregues:
        print("Preenchendo campos dos entregues na planilha...")
        try:
            n = atualizar_entregues_planilha(creds, wy, entregues)
            print(f"  {n} pacotes preenchidos (Status/Ação/Link/Final)")
        except Exception as e:
            print(f"  [AVISO] Atualização planilha falhou: {e}")
    hoje_str = datetime.now().strftime('%d/%m/%Y')
    print("Movendo concluídos para Histórico (ON WAY)...")
    try:
        n_mov, novas_hi = mover_concluidos_historico(creds, wy, hoje_str)
        print(f"  {n_mov} pacotes ON WAY movidos para Histórico")
        hi.extend(novas_hi)
        ids_movidos = set()
        for r in wy:
            if (_ow_norm(r[28] if len(r) > 28 else '') == 'concluido'
                    and (r[29] if len(r) > 29 else '').strip()):
                ids_movidos.add(r[2] if len(r) > 2 else '')
        wy = [r for r in wy if (r[2] if len(r) > 2 else '') not in ids_movidos]
    except Exception as e:
        print(f"  [AVISO] Mover histórico ON WAY falhou: {e}")
    print("Detectando pacotes devolvidos no ON ROUTE...")
    n_devolvidos_rt = 0
    try:
        n_devolvidos_rt = atualizar_devolvidos_rt(creds, rt, hoje_str)
        print(f"  {n_devolvidos_rt} pacote(s) devolvido(s) marcados para Histórico (Recuperado)")
    except Exception as e:
        print(f"  [AVISO] Atualizar devolvidos RT falhou: {e}")
    print("Movendo concluídos para Histórico (ON ROUTE)...")
    try:
        n_mov_rt, novas_hi_rt = mover_concluidos_historico_rt(creds, rt, hoje_str)
        print(f"  {n_mov_rt} pacotes ON ROUTE movidos para Histórico")
        hi.extend(novas_hi_rt)
        ids_movidos_rt = set()
        for r in rt:
            if (_ow_norm(r[28] if len(r) > 28 else '') == 'concluido'
                    and (r[29] if len(r) > 29 else '').strip()):
                ids_movidos_rt.add(r[2] if len(r) > 2 else '')
        rt = [r for r in rt if (r[2] if len(r) > 2 else '') not in ids_movidos_rt]
    except Exception as e:
        print(f"  [AVISO] Mover histórico ON ROUTE falhou: {e}")
    print("Lendo Briefing Matinal (BigQuery)...")
    bq_briefing = []
    for _tentativa in range(3):
        try:
            bq_briefing = carregar_briefing(creds)
            print(f"  Briefing: {len(bq_briefing)} casos (últimos 90 dias)")
            break
        except Exception as e:
            if 'quotaExceeded' in str(e) or 'max_queued' in str(e):
                import time
                print(f"  [QUOTA] Fila BQ cheia, aguardando 30s (tentativa {_tentativa+1}/3)...")
                time.sleep(30)
            else:
                print(f"  [AVISO] Briefing BQ falhou: {e}")
                break
    else:
        print("  [AVISO] Briefing BQ falhou após 3 tentativas — continuando sem dados.")
    escrever_abas_grid(creds, rt, wy, at_station_rows)
    print("Processando dados...")
    dados = processar(rt, wy, hi, descricoes=descricoes, cftv_map=cftv_map, entregues=entregues)
    dados['places']      = processar_places(places_rows, dit_data)
    dados['at_station']  = processar_at_station(at_station_rows)
    dados['r_devolvidos'] = n_devolvidos_rt
    dados['briefing']    = processar_briefing(bq_briefing, wy)
    dados['snapshots']   = _processar_snapshots(snaps)
    print("Gerando HTML...")
    html = gerar_html(dados)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Dashboard salvo em: {OUTPUT}")
    if not os.environ.get('CI'):
        webbrowser.open(f'file:///{OUTPUT.replace(chr(92), "/")}')
        print("Abrindo no navegador!")
