#!/usr/bin/env python3
"""CFTV SSP30 — gera cftv.html a partir do Google Sheets."""

import json, os, webbrowser
from datetime import datetime
from _diario_widget import diario_css, diario_nav_btn, diario_panel_html, diario_js
from google.auth import default
import gspread

# ── Config ────────────────────────────────────────────────────────────────────
CFTV_SHEET_ID = '18isURInofILBi-RS9YrCQyYcnb6JeU_stNqnspxiqLM'
CFTV_ABA      = 'Respostas ao formulário 2'
MELI_URL      = 'https://shipping-bo.adminml.com/sauron/shipments/shipment'
OUTPUT_HTML   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cftv.html')

# ── Auth ──────────────────────────────────────────────────────────────────────
def autenticar():
    creds, _ = default(scopes=[
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ])
    return gspread.authorize(creds)

# ── Leitura ───────────────────────────────────────────────────────────────────
def carregar_cftv(gs):
    print("  Lendo planilha CFTV...")
    pl   = gs.open_by_key(CFTV_SHEET_ID)
    data = pl.worksheet(CFTV_ABA).get_all_values()
    if len(data) <= 1:
        return []
    header = data[0]
    rows   = [dict(zip(header, r)) for r in data[1:] if any(r)]
    print(f"  {len(rows)} solicitações CFTV")
    return rows

# ── Processamento ─────────────────────────────────────────────────────────────
def processar_cftv(rows):
    def _valor(v):
        try:
            return float(str(v).replace('R$','').replace('\xa0','').replace('.','').replace(',','.').strip() or 0)
        except:
            return 0.0
    def _status(s):
        s = s.strip().lower()
        if 'conclu' in s: return 'Concluído'
        if 'expira' in s or 'expid' in s: return 'SLA Vencido'
        return 'Em Andamento'

    out = []
    for r in rows:
        ts       = r.get('Carimbo de data/hora', '')
        data     = ts.split(' ')[0] if ts else ''
        data_iso = ''
        if data and len(data) == 10:
            try: data_iso = f"{data[6:]}-{data[3:5]}-{data[:2]}"
            except: pass
        status = _status(r.get('Status', ''))
        out.append({
            'data':       data,
            'data_iso':   data_iso,
            'week':       str(r.get('Week', '')).strip(),
            'solicitante':r.get('Solicitante', '').strip(),
            'operacao':   r.get('Operação', '').strip(),
            'shp':        str(r.get('Shipment', '')).strip(),
            'produto':    str(r.get('Informe a descrição do ID', '')).strip()[:60],
            'valor':      _valor(r.get('Valor em R$', '')),
            'prioridade': r.get('Nivel de Prioridade', '').strip(),
            'status':     status,
            'sla':        str(r.get('SLA', '') or '').strip(),
            'responsavel':r.get('Responsável', '').strip(),
            'conclusao':  r.get('Conclusão', '').strip(),
            'driver':     str(r.get('Driver', '') or '').strip(),
            'placa':      str(r.get('Placa', '') or '').strip(),
            'mlp':        str(r.get('MLP', '') or '').strip(),
        })
    out.sort(key=lambda x: x['data_iso'], reverse=True)
    total      = len(out)
    concluidos = sum(1 for r in out if r['status'] == 'Concluído')
    sla_venc   = sum(1 for r in out if r['status'] == 'SLA Vencido')
    em_and     = total - concluidos - sla_venc
    return {
        'total': total, 'concluidos': concluidos,
        'em_andamento': em_and, 'sla_vencido': sla_venc,
        'rows': out,
        'gerado': datetime.now().strftime('%d/%m/%Y %H:%M'),
    }

