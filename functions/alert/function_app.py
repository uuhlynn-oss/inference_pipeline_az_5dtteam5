import azure.functions as func
import json
import logging
import os
import requests


app = func.FunctionApp(
    http_auth_level=func.AuthLevel.FUNCTION
)


# =========================================================
# Teams Webhook
# =========================================================

def send_teams_alert(
    location_id,
    traffic_sum_5m
):

    webhook_url = os.getenv("TEAMS_WEBHOOK_URL")

    if not webhook_url:
        raise ValueError(
            "TEAMS_WEBHOOK_URL is not configured."
        )

    message = {
        "message": (
            "<span style=\"font-size: 24px\">"
            "🚨 지하철역 승하차량 급증 알림"
            "</span><br><br>"

            "<ul>"

            f"<li>역: {location_id}</li>"

            "<li>최근 5분 승하차량: "
            "<span style=\"color: red; font-weight: bold;\">"
            f"{traffic_sum_5m:,}명"
            "</span>"
            "</li>"

            "<li>알림 기준: "
            "500명 이상"
            "</li>"

            "<li>상태: 승하차량 급증 감지</li>"

            "</ul>"
        )
    }

    response = requests.post(
        webhook_url,
        json=message,
        timeout=10
    )

    response.raise_for_status()

    logging.info(
        f"[TEAMS] Alert sent successfully: "
        f"{location_id}, {traffic_sum_5m}"
    )


# =========================================================
# HTTP Trigger
# =========================================================

@app.route(
    route="subway_alert",
    methods=["POST"]
)
def subway_alert(
    req: func.HttpRequest
) -> func.HttpResponse:

    logging.info(
        "========================================"
    )
    logging.info(
        "SUBWAY ALERT FUNCTION START"
    )
    logging.info(
        "========================================"
    )

    try:

        # -------------------------------------------------
        # 1. SA 데이터 수신
        # -------------------------------------------------

        body = req.get_json()

        logging.info(
            f"Received payload: {body}"
        )

        # -------------------------------------------------
        # 2. SA Output 형태 처리
        #
        # 단건:
        # {
        #   "location_id": "...",
        #   "traffic_sum_5m": 550
        # }
        #
        # 배열:
        # [
        #   {...},
        #   {...}
        # ]
        # -------------------------------------------------

        items = (
            body
            if isinstance(body, list)
            else [body]
        )

        logging.info(
            f"Received item count: {len(items)}"
        )

        # -------------------------------------------------
        # 3. 각 Alert 데이터 처리
        # -------------------------------------------------

        for item in items:

            logging.info(
                f"Processing alert item: {item}"
            )

            location_id = item["location_id"]

            traffic_sum_5m = int(
                item["traffic_sum_5m"]
            )

            # -------------------------------------------------
            # 4. Teams 알림 전송
            #
            # ※ 500명 이상 판단은 SA에서 수행
            # -------------------------------------------------

            send_teams_alert(
                location_id=location_id,
                traffic_sum_5m=traffic_sum_5m
            )

        # -------------------------------------------------
        # 5. 성공 응답
        # -------------------------------------------------

        return func.HttpResponse(
            json.dumps(
                {
                    "status": "alert_sent",
                    "processed_count": len(items)
                },
                ensure_ascii=False
            ),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:

        logging.exception(
            "Alert Function error"
        )

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

        logging.info(
            "========================================"
        )
        logging.info(
            "SUBWAY ALERT FUNCTION END"
        )
        logging.info(
            "========================================"
        )