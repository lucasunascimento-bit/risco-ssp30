"""
bloqueio.py v2 — Aba Bloqueios do Dashboard SSP30
Critério: BPP >= $300 E fraud SHPs >= 5 (janela móvel 90 dias)
Injeta tab-bloqueios em fraude.html
"""

import json, re
from datetime import datetime
from pathlib import Path
from google.cloud import bigquery
from google.auth import default
import gspread

MIN_BPP       = 300
MIN_FRAUD     = 5
FACILITY      = 'Guarulhos Mega'
DRIVERS_CACHE = Path(__file__).parent / '_drivers_conhecidos.json'


def _carregar_ids_conhecidos():
    try:
        return set(json.loads(DRIVERS_CACHE.read_text(encoding='utf-8')).get('ids', []))
    except Exception:
        return set()


def _salvar_ids_conhecidos(ids):
    try:
        DRIVERS_CACHE.write_text(
            json.dumps({'ids': sorted(ids), 'ts': datetime.now().isoformat()}, ensure_ascii=False),
            encoding='utf-8'
        )
    except Exception as e:
        print(f'  Aviso: não salvei cache de IDs: {e}')
HTML_OUT      = Path(__file__).parent / 'fraude.html'
LOG_URL       = 'https://shipping-bo.adminml.com/sauron/shipments/shipment/'
BO_DRIVER     = 'https://shipping-bo.adminml.com/sauron/drivers/driver/'
ANALISTA      = 'Lucas de Oliveira Nascimento'
BLOCK_LIST_ID = '1521Ek2wn8qYLj7g6dh0aBBMmpVYHjCp2hftGKNG9bO0'

# Mapeamento de status da planilha para código JS
_SHEET_STATUS_MAP = {
    'Bloqueado':        'blq',
    'Inativo':          'ina',
    'Já excluído':      'ina',
    'Monitorado':       'ati',
    'Sendo Monitorado': 'ati',
    'Pausado':          'ati',
    'Recusado':         'ati',
    'Desbloqueio':      'ati',
    'Solicitado':       'ati',
}

# Classificações para contar SHPs de fraude (inclui STOLEN ON ROUTE e LOST ON ROUTE)
_FC = (
    "Classification_LM LIKE 'FRAUD%' "
    "OR Classification_LM = 'STOLEN ON ROUTE' "
    "OR Classification_LM = 'PNR C' "
    "OR Classification_LM = 'EMPTY BOX' "
    "OR Classification_LM = 'LOST ON ROUTE'"
)

# Classificações para calcular BPP exibido — alinhado ao KPI Megas (sem STOLEN ON ROUTE)
_FC_BPP = (
    "Classification_LM LIKE 'FRAUD%' "
    "OR Classification_LM = 'PNR C' "
    "OR Classification_LM = 'EMPTY BOX' "
    "OR Classification_LM = 'LOST ON ROUTE'"
)

QUERY = f"""
-- CTE deduplica por (DRIVER_ID, SHIPMENT_ID) antes de agregar
-- DM_LP_MELI_OPTIMIZADO tem 1 linha por item/produto dentro do SHP
-- MAX(BPP_CASHOUT_USD) pega o valor SHP-level (repetido igual em todas as linhas do mesmo SHP)
WITH shp_dedup AS (
    SELECT
        SAFE_CAST(DRIVER_ID AS STRING) AS DRIVER_ID,
        CAST(SHIPMENT_ID AS STRING)    AS SHIPMENT_ID,
        Classification_LM,
        date_bpp,
        MAX(DRIVER_NAME)       AS DRIVER_NAME,
        MAX(PLATE)             AS PLATE,
        MAX(MLP)               AS MLP,
        MAX(BPP_CASHOUT_USD)   AS BPP_CASHOUT_USD
    FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
    WHERE SHP_LG_FACILITY_NAME = '{FACILITY}'
      AND date_bpp >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
      AND date_bpp <= CURRENT_DATE()
      AND DRIVER_ID IS NOT NULL
    GROUP BY 1, 2, 3, 4
)
SELECT
    DRIVER_ID                                                  AS id,
    IFNULL(MAX(DRIVER_NAME), '')                              AS nome,
    IFNULL(MAX(PLATE), '')                                    AS placa,
    IFNULL(MAX(MLP), '')                                      AS mlp,
    COUNT(DISTINCT SHIPMENT_ID)                               AS total,
    COUNT(DISTINCT CASE WHEN {_FC} THEN SHIPMENT_ID END)     AS fraud,
    ROUND(SUM(CASE WHEN {_FC_BPP} THEN BPP_CASHOUT_USD ELSE 0 END), 2) AS bpp,
    APPROX_TOP_COUNT(Classification_LM, 1)[OFFSET(0)].value  AS classe,
    ARRAY_AGG(DISTINCT CASE WHEN {_FC_BPP} THEN FORMAT_DATE('%Y-%m-%d', date_bpp) END IGNORE NULLS) AS meses,
    ARRAY_AGG(CASE WHEN {_FC_BPP} THEN SHIPMENT_ID END IGNORE NULLS
        ORDER BY BPP_CASHOUT_USD DESC LIMIT 500)              AS shp_ids,
    ARRAY_AGG(CASE WHEN {_FC_BPP} THEN ROUND(BPP_CASHOUT_USD, 2) END IGNORE NULLS
        ORDER BY BPP_CASHOUT_USD DESC LIMIT 500)              AS shp_bpps,
    ARRAY_AGG(CASE WHEN {_FC_BPP} THEN Classification_LM END IGNORE NULLS
        ORDER BY BPP_CASHOUT_USD DESC LIMIT 500)              AS shp_cls,
    ARRAY_AGG(CASE WHEN {_FC_BPP} THEN FORMAT_DATE('%d/%m/%Y', date_bpp) END IGNORE NULLS
        ORDER BY BPP_CASHOUT_USD DESC LIMIT 500)              AS shp_dates,
    ARRAY_AGG(CASE WHEN {_FC_BPP} THEN FORMAT_DATE('%Y-W%V', date_bpp) END IGNORE NULLS
        ORDER BY BPP_CASHOUT_USD DESC LIMIT 500)              AS shp_weeks
FROM shp_dedup
GROUP BY 1
HAVING ROUND(SUM(CASE WHEN {_FC_BPP} THEN BPP_CASHOUT_USD ELSE 0 END), 2) >= {MIN_BPP}
   AND COUNT(DISTINCT CASE WHEN {_FC} THEN SHIPMENT_ID END) >= {MIN_FRAUD}
ORDER BY bpp DESC
"""


def ler_block_list_sheets(creds):
    """Lê a aba 'Drivers Bloqueados' da Block List e retorna {driver_id: status_code}."""
    try:
        gc = gspread.Client(auth=creds)
        sh = gc.open_by_key(BLOCK_LIST_ID)
        ws = sh.worksheet('Drivers Bloqueados')
        rows = ws.get_all_values()
        if not rows:
            return {}
        header = rows[0]
        col_cad    = header.index('CAD')
        col_id     = header.index('Driver ID')
        col_status = header.index('Status')
        col_data   = header.index('Data Solicitação') if 'Data Solicitação' in header else -1

        from collections import defaultdict

        def parse_dt(s):
            s = (s or '').strip().replace('//', '/')
            for fmt in ['%d/%m/%Y', '%d/%m/%y']:
                try:
                    return datetime.strptime(s, fmt)
                except Exception:
                    pass
            return datetime.min

        # Coleta todos os registros SSP30 por driver_id
        records = defaultdict(list)
        for r in rows[1:]:
            if len(r) <= col_cad or r[col_cad] != 'SSP30':
                continue
            did    = r[col_id].strip()    if len(r) > col_id    else ''
            status = r[col_status].strip() if len(r) > col_status else ''
            data   = r[col_data].strip()   if col_data >= 0 and len(r) > col_data else ''
            if did and status:
                records[did].append({'status': status, 'dt': parse_dt(data)})

        # Status final = registro mais recente
        result = {}
        for did, recs in records.items():
            recs.sort(key=lambda x: x['dt'], reverse=True)
            code = _SHEET_STATUS_MAP.get(recs[0]['status'], 'ati')
            result[did] = code

        n_blq = sum(1 for v in result.values() if v == 'blq')
        n_ina = sum(1 for v in result.values() if v == 'ina')
        print(f'  Block list Sheets: {len(result)} drivers SSP30 '
              f'({n_blq} bloqueados, {n_ina} inativos)')
        return result
    except Exception as e:
        print(f'  Aviso block list Sheets: {e}')
        return {}