# ── Helpers HTML ──────────────────────────────────────────────────────────────
def rows_cftv(rows):
    STATUS_COR = {'Concluído':'#10b981','Em Andamento':'#3b82f6','SLA Vencido':'#ef4444'}
    PRIO_COR   = {'Alto':'#ef4444','Moderado':'#f59e0b'}
    CONCL_COR  = {'Conclusivo':'#10b981','Inconclusivo':'#ef4444'}
    out = ''
    for r in rows[:1000]:
        st_cor   = STATUS_COR.get(r['status'], '#9ca3af')
        pr_cor   = PRIO_COR.get(r['prioridade'], '#9ca3af')
        co_cor   = CONCL_COR.get(r['conclusao'], '#6b7280')
        shp_link = (f'<a href="{MELI_URL}/{r["shp"]}" target="_blank" '
                    f'style="color:#60a5fa;text-decoration:none;font-family:monospace;font-size:12px">{r["shp"]}</a>'
                    if r['shp'] else '—')
        search_txt = f'{r["shp"]} {r["driver"]} {r["solicitante"]} {r["produto"]}'.lower()
        prod_esc   = r['produto'].replace('"', '&quot;')
        out += f'''<tr class="cftv-row" data-operacao="{r["operacao"]}" data-status="{r["status"]}" data-prio="{r["prioridade"]}" data-resp="{r["responsavel"]}" data-search="{search_txt}">
          <td style="font-size:11px;color:#9ca3af;white-space:nowrap">{r["data"]}</td>
          <td style="font-size:11px;color:#6b7280">W{r["week"]}</td>
          <td><span style="background:#1f2937;color:#e2e8f0;border-radius:4px;padding:2px 6px;font-size:10px;font-weight:600">{r["operacao"]}</span></td>
          <td>{shp_link}</td>
          <td style="font-size:11px;color:#d1d5db;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{prod_esc}">{r["produto"]}</td>
          <td style="color:#10b981;font-size:12px;text-align:right;white-space:nowrap">R${r["valor"]:,.2f}</td>
          <td><span style="color:{pr_cor};font-size:11px;font-weight:600">{r["prioridade"] or "—"}</span></td>
          <td><span style="color:{st_cor};font-size:11px;font-weight:600">{r["status"]}</span></td>
          <td style="font-size:11px;color:#9ca3af;text-align:center">{r["sla"] or "—"}</td>
          <td style="font-size:11px;color:#d1d5db">{r["responsavel"] or "—"}</td>
          <td><span style="color:{co_cor};font-size:11px">{r["conclusao"] or "—"}</span></td>
          <td style="font-size:11px;color:#9ca3af">{r["driver"] or "—"}</td>
        </tr>'''
    return out

# ── HTML ──────────────────────────────────────────────────────────────────────
def gerar_html(d):
    responsaveis = sorted(set(r["responsavel"] for r in d["rows"] if r["responsavel"]))
    opts_resp = ''.join(f'<option value="{r}">{r}</option>' for r in responsaveis)
    return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CFTV — SSP30</title>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#080d19;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:flex;flex-direction:column;min-height:100vh}}
