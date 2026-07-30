#!/usr/bin/env python3
"""
On Way / On Route Local Server — SSP30 Loss Prevention
Serve index.html + endpoints /update e /mover_historico para ambas as abas.
"""

import os
import unicodedata
from flask import Flask, request, jsonify, send_file
import gspread
from google.auth import default
from _shared import _FINAL_MAP

app = Flask(__name__)

PLANILHA_ID       = '1rFcUXxl53WVQf_ASRx3mhlEvFoJevcaiwjMZY1vso5Y'
SINISTRO_SHEET_ID = '12-JUN1u4UfXBv0Mkeq9D3lsosG3e6OjbysMt4h7cpII'
SINISTRO_ABA      = 'Eventos SVC'
ABA_ON_WAY    = 'Tratativas Risco On Way (HV) - Lucas'
ABA_ON_ROUTE  = 'Tratativas Risco On Route (HV) - Lucas'
ABA_HISTORICO = 'Histórico'
ABA_DIARIO    = 'Diário de Bordo'

# Schedule semanal fixo: dia 1=Segunda...5=Sexta → [(hora_ini, hora_fim, atividade, tipo), ...]
DIARIO_SCHEDULE = {
    1: [
        ('08:00', '09:30', 'Investigação CFTV',    'Análise'),
        ('09:30', '10:30', 'Gemba APP / MLB Megas', 'Gemba'),
        ('10:30', '11:00', 'DDS / Touch Point',    'Reunião'),
        ('14:00', '14:30', 'Aduana Faltante',      'Análise'),
        ('14:30', '15:00', 'Ronda Virtual',        'Análise'),
        ('15:00', '15:30', 'Drivers Ofensores',    'Análise'),
        ('15:30', '17:00', 'Risco On Way / Route', 'Análise'),
    ],
    2: [
        ('08:00', '09:30', 'Investigação CFTV',    'Análise'),
        ('09:30', '10:30', 'Gemba APP / MLB Megas', 'Gemba'),
        ('13:00', '13:30', 'Weekly Stolen',        'Reunião'),
        ('14:00', '14:30', 'Aduana Faltante',      'Análise'),
        ('14:30', '15:00', 'Ronda Virtual',        'Análise'),
        ('15:30', '17:00', 'Risco On Way / Route', 'Análise'),
    ],
    3: [
        ('08:00', '09:30', 'Investigação CFTV',    'Análise'),
        ('09:30', '10:30', 'Gemba APP / MLB Megas', 'Gemba'),
        ('14:00', '14:30', 'Aduana Faltante',      'Análise'),
        ('14:30', '15:00', 'Ronda Virtual',        'Análise'),
        ('15:00', '15:30', 'Drivers Ofensores',    'Análise'),
        ('15:30', '17:00', 'Risco On Way / Route', 'Análise'),
    ],
    4: [
        ('08:00', '09:30', 'Investigação CFTV',    'Análise'),
        ('09:30', '10:30', 'Gemba APP / MLB Megas', 'Gemba'),
        ('14:00', '14:30', 'Aduana Faltante',      'Análise'),
        ('14:30', '15:00', 'Ronda Virtual',        'Análise'),
        ('15:00', '15:30', 'Drivers Ofensores',    'Análise'),
        ('15:30', '17:00', 'Risco On Way / Route', 'Análise'),
    ],
    5: [
        ('08:00', '09:30', 'Investigação CFTV',    'Análise'),
        ('09:30', '10:30', 'Gemba APP / MLB Megas', 'Gemba'),
        ('14:00', '14:30', 'Aduana Faltante',      'Análise'),
        ('14:30', '15:00', 'Ronda Virtual',        'Análise'),
        ('15:30', '17:00', 'Risco On Way / Route', 'Análise'),
    ],
}

# _FINAL_MAP importado de _shared.py

# Índices 0-based das colunas editáveis por tab
_COLS = {
    'wy': {'acao': 22, 'link': 23, 'status': 28, 'final': 29, 'gmv': 21, 'label': 'ON WAY',   'aba': ABA_ON_WAY},
    'rt': {'acao': 23,              'status': 28, 'final': 29, 'gmv': 22, 'label': 'ON ROUTE', 'aba': ABA_ON_ROUTE},
}

