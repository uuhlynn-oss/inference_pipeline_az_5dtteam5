import azure.functions as func

import json
import logging
import os
import requests

import psycopg2
import psycopg2.extras

from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
# from decimal import Decimal # 코드 내에서 실제 사용되지 않으므로 제거 가능


app = func.FunctionApp(
    http_auth_level=func.AuthLevel.FUNCTION
)


# =========================================================
# PostgreSQL Connection
# =========================================================

def get_db_connection():
    """
    환경 변수를 기반으로 PostgreSQL DB 연결 객체를 반환합니다.
    """
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", "5432"),
        sslmode=os.getenv("POSTGRES_SSLMODE", "require")
    )


# =========================================================
# [추가/개선] Batch DB Lookup Functions
# 룩업 딕셔너리를 구성하여 루프 내 DB 조회를 제거합니다.
# =========================================================

def fetch_lookup_data_batch(conn, location_ids, target_dates, reference_time):
    """
    여러 location_id 및 date에 대한 날씨, 이벤트, 휴일 정보를 단 3번의 쿼리로 벌크 조회하여
    룩업 딕셔너리(Map)를 구성합니다.
    - 날씨/이벤트: location_id를 키로 사용
    - 휴일: date 객체를 키로 사용
    """
    
    if not location_ids and not target_dates:
        return {}, {}, {}

    # try...finally 블록을 사용하여 커서가 항상 닫히도록 보장
    # 딕셔너리 형태의 결과 반환을 위해 DictCursor 사용
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    weather_lookup = {}
    event_lookup = {}
    holiday_lookup = {}

    try:
        # --- (1) 날씨 벌크 조회 (Batch Weather) ---
        if location_ids:
            # ROW_NUMBER() OVER(PARTITION BY ...)를 사용하여 
            # 조회 대상 location_id 각각에 대한 최신 1건의 데이터를 효율적으로 가져옵니다.
            sql_weather = """
                SELECT location_id, temperature, rain, snow
                FROM (
                    SELECT location_id, temperature, rain, snow,
                           ROW_NUMBER() OVER(PARTITION BY location_id ORDER BY event_time DESC) as rn
                    FROM transit_mvp.weather_history
                    WHERE location_id IN %s
                ) as ranked_weather
                WHERE rn = 1;
            """
            # psycopg2는 튜플을 WHERE IN 절의 인자로 받습니다.
            cur.execute(sql_weather, (tuple(location_ids),))
            rows = cur.fetchall()
            for row in rows:
                # 결과 row(DictRow)를 일반 딕셔너리로 변환하여 키로 저장
                weather_lookup[row["location_id"]] = dict(row)
        
        # --- (2) 이벤트 벌크 조회 (Batch Event) ---
        if location_ids:
            # 여러 location_id에 대한 이벤트를 COUNT, MAX(event_scale)로 집계
            sql_event = """
                SELECT 
                    location_id,
                    COUNT(*) AS event_count,
                    COALESCE(MAX(event_scale), 0) AS event_scale
                FROM transit_mvp.event_master
                WHERE location_id IN %s
                  AND status = 'SCHEDULED'
                  AND start_at <= %s
                  AND end_at >= %s
                GROUP BY location_id;
            """
            cur.execute(sql_event, (tuple(location_ids), reference_time, reference_time))
            rows = cur.fetchall()
            for row in rows:
                event_lookup[row["location_id"]] = {
                    "event": int(row["event_count"] > 0),
                    "event_scale": int(row["event_scale"])
                }

        # --- (3) 휴일 벌크 조회 (Batch Holiday) ---
        if target_dates:
            # 날짜 리스트에 대한 휴일 여부 조회
            sql_holiday = """
                SELECT holiday_date, is_holiday
                FROM transit_mvp.holiday_master
                WHERE holiday_date IN %s;
            """
            cur.execute(sql_holiday, (tuple(target_dates),))
            rows = cur.fetchall()
            for row in rows:
                # 딕셔너리 키로 date 객체 자체를 사용합니다.
                holiday_lookup[row["holiday_date"]] = row["is_holiday"]

    except Exception:
        # 상위 try...except 블록에서 처리하도록 예외를 던집니다.
        raise
    finally:
        # 예외 발생 여부와 상관없이 커서를 닫습니다.
        cur.close()

    logging.info("[DB] 벌크 조회 완료 (Weather: %s건, Event: %s건, Holiday: %s건)",
                 len(weather_lookup), len(event_lookup), len(holiday_lookup))

    return weather_lookup, event_lookup, holiday_lookup


