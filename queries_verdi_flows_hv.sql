-- ============================================================
-- QUERIES PARA VERDI FLOWS — Alarma HV SSP30
-- Baseado no template oficial TOOLKIT SPISUL (FLUXO_HV)
--
-- Alterações em relação ao template original (SSP45 → SSP30):
--   1. FACILITY: 'SSP45' → 'SSP30'
--   2. HORÁRIO: '00:30:00'/'10:00:00' → '11:00:00'/'13:00:00' (ciclo PM)
--   3. -240 mantido (offset interno da plataforma)
-- ============================================================


-- ==========================
-- SQL 1 — QUERY REPORTE PM
-- (Nodo: QUERY REPORTE PM)
-- Retorna mensagem formatada para o Google Chat
-- ==========================

WITH DistinctShipments AS (
  SELECT
    L.SHP_LG_FACILITY_ID,
    S.SHP_SHIPMENT_ID AS ID_ENVIO,
    S.SHP_ORDER_COST_USD AS VALOR_USD,
    ANY_VALUE(ITE.SHP_ITEM_DESC) AS DESCRICAO_ITEM,
    L.SHP_LG_LAST_UPDATED
  FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` AS S
  LEFT JOIN UNNEST(S.ITEMS) AS ITE
  INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS` AS L
    ON S.SHP_SHIPMENT_ID = L.SHP_SHIPMENT_ID
  WHERE S.SIT_SITE_ID = 'MLB'
    AND L.SHP_LG_FACILITY_ID IN ('SSP30')
    AND S.SHP_ORDER_COST_USD >= 350
    AND L.SHP_LG_SUB_STATUS = 'sorting'
    AND L.SHP_LG_LAST_UPDATED >= DATETIME(CURRENT_DATE('America/Sao_Paulo'), '11:00:00')
    AND L.SHP_LG_LAST_UPDATED <= DATETIME(CURRENT_DATE('America/Sao_Paulo'), '13:00:00')
  GROUP BY 1, 2, 3, 5
),
CalculatedData AS (
  SELECT
    *,
    (DATETIME_DIFF(CURRENT_DATETIME(), DATETIME(SHP_LG_LAST_UPDATED), MINUTE) - 240) AS MINUTOS_NA_FILA
  FROM DistinctShipments
)
SELECT
  CAST(COUNT(DISTINCT ID_ENVIO) AS INT64) AS total_itens,
  FORMAT("%'d", CAST(ROUND(SUM(VALOR_USD), 0) AS INT64)) AS valor_financeiro_total,
  STRING_AGG(
    CONCAT(
      IF(MINUTOS_NA_FILA > 60, '🔴 ', '🟡 '),
      'ID: ', ID_ENVIO,
      ' | USD ', FORMAT("%'d", CAST(ROUND(VALOR_USD, 0) AS INT64)),
      '\n   ┗ ⏳ Tempo: ',
      CAST(DIV(MINUTOS_NA_FILA, 60) AS STRING), ' horas e ',
      CAST(MOD(MINUTOS_NA_FILA, 60) AS STRING), ' minutos',
      ' | ', LEFT(DESCRICAO_ITEM, 25), '...'
    ),
    '\n\n' ORDER BY VALOR_USD DESC
  ) AS lista_formatada
FROM CalculatedData;


-- ==========================
-- SQL 2 — QUERY PLANILHA PM
-- (Nodo: QUERY PLANILHA PM)
-- Grava histórico no Google Sheets (aba raw)
-- ==========================

WITH DistinctShipments AS (
  SELECT
    L.SHP_LG_FACILITY_ID AS FACILITY_ID,
    S.SHP_SHIPMENT_ID     AS ID_ENVIO,
    S.SHP_ORDER_COST_USD  AS VALOR_USD,
    ANY_VALUE(ITE.SHP_ITEM_DESC) AS DESCRICAO_ITEM,
    L.SHP_LG_LAST_UPDATED AS LAST_UPDATED
  FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` AS S
  LEFT JOIN UNNEST(S.ITEMS) AS ITE
  INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS` AS L
    ON S.SHP_SHIPMENT_ID = L.SHP_SHIPMENT_ID
  WHERE S.SIT_SITE_ID = 'MLB'
    AND L.SHP_LG_FACILITY_ID IN ('SSP30')
    AND L.SHP_LG_SUB_STATUS = 'sorting'
    AND S.SHP_ORDER_COST_USD >= 350
    AND L.SHP_LG_LAST_UPDATED >= DATETIME(CURRENT_DATE('America/Sao_Paulo'), '11:00:00')
    AND L.SHP_LG_LAST_UPDATED <= DATETIME(CURRENT_DATE('America/Sao_Paulo'), '13:00:00')
  GROUP BY 1, 2, 3, 5
)
SELECT
  CURRENT_DATETIME('America/Sao_Paulo') AS cycle_ts,
  CURRENT_DATE('America/Sao_Paulo')     AS report_date,
  FACILITY_ID                           AS facility_id,
  ID_ENVIO                              AS id_envio,
  VALOR_USD                             AS valor_usd,
  DESCRICAO_ITEM                        AS descricao_item,
  LAST_UPDATED                          AS last_updated,
  'PM'                                  AS nome_ciclo
FROM DistinctShipments
ORDER BY VALOR_USD DESC;
