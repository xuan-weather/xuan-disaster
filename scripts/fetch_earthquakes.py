import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_KEY = os.environ["CWA_API_KEY"]
BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
DATASETS = [
    ("E-A0015-001", "顯著有感地震", "significant"),
    ("E-A0016-001", "小區域有感地震", "local"),
]
OUT = Path("data/earthquakes.json")
TIMEOUT = 45


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def first(objects, keys):
    for obj in objects:
        if isinstance(obj, dict):
            for key in keys:
                value = obj.get(key)
                if value not in (None, ""):
                    return value
    return None


def number(value):
    if isinstance(value, dict):
        value = first([value], ["value", "Value", "#text"])
    if value is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def fetch(session, dataset_id):
    url = f"{BASE}/{dataset_id}"
    params = {"Authorization": API_KEY, "format": "JSON"}

    for attempt in range(1, 5):
        try:
            response = session.get(url, params=params, timeout=TIMEOUT)

            if response.status_code == 429:
                time.sleep(min(5 * attempt, 30))
                continue

            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, dict):
                raise RuntimeError("API 回傳不是 JSON 物件")

            if payload.get("success") is False:
                raise RuntimeError(str(payload))

            return payload

        except Exception:
            if attempt == 4:
                raise
            time.sleep(min(3 * attempt, 12))

    raise RuntimeError(f"{dataset_id} 取得失敗")


def get_records(payload):
    records = payload.get("records", {})
    result = []

    if isinstance(records, list):
        return [x for x in records if isinstance(x, dict)]

    if isinstance(records, dict):
        for key in (
            "Earthquake",
            "earthquake",
            "Earthquakes",
            "EarthquakeInfo",
            "Record",
        ):
            value = records.get(key)

            if isinstance(value, list):
                result.extend(x for x in value if isinstance(x, dict))
            elif isinstance(value, dict):
                result.append(value)

        if not result:
            for value in records.values():
                if isinstance(value, dict):
                    result.append(value)
                elif isinstance(value, list):
                    result.extend(x for x in value if isinstance(x, dict))

    return result


def get_intensities(root):
    rows = []
    seen = set()

    for item in walk(root):
        area = first([item], [
            "AreaDesc", "CountyName", "areaDesc",
            "countyName", "Location", "location"
        ])

        intensity = first([item], [
            "AreaIntensity", "SeismicIntensity",
            "IntScaleValue", "areaIntensity",
            "seismicIntensity"
        ])

        station = first([item], [
            "StationName", "StationID",
            "stationName", "stationId"
        ])

        if area is not None and intensity is not None:
            key = (str(area), str(intensity), str(station or ""))

            if key not in seen:
                seen.add(key)
                rows.append({
                    "area": str(area),
                    "intensity": str(intensity),
                    "station": str(station or "")
                })

    return rows


def normalize(root, title, kind):
    objects = list(walk(root))

    origin = first(objects, [
        "originTime", "OriginTime",
        "EarthquakeTime", "origin_time"
    ])

    latitude = first(objects, [
        "epicenterLatitude", "EpicenterLatitude",
        "Latitude", "latitude"
    ])

    longitude = first(objects, [
        "epicenterLongitude", "EpicenterLongitude",
        "Longitude", "longitude"
    ])

    depth = first(objects, [
        "depth", "FocalDepth", "focalDepth"
    ])

    magnitude = first(objects, [
        "magnitudeValue", "MagnitudeValue",
        "magnitude", "Magnitude"
    ])

    if isinstance(magnitude, dict):
        magnitude = first(list(walk(magnitude)), [
            "magnitudeValue", "MagnitudeValue",
            "value", "Value"
        ])

    location = first(objects, [
        "Location", "location",
        "EpicenterLocation", "epicenterLocation",
        "ReportContent", "reportContent"
    ])

    event_no = first(objects, [
        "EarthquakeNo", "earthquakeNo",
        "ReportNo", "reportNo",
        "Identifier", "identifier"
    ])

    lat = number(latitude)
    lon = number(longitude)

    if not origin or lat is None or lon is None:
        return None

    report_url = first(objects, [
        "Web", "web", "ReportURL",
        "ReportUrl", "reportUrl"
    ])

    event_id = str(
        event_no or f"{kind}-{origin}-{lat}-{lon}"
    )

    return {
        "id": event_id,
        "eventType": title,
        "eventTypeKey": kind,
        "originTime": str(origin),
        "latitude": lat,
        "longitude": lon,
        "location": str(location) if location else None,
        "depth": number(depth),
        "magnitude": number(magnitude),
        "intensities": get_intensities(root),
        "reportUrl": str(report_url) if report_url else
            "https://www.cwa.gov.tw/V8/C/E/index.html",
        "raw": root
    }


def main():
    session = requests.Session()
    events = {}
    status = []

    for dataset_id, title, kind in DATASETS:
        print(f"Fetching {dataset_id} {title}...")

        try:
            payload = fetch(session, dataset_id)
            records = get_records(payload)
            count = 0

            for record in records:
                event = normalize(record, title, kind)

                if event:
                    events[event["id"]] = event
                    count += 1

            status.append({
                "id": dataset_id,
                "name": title,
                "success": True,
                "recordCount": count
            })

            print(
                f"{dataset_id}: normalized {count} "
                f"records from {len(records)} source records"
            )

        except Exception as exc:
            status.append({
                "id": dataset_id,
                "name": title,
                "success": False,
                "error": str(exc)
            })
            print(f"WARNING {dataset_id}: {exc}")

    ordered = sorted(
        events.values(),
        key=lambda x: x["originTime"],
        reverse=True
    )

    # 不允許空資料覆蓋原本有效資料
    if not ordered:
        raise RuntimeError(
            "No earthquake events were normalized. "
            "Check CWA_API_KEY and API response schema. "
            "Existing data file was preserved."
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "success": True,
        "source": "中央氣象署開放資料平台",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "eventCount": len(ordered),
        "datasetStatus": status,
        "earthquakes": ordered,
        "notes": [
            "此資料檔為排程擷取快照。",
            "資料範圍依中央氣象署兩項地震報告資料集實際回傳內容。",
            "官方資料未提供的欄位保持 null，不以示範值補齊。"
        ]
    }

    OUT.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8"
    )

    print(f"Saved {len(ordered)} unique events to {OUT}")


if __name__ == "__main__":
    main()
