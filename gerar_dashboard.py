# ============================================================
# gerar_dashboard.py — Gera o Dashboard HTML do Risco SSP30
# Como rodar: duplo clique em abrir_dashboard_html.bat
# ============================================================

import json, webbrowser, os
from datetime import datetime
from google.auth import default
import gspread

# ============================================================
# CONFIGURAÇÃO
# ============================================================
PLANILHA_CONTROLE_ID = '1rFcUXxl53WVQf_ASRx3mhlEvFoJevcaiwjMZY1vso5Y'
ABA_ON_ROUTE  = 'Tratativas Risco On Route (HV) - Lucas'
ABA_ON_WAY    = 'Tratativas Risco On Way (HV) - Lucas'
ABA_HISTORICO = 'Histórico'
OUTPUT        = os.path.join(os.path.dirname(__file__), 'index.html')
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
    return rt, wy, hi

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
    datas_todas = sorted(set(list(datas_rt.keys()) + list(datas_wy.keys())))

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
        # Evolução
        'evo_labels':  datas_todas,
        'evo_rt':      [datas_rt.get(d, 0)      for d in datas_todas],
        'evo_wy':      [datas_wy.get(d, 0)      for d in datas_todas],
        'evo_gmv_rt':  [round(gmv_rt_dt.get(d, 0), 2) for d in datas_todas],
        'evo_gmv_wy':  [round(gmv_wy_dt.get(d, 0), 2) for d in datas_todas],
    }