def _norm(s):
    s = (s or '').strip().lower()
    return unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode('ascii')

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(BASE_DIR, 'index.html')

# Cache separado por tab: {'ws': worksheet, 'idx': {shp_id: row_number}, 'ts': timestamp}
_cache_wy = None
_cache_rt = None
_CACHE_TTL = 600  # 10 minutos — reler planilha se edição direta no Sheets

# Cache do Diário de Bordo: {'ws': ws, 'idx': {(data, atividade): row_number}}
_cache_diario     = None
_cache_diario_dia = None   # date string quando o cache foi construído

# ─── Cache BPP para auto-preenchimento de sinistros ───────────────
_bpp_rows   = []   # linhas de dados da aba BPP (sem cabeçalho)
_bpp_header = {}   # nome_coluna → índice
_bpp_loaded = False

def _parse_usd(val):
    """Converte '$1,234.56' ou '13' para float."""
    v = (val or '').strip().replace('$', '').replace(',', '')
    try:
        return float(v)
    except Exception:
        return 0.0

def _load_bpp_cache():
    global _bpp_rows, _bpp_header, _bpp_loaded
    try:
        creds, _ = default(scopes=['https://www.googleapis.com/auth/spreadsheets'])
        gc = gspread.authorize(creds)
        ws = gc.open_by_key(SINISTRO_SHEET_ID).worksheet('BPP')
        all_vals = ws.get_all_values()
        if all_vals:
            _bpp_header = {h.strip(): i for i, h in enumerate(all_vals[0])}
            _bpp_rows   = all_vals[1:]
            _bpp_loaded = True
            print(f'[BPP cache] {len(_bpp_rows)} linhas carregadas')
    except Exception as e:
        print(f'[BPP cache] Erro ao carregar: {e}')


def get_cache_diario():
    global _cache_diario, _cache_diario_dia
    from datetime import date
    hoje = str(date.today())
    if _cache_diario is not None and _cache_diario_dia == hoje:
        return _cache_diario
    creds, _ = default(scopes=['https://www.googleapis.com/auth/spreadsheets'])
    gc = gspread.authorize(creds)
    pl = gc.open_by_key(PLANILHA_ID)
    try:
        ws = pl.worksheet(ABA_DIARIO)
    except Exception:
        ws = pl.add_worksheet(title=ABA_DIARIO, rows=1000, cols=8)
        ws.update('A1:H1', [['Data', 'Hora_Inicio', 'Hora_Fim', 'Atividade', 'Tipo', 'Feito', 'Observacao', 'Extra']])
    rows = ws.get_all_values()
    idx  = {}
    for i, row in enumerate(rows[1:], start=2):
        while len(row) < 8:
            row.append('')
        if row[0].strip() and row[3].strip():
            idx[(row[0].strip(), row[3].strip())] = i
    _cache_diario     = {'ws': ws, 'idx': idx}
    _cache_diario_dia = hoje
    return _cache_diario


def invalidate_diario():
    global _cache_diario
    _cache_diario = None


def invalidate_cache(tab='wy'):
    global _cache_wy, _cache_rt
    if tab == 'rt': _cache_rt = None
    else:           _cache_wy = None


def get_cache(tab='wy'):
    import time
    global _cache_wy, _cache_rt
    cache = _cache_rt if tab == 'rt' else _cache_wy
    if cache is None or (time.time() - cache.get('ts', 0)) > _CACHE_TTL:
        aba  = _COLS[tab]['aba']
        creds, _ = default(scopes=['https://www.googleapis.com/auth/spreadsheets'])
        gc   = gspread.authorize(creds)
        ws   = gc.open_by_key(PLANILHA_ID).worksheet(aba)
        rows = ws.get_all_values()
        idx  = {}
        for i, row in enumerate(rows[1:], start=2):
            if len(row) > 2 and row[2].strip():
                idx[row[2].strip()] = i
        cache = {'ws': ws, 'idx': idx, 'rows': rows, 'ts': time.time()}
        if tab == 'rt': _cache_rt = cache
        else:           _cache_wy = cache
    return cache


