WITH dit_dedup AS (
    SELECT
        SHP_SHIPMENT_ID,
        SHP_DESTINATION_FACILITY_ID    AS place_id,
        LM_DESTINATION_FACILITY_TYPE   AS tipo,
        LT_DELAY_CAUSE_L2              AS causa,
        SHP_LG_SUB_STATUS              AS sub_status,
        DATE_DIFF(CURRENT_DATE(), SHP_DATE_HANDLING_ID, DAY) AS dias_parado
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (PARTITION BY SHP_SHIPMENT_ID ORDER BY AUD_UPD_DTTM DESC) AS rn
        FROM `meli-bi-data.WHOWNER.BT_SHP_TRACKER_DELAY_CAUSE_DIT`
        WHERE SHP_SITE_ID = 'MLB'
          AND SHP_DATE_HANDLING_ID >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
          AND SHP_STATUS_ID NOT IN ('delivered','cancelled','not_delivered')
          AND LM_DESTINATION_FACILITY_TYPE IN ('NEX','XPT','DC')
          AND SHP_DESTINATION_FACILITY_ID IS NOT NULL
    )
    WHERE rn = 1
)
SELECT COUNT(*) as teste FROM dit_dedup LIMIT 1
