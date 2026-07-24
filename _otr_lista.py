from google.cloud import bigquery
from google.auth import default
from datetime import date

creds, _ = default()
client = bigquery.Client(project='meli-bi-data', credentials=creds)

q = """
WITH dit_dedup AS (
    SELECT
        SHP_SHIPMENT_ID,
        SHP_DESTINATION_FACILITY_ID    AS place_id,
        LM_DESTINATION_FACILITY_TYPE   AS tipo,
        SHP_LG_SUB_STATUS              AS sub_status,
        DATE_DIFF(CURRENT_DATE(), SHP_DATE_HANDLING_ID, DAY) AS dias_parado
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY SHP_SHIPMENT_ID ORDER BY AUD_UPD_DTTM DESC
            ) AS rn
        FROM `meli-bi-data.WHOWNER.BT_SHP_TRACKER_DELAY_CAUSE_DIT`
        WHERE SHP_SITE_ID = 'MLB'
          AND SHP_DATE_HANDLING_ID >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
          AND SHP_STATUS_ID NOT IN ('delivered','cancelled','not_delivered')
          AND LM_DESTINATION_FACILITY_TYPE IN ('NEX','XPT','DC')
          AND SHP_DESTINATION_FACILITY_ID IS NOT NULL
    ) sub
    WHERE rn = 1
),
missing_ids AS (
    SELECT DISTINCT SHP_SHIPMENT_ID
    FROM `meli-bi-data.WHOWNER.LK_SHP_MISSING_MANAGEMENT_PACKAGES`
    WHERE SIT_SITE_ID = 'MLB'
),
ssp30_places AS (
    SELECT DISTINCT SHP_DESTINATION_ID_LM AS place_id
    FROM `meli-bi-data.WHOWNER.LK_SHP_MISSING_MANAGEMENT_PACKAGES`
    WHERE SHP_LG_FACILITY_ID = 'SSP30'
      AND SHP_TRAMO IN ('NEX','DC')
),
dit_agg AS (
    SELECT
        d.place_id,
        d.tipo,
        COUNTIF(m.SHP_SHIPMENT_ID IS NULL)                                        AS blind_spot,
        ROUND(AVG(CASE WHEN m.SHP_SHIPMENT_ID IS NULL THEN d.dias_parado END), 1) AS avg_dias,
        COUNTIF(d.sub_status = 'delivered_place' AND m.SHP_SHIPMENT_ID IS NULL)   AS stuck_place
    FROM dit_dedup d
    LEFT JOIN missing_ids m USING (SHP_SHIPMENT_ID)
    INNER JOIN ssp30_places sp ON sp.place_id = d.place_id
    GROUP BY 1, 2
)
SELECT
    place_id,
    tipo,
    blind_spot,
    avg_dias,
    stuck_place,
    CASE
        WHEN (blind_spot >= 50 AND avg_dias >= 7) OR stuck_place >= 20 THEN 'IMEDIATO'
        WHEN blind_spot >= 20 AND avg_dias >= 5                        THEN 'MONITORAMENTO'
        ELSE 'OBSERVAR'
    END AS nivel_otr
FROM dit_agg
WHERE blind_spot >= 10 OR stuck_place >= 10
ORDER BY
    CASE WHEN (blind_spot >= 50 AND avg_dias >= 7) OR stuck_place >= 20 THEN 0
         WHEN blind_spot >= 20 AND avg_dias >= 5                        THEN 1
         ELSE 2 END,
    blind_spot DESC
"""

job = client.query(q)
rows = list(job.result())
hoje = date.today().strftime('%d/%m/%Y')

imediatos = [r for r in rows if r['nivel_otr'] == 'IMEDIATO']
monitora  = [r for r in rows if r['nivel_otr'] == 'MONITORAMENTO']
observar  = [r for r in rows if r['nivel_otr'] == 'OBSERVAR']

print(f'LISTA OTR — Places SSP30 — {hoje}')
print('='*70)

print(f'\n[IMEDIATO] {len(imediatos)} places — acionar agora')
print(f'{"PLACE":<15} {"TIPO":<6} {"DIT s/flag":>10} {"Avg dias":>9} {"Preso":>7}  MOTIVO')
print('-'*70)
for r in imediatos:
    m = []
    if r['blind_spot'] >= 50 and r['avg_dias'] and r['avg_dias'] >= 7:
        m.append('volume+tempo')
    if r['stuck_place'] >= 20:
        m.append('preso no place')
    avg = f"{r['avg_dias']}d" if r['avg_dias'] else '-'
    print(f"{r['place_id']:<15} {r['tipo']:<6} {r['blind_spot']:>10} {avg:>9} {r['stuck_place']:>7}  {' + '.join(m)}")

print(f'\n[MONITORAMENTO] {len(monitora)} places — acompanhar')
print(f'{"PLACE":<15} {"TIPO":<6} {"DIT s/flag":>10} {"Avg dias":>9} {"Preso":>7}')
print('-'*55)
for r in monitora:
    avg = f"{r['avg_dias']}d" if r['avg_dias'] else '-'
    print(f"{r['place_id']:<15} {r['tipo']:<6} {r['blind_spot']:>10} {avg:>9} {r['stuck_place']:>7}")

if observar:
    print(f'\nOBSERVAR ({len(observar)} places)')
    for r in observar:
        avg = f"{r['avg_dias']}d" if r['avg_dias'] else '-'
        print(f"  {r['place_id']} ({r['tipo']}) — {r['blind_spot']} pcts, {avg}")
