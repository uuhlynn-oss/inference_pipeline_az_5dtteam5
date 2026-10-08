/* =========================================================
   BUS / SUBWAY -- 5분 집계 후 observation_5m 테이블 저장
   ========================================================= */
SELECT
    source_id,
    location_id,
    data_type,
    DATEADD(minute,-5,System.Timestamp()) AS window_start,
    System.Timestamp() AS window_end,
    CAST(COUNT(*) AS bigint) AS sample_count,
    CAST(
    	SUM(TRY_CAST(boarding AS bigint) 
    	+ TRY_CAST(alighting AS bigint))AS bigint) AS traffic_sum_5m,
    CAST(SUM(TRY_CAST(boarding AS bigint))AS bigint AS boarding_sum_5m,
    CAST(SUM(TRY_CAST(alighting AS bigint))AS bigint) AS alighting_sum_5m
INTO [observation-5m-postgres]
FROM [eh_raw]
TIMESTAMP BY event_time
WHERE
    data_type IN ('BUS', 'SUBWAY')
    AND TRY_CAST(boarding AS bigint) IS NOT NULL
    AND TRY_CAST(alighting AS bigint) IS NOT NULL
GROUP BY
    source_id,
    location_id,
    data_type,
    TumblingWindow(minute, 5);


/* =========================================================
   WEATHER
   weather_history 테이블 저장
   ========================================================= */

SELECT
    source_id,
    location_id,
    event_time,
    TRY_CAST(temperature AS float) AS temperature,
    CASE
        WHEN rain = true
        THEN CAST(1 AS bigint)
        ELSE CAST(0 AS bigint)
    END AS rain,
    CASE
        WHEN snow = true
        THEN CAST(1 AS bigint)
        ELSE CAST(0 AS bigint)
    END AS snow
INTO [weather_history_postgres]
FROM [eh_raw]
TIMESTAMP BY event_time
WHERE
    data_type = 'WEATHER'
    AND TRY_CAST(temperature AS float) IS NOT NULL;


/* =========================================================
   5분 집계 결과
   추론 Function 입력
   ========================================================= */

SELECT
    source_id,
    location_id,
    data_type,
    DATEADD(minute, -5, System.Timestamp()) AS window_start,
    System.Timestamp() AS window_end,
    CAST(COUNT(*) AS bigint) AS sample_count,
    CAST(
        SUM(TRY_CAST(boarding AS bigint)
        + TRY_CAST(alighting AS bigint)) AS bigint
    ) AS traffic_sum_5m,
    CAST(
        SUM(TRY_CAST(boarding AS bigint)) AS bigint
    ) AS boarding_sum_5m,
    CAST(
        SUM(TRY_CAST(alighting AS bigint))AS bigint
    ) AS alighting_sum_5m
INTO [predict-traffic]
FROM [eh_raw]
TIMESTAMP BY event_time
WHERE
    data_type IN ('BUS', 'SUBWAY')
    AND TRY_CAST(boarding AS bigint) IS NOT NULL
    AND TRY_CAST(alighting AS bigint) IS NOT NULL

GROUP BY
    source_id,
    location_id,
    data_type,
    TumblingWindow(minute, 5);


/* =========================================================
   SUBWAY
   승하차 임계치 초과 시 알람
   ========================================================= */

SELECT
    source_id,
    location_id,
    data_type,
    System.Timestamp() AS window_end,
    CAST(
        SUM(TRY_CAST(boarding AS bigint)
            + TRY_CAST(alighting AS bigint)
        )AS bigint
    ) AS traffic_sum_5m
INTO [subway-alert]
FROM [eh_raw]
TIMESTAMP BY event_time
WHERE
    data_type = 'SUBWAY'
    AND TRY_CAST(boarding AS bigint) IS NOT NULL
    AND TRY_CAST(alighting AS bigint) IS NOT NULL
GROUP BY
    source_id,
    location_id,
    data_type,
    TumblingWindow(minute, 5)
HAVING
    SUM(TRY_CAST(boarding AS bigint)
        + TRY_CAST(alighting AS bigint)
    ) >= 500;