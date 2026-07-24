# ============================================================
# tests/test_risco.py  — Plano 90 dias: Semanas 5-6 (Pytest)
#
# Como rodar:  pytest tests/ -v
# Instalar:    pip install pytest
# ============================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from atualizacao_risco import (
    montar_linha_on_route,
    montar_linha_on_way,
    FACILITY,
)

# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture
def row_route():
    """Linha simulada de ON ROUTE vinda do BigQuery."""
    return {
        'SHP_SHIPMENT_ID':        '12345678901',
        'FACILITY':               'SSP30',
        'SHP_LG_STATUS':          'at_station',
        'SHP_LG_SUB_STATUS':      '',
        'SHP_LG_SHIPMENT_CHK_DT': '2026-07-24 08:00:00',
        'DIAS_VENCIMENTO_PROMESSA': '-3',
        'ULTIMO_SISTEMA':         'SSP30',
        'SHP_DESTINATION_ID':     'NEX_SP_01',
        'DATA_CANCELAMENTO':      '',
        'DATA_ULTIMA_MOVIMENTACAO': '2026-07-24 07:00:00',
        'SHP_STATUS_ID':          'shipped',
        'SHP_SUBSTATUS_ID':       'in_transit',
        'DATA_ATIVO_POC':         '',
        'DIAS_SEM_MOVIMENTACAO':  '2',
        'ORIGEN_FACILITY_ID':     'SSP30',
        'FACILITY_XD_OW':         '',
        'GMV_USD':                '150.00',
        'Situation':              'Possivel Lost',
    }

@pytest.fixture
def row_way():
    """Linha simulada de ON WAY vinda do BigQuery."""
    return {
        'SHP_SHIPMENT_ID':        '98765432109',
        'SHP_LG_STATUS':          'on_way',
        'SHP_LG_SUB_STATUS':      '',
        'SHP_LG_SHIPMENT_CHK_DT': '2026-07-20 10:00:00',
        'DIAS_VENCIMENTO_PROMESSA': '-5',
        'DATA_CANCELAMENTO':      '',
        'DIAS_CANCELAMENTO':      '',
        'SHP_STATUS_ID':          'shipped',
        'SHP_SUBSTATUS_ID':       '',
        'DIAS_ON_WAY':            '13',
        'CARRIER_NAME':           'JADLOG',
        'ROUTE_ID':               '100001',
        'VEHICLE_PLATE':          'ABC1D23',
        'FACILITY_XD_ANTERIOR':   'GRU2',
        'TMS_PACKINGLIST':        '55001',
        'FLAG_HU_RECEBIDA':       'false',
        'ULTIMO_SISTEMA_SVC':     'GRU2',
        'DATA_ULTIMA_SVC':        '2026-07-20 10:00:00',
        'GMV_USD':                '220.50',
        'Situation':              '>= 11 dias OW',
    }

# ============================================================
# TESTES — montar_linha_on_route
# ============================================================

class TestMontarLinhaOnRoute:
    def test_retorna_lista_23_colunas(self, row_route):
        linha = montar_linha_on_route(row_route)
        assert len(linha) == 23, f"Esperado 23 colunas, got {len(linha)}"

    def test_coluna_b_situation(self, row_route):
        linha = montar_linha_on_route(row_route)
        assert linha[1] == 'Possivel Lost'

    def test_coluna_c_shp_id(self, row_route):
        linha = montar_linha_on_route(row_route)
        assert linha[2] == '12345678901'

    def test_coluna_e_tramo_e_facility(self, row_route):
        linha = montar_linha_on_route(row_route)
        assert linha[4] == FACILITY      # E = TRAMO
        assert linha[5] == 'SSP30'      # F = FACILITY

    def test_coluna_w_gmv(self, row_route):
        linha = montar_linha_on_route(row_route)
        assert linha[22] == '150.00'

    def test_colunas_vazias_editaveis(self, row_route):
        """Colunas A, D, R, S devem vir vazias (preenchidas manualmente)."""
        linha = montar_linha_on_route(row_route)
        assert linha[0]  == ''  # A - Responsável
        assert linha[3]  == ''  # D - Ação
        assert linha[17] == ''  # R - Dias pedido cancelamento
        assert linha[18] == ''  # S - Retorno POC

    def test_valores_string(self, row_route):
        """Todos os valores devem ser strings (para o Sheets API)."""
        linha = montar_linha_on_route(row_route)
        assert all(isinstance(v, str) for v in linha)

# ============================================================
# TESTES — montar_linha_on_way
# ============================================================

class TestMontarLinhaOnWay:
    def test_retorna_lista_22_colunas(self, row_way):
        linha = montar_linha_on_way(row_way)
        assert len(linha) == 22, f"Esperado 22 colunas, got {len(linha)}"

    def test_coluna_b_situation(self, row_way):
        linha = montar_linha_on_way(row_way)
        assert linha[1] == '>= 11 dias OW'

    def test_coluna_m_dias_on_way(self, row_way):
        linha = montar_linha_on_way(row_way)
        assert linha[12] == '13'

    def test_coluna_v_gmv(self, row_way):
        linha = montar_linha_on_way(row_way)
        assert linha[21] == '220.50'

    def test_valores_string(self, row_way):
        linha = montar_linha_on_way(row_way)
        assert all(isinstance(v, str) for v in linha)

# ============================================================
# TESTES — constantes e configuração
# ============================================================

class TestConfiguracoes:
    def test_facility_e_ssp30(self):
        assert FACILITY == 'SSP30'

    def test_gmv_minimo_positivo(self):
        from atualizacao_risco import GMV_MINIMO_USD, GMV_MINIMO_PROCURAR_USD, GMV_MINIMO_OW_USD, GMV_ALERTA_USD
        assert GMV_MINIMO_USD > 0
        assert GMV_MINIMO_PROCURAR_USD >= GMV_MINIMO_USD
        assert GMV_ALERTA_USD >= GMV_MINIMO_USD