def buscar_dc_kangu(client, driver_ids):
    """Busca o nó/DC dominante (SHP_NODE_ID) dos drivers da Agências Kangu.
    Só retorna quando o valor tem formato de DC de verdade (ex: BRDSP0200),
    não um código de loja/agência de coleta (que tem underscore no ID)."""
    if not driver_ids:
        return {}
    query = """
    SELECT
      SAFE_CAST(ROUTE.SHP_LG_DRIVER_ID AS STRING) AS driver_id,
      SHP_NODE_ID,
      COUNT(*) AS n
    FROM `meli-bi-data.WHOWNER.BT_LP_NODES`
    WHERE SAFE_CAST(ROUTE.SHP_LG_DRIVER_ID AS STRING) IN UNNEST(@driver_ids)
      AND DATE_BPP >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
      AND REGEXP_CONTAINS(IFNULL(SHP_NODE_ID, ''), r'^BR[A-Z]{2,5}[0-9]{2,6}$')
    GROUP BY 1, 2
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter('driver_ids', 'STRING', driver_ids)]
    )
    counts = {}
    for row in client.query(query, job_config=job_config).result():
        counts.setdefault(row['driver_id'], {})[row['SHP_NODE_ID']] = row['n']
    dc_map = {}
    for did, nodes in counts.items():
        top = max(nodes.items(), key=lambda kv: kv[1])
        dc_map[did] = top[0]
    return dc_map


def carregar_dados():
    # Fallback de nomes via _bl_cache.json (Google Sheets)
    name_lookup = {}
    try:
        bl_cache_path = Path(__file__).parent / '_bl_cache.json'
        bl_cache = json.loads(bl_cache_path.read_text(encoding='utf-8'))
        for r in bl_cache:
            did = str(r.get('Driver ID', '') or '').strip()
            nm  = str(r.get('Nome', '') or '').strip()
            if did and nm:
                name_lookup.setdefault(did, nm)
        print(f'  Nome lookup: {len(name_lookup)} drivers do _bl_cache.json')
    except Exception as e:
        print(f'  Aviso name_lookup: {e}')

    creds, _ = default(scopes=[
        'https://www.googleapis.com/auth/bigquery.readonly',
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/drive.readonly',
    ])
    print('Lendo block list da planilha...')
    sheet_status = ler_block_list_sheets(creds)

    client = bigquery.Client(credentials=creds, project='meli-bi-data')
    print(f'Consultando candidatos (BPP >= ${MIN_BPP}, fraud >= {MIN_FRAUD})...')
    rows = list(client.query(QUERY).result())
    print(f'  {len(rows)} candidatos encontrados')
    drivers = []
    for row in rows:
        total = int(row['total'])
        fraud = int(row['fraud'])
        pct   = round(fraud / total * 100, 1) if total else 0.0
        meses     = sorted(m for m in (row['meses'] or []) if m)
        raw_ids   = [str(s) for s in (row['shp_ids']   or []) if s]
        raw_bpps  = [float(b) for b in (row['shp_bpps'] or []) if b is not None]
        raw_cls   = [str(c) for c in (row['shp_cls']   or []) if c]
        raw_dates = [str(d) for d in (row['shp_dates'] or []) if d]
        raw_weeks = [str(w) for w in (row['shp_weeks'] or []) if w]
        seen_ids = set()
        shps = []
        for _i, _sid in enumerate(raw_ids):
            if _sid not in seen_ids:
                seen_ids.add(_sid)
                shps.append({
                    'id':   _sid,
                    'bpp':  raw_bpps[_i]  if _i < len(raw_bpps)  else 0.0,
                    'cls':  raw_cls[_i]   if _i < len(raw_cls)   else '',
                    'date': raw_dates[_i] if _i < len(raw_dates) else '',
                    'week': raw_weeks[_i] if _i < len(raw_weeks) else '',
                })
                if len(shps) >= 500:
                    break
        nome_bq = (row['nome'] or '').strip()
        nome    = nome_bq or name_lookup.get(str(row['id']), '')
        drivers.append({
            'id':    row['id'],
            'nome':  nome,
            'placa': (row['placa'] or '').strip(),
            'mlp':   row['mlp'] or '',
            'total': total,
            'fraud': fraud,
            'pct':   pct,
            'bpp':   float(row['bpp'] or 0),
            'classe': row['classe'] or '',
            'meses': meses,
            'shps':  shps,
        })

    kangu_ids = [str(d['id']) for d in drivers if 'kangu' in d['mlp'].lower()]
    print(f'Consultando nó/DC para {len(kangu_ids)} driver(s) da Agências Kangu...')
    dc_map = buscar_dc_kangu(client, kangu_ids)
    for d in drivers:
        d['dc'] = dc_map.get(str(d['id']), '')
    if dc_map:
        print(f'  {len(dc_map)} driver(s) com DC identificado')

    ids_conhecidos = _carregar_ids_conhecidos()
    novos = 0
    for d in drivers:
        d['is_new'] = str(d['id']) not in ids_conhecidos
        if d['is_new']:
            novos += 1
    if novos:
        print(f'  {novos} driver(s) NOVO(S) detectado(s)')

    return drivers, sheet_status


def gerar_tab(drivers, sheet_status=None):
    if not drivers:
        return '<div id="tab-bloqueios" class="content"><div style="padding:40px;text-align:center;color:#6b7280">Nenhum candidato encontrado.</div></div>'

    now         = datetime.now().strftime('%d/%m/%Y %H:%M')
    now_iso     = datetime.now().isoformat()
    n_drivers   = len(drivers)
    total_bpp   = sum(d['bpp'] for d in drivers)
    total_fraud = sum(d['fraud'] for d in drivers)
    avg_pct     = round(sum(d['pct'] for d in drivers) / n_drivers, 1)

    mlp_count = {}
    for d in drivers:
        k = d['mlp'] or 'Sem transportadora'
        mlp_count[k] = mlp_count.get(k, 0) + 1
    mlp_sorted   = sorted(mlp_count.items(), key=lambda x: -x[1])
    top_mlp      = mlp_sorted[0][0] if mlp_sorted else '—'
    top_mlp_n    = mlp_sorted[0][1] if mlp_sorted else 0
    top_mlp_short = (top_mlp[:18] + '…') if len(top_mlp) > 18 else top_mlp

    mlp_items  = mlp_sorted[:8]
    mlp_labels = json.dumps([x[0][:22] for x in mlp_items]).replace('</', '<\\/')
    mlp_vals   = json.dumps([x[1] for x in mlp_items])

    top10      = drivers[:10]
    top_labels = json.dumps([d['id'] for d in top10])
    top_vals   = json.dumps([round(d['bpp'], 2) for d in top10])

    all_classes = sorted({d['classe'] for d in drivers if d['classe']})
    all_mlps    = sorted({d['mlp'] for d in drivers if d['mlp']})

    mlp_cbs = ''.join(
        f'<label class="blq-ms-item"><input type="checkbox" class="blq-ms-cb blq-ms-cb-mlp" value="{m}" onchange="blqMsChg(\'mlp\')"> {m}</label>'
        for m in all_mlps
    )
    cls_cbs = ''.join(
        f'<label class="blq-ms-item"><input type="checkbox" class="blq-ms-cb blq-ms-cb-cls" value="{c}" onchange="blqMsChg(\'cls\')"> {c}</label>'
        for c in all_classes
    )

    data_json = json.dumps(drivers, ensure_ascii=False).replace('</', '<\\/')

    # Extrair IDs com status=Bloqueado do BL_DATA já embutido no fraude.html (fonte de verdade)
    blocked_ids = []
    try:
        existing_html = HTML_OUT.read_text(encoding='utf-8')
        idx_bl = existing_html.find('const BL_DATA')
        if idx_bl >= 0:
            sb = existing_html.find('[', idx_bl)
            depth = 0
            eb = sb
            for i, c in enumerate(existing_html[sb:], sb):
                if c == '[': depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        eb = i
                        break
            bl_data = json.loads(existing_html[sb:eb+1])
            blocked_ids = sorted(set(
                r['driver_id'] for r in bl_data
                if r.get('status', '').lower() == 'bloqueado' and r.get('driver_id')
            ))
    except Exception:
        pass
    blocked_ids_js   = json.dumps(blocked_ids)
    sheet_status_js  = json.dumps(sheet_status or {}, ensure_ascii=False)

    return f"""<div id="tab-bloqueios" class="content">
