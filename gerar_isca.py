#!/usr/bin/env python3
"""Gestão de Iscas SSP30 — gera isca.html a partir do Google Sheets."""

import json, os, webbrowser
from datetime import datetime
from collections import defaultdict
from google.auth import default
import gspread

# ── Config ────────────────────────────────────────────────────────────────────
ISCA_SHEET_ID = '1Y2xydLcUEtxvM1fx3obqdysg3NgVYWWQTzStnVpdXGU'
ABA_ISCAS     = 'Controle de Iscas'
OUTPUT_HTML   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'isca.html')
SHP_URL       = 'https://shipping-bo.adminml.com/sauron/shipments/shipment'

# Colunas (0-indexed) após inserção da col A "Responsável"
COL = dict(responsavel=0, descricao=1, cargo_track=2, etiqueta=3,
           week=4, data=5, status_rota=6, shp_id=7, mlp=8,
           rota=10, placa=11, motorista=12, motorista_id=13, resultado=14)

# ── Auth ──────────────────────────────────────────────────────────────────────
def autenticar():
    creds, _ = default(scopes=[
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ])
    return gspread.authorize(creds)

# ── Leitura ───────────────────────────────────────────────────────────────────
def _norm_resultado(raw):
    r = raw.strip().lower()
    if 'violada' in r or r.startswith('isca'):
        return 'Violada'
    if r == 'devolvida':
        return 'Devolvida'
    return 'Em aberto'

def carregar_iscas(gs):
    print("  Lendo planilha Gestão de Iscas...")
    pl   = gs.open_by_key(ISCA_SHEET_ID)
    ws   = pl.worksheet(ABA_ISCAS)
    data = ws.get_all_values()
    rows = []
    for r in data[1:]:
        g = lambda i: r[i].strip() if i < len(r) else ''
        if not g(COL['data']) and not g(COL['shp_id']):
            continue
        rows.append({
            'responsavel':  g(COL['responsavel']),
            'descricao':    g(COL['descricao']),
            'cargo_track':  g(COL['cargo_track']),
            'etiqueta':     g(COL['etiqueta']),
            'week':         g(COL['week']),
            'data':         g(COL['data']),
            'status_rota':  g(COL['status_rota']),
            'shp_id':       g(COL['shp_id']),
            'mlp':          g(COL['mlp']),
            'rota':         g(COL['rota']),
            'placa':        g(COL['placa']),
            'motorista':    g(COL['motorista']),
            'motorista_id': g(COL['motorista_id']),
            'resultado':    _norm_resultado(g(COL['resultado'])),
        })
    return rows

# ── Processamento ─────────────────────────────────────────────────────────────
def processar(rows):
    violadas   = [r for r in rows if r['resultado'] == 'Violada']
    devolvidas = [r for r in rows if r['resultado'] == 'Devolvida']
    em_aberto  = [r for r in rows if r['resultado'] == 'Em aberto']
    nv, nd     = len(violadas), len(devolvidas)
    taxa       = round(nv / (nv + nd) * 100, 1) if (nv + nd) > 0 else 0.0

    # Agrupamento por semana
    bw = defaultdict(lambda: {'violadas': 0, 'devolvidas': 0, 'total': 0})
    for r in rows:
        wk = r['week']
        if wk:
            bw[wk]['total'] += 1
            if r['resultado'] == 'Violada':    bw[wk]['violadas'] += 1
            elif r['resultado'] == 'Devolvida': bw[wk]['devolvidas'] += 1
    wks = sorted(bw, key=lambda w: int(w) if w.isdigit() else 0)
    weekly = [{'week': f'Sem {w}', 'violadas': bw[w]['violadas'],
               'devolvidas': bw[w]['devolvidas'], 'total': bw[w]['total']} for w in wks]

    # Ranking motoristas (violadas)
    mv = defaultdict(int)
    for r in violadas:
        mv[r['motorista'] or r['motorista_id'] or '—'] += 1
    rank_motoristas = sorted([{'nome': k, 'qtd': v} for k, v in mv.items()], key=lambda x: -x['qtd'])[:15]

    # Ranking rotas (violadas)
    rv = defaultdict(int)
    for r in violadas:
        rv[r['rota'] or '—'] += 1
    rank_rotas = sorted([{'rota': k, 'qtd': v} for k, v in rv.items()], key=lambda x: -x['qtd'])[:15]

    # Ranking MLP
    ml = defaultdict(lambda: {'violadas': 0, 'devolvidas': 0, 'total': 0})
    for r in rows:
        m = r['mlp'] or '—'
        ml[m]['total'] += 1
        if r['resultado'] == 'Violada':    ml[m]['violadas'] += 1
        elif r['resultado'] == 'Devolvida': ml[m]['devolvidas'] += 1
    rank_mlp = sorted([{'mlp': k, **v} for k, v in ml.items()], key=lambda x: -x['violadas'])

    return {
        'total': len(rows), 'violadas': nv, 'devolvidas': nd,
        'em_aberto': len(em_aberto), 'taxa': taxa,
        'rows': rows, 'rows_violadas': violadas,
        'rows_devolvidas': devolvidas, 'rows_em_aberto': em_aberto,
        'weekly': weekly, 'rank_motoristas': rank_motoristas,
        'rank_rotas': rank_rotas, 'rank_mlp': rank_mlp,
        'gerado': datetime.now().strftime('%d/%m/%Y %H:%M'),
    }

