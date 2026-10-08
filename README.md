# Real-time Traffic Data Pipeline

Azure 기반 실시간 대중교통 혼잡도 예측 및 알림 파이프라인입니다.

## Architecture

```text
External API
   │
   ▼
Data Collection
   ├─ Realtime
   │    └─ Seoul Citydata API
   │
   └─ Batch
        ├─ Event API       (추후 구현)
        ├─ Holiday API     (추후 구현)
        └─ Social/X        (별도 수집 코드)
   │
   ▼
Azure Event Hub
   │
   ▼
Azure Stream Analytics
   ├─ observation_5m
   ├─ weather_history
   ├─ predict-traffic
   └─ subway-alert
        │
        ├───────────────┐
        ▼               ▼
Inference Function   Alert Function
        │               │
        ▼               ▼
Azure ML            Microsoft Teams
(6 Endpoints)
        │
        ▼
PostgreSQL
   └─ prediction_result

[UI]
  └─ 추후 구현
```

## Repository Structure

```text
.
├── data_collection/
│   ├── realtime/
│   │   └── realtime_collection.py
│   │
│   └── batch/
│       ├── event/
│       │   └── README.md
│       ├── holiday/
│       │   └── README.md
│       ├── social/
│       │   └── collect_x_posts_cdp.py
│       └── README.md
│
├── stream_analytics/
│   └── SA_Query.sql
│
├── functions/
│   ├── inference/
│   │   ├── function_app.py
│   │   ├── host.json
│   │   └── requirements.txt
│   │
│   └── alert/
│       ├── function_app.py
│       ├── host.json
│       └── requirements.txt
│
├── aml/
│   └── README.md
│
├── database/
│   └── README.md
│
├── notebooks/
│   └── realtime_e2e_validation.ipynb
│
└── ui/
    └── README.md
```

## Main Flow

### 1. Realtime Data Collection

Seoul Citydata API에서 버스·지하철 실시간 데이터를 수집하고, 날씨 및 이벤트 정보를 함께 처리하는 기존 수집 코드를 `data_collection/realtime/`에 통합했습니다.

### 2. Stream Analytics

Event Hub의 원천 데이터를 event time 기준 5분 Tumbling Window로 집계합니다.

주요 Output:

- `observation-5m-postgres`
- `weather_history_postgres`
- `predict-traffic`
- `subway-alert`

### 3. Inference Function

`predict-traffic`를 입력으로 받아:

1. 입력 검증
2. PostgreSQL에서 Weather / Event / Holiday 조회
3. Feature Engineering
4. BUS / SUBWAY 예측 Job 생성
5. Azure ML Endpoint 호출
6. `prediction_result` 저장

을 수행합니다.

현재 통합된 버전은 PostgreSQL Weather/Event/Holiday 데이터를 먼저 벌크 조회하고, Azure ML 최대 6개 Endpoint 호출을 `ThreadPoolExecutor`로 병렬 처리합니다.

### 4. Alert Function

Stream Analytics의 `subway-alert` Output을 받아 Microsoft Teams Webhook으로 승하차량 급증 알림을 전송합니다.

알림 임계값 판단은 Stream Analytics에서 수행합니다.

## Azure ML

AML은 실제 프로젝트의 핵심 구성요소이지만, 이번 Repository 통합에서는 모델 및 Endpoint 관련 산출물을 아직 추가하지 않고 `aml/` 디렉터리만 구성했습니다.

Inference Function은 다음 6개 Endpoint를 환경변수로 참조합니다.

- BUS: current / 1h / 2h
- SUBWAY: current / 1h / 2h

## Batch Data Collection

현재 제공된 소스에는 메인 프로젝트의 Event API / Holiday API 배치 수집 구현이 없습니다. 따라서 해당 디렉터리 구조만 먼저 구성했습니다.

`collect_x_posts_cdp.py`는 서울교통공사 및 서울시 교통정보센터 TOPIS 공식 X 게시물을 수집하는 별도 배치성 수집 코드로 `data_collection/batch/social/`에 보관했습니다.

## UI

UI는 백엔드 데이터 파이프라인 통합 이후 마지막 단계에서 구현합니다. 현재 Repository에는 UI 구현 코드를 포함하지 않습니다.

## Security

API Key, Azure ML Endpoint Key, PostgreSQL Password, Teams Webhook URL 등 비밀값은 환경변수 또는 Azure 설정을 통해 주입하며 Repository에 직접 저장하지 않습니다.

## Deployment

Azure Functions는 Function App 단위로 배포할 수 있도록 Inference와 Alert를 별도 디렉터리로 분리했습니다.

- `functions/inference/`
- `functions/alert/`

각 디렉터리는 독립적인 `host.json`과 `requirements.txt`를 가집니다.