# =========================================================
# Feature Engineering (기존 동일)
# =========================================================

def make_features(
    traffic_sum_5m,
    window_start,
    window_end,
    weather,
    event_info,
    holiday
):
    """
    수집된 데이터를 기반으로 모델 입력 피처를 가공합니다.
    """

    feature_hour = window_end.hour
    feature_weekday = window_start.isoweekday()

    feature_holiday = (
        feature_weekday >= 6
        or holiday
    )

    return {
        "hour": feature_hour,
        "weekday": feature_weekday,
        "holiday": int(feature_holiday),
        "traffic_volume": traffic_sum_5m,
        "rain": weather["rain"],
        "snow": weather["snow"],
        "temperature": float(weather["temperature"]),
        "event": int(event_info["event"]),
        "event_scale": int(event_info["event_scale"])
    }


# =========================================================
# AML Endpoint Configuration (기존 동일)
# =========================================================

AML_ENDPOINTS = {
    "BUS": {
        0: {
            "url_env": "AML_BUS_CURRENT_URL",
            "key_env": "AML_BUS_CURRENT_KEY",
            "model_version": "bus-congestion-realtime"
        },
        60: {
            "url_env": "AML_BUS_1H_URL",
            "key_env": "AML_BUS_1H_KEY",
            "model_version": "bus-congestion-1h"
        },
        120: {
            "url_env": "AML_BUS_2H_URL",
            "key_env": "AML_BUS_2H_KEY",
            "model_version": "bus-congestion-2h"
        }
    },

    "SUBWAY": {
        0: {
            "url_env": "AML_SUBWAY_CURRENT_URL",
            "key_env": "AML_SUBWAY_CURRENT_KEY",
            "model_version": "subway-congestion-realtime"
        },
        60: {
            "url_env": "AML_SUBWAY_1H_URL",
            "key_env": "AML_SUBWAY_1H_KEY",
            "model_version": "subway-congestion-1h"
        },
        120: {
            "url_env": "AML_SUBWAY_2H_URL",
            "key_env": "AML_SUBWAY_2H_KEY",
            "model_version": "subway-congestion-2h"
        }
    }
}


# =========================================================
# AML Payload (기존 동일)
# =========================================================

def build_aml_payload(features):
    """
    AML Designer 엔드포인트 형식에 맞게 페이로드를 구성합니다.
    """
    return {
        "Inputs": {
            "input1": [
                {
                    "hour": features["hour"],
                    "weekday": features["weekday"],
                    "holiday": features["holiday"],
                    "traffic_volume": features["traffic_volume"],
                    "rain": features["rain"],
                    "snow": features["snow"],
                    "temperature": features["temperature"],
                    "event": features["event"],
                    "event_scale": features["event_scale"]
                }
            ]
        },
        "GlobalParameters": {}
    }


# =========================================================
# AML Response Parsing (기존 동일)
# =========================================================

def extract_congestion_score(result):
    """
    AML 응답 JSON에서 'Scored Labels' 값을 추출합니다.
    """
    try:
        results = result["Results"]

        # Designer Endpoint마다 WebServiceOutput0 / WebServiceOutput1
        # 이름이 다를 수 있으므로 첫 번째 출력 사용
        for output_name, output_rows in results.items():
            if (
                output_name.startswith("WebServiceOutput")
                and isinstance(output_rows, list)
                and len(output_rows) > 0
                and "Scored Labels" in output_rows[0]
            ):
                return float(
                    output_rows[0]["Scored Labels"]
                )

        raise ValueError(
            f"Scored Labels가 포함된 WebServiceOutput을 찾지 못했습니다: {result}"
        )

    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError(
            f"AML 응답에서 Scored Labels 추출 실패: {result}"
        ) from error


