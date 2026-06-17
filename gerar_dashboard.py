# ============================================================
# gerar_dashboard.py — Gera o Dashboard HTML do Risco SSP30
# Como rodar: duplo clique em abrir_dashboard_html.bat
# ============================================================

import json, webbrowser, os
from datetime import datetime
from google.auth import default
from google.cloud import bigquery
import gspread

# ============================================================
# CONFIGURAÇÃO
# ============================================================
PLANILHA_CONTROLE_ID = '1rFcUXxl53WVQf_ASRx3mhlEvFoJevcaiwjMZY1vso5Y'
ABA_ON_ROUTE  = 'Tratativas Risco On Route (HV) - Lucas'
ABA_ON_WAY    = 'Tratativas Risco On Way (HV) - Lucas'
ABA_HISTORICO = 'Histórico'
OUTPUT        = os.path.join(os.path.dirname(__file__), 'index.html')

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
MESES_PT      = {1:'jan',2:'fev',3:'mar',4:'abr',5:'mai',6:'jun',
                 7:'jul',8:'ago',9:'set',10:'out',11:'nov',12:'dez'}

# ============================================================
# LEITURA DA PLANILHA
# ============================================================
def carregar():
    scopes = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive',
              'https://www.googleapis.com/auth/cloud-platform']
    creds, _ = default(scopes=scopes)
    gc = gspread.authorize(creds)
    pl = gc.open_by_key(PLANILHA_CONTROLE_ID)

    def ler(nome):
        rows = pl.worksheet(nome).get_all_values()
        if len(rows) <= 1:
            return [], []
        return rows[0], [r for r in rows[1:] if len(r) > 2 and r[2].strip()]

    h_rt, rt = ler(ABA_ON_ROUTE)
    h_wy, wy = ler(ABA_ON_WAY)
    try:
        h_hi, hi = ler(ABA_HISTORICO)
    except Exception:
        h_hi, hi = [], []
    return rt, wy, hi, creds

# ============================================================
# PROCESSAMENTO
# ============================================================
def flt(v):
    try:    return float(str(v).replace(',','.'))
    except: return 0.0

def calc_dias(entrada_str):
    try:
        dt = datetime.strptime(entrada_str.strip(), '%d/%m/%Y')
        return (datetime.now() - dt).days
    except:
        return -1

