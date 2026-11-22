import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_KEY = os.environ["CWA_API_KEY"]
BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
DATASETS = [
    ("W-C0033-001", "各別縣市地區目前之天氣警特報情形"),
    ("W-C0033-002", "各別天氣警特報之內容及所影響的區域"),
]
OUTPUT = Path("data/weather-alerts.json")
TIMEOUT = 45


def fetch(session, dataset_id):
    response = session.get(
        f"{BASE}/{dataset_id}",
        params={"Authorization": API_KEY, "format": "JSON"},
        timeout=TIMEOUT,
    )
    if response.status_code == 429:
        time.sleep(5)
        response = session.get(
            f"{BASE}/{dataset_id}",
            params={"Authorization": API_KEY, "format": "JSON"},
            timeout=TIMEOUT,
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is False:
        raise RuntimeError(f"Invalid CWA response for {dataset_id}")
    return payload


def extract_records(payload):
    records = payload.get("records", {})
    if isinstance(records, list):
        return [x for x in records if isinstance(x, dict)]
    if isinstance(records, dict):
        for value in records.values():
            if isinstance(value, list) and all(isinstance(x, dict) for x in value):
                return value
            if isinstance(value, dict):
                for nested in value.values():
                    if isinstance(nested, list) and all(isinstance(x, dict) for x in nested):
                        return nested
                return [value]
    return []


def main():
    session = requests.Session()
    all_alerts = []
    dataset_status = []
    failures = []

    for dataset_id, dataset_name in DATASETS:
        try:
            payload = fetch(session, dataset_id)
            records = extract_records(payload)
            dataset_status.append({
                "id": dataset_id,
                "name": dataset_name,
                "success": True,
                "recordCount": len(records),
            })
            for record in records:
                all_alerts.append({
                    "datasetId": dataset_id,
                    "datasetName": dataset_name,
                    "raw": record,
                })
            print(f"{dataset_id}: {len(records)} records")
        except Exception as exc:
            failures.append(f"{dataset_id}: {exc}")
            dataset_status.append({
                "id": dataset_id,
                "name": dataset_name,
                "success": False,
                "error": str(exc),
            })
            print(f"WARNING {dataset_id}: {exc}")

    if not any(item["success"] for item in dataset_status):
        raise RuntimeError("All CWA warning datasets failed; existing alert data was not overwritten. " + "; ".join(failures))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "success": True,
        "source": "中央氣象署開放資料平台",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "datasets": dataset_status,
        "alerts": all_alerts,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Saved {len(all_alerts)} official warning records to {OUTPUT}")


if __name__ == "__main__":
    main()