<style>
#tab-bloqueios .blq-crit{{background:#0d1017;border:1px solid rgba(239,68,68,.2);border-radius:6px;padding:8px 16px;font-size:11px;color:#6b7280;display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
#tab-bloqueios .blq-crit strong{{color:#f87171}}
#tab-bloqueios .blq-kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}
#tab-bloqueios .blq-vis-row{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}}
#tab-bloqueios .blq-vis-box{{background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px;text-align:center}}
#tab-bloqueios .blq-vis-box.amb{{border-color:rgba(251,191,36,.35)}}
#tab-bloqueios .blq-vis-l{{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#6b7280;margin-bottom:4px}}
#tab-bloqueios .blq-vis-box.amb .blq-vis-l{{color:#fbbf24}}
#tab-bloqueios .blq-gauge-v{{font-size:20px;font-weight:700;color:#fbbf24;margin-top:-58px}}
#tab-bloqueios .blq-s-ativo{{background:rgba(74,222,128,.08);color:#4ade80;border:1px solid rgba(74,222,128,.2)}}
#tab-bloqueios .blq-kpi{{background:#0d1321;border:1px solid #111827;border-radius:8px;padding:14px 16px}}
#tab-bloqueios .blq-kpi-l{{font-size:9px;text-transform:uppercase;letter-spacing:.7px;color:#4b5563;font-weight:700;margin-bottom:6px}}
#tab-bloqueios .blq-kpi-v{{font-size:22px;font-weight:800;color:#fff;line-height:1;letter-spacing:-1px}}
#tab-bloqueios .blq-kpi-s{{font-size:10px;color:#6b7280;margin-top:4px}}
#tab-bloqueios .blq-kpi.red .blq-kpi-v{{color:#f87171}}
#tab-bloqueios .blq-kpi.amb .blq-kpi-v{{color:#fbbf24}}
#tab-bloqueios .blq-kpi.blu .blq-kpi-v{{color:#60a5fa}}
#tab-bloqueios .blq-controls{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:10px 0;border-bottom:1px solid #111827}}
#tab-bloqueios .blq-controls select,#tab-bloqueios .blq-controls input{{background:#0d1321;border:1px solid #1f2937;color:#e2e8f0;font-size:12px;padding:4px 8px;border-radius:6px;height:30px}}
#tab-bloqueios .blq-btn-r{{background:transparent;border:1px solid #1f2937;color:#4b5563;font-size:11px;padding:4px 12px;border-radius:6px;cursor:pointer;height:30px}}
#tab-bloqueios .blq-btn-r:hover{{border-color:#374151;color:#9ca3af}}
#tab-bloqueios .blq-ms-wrap{{position:relative;display:inline-block}}
#tab-bloqueios .blq-ms-btn{{background:#0d1321;border:1px solid #1f2937;color:#e2e8f0;font-size:12px;padding:0 10px;border-radius:6px;height:30px;cursor:pointer;min-width:110px;text-align:left}}
#tab-bloqueios .blq-ms-btn.active{{border-color:#ef4444;color:#f87171}}
#tab-bloqueios .blq-ms-panel{{position:absolute;top:34px;left:0;z-index:200;background:#0d1321;border:1px solid #1f2937;border-radius:8px;padding:8px 0 6px;min-width:200px;max-height:280px;overflow-y:auto;box-shadow:0 8px 24px rgba(0,0,0,.7)}}
#tab-bloqueios .blq-ms-actions{{display:flex;gap:5px;padding:4px 10px 8px;border-bottom:1px solid #111827;margin-bottom:4px}}
#tab-bloqueios .blq-ms-actions button{{font-size:10px;color:#4b5563;background:transparent;border:1px solid #1f2937;border-radius:4px;padding:2px 8px;cursor:pointer}}
#tab-bloqueios .blq-ms-item{{display:flex;align-items:center;gap:8px;padding:4px 10px;font-size:12px;color:#e2e8f0;cursor:pointer}}
#tab-bloqueios .blq-ms-item:hover{{background:#111827}}
#tab-bloqueios .blq-ms-item input{{accent-color:#ef4444;cursor:pointer;width:13px;height:13px}}
#tab-bloqueios .blq-tbl-scr{{border-radius:8px;border:1px solid #111827;overflow-x:auto;margin-top:10px}}
#tab-bloqueios table{{width:100%;border-collapse:collapse;font-size:12px}}
#tab-bloqueios thead th{{background:#0b101e;padding:9px 12px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.7px;color:#374151;font-weight:700;border-bottom:1px solid #111827;white-space:nowrap;cursor:pointer;user-select:none}}
#tab-bloqueios thead th:hover{{color:#9ca3af}}
#tab-bloqueios thead th.blq-sorted{{color:#9ca3af}}
#tab-bloqueios thead th.blq-no-sort{{cursor:default}}
#tab-bloqueios thead th.blq-no-sort:hover{{color:#374151}}
#tab-bloqueios tbody tr.blq-dr-row{{border-bottom:1px solid #0b101e;transition:background .1s}}
#tab-bloqueios tbody tr.blq-dr-row:hover{{background:#0d1321}}
#tab-bloqueios tbody td{{padding:8px 12px;color:#e2e8f0;white-space:nowrap}}
#tab-bloqueios .blq-did-btn{{background:none;border:none;color:#f9fafb;font-weight:700;cursor:pointer;font-size:12px;padding:0;font-family:inherit;display:flex;align-items:center;gap:5px}}
#tab-bloqueios .blq-did-btn:hover{{color:#60a5fa}}
#tab-bloqueios .blq-chv{{font-size:9px;color:#374151;transition:transform .15s;display:inline-block}}
#tab-bloqueios .blq-chv.open{{transform:rotate(180deg);color:#60a5fa}}
#tab-bloqueios .blq-shp-row td{{background:#06090f;padding:10px 16px 14px 52px;border-bottom:1px solid #1f2937}}
#tab-bloqueios .blq-shp-meta{{display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap}}
#tab-bloqueios .blq-drv-link{{font-size:10px;color:#9ca3af;background:#111827;border:1px solid #1f2937;border-radius:4px;padding:3px 10px;text-decoration:none;white-space:nowrap}}
#tab-bloqueios .blq-drv-link:hover{{color:#e2e8f0;border-color:#374151}}
#tab-bloqueios .blq-shp-list{{display:flex;flex-wrap:wrap;gap:6px}}
#tab-bloqueios .blq-chip{{font-size:11px;color:#60a5fa;background:rgba(96,165,250,.08);border:1px solid rgba(96,165,250,.2);border-radius:4px;padding:3px 8px;text-decoration:none;font-variant-numeric:tabular-nums;white-space:nowrap}}
#tab-bloqueios .blq-chip:hover{{background:rgba(96,165,250,.18);border-color:rgba(96,165,250,.4)}}
#tab-bloqueios .blq-tag{{font-size:10px;color:#9ca3af;background:#111827;padding:2px 7px;border-radius:4px;max-width:120px;overflow:hidden;text-overflow:ellipsis;display:inline-block}}
#tab-bloqueios .blq-badge{{display:inline-block;font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px;cursor:pointer;transition:opacity .1s}}
#tab-bloqueios .blq-badge:hover{{opacity:.8}}
#tab-bloqueios .blq-novo{{display:inline-block;font-size:8px;font-weight:800;padding:1px 5px;border-radius:10px;margin-left:5px;vertical-align:middle;background:#f59e0b;color:#1a1a1a;letter-spacing:.4px;animation:blq-novo-pulse 1.5s ease-in-out infinite}}
@keyframes blq-novo-pulse{{0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(245,158,11,.4)}}50%{{opacity:.75;box-shadow:0 0 0 4px rgba(245,158,11,0)}}}}
#tab-bloqueios .blq-s-ati{{background:rgba(156,163,175,.08);color:#9ca3af;border:1px solid rgba(156,163,175,.2)}}
#tab-bloqueios .blq-s-mon{{background:rgba(74,222,128,.08);color:#4ade80;border:1px solid rgba(74,222,128,.2)}}
#tab-bloqueios .blq-s-inv{{background:rgba(251,191,36,.08);color:#fbbf24;border:1px solid rgba(251,191,36,.2)}}
#tab-bloqueios .blq-s-blq{{background:rgba(239,68,68,.08);color:#f87171;border:1px solid rgba(239,68,68,.2)}}
#tab-bloqueios .blq-s-ina{{background:rgba(107,114,128,.08);color:#6b7280;border:1px solid rgba(107,114,128,.3);text-decoration:line-through}}
#tab-bloqueios .blq-btn-pdf{{background:rgba(37,99,235,.1);border:1px solid rgba(37,99,235,.25);color:#93c5fd;font-size:10px;padding:3px 9px;border-radius:5px;cursor:pointer;white-space:nowrap;font-family:inherit;transition:background .1s}}
#tab-bloqueios .blq-btn-pdf:hover{{background:rgba(37,99,235,.22);border-color:#3b82f6;color:#bfdbfe}}
</style>
<div style="padding:20px 32px">

  <!-- HEADER -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:16px;font-weight:700;color:#f9fafb">Candidatos a Bloqueio</div>
      <div style="display:flex;align-items:center;gap:8px;margin-top:4px;flex-wrap:wrap">
        <span style="font-size:11px;color:#6b7280">SSP30 · Guarulhos Mega · janela 90 dias (acúmulo)</span>
        <span id="blq-last-update" data-ts="{now_iso}" style="background:rgba(34,211,238,.1);color:#22d3ee;border:1px solid rgba(34,211,238,.25);border-radius:10px;padding:2px 10px;font-size:10px;font-weight:600">&#8635; Atualizado {now} · semanal</span>
      </div>
    </div>
    <button onclick="blqExportCSV()" style="background:#1f2937;color:#9ca3af;border:1px solid #374151;border-radius:6px;padding:5px 14px;font-size:11px;cursor:pointer">⬇ Exportar CSV</button>
  </div>

  <!-- CRITERIO -->
  <div class="blq-crit" style="margin-bottom:14px">
    <strong>Premissa de bloqueio:</strong>
    <span>BPP acumulado &gt; <strong>US$ {MIN_BPP}</strong></span>
    <span style="color:#1f2937">·</span>
    <span>Fraud SHPs &ge; <strong>{MIN_FRAUD} pacotes</strong></span>
    <span style="color:#1f2937">·</span>
    <span>Status salvo por browser (persiste entre sessões)</span>
  </div>

  <!-- Overview status cards -->
  <div id="blq-overview" style="display:flex;gap:8px;flex-wrap:wrap;padding:12px 18px 4px;align-items:center">
    <div style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:6px;padding:7px 14px;text-align:center;min-width:80px">
      <div style="font-size:18px;font-weight:700;color:#f87171" id="blq-ov-blq">0</div>
      <div style="font-size:10px;color:#9ca3af">Bloqueados</div>
    </div>
    <div style="background:rgba(107,114,128,.08);border:1px solid rgba(107,114,128,.25);border-radius:6px;padding:7px 14px;text-align:center;min-width:80px">
      <div style="font-size:18px;font-weight:700;color:#6b7280" id="blq-ov-ina">0</div>
      <div style="font-size:10px;color:#9ca3af">Inativos</div>
    </div>
    <div style="background:rgba(156,163,175,.06);border:1px solid rgba(156,163,175,.15);border-radius:6px;padding:7px 14px;text-align:center;min-width:80px">
      <div style="font-size:18px;font-weight:700;color:#9ca3af" id="blq-ov-ati">0</div>
      <div style="font-size:10px;color:#9ca3af">Ativos</div>
    </div>
    <div style="margin-left:auto;display:flex;align-items:center;gap:8px;font-size:11px;color:#6b7280;flex-wrap:wrap">
      <span>&#128197; Período:</span>
      <div style="display:flex;gap:2px;background:#080d19;border-radius:6px;padding:2px">
        <button class="blq-period-chip" data-days="7" onclick="blqSetPeriodChip(7)"
          style="padding:4px 9px;font-size:10px;color:#6b7280;border-radius:4px;cursor:pointer;background:transparent;border:none">7d</button>
        <button class="blq-period-chip" data-days="30" onclick="blqSetPeriodChip(30)"
          style="padding:4px 9px;font-size:10px;color:#6b7280;border-radius:4px;cursor:pointer;background:transparent;border:none">30d</button>
        <button class="blq-period-chip" data-days="90" onclick="blqSetPeriodChip(90)"
          style="padding:4px 9px;font-size:10px;color:#6b7280;border-radius:4px;cursor:pointer;background:transparent;border:none">90d</button>
        <button class="blq-period-chip active" data-days="0" onclick="blqSetPeriodChip(0)"
          style="padding:4px 9px;font-size:10px;color:#04303a;background:#22d3ee;border-radius:4px;cursor:pointer;border:none;font-weight:700">Tudo</button>
      </div>
      <span style="color:#1f2937">|</span>
      <input type="date" id="blq-cal-de" oninput="blqCustomPeriod()"
        style="background:#111827;border:1px solid #1f2937;border-radius:4px;color:#e2e8f0;font-size:11px;padding:3px 6px;cursor:pointer">
      <span>&#8594;</span>
      <input type="date" id="blq-cal-ate" oninput="blqCustomPeriod()"
        style="background:#111827;border:1px solid #1f2937;border-radius:4px;color:#e2e8f0;font-size:11px;padding:3px 6px;cursor:pointer">
      <span id="blq-period-label" style="font-size:10px;color:#22d3ee"></span>
    </div>
  </div>
  <!-- KPIs -->
  <div class="blq-kpis" style="margin-bottom:14px">
    <div class="blq-kpi red">
      <div class="blq-kpi-l">Candidatos</div>
      <div class="blq-kpi-v" id="blq-k-total">{n_drivers}</div>
      <div class="blq-kpi-s">drivers no critério</div>
    </div>
    <div class="blq-kpi red">
      <div class="blq-kpi-l">BPP Acumulado</div>
      <div class="blq-kpi-v" id="blq-k-bpp">US$ {total_bpp:,.0f}</div>
      <div class="blq-kpi-s">total exposto</div>
    </div>
    <div class="blq-kpi">
      <div class="blq-kpi-l">Fraud SHPs</div>
      <div class="blq-kpi-v" id="blq-k-fraud">{total_fraud:,}</div>
      <div class="blq-kpi-s">pacotes comprometidos</div>
    </div>
    <div class="blq-kpi blu">
      <div class="blq-kpi-l">Top Transportadora</div>
      <div class="blq-kpi-v" style="font-size:13px;letter-spacing:0;padding-top:4px">{top_mlp_short}</div>
      <div class="blq-kpi-s">{top_mlp_n} candidatos</div>
    </div>
  </div>

  <!-- GAUGE + ANEL DE STATUS -->
  <div class="blq-vis-row">
    <div class="blq-vis-box amb">
      <div class="blq-vis-l">Média % Fraude</div>
      <div style="position:relative;height:110px"><canvas id="blqGaugeFraude"></canvas></div>
      <div class="blq-gauge-v" id="blq-gauge-v">{avg_pct}%</div>
    </div>
    <div class="blq-vis-box">
      <div class="blq-vis-l">Status dos Candidatos</div>
      <div style="position:relative;height:110px"><canvas id="blqDonutStatus"></canvas></div>
    </div>
  </div>

  <!-- GRAFICOS -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Candidatos por Transportadora</div>
      <div style="position:relative;height:200px"><canvas id="blqCMlp"></canvas></div>
    </div>
    <div style="background:#0d1321;border:1px solid #111827;border-radius:8px;padding:12px 14px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#6b7280;margin-bottom:8px">Top 10 por BPP Acumulado</div>
      <div style="position:relative;height:200px"><canvas id="blqCTop"></canvas></div>
    </div>
  </div>

  <!-- CONTROLES -->
  <div class="blq-controls">
    <div class="blq-ms-wrap" id="blq-msw-mlp">
      <button class="blq-ms-btn" id="blq-msb-mlp" onclick="blqToggleMs('mlp')">Todas transp. ▾</button>
      <div class="blq-ms-panel" id="blq-msp-mlp" style="display:none">
        <div class="blq-ms-actions">
          <button onclick="blqMsAll('mlp')">Todas</button>
          <button onclick="blqMsNone('mlp')">Nenhuma</button>
        </div>
        {mlp_cbs}
      </div>
    </div>
    <div class="blq-ms-wrap" id="blq-msw-cls">
      <button class="blq-ms-btn" id="blq-msb-cls" onclick="blqToggleMs('cls')">Toda classe ▾</button>
      <div class="blq-ms-panel" id="blq-msp-cls" style="display:none">
        <div class="blq-ms-actions">
          <button onclick="blqMsAll('cls')">Todas</button>
          <button onclick="blqMsNone('cls')">Nenhuma</button>
        </div>
        {cls_cbs}
      </div>
    </div>
    <span style="color:#1f2937;font-size:18px">|</span>
    <span style="font-size:11px;color:#6b7280">Status</span>
    <select id="blq-status" onchange="blqRender()">
      <option value="">Todos</option>
      <option value="ati">Ativo</option>
      <option value="blq">Bloqueado</option>
      <option value="ina">Inativo</option>
    </select>
    <input type="number" id="blq-pct" value="0" min="0" max="100" oninput="blqRender()"
      style="width:52px;text-align:center" placeholder="% min">
    <input type="search" id="blq-busca" placeholder="Driver ID..." oninput="blqRender()"
      style="width:130px">
    <button class="blq-btn-r" onclick="blqResetF()">&#x2715; Limpar</button>
    <button class="blq-btn-r" onclick="blqResetStatus()" title="Apaga overrides manuais de Status e volta ao estado da block list">&#x21BA; Status</button>
    <span style="font-size:11px;color:#4b5563;margin-left:4px" id="blq-tbl-ct"></span>
  </div>

  <!-- CONCLUIDOS -->
  <div style="margin-bottom:14px">
    <div style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px">Concluídos <span id="blq-concl-count" style="font-size:10px;color:#4b5563;text-transform:none;font-weight:400"></span></div>
    <div class="blq-tbl-scr">
      <table>
        <thead>
          <tr>
            <th class="blq-no-sort">#</th>
            <th onclick="blqSortBy('bpp')">BPP (USD) ↕</th>
            <th class="blq-no-sort">Driver ID</th>
            <th class="blq-no-sort">Transportadora</th>
            <th onclick="blqSortBy('fraud')">Fraud SHPs ↕</th>
            <th onclick="blqSortBy('pct')">% Fraude ↕</th>
            <th onclick="blqSortBy('total')">Total SHPs ↕</th>
            <th class="blq-no-sort">Classificacao</th>
            <th class="blq-no-sort">Meses ativo</th>
            <th class="blq-no-sort">Ação</th>
            <th class="blq-no-sort">Status</th>
            <th class="blq-no-sort">PDF</th>
          </tr>
        </thead>
        <tbody id="blq-concl-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- EM ACOMPANHAMENTO -->
  <div style="margin-bottom:14px">
    <div style="font-size:11px;font-weight:700;color:#fbbf24;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px">Em acompanhamento <span id="blq-acomp-count" style="font-size:10px;color:#4b5563;text-transform:none;font-weight:400"></span></div>
    <div class="blq-tbl-scr">
      <table>
        <thead>
          <tr>
            <th class="blq-no-sort">#</th>
            <th onclick="blqSortBy('bpp')">BPP (USD) ↕</th>
            <th class="blq-no-sort">Driver ID</th>
            <th class="blq-no-sort">Transportadora</th>
            <th onclick="blqSortBy('fraud')">Fraud SHPs ↕</th>
            <th onclick="blqSortBy('pct')">% Fraude ↕</th>
            <th onclick="blqSortBy('total')">Total SHPs ↕</th>
            <th class="blq-no-sort">Classificacao</th>
            <th class="blq-no-sort">Meses ativo</th>
            <th class="blq-no-sort">Ação</th>
            <th class="blq-no-sort">Status</th>
            <th class="blq-no-sort">PDF</th>
          </tr>
        </thead>
        <tbody id="blq-acomp-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- CASOS PARA ACOMPANHAR -->
  <div>
    <div style="font-size:11px;font-weight:700;color:#4ade80;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px">Casos para acompanhar <span id="blq-fila-count" style="font-size:10px;color:#4b5563;text-transform:none;font-weight:400"></span></div>
    <div class="blq-tbl-scr">
      <table>
        <thead>
          <tr>
            <th class="blq-no-sort">#</th>
            <th onclick="blqSortBy('bpp')">BPP (USD) ↕</th>
            <th class="blq-no-sort">Driver ID</th>
            <th class="blq-no-sort">Transportadora</th>
            <th onclick="blqSortBy('fraud')">Fraud SHPs ↕</th>
            <th onclick="blqSortBy('pct')">% Fraude ↕</th>
            <th onclick="blqSortBy('total')">Total SHPs ↕</th>
            <th class="blq-no-sort">Classificacao</th>
            <th class="blq-no-sort">Meses ativo</th>
            <th class="blq-no-sort">Ação</th>
            <th class="blq-no-sort">Status</th>
            <th class="blq-no-sort">PDF</th>
          </tr>
        </thead>
        <tbody id="blq-fila-tbody"></tbody>
      </table>
    </div>
  </div>