@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin']  = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Private-Network'] = 'true'
    return response


@app.route('/')
def serve_index():
    resp = send_file(INDEX_HTML)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/<string:filename>')
def serve_static(filename):
    # <string:> não captura barras — paths de API (sinistros/*, diario/*) ficam livres
    target = os.path.join(BASE_DIR, filename)
    file_path = target if os.path.isfile(target) else INDEX_HTML
    resp = send_file(file_path)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@app.route('/ping')
def ping():
    return jsonify({'ok': True})


@app.route('/restart', methods=['POST', 'OPTIONS'])
def restart_server():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})
    import subprocess, sys, threading, time, os
    def do_restart():
        time.sleep(0.4)
        subprocess.Popen([sys.executable, os.path.abspath(__file__)])
        sys.stdout.flush()
        os._exit(0)
    threading.Thread(target=do_restart, daemon=True).start()
    return jsonify({'ok': True})


@app.route('/ow_values')
def ow_values():
    """Retorna valores editáveis indexados por SHP_ID. ?tab=wy (padrão) ou ?tab=rt."""
    tab = request.args.get('tab', 'wy')
    if tab not in _COLS:
        return jsonify({'error': 'tab inválido'}), 400
    try:
        cached = get_cache(tab)
        rows   = cached['rows']
        c      = _COLS[tab]
        result = {}
        for row in rows[1:]:
            while len(row) < 30:
                row.append('')
            shp_id = row[2].strip()
            if not shp_id:
                continue
            entry = {
                'acao':   row[c['acao']],
                'status': row[c['status']],
                'final':  row[c['final']],
            }
            if tab == 'wy':
                entry['link'] = row[c['link']]
            result[shp_id] = entry
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/update', methods=['POST', 'OPTIONS'])
def update():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})
    data   = request.json
    shp_id = str(data.get('shp_id', '')).strip()
    tab    = str(data.get('tab', 'wy'))
    col    = int(data['col'])
    val    = str(data.get('value', ''))
    if tab not in _COLS:
        return jsonify({'ok': False, 'error': 'tab inválido'}), 400
    try:
        c   = get_cache(tab)
        row = c['idx'].get(shp_id)
        if row is None:
            return jsonify({'ok': False, 'error': f'SHP {shp_id} não encontrado'}), 404
        c['ws'].update_cell(row, col, val)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/mover_historico', methods=['POST', 'OPTIONS'])
def mover_historico():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})
    data   = request.json
    shp_id = str(data.get('shp_id', '')).strip()
    tab    = str(data.get('tab', 'wy'))
    hoje   = str(data.get('hoje', ''))
    if tab not in _COLS:
        return jsonify({'ok': False, 'error': 'tab inválido'}), 400
    try:
        c       = get_cache(tab)
        ws      = c['ws']
        row_idx = c['idx'].get(shp_id)
        if row_idx is None:
            return jsonify({'ok': False, 'error': f'SHP {shp_id} não encontrado'}), 404
        rows = ws.get_all_values()
        r    = list(rows[row_idx - 1])
        while len(r) < 35:
            r.append('')
        if _norm(r[28]) != 'concluido' or not r[29].strip():
            return jsonify({'ok': False, 'error': 'Condições não atendidas'}), 400
        cols       = _COLS[tab]
        final_hist = _FINAL_MAP.get(_norm(r[29].strip()), r[29].strip())
        creds, _   = default(scopes=['https://www.googleapis.com/auth/spreadsheets'])
        gc         = gspread.authorize(creds)
        pl         = gc.open_by_key(PLANILHA_ID)
        hist_ws    = pl.worksheet(ABA_HISTORICO)
        hist_ws.append_row([
            hoje, cols['label'],
            r[2]  if len(r) > 2  else '',
            r[1]  if len(r) > 1  else '',
            r[cols['gmv']] if len(r) > cols['gmv'] else '',
            r[0]  if len(r) > 0  else '',
            r[28] if len(r) > 28 else '',
            final_hist,
        ], value_input_option='RAW')
        pl.worksheet(cols['aba']).delete_rows(row_idx)
        invalidate_cache(tab)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/diario')