def processar(rt, wy, hi):
    agora   = datetime.now()
    hoje    = agora.strftime('%d/%m/%Y')
    mes_ano = agora.strftime('%m/%Y')
    mes_lbl = f"{MESES_PT[agora.month]}/{agora.year}"

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
        r_rows.append({
            'id':            r[2]  if len(r) > 2  else '',
            'sit':           r[1]  if len(r) > 1  else '',
            'gmv':           flt(r[22]) if len(r) > 22 else 0,
            'resp':          r[0]  if len(r) > 0  else '',
            'cftv':          r[24] if len(r) > 24 else '',
            'status':        r[28] if len(r) > 28 else '',
            'entrada':       entrada,
            'dias_carteira': calc_dias(entrada),
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
        w_rows.append({
            'id':            r[2]  if len(r) > 2  else '',
            'sit':           r[1]  if len(r) > 1  else '',
            'gmv':           flt(r[21]) if len(r) > 21 else 0,
            'dias_ow':       r[12] if len(r) > 12 else '',
            'carrier':       r[13] if len(r) > 13 else '',
            'resp':          r[0]  if len(r) > 0  else '',
            'cftv':          r[24] if len(r) > 24 else '',
            'status':        r[28] if len(r) > 28 else '',
            'entrada':       entrada,
            'dias_carteira': calc_dias(entrada),
        })
    w_rows.sort(key=lambda x: -x['gmv'])

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
    recuperados = sum(1 for r in hist_mes if len(r) > 7 and 'fluxo'  in r[7].lower())
    removidos   = len(hist_mes)
    hist_rows   = [{'data': r[0], 'origem': r[1], 'id': r[2],
                    'sit':  r[3], 'gmv':  r[4],   'resp': r[5],
                    'status': r[6] if len(r) > 6 else '',
                    'final':  r[7] if len(r) > 7 else ''}
                   for r in hist_mes]

    # Histórico completo (todas as datas) para a aba com filtro de mês
    def mes_de(data_str):
        try:
            p = data_str.strip().split('/')
            return f"{p[1]}/{p[2]}"   # mm/yyyy
        except: return ''

    hist_todos = []
    for r in hi:
        if not (len(r) > 0 and r[0].strip()): continue
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
    hist_todos.sort(
        key=lambda r: datetime.strptime(r['data'], '%d/%m/%Y') if r['data'] else datetime.min,
        reverse=True
    )
    # meses disponíveis em ordem cronológica
    def lbl_mes(m):
        try:
            dt = datetime.strptime('01/' + m, '%d/%m/%Y')
            return f"{MESES_PT[dt.month]}/{dt.year}"
        except: return m
    meses_hist = [{'val': m, 'lbl': lbl_mes(m)}
                  for m in sorted(set(r['mes'] for r in hist_todos if r['mes']),
                                  key=lambda m: datetime.strptime('01/'+m, '%d/%m/%Y'))]

    # Taxa de recupero, GMV recuperado e GMV perdido
    taxa_recupero  = round(recuperados / removidos * 100, 1) if removidos > 0 else 0
    gmv_recuperado = sum(flt(r['gmv']) for r in hist_rows if 'fluxo'   in r['final'].lower())
    gmv_perdido    = sum(flt(r['gmv']) for r in hist_rows if 'perdido' in r['final'].lower())

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
            heatmap[datetime.strptime(data.strip(), '%d/%m/%Y').weekday()] += 1
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
        'gmv_perdido': gmv_perdido,
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

def row_bg(d):
    """Cor de fundo da linha baseado nos dias na carteira."""
    if d >= 8:  return 'background:#3b1a1a'
    if d >= 4:  return 'background:#2d1f0e'
    return ''

MELI_PKG_URL = 'https://envios.adminml.com/logistics/package-management/package'

def id_link(shp_id):
    return f'<a href="{MELI_PKG_URL}/{shp_id}" target="_blank" class="shp-link">{shp_id}</a>'

def rows_table_rt(rows):
    out = ''
    for r in rows:
        g   = f'${r["gmv"]:,.2f}' if r['gmv'] else '—'
        bg  = row_bg(r['dias_carteira'])
        out += f'''<tr style="{bg}" class="data-row"
            data-id="{r["id"].lower()}"
            data-sit="{r["sit"].lower()}"
            data-status="{r["status"].lower()}"
            data-resp="{r["resp"].lower()}">
            <td style="font-family:monospace;font-size:12px">{id_link(r["id"])}</td>
            <td>{pill(r["sit"])}</td>
            <td style="font-weight:700;color:#10B981">{g}</td>
            <td>{r["resp"] or "—"}</td>
            <td style="text-align:center">{"✅" if r["cftv"]=="Sim" else "❌"}</td>
            <td>{pill_status(r["status"])}</td>
            <td style="text-align:center">{dias_badge(r["dias_carteira"])}</td>
            <td style="font-size:11px;color:#9CA3AF">{r["entrada"] or "—"}</td>
        </tr>'''
    return out

def rows_table_wy(rows):
    out = ''
    for r in rows:
        g   = f'${r["gmv"]:,.2f}' if r['gmv'] else '—'
        bg  = row_bg(r['dias_carteira'])
        out += f'''<tr style="{bg}" class="data-row"
            data-id="{r["id"].lower()}"
            data-sit="{r["sit"].lower()}"
            data-status="{r["status"].lower()}"
            data-resp="{r["resp"].lower()}">
            <td style="font-family:monospace;font-size:12px">{id_link(r["id"])}</td>
            <td>{pill(r["sit"])}</td>
            <td style="font-weight:700;color:#10B981">{g}</td>
            <td style="text-align:center;font-weight:700;color:#FBBF24">{r["dias_ow"] or "—"}</td>
            <td>{r["carrier"] or "—"}</td>
            <td style="text-align:center">{"✅" if r["cftv"]=="Sim" else "❌"}</td>
            <td>{pill_status(r["status"])}</td>
            <td style="text-align:center">{dias_badge(r["dias_carteira"])}</td>
            <td style="font-size:11px;color:#9CA3AF">{r["entrada"] or "—"}</td>
        </tr>'''
    return out

def rows_table_top(rows):
    out = ''
    for i, r in enumerate(rows, 1):
        g        = f'${r["gmv"]:,.2f}' if r['gmv'] else '—'
        orig_bg  = '#1D4ED8' if r['origem'] == 'ON ROUTE' else '#065F46'
        bg       = row_bg(r['dias_carteira'])
        out += f'''<tr style="{bg}">
            <td style="text-align:center;font-weight:700;color:#FFE600">{i}</td>
            <td><span style="background:{orig_bg};color:#fff;padding:2px 7px;border-radius:10px;font-size:11px">{r["origem"]}</span></td>
            <td style="font-family:monospace;font-size:12px">{id_link(r["id"])}</td>
            <td>{pill(r["sit"])}</td>
            <td style="font-weight:700;color:#10B981;font-size:14px">{g}</td>
            <td>{r["resp"] or "—"}</td>
            <td>{pill_status(r["status"])}</td>
            <td style="text-align:center">{dias_badge(r["dias_carteira"])}</td>
        </tr>'''
    return out

def rows_table_hist(rows):
    out = ''
    for r in rows:
        orig_bg = '#1D4ED8' if 'Route' in r['origem'] else '#065F46'
        g       = f'${flt(r["gmv"]):,.2f}' if r['gmv'] else '—'
        mes     = r.get('mes', '')
        out += f'''<tr class="hist-row" data-mes="{mes}">
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
# PLACES — BigQuery
# ============================================================
def carregar_places(creds):
    client = bigquery.Client(project='meli-bi-data', credentials=creds)
    job    = client.query(PLACES_QUERY)
    return [dict(r) for r in job.result()]

RISK_LABEL = {'CRITICO': 'Crítico', 'ALTO': 'Alto', 'MODERADO': 'Moderado'}
def norm_risk(val):
    v = str(val or '').strip().upper()
    return RISK_LABEL.get(v, val.capitalize() if val else '—')

def extract_acao(action_detail):
    if not action_detail:
        return '—'
    parts = str(action_detail).split('|')
    return parts[-1].strip()

def processar_places(rows):
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
        'rows': rows,
        'ranking': processar_places_ranking(rows),
    }

def processar_places_ranking(rows):
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
        result.append({'place_id': pid, 'tramo': d['tramo'], 'qtd': qtd,
                       'gmv': gmv, 'gmv_pkg': gmv_pkg,
                       'max_dias': max_d, 'avg_dias': avg_d, 'pkgs': pkgs_sorted})
    return sorted(result, key=lambda x: x['gmv'], reverse=True)

ROTA_URL_PLACES = 'https://envios.adminml.com/logistics/monitoring-distribution/detail/{id}?site=MLB'

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
        toggle_btn = f'<button onclick="togglePlaceRow(\'{safe_pid}\')" id="pbtn-{safe_pid}" style="background:none;border:1px solid #374151;color:#9ca3af;border-radius:4px;padding:1px 7px;cursor:pointer;font-size:11px">▶</button>'

        # sub-row com lista de IDs
        pkg_chips = ''
        for pkg in p['pkgs']:
            rk_low = pkg['risk'].lower()
            rc = '#f87171' if 'cr' in rk_low else ('#fbbf24' if 'alt' in rk_low else '#9ca3af')
            pkg_chips += f'''<div style="display:flex;align-items:center;gap:8px;padding:4px 8px;background:#0d1321;border-radius:4px;border:1px solid #1f2937">
                {id_link(pkg["id"])}
                <span style="color:#10B981;font-size:11px;font-weight:600">${pkg["gmv"]:,.2f}</span>
                <span style="color:{rc};font-size:10px">{pkg["risk"]}</span>
                <span style="color:#6b7280;font-size:10px">{dias_badge(pkg["dias"])}</span>
                <span style="color:#94a3b8;font-size:10px">{pkg["acao"]}</span>
            </div>'''

        detail_row = f'''<tr id="pdr-{safe_pid}" style="display:none">
            <td colspan="9" style="padding:8px 12px;background:#080e1a;border-top:1px solid #1f2937">
              <div style="font-size:11px;color:#64748b;margin-bottom:6px;font-weight:600">
                {p["qtd"]} pacote(s) em {p["place_id"]} — ordenados por GMV
              </div>
              <div style="display:flex;flex-wrap:wrap;gap:6px">{pkg_chips}</div>
            </td>
        </tr>'''

        out += f'''<tr style="{row_bg}" class="rank-row">
            <td style="text-align:center">{toggle_btn} {rank_badge}</td>
            <td style="font-family:monospace;font-size:12px;color:#a78bfa;font-weight:600">{p["place_id"]}</td>
            <td>{tramo_pill}</td>
            <td style="text-align:center;font-weight:700;color:#e2e8f0">{p["qtd"]}</td>
            <td style="font-weight:700;color:#10B981">${p["gmv"]:,.2f}</td>
            <td style="font-weight:700;color:#{"f87171" if p["gmv_pkg"]>=300 else "fbbf24" if p["gmv_pkg"]>=100 else "9ca3af"}">${p["gmv_pkg"]:,.2f}</td>
            <td style="text-align:center">{dias_badge(p["max_dias"])}</td>
            <td style="text-align:center">{dias_badge(int(p["avg_dias"]))}</td>
            <td style="font-size:11px">{alert}</td>
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
        <option value="">Sem acompanhamento</option>
      </select>
      <button onclick="exportCSV('{tab_id}', 'ssp30_{tab_id}.csv')" class="btn-export">⬇ Exportar CSV</button>
    </div>'''

def gerar_html(d):
    j = lambda x: json.dumps(x, ensure_ascii=False)

    sit_rt_labels = j(list(d['r_sit'].keys()))
    sit_rt_values = j(list(d['r_sit'].values()))
    sit_wy_labels = j(list(d['w_sit'].keys()))
    sit_wy_values = j(list(d['w_sit'].values()))

    top_labels = j([r['id'][:12]+'…' if len(r['id'])>12 else r['id'] for r in d['top15']])
    top_values = j([r['gmv'] for r in d['top15']])
    top_colors = j(['#EF4444' if 'Lost' in r['sit'] else '#F97316' if 'Procurar' in r['sit']
                    else '#FBBF24' if '>=' in r['sit'] else '#60A5FA' for r in d['top15']])

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

    hist_table = (
        "<div class='tbl-scroll'><table id='tbl_hist'><thead><tr>"
        "<th>Data</th><th>Origem</th><th>SHP ID</th><th>Situation</th>"
        "<th>GMV USD</th><th>Responsável</th><th>Status</th><th>Finalização</th>"
        "</tr></thead><tbody>" + rows_table_hist(d["hist_rows"]) + "</tbody></table></div>"
        if d["hist_rows"] else
        '<p style="padding:24px;color:#64748b;text-align:center">Nenhum registro arquivado este mês ainda.</p>'
    )

    return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Risco SSP30 — Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🔔</text></svg>">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#080d19;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;flex-direction:column;height:100vh;overflow:hidden}}
  /* HEADER */
  .header{{background:#080d19;padding:16px 32px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1f2937;flex-shrink:0}}
  .header-brand{{display:flex;align-items:center;gap:10px}}
  .header-accent{{width:3px;height:28px;background:#FFE600;border-radius:2px}}
  .header-title{{font-size:16px;font-weight:700;color:#ffffff;letter-spacing:-0.3px}}
  .header-sub{{font-size:11px;color:#374151;margin-top:2px}}
  .header-right{{display:flex;align-items:center;gap:10px}}
  .status-dot{{width:7px;height:7px;border-radius:50%;background:#FFE600;animation:pulse 2.5s infinite}}
  @keyframes pulse{{0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(255,230,0,.4)}}50%{{opacity:.6;box-shadow:0 0 0 5px rgba(255,230,0,0)}}}}
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
  .mod-btn.m-disabled{{opacity:.35;cursor:not-allowed;pointer-events:none}}
  /* CARD CLICÁVEL */
  .card-link{{cursor:pointer;position:relative}}
  .card-link::after{{content:'↗';position:absolute;top:14px;right:14px;font-size:10px;color:#1f2937;transition:color .3s ease}}
  .card-link:hover::after{{color:#6b7280}}
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
</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <div class="header-brand">
    <div class="header-accent"></div>
    <div>
      <div class="header-title">Risco SSP30</div>
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
    </div>
  </div>
</div>

<div class="app-body">
<nav class="sidebar">
  <div class="sb-item active" data-tab="geral" onclick="showTab('geral',this)">
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
  <div class="sb-divider"></div>
  <div class="sb-section-header">Places</div>
  <div class="sb-item" data-tab="places" onclick="showTab('places',this)">
    <i data-lucide="map-pin" width="14" height="14" class="ci"></i>
    NEX / DC <span class="sb-badge">{d["places"]["total"]}</span>
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Análise</div>
  <div class="sb-item" data-tab="gmv" onclick="showTab('gmv',this)">
    <i data-lucide="dollar-sign" width="14" height="14" class="ci"></i> Top GMV
  </div>
  <div class="sb-item" data-tab="hist" onclick="showTab('hist',this)">
    <i data-lucide="clock" width="14" height="14" class="ci"></i> Histórico
  </div>
</nav>
<main class="main-content">

<!-- ===================== ABA 1: VISÃO GERAL ===================== -->
<div id="tab-geral" class="content active">
  <div class="cards">
    <div class="card card-link" onclick="irPara('route')">
      <div class="card-header"><i data-lucide="package" class="card-icon" width="14" height="14"></i><span class="card-label">ON ROUTE</span></div>
      <div class="card-value">{d["r_total"]}</div>
      <div class="card-delta">+{d["r_novos"]} novos · {trend(d["net_rt"])}</div>
    </div>
    <div class="card card-link" onclick="irPara('way')">
      <div class="card-header"><i data-lucide="truck" class="card-icon" width="14" height="14"></i><span class="card-label">ON WAY</span></div>
      <div class="card-value">{d["w_total"]}</div>
      <div class="card-delta">+{d["w_novos"]} novos · {trend(d["net_wy"])}</div>
    </div>
    <div class="card card-link" onclick="irPara('route')">
      <div class="card-header"><i data-lucide="dollar-sign" class="card-icon" width="14" height="14"></i><span class="card-label">GMV ON ROUTE</span></div>
      <div class="card-value">${d["r_gmv"]:,.0f}</div>
    </div>
    <div class="card card-link" onclick="irPara('way')">
      <div class="card-header"><i data-lucide="dollar-sign" class="card-icon" width="14" height="14"></i><span class="card-label">GMV ON WAY</span></div>
      <div class="card-value">${d["w_gmv"]:,.0f}</div>
    </div>
    <div class="card card-alert card-link" onclick="irPara('gmv')">
      <div class="card-header"><i data-lucide="alert-triangle" class="card-icon" width="14" height="14" style="color:#7f1d1d"></i><span class="card-label">GMV EM RISCO</span></div>
      <div class="card-value val-alert">${d["gmv_total"]:,.0f}</div>
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
      <div class="card-value val-ok">{d["recuperados"]}</div>
      <div class="card-delta">{d["mes_lbl"]} · Seguiram fluxo correto</div>
    </div>
    <div class="card card-ok card-link" onclick="irPara('hist')">
      <div class="card-header"><i data-lucide="trending-up" class="card-icon" width="14" height="14" style="color:#064e3b"></i><span class="card-label">GMV do Mês</span></div>
      <div class="card-value val-ok">${d["gmv_recuperado"]:,.0f}</div>
      <div class="card-delta">
        <span style="color:#10b981">↑ ${d["gmv_recuperado"]:,.0f} recuperado</span><br>
        <span style="color:#ef4444">↓ ${d["gmv_perdido"]:,.0f} perdido</span>
      </div>
    </div>
    <div class="card card-link" onclick="irPara('hist')">
      <div class="card-header"><i data-lucide="percent" class="card-icon" width="14" height="14"></i><span class="card-label">Taxa de Recupero</span></div>
      <div class="card-value">{d["taxa_recupero"]}%</div>
      <div class="card-delta">{d["recuperados"]} de {d["removidos"]} removidos</div>
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
</div>

<!-- ===================== ABA 2: CRÍTICOS ===================== -->
<div id="tab-criticos" class="content">
  {'<div style="background:#7f1d1d;border:1px solid #EF4444;border-radius:12px;padding:20px;margin-bottom:24px;display:flex;align-items:center;gap:16px"><span style="font-size:32px">🚨</span><div><div style="font-size:16px;font-weight:800;color:#FCA5A5">'+str(len(d["criticos"]))+' pacotes precisam de atenção urgente</div><div style="color:#FCA5A5;opacity:0.8;font-size:13px;margin-top:4px">Critérios: Possivel Lost / +11d OW + GMV alto + muitos dias na carteira (2 ou mais fatores)</div></div></div>' if d['criticos'] else '<div style="text-align:center;padding:48px;color:#64748b"><div style="font-size:48px">✅</div><div style="font-size:18px;margin-top:12px">Nenhum pacote crítico no momento!</div></div>'}
  {''.join([f"""
  <div style="background:#1e293b;border-radius:12px;margin-bottom:16px;border-left:4px solid #EF4444;overflow:hidden">
    <div style="padding:16px 20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
        <a href="https://envios.adminml.com/logistics/package-management/package/{r["id"]}" target="_blank"
           style="font-family:monospace;font-size:15px;font-weight:800;color:#60A5FA;text-decoration:none">{r["id"]}</a>
        {pill(r["sit"])}
        <span style="background:#1D4ED8;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px">{r["origem"]}</span>
      </div>
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
        <span style="font-size:20px;font-weight:800;color:#10B981">${r["gmv"]:,.2f}</span>
        {dias_badge(r["dias_carteira"])} na carteira
        {pill_status(r["status"])}
      </div>
    </div>
    <div style="padding:8px 20px 14px;display:flex;gap:8px;flex-wrap:wrap">
      {''.join(f'<span style="background:#7f1d1d;color:#FCA5A5;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600">{m}</span>' for m in r["motivos"])}
    </div>
  </div>""" for r in d["criticos"]])}
</div>

<!-- ===================== ABA: ON ROUTE ===================== -->
<div id="tab-route" class="content">
  <div class="cards">
    <div class="card yellow"><div class="label">Total</div><div class="value">{d["r_total"]}</div></div>
    <div class="card green"><div class="label">GMV Total</div><div class="value">${d["r_gmv"]:,.0f}</div></div>
    <div class="card red"><div class="label">Possivel Lost</div><div class="value">{d["r_sit"].get("Possivel Lost",0)}</div></div>
    <div class="card orange"><div class="label">Procurar Pacote</div><div class="value">{d["r_sit"].get("Procurar Pacote",0)}</div></div>
    <div class="card blue"><div class="label">CFTV Solicitado</div><div class="value">{d["r_cftv"]}</div></div>
    <div class="card yellow"><div class="label">Novos Hoje</div><div class="value">{d["r_novos"]}</div></div>
  </div>
  <div class="tbl-wrap">
    <div class="tbl-title">📦 Pacotes ON ROUTE — ordenados por GMV</div>
    {filtros_html("route", sits_rt)}
    <div class="tbl-scroll">
    <table id="tbl_route">
      <thead><tr>
        <th class="sortable" onclick="sortTable('tbl_route',0)">SHP ID</th>
        <th class="sortable" onclick="sortTable('tbl_route',1)">Situation</th>
        <th class="sortable" onclick="sortTable('tbl_route',2)">GMV USD</th>
        <th class="sortable" onclick="sortTable('tbl_route',3)">Responsável</th>
        <th>CFTV</th>
        <th class="sortable" onclick="sortTable('tbl_route',5)">Status Caso</th>
        <th class="sortable" onclick="sortTable('tbl_route',6)">Dias Cart.</th>
        <th class="sortable" onclick="sortTable('tbl_route',7)">Entrada</th>
      </tr></thead>
      <tbody>{rows_table_rt(d["r_rows"])}</tbody>
    </table>
    </div>
  </div>
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
  <div class="tbl-wrap">
    <div class="tbl-title">🚛 Pacotes ON WAY — ordenados por GMV</div>
    {filtros_html("way", sits_wy)}
    <div class="tbl-scroll">
    <table id="tbl_way">
      <thead><tr>
        <th class="sortable" onclick="sortTable('tbl_way',0)">SHP ID</th>
        <th class="sortable" onclick="sortTable('tbl_way',1)">Situation</th>
        <th class="sortable" onclick="sortTable('tbl_way',2)">GMV USD</th>
        <th class="sortable" onclick="sortTable('tbl_way',3)">Dias OW</th>
        <th class="sortable" onclick="sortTable('tbl_way',4)">Transportadora</th>
        <th>CFTV</th>
        <th class="sortable" onclick="sortTable('tbl_way',6)">Status Caso</th>
        <th class="sortable" onclick="sortTable('tbl_way',7)">Dias Cart.</th>
        <th class="sortable" onclick="sortTable('tbl_way',8)">Entrada</th>
      </tr></thead>
      <tbody>{rows_table_wy(d["w_rows"])}</tbody>
    </table>
    </div>
  </div>
</div>

<!-- ===================== ABA 4: TOP GMV ===================== -->
<div id="tab-gmv" class="content">
  <div class="box" style="margin-bottom:24px"><h3>💰 Top 15 por GMV (ON ROUTE + ON WAY)</h3><canvas id="cTop" height="350"></canvas></div>
  <div class="tbl-wrap">
    <div class="tbl-title">Ranking completo</div>
    <div class="tbl-scroll">
    <table>
      <thead><tr>
        <th>#</th><th>Origem</th><th>SHP ID</th><th>Situation</th>
        <th>GMV USD</th><th>Responsável</th><th>Status</th><th>Dias Cart.</th>
      </tr></thead>
      <tbody>{rows_table_top(d["top15"])}</tbody>
    </table>
    </div>
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

  <div class="tbl-wrap">
    <div class="tbl-title">Pacotes arquivados</div>
    <div class="tbl-scroll">
    <table id="tbl_hist">
      <thead><tr>
        <th>Data</th><th>Origem</th><th>SHP ID</th><th>Situation</th>
        <th>GMV USD</th><th>Responsável</th><th>Status</th><th>Finalização</th>
      </tr></thead>
      <tbody>{rows_table_hist(d["hist_todos"])}</tbody>
    </table>
    </div>
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
  </div>

  <div class="grid2 mb16">
    <div class="box"><div class="box-title">Pacotes por Ação</div><div style="position:relative;height:220px"><canvas id="cPlAcao"></canvas></div></div>
    <div class="box"><div class="box-title">Distribuição NEX / DC</div><div style="position:relative;height:220px"><canvas id="cPlTramo"></canvas></div></div>
  </div>

  <div class="tbl-wrap" style="margin-bottom:18px">
    <div class="tbl-title">🏆 Places Ofensores — Ranking por GMV (SSP30)</div>
    <div class="tbl-scroll">
    <table id="tbl_places_rank" style="min-width:750px">
      <thead><tr>
        <th style="width:40px">#</th>
        <th>Place ID</th><th>Tramo</th>
        <th class="sortable" onclick="sortTable('tbl_places_rank',3)">Pkgs</th>
        <th class="sortable" onclick="sortTable('tbl_places_rank',4)">GMV Total</th>
        <th class="sortable" onclick="sortTable('tbl_places_rank',5)">GMV/pkg</th>
        <th class="sortable" onclick="sortTable('tbl_places_rank',6)">Max Dias</th>
        <th class="sortable" onclick="sortTable('tbl_places_rank',7)">Avg Dias</th>
        <th>Alerta</th>
      </tr></thead>
      <tbody>{rows_ranking_places(d["places"]["ranking"])}</tbody>
    </table>
    </div>
  </div>

  <div class="tbl-wrap">
    <div class="tbl-title">📍 Pacotes nos Places (NEX + DC) — SSP30 · ordenados por GMV</div>
    <div class="filter-bar">
      <input type="text" id="busca_places" placeholder="🔍 Buscar por SHP ID..." oninput="filtrarPlaces()" class="filter-input" style="max-width:260px">
      <select id="tramo_places" onchange="filtrarPlaces()" class="filter-select">
        <option value="">NEX + DC</option>
        <option value="nex">NEX</option>
        <option value="dc">DC</option>
      </select>
      <select id="acao_places" onchange="filtrarPlaces()" class="filter-select">
        <option value="">Todas as Ações</option>
        {''.join(f'<option value="{a.lower()}">{a}</option>' for a in d["places"]["acao_cnt"].keys())}
      </select>
      <select id="risk_places" onchange="filtrarPlaces()" class="filter-select">
        <option value="">Todos os Riscos</option>
        <option value="cr">Crítico</option>
        <option value="alt">Alto</option>
        <option value="mod">Moderado</option>
      </select>
      <button onclick="exportCSV('tbl_places', 'places_ssp30.csv')" class="btn-export">⬇ Exportar CSV</button>
    </div>
    <div class="tbl-scroll">
    <table id="tbl_places">
      <thead><tr>
        <th>SHP ID</th><th>Tramo</th><th>Place ID</th><th>Ação</th><th>Risco</th>
        <th class="sortable" onclick="sortTable('tbl_places',5)">Dias S/ Mov</th>
        <th class="sortable" onclick="sortTable('tbl_places',6)">GMV USD</th>
        <th>Retorno Ativo</th><th>Transportadora</th><th>Rota</th><th>Último Status</th>
      </tr></thead>
      <tbody>{rows_table_places(d["places"]["rows"])}</tbody>
    </table>
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
}}

// Abre aba pelo hash da URL (ex: #criticos)
window.addEventListener('load', () => {{
  const hash = window.location.hash.replace('#','');
  if (TAB_ORDER.includes(hash)) {{
    const el = document.querySelector(`.sb-item[data-tab="${{hash}}"]`);
    if (el) showTab(hash, el);
  }}
}});

// Navega para uma aba ao clicar num card
function irPara(tabName) {{
  const el = document.querySelector(`.sb-item[data-tab="${{tabName}}"]`);
  if (el) {{ showTab(tabName, el); window.scrollTo({{top:0, behavior:'smooth'}}); }}
}}

// Expand/collapse pacotes de um place no ranking
function togglePlaceRow(pid) {{
  const row = document.getElementById('pdr-' + pid);
  const btn = document.getElementById('pbtn-' + pid);
  if (!row) return;
  const open = row.style.display !== 'none';
  row.style.display = open ? 'none' : 'table-row';
  if (btn) btn.textContent = open ? '▶' : '▼';
}}

// Filtro por mês no Histórico
function filtrarMes(mes) {{
  document.querySelectorAll('.hist-row').forEach(tr => {{
    tr.style.display = (!mes || tr.dataset.mes === mes) ? '' : 'none';
  }});
  document.querySelectorAll('.mes-btn').forEach(btn => {{
    btn.classList.toggle('mes-ativo', btn.dataset.mes === mes);
  }});
}}
// Abre no mês atual por padrão
filtrarMes('{d["mes_ano"]}');

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
    const matchStatus = !status || st.includes(status);
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

// Top GMV horizontal
new Chart(document.getElementById('cTop'), {{
  type: 'bar',
  data: {{ labels:{top_labels}, datasets:[{{ data:{top_values}, backgroundColor:{top_colors}, borderRadius:4 }}] }},
  options: {{
    indexAxis:'y', responsive:true,
    plugins:{{ legend:{{ display:false }}, tooltip:{{ callbacks:{{ label: ctx=>' $'+ctx.raw.toLocaleString('pt-BR',{{minimumFractionDigits:2}}) }} }} }},
    scales:{{ x:{{ ticks:{{ color:'#8a8a8a', callback: v=>'$'+v.toLocaleString('pt-BR') }}, grid:{{ color:'#334155' }} }},
              y:{{ ticks:{{ color:'#94a3b8', font:{{ size:11 }} }}, grid:{{ display:false }} }} }}
  }}
}});
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

// Inicializa ícones Lucide
lucide.createIcons();
</script>
</main>
</div>
</body>
</html>'''

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("Lendo planilha...")
    rt, wy, hi, creds = carregar()
    print(f"  ON ROUTE: {len(rt)} | ON WAY: {len(wy)} | Histórico: {len(hi)}")
    print("Lendo Places (BigQuery)...")
    try:
        places_rows = carregar_places(creds)
        print(f"  Places: {len(places_rows)} pacotes (NEX+DC)")
    except Exception as e:
        print(f"  [AVISO] Places BQ falhou: {e}")
        places_rows = []
    print("Processando dados...")
    dados = processar(rt, wy, hi)
    dados['places'] = processar_places(places_rows)
    print("Gerando HTML...")
    html = gerar_html(dados)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Dashboard salvo em: {OUTPUT}")
    if not os.environ.get('CI'):
        webbrowser.open(f'file:///{OUTPUT.replace(chr(92), "/")}')
        print("Abrindo no navegador!")