</div>
<script>
(function(){{
var BLQ_DATA     = {data_json};
var BLQ_LOG      = '{LOG_URL}';
var BLQ_DRV      = '{BO_DRIVER}';
var BLQ_ANALISTA = '{ANALISTA}';
var BLQ_BLOCKED     = new Set({blocked_ids_js});
var BLQ_SHEET_STATUS = {sheet_status_js};
(function(){{
  var el = document.getElementById('blq-last-update');
  if(!el) return;
  var ts = el.getAttribute('data-ts');
  if(!ts) return;
  var dias = Math.floor((Date.now() - new Date(ts).getTime())/86400000);
  var suf = dias<=0 ? 'hoje' : dias===1 ? 'há 1 dia' : ('há '+dias+' dias');
  el.textContent = el.textContent + ' (' + suf + ')';
}})();
var _blqSort = 'bpp', _blqDir = -1;

var _blqCMlp = null, _blqCTop = null, _blqGauge = null, _blqDonut = null;

function blqBuildGaugeDonut(avgPct, ov){{
  var eG = document.getElementById('blqGaugeFraude');
  var eD = document.getElementById('blqDonutStatus');
  if(eG){{
    if(_blqGauge){{ try{{_blqGauge.destroy();}}catch(ee){{}} _blqGauge=null; }}
    try{{
      _blqGauge = new Chart(eG, {{
        type:'doughnut',
        data:{{datasets:[{{data:[avgPct, Math.max(0,100-avgPct)],backgroundColor:['#fbbf24','#1c1f26'],borderWidth:0}}]}},
        options:{{rotation:-90,circumference:180,cutout:'76%',responsive:true,maintainAspectRatio:false,
          plugins:{{legend:{{display:false}},tooltip:{{enabled:false}}}}}}
      }});
    }}catch(ee){{console.error('blqGauge:',ee);}}
  }}
  if(eD){{
    if(_blqDonut){{ try{{_blqDonut.destroy();}}catch(ee){{}} _blqDonut=null; }}
    try{{
      _blqDonut = new Chart(eD, {{
        type:'doughnut',
        data:{{labels:['Bloqueados','Ativos','Inativos'],datasets:[{{data:[ov.blq||0,ov.ati||0,ov.ina||0],backgroundColor:['#ef4444','#22d3ee','#374151'],borderWidth:0}}]}},
        options:{{responsive:true,maintainAspectRatio:false,cutout:'55%',
          plugins:{{legend:{{position:'bottom',labels:{{color:'#9ca3af',font:{{size:9}},boxWidth:8,padding:6}}}}}}}}
      }});
    }}catch(ee){{console.error('blqDonut:',ee);}}
  }}
}}

function blqBuildCharts(){{
  var eMlp = document.getElementById('blqCMlp');
  var eTop = document.getElementById('blqCTop');
  if(eMlp){{
    var pw = eMlp.parentElement ? eMlp.parentElement.clientWidth : 0;
    if(pw > 10){{ eMlp.setAttribute('width', pw); eMlp.setAttribute('height', 200); }}
    if(_blqCMlp){{ try{{_blqCMlp.destroy();}}catch(ee){{}} _blqCMlp=null; }}
    try{{
      _blqCMlp = new Chart(eMlp, {{
        type:'bar',
        data:{{labels:{mlp_labels}, datasets:[{{data:{mlp_vals},backgroundColor:'rgba(239,68,68,0.65)',borderRadius:3,barThickness:16}}]}},
        options:{{indexAxis:'y',responsive:false,maintainAspectRatio:false,
          plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return ' '+c.parsed.x+' drivers';}}}}}}}},
          scales:{{x:{{grid:{{color:'#111827'}},ticks:{{color:'#6b7280',font:{{size:9}}}}}},y:{{grid:{{display:false}},ticks:{{color:'#9ca3af',font:{{size:9}}}}}}}}
        }}
      }});
    }}catch(ee){{console.error('blqCMlp:',ee);}}
  }}
  if(eTop){{
    var pw2 = eTop.parentElement ? eTop.parentElement.clientWidth : 0;
    if(pw2 > 10){{ eTop.setAttribute('width', pw2); eTop.setAttribute('height', 200); }}
    if(_blqCTop){{ try{{_blqCTop.destroy();}}catch(ee){{}} _blqCTop=null; }}
    try{{
      _blqCTop = new Chart(eTop, {{
        type:'bar',
        data:{{labels:{top_labels}, datasets:[{{data:{top_vals},backgroundColor:'rgba(251,191,36,0.65)',borderRadius:3,barThickness:16}}]}},
        options:{{indexAxis:'y',responsive:false,maintainAspectRatio:false,
          plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:function(c){{return ' $'+c.parsed.x.toLocaleString('en-US',{{minimumFractionDigits:2}});}}}}}}}},
          scales:{{x:{{grid:{{color:'#111827'}},ticks:{{color:'#6b7280',font:{{size:9}},callback:function(v){{return '$'+v.toLocaleString();}}}}}},y:{{grid:{{display:false}},ticks:{{color:'#9ca3af',font:{{size:9}}}}}}}}
        }}
      }});
    }}catch(ee){{console.error('blqCTop:',ee);}}
  }}
  var _ovInit = {{ati:0,blq:0,ina:0}};
  BLQ_DATA.forEach(function(d){{ var s=blqGetSt2(d.id); if(_ovInit[s]!==undefined)_ovInit[s]++; else _ovInit.ati++; }});
  blqBuildGaugeDonut({avg_pct}, _ovInit);
}}