def diario_get():
    from datetime import date
    hoje     = date.today()
    hoje_str = str(hoje)
    weekday  = hoje.isoweekday()   # 1=segunda … 7=domingo
    schedule = DIARIO_SCHEDULE.get(weekday, [])
    try:
        cache    = get_cache_diario()
        all_rows = cache['ws'].get_all_values()
        log    = {}
        extras = []
        for row in all_rows[1:]:
            while len(row) < 8:
                row.append('')
            if row[0].strip() != hoje_str:
                continue
            atv = row[3].strip()
            if not atv:
                continue
            entry = {
                'hora_ini': row[1], 'hora_fim': row[2],
                'atividade': atv,   'tipo': row[4],
                'feito': row[5] == '1', 'obs': row[6],
                'extra': row[7] == '1',
            }
            if row[7] == '1':
                extras.append(entry)
            else:
                log[atv] = entry
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    items = []
    for hi, hf, atv, tipo in schedule:
        logged = log.get(atv, {})
        items.append({'hora_ini': hi, 'hora_fim': hf, 'atividade': atv, 'tipo': tipo,
                      'feito': logged.get('feito', False), 'obs': logged.get('obs', ''), 'extra': False})
    return jsonify({'data': hoje_str, 'dia_semana': weekday, 'items': items, 'extras': extras})


@app.route('/diario/toggle', methods=['POST', 'OPTIONS'])
def diario_toggle():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})
    data    = request.json
    hoje    = str(data.get('data', ''))
    atv     = str(data.get('atividade', '')).strip()
    hi      = str(data.get('hora_ini', ''))
    hf      = str(data.get('hora_fim', ''))
    tipo    = str(data.get('tipo', ''))
    feito   = bool(data.get('feito', True))
    try:
        cache   = get_cache_diario()
        key     = (hoje, atv)
        row_idx = cache['idx'].get(key)
        if row_idx:
            cache['ws'].update_cell(row_idx, 6, '1' if feito else '')
        else:
            cache['ws'].append_row([hoje, hi, hf, atv, tipo, '1' if feito else '', '', ''])
            invalidate_diario()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/diario/obs', methods=['POST', 'OPTIONS'])
def diario_obs():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})
    data    = request.json
    hoje    = str(data.get('data', ''))
    atv     = str(data.get('atividade', '')).strip()
    hi      = str(data.get('hora_ini', ''))
    hf      = str(data.get('hora_fim', ''))
    tipo    = str(data.get('tipo', ''))
    obs     = str(data.get('obs', ''))
    try:
        cache   = get_cache_diario()
        key     = (hoje, atv)
        row_idx = cache['idx'].get(key)
        if row_idx:
            cache['ws'].update_cell(row_idx, 7, obs)
        else:
            cache['ws'].append_row([hoje, hi, hf, atv, tipo, '', obs, ''])
            invalidate_diario()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/diario/extra', methods=['POST', 'OPTIONS'])
def diario_extra():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})
    data  = request.json
    hoje  = str(data.get('data', ''))
    atv   = str(data.get('atividade', '')).strip()
    hi    = str(data.get('hora_ini', ''))
    hf    = str(data.get('hora_fim', ''))
    obs   = str(data.get('obs', ''))
    feito = '1' if data.get('feito', False) else ''
    try:
        cache = get_cache_diario()
        cache['ws'].append_row([hoje, hi, hf, atv, 'Extra', feito, obs, '1'])
        invalidate_diario()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/diario/delete_extra', methods=['POST', 'OPTIONS'])
def diario_delete_extra():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})
    data    = request.json
    hoje    = str(data.get('data', ''))
    atv     = str(data.get('atividade', '')).strip()
    try:
        cache   = get_cache_diario()
        row_idx = cache['idx'].get((hoje, atv))
        if row_idx:
            cache['ws'].delete_rows(row_idx)
            invalidate_diario()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/sinistros/rota-info', methods=['POST', 'OPTIONS'])
