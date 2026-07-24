# ============================================================
# analise_fraude.py — Dashboard de Análise de Fraude SSP30
# Como rodar: duplo clique em abrir_analise_fraude.bat
# ============================================================

import json, webbrowser, os, pickle, hashlib, time
from datetime import datetime
from _diario_widget import diario_css, diario_nav_btn, diario_panel_html, diario_js
from google.cloud import bigquery
from google.auth import default
import gspread

FACILITY_NAME  = 'Guarulhos Mega'
ANO_INICIO     = '2026-01-01'

_SB_DRAG_JS = """
(function(){
var KEY='sb_order_'+(location.pathname.split('/').pop()||'idx');
var dragEl=null,sb=null;
function save(){
  var dc=0,order=Array.from(sb.children).map(function(el){
    if(el.classList.contains('sb-item'))return 'i:'+el.dataset.tab;
    if(el.classList.contains('sb-divider'))return 'd:'+(dc++);
    if(el.classList.contains('sb-section-header'))return 'h:'+el.textContent.trim();
    return null;
  }).filter(Boolean);
  try{localStorage.setItem(KEY,JSON.stringify(order));}catch(e){}
}
function restore(){
  try{
    var saved=JSON.parse(localStorage.getItem(KEY)||'null');
    if(!saved||!saved.length)return;
    var im={},hm={},da=[];
    Array.from(sb.children).forEach(function(el){
      if(el.classList.contains('sb-item'))im[el.dataset.tab]=el;
      else if(el.classList.contains('sb-section-header'))hm[el.textContent.trim()]=el;
      else if(el.classList.contains('sb-divider'))da.push(el);
    });
    var di=0;
    saved.forEach(function(e){
      var el=null;
      if(e.startsWith('i:'))el=im[e.slice(2)];
      else if(e.startsWith('h:'))el=hm[e.slice(2)];
      else if(e.startsWith('d:'))el=da[di++];
      if(el)sb.appendChild(el);
    });
  }catch(e){}
}
document.addEventListener('DOMContentLoaded',function(){
  sb=document.querySelector('.sidebar');
  if(!sb)return;
  restore();
  Array.from(sb.querySelectorAll('.sb-item')).forEach(function(el){
    el.setAttribute('draggable','true');
    var h=document.createElement('span');
    h.className='sb-drag-handle';h.textContent='⠿';
    el.insertBefore(h,el.firstChild);
  });
  sb.addEventListener('dragstart',function(e){
    var t=e.target.closest('.sb-item');
    if(!t)return;
    dragEl=t;setTimeout(function(){t.classList.add('sb-dragging');},0);
    e.dataTransfer.effectAllowed='move';
  });
  sb.addEventListener('dragend',function(){
    if(dragEl){dragEl.classList.remove('sb-dragging');dragEl=null;}
    sb.querySelectorAll('.sb-drop-before').forEach(function(el){el.classList.remove('sb-drop-before');});
    save();
  });
  sb.addEventListener('dragover',function(e){
    e.preventDefault();if(!dragEl)return;
    var t=e.target.closest('.sb-item');
    sb.querySelectorAll('.sb-drop-before').forEach(function(el){el.classList.remove('sb-drop-before');});
    if(t&&t!==dragEl){
      var r=t.getBoundingClientRect();
      if(e.clientY<r.top+r.height/2){sb.insertBefore(dragEl,t);t.classList.add('sb-drop-before');}
      else{sb.insertBefore(dragEl,t.nextSibling);}
    }
  });
  sb.addEventListener('drop',function(e){e.preventDefault();});
});
})();
"""
OUTPUT            = os.path.join(os.path.dirname(__file__), 'fraude.html')
SINISTROS_OUTPUT  = os.path.join(os.path.dirname(__file__), 'sinistros.html')
BLOCK_LIST_ID  = '1521Ek2wn8qYLj7g6dh0aBBMmpVYHjCp2hftGKNG9bO0'
ABA_BLOQUEIOS  = 'Drivers Bloqueados'
CFTV_SHEET_ID  = '18isURInofILBi-RS9YrCQyYcnb6JeU_stNqnspxiqLM'
CFTV_ABA          = 'Respostas ao formulário 2'
SINISTRO_SHEET_ID = '12-JUN1u4UfXBv0Mkeq9D3lsosG3e6OjbysMt4h7cpII'
SINISTRO_ABA      = 'Eventos SVC'

# ============================================================
# QUERIES
# ============================================================
QUERY_DRIVER_SCORE = f"""
-- Score combinado por driver — usa DRIVER_ID direto da tabela (sem join)
SELECT
    SAFE_CAST(DRIVER_ID AS STRING)                                           AS DRIVER_ID,
    COUNT(DISTINCT SHIPMENT_ID)                                              AS TOTAL_INCIDENTES,
    ROUND(SUM(BPP_CASHOUT_USD), 2)                                           AS TOTAL_BPP,
    COUNTIF(Classification_LM IN (
        'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
        'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE'))                     AS TOTAL_FRAUDE,
    COUNTIF(Classification_LM LIKE 'DAMAGED%')                              AS TOTAL_DAMAGED,
    COUNTIF(Classification_LM LIKE 'FRAUD%')                                AS FRAUD_CONFIRMADO
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND date_bpp >= '{ANO_INICIO}'
  AND date_bpp <= CURRENT_DATE()
  AND DRIVER_ID IS NOT NULL
GROUP BY 1
ORDER BY TOTAL_INCIDENTES DESC
LIMIT 60
"""

QUERY_DRIVER_SHIPMENTS = f"""
-- Todos os SHP IDs por driver para exibir no dashboard
SELECT
    SAFE_CAST(DRIVER_ID AS STRING)       AS DRIVER_ID,
    CAST(SHIPMENT_ID AS STRING)          AS SHP_ID,
    Classification_LM                    AS CLASSIFICACAO,
    ROUND(BPP_CASHOUT_USD, 2)            AS BPP,
    FORMAT_DATE('%d/%m/%Y', date_bpp)    AS DATA,
    FORMAT_DATE('%Y-W%V', date_bpp)      AS SEMANA
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND date_bpp >= '{ANO_INICIO}'
  AND date_bpp <= CURRENT_DATE()
  AND DRIVER_ID IS NOT NULL
ORDER BY SAFE_CAST(DRIVER_ID AS INT64), BPP_CASHOUT_USD DESC
"""

QUERY_DRIVER_PLACE = f"""
-- Driver x Place — usa DRIVER_ID direto (sem join com checkpoints)
WITH fraud_driver AS (
    SELECT
        SAFE_CAST(DRIVER_ID AS STRING)      AS DRIVER_ID,
        SAFE_CAST(SHIPMENT_ID AS STRING)    AS SHP_SHIPMENT_ID,
        Classification_LM,
        ROUND(BPP_CASHOUT_USD, 2)           AS BPP_CASHOUT_USD
    FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
    WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
      AND date_bpp >= '{ANO_INICIO}'
      AND date_bpp <= CURRENT_DATE()
      AND DRIVER_ID IS NOT NULL
)
SELECT
    fd.DRIVER_ID,
    p.SHP_AGENCY_ID,
    p.SHP_AGEN_DESC                                                              AS PLACE_NOME,
    COUNT(DISTINCT fd.SHP_SHIPMENT_ID)                                           AS INCIDENTES_EM_COMUM,
    COUNTIF(fd.Classification_LM LIKE 'LOST%' OR fd.Classification_LM LIKE 'FRAUD%') AS FRAUDES,
    COUNTIF(fd.Classification_LM LIKE 'DAMAGED%')                               AS DAMAGED,
    ROUND(SUM(fd.BPP_CASHOUT_USD), 2)                                            AS TOTAL_BPP
FROM fraud_driver fd
JOIN `meli-bi-data.WHOWNER.BT_SHP_PLACES_AND_NODES` p
    ON CAST(p.SHP_SHIPMENT_ID AS STRING) = fd.SHP_SHIPMENT_ID
   AND p.SERVICE_TYPE = 'DO'
GROUP BY 1, 2, 3
HAVING INCIDENTES_EM_COMUM >= 2
ORDER BY INCIDENTES_EM_COMUM DESC
LIMIT 80
"""

QUERY_PLACES = f"""
-- Ranking de places por fraudes (LOST + FRAUD apenas)
WITH fraudes AS (
    SELECT SAFE_CAST(SHIPMENT_ID AS STRING) AS SHP_SHIPMENT_ID,
           Classification_LM,
           ROUND(BPP_CASHOUT_USD, 2) AS BPP_CASHOUT_USD
    FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
    WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
      AND date_bpp >= '{ANO_INICIO}'
      AND date_bpp <= CURRENT_DATE()
      AND Classification_LM IN (
          'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
          'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE')
)
SELECT
    p.SHP_AGENCY_ID,
    p.SHP_AGEN_DESC                                             AS PLACE_NOME,
    COUNT(DISTINCT f.SHP_SHIPMENT_ID)                           AS TOTAL,
    ROUND(SUM(f.BPP_CASHOUT_USD), 2)                            AS TOTAL_BPP,
    COUNTIF(f.Classification_LM = 'LOST ON ROUTE')              AS LOST_ON_ROUTE,
    COUNTIF(f.Classification_LM = 'LOST ON WAY')                AS LOST_ON_WAY,
    COUNTIF(f.Classification_LM = 'LOST AT STATION')            AS LOST_AT_STATION,
    COUNTIF(f.Classification_LM = 'LOST ENE')                   AS LOST_ENE,
    COUNTIF(f.Classification_LM LIKE 'FRAUD%')                  AS FRAUD_CONFIRMADO
FROM fraudes f
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_PLACES_AND_NODES` p
    ON CAST(p.SHP_SHIPMENT_ID AS STRING) = f.SHP_SHIPMENT_ID
   AND p.SERVICE_TYPE = 'DO'
GROUP BY 1, 2
ORDER BY TOTAL DESC
LIMIT 50
"""

QUERY_DRIVER_STATUS = f"""
-- Status de todos os drivers: blocked, inactive/fraud_prevention, removed, active
-- Prioridade: blocked > inactive > removed > active
WITH loyalty AS (
  SELECT
    crowd_driver_id AS driverid,
    CASE
      WHEN scenarios.last_mile_crowd.progress.value = 1 THEN 'Bronze'
      WHEN scenarios.last_mile_crowd.progress.value = 2 THEN 'Prata'
      WHEN scenarios.last_mile_crowd.progress.value = 3 THEN 'Ouro'
      WHEN scenarios.last_mile_crowd.progress.value = 4 THEN 'Platina'
      ELSE 'N/A'
    END AS lealdade
  FROM `meli-bi-data.WHOWNER.BT_SHP_MT_METRICS_LOYALTY` l
  LEFT JOIN UNNEST(l.player.profiles.last_mile_crowd) AS crowd_driver_id
  WHERE l.period_monthly = TRUE
    AND l.player.scenarios.is_last_mile_crowd = TRUE
    AND l.site_id = 'MLB'
    AND l.period_id = EXTRACT(YEAR FROM DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH)) * 100
                    + EXTRACT(MONTH FROM DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH))
),
-- Todos os status distintos por driver (pode ter múltiplos)
status_raw AS (
  SELECT DISTINCT
    CAST(r.DRIVER_ID AS STRING)       AS DRIVER_ID,
    s.SHP_CROWD_STATUS                AS STATUS,
    s.SHP_CROWD_SUBSTATUS             AS SUBSTATUS
  FROM `meli-bi-data.WHOWNER.BT_SHP_CROWD_TRACKER_REGIST` AS r
  LEFT JOIN `meli-bi-data.WHOWNER.BT_SHP_CROWD_DRIVER_REG_STATUS` AS ds
    ON r.DRIVER_ID = ds.SHP_CROWD_DRIVER_ID
  LEFT JOIN `meli-bi-data.WHOWNER.BT_SHP_CROWD_REG_STATUS` AS s
    ON ds.SHP_CROWD_STATUS_ID = s.SHP_CROWD_ID
  WHERE r.SITE = 'MLB'
),
-- Pega o status mais grave por driver
status_priority AS (
  SELECT DISTINCT
    DRIVER_ID,
    FIRST_VALUE(STATUS) OVER (
      PARTITION BY DRIVER_ID
      ORDER BY CASE STATUS
        WHEN 'blocked'  THEN 1
        WHEN 'inactive' THEN 2
        WHEN 'removed'  THEN 3
        ELSE 4
      END
    ) AS STATUS,
    FIRST_VALUE(SUBSTATUS) OVER (
      PARTITION BY DRIVER_ID
      ORDER BY CASE STATUS
        WHEN 'blocked'  THEN 1
        WHEN 'inactive' THEN 2
        WHEN 'removed'  THEN 3
        ELSE 4
      END
    ) AS SUBSTATUS
  FROM status_raw
),
fraud_drivers AS (
  SELECT DISTINCT SAFE_CAST(DRIVER_ID AS STRING) AS DRIVER_ID
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
    AND date_bpp >= '{ANO_INICIO}'
    AND DRIVER_ID IS NOT NULL
)
SELECT
  sp.DRIVER_ID,
  sp.STATUS,
  sp.SUBSTATUS,
  loy.lealdade AS CATEGORIA
FROM status_priority sp
INNER JOIN fraud_drivers fd ON sp.DRIVER_ID = fd.DRIVER_ID
LEFT JOIN loyalty loy ON sp.DRIVER_ID = CAST(loy.driverid AS STRING)
ORDER BY
  CASE sp.STATUS WHEN 'blocked' THEN 1 WHEN 'inactive' THEN 2 WHEN 'removed' THEN 3 ELSE 4 END
"""

QUERY_DRIVER_ROUTES = f"""
-- Última rota, transportadora e atividade dos drivers da análise de fraude
SELECT
    CAST(r.SHP_LG_DRIVER_ID AS STRING)                                          AS DRIVER_ID,
    c.SHP_COMPANY_NAME                                                           AS TRANSPORTADORA,
    MAX(DATE(r.SHP_LG_ROUTE_INIT_DATE))                                         AS ULTIMA_ROTA,
    DATE_DIFF(CURRENT_DATE(), MAX(DATE(r.SHP_LG_ROUTE_INIT_DATE)), DAY)         AS DIAS_SEM_ROTA,
    COUNT(DISTINCT r.SHP_LG_ROUTE_ID)                                            AS ROTAS_ANO
FROM `meli-bi-data.WHOWNER.BT_SHP_LG_SHIPMENTS_ROUTES` r
LEFT JOIN `meli-bi-data.WHOWNER.LK_SHP_COMPANIES` c
    ON r.SHP_COMPANY_ID = c.SHP_COMPANY_ID
WHERE r.SHP_LG_FACILITY_ID = 'SSP30'
  AND DATE(r.SHP_LG_ROUTE_INIT_DATE) >= '{ANO_INICIO}'
  AND CAST(r.SHP_LG_DRIVER_ID AS STRING) IN (
      SELECT DISTINCT SAFE_CAST(DRIVER_ID AS STRING)
      FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
      WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
        AND date_bpp >= '{ANO_INICIO}'
        AND DRIVER_ID IS NOT NULL
  )
GROUP BY 1, 2
ORDER BY DIAS_SEM_ROTA DESC
"""

QUERY_DRIVER_PLACA = f"""
-- Placa mais recente por driver (SSP30, baseado em atribuições de rota)
SELECT
    CAST(DRIVER_ID AS STRING)                                                    AS DRIVER_ID,
    LICENCE_PLATE
FROM `meli-bi-data.WHOWNER.BT_SHP_CROWD_DASS_ASSIGNMENT`
WHERE FACILITY_ID = 'SSP30'
  AND DATE(CREATED_AT) >= '{ANO_INICIO}'
  AND LICENCE_PLATE IS NOT NULL
  AND LICENCE_PLATE != ''
QUALIFY ROW_NUMBER() OVER (PARTITION BY DRIVER_ID ORDER BY CREATED_AT DESC) = 1
"""

QUERY_PLACE_SHIPMENTS = f"""
-- SHP IDs por place (LOST + FRAUD apenas)
SELECT
    p.SHP_AGENCY_ID                                                                    AS AGENCY_ID,
    REGEXP_REPLACE(p.SHP_AGEN_DESC, r'Ag[êe]ncia Mercado Livre - ', '')               AS PLACE_NOME,
    CAST(f.SHIPMENT_ID AS STRING)                                                       AS SHP_ID,
    SAFE_CAST(f.DRIVER_ID AS STRING)                                                    AS DRIVER_ID,
    f.Classification_LM                                                                 AS CLASSIFICACAO,
    ROUND(f.BPP_CASHOUT_USD, 2)                                                         AS BPP,
    FORMAT_DATE('%d/%m/%Y', f.date_bpp)                                                 AS DATA
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO` f
JOIN `meli-bi-data.WHOWNER.BT_SHP_PLACES_AND_NODES` p
    ON CAST(p.SHP_SHIPMENT_ID AS STRING) = CAST(f.SHIPMENT_ID AS STRING)
   AND p.SERVICE_TYPE = 'DO'
WHERE f.SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND f.date_bpp >= '{ANO_INICIO}'
  AND f.date_bpp <= CURRENT_DATE()
  AND f.Classification_LM IN (
      'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
      'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE')
ORDER BY p.SHP_AGEN_DESC, f.BPP_CASHOUT_USD DESC
"""

QUERY_CRUZAMENTO = f"""
-- Sellers e Buyers ofensores cruzados com drivers de fraude
WITH fraudes AS (
  SELECT
    CAST(SHIPMENT_ID AS STRING)    AS SHP_ID,
    SAFE_CAST(DRIVER_ID AS STRING) AS DRIVER_ID
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = '{FACILITY_NAME}'
    AND date_bpp >= '{ANO_INICIO}'
    AND date_bpp <= CURRENT_DATE()
    AND Classification_LM IN (
      'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
      'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE'
    )
)
SELECT
  CAST(shp.SHP_SENDER_ID   AS STRING)     AS SELLER_ID,
  CAST(shp.SHP_RECEIVER_ID AS STRING)     AS BUYER_ID,
  COUNT(DISTINCT f.SHP_ID)                AS QTD_FRAUDES,
  STRING_AGG(DISTINCT f.DRIVER_ID, ',')   AS DRIVERS,
  STRING_AGG(DISTINCT f.SHP_ID, ',')      AS SHP_IDS
FROM fraudes f
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` shp
  ON CAST(shp.SHP_SHIPMENT_ID AS STRING) = f.SHP_ID
GROUP BY 1, 2
HAVING COUNT(DISTINCT f.SHP_ID) >= 2
ORDER BY 3 DESC
LIMIT 200
"""

QUERY_CRUZAMENTO_MES = f"""
WITH fraudes AS (
  SELECT CAST(SHIPMENT_ID AS STRING) AS SHP_ID,
    FORMAT_DATE('%Y-%m', date_bpp) AS MES
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = '{FACILITY_NAME}'
    AND date_bpp >= '{ANO_INICIO}'
    AND date_bpp <= CURRENT_DATE()
    AND Classification_LM IN (
      'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
      'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE'
    )
)
SELECT f.MES,
  CAST(shp.SHP_SENDER_ID   AS STRING) AS SELLER_ID,
  CAST(shp.SHP_RECEIVER_ID AS STRING) AS BUYER_ID,
  COUNT(DISTINCT f.SHP_ID)            AS QTD_FRAUDES
FROM fraudes f
INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` shp
  ON CAST(shp.SHP_SHIPMENT_ID AS STRING) = f.SHP_ID
GROUP BY 1, 2, 3
ORDER BY 1 DESC, 4 DESC
"""

QUERY_DAMAGED = f"""
-- Damaged por driver — usa DRIVER_ID direto da tabela
SELECT
    SAFE_CAST(DRIVER_ID AS STRING)                           AS DRIVER_ID,
    COUNT(DISTINCT SHIPMENT_ID)                              AS TOTAL_DAMAGED,
    ROUND(SUM(BPP_CASHOUT_USD), 2)                           AS TOTAL_BPP,
    COUNTIF(Classification_LM = 'DAMAGED ON ROUTE')          AS DAMAGED_ON_ROUTE,
    COUNTIF(Classification_LM = 'DAMAGED AT STATION')        AS DAMAGED_AT_STATION,
    COUNTIF(Classification_LM = 'DAMAGED ENE')               AS DAMAGED_ENE
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND date_bpp >= '{ANO_INICIO}'
  AND date_bpp <= CURRENT_DATE()
  AND Classification_LM LIKE 'DAMAGED%'
  AND DRIVER_ID IS NOT NULL
GROUP BY 1
ORDER BY TOTAL_DAMAGED DESC
LIMIT 60
"""

QUERY_DC_NEX = f"""
-- Pacotes da SSP30 (Guarulhos Mega) com perda confirmada que passaram por DC/NEX/XPT
WITH lost_sssp30 AS (
  SELECT
    CAST(SHIPMENT_ID AS STRING)      AS shp_id,
    ROUND(BPP_CASHOUT_USD, 2)        AS bpp,
    Classification_LM                AS classificacao,
    FORMAT_DATE('%d/%m/%Y', date_bpp) AS data_bpp
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
    AND date_bpp >= '{ANO_INICIO}'
    AND date_bpp <= CURRENT_DATE()
    AND Classification_LM IN (
      'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
      'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE')
),
dc_nex AS (
  SELECT
    CAST(SHP_SHIPMENT_ID AS STRING)      AS shp_id,
    CAST(SHP_DESTINATION_FACILITY_ID AS STRING) AS facility_id,
    LM_DESTINATION_FACILITY_TYPE         AS tipo,
    MAX(SHP_DATE_HANDLING_ID)            AS ultima_data
  FROM `meli-bi-data.WHOWNER.BT_SHP_TRACKER_DELAY_CAUSE_DIT`
  WHERE SHP_SITE_ID = 'MLB'
    AND SHP_DATE_HANDLING_ID >= '{ANO_INICIO}'
    AND LM_DESTINATION_FACILITY_TYPE IN ('NEX','DC','XPT')
  GROUP BY 1, 2, 3
)
SELECT
  l.shp_id,
  l.classificacao,
  l.bpp,
  l.data_bpp,
  d.facility_id,
  d.tipo,
  FORMAT_DATE('%d/%m/%Y', d.ultima_data) AS data_dc_nex,
  CAST(pan.DRIVER_LM AS STRING)           AS driver_lm,
  REGEXP_REPLACE(pan.SHP_AGEN_DESC, r'Ag[êe]ncia Mercado Livre - ', '') AS place_nome
FROM lost_sssp30 l
JOIN dc_nex d ON d.shp_id = l.shp_id
LEFT JOIN `meli-bi-data.WHOWNER.BT_SHP_PLACES_AND_NODES` pan
  ON CAST(pan.SHP_SHIPMENT_ID AS STRING) = l.shp_id
  AND pan.SERVICE_TYPE = d.tipo
ORDER BY l.bpp DESC
LIMIT 300
"""

QUERY_DAMAGED_ENE_CASOS = """
SELECT
  CAST(lp.SHIPMENT_ID AS STRING)           AS shp_id,
  lp.CUS_NICKNAME_SEL                      AS seller_nome,
  ROUND(lp.BPP_CASHOUT_USD, 2)             AS bpp,
  FORMAT_DATE('%d/%m/%Y', lp.date_bpp)     AS data,
  FORMAT_DATE('%Y-%m', lp.date_bpp)        AS mes,
  COALESCE(o.ITEM_TITLE, '')               AS item_title
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO` lp
LEFT JOIN `meli-bi-data.WHOWNER.BT_VIEW_ORD_ORDERS` o
  ON CAST(lp.SHIPMENT_ID AS INT64) = o.SHP_SHIPMENT_ID
WHERE lp.SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND lp.date_bpp >= '2026-01-01'
  AND lp.date_bpp <= CURRENT_DATE()
  AND lp.Classification_LM = 'DAMAGED ENE'
ORDER BY lp.date_bpp DESC
LIMIT 5000
"""

QUERY_BUYER_VELOCIDADE = f"""
-- Velocidade de fraude: buyers com pico de SHPs de fraude/perda na SSP30 por mês
-- Mesmo padrão do CRUZAMENTO: filtra SSP30 primeiro (pequeno), depois join BT_SHP_SHIPMENTS
WITH fraudes AS (
  SELECT
    CAST(SHIPMENT_ID AS STRING)         AS SHP_ID,
    FORMAT_DATE('%Y-%m', date_bpp)      AS MES,
    ROUND(BPP_CASHOUT_USD, 2)           AS BPP
  FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
  WHERE SHP_LG_FACILITY_NAME = '{FACILITY_NAME}'
    AND date_bpp >= '{ANO_INICIO}'
    AND date_bpp <= CURRENT_DATE()
    AND Classification_LM IN (
      'LOST ON ROUTE','LOST ON WAY','LOST AT STATION','LOST ENE',
      'FRAUD ON ROUTE','FRAUD AT STATION','FRAUD ENE'
    )
),
com_buyer AS (
  SELECT
    CAST(shp.SHP_RECEIVER_ID AS STRING) AS BUYER_ID,
    f.MES,
    f.SHP_ID,
    f.BPP
  FROM fraudes f
  INNER JOIN `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS` shp
    ON CAST(shp.SHP_SHIPMENT_ID AS STRING) = f.SHP_ID
),
mensal AS (
  SELECT
    BUYER_ID,
    MES,
    COUNT(DISTINCT SHP_ID)   AS FRAUDES_MES,
    ROUND(SUM(BPP), 2)       AS BPP_MES
  FROM com_buyer
  GROUP BY 1, 2
)
SELECT
  BUYER_ID,
  SUM(FRAUDES_MES)                                              AS TOTAL_FRAUDES,
  MAX(FRAUDES_MES)                                              AS PICO_FRAUDES_MES,
  ROUND(SUM(BPP_MES), 2)                                        AS BPP_TOTAL_USD,
  MAX(BPP_MES)                                                  AS BPP_PICO_MES,
  COUNT(DISTINCT MES)                                           AS MESES_ATIVOS,
  MAX(MES)                                                      AS MES_PICO,
  STRING_AGG(
    CONCAT(MES, ':', CAST(FRAUDES_MES AS STRING)),
    '|' ORDER BY MES
  )                                                             AS HISTORICO_MENSAL
FROM mensal
GROUP BY 1
HAVING SUM(FRAUDES_MES) >= 2
ORDER BY BPP_TOTAL_USD DESC, PICO_FRAUDES_MES DESC
LIMIT 100
"""

QUERY_DAMAGED_ENE_CAUSAS = """
SELECT
  SHP_NODE_CAUSE    AS causa,
  SHP_NODE_CAUSE_L2 AS causa_l2,
  COUNT(DISTINCT CAST(SHP_SHIPMENT_BPP AS STRING)) AS total
FROM `meli-bi-data.WHOWNER.BT_LP_NODES`
WHERE SHP_LG_FACILITY_ID = 'SSP30'
  AND DATEPARAMETER >= '2026-01-01'
  AND SHP_BKO_SUBSTATUS = 'damaged'
  AND SHP_NODE_CAUSE IS NOT NULL
GROUP BY 1, 2
ORDER BY total DESC
LIMIT 200
"""

QUERY_FRAUD_ENE_CASOS = """
SELECT
  CAST(lp.SHIPMENT_ID AS STRING)           AS shp_id,
  lp.CUS_NICKNAME_SEL                      AS seller_nome,
  ROUND(lp.BPP_CASHOUT_USD, 2)             AS bpp,
  FORMAT_DATE('%d/%m/%Y', lp.date_bpp)     AS data,
  FORMAT_DATE('%Y-%m', lp.date_bpp)        AS mes,
  lp.Classification_LM                     AS classificacao,
  lp.TIPO_FRAUDE                           AS tipo_fraude,
  COALESCE(o.ITEM_TITLE, '')               AS item_title
FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO` lp
LEFT JOIN `meli-bi-data.WHOWNER.BT_VIEW_ORD_ORDERS` o
  ON CAST(lp.SHIPMENT_ID AS INT64) = o.SHP_SHIPMENT_ID
WHERE lp.SHP_LG_FACILITY_NAME = 'Guarulhos Mega'
  AND lp.date_bpp >= '2026-01-01'
  AND lp.date_bpp <= CURRENT_DATE()
  AND CAST(lp.FLAG_ENE AS STRING) = '1'
  AND lp.TIPO_FRAUDE != 'NOT_FRAUD'
ORDER BY lp.date_bpp DESC
LIMIT 5000
"""

# ============================================================
# CONEXÃO E CONSULTAS
# ============================================================
def norm_id(s):
    """Normaliza ID: '292999.0' → '292999'"""
    try:    return str(int(float(str(s).strip())))
    except: return str(s).strip()

def _ym(d):
    """'dd/mm/yyyy' → 'yyyy-mm'"""
    try: return d[6:10]+'-'+d[3:5] if len(str(d)) >= 10 else ''
    except: return ''

def conectar():
    print("Conectando ao BigQuery e Google Sheets...")
    scopes = [
        'https://www.googleapis.com/auth/bigquery',
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/cloud-platform',
    ]
    creds, _ = default(scopes=scopes)
    bq = bigquery.Client(credentials=creds, project='meli-bi-data')
    gs = gspread.authorize(creds)
    return bq, gs

def processar_acumulo_bloqueio(drivers, shp_por_driver, days=90):
    from datetime import datetime as _dta, timedelta as _td
    STATUS_NAO_BLOQ = {'inactive','inativo','bloqueado','blocked','suspendido','suspended'}
    cutoff = _dta.now() - _td(days=days)
    min_meses = 2 if days <= 60 else 3
    result = []
    for d in drivers:
        did  = str(d.get('id','') or '').strip()
        dst  = str(d.get('status','') or '').strip().lower()
        if not did or dst in STATUS_NAO_BLOQ: continue
        shps_all = shp_por_driver.get(did, [])
        # Apenas FRAUD e LOST ON ROUTE nos últimos 90 dias
        # DAMAGED, LOST AT STATION e LOST ON WAY excluídos
        _CLASSES_VALIDAS = ('FRAUD', 'LOST ON ROUTE')
        def _in_window(s):
            try:
                return _dta.strptime(s.get('data',''), '%d/%m/%Y') >= cutoff
            except Exception:
                return False
        shps = [s for s in shps_all
                if float(s.get('bpp',0) or 0) > 0
                and _in_window(s)
                and any(k in str(s.get('class','')) for k in _CLASSES_VALIDAS)]
        if not shps: continue
        meses = set()
        for s in shps:
            try:
                dr = _dta.strptime(s.get('data',''), '%d/%m/%Y')
                meses.add(f'{dr.month:02d}/{dr.year}')
            except Exception: pass
        if len(meses) < min_meses: continue
        classes = [str(s.get('class','')) for s in shps]
        has_fraud = any('FRAUD' in c for c in classes)
        has_lost  = any('LOST'  in c for c in classes)
        tipo = 'lost_fraude' if (has_fraud and has_lost) else ('fraude_pura' if has_fraud else 'outro')
        total_bpp = round(sum(float(s.get('bpp',0) or 0) for s in shps), 2)
        max_bpp   = round(max((float(s.get('bpp',0) or 0) for s in shps), default=0), 2)
        residual  = round(total_bpp - max_bpp, 2)
        n_pkgs    = len(shps)
        apto, motivo = True, ''
        if n_pkgs <= 5:
            apto, motivo = False, f'Apenas {n_pkgs} pacotes (mínimo 6)'
        elif residual <= 300:
            apto, motivo = False, f'Residual ${residual:.0f} abaixo de $300'
        nome = str(d.get('nome','') or d.get('transportadora','') or '').strip()
        transp = str(d.get('transportadora','') or '').strip()
        result.append({
            'id':did,'nome':nome,'transportadora':transp,'status':dst,
            'n_meses':len(meses),'meses':sorted(meses),
            'n_pkgs':n_pkgs,'total_bpp':total_bpp,'max_bpp':max_bpp,'residual':residual,
            'tipo':tipo,'apto':apto,'motivo':motivo,
            'shps':sorted(shps, key=lambda x: -float(x.get('bpp',0) or 0)),
        })
    result.sort(key=lambda x: (0 if x['apto'] else 1, -x['n_meses'], -x['total_bpp']))
    return result

def rows_acumulo_bloqueio(candidatos, pid=''):
    if not candidatos:
        return '<div style="padding:32px;text-align:center;color:#6b7280">Nenhum driver com acúmulo BPP neste período encontrado.</div>'
    TIPO_LBL = {'fraude_pura':'Fraude','lost_fraude':'Lost + Fraude','outro':'Outro'}
    TIPO_COR  = {'fraude_pura':'#A32D2D;background:#FCEBEB','lost_fraude':'#0C447C;background:#E6F1FB','outro':'#5F5E5A;background:#F1EFE8'}
    html = ''
    _pfx = f'p{pid}_' if pid else ''
    for i, c in enumerate(candidatos):
        tid = f'acbl_{_pfx}{c["id"]}'
        tipo_lbl = TIPO_LBL.get(c['tipo'], c['tipo'])
        tipo_cor  = TIPO_COR.get(c['tipo'], '#5F5E5A;background:#F1EFE8')
        if c['apto']:
            if c['tipo'] == 'fraude_pura':
                apto_html = '<span style="background:#EAF3DE;color:#27500A;font-size:10px;padding:2px 9px;border-radius:10px;font-weight:500">✓ Acionar time de fraude</span>'
            else:
                apto_html = '<span style="background:#EAF3DE;color:#27500A;font-size:10px;padding:2px 9px;border-radius:10px;font-weight:500">✓ Apto para bloqueio</span>'
        else:
            apto_html = f'<span style="background:#F1EFE8;color:#5F5E5A;font-size:10px;padding:2px 9px;border-radius:10px;font-weight:500">✗ {c["motivo"]}</span>'
        # Tabela de pacotes
        rows_shp = ''
        for s in c['shps']:
            bpp_v = float(s.get('bpp',0) or 0)
            is_max = abs(bpp_v - c['max_bpp']) < 0.01
            max_tag = ' <span style="background:#FAEEDA;color:#633806;font-size:9px;padding:1px 5px;border-radius:4px">maior</span>' if is_max else ''
            cls = str(s.get('class',''))
            cls_cor = '#A32D2D' if 'FRAUD' in cls else ('#633806' if 'LOST' in cls else '#374151')
            sem = str(s.get('semana',''))
            # Converter SEMANA '2026-W15' -> 'S15'
            if '-W' in sem:
                sem = 'S' + sem.split('-W')[-1].lstrip('0') or 'S?'
            shp_id = s.get('id','')
            shp_link = (f'<a href="https://shipping-bo.adminml.com/sauron/shipments/shipment/{shp_id}" '
                        f'target="_blank" style="color:#60a5fa;font-family:monospace;text-decoration:none">'
                        f'{shp_id}</a>') if shp_id else '—'
            rows_shp += (f'<tr><td style="font-size:11px;color:#6b7280">{sem}</td>'
                         f'<td style="font-size:11px;color:{cls_cor}">{cls}</td>'
                         f'<td style="font-size:11px">{shp_link}</td>'
                         f'<td style="font-size:11px;text-align:right;font-weight:500">'
                         f'${bpp_v:,.2f}{max_tag}</td></tr>')
        residual_txt = (f'<b>BPP sem maior: ${c["residual"]:,.2f}</b>'
                        + (' ≥ $300 ✓' if c["residual"]>=300 else ' &lt; $300 ✗')
                        if c['tipo']=='lost_fraude' else
                        'Fraude pura → acionar time de fraude para validação')
        html += f'''<div style="border:0.5px solid #1a2035;border-radius:10px;margin-bottom:10px;overflow:hidden">
  <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;background:#0d1526;border-bottom:0.5px solid #1a2035;cursor:pointer" onclick="var el=document.getElementById('{tid}');el.style.display=el.style.display==='none'?'block':'none'">
    <div style="flex:1">
      <a href="{'https://envios.adminml.com/logistics/drivers-management/drivers/' + str(c['id']) if 'meli extra' in (c.get('transportadora') or '').lower() else 'https://envios.adminml.com/logistics/provider-management/drivers-block/list?searchType=id&searchValue=' + str(c['id'])}" target="_blank" onclick="event.stopPropagation()" style="font-size:15px;font-weight:700;color:#60a5fa;font-family:monospace;text-decoration:none">{c["id"]}</a>
      <div style="font-size:10px;color:#6b7280;margin-top:2px">{c["nome"] or c["transportadora"] or "—"}</div>
    </div>
    <span style="color:{tipo_cor};font-size:10px;padding:2px 9px;border-radius:10px;font-weight:500">{tipo_lbl}</span>
    {apto_html}
    <button onclick="event.stopPropagation();gerarPptx('{c["id"]}')" title="Gerar apresentação .pptx"
      style="font-size:10px;padding:3px 10px;border-radius:6px;border:1px solid #1f3050;background:#0d1526;
             color:#60a5fa;cursor:pointer;white-space:nowrap;display:flex;align-items:center;gap:4px">
      📊 .pptx
    </button>
    <span style="color:#4b5563;font-size:14px">▾</span>
  </div>
  <div style="display:flex;gap:0;border-bottom:0.5px solid #1a2035">
    <div style="flex:1;padding:8px 14px;border-right:0.5px solid #1a2035">
      <div style="font-size:18px;font-weight:600;color:#f9fafb">{c["n_meses"]}</div>
      <div style="font-size:10px;color:#6b7280">meses</div>
    </div>
    <div style="flex:1;padding:8px 14px;border-right:0.5px solid #1a2035">
      <div style="font-size:18px;font-weight:600;color:#f9fafb">{c["n_pkgs"]}</div>
      <div style="font-size:10px;color:#6b7280">pacotes</div>
    </div>
    <div style="flex:1;padding:8px 14px;border-right:0.5px solid #1a2035">
      <div style="font-size:18px;font-weight:600;color:#E24B4A">${c["total_bpp"]:,.0f}</div>
      <div style="font-size:10px;color:#6b7280">BPP total</div>
    </div>
    <div style="flex:1;padding:8px 14px">
      <div style="font-size:13px;font-weight:600;color:{'#E24B4A' if c.get('data_solicitacao') else '#6b7280'}">{'Sim — ' + c['data_solicitacao'] if c.get('data_solicitacao') else 'Não'}</div>
      <div style="font-size:10px;color:#6b7280">tentativa bloqueio</div>
    </div>
  </div>
  <div id="{tid}" style="display:none">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr style="border-bottom:0.5px solid #1a2035;background:#060c18">
        <th style="padding:6px 14px;font-size:10px;color:#4b5563;font-weight:600;text-align:left">Semana</th>
        <th style="padding:6px 14px;font-size:10px;color:#4b5563;font-weight:600;text-align:left">Tipo</th>
        <th style="padding:6px 14px;font-size:10px;color:#4b5563;font-weight:600;text-align:left">Shipment ID</th>
        <th style="padding:6px 14px;font-size:10px;color:#4b5563;font-weight:600;text-align:right">BPP</th>
      </tr></thead>
      <tbody>{rows_shp}</tbody>
    </table>
    <div style="padding:8px 14px;font-size:11px;color:#9ca3af;background:#060c18;border-top:0.5px solid #1a2035">{residual_txt}</div>
  </div>
</div>'''
    return html

def carregar_block_list(gs):
    print("  Lendo Block List...")
    try:
        pl    = gs.open_by_key(BLOCK_LIST_ID)
        dados = pl.worksheet(ABA_BLOQUEIOS).get_all_values()
        if len(dados) <= 1:
            return []
        header = dados[0]
        rows   = []
        for r in dados[1:]:
            if not any(r): continue
            row = dict(zip(header, r))
            ano = row.get('Ano','').strip()
            if ano == '2026' or not ano:
                rows.append(row)
        print(f"  {len(rows)} registros na Block List")
        return rows
    except Exception as e:
        print(f"  Aviso Block List: {e}")
        return []

def carregar_cftv(gs):
    print("  Lendo planilha CFTV...")
    def _fetch():
        pl   = gs.open_by_key(CFTV_SHEET_ID)
        data = pl.worksheet(CFTV_ABA).get_all_values()
        if len(data) <= 1:
            return []
        header = data[0]
        rows   = [dict(zip(header, r)) for r in data[1:] if any(r)]
        print(f"  {len(rows)} solicitações CFTV")
        return rows
    try:
        return _sheets_cache('cftv', _fetch)
    except Exception as e:
        print(f"  Aviso CFTV: {e}")
        return []

def _sin_row_html(c):
    rec_ok    = (c.get('recup_carga') or '').strip().lower() in ('sim', 'yes', 's')
    tipo_val  = (c.get('tipo') or '').strip()
    tipo_col  = '#f87171' if 'sinistro' in tipo_val.lower() else '#fbbf24'
    bpp_val   = c.get('bpp', 0) or 0
    bpp_fmt   = f"${bpp_val:,.2f}" if bpp_val else '—'
    relato    = (c.get('relato') or '').strip()
    relato    = (relato[:65] + '...') if len(relato) > 65 else relato
    rec_color = '#4ade80' if rec_ok else '#f87171'
    rec_txt   = 'Sim' if rec_ok else 'Não'
    return (f'<tr style="border-top:1px solid #111827">'
            f'<td style="padding:7px 10px;white-space:nowrap">{c.get("data","")}</td>'
            f'<td style="padding:7px 10px;white-space:nowrap">{c.get("horario","")}</td>'
            f'<td style="padding:7px 10px"><span style="background:{tipo_col}22;color:{tipo_col};padding:2px 6px;border-radius:4px;font-size:10px">{tipo_val or "—"}</span></td>'
            f'<td style="padding:7px 10px"><span style="font-family:monospace;color:#60a5fa">{c.get("driver_id","")}</span>'
            f'<br><span style="font-size:10px;color:#9ca3af">{c.get("nome","")}</span></td>'
            f'<td style="padding:7px 10px;font-size:11px">{c.get("transportadora","") or "—"}</td>'
            f'<td style="padding:7px 10px;font-family:monospace;font-size:11px">{c.get("placa","") or "—"}</td>'
            f'<td style="padding:7px 10px;text-align:center">{c.get("qtd_shp","") or "—"}</td>'
            f'<td style="padding:7px 10px;text-align:right;font-weight:600;color:#f87171">{bpp_fmt}</td>'
            f'<td style="padding:7px 10px;text-align:center;color:{rec_color}">{rec_txt}</td>'
            f'<td style="padding:7px 10px;font-size:10px;color:#9ca3af;max-width:200px">{relato}</td>'
            f'</tr>')


def carregar_sinistros(gs):
    print("  Lendo planilha Sinistros (Eventos SVC)...")
    def _fetch():
        pl   = gs.open_by_key(SINISTRO_SHEET_ID)
        data = pl.worksheet(SINISTRO_ABA).get_all_values()
        if len(data) <= 1:
            return {'casos': [], 'total': 0, 'bpp_total': 0.0, 'recuperados': 0, 'bpp_recuperado': 0.0}
        header = data[0]
        def _g(r, col):
            try:
                idx = next(i for i, h in enumerate(header) if h.strip() == col)
                return r[idx].strip() if idx < len(r) else ''
            except StopIteration:
                return ''
        casos = []
        for r in data[1:]:
            if not any(r): continue
            try:
                bpp = float((_g(r,'Bpp Cashout Usd') or '0').replace(',','.').replace('$','').replace(' ','') or '0')
            except: bpp = 0.0
            try:
                rbpp = float((_g(r,'Recup. Cashout Usd') or '0').replace(',','.').replace('$','').replace(' ','') or '0')
            except: rbpp = 0.0
            casos.append({
                'data':          _g(r,'F') or _g(r,'Data'),
                'horario':       _g(r,'Horario'),
                'rota':          _g(r,'Rota'),
                'driver_id':     _g(r,'Drive'),
                'nome':          _g(r,'Nome Drive'),
                'placa':         _g(r,'Placa'),
                'tipo':          _g(r,'TIPO 2'),
                'qtd_shp':       _g(r,'Qtde Shp'),
                'bpp':           bpp,
                'recup_carga':   _g(r,'Recup. da Carga?'),
                'recup_shp':     _g(r,'Recup. Shp'),
                'recup_bpp':     rbpp,
                'cep':           _g(r,'CEP'),
                'rua':           _g(r,'Rua'),
                'bairro':        _g(r,'Bairro ') or _g(r,'Bairro'),
                'cidade':        _g(r,'Cidade ') or _g(r,'Cidade'),
                'cluster':       _g(r,'CLUSTER'),
                'transportadora':_g(r,'MLP'),
                'veiculo':       _g(r,'Veículo') or _g(r,'Veiculo'),
                'natureza':      _g(r,'Natureza do evento'),
                'modus':         _g(r,'MODUS OPERANDI'),
                'boletim':       _g(r,'Boletim de ocorrência'),
                'link_bo':       _g(r,'Link boletim'),
                'relato':        _g(r,'Relato'),
            })
        recuperados = sum(1 for c in casos if (c['recup_carga'] or '').strip().lower() in ('sim','yes','s'))
        bpp_total   = sum(c['bpp'] for c in casos)
        bpp_rec     = sum(c['recup_bpp'] for c in casos)
        print(f"  {len(casos)} sinistros carregados")
        return {
            'casos':          casos,
            'total':          len(casos),
            'bpp_total':      round(bpp_total, 2),
            'recuperados':    recuperados,
            'bpp_recuperado': round(bpp_rec, 2),
        }
    try:
        return _sheets_cache('sinistros', _fetch)
    except Exception as e:
        print(f"  Aviso Sinistros: {e}")
        return {'casos': [], 'total': 0, 'bpp_total': 0.0, 'recuperados': 0, 'bpp_recuperado': 0.0}


def atualizar_planilha_ene(gs, damaged_ene, fraud_ene):
    """Reescreve A:F da planilha ENE SSP30 com os casos mais recentes do BQ."""
    SPREADSHEET_ENE = '1Ua5HDoP9HyPccMMYGel-GdCqj7cQh6G5w6MIf2wRjdo'
    try:
        sh = gs.open_by_key(SPREADSHEET_ENE)
        ws = sh.worksheet('Hoja 1')
        rows = [['SHP ID', 'Seller', 'Valor (USD)', 'KPI', 'Data', 'Descrição']]
        for c in (damaged_ene.get('casos') or []):
            rows.append([
                c.get('shp_id', ''),
                c.get('seller_nome', ''),
                str(c.get('bpp', '')),
                'Damaged ENE',
                c.get('data', ''),
                c.get('item_title', ''),
            ])
        for c in (fraud_ene or []):
            tipo = c.get('tipo_fraude', 'FRAUDE')
            rows.append([
                c.get('shp_id', ''),
                c.get('seller_nome', ''),
                str(c.get('bpp', '')),
                f'Fraud ENE ({tipo})',
                c.get('data', ''),
                c.get('item_title', ''),
            ])
        n = len(rows)
        ws.update(f'A1:F{n}', rows, value_input_option='USER_ENTERED')
        print(f"  Planilha ENE atualizada: {n - 1} casos → A1:F{n}")
    except Exception as e:
        print(f"  Aviso planilha ENE: {e}")


def sincronizar_status_block_list(gs, bq, bl_rows):
    """Consulta BQ e atualiza status na planilha para drivers Solicitado/Monitorado."""
    _sync_flag = os.path.join(_CACHE_DIR, 'sh_sync_bl.pkl')
    os.makedirs(_CACHE_DIR, exist_ok=True)
    if os.path.exists(_sync_flag):
        age = time.time() - os.path.getmtime(_sync_flag)
        if age < _SYNC_TTL:
            print(f"  Sync BL — pulando (rodou {int(age/60)}min atrás)")
            return

    ATUALIZAR = {'solicitado', 'monitorado'}
    def _st(r): return r.get('Status', r.get('status', '')).strip().lower()
    def _did(r): return str(r.get('Driver ID', r.get('driver_id', ''))).strip()
    pendentes = {_did(r) for r in bl_rows if _st(r) in ATUALIZAR and _did(r)}
    if not pendentes:
        print("  Nenhum driver pendente para sincronizar.")
        with open(_sync_flag, 'wb') as _f: pickle.dump(True, _f)
        return

    print(f"  Sincronizando status de {len(pendentes)} drivers com BQ...")

    def col_letter(n):
        s = ''
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    query = """
    SELECT DISTINCT DRIVER_ID, DRIVER_STATUS
    FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
    WHERE DATE_BPP >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
      AND DRIVER_ID IN UNNEST(@ids)
    QUALIFY ROW_NUMBER() OVER (PARTITION BY DRIVER_ID ORDER BY DATE_BPP DESC) = 1
    """
    job_cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter('ids', 'STRING', list(pendentes))
    ])
    try:
        status_bq = {str(r['DRIVER_ID']): r['DRIVER_STATUS']
                     for r in bq.query(query, job_config=job_cfg).result()}
    except Exception as e:
        print(f"  Aviso BQ status: {e}")
        return

    def mapear(s):
        if s == 'blocked':                             return 'Bloqueado'
        if s == 'active':                              return 'Monitorado'
        if s in ('inactive', 'removed', 'disabled'):  return 'Inativo'
        return None

    try:
        pl       = gs.open_by_key(BLOCK_LIST_ID)
        ws       = pl.worksheet(ABA_BLOQUEIOS)
        all_vals = ws.get_all_values()
        header   = all_vals[0] if all_vals else []
        col_id     = header.index('Driver ID') + 1 if 'Driver ID' in header else None
        col_status = header.index('Status')    + 1 if 'Status'    in header else None
        if not col_id or not col_status:
            print("  Colunas não encontradas na planilha.")
            return

        updates     = []
        status_memo = {}
        for i, row_vals in enumerate(all_vals[1:], start=2):
            did     = row_vals[col_id - 1].strip()     if len(row_vals) >= col_id     else ''
            current = row_vals[col_status - 1].strip() if len(row_vals) >= col_status else ''
            if did not in status_bq:
                continue
            novo = mapear(status_bq[did])
            if novo and novo != current:
                updates.append({'range': f'{col_letter(col_status)}{i}', 'values': [[novo]]})
                status_memo[did] = novo

        if updates:
            ws.batch_update(updates)
            print(f"  {len(updates)} status atualizados na planilha.")
            for r in bl_rows:
                did = _did(r)
                if did in status_memo:
                    if 'Status' in r:    r['Status']    = status_memo[did]
                    if 'status' in r:    r['status']    = status_memo[did]
        else:
            print("  Nenhuma alteração de status necessária.")
        with open(_sync_flag, 'wb') as _f: pickle.dump(True, _f)
    except Exception as e:
        print(f"  Aviso ao atualizar planilha: {e}")


def processar_block_list(rows):
    if not rows:
        return {
            'total':0,'bloqueados':0,'solicitados':0,
            'monitorados':0,'recusados':0,'gmv_protegido':0.0,
            'por_transp':{},'rows':[],'por_status':{}
        }
    def flt(v):
        try: return float(str(v).replace('$','').replace(',','.').strip() or 0)
        except: return 0.0
    def norm_status(s):
        s = s.strip().lower()
        if 'bloqueado' in s: return 'Bloqueado'
        if 'solicitado' in s: return 'Solicitado'
        if 'sendo' in s or 'monit' in s: return 'Monitorado'
        if 'recusado' in s: return 'Recusado'
        return 'Inativo'

    total = len(rows)
    bloqueados  = sum(1 for r in rows if 'bloqueado' in r.get('Status','').lower())
    solicitados = sum(1 for r in rows if 'solicitado' in r.get('Status','').lower())
    monitorados = sum(1 for r in rows if 'sendo' in r.get('Status','').lower() or 'monit' in r.get('Status','').lower())
    recusados   = sum(1 for r in rows if 'recusado' in r.get('Status','').lower())
    gmv_protegido = sum(flt(r.get('USD$','0')) for r in rows if 'bloqueado' in r.get('Status','').lower())

    por_transp = {}
    por_status = {}
    rows_out   = []
    for r in rows:
        mlp    = r.get('MLP','').strip() or 'N/A'
        status = norm_status(r.get('Status',''))
        por_transp[mlp]    = por_transp.get(mlp, 0) + 1
        por_status[status] = por_status.get(status, 0) + 1
        rows_out.append({
            'driver_id':  r.get('Driver ID','').strip(),
            'nome':       r.get('Nome','').strip(),
            'mlp':        mlp,
            'placa':      r.get('Placa','').strip(),
            'shp':        r.get('SHP','').strip(),
            'usd':        flt(r.get('USD$','0')),
            'semana':     r.get('Semana','').strip(),
            'data':       r.get('Data Solicitação','').strip(),
            'status':     status,
            'motivo':     r.get('Motivo','').strip(),
        })
    # Remove Recusado do gráfico (status descontinuado)
    por_status.pop('Recusado', None)
    # Agrupar por driver_id para histórico de solicitações
    from collections import defaultdict as _dd
    from datetime import datetime as _dt2
    _PRIO = {'Bloqueado':0,'Monitorado':1,'Solicitado':2,'Inativo':3,'Recusado':4}
    def _parse_dt(d):
        try: return _dt2.strptime(d, '%d/%m/%Y').timestamp()
        except: return 0.0
    grupos = _dd(list)
    for r in rows_out:
        grupos[r['driver_id'] or ''].append(r)
    final_rows = []
    for did, entries in grupos.items():
        entries_s = sorted(entries, key=lambda x: (_PRIO.get(x['status'],9), -_parse_dt(x['data'])))
        main = entries_s[0].copy()
        main['historico']      = entries_s
        main['n_solicitacoes'] = len(entries_s)
        final_rows.append(main)
    final_rows.sort(key=lambda x: (_PRIO.get(x['status'],9), -x['usd']))
    return {
        'total': total, 'bloqueados': bloqueados,
        'solicitados': solicitados, 'monitorados': monitorados,
        'recusados': recusados, 'gmv_protegido': gmv_protegido,
        'por_transp': por_transp, 'por_status': por_status,
        'rows': final_rows,
    }

def processar_cruzamento(df):
    if df is None or df.empty:
        return {'sellers':[],'buyers':[],'pares':[],
                'total_sellers':0,'total_buyers':0,'total_pares':0,'total_drivers':0}
    rows = df.to_dict('records')
    def _drivers(raw):
        if not raw or str(raw) in ('None','nan',''): return set()
        return {d.strip() for d in str(raw).split(',') if d.strip() and d.strip() != 'None'}

    seller_map, buyer_map = {}, {}
    for r in rows:
        sid = str(r['SELLER_ID']); bid = str(r['BUYER_ID']); qtd = int(r['QTD_FRAUDES'])
        drv = _drivers(r.get('DRIVERS',''))
        if sid not in seller_map: seller_map[sid] = {'seller_id':sid,'qtd':0,'buyers':set(),'drivers':set()}
        seller_map[sid]['qtd'] += qtd; seller_map[sid]['buyers'].add(bid); seller_map[sid]['drivers'] |= drv
        if bid not in buyer_map:  buyer_map[bid]  = {'buyer_id':bid, 'qtd':0,'sellers':set(),'drivers':set()}
        buyer_map[bid]['qtd']  += qtd; buyer_map[bid]['sellers'].add(sid); buyer_map[bid]['drivers']  |= drv

    sellers = sorted([{**v,
                       'buyers':len(v['buyers']),
                       'drivers':sorted(v['drivers'])[:6],
                       'n_drivers':len(v['drivers']),
                       'driver_ids':sorted(v['drivers'])[:6]}
                      for v in seller_map.values()], key=lambda x:-x['qtd'])
    buyers  = sorted([{**v,
                       'sellers':len(v['sellers']),
                       'seller_ids':sorted(v['sellers'])[:10],
                       'drivers':sorted(v['drivers'])[:6],
                       'n_drivers':len(v['drivers']),
                       'driver_ids':sorted(v['drivers'])[:6]}
                      for v in buyer_map.values()],  key=lambda x:-x['qtd'])
    def _clean(v): return str(v) if v and str(v) not in ('None','nan','') else ''
    pares = []
    for r in rows:
        shp_raw = _clean(r.get('SHP_IDS',''))
        shp_ids = [s.strip() for s in shp_raw.split(',') if s.strip()] if shp_raw else []
        pares.append({'seller_id':str(r['SELLER_ID']),'buyer_id':str(r['BUYER_ID']),
                      'qtd':int(r['QTD_FRAUDES']),
                      'drivers':_clean(r.get('DRIVERS','')) or '—',
                      'shp_ids':shp_ids})
    # Driver × Seller/Buyer cross-reference
    driver_map = {}
    for r in rows:
        sid = str(r['SELLER_ID']); bid = str(r['BUYER_ID']); qtd = int(r['QTD_FRAUDES'])
        for drv in _drivers(r.get('DRIVERS', '')):
            if drv not in driver_map:
                driver_map[drv] = {'driver_id': drv, 'qtd': 0, 'sellers': set(), 'buyers': set()}
            driver_map[drv]['qtd']     += qtd
            driver_map[drv]['sellers'].add(sid)
            driver_map[drv]['buyers'].add(bid)
    driver_crz = sorted(
        [{'driver_id': k, 'qtd': v['qtd'],
          'n_sellers': len(v['sellers']), 'n_buyers': len(v['buyers']),
          'sellers': sorted(v['sellers'])[:5], 'buyers': sorted(v['buyers'])[:5]}
         for k, v in driver_map.items()],
        key=lambda x: -x['qtd']
    )
    all_drv = set(); [all_drv.update(_drivers(r.get('DRIVERS',''))) for r in rows]
    return {'sellers':sellers,'buyers':buyers,'pares':pares,'driver_crz':driver_crz,
            'total_sellers':len(seller_map),'total_buyers':len(buyer_map),
            'total_pares':len(pares),'total_drivers':len(all_drv)}

def detectar_alertas_bl(bl, shp_por_driver, min_bpp=200.0, janela_dias=15):
    from datetime import datetime, timedelta
    hoje  = datetime.now().date()
    limite = hoje - timedelta(days=janela_dias)
    alertas = []
    for row in bl.get('rows', []):
        did    = row.get('driver_id', '').strip()
        status = row.get('status', '')
        if not did or status in ('Recusado', 'Inativo'):
            continue
        recentes = []
        for s in shp_por_driver.get(did, []):
            try:
                raw = s['data'][:10]
                fmt = '%d/%m/%Y' if '/' in raw else '%Y-%m-%d'
                dt  = datetime.strptime(raw, fmt).date()
            except Exception:
                continue
            if dt >= limite and s.get('bpp', 0) >= min_bpp:
                recentes.append({
                    'shp_id':     s['id'],
                    'bpp':        s['bpp'],
                    'data':       dt.strftime('%d/%m/%Y'),
                    'dias_atras': (hoje - dt).days,
                    'class':      s.get('class', ''),
                })
        if recentes:
            recentes.sort(key=lambda x: -x['bpp'])
            alertas.append({
                'driver_id':  did,
                'nome':       row.get('nome', ''),
                'status_bl':  status,
                'shps':       recentes,
                'max_bpp':    max(x['bpp'] for x in recentes),
                'total_shps': len(recentes),
            })
    alertas.sort(key=lambda x: -x['max_bpp'])
    return alertas

def rows_alertas_bl(alertas):
    if not alertas:
        return ''
    STATUS_COR = {'Bloqueado':'#ef4444','Solicitado':'#60a5fa','Monitorado':'#f59e0b'}
    out = []
    for a in alertas:
        for s in a['shps'][:3]:
            cor = STATUS_COR.get(a['status_bl'], '#9ca3af')
            nome_tag = f' <span style="color:#6b7280;font-size:10px">({a["nome"]})</span>' if a['nome'] else ''
            out.append(
                f'<tr style="border-top:1px solid #1f2937">'
                f'<td style="padding:6px 0;color:#e2e8f0;font-weight:500">{a["driver_id"]}{nome_tag}</td>'
                f'<td style="padding:6px 0"><span style="color:{cor};font-size:11px;font-weight:600">{a["status_bl"]}</span></td>'
                f'<td style="padding:6px 0;color:#9ca3af">{s["shp_id"]}</td>'
                f'<td style="padding:6px 0;text-align:right;color:#f87171;font-weight:700">${s["bpp"]:,.2f}</td>'
                f'<td style="padding:6px 0;text-align:right;color:#6b7280">{s["data"]} <span style="color:#4b5563">({s["dias_atras"]}d)</span></td>'
                f'</tr>'
            )
    return ''.join(out)

def processar_cruzamento_mes(df):
    if df is None or df.empty:
        return {}
    seller_by_mes, buyer_by_mes = {}, {}
    for _, r in df.iterrows():
        mes = str(r.get('MES', ''))
        sid = str(r['SELLER_ID']); bid = str(r['BUYER_ID']); qtd = int(r['QTD_FRAUDES'])
        if mes not in seller_by_mes:
            seller_by_mes[mes] = {}; buyer_by_mes[mes] = {}
        seller_by_mes[mes][sid] = seller_by_mes[mes].get(sid, 0) + qtd
        buyer_by_mes[mes][bid]  = buyer_by_mes[mes].get(bid, 0)  + qtd
    result = {}
    for mes in seller_by_mes:
        result[mes] = {
            'sellers': sorted([{'id':k,'qtd':v} for k,v in seller_by_mes[mes].items()], key=lambda x:-x['qtd'])[:10],
            'buyers':  sorted([{'id':k,'qtd':v} for k,v in buyer_by_mes[mes].items()],  key=lambda x:-x['qtd'])[:10],
        }
    return result

def processar_buyer_velocidade(df):
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        hist_raw = str(r.get('HISTORICO_MENSAL', '') or '')
        hist = {}
        for part in hist_raw.split('|'):
            if ':' in part:
                mes, qtd = part.rsplit(':', 1)
                try: hist[mes.strip()] = int(qtd.strip())
                except: pass
        rows.append({
            'buyer_id':         str(r.get('BUYER_ID', '')),
            'qtd_fraudes':      int(r.get('TOTAL_FRAUDES', 0) or 0),
            'bpp_fraude_usd':   float(r.get('BPP_TOTAL_USD', 0) or 0),
            'bpp_pico_mes':     float(r.get('BPP_PICO_MES', 0) or 0),
            'pico_pedidos_mes': int(r.get('PICO_FRAUDES_MES', 0) or 0),
            'total_pedidos':    int(r.get('TOTAL_FRAUDES', 0) or 0),
            'meses_ativos':     int(r.get('MESES_ATIVOS', 0) or 0),
            'mes_pico':         str(r.get('MES_PICO', '') or ''),
            'historico':        hist,
        })
    return rows

def carregar_cobrar_otr(gs):
    """Lê coluna 'Cobrar OTR' (col 33 = r[32]) da aba ON ROUTE.
    Retorna dict {shp_id: cobrar_otr_status}."""
    try:
        PLANILHA_RISCO_ID = '1rFcUXxl53WVQf_ASRx3mhlEvFoJevcaiwjMZY1vso5Y'
        ABA = 'Tratativas Risco On Route (HV) - Lucas'
        ws = gs.open_by_key(PLANILHA_RISCO_ID).worksheet(ABA)
        rows = ws.get_all_values()
        result = {}
        for r in rows[1:]:
            shp_id = r[2].strip() if len(r) > 2 else ''
            cobrar = r[32].strip() if len(r) > 32 else ''
            if shp_id:
                result[shp_id] = cobrar
        print(f"  Cobrar OTR: {len(result)} registros ON ROUTE lidos")
        return result
    except Exception as e:
        print(f"  Aviso Cobrar OTR: {e}")
        return {}

def processar_dc_nex(df, cobrar_otr_map):
    """Processa pacotes que passaram por DC/NEX/XPT.
    Retorna dict com facilities (agrupadas), driver ranking e análise de responsabilidade."""
    if df is None or df.empty:
        return {'facilities': [], 'drivers': [], 'total_pkgs': 0, 'total_gmv': 0.0}
    tipo_label = {'NEX': 'NEX', 'DC': 'DC', 'XPT': 'Transportadora XPT'}
    fac_map    = {}
    driver_map = {}
    seen_shp   = set()

    for _, r in df.iterrows():
        shp = str(r.get('shp_id', '')).strip()
        if shp in seen_shp:
            continue
        seen_shp.add(shp)
        fid       = str(r.get('facility_id', '')).strip()
        tipo      = tipo_label.get(str(r.get('tipo', '')), str(r.get('tipo', '')))
        place_nm  = str(r.get('place_nome', '') or '').strip() or fid
        drv_lm    = str(r.get('driver_lm', '') or '').strip()
        if drv_lm in ('None', 'nan', ''):
            drv_lm = ''
        cobrar    = cobrar_otr_map.get(shp, '')
        fkey      = f'{tipo}|{fid}'
        bpp       = float(r.get('bpp', 0) or 0)

        pkg = {
            'shp_id':        shp,
            'classificacao': str(r.get('classificacao', '')),
            'bpp':           bpp,
            'data_bpp':      str(r.get('data_bpp', '')),
            'data_dc_nex':   str(r.get('data_dc_nex', '')),
            'cobrar_otr':    cobrar,
            'driver_lm':     drv_lm,
        }

        # --- facility map ---
        if fkey not in fac_map:
            fac_map[fkey] = {
                'facility_id': fid,
                'place_nome':  place_nm,
                'tipo':        tipo,
                'gmv':         0.0,
                'cobrados':    0, 'aguardando': 0,
                'sem_retorno': 0, 'sem_status':  0,
                'drivers':     set(),
                'pacotes':     [],
            }
        fac_map[fkey]['gmv'] += bpp
        fac_map[fkey]['pacotes'].append(pkg)
        if drv_lm:
            fac_map[fkey]['drivers'].add(drv_lm)
        if   cobrar == 'Cobrado':            fac_map[fkey]['cobrados']   += 1
        elif cobrar == 'Aguardando Retorno': fac_map[fkey]['aguardando'] += 1
        elif cobrar == 'Sem Retorno':        fac_map[fkey]['sem_retorno']+= 1
        else:                                fac_map[fkey]['sem_status'] += 1

        # --- driver map ---
        if drv_lm:
            if drv_lm not in driver_map:
                driver_map[drv_lm] = {'driver_id': drv_lm, 'total': 0, 'gmv': 0.0,
                                       'facilities': set(), 'tipos': set()}
            driver_map[drv_lm]['total']      += 1
            driver_map[drv_lm]['gmv']        += bpp
            driver_map[drv_lm]['facilities'].add(place_nm or fid)
            driver_map[drv_lm]['tipos'].add(tipo)

    # --- finaliza facilities ---
    facilities = []
    for f in fac_map.values():
        n      = len(f['pacotes'])
        n_drv  = len(f['drivers'])
        # veredicto: place = muitos drivers diferentes; driver = sempre mesmo driver
        if n_drv == 0:
            veredicto = 'SEM DADO'
            v_cor     = '#4b5563'
        elif n_drv == 1:
            veredicto = 'DRIVER SUSPEITO'
            v_cor     = '#ef4444'
        elif n_drv / n >= 0.6:
            veredicto = 'PLACE SUSPEITA'
            v_cor     = '#f59e0b'
        else:
            veredicto = 'AMBOS'
            v_cor     = '#a78bfa'
        f['total']     = n
        f['n_drivers'] = n_drv
        f['veredicto'] = veredicto
        f['v_cor']     = v_cor
        f['gmv']       = round(f['gmv'], 2)
        f['drivers']   = sorted(f['drivers'])
        f['pacotes'].sort(key=lambda x: -x['bpp'])
        facilities.append(f)
    facilities.sort(key=lambda x: -x['gmv'])

    # --- finaliza drivers ---
    drivers = []
    for d in driver_map.values():
        d['gmv']        = round(d['gmv'], 2)
        d['n_fac']      = len(d['facilities'])
        d['facilities'] = sorted(d['facilities'])
        d['tipos']      = sorted(d['tipos'])
        # suspeição: aparece em muitas facilities = padrão do driver
        if d['n_fac'] >= 3:   d['nivel'] = 'ALTO';   d['n_cor'] = '#ef4444'
        elif d['n_fac'] >= 2: d['nivel'] = 'MÉDIO';  d['n_cor'] = '#f59e0b'
        else:                 d['nivel'] = 'BAIXO';  d['n_cor'] = '#6b7280'
        drivers.append(d)
    drivers.sort(key=lambda x: (-x['total'], -x['gmv']))

    total_gmv = round(sum(f['gmv'] for f in facilities), 2)
    return {
        'facilities': facilities,
        'drivers':    drivers,
        'total_pkgs': len(seen_shp),
        'total_gmv':  total_gmv,
    }

def processar_damaged_ene(df_casos, df_causas):
    import re
    if df_casos is None or df_casos.empty:
        return {
            'casos': [], 'sellers': [], 'meses': [],
            'causas': [], 'wordcloud': [],
            'total': 0, 'total_bpp': 0.0, 'total_sellers': 0,
        }
    casos = []
    for _, r in df_casos.iterrows():
        casos.append({
            'shp_id':      str(r.get('shp_id', '')),
            'seller_nome': str(r.get('seller_nome', '') or ''),
            'bpp':         float(r.get('bpp', 0) or 0),
            'data':        str(r.get('data', '')),
            'mes':         str(r.get('mes', '')),
            'item_title':  str(r.get('item_title', '') or ''),
        })
    seller_map = {}
    for c in casos:
        key = c['seller_nome'] or 'desconhecido'
        if key not in seller_map:
            seller_map[key] = {
                'seller_nome': c['seller_nome'],
                'total': 0, 'bpp': 0.0, 'meses': set(),
            }
        s = seller_map[key]
        s['total'] += 1
        s['bpp'] += c['bpp']
        s['meses'].add(c['mes'])
    sellers = sorted(seller_map.values(), key=lambda x: -x['total'])
    for s in sellers:
        s['bpp']    = round(s['bpp'], 2)
        s['meses']  = sorted(s['meses'])
        s['n_meses']= len(s['meses'])
    mes_map = {}
    for c in casos:
        m = c['mes']
        if not m:
            continue
        if m not in mes_map:
            mes_map[m] = {'mes': m, 'total': 0, 'bpp': 0.0}
        mes_map[m]['total'] += 1
        mes_map[m]['bpp'] += c['bpp']
    meses = sorted(mes_map.values(), key=lambda x: x['mes'])
    for m in meses:
        m['bpp'] = round(m['bpp'], 2)
    STOP = {
        'de','do','da','dos','das','e','o','a','os','as','em','no','na','por',
        'para','com','que','se','ao','um','uma','the','of','in','to','and','or',
        'is','at','n/a','sim','nao','não','sem','outro','outros','foi','ser','nao',
    }
    word_freq = {}
    causas = []
    if df_causas is not None and not df_causas.empty:
        for _, r in df_causas.iterrows():
            causa    = str(r.get('causa', '') or '').strip()
            causa_l2 = str(r.get('causa_l2', '') or '').strip()
            total    = int(r.get('total', 0) or 0)
            if not causa or total == 0:
                continue
            causas.append({'causa': causa, 'causa_l2': causa_l2, 'total': total})
            for txt in [causa, causa_l2]:
                if not txt or txt.lower() in ('none', 'null', 'nan', ''):
                    continue
                for w in re.split(r'[\s_/\-\+&]+', txt.upper()):
                    w = w.strip('.,;:!?()"\'')
                    if len(w) > 2 and w.lower() not in STOP:
                        word_freq[w] = word_freq.get(w, 0) + total
    wordcloud = sorted(
        [{'word': w, 'count': c} for w, c in word_freq.items()],
        key=lambda x: -x['count']
    )[:80]
    return {
        'casos':         casos[:2000],
        'sellers':       sellers[:100],
        'meses':         meses,
        'causas':        causas[:50],
        'wordcloud':     wordcloud,
        'total':         len(casos),
        'total_bpp':     round(sum(c['bpp'] for c in casos), 2),
        'total_sellers': len(sellers),
    }

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
            'data':          data,
            'data_iso':      data_iso,
            'week':          str(r.get('Week', '')).strip(),
            'solicitante':   r.get('Solicitante', '').strip(),
            'operacao':      r.get('Operação', '').strip(),
            'shp':           str(r.get('Shipment', '')).strip(),
            'produto':       str(r.get('Informe a descrição do ID', '')).strip()[:60],
            'valor':         _valor(r.get('Valor em R$', '')),
            'prioridade':    r.get('Nivel de Prioridade', '').strip(),
            'status':        status,
            'data_inicio':   r.get('Data Inicio', '').strip(),
            'data_conclusao':r.get('Data Conclusão', '').strip(),
            'sla':           str(r.get('SLA', '') or '').strip(),
            'responsavel':   r.get('Responsável', '').strip(),
            'conclusao':     r.get('Conclusão', '').strip(),
            'driver':        str(r.get('Driver', '') or '').strip(),
            'placa':         str(r.get('Placa', '') or '').strip(),
            'mlp':           str(r.get('MLP', '') or '').strip(),
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
    }

_CACHE_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_bq_cache')
_CACHE_TTL  = 4 * 60 * 60   # 4 horas (BQ — dado de fraude não muda minuto a minuto)
_SHEETS_TTL = 2 * 60 * 60   # 2 horas (Sheets read-only: CFTV, Sinistros)
_SYNC_TTL   = 2 * 60 * 60   # 2 horas (sync de status BL — evita BQ extra)

def _sheets_cache(key, fetch_fn, ttl=None):
    """Cache para leituras Google Sheets — retorna do disco se dentro do TTL."""
    if ttl is None:
        ttl = _SHEETS_TTL
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = os.path.join(_CACHE_DIR, f'sh_{key}.pkl')
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < ttl:
            with open(path, 'rb') as f:
                data = pickle.load(f)
            print(f"  cache local ({int(age/60)}min atrás)")
            return data
    data = fetch_fn()
    try:
        with open(path, 'wb') as f:
            pickle.dump(data, f)
    except Exception:
        pass
    return data

def buscar(bq, query, nome):
    os.makedirs(_CACHE_DIR, exist_ok=True)
    key  = hashlib.md5(query.encode()).hexdigest()[:12]
    path = os.path.join(_CACHE_DIR, f'{key}.pkl')
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < _CACHE_TTL:
            with open(path, 'rb') as f:
                df = pickle.load(f)
            print(f"  {nome} — cache ({int(age/60)}min atrás, {len(df)} linhas)")
            return df
    print(f"  Buscando {nome}...")
    df = bq.query(query).to_dataframe()
    print(f"  {len(df)} linhas")
    with open(path, 'wb') as f:
        pickle.dump(df, f)
    return df

# ============================================================
# PROCESSAMENTO
# ============================================================
def flt(v):
    try:    return float(v or 0)
    except: return 0.0

def prioridade(score, fraud_conf):
    if score >= 15 or fraud_conf >= 3: return 'PRIORIDADE MAXIMA'
    if score >= 8  or fraud_conf >= 2: return 'ALTA'
    if score >= 4:                     return 'MEDIA'
    return 'BAIXA'

def processar(df_score, df_dxp, df_places, df_damaged, df_shp, df_place_shp, df_status, df_routes):
    # ---- Drivers (score combinado) ----
    drivers = []
    for _, r in df_score.iterrows():
        fraude  = int(r.get('TOTAL_FRAUDE', 0) or 0)
        damaged = int(r.get('TOTAL_DAMAGED', 0) or 0)
        fraud_c = int(r.get('FRAUD_CONFIRMADO', 0) or 0)
        bpp     = flt(r.get('TOTAL_BPP', 0))
        score   = (fraude * 3) + (damaged * 1)
        drivers.append({
            'id':      str(r['DRIVER_ID']),
            'total':   int(r.get('TOTAL_INCIDENTES', 0) or 0),
            'fraude':  fraude, 'damaged': damaged,
            'fraud_c': fraud_c, 'bpp': bpp, 'score': score,
            'prio':    prioridade(score, fraud_c),
        })
    drivers.sort(key=lambda x: -x['score'])

    # ---- Driver × Place ----
    dxp = []
    for _, r in df_dxp.iterrows():
        nome = str(r.get('PLACE_NOME', '')).replace('Agncia Mercado Livre - ', '').replace('Agência Mercado Livre - ', '')
        dxp.append({
            'driver':   norm_id(r['DRIVER_ID']),
            'place':    nome,
            'total':    int(r.get('INCIDENTES_EM_COMUM', 0) or 0),
            'fraudes':  int(r.get('FRAUDES', 0) or 0),
            'damaged':  int(r.get('DAMAGED', 0) or 0),
            'bpp':      flt(r.get('TOTAL_BPP', 0)),
        })

    # ---- Places ----
    places = []
    for _, r in df_places.iterrows():
        nome = str(r.get('PLACE_NOME', '')).replace('Agncia Mercado Livre - ', '').replace('Agência Mercado Livre - ', '')
        places.append({
            'nome':    nome,
            'total':   int(r.get('TOTAL', 0) or 0),
            'bpp':     flt(r.get('TOTAL_BPP', 0)),
            'route':   int(r.get('LOST_ON_ROUTE', 0) or 0),
            'way':     int(r.get('LOST_ON_WAY', 0) or 0),
            'station': int(r.get('LOST_AT_STATION', 0) or 0),
            'ene':     int(r.get('LOST_ENE', 0) or 0),
            'fraud':   int(r.get('FRAUD_CONFIRMADO', 0) or 0),
        })

    # ---- Damaged drivers ----
    damaged = []
    for _, r in df_damaged.iterrows():
        damaged.append({
            'id':      str(r['DRIVER_ID']),
            'total':   int(r.get('TOTAL_DAMAGED', 0) or 0),
            'bpp':     flt(r.get('TOTAL_BPP', 0)),
            'route':   int(r.get('DAMAGED_ON_ROUTE', 0) or 0),
            'station': int(r.get('DAMAGED_AT_STATION', 0) or 0),
            'ene':     int(r.get('DAMAGED_ENE', 0) or 0),
        })

    # ---- Rotas dos drivers (transportadora + última rota) ----
    routes_map = {}
    for _, r in df_routes.iterrows():
        did  = norm_id(r.get('DRIVER_ID', ''))
        dias = int(r.get('DIAS_SEM_ROTA', -1) or -1)
        if did:
            # mantém o registro com menos dias (mais recente)
            if did not in routes_map or dias < routes_map[did]['dias_sem_rota']:
                routes_map[did] = {
                'transportadora': str(r.get('TRANSPORTADORA', '') or 'N/A'),
                'ultima_rota':    str(r.get('ULTIMA_ROTA', '') or ''),
                'dias_sem_rota':  dias,
                'rotas_ano':      int(r.get('ROTAS_ANO', 0) or 0),
            }

    # ---- Status dos drivers ----
    status_map = {}
    bloqueados = []
    for _, r in df_status.iterrows():
        did = norm_id(r.get('DRIVER_ID', ''))
        if not did: continue
        status    = str(r.get('STATUS',    '') or '')
        substatus = str(r.get('SUBSTATUS', '') or '')
        lealdade  = str(r.get('CATEGORIA', '') or 'N/A')
        # Considera "removido do mercado" se: blocked, inactive/fraud_prevention, ou removed
        removido = (
            status == 'blocked' or
            (status == 'inactive' and 'fraud' in substatus.lower()) or
            status == 'removed'
        )
        info = {
            'status':    status,
            'substatus': substatus,
            'lealdade':  lealdade,
            'removido':  removido,
        }
        status_map[did] = info
        if removido:
            bloqueados.append({'id': did, **info})

    # Enriquece drivers com status, transportadora e atividade
    drivers_ativos     = []
    drivers_bloqueados = []
    for d in drivers:
        st  = status_map.get(d['id'], {})
        rt  = routes_map.get(d['id'], {})
        dias = rt.get('dias_sem_rota', -1)

        d['status']         = st.get('status', '')
        d['lealdade']       = st.get('lealdade', 'N/A')
        d['data_ativacao']  = st.get('data_ativacao', '')
        d['transportadora'] = rt.get('transportadora', 'N/A')
        d['ultima_rota']    = rt.get('ultima_rota', '—')
        d['dias_sem_rota']  = dias
        d['rotas_ano']      = rt.get('rotas_ano', 0)

        # Determina atividade
        if st.get('removido', False):
            # Label conforme substatus
            sub = st.get('substatus','')
            if st.get('status') == 'blocked':
                d['atividade'] = 'Bloqueado'
            elif 'fraud' in sub.lower():
                d['atividade'] = 'Inativo por Fraude'
            else:
                d['atividade'] = 'Removido'
            d['ativ_cor'] = '#ef4444'
            drivers_bloqueados.append(d)
        elif dias < 0:
            d['atividade']    = 'Sem dados'
            d['ativ_cor']     = '#4b5563'
            drivers_ativos.append(d)
        elif dias <= 30:
            d['atividade']    = 'Ativo'
            d['ativ_cor']     = '#10b981'
            drivers_ativos.append(d)
        elif dias <= 90:
            d['atividade']    = 'Em observação'
            d['ativ_cor']     = '#f59e0b'
            drivers_ativos.append(d)
        else:
            d['atividade']    = 'Inativo'
            d['ativ_cor']     = '#ef4444'
            drivers_ativos.append(d)

    # ---- SHP IDs por (driver, place) — para Driver × Place correto ----
    shp_dxp = {}   # {(driver_id, place_nome): [shps]}
    for _, r in df_place_shp.iterrows():
        did  = norm_id(r.get('DRIVER_ID', ''))
        nome = str(r.get('PLACE_NOME', '')).replace('Agncia Mercado Livre - ','').replace('Agência Mercado Livre - ','')
        if not did or not nome: continue
        key = (did, nome)
        if key not in shp_dxp:
            shp_dxp[key] = []
        shp_dxp[key].append({
            'id':    str(r.get('SHP_ID', '')),
            'class': str(r.get('CLASSIFICACAO', '')),
            'bpp':   flt(r.get('BPP', 0)),
            'data':  str(r.get('DATA', '')),
        })

    # ---- SHP IDs por place ----
    shp_por_place = {}
    for _, r in df_place_shp.iterrows():
        nome = str(r.get('PLACE_NOME', '')).replace('Agncia Mercado Livre - ', '').replace('Agência Mercado Livre - ', '')
        if not nome: continue
        if nome not in shp_por_place:
            shp_por_place[nome] = []
        shp_por_place[nome].append({
            'id':      str(r.get('SHP_ID', '')),
            'driver':  str(r.get('DRIVER_ID', '') or '—'),
            'class':   str(r.get('CLASSIFICACAO', '')),
            'bpp':     flt(r.get('BPP', 0)),
            'data':    str(r.get('DATA', '')),
        })

    # ---- SHP IDs por driver ----
    shp_por_driver = {}
    for _, r in df_shp.iterrows():
        did = str(r.get('DRIVER_ID', ''))
        if not did: continue
        if did not in shp_por_driver:
            shp_por_driver[did] = []
        shp_por_driver[did].append({
            'id':    str(r.get('SHP_ID', '')),
            'class': str(r.get('CLASSIFICACAO', '')),
            'bpp':   flt(r.get('BPP', 0)),
            'data':  str(r.get('DATA', '')),
            'semana':str(r.get('SEMANA', '')),
        })

    # Adiciona os SHP IDs a cada driver
    for d in drivers:
        d['shps'] = shp_por_driver.get(d['id'], [])

    # Meses ativos por entidade (para filtro por período cross-tab)
    for d in drivers:
        d['months'] = ' '.join(sorted({_ym(s['data']) for s in d['shps'] if s.get('data')}))
    for r in dxp:
        r['months'] = ' '.join(sorted({_ym(s['data']) for s in shp_dxp.get((r['driver'], r['place']), []) if s.get('data')}))
    for p in places:
        p['months'] = ' '.join(sorted({_ym(s['data']) for s in shp_por_place.get(p['nome'], []) if s.get('data')}))
    for dmg in damaged:
        dmg['months'] = ' '.join(sorted({_ym(s['data']) for s in shp_por_driver.get(dmg['id'], []) if s.get('data') and 'DAMAGED' in s.get('class','')}))
    # Agrega damaged por mês por driver (para recalcular totais no JS ao filtrar período)
    for dmg in damaged:
        monthly = {}
        for s in shp_por_driver.get(dmg['id'], []):
            if 'DAMAGED' not in s.get('class', ''):
                continue
            ym = _ym(s['data'])
            if not ym:
                continue
            if ym not in monthly:
                monthly[ym] = {'total': 0, 'bpp': 0.0, 'route': 0, 'station': 0, 'ene': 0}
            monthly[ym]['total'] += 1
            monthly[ym]['bpp']    = round(monthly[ym]['bpp'] + s.get('bpp', 0.0), 2)
            cls = s.get('class', '')
            if 'ON ROUTE' in cls:
                monthly[ym]['route'] += 1
            elif 'AT STATION' in cls:
                monthly[ym]['station'] += 1
            elif 'ENE' in cls:
                monthly[ym]['ene'] += 1
        dmg['monthly'] = monthly
    # Ranking de transportadoras por damaged (usa routes_map já construído)
    _transp_dmg_agg = {}
    for dmg in damaged:
        rt     = routes_map.get(dmg['id'], {})
        transp = (rt.get('transportadora') or 'N/A').strip() or 'N/A'
        if transp not in _transp_dmg_agg:
            _transp_dmg_agg[transp] = {'total': 0, 'bpp': 0.0, 'drivers': set()}
        _transp_dmg_agg[transp]['total'] += dmg['total']
        _transp_dmg_agg[transp]['bpp']    = round(_transp_dmg_agg[transp]['bpp'] + dmg['bpp'], 2)
        _transp_dmg_agg[transp]['drivers'].add(dmg['id'])
    transp_damaged_ranking = sorted(
        [{'transp': k, 'total': v['total'], 'bpp': v['bpp'], 'n_drivers': len(v['drivers'])}
         for k, v in _transp_dmg_agg.items()],
        key=lambda x: -x['total']
    )[:15]

    # Agrega KPIs por mês — mesmo escopo dos top-60 (consistente com os cards)
    ids_top60   = {d['id'] for d in drivers}
    monthly_agg = {}
    for did, shps in shp_por_driver.items():
        if did not in ids_top60:
            continue
        for s in shps:
            ym = _ym(s['data'])
            if not ym:
                continue
            if ym not in monthly_agg:
                monthly_agg[ym] = {'fraudes': 0, 'damaged': 0, 'bpp': 0.0, 'total': 0}
            cls = s.get('class', '')
            monthly_agg[ym]['total'] += 1
            monthly_agg[ym]['bpp']   += s.get('bpp', 0.0)
            if any(x in cls for x in ('LOST', 'FRAUD')):
                monthly_agg[ym]['fraudes'] += 1
            elif 'DAMAGED' in cls:
                monthly_agg[ym]['damaged'] += 1

    # Ranking de drivers por mês
    monthly_dr_raw = {}
    for did, shps in shp_por_driver.items():
        for s in shps:
            ym = _ym(s['data'])
            if not ym:
                continue
            if ym not in monthly_dr_raw:
                monthly_dr_raw[ym] = {}
            if did not in monthly_dr_raw[ym]:
                monthly_dr_raw[ym][did] = {'fraudes': 0, 'damaged': 0, 'bpp': 0.0}
            cls = s.get('class', '')
            monthly_dr_raw[ym][did]['bpp'] = round(monthly_dr_raw[ym][did]['bpp'] + s.get('bpp', 0.0), 2)
            if any(x in cls for x in ('LOST', 'FRAUD')):
                monthly_dr_raw[ym][did]['fraudes'] += 1
            elif 'DAMAGED' in cls:
                monthly_dr_raw[ym][did]['damaged'] += 1
    monthly_dr = {
        ym: sorted(
            [{'id': did, **v, 'score': v['fraudes']*3 + v['damaged']}
             for did, v in drv.items() if v['fraudes'] + v['damaged'] > 0],
            key=lambda x: -x['score']
        )[:10]
        for ym, drv in monthly_dr_raw.items()
    }

    # ---- Conjunto de IDs que aparecem nas duas análises ----
    ids_fraude   = {d['id'] for d in drivers if d['fraude'] > 0}
    ids_damaged  = {d['id'] for d in damaged}
    ids_cruzados = ids_fraude & ids_damaged

    # ---- Totais ----
    total_fraudes = sum(d['fraude']  for d in drivers)
    total_damaged = sum(d['damaged'] for d in drivers)
    total_bpp     = sum(d['bpp']     for d in drivers)
    criticos      = sum(1 for d in drivers if d['prio'] in ('PRIORIDADE MAXIMA', 'ALTA'))

    # ---- Dados para gráficos ----
    top10_labels = [d['id'] for d in drivers[:10]]
    top10_fraude = [d['fraude']  for d in drivers[:10]]
    top10_damage = [d['damaged'] for d in drivers[:10]]
    top10_fraud_c= [d['fraud_c'] for d in drivers[:10]]

    top10_places_labels = [p['nome'][:25]+'…' if len(p['nome'])>25 else p['nome'] for p in places[:10]]
    top10_places_vals   = [p['total'] for p in places[:10]]

    return {
        'gerado':    datetime.now().strftime('%d/%m/%Y %H:%M'),
        'ano':       ANO_INICIO[:4],
        'mes_atual': datetime.now().strftime('%Y-%m'),
        'drivers':   drivers,
        'dxp':       dxp,
        'places':    places,
        'damaged':   damaged,
        'cruzados':          ids_cruzados,
        'shp_por_driver':    shp_por_driver,
        'shp_por_place':     shp_por_place,
        'shp_dxp':           shp_dxp,
        'drivers_ativos':    drivers_ativos,
        'drivers_bloqueados':drivers_bloqueados,
        'total_bloqueados':  len(drivers_bloqueados),
        'bl':                {},  # preenchido no main após carregar_block_list
        # Totais
        'total_fraudes': total_fraudes,
        'total_damaged': total_damaged,
        'total_bpp':     total_bpp,
        'criticos':      criticos,
        'total_places':  len(places),
        # Charts
        'top10_labels':  top10_labels,
        'top10_fraude':  top10_fraude,
        'top10_damage':  top10_damage,
        'top10_fraud_c': top10_fraud_c,
        'top10_places_labels': top10_places_labels,
        'top10_places_vals':   top10_places_vals,
        'monthly_agg':         monthly_agg,
        'monthly_dr':          monthly_dr,
        'acumulo_bloqueio': processar_acumulo_bloqueio(drivers, shp_por_driver, days=90),
        'acumulo_por_periodo': {
            '30':  processar_acumulo_bloqueio(drivers, shp_por_driver, days=30),
            '60':  processar_acumulo_bloqueio(drivers, shp_por_driver, days=60),
            '90':  processar_acumulo_bloqueio(drivers, shp_por_driver, days=90),
            '180': processar_acumulo_bloqueio(drivers, shp_por_driver, days=180),
        },
        'transp_damaged_ranking': transp_damaged_ranking,
        'driver_transp': {str(dmg['id']): ((routes_map.get(dmg['id'], {}) or {}).get('transportadora') or 'N/A').strip() or 'N/A' for dmg in damaged},
    }

# ============================================================
# HELPERS HTML
# ============================================================
def prio_badge(p):
    cores = {
        'PRIORIDADE MAXIMA': ('#7f1d1d','#fca5a5'),
        'ALTA':              ('#7c2d12','#fdba74'),
        'MEDIA':             ('#713f12','#fde68a'),
        'BAIXA':             ('#1f2937','#9ca3af'),
    }
    bg, fg = cores.get(p, ('#1f2937','#9ca3af'))
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{p}</span>'

MELI_URL = 'https://shipping-bo.adminml.com/sauron/shipments/shipment'

def lealdade_badge(l):
    cores = {'Bronze':'#cd7f32','Prata':'#9ca3af','Ouro':'#f59e0b','Platina':'#a78bfa','N/A':'#374151'}
    cor = cores.get(l, '#374151')
    return f'<span style="background:{cor};color:#fff;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:600">{l}</span>'

def rows_drivers(drivers, cruzados):
    out = ''
    for d in drivers:
        cruz   = '⚠️' if d['id'] in cruzados else ''
        row_id = f'dr_{d["id"]}'
        # linhas dos SHP IDs
        shp_rows = ''
        for s in d.get('shps', []):
            cls_cor = '#ef4444' if 'FRAUD' in s['class'] else '#f59e0b' if 'DAMAGED' in s['class'] else '#94a3b8'
            raw_dt = s.get('data', '')
            if '/' in raw_dt and len(raw_dt) >= 10:
                parts = raw_dt.split('/')
                shp_ym = f'{parts[2][:4]}-{parts[1]}'
            else:
                shp_ym = raw_dt[:7]
            shp_rows += f'''<tr data-ym="{shp_ym}" style="background:#060c1a">
                <td colspan="2" style="padding:6px 16px 6px 40px;font-family:monospace;font-size:12px">
                  <a href="{MELI_URL}/{s["id"]}" target="_blank" style="color:#60a5fa;text-decoration:none">{s["id"]}</a>
                </td>
                <td style="color:{cls_cor};font-size:11px">{s["class"]}</td>
                <td style="color:#10b981;font-size:12px">${s["bpp"]:,.2f}</td>
                <td style="color:#6b7280;font-size:11px">{s["data"]}</td>
                <td colspan="2" style="color:#4b5563;font-size:11px">{s["semana"]}</td>
            </tr>'''
        toggle = f'onclick="toggleDriver(\'{row_id}\')" style="cursor:pointer"' if d['shps'] else ''
        seta   = f' <span id="arrow_{row_id}" style="font-size:10px;color:#4b5563">▶ {len(d["shps"])} pacotes</span>' if d['shps'] else ''
        dias     = d.get('dias_sem_rota', -1)
        dias_str = f'{dias}d' if dias >= 0 else '—'
        dias_cor = '#10b981' if 0 <= dias <= 30 else '#f59e0b' if dias <= 90 else '#ef4444' if dias > 0 else '#4b5563'
        ativ_cor = d.get('ativ_cor', '#4b5563')
        leal = d.get('lealdade','N/A')
        leal_html = lealdade_badge(leal) if leal not in ('N/A','') else '<span style="color:#374151;font-size:10px">Não se enquadra</span>'
        out += f'''<tr {toggle}
            data-id="{d["id"]}"
            data-transp="{d.get("transportadora","").lower()}"
            data-ativ="{d.get("atividade","").lower()}"
            data-months="{d.get("months","")}"
            data-prio="{d.get("prio","").lower()}"
            data-cruzado="{'1' if d['id'] in cruzados else '0'}">
            <td style="font-weight:700;color:#f9fafb">{d["id"]}{seta} {cruz}</td>
            <td>{prio_badge(d["prio"])}</td>
            <td style="font-size:11px;color:#9ca3af">{d.get("transportadora","—")}</td>
            <td>{leal_html}</td>
            <td>
              <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{ativ_cor};margin-right:5px"></span>
              <span style="font-size:11px;color:{ativ_cor}">{d.get("atividade","—")}</span>
              <span style="font-size:10px;color:{dias_cor};margin-left:4px">({dias_str})</span>
            </td>
            <td style="font-size:11px;color:#6b7280">{d.get("ultima_rota","—")}</td>
            <td style="text-align:center;font-weight:700;color:#f9fafb">{d["score"]}</td>
            <td style="text-align:center;color:#ef4444;font-weight:600">{d["fraude"]}</td>
            <td style="text-align:center;color:#f59e0b">{d["damaged"]}</td>
            <td style="text-align:center;color:#ef4444">{d["fraud_c"]}</td>
            <td style="color:#10b981;font-weight:600">${d["bpp"]:,.2f}</td>
        </tr>
        <tbody id="{row_id}" style="display:none">{shp_rows}</tbody>'''
    return out

def status_bl_badge(s):
    cores = {'Bloqueado':('#064e3b','#4ade80'), 'Solicitado':('#1e3a5f','#60a5fa'),
             'Monitorado':('#713f12','#fde68a'), 'Recusado':('#7f1d1d','#fca5a5'),
             'Inativo':('#1f2937','#6b7280')}
    bg, fg = cores.get(s, ('#1f2937','#9ca3af'))
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{s}</span>'

def rows_block_list(rows):
    from datetime import datetime as _dtbl
    def _iso(d):
        try: return _dtbl.strptime(d.strip(), '%d/%m/%Y').strftime('%Y-%m-%d')
        except: return ''

    out = ''
    for idx, r in enumerate(rows):
        did      = r['driver_id']
        link     = f'https://envios.adminml.com/logistics/drivers-management/drivers/{did}' if did else '#'
        data_iso = _iso(r["data"]) if r["data"] else ''
        n_sol    = r.get('n_solicitacoes', 1)
        row_id   = f'blh_{idx}'

        # badge de contagem de solicitações
        badge_hist = ''
        if n_sol > 1:
            badge_hist = f' <span title="Ver histórico" style="background:#1e3a5f;color:#60a5fa;font-size:10px;font-weight:700;padding:1px 6px;border-radius:10px;cursor:pointer" onclick="toggleBl(\'{row_id}\')">{n_sol}x</span>'

        # célula do driver: com ou sem expand
        if n_sol > 1:
            driver_cell = f'''<td style="font-weight:700;cursor:pointer" onclick="toggleBl('{row_id}')">
              <span id="{row_id}_arrow" style="color:#4b5563;margin-right:3px;font-size:10px">▶</span>
              <a href="{link}" target="_blank" style="color:#60a5fa;text-decoration:none;font-family:monospace;font-size:12px" onclick="event.stopPropagation()">{did or "—"}</a>{badge_hist}
            </td>'''
        else:
            driver_cell = f'''<td style="font-weight:700">
              <a href="{link}" target="_blank" style="color:#60a5fa;text-decoration:none;font-family:monospace;font-size:12px">{did or "—"}</a>
            </td>'''

        search_txt = f'{did} {r["nome"]}'.lower()
        out += f'''<tr class="bl-row" data-data="{data_iso}" data-status="{r["status"]}" data-transp="{r["mlp"]}" data-usd="{r["usd"]}" data-search="{search_txt}">
            {driver_cell}
            <td style="font-size:12px;color:#d1d5db">{r["nome"] or "—"}</td>
            <td style="font-size:11px;color:#9ca3af">{r["mlp"]}</td>
            <td style="font-size:11px;color:#6b7280">{r["placa"] or "—"}</td>
            <td style="text-align:center">{r["shp"] or "—"}</td>
            <td style="color:#10b981;font-weight:600">${r["usd"]:,.2f}</td>
            <td>{status_bl_badge(r["status"])}</td>
            <td style="font-size:11px;color:#9ca3af">{r["motivo"] or "—"}</td>
            <td style="font-size:11px;color:#6b7280">{r["data"] or "—"}</td>
            <td style="font-size:11px;color:#6b7280">Sem {r["semana"]}</td>
        </tr>'''

        # subrow com histórico completo
        if n_sol > 1:
            hist_rows = ''
            for i, h in enumerate(r.get('historico', []), 1):
                hist_rows += f'''<tr style="background:#060c1a">
                    <td style="padding:4px 8px;font-size:11px;color:#6b7280;text-align:center">#{i}</td>
                    <td style="padding:4px 8px;font-size:11px;color:#9ca3af">{h["data"] or "—"}</td>
                    <td style="padding:4px 8px;font-size:11px">Sem {h["semana"]}</td>
                    <td style="padding:4px 8px">{status_bl_badge(h["status"])}</td>
                    <td style="padding:4px 8px;font-size:11px;color:#6b7280">{h["motivo"] or "—"}</td>
                    <td style="padding:4px 8px;font-size:11px;color:#10b981">${h["usd"]:,.2f}</td>
                </tr>'''
            out += f'''<tr id="{row_id}" class="bl-hist-row" style="display:none">
                <td colspan="10" style="padding:0 0 6px 32px;background:#07111e">
                    <table style="width:100%;border-collapse:collapse;border:1px solid #1e3a5f;border-radius:4px">
                        <thead><tr style="background:#0a1929">
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:center;width:32px">#</th>
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:left">Data</th>
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:left">Semana</th>
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:left">Status</th>
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:left">Motivo</th>
                            <th style="padding:4px 8px;font-size:10px;color:#4b5563;text-align:left">USD$</th>
                        </tr></thead>
                        <tbody>{hist_rows}</tbody>
                    </table>
                </td>
            </tr>'''
    return out

def rows_cftv(rows):
    STATUS_COR  = {'Concluído':'#10b981','Em Andamento':'#3b82f6','SLA Vencido':'#ef4444'}
    PRIO_COR    = {'Alto':'#ef4444','Moderado':'#f59e0b'}
    CONCL_COR   = {'Conclusivo':'#10b981','Inconclusivo':'#ef4444'}
    out = ''
    for r in rows:
        st_cor  = STATUS_COR.get(r['status'], '#9ca3af')
        pr_cor  = PRIO_COR.get(r['prioridade'], '#9ca3af')
        co_cor  = CONCL_COR.get(r['conclusao'], '#6b7280')
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

def rows_historico_bloqueios(bloqueados):
    if not bloqueados:
        return ''
    rows = ''
    for b in bloqueados:
        rows += f'''<tr style="background:#051505">
            <td style="font-weight:700;color:#4ade80">{b["id"]}</td>
            <td>{lealdade_badge(b.get("lealdade","N/A"))}</td>
            <td style="color:#6b7280;font-size:11px">{b.get("atividade",b.get("substatus",""))}</td>
            <td style="color:#9ca3af;font-size:12px">{b.get("primeira_fraude","")}</td>
            <td style="color:#9ca3af;font-size:12px">{b.get("ultima_fraude","")}</td>
            <td style="text-align:center;font-weight:700;color:#f9fafb">{b.get("fraude",0) + b.get("damaged",0)}</td>
            <td style="color:#10b981">${b.get("bpp",0):,.2f}</td>
        </tr>'''
    return f'''<div class="tbl-wrap" style="border-color:#166534">
    <div class="tbl-title" style="color:#4ade80">Drivers Bloqueados — Removidos do Mercado ({len(bloqueados)})</div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th>Driver ID</th><th>Categoria</th><th>Substatus</th>
        <th>1ª Fraude</th><th>Última Fraude</th><th>Total Incidentes</th><th>BPP Total</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
  </div>'''

def rows_dxp(dxp, shp_por_driver, shp_dxp):
    out = ''
    for i, r in enumerate(dxp):
        alert  = r['total'] >= 5
        bg     = 'background:#1a0a0a' if alert else ''
        row_id = f'dxp_{i}'
        # SHP IDs que cruzam especificamente este driver + este place
        shps = shp_dxp.get((r['driver'], r['place']), [])
        shp_rows = ''
        for s in shps:
            cls_cor = '#ef4444' if 'FRAUD' in s['class'] else '#f59e0b' if 'DAMAGED' in s['class'] else '#94a3b8'
            shp_rows += f'''<tr style="background:#060c1a">
                <td colspan="2" style="padding:6px 16px 6px 40px;font-family:monospace;font-size:12px">
                  <a href="{MELI_URL}/{s["id"]}" target="_blank" style="color:#60a5fa;text-decoration:none">{s["id"]}</a>
                </td>
                <td style="color:{cls_cor};font-size:11px" colspan="2">{s["class"]}</td>
                <td style="color:#10b981;font-size:12px">${s["bpp"]:,.2f}</td>
                <td style="color:#6b7280;font-size:11px">{s["data"]}</td>
            </tr>'''
        seta   = f' <span id="arrow_dxp_{i}" style="font-size:10px;color:#4b5563">▶ {len(shps)} pacotes</span>' if shps else ''
        toggle = f'onclick="toggleDriver(\'dxp_{i}\')" style="cursor:pointer"' if shps else ''
        out += f'''<tr style="{bg}" {toggle} data-months="{r.get("months","")}">
            <td style="font-weight:700;color:{"#fca5a5" if alert else "#f9fafb"}">{r["driver"]}{seta}</td>
            <td>{r["place"]}</td>
            <td style="text-align:center;font-weight:800;color:{"#ef4444" if alert else "#f9fafb"}">{r["total"]}</td>
            <td style="text-align:center;color:#ef4444">{r["fraudes"]}</td>
            <td style="text-align:center;color:#f59e0b">{r["damaged"]}</td>
            <td style="color:#10b981">${r["bpp"]:,.2f}</td>
        </tr>
        <tbody id="dxp_{i}" style="display:none">{shp_rows}</tbody>'''
    return out

def rows_places(places, shp_por_place):
    out = ''
    for i, p in enumerate(places):
        row_id = f'pl_{i}'
        shps   = shp_por_place.get(p['nome'], [])
        shp_rows = ''
        for s in shps:
            cls_cor = '#ef4444' if 'FRAUD' in s['class'] else '#94a3b8'
            shp_rows += f'''<tr style="background:#060c1a">
                <td style="padding:6px 16px 6px 36px;font-family:monospace;font-size:12px">
                  <a href="{MELI_URL}/{s["id"]}" target="_blank" style="color:#60a5fa;text-decoration:none">{s["id"]}</a>
                </td>
                <td style="color:#6b7280;font-size:11px">Driver: {s["driver"]}</td>
                <td style="color:{cls_cor};font-size:11px" colspan="2">{s["class"]}</td>
                <td style="color:#10b981;font-size:12px">${s["bpp"]:,.2f}</td>
                <td style="color:#6b7280;font-size:11px" colspan="3">{s["data"]}</td>
            </tr>'''
        seta   = f' <span id="arrow_pl_{i}" style="font-size:10px;color:#4b5563">▶ {len(shps)} ids</span>' if shps else ''
        toggle = f'onclick="toggleDriver(\'pl_{i}\')" style="cursor:pointer"' if shps else ''
        out += f'''<tr {toggle} data-months="{p.get("months","")}">
            <td style="font-weight:600;color:#f9fafb">{p["nome"]}{seta}</td>
            <td style="text-align:center;font-weight:700;color:#f9fafb">{p["total"]}</td>
            <td style="color:#10b981">${p["bpp"]:,.2f}</td>
            <td style="text-align:center;color:#ef4444">{p["route"]}</td>
            <td style="text-align:center;color:#60a5fa">{p["way"]}</td>
            <td style="text-align:center;color:#a78bfa">{p["station"]}</td>
            <td style="text-align:center;color:#94a3b8">{p["ene"]}</td>
            <td style="text-align:center;color:#f87171;font-weight:700">{p["fraud"]}</td>
        </tr>
        <tbody id="pl_{i}" style="display:none">{shp_rows}</tbody>'''
    return out

def rows_driver_ranking(dc_nex_data):
    """Tabela de ranking de drivers suspeitos (DRIVER_LM)."""
    drivers = dc_nex_data.get('drivers', [])
    if not drivers:
        return ''
    rows = ''
    for i, d in enumerate(drivers[:20], 1):
        facs_txt = ', '.join(d['facilities'][:3])
        if len(d['facilities']) > 3:
            facs_txt += f' +{len(d["facilities"])-3}'
        rows += f'''<tr style="border-top:1px solid #1a2035">
            <td style="padding:7px 10px;text-align:center;color:#6b7280;font-size:11px">{i}</td>
            <td style="padding:7px 10px;font-family:monospace;font-weight:700;color:#f9fafb">{d["driver_id"]}</td>
            <td style="padding:7px 10px;text-align:center;font-weight:700;color:#f87171">{d["total"]}</td>
            <td style="padding:7px 10px;text-align:right;font-weight:700;color:#10b981">${d["gmv"]:,.2f}</td>
            <td style="padding:7px 10px;text-align:center;font-size:11px;color:#9ca3af">{d["n_fac"]}</td>
            <td style="padding:7px 10px;font-size:11px;color:#9ca3af;max-width:250px">{facs_txt}</td>
            <td style="padding:7px 10px;text-align:center">
              <span style="background:{d["n_cor"]};color:#fff;padding:2px 9px;border-radius:10px;font-size:10px;font-weight:700">{d["nivel"]}</span>
            </td>
        </tr>'''
    return f'''<div class="tbl-wrap" style="margin-bottom:18px">
    <div class="tbl-title" style="color:#a78bfa">
      <i data-lucide="user-x" width="14" height="14" style="color:#a78bfa;margin-right:6px;vertical-align:middle"></i>
      Ranking de Drivers Suspeitos (Last Mile NEX) — {len(drivers)} driver(s) identificados
      <span style="font-size:10px;font-weight:400;color:#6b7280;float:right">Nível ALTO = mesmo driver em 3+ facilities diferentes</span>
    </div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th style="text-align:center">#</th>
        <th>Driver ID</th>
        <th style="text-align:center">Pacotes</th>
        <th style="text-align:right">GMV</th>
        <th style="text-align:center">Facilities</th>
        <th>Places</th>
        <th style="text-align:center">Nível</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
  </div>'''

def rows_dc_nex(dc_nex_data):
    MELI_URL = 'https://shipping-bo.adminml.com/sauron/shipments/shipment'
    tipo_cor  = {'NEX': '#f59e0b', 'DC': '#60a5fa', 'Transportadora XPT': '#a78bfa'}
    tipo_bg   = {'NEX': 'rgba(245,158,11,.12)', 'DC': 'rgba(96,165,250,.12)', 'Transportadora XPT': 'rgba(167,139,250,.12)'}
    facilities = dc_nex_data.get('facilities', [])
    if not facilities:
        return '<tr><td colspan="5" style="text-align:center;color:#4b5563;padding:32px">Nenhum pacote encontrado</td></tr>'
    out = ''
    for i, f in enumerate(facilities):
        row_id  = f'dcnex_{i}'
        tp_cor  = tipo_cor.get(f['tipo'], '#9ca3af')
        tp_bg   = tipo_bg.get(f['tipo'], 'transparent')
        n       = f['total']
        n_drv   = f.get('n_drivers', 0)
        verd    = f.get('veredicto', '—')
        v_cor   = f.get('v_cor', '#4b5563')
        place_nm = f.get('place_nome', f['facility_id'])
        # sub-rows (pacotes individuais)
        sub = ''
        for p in f['pacotes']:
            cls_cor = '#ef4444' if 'FRAUD' in p['classificacao'] else '#94a3b8'
            drv_txt = p.get('driver_lm', '') or '—'
            sub += f'''<tr style="background:#060c1a">
                <td style="padding:6px 10px 6px 32px;font-family:monospace;font-size:12px">
                  <a href="{MELI_URL}/{p['shp_id']}" target="_blank" style="color:#60a5fa;text-decoration:none">{p['shp_id']}</a>
                </td>
                <td style="padding:6px 10px;font-weight:700;color:#10b981;text-align:right">${p['bpp']:,.2f}</td>
                <td style="padding:6px 10px;font-size:11px;color:{cls_cor}">{p['classificacao']}</td>
                <td style="padding:6px 10px;font-size:11px;color:#6b7280">{p['data_dc_nex']}</td>
                <td style="padding:6px 10px;font-size:11px;font-family:monospace;color:#c084fc">{drv_txt}</td>
            </tr>'''
        seta = f'<span id="arrow_{row_id}" style="font-size:10px;color:#4b5563;margin-left:6px">▶ {n} pacotes</span>'
        drv_info = f'<span style="font-size:10px;color:#9ca3af;margin-left:6px">{n_drv} driver(s)</span>'
        out += f'''<tr onclick="toggleDriver('{row_id}')" style="cursor:pointer;border-top:1px solid #1a2035;background:{tp_bg}">
            <td style="padding:10px 12px;font-weight:700;color:{tp_cor}">
              {f['tipo']} {seta}
            </td>
            <td style="padding:10px 12px;font-size:11px;color:#9ca3af;max-width:180px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="{place_nm}">{place_nm}</td>
            <td style="padding:10px 12px;font-weight:700;color:#10b981;text-align:right">${f['gmv']:,.2f}</td>
            <td style="padding:10px 12px;text-align:center;font-weight:700;color:#f9fafb">{n}</td>
            <td style="padding:10px 12px;text-align:center">
              <span style="background:{v_cor};color:#fff;padding:2px 8px;border-radius:10px;font-size:9px;font-weight:700">{verd}</span>
              {drv_info}
            </td>
        </tr>
        <tbody id="{row_id}" style="display:none">{sub}</tbody>'''
    return out

def rows_damaged(damaged, cruzados_fraude, shp_por_driver, driver_transp_map=None):
    out = ''
    for i, d in enumerate(damaged):
        cruz   = ' ⚠️' if d['id'] in cruzados_fraude else ''
        row_id = f'dmg_{i}'
        _transp = (driver_transp_map or {}).get(str(d['id']), 'N/A')
        # filtra só DAMAGED
        shps   = [s for s in shp_por_driver.get(d['id'], []) if 'DAMAGED' in s['class']]
        shp_rows = ''
        for s in shps:
            cls_cor = '#f59e0b'
            shp_rows += f'''<tr style="background:#060c1a">
                <td style="padding:6px 16px 6px 36px;font-family:monospace;font-size:12px" colspan="2">
                  <a href="{MELI_URL}/{s["id"]}" target="_blank" style="color:#60a5fa;text-decoration:none">{s["id"]}</a>
                </td>
                <td style="color:{cls_cor};font-size:11px" colspan="2">{s["class"]}</td>
                <td style="color:#10b981;font-size:12px">${s["bpp"]:,.2f}</td>
                <td style="color:#6b7280;font-size:11px">{s["data"]}</td>
                <td></td>
            </tr>'''
        seta   = f' <span id="arrow_dmg_{i}" style="font-size:10px;color:#4b5563">▶ {len(shps)} ids</span>' if shps else ''
        toggle = f'onclick="toggleDriver(\'dmg_{i}\')" style="cursor:pointer"' if shps else ''
        out += f'''<tr {toggle} data-months="{d.get("months","")}" data-driver="{d["id"]}" data-transp="{_transp}">
            <td style="font-weight:700;color:#f9fafb">{d["id"]}{seta}{cruz}</td>
            <td class="dmg-total" style="text-align:center;font-weight:700;color:#f59e0b">{d["total"]}</td>
            <td class="dmg-bpp" style="color:#10b981">${d["bpp"]:,.2f}</td>
            <td class="dmg-route" style="text-align:center">{d["route"]}</td>
            <td class="dmg-station" style="text-align:center">{d["station"]}</td>
            <td class="dmg-ene" style="text-align:center">{d["ene"]}</td>
            <td class="dmg-transp" style="color:#9ca3af;font-size:12px;white-space:nowrap">{_transp}</td>
        </tr>
        <tbody id="dmg_{i}" style="display:none">{shp_rows}</tbody>'''
    return out

def _transp_damaged_html(ranking):
    if not ranking:
        return ''
    rows = ''
    max_total = ranking[0]['total'] if ranking else 1
    for i, r in enumerate(ranking, 1):
        bar_w = max(4, round(r['total'] / max_total * 100))
        color = '#f87171' if i <= 3 else '#f59e0b' if i <= 7 else '#6b7280'
        rows += f'''<tr>
            <td style="text-align:center;color:#4b5563;font-size:11px">{i}</td>
            <td style="font-weight:600;color:#f9fafb">
              {r["transp"]}
              <div style="height:3px;background:#1a2035;border-radius:2px;margin-top:3px;width:140px">
                <div style="height:3px;background:{color};border-radius:2px;width:{bar_w}%"></div>
              </div>
            </td>
            <td style="text-align:center;font-weight:700;color:{color}">{r["total"]}</td>
            <td style="color:#10b981">${r["bpp"]:,.2f}</td>
            <td style="text-align:center;color:#9ca3af">{r["n_drivers"]}</td>
        </tr>'''
    return f'''<div class="tbl-wrap" style="margin-bottom:16px">
    <div class="tbl-title" style="color:#f59e0b">
      <i data-lucide="truck" width="14" height="14" style="color:#f59e0b;margin-right:6px;vertical-align:middle"></i>
      Ranking de Transportadoras — Damaged ({len(ranking)} transportadora(s))
    </div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th style="text-align:center">#</th>
        <th>Transportadora</th>
        <th style="text-align:center">Total Damaged</th>
        <th>BPP Total</th>
        <th style="text-align:center">Drivers</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
  </div>'''

# ============================================================
# GERAÇÃO DO HTML
# ============================================================
def gerar_html(d):
    j = lambda x: json.dumps(x, ensure_ascii=False)

    _week     = datetime.now().isocalendar()[1]
    _ryear    = datetime.now().year
    _sin      = d.get('sinistros', {})
    _sin_total = _sin.get('total', 0)
    _sin_bpp   = _sin.get('bpp_total', 0.0)
    _sin_rec   = _sin.get('recuperados', 0)
    _sin_bpp_r = _sin.get('bpp_recuperado', 0.0)
    _sin_taxa  = round(_sin_rec / _sin_total * 100, 1) if _sin_total else 0.0
    _bl        = d.get('bl', {})
    _wp_base = (
        f"📊 *Relatório LP SSP30 — W{_week}/{_ryear}*\n\n"
        f"🔴 *SINISTROS*\n"
        f"• Eventos: {_sin_total} | BPP: ${_sin_bpp:,.0f}\n"
        f"• Recuperados: {_sin_rec} ({_sin_taxa:.1f}%) | BPP Rec.: ${_sin_bpp_r:,.0f}\n\n"
        f"🛡️ *BLOCK LIST*\n"
        f"• Bloqueados: {_bl.get('bloqueados',0)} | Monitorados: {_bl.get('monitorados',0)}\n"
        f"• GMV Protegido: ${_bl.get('gmv_protegido',0.0):,.0f}\n\n"
        f"📦 *FRAUDES/DAMAGED (acumulado {d['ano']})*\n"
        f"• Fraudes: {d['total_fraudes']} | Damaged: {d['total_damaged']}\n"
        f"• BPP Total: ${d['total_bpp']:,.0f}\n\n"
        f"⚡ *ON WAY / ON ROUTE*\n"
    )
    _em_base = (
        f"Relatório LP SSP30 — W{_week}/{_ryear}\n\n"
        f"SINISTROS\n"
        f"Eventos: {_sin_total} | BPP: ${_sin_bpp:,.0f}\n"
        f"Recuperados: {_sin_rec} ({_sin_taxa:.1f}%) | BPP Rec.: ${_sin_bpp_r:,.0f}\n\n"
        f"BLOCK LIST\n"
        f"Bloqueados: {_bl.get('bloqueados',0)} | Monitorados: {_bl.get('monitorados',0)}\n"
        f"GMV Protegido: ${_bl.get('gmv_protegido',0.0):,.0f}\n\n"
        f"FRAUDES/DAMAGED (acumulado {d['ano']})\n"
        f"Fraudes: {d['total_fraudes']} | Damaged: {d['total_damaged']} | BPP Total: ${d['total_bpp']:,.0f}\n\n"
        f"ON WAY / ON ROUTE\n"
    )

    cruzados_list = sorted(d['cruzados'])
    rows_cruzados = ''
    for did in cruzados_list:
        drv = next((x for x in d['drivers'] if x['id'] == did), None)
        dam = next((x for x in d['damaged'] if x['id'] == did), None)
        if drv and dam:
            score = drv['score']
            rows_cruzados += f'''<tr style="background:#160a0a" data-months="{drv.get("months","")}">
                <td style="font-weight:800;color:#fca5a5">{did}</td>
                <td>{prio_badge(drv["prio"])}</td>
                <td style="text-align:center;color:#ef4444;font-weight:700">{drv["fraude"]}</td>
                <td style="text-align:center;color:#f59e0b;font-weight:700">{dam["total"]}</td>
                <td style="text-align:center;font-weight:800;color:#f9fafb">{score}</td>
                <td style="color:#10b981">${drv["bpp"]:,.2f}</td>
            </tr>'''

    return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fraude SSP30 — Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🔍</text></svg>">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/wordcloud@1.2.2/src/wordcloud2.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#080d19;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:flex;flex-direction:column;height:100vh;overflow:hidden}}
  .header{{background:#080d19;padding:16px 32px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #7f1d1d;flex-shrink:0}}
  .header-brand{{display:flex;align-items:center;gap:10px}}
  .header-accent{{width:3px;height:28px;background:#ef4444;border-radius:2px}}
  .header-title{{font-size:16px;font-weight:700;color:#ffffff}}
  .header-sub{{font-size:11px;color:#374151;margin-top:2px}}
  .app-body{{display:flex;flex:1;overflow:hidden}}
  .sidebar{{width:220px;flex-shrink:0;background:#060a14;border-right:1px solid #111827;overflow-y:auto;padding:6px 0;display:flex;flex-direction:column}}
  .sb-divider{{height:1px;background:#111827;margin:6px 0;flex-shrink:0}}
  .sb-section-header{{padding:10px 16px 4px;font-size:9px;text-transform:uppercase;letter-spacing:1px;color:#374151;font-weight:700;flex-shrink:0}}
  .sb-item{{display:flex;align-items:center;gap:9px;padding:9px 16px;font-size:12px;color:#6b7280;cursor:pointer;transition:all .2s;border-left:2px solid transparent;white-space:nowrap;flex-shrink:0}}
  .sb-item:hover{{background:#0d1321;color:#e2e8f0}}
  .sb-item.active{{background:linear-gradient(90deg,rgba(239,68,68,.15),transparent);color:#ffffff;border-left-color:#ef4444;font-weight:600}}
  .sb-drag-handle{{opacity:0;cursor:grab;margin-right:5px;color:#374151;font-size:14px;flex-shrink:0;user-select:none;transition:opacity .15s}}
  .sb-item:hover .sb-drag-handle{{opacity:1}}
  .sb-item.sb-dragging{{opacity:.35}}
  .sb-item.sb-drop-before{{border-top:2px solid #ef4444!important}}
  .sb-badge{{margin-left:auto;background:rgba(239,68,68,.2);color:#f87171;font-size:9px;font-weight:700;padding:2px 6px;border-radius:3px;flex-shrink:0}}
  .sb-badge.green{{background:rgba(74,222,128,.15);color:#4ade80}}
  .sb-badge.amber{{background:rgba(245,158,11,.15);color:#f59e0b}}
  .sb-badge.purple{{background:rgba(167,139,250,.15);color:#a78bfa}}
  .acbl-prd-btn{{background:#1f2937;border:1px solid #374151;color:#9ca3af;font-size:11px;padding:4px 12px;border-radius:6px;cursor:pointer;transition:all .15s}}
  .acbl-prd-btn:hover{{border-color:#6b7280;color:#e2e8f0}}
  .acbl-prd-btn.acbl-prd-active{{background:#ef4444;border-color:#ef4444;color:#fff;font-weight:600}}
  .main-content{{flex:1;overflow-y:auto}}
  .content{{display:none;padding:28px 32px}}
  .content.active{{display:block}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px}}
  .card{{background:#0d1321;border-radius:8px;padding:18px 20px;border:1px solid #111827;transition:all .3s ease;display:flex;flex-direction:column;min-height:90px}}
  .card:hover{{border-color:#1f2937;transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.5)}}
  .card.c-red{{border-color:#450a0a;background:#0f0606}}
  .card-header{{display:flex;align-items:center;gap:7px;margin-bottom:12px}}
  .ci{{color:#374151;flex-shrink:0}}
  .cl{{font-size:10px;color:#4b5563;text-transform:uppercase;letter-spacing:.7px;font-weight:600}}
  .cv{{font-size:28px;font-weight:800;color:#ffffff;line-height:1;letter-spacing:-1px}}
  .cv.red{{color:#ef4444}}
  .cv.amber{{color:#f59e0b}}
  .cv.green{{color:#10b981}}
  .cd{{font-size:11px;color:#374151;margin-top:auto;padding-top:6px}}
  .cards-grid{{display:grid;gap:14px;margin-bottom:18px}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}}
  @media(max-width:900px){{.grid2{{grid-template-columns:1fr}}}}
  .box{{background:#0d1321;border-radius:8px;padding:20px;border:1px solid #111827;margin-bottom:14px}}
  .bt{{font-size:10px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.7px;margin-bottom:16px}}
  .tbl-wrap{{background:#0d1321;border-radius:8px;overflow:hidden;margin-bottom:20px;border:1px solid #111827}}
  .tbl-title{{padding:14px 24px;font-size:10px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.7px;border-bottom:1px solid #111827}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{background:#080d19;padding:10px 16px;text-align:left;font-size:10px;font-weight:600;color:#374151;text-transform:uppercase;letter-spacing:.6px}}
  td{{padding:10px 16px;border-bottom:1px solid #0d1321;color:#d1d5db}}
  tr:hover td{{background:#111827!important}}
  tr:last-child td{{border-bottom:none}}
  .tbl-scroll{{overflow-x:auto}}
  /* PERIOD BUTTONS */
  .pbtn{{background:#0d1321;border:1px solid #1f2937;border-radius:20px;padding:5px 14px;color:#6b7280;font-size:11px;cursor:pointer;transition:all .2s ease;white-space:nowrap}}
  .pbtn:hover{{background:#111827;color:#e2e8f0;border-color:#374151}}
  .pbtn.ativo{{background:#ef4444;border-color:#ef4444;color:#fff;font-weight:600}}
  /* DATE PICKER */
  input[type="date"],input[type="month"]{{background:#0d1321;border:1px solid #1f2937;border-radius:6px;padding:7px 12px;color:#9ca3af;font-size:12px;outline:none;cursor:pointer;transition:border-color .3s ease;color-scheme:dark}}
  input[type="date"]:focus,input[type="month"]:focus{{border-color:#374151;color:#e2e8f0}}
  /* FILTROS */
  .filter-bar{{display:flex;gap:8px;padding:12px 20px;flex-wrap:wrap;border-bottom:1px solid #111827;align-items:center;background:#080d19}}
  .filter-input{{background:#0d1321;border:1px solid #1f2937;border-radius:6px;padding:8px 14px;color:#e2e8f0;font-size:12px;flex:1;min-width:180px;outline:none;transition:border-color .3s ease}}
  .filter-input:focus{{border-color:#374151}}
  .filter-input::placeholder{{color:#374151}}
  .filter-select{{background:#0d1321;border:1px solid #1f2937;border-radius:6px;padding:8px 14px;color:#9ca3af;font-size:12px;outline:none;cursor:pointer;appearance:none;-webkit-appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%236b7280'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center;padding-right:28px;transition:border-color .3s ease}}
  .filter-select:focus{{border-color:#374151;color:#e2e8f0}}
  .filter-label{{font-size:10px;color:#4b5563;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap}}
  /* NAV */
  .mod-nav{{display:flex;gap:4px;align-items:center}}
  .mod-btn{{padding:6px 14px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid #1f2937;text-decoration:none;transition:all .2s;color:#9ca3af;background:#0d1321;display:flex;align-items:center;gap:6px}}
  .mod-btn:hover{{background:#1f2937;color:#e2e8f0;border-color:#374151}}
  .mod-btn.m-fraude{{color:#ef4444;background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.3)}}
  .mod-btn.m-risco{{color:#FFE600;background:rgba(255,230,0,.08);border-color:rgba(255,230,0,.2)}}
  .mod-btn.m-isca{{color:#4ade80;background:rgba(74,222,128,.08);border-color:rgba(74,222,128,.2)}}
  .mod-btn.m-cftv{{color:#60a5fa;background:rgba(96,165,250,.08);border-color:rgba(96,165,250,.2)}}
  .mod-btn.m-sinistros{{color:#f97316;background:rgba(249,115,22,.08);border-color:rgba(249,115,22,.2)}}
  .mod-btn.m-disabled{{opacity:.35;cursor:not-allowed;pointer-events:none}}
  .alerta-box{{background:#160a0a;border:1px solid #7f1d1d;border-radius:8px;padding:16px 20px;margin-bottom:20px;display:flex;align-items:center;gap:14px}}
  .alerta-box .num{{font-size:28px;font-weight:800;color:#fca5a5}}
  .alerta-box .txt{{color:#fca5a5;font-size:13px}}
  {diario_css()}
</style>
<script src="https://cdn.jsdelivr.net/npm/pptxgenjs@3.12.0/dist/pptxgen.bundle.js"></script>
</head>
<body>

<div class="header">
  <div class="header-brand">
    <div class="header-accent"></div>
    <div>
      <div class="header-title">Análise de Fraude — SSP30</div>
      <div class="header-sub">Base {d["ano"]} · <span id="upd-badge">Gerado em {d["gerado"]}</span><span id="upd-ts" data-ts="{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}" style="display:none"></span></div>
    </div>
  </div>
  <a href="https://github.com/lucasunascimento-bit/risco-ssp30/actions/workflows/update-dashboard.yml"
     target="_blank" title="Atualizar dados"
     style="position:fixed;bottom:20px;right:20px;background:#064e3b;color:#4ade80;border:1px solid #166534;border-radius:50%;width:38px;height:38px;display:flex;align-items:center;justify-content:center;text-decoration:none;font-size:18px;z-index:999;box-shadow:0 2px 8px rgba(0,0,0,.5);transition:all .2s"
     onmouseover="this.style.background='#065f46';this.style.transform='scale(1.1)'" onmouseout="this.style.background='#064e3b';this.style.transform='scale(1)'">↻</a>
  <div class="mod-nav">
    <a href="./fraude.html" class="mod-btn m-fraude">
      <i data-lucide="shield-alert" width="12" height="12"></i> Fraude
    </a>
    <a href="./index.html" class="mod-btn">
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
    {diario_nav_btn()}
    <div id="srv-status" style="display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:6px;border:1px solid #1f2937;background:#080d19;font-size:11px;font-weight:600;color:#6b7280;cursor:default" title="Status do servidor local">
      <span id="srv-dot" style="width:7px;height:7px;border-radius:50%;background:#374151;flex-shrink:0;transition:background .3s"></span>
      <span id="srv-label">Servidor</span>
    </div>
  </div>
</div>
{diario_panel_html()}
<div class="app-body">
<nav class="sidebar">
  <div class="sb-item active" data-tab="geral" onclick="showTab('geral',this)">
    <i data-lucide="bar-chart-2" width="14" height="14" class="ci"></i> Visão Geral
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Análise de Risco</div>
  <div class="sb-item" data-tab="acumulo" onclick="showTab('acumulo',this)">
    <i data-lucide="shield-x" width="14" height="14" class="ci"></i>
    Acúmulo Bloqueio <span class="sb-badge" id="tab-count-acumulo">{len(d["acumulo_bloqueio"])}</span>
  </div>
  <div class="sb-item" data-tab="dxp" onclick="showTab('dxp',this)">
    <i data-lucide="map-pin" width="14" height="14" class="ci"></i>
    Driver × Place <span class="sb-badge" id="tab-count-dxp">{len(d["dxp"])}</span>
  </div>
  <div class="sb-item" data-tab="places" onclick="showTab('places',this)">
    <i data-lucide="building-2" width="14" height="14" class="ci"></i>
    Places <span class="sb-badge" id="tab-count-places">{d["total_places"]}</span>
  </div>
  <div class="sb-item" data-tab="damaged" onclick="showTab('damaged',this)">
    <i data-lucide="package-x" width="14" height="14" class="ci"></i>
    Damaged <span class="sb-badge" id="tab-count-damaged">{len(d["damaged"])}</span>
  </div>
  <div class="sb-item" data-tab="tendencia" onclick="showTab('tendencia',this)">
    <i data-lucide="trending-up" width="14" height="14" class="ci"></i>
    Tendência
  </div>
  <div class="sb-item" data-tab="dcnex" onclick="showTab('dcnex',this)">
    <i data-lucide="warehouse" width="14" height="14" class="ci"></i>
    DC / NEX <span class="sb-badge red" id="tab-count-dcnex">{d["dc_nex"]["total_pkgs"]}</span>
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Investigação LP</div>
  <div class="sb-item" data-tab="saidas" onclick="showTab('saidas',this)">
    <i data-lucide="repeat-2" width="14" height="14" class="ci"></i>
    Saídas Múltiplas <span class="sb-badge red" id="tab-count-saidas">0</span>
  </div>
  <div class="sb-item" data-tab="devolucoes" onclick="showTab('devolucoes',this)">
    <i data-lucide="package-open" width="14" height="14" class="ci"></i>
    Devoluções <span class="sb-badge amber" id="tab-count-devolucoes">0</span>
  </div>
  <div class="sb-item" data-tab="sellers_ene" onclick="showTab('sellers_ene',this)">
    <i data-lucide="store" width="14" height="14" class="ci"></i>
    Sellers ENE <span class="sb-badge red" id="tab-count-sellers-ene">0</span>
  </div>
  <div class="sb-item" data-tab="damaged_ene" onclick="showTab('damaged_ene',this)">
    <i data-lucide="package-open" width="14" height="14" class="ci"></i>
    Damaged ENE <span class="sb-badge red" id="tab-count-damaged-ene">0</span>
  </div>
  <div class="sb-item" data-tab="ofensores" onclick="showTab('ofensores',this)">
    <i data-lucide="target" width="14" height="14" class="ci"></i>
    Ofensores
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Block List</div>
  <div class="sb-item" data-tab="bloqueios" onclick="showTab('bloqueios',this)">
    <i data-lucide="shield" width="14" height="14" class="ci"></i>
    Bloqueios <span class="sb-badge green" id="tab-count-bloqueios">{d["bl"]["total"]}</span>
  </div>
  <div class="sb-item" data-tab="cruzamento" onclick="showTab('cruzamento',this)">
    <i data-lucide="git-merge" width="14" height="14" class="ci"></i>
    BSD <span class="sb-badge amber">{d["crz"]["total_pares"]}</span>
  </div>
  <div class="sb-divider"></div>
  <div class="sb-section-header">Relatórios</div>
  <div class="sb-item" data-tab="relatorio" onclick="showTab('relatorio',this)">
    <i data-lucide="file-text" width="14" height="14" class="ci"></i>
    Rel. Semanal
  </div>
</nav>
<main class="main-content">

<!-- BARRA DE PERÍODO — oculta na aba Acúmulo -->
<div id="barra-periodo" style="background:#080d19;border-bottom:1px solid #1f2937;padding:10px 32px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
  <span class="filter-label">Período:</span>
  <span style="font-size:11px;color:#6b7280">De</span>
  <input type="month" id="pd_de" onchange="setPeriodo()" min="{d["ano"]}-01" max="{d["mes_atual"]}" style="max-width:150px">
  <span style="font-size:11px;color:#6b7280">Até</span>
  <input type="month" id="pd_ate" onchange="setPeriodo()" min="{d["ano"]}-01" max="{d["mes_atual"]}" style="max-width:150px">
  <button onclick="resetPeriodo()" style="background:#1f2937;color:#6b7280;border:1px solid #374151;border-radius:6px;padding:6px 12px;font-size:11px;cursor:pointer">Limpar</button>
  <span id="pd_label" style="font-size:12px;font-weight:600;color:#60a5fa"></span>
</div>

<!-- VISÃO GERAL -->
<div id="tab-geral" class="content active">

  <div class="cards">
    <div class="card c-red">
      <div class="card-header"><i data-lucide="alert-triangle" class="ci" width="14" height="14" style="color:#7f1d1d"></i><span class="cl">Drivers Críticos</span></div>
      <div class="cv red" id="cv-criticos">{d["criticos"]}</div>
      <div class="cd" id="sub-criticos">Prioridade Alta ou Máxima</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="package-x" class="ci" width="14" height="14"></i><span class="cl">Total Fraudes/Lost</span></div>
      <div class="cv" id="cv-fraudes">{d["total_fraudes"]}</div>
      <div class="cd" id="sub-fraudes">{d["ano"]}</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="box" class="ci" width="14" height="14"></i><span class="cl">Total Damaged</span></div>
      <div class="cv amber" id="cv-damaged">{d["total_damaged"]}</div>
      <div class="cd" id="sub-damaged">{d["ano"]}</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="dollar-sign" class="ci" width="14" height="14"></i><span class="cl">BPP Total</span></div>
      <div class="cv green" id="cv-bpp">${d["total_bpp"]:,.2f}</div>
      <div class="cd" id="sub-bpp">Cashout {d["ano"]}</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="map-pin" class="ci" width="14" height="14"></i><span class="cl">Places Suspeitos</span></div>
      <div class="cv" id="cv-places">{d["total_places"]}</div>
      <div class="cd">Com fraude/lost</div>
    </div>
    <div class="card c-red">
      <div class="card-header"><i data-lucide="zap" class="ci" width="14" height="14" style="color:#7f1d1d"></i><span class="cl">Cruzados F+D</span></div>
      <div class="cv red" id="cv-cruzados">{len(d["cruzados"])}</div>
      <div class="cd">Fraude + Damaged</div>
    </div>
    <div class="card card-ok card-link" onclick="irPara('bloqueios')">
      <div class="card-header"><i data-lucide="shield-check" class="ci" width="14" height="14" style="color:#064e3b"></i><span class="cl">Bloqueios Confirmados</span></div>
      <div class="cv val-ok">{d["bl"]["bloqueados"]}</div>
      <div class="card-delta">de {d["bl"]["total"]} sol. · ${d["bl"]["gmv_protegido"]:,.0f} protegido</div>
    </div>
  </div>
  <div style="font-size:11px;color:#4b5563;padding:2px 0 10px;text-align:right">
    ℹ️ Fraudes, Damaged e BPP calculados sobre os top 60 drivers por score · BSD e DC/NEX podem incluir pacotes de drivers fora deste grupo
  </div>

  {"" if not d["cruzados"] else f'''
  <div class="alerta-box" id="alerta-cruzados">
    <div class="num" id="num-cruzados-alert">{len(d["cruzados"])}</div>
    <div class="txt"><strong><span id="num-cruzados-txt">{len(d["cruzados"])}</span> drivers aparecem em AMBAS as análises (Fraude + Damaged)</strong><br>
    Estes são os principais alvos para investigação e bloqueio.</div>
  </div>
  <div class="tbl-wrap" id="wrap-cruzados">
    <div class="tbl-title">Drivers com Fraude + Damaged (maior risco)</div>
    <div class="tbl-scroll"><table id="tbl_cruzados">
      <thead><tr><th>Driver ID</th><th>Prioridade</th><th>Fraudes</th><th>Damaged</th><th>Score</th><th>BPP Total</th></tr></thead>
      <tbody>''' + rows_cruzados + '''</tbody></table></div>
  </div>'''}

  <div class="grid2">
    <div class="box"><div class="bt">Top 10 Drivers — Fraude vs Damaged <span style="font-weight:400;font-size:11px;color:#6b7280">· reativo ao período</span></div><canvas id="cDrivers" height="280"></canvas></div>
    <div class="box"><div class="bt">Top 10 Places com mais Fraudes <span style="font-weight:400;font-size:11px;color:#6b7280">· acumulado anual</span></div><canvas id="cPlaces" height="280"></canvas></div>
  </div>
</div>

<!-- ACÚMULO BLOQUEIO -->
<div id="tab-acumulo" class="content">
  <div style="font-size:18px;font-weight:700;color:#f9fafb;margin-bottom:6px">Acúmulo BPP</div>
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">
    <span style="font-size:11px;color:#9ca3af;font-weight:500">Janela:</span>
    {''.join(f'<button class="acbl-prd-btn{" acbl-prd-active" if p=="90" else ""}" onclick="switchAcblPeriod({p},this)">{p} dias</button>' for p in ["30","60","90","180"])}
  </div>
  <div id="acbl-subtitle" style="font-size:12px;color:#6b7280;margin-bottom:18px">
    Drivers com BPP em 3+ meses distintos · Apenas status ativo · Últimos 90 dias
  </div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:18px">
    <div class="card">
      <div class="card-header"><i data-lucide="alert-triangle" class="ci" width="14" height="14" style="color:#E24B4A"></i><span class="cl">Candidatos</span></div>
      <div id="acbl-card-total" class="card-value" style="color:#E24B4A">{len(d["acumulo_bloqueio"])}</div>
      <div class="card-delta" id="acbl-card-delta-total">drivers com 3+ meses BPP</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="shield-x" class="ci" width="14" height="14" style="color:#10b981"></i><span class="cl">Aptos</span></div>
      <div id="acbl-card-aptos" class="card-value" style="color:#10b981">{sum(1 for x in d["acumulo_bloqueio"] if x["apto"])}</div>
      <div class="card-delta">atendem aos critérios (&gt;6 pkgs e &gt;$300)</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="dollar-sign" class="ci" width="14" height="14"></i><span class="cl">BPP Total</span></div>
      <div id="acbl-card-bpp" class="card-value" style="font-size:20px">${sum(x["total_bpp"] for x in d["acumulo_bloqueio"]):,.0f}</div>
      <div class="card-delta">acumulado pelos candidatos</div>
    </div>
  </div>
  {''.join(f'<div id="acbl-content-{p}" class="acbl-period-content" style="display:{"" if p=="90" else "none"}">{rows_acumulo_bloqueio(d["acumulo_por_periodo"][p], pid=p)}</div>' for p in ["30","60","90","180"])}
</div>

<!-- DRIVER × PLACE -->
<div id="tab-dxp" class="content">
  <div class="tbl-wrap">
    <div class="tbl-title">Driver × Place — Combinações com 2+ Fraudes em Comum <span style="font-weight:400;font-size:11px;color:#6b7280">· filtro mostra linhas ativas no período; valores são totais anuais</span></div>
    <div class="tbl-scroll"><table id="tbl_dxp">
      <thead><tr><th>Driver ID</th><th>Place</th><th>Total</th><th>Fraudes</th><th>Damaged</th><th>BPP Total</th></tr></thead>
      <tbody>{rows_dxp(d["dxp"], d["shp_por_driver"], d["shp_dxp"])}</tbody>
    </table></div>
  </div>
</div>

<!-- PLACES / OFENSORES -->
<div id="tab-places" class="content">

  <!-- Top 3 cards -->
  <div class="cards" style="grid-template-columns:repeat(3,1fr);margin-bottom:20px">
    {''.join(f"""
    <div class="card {'c-red' if i==0 else ''}">
      <div class="card-header"><i data-lucide="map-pin" class="ci" width="14" height="14" {'style="color:#7f1d1d"' if i==0 else ''}></i>
        <span class="cl">#{i+1} Ofensor</span></div>
      <div class="cv {'red' if i==0 else ''}" style="font-size:14px;font-weight:700">{p["nome"][:30]}{"…" if len(p["nome"])>30 else ""}</div>
      <div class="cd">{p["total"]} incidentes · ${p["bpp"]:,.2f} BPP</div>
    </div>""" for i, p in enumerate(d["places"][:3]))}
  </div>

  <div class="box mb16"><div class="bt">Top 8 Places — Total de Fraudes/Lost <span style="font-weight:400;font-size:11px;color:#6b7280">· acumulado anual</span></div><canvas id="cPlacesBar" height="200"></canvas></div>

  <div class="tbl-wrap">
    <div class="tbl-title">Ranking completo — Places Ofensores <span style="font-weight:400;font-size:11px;color:#6b7280">· filtro mostra places ativos no período; valores são totais anuais</span></div>
    <div class="tbl-scroll"><table id="tbl_places">
      <thead><tr>
        <th>Place</th><th>Total</th><th>BPP</th>
        <th>Lost Route</th><th>Lost Way</th><th>Lost Station</th><th>Lost ENE</th><th>Fraud Confirm.</th>
      </tr></thead>
      <tbody>{rows_places(d["places"], d["shp_por_place"])}</tbody>
    </table></div>
  </div>
</div>

<!-- DAMAGED -->
<div id="tab-damaged" class="content">
  <div class="tbl-wrap" id="transp_damaged_ranking" style="margin-bottom:16px"></div>
  <div class="tbl-wrap">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap">
      <div class="tbl-title" style="margin-bottom:0">Damaged por Driver — ⚠️ indica driver que também tem fraudes · <span style="font-weight:400;color:#6b7280">valores recalculados ao filtrar período</span></div>
      <div style="display:flex;align-items:center;gap:6px;margin-left:auto">
        <label style="color:#9ca3af;font-size:12px">Transportadora:</label>
        <select id="dmg-transp-filter" onchange="_applyDamagedFilters()" style="background:#0a1120;color:#f9fafb;border:1px solid #1e293b;border-radius:4px;padding:4px 10px;font-size:12px;cursor:pointer">
          <option value="">Todas</option>
        </select>
      </div>
    </div>
    <div class="tbl-scroll"><table id="tbl_damaged">
      <thead><tr>
        <th>Driver ID</th><th>Total Damaged</th><th>BPP Total</th>
        <th>On Route</th><th>At Station</th><th>ENE</th><th>Transportadora</th>
      </tr></thead>
      <tbody>{rows_damaged(d["damaged"], d["cruzados"], d["shp_por_driver"], d.get("driver_transp", {}))}</tbody>
    </table></div>
  </div>
</div>

<script>
// Filtro da aba Bloqueios — usa período global _periodDe/_periodAte
function filtrarBloqueios() {{
  const status = document.getElementById('bl_status')?.value || '';
  const transp = document.getElementById('bl_transp')?.value || '';
  const search = (document.getElementById('bl_search')?.value || '').toLowerCase();
  document.querySelectorAll('.bl-row').forEach(tr => {{
    const d   = tr.dataset.data   || '';
    const ym  = d.substring(0,7);
    const st  = tr.dataset.status || '';
    const tp  = tr.dataset.transp || '';
    const src = tr.dataset.search || '';
    const ok  = (!_periodDe || ym >= _periodDe)
             && (!_periodAte || ym <= _periodAte)
             && (!status || st === status)
             && (!transp || tp === transp)
             && (!search || src.includes(search));
    tr.style.display = ok ? '' : 'none';
    const nx = tr.nextElementSibling;
    if (nx && nx.classList.contains('bl-hist-row') && !ok) nx.style.display = 'none';
  }});
  updateBloqueiosCards();
}}

// Exportar linhas visíveis como CSV
function exportBlCSV() {{
  const cols = ['Driver ID','Nome','Transportadora','Placa','SHP','USD$','Status','Motivo','Data','Semana'];
  const rows = [cols.join(',')];
  document.querySelectorAll('.bl-row').forEach(tr => {{
    if (tr.style.display === 'none') return;
    const tds = [...tr.querySelectorAll('td')];
    const vals = tds.map(td => '"' + td.textContent.trim().replace(/"/g,'""') + '"');
    rows.push(vals.join(','));
  }});
  const blob = new Blob([rows.join('\\n')], {{type:'text/csv;charset=utf-8;'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'block_list_ssp30.csv';
  a.click();
}}

// Ordenação das colunas
let _blSortCol = null, _blSortDir = 1;
function sortBl(col) {{
  if (_blSortCol === col) _blSortDir *= -1; else {{ _blSortCol = col; _blSortDir = 1; }}
  ['did','usd','status','data'].forEach(c => {{
    const el = document.getElementById('bl-sort-' + c);
    if (el) el.textContent = c === col ? (_blSortDir === 1 ? ' ↑' : ' ↓') : '';
  }});
  const tbody = document.getElementById('bl-tbody');
  if (!tbody) return;
  const all = [...tbody.children];
  const units = [];
  let i = 0;
  while (i < all.length) {{
    const main = all[i];
    const nx   = all[i+1];
    if (nx && nx.classList.contains('bl-hist-row')) {{ units.push([main, nx]); i += 2; }}
    else {{ units.push([main]); i++; }}
  }}
  const getVal = (tr) => {{
    if (col === 'usd')    return parseFloat(tr.dataset.usd || '0');
    if (col === 'status') return tr.dataset.status || '';
    if (col === 'data')   return tr.dataset.data   || '';
    if (col === 'did')    return (tr.dataset.search || '').split(' ')[0];
    return '';
  }};
  units.sort((a,b) => {{
    const va = getVal(a[0]), vb = getVal(b[0]);
    return (va < vb ? -1 : va > vb ? 1 : 0) * _blSortDir;
  }});
  units.forEach(u => u.forEach(tr => tbody.appendChild(tr)));
}}

// Badge "atualizado há X min"
function _updBadge() {{
  const ts = document.getElementById('upd-ts')?.dataset.ts;
  const badge = document.getElementById('upd-badge');
  if (!ts || !badge) return;
  const diff = Math.round((Date.now() - new Date(ts).getTime()) / 60000);
  badge.textContent = diff < 1 ? 'Atualizado agora' : `Atualizado há ${{diff}} min`;
}}
_updBadge();
setInterval(_updBadge, 60000);

function toggleBl(id) {{
  const el = document.getElementById(id);
  const ar = document.getElementById(id + '_arrow');
  if (!el) return;
  const show = el.style.display === 'none';
  el.style.display = show ? '' : 'none';
  if (ar) ar.textContent = show ? '▼' : '▶';
}}

function updateBloqueiosCards() {{
  const set = (id, v) => {{ const e = document.getElementById(id); if(e) e.textContent = v; }};
  const counts = {{}};
  const byTransp = {{}};
  let total = 0, allTotal = 0, gmv = 0;
  document.querySelectorAll('.bl-row').forEach(tr => {{
    allTotal++;
    if (tr.style.display !== 'none') {{
      total++;
      const st  = tr.dataset.status || '';
      const tp  = tr.dataset.transp || '';
      const usd = parseFloat(tr.dataset.usd || '0') || 0;
      counts[st]  = (counts[st]  || 0) + 1;
      byTransp[tp] = (byTransp[tp] || 0) + 1;
      if (st === 'Bloqueado') gmv += usd;
    }}
  }});
  set('bl-cv-total', total);
  const noteEl = document.getElementById('bl-period-note');
  if (noteEl) {{
    if (allTotal > 0 && total === 0) {{
      noteEl.textContent = allTotal + (allTotal > 1 ? ' entradas ocultadas' : ' entrada ocultada') + ' pelo filtro de período — limpe o período para ver todas';
      noteEl.style.display = '';
    }} else if (total > 0 && total < allTotal) {{
      noteEl.textContent = 'Exibindo ' + total + ' de ' + allTotal + ' entradas no período selecionado';
      noteEl.style.display = '';
    }} else {{
      noteEl.style.display = 'none';
    }}
  }}
  set('bl-cv-bloqueados', counts['Bloqueado']  || 0);
  set('bl-cv-solicitados',counts['Solicitado'] || 0);
  set('bl-cv-monitorados',counts['Monitorado'] || 0);
  set('bl-cv-gmv', '$' + gmv.toLocaleString('en-US', {{minimumFractionDigits:2,maximumFractionDigits:2}}));
  if (_chartBlStatus) {{
    const labels = Object.keys(counts).filter(k => BL_COLORS[k]);
    _chartBlStatus.data.labels = labels;
    _chartBlStatus.data.datasets[0].data = labels.map(k => counts[k]);
    _chartBlStatus.data.datasets[0].backgroundColor = _blColors(labels);
    _chartBlStatus.update();
  }}
  if (_chartBlTransp) {{
    const sorted = Object.entries(byTransp).sort((a,b) => b[1]-a[1]);
    _chartBlTransp.data.labels = sorted.map(e => e[0]);
    _chartBlTransp.data.datasets[0].data = sorted.map(e => e[1]);
    _chartBlTransp.update();
  }}
}}

// Menu Dashboards — abre/fecha com click, fecha ao clicar fora
// Filtro da tabela de drivers
function filtrarDrivers() {{
  const busca  = (document.getElementById('busca_driver')?.value || '').toLowerCase();
  const transp = (document.getElementById('filtro_transp')?.value || '').toLowerCase();
  const ativ   = (document.getElementById('filtro_ativ')?.value || '').toLowerCase();
  document.querySelectorAll('#tbl_drivers > tbody > tr[data-id]').forEach(tr => {{
    const id    = (tr.dataset.id    || '').toLowerCase();
    const tp    = (tr.dataset.transp|| '').toLowerCase();
    const at    = (tr.dataset.ativ  || '').toLowerCase();
    const periodOk = (!_periodDe && !_periodAte) || (tr.dataset.months||'').split(' ').some(m => (!_periodDe || m >= _periodDe) && (!_periodAte || m <= _periodAte));
    const ok = periodOk
            && (!busca  || id.includes(busca))
            && (!transp || tp.includes(transp))
            && (!ativ   || at.includes(ativ));
    tr.style.display = ok ? '' : 'none';
    const nextSibling = tr.nextElementSibling;
    if (nextSibling && nextSibling.tagName === 'TBODY' && !ok) nextSibling.style.display = 'none';
    // Atualiza contador "X pacotes" conforme período
    if (ok) {{
      const rowId  = 'dr_' + tr.dataset.id;
      const arrow  = document.getElementById('arrow_' + rowId);
      const shpTbody = document.getElementById(rowId);
      if (arrow && shpTbody) {{
        let count = 0;
        shpTbody.querySelectorAll('tr[data-ym]').forEach(shpTr => {{
          const ym = shpTr.dataset.ym;
          if ((!_periodDe || ym >= _periodDe) && (!_periodAte || ym <= _periodAte)) count++;
        }});
        const sym = /^[▶▼]/.exec(arrow.textContent)?.[0] || '▶';
        arrow.textContent = sym + ' ' + count + ' pacotes';
      }}
    }}
  }});
  updateCountCards();
}}

// Expandir/recolher SHP IDs do driver
function toggleDriver(id) {{
  const el = document.getElementById(id);
  const ar = document.getElementById('arrow_' + id);
  if (!el) return;
  const open = el.style.display === 'none';
  el.style.display = open ? '' : 'none';
  if (ar) ar.textContent = ar.textContent.replace(open ? '▶' : '▼', open ? '▼' : '▶');
}}

const ACUMULO_DATA = {j(d.get("acumulo_bloqueio", []))};
const ACUMULO_POR_PERIODO = {j({str(k): [{k2: v2 for k2, v2 in c.items() if k2 != 'shps'} for c in v] for k, v in d.get("acumulo_por_periodo", {}).items()})};

const SAIDAS_DATA = {j(d.get("saidas", []))};
const DEVOLUCOES_DATA = {j(d.get("devolucoes", []))};
const SELLERS_ENE_DATA = {j(d.get("sellers_ene", []))};
const ENE_SERVICE_DATA = {j(d.get("ene_service", []))};
const DAMAGED_MONTHLY = {j({str(dmg['id']): dmg.get('monthly', {}) for dmg in d['damaged']})};
const DRIVER_TRANSP   = {j(d.get('driver_transp', {}))};
const CRITICOS_COUNT  = {d.get('criticos', 0)};
const DAMAGED_ENE_DATA = {j(d['damaged_ene'])};
const FRAUD_ENE_DATA   = {j(d['fraud_ene'])};
const CRZ_DRIVERS_DATA = {j(d['crz'].get('driver_crz', [])[:50])};
const CRZ_BUYERS_DATA  = {j(d['crz'].get('buyers', [])[:50])};
const CRZ_SELLERS_DATA = {j(d['crz'].get('sellers', [])[:50])};
const BUYER_VEL_DATA   = {j(d.get('buyer_vel', [])[:50])};

const ALL_TABS = ['geral','acumulo','dxp','places','damaged','tendencia','dcnex','saidas','devolucoes','sellers_ene','damaged_ene','ofensores','bloqueios','cruzamento','relatorio'];
function showTab(name, el) {{
  _currentTab = name;
  document.querySelectorAll('.content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.sb-item').forEach(e => e.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  el.classList.add('active');
  history.replaceState(null,'','#'+name);
  const bp = document.getElementById('barra-periodo');
  const _noPeriod = ['acumulo','relatorio','dcnex','sellers_ene','saidas','devolucoes'];
  if (bp) bp.style.display = _noPeriod.includes(name) ? 'none' : 'flex';
  applyPeriodoToTab(name);
  if (name === 'bloqueios') initBlCharts();
  if (name === 'relatorio') carregarTratados();
  if (name === 'saidas') initSaidas();
  if (name === 'devolucoes') initDevolucoes();
  if (name === 'sellers_ene') initSellersENE();
  if (name === 'damaged_ene') initDamagedENE();
  if (name === 'ofensores') initOfensores();
}}
function _handleHashNav(delay) {{
  const raw = window.location.hash.replace('#','');
  const parts = raw.split('__');
  const tabName = parts[0];
  const driverId = parts[1] || null;
  if (!ALL_TABS.includes(tabName)) return;
  const el = document.querySelector(`.sb-item[data-tab="${{tabName}}"]`);
  if (el) showTab(tabName, el);
  if (!driverId) return;
  setTimeout(() => {{
    const detail = document.getElementById('acbl_' + driverId);
    if (!detail) return;
    detail.style.display = 'block';
    const wrapper = detail.parentElement;
    if (wrapper) {{
      wrapper.style.outline = '2px solid #60a5fa';
      setTimeout(() => {{ wrapper.style.outline = ''; }}, 2000);
      wrapper.scrollIntoView({{behavior:'smooth', block:'start'}});
    }}
  }}, delay);
}}
window.addEventListener('load',       () => _handleHashNav(400));
window.addEventListener('pageshow',   (e) => {{ if (e.persisted) _handleHashNav(400); }});
window.addEventListener('hashchange', () => _handleHashNav(100));

// Modo restrito: ?view=ofensores — esconde nav e trava na aba
(function() {{
  const p = new URLSearchParams(window.location.search);
  if (p.get('view') !== 'ofensores') return;
  // Injeta CSS para esconder sidebar, botoes de topo e barra de periodo
  const s = document.createElement('style');
  s.textContent = `
    nav {{ display:none !important; }}
    .mod-nav {{ display:none !important; }}
    #srv-status {{ display:none !important; }}
    #barra-periodo {{ display:none !important; }}
  `;
  document.head.appendChild(s);
  // Quando carregado, força tab Ofensores e bloqueia troca de tab
  window.addEventListener('load', () => {{
    const el = document.querySelector('.sb-item[data-tab="ofensores"]');
    if (el) showTab('ofensores', el);
    // Sobrescreve showTab para impedir navegação
    window.showTab = function(name, el) {{
      if (name !== 'ofensores') return;
      const tabs = document.querySelectorAll('.sb-item');
      tabs.forEach(t => t.classList.remove('active'));
      if (el) el.classList.add('active');
      document.querySelectorAll('.content').forEach(c => c.style.display = 'none');
      const t = document.getElementById('tab-ofensores');
      if (t) t.style.display = 'block';
      initOfensores();
    }};
  }}, {{ once: true }});
}})();

Chart.defaults.plugins.tooltip.backgroundColor = '#0d1321';
Chart.defaults.plugins.tooltip.titleColor      = '#f9fafb';
Chart.defaults.plugins.tooltip.bodyColor       = '#9ca3af';
Chart.defaults.plugins.tooltip.borderColor     = '#1f2937';
Chart.defaults.plugins.tooltip.borderWidth     = 1;
Chart.defaults.plugins.tooltip.cornerRadius    = 6;
Chart.defaults.plugins.tooltip.padding         = 10;

let _cDriversChart = new Chart(document.getElementById('cDrivers'), {{
  type: 'bar',
  data: {{
    labels: {j(d["top10_labels"])},
    datasets: [
      {{ label:'Fraudes/Lost', data:{j(d["top10_fraude"])}, backgroundColor:'rgba(239,68,68,0.8)', borderRadius:4 }},
      {{ label:'Damaged',      data:{j(d["top10_damage"])}, backgroundColor:'rgba(245,158,11,0.8)', borderRadius:4 }},
    ]
  }},
  options: {{
    responsive:true,
    plugins:{{ legend:{{ labels:{{ color:'#94a3b8', font:{{size:11}} }} }} }},
    scales:{{
      x:{{ stacked:false, ticks:{{color:'#8a8a8a'}}, grid:{{color:'#1e293b'}} }},
      y:{{ ticks:{{color:'#8a8a8a'}}, grid:{{color:'#334155'}} }}
    }}
  }}
}});

new Chart(document.getElementById('cPlaces'), {{
  type: 'bar',
  data: {{
    labels: {j(d["top10_places_labels"])},
    datasets: [{{ data:{j(d["top10_places_vals"])}, backgroundColor:'rgba(239,68,68,0.7)', borderRadius:4 }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    plugins: {{ legend:{{display:false}} }},
    scales: {{
      x:{{ ticks:{{color:'#8a8a8a'}}, grid:{{color:'#334155'}} }},
      y:{{ ticks:{{color:'#8a8a8a', font:{{size:11}}}}, grid:{{display:false}} }}
    }}
  }}
}});

// Gráfico de Places Ofensores
new Chart(document.getElementById('cPlacesBar'), {{
  type: 'bar',
  data: {{
    labels: {j([p["nome"][:22]+"…" if len(p["nome"])>22 else p["nome"] for p in d["places"][:8]])},
    datasets: [
      {{
        label: 'Fraudes/Lost',
        data: {j([p["total"] for p in d["places"][:8]])},
        backgroundColor: [
          'rgba(239,68,68,0.9)','rgba(239,68,68,0.82)','rgba(239,68,68,0.74)',
          'rgba(239,68,68,0.66)','rgba(239,68,68,0.58)','rgba(239,68,68,0.50)',
          'rgba(239,68,68,0.42)','rgba(239,68,68,0.34)'
        ],
        borderRadius: 6,
        barThickness: 18,
      }}
    ]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: ctx => ` ${{ctx.parsed.x}} incidentes`
        }}
      }}
    }},
    scales: {{
      x: {{ ticks: {{color:'#6b7280',font:{{size:10}}}}, grid: {{color:'#1e293b'}} }},
      y: {{ ticks: {{color:'#d1d5db',font:{{size:11}}}}, grid: {{display:false}} }}
    }}
  }}
}});

// Mapa fixo de cores por status — evita troca de cor ao atualizar dinamicamente
const BL_COLORS = {{Bloqueado:'#10b981',Solicitado:'#3b82f6',Monitorado:'#f59e0b',Inativo:'#6b7280'}};
function _blColors(labels) {{ return labels.map(l => BL_COLORS[l] || '#94a3b8'); }}

// ── Acúmulo BPP: filtro de janela temporal ──────────────────────────────────
function switchAcblPeriod(dias, btn) {{
  document.querySelectorAll('.acbl-prd-btn').forEach(b => b.classList.remove('acbl-prd-active'));
  if (btn) btn.classList.add('acbl-prd-active');
  document.querySelectorAll('.acbl-period-content').forEach(el => el.style.display = 'none');
  const el = document.getElementById('acbl-content-' + dias);
  if (el) el.style.display = '';
  const candidatos = ACUMULO_POR_PERIODO[String(dias)] || [];
  const total = candidatos.length;
  const aptos = candidatos.filter(x => x.apto).length;
  const bpp   = candidatos.reduce((s, x) => s + (x.total_bpp || 0), 0);
  const setEl = (id, v) => {{ const e = document.getElementById(id); if (e) e.textContent = v; }};
  setEl('acbl-card-total', total);
  setEl('acbl-card-aptos', aptos);
  setEl('acbl-card-bpp', '$' + bpp.toLocaleString('pt-BR', {{maximumFractionDigits:0}}));
  const minM = dias <= 60 ? 2 : 3;
  const sub = document.getElementById('acbl-subtitle');
  if (sub) sub.textContent = `Drivers com BPP em ${{minM}}+ meses distintos · Apenas status ativo · Últimos ${{dias}} dias`;
  const dt = document.getElementById('acbl-card-delta-total');
  if (dt) dt.textContent = `drivers com ${{minM}}+ meses BPP`;
}}

// Gráficos de Bloqueios — criados na 1ª vez que a aba abre
// setTimeout(0) garante reflow do CSS antes do Chart.js medir o canvas
let _blDone = false, _chartBlStatus = null, _chartBlTransp = null;
function initBlCharts() {{
  if (_blDone) return;
  _blDone = true;
  setTimeout(function() {{
    const _stLabels = {j([k for k in d["bl"]["por_status"].keys() if k in ('Bloqueado','Monitorado','Solicitado','Inativo')])};
    const _stData   = {j([v for k, v in d["bl"]["por_status"].items() if k in ('Bloqueado','Monitorado','Solicitado','Inativo')])};
    _chartBlStatus = new Chart(document.getElementById('cBlStatus'), {{
      type: 'doughnut',
      data: {{
        labels: _stLabels,
        datasets: [{{ data: _stData,
          backgroundColor: _blColors(_stLabels), borderWidth:0 }}]
      }},
      options: {{ responsive:true, maintainAspectRatio:false, plugins:{{ legend:{{ labels:{{ color:'#94a3b8',font:{{size:11}} }} }} }}, cutout:'40%' }}
    }});
    _chartBlTransp = new Chart(document.getElementById('cBlTransp'), {{
      type: 'bar',
      data: {{
        labels: {j(list(d["bl"]["por_transp"].keys()))},
        datasets: [{{ data: {j(list(d["bl"]["por_transp"].values()))},
          backgroundColor: 'rgba(74,222,128,0.75)', borderRadius:4 }}]
      }},
      options: {{
        responsive:true, maintainAspectRatio:false, plugins:{{ legend:{{display:false}} }},
        scales:{{ x:{{ ticks:{{color:'#8a8a8a'}}, grid:{{color:'#1e293b'}} }},
                  y:{{ ticks:{{color:'#8a8a8a'}}, grid:{{color:'#334155'}} }} }}
      }}
    }});
  }}, 0);
}}

// ---- Filtro de período ----
const MONTHLY = {j(d.get("monthly", []))};
const ANNUAL  = {{
  fraudes: MONTHLY.reduce((s,m)=>s+m.fraudes,0),
  damaged: MONTHLY.reduce((s,m)=>s+m.damaged,0),
  bpp:     MONTHLY.reduce((s,m)=>s+m.bpp,0)
}};
let _periodDe = '', _periodAte = '';
let _currentTab = 'geral';

// Aplica filtro de período apenas na aba indicada
function applyPeriodoToTab(name) {{
  const filterByMonths = (tblId) => {{
    const tbl = document.getElementById(tblId);
    if (!tbl) return;
    tbl.querySelectorAll('tbody > tr[data-months]').forEach(tr => {{
      const months = (tr.dataset.months||'').split(' ');
      const show = (!_periodDe && !_periodAte) ? true
        : months.some(m => (!_periodDe || m >= _periodDe) && (!_periodAte || m <= _periodAte));
      tr.style.display = show ? '' : 'none';
      const nx = tr.nextElementSibling;
      if (nx && nx.tagName === 'TBODY' && !show) nx.style.display = 'none';
    }});
  }};
  if      (name === 'geral')      {{ filterByMonths('tbl_cruzados'); updateCountCards(); }}
  else if (name === 'acumulo')    {{ /* período gerenciado por switchAcblPeriod */ }}
  else if (name === 'dxp')        {{ filterByMonths('tbl_dxp'); }}
  else if (name === 'places')     {{ filterByMonths('tbl_places'); }}
  else if (name === 'damaged')    {{ _applyDamagedFilters(); }}
  else if (name === 'bloqueios')  {{ filtrarBloqueios(); }}
  else if (name === 'tendencia')  {{ renderTendencia(); }}
  else if (name === 'ofensores')  {{ try {{ renderOfensores(); }} catch(e) {{}} }}
  else if (name === 'cruzamento') {{ renderCrzMes(); }}
  else if (name === 'damaged_ene') {{ initDamagedENE(); }}
}}

function _recalcDamagedTotals() {{
  document.querySelectorAll('#tbl_damaged > tbody > tr[data-months]').forEach(tr => {{
    if (tr.style.display === 'none') return;
    const did = tr.dataset.driver;
    const monthly = DAMAGED_MONTHLY[did];
    if (!monthly) return;
    let total = 0, bpp = 0, route = 0, station = 0, ene = 0;
    Object.entries(monthly).forEach(([ym, v]) => {{
      if ((!_periodDe || ym >= _periodDe) && (!_periodAte || ym <= _periodAte)) {{
        total   += v.total;
        bpp     += v.bpp;
        route   += v.route;
        station += v.station;
        ene     += v.ene;
      }}
    }});
    const fmtBpp = '$' + bpp.toLocaleString('en-US', {{minimumFractionDigits:2, maximumFractionDigits:2}});
    const totEl     = tr.querySelector('.dmg-total');
    const bppEl     = tr.querySelector('.dmg-bpp');
    const routeEl   = tr.querySelector('.dmg-route');
    const stationEl = tr.querySelector('.dmg-station');
    const eneEl     = tr.querySelector('.dmg-ene');
    const arrowEl   = tr.querySelector('span[id^="arrow_dmg_"]');
    if (totEl)     totEl.textContent     = total;
    if (bppEl)     bppEl.textContent     = fmtBpp;
    if (routeEl)   routeEl.textContent   = route;
    if (stationEl) stationEl.textContent = station;
    if (eneEl)     eneEl.textContent     = ene;
    if (arrowEl)   arrowEl.textContent   = '▶ ' + total + ' ids';
  }});
  _rebuildTranspRanking();
}}

function _applyDamagedFilters() {{
  const selTransp = document.getElementById('dmg-transp-filter');
  const filterTransp = selTransp ? selTransp.value : '';
  document.querySelectorAll('#tbl_damaged > tbody > tr[data-months]').forEach(tr => {{
    const months = (tr.dataset.months||'').split(' ');
    const periodOk = (!_periodDe && !_periodAte) ? true
      : months.some(m => (!_periodDe || m >= _periodDe) && (!_periodAte || m <= _periodAte));
    const transpOk = !filterTransp || (tr.dataset.transp || '') === filterTransp;
    const show = periodOk && transpOk;
    tr.style.display = show ? '' : 'none';
    const nx = tr.nextElementSibling;
    if (nx && nx.tagName === 'TBODY' && !show) nx.style.display = 'none';
  }});
  _recalcDamagedTotals();
}}

function _rebuildTranspRanking() {{
  const agg = {{}};
  Object.entries(DAMAGED_MONTHLY).forEach(([did, monthly]) => {{
    let total = 0, bpp = 0;
    Object.entries(monthly).forEach(([ym, v]) => {{
      if ((!_periodDe || ym >= _periodDe) && (!_periodAte || ym <= _periodAte)) {{
        total += v.total; bpp += v.bpp;
      }}
    }});
    if (total === 0) return;
    const transp = DRIVER_TRANSP[did] || 'N/A';
    if (!agg[transp]) agg[transp] = {{total: 0, bpp: 0, drivers: new Set()}};
    agg[transp].total += total;
    agg[transp].bpp    = Math.round((agg[transp].bpp + bpp) * 100) / 100;
    agg[transp].drivers.add(did);
  }});
  const ranking = Object.entries(agg)
    .map(([transp, v]) => ({{transp, total: v.total, bpp: v.bpp, n: v.drivers.size}}))
    .sort((a, b) => b.total - a.total).slice(0, 15);
  const container = document.getElementById('transp_damaged_ranking');
  if (!container) return;
  if (ranking.length === 0) {{ container.innerHTML = ''; return; }}
  const maxT = ranking[0].total || 1;
  const clr  = (i) => i < 3 ? '#f87171' : i < 7 ? '#f59e0b' : '#6b7280';
  let rows = '';
  ranking.forEach((r, i) => {{
    const barW = Math.max(4, Math.round(r.total / maxT * 100));
    const c = clr(i);
    const fmtBpp = '$' + r.bpp.toLocaleString('en-US', {{minimumFractionDigits:2,maximumFractionDigits:2}});
    rows += '<tr>' +
      '<td style="text-align:center;color:#4b5563;font-size:11px">' + (i+1) + '</td>' +
      '<td style="font-weight:600;color:#f9fafb">' + r.transp +
        '<div style="height:3px;background:#1a2035;border-radius:2px;margin-top:3px;width:140px">' +
        '<div style="height:3px;background:' + c + ';border-radius:2px;width:' + barW + '%"></div></div></td>' +
      '<td style="text-align:center;font-weight:700;color:' + c + '">' + r.total + '</td>' +
      '<td style="color:#10b981">' + fmtBpp + '</td>' +
      '<td style="text-align:center;color:#9ca3af">' + r.n + '</td>' +
      '</tr>';
  }});
  container.innerHTML =
    '<div class="tbl-title" style="color:#f59e0b">Ranking de Transportadoras — Damaged (' + ranking.length + ' transportadora(s))' +
    '<span style="font-weight:400;font-size:11px;color:#9ca3af;margin-left:8px">· reativo ao período</span></div>' +
    '<div class="tbl-scroll"><table>' +
    '<thead><tr><th style="text-align:center">#</th><th>Transportadora</th>' +
    '<th style="text-align:center">Total Damaged</th><th>BPP Total</th>' +
    '<th style="text-align:center">Drivers</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table></div>';
}}

function setPeriodo() {{
  const de  = document.getElementById('pd_de').value;
  const ate = document.getElementById('pd_ate').value;
  _periodDe  = de;
  _periodAte = ate;

  const meses = MONTHLY.filter(m => (!de || m.key >= de) && (!ate || m.key <= ate));
  const dt = (!de && !ate) ? ANNUAL
    : meses.length > 0
      ? {{ fraudes: meses.reduce((s,m)=>s+m.fraudes,0), damaged: meses.reduce((s,m)=>s+m.damaged,0), bpp: meses.reduce((s,m)=>s+m.bpp,0) }}
      : {{ fraudes:0, damaged:0, bpp:0 }};

  document.getElementById('cv-fraudes').textContent = dt.fraudes.toLocaleString('pt-BR');
  document.getElementById('cv-damaged').textContent = dt.damaged.toLocaleString('pt-BR');
  document.getElementById('cv-bpp').textContent = '$' + dt.bpp.toLocaleString('en-US', {{minimumFractionDigits:2, maximumFractionDigits:2}});

  const lbl_de  = de  ? (MONTHLY.find(m=>m.key===de)?.label  || de)  : '';
  const lbl_ate = ate ? (MONTHLY.find(m=>m.key===ate)?.label || ate) : '';
  const lbl = (!de && !ate) ? '{d["ano"]}'
    : (lbl_de && lbl_ate) ? lbl_de + ' → ' + lbl_ate
    : lbl_de || lbl_ate;
  document.getElementById('sub-fraudes').textContent = lbl;
  document.getElementById('sub-damaged').textContent = lbl;
  document.getElementById('sub-bpp').textContent = 'Cashout ' + lbl;
  document.getElementById('pd_label').textContent = (!de && !ate) ? '' : '📅 ' + lbl;

  applyPeriodoToTab(_currentTab);
  _updateAllTabCounts();
  renderDrMes(); renderCrzMes();
  try {{ renderOfensores(); }} catch(e) {{}}
}}

// Atualiza contadores de todas as abas sem mexer nas linhas das abas inativas
function _updateAllTabCounts() {{
  const _set = (id, v) => {{ const e = document.getElementById(id); if (e) e.textContent = v; }};
  const countMonths = (tblId) => {{
    let n = 0;
    const tbl = document.getElementById(tblId);
    if (!tbl) return 0;
    tbl.querySelectorAll('tbody > tr[data-months]').forEach(tr => {{
      const months = (tr.dataset.months||'').split(' ');
      if ((!_periodDe && !_periodAte) || months.some(m => (!_periodDe||m>=_periodDe)&&(!_periodAte||m<=_periodAte))) n++;
    }});
    return n;
  }};
  // tab-count-acumulo é estático (gerado no Python), não precisa de updateCountCards
  _set('tab-count-dxp',     countMonths('tbl_dxp'));
  _set('tab-count-places',  countMonths('tbl_places'));
  _set('tab-count-damaged', countMonths('tbl_damaged'));
  let blCount = 0;
  document.querySelectorAll('.bl-row').forEach(tr => {{
    const ym = (tr.dataset.data||'').substring(0,7);
    if ((!_periodDe||ym>=_periodDe)&&(!_periodAte||ym<=_periodAte)) blCount++;
  }});
  _set('tab-count-bloqueios', blCount);
}}

function resetPeriodo() {{
  const first = MONTHLY.length > 0 ? MONTHLY[0].key                   : '';
  const last  = MONTHLY.length > 0 ? MONTHLY[MONTHLY.length-1].key   : '';
  document.getElementById('pd_de').value  = first;
  document.getElementById('pd_ate').value = last;
  setPeriodo();
}}

function updateCountCards() {{
  function vis(sel) {{
    let n = 0;
    document.querySelectorAll(sel).forEach(tr => {{ if (tr.style.display !== 'none') n++; }});
    return n;
  }}
  function set(id, v) {{ const e = document.getElementById(id); if (e) e.textContent = v; }}

  // Drivers críticos — contagem estática (prioridade calculada no Python sobre todos os dados)
  set('cv-criticos', CRITICOS_COUNT);

  // Contagem period-aware (independe da aba ter sido visitada)
  const _cntMths = (tblId) => {{
    let n = 0;
    const tbl = document.getElementById(tblId);
    if (!tbl) return 0;
    tbl.querySelectorAll('tbody > tr[data-months]').forEach(tr => {{
      const months = (tr.dataset.months||'').split(' ');
      if ((!_periodDe && !_periodAte) || months.some(m => (!_periodDe||m>=_periodDe)&&(!_periodAte||m<=_periodAte))) n++;
    }});
    return n;
  }};

  // Places
  const placesN = _cntMths('tbl_places');
  set('cv-places', placesN);
  set('tab-count-places', placesN);

  // DxP
  set('tab-count-dxp', _cntMths('tbl_dxp'));

  // Damaged
  set('tab-count-damaged', _cntMths('tbl_damaged'));

  // Cruzados F+D — filtra tabela e alerta
  let cruzados = 0;
  document.querySelectorAll('#tbl_cruzados > tbody > tr[data-months]').forEach(tr => {{
    if (tr.style.display !== 'none') cruzados++;
  }});
  set('cv-cruzados', cruzados);
  set('num-cruzados-alert', cruzados);
  set('num-cruzados-txt', cruzados);
  const alerta = document.getElementById('alerta-cruzados');
  const wrapCruz = document.getElementById('wrap-cruzados');
  if (alerta)   alerta.style.display   = cruzados > 0 ? '' : 'none';
  if (wrapCruz) wrapCruz.style.display = cruzados > 0 ? '' : 'none';
}}

// Dados para Ofensores do Período (precisa estar antes do IIFE que chama setPeriodo)
const _drMes  = {j(d["monthly_dr"])};
const _crzMes = {j(d["crz_mes"])};

// Inicializa: intervalo completo disponível
(function() {{
  const first = MONTHLY.length > 0 ? MONTHLY[0].key                 : '';
  const last  = MONTHLY.length > 0 ? MONTHLY[MONTHLY.length-1].key : '';
  document.getElementById('pd_de').value  = first;
  document.getElementById('pd_ate').value = last;
  setPeriodo();
}})();

// Ofensores do Período — reage ao filtro global pd_de/pd_ate
function renderDrMes() {{
  const meses = Object.keys(_drMes).filter(m => (!_periodDe || m >= _periodDe) && (!_periodAte || m <= _periodAte));
  const acc = {{}};
  meses.forEach(m => {{
    (_drMes[m] || []).forEach(r => {{
      if (!acc[r.id]) acc[r.id] = {{id: r.id, fraudes: 0, damaged: 0, bpp: 0}};
      acc[r.id].fraudes += r.fraudes;
      acc[r.id].damaged += r.damaged;
      acc[r.id].bpp     += r.bpp;
    }});
  }});
  const rows = Object.values(acc)
    .map(r => ({{...r, score: r.fraudes * 3 + r.damaged}}))
    .sort((a, b) => b.score - a.score)
    .slice(0, 10);
  const tb = document.getElementById('dr-mes-tbody');
  if (tb) tb.innerHTML = rows.length ? rows.map((r, i) =>
    `<tr><td style="color:#6b7280">#${{i+1}}</td><td style="color:#e2e8f0;font-weight:500">${{r.id}}</td><td style="color:#ef4444">${{r.fraudes}}</td><td style="color:#f59e0b">${{r.damaged}}</td><td>${{r.bpp.toFixed(2)}}</td><td style="color:#FFE600;font-weight:700">${{r.score}}</td></tr>`
  ).join('') : '<tr><td colspan="6" style="color:#6b7280;text-align:center;padding:16px">Sem dados para este período</td></tr>';
  // Atualiza gráfico Top 10 Drivers com dados do período filtrado
  if (_cDriversChart && rows.length) {{
    _cDriversChart.data.labels = rows.map(r => r.id);
    _cDriversChart.data.datasets[0].data = rows.map(r => r.fraudes);
    _cDriversChart.data.datasets[1].data = rows.map(r => r.damaged);
    _cDriversChart.update();
  }}
}}
function renderCrzMes() {{
  const meses = Object.keys(_crzMes).filter(m => (!_periodDe || m >= _periodDe) && (!_periodAte || m <= _periodAte));
  const accS = {{}}, accB = {{}};
  meses.forEach(m => {{
    const data = _crzMes[m] || {{sellers:[], buyers:[]}};
    data.sellers.forEach(r => {{ accS[r.id] = (accS[r.id] || 0) + r.qtd; }});
    data.buyers.forEach(r  => {{ accB[r.id] = (accB[r.id] || 0) + r.qtd; }});
  }});
  const toRows = acc => Object.entries(acc).map(([id, qtd]) => ({{id, qtd}})).sort((a, b) => b.qtd - a.qtd).slice(0, 10);
  const sellers = toRows(accS), buyers = toRows(accB);
  const tbS = document.getElementById('crz-sellers-mes-tbody');
  const tbB = document.getElementById('crz-buyers-mes-tbody');
  if (tbS) tbS.innerHTML = sellers.length ? sellers.map((r, i) =>
    `<tr><td style="color:#6b7280">#${{i+1}}</td><td style="color:#e2e8f0">${{r.id}}</td><td style="color:#f59e0b;font-weight:700">${{r.qtd}}</td></tr>`
  ).join('') : '<tr><td colspan="3" style="color:#6b7280;text-align:center;padding:12px">Sem dados</td></tr>';
  if (tbB) tbB.innerHTML = buyers.length ? buyers.map((r, i) =>
    `<tr><td style="color:#6b7280">#${{i+1}}</td><td style="color:#e2e8f0">${{r.id}}</td><td style="color:#60a5fa;font-weight:700">${{r.qtd}}</td></tr>`
  ).join('') : '<tr><td colspan="3" style="color:#6b7280;text-align:center;padding:12px">Sem dados</td></tr>';
  // Atualiza cards de totais do período (sellers e buyers são reativos; pares/drivers ficam estáticos)
  const _s = (id, v) => {{ const e = document.getElementById(id); if (e) e.textContent = v; }};
  _s('crz-cv-sellers', Object.keys(accS).length);
  _s('crz-cv-buyers',  Object.keys(accB).length);
}}

// Tendência — Gráfico combo barras (Fraudes+Damaged) + linha (BPP)
let _tendChart = null;
function renderTendencia() {{
  const meses = MONTHLY.filter(m => (!_periodDe || m.key >= _periodDe) && (!_periodAte || m.key <= _periodAte));
  if (!meses.length) return;

  const labels  = meses.map(m => m.label);
  const fraudes = meses.map(m => m.fraudes);
  const damaged = meses.map(m => m.damaged);
  const bpp     = meses.map(m => m.bpp);

  const totF = fraudes.reduce((s,v) => s+v, 0);
  const totD = damaged.reduce((s,v) => s+v, 0);
  const totB = bpp.reduce((s,v) => s+v, 0);
  const pico  = meses.reduce((a, m) => m.fraudes > a.fraudes ? m : a, meses[0]);

  const _s = (id, v) => {{ const e = document.getElementById(id); if (e) e.textContent = v; }};
  _s('tend-cv-fraudes', totF.toLocaleString('pt-BR'));
  _s('tend-cv-damaged', totD.toLocaleString('pt-BR'));
  _s('tend-cv-bpp', '$' + totB.toLocaleString('en-US', {{minimumFractionDigits:2, maximumFractionDigits:2}}));
  _s('tend-cv-pico',  pico.fraudes.toLocaleString('pt-BR'));
  _s('tend-cd-pico',  pico.label);

  // Tabela de detalhe
  const tb = document.getElementById('tend-tbody');
  if (tb) tb.innerHTML = meses.map(m => {{
    const score = m.fraudes * 3 + m.damaged;
    const isPico = m.key === pico.key;
    return `<tr style="${{isPico ? 'background:#1a0e0e' : ''}}">
      <td style="color:${{isPico ? '#f87171' : '#e2e8f0'}};font-weight:${{isPico ? '600' : '400'}}">${{m.label}}${{isPico ? ' ★' : ''}}</td>
      <td style="text-align:right;color:#ef4444">${{m.fraudes}}</td>
      <td style="text-align:right;color:#f59e0b">${{m.damaged}}</td>
      <td style="text-align:right;color:#FFE600">$${{m.bpp.toLocaleString('en-US', {{minimumFractionDigits:2, maximumFractionDigits:2}})}}</td>
      <td style="text-align:right;color:#9ca3af">${{score}}</td>
    </tr>`;
  }}).join('');

  const canvas = document.getElementById('cTendencia');
  if (!canvas) return;

  const datasets = [
    {{label:'Fraudes', data:fraudes, backgroundColor:'rgba(239,68,68,0.75)', stack:'s', order:2}},
    {{label:'Damaged', data:damaged, backgroundColor:'rgba(245,158,11,0.65)', stack:'s', order:2}},
    {{type:'line', label:'BPP USD', data:bpp, borderColor:'#FFE600', backgroundColor:'transparent',
      tension:0.4, pointRadius:4, pointBackgroundColor:'#FFE600', borderWidth:2, yAxisID:'y2', order:1}}
  ];

  if (_tendChart) {{
    _tendChart.data.labels = labels;
    _tendChart.data.datasets.forEach((ds, i) => {{ ds.data = datasets[i].data; }});
    _tendChart.update();
  }} else {{
    _tendChart = new Chart(canvas, {{
      type:'bar',
      data:{{labels, datasets}},
      options:{{
        responsive:true, maintainAspectRatio:false,
        interaction:{{mode:'index', intersect:false}},
        plugins:{{
          legend:{{display:true, position:'top', labels:{{color:'#9ca3af', font:{{size:11}}, boxWidth:12}}}},
          tooltip:{{callbacks:{{label: ctx => ctx.dataset.label === 'BPP USD'
            ? ' BPP: $' + ctx.raw.toLocaleString('en-US', {{minimumFractionDigits:2}})
            : ' ' + ctx.dataset.label + ': ' + ctx.raw}}}}
        }},
        scales:{{
          x:{{grid:{{display:false}}, ticks:{{color:'#6b7280', font:{{size:11}}}}}},
          y:{{stacked:true, grid:{{color:'rgba(255,255,255,0.04)'}}, ticks:{{color:'#6b7280', font:{{size:11}}}},
             title:{{display:true, text:'Ocorrências', color:'#4b5563', font:{{size:10}}}}}},
          y2:{{position:'right', grid:{{display:false}},
              ticks:{{color:'#b5a205', font:{{size:11}}, callback: v => '$' + Math.round(v).toLocaleString('en-US')}},
              title:{{display:true, text:'BPP USD', color:'#b5a205', font:{{size:10}}}}}}
        }}
      }}
    }});
  }}
}}

{diario_js()}

// ── GERAR PPTX ──────────────────────────────────────────────
async function gerarPptx(driverId) {{
  const c = ACUMULO_DATA.find(x => String(x.id) === String(driverId));
  if (!c) {{ alert('Dados do driver não encontrados.'); return; }}

  const pptx = new PptxGenJS();
  pptx.layout  = 'LAYOUT_WIDE';
  pptx.author  = 'SSP30 Loss Prevention';
  pptx.subject = 'Solicitação de Bloqueio de Driver';

  const BG    = '0B0F1C';
  const PANEL = '131928';
  const WH    = 'FFFFFF';
  const RED   = 'C0392B';
  const GRAY  = '8A93A8';
  const LINE  = '1E2740';
  const TIPO_LBL = {{fraude_pura:'Fraude',lost_fraude:'Lost + Fraude',outro:'Outro'}};
  const today    = new Date().toLocaleDateString('pt-BR');
  const nomeExib = c.nome || '—';
  const placa    = c.placa || '—';
  const transp   = c.transportadora || '—';
  const status   = c.status_bl || c.status || '—';
  const tentativaBloqueio = c.data_solicitacao
    ? ('Sim — ' + c.data_solicitacao)
    : 'Nao';
  const aptoTxt  = c.apto
    ? (c.tipo === 'fraude_pura' ? 'Acionar time de fraude para validacao' : 'Apto para bloqueio')
    : (c.motivo || 'Nao apto');

  // ── Slide 1: CAPA ────────────────────────────────────────
  const s1 = pptx.addSlide();
  s1.background = {{color: BG}};

  // Barra vermelha topo
  s1.addShape(pptx.ShapeType.rect, {{x:0, y:0, w:13.33, h:0.08, fill:{{color:RED}}}});

  // Coluna esquerda — identidade do caso
  s1.addText('SOLICITACAO DE BLOQUEIO', {{
    x:0.5, y:0.3, w:6.5, h:0.35,
    fontSize:9, bold:true, color:RED, fontFace:'Calibri', charSpacing:2
  }});
  s1.addText('Loss Prevention — SSP30', {{
    x:0.5, y:0.62, w:6.5, h:0.3,
    fontSize:10, color:GRAY, fontFace:'Calibri'
  }});

  // Linha divisória vertical
  s1.addShape(pptx.ShapeType.rect, {{x:6.9, y:0.25, w:0.02, h:6.9, fill:{{color:LINE}}}});

  // Driver ID destaque
  s1.addText(String(c.id), {{
    x:0.5, y:1.15, w:6.0, h:1.1,
    fontSize:56, bold:true, color:WH, fontFace:'Calibri'
  }});

  // Nome do driver
  s1.addText(nomeExib, {{
    x:0.5, y:2.2, w:6.0, h:0.5,
    fontSize:16, bold:false, color:GRAY, fontFace:'Calibri'
  }});

  // Separador
  s1.addShape(pptx.ShapeType.rect, {{x:0.5, y:2.75, w:6.0, h:0.015, fill:{{color:LINE}}}});

  // Grade de campos — 2 colunas
  const campos = [
    ['Placa',          placa],
    ['Transportadora', transp],
    ['Tentativa de bloqueio', tentativaBloqueio],
    ['Tipo',           TIPO_LBL[c.tipo] || c.tipo],
    ['Meses de acumulo', String(c.n_meses) + ' meses'],
    ['Pacotes (FRAUD/LOST)', String(c.n_pkgs)],
    ['BPP Total',      '$' + c.total_bpp.toLocaleString('pt-BR',{{minimumFractionDigits:2}})],
    ['Data da solicitacao', today],
  ];
  campos.forEach((f, i) => {{
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x   = 0.5 + col * 3.15;
    const y   = 2.95 + row * 0.88;
    s1.addText(f[0].toUpperCase(), {{x, y, w:2.9, h:0.25, fontSize:7, bold:true, color:GRAY, fontFace:'Calibri', charSpacing:1}});
    s1.addText(f[1], {{x, y:y+0.24, w:2.9, h:0.45, fontSize:13, bold:true, color:WH, fontFace:'Calibri'}});
  }});

  // Conclusao
  s1.addShape(pptx.ShapeType.rect, {{x:0.5, y:6.45, w:6.0, h:0.015, fill:{{color:LINE}}}});
  s1.addText('CONCLUSAO', {{x:0.5, y:6.55, w:2, h:0.25, fontSize:7, bold:true, color:GRAY, fontFace:'Calibri', charSpacing:1}});
  s1.addText(aptoTxt, {{x:0.5, y:6.78, w:6.0, h:0.35, fontSize:12, bold:true, color:RED, fontFace:'Calibri'}});

  // Coluna direita — branding
  s1.addText('LOSS', {{
    x:7.1, y:2.6, w:5.8, h:1.4,
    fontSize:72, bold:true, color:LINE, align:'center', fontFace:'Calibri'
  }});
  s1.addText('PREVENTION', {{
    x:7.1, y:3.85, w:5.8, h:1.0,
    fontSize:38, bold:true, color:LINE, align:'center', fontFace:'Calibri'
  }});
  s1.addShape(pptx.ShapeType.rect, {{x:7.5, y:4.85, w:4.8, h:0.04, fill:{{color:RED}}}});
  s1.addText('Mercado Livre — Guarulhos Mega', {{
    x:7.1, y:5.05, w:5.8, h:0.35, align:'center',
    fontSize:10, color:GRAY, fontFace:'Calibri'
  }});

  // Rodape
  s1.addShape(pptx.ShapeType.rect, {{x:0, y:7.3, w:13.33, h:0.015, fill:{{color:LINE}}}});
  s1.addText('CONFIDENCIAL — Uso interno. Este documento e de uso restrito ao time de Loss Prevention.', {{
    x:0.5, y:7.33, w:12.5, h:0.2,
    fontSize:7, color:GRAY, fontFace:'Calibri'
  }});

  // ── Slide 2: DADOS E EVIDENCIAS ───────────────────────────
  const s2 = pptx.addSlide();
  s2.background = {{color: BG}};
  s2.addShape(pptx.ShapeType.rect, {{x:0, y:0, w:13.33, h:0.08, fill:{{color:RED}}}});

  // Header
  s2.addText('DADOS E EVIDENCIAS', {{
    x:0.5, y:0.25, w:9, h:0.38,
    fontSize:9, bold:true, color:RED, fontFace:'Calibri', charSpacing:2
  }});
  s2.addText('Driver ' + String(c.id) + '  —  ' + nomeExib + '  |  Placa: ' + placa + '  |  ' + transp, {{
    x:0.5, y:0.6, w:12.3, h:0.32,
    fontSize:11, color:GRAY, fontFace:'Calibri'
  }});
  s2.addShape(pptx.ShapeType.rect, {{x:0.5, y:0.92, w:12.3, h:0.015, fill:{{color:LINE}}}});

  // Tabela
  const hdrs = [
    {{text:'SEMANA',        options:{{bold:true, color:GRAY, fontSize:8, fill:PANEL, align:'center', charSpacing:1}}}},
    {{text:'CLASSIFICACAO', options:{{bold:true, color:GRAY, fontSize:8, fill:PANEL, charSpacing:1}}}},
    {{text:'SHIPMENT ID',   options:{{bold:true, color:GRAY, fontSize:8, fill:PANEL, charSpacing:1}}}},
    {{text:'BPP (USD)',     options:{{bold:true, color:GRAY, fontSize:8, fill:PANEL, align:'right', charSpacing:1}}}},
  ];
  const tblRows = [hdrs];
  let bppTot = 0;
  (c.shps || []).forEach((shp, idx) => {{
    const bpp_v   = parseFloat(shp.bpp || 0);
    const isMaior = Math.abs(bpp_v - c.max_bpp) < 0.02;
    const isFraud = shp.class && shp.class.includes('FRAUD');
    const rowBg   = idx % 2 === 0 ? BG : PANEL;
    let sem = shp.semana || '';
    if (sem.includes('-W')) sem = 'Sem. ' + sem.split('-W')[1].replace(/^0+/,'');
    bppTot += bpp_v;
    tblRows.push([
      {{text:sem,                   options:{{fontSize:9, color:GRAY, align:'center', fill:rowBg}}}},
      {{text:shp.class || '—',      options:{{fontSize:9, color:isFraud?'E57373':WH, fill:rowBg}}}},
      {{text:String(shp.id || '—'), options:{{fontSize:9, color:WH, fontFace:'Courier New', fill:rowBg}}}},
      {{text:'$' + bpp_v.toLocaleString('pt-BR',{{minimumFractionDigits:2}}) + (isMaior?' *':''),
        options:{{fontSize:9, color:isMaior?'F59E0B':WH, align:'right', bold:isMaior, fill:rowBg}}}},
    ]);
  }});
  s2.addTable(tblRows, {{
    x:0.5, y:1.1, w:12.3,
    rowH:0.26,
    border:{{color:LINE, pt:0.5}},
    fontFace:'Calibri',
  }});

  // Linha de total
  const yTot = 1.1 + 0.26 * tblRows.length + 0.12;
  s2.addShape(pptx.ShapeType.rect, {{x:8.5, y:yTot, w:4.3, h:0.015, fill:{{color:LINE}}}});
  s2.addText('TOTAL', {{x:8.5, y:yTot+0.05, w:2.5, h:0.3, fontSize:8, bold:true, color:GRAY, fontFace:'Calibri', align:'right', charSpacing:1}});
  s2.addText('$' + bppTot.toLocaleString('pt-BR',{{minimumFractionDigits:2}}), {{
    x:11.1, y:yTot+0.05, w:1.7, h:0.3,
    fontSize:12, bold:true, color:WH, fontFace:'Calibri', align:'right'
  }});

  // Criterio
  let crit = c.tipo === 'lost_fraude'
    ? 'Criterio Lost+Fraude: ' + c.n_pkgs + ' pacotes (min. 5) | BPP sem maior: $' + c.residual.toFixed(2) + ' (min. $300) | ' + (c.apto ? 'APTO' : 'NAO APTO')
    : 'Criterio Fraude Pura: acumulo em 3+ meses — encaminhar para validacao do time de fraude';
  s2.addText(crit, {{
    x:0.5, y:7.1, w:12.3, h:0.22,
    fontSize:8, color:GRAY, fontFace:'Calibri', italic:true
  }});
  s2.addShape(pptx.ShapeType.rect, {{x:0, y:7.3, w:13.33, h:0.015, fill:{{color:LINE}}}});
  s2.addText('CONFIDENCIAL — Uso interno. Este documento e de uso restrito ao time de Loss Prevention.', {{
    x:0.5, y:7.33, w:12.5, h:0.2, fontSize:7, color:GRAY, fontFace:'Calibri'
  }});

  // ── Slide 3: ENCERRAMENTO ─────────────────────────────────
  const s3 = pptx.addSlide();
  s3.background = {{color: BG}};
  s3.addShape(pptx.ShapeType.rect, {{x:0, y:0, w:13.33, h:0.08, fill:{{color:RED}}}});
  s3.addShape(pptx.ShapeType.rect, {{x:0, y:7.3, w:13.33, h:0.015, fill:{{color:LINE}}}});
  s3.addText('LOSS PREVENTION', {{
    x:0, y:2.9, w:13.33, h:1.0,
    fontSize:48, bold:true, color:LINE, align:'center', fontFace:'Calibri'
  }});
  s3.addShape(pptx.ShapeType.rect, {{x:4.5, y:3.95, w:4.3, h:0.04, fill:{{color:RED}}}});
  s3.addText('Mercado Livre — SSP30 — Guarulhos Mega', {{
    x:0, y:4.15, w:13.33, h:0.4,
    fontSize:12, color:GRAY, align:'center', fontFace:'Calibri'
  }});
  s3.addText('Gerado em ' + today + ' pelo sistema LP Dashboard', {{
    x:0, y:4.7, w:13.33, h:0.3,
    fontSize:10, color:LINE, align:'center', fontFace:'Calibri'
  }});
  s3.addText('CONFIDENCIAL — Uso interno. Este documento e de uso restrito ao time de Loss Prevention.', {{
    x:0.5, y:7.33, w:12.5, h:0.2, fontSize:7, color:GRAY, fontFace:'Calibri'
  }});

  const nomeArq = 'bloqueio_' + c.id + '_' + today.split('/').join('-') + '.pptx';
  await pptx.writeFile({{fileName: nomeArq}});
}}
// ── FIM GERAR PPTX ──────────────────────────────────────────

function irPara(tab) {{
  var el = document.querySelector('.sb-item[data-tab="' + tab + '"]');
  if (el) showTab(tab, el);
}}

// === STATUS DO SERVIDOR ===
function checkSrv() {{
  fetch('http://localhost:5000/ping', {{signal: AbortSignal.timeout(2000)}})
    .then(r => r.json()).then(r => setSrv(r.ok === true))
    .catch(() => setSrv(false));
}}
function setSrv(online) {{
  const dot = document.getElementById('srv-dot');
  const lbl = document.getElementById('srv-label');
  const box = document.getElementById('srv-status');
  if (!dot) return;
  dot.style.background = online ? '#4ade80' : '#374151';
  if (lbl) lbl.textContent = online ? 'Online' : 'Servidor';
  if (box) box.style.borderColor = online ? '#166534' : '#1f2937';
}}
checkSrv();
setInterval(checkSrv, 30000);

// === RELATÓRIO SEMANAL ===
const WP_BASE = {j(_wp_base)};
const EM_BASE = {j(_em_base)};
var _wyCount = null, _rtCount = null;
function carregarTratados() {{
  const base = 'http://localhost:5000';
  Promise.allSettled([
    fetch(base + '/ow_values?tab=wy').then(r => r.json()),
    fetch(base + '/ow_values?tab=rt').then(r => r.json())
  ]).then(results => {{
    const wyRes = results[0];
    const rtRes = results[1];
    const wyTrat = wyRes.status === 'fulfilled'
      ? Object.values(wyRes.value).filter(v => (v.acao||'').trim() || (v.final||'').trim()).length
      : null;
    const rtTrat = rtRes.status === 'fulfilled'
      ? Object.values(rtRes.value).filter(v => (v.acao||'').trim() || (v.final||'').trim()).length
      : null;
    _wyCount = wyTrat;
    _rtCount = rtTrat;
    const wyEl = document.getElementById('rel-wy-count');
    const rtEl = document.getElementById('rel-rt-count');
    if (wyEl) wyEl.textContent = wyTrat !== null ? wyTrat : '—';
    if (rtEl) rtEl.textContent = 'ON ROUTE: ' + (rtTrat !== null ? rtTrat : '—');
  }});
}}
function copiarRelatorio(modo) {{
  const obs = (document.getElementById('rel-obs')?.value || '').trim();
  const wy  = _wyCount !== null ? String(_wyCount) : '—';
  const rt  = _rtCount !== null ? String(_rtCount) : '—';
  let txt = modo === 'whatsapp' ? WP_BASE : EM_BASE;
  if (modo === 'whatsapp') {{
    txt += '• Tratados ON WAY: ' + wy + '\\n• Tratados ON ROUTE: ' + rt;
    if (obs) txt += '\\n\\n📝 *Observações:* ' + obs;
  }} else {{
    txt += 'Tratados ON WAY: ' + wy + ' | Tratados ON ROUTE: ' + rt;
    if (obs) txt += '\\n\\nObservações: ' + obs;
  }}
  navigator.clipboard.writeText(txt).then(() => {{
    const t = document.getElementById('rel-toast');
    if (t) {{ t.style.display = 'block'; setTimeout(() => {{ t.style.display = 'none'; }}, 2000); }}
  }}).catch(() => {{
    const ta = document.createElement('textarea');
    ta.value = txt; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
    const t = document.getElementById('rel-toast');
    if (t) {{ t.style.display = 'block'; setTimeout(() => {{ t.style.display = 'none'; }}, 2000); }}
  }});
}}

// ── INVESTIGAÇÃO LP — helper global ──────────────────────────
function _setEl(id, v) {{ const e = document.getElementById(id); if (e) e.textContent = v; }}

// ── SAÍDAS MÚLTIPLAS ─────────────────────────────────────────
function initSaidas() {{
  const sel = document.getElementById('sm-filtro-mes');
  if (sel && sel.options.length <= 1) {{
    const meses = [...new Set(SAIDAS_DATA.map(r => r.data_ini.slice(0,7)))].sort();
    meses.forEach(m => {{ const o = document.createElement('option'); o.value = m; o.textContent = m; sel.appendChild(o); }});
  }}
  const badge = document.getElementById('tab-count-saidas');
  if (badge) badge.textContent = SAIDAS_DATA.length;
  filtrarSaidas();
}}
initSaidas();

function filtrarSaidas() {{
  const risco  = (document.getElementById('sm-filtro-risco')||{{}}).value || '';
  const busca  = ((document.getElementById('sm-busca')||{{}}).value || '').toLowerCase();
  const mes    = (document.getElementById('sm-filtro-mes')||{{}}).value || '';
  const RISCO_COR = {{CRITICO:'#f87171',ALTO:'#fb923c',MEDIO:'#fbbf24',BAIXO:'#86efac'}};
  const dados  = SAIDAS_DATA.filter(r =>
    (!risco || r.risco === risco) &&
    (!mes   || r.data_ini.startsWith(mes)) &&
    (!busca || r.id.includes(busca) || (r.motorista||'').toLowerCase().includes(busca) || (r.transportadora||'').toLowerCase().includes(busca))
  );
  const tb = document.getElementById('sm-tbody');
  if (!tb) return;
  tb.innerHTML = dados.slice(0,500).map(r => `
    <tr style="border-bottom:1px solid #1e293b">
      <td style="padding:7px 10px;color:#38bdf8;font-family:monospace">${{r.id}}</td>
      <td style="padding:7px 10px;text-align:center;font-weight:700;color:${{r.tentativas>=3?'#f87171':'#fbbf24'}}">${{r.tentativas}}</td>
      <td style="padding:7px 10px;color:#cbd5e1">${{r.data_ini}}</td>
      <td style="padding:7px 10px;color:#cbd5e1">${{r.data_fim}}</td>
      <td style="padding:7px 10px;color:#e2e8f0">${{r.transportadora||'—'}}</td>
      <td style="padding:7px 10px;color:#e2e8f0;font-family:monospace">${{r.motorista||'—'}}</td>
      <td style="padding:7px 10px"><span style="color:${{r.status==='NOT DELIVERED'?'#f87171':'#86efac'}}">${{r.status||'—'}}</span>${{r.sub_status?`<span style='color:#64748b;font-size:10px'> ${{r.sub_status}}</span>`:''}}</td>
      <td style="padding:7px 10px"><span style="background:${{(RISCO_COR[r.risco]||'#64748b')}}22;color:${{RISCO_COR[r.risco]||'#94a3b8'}};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">${{r.risco}}</span></td>
    </tr>`).join('');
  _setEl('sm-total', dados.length);
  _setEl('sm-criticos', dados.filter(r=>r.risco==='CRITICO').length);
  _setEl('sm-insucesso', dados.filter(r=>r.insucesso).length);
  _setEl('sm-max', dados.length ? Math.max(...dados.map(r=>r.tentativas)) : 0);
  const emEl = document.getElementById('sm-empty');
  if (emEl) emEl.style.display = dados.length ? 'none' : 'block';
}}

// ── DEVOLUÇÕES ────────────────────────────────────────────────
function initDevolucoes() {{
  const sel = document.getElementById('dv-filtro-mes');
  if (sel && sel.options.length <= 1) {{
    const meses = [...new Set(DEVOLUCOES_DATA.map(r => r.data.slice(0,7)))].sort();
    meses.forEach(m => {{ const o = document.createElement('option'); o.value = m; o.textContent = m; sel.appendChild(o); }});
  }}
  const badge = document.getElementById('tab-count-devolucoes');
  if (badge) badge.textContent = DEVOLUCOES_DATA.length;
  const semDados = document.getElementById('dv-sem-dados');
  if (semDados) semDados.style.display = DEVOLUCOES_DATA.length === 0 ? 'block' : 'none';
  filtrarDevolucoes();
}}
initDevolucoes();

function filtrarDevolucoes() {{
  const flujo  = (document.getElementById('dv-filtro-flujo')||{{}}).value || '';
  const classL = (document.getElementById('dv-filtro-class')||{{}}).value || '';
  const busca  = ((document.getElementById('dv-busca')||{{}}).value || '').toLowerCase();
  const mes    = (document.getElementById('dv-filtro-mes')||{{}}).value || '';
  const LP_COR = {{FRAUD:'#f87171',LOST:'#a78bfa',DAMAGED:'#fb923c'}};
  const dados  = DEVOLUCOES_DATA.filter(r =>
    (!flujo  || r.flujo === flujo) &&
    (!classL || r.class_lp === classL) &&
    (!mes    || r.data.startsWith(mes)) &&
    (!busca  || r.id.includes(busca) || (r.seller||'').toLowerCase().includes(busca) || (r.causa||'').toLowerCase().includes(busca))
  );
  _setEl('dv-total', dados.length);
  _setEl('dv-fraud', dados.filter(r=>r.class_lp==='FRAUD').length);
  _setEl('dv-lost',  dados.filter(r=>(r.status_rts||'').includes('LOST')||(r.sub_bko||'').includes('lost')).length);
  const sellersMap = {{}};
  dados.forEach(r => {{
    if (!r.seller_id) return;
    if (!sellersMap[r.seller_id]) sellersMap[r.seller_id] = {{nome:r.seller,cnt:0,fraud:0}};
    sellersMap[r.seller_id].cnt++;
    if (r.class_lp==='FRAUD') sellersMap[r.seller_id].fraud++;
  }});
  const sellers = Object.values(sellersMap).sort((a,b)=>b.cnt-a.cnt);
  _setEl('dv-sellers', sellers.length);
  const stb = document.getElementById('dv-sellers-tbody');
  if (stb) stb.innerHTML = sellers.slice(0,10).map(s =>
    `<tr><td style="padding:4px 6px;color:#e2e8f0">${{s.nome||'—'}}</td><td style="padding:4px 6px;text-align:center;color:#fbbf24;font-weight:600">${{s.cnt}}</td><td style="padding:4px 6px;color:${{s.fraud>0?'#f87171':'#86efac'}}">${{s.fraud>0?`FRAUD (${{s.fraud}})`:'OK'}}</td></tr>`
  ).join('');
  const causaMap = {{}};
  dados.forEach(r => {{ const k = r.causa||'N/A'; causaMap[k]=(causaMap[k]||0)+1; }});
  const ctb = document.getElementById('dv-causa-tbody');
  if (ctb) ctb.innerHTML = Object.entries(causaMap).sort((a,b)=>b[1]-a[1]).slice(0,10).map(([c,n]) =>
    `<tr><td style="padding:4px 6px;color:#e2e8f0">${{c}}</td><td style="padding:4px 6px;text-align:center;color:#fbbf24;font-weight:600">${{n}}</td></tr>`
  ).join('');
  const dtb = document.getElementById('dv-tbody');
  if (dtb) dtb.innerHTML = dados.slice(0,500).map(r => `
    <tr style="border-bottom:1px solid #1e293b">
      <td style="padding:7px 10px;color:#94a3b8">${{r.data}}</td>
      <td style="padding:7px 10px;color:#38bdf8;font-family:monospace">${{r.id}}</td>
      <td style="padding:7px 10px;color:#e2e8f0">${{r.seller||'—'}}</td>
      <td style="padding:7px 10px;color:#cbd5e1">${{r.flujo||'—'}}</td>
      <td style="padding:7px 10px;color:#cbd5e1">${{r.causa||'—'}}</td>
      <td style="padding:7px 10px;color:#94a3b8;font-size:11px">${{r.class||'—'}}</td>
      <td style="padding:7px 10px"><span style="background:${{(LP_COR[r.class_lp]||'#64748b')}}22;color:${{LP_COR[r.class_lp]||'#94a3b8'}};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">${{r.class_lp||'—'}}</span></td>
      <td style="padding:7px 10px;color:${{(r.status_rts||'').includes('LOST')?'#f87171':'#94a3b8'}};font-size:11px">${{r.status_rts||'—'}}</td>
      <td style="padding:7px 10px;color:${{r.status_bko==='not_delivered'?'#f87171':r.status_bko==='delivered'?'#86efac':'#94a3b8'}};font-size:11px">${{r.status_bko||'—'}}</td>
    </tr>`).join('');
  const emEl = document.getElementById('dv-empty');
  if (emEl) emEl.style.display = dados.length ? 'none' : 'block';
}}

// ── SELLERS ENE ──────────────────────────────────────────────
function initSellersENE() {{
  const badge = document.getElementById('tab-count-sellers-ene');
  if (badge) badge.textContent = SELLERS_ENE_DATA.length;
  filtrarSellersENE();
}}
initSellersENE();

function exportCSVSellersENE() {{
  const rows = [['Seller Nome','Seller ID','Qtd ENE','Cashout USD','Causas','Primeira','Última','Meses','SHP IDs']];
  SELLERS_ENE_DATA.forEach(r => rows.push([r.seller_nome,r.seller_id,r.qtd,r.cashout,r.causas,r.primeira,r.ultima,r.meses,r.shp_ids]));
  const csv = rows.map(r => r.map(v => `"${{String(v).replace(/"/g,'""')}}"`).join(',')).join('\\n');
  const a = document.createElement('a'); a.href = 'data:text/csv;charset=utf-8,\\uFEFF' + encodeURIComponent(csv);
  a.download = 'sellers_ene_ssp30.csv'; a.click();
}}

function filtrarSellersENE() {{
  const busca = ((document.getElementById('ene-busca')||{{}}).value || '').toLowerCase();
  const dados = SELLERS_ENE_DATA.filter(r =>
    (!busca || (r.seller_nome||'').toLowerCase().includes(busca) || r.seller_id.includes(busca) || r.shp_ids.toLowerCase().includes(busca))
  );
  _setEl('ene-total-sellers', dados.length);
  _setEl('ene-total-cashout', 'US$ ' + dados.reduce((s,r)=>s+r.cashout,0).toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}}));
  _setEl('ene-total-ene',    dados.reduce((s,r)=>s+r.qtd,0));
  _setEl('ene-total-com-cashout', dados.filter(r=>r.cashout>0).length);
  const tb = document.getElementById('ene-tbody');
  if (!tb) return;
  tb.innerHTML = dados.slice(0,200).map((r,i) => `
    <tr style="border-bottom:1px solid #1e293b;${{i<3?'background:#1a0a0a':''}}" title="SHPs: ${{r.shp_ids}}">
      <td style="padding:7px 10px;text-align:center;color:#64748b;font-size:11px">${{i+1}}</td>
      <td style="padding:7px 10px;color:#38bdf8;font-family:monospace">
        <span style="color:#38bdf8;font-weight:600">${{r.seller_nome||r.seller_id}}</span>
        <span style="color:#475569;font-size:10px;margin-left:4px;font-family:monospace" title="Copiar ID" style="cursor:pointer" onclick="navigator.clipboard.writeText('${{r.seller_id}}')">#${{r.seller_id}}</span>
      </td>
      <td style="padding:7px 10px;text-align:center;font-weight:700;color:${{r.qtd>=10?'#f87171':r.qtd>=5?'#fb923c':'#fbbf24'}}">${{r.qtd}}</td>
      <td style="padding:7px 10px;text-align:right;font-weight:600;color:#86efac">US$ ${{r.cashout.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}})}}</td>
      <td style="padding:7px 10px;color:#94a3b8;font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${{r.causas}}">${{r.causas||'—'}}</td>
      <td style="padding:7px 10px;color:#94a3b8;font-size:11px">${{r.primeira||'—'}}</td>
      <td style="padding:7px 10px;color:#94a3b8;font-size:11px">${{r.ultima||'—'}}</td>
      <td style="padding:7px 10px;color:#64748b;font-size:10px;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{r.meses||'—'}}</td>
    </tr>`).join('');
  const emEl = document.getElementById('ene-empty');
  if (emEl) emEl.style.display = dados.length ? 'none' : 'block';
}}

// ── DAMAGED ENE ──────────────────────────────────────────────
let _deneChartMensal = null;
let _deneChartSellers = null;
let _deneWCDone = false;

function initDamagedENE() {{
  const d = DAMAGED_ENE_DATA;

  // Filtra casos pelo período global
  const allCasos = d.casos || [];
  const casosFiltrados = allCasos.filter(r =>
    (!_periodDe || r.mes >= _periodDe) && (!_periodAte || r.mes <= _periodAte)
  );

  // Re-agrega sellers e meses a partir dos casos filtrados
  const _selMap = {{}};
  const _mesMap = {{}};
  let totalBpp = 0;
  casosFiltrados.forEach(c => {{
    totalBpp += c.bpp || 0;
    const key = c.seller_nome || '—';
    if (!_selMap[key]) _selMap[key] = {{ seller_nome: key, total: 0, bpp: 0, meses: new Set() }};
    _selMap[key].total++;
    _selMap[key].bpp += c.bpp || 0;
    _selMap[key].meses.add(c.mes);
    if (!_mesMap[c.mes]) _mesMap[c.mes] = {{ mes: c.mes, total: 0, bpp: 0 }};
    _mesMap[c.mes].total++;
    _mesMap[c.mes].bpp += c.bpp || 0;
  }});
  const sellers = Object.values(_selMap)
    .map(s => ({{ ...s, n_meses: s.meses.size, meses: [...s.meses].sort() }}))
    .sort((a, b) => b.total - a.total);
  const meses = Object.entries(_mesMap)
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([, v]) => v);

  // KPI cards
  const badge = document.getElementById('tab-count-damaged-ene');
  if (badge) badge.textContent = casosFiltrados.length;
  _setEl('dene-total', casosFiltrados.length.toLocaleString('pt-BR'));
  _setEl('dene-bpp', 'US$ ' + totalBpp.toLocaleString('pt-BR', {{minimumFractionDigits:2,maximumFractionDigits:2}}));
  _setEl('dene-sellers', sellers.length);
  _setEl('dene-meses', meses.length);

  // Monthly chart
  const ctxM = document.getElementById('dene-chart-mensal');
  if (ctxM) {{
    if (_deneChartMensal) {{ _deneChartMensal.destroy(); _deneChartMensal = null; }}
    _deneChartMensal = new Chart(ctxM, {{
      type: 'bar',
      data: {{
        labels: meses.map(m => m.mes),
        datasets: [
          {{ label: 'Casos', data: meses.map(m => m.total), backgroundColor: '#f87171', yAxisID: 'y', order: 2 }},
          {{ label: 'BPP USD', data: meses.map(m => m.bpp), type: 'line', borderColor: '#fbbf24', backgroundColor: 'transparent', tension: 0.3, yAxisID: 'y2', order: 1 }},
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ labels: {{ color: '#94a3b8', font: {{ size: 11 }} }} }} }},
        scales: {{
          x: {{ ticks: {{ color: '#64748b', font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }} }},
          y: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }}, title: {{ display: true, text: 'Casos', color: '#64748b', font: {{ size: 10 }} }} }},
          y2: {{ position: 'right', ticks: {{ color: '#fbbf24', font: {{ size: 10 }}, callback: v => 'US$ '+v.toLocaleString('pt-BR',{{minimumFractionDigits:0}}) }}, grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'BPP USD', color: '#fbbf24', font: {{ size: 10 }} }} }},
        }}
      }}
    }});
  }}

  // Sellers bar chart (top 10)
  const ctxS = document.getElementById('dene-chart-sellers');
  if (ctxS) {{
    if (_deneChartSellers) {{ _deneChartSellers.destroy(); _deneChartSellers = null; }}
    const top = sellers.slice(0, 10);
    _deneChartSellers = new Chart(ctxS, {{
      type: 'bar',
      data: {{
        labels: top.map(s => s.seller_nome),
        datasets: [{{ label: 'Casos', data: top.map(s => s.total), backgroundColor: '#818cf8' }}]
      }},
      options: {{
        indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }} }},
          y: {{ ticks: {{ color: '#e2e8f0', font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }} }},
        }}
      }}
    }});
  }}

  // Word cloud — estático (causas do BT_LP_NODES, independente do período)
  if (!_deneWCDone) {{
    const wcEl = document.getElementById('dene-wordcloud');
    const wc = (d.wordcloud || []);
    if (wcEl && wc.length > 0 && typeof WordCloud !== 'undefined') {{
      _deneWCDone = true;
      const maxCount = wc[0].count || 1;
      WordCloud(wcEl, {{
        list: wc.map(w => [w.word, Math.max(8, Math.round(w.count / maxCount * 48))]),
        gridSize: 8, weightFactor: 1, fontFamily: 'sans-serif',
        color: function() {{
          const colors = ['#60a5fa','#f87171','#fbbf24','#34d399','#a78bfa','#fb923c'];
          return colors[Math.floor(Math.random() * colors.length)];
        }},
        rotateRatio: 0.3, rotationSteps: 2,
        backgroundColor: 'transparent', drawOutOfBound: false,
      }});
    }} else if (wcEl && wc.length === 0) {{
      wcEl.style.display = 'none';
      const em = document.getElementById('dene-wc-empty');
      if (em) em.style.display = 'block';
    }}
  }}

  // Causas recorrentes — estáticas (BT_LP_NODES)
  const clEl = document.getElementById('dene-causas-lista');
  if (clEl) {{
    const causas = (d.causas || []).slice(0, 20);
    if (causas.length === 0) {{
      clEl.innerHTML = '<div style="color:#64748b;font-size:12px;text-align:center;padding:20px">Sem dados de causa disponíveis</div>';
    }} else {{
      const maxT = causas[0].total || 1;
      clEl.innerHTML = causas.map((c,i) => `
        <div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;margin-bottom:3px">
            <span style="font-size:11px;color:#e2e8f0;font-weight:${{i<3?700:400}}">${{c.causa}}</span>
            <span style="font-size:11px;color:#fbbf24;font-weight:700">${{c.total}}</span>
          </div>
          ${{c.causa_l2 ? `<div style="font-size:10px;color:#64748b;margin-bottom:3px">${{c.causa_l2}}</div>` : ''}}
          <div style="height:4px;background:#1e293b;border-radius:2px;overflow:hidden">
            <div style="height:100%;width:${{Math.round(c.total/maxT*100)}}%;background:${{i<3?'#f87171':'#334155'}};border-radius:2px"></div>
          </div>
        </div>`).join('');
    }}
  }}

  // Sellers ranking table — com dropdown expansível de SHP IDs
  const stb = document.getElementById('dene-sellers-tbody');
  if (stb) {{
    stb.innerHTML = sellers.slice(0, 50).map((s,i) => {{
      const casosSeller = (DAMAGED_ENE_DATA.casos || []).filter(c => c.seller_nome === s.seller_nome);
      const idsRows = casosSeller.map(c =>
        '<tr style="background:#070e1c">' +
        '<td style="padding:4px 10px 4px 30px;font-family:monospace;color:#60a5fa;font-size:11px;user-select:all">' + c.shp_id + '</td>' +
        '<td style="padding:4px 10px;text-align:right;color:#f87171;font-size:11px">US$ ' + c.bpp.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}}) + '</td>' +
        '<td style="padding:4px 10px;color:#94a3b8;font-size:11px">' + (c.data||'') + '</td>' +
        '<td style="padding:4px 10px;color:#64748b;font-size:11px" colspan="2">' + (c.mes||'') + '</td>' +
        '<td style="padding:4px 10px;color:#cbd5e1;font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + (c.item_title||'') + '">' + (c.item_title||'—') + '</td>' +
        '</tr>'
      ).join('');
      return (
        '<tr id="dene-sr-' + i + '" style="border-bottom:1px solid #1e293b;cursor:pointer;' + (i<3?'background:#1a0a0a':'') + '" onclick="toggleDeneSeller(' + i + ')">' +
        '<td style="padding:7px 10px;text-align:center;color:#64748b;font-size:11px">' + (i+1) + '</td>' +
        '<td style="padding:7px 10px;color:#38bdf8;font-weight:600">' +
          '<span id="dene-sc-' + i + '" style="display:inline-block;transition:transform .15s;margin-right:5px;font-size:9px;color:#64748b;vertical-align:middle">▶</span>' +
          (s.seller_nome||'—') + '</td>' +
        '<td style="padding:7px 10px;text-align:center;font-weight:700;color:' + (s.total>=10?'#f87171':s.total>=5?'#fb923c':'#fbbf24') + '">' + s.total + '</td>' +
        '<td style="padding:7px 10px;text-align:right;color:#86efac;font-weight:600">US$ ' + s.bpp.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}}) + '</td>' +
        '<td style="padding:7px 10px;text-align:center;color:#818cf8">' + s.n_meses + '</td>' +
        '<td style="padding:7px 10px;color:#64748b;font-size:11px">' + (s.meses||[]).join(' · ') + '</td>' +
        '</tr>' +
        '<tr id="dene-sd-' + i + '" style="display:none">' +
        '<td colspan="6" style="padding:0;border-bottom:2px solid #1e3a5f">' +
        '<div style="padding:6px 0 4px 0;background:#070e1c">' +
        '<table style="width:100%;border-collapse:collapse">' +
        '<thead><tr>' +
        '<th style="padding:4px 10px 4px 30px;text-align:left;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.5px">SHP ID</th>' +
        '<th style="padding:4px 10px;text-align:right;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.5px">BPP USD</th>' +
        '<th style="padding:4px 10px;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Data</th>' +
        '<th style="padding:4px 10px;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.5px" colspan="2">Mês</th>' +
        '<th style="padding:4px 10px;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Produto</th>' +
        '</tr></thead><tbody>' +
        (idsRows || '<tr><td colspan="4" style="padding:8px 30px;color:#64748b;font-size:11px">Sem casos no período selecionado</td></tr>') +
        '</tbody></table></div></td></tr>'
      );
    }}).join('');
  }}

  function toggleDeneSeller(i) {{
    const det = document.getElementById('dene-sd-' + i);
    const chev = document.getElementById('dene-sc-' + i);
    if (!det) return;
    const open = det.style.display !== 'none';
    det.style.display = open ? 'none' : 'table-row';
    if (chev) chev.style.transform = open ? '' : 'rotate(90deg)';
  }}

  filtrarDamagedENE(casosFiltrados);
}}

function filtrarDamagedENE(casosPeriodo) {{
  // Se chamado sem argumento (pelo oninput do search), re-aplica filtro de período
  const base = casosPeriodo || (DAMAGED_ENE_DATA.casos || []).filter(r =>
    (!_periodDe || r.mes >= _periodDe) && (!_periodAte || r.mes <= _periodAte)
  );
  const busca = ((document.getElementById('dene-busca')||{{}}).value || '').toLowerCase();
  const casos = base.filter(r =>
    !busca ||
    r.shp_id.includes(busca) ||
    (r.seller_nome||'').toLowerCase().includes(busca)
  );
  const tb = document.getElementById('dene-casos-tbody');
  const em = document.getElementById('dene-casos-empty');
  if (!tb) return;
  if (casos.length === 0) {{
    tb.innerHTML = '';
    if (em) em.style.display = 'block';
    return;
  }}
  if (em) em.style.display = 'none';
  tb.innerHTML = casos.slice(0, 500).map(r => `
    <tr style="border-bottom:1px solid #1e293b">
      <td style="padding:6px 10px;font-family:monospace;color:#60a5fa;font-size:11px">${{r.shp_id}}</td>
      <td style="padding:6px 10px;color:#e2e8f0;font-size:12px">${{r.seller_nome||'—'}}</td>
      <td style="padding:6px 10px;text-align:right;color:#f87171;font-weight:600;font-size:12px">US$ ${{r.bpp.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}})}}</td>
      <td style="padding:6px 10px;color:#94a3b8;font-size:11px">${{r.data}}</td>
      <td style="padding:6px 10px;color:#64748b;font-size:11px">${{r.mes}}</td>
    </tr>`).join('');
}}

function exportCSVDamagedENE() {{
  const casos = (DAMAGED_ENE_DATA.casos || []).filter(r =>
    (!_periodDe || r.mes >= _periodDe) && (!_periodAte || r.mes <= _periodAte)
  );
  const rows = [['SHP ID','Seller Nome','BPP USD','Data BPP','Mês']];
  casos.forEach(r => rows.push([r.shp_id, r.seller_nome, r.bpp, r.data, r.mes]));
  const csv = rows.map(r => r.map(v => `"${{String(v||'').replace(/"/g,'""')}}"`).join(',')).join('\\n');
  const a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,\\uFEFF' + encodeURIComponent(csv);
  a.download = 'damaged_ene_ssp30.csv';
  a.click();
}}

// ═══════════════════════════════════════════════════════
//  ABA OFENSORES
// ═══════════════════════════════════════════════════════
let _ofensView = 'ene';
let _ofensMetric = 'cashout';
let _ofensChart = null;

function _sellerENEInPeriod(r) {{
  if (!_periodDe && !_periodAte) return true;
  const meses = (r.meses || '').split(',');
  return meses.some(m => m && (!_periodDe || m >= _periodDe) && (!_periodAte || m <= _periodAte));
}}
function _updateOfensKPIs() {{
  const selsFilt = SELLERS_ENE_DATA.filter(_sellerENEInPeriod);
  const cashoutTotal = selsFilt.reduce((s,r) => s + r.cashout, 0);
  const devFilt = DEVOLUCOES_DATA.filter(_inPeriod);
  const eneDamCount = devFilt.filter(r => r.class === 'DAMAGED ENE').length;
  _setEl('ofens-kpi-sellers', selsFilt.length);
  _setEl('ofens-kpi-cashout', 'US$ ' + cashoutTotal.toLocaleString('pt-BR', {{minimumFractionDigits:2,maximumFractionDigits:2}}));
  _setEl('ofens-kpi-comcashout', selsFilt.filter(r => r.cashout > 0).length);
  _setEl('ofens-kpi-devos', devFilt.length);
  _setEl('ofens-kpi-ene-dam', eneDamCount);
}}
function initOfensores() {{
  _updateOfensKPIs();
  renderOfensores();
}}

function _ofensStyleBtn(id, active) {{
  const b = document.getElementById(id);
  if (!b) return;
  const dot = b.querySelector('span');
  if (active) {{
    b.style.color = '#fca311'; b.style.borderBottomColor = '#fca311'; b.style.fontWeight = '700';
    if (dot) dot.style.background = '#fca311';
  }} else {{
    b.style.color = '#64748b'; b.style.borderBottomColor = 'transparent'; b.style.fontWeight = '600';
    if (dot) dot.style.background = '#475569';
  }}
}}
function _ofensStyleMetric(id, active) {{
  const b = document.getElementById(id);
  if (!b) return;
  if (active) {{
    b.style.background = '#92400e'; b.style.color = '#fbbf24'; b.style.border = 'none';
  }} else {{
    b.style.background = 'transparent'; b.style.color = '#64748b'; b.style.border = '1px solid #334155';
  }}
}}

function setOfensView(v) {{
  console.log('[Ofensores] setOfensView:', v);
  _ofensView = v;
  ['ene','seller_devo','buyer_devo','ene_dam_seller','ene_dam_buyer','origem','dominio','ene_svc','buyer_fraude','buyer_vel'].forEach(id => _ofensStyleBtn('ofens-btn-' + id, id === v));
  const mt = document.getElementById('ofens-metric-toggle');
  if (mt) mt.style.display = v === 'ene' ? 'flex' : 'none';
  try {{
    renderOfensores();
  }} catch(e) {{
    console.error('[Ofensores] renderOfensores error:', e);
  }}
}}

function setOfensMetric(m) {{
  _ofensMetric = m;
  ['cashout','qty'].forEach(id => _ofensStyleMetric('ofens-metric-' + id, id === m));
  renderOfensores();
}}

function _ofensTopSellersENE() {{
  return [...SELLERS_ENE_DATA]
    .filter(r => !_isMLInternal(r.seller_nome) && _sellerENEInPeriod(r))
    .sort((a,b) => _ofensMetric === 'cashout' ? b.cashout - a.cashout : b.qtd - a.qtd)
    .slice(0, 10);
}}
const _ML_INTERNAL = ['mercado livre brasil','mercado livre eletronicos','mercadolivre','mercado livre','ml brasil'];
function _isMLInternal(name) {{
  return _ML_INTERNAL.some(n => (name||'').toLowerCase().includes(n));
}}
function _inPeriod(r) {{
  const ym = (r.data||'').substring(0,7);
  return (!_periodDe || ym >= _periodDe) && (!_periodAte || ym <= _periodAte);
}}
function _ofensTopSellersDevo() {{
  const map = {{}};
  DEVOLUCOES_DATA.filter(_inPeriod).forEach(r => {{
    const nome = r.seller || r.seller_nome || '';
    if (!nome || _isMLInternal(nome)) return;
    if (!map[nome]) map[nome] = {{nome, id: r.seller_id||'', shpSet: new Set(), domMap: {{}}}};
    if (r.id) map[nome].shpSet.add(String(r.id));
    const d = (r.dominio||'').trim();
    if (d && d !== 'null') map[nome].domMap[d] = (map[nome].domMap[d]||0) + 1;
  }});
  return Object.values(map)
    .map(v => ({{...v, count: v.shpSet.size, shps: [...v.shpSet],
                dominios: Object.entries(v.domMap).sort((a,b)=>b[1]-a[1]).slice(0,5)}}))
    .sort((a,b) => b.count - a.count).slice(0, 10);
}}
function _ofensTopBuyersDevo() {{
  const map = {{}};
  DEVOLUCOES_DATA.filter(_inPeriod).forEach(r => {{
    if (!r.buyer_id) return;
    if (!map[r.buyer_id]) map[r.buyer_id] = {{id:r.buyer_id, nome:r.buyer_nome||'', estado:r.buyer_uf||r.buyer_estado||'', shpSet: new Set(), domMap: {{}}}};
    if (r.id) map[r.buyer_id].shpSet.add(String(r.id));
    const d = (r.dominio||'').trim();
    if (d && d !== 'null') map[r.buyer_id].domMap[d] = (map[r.buyer_id].domMap[d]||0) + 1;
  }});
  return Object.values(map)
    .map(v => ({{...v, count: v.shpSet.size, shps: [...v.shpSet],
                dominios: Object.entries(v.domMap).sort((a,b)=>b[1]-a[1]).slice(0,5)}}))
    .sort((a,b) => b.count - a.count).slice(0, 10);
}}
function _ofensTopSellersENEDam() {{
  const map = {{}};
  DEVOLUCOES_DATA.filter(r => r.class === 'DAMAGED ENE' && _inPeriod(r)).forEach(r => {{
    const nome = r.seller || r.seller_nome || '';
    if (!nome || _isMLInternal(nome)) return;
    if (!map[nome]) map[nome] = {{nome, id: r.seller_id||'', shpSet: new Set()}};
    if (r.id) map[nome].shpSet.add(String(r.id));
  }});
  return Object.values(map)
    .map(v => ({{...v, count: v.shpSet.size, shps: [...v.shpSet]}}))
    .sort((a,b) => b.count - a.count).slice(0, 15);
}}
function _ofensTopBuyersENEDam() {{
  const map = {{}};
  DEVOLUCOES_DATA.filter(r => r.class === 'DAMAGED ENE' && _inPeriod(r)).forEach(r => {{
    if (!r.buyer_id) return;
    if (!map[r.buyer_id]) map[r.buyer_id] = {{id:r.buyer_id, shpSet: new Set()}};
    if (r.id) map[r.buyer_id].shpSet.add(String(r.id));
  }});
  return Object.values(map)
    .map(v => ({{...v, count: v.shpSet.size, shps: [...v.shpSet]}}))
    .sort((a,b) => b.count - a.count).slice(0, 15);
}}

function _ofensOrigemNodo() {{
  const map = {{}};
  DEVOLUCOES_DATA.filter(_inPeriod).forEach(r => {{
    const n = (r.origem_cross || '').trim();
    if (!n || n === 'null') return;
    if (!map[n]) map[n] = {{node: n, count: 0, shps: [], domMap: {{}}}};
    map[n].count++;
    if (r.id) map[n].shps.push(String(r.id));
    const d = (r.dominio || '').trim();
    if (d && d !== 'null') map[n].domMap[d] = (map[n].domMap[d] || 0) + 1;
  }});
  return Object.values(map)
    .map(v => ({{...v, dominios: Object.entries(v.domMap).sort((a,b) => b[1]-a[1]).slice(0,5)}}))
    .sort((a,b) => b.count - a.count).slice(0, 15);
}}

const _DOM_PT = {{
  'CELLPHONES':'Celulares','PERFUMES':'Perfumes','DESKTOP_COMPUTER_KITS':'Kits de Computador',
  'STREAMING_MEDIA_DEVICES':'Streamings / Smart TV','SUPPLEMENTS':'Suplementos',
  'TOILET_TANKS':'Caixas d\\'Água','KICK_SCOOTERS':'Patinetes','ELECTRIC_BICYCLES':'Bicicletas Elétricas',
  'ELECTRIC_SCOOTERS':'Scooters Elétricos','HEADPHONES':'Fones de Ouvido','DRINKING_GLASSES':'Copos',
  'T_SHIRTS':'Camisetas','ELECTRONIC_PRODUCTS':'Eletrônicos','BICYCLES':'Bicicletas',
  'PANTS':'Calças','KITCHEN_POTS':'Panelas','PLANTS':'Plantas',
  'JACKETS_AND_COATS':'Jaquetas e Casacos','TOILET_SEATS':'Assentos Sanitários','TABLETS':'Tablets',
  'LATEX_ENAMEL_AND_ACRYLIC_PAINTS':'Tintas Acrílicas','SWIMWEAR':'Roupas de Banho',
  'FOOD_STORAGE_CONTAINERS':'Potes de Alimentos','BICYCLE_COMBUSTION_ENGINE_KITS':'Kits Motor Bicicleta',
  'VEHICLE_SPRING_SUSPENSIONS':'Suspensões Veiculares','SHOES':'Calçados','SNEAKERS':'Tênis',
  'MONITORS':'Monitores','PRINTERS':'Impressoras','NOTEBOOKS':'Notebooks','CAMERAS':'Câmeras',
  'SPORTS_SHOES':'Tênis Esportivos','CHAIRS':'Cadeiras','SOFAS':'Sofás','BEDS':'Camas',
  'MATTRESSES':'Colchões','REFRIGERATORS':'Geladeiras','WASHING_MACHINES':'Máquinas de Lavar',
  'AIR_CONDITIONERS':'Ar-Condicionados','TELEVISIONS':'Televisores','VACUUM_CLEANERS':'Aspiradores',
  'BLENDERS':'Liquidificadores','COFFEE_MAKERS':'Cafeteiras','IRONS':'Ferros de Passar',
  'FANS':'Ventiladores','WATER_HEATERS':'Aquecedores','TOOLS':'Ferramentas',
  'POWER_TOOLS':'Ferramentas Elétricas','GENERATORS':'Geradores',
  'GAME_CONSOLES':'Videogames','SMART_WATCHES':'Smartwatches','EARPHONES':'Fones Intra',
  'SPEAKERS':'Caixas de Som','ROUTERS':'Roteadores','HARD_DRIVES':'HDs/SSDs',
  'MEMORY_CARDS':'Cartões de Memória','USB_CABLES':'Cabos USB','CHARGERS':'Carregadores',
  'BACKPACKS':'Mochilas','BAGS':'Bolsas','SUNGLASSES':'Óculos de Sol',
  'WATCHES':'Relógios','JEWELRY':'Joias','COSMETICS':'Cosméticos',
  'VITAMINS':'Vitaminas','PROTEIN_POWDER':'Whey Protein',
}};
function _domPT(d) {{
  if (!d) return '';
  return _DOM_PT[d] || d.replace(/_/g,' ').toLowerCase().replace(/(?:^| )([a-z])/g,(_,c)=>c.toUpperCase());
}}

function _ofensDominio() {{
  const map = {{}};
  DEVOLUCOES_DATA.filter(_inPeriod).forEach(r => {{
    const d = (r.dominio || '').trim();
    if (!d || d === 'null' || d === '') return;
    if (!map[d]) map[d] = {{dominio: d, vertical: r.vertical || '', count: 0, shps: []}};
    map[d].count++;
    if (r.id) map[d].shps.push(String(r.id));
  }});
  return Object.values(map)
    .sort((a,b) => b.count - a.count).slice(0, 20);
}}

function _ofensENEService() {{
  return ENE_SERVICE_DATA.map(r => ({{
    ...r,
    shps: (r.shp_ids || '').split(',').map(s => s.trim()).filter(Boolean),
  }})).sort((a,b) => b.nao_entregue - a.nao_entregue);
}}

var _selBuyerId = null;
function _renderBuyerFraude() {{
  const list = document.getElementById('ofens-buyer-list');
  if (!list) return;
  const rows = CRZ_BUYERS_DATA.slice(0, 50);
  list.innerHTML = rows.map((r, i) => {{
    const risco = r.qtd >= 5 ? '#f87171' : r.qtd >= 3 ? '#fb923c' : '#fbbf24';
    return `<div id="bl-${{r.buyer_id}}" onclick="_selectBuyer('${{r.buyer_id}}')"
      style="padding:10px 14px;border-bottom:1px solid #1e293b;cursor:pointer;display:flex;align-items:center;gap:8px;transition:background .15s"
      onmouseover="if('${{r.buyer_id}}'!==_selBuyerId)this.style.background='#0c1626'"
      onmouseout="if('${{r.buyer_id}}'!==_selBuyerId)this.style.background=''">
      <span style="color:#475569;font-size:10px;min-width:20px">${{i+1}}.</span>
      <span style="color:#a78bfa;font-weight:700;font-family:monospace;flex:1;font-size:12px">${{r.buyer_id}}</span>
      <span style="color:${{risco}};font-weight:700;font-size:13px">${{r.qtd}}</span>
    </div>`;
  }}).join('');
  if (!_selBuyerId && rows.length) _selectBuyer(rows[0].buyer_id);
}}
function _selectBuyer(bid) {{
  if (_selBuyerId) {{
    const prev = document.getElementById('bl-' + _selBuyerId);
    if (prev) prev.style.background = '';
  }}
  _selBuyerId = bid;
  const el = document.getElementById('bl-' + bid);
  if (el) el.style.background = '#0f1629';
  const r = CRZ_BUYERS_DATA.find(x => x.buyer_id === bid);
  if (!r) return;
  const detail = document.getElementById('ofens-buyer-detail');
  if (!detail) return;
  const risco = r.qtd >= 5 ? '#f87171' : r.qtd >= 3 ? '#fb923c' : '#fbbf24';
  const boLink = 'https://adminml.com/users/' + bid;
  const selChips = (r.seller_ids || []).map(s =>
    `<span style="display:inline-block;margin:2px;padding:2px 8px;border-radius:8px;background:#1c2030;color:#f59e0b;font-size:11px;font-family:monospace">${{s}}</span>`
  ).join('');
  const drvChips = (r.driver_ids || []).map(d =>
    `<span style="display:inline-block;margin:2px;padding:2px 8px;border-radius:8px;background:#1c2030;color:#94a3b8;font-size:11px;font-family:monospace">${{d}}</span>`
  ).join('');
  detail.innerHTML = `
    <div class="bt" style="margin-bottom:14px">
      <a href="${{boLink}}" target="_blank" style="color:#a78bfa;text-decoration:none;font-family:monospace">Buyer ${{bid}} ↗</a>
      <span onclick="navigator.clipboard.writeText('${{bid}}')" title="Copiar" style="margin-left:8px;cursor:pointer;color:#475569;font-size:11px">⎘</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px">
      <div style="background:#060c1a;border-radius:8px;padding:12px">
        <div style="font-size:10px;color:#64748b;text-transform:uppercase;margin-bottom:4px">SHPs Fraude</div>
        <div style="font-size:24px;font-weight:800;color:${{risco}}">${{r.qtd}}</div>
      </div>
      <div style="background:#060c1a;border-radius:8px;padding:12px">
        <div style="font-size:10px;color:#64748b;text-transform:uppercase;margin-bottom:4px">Sellers</div>
        <div style="font-size:24px;font-weight:800;color:#f59e0b">${{r.sellers}}</div>
      </div>
      <div style="background:#060c1a;border-radius:8px;padding:12px">
        <div style="font-size:10px;color:#64748b;text-transform:uppercase;margin-bottom:4px">Drivers</div>
        <div style="font-size:24px;font-weight:800;color:#38bdf8">${{r.n_drivers}}</div>
      </div>
    </div>
    ${{selChips ? '<div style="margin-bottom:12px"><div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">Sellers Envolvidos</div>' + selChips + '</div>' : ''}}
    ${{drvChips ? '<div><div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">Drivers Envolvidos</div>' + drvChips + '</div>' : ''}}
  `;
}}

function renderOfensores() {{
  _updateOfensKPIs();
  const _ng = document.getElementById('ofens-normal-grid');
  const _bp = document.getElementById('ofens-buyer-panel');
  if (_ofensView === 'buyer_fraude') {{
    if (_ng) _ng.style.display = 'none';
    if (_bp) _bp.style.display = '';
    _renderBuyerFraude();
    return;
  }}
  if (_ng) _ng.style.display = '';
  if (_bp) _bp.style.display = 'none';
  let rows, labels, vals, title, metricLabel;
  const COLS = ['#6366f1','#fca311','#2a9d8f','#e76f51','#457b9d','#8172B3','#937860','#DD8452','#55A868','#C44E52'];

  if (_ofensView === 'ene') {{
    rows = _ofensTopSellersENE();
    labels = rows.map(r => r.seller_nome || r.seller_id);
    vals   = rows.map(r => _ofensMetric === 'cashout' ? r.cashout : r.qtd);
    metricLabel = _ofensMetric === 'cashout' ? 'Cashout USD' : 'Qtd ENE';
    title = `Top 10 Sellers ENE por ${{metricLabel}}`;
  }} else if (_ofensView === 'seller_devo') {{
    rows = _ofensTopSellersDevo();
    labels = rows.map(r => r.nome);
    vals   = rows.map(r => r.count);
    metricLabel = 'Qtd Devoluções'; title = 'Top 10 Sellers — Devoluções';
  }} else if (_ofensView === 'ene_dam_seller') {{
    rows = _ofensTopSellersENEDam();
    labels = rows.map(r => r.nome);
    vals   = rows.map(r => r.count);
    metricLabel = 'Qtd ENE Damaged'; title = 'Sellers — ENE Damaged (pacotes devolvidos avariados)';
  }} else if (_ofensView === 'ene_dam_buyer') {{
    rows = _ofensTopBuyersENEDam();
    labels = rows.map(r => r.id);
    vals   = rows.map(r => r.count);
    metricLabel = 'Qtd ENE Damaged'; title = 'Buyers — ENE Damaged (compradores recorrentes)';
  }} else if (_ofensView === 'origem') {{
    rows = _ofensOrigemNodo();
    labels = rows.map(r => r.node);
    vals   = rows.map(r => r.count);
    metricLabel = 'Qtd Devoluções'; title = 'Cross / SVC de Origem — De onde os pacotes vieram para SSP30';
  }} else if (_ofensView === 'dominio') {{
    rows = _ofensDominio();
    labels = rows.map(r => r.dominio);
    vals   = rows.map(r => r.count);
    metricLabel = 'Qtd Devoluções'; title = 'Domínio Ofensor — Tipo de Produto com Devoluções';
  }} else if (_ofensView === 'ene_svc') {{
    rows = _ofensENEService();
    labels = rows.map(r => r.carrier);
    vals   = rows.map(r => r.nao_entregue);
    metricLabel = 'Não Entregue'; title = 'ENE Service — Carriers c/ Entrega Não Efetiva (por Transportadora)';
  }} else if (_ofensView === 'buyer_vel') {{
    rows = BUYER_VEL_DATA.slice(0, 20);
    labels = rows.map(r => 'Buyer ' + (r.buyer_id || '—'));
    vals   = rows.map(r => r.pico_pedidos_mes);
    metricLabel = 'Pico Pedidos/Mês'; title = 'Velocidade de Compra — Pico de Pedidos em 1 Mês';
  }} else {{
    rows = _ofensTopBuyersDevo();
    labels = rows.map(r => r.nome || r.id);
    vals   = rows.map(r => r.count);
    metricLabel = 'Qtd Devoluções'; title = 'Top 10 Buyers — Devoluções';
  }}

  const titleEl = document.getElementById('ofens-chart-title');
  if (titleEl) titleEl.textContent = title;

  const ctx = document.getElementById('ofens-chart');
  if (!ctx) return;
  if (_ofensChart) _ofensChart.destroy();
  _ofensChart = new Chart(ctx.getContext('2d'), {{
    type: 'bar',
    data: {{
      labels,
      datasets: [{{
        label: metricLabel, data: vals,
        backgroundColor: COLS.map(c => c + 'CC'),
        borderColor: COLS, borderWidth: 1, borderRadius: 4
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false, indexAxis: 'y',
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: (c) => (_ofensView === 'ene' && _ofensMetric === 'cashout')
              ? 'US$ ' + Number(c.parsed.x).toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}})
              : Number(c.parsed.x).toLocaleString('pt-BR') + ' casos'
          }}
        }}
      }},
      scales: {{
        x: {{
          beginAtZero: true,
          ticks: {{
            color: '#64748b',
            callback: (v) => (_ofensView === 'ene' && _ofensMetric === 'cashout')
              ? 'US$' + Number(v).toLocaleString('pt-BR',{{minimumFractionDigits:0,maximumFractionDigits:0}})
              : Number(v).toLocaleString('pt-BR')
          }},
          grid: {{ color: 'rgba(255,255,255,0.05)' }}
        }},
        y: {{ grid: {{ display: false }}, ticks: {{ color: '#9ca3af', font: {{ size: 11 }} }} }}
      }}
    }}
  }});

  const _BO_SHP  = 'https://shipping-bo.adminml.com/sauron/shipments/shipment/';
  const _BO_USER = 'https://envios.adminml.com/logistics/users/';

  // ── thead dinâmico por view ──────────────────────────────────
  const thead = document.querySelector('#ofens-table thead');
  if (_ofensView === 'ene_svc') {{
    if (thead) thead.innerHTML = `<tr>
      <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px;width:22px">#</th>
      <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Transportadora</th>
      <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Total ENE</th>
      <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Não Entregue</th>
      <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Perdido</th>
      <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Avariado</th>
      <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px" title="Sem rota ou rota < 2h (possível descon)">Descon?</th>
    </tr>`;
  }} else if (_ofensView === 'origem' || _ofensView === 'dominio') {{
    const col2Label = _ofensView === 'dominio' ? 'Domínio / Vertical' : 'Nó de Origem';
    if (thead) thead.innerHTML = `<tr>
      <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px;width:22px">#</th>
      <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">${{col2Label}}</th>
      <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Devoluções</th>
    </tr>`;
  }} else if (_ofensView === 'buyer_vel') {{
    if (thead) thead.innerHTML = `<tr>
      <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px;width:28px">#</th>
      <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Buyer ID</th>
      <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Pico/Mês</th>
      <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Total Fraudes</th>
      <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">BPP Total USD</th>
      <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px" title="BPP no mês de pico">BPP Pico</th>
    </tr>`;
  }} else {{
    const metricHeader = _ofensView === 'ene'
      ? (_ofensMetric === 'cashout' ? 'Cashout USD' : 'Qtd ENE')
      : 'Qtd Devoluções';
    if (thead) thead.innerHTML = `<tr>
      <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px;width:28px">#</th>
      <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Nome / ID</th>
      <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">${{metricHeader}}</th>
    </tr>`;
  }}

  const tbody = document.getElementById('ofens-tbody');
  if (!tbody) return;

  // ── tbody Velocidade de Compra ───────────────────────────────
  if (_ofensView === 'buyer_vel') {{
    const maxPico = rows.length ? rows[0].pico_pedidos_mes : 1;
    tbody.innerHTML = rows.map((r, i) => {{
      const barW  = Math.round((r.pico_pedidos_mes / maxPico) * 100);
      const risco = r.pico_pedidos_mes >= 15 ? '#f87171' : r.pico_pedidos_mes >= 8 ? '#fb923c' : '#fbbf24';
      const boLink = `https://adminml.com/users/${{r.buyer_id}}`;
      const hist   = r.historico || {{}};
      const meses  = Object.keys(hist).sort();
      const histEl = meses.map(m => {{
        const qtd = hist[m];
        const cor = qtd >= 15 ? '#f87171' : qtd >= 8 ? '#fb923c' : qtd >= 3 ? '#fbbf24' : '#475569';
        return `<span title="${{m}}: ${{qtd}} pedidos" style="color:${{cor}};font-size:10px;margin-right:6px">${{m.slice(5)}}: ${{qtd}}</span>`;
      }}).join('');
      return `<tr style="border-bottom:1px solid #1e293b">
        <td style="padding:8px 10px;color:#475569;font-size:11px">${{i+1}}</td>
        <td style="padding:8px 10px">
          <a href="${{boLink}}" target="_blank" style="color:#a78bfa;font-weight:700;font-family:monospace;text-decoration:none">${{r.buyer_id}} ↗</a>
          <span style="color:#475569;font-size:10px;margin-left:6px;cursor:pointer" onclick="navigator.clipboard.writeText('${{r.buyer_id}}')" title="Copiar">⎘</span>
          <div style="height:3px;background:#1e293b;border-radius:2px;margin-top:5px;width:140px">
            <div style="height:3px;background:${{risco}};border-radius:2px;width:${{barW}}%"></div>
          </div>
          ${{histEl ? `<div style="margin-top:5px">${{histEl}}</div>` : ''}}
        </td>
        <td style="padding:8px 10px;text-align:right"><span style="color:${{risco}};font-weight:700">${{r.pico_pedidos_mes}}</span></td>
        <td style="padding:8px 10px;text-align:right;color:#94a3b8">${{r.qtd_fraudes}}</td>
        <td style="padding:8px 10px;text-align:right;color:#fbbf24;font-weight:600">${{r.bpp_fraude_usd != null ? '$' + r.bpp_fraude_usd.toFixed(2) : '—'}}</td>
        <td style="padding:8px 10px;text-align:right;color:#fb923c">${{r.bpp_pico_mes != null ? '$' + r.bpp_pico_mes.toFixed(2) : '—'}}</td>
      </tr>`;
    }}).join('');
    return;
  }}

  // ── tbody para view ENE Service (por Transportadora) ────────
  if (_ofensView === 'ene_svc') {{
    tbody.innerHTML = rows.map((r, i) => {{
      const uid = 'ofensvc_' + i;
      const descon = (r.sem_rota || 0) + (r.rota_curta_2h || 0);
      const desconColor = descon > 0 ? '#f87171' : '#475569';
      const seta = r.shps.length ? `<span id="arrow_${{uid}}" style="font-size:10px;color:#4b5563;margin-left:6px">▶ ${{r.shps.length}} ids</span>` : '';
      const toggle = r.shps.length ? `onclick="toggleDriver('${{uid}}')" style="cursor:pointer;border-bottom:1px solid #1e293b"` : 'style="border-bottom:1px solid #1e293b"';
      const detailContent = r.shps.map(s =>
        `<a href="https://shipping-bo.adminml.com/sauron/shipments/shipment/${{s}}"
              target="_blank"
              style="color:#60a5fa;font-family:monospace;font-size:12px;display:block;padding:2px 0;text-decoration:none"
              title="Abrir no BO: ${{s}}">${{s}} ↗</a>`
      ).join('');
      const detailRow = r.shps.length ? `<tr id="${{uid}}" style="display:none;background:#060c1a"><td></td><td colspan="6" style="padding:6px 10px 8px 28px">${{detailContent}}</td></tr>` : '';
      return `<tr ${{toggle}}>
        <td style="padding:8px 8px;color:#475569;font-size:11px">${{i+1}}</td>
        <td style="padding:8px 8px"><span style="color:#38bdf8;font-weight:600">${{r.carrier}}</span>${{seta}}</td>
        <td style="padding:8px 8px;text-align:right"><span style="color:#94a3b8">${{r.total}}</span></td>
        <td style="padding:8px 8px;text-align:right"><span style="color:#fbbf24;font-weight:700">${{r.nao_entregue}}</span></td>
        <td style="padding:8px 8px;text-align:right"><span style="color:#f87171">${{r.perdido}}</span></td>
        <td style="padding:8px 8px;text-align:right"><span style="color:#fb923c">${{r.avariado}}</span></td>
        <td style="padding:8px 8px;text-align:right" title="sem_rota=${{r.sem_rota}} + rota<2h=${{r.rota_curta_2h}}">
          <span style="color:${{desconColor}};font-weight:${{descon > 0 ? '700' : '400'}}">${{descon > 0 ? '⚠ ' + descon : '—'}}</span>
        </td>
      </tr>${{detailRow}}`;
    }}).join('');
    return;
  }}

  // ── tbody para views Origem Nó e Domínio Ofensor ─────────────
  if (_ofensView === 'origem' || _ofensView === 'dominio') {{
    const total = rows.reduce((s,r) => s + r.count, 0);
    const isDom = _ofensView === 'dominio';
    tbody.innerHTML = rows.map((r, i) => {{
      const uid = (isDom ? 'ofdom_' : 'ofnod_') + i;
      const pct = total > 0 ? ((r.count/total)*100).toFixed(1) : '0.0';
      const barW = total > 0 ? Math.round((r.count/rows[0].count)*100) : 0;
      const seta = r.shps.length ? `<span id="arrow_${{uid}}" style="font-size:10px;color:#4b5563;margin-left:6px">▶ ${{r.shps.length}} ids</span>` : '';
      const toggle = r.shps.length ? `onclick="toggleDriver('${{uid}}')" style="cursor:pointer;border-bottom:1px solid #1e293b"` : 'style="border-bottom:1px solid #1e293b"';
      const detailContent = r.shps.map(s =>
        `<a href="https://shipping-bo.adminml.com/sauron/shipments/shipment/${{s}}"
              target="_blank"
              style="color:#60a5fa;font-family:monospace;font-size:12px;display:block;padding:2px 0;text-decoration:none"
              title="Abrir no BO: ${{s}}">${{s}} ↗</a>`
      ).join('');
      const detailRow = r.shps.length
        ? `<tr id="${{uid}}" style="display:none;background:#060c1a"><td></td><td colspan="2" style="padding:6px 10px 8px 28px">${{detailContent}}</td></tr>`
        : '';
      const mainLabel = isDom
        ? `<span style="color:#a78bfa;font-weight:700">${{r.dominio}}</span>
           <span style="color:#475569;font-size:10px;margin-left:6px;font-style:italic">${{r.vertical}}</span>`
        : `<span style="color:#38bdf8;font-weight:600;font-family:monospace">${{r.node}}</span>`;
      const barColor = isDom ? '#a78bfa' : '#6366f1';
      const domHtmlOrigem = (!isDom && r.dominios && r.dominios.length)
        ? `<div style="margin-top:5px">${{r.dominios.map(([d,n])=>`<span style="display:inline-block;margin:2px 3px 2px 0;padding:1px 7px;border-radius:10px;background:#1e1040;color:#c4b5fd;font-size:10px">${{_domPT(d)}} <span style="color:#7c3aed;font-weight:700">${{n}}</span></span>`).join('')}}</div>`
        : '';
      return `<tr ${{toggle}}>
        <td style="padding:8px 10px;color:#475569;font-size:11px">${{i+1}}</td>
        <td style="padding:8px 10px">
          ${{mainLabel}}${{seta}}
          <div style="height:3px;background:#1e293b;border-radius:2px;margin-top:4px;width:160px">
            <div style="height:3px;background:${{barColor}};border-radius:2px;width:${{barW}}%"></div>
          </div>
          ${{domHtmlOrigem}}
        </td>
        <td style="padding:8px 10px;text-align:right">
          <span style="color:#fbbf24;font-weight:700">${{r.count}}</span>
          <span style="color:#475569;font-size:10px;margin-left:4px">(${{pct}}%)</span>
        </td>
      </tr>${{detailRow}}`;
    }}).join('');
    return;
  }}

  // ── tbody padrão (ene, seller_devo, buyer_devo, ene_dam_*) ──
  tbody.innerHTML = rows.map((r, i) => {{
    const uid = 'ofens_' + i;
    const isENE = _ofensView === 'ene';
    const isBuyer = _ofensView === 'buyer_devo' || _ofensView === 'ene_dam_buyer';
    const isENEDam = _ofensView === 'ene_dam_seller' || _ofensView === 'ene_dam_buyer';
    const linkColor = isBuyer ? '#a78bfa' : isENEDam ? '#fb923c' : '#38bdf8';
    const nome  = isENE ? (r.seller_nome||r.seller_id) : (r.nome||r.id);
    const uid_id = isENE ? r.seller_id : r.id;
    const label = `<span style="color:${{linkColor}};font-weight:600">${{nome}}</span>`
                + `<span style="color:#475569;font-size:10px;margin-left:5px;font-family:monospace;cursor:pointer" title="Copiar ID" onclick="navigator.clipboard.writeText('${{uid_id}}')">#${{uid_id}}</span>`;
    const val = isENE && _ofensMetric === 'cashout'
      ? '<span style="color:#86efac;font-weight:700">US$ ' + Number(r.cashout).toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}}) + '</span>'
      : '<span style="color:#fbbf24;font-weight:700">' + Number(isENE ? r.qtd : r.count).toLocaleString('pt-BR') + '</span>';
    const shpIds = isENE
      ? (r.shp_ids || '').split(',').filter(Boolean)
      : (r.shps || []);
    const seta = shpIds.length
      ? `<span id="arrow_${{uid}}" style="font-size:10px;color:#4b5563;margin-left:6px">▶ ${{shpIds.length}} ids</span>`
      : '';
    const toggle = shpIds.length ? `onclick="toggleDriver('${{uid}}')" style="cursor:pointer;border-bottom:1px solid #1e293b"` : 'style="border-bottom:1px solid #1e293b"';
    const domHtml = ((_ofensView === 'seller_devo' || _ofensView === 'buyer_devo') && r.dominios && r.dominios.length)
      ? `<div style="margin-bottom:6px;padding:6px 8px;background:#0c1626;border-radius:4px;border-left:3px solid #a78bfa">
           <div style="font-size:10px;color:#6366f1;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px;font-weight:700">Domínios Ofensores</div>
           ${{r.dominios.map(([d,n])=>`<span style="display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;border-radius:12px;background:#1e1040;color:#c4b5fd;font-size:11px">${{_domPT(d)}} <span style="color:#7c3aed;font-weight:700">${{n}}</span></span>`).join('')}}
         </div>`
      : '';
    const detailContent = shpIds.map(s =>
      `<a href="https://shipping-bo.adminml.com/sauron/shipments/shipment/${{s.trim()}}"
            target="_blank"
            style="color:#60a5fa;font-family:monospace;font-size:12px;display:block;padding:2px 0;text-decoration:none"
            title="Abrir no BO: ${{s.trim()}}">${{s.trim()}} ↗</a>`
    ).join('');
    const detailRow = shpIds.length
      ? `<tr id="${{uid}}" style="display:none;background:#060c1a">
          <td></td><td colspan="2" style="padding:6px 10px 8px 28px">${{domHtml}}${{detailContent}}</td>
        </tr>`
      : '';
    return `<tr ${{toggle}}>
      <td style="padding:9px 10px;color:#475569;font-size:11px">${{i+1}}</td>
      <td style="padding:9px 10px">${{label}}${{seta}}</td>
      <td style="padding:9px 10px;text-align:right">${{val}}</td>
    </tr>${{detailRow}}`;
  }}).join('');
}}

lucide.createIcons();
{_SB_DRAG_JS}

// Popula select de transportadoras na aba Damaged
(function() {{
  const sel = document.getElementById('dmg-transp-filter');
  if (!sel) return;
  const seen = {{}};
  Object.values(DRIVER_TRANSP).forEach(t => {{ seen[t] = 1; }});
  Object.keys(seen).sort().forEach(t => {{
    const opt = document.createElement('option');
    opt.value = t; opt.textContent = t;
    sel.appendChild(opt);
  }});
}})();


</script>

<!-- ABA OFENSORES -->
<div id="tab-ofensores" class="content">
  <div style="background:linear-gradient(90deg,#0f172a,#1e293b);padding:16px 20px;border-radius:8px;margin-bottom:14px;display:flex;align-items:center;gap:12px">
    <i data-lucide="target" width="20" height="20" style="color:#fca311"></i>
    <div>
      <div style="font-size:16px;font-weight:800;color:#f1f5f9">Análise de Ofensores — SSP30</div>
      <div style="font-size:12px;color:#64748b">Top 10 por Cashout USD e por Quantidade · ENE + Devoluções</div>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:14px">
    <div style="background:#0f172a;border:1px solid #1e293b;border-left:4px solid #fca311;border-radius:8px;padding:14px 16px">
      <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">Sellers ENE</div>
      <div style="font-size:22px;font-weight:700;color:#f1f5f9" id="ofens-kpi-sellers">—</div>
      <div style="font-size:11px;color:#64748b">últimos 3 meses</div>
    </div>
    <div style="background:#0f172a;border:1px solid #1e293b;border-left:4px solid #10b981;border-radius:8px;padding:14px 16px">
      <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">Cashout Total ENE</div>
      <div style="font-size:20px;font-weight:700;color:#86efac" id="ofens-kpi-cashout">—</div>
    </div>
    <div style="background:#0f172a;border:1px solid #1e293b;border-left:4px solid #ef4444;border-radius:8px;padding:14px 16px">
      <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">Com BPP Confirmado</div>
      <div style="font-size:22px;font-weight:700;color:#f87171" id="ofens-kpi-comcashout">—</div>
      <div style="font-size:11px;color:#64748b">sellers c/ cashout &gt; 0</div>
    </div>
    <div style="background:#0f172a;border:1px solid #1e293b;border-left:4px solid #60a5fa;border-radius:8px;padding:14px 16px">
      <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">Total Devoluções</div>
      <div style="font-size:22px;font-weight:700;color:#60a5fa" id="ofens-kpi-devos">—</div>
    </div>
    <div style="background:#0f172a;border:1px solid #fb923c;border-left:4px solid #fb923c;border-radius:8px;padding:14px 16px">
      <div style="font-size:10px;color:#fb923c;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">ENE Damaged</div>
      <div style="font-size:22px;font-weight:700;color:#fb923c" id="ofens-kpi-ene-dam">—</div>
      <div style="font-size:11px;color:#64748b">pacotes devol. avariados</div>
    </div>
  </div>
  <div style="display:flex;border-bottom:2px solid #1e293b;margin-bottom:0">
    <button onclick="setOfensView('ene')" id="ofens-btn-ene" style="padding:11px 20px;border:none;border-bottom:2px solid #fca311;margin-bottom:-2px;background:transparent;color:#fca311;font-size:12px;font-weight:700;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span style="width:8px;height:8px;border-radius:50%;background:#ef4444;display:inline-block"></span>Sellers ENE
    </button>
    <button onclick="setOfensView('seller_devo')" id="ofens-btn-seller_devo" style="padding:11px 20px;border:none;border-bottom:2px solid transparent;margin-bottom:-2px;background:transparent;color:#64748b;font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span style="width:8px;height:8px;border-radius:50%;background:#475569;display:inline-block"></span>Sellers Devoluções
    </button>
    <button onclick="setOfensView('buyer_devo')" id="ofens-btn-buyer_devo" style="padding:11px 20px;border:none;border-bottom:2px solid transparent;margin-bottom:-2px;background:transparent;color:#64748b;font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span style="width:8px;height:8px;border-radius:50%;background:#475569;display:inline-block"></span>Buyers Devoluções
    </button>
    <div style="width:1px;background:#1e293b;margin:8px 4px"></div>
    <button onclick="setOfensView('ene_dam_seller')" id="ofens-btn-ene_dam_seller" style="padding:11px 20px;border:none;border-bottom:2px solid transparent;margin-bottom:-2px;background:transparent;color:#64748b;font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span style="width:8px;height:8px;border-radius:50%;background:#475569;display:inline-block"></span>ENE Dam. Sellers
    </button>
    <button onclick="setOfensView('ene_dam_buyer')" id="ofens-btn-ene_dam_buyer" style="padding:11px 20px;border:none;border-bottom:2px solid transparent;margin-bottom:-2px;background:transparent;color:#64748b;font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span style="width:8px;height:8px;border-radius:50%;background:#475569;display:inline-block"></span>ENE Dam. Buyers
    </button>
    <div style="width:1px;background:#1e293b;margin:8px 4px"></div>
    <button onclick="setOfensView('origem')" id="ofens-btn-origem" style="padding:11px 20px;border:none;border-bottom:2px solid transparent;margin-bottom:-2px;background:transparent;color:#64748b;font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span style="width:8px;height:8px;border-radius:50%;background:#475569;display:inline-block"></span>Origem Cross
    </button>
    <button onclick="setOfensView('dominio')" id="ofens-btn-dominio" style="padding:11px 20px;border:none;border-bottom:2px solid transparent;margin-bottom:-2px;background:transparent;color:#64748b;font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span style="width:8px;height:8px;border-radius:50%;background:#475569;display:inline-block"></span>Domínio Ofensor
    </button>
    <button onclick="setOfensView('ene_svc')" id="ofens-btn-ene_svc" style="padding:11px 20px;border:none;border-bottom:2px solid transparent;margin-bottom:-2px;background:transparent;color:#64748b;font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span style="width:8px;height:8px;border-radius:50%;background:#475569;display:inline-block"></span>ENE Service
    </button>
    <button onclick="setOfensView('buyer_fraude')" id="ofens-btn-buyer_fraude" style="padding:11px 20px;border:none;border-bottom:2px solid transparent;margin-bottom:-2px;background:transparent;color:#64748b;font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span style="width:8px;height:8px;border-radius:50%;background:#475569;display:inline-block"></span>🛒 Buyers Fraude
    </button>
    <button onclick="setOfensView('buyer_vel')" id="ofens-btn-buyer_vel" style="padding:11px 20px;border:none;border-bottom:2px solid transparent;margin-bottom:-2px;background:transparent;color:#64748b;font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span style="width:8px;height:8px;border-radius:50%;background:#475569;display:inline-block"></span>⚡ Velocidade
    </button>
  </div>
  <div id="ofens-metric-toggle" style="display:flex;gap:8px;padding:12px 0 14px 0">
    <button onclick="setOfensMetric('cashout')" id="ofens-metric-cashout" style="padding:6px 14px;border-radius:6px;border:none;background:#92400e;color:#fbbf24;font-size:11px;cursor:pointer;font-weight:600">↓ Cashout USD</button>
    <button onclick="setOfensMetric('qty')" id="ofens-metric-qty" style="padding:6px 14px;border-radius:6px;border:1px solid #334155;background:transparent;color:#64748b;font-size:11px;cursor:pointer;font-weight:600">↓ Qtd ENE</button>
  </div>
  <div id="ofens-normal-grid" style="display:grid;grid-template-columns:1.4fr 1fr;gap:14px">
    <div class="box">
      <div class="bt" id="ofens-chart-title">Top 10 Sellers ENE por Cashout USD</div>
      <div style="position:relative;height:320px"><canvas id="ofens-chart"></canvas></div>
    </div>
    <div class="box">
      <div class="bt">Tabela</div>
      <div style="overflow-x:auto">
        <table id="ofens-table" style="width:100%;border-collapse:collapse;font-size:12px">
          <thead><tr>
            <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px;width:28px">#</th>
            <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Nome / ID</th>
            <th style="text-align:right;padding:8px 10px;border-bottom:2px solid #1e293b;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Qtd</th>
          </tr></thead>
          <tbody id="ofens-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>
  <div id="ofens-buyer-panel" style="display:none">
    <div style="display:grid;grid-template-columns:230px 1fr;gap:14px;align-items:start">
      <div class="box" style="padding:0;overflow:hidden">
        <div class="bt" style="padding:10px 14px;font-size:12px">🛒 Buyers c/ Fraude</div>
        <div id="ofens-buyer-list" style="overflow-y:auto;max-height:430px"></div>
      </div>
      <div class="box" id="ofens-buyer-detail">
        <div class="bt" style="color:#475569">Selecione um buyer</div>
        <div style="color:#334155;font-size:13px;padding:20px 0">← Clique em um buyer para ver detalhes</div>
      </div>
    </div>
  </div>
</div>


<!-- ABA BLOQUEIOS -->
<div id="tab-bloqueios" class="content">

  {f'''<div style="background:#0f0606;border:1px solid #7f1d1d;border-radius:8px;padding:14px 18px;margin-bottom:20px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
      <i data-lucide="alert-triangle" width="16" height="16" style="color:#ef4444;flex-shrink:0"></i>
      <span style="font-size:13px;font-weight:600;color:#f87171">{len(d["alertas_bl"])} driver(s) da Block List com SHP de alto valor nos últimos 15 dias</span>
      <span style="margin-left:auto;background:#450a0a;color:#f87171;font-size:10px;font-weight:700;padding:3px 9px;border-radius:3px;white-space:nowrap">≥ $200 USD</span>
    </div>
    <table style="width:100%;font-size:12px;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left;color:#6b7280;font-size:10px;font-weight:600;padding:4px 0;text-transform:uppercase;letter-spacing:.5px">Driver</th>
        <th style="text-align:left;color:#6b7280;font-size:10px;font-weight:600;padding:4px 0;text-transform:uppercase;letter-spacing:.5px">Status BL</th>
        <th style="text-align:left;color:#6b7280;font-size:10px;font-weight:600;padding:4px 0;text-transform:uppercase;letter-spacing:.5px">SHP ID</th>
        <th style="text-align:right;color:#6b7280;font-size:10px;font-weight:600;padding:4px 0;text-transform:uppercase;letter-spacing:.5px">BPP</th>
        <th style="text-align:right;color:#6b7280;font-size:10px;font-weight:600;padding:4px 0;text-transform:uppercase;letter-spacing:.5px">Data</th>
      </tr></thead>
      <tbody>{rows_alertas_bl(d["alertas_bl"])}</tbody>
    </table>
  </div>''' if d["alertas_bl"] else ''}

  <div class="cards">
    <div class="card">
      <div class="card-header"><i data-lucide="list" class="ci" width="14" height="14"></i><span class="cl">Total Solicitações</span></div>
      <div class="cv" id="bl-cv-total">{d["bl"]["total"]}</div><div class="cd">2026</div>
    </div>
    <div class="card card-ok">
      <div class="card-header"><i data-lucide="shield-check" class="ci" width="14" height="14" style="color:#064e3b"></i><span class="cl">Bloqueados</span></div>
      <div class="cv val-ok" id="bl-cv-bloqueados">{d["bl"]["bloqueados"]}</div><div class="cd">Confirmados</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="clock" class="ci" width="14" height="14"></i><span class="cl">Solicitados</span></div>
      <div class="cv" style="color:#60a5fa" id="bl-cv-solicitados">{d["bl"]["solicitados"]}</div><div class="cd">Aguardando</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="eye" class="ci" width="14" height="14"></i><span class="cl">Monitorados</span></div>
      <div class="cv val-warn" id="bl-cv-monitorados">{d["bl"]["monitorados"]}</div><div class="cd">Em acompanhamento</div>
    </div>
    <div class="card card-ok">
      <div class="card-header"><i data-lucide="dollar-sign" class="ci" width="14" height="14" style="color:#064e3b"></i><span class="cl">GMV Protegido</span></div>
      <div class="cv val-ok" id="bl-cv-gmv">${d["bl"]["gmv_protegido"]:,.2f}</div><div class="cd">Bloqueados confirmados</div>
    </div>
  </div>

  {'<div style="background:#1c1008;border:1px solid #92400e;border-radius:8px;padding:14px 18px;margin-bottom:16px;display:flex;align-items:center;gap:12px"><span style="font-size:18px">📋</span><div><div style="font-size:12px;font-weight:600;color:#fbbf24">Block List vazia</div><div style="font-size:11px;color:#9ca3af;margin-top:4px">Nenhum driver adicionado ainda. Preencha a aba <em>Drivers Bloqueados</em> na planilha e reprocesse o dashboard.</div></div></div>' if not d["bl"]["rows"] else ''}
  <div id="bl-period-note" style="display:none;font-size:11px;color:#fb923c;background:#1c1008;border:1px solid #92400e;border-radius:6px;padding:6px 14px;margin-bottom:16px;text-align:center"></div>

  <div class="grid2 mb16">
    <div class="box"><div class="bt">Por Status</div><div style="position:relative;height:220px"><canvas id="cBlStatus"></canvas></div></div>
    <div class="box"><div class="bt">Por Transportadora</div><div style="position:relative;height:220px"><canvas id="cBlTransp"></canvas></div></div>
  </div>

  <div class="tbl-wrap">
    <div class="tbl-title">Lista Completa — Block List 2026</div>
    <div class="filter-bar">
      <input id="bl_search" type="text" oninput="filtrarBloqueios()" class="filter-select" placeholder="🔍 Driver ID ou Nome..." style="width:180px">
      <select id="bl_status" onchange="filtrarBloqueios()" class="filter-select">
        <option value="">Todos os status</option>
        <option value="Bloqueado">Bloqueado</option>
        <option value="Solicitado">Solicitado</option>
        <option value="Monitorado">Monitorado</option>
      </select>
      <select id="bl_transp" onchange="filtrarBloqueios()" class="filter-select">
        <option value="">Todas as transportadoras</option>
        {''.join(f'<option value="{t}">{t}</option>' for t in sorted(t for t in set(r["mlp"] for r in d["bl"]["rows"]) if t and t not in ("N/A","")))}
      </select>
      <button onclick="document.getElementById('bl_status').value='';document.getElementById('bl_transp').value='';document.getElementById('bl_search').value='';filtrarBloqueios()" style="background:#1f2937;color:#6b7280;border:1px solid #374151;border-radius:6px;padding:7px 12px;font-size:11px;cursor:pointer">Limpar</button>
      <button onclick="exportBlCSV()" style="background:#1e3a5f;color:#60a5fa;border:1px solid #1e40af;border-radius:6px;padding:7px 12px;font-size:11px;cursor:pointer;margin-left:auto">⬇ Exportar CSV</button>
    </div>
    <div class="tbl-scroll"><table id="bl-table">
      <thead><tr>
        <th onclick="sortBl('did')" style="cursor:pointer">Driver ID <span id="bl-sort-did"></span></th>
        <th>Nome</th><th>Transportadora</th><th>Placa</th><th>SHP</th>
        <th onclick="sortBl('usd')" style="cursor:pointer">USD$ <span id="bl-sort-usd"></span></th>
        <th onclick="sortBl('status')" style="cursor:pointer">Status <span id="bl-sort-status"></span></th>
        <th>Motivo</th>
        <th onclick="sortBl('data')" style="cursor:pointer">Data <span id="bl-sort-data"></span></th>
        <th>Semana</th>
      </tr></thead>
      <tbody id="bl-tbody">{rows_block_list(d["bl"]["rows"])}</tbody>
    </table></div>
  </div>
</div>

<!-- ===== ABA BSD (Buyer Seller Driver) ===== -->
<div id="tab-cruzamento" class="content">

  <!-- Top Sellers & Buyers do Mês -->
  <div class="box" style="margin-bottom:18px">
    <div class="box-title" style="margin-bottom:12px">
      <i data-lucide="trophy" width="12" height="12" class="ci" style="margin-right:6px;color:#FFE600"></i>TOP SELLERS & BUYERS DO PERÍODO
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <div>
        <div style="font-size:10px;color:#6b7280;margin-bottom:8px;font-weight:600;text-transform:uppercase;letter-spacing:.6px">Sellers Ofensores</div>
        <table style="width:100%;font-size:12px"><thead><tr>
          <th style="width:40px">#</th><th>Seller ID</th><th>Fraudes</th>
        </tr></thead><tbody id="crz-sellers-mes-tbody"></tbody></table>
      </div>
      <div>
        <div style="font-size:10px;color:#6b7280;margin-bottom:8px;font-weight:600;text-transform:uppercase;letter-spacing:.6px">Buyers Ofensores</div>
        <table style="width:100%;font-size:12px"><thead><tr>
          <th style="width:40px">#</th><th>Buyer ID</th><th>Fraudes</th>
        </tr></thead><tbody id="crz-buyers-mes-tbody"></tbody></table>
      </div>
    </div>
  </div>

  <div class="cards-grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card">
      <div class="card-header"><i data-lucide="store" class="ci" width="14" height="14"></i><span class="cl">Sellers Ofensores</span></div>
      <div class="cv" style="color:#f59e0b" id="crz-cv-sellers">{d["crz"]["total_sellers"]}</div><div class="cd" id="crz-cd-sellers">Vendedores c/ ≥2 fraudes</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="user" class="ci" width="14" height="14"></i><span class="cl">Buyers Ofensores</span></div>
      <div class="cv" style="color:#60a5fa" id="crz-cv-buyers">{d["crz"]["total_buyers"]}</div><div class="cd" id="crz-cd-buyers">Compradores c/ ≥2 fraudes</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="git-merge" class="ci" width="14" height="14"></i><span class="cl">Pares Seller×Buyer</span></div>
      <div class="cv" id="crz-cv-pares">{d["crz"]["total_pares"]}</div><div class="cd" id="crz-cd-pares">Combinações suspeitas</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="truck" class="ci" width="14" height="14"></i><span class="cl">Drivers Conectados</span></div>
      <div class="cv" style="color:#4ade80" id="crz-cv-drivers">{d["crz"]["total_drivers"]}</div><div class="cd" id="crz-cd-drivers">Motoristas envolvidos</div>
    </div>
  </div>

  <div class="grid2 mb16">
    <div class="tbl-wrap">
      <div class="tbl-title" style="color:#f59e0b">Top Sellers Ofensores</div>
      <div class="tbl-scroll"><table>
        <thead><tr><th>Seller ID</th><th>Fraudes</th><th>Buyers</th><th>Drivers</th><th>Drivers Associados</th></tr></thead>
        <tbody>
          {''.join(f"""<tr style="background:{'#1a100a' if i==0 else ''}">
            <td style="font-family:monospace;font-size:12px;color:#f59e0b">{s["seller_id"]}</td>
            <td style="font-weight:700;color:#ef4444;text-align:center">{s["qtd"]}</td>
            <td style="text-align:center;color:#9ca3af">{s["buyers"]}</td>
            <td style="text-align:center;color:#4ade80">{s["n_drivers"]}</td>
            <td style="font-size:10px;color:#6b7280">{' '.join(f'<span style="background:#1a2035;padding:1px 5px;border-radius:3px;color:#9ca3af">{dr}</span>' for dr in s["driver_ids"])}</td>
          </tr>""" for i,s in enumerate(d["crz"]["sellers"][:30]))}
        </tbody>
      </table></div>
    </div>
    <div class="tbl-wrap">
      <div class="tbl-title" style="color:#60a5fa">Top Buyers Ofensores</div>
      <div class="tbl-scroll"><table>
        <thead><tr><th>Buyer ID</th><th>Fraudes</th><th>Sellers</th><th>Drivers</th><th>Drivers Associados</th></tr></thead>
        <tbody>
          {''.join(f"""<tr style="background:{'#0a0f1a' if i==0 else ''}">
            <td style="font-family:monospace;font-size:12px;color:#60a5fa">{b["buyer_id"]}</td>
            <td style="font-weight:700;color:#ef4444;text-align:center">{b["qtd"]}</td>
            <td style="text-align:center;color:#9ca3af">{b["sellers"]}</td>
            <td style="text-align:center;color:#4ade80">{b["n_drivers"]}</td>
            <td style="font-size:10px;color:#6b7280">{' '.join(f'<span style="background:#1a2035;padding:1px 5px;border-radius:3px;color:#9ca3af">{dr}</span>' for dr in b["driver_ids"])}</td>
          </tr>""" for i,b in enumerate(d["crz"]["buyers"][:30]))}
        </tbody>
      </table></div>
    </div>
  </div>

  <div class="tbl-wrap">
    <div class="tbl-title">BSD — Buyer × Seller × Driver × Pacotes</div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th>#</th><th>Seller ID</th><th>Buyer ID</th><th>Fraudes</th><th>Drivers</th><th>Pacotes (SHP IDs)</th>
      </tr></thead>
      <tbody>
        {''.join(f"""<tr>
          <td style="color:#4b5563;font-size:11px;text-align:center">{i+1}</td>
          <td style="font-family:monospace;font-size:12px;color:#f59e0b">{p["seller_id"]}</td>
          <td style="font-family:monospace;font-size:12px;color:#60a5fa">{p["buyer_id"]}</td>
          <td style="font-weight:700;color:#ef4444;text-align:center">{p["qtd"]}</td>
          <td style="font-size:11px;color:#9ca3af">{p["drivers"]}</td>
          <td style="font-size:11px">{' '.join(f'<a href="{MELI_URL}/{s}" target="_blank" style="color:#60a5fa;text-decoration:none;font-family:monospace;margin-right:4px">{s}</a>' for s in p["shp_ids"])}</td>
        </tr>""" for i,p in enumerate(d["crz"]["pares"]))}
      </tbody>
    </table></div>
  </div>

  <div class="tbl-wrap" style="margin-top:18px">
    <div class="tbl-title" style="display:flex;align-items:center;gap:8px">
      <i data-lucide="git-merge" width="14" height="14" style="color:#a78bfa"></i>
      <span style="color:#a78bfa">Driver × Seller / Buyer — Conexões Suspeitas</span>
    </div>
    <div style="font-size:11px;color:#4b5563;padding:0 18px 10px">Drivers que aparecem em múltiplos pares Seller×Buyer — possível conluio.</div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th>#</th><th>Driver ID</th><th>Fraudes</th>
        <th style="color:#f59e0b">Sellers</th><th style="color:#60a5fa">Buyers</th>
        <th style="color:#f59e0b">Top Sellers</th><th style="color:#60a5fa">Top Buyers</th>
      </tr></thead>
      <tbody>
        {''.join(f"""<tr style="border-top:1px solid #111827">
          <td style="padding:7px 10px;color:#4b5563;font-size:11px">#{i+1}</td>
          <td style="padding:7px 10px;font-family:monospace;font-size:12px;color:#e2e8f0">{dc["driver_id"]}</td>
          <td style="padding:7px 10px;font-weight:700;color:#ef4444;text-align:center">{dc["qtd"]}</td>
          <td style="padding:7px 10px;text-align:center;color:#f59e0b;font-weight:600">{dc["n_sellers"]}</td>
          <td style="padding:7px 10px;text-align:center;color:#60a5fa;font-weight:600">{dc["n_buyers"]}</td>
          <td style="padding:7px 10px;font-size:10px">{' '.join(f'<span style="background:rgba(245,158,11,.12);color:#f59e0b;padding:2px 5px;border-radius:3px;margin-right:3px">{s}</span>' for s in dc["sellers"])}</td>
          <td style="padding:7px 10px;font-size:10px">{' '.join(f'<span style="background:rgba(96,165,250,.12);color:#60a5fa;padding:2px 5px;border-radius:3px;margin-right:3px">{b}</span>' for b in dc["buyers"])}</td>
        </tr>""" for i,dc in enumerate(d["crz"]["driver_crz"][:40]))}
      </tbody>
    </table></div>
  </div>
</div>

<!-- ABA DC/NEX -->
<div id="tab-dcnex" class="content">
  <div class="cards-grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:18px">
    <div class="card">
      <div class="card-header"><i data-lucide="warehouse" class="ci" width="14" height="14"></i><span class="cl">Facilities Ofensoras</span></div>
      <div class="cv red">{len(d["dc_nex"]["facilities"])}</div>
      <div class="cd">DC / NEX / XPT distintas</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="package" class="ci" width="14" height="14"></i><span class="cl">Total Pacotes</span></div>
      <div class="cv" style="color:#f87171">{d["dc_nex"]["total_pkgs"]}</div>
      <div class="cd">fraudes rastreadas</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="dollar-sign" class="ci" width="14" height="14"></i><span class="cl">GMV Total</span></div>
      <div class="cv" style="color:#10b981">${d["dc_nex"]["total_gmv"]:,.2f}</div>
      <div class="cd">BPP em risco</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="user-x" class="ci" width="14" height="14"></i><span class="cl">Drivers Suspeitos</span></div>
      <div class="cv" style="color:#a78bfa">{len(d["dc_nex"]["drivers"])}</div>
      <div class="cd">Last Mile identificados</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="alert-triangle" class="ci" width="14" height="14"></i><span class="cl">Drivers Alto Risco</span></div>
      <div class="cv" style="color:#ef4444">{sum(1 for dr in d["dc_nex"]["drivers"] if dr["nivel"]=="ALTO")}</div>
      <div class="cd">aparecem em 3+ facilities</div>
    </div>
  </div>
  {rows_driver_ranking(d["dc_nex"])}
  <div class="tbl-wrap">
    <div class="tbl-title" style="color:#f59e0b">
      <i data-lucide="warehouse" width="14" height="14" style="color:#f59e0b;margin-right:6px;vertical-align:middle"></i>
      Ranking de Ofensores DC / NEX / XPT — {len(d["dc_nex"]["facilities"])} facilities · {d["dc_nex"]["total_pkgs"]} pacotes
      <span style="font-size:10px;font-weight:400;color:#f59e0b;margin-left:6px">· exibindo até 300 registros</span>
      <span style="font-size:10px;font-weight:400;color:#6b7280;float:right">Clique na linha para expandir os pacotes · Cobrar OTR editável no painel ON ROUTE</span>
    </div>
    <div class="tbl-scroll"><table>
      <thead><tr>
        <th>Tipo / Facility</th>
        <th>Place Nome</th>
        <th style="text-align:right">GMV Total</th>
        <th style="text-align:center">Pacotes</th>
        <th style="text-align:center">Veredicto</th>
      </tr></thead>
      <tbody>{rows_dc_nex(d["dc_nex"])}</tbody>
    </table></div>
  </div>
</div>

<!-- ABA TENDÊNCIA -->
<div id="tab-tendencia" class="content">
  <div class="cards-grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card">
      <div class="card-header"><i data-lucide="trending-up" class="ci" width="14" height="14"></i><span class="cl">Fraudes</span></div>
      <div class="cv red" id="tend-cv-fraudes">—</div><div class="cd">no período selecionado</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="package-x" class="ci" width="14" height="14"></i><span class="cl">Damaged</span></div>
      <div class="cv" style="color:#f59e0b" id="tend-cv-damaged">—</div><div class="cd">no período selecionado</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="dollar-sign" class="ci" width="14" height="14"></i><span class="cl">BPP Total</span></div>
      <div class="cv" style="color:#FFE600" id="tend-cv-bpp">—</div><div class="cd">cashout no período</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="calendar" class="ci" width="14" height="14"></i><span class="cl">Pico Mensal</span></div>
      <div class="cv" style="color:#f87171" id="tend-cv-pico">—</div><div class="cd" id="tend-cd-pico">mês com mais fraudes</div>
    </div>
  </div>

  <div class="box" style="margin-top:18px">
    <div class="bt">Evolução Mensal — Fraudes, Damaged e BPP em Risco</div>
    <div style="position:relative;height:300px"><canvas id="cTendencia"></canvas></div>
  </div>

  <div class="box" style="margin-top:18px">
    <div class="bt">Detalhamento por Mês</div>
    <table style="width:100%;font-size:12px">
      <thead><tr>
        <th>Mês</th><th style="text-align:right">Fraudes</th><th style="text-align:right">Damaged</th>
        <th style="text-align:right">BPP USD</th><th style="text-align:right">Score Total</th>
      </tr></thead>
      <tbody id="tend-tbody"></tbody>
    </table>
  </div>
</div>


<!-- ABA SAÍDAS MÚLTIPLAS -->
<div id="tab-saidas" class="content">
  <div style="padding:20px 28px">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:16px">
      <div>
        <div style="font-size:18px;font-weight:700;color:#f9fafb">Saídas Múltiplas / Estepe</div>
        <div style="font-size:12px;color:#94a3b8;margin-top:2px">Pacotes que saíram mais de uma vez do SSP30 — Jan/2026 até hoje</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <select id="sm-filtro-risco" onchange="filtrarSaidas()" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 10px;font-size:12px">
          <option value="">Todos os riscos</option>
          <option value="CRITICO">Crítico</option>
          <option value="ALTO">Alto</option>
          <option value="MEDIO">Médio</option>
          <option value="BAIXO">Baixo</option>
        </select>
        <input type="text" id="sm-busca" oninput="filtrarSaidas()" placeholder="Buscar ID / motorista / transportadora…"
          style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 10px;font-size:12px;width:240px">
        <select id="sm-filtro-mes" onchange="filtrarSaidas()" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 10px;font-size:12px">
          <option value="">Todos os meses</option>
        </select>
      </div>
    </div>

    <!-- Cards resumo -->
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px">
      <div class="box" style="flex:1;min-width:140px;padding:14px 18px">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">Total Shipments</div>
        <div id="sm-total" style="font-size:28px;font-weight:700;color:#f9fafb;margin-top:4px">0</div>
      </div>
      <div class="box" style="flex:1;min-width:140px;padding:14px 18px;border-color:#dc2626">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">Críticos</div>
        <div id="sm-criticos" style="font-size:28px;font-weight:700;color:#f87171;margin-top:4px">0</div>
      </div>
      <div class="box" style="flex:1;min-width:140px;padding:14px 18px;border-color:#f59e0b">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">Com Insucesso</div>
        <div id="sm-insucesso" style="font-size:28px;font-weight:700;color:#fbbf24;margin-top:4px">0</div>
      </div>
      <div class="box" style="flex:1;min-width:140px;padding:14px 18px">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">Máx. Tentativas</div>
        <div id="sm-max" style="font-size:28px;font-weight:700;color:#a78bfa;margin-top:4px">0</div>
      </div>
    </div>

    <div class="tbl-wrap">
      <table id="sm-table" style="width:100%;font-size:12px;border-collapse:collapse">
        <thead><tr>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Shipment ID</th>
          <th style="text-align:center;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Saídas</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Primeira Saída</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Última Ação</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Transportadora</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Motorista</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Status Final</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Risco</th>
        </tr></thead>
        <tbody id="sm-tbody"></tbody>
      </table>
    </div>
    <div id="sm-empty" style="display:none;text-align:center;padding:40px;color:#94a3b8;font-size:13px">Nenhum resultado encontrado</div>
  </div>
</div>

<!-- ABA DEVOLUÇÕES -->
<div id="tab-devolucoes" class="content">
  <div style="padding:20px 28px">
    <div id="dv-sem-dados" style="display:none;background:#7c2d1222;border:1px solid #dc2626;color:#fca5a5;padding:12px 16px;border-radius:6px;margin-bottom:14px;font-size:13px">
      ⚠️ Nenhum dado de devoluções carregado — a query BQ retornou vazio ou o cache está desatualizado. Aguarde a próxima atualização ou rode <code>analise_fraude.py</code> novamente.
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:16px">
      <div>
        <div style="font-size:18px;font-weight:700;color:#f9fafb">Devoluções / Empty Box</div>
        <div style="font-size:12px;color:#94a3b8;margin-top:2px">Fluxo de devoluções SSP30 — Jan/2026 até hoje</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <select id="dv-filtro-flujo" onchange="filtrarDevolucoes()" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 10px;font-size:12px">
          <option value="">Todos os fluxos</option>
          <option value="Devolutions">Devolutions</option>
          <option value="EnE">EnE (Entrega em Endereço)</option>
          <option value="Forward">Forward</option>
        </select>
        <select id="dv-filtro-class" onchange="filtrarDevolucoes()" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 10px;font-size:12px">
          <option value="">Todas as classificações LP</option>
          <option value="FRAUD">FRAUD</option>
          <option value="LOST">LOST</option>
          <option value="DAMAGED">DAMAGED</option>
        </select>
        <select id="dv-filtro-mes" onchange="filtrarDevolucoes()" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 10px;font-size:12px">
          <option value="">Todos os meses</option>
        </select>
        <input type="text" id="dv-busca" oninput="filtrarDevolucoes()" placeholder="Buscar seller / ID / causa…"
          style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 10px;font-size:12px;width:220px">
      </div>
    </div>

    <!-- Cards resumo -->
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px">
      <div class="box" style="flex:1;min-width:140px;padding:14px 18px">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">Total Devoluções</div>
        <div id="dv-total" style="font-size:28px;font-weight:700;color:#f9fafb;margin-top:4px">0</div>
      </div>
      <div class="box" style="flex:1;min-width:140px;padding:14px 18px;border-color:#dc2626">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">Classificadas FRAUD</div>
        <div id="dv-fraud" style="font-size:28px;font-weight:700;color:#f87171;margin-top:4px">0</div>
      </div>
      <div class="box" style="flex:1;min-width:140px;padding:14px 18px;border-color:#f59e0b">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">Sellers Únicos</div>
        <div id="dv-sellers" style="font-size:28px;font-weight:700;color:#fbbf24;margin-top:4px">0</div>
      </div>
      <div class="box" style="flex:1;min-width:140px;padding:14px 18px;border-color:#8b5cf6">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">Perdidos/Roubados</div>
        <div id="dv-lost" style="font-size:28px;font-weight:700;color:#a78bfa;margin-top:4px">0</div>
      </div>
    </div>

    <!-- Ranking de sellers ofensores -->
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
      <div class="box" style="padding:14px 16px">
        <div class="bt" style="margin-bottom:10px">Top Sellers com mais Devoluções</div>
        <table style="width:100%;font-size:11px;border-collapse:collapse">
          <thead><tr>
            <th style="text-align:left;padding:4px 6px;color:#94a3b8">Seller</th>
            <th style="text-align:center;padding:4px 6px;color:#94a3b8">Qtd</th>
            <th style="text-align:left;padding:4px 6px;color:#94a3b8">Classif. LP</th>
          </tr></thead>
          <tbody id="dv-sellers-tbody"></tbody>
        </table>
      </div>
      <div class="box" style="padding:14px 16px">
        <div class="bt" style="margin-bottom:10px">Distribuição por Causa (Node Cause)</div>
        <table style="width:100%;font-size:11px;border-collapse:collapse">
          <thead><tr>
            <th style="text-align:left;padding:4px 6px;color:#94a3b8">Causa</th>
            <th style="text-align:center;padding:4px 6px;color:#94a3b8">Qtd</th>
          </tr></thead>
          <tbody id="dv-causa-tbody"></tbody>
        </table>
      </div>
    </div>

    <div class="tbl-wrap">
      <table id="dv-table" style="width:100%;font-size:12px;border-collapse:collapse">
        <thead><tr>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Data</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Shipment ID</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Seller</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Fluxo</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Causa</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Classif. LM</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Classif. LP</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Status RTS</th>
          <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Status BKO</th>
        </tr></thead>
        <tbody id="dv-tbody"></tbody>
      </table>
    </div>
    <div id="dv-empty" style="display:none;text-align:center;padding:40px;color:#94a3b8;font-size:13px">Nenhum resultado encontrado</div>
  </div>
</div>

<!-- SELLERS ENE -->
<div id="tab-sellers_ene" class="content">
  <div style="padding:24px 32px;max-width:1200px">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:6px;flex-wrap:wrap;gap:12px">
      <div>
        <div style="font-size:18px;font-weight:800;color:#fff">Sellers ENE — Entrega Não Efetiva</div>
        <div style="font-size:12px;color:#64748b;margin-top:4px">Sellers com pacotes no fluxo ENE no SSP30 — últimos 3 meses · fonte: BT_LP_NODES</div>
      </div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <input type="text" id="ene-busca" oninput="filtrarSellersENE()" placeholder="Buscar seller nome / ID / SHP…"
          style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 12px;font-size:12px;width:240px">
        <button onclick="exportCSVSellersENE()" style="background:#334155;color:#e2e8f0;border:none;border-radius:6px;padding:6px 14px;font-size:12px;cursor:pointer">
          ⬇ Exportar CSV
        </button>
      </div>
    </div>

    <!-- CARDS -->
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px">
      <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:16px 20px">
        <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Sellers Únicos</div>
        <div id="ene-total-sellers" style="font-size:28px;font-weight:800;color:#fff">0</div>
      </div>
      <div style="background:#0f172a;border:2px solid #86efac22;border-radius:10px;padding:16px 20px">
        <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Total Cashout USD</div>
        <div id="ene-total-cashout" style="font-size:22px;font-weight:800;color:#86efac">0</div>
      </div>
      <div style="background:#0f172a;border:2px solid #fbbf2422;border-radius:10px;padding:16px 20px">
        <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Total ENE (pacotes)</div>
        <div id="ene-total-ene" style="font-size:28px;font-weight:800;color:#fbbf24">0</div>
      </div>
      <div style="background:#0f172a;border:2px solid #38bdf822;border-radius:10px;padding:16px 20px">
        <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Com Cashout</div>
        <div id="ene-total-com-cashout" style="font-size:28px;font-weight:800;color:#38bdf8">0</div>
      </div>
    </div>

    <!-- NOTA -->
    <div style="background:#0c1a2e;border:1px solid #1d4ed8;border-radius:8px;padding:12px 16px;margin-bottom:18px;font-size:12px;color:#93c5fd">
      <strong>Objetivo:</strong> Identificar sellers com recorrência de pacotes no fluxo ENE (Entrega Não Efetiva) no SSP30.
      Dados extraídos de <code>BT_LP_NODES</code> com <code>FLUJO = 'EnE'</code>. Cashout cruzado com <code>DM_LP_MELI_OPTIMIZADO</code>.
      Hover na linha para ver os SHP IDs relacionados.
    </div>

    <!-- TABELA -->
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead>
          <tr>
            <th style="text-align:center;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155;width:40px">#</th>
            <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Seller</th>
            <th style="text-align:center;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Qtd ENE</th>
            <th style="text-align:right;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Cashout USD</th>
            <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Principais Causas</th>
            <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">1ª Ocorrência</th>
            <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Última</th>
            <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Meses</th>
          </tr>
        </thead>
        <tbody id="ene-tbody"></tbody>
      </table>
    </div>
    <div id="ene-empty" style="display:none;text-align:center;padding:40px;color:#94a3b8;font-size:13px">Nenhum resultado encontrado</div>
  </div>
</div>

<!-- DAMAGED ENE -->
<div id="tab-damaged_ene" class="content">
  <div style="padding:24px 32px;max-width:1200px">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:6px;flex-wrap:wrap;gap:12px">
      <div>
        <div style="font-size:18px;font-weight:800;color:#fff">Damaged ENE — Embalagem Danificada</div>
        <div style="font-size:12px;color:#64748b;margin-top:4px">Casos desde jan/2026 — seller alega embalagem chegou danificada · SSP30 · fonte: DM_LP_MELI_OPTIMIZADO + BT_LP_NODES</div>
      </div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <input type="text" id="dene-busca" oninput="filtrarDamagedENE()" placeholder="Buscar SHP / Seller…"
          style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 12px;font-size:12px;width:220px">
        <button onclick="exportCSVDamagedENE()" style="background:#334155;color:#e2e8f0;border:none;border-radius:6px;padding:6px 14px;font-size:12px;cursor:pointer">
          ⬇ Exportar CSV
        </button>
      </div>
    </div>

    <!-- KPI CARDS -->
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px">
      <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:16px 20px">
        <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Total Casos</div>
        <div id="dene-total" style="font-size:28px;font-weight:800;color:#fff">0</div>
      </div>
      <div style="background:#0f172a;border:2px solid #f8717122;border-radius:10px;padding:16px 20px">
        <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">BPP Total USD</div>
        <div id="dene-bpp" style="font-size:22px;font-weight:800;color:#f87171">$0</div>
      </div>
      <div style="background:#0f172a;border:2px solid #fbbf2422;border-radius:10px;padding:16px 20px">
        <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Sellers Únicos</div>
        <div id="dene-sellers" style="font-size:28px;font-weight:800;color:#fbbf24">0</div>
      </div>
      <div style="background:#0f172a;border:2px solid #818cf822;border-radius:10px;padding:16px 20px">
        <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Meses com Dado</div>
        <div id="dene-meses" style="font-size:28px;font-weight:800;color:#818cf8">0</div>
      </div>
    </div>

    <!-- CHARTS ROW -->
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:22px">
      <div style="background:#0c1a2e;border:1px solid #1e3a5f;border-radius:10px;padding:18px">
        <div style="font-size:13px;font-weight:700;color:#93c5fd;margin-bottom:14px">Evolução Mensal de Casos</div>
        <div style="height:200px"><canvas id="dene-chart-mensal"></canvas></div>
      </div>
      <div style="background:#0c1a2e;border:1px solid #1e3a5f;border-radius:10px;padding:18px">
        <div style="font-size:13px;font-weight:700;color:#93c5fd;margin-bottom:14px">Top 10 Sellers por Casos</div>
        <div style="height:200px"><canvas id="dene-chart-sellers"></canvas></div>
      </div>
    </div>

    <!-- WORD CLOUD + CAUSAS -->
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:22px">
      <div style="background:#0c1a2e;border:1px solid #1e3a5f;border-radius:10px;padding:18px">
        <div style="font-size:13px;font-weight:700;color:#93c5fd;margin-bottom:12px">Nuvem de Palavras — Reclamações</div>
        <div id="dene-wordcloud" style="width:100%;height:220px;position:relative;overflow:hidden"></div>
        <div id="dene-wc-empty" style="display:none;text-align:center;padding:40px;color:#64748b;font-size:12px">Sem dados de causa disponíveis</div>
      </div>
      <div style="background:#0c1a2e;border:1px solid #1e3a5f;border-radius:10px;padding:18px">
        <div style="font-size:13px;font-weight:700;color:#93c5fd;margin-bottom:12px">Causas Recorrentes</div>
        <div id="dene-causas-lista" style="max-height:240px;overflow-y:auto"></div>
      </div>
    </div>

    <!-- SELLERS RANKING TABLE -->
    <div style="margin-bottom:22px">
      <div style="font-size:13px;font-weight:700;color:#93c5fd;margin-bottom:10px">Ranking de Sellers Ofensores</div>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <thead>
            <tr>
              <th style="text-align:center;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155;width:36px">#</th>
              <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Seller</th>
              <th style="text-align:center;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Casos</th>
              <th style="text-align:right;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">BPP USD</th>
              <th style="text-align:center;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Meses</th>
              <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Período</th>
            </tr>
          </thead>
          <tbody id="dene-sellers-tbody"></tbody>
        </table>
      </div>
    </div>

    <!-- CASE TABLE -->
    <div>
      <div style="font-size:13px;font-weight:700;color:#93c5fd;margin-bottom:10px">Casos Detalhados (máx. 500 exibidos)</div>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <thead>
            <tr>
              <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">SHP ID</th>
              <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Seller</th>
              <th style="text-align:right;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">BPP USD</th>
              <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Data BPP</th>
              <th style="text-align:left;padding:8px 10px;background:#1e293b;color:#94a3b8;font-weight:600;border-bottom:1px solid #334155">Mês</th>
            </tr>
          </thead>
          <tbody id="dene-casos-tbody"></tbody>
        </table>
      </div>
      <div id="dene-casos-empty" style="display:none;text-align:center;padding:40px;color:#94a3b8;font-size:13px">Nenhum caso encontrado</div>
    </div>
  </div>
</div>

<!-- RELATÓRIO SEMANAL -->
<div id="tab-relatorio" class="content">
  <div style="padding:24px 32px;max-width:1100px">

    <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:12px">
      <div>
        <div style="font-size:18px;font-weight:800;color:#fff">Relatório LP — W{_week}/{_ryear}</div>
        <div style="font-size:12px;color:#6b7280;margin-top:4px">Gerado em {d["gerado"]} · dados acumulados {d["ano"]}</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button onclick="copiarRelatorio('whatsapp')" style="background:#25d366;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-size:13px;font-weight:700;cursor:pointer;display:flex;align-items:center;gap:6px">
          <i data-lucide="message-circle" width="14" height="14"></i> Copiar WhatsApp
        </button>
        <button onclick="copiarRelatorio('email')" style="background:#1f2937;color:#e2e8f0;border:1px solid #374151;border-radius:8px;padding:8px 16px;font-size:13px;font-weight:700;cursor:pointer;display:flex;align-items:center;gap:6px">
          <i data-lucide="mail" width="14" height="14"></i> Copiar Email
        </button>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px">
      <div style="background:#0d1321;border:1px solid rgba(249,115,22,.3);border-radius:10px;padding:16px">
        <div style="font-size:10px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">Sinistros</div>
        <div style="font-size:28px;font-weight:800;color:#f97316">{_sin_total}</div>
        <div style="font-size:11px;color:#9ca3af;margin-top:4px">BPP: ${_sin_bpp:,.0f}</div>
      </div>
      <div style="background:#0d1321;border:1px solid rgba(74,222,128,.3);border-radius:10px;padding:16px">
        <div style="font-size:10px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">GMV Recuperado</div>
        <div style="font-size:28px;font-weight:800;color:#4ade80">${_sin_bpp_r:,.0f}</div>
        <div style="font-size:11px;color:#9ca3af;margin-top:4px">{_sin_rec} eventos ({_sin_taxa:.1f}%)</div>
      </div>
      <div style="background:#0d1321;border:1px solid rgba(96,165,250,.3);border-radius:10px;padding:16px">
        <div style="font-size:10px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">Bloqueados</div>
        <div style="font-size:28px;font-weight:800;color:#60a5fa">{_bl.get("bloqueados",0)}</div>
        <div style="font-size:11px;color:#9ca3af;margin-top:4px">GMV: ${_bl.get("gmv_protegido",0.0):,.0f}</div>
      </div>
      <div style="background:#0d1321;border:1px solid rgba(167,139,250,.3);border-radius:10px;padding:16px">
        <div style="font-size:10px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">ON WAY tratados</div>
        <div style="font-size:28px;font-weight:800;color:#a78bfa" id="rel-wy-count">—</div>
        <div style="font-size:11px;color:#9ca3af;margin-top:4px" id="rel-rt-count">ON ROUTE: —</div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px">
      <div class="box">
        <div class="bt">Sinistros — Detalhe</div>
        <table style="width:100%;font-size:13px;border-collapse:collapse">
          <tr><td style="padding:6px 0;color:#9ca3af;border-bottom:1px solid #1f2937">Total de eventos</td><td style="text-align:right;font-weight:700;border-bottom:1px solid #1f2937">{_sin_total}</td></tr>
          <tr><td style="padding:6px 0;color:#9ca3af;border-bottom:1px solid #1f2937">BPP total</td><td style="text-align:right;color:#f97316;font-weight:700;border-bottom:1px solid #1f2937">${_sin_bpp:,.2f}</td></tr>
          <tr><td style="padding:6px 0;color:#9ca3af;border-bottom:1px solid #1f2937">Casos recuperados</td><td style="text-align:right;font-weight:700;border-bottom:1px solid #1f2937">{_sin_rec}</td></tr>
          <tr><td style="padding:6px 0;color:#9ca3af;border-bottom:1px solid #1f2937">BPP recuperado</td><td style="text-align:right;color:#4ade80;font-weight:700;border-bottom:1px solid #1f2937">${_sin_bpp_r:,.2f}</td></tr>
          <tr><td style="padding:6px 0;color:#9ca3af">Taxa de recuperação</td><td style="text-align:right;font-weight:700">{_sin_taxa:.1f}%</td></tr>
        </table>
      </div>
      <div class="box">
        <div class="bt">Block List — Detalhe</div>
        <table style="width:100%;font-size:13px;border-collapse:collapse">
          <tr><td style="padding:6px 0;color:#9ca3af;border-bottom:1px solid #1f2937">Total de registros</td><td style="text-align:right;font-weight:700;border-bottom:1px solid #1f2937">{_bl.get("total",0)}</td></tr>
          <tr><td style="padding:6px 0;color:#9ca3af;border-bottom:1px solid #1f2937">Bloqueados</td><td style="text-align:right;color:#60a5fa;font-weight:700;border-bottom:1px solid #1f2937">{_bl.get("bloqueados",0)}</td></tr>
          <tr><td style="padding:6px 0;color:#9ca3af;border-bottom:1px solid #1f2937">Monitorados</td><td style="text-align:right;color:#fbbf24;font-weight:700;border-bottom:1px solid #1f2937">{_bl.get("monitorados",0)}</td></tr>
          <tr><td style="padding:6px 0;color:#9ca3af;border-bottom:1px solid #1f2937">Solicitados</td><td style="text-align:right;font-weight:700;border-bottom:1px solid #1f2937">{_bl.get("solicitados",0)}</td></tr>
          <tr><td style="padding:6px 0;color:#9ca3af">GMV protegido</td><td style="text-align:right;color:#4ade80;font-weight:700">${_bl.get("gmv_protegido",0.0):,.2f}</td></tr>
        </table>
      </div>
    </div>

    <div class="box" style="margin-bottom:24px">
      <div class="bt">Fraudes/Damaged — Acumulado {d["ano"]}</div>
      <div style="display:flex;gap:32px;flex-wrap:wrap;padding-top:8px">
        <div><div style="font-size:11px;color:#6b7280;margin-bottom:4px">Fraudes/Lost</div><div style="font-size:22px;font-weight:800;color:#ef4444">{d["total_fraudes"]}</div></div>
        <div><div style="font-size:11px;color:#6b7280;margin-bottom:4px">Damaged</div><div style="font-size:22px;font-weight:800;color:#f59e0b">{d["total_damaged"]}</div></div>
        <div><div style="font-size:11px;color:#6b7280;margin-bottom:4px">BPP Total</div><div style="font-size:22px;font-weight:800;color:#34d399">${d["total_bpp"]:,.0f}</div></div>
        <div><div style="font-size:11px;color:#6b7280;margin-bottom:4px">Cruzados F+D</div><div style="font-size:22px;font-weight:800;color:#f87171">{len(d["cruzados"])}</div></div>
      </div>
    </div>

    <div class="box">
      <div class="bt">Observações</div>
      <textarea id="rel-obs" placeholder="Adicione observações para incluir no relatório..." style="width:100%;min-height:70px;background:#080d19;color:#e2e8f0;border:1px solid #374151;border-radius:6px;padding:10px;font-size:13px;resize:vertical;font-family:inherit;margin-top:8px"></textarea>
    </div>

  </div>
  <div id="rel-toast" style="position:fixed;bottom:24px;right:24px;background:#0d1321;color:#4ade80;border:1px solid #4ade80;border-radius:8px;padding:10px 18px;font-size:13px;font-weight:700;display:none;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,.5)">✓ Copiado!</div>
</div>

</main>
</div>

</body>
</html>'''

# ============================================================
# SINISTROS — página standalone
# ============================================================
def gerar_sinistros_html(dados):
    sin_d          = dados.get('sinistros', {})
    sin_casos      = sin_d.get('casos', [])
    sin_total      = sin_d.get('total', 0)
    sin_bpp        = sin_d.get('bpp_total', 0.0)
    sin_rec        = sin_d.get('recuperados', 0)
    sin_bpp_r      = sin_d.get('bpp_recuperado', 0.0)
    taxa_rec       = round(sin_rec / sin_total * 100, 1) if sin_total else 0
    sin_rows       = (''.join(_sin_row_html(c) for c in reversed(sin_casos[-200:])) or
                     '<tr><td colspan="10" style="text-align:center;padding:48px 20px;color:#6b7280">'
                     '<div style="font-size:15px;margin-bottom:10px">⚠️ Nenhum evento SVC cadastrado</div>'
                     '<div style="font-size:12px;line-height:1.6">Clique em <span style="color:#f97316;font-weight:600">🚨 Novo Caso</span>'
                     ' para registrar o primeiro sinistro na planilha <em>Eventos SVC</em>.</div>'
                     '</td></tr>')
    sin_json       = json.dumps(sin_casos, ensure_ascii=False)
    cep_cluster_map = dados.get('cep_cluster_map', {})
    cep_cluster_json = json.dumps(cep_cluster_map, ensure_ascii=False)
    return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sinistros SSP30 — Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🚨</text></svg>">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#080d19;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}}
  .header{{background:#080d19;padding:16px 32px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #7f1d1d;position:sticky;top:0;z-index:100}}
  .header-brand{{display:flex;align-items:center;gap:12px}}
  .header-accent{{width:3px;height:32px;background:#f97316;border-radius:2px}}
  .header-title{{font-size:15px;font-weight:700;color:#fff}}
  .header-sub{{font-size:11px;color:#6b7280;margin-top:2px}}
  .mod-nav{{display:flex;gap:4px;align-items:center}}
  .mod-btn{{padding:6px 14px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid #1f2937;text-decoration:none;transition:all .2s;color:#9ca3af;background:#0d1321;display:flex;align-items:center;gap:6px}}
  .mod-btn:hover{{background:#1f2937;color:#e2e8f0;border-color:#374151}}
  .mod-btn.m-fraude{{color:#ef4444;background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.3)}}
  .mod-btn.m-risco{{color:#FFE600;background:rgba(255,230,0,.08);border-color:rgba(255,230,0,.2)}}
  .mod-btn.m-isca{{color:#4ade80;background:rgba(74,222,128,.08);border-color:rgba(74,222,128,.2)}}
  .mod-btn.m-cftv{{color:#60a5fa;background:rgba(96,165,250,.08);border-color:rgba(96,165,250,.2)}}
  .mod-btn.m-sinistros{{color:#f97316;background:rgba(249,115,22,.08);border-color:rgba(249,115,22,.2)}}
  .main{{padding:24px 32px;max-width:1400px;margin:0 auto}}
  .cards-grid{{display:grid;gap:16px;margin-bottom:24px}}
  .card{{background:#0d1321;border:1px solid #1f2937;border-radius:10px;padding:16px 20px}}
  .card-header{{display:flex;align-items:center;gap:8px;margin-bottom:10px}}
  .ci{{color:#6b7280;flex-shrink:0}}
  .cl{{font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.5px}}
  .cv{{font-size:26px;font-weight:800;letter-spacing:-.5px}}
  .cd{{font-size:11px;color:#6b7280;margin-top:4px}}
  .red{{color:#f87171}}
  .green{{color:#4ade80}}
  .c-red{{border-color:rgba(248,113,113,.2);background:rgba(248,113,113,.04)}}
  .box{{background:#0d1321;border:1px solid #1f2937;border-radius:10px;padding:20px}}
  .bt{{font-size:13px;font-weight:700;color:#e2e8f0;margin-bottom:14px}}
  #sin-mapa .leaflet-container{{background:#080d19}}
  {diario_css()}
</style>
</head>
<body>

<div class="header">
  <div class="header-brand">
    <div class="header-accent"></div>
    <div>
      <div class="header-title">Sinistros / Eventos SVC — SSP30</div>
      <div class="header-sub">Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}</div>
    </div>
  </div>
  <div class="mod-nav">
    <a href="./fraude.html" class="mod-btn m-fraude">
      <i data-lucide="shield-alert" width="12" height="12"></i> Fraude
    </a>
    <a href="./index.html" class="mod-btn">
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
    {diario_nav_btn()}
  </div>
</div>
{diario_panel_html()}

<div class="main">

  <div class="cards-grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card c-red">
      <div class="card-header"><span style="font-size:14px">🚨</span><span class="cl">Total Sinistros</span></div>
      <div class="cv red">{sin_total}</div>
      <div class="cd">Eventos registrados</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="dollar-sign" class="ci" width="14" height="14"></i><span class="cl">BPP em Risco</span></div>
      <div class="cv red">${sin_bpp:,.2f}</div>
      <div class="cd">Valor total dos casos</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="check-circle" class="ci" width="14" height="14"></i><span class="cl">Carga Recuperada</span></div>
      <div class="cv green">{sin_rec}</div>
      <div class="cd">{taxa_rec}% dos casos</div>
    </div>
    <div class="card">
      <div class="card-header"><i data-lucide="trending-down" class="ci" width="14" height="14"></i><span class="cl">BPP Recuperado</span></div>
      <div class="cv green">${sin_bpp_r:,.2f}</div>
      <div class="cd">Valor recuperado</div>
    </div>
  </div>

  <div class="box">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <div class="bt" style="margin-bottom:0">Eventos SVC — Histórico ({sin_total} registros)</div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <select id="sin-mes-filtro" onchange="filtrarSinistrosPeriodo()"
          style="background:#1f2937;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:6px 10px;font-size:11px">
          <option value="">Todos os meses</option>
        </select>
        <input id="sin-filter" type="text" placeholder="Filtrar por driver, placa, tipo..."
          oninput="filtrarSinistrosPeriodo()"
          style="background:#1f2937;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:6px 12px;font-size:11px;width:220px">
        <button onclick="toggleMapa()"
          style="background:#1f2937;color:#60a5fa;border:1px solid #374151;border-radius:6px;padding:7px 14px;font-size:12px;cursor:pointer;white-space:nowrap">
          🗺️ Mapa
        </button>
        <button onclick="openSinistroModal()"
          style="background:#dc2626;color:#fff;border:none;border-radius:6px;padding:7px 16px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap">
          🚨 Novo Caso
        </button>
      </div>
    </div>
    <div id="sin-mapa-wrap" style="display:none;margin-bottom:16px">
      <div id="sin-mapa" style="height:500px;border-radius:8px;border:1px solid #374151"></div>
    </div>
    <div style="overflow-x:auto;max-height:65vh;overflow-y:auto">
      <table style="width:100%;font-size:12px;border-collapse:collapse">
        <thead style="position:sticky;top:0;background:#0d1321;z-index:1">
          <tr>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;font-weight:600;white-space:nowrap">Data</th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;font-weight:600">Hora</th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;font-weight:600">Tipo</th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;font-weight:600">Driver</th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;font-weight:600">Transportadora</th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;font-weight:600">Placa</th>
            <th style="padding:8px 10px;text-align:center;color:#6b7280;font-weight:600">Qtd</th>
            <th style="padding:8px 10px;text-align:right;color:#6b7280;font-weight:600">BPP</th>
            <th style="padding:8px 10px;text-align:center;color:#6b7280;font-weight:600">Recup.</th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;font-weight:600">Relato</th>
          </tr>
        </thead>
        <tbody id="sin-tbody">{sin_rows}</tbody>
      </table>
    </div>
  </div>

</div>

<!-- MODAL NOVO SINISTRO -->
<div id="sin-modal" onclick="if(event.target===this)closeSinistroModal()"
  style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:9999;align-items:center;justify-content:center">
  <div style="background:#0d1321;border:1px solid #374151;border-radius:12px;width:900px;max-width:95vw;max-height:90vh;overflow-y:auto;padding:28px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
      <h2 style="font-size:16px;font-weight:700;color:#fff">🚨 Registrar Novo Sinistro</h2>
      <button onclick="closeSinistroModal()" style="background:none;border:none;color:#6b7280;font-size:22px;cursor:pointer;line-height:1">✕</button>
    </div>
    <div style="margin-bottom:16px">
      <label style="font-size:11px;color:#6b7280;display:block;margin-bottom:6px">Cole o texto do WhatsApp (o parser preenche os campos automaticamente):</label>
      <textarea id="sin-raw" rows="6"
        placeholder="REPORT DE SINISTRO&#10;🗓️ Data: 13/06/2025&#10;⌚ Horário: 23:25h&#10;🚚 Transportadora: BR LOGISTICS&#10;📱 ROTA: 8005&#10;👤 Nome completo do driver: ..."
        style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:8px;padding:12px;font-size:12px;resize:vertical;font-family:monospace"></textarea>
      <button onclick="parsearSinistro()" style="margin-top:8px;background:#1f2937;color:#60a5fa;border:1px solid #374151;border-radius:6px;padding:7px 18px;font-size:12px;cursor:pointer">
        ⚡ Parsear campos automaticamente
      </button>
    </div>
    <hr style="border-color:#1f2937;margin-bottom:16px">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Data</label>
        <input id="sf-data" type="text" placeholder="dd/mm/aaaa" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Horário</label>
        <input id="sf-horario" type="text" placeholder="HH:MM" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Transportadora (MLP)</label>
        <input id="sf-transp" type="text" placeholder="Ex: BR LOGISTICS" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Rota</label>
        <input id="sf-rota" type="text" placeholder="Ex: 8005" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">ID do Driver</label>
        <input id="sf-id-driver" type="text" placeholder="Ex: 3416578" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Nome do Driver</label>
        <input id="sf-nome" type="text" placeholder="Nome completo" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Placa</label>
        <input id="sf-placa" type="text" placeholder="Ex: BJK5A49" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Veículo</label>
        <input id="sf-veiculo" type="text" placeholder="Ex: FURGÃO" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Qtd Total de Embalagens</label>
        <input id="sf-qtd-total" type="text" placeholder="Ex: 22" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Qtd Recuperada</label>
        <input id="sf-qtd-rec" type="text" placeholder="Ex: 0" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Valor do Sinistro (BPP)</label>
        <input id="sf-valor" type="text" placeholder="Ex: $405.43" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">CEP <span id="sf-cep-spin" style="display:none;color:#60a5fa">⟳</span></label>
        <input id="sf-cep" type="text" placeholder="Ex: 02533010" oninput="buscarCEP(this.value)" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Bairro</label>
        <input id="sf-bairro" type="text" placeholder="Auto-preenchido via CEP" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Cidade</label>
        <input id="sf-cidade" type="text" placeholder="Auto-preenchido via CEP" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Cluster</label>
        <input id="sf-cluster" type="text" placeholder="Auto-preenchido via CEP" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px"></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Natureza do Evento</label>
        <select id="sf-natureza" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px">
          <option value="">Selecione...</option>
          <option>Roubo</option>
          <option>Furto</option>
          <option>Extravio</option>
          <option>Dano</option>
          <option>Outros</option>
        </select></div>
      <div><label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Boletim de Ocorrência</label>
        <select id="sf-boletim" onchange="toggleLinkBO()" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px">
          <option value="">Selecione...</option>
          <option>Sim</option>
          <option>Não</option>
        </select></div>
    </div>
    <div id="sf-link-bo-wrap" style="margin-bottom:12px;display:none">
      <label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Link do Boletim</label>
      <input id="sf-link-bo" type="text" placeholder="URL ou número do BO" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px">
    </div>
    <div style="margin-bottom:12px">
      <label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Local / Rua</label>
      <input id="sf-local" type="text" placeholder="Endereço completo" style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px">
    </div>
    <div style="margin-bottom:12px">
      <label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Modus Operandi</label>
      <input id="sf-modus" type="text" placeholder="Descreva como ocorreu..." style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:8px 10px;font-size:12px">
    </div>
    <div style="margin-bottom:20px">
      <label style="font-size:10px;color:#6b7280;display:block;margin-bottom:4px">Relato</label>
      <textarea id="sf-relato" rows="4" placeholder="Descrição do evento..."
        style="width:100%;background:#060a14;border:1px solid #374151;color:#e2e8f0;border-radius:6px;padding:10px;font-size:12px;resize:vertical"></textarea>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:10px">
      <button onclick="closeSinistroModal()" style="background:#1f2937;color:#6b7280;border:1px solid #374151;border-radius:6px;padding:8px 20px;font-size:12px;cursor:pointer">Cancelar</button>
      <button onclick="salvarSinistro()" id="sin-save-btn"
        style="background:#dc2626;color:#fff;border:none;border-radius:6px;padding:8px 24px;font-size:12px;font-weight:600;cursor:pointer">
        💾 Salvar na Planilha
      </button>
    </div>
    <div id="sin-msg" style="margin-top:12px;font-size:12px;text-align:center;min-height:18px"></div>
  </div>
</div>

<script>
const SINISTROS_DATA = {sin_json};
const _CEP_CLUSTER = {cep_cluster_json};

// --- Meses disponíveis para filtro ---
(function initMesFiltro() {{
  const meses = {{}};
  SINISTROS_DATA.forEach(c => {{
    if (!c.data) return;
    const parts = c.data.split('/');
    if (parts.length < 3) return;
    const key = parts[2] + '-' + parts[1];
    const label = parts[1] + '/' + parts[2];
    meses[key] = label;
  }});
  const sel = document.getElementById('sin-mes-filtro');
  if (!sel) return;
  Object.keys(meses).sort().reverse().forEach(k => {{
    const opt = document.createElement('option');
    opt.value = k;
    opt.textContent = meses[k];
    sel.appendChild(opt);
  }});
}})();

function openSinistroModal() {{
  document.getElementById('sin-modal').style.display = 'flex';
}}
function closeSinistroModal() {{
  document.getElementById('sin-modal').style.display = 'none';
  const msg = document.getElementById('sin-msg');
  if (msg) msg.textContent = '';
}}

function toggleLinkBO() {{
  const v = document.getElementById('sf-boletim').value;
  document.getElementById('sf-link-bo-wrap').style.display = v === 'Sim' ? 'block' : 'none';
}}

async function buscarCEP(rawCep) {{
  const cep = rawCep.replace(/\\D/g, '');
  if (cep.length !== 8) return;
  const spin = document.getElementById('sf-cep-spin');
  if (spin) spin.style.display = 'inline';
  try {{
    // Cluster do dicionário BQ
    const cc = _CEP_CLUSTER[cep];
    if (cc) {{
      document.getElementById('sf-cluster').value = cc.cluster || '';
    }}
    // Endereço via ViaCEP
    const r = await fetch('https://viacep.com.br/ws/' + cep + '/json/');
    const d = await r.json();
    if (!d.erro) {{
      if (d.bairro) document.getElementById('sf-bairro').value = d.bairro;
      if (d.localidade) document.getElementById('sf-cidade').value = d.localidade;
      if (d.logradouro && !document.getElementById('sf-local').value)
        document.getElementById('sf-local').value = d.logradouro;
    }}
  }} catch(e) {{}}
  finally {{ if (spin) spin.style.display = 'none'; }}
}}

function parsearSinistro() {{
  const txt = document.getElementById('sin-raw').value;
  const lines = txt.split('\\n');
  const fld = {{}};
  for (const ln of lines) {{
    const ci = ln.indexOf(':');
    if (ci < 0) continue;
    const rk = ln.slice(0, ci);
    const val = ln.slice(ci + 1).trim();
    if      (rk.includes('🗓') || /^data$/i.test(rk.trim()))       fld.data = val;
    else if (rk.includes('⌚') || /hor[aá]r/i.test(rk))            fld.horario = val;
    else if (rk.includes('🚚') || /transportadora/i.test(rk))      fld.transp = val;
    else if (rk.includes('📱') || /rota/i.test(rk))                fld.rota = val;
    else if (rk.includes('👤') || /nome/i.test(rk))                fld.nome = val;
    else if (rk.includes('🙍') || /id.+driver/i.test(rk))          fld.id_driver = val;
    else if (rk.includes('🚘') || /placa/i.test(rk))               fld.placa = val;
    else if (rk.includes('🚙') || /ve[ií]culo/i.test(rk))          fld.veiculo = val;
    else if (rk.includes('📍') || /local/i.test(rk))               fld.local = val;
    else if (rk.includes('🗾') || /cep/i.test(rk))                 fld.cep = val;
    else if ((rk.includes('📦') && !rk.includes('✅')) || /qtd.+total/i.test(rk)) fld.qtd_total = val;
    else if (rk.includes('✅') || /qtd.+recup/i.test(rk))          fld.qtd_rec = val;
    else if (rk.includes('💰') || /valor/i.test(rk))               fld.valor = val;
    else if (/bairro/i.test(rk))                                    fld.bairro = val;
    else if (/cidade/i.test(rk))                                    fld.cidade = val;
    else if (/natureza/i.test(rk))                                  fld.natureza = val;
    else if (/modus/i.test(rk))                                     fld.modus = val;
    else if (/boletim/i.test(rk))                                   fld.boletim = val;
    else if (/link.*(bo|boletim)/i.test(rk))                        fld.link_bo = val;
    else if (rk.includes('🗒') || /relato/i.test(rk))              fld.relato = val;
  }}
  const sv = (id, v) => {{ if (v !== undefined) document.getElementById(id).value = v; }};
  sv('sf-data',      fld.data);
  sv('sf-horario',   fld.horario);
  sv('sf-transp',    fld.transp);
  sv('sf-rota',      fld.rota);
  sv('sf-id-driver', fld.id_driver);
  sv('sf-nome',      fld.nome);
  sv('sf-placa',     fld.placa);
  sv('sf-veiculo',   fld.veiculo);
  sv('sf-qtd-total', fld.qtd_total);
  sv('sf-qtd-rec',   fld.qtd_rec);
  sv('sf-valor',     fld.valor);
  sv('sf-cep',       fld.cep);
  sv('sf-bairro',    fld.bairro);
  sv('sf-cidade',    fld.cidade);
  sv('sf-natureza',  fld.natureza);
  sv('sf-modus',     fld.modus);
  sv('sf-boletim',   fld.boletim);
  sv('sf-link-bo',   fld.link_bo);
  sv('sf-local',     fld.local);
  sv('sf-relato',    fld.relato);
  if (fld.cep) buscarCEP(fld.cep);
  toggleLinkBO();
}}

function filtrarSinistros(q) {{
  q = (q || '').toLowerCase();
  if (!q) {{ renderSinistrosTable(SINISTROS_DATA); return; }}
  const filtered = SINISTROS_DATA.filter(c =>
    ((c.driver_id||'')+(c.nome||'')+(c.placa||'')+(c.transportadora||'')+(c.tipo||'')+(c.rua||'')+(c.relato||''))
    .toLowerCase().includes(q)
  );
  renderSinistrosTable(filtered);
}}

function filtrarSinistrosPeriodo() {{
  const q = (document.getElementById('sin-filter').value || '').toLowerCase();
  const mes = document.getElementById('sin-mes-filtro').value || '';
  let filtered = SINISTROS_DATA;
  if (mes) {{
    const [ano, mm] = mes.split('-');
    filtered = filtered.filter(c => {{
      if (!c.data) return false;
      const parts = c.data.split('/');
      if (parts.length < 3) return false;
      return parts[2] === ano && parts[1] === mm;
    }});
  }}
  if (q) {{
    filtered = filtered.filter(c =>
      ((c.driver_id||'')+(c.nome||'')+(c.placa||'')+(c.transportadora||'')+(c.tipo||'')+(c.rua||'')+(c.bairro||'')+(c.natureza||'')+(c.relato||''))
      .toLowerCase().includes(q)
    );
  }}
  renderSinistrosTable(filtered);
}}

function renderSinistrosTable(data) {{
  const tbody = document.getElementById('sin-tbody');
  if (!tbody) return;
  const rows = [...data].reverse().slice(0,200);
  tbody.innerHTML = rows.map(c => {{
    const rec = (c.recup_carga||'').toLowerCase();
    const recOk = rec==='sim'||rec==='yes'||rec==='s';
    const bpp = c.bpp ? '$'+parseFloat(c.bpp).toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}}) : '—';
    const tc = /sinistro/i.test(c.tipo||'') ? '#f87171' : '#fbbf24';
    const relato = (c.relato||'').slice(0,65)+((c.relato||'').length>65?'...':'');
    return `<tr style="border-top:1px solid #111827">
      <td style="padding:7px 10px;white-space:nowrap">${{c.data}}</td>
      <td style="padding:7px 10px;white-space:nowrap">${{c.horario}}</td>
      <td style="padding:7px 10px"><span style="background:${{tc}}22;color:${{tc}};padding:2px 6px;border-radius:4px;font-size:10px">${{c.tipo||'—'}}</span></td>
      <td style="padding:7px 10px"><span style="font-family:monospace;color:#60a5fa">${{c.driver_id}}</span><br><span style="font-size:10px;color:#9ca3af">${{c.nome}}</span></td>
      <td style="padding:7px 10px;font-size:11px">${{c.transportadora||'—'}}</td>
      <td style="padding:7px 10px;font-family:monospace;font-size:11px">${{c.placa||'—'}}</td>
      <td style="padding:7px 10px;text-align:center">${{c.qtd_shp||'—'}}</td>
      <td style="padding:7px 10px;text-align:right;font-weight:600;color:#f87171">${{bpp}}</td>
      <td style="padding:7px 10px;text-align:center;color:${{recOk?'#4ade80':'#f87171'}}">${{recOk?'Sim':'Não'}}</td>
      <td style="padding:7px 10px;font-size:10px;color:#9ca3af;max-width:200px">${{relato}}</td>
    </tr>`;
  }}).join('');
}}

async function salvarSinistro() {{
  const btn = document.getElementById('sin-save-btn');
  const msg = document.getElementById('sin-msg');
  btn.disabled = true; btn.textContent = 'Salvando...';
  msg.textContent = '';
  const payload = {{
    data:      document.getElementById('sf-data').value.trim(),
    horario:   document.getElementById('sf-horario').value.trim(),
    transp:    document.getElementById('sf-transp').value.trim(),
    rota:      document.getElementById('sf-rota').value.trim(),
    id_driver: document.getElementById('sf-id-driver').value.trim(),
    nome:      document.getElementById('sf-nome').value.trim(),
    placa:     document.getElementById('sf-placa').value.trim(),
    veiculo:   document.getElementById('sf-veiculo').value.trim(),
    qtd_total: document.getElementById('sf-qtd-total').value.trim(),
    qtd_rec:   document.getElementById('sf-qtd-rec').value.trim(),
    valor:     document.getElementById('sf-valor').value.trim(),
    cep:       document.getElementById('sf-cep').value.trim(),
    bairro:    document.getElementById('sf-bairro').value.trim(),
    cidade:    document.getElementById('sf-cidade').value.trim(),
    cluster:   document.getElementById('sf-cluster').value.trim(),
    local:     document.getElementById('sf-local').value.trim(),
    natureza:  document.getElementById('sf-natureza').value.trim(),
    modus:     document.getElementById('sf-modus').value.trim(),
    boletim:   document.getElementById('sf-boletim').value.trim(),
    link_bo:   document.getElementById('sf-link-bo').value.trim(),
    relato:    document.getElementById('sf-relato').value.trim(),
  }};
  try {{
    const r = await fetch('http://localhost:5000/sinistros/add', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify(payload)
    }});
    const res = await r.json();
    if (res.ok) {{
      msg.style.color = '#4ade80';
      msg.textContent = '✓ Salvo com sucesso na planilha!';
      setTimeout(() => closeSinistroModal(), 1800);
    }} else {{
      msg.style.color = '#f87171';
      msg.textContent = 'Erro: ' + (res.error || 'desconhecido');
    }}
  }} catch(e) {{
    msg.style.color = '#f87171';
    msg.textContent = 'Erro de conexão com servidor local (porta 5000).';
  }}
  btn.disabled = false; btn.textContent = '💾 Salvar na Planilha';
}}

// --- MAPA LEAFLET ---
var _leafletMap = null;
function toggleMapa() {{
  const wrap = document.getElementById('sin-mapa-wrap');
  if (!wrap) return;
  const visible = wrap.style.display !== 'none';
  wrap.style.display = visible ? 'none' : 'block';
  if (!visible) initMapa();
}}

function initMapa() {{
  if (_leafletMap) {{ _leafletMap.invalidateSize(); return; }}
  _leafletMap = L.map('sin-mapa').setView([-23.55, -46.63], 11);
  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    attribution: '&copy; OpenStreetMap &copy; CARTO',
    subdomains: 'abcd', maxZoom: 19
  }}).addTo(_leafletMap);

  // Agrupar por CEP5 para hotspots
  const cep5map = {{}};
  const pontos = [];

  SINISTROS_DATA.forEach(c => {{
    const cepRaw = (c.cep || '').replace(/\\D/g,'');
    if (!cepRaw) return;
    const cc = _CEP_CLUSTER[cepRaw];
    if (!cc || !cc.lat || !cc.lon) return;
    const lat = cc.lat, lon = cc.lon;
    const bpp = parseFloat(c.bpp) || 0;
    let cor = '#facc15';   // amarelo  < 500
    if (bpp >= 2000) cor = '#ef4444'; // vermelho > 2000
    else if (bpp >= 500) cor = '#f97316'; // laranja 500-2000
    const popup = '<b>' + (c.nome||'Driver') + '</b><br>Data: ' + (c.data||'—') +
      '<br>BPP: $' + bpp.toFixed(2) +
      '<br>Bairro: ' + (c.bairro || cc.cluster || '—') +
      '<br>Cluster: ' + (cc.cluster || '—');
    pontos.push({{lat, lon, cor, popup, cep5: cepRaw.slice(0,5)}});
    const k = cepRaw.slice(0,5);
    cep5map[k] = (cep5map[k] || 0) + 1;
  }});

  pontos.forEach(p => {{
    L.circleMarker([p.lat, p.lon], {{
      radius: 7, color: p.cor, fillColor: p.cor,
      fillOpacity: 0.8, weight: 1.5
    }}).bindPopup(p.popup).addTo(_leafletMap);
  }});

  // Hotspots: CEP5 com 2+ sinistros
  const cep5lats = {{}};
  pontos.forEach(p => {{
    if (!cep5lats[p.cep5]) cep5lats[p.cep5] = [];
    cep5lats[p.cep5].push([p.lat, p.lon]);
  }});
  Object.entries(cep5map).forEach(([k, cnt]) => {{
    if (cnt < 2) return;
    const coords = cep5lats[k];
    if (!coords || !coords.length) return;
    const avgLat = coords.reduce((s,c) => s+c[0],0)/coords.length;
    const avgLon = coords.reduce((s,c) => s+c[1],0)/coords.length;
    L.circle([avgLat, avgLon], {{
      radius: 300 + cnt * 80,
      color: '#f97316', fillColor: '#f97316',
      fillOpacity: 0.15, weight: 2, dashArray: '6 4'
    }}).bindPopup('<b>Hotspot CEP ' + k + 'xxx</b><br>' + cnt + ' sinistros').addTo(_leafletMap);
  }});

  if (pontos.length > 0) {{
    const bounds = pontos.map(p => [p.lat, p.lon]);
    _leafletMap.fitBounds(L.latLngBounds(bounds).pad(0.15));
  }}
}}

lucide.createIcons();
{diario_js()}
</script>

</body>
</html>'''


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("="*55)
    print(f"Análise de Fraude SSP30 — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("="*55)

    bq, gs = conectar()

    print("\nConsultando BigQuery (paralelo)...")
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
    _queries = {
        'score':    (QUERY_DRIVER_SCORE,    'Score por Driver'),
        'shp':      (QUERY_DRIVER_SHIPMENTS,'SHP IDs por Driver'),
        'status':   (QUERY_DRIVER_STATUS,   'Status dos Drivers'),
        'routes':   (QUERY_DRIVER_ROUTES,   'Rotas dos Drivers'),
        'placa':    (QUERY_DRIVER_PLACA,    'Placas dos Drivers'),
        'dxp':      (QUERY_DRIVER_PLACE,    'Driver x Place'),
        'places':   (QUERY_PLACES,          'Places'),
        'place_shp':(QUERY_PLACE_SHIPMENTS, 'SHP IDs por Place'),
        'damaged':  (QUERY_DAMAGED,         'Damaged por Driver'),
        'crz':      (QUERY_CRUZAMENTO,       'Sellers/Buyers Ofensores'),
        'crz_mes':  (QUERY_CRUZAMENTO_MES,  'Sellers/Buyers por Mês'),
        'dc_nex':   (QUERY_DC_NEX,          'DC/NEX/XPT Passages'),
        'buyer_vel':(QUERY_BUYER_VELOCIDADE,'Velocidade de Compra Buyers'),
    }
    _res = {}
    with ThreadPoolExecutor(max_workers=13) as _pool:
        _futs = {_pool.submit(buscar, bq, q, nm): key for key, (q, nm) in _queries.items()}
        for _f in _as_completed(_futs):
            _res[_futs[_f]] = _f.result()
    df_score          = _res['score']
    df_shp            = _res['shp']
    df_status         = _res['status']
    df_routes         = _res['routes']
    df_placa          = _res['placa']
    df_dxp            = _res['dxp']
    df_places         = _res['places']
    df_place_shp      = _res['place_shp']
    df_damaged        = _res['damaged']
    df_cruzamento     = _res['crz']
    df_cruzamento_mes = _res['crz_mes']
    df_dc_nex         = _res['dc_nex']
    df_buyer_vel      = _res['buyer_vel']

    bl_rows   = carregar_block_list(gs)
    sincronizar_status_block_list(gs, bq, bl_rows)
    cobrar_otr_map = carregar_cobrar_otr(gs)

    print("\nProcessando...")
    dados = processar(df_score, df_dxp, df_places, df_damaged, df_shp, df_place_shp, df_status, df_routes)
    dados['bl']        = processar_block_list(bl_rows)
    dados['alertas_bl'] = detectar_alertas_bl(dados['bl'], dados['shp_por_driver'])
    # Placa do BQ (fonte primária) + block list (nome + fallback placa)
    _placa_map = {norm_id(str(r.get('DRIVER_ID', ''))): str(r.get('LICENCE_PLATE', '') or '').strip()
                  for _, r in df_placa.iterrows()
                  if str(r.get('LICENCE_PLATE', '') or '').strip()}
    _bl_map = {r['driver_id']: r for r in dados['bl'].get('rows', [])}
    _JA_BLOQUEADOS = {'bloqueado', 'blocked'}
    def _enrich_acbl(lista):
        for _c in lista:
            _bl = _bl_map.get(_c['id'], {})
            if not _c.get('nome') and _bl.get('nome'):
                _c['nome'] = _bl['nome']
            _c['placa']           = _placa_map.get(_c['id']) or _bl.get('placa', '')
            _c['data_solicitacao']= _bl.get('data', '')
            _c['status_bl']       = _bl.get('status', '')
        return [c for c in lista if c.get('status_bl', '').strip().lower() not in _JA_BLOQUEADOS]
    dados['acumulo_bloqueio'] = _enrich_acbl(dados.get('acumulo_bloqueio', []))
    for _pk in list(dados.get('acumulo_por_periodo', {}).keys()):
        dados['acumulo_por_periodo'][_pk] = _enrich_acbl(dados['acumulo_por_periodo'][_pk])
    dados['crz']       = processar_cruzamento(df_cruzamento)
    dados['crz_mes']   = processar_cruzamento_mes(df_cruzamento_mes)
    dados['dc_nex']    = processar_dc_nex(df_dc_nex, cobrar_otr_map)
    dados['buyer_vel'] = processar_buyer_velocidade(df_buyer_vel)
    # --- CEP → Cluster (SSP30) ---
    print("  Buscando mapa CEP->Cluster no BQ...")
    try:
        query_cep = """
            SELECT
                SHP_ADD_ZIP_CODE AS cep,
                MAX(SHP_LG_CLUSTER_ID) AS cluster
            FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS_LAST_MILE`
            WHERE SHP_LG_FACILITY_ID = 'SSP30'
                AND SHP_ADD_ZIP_CODE IS NOT NULL
                AND SHP_LG_CLUSTER_ID IS NOT NULL
            GROUP BY 1
            LIMIT 5000
        """
        from google.cloud import bigquery as bq_module
        bq_client = bq_module.Client(project='meli-bi-data')
        cep_cluster_map = {}
        for row in bq_client.query(query_cep).result():
            cep_cluster_map[str(row.cep).replace('-', '')] = {
                'cluster': row.cluster or '',
                'lat': None,
                'lon': None,
            }
        print(f"  CEP->Cluster: {len(cep_cluster_map)} CEPs mapeados")
    except Exception as e:
        print(f"  Aviso CEP->Cluster: {e}")
        cep_cluster_map = {}

    dados['cep_cluster_map'] = cep_cluster_map
    dados['sinistros'] = carregar_sinistros(gs)

    # Geocodificar CEPs dos sinistros: ViaCEP -> endereço -> Nominatim
    import urllib.request as _ur, urllib.parse as _up, json as _json, time as _time
    _NOMINATIM_UA = 'SSP30-Dashboard/1.0 lucas.unascimento@mercadolivre.com'
    _ceps_sin = list({c['cep'].replace('-','').strip() for c in dados['sinistros']['casos'] if c.get('cep') and len(c['cep'].replace('-','').strip()) == 8})
    _geo_cache = {}
    print(f"  Geocodificando {len(_ceps_sin)} CEPs únicos dos sinistros...")
    for _cep in _ceps_sin:
        if _cep in cep_cluster_map and cep_cluster_map[_cep].get('lat'):
            _geo_cache[_cep] = dict(cep_cluster_map[_cep])
            continue
        _lat = _lon = None
        try:
            # Passo 1: pega endereço via ViaCEP
            _vreq = _ur.Request(f'https://viacep.com.br/ws/{_cep}/json/', headers={'User-Agent': _NOMINATIM_UA})
            with _ur.urlopen(_vreq, timeout=5) as _r:
                _via = _json.loads(_r.read())
            _time.sleep(1.1)
            # Passo 2: geocodifica via Nominatim com fallbacks progressivos
            _logr = _via.get('logradouro','')
            _bairro = _via.get('bairro','')
            _cidade = _via.get('localidade','')
            _uf = _via.get('uf','')
            for _parts in [
                [_logr, _bairro, _cidade, _uf, 'Brasil'],
                [_bairro, _cidade, _uf, 'Brasil'],
                [_cidade, _uf, 'Brasil'],
            ]:
                _addr = ', '.join(filter(None, _parts))
                if not _addr.replace(',','').strip():
                    continue
                _nreq = _ur.Request('https://nominatim.openstreetmap.org/search?q=' + _up.quote(_addr) + '&countrycodes=BR&format=json&limit=1', headers={'User-Agent': _NOMINATIM_UA})
                with _ur.urlopen(_nreq, timeout=6) as _r:
                    _res = _json.loads(_r.read())
                _time.sleep(1.1)
                if _res:
                    _lat = float(_res[0]['lat'])
                    _lon = float(_res[0]['lon'])
                    break
        except Exception as _e:
            print(f"    geocoding {_cep}: {_e}")
        _geo_cache[_cep] = {'cluster': cep_cluster_map.get(_cep, {}).get('cluster', ''), 'lat': _lat, 'lon': _lon}
    # Atualiza cep_cluster_map com coordenadas reais
    cep_cluster_map.update(_geo_cache)
    dados['cep_cluster_map'] = cep_cluster_map
    _com_coords = sum(1 for v in _geo_cache.values() if v.get('lat'))
    print(f"  Geocodificação: {_com_coords}/{len(_ceps_sin)} CEPs com coordenadas")

    # --- Investigação LP: cache + 3 queries BQ em paralelo ---
    import json as _json_bq, time as _tbq
    _BQ_CACHE_PATH = os.path.join(os.path.dirname(__file__), 'bq_investigacao_cache.json')
    _BQ_CACHE_MAX  = 4 * 3600  # 4 horas

    def _bq_cache_load():
        if not os.path.exists(_BQ_CACHE_PATH): return None
        if _tbq.time() - os.path.getmtime(_BQ_CACHE_PATH) > _BQ_CACHE_MAX: return None
        try:
            with open(_BQ_CACHE_PATH, encoding='utf-8') as _f: return _json_bq.load(_f)
        except: return None

    _bq_cache = _bq_cache_load()
    if _bq_cache:
        print(f"  Cache BQ válido (< 4h) — carregando do disco...")
        dados['saidas']      = _bq_cache.get('saidas', [])
        dados['devolucoes']  = _bq_cache.get('devolucoes', [])
        dados['sellers_ene'] = _bq_cache.get('sellers_ene', [])
        dados['ene_service'] = _bq_cache.get('ene_service', [])
        dados['damaged_ene'] = _bq_cache.get('damaged_ene', {})
        print(f"  Cache: {len(dados['saidas'])} saídas | {len(dados['devolucoes'])} devoluções | {len(dados['sellers_ene'])} sellers ENE | {len(dados['ene_service'])} ENE svc | {dados['damaged_ene'].get('total',0)} damaged ENE")
    else:
        print("  Buscando BQ: 4 queries disparadas em paralelo...")
        from google.cloud import bigquery as _bqm
        _bqc = _bqm.Client(project='meli-bi-data')

        # ── Definição das 4 queries ──────────────────────────────
        _q_saidas = """
            WITH saidas AS (
              SELECT
                SHP_SHIPMENT_ID,
                MAX(SHP_SHIPMENT_DELIVERY_ATTEMPTS) AS tentativas,
                MIN(SHP_LG_INIT_DT_TZ) AS data_primeira_saida,
                MAX(SHP_LG_INIT_DT_TZ) AS data_ultima_acao,
                ANY_VALUE(SHP_COMPANY_NAME) AS transportadora,
                CAST(ANY_VALUE(SHP_LG_DRIVER_ID) AS STRING) AS motorista_id,
                CAST(ANY_VALUE(SHP_LG_ROUTE_ID) AS STRING) AS rota_id,
                ANY_VALUE(DELIVERY_SUCCESS_LEVEL_1) AS status_final,
                ANY_VALUE(DELIVERY_SUCCESS_LEVEL_2) AS sub_status_final,
                MAX(CASE WHEN SHP_FAILED_ATTEMPT_FLAG THEN 1 ELSE 0 END) AS teve_insucesso,
                ANY_VALUE(SHP_LOGISTIC_TYPE) AS tipo_logistica
              FROM `meli-bi-data.WHOWNER.BT_SHP_SHIPMENTS_LAST_MILE`
              WHERE SHP_LG_FACILITY_ID = 'SSP30'
                AND SHP_LG_INIT_DT_TZ BETWEEN '2026-01-01' AND CURRENT_DATE()
              GROUP BY SHP_SHIPMENT_ID
              HAVING MAX(SHP_SHIPMENT_DELIVERY_ATTEMPTS) > 1
            )
            SELECT *,
              CASE
                WHEN status_final = 'NOT DELIVERED' AND teve_insucesso = 1 THEN 'CRITICO'
                WHEN tentativas >= 3 THEN 'ALTO'
                WHEN teve_insucesso = 1 THEN 'MEDIO'
                ELSE 'BAIXO'
              END AS nivel_risco
            FROM saidas
            ORDER BY tentativas DESC, nivel_risco
            LIMIT 2000
        """
        _q_devos = """
            SELECT
              DATEPARAMETER AS data,
              CAST(SHP_SHIPMENT_BPP AS STRING) AS shipment_id,
              SHP_LG_FACILITY_ID AS facility,
              SHP_NODE_ID AS node_id,
              FLUJO AS flujo,
              SHP_ROUTE_TYPE AS tipo_rota,
              LP_TRACKING_CODE AS tracking_code,
              SHP_NODE_CAUSE AS causa_node,
              SHP_NODE_CAUSE_L2 AS causa_l2,
              CLASSIFICATION_LM AS classificacao,
              BPP_LP_CLASSIFICATION AS classificacao_lp,
              SHP_BKO_STATUS AS status_bko,
              SHP_BKO_SUBSTATUS AS substatus_bko,
              FORMAT_DATETIME('%Y-%m-%d', RTN_DATE) AS data_rtn,
              RTN_STATUS AS status_rtn,
              CAST(RTS_DATE AS STRING) AS data_rts,
              RTS_STATUS AS status_rts,
              FLAG_RTS_ROUTE AS flag_rts,
              FLAG_DEVO_WH AS flag_devo_wh,
              CAST(SELLER.SHP_SELLER_ID AS STRING) AS seller_id,
              SELLER.SHP_SELLER_NICKNAME AS seller_nome,
              SELLER.SHP_SELLER_STATE AS seller_estado,
              CAST(BUYER.SHP_BUYER_ID AS STRING) AS buyer_id,
              BUYER.SHP_BUYER_STATE AS buyer_estado,
              DOM_DOMAIN_ID AS dominio,
              VERTICAL AS vertical,
              SHP_LOGISTIC_CENTER_ID AS origem_cross
            FROM `meli-bi-data.WHOWNER.BT_LP_NODES`
            WHERE SHP_LG_FACILITY_ID = 'SSP30'
              AND DATEPARAMETER BETWEEN '2026-01-01' AND CURRENT_DATE()
              AND (FLUJO = 'Devolutions' OR FLAG_DEVO_WH = '1' OR FLAG_RTS_ROUTE = 'Sí')
            ORDER BY DATEPARAMETER DESC
            LIMIT 2000
        """
        _q_ene = f"""
            SELECT
              CUS_NICKNAME_SEL                                                AS seller_nome,
              ''                                                              AS seller_id,
              COUNT(DISTINCT SHIPMENT_ID)                                     AS qtd_ene,
              ROUND(SUM(BPP_CASHOUT_USD), 2)                                  AS total_cashout,
              STRING_AGG(DISTINCT CLASSIFICATION_LM
                         ORDER BY CLASSIFICATION_LM LIMIT 4)                 AS causas,
              FORMAT_DATE('%d/%m/%Y', MIN(date_bpp))                          AS primeira,
              FORMAT_DATE('%d/%m/%Y', MAX(date_bpp))                          AS ultima,
              STRING_AGG(DISTINCT FORMAT_DATE('%Y-%m', date_bpp)
                         ORDER BY FORMAT_DATE('%Y-%m', date_bpp))             AS meses,
              STRING_AGG(DISTINCT CAST(SHIPMENT_ID AS STRING) LIMIT 50)       AS shp_ids
            FROM `meli-bi-data.WHOWNER.DM_LP_MELI_OPTIMIZADO`
            WHERE SHP_LG_FACILITY_NAME = '{FACILITY_NAME}'
              AND CAST(FLAG_ENE AS STRING) = '1'
              AND date_bpp >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH)
              AND CUS_NICKNAME_SEL IS NOT NULL
            GROUP BY 1
            ORDER BY total_cashout DESC, qtd_ene DESC
            LIMIT 300
        """
        _q_ene_svc = f"""
            SELECT
              IFNULL(ROUTE.SHP_CARRIER_NAME, 'Sem Transportadora')          AS carrier,
              COUNT(DISTINCT SHP_SHIPMENT_BPP)                               AS total,
              COUNTIF(SHP_BKO_STATUS = 'not_delivered')                      AS nao_entregue,
              COUNTIF(SHP_BKO_SUBSTATUS = 'lost')                            AS perdido,
              COUNTIF(SHP_BKO_SUBSTATUS = 'damaged')                         AS avariado,
              COUNTIF(HAS_ROUTE = 'No'
                AND (SHP_NODE_CAUSE LIKE '%RETURN%'
                     OR SHP_BKO_STATUS = 'not_delivered'))                   AS sem_rota,
              COUNTIF(
                ROUTE.SHP_LG_ROUTE_INIT_DATE IS NOT NULL
                AND ROUTE.SHP_LG_ROUTE_END_DATE IS NOT NULL
                AND DATETIME_DIFF(
                      ROUTE.SHP_LG_ROUTE_END_DATE,
                      ROUTE.SHP_LG_ROUTE_INIT_DATE, MINUTE) <= 120)         AS rota_curta_2h,
              STRING_AGG(DISTINCT CAST(SHP_SHIPMENT_BPP AS STRING) LIMIT 30) AS shp_ids
            FROM `meli-bi-data.WHOWNER.BT_LP_NODES`
            WHERE SHP_LG_FACILITY_ID = 'SSP30'
              AND DATEPARAMETER >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH)
              AND FLUJO LIKE '%EnE%'
            GROUP BY 1
            ORDER BY nao_entregue DESC, total DESC
            LIMIT 50
        """

        # ── Dispara as 4 em paralelo (sem esperar) ───────────────
        _job_saidas    = _bqc.query(_q_saidas)
        _job_devos     = _bqc.query(_q_devos)
        _job_ene       = _bqc.query(_q_ene)
        _job_ene_svc   = _bqc.query(_q_ene_svc)
        print("  4 queries disparadas em paralelo no BQ...")

        # ── Coleta resultados (agora aguarda cada uma) ───────────
        _saidas_rows = []
        try:
            for _r in _job_saidas.result():
                _saidas_rows.append({
                    'id':             str(_r.SHP_SHIPMENT_ID),
                    'tentativas':     int(_r.tentativas or 0),
                    'data_ini':       str(_r.data_primeira_saida or ''),
                    'data_fim':       str(_r.data_ultima_acao or ''),
                    'transportadora': str(_r.transportadora or ''),
                    'motorista':      str(_r.motorista_id or ''),
                    'rota':           str(_r.rota_id or ''),
                    'status':         str(_r.status_final or ''),
                    'sub_status':     str(_r.sub_status_final or ''),
                    'insucesso':      bool(_r.teve_insucesso),
                    'tipo':           str(_r.tipo_logistica or ''),
                    'risco':          str(_r.nivel_risco or ''),
                })
            print(f"  Saídas: {len(_saidas_rows)} shipments")
        except Exception as _e:
            print(f"  Aviso saídas: {_e}")
        dados['saidas'] = _saidas_rows

        _devos_rows = []
        try:
            for _r in _job_devos.result():
                _devos_rows.append({
                    'data':       str(_r.data or ''),
                    'id':         str(_r.shipment_id or ''),
                    'facility':   str(_r.facility or ''),
                    'node':       str(_r.node_id or ''),
                    'flujo':      str(_r.flujo or ''),
                    'tipo_rota':  str(_r.tipo_rota or ''),
                    'tracking':   str(_r.tracking_code or ''),
                    'causa':      str(_r.causa_node or ''),
                    'causa_l2':   str(_r.causa_l2 or ''),
                    'class':      str(_r.classificacao or ''),
                    'class_lp':   str(_r.classificacao_lp or ''),
                    'status_bko': str(_r.status_bko or ''),
                    'sub_bko':    str(_r.substatus_bko or ''),
                    'data_rtn':   str(_r.data_rtn or ''),
                    'status_rtn': str(_r.status_rtn or ''),
                    'data_rts':   str(_r.data_rts or ''),
                    'status_rts': str(_r.status_rts or ''),
                    'flag_rts':   str(_r.flag_rts or ''),
                    'flag_devo':  str(_r.flag_devo_wh or ''),
                    'seller_id':  str(_r.seller_id or ''),
                    'seller':     str(_r.seller_nome or ''),
                    'seller_uf':  str(_r.seller_estado or ''),
                    'buyer_id':   str(_r.buyer_id or ''),
                    'buyer_uf':   str(_r.buyer_estado or ''),
                    'dominio':      str(_r.dominio or ''),
                    'vertical':     str(_r.vertical or ''),
                    'origem_cross': str(_r.origem_cross or ''),
                })
            print(f"  Devoluções: {len(_devos_rows)} casos")
        except Exception as _e:
            print(f"  Aviso devoluções: {_e}")
        dados['devolucoes'] = _devos_rows

        _ene_rows = []
        try:
            for _r in _job_ene.result():
                _ene_rows.append({
                    'seller_id':   str(_r.seller_id or ''),
                    'seller_nome': str(_r.seller_nome or ''),
                    'qtd':         int(_r.qtd_ene or 0),
                    'cashout':     float(_r.total_cashout or 0),
                    'causas':      str(_r.causas or ''),
                    'primeira':    str(_r.primeira or ''),
                    'ultima':      str(_r.ultima or ''),
                    'meses':       str(_r.meses or ''),
                    'shp_ids':     str(_r.shp_ids or ''),
                })
            print(f"  Sellers ENE: {len(_ene_rows)} sellers")
        except Exception as _e:
            print(f"  Aviso sellers ENE: {_e}")
        dados['sellers_ene'] = _ene_rows

        _ene_svc_rows = []
        try:
            for _r in _job_ene_svc.result():
                _ene_svc_rows.append({
                    'carrier':       str(_r.carrier or ''),
                    'total':         int(_r.total or 0),
                    'nao_entregue':  int(_r.nao_entregue or 0),
                    'perdido':       int(_r.perdido or 0),
                    'avariado':      int(_r.avariado or 0),
                    'sem_rota':      int(_r.sem_rota or 0),
                    'rota_curta_2h': int(_r.rota_curta_2h or 0),
                    'shp_ids':       str(_r.shp_ids or ''),
                })
            print(f"  ENE Service: {len(_ene_svc_rows)} carriers")
        except Exception as _e:
            print(f"  Aviso ENE Service: {_e}")
        dados['ene_service'] = _ene_svc_rows

        # ── Dispara Damaged ENE após coletar as 4 principais (evita quota BQ) ──
        import pandas as _pd_dene
        _job_dene_cas  = _bqc.query(QUERY_DAMAGED_ENE_CASOS)
        _job_dene_cau  = _bqc.query(QUERY_DAMAGED_ENE_CAUSAS)
        print("  2 queries Damaged ENE disparadas...")
        _df_dene_cas = _pd_dene.DataFrame()
        _df_dene_cau = _pd_dene.DataFrame()
        try:
            _df_dene_cas = _job_dene_cas.result().to_dataframe()
            print(f"  Damaged ENE casos: {len(_df_dene_cas)} casos")
        except Exception as _e:
            print(f"  Aviso Damaged ENE casos: {_e}")
        try:
            _df_dene_cau = _job_dene_cau.result().to_dataframe()
            print(f"  Damaged ENE causas: {len(_df_dene_cau)} causas")
        except Exception as _e:
            print(f"  Aviso Damaged ENE causas: {_e}")
        dados['damaged_ene'] = processar_damaged_ene(_df_dene_cas, _df_dene_cau)

        # ── Dispara FRAUD ENE após Damaged ENE (evita quota BQ) ──
        import pandas as _pd_frene
        _job_frene = _bqc.query(QUERY_FRAUD_ENE_CASOS)
        print("  1 query Fraud ENE disparada...")
        _df_frene = _pd_frene.DataFrame()
        try:
            _df_frene = _job_frene.result().to_dataframe()
            print(f"  Fraud ENE casos: {len(_df_frene)} casos")
        except Exception as _e:
            print(f"  Aviso Fraud ENE casos: {_e}")
        dados['fraud_ene'] = [
            {'shp_id': str(r.shp_id), 'seller_nome': str(r.seller_nome or ''),
             'bpp': float(r.bpp or 0), 'data': str(r.data or ''), 'mes': str(r.mes or ''),
             'tipo_fraude': str(r.tipo_fraude or ''), 'item_title': str(r.item_title or '')}
            for _, r in _df_frene.iterrows()
        ] if not _df_frene.empty else []

        # ── Sincroniza planilha ENE com os dados frescos do BQ ────
        print("\nAtualizando planilha ENE SSP30...")
        atualizar_planilha_ene(gs, dados['damaged_ene'], dados['fraud_ene'])

        # ── Salva cache para próximos builds (válido 4h) ─────────
        try:
            with open(_BQ_CACHE_PATH, 'w', encoding='utf-8') as _cf:
                _json_bq.dump({
                    'saidas': dados['saidas'], 'devolucoes': dados['devolucoes'],
                    'sellers_ene': dados['sellers_ene'], 'ene_service': dados['ene_service'],
                    'damaged_ene': dados['damaged_ene'], 'fraud_ene': dados['fraud_ene'],
                }, _cf, ensure_ascii=False, default=str)
            print("  Cache BQ salvo (válido por 4h).")
        except Exception as _ec:
            print(f"  Aviso cache: {_ec}")

        dados.setdefault('saidas', [])
        dados.setdefault('devolucoes', [])
        dados.setdefault('sellers_ene', [])
        dados.setdefault('ene_service', [])
        dados.setdefault('damaged_ene', {})
        dados.setdefault('fraud_ene', [])

    MONTHS_PT = {1:'Jan',2:'Fev',3:'Mar',4:'Abr',5:'Mai',6:'Jun',
                 7:'Jul',8:'Ago',9:'Set',10:'Out',11:'Nov',12:'Dez'}
    monthly = []
    for ym in sorted(dados.get('monthly_agg', {})):
        agg = dados['monthly_agg'][ym]
        mo, yr = int(ym[5:7]), int(ym[:4])
        monthly.append({
            'key':     ym,
            'label':   f"{MONTHS_PT[mo]}/{yr}",
            'fraudes': agg['fraudes'],
            'damaged': agg['damaged'],
            'bpp':     round(agg['bpp'], 2),
            'total':   agg['total'],
        })
    dados['monthly'] = monthly

    # Exporta acumulo_bloqueio para que gerar_dashboard.py use a mesma fonte de dados
    _acumulo_export = os.path.join(os.path.dirname(__file__), '_acumulo_bloqueio.json')
    _acumulo_export_data = {
        str(pk): [
            {k: v for k, v in c.items() if k != 'shps'}
            for c in pv
        ]
        for pk, pv in dados.get('acumulo_por_periodo', {}).items()
    }
    # mantém compatibilidade: chave '90' é o padrão
    with open(_acumulo_export, 'w', encoding='utf-8') as _f:
        json.dump(_acumulo_export_data, _f, ensure_ascii=False, indent=2)

    _dene_default = {'casos':[],'sellers':[],'meses':[],'causas':[],'wordcloud':[],'total':0,'total_bpp':0.0,'total_sellers':0}
    dados.setdefault('damaged_ene', _dene_default)
    if not isinstance(dados['damaged_ene'], dict):
        dados['damaged_ene'] = _dene_default
    dados.setdefault('fraud_ene', [])
    if not isinstance(dados['fraud_ene'], list):
        dados['fraud_ene'] = []

    print("Gerando dashboard...")
    html = gerar_html(dados)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Salvo em: {OUTPUT}")

    html_sin = gerar_sinistros_html(dados)
    with open(SINISTROS_OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html_sin)
    print(f"Sinistros salvo em: {SINISTROS_OUTPUT}")

    # Gera ofensores.html — visao restrita somente Ofensores (para gestora)
    _RESTRICT_CSS = """<style id="restrict-ofensores">
nav.sidebar { display:none !important; }
.mod-nav     { display:none !important; }
#srv-status  { display:none !important; }
</style>
<script>
window.addEventListener('load', function() {
  var el = document.querySelector('.sb-item[data-tab="ofensores"]');
  if (el) { showTab('ofensores', el); }
  var orig = window.showTab;
  window.showTab = function(name, el) {
    if (name !== 'ofensores') return;
    orig(name, el);
  };
}, { once: true });
</script>"""
    html_ofensores = html.replace('</head>', _RESTRICT_CSS + '\n</head>', 1)
    ofensores_path = os.path.join(os.path.dirname(__file__), 'ofensores.html')
    with open(ofensores_path, 'w', encoding='utf-8') as f:
        f.write(html_ofensores)
    print(f"Ofensores salvo em: {ofensores_path}")

    if not os.environ.get('CI'):
        webbrowser.open(f'file:///{OUTPUT.replace(chr(92),"/")}')
        print("Abrindo no navegador!")

    print(f"\n{'='*55}")
    print(f"Drivers criticos : {dados['criticos']}")
    print(f"Total fraudes    : {dados['total_fraudes']}")
    print(f"Total damaged    : {dados['total_damaged']}")
    print(f"BPP Total        : ${dados['total_bpp']:,.2f}")
    print(f"Cruzados F+D     : {len(dados['cruzados'])}")
    print("="*55)