function blqGetSt(id) {{
  try {{ return localStorage.getItem('blq_vg_'+id) || 'mon'; }} catch(e) {{ return 'mon'; }}
}}
function blqMlpLabel(d) {{
  return (d.mlp||'—') + (d.dc ? ' (DC '+d.dc+')' : '');
}}
function blqNextSt(id) {{
  var cycle = ['mon','inv','blq'];
  var cur; try {{ cur = localStorage.getItem('blq_vg_'+id) || 'mon'; }} catch(e) {{ cur = 'mon'; }}
  var next = cycle[(cycle.indexOf(cur)+1)%3];
  try {{ localStorage.setItem('blq_vg_'+id, next); }} catch(e) {{}}
  blqRender();
}}
function blqGetSt2(id) {{
  var valid = {{ati:1,blq:1,ina:1}};
  try {{
    var s = localStorage.getItem('blq_s2_'+id);
    if (s !== null) return valid[s] ? s : 'ati';
  }} catch(e) {{}}
  var sh = BLQ_SHEET_STATUS[id]; if (sh) return valid[sh] ? sh : 'ati';
  return BLQ_BLOCKED.has(id) ? 'blq' : 'ati';
}}
function blqToggleSt2(id) {{
  var cycle = ['ati','blq','ina'];
  var cur = blqGetSt2(id);
  var next = cycle[(cycle.indexOf(cur)+1) % cycle.length];
  try {{
    localStorage.setItem('blq_s2_'+id, next);
    localStorage.setItem('blq_stts_'+id, new Date().toISOString());
  }} catch(e) {{}}
  blqRender();
}}
function blqSortBy(k) {{
  if(_blqSort===k) _blqDir*=-1; else {{_blqSort=k;_blqDir=-1;}}
  blqRender();
}}
function blqToggleMs(id) {{
  var p = document.getElementById('blq-msp-'+id);
  p.style.display = p.style.display==='none' ? 'block' : 'none';
}}
function blqMsChg(id) {{ blqUpdMsBtn(id); blqRender(); }}
function blqUpdMsBtn(id) {{
  var n = document.querySelectorAll('.blq-ms-cb-'+id+':checked').length;
  var btn = document.getElementById('blq-msb-'+id);
  var lbl = id==='mlp' ? 'transp.' : 'classe(s)';
  var def = id==='mlp' ? 'Todas transp. ▾' : 'Toda classe ▾';
  btn.textContent = n ? n+' '+lbl+' ▾' : def;
  btn.className = n ? 'blq-ms-btn active' : 'blq-ms-btn';
}}
function blqMsAll(id) {{ document.querySelectorAll('.blq-ms-cb-'+id).forEach(function(e){{e.checked=true;}}); blqUpdMsBtn(id); blqRender(); }}
function blqMsNone(id) {{ document.querySelectorAll('.blq-ms-cb-'+id).forEach(function(e){{e.checked=false;}}); blqUpdMsBtn(id); blqRender(); }}