def sinistros_rota_info():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})
    body = request.get_json(force=True) or {}
    rota_id = body.get('rota', '').strip()
    if not rota_id:
        return jsonify({'ok': False, 'error': 'rota obrigatório'})
    if not _bpp_loaded:
        _load_bpp_cache()
    if not _bpp_rows:
        return jsonify({'ok': False, 'error': 'BPP cache vazio — reinicie o servidor'})

    ri = _bpp_header.get('Rota', -1)
    bi = _bpp_header.get('Bpp Cashout Usd', -1)
    ci = _bpp_header.get('Causa BPP', -1)
    mi = _bpp_header.get('MLP', -1)

    if ri < 0 or bi < 0:
        return jsonify({'ok': False, 'error': 'Colunas Rota/Bpp não encontradas'})

    matching = [r for r in _bpp_rows if len(r) > ri and r[ri].strip() == rota_id]
    if not matching:
        return jsonify({'ok': True, 'found': 0, 'qtd_shp': 0, 'bpp_valor': 0.0, 'rota_total': 0.0, 'mlp': ''})

    stolen     = [r for r in matching if ci >= 0 and len(r) > ci and 'stolen' in r[ci].lower()]
    qtd_shp    = len(stolen)
    bpp_valor  = sum(_parse_usd(r[bi]) for r in stolen if len(r) > bi)
    rota_total = sum(_parse_usd(r[bi]) for r in matching if len(r) > bi)
    mlp        = (matching[0][mi] if mi >= 0 and len(matching[0]) > mi else '') or ''

    return jsonify({
        'ok':        True,
        'found':     len(matching),
        'qtd_shp':   qtd_shp,
        'bpp_valor':  round(bpp_valor,  2),
        'rota_total': round(rota_total, 2),
        'mlp':       mlp,
    })


@app.route('/sinistros/bpp-reload', methods=['POST', 'OPTIONS'])
def sinistros_bpp_reload():
    """Força recarga do cache BPP (útil após atualização da planilha)."""
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})
    global _bpp_loaded
    _bpp_loaded = False
    _load_bpp_cache()
    return jsonify({'ok': True, 'rows': len(_bpp_rows)})


def _col_a1(row_n, col_idx_0):
    """Converte (linha 1-based, coluna 0-based) para notação A1 ex: 'V522'."""
    n = col_idx_0 + 1
    col_str = ''
    while n > 0:
        n, r = divmod(n - 1, 26)
        col_str = chr(65 + r) + col_str
    return f'{col_str}{row_n}'