# ============================================================
# HELPERS HTML
# ============================================================
def pill(sit):
    cores = {
        'Possivel Lost':   ('#EF4444','#fff'),
        'Procurar Pacote': ('#F97316','#fff'),
        '>= 11 dias OW':   ('#FBBF24','#1a1a1a'),
        '< 11 dias OW':    ('#60A5FA','#fff'),
    }
    bg, fg = cores.get(sit, ('#9CA3AF','#fff'))
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
            <td style="font-family:monospace;font-size:12px">{r["id"]}</td>
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
            <td style="font-family:monospace;font-size:12px">{r["id"]}</td>
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
            <td style="font-family:monospace;font-size:12px">{r["id"]}</td>
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
        orig_bg = '#1D4ED8' if r['origem'] == 'ON ROUTE' else '#065F46'
        g       = f'${flt(r["gmv"]):,.2f}' if r['gmv'] else '—'
        out += f'''<tr>
            <td style="font-size:12px;color:#9CA3AF">{r["data"]}</td>
            <td><span style="background:{orig_bg};color:#fff;padding:2px 7px;border-radius:10px;font-size:11px">{r["origem"]}</span></td>
            <td style="font-family:monospace;font-size:12px">{r["id"]}</td>
            <td>{pill(r["sit"])}</td>
            <td style="color:#10B981;font-weight:600">{g}</td>
            <td>{r["resp"] or "—"}</td>
            <td>{pill_status(r["status"])}</td>
            <td style="font-size:11px;color:#9CA3AF">{r["final"] or "—"}</td>
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

    CORES_SIT = {'Possivel Lost':'#EF4444','Procurar Pacote':'#F97316',
                 '>= 11 dias OW':'#FBBF24','< 11 dias OW':'#60A5FA'}
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
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0f172a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
  /* HEADER */
  .header{{background:#FFE600;padding:14px 28px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}}
  .header h1{{color:#1a1a1a;font-size:20px;font-weight:800}}
  .header-right{{display:flex;flex-direction:column;align-items:flex-end;gap:2px}}
  .header .sub{{color:#1a1a1a;font-size:11px;opacity:0.7}}
  .countdown{{background:#1a1a1a;color:#FFE600;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:700}}
  /* TABS */
  .tabs{{background:#1e293b;border-bottom:2px solid #334155;padding:0 24px;display:flex;gap:4px;overflow-x:auto}}
  .tab{{padding:12px 20px;cursor:pointer;font-size:13px;font-weight:600;color:#94a3b8;border-bottom:3px solid transparent;transition:.2s;white-space:nowrap}}
  .tab:hover{{color:#e2e8f0}}
  .tab.active{{color:#FFE600;border-bottom-color:#FFE600}}
  /* CONTENT */
  .content{{display:none;padding:24px;max-width:1400px;margin:0 auto}}
  .content.active{{display:block}}
  /* CARDS */
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:14px;margin-bottom:24px}}
  .card{{background:#1e293b;border-radius:12px;padding:16px;border-left:4px solid #334155}}
  .card.yellow{{border-left-color:#FFE600}}
  .card.green{{border-left-color:#10B981}}
  .card.red{{border-left-color:#EF4444}}
  .card.blue{{border-left-color:#3B82F6}}
  .card.orange{{border-left-color:#F97316}}
  .card.purple{{border-left-color:#A78BFA}}
  .card .label{{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
  .card .value{{font-size:26px;font-weight:800;line-height:1}}
  .card .delta{{font-size:11px;color:#64748b;margin-top:4px}}
  /* GRIDS */
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}}
  @media(max-width:900px){{.grid2{{grid-template-columns:1fr}}}}
  /* CHART BOX */
  .box{{background:#1e293b;border-radius:12px;padding:20px}}
  .box h3{{font-size:13px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;margin-bottom:16px}}
  /* TABLE */
  .tbl-wrap{{background:#1e293b;border-radius:12px;overflow:hidden;margin-bottom:24px}}
  .tbl-title{{padding:14px 20px;font-size:13px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #334155}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{background:#0f172a;padding:10px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.5px}}
  td{{padding:10px 14px;border-bottom:1px solid #1a2540;background:transparent}}
  tr:hover td{{background:#1a2d45!important}}
  tr:last-child td{{border-bottom:none}}
  .tbl-scroll{{overflow-x:auto}}
  /* PILL */
  .pill{{padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;white-space:nowrap}}
  /* LEGEND */
  .legend{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px;font-size:12px}}
  .legend-item{{display:flex;align-items:center;gap:5px;color:#94a3b8}}
  .legend-dot{{width:10px;height:10px;border-radius:50%}}
  /* FILTER BAR */
  .filter-bar{{display:flex;gap:10px;padding:12px 20px;flex-wrap:wrap;border-bottom:1px solid #334155;align-items:center}}
  .filter-input{{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:8px 12px;color:#e2e8f0;font-size:13px;flex:1;min-width:200px}}
  .filter-input:focus{{outline:none;border-color:#FFE600}}
  .filter-select{{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:8px 12px;color:#e2e8f0;font-size:13px}}
  .filter-select:focus{{outline:none;border-color:#FFE600}}
  .btn-export{{background:#1D4ED8;color:#fff;border:none;border-radius:8px;padding:8px 14px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap}}
  .btn-export:hover{{background:#2563EB}}
  /* DIVIDER */
  .divider{{height:1px;background:#334155;margin:20px 0}}
</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <div>
    <h1>🔔 Risco SSP30 — Dashboard</h1>
    <div class="sub">Fonte: Planilha de Controle · Gerado em {d["gerado"]}</div>
  </div>
  <div class="header-right">
    <span class="countdown" id="countdown">Calculando...</span>
    <div class="sub" style="color:#1a1a1a;margin-top:2px">Atualiza todo dia às 08:00 BRT</div>
  </div>
</div>

<!-- TABS -->
<div class="tabs">
  <div class="tab active" onclick="showTab('geral',this)">📊 Visão Geral</div>
  <div class="tab" onclick="showTab('route',this)">📦 ON ROUTE ({d["r_total"]})</div>
  <div class="tab" onclick="showTab('way',this)">🚛 ON WAY ({d["w_total"]})</div>
  <div class="tab" onclick="showTab('gmv',this)">💰 Top GMV</div>
  <div class="tab" onclick="showTab('hist',this)">📁 Histórico {d["mes_lbl"]}</div>
</div>

<!-- ===================== ABA 1: VISÃO GERAL ===================== -->
<div id="tab-geral" class="content active">
  <div class="cards">
    <div class="card yellow"><div class="label">📦 ON ROUTE</div><div class="value">{d["r_total"]}</div><div class="delta">+{d["r_novos"]} hoje</div></div>
    <div class="card blue"><div class="label">🚛 ON WAY</div><div class="value">{d["w_total"]}</div><div class="delta">+{d["w_novos"]} hoje</div></div>
    <div class="card green"><div class="label">💰 GMV ON ROUTE</div><div class="value">${d["r_gmv"]:,.0f}</div></div>
    <div class="card green"><div class="label">💰 GMV ON WAY</div><div class="value">${d["w_gmv"]:,.0f}</div></div>
    <div class="card red"><div class="label">🔥 GMV TOTAL EM RISCO</div><div class="value">${d["gmv_total"]:,.0f}</div></div>
    <div class="card orange"><div class="label">📹 CFTV Solicitado</div><div class="value">{d["cftv_total"]}</div><div class="delta">Route {d["r_cftv"]} | Way {d["w_cftv"]}</div></div>
    <div class="card purple"><div class="label">⏱ Dias médio carteira</div><div class="value">{d["dias_medio"]}</div><div class="delta">pacotes ativos</div></div>
    <div class="card green"><div class="label">🏆 Recuperados {d["mes_lbl"]}</div><div class="value">{d["recuperados"]}</div><div class="delta">Seguiram fluxo correto</div></div>
    <div class="card purple"><div class="label">✅ Concluídos {d["mes_lbl"]}</div><div class="value">{d["concluidos"]}</div><div class="delta">{d["removidos"]} removidos no mês</div></div>
  </div>

  <div class="legend">
    <span class="legend-item"><span class="legend-dot" style="background:#10B981"></span> 0–3 dias (ok)</span>
    <span class="legend-item"><span class="legend-dot" style="background:#F97316"></span> 4–7 dias (atenção)</span>
    <span class="legend-item"><span class="legend-dot" style="background:#EF4444"></span> 8+ dias (crítico)</span>
  </div>

  <div class="grid2">
    <div class="box"><h3>ON ROUTE por Situation</h3><canvas id="cSitRt" height="220"></canvas></div>
    <div class="box"><h3>ON WAY por Situation</h3><canvas id="cSitWy" height="220"></canvas></div>
  </div>
  <div class="grid2">
    <div class="box"><h3>Status dos Casos (ambas as abas)</h3><canvas id="cStatus" height="220"></canvas></div>
    <div class="box"><h3>GMV em Risco por Data de Entrada</h3><canvas id="cGmvEvo" height="220"></canvas></div>
  </div>
  <div class="box" style="margin-bottom:24px"><h3>Pacotes por Data de Entrada</h3><canvas id="cEvo" height="180"></canvas></div>
</div>

<!-- ===================== ABA 2: ON ROUTE ===================== -->
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
        <th>SHP ID</th><th>Situation</th><th>GMV USD</th>
        <th>Responsável</th><th>CFTV</th><th>Status Caso</th>
        <th>Dias Cart.</th><th>Entrada</th>
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
        <th>SHP ID</th><th>Situation</th><th>GMV USD</th>
        <th>Dias OW</th><th>Transportadora</th><th>CFTV</th>
        <th>Status Caso</th><th>Dias Cart.</th><th>Entrada</th>
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
    <div class="card green"><div class="label">✅ Concluídos no mês</div><div class="value">{d["concluidos"]}</div></div>
    <div class="card green"><div class="label">🏆 Recuperados</div><div class="value">{d["recuperados"]}</div><div class="delta">Seguiram fluxo correto</div></div>
    <div class="card blue"><div class="label">📤 Total removidos</div><div class="value">{d["removidos"]}</div></div>
    <div class="card yellow"><div class="label">📅 Mês</div><div class="value" style="font-size:18px">{d["mes_lbl"]}</div></div>
  </div>
  <div class="tbl-wrap">
    <div class="tbl-title">📁 Pacotes arquivados em {d["mes_lbl"]}</div>
    {hist_table}
  </div>
</div>

<!-- ===================== SCRIPTS ===================== -->
<script>
// Troca de abas
function showTab(name, el) {{
  document.querySelectorAll('.content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(e => e.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  el.classList.add('active');
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

// Opções padrão dos gráficos
const defOpts = {{
  responsive: true,
  plugins: {{ legend: {{ labels: {{ color:'#94a3b8', font:{{ size:12 }} }} }} }},
}};

// Doughnut ON ROUTE
new Chart(document.getElementById('cSitRt'), {{
  type: 'doughnut',
  data: {{ labels:{sit_rt_labels}, datasets:[{{ data:{sit_rt_values}, backgroundColor:{rt_colors}, borderWidth:0 }}] }},
  options: {{ ...defOpts, cutout:'40%' }}
}});

// Doughnut ON WAY
new Chart(document.getElementById('cSitWy'), {{
  type: 'doughnut',
  data: {{ labels:{sit_wy_labels}, datasets:[{{ data:{sit_wy_values}, backgroundColor:{wy_colors}, borderWidth:0 }}] }},
  options: {{ ...defOpts, cutout:'40%' }}
}});

// Status dos casos
new Chart(document.getElementById('cStatus'), {{
  type: 'bar',
  data: {{ labels:{st_labels}, datasets:[{{ data:{st_values},
    backgroundColor:['#3B82F6','#F59E0B','#9CA3AF'], borderRadius:6 }}] }},
  options: {{ ...defOpts, plugins:{{ legend:{{ display:false }} }},
    scales:{{ x:{{ ticks:{{ color:'#64748b' }}, grid:{{ color:'#1e293b' }} }},
              y:{{ ticks:{{ color:'#64748b' }}, grid:{{ color:'#334155' }} }} }} }}
}});

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
    scales:{{ x:{{ stacked:true, ticks:{{ color:'#64748b', maxRotation:45 }}, grid:{{ color:'#1e293b' }} }},
              y:{{ stacked:true, ticks:{{ color:'#64748b', callback: v=>'$'+v.toLocaleString('pt-BR') }}, grid:{{ color:'#334155' }} }} }},
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
    scales:{{ x:{{ stacked:true, ticks:{{ color:'#64748b', maxRotation:45 }}, grid:{{ color:'#1e293b' }} }},
              y:{{ stacked:true, ticks:{{ color:'#64748b' }}, grid:{{ color:'#334155' }} }} }}
  }}
}});

// Top GMV horizontal
new Chart(document.getElementById('cTop'), {{
  type: 'bar',
  data: {{ labels:{top_labels}, datasets:[{{ data:{top_values}, backgroundColor:{top_colors}, borderRadius:4 }}] }},
  options: {{
    indexAxis:'y', responsive:true,
    plugins:{{ legend:{{ display:false }}, tooltip:{{ callbacks:{{ label: ctx=>' $'+ctx.raw.toLocaleString('pt-BR',{{minimumFractionDigits:2}}) }} }} }},
    scales:{{ x:{{ ticks:{{ color:'#64748b', callback: v=>'$'+v.toLocaleString('pt-BR') }}, grid:{{ color:'#334155' }} }},
              y:{{ ticks:{{ color:'#94a3b8', font:{{ size:11 }} }}, grid:{{ display:false }} }} }}
  }}
}});
</script>
</body>
</html>'''

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("Lendo planilha...")
    rt, wy, hi = carregar()
    print(f"  ON ROUTE: {len(rt)} | ON WAY: {len(wy)} | Histórico: {len(hi)}")
    print("Processando dados...")
    dados = processar(rt, wy, hi)
    print("Gerando HTML...")
    html = gerar_html(dados)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Dashboard salvo em: {OUTPUT}")
    if not os.environ.get('CI'):
        webbrowser.open(f'file:///{OUTPUT.replace(chr(92), "/")}')
        print("Abrindo no navegador!")