function blqSetPeriodChip(days) {{
  var deEl = document.getElementById('blq-cal-de');
  var ateEl = document.getElementById('blq-cal-ate');
  if (!deEl || !ateEl) return;
  if (days === 0) {{
    deEl.value = ''; ateEl.value = '';
  }} else {{
    var today = new Date();
    var from = new Date(today);
    from.setDate(from.getDate() - days);
    ateEl.value = today.toISOString().slice(0, 10);
    deEl.value = from.toISOString().slice(0, 10);
  }}
  document.querySelectorAll('.blq-period-chip').forEach(function(b) {{
    var isActive = b.dataset.days == days;
    b.classList.toggle('active', isActive);
    b.style.background = isActive ? '#22d3ee' : 'transparent';
    b.style.color = isActive ? '#04303a' : '#6b7280';
    b.style.fontWeight = isActive ? '700' : '400';
  }});
  blqRender();
}}
function blqCustomPeriod() {{
  document.querySelectorAll('.blq-period-chip').forEach(function(b) {{
    b.classList.remove('active'); b.style.background = 'transparent'; b.style.color = '#6b7280'; b.style.fontWeight = '400';
  }});
  blqRender();
}}
function blqRender() {{
  var de  = ((document.getElementById('blq-cal-de')||{{}}).value||'');
  var ate = ((document.getElementById('blq-cal-ate')||{{}}).value||'');
  var lbl = document.getElementById('blq-period-label');
  if (lbl) lbl.textContent = (de || ate) ? (de||'início') + ' → ' + (ate||'hoje') : '';
  var stF    = (document.getElementById('blq-status')||{{}}).value||'';
  var minPct = parseFloat((document.getElementById('blq-pct')||{{}}).value)||0;
  var busca  = ((document.getElementById('blq-busca')||{{}}).value||'').trim();
  var mlpSel = new Set([].slice.call(document.querySelectorAll('.blq-ms-cb-mlp:checked')).map(function(e){{return e.value;}}));
  var clsSel = new Set([].slice.call(document.querySelectorAll('.blq-ms-cb-cls:checked')).map(function(e){{return e.value;}}));

  var rows = BLQ_DATA.filter(function(d) {{
    if(de||ate){{ var ok=d.meses.some(function(m){{return(!de||m>=de)&&(!ate||m<=ate);}});if(!ok)return false; }}
    if(mlpSel.size && !mlpSel.has(d.mlp||'')) return false;
    if(clsSel.size && !clsSel.has(d.classe))  return false;
    if(stF && blqGetSt2(d.id) !== stF) return false;
    if(minPct>0 && d.pct<minPct)              return false;
    if(busca && d.id.indexOf(busca)<0)        return false;
    return true;
  }});

  rows.sort(function(a,b){{return _blqDir*(a[_blqSort]-b[_blqSort]);}});

  // Atualiza KPIs dinamicamente
  var filtBpp   = rows.reduce(function(s,d){{return s+d.bpp;}},0);
  var filtFraud = rows.reduce(function(s,d){{return s+d.fraud;}},0);
  var kT=document.getElementById('blq-k-total');
  if(kT) kT.textContent = rows.length;
  var kB=document.getElementById('blq-k-bpp');
  if(kB) kB.textContent = 'US$ '+filtBpp.toLocaleString('pt-BR',{{minimumFractionDigits:0,maximumFractionDigits:0}});
  var kF=document.getElementById('blq-k-fraud');
  if(kF) kF.textContent = filtFraud.toLocaleString('pt-BR');

  var inv = rows.filter(function(d){{return blqGetSt(d.id)==='inv';}}).length;
  var blk = rows.filter(function(d){{return blqGetSt2(d.id)==='blq';}}).length;
  var kBL = document.getElementById('blq-k-blq');
  if(kBL) kBL.textContent = blk;
  var ct  = document.getElementById('blq-tbl-ct');
  if(ct) ct.textContent = rows.length+' drivers · '+inv+' monitorando · '+blk+' bloqueados';

  // Atualiza gráficos se já foram construídos
  if(_blqCMlp || _blqCTop) {{
    var mlpCnt = {{}};
    rows.forEach(function(d){{ var k=d.mlp||'Sem transportadora'; mlpCnt[k]=(mlpCnt[k]||0)+1; }});
    var mlpArr = Object.keys(mlpCnt).map(function(k){{return [k,mlpCnt[k]];}}).sort(function(a,b){{return b[1]-a[1];}}).slice(0,8);
    if(_blqCMlp){{
      _blqCMlp.data.labels = mlpArr.map(function(x){{return x[0].substring(0,22);}});
      _blqCMlp.data.datasets[0].data = mlpArr.map(function(x){{return x[1];}});
      _blqCMlp.update();
    }}
    var topArr = rows.slice(0).sort(function(a,b){{return b.bpp-a.bpp;}}).slice(0,10);
    if(_blqCTop){{
      _blqCTop.data.labels = topArr.map(function(d){{return d.id;}});
      _blqCTop.data.datasets[0].data = topArr.map(function(d){{return Math.round(d.bpp*100)/100;}});
      _blqCTop.update();
    }}
  }}

  // Atualizar overview por status (todos os drivers, não filtrados)
  var _ov = {{ati:0,blq:0,ina:0}};
  BLQ_DATA.forEach(function(d){{ var s=blqGetSt2(d.id); if(_ov[s]!==undefined)_ov[s]++; else _ov.ati++; }});
  ['ati','blq','ina'].forEach(function(k){{
    var el=document.getElementById('blq-ov-'+k); if(el) el.textContent=_ov[k];
  }});
  var avgPct = rows.length ? Math.round((rows.reduce(function(s,d){{return s+d.pct;}},0)/rows.length)*10)/10 : 0;
  var gv = document.getElementById('blq-gauge-v'); if(gv) gv.textContent = avgPct+'%';
  if(_blqGauge || _blqDonut) {{
    blqBuildGaugeDonut(avgPct, _ov);
  }}

  // Passagem: cada driver pertence a exatamente 1 das 3 secoes (mon/inv/blq), sem repeticao
  var concl = rows.filter(function(d){{return blqGetSt(d.id)==='blq';}});
  var acomp = rows.filter(function(d){{return blqGetSt(d.id)==='inv';}});
  var fila  = rows.filter(function(d){{return blqGetSt(d.id)==='mon';}});
  _blqRenderSection('concl', concl, 'driver(s) finalizados');
  _blqRenderSection('acomp', acomp, 'driver(s) com trativa em andamento');
  _blqRenderSection('fila',  fila,  'driver(s) ainda sem trativa iniciada');
}}

var _ST_LBL  = {{mon:'Monitorando',inv:'Em investigação',blq:'Concluído'}};
var _ST_CLS  = {{mon:'blq-s-mon',inv:'blq-s-inv',blq:'blq-s-ati'}};
var _ST2_LBL = {{ati:'Ativo',blq:'Bloqueado',ina:'Inativo'}};
var _ST2_CLS = {{ati:'blq-s-ati',blq:'blq-s-blq',ina:'blq-s-ina'}};

function _blqRowHtml(d,i){{
  var st      = blqGetSt(d.id);
  var st2     = blqGetSt2(d.id);
  var pctCol  = d.pct>=50?'#f87171':d.pct>=20?'#fbbf24':'#6b7280';
  var pctW    = d.pct>=50?'700':'400';
  var _uniqMs = d.meses.map(function(m){{return m.slice(0,7);}}).filter(function(v,i,a){{return a.indexOf(v)===i;}});
  var mLbl    = _uniqMs.length
    ? _uniqMs.slice(-3).map(function(m){{
        try{{var dt=new Date(m+'-15');return dt.toLocaleDateString('pt-BR',{{month:'short',year:'2-digit'}}).replace('. ','/');}}catch(e){{return m;}}
      }}).join(' · ')+(_uniqMs.length>3?' +':'')
    : '—';
  var novoBadge = d.is_new ? '<span class="blq-novo">NOVO</span>' : '';

  var mainRow =
    '<tr class="blq-dr-row">'+
    '<td style="color:#374151;font-size:11px">'+(i+1)+'</td>'+
    '<td style="color:#f87171;font-weight:700">US$ '+d.bpp.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</td>'+
    '<td><button class="blq-did-btn" onclick="blqToggleShps(\\''+d.id+'\\')">'+d.id+novoBadge+
      '<span class="blq-chv" id="blq-chv-'+d.id+'">&#9660;</span></button></td>'+
    '<td class="blq-mlp" title="'+(d.mlp||'')+'">'+blqMlpLabel(d)+'</td>'+
    '<td style="color:#f87171">'+d.fraud+'</td>'+
    '<td style="color:'+pctCol+';font-weight:'+pctW+'">'+d.pct.toFixed(1)+'%</td>'+
    '<td>'+d.total+'</td>'+
    '<td><span class="blq-tag" title="'+(d.classe||'')+'">'+(d.classe||'—')+'</span></td>'+
    '<td style="font-size:10px;color:#6b7280">'+mLbl+'</td>'+
    '<td><span class="blq-badge '+_ST_CLS[st]+'" onclick="blqNextSt(\\''+d.id+'\\')">'+_ST_LBL[st]+'</span></td>'+
    '<td><span class="blq-badge '+_ST2_CLS[st2]+'" onclick="blqToggleSt2(\\''+d.id+'\\')">'+_ST2_LBL[st2]+'</span></td>'+
    '<td><button class="blq-btn-pdf" onclick="blqGerarApresentacao(\\''+d.id+'\\')">&#9998; PDF</button></td>'+
    '</tr>';

  var shps = d.shps || [];
  var shpRow = '';
  if(shps.length){{
    var chips = shps.map(function(s){{
      return '<a href="'+BLQ_LOG+s.id+'" target="_blank" class="blq-chip">'+s.id+'</a>';
    }}).join('');
    var extra = d.total>shps.length ? ' <span style="color:#374151"> · +'+(d.total-shps.length)+' nao exibidos</span>' : '';
    shpRow =
      '<tr class="blq-shp-row" id="blq-shps-'+d.id+'" style="display:none">'+
      '<td colspan="12"><div class="blq-shp-meta">'+
        '<a href="'+BLQ_DRV+d.id+'" target="_blank" class="blq-drv-link">&#8599; Ver driver no backoffice</a>'+
        '<span style="font-size:10px;color:#4b5563">'+shps.length+' IDs (top BPP)'+extra+'</span>'+
      '</div><div class="blq-shp-list">'+chips+'</div></td></tr>';
  }}
  return mainRow + shpRow;
}}

function _blqRenderSection(key, list, sufixo){{
  var cnt = document.getElementById('blq-'+key+'-count');
  if(cnt) cnt.textContent = '- '+list.length.toLocaleString('pt-BR')+' '+sufixo;
  var body = document.getElementById('blq-'+key+'-tbody');
  if(!body) return;
  body.innerHTML = list.length
    ? list.map(_blqRowHtml).join('')
    : '<tr><td colspan="12" style="text-align:center;padding:24px;color:#374151">Nenhum driver aqui com os filtros atuais.</td></tr>';
}}