.header{{background:#060a14;border-bottom:1px solid #111827;padding:10px 20px;display:flex;align-items:center;gap:14px;flex-shrink:0}}
.header-accent{{width:3px;height:32px;background:#60a5fa;border-radius:2px;flex-shrink:0}}
.header-title{{font-size:14px;font-weight:700;color:#f9fafb;letter-spacing:.3px}}
.header-sub{{font-size:11px;color:#4b5563;margin-top:2px}}
.header-brand{{display:flex;align-items:center;gap:10px;flex:1}}
.mod-nav{{display:flex;gap:4px;align-items:center}}
.mod-btn{{padding:6px 14px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid #1f2937;text-decoration:none;transition:all .2s;color:#9ca3af;background:#0d1321;display:flex;align-items:center;gap:6px}}
.mod-btn:hover{{background:#1f2937;color:#e2e8f0;border-color:#374151}}
.mod-btn.m-fraude{{color:#ef4444;background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.3)}}
.mod-btn.m-risco{{color:#FFE600;background:rgba(255,230,0,.08);border-color:rgba(255,230,0,.2)}}
.mod-btn.m-isca{{color:#4ade80;background:rgba(74,222,128,.08);border-color:rgba(74,222,128,.2)}}
.mod-btn.m-cftv{{color:#60a5fa;background:rgba(96,165,250,.1);border-color:rgba(96,165,250,.3)}}
{diario_css()}
.main{{padding:20px;flex:1}}
.cards-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}}
.card{{background:#0d1321;border:1px solid #1f2937;border-radius:8px;padding:14px 18px}}
.card-header{{display:flex;align-items:center;gap:7px;margin-bottom:8px}}
.cl{{font-size:10px;text-transform:uppercase;letter-spacing:.7px;color:#4b5563;font-weight:700}}
.cv{{font-size:26px;font-weight:700;color:#e2e8f0}}
.cv.green{{color:#10b981}} .cv.blue{{color:#60a5fa}} .cv.red{{color:#ef4444}} .cv.amber{{color:#f59e0b}}
.cd{{font-size:11px;color:#374151;margin-top:4px}}
.ci{{color:#6b7280}}
.tbl-wrap{{background:#0d1321;border:1px solid #1f2937;border-radius:8px;overflow:hidden}}
.tbl-title{{font-size:11px;text-transform:uppercase;letter-spacing:.7px;color:#4b5563;font-weight:700;padding:14px 18px 10px}}
.filter-bar{{display:flex;align-items:center;gap:8px;padding:0 14px 12px;flex-wrap:wrap}}
.filter-label{{font-size:11px;color:#6b7280}}
.filter-input,.filter-select{{background:#111827;border:1px solid #1f2937;color:#e2e8f0;border-radius:6px;padding:6px 10px;font-size:11px;outline:none}}
.tbl-scroll{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;padding:8px 10px;font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#4b5563;font-weight:700;border-bottom:1px solid #1f2937;background:#060a14;white-space:nowrap}}
td{{padding:7px 10px;border-top:1px solid #0d1321}}
tr:hover td{{background:#0d1829}}
</style>
</head>
<body>

<div class="header">
  <div class="header-brand">
    <div class="header-accent"></div>
    <div>
      <div class="header-title">CFTV — SSP30</div>
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
    <a href="./isca.html" class="mod-btn">
      <i data-lucide="fish" width="12" height="12"></i> Isca
    </a>
    <a href="./cftv.html" class="mod-btn m-cftv">
      <i data-lucide="camera" width="12" height="12"></i> CFTV
    </a>
    {diario_nav_btn()}
  </div>
</div>
{diario_panel_html()}
<div class="main">
  <div class="cards-grid">
    <div class="card">
      <div class="card-header"><i data-lucide="camera" class="ci" width="14" height="14"></i><span class="cl">Solicitações</span></div>
      <div class="cv">{d["total"]}</div><div class="cd">Total de solicitações</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="check-circle" class="ci" width="14" height="14"></i><span class="cl">Concluídos</span></div>
      <div class="cv green">{d["concluidos"]}</div><div class="cd">Investigações encerradas</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="clock" class="ci" width="14" height="14"></i><span class="cl">Em Andamento</span></div>
      <div class="cv blue">{d["em_andamento"]}</div><div class="cd">Aguardando conclusão</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="alert-triangle" class="ci" width="14" height="14"></i><span class="cl">SLA Vencido</span></div>
      <div class="cv red">{d["sla_vencido"]}</div><div class="cd">Prazo expirado</div>
    </div>
  </div>

  <div class="tbl-wrap">
    <div class="tbl-title">Solicitações CFTV — {d["total"]} registros</div>
    <div class="filter-bar">
      <span class="filter-label">Operação</span>
      <select id="cftv_op" class="filter-select" onchange="filtrar()">
        <option value="">Todas</option>
        <option value="SSP30">SSP30</option>
        <option value="XSP10">XSP10</option>
      </select>
      <span class="filter-label">Status</span>
      <select id="cftv_status" class="filter-select" onchange="filtrar()">
        <option value="">Todos</option>
        <option value="Concluído">Concluído</option>
        <option value="Em Andamento">Em Andamento</option>
        <option value="SLA Vencido">SLA Vencido</option>
      </select>
      <span class="filter-label">Prioridade</span>
      <select id="cftv_prio" class="filter-select" onchange="filtrar()">
        <option value="">Todas</option>
        <option value="Alto">Alto</option>
        <option value="Moderado">Moderado</option>
      </select>
      <span class="filter-label">Responsável</span>
      <select id="cftv_resp" class="filter-select" onchange="filtrar()">
        <option value="">Todos</option>
        {opts_resp}
      </select>
      <input id="cftv_search" type="text" oninput="filtrar()" class="filter-select" placeholder="🔍 SHP / Driver / Solicitante..." style="width:220px">
    </div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th>Data</th><th>Wk</th><th>Op</th><th>Shipment</th><th>Produto</th>
        <th style="text-align:right">Valor R$</th><th>Prioridade</th><th>Status</th>
        <th>SLA</th><th>Responsável</th><th>Conclusão</th><th>Driver</th>
      </tr></thead>
      <tbody id="cftv-tbody">{rows_cftv(d["rows"])}</tbody>
    </table></div>
  </div>
</div>

<script>
function filtrar() {{
  const op     = document.getElementById('cftv_op')?.value     || '';
  const status = document.getElementById('cftv_status')?.value || '';
  const prio   = document.getElementById('cftv_prio')?.value   || '';
  const resp   = document.getElementById('cftv_resp')?.value   || '';
  const search = (document.getElementById('cftv_search')?.value || '').toLowerCase();
  document.querySelectorAll('.cftv-row').forEach(tr => {{
    const ok = (!op     || tr.dataset.operacao === op)
            && (!status || tr.dataset.status   === status)
            && (!prio   || tr.dataset.prio     === prio)
            && (!resp   || tr.dataset.resp     === resp)
            && (!search || (tr.dataset.search || '').includes(search));
    tr.style.display = ok ? '' : 'none';
  }});
}}
{diario_js()}
lucide.createIcons();
</script>
</body>
</html>'''

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("CFTV SSP30")
    print("-" * 40)
    gs   = autenticar()
    rows = carregar_cftv(gs)
    d    = processar_cftv(rows)
    print(f"  Total: {d['total']} | Concluídos: {d['concluidos']} | Em Andamento: {d['em_andamento']} | SLA Vencido: {d['sla_vencido']}")
    html = gerar_html(d)
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  Salvo em: {OUTPUT_HTML}")
    webbrowser.open(f'file:///{OUTPUT_HTML}')
