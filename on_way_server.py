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

_FINAL_MAP = {
    'reversao': 'Retornou ao fluxo',
    'reversão': 'Retornou ao fluxo',
    'bpp':      'Perdido',
}

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
        cache = {'ws': ws, 'idx': idx, 'ts': time.time()}
        if tab == 'rt': _cache_rt = cache
        else:           _cache_wy = cache
    return cache


@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin']  = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_static(path):
    target = os.path.join(BASE_DIR, path) if path else INDEX_HTML
    file_path = target if (path and os.path.isfile(target)) else INDEX_HTML
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
        ws   = get_cache(tab)['ws']
        rows = ws.get_all_values()
        c    = _COLS[tab]
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
        set_col('F',               payload.get('data', ''))
        set_col('Data',            payload.get('data', ''))
        set_col('Horario',         payload.get('horario', ''))
        set_col('Rota',            payload.get('rota', ''))
        set_col('Drive',           payload.get('id_driver', ''))
        set_col('Nome Drive',      payload.get('nome', ''))
        set_col('Placa',           payload.get('placa', ''))
        set_col('Qtde Shp',        payload.get('qtd_total', ''))
        set_col('Bpp Cashout Usd', payload.get('valor', ''))
        set_col('Recup. Shp',      payload.get('qtd_rec', ''))
        set_col('CEP',             payload.get('cep', ''))
        set_col('Rua',             payload.get('local', ''))
        set_col('MLP',             payload.get('transp', ''))
        set_col('Veículo',         payload.get('veiculo', ''))
        set_col('Bairro ',         payload.get('bairro', ''))
        set_col('Cidade ',         payload.get('cidade', ''))
        set_col('CLUSTER',         payload.get('cluster', ''))
        set_col('Natureza do evento', payload.get('natureza', ''))
        set_col('MODUS OPERANDI',  payload.get('modus', ''))
        set_col('Boletim de ocorrência', payload.get('boletim', ''))
        set_col('Link boletim',    payload.get('link_bo', ''))
        set_col('Relato',          payload.get('relato', ''))
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