@app.route('/sinistros/fill-all', methods=['POST', 'OPTIONS'])
def sinistros_fill_all():
    """
    Preenche V (Qtde Shp), W (BPP Cashout USD) e X ($Rota) para todas as
    linhas de Eventos SVC que têm Rota mas estão sem esses valores.
    Parâmetro JSON opcional: { "force": true } para sobrescrever existentes.
    """
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})

    body  = request.get_json(force=True) or {}
    force = bool(body.get('force', False))

    if not _bpp_loaded:
        _load_bpp_cache()

    if not _bpp_rows:
        return jsonify({'ok': False, 'error': 'BPP cache vazio — rode /sinistros/bpp-reload primeiro'})

    try:
        creds, _ = default(scopes=['https://www.googleapis.com/auth/spreadsheets'])
        gc = gspread.authorize(creds)
        ws = gc.open_by_key(SINISTRO_SHEET_ID).worksheet(SINISTRO_ABA)
        all_rows = ws.get_all_values()
        headers  = all_rows[0]

        def col_idx(name):
            for i, h in enumerate(headers):
                if h.strip() == name:
                    return i
            return -1

        rota_col = col_idx('Rota')
        v_col    = col_idx('Qtde Shp')
        w_col    = col_idx('Bpp Cashout Usd')
        x_col    = col_idx('$Rota')

        if rota_col < 0:
            return jsonify({'ok': False, 'error': 'Coluna Rota não encontrada no cabeçalho'})

        ri = _bpp_header.get('Rota', -1)
        bi = _bpp_header.get('Bpp Cashout Usd', -1)
        ci = _bpp_header.get('Causa BPP', -1)

        batch_data = []
        updated = skipped = not_found = 0

        for row_num, row in enumerate(all_rows[1:], start=2):
            # Garante que a linha tem células suficientes para leitura
            max_col = max(c for c in [rota_col, v_col, w_col, x_col] if c >= 0)
            while len(row) <= max_col:
                row.append('')

            rota_id = row[rota_col].strip()
            if not rota_id:
                continue

            v_val = row[v_col].strip() if v_col >= 0 else ''
            w_val = row[w_col].strip() if w_col >= 0 else ''
            x_val = (row[x_col].strip() if x_col >= 0 and x_col < len(row) else '')

            if not force and v_val and w_val:
                skipped += 1
                continue

            # Lookup no cache BPP
            if ri < 0 or bi < 0:
                not_found += 1
                continue

            matching = [r for r in _bpp_rows if len(r) > ri and r[ri].strip() == rota_id]
            if not matching:
                not_found += 1
                continue

            stolen     = [r for r in matching if ci >= 0 and len(r) > ci and 'stolen' in r[ci].lower()]
            qtd_shp    = len(stolen)
            bpp_valor  = sum(_parse_usd(r[bi]) for r in stolen  if len(r) > bi)
            rota_total = sum(_parse_usd(r[bi]) for r in matching if len(r) > bi)

            if v_col >= 0 and (force or not v_val):
                batch_data.append({'range': _col_a1(row_num, v_col), 'values': [[str(qtd_shp)]]})
            if w_col >= 0 and (force or not w_val):
                batch_data.append({'range': _col_a1(row_num, w_col), 'values': [[str(round(bpp_valor, 2))]]})
            if x_col >= 0 and (force or not x_val):
                batch_data.append({'range': _col_a1(row_num, x_col), 'values': [[str(round(rota_total, 2))]]})

            updated += 1

        # Envia em lotes de 200 células para evitar rate-limit
        chunk = 200
        for i in range(0, len(batch_data), chunk):
            ws.batch_update(batch_data[i:i + chunk], value_input_option='USER_ENTERED')

        return jsonify({
            'ok':               True,
            'linhas_atualizadas': updated,
            'ja_preenchidas':   skipped,
            'sem_dados_bpp':    not_found,
            'celulas_escritas': len(batch_data),
        })

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/sinistros/bq-fill', methods=['POST', 'OPTIONS'])
def sinistros_bq_fill():
    """
    Preenche V (Qtde Shp), W (BPP Cashout USD) e X ($Rota) usando BigQuery
    para linhas que não têm dados no cache BPP local.
    Usa: BT_BPP_TRAMO_DETALHADA_ENRIQUECIDA (W, X) e BT_BASEROTAS_LASTMILE (V).
    Parâmetro JSON: { "force": false, "date_from": "2024-01-01" }
    """
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})

    body      = request.get_json(force=True) or {}
    force     = bool(body.get('force', False))
    date_from = body.get('date_from', '2024-01-01')

    try:
        from google.cloud import bigquery as bq_lib
    except ImportError:
        return jsonify({'ok': False, 'error': 'pip install google-cloud-bigquery'})

    try:
        creds, _ = default(scopes=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/bigquery.readonly',
            'https://www.googleapis.com/auth/cloud-platform',
        ])
        bq = bq_lib.Client(credentials=creds, project='meli-bi-data')
        gc = gspread.authorize(creds)
        ws = gc.open_by_key(SINISTRO_SHEET_ID).worksheet(SINISTRO_ABA)

        all_rows = ws.get_all_values()
        headers  = all_rows[0]

        def col_idx(name):
            for i, h in enumerate(headers):
                if h.strip() == name:
                    return i
            return -1

        rota_col = col_idx('Rota')
        v_col    = col_idx('Qtde Shp')
        w_col    = col_idx('Bpp Cashout Usd')
        x_col    = col_idx('$Rota')

        if rota_col < 0:
            return jsonify({'ok': False, 'error': 'Coluna Rota não encontrada'})

        # Coleta rotas que precisam de preenchimento
        rotas_pendentes = {}  # rota_id → [row_num, ...]
        for row_num, row in enumerate(all_rows[1:], start=2):
            max_col = max(c for c in [rota_col, v_col, w_col, x_col] if c >= 0)
            while len(row) <= max_col:
                row.append('')
            rota_id = row[rota_col].strip()
            if not rota_id:
                continue
            v_val = row[v_col].strip() if v_col >= 0 else ''
            w_val = row[w_col].strip() if w_col >= 0 else ''
            if not force and v_val and w_val:
                continue
            if rota_id not in rotas_pendentes:
                rotas_pendentes[rota_id] = []
            rotas_pendentes[rota_id].append((row_num, row))

        if not rotas_pendentes:
            return jsonify({'ok': True, 'msg': 'Nenhuma linha pendente', 'atualizadas': 0})

        # IDs numéricos para IN clause; filtra só os que são dígitos
        ids_numericos = [r for r in rotas_pendentes.keys() if r.isdigit()]
        if not ids_numericos:
            return jsonify({'ok': False, 'error': 'Nenhum Rota ID numérico encontrado'})
        ids_list = ', '.join(ids_numericos)

        # ── Query 1: BPP Cashout por rota (W e X) ────────────────────────
        q_bpp = f"""
        SELECT
          CAST(SHP_LG_ROUTE_ID AS STRING) AS rota_id,
          ROUND(SUM(CASE WHEN LOWER(CAUSA_BPP) LIKE '%stolen%'
                         THEN CAST(BPP_CASHOUT_USD AS FLOAT64) ELSE 0 END), 2) AS stolen_bpp,
          ROUND(SUM(CAST(BPP_CASHOUT_USD AS FLOAT64)), 2) AS total_bpp
        FROM `meli-bi-data.WHOWNER.BT_BPP_TRAMO_DETALHADA_ENRIQUECIDA`
        WHERE SHP_LG_ROUTE_ID IN ({ids_list})
          AND DATE_BPP >= '{date_from}'
        GROUP BY rota_id
        """
        bpp_data = {}
        for r in bq.query(q_bpp).result():
            bpp_data[r.rota_id] = {'stolen_bpp': float(r.stolen_bpp or 0),
                                   'total_bpp':  float(r.total_bpp  or 0)}

        # ── Query 2: Stolen count por rota (V) ────────────────────────────
        q_stolen = f"""
        SELECT
          CAST(SHP_LG_ROUTE_ID AS STRING) AS rota_id,
          SUM(CAST(STOLEN AS INT64))       AS stolen_count,
          SUM(CAST(QTDE_PACOTES AS INT64)) AS total_pacotes
        FROM `meli-bi-data.WHOWNER.BT_BASEROTAS_LASTMILE`
        WHERE SHP_LG_ROUTE_ID IN ({ids_list})
          AND DATA_FIM >= '{date_from}'
        GROUP BY rota_id
        """
        stolen_data = {}
        for r in bq.query(q_stolen).result():
            stolen_data[r.rota_id] = {'stolen_count':  int(r.stolen_count  or 0),
                                       'total_pacotes': int(r.total_pacotes or 0)}

        # ── Prepara batch update ──────────────────────────────────────────
        batch_data = []
        atualizadas = nao_encontradas = 0

        for rota_id, linhas in rotas_pendentes.items():
            bpp    = bpp_data.get(rota_id)
            stolen = stolen_data.get(rota_id)

            if not bpp and not stolen:
                nao_encontradas += len(linhas)
                continue

            for row_num, row in linhas:
                v_val = row[v_col].strip() if v_col >= 0 else ''
                w_val = row[w_col].strip() if w_col >= 0 else ''
                x_val = (row[x_col].strip() if x_col >= 0 and x_col < len(row) else '')

                # V: stolen count (só preenche se vazio)
                if v_col >= 0 and stolen and (force or not v_val):
                    batch_data.append({'range': _col_a1(row_num, v_col),
                                       'values': [[str(stolen['stolen_count'])]]})
                # W: BPP cashout stolen
                if w_col >= 0 and bpp and (force or not w_val):
                    batch_data.append({'range': _col_a1(row_num, w_col),
                                       'values': [[str(bpp['stolen_bpp'])]]})
                # X: BPP cashout total da rota
                if x_col >= 0 and bpp and (force or not x_val):
                    batch_data.append({'range': _col_a1(row_num, x_col),
                                       'values': [[str(bpp['total_bpp'])]]})
                atualizadas += 1

        # Batch write em chunks
        chunk = 200
        for i in range(0, len(batch_data), chunk):
            ws.batch_update(batch_data[i:i + chunk], value_input_option='USER_ENTERED')

        return jsonify({
            'ok':               True,
            'rotas_buscadas':   len(rotas_pendentes),
            'com_dados_bq':     len(bpp_data) + len(stolen_data),
            'linhas_atualizadas': atualizadas,
            'sem_dados_bq':     nao_encontradas,
            'celulas_escritas': len(batch_data),
        })

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/sinistros/add', methods=['POST', 'OPTIONS'])
def sinistros_add():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})
    try:
        payload = request.get_json(force=True)
        creds, _ = default(scopes=['https://www.googleapis.com/auth/spreadsheets'])
        gc = gspread.authorize(creds)
        ws = gc.open_by_key(SINISTRO_SHEET_ID).worksheet(SINISTRO_ABA)
        headers = ws.row_values(1)
        row = [''] * len(headers)
        def set_col(col_name, value):
            try:
                idx = next(i for i, h in enumerate(headers) if h.strip() == col_name)
                row[idx] = value
            except StopIteration:
                pass
        # Colunas conforme estrutura real da aba "Eventos SVC"
        set_col('Data',                 payload.get('data', ''))       # A: data
        set_col('Horario',              payload.get('horario', ''))    # B
        set_col('Rota',                 payload.get('rota', ''))       # F
        set_col('Drive',                payload.get('id_driver', ''))  # G
        set_col('Nome Drive',           payload.get('nome', ''))       # H
        set_col('Placa',                payload.get('placa', ''))      # M
        set_col('TIPO 2',               payload.get('tipo2', ''))      # O: Sinistro/Tentativa
        set_col('Qtde Shp',             payload.get('qtd_total', ''))   # V
        set_col('Bpp Cashout Usd',      payload.get('valor', ''))       # W
        set_col('$Rota',                payload.get('rota_total', ''))  # X
        set_col('Recup. da Carga?',     payload.get('recup_carga', '')) # Y
        set_col('Recup. Shp',           payload.get('qtd_rec', ''))     # Z
        set_col('Recup. Cashout Usd',   payload.get('recup_bpp', ''))   # AA
        set_col('Cidade',               payload.get('cidade', ''))       # AB
        set_col('Distrito',             payload.get('distrito', ''))     # AC
        set_col('Bairro',               payload.get('bairro', ''))       # AD
        set_col('CEP',                  payload.get('cep', ''))          # AE
        set_col('CLUSTER',              payload.get('cluster', ''))      # AF
        set_col('Rua',                  payload.get('local', ''))       # AH
        set_col('MLP',                  payload.get('transp', ''))      # AJ
        set_col('Veículo',              payload.get('veiculo', ''))     # AK
        set_col('Natureza do evento',   payload.get('natureza', ''))    # AR
        set_col('MODUS OPERANDI',       payload.get('modus', ''))       # AS
        set_col('Boletim de ocorrência',payload.get('boletim', ''))     # AU
        set_col('Link boletim',         payload.get('link_bo', ''))     # AV
        set_col('Relato',               payload.get('relato', ''))      # AW
        ws.append_row(row, value_input_option='USER_ENTERED')
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


if __name__ == '__main__':
    import sys
    log_path = os.path.join(BASE_DIR, 'server.log')
    try:
        sys.stdout = open(log_path, 'a', encoding='utf-8')
        sys.stderr = sys.stdout
    except Exception:
        pass
    print("=== On Way / On Route Server iniciando ===")
    print(f"Servindo: {INDEX_HTML}")
    try:
        app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
    except Exception as e:
        print(f"ERRO ao iniciar servidor: {e}")