# ── Helpers HTML ──────────────────────────────────────────────────────────────
def j(x): return json.dumps(x, ensure_ascii=False)

RES_COR = {'Violada': '#ef4444', 'Devolvida': '#4ade80', 'Em aberto': '#f59e0b'}

def res_badge(res):
    cor = RES_COR.get(res, '#9ca3af')
    bg  = cor.replace('#', '')
    return f'<span style="color:{cor};font-size:10px;font-weight:700;background:rgba({int(bg[:2],16)},{int(bg[2:4],16)},{int(bg[4:],16)},.15);padding:2px 7px;border-radius:3px">{res}</span>'

def rows_table(rows, cols, max_rows=500):
    out = []
    for r in rows[:max_rows]:
        tds = ''
        for c in cols:
            v = r.get(c, '—') or '—'
            if c == 'shp_id' and v != '—':
                v = f'<a href="{SHP_URL}/{v}" target="_blank" style="color:#60a5fa;text-decoration:none">{v}</a>'
            elif c == 'resultado':
                v = res_badge(v)
            elif c == 'motorista' and r.get('motorista_id'):
                v = f'{v} <span style="color:#4b5563;font-size:10px">({r["motorista_id"]})</span>'
            tds += f'<td style="padding:7px 10px;font-size:11px;color:#d1d5db;white-space:nowrap">{v}</td>'
        out.append(f'<tr style="border-top:1px solid #111827">{tds}</tr>')
    return ''.join(out)

# ── HTML ──────────────────────────────────────────────────────────────────────
def gerar_html(d):
    weekly_js = j(d['weekly'])
    return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gestão de Iscas — SSP30</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#080d19;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:flex;flex-direction:column;height:100vh;overflow:hidden}}
