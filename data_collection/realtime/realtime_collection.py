import pandas as pd
import requests


# ---------------------------------------------------------
# 1. 60분 예측 전용 함수
# ---------------------------------------------------------
def predict_60m(x_values, avg_5m, avg_10m, avg_30m):
    """5, 10, 30분 데이터를 이용해 60분 평균값을 추정하는 함수"""
    y_values = np.array([avg_5m, avg_10m, avg_30m], dtype=float)
    if np.all(y_values == 0):
        return 0.0

    slope, intercept = np.polyfit(x_values, y_values, 1)
    pred_60m_avg = max(0.0, slope * 60 + intercept)
    return round(pred_60m_avg)


def get_xml_tag_value(parent_node, tag_name):
    if parent_node is None:
        return 0.0
    elem = parent_node.find(f".//{tag_name}")
    if elem is not None and elem.text:
        try:
            return float(elem.text)
        except ValueError:
            return 0.0
    return 0.0


# ---------------------------------------------------------
# 2. 실시간 API 호출 및 DataFrame 생성 함수
# ---------------------------------------------------------
def fetch_and_split_realtime_data(api_url, line2_ratio=0.7072):
    response = requests.get(api_url, timeout=30)
    response.raise_for_status()

    root = ET.fromstring(response.content)

    # 노드 파싱
    sub_node = root.find(".//LIVE_SUB_PPLTN")
    bus_node = root.find(".//LIVE_BUS_PPLTN")
    weather_node = root.find(".//WEATHER_STTS/WEATHER_STTS")
    event_node = root.find(".//EVENT_STTS/EVENT_STTS")

    x_values = np.array([5, 10, 30], dtype=float)

    # ---------------------------------------------------------
    # A. 버스 60분 예측 계산
    # ---------------------------------------------------------
    bus_gton_5 = (
        get_xml_tag_value(bus_node, "BUS_5WTHN_GTON_PPLTN_MIN")
        + get_xml_tag_value(bus_node, "BUS_5WTHN_GTON_PPLTN_MAX")
    ) / 2.0
    bus_gton_10 = (
        get_xml_tag_value(bus_node, "BUS_10WTHN_GTON_PPLTN_MIN")
        + get_xml_tag_value(bus_node, "BUS_10WTHN_GTON_PPLTN_MAX")
    ) / 2.0
    bus_gton_30 = (
        get_xml_tag_value(bus_node, "BUS_30WTHN_GTON_PPLTN_MIN")
        + get_xml_tag_value(bus_node, "BUS_30WTHN_GTON_PPLTN_MAX")
    ) / 2.0

    bus_gtoff_5 = (
        get_xml_tag_value(bus_node, "BUS_5WTHN_GTOFF_PPLTN_MIN")
        + get_xml_tag_value(bus_node, "BUS_5WTHN_GTOFF_PPLTN_MAX")
    ) / 2.0
    bus_gtoff_10 = (
        get_xml_tag_value(bus_node, "BUS_10WTHN_GTOFF_PPLTN_MIN")
        + get_xml_tag_value(bus_node, "BUS_10WTHN_GTOFF_PPLTN_MAX")
    ) / 2.0
    bus_gtoff_30 = (
        get_xml_tag_value(bus_node, "BUS_30WTHN_GTOFF_PPLTN_MIN")
        + get_xml_tag_value(bus_node, "BUS_30WTHN_GTOFF_PPLTN_MAX")
    ) / 2.0

    bus_boarding_60m_avg = predict_60m(
        x_values, bus_gton_5, bus_gton_10, bus_gton_30
    )
    bus_alighting_60m_avg = predict_60m(
        x_values, bus_gtoff_5, bus_gtoff_10, bus_gtoff_30
    )

    # ★ 버스 60분 총 예상 승하차 평균인원 (bus_prediction['total']['avg'])
    bus_total_60m_avg = bus_boarding_60m_avg + bus_alighting_60m_avg

    # ---------------------------------------------------------
    # B. 지하철 60분 및 2호선 예측 계산
    # ---------------------------------------------------------
    sub_gton_5 = (
        get_xml_tag_value(sub_node, "SUB_5WTHN_GTON_PPLTN_MIN")
        + get_xml_tag_value(sub_node, "SUB_5WTHN_GTON_PPLTN_MAX")
    ) / 2.0
    sub_gton_10 = (
        get_xml_tag_value(sub_node, "SUB_10WTHN_GTON_PPLTN_MIN")
        + get_xml_tag_value(sub_node, "SUB_10WTHN_GTON_PPLTN_MAX")
    ) / 2.0
    sub_gton_30 = (
        get_xml_tag_value(sub_node, "SUB_30WTHN_GTON_PPLTN_MIN")
        + get_xml_tag_value(sub_node, "SUB_30WTHN_GTON_PPLTN_MAX")
    ) / 2.0

    sub_gtoff_5 = (
        get_xml_tag_value(sub_node, "SUB_5WTHN_GTOFF_PPLTN_MIN")
        + get_xml_tag_value(sub_node, "SUB_5WTHN_GTOFF_PPLTN_MAX")
    ) / 2.0
    sub_gtoff_10 = (
        get_xml_tag_value(sub_node, "SUB_10WTHN_GTOFF_PPLTN_MIN")
        + get_xml_tag_value(sub_node, "SUB_10WTHN_GTOFF_PPLTN_MAX")
    ) / 2.0
    sub_gtoff_30 = (
        get_xml_tag_value(sub_node, "SUB_30WTHN_GTOFF_PPLTN_MIN")
        + get_xml_tag_value(sub_node, "SUB_30WTHN_GTOFF_PPLTN_MAX")
    ) / 2.0

    sub_boarding_60m_avg = predict_60m(
        x_values, sub_gton_5, sub_gton_10, sub_gton_30
    )
    sub_alighting_60m_avg = predict_60m(
        x_values, sub_gtoff_5, sub_gtoff_10, sub_gtoff_30
    )

    # 지하철 전체 60분 총 예상
    sub_total_60m_avg = sub_boarding_60m_avg + sub_alighting_60m_avg

    # ★ 2호선 60분 총 예상 승하차 평균인원 (line2_prediction['total']['avg'])
    line2_total_60m_avg = round(sub_total_60m_avg * line2_ratio)

    # ---------------------------------------------------------
    # C. 공통 날씨 및 이벤트 정보
    # ---------------------------------------------------------
    def get_str(parent, tag, default=""):
        elem = parent.find(f".//{tag}") if parent is not None else None
        return elem.text if (elem is not None and elem.text) else default

    temp = float(get_str(weather_node, "TEMP", "0"))
    precpt_type = get_str(weather_node, "PRECPT_TYPE", "없음")
    rain_val = 1.0 if "비" in precpt_type else 0.0
    snow_val = 1.0 if "눈" in precpt_type else 0.0
    event_val = 1.0 if event_node is not None else 0.0

    time_str = get_str(weather_node, "WEATHER_TIME", "")
    if time_str:
        dt = pd.to_datetime(time_str)
    else:
        dt = pd.Timestamp.now()

    hour_val = dt.hour
    weekday_val = dt.weekday()

    # 현재 혼잡도 (30분 평균 승하차 합계)
    bus_current_co = bus_gton_30 + bus_gtoff_30
    sub_current_co = sub_gton_30 + sub_gtoff_30

    # ---------------------------------------------------------
    # D. DataFrame 생성
    # ---------------------------------------------------------
    # 1. 버스 DataFrame
    df_bus = pd.DataFrame(
        [
            {
                "datetime": dt,
                "hour": hour_val,
                "weekday": weekday_val,
                "holiday": 0,
                "traffic_volume": bus_total_60m_avg,  # bus_prediction['total']['avg']
                "rain": rain_val,
                "snow": snow_val,
                "temperature": temp,
                "event": event_val,
                "event_scale": 0.0,
                "current_congestion": bus_current_co,
                "target_1h": None,
                "target_2h": None,
            }
        ]
    )

    # 2. 지하철 DataFrame
    df_subway = pd.DataFrame(
        [
            {
                "datetime": dt,
                "hour": hour_val,
                "weekday": weekday_val,
                "holiday": 0,
                "traffic_volume": line2_total_60m_avg,  # line2_prediction['total']['avg']
                "rain": rain_val,
                "snow": snow_val,
                "temperature": temp,
                "event": event_val,
                "event_scale": 0.0,
                "current_congestion": sub_current_co,
                "target_1h": None,
                "target_2h": None,
            }
        ]
    )

    return df_bus, df_subway


# --- 실행 및 확인 ---
KEY = ""
AREA_NM = "POI109"
url = f"http://openapi.seoul.go.kr:8088/{KEY}/xml/citydata/1/5/{AREA_NM}"

df_bus, df_subway = fetch_and_split_realtime_data(url)

print("=== 🚌 버스 데이터프레임 ===")
print(df_bus.to_string())

print("\n=== 🚇 지하철 데이터프레임 ===")
print(df_subway.to_string())