# =========================================================
# AML Request (기존 동일, 병렬 스레드 내에서 실행됨)
# =========================================================

def call_aml_endpoint(
    data_type,
    horizon_min,
    features
):
    """
    특정 데이터 타입 및 예측 시점에 대한 AML 엔드포인트를 호출합니다.
    """

    config = AML_ENDPOINTS[
        data_type
    ][horizon_min]

    endpoint_url = os.getenv(
        config["url_env"]
    )

    endpoint_key = os.getenv(
        config["key_env"]
    )

    if not endpoint_url:
        raise ValueError(
            f"{config['url_env']} 환경변수가 없습니다."
        )

    if not endpoint_key:
        raise ValueError(
            f"{config['key_env']} 환경변수가 없습니다."
        )

    payload = build_aml_payload(
        features
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {endpoint_key}"
    }

    logging.info(
        "[AML REQUEST] data_type=%s horizon=%s",
        data_type,
        horizon_min
    )

    # I/O Bound 작업: 네트워크 응답 대기 발생
    response = requests.post(
        endpoint_url,
        headers=headers,
        json=payload,
        timeout=30
    )

    response.raise_for_status()

    try:
        raw_result = response.json()

    except ValueError:
        raw_result = response.text

    congestion_score = (
        extract_congestion_score(
            raw_result
        )
    )

    if not (
        0 <= congestion_score <= 100
    ):
        raise ValueError(
            "congestion_score 범위 오류: "
            f"{congestion_score}"
        )

    logging.info(
        "[AML RESPONSE] "
        "data_type=%s horizon=%s score=%s",
        data_type,
        horizon_min,
        congestion_score
    )

    return {
        "data_type": data_type,
        "horizon_min": horizon_min,
        "congestion_score": congestion_score,
        "model_version": config["model_version"],
        "raw_result": raw_result
    }


# =========================================================
# AML Multi-thread (기존 동일)
# =========================================================

def call_models_parallel(
    prediction_jobs
):
    """
    ThreadPoolExecutor를 사용하여 AML 모델 호출 작업을 병렬로 수행합니다.
    """

    results = []

    if not prediction_jobs:
        return results

    # 최대 6개의 스레드 사용
    max_workers = min(
        6,
        len(prediction_jobs)
    )

    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as executor:

        future_map = {}

        # 스레드 풀에 작업 제출
        for job in prediction_jobs:

            future = executor.submit(
                call_aml_endpoint,
                job["data_type"],
                job["horizon_min"],
                job["features"]
            )

            future_map[future] = job

        # 완료된 작업부터 결과 수집
        for future in as_completed(
            future_map
        ):

            job = future_map[future]

            try:

                result = future.result()

                # 원래 job 정보 매핑
                result["location_id"] = job["location_id"]
                result["generated_at"] = job["generated_at"]
                result["target_at"] = job["target_at"]

                results.append(result)

            except Exception as error:

                logging.exception(
                    "[AML ERROR] data_type=%s horizon=%s",
                    job["data_type"],
                    job["horizon_min"]
                )

                # 에러 정보 포함하여 결과에 추가
                results.append(
                    {
                        "data_type": job["data_type"],
                        "horizon_min": job["horizon_min"],
                        "location_id": job["location_id"],
                        "generated_at": job["generated_at"],
                        "target_at": job["target_at"],
                        "error": str(error)
                    }
                )

    # 결과 정렬 (data_type, horizon_min 순)
    results.sort(
        key=lambda item: (
            item["data_type"],
            item["horizon_min"]
        )
    )

    return results


# =========================================================
# prediction_result INSERT (기존 동일)
# =========================================================