/* HEADER */
.header{{background:#060a14;border-bottom:1px solid #111827;padding:10px 20px;display:flex;align-items:center;gap:14px;flex-shrink:0}}
.header-accent{{width:3px;height:32px;background:#4ade80;border-radius:2px;flex-shrink:0}}
.header-title{{font-size:14px;font-weight:700;color:#f9fafb;letter-spacing:.3px}}
.header-sub{{font-size:11px;color:#4b5563;margin-top:2px}}
.header-brand{{display:flex;align-items:center;gap:10px;flex:1}}
/* MODULE NAV */
.mod-nav{{display:flex;gap:4px;align-items:center}}
.mod-btn{{padding:6px 14px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid transparent;text-decoration:none;transition:all .2s;color:#6b7280;background:transparent;display:flex;align-items:center;gap:6px}}
.mod-btn:hover{{background:#1f2937;color:#e2e8f0}}
.mod-btn.m-fraude{{color:#ef4444;background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.3)}}
.mod-btn.m-risco{{color:#FFE600;background:rgba(255,230,0,.08);border-color:rgba(255,230,0,.2)}}
.mod-btn.m-isca{{color:#4ade80;background:rgba(74,222,128,.08);border-color:rgba(74,222,128,.2)}}
.mod-btn.m-disabled{{opacity:.35;cursor:not-allowed;pointer-events:none}}
/* LAYOUT */
.app-body{{display:flex;flex:1;overflow:hidden}}
.sidebar{{width:220px;flex-shrink:0;background:#060a14;border-right:1px solid #111827;overflow-y:auto;padding:6px 0;display:flex;flex-direction:column}}
.sb-divider{{height:1px;background:#111827;margin:6px 0;flex-shrink:0}}
.sb-section-header{{padding:10px 16px 4px;font-size:9px;text-transform:uppercase;letter-spacing:1px;color:#374151;font-weight:700;flex-shrink:0}}
.sb-item{{display:flex;align-items:center;gap:9px;padding:9px 16px;font-size:12px;color:#6b7280;cursor:pointer;transition:all .2s;border-left:2px solid transparent;white-space:nowrap;flex-shrink:0}}
.sb-item:hover{{background:#0d1321;color:#e2e8f0}}
.sb-item.active{{background:linear-gradient(90deg,rgba(74,222,128,.12),transparent);color:#ffffff;border-left-color:#4ade80;font-weight:600}}
.sb-badge{{margin-left:auto;background:rgba(74,222,128,.15);color:#4ade80;font-size:9px;font-weight:700;padding:2px 6px;border-radius:3px;flex-shrink:0}}
.sb-badge.red{{background:rgba(239,68,68,.2);color:#f87171}}
.sb-badge.amber{{background:rgba(245,158,11,.15);color:#f59e0b}}
.main-content{{flex:1;overflow-y:auto;padding:20px}}
.content{{display:none}}
.content.active{{display:block}}
/* CARDS */
.cards-grid{{display:grid;gap:14px;margin-bottom:18px}}
.card{{background:#0d1321;border:1px solid #1f2937;border-radius:8px;padding:14px 18px}}
.card-header{{display:flex;align-items:center;gap:7px;margin-bottom:8px}}
.cl{{font-size:10px;text-transform:uppercase;letter-spacing:.7px;color:#4b5563;font-weight:700}}
.cv{{font-size:26px;font-weight:700;color:#e2e8f0}}
.cv.red{{color:#f87171}}
.cv.green{{color:#4ade80}}
.cv.amber{{color:#f59e0b}}
.cd{{font-size:11px;color:#374151;margin-top:4px}}
.ci{{color:#6b7280}}
/* BOX */
.box{{background:#0d1321;border:1px solid #1f2937;border-radius:8px;padding:16px 20px;margin-bottom:18px}}
.bt{{font-size:11px;text-transform:uppercase;letter-spacing:.7px;color:#4b5563;font-weight:700;margin-bottom:14px}}
/* TABLE */
.tbl-wrap{{background:#0d1321;border:1px solid #1f2937;border-radius:8px;overflow:hidden;margin-bottom:18px}}
.tbl-title{{font-size:11px;text-transform:uppercase;letter-spacing:.7px;color:#4b5563;font-weight:700;padding:14px 18px 10px}}
.filter-bar{{display:flex;align-items:center;gap:8px;padding:0 14px 12px;flex-wrap:wrap}}
.filter-label{{font-size:11px;color:#6b7280}}
.filter-input,.filter-select{{background:#111827;border:1px solid #1f2937;color:#e2e8f0;border-radius:6px;padding:6px 10px;font-size:11px;outline:none}}
.tbl-scroll{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;padding:8px 10px;font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#4b5563;font-weight:700;border-bottom:1px solid #1f2937;background:#060a14;white-space:nowrap}}
</style>
</head>
<body>

<div class="header">
  <div class="header-brand">
    <div class="header-accent"></div>
    <div>
      <div class="header-title">Gestão de Iscas — SSP30</div>
      <div class="header-sub">Atualizado em {d["gerado"]}</div>
    </div>
  </div>
  <div class="mod-nav">
    <a href="./fraude.html" class="mod-btn">
      <i data-lucide="shield-alert" width="12" height="12"></i> Fraude
    </a>
    <a href="./index.html" class="mod-btn">
      <i data-lucide="truck" width="12" height="12"></i> Risco
    </a>
    <a href="./isca.html" class="mod-btn m-isca">
      <i data-lucide="fish" width="12" height="12"></i> Isca
    </a>
  </div>
</div>

<div class="app-body">
<nav class="sidebar">
  <div class="sb-item active" data-tab="geral" onclick="showTab('geral',this)">
    <i data-lucide="bar-chart-2" width="14" height="14" class="ci"></i> Visão Geral
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Registros</div>
  <div class="sb-item" data-tab="violadas" onclick="showTab('violadas',this)">
    <i data-lucide="alert-triangle" width="14" height="14" class="ci"></i>
    Violadas <span class="sb-badge red">{d["violadas"]}</span>
  </div>
  <div class="sb-item" data-tab="devolvidas" onclick="showTab('devolvidas',this)">
    <i data-lucide="check-circle" width="14" height="14" class="ci"></i>
    Devolvidas <span class="sb-badge">{d["devolvidas"]}</span>
  </div>
  <div class="sb-item" data-tab="em_aberto" onclick="showTab('em_aberto',this)">
    <i data-lucide="clock" width="14" height="14" class="ci"></i>
    Em Aberto <span class="sb-badge amber">{d["em_aberto"]}</span>
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Análise</div>
  <div class="sb-item" data-tab="motoristas" onclick="showTab('motoristas',this)">
    <i data-lucide="user" width="14" height="14" class="ci"></i>
    Por Motorista
  </div>
  <div class="sb-item" data-tab="rotas" onclick="showTab('rotas',this)">
    <i data-lucide="map-pin" width="14" height="14" class="ci"></i>
    Por Rota
  </div>
  <div class="sb-item" data-tab="mlp" onclick="showTab('mlp',this)">
    <i data-lucide="truck" width="14" height="14" class="ci"></i>
    Por MLP
  </div>
</nav>
<main class="main-content">

<!-- VISÃO GERAL -->
<div id="tab-geral" class="content active">
  <div class="cards-grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card">
      <div class="card-header"><i data-lucide="fish" class="ci" width="14" height="14"></i><span class="cl">Total Iscas</span></div>
      <div class="cv">{d["total"]}</div><div class="cd">inseridas em rotas</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="alert-triangle" class="ci" width="14" height="14"></i><span class="cl">Violadas</span></div>
      <div class="cv red">{d["violadas"]}</div><div class="cd">fraudes capturadas</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="check-circle" class="ci" width="14" height="14"></i><span class="cl">Devolvidas</span></div>
      <div class="cv green">{d["devolvidas"]}</div><div class="cd">drivers honestos</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="percent" class="ci" width="14" height="14"></i><span class="cl">Taxa Violação</span></div>
      <div class="cv amber">{d["taxa"]}%</div><div class="cd">violadas ÷ (viol+dev)</div>
    </div>
  </div>

  <div class="box">
    <div class="bt">Evolução por Semana — Violadas vs Devolvidas</div>
    <div style="position:relative;height:280px"><canvas id="cWeekly"></canvas></div>
  </div>
</div>

<!-- VIOLADAS -->
<div id="tab-violadas" class="content">
  <div class="tbl-wrap">
    <div class="tbl-title">Iscas Violadas — {d["violadas"]} registros</div>
    <div class="filter-bar">
      <input type="text" class="filter-input" id="busca_viol" placeholder="Motorista / Rota / SHP..." oninput="filtrar('tbl_viol','busca_viol')" style="width:220px">
      <select class="filter-select" id="filtro_viol_mlp" onchange="filtrar('tbl_viol','busca_viol')">
        <option value="">MLP</option>
        {''.join(f'<option value="{m}">{m}</option>' for m in sorted(set(r["mlp"] for r in d["rows_violadas"] if r["mlp"])))}
      </select>
    </div>
    <div class="tbl-scroll"><table id="tbl_viol">
      <thead><tr>
        <th>Data</th><th>Semana</th><th>Responsável</th><th>Produto</th>
        <th>MLP</th><th>Rota</th><th>Placa</th><th>Motorista</th><th>SHP ID</th>
      </tr></thead>
      <tbody>{rows_table(d["rows_violadas"],
        ['data','week','responsavel','descricao','mlp','rota','placa','motorista','shp_id'])}</tbody>
    </table></div>
  </div>
</div>

<!-- DEVOLVIDAS -->
<div id="tab-devolvidas" class="content">
  <div class="tbl-wrap">
    <div class="tbl-title">Iscas Devolvidas — {d["devolvidas"]} registros</div>
    <div class="filter-bar">
      <input type="text" class="filter-input" id="busca_dev" placeholder="Motorista / Rota / SHP..." oninput="filtrar('tbl_dev','busca_dev')" style="width:220px">
    </div>
    <div class="tbl-scroll"><table id="tbl_dev">
      <thead><tr>
        <th>Data</th><th>Semana</th><th>Responsável</th><th>Produto</th>
        <th>MLP</th><th>Rota</th><th>Placa</th><th>Motorista</th><th>SHP ID</th>
      </tr></thead>
      <tbody>{rows_table(d["rows_devolvidas"],
        ['data','week','responsavel','descricao','mlp','rota','placa','motorista','shp_id'])}</tbody>
    </table></div>
  </div>
</div>

<!-- EM ABERTO -->
<div id="tab-em_aberto" class="content">
  <div class="tbl-wrap">
    <div class="tbl-title">Em Aberto (sem resultado) — {d["em_aberto"]} registros</div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th>Data</th><th>Semana</th><th>Responsável</th><th>Produto</th>
        <th>MLP</th><th>Rota</th><th>Placa</th><th>Motorista</th><th>SHP ID</th>
      </tr></thead>
      <tbody>{rows_table(d["rows_em_aberto"],
        ['data','week','responsavel','descricao','mlp','rota','placa','motorista','shp_id'])}</tbody>
    </table></div>
  </div>
</div>

<!-- POR MOTORISTA -->
<div id="tab-motoristas" class="content">
  <div class="box">
    <div class="bt">Ranking — Motoristas com mais Iscas Violadas</div>
    <table style="width:100%;font-size:12px">
      <thead><tr><th>#</th><th>Motorista</th><th style="text-align:right">Violadas</th></tr></thead>
      <tbody>{''.join(
        f'<tr style="border-top:1px solid #111827"><td style="padding:7px 10px;color:#6b7280">#{i+1}</td>'
        f'<td style="padding:7px 10px;color:#e2e8f0;font-weight:500">{r["nome"]}</td>'
        f'<td style="padding:7px 10px;text-align:right;color:#f87171;font-weight:700">{r["qtd"]}</td></tr>'
        for i, r in enumerate(d["rank_motoristas"])
      )}</tbody>
    </table>
  </div>
</div>

<!-- POR ROTA -->
<div id="tab-rotas" class="content">
  <div class="box">
    <div class="bt">Ranking — Rotas com mais Iscas Violadas</div>
    <table style="width:100%;font-size:12px">
      <thead><tr><th>#</th><th>Rota</th><th style="text-align:right">Violadas</th></tr></thead>
      <tbody>{''.join(
        f'<tr style="border-top:1px solid #111827"><td style="padding:7px 10px;color:#6b7280">#{i+1}</td>'
        f'<td style="padding:7px 10px;color:#e2e8f0;font-weight:500">{r["rota"]}</td>'
        f'<td style="padding:7px 10px;text-align:right;color:#f87171;font-weight:700">{r["qtd"]}</td></tr>'
        for i, r in enumerate(d["rank_rotas"])
      )}</tbody>
    </table>
  </div>
</div>

<!-- POR MLP -->
<div id="tab-mlp" class="content">
  <div class="box">
    <div class="bt">Por Transportadora (MLP)</div>
    <table style="width:100%;font-size:12px">
      <thead><tr><th>MLP</th><th style="text-align:right">Violadas</th><th style="text-align:right">Devolvidas</th><th style="text-align:right">Total</th><th style="text-align:right">Taxa</th></tr></thead>
      <tbody>{''.join(
        f'<tr style="border-top:1px solid #111827">'
        f'<td style="padding:7px 10px;color:#e2e8f0;font-weight:500">{r["mlp"]}</td>'
        f'<td style="padding:7px 10px;text-align:right;color:#f87171;font-weight:700">{r["violadas"]}</td>'
        f'<td style="padding:7px 10px;text-align:right;color:#4ade80">{r["devolvidas"]}</td>'
        f'<td style="padding:7px 10px;text-align:right;color:#9ca3af">{r["total"]}</td>'
        f'<td style="padding:7px 10px;text-align:right;color:#f59e0b">{round(r["violadas"]/(r["violadas"]+r["devolvidas"])*100,1) if (r["violadas"]+r["devolvidas"])>0 else 0}%</td>'
        f'</tr>'
        for r in d["rank_mlp"]
      )}</tbody>
    </table>
  </div>
</div>

</main>
</div>

<script>
const WEEKLY = {weekly_js};

function showTab(name, el) {{
  document.querySelectorAll('.content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.sb-item').forEach(e => e.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  el.classList.add('active');
  if (name === 'geral') renderWeekly();
}}

window.addEventListener('load', () => {{
  const h = location.hash.replace('#','');
  const el = document.querySelector(`.sb-item[data-tab="${{h}}"]`);
  if (el) showTab(h, el);
  renderWeekly();
}});

function filtrar(tblId, inputId) {{
  const q   = (document.getElementById(inputId)?.value || '').toLowerCase();
  const mlp = document.getElementById('filtro_viol_mlp')?.value || '';
  document.querySelectorAll('#' + tblId + ' tbody tr').forEach(tr => {{
    const txt = tr.textContent.toLowerCase();
    const ok  = (!q || txt.includes(q)) && (!mlp || txt.includes(mlp.toLowerCase()));
    tr.style.display = ok ? '' : 'none';
  }});
}}

let _weekChart = null;
function renderWeekly() {{
  const canvas = document.getElementById('cWeekly');
  if (!canvas) return;
  const labels    = WEEKLY.map(w => w.week);
  const violadas  = WEEKLY.map(w => w.violadas);
  const devolvidas = WEEKLY.map(w => w.devolvidas);
  if (_weekChart) {{
    _weekChart.data.labels = labels;
    _weekChart.data.datasets[0].data = violadas;
    _weekChart.data.datasets[1].data = devolvidas;
    _weekChart.update();
    return;
  }}
  _weekChart = new Chart(canvas, {{
    type: 'bar',
    data: {{
      labels,
      datasets: [
        {{label:'Violadas', data:violadas, backgroundColor:'rgba(239,68,68,.75)', stack:'s'}},
        {{label:'Devolvidas', data:devolvidas, backgroundColor:'rgba(74,222,128,.6)', stack:'s'}}
      ]
    }},
    options: {{
      responsive:true, maintainAspectRatio:false,
      interaction:{{mode:'index',intersect:false}},
      plugins:{{legend:{{display:true,position:'top',labels:{{color:'#9ca3af',font:{{size:11}},boxWidth:12}}}}}},
      scales:{{
        x:{{grid:{{display:false}},ticks:{{color:'#6b7280',font:{{size:11}}}}}},
        y:{{stacked:true,grid:{{color:'rgba(255,255,255,0.04)'}},ticks:{{color:'#6b7280',font:{{size:11}}}}}}
      }}
    }}
  }});
}}

lucide.createIcons();
</script>
</body>
</html>'''

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("Gestão de Iscas SSP30")
    print("-" * 40)
    gs   = autenticar()
    rows = carregar_iscas(gs)
    print(f"  {len(rows)} registros carregados")
    d    = processar(rows)
    print(f"  Violadas: {d['violadas']} | Devolvidas: {d['devolvidas']} | Em aberto: {d['em_aberto']}")
    html = gerar_html(d)
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  Salvo em: {OUTPUT_HTML}")
    webbrowser.open(f'file:///{OUTPUT_HTML}')