window.blqToggleShps = function(id) {{
  var row = document.getElementById('blq-shps-'+id);
  var chv = document.getElementById('blq-chv-'+id);
  if(!row) return;
  var open = row.style.display!=='none';
  row.style.display = open ? 'none' : 'table-row';
  if(chv) chv.className = open ? 'blq-chv' : 'blq-chv open';
}};
window.BLQ_DATA     = BLQ_DATA;
window.blqNextSt    = blqNextSt;
window.blqGetSt2    = blqGetSt2;
window.blqToggleSt2 = blqToggleSt2;
window.blqSortBy    = blqSortBy;
window.blqMsChg     = blqMsChg;
window.blqToggleMs  = blqToggleMs;
window.blqMsAll     = blqMsAll;
window.blqMsNone    = blqMsNone;
window.blqResetF    = function() {{
  ['blq-status'].forEach(function(id){{ var e=document.getElementById(id);if(e)e.value=''; }});
  var b=document.getElementById('blq-busca');if(b)b.value='';
  var p=document.getElementById('blq-pct');if(p)p.value='0';
  blqMsNone('mlp'); blqMsNone('cls');
  blqRender();
}};
window.blqResetStatus = function() {{
  Object.keys(localStorage).filter(function(k){{return k.startsWith('blq_s2_');}}).forEach(function(k){{localStorage.removeItem(k);}});
  blqRender();
}};
window.blqExportCSV = function() {{
  var rows = [['Driver ID','Nome','Transportadora','Total','Fraud','% Fraude','BPP USD','Classificacao','Status']];
  BLQ_DATA.forEach(function(d) {{
    rows.push([d.id,d.nome,d.mlp,d.total,d.fraud,d.pct,d.bpp,d.classe,blqGetSt(d.id)]);
  }});
  var csv = rows.map(function(r){{
    return r.map(function(v){{return '"'+String(v).replace(/"/g,'""')+'"';}}).join(',');
  }}).join('\\n');
  var a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,﻿' + encodeURIComponent(csv);
  a.download = 'bloqueios_ssp30.csv';
  a.click();
}};
window.blqGerarApresentacao = function(drvId) {{
  var d = BLQ_DATA.find(function(x){{ return x.id===drvId; }});
  if(!d) return;
  var hoje = new Date().toLocaleDateString('pt-BR',{{day:'2-digit',month:'2-digit',year:'numeric'}});
  var isBloq = blqGetSt2(drvId)==='blq';
  var st2 = blqGetSt2(drvId);
  var ST2_LBL_MAP = {{ati:'Ativo',blq:'Bloqueado',ina:'Inativo'}};
  var driverStatusLbl = ST2_LBL_MAP[st2] || 'Ativo';
  var fmtM = function(m){{
    try{{
      var s = m.length===7 ? m+'-01' : m.slice(0,10);
      var dt = new Date(s+'T12:00:00');
      return dt.toLocaleDateString('pt-BR',{{month:'short',year:'2-digit'}}).replace('.','').replace(' ','/');
    }}catch(e){{ return m; }}
  }};
  var _uniqMonthsAll = d.meses.map(function(m){{return m.slice(0,7);}}).filter(function(v,i,a){{return a.indexOf(v)===i;}}).sort();
  // Janela é de 90 dias (~3 meses), mas pode tocar 4 meses-calendário na borda (ex: 25/mai a 23/ago).
  // Nunca exibir mais de 3 meses no período — evita dar pretexto para negar o bloqueio.
  var _uniqMonths = _uniqMonthsAll.slice(-3);
  var nM = _uniqMonths.length;
  var periodoLabel = nM ? nM+' mes'+(nM>1?'es':'') : '—';
  var periodoRange = nM ? fmtM(_uniqMonths[0])+(nM>1?' a '+fmtM(_uniqMonths[nM-1]):'') : '';
  var shps = d.shps||[];
  var clsSet={{}};
  shps.forEach(function(s){{ if(s.cls) clsSet[s.cls]=1; }});
  var clsKeys=Object.keys(clsSet);
  var hasLOR=clsKeys.some(function(c){{ return c==='LOST ON ROUTE'; }});
  var hasFr=clsKeys.some(function(c){{ return c!=='LOST ON ROUTE'&&c!=='STOLEN ON ROUTE'; }});
  var tipo;
  if(hasLOR&&hasFr) tipo='LOR + FRAUDE';
  else if(hasLOR) tipo='LOR (Lost on Route)';
  else if(d.classe==='PNR C') tipo='PNR (Pendência NR)';
  else if(d.classe==='EMPTY BOX') tipo='Fraude — Caixa Vazia';
  else if(d.classe==='STOLEN ON ROUTE') tipo='Furto em Rota';
  else tipo=d.classe||'Fraude';
  var bppFmt=function(v){{ return '$ '+v.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}}); }};
  var bppSemMaior=shps.length>0 ? d.bpp-(shps[0].bpp||0) : d.bpp;
  var clsClr=function(c){{
    if(!c) return '#374151';
    if(c.indexOf('FRAUD')>=0||c==='EMPTY BOX') return '#dc2626';
    if(c==='PNR C') return '#1d4ed8';
    if(c==='LOST ON ROUTE') return '#c2410c';
    if(c==='STOLEN ON ROUTE') return '#6d28d9';
    return '#374151';
  }};
  var top=shps;
  var shpRows=top.map(function(s,i){{
    var cls=s.cls||d.classe||'';
    return '<tr class="'+(i%2?'alt':'')+'">'
      +'<td class="cn">'+(i+1)+'</td>'
      +'<td style="color:#555;font-size:9.5px;white-space:nowrap">'+(s.week||'—')+'</td>'
      +'<td style="color:'+clsClr(cls)+'">'+cls+'</td>'
      +'<td><a href="'+BLQ_LOG+s.id+'" style="color:#1d4ed8;text-decoration:none">'+s.id+'</a></td>'
      +'<td style="color:#555;font-size:9.5px;white-space:nowrap">'+(s.date||'—')+'</td>'
      +'<td style="color:#555">'+d.id+'</td>'
      +'<td style="color:#555;font-size:9px;max-width:90px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+(d.mlp||'')+'">'+(d.mlp||'—')+'</td>'
      +'<td style="color:#555">'+driverStatusLbl+'</td>'
      +'<td class="rn">'+bppFmt(s.bpp||0)+'</td></tr>';
  }}).join('');
  var stolen=d.fraud-shps.length; var extraShps=stolen>0?stolen+' STOLEN ON ROUTE não listados (sem BPP)':'';
  var apoLbl=isBloq?'JÁ BLOQUEADO':'APTO PARA BLOQUEIO';
  var css=[
    '*{{box-sizing:border-box;margin:0;padding:0}}',
    'body{{font-family:Arial,sans-serif;background:#fff;color:#111;font-size:12px}}',
    '.page{{width:210mm;min-height:297mm;padding:16mm 20mm;margin:0 auto;display:flex;flex-direction:column;page-break-after:always}}',
    '.page:last-child{{page-break-after:auto}}',
    '.hdr{{background:#FFE600;padding:9px 16px;display:flex;align-items:center;justify-content:space-between;border-radius:5px;margin-bottom:18px}}',
    '.hdr-l{{font-weight:900;font-size:12px;letter-spacing:1px;color:#111}}',
    '.hdr-r{{font-size:9px;color:#555}}',
    '.ttl{{text-align:center;margin-bottom:16px}}',
    '.ttl h1{{font-size:20px;font-weight:900;letter-spacing:2px;text-transform:uppercase;color:#111}}',
    '.ttl h2{{font-size:10px;color:#555;font-weight:400;text-transform:uppercase;letter-spacing:1px;margin-top:3px}}',
    '.cid{{background:#111;color:#FFE600;font-size:34px;font-weight:900;text-align:center;padding:10px;border-radius:6px;letter-spacing:3px;margin-bottom:16px}}',
    '.igrid{{display:grid;grid-template-columns:1fr 1fr;gap:0;border:1px solid #ddd;border-radius:6px;overflow:hidden;margin-bottom:14px}}',
    '.ii{{padding:9px 12px;border-bottom:1px solid #eee}}',
    '.ii:nth-child(odd){{border-right:1px solid #eee}}',
    '.ilbl{{font-size:7.5px;text-transform:uppercase;letter-spacing:0.8px;color:#999;font-weight:700}}',
    '.ival{{font-size:13px;font-weight:700;color:#111;margin-top:2px}}',
    '.ival.red{{color:#dc2626}}',
    '.conc{{background:#f0fdf4;border:1.5px solid #16a34a;border-radius:5px;padding:12px 16px;margin-top:auto}}',
    '.conc .ct{{font-size:12px;font-weight:900;color:#15803d;margin-bottom:3px}}',
    '.conc .cs{{font-size:10px;color:#166534}}',
    '.fconf{{font-size:8.5px;color:#bbb;text-align:center;margin-top:12px;border-top:1px solid #eee;padding-top:7px}}',
    '.evh h2{{font-size:15px;font-weight:900;color:#111}}',
    '.evh .sub{{font-size:10px;color:#555;margin-top:2px;margin-bottom:10px}}',
    '.etbl{{width:100%;border-collapse:collapse;margin-bottom:8px}}',
    '.etbl th{{background:#111;color:#FFE600;font-size:8.5px;text-transform:uppercase;letter-spacing:0.7px;padding:6px 9px;text-align:left}}',
    '.etbl td{{padding:5px 9px;border-bottom:1px solid #eee;font-size:10.5px}}',
    '.etbl tr.alt td{{background:#f9fafb}}',
    '.etbl td.cn{{text-align:center;color:#aaa;width:28px}}',
    '.etbl td.rn{{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}}',
    '.efoot{{font-size:9.5px;color:#555;background:#f9fafb;border:1px solid #eee;border-radius:4px;padding:7px 10px;margin-top:4px}}',
    '.bkpg{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}}',
    '.bkpg .lp{{font-size:14px;font-weight:900;letter-spacing:3px;text-transform:uppercase}}',
    '.bkpg .bar{{background:#FFE600;width:50px;height:3px;margin:10px auto}}',
    '.bkpg .un{{font-size:10px;color:#555;margin-top:6px}}',
    '.bkpg .gn{{font-size:9px;color:#999;margin-top:18px;border-top:1px solid #eee;padding-top:12px;width:100%}}',
    '.pbtn{{position:fixed;bottom:18px;right:18px;background:#111;color:#FFE600;border:none;padding:9px 18px;border-radius:5px;font-size:12px;font-weight:700;cursor:pointer;letter-spacing:0.5px;z-index:99;box-shadow:0 4px 12px rgba(0,0,0,.3)}}',
    '.pbtn:hover{{background:#333}}',
    '.editavel{{border-bottom:1.5px dashed #bbb;min-width:150px;display:inline-block;color:#999;font-style:italic;cursor:text;padding:0 2px}}',
    '.editavel:focus{{outline:none;border-color:#3b82f6;color:#111;font-style:normal}}',
    '.editavel:not(:empty){{color:#111;font-style:normal}}',
    '.edit-hint{{font-size:8px;color:#f59e0b;display:block;margin-top:2px}}',
    '@media print{{.pbtn{{display:none}}.page{{width:100%;margin:0}}.edit-hint{{display:none}}.editavel{{border:none;color:#111;font-style:normal}}}}'
  ].join('');
  var pg1='<div class="page">'+
    '<div class="hdr"><div class="hdr-l">LOSS PREVENTION</div><div class="hdr-r">SSP30 · Guarulhos Mega · Mercado Livre</div></div>'+
    '<div class="ttl"><h1>Solicitação de Bloqueio</h1><h2>Painel Operacional do Caso</h2></div>'+
    '<div class="cid"># '+d.id+'</div>'+
    '<div class="igrid">'+
      '<div class="ii"><div class="ilbl">Driver</div><div class="ival">'+(d.nome||'<span class="editavel" contenteditable="true" title="Clique para editar"></span><span class="edit-hint">⚠ Nome não encontrado — clique para preencher</span>')+'</div></div>'+
      '<div class="ii"><div class="ilbl">Transportadora</div><div class="ival">'+blqMlpLabel(d)+'</div></div>'+
      '<div class="ii"><div class="ilbl">Placa</div><div class="ival">'+(d.placa||'—')+'</div></div>'+
      '<div class="ii"><div class="ilbl">Tipo de Ocorrência</div><div class="ival">'+tipo+'</div></div>'+
      '<div class="ii"><div class="ilbl">Período de Acúmulo</div><div class="ival">'+periodoLabel+(periodoRange?' · '+periodoRange:'')+'</div></div>'+
      '<div class="ii"><div class="ilbl">Data da Solicitação</div><div class="ival">'+hoje+'</div></div>'+
      '<div class="ii"><div class="ilbl">Unidade Emissora</div><div class="ival">Guarulhos Mega</div></div>'+
      '<div class="ii"><div class="ilbl">Pacotes Fraud / Lost</div><div class="ival red">'+d.fraud+' pacotes</div></div>'+
      '<div class="ii"><div class="ilbl">BPP Total Acumulado</div><div class="ival red">'+bppFmt(d.bpp)+'</div></div>'+
    '</div>'+
    '<div class="conc">'+
      '<div class="ct">✓ Conclusão: '+apoLbl+'</div>'+
      '<div class="cs">O caso atinge todos os critérios operacionais exigidos pela política interna de segurança.</div>'+
    '</div>'+
    '<div class="fconf">CONFIDENCIAL — Uso Interno — Loss Prevention SSP30</div>'+
  '</div>';
  var pg2='<div class="page">'+
    '<div class="hdr"><div class="hdr-l">LOSS PREVENTION</div><div class="hdr-r">Evidências · Driver '+d.id+'</div></div>'+
    '<div class="evh"><h2>Evidências — Driver '+d.id+'</h2><div class="sub">'+(d.nome?d.nome+' | ':'<span class="editavel" contenteditable="true" title="Clique para editar">Inserir nome</span> | ')+d.mlp+'</div></div>'+
    '<table class="etbl">'+
      '<thead><tr><th class="cn">#</th><th>Semana</th><th>Classificação</th><th>Shipment ID</th><th>Data BPP</th><th>Driver ID</th><th>MLP</th><th>Status</th><th class="rn">BPP (USD)</th></tr></thead>'+
      '<tbody>'+shpRows+'</tbody>'+
    '</table>'+
    '<div class="efoot">'+
      'Critério: <strong>'+d.fraud+' pacotes</strong> · BPP Total: <strong>'+bppFmt(d.bpp)+'</strong>'+
      (shps.length>0?' · BPP s/ maior: <strong>'+bppFmt(bppSemMaior)+'</strong>':'')+
      (extraShps?' &nbsp;|&nbsp; '+extraShps:'')+
      '&nbsp;|&nbsp; <strong>'+apoLbl+'</strong>'+
    '</div>'+
    '<div class="fconf">CONFIDENCIAL — Uso Interno — Loss Prevention SSP30</div>'+
  '</div>';
  var pg3='<div class="page">'+
    '<div class="bkpg">'+
      '<div class="lp">Loss Prevention</div>'+
      '<div class="bar"></div>'+
      '<div class="un">Mercado Livre · SSP30 · Guarulhos Mega</div>'+
      '<div class="gn">Gerado em '+hoje+' &nbsp;|&nbsp; '+BLQ_ANALISTA+'<br><br>CONFIDENCIAL — Uso Interno — Loss Prevention SSP30</div>'+
    '</div>'+
  '</div>';
  var full='<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><title>Bloqueio '+d.id+'</title>'+
    '<style>'+css+'</style></head><body>'+pg1+pg2+pg3+
    '<button class="pbtn" onclick="window.print()">&#128424; Imprimir / PDF</button>'+
    '</body></html>';
  var w=window.open('','_blank','width=1100,height=900,scrollbars=yes');
  if(!w){{ alert('Permita pop-ups para gerar a apresentação.'); return; }}
  w.document.write(full);
  w.document.close();
  w.focus();
}};
window.blqBuildCharts = blqBuildCharts;
window.blqRender      = blqRender;
window.blqSetPeriodChip = blqSetPeriodChip;
window.blqCustomPeriod  = blqCustomPeriod;

// Período da aba Bloqueios agora é independente da barra global do topo
// (controle próprio com atalhos 7d/30d/90d/Tudo — ver blqSetPeriodChip/blqCustomPeriod).

// Badge: set synchronously + DOMContentLoaded (belt-and-suspenders)
(function() {{ var b=document.getElementById('tab-count-bloqueios'); if(b) b.textContent=BLQ_DATA.length; }})();

document.addEventListener('DOMContentLoaded', function() {{
  var b2=document.getElementById('tab-count-bloqueios'); if(b2) b2.textContent=BLQ_DATA.length;
  try {{ blqRender(); }} catch(e) {{ console.error('blqRender init error:', e); }}
  setTimeout(function() {{
    // Re-seta badge (sobrescreve qualquer reset do JS principal)
    var b3=document.getElementById('tab-count-bloqueios'); if(b3) b3.textContent=BLQ_DATA.length;
    // Constrói charts se a aba já estiver visível (restaurada do localStorage sem onclick)
    var tab=document.getElementById('tab-bloqueios');
    if(tab && getComputedStyle(tab).display!=='none') {{
      try {{ blqBuildCharts(); }} catch(e) {{ console.error('blqBuildCharts init:', e); }}
    }}
  }}, 500);
}});
}})();
</script>
</div>"""


def inject_bloqueios_sidebar(html):
    if 'data-tab="bloqueios"' in html:
        return html
    old = '<div class="sb-item" data-tab="nodos"'
    new = (
        '<div class="sb-item" data-tab="bloqueios" onclick="showTab(\'bloqueios\',this);'
        'setTimeout(function(){if(window.blqBuildCharts)window.blqBuildCharts();'
        'if(window.blqRender)window.blqRender();},250)">\n'
        '      <i data-lucide="shield-x" width="14" height="14" class="ci"></i>\n'
        '      Drivers <span class="sb-badge" id="tab-count-bloqueios">0</span>\n'
        '    </div>\n'
        '    <div class="sb-item" data-tab="nodos"'
    )
    return html.replace(old, new, 1)


def find_and_replace_tab(content, tab_id, new_html):
    start_tag = f'<div id="{tab_id}" class="content">'
    start = content.find(start_tag)
    if start == -1:
        return content, False
    after = start + len(start_tag)
    # Usa '\n<' como prefixo para evitar falsos positivos dentro de <script>
    candidates = []
    for marker in ['\n<div id="tab-', '\n</main>', '\n</body>']:
        idx = content.find(marker, after)
        if idx != -1:
            candidates.append(idx + 1)  # +1: aponta para '<', não '\n'
    if not candidates:
        return content, False
    end = min(candidates)
    return content[:start] + new_html + '\n' + content[end:], True


def main():
    drivers, sheet_status = carregar_dados()

    print('Gerando HTML...')
    tab_html = gerar_tab(drivers, sheet_status)

    print('Lendo fraude.html...')
    html = HTML_OUT.read_text(encoding='utf-8')

    html = inject_bloqueios_sidebar(html)

    html, ok = find_and_replace_tab(html, 'tab-bloqueios', tab_html)
    if not ok:
        ins = html.rfind('</main>')
        if ins == -1:
            ins = html.rfind('</body>')
        if ins > 0:
            html = html[:ins] + tab_html + '\n' + html[ins:]
            ok = True
    print(f'  tab-bloqueios {"atualizada" if ok else "ERRO"}')

    m = re.search(r'<span class="ver-badge">v(\d+)\.(\d+)</span>', html)
    new_ver = f'v{m.group(1)}.{int(m.group(2))+1}' if m else 'v4.37'
    html = re.sub(
        r'<span class="ver-badge">v[\d.]+</span>',
        f'<span class="ver-badge">{new_ver}</span>',
        html, count=1
    )

    HTML_OUT.write_text(html, encoding='utf-8')
    mb = HTML_OUT.stat().st_size / 1024 / 1024
    _salvar_ids_conhecidos({str(d['id']) for d in drivers})
    print(f'Pronto! {mb:.1f} MB — {new_ver}')


if __name__ == '__main__':
    main()