def save_prediction_results(
    conn,
    predictions
):
    """
    성공한 예측 결과를 DB의 prediction_result 테이블에 저장합니다.
    """

    # 에러가 없는 결과만 필터링
    successful_predictions = [
        prediction
        for prediction in predictions
        if "error" not in prediction
    ]

    if not successful_predictions:
        logging.warning("저장 가능한 AML 예측결과가 없습니다.")
        return 0

    sql = """
        INSERT INTO transit_mvp.prediction_result (
            data_type,
            location_id,
            generated_at,
            target_at,
            horizon_min,
            congestion_score,
            model_version
        )
        VALUES (
            %(data_type)s,
            %(location_id)s,
            %(generated_at)s,
            %(target_at)s,
            %(horizon_min)s,
            %(congestion_score)s,
            %(model_version)s
        );
    """

    cur = conn.cursor()

    try:
        # 트랜잭션 하나로 배치 INSERT 수행
        for prediction in successful_predictions:

            cur.execute(
                sql,
                {
                    "data_type": prediction["data_type"],
                    "location_id": prediction["location_id"],
                    "generated_at": prediction["generated_at"],
                    "target_at": prediction["target_at"],
                    "horizon_min": prediction["horizon_min"],
                    "congestion_score": round(float(prediction["congestion_score"]), 2),
                    "model_version": prediction["model_version"]
                }
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()

    logging.info("[DB] prediction_result %s건 저장 완료", len(successful_predictions))

    return len(successful_predictions)


# =========================================================
# HTTP Trigger (개선됨)
# ASA → Function → AML → PostgreSQL
# DB 조회를 배치로 통합하여 성능을 획기적으로 향상시켰습니다.
# =========================================================

@app.route(
    route="predict_traffic",
    methods=["POST"]
)
def predict_traffic(
    req: func.HttpRequest
) -> func.HttpResponse:

    logging.info("========================================")
    logging.info("ASA → Function App Prediction START")
    logging.info("========================================")

    conn = None

    try:
        # 페이로드 가져오기
        req_body = req.get_json()

        logging.info("[1] Received payload from ASA: %s", req_body)

        # 단일 아이템 요청을 리스트로 통일
        items = (
            req_body
            if isinstance(req_body, list)
            else [req_body]
        )
        item_count = len(items)
        logging.info("[2] Received item count: %s", item_count)

        if item_count == 0:
            return func.HttpResponse(json.dumps({"status": "success", "processed_count": 0}), 200, mimetype="application/json")


        # [추가] 1. 통합 벌크 조회에 필요한 정보 미리 추출 (Prep)
        all_location_ids = set() # 중복 제거를 위해 set 사용
        all_dates = set()
        
        # 이벤트 조회에 사용될 reference_time (ASA 페이로드의 window_end 사용)
        # item['window_end'] 형식: "2024-05-20T10:05:00Z" -> replace로 timezone 처리
        window_end_str_for_ref = items[0]["window_end"].replace("Z", "+00:00")
        reference_time_for_event = datetime.fromisoformat(window_end_str_for_ref)

        for item in items:
            all_location_ids.add(item["location_id"])
            
            # window_start에서 날짜 객체 추출 (휴일 조회용)
            # "2024-05-20T10:00:00Z" -> "2024-05-20"
            window_start_str = item["window_start"].replace("Z", "+00:00")
            window_start_dt = datetime.fromisoformat(window_start_str)
            all_dates.add(window_start_dt.date())

        logging.info("[DB Prep] unique location_ids: %s건, unique dates: %s건", len(all_location_ids), len(all_dates))


        # DB 연결
        conn = get_db_connection()


        # [추가] 2. 통합 벌크 조회 실행 -> 루프 밖에서 단 3번의 쿼리 수행
        weather_lookup, event_lookup, holiday_lookup = fetch_lookup_data_batch(
            conn, 
            list(all_location_ids), # set을 list로 변환하여 전달
            list(all_dates), 
            reference_time_for_event
        )


        prediction_jobs = []
        feature_results = []

        # =================================================
        # Feature 생성 (개선: 루프 내 DB 조회 완전히 제거)
        # =================================================

        for item in items:
            # item에서 필수 값 추출 (기존과 동일)
            location_id = item["location_id"]
            data_type = item["data_type"]
            traffic_sum_5m = item["traffic_sum_5m"]

            if data_type not in ("BUS", "SUBWAY"):
                raise ValueError(f"Unsupported data_type: {data_type}")

            # 타임스탬프 처리 (Z -> timezone offset)
            window_start_str = item["window_start"].replace("Z", "+00:00")
            window_end_str = item["window_end"].replace("Z", "+00:00")
            window_start = datetime.fromisoformat(window_start_str)
            window_end = datetime.fromisoformat(window_end_str)

            
            # [변경] 3. 미리 조회해둔 룩업 딕셔너리(메모리)에서 데이터 가져오기
            # .get()을 사용하여 키가 없을 경우의 기본값(Default)을 설정합니다.

            # (1) 날씨 데이터 가져오기 (기존 get_latest_weather 내 기본값과 동일)
            weather = weather_lookup.get(
                location_id, 
                {"temperature": 20.0, "rain": 0, "snow": 0} # DB에 최신 데이터가 없을 때 기본값
            )

            # (2) 이벤트 데이터 가져오기 (기존 get_event 내 기본값과 동일)
            event_info = event_lookup.get(
                location_id, 
                {"event": 0, "event_scale": 0} # DB에 이벤트가 없을 때 기본값
            )

            # (3) 휴일 데이터 가져오기 (기존 get_holiday 내 기본값과 동일)
            # 키로 date 객체(window_start.date())를 사용합니다.
            holiday = holiday_lookup.get(
                window_start.date(), 
                False # DB에 정보가 없거나 휴일이 아닐 때 기본값
            )


            # 피처 생성 함수 호출
            features = make_features(
                traffic_sum_5m=traffic_sum_5m,
                window_start=window_start,
                window_end=window_end,
                weather=weather,
                event_info=event_info,
                holiday=holiday
            )

            feature_results.append({
                "data_type": data_type,
                "location_id": location_id,
                "features": features
            })

            # 예측 작업 구성 루프 ( generated_at 기준 horizon_min 별 작업 추가 )
            # generated_at을 예측 생성 시각으로 사용 (window_end)
            generated_at = window_end
            for horizon_min in (0, 60, 120): # 현재, 1시간 후, 2시간 후 예측
                target_at = generated_at + timedelta(minutes=horizon_min) # 예측 타겟 시각
                prediction_jobs.append({
                    "data_type": data_type,
                    "horizon_min": horizon_min,
                    "location_id": location_id,
                    "generated_at": generated_at,
                    "target_at": target_at,
                    "features": features
                })

        # =================================================
        # AML 최대 6개 병렬 호출 (I/O Bound작업 최적화, 기존 동일)
        # =================================================

        logging.info("[3] AML parallel jobs: %s건", len(prediction_jobs))

        predictions = call_models_parallel(prediction_jobs)

        # =================================================
        # prediction_result 저장 (기존 동일)
        # =================================================

        saved_count = save_prediction_results(conn, predictions)

        logging.info("[4] prediction_result saved: %s건", saved_count)

        # =================================================
        # Response 반환 (ensure_ascii=False 설정)
        # =================================================

        return func.HttpResponse(
            json.dumps(
                {
                    "status": "success",
                    "processed_count": len(items),
                    "prediction_job_count": len(prediction_jobs),
                    "saved_count": saved_count,
                    "features": feature_results,
                    "predictions": predictions
                },
                ensure_ascii=False,
                default=str # datetime 객체 등의 직렬화를 위해 설정
            ),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        # 예외 로깅 및 에러 응답 반환
        logging.exception("Error executing prediction pipeline")
        return func.HttpResponse(
            json.dumps(
                {
                    "status": "error",
                    "message": str(e)
                },
                ensure_ascii=False
            ),
            status_code=500,
            mimetype="application/json"
        )

    finally:
        # DB 연결 종료 보장
        if conn:
            conn.close()

        logging.info("========================================")
        logging.info("ASA → Function App Prediction END")
        logging.info("========================================")