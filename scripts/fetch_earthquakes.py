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
OUTPUT = Path("data/earthquakes.json")
TIMEOUT = 45


def keynorm(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def find_value(objects, names):
    wanted = {keynorm(name) for name in names}
    for obj in objects:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if keynorm(key) in wanted and value not in (None, ""):
                    return value
    return None


def scalar(value):
    if isinstance(value, dict):
        return find_value([value], ["value", "content", "text", "#text"])
    return value


def numeric(value):
    value = scalar(value)
    if value is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def fetch_dataset(session, dataset_id):
    url = f"{BASE}/{dataset_id}"
    params = {"Authorization": API_KEY, "format": "JSON"}
    last_error = None
    for attempt in range(1, 5):
        try:
            response = session.get(url, params=params, timeout=TIMEOUT)
            if response.status_code == 429:
                time.sleep(min(attempt * 5, 30))
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("API response is not a JSON object")
            if payload.get("success") is False:
                raise RuntimeError(str(payload))
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(attempt * 3)
    raise RuntimeError(f"{dataset_id}: {last_error}")


def extract_records(payload):
    records = payload.get("records", {})
    if isinstance(records, list):
        return [x for x in records if isinstance(x, dict)]
    if isinstance(records, dict):
        for name in ("Earthquake", "earthquake", "Earthquakes", "earthquakes"):
            value = records.get(name)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                return [value]
        # Fallback for API wrappers whose list name changed.
        for value in records.values():
            if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
                return value
    return []


def normalize_record(record, dataset_name, dataset_key):
    objects = list(walk(record))
    info = find_value([record], ["EarthquakeInfo", "earthquakeInfo"])
    info_objects = list(walk(info)) if info is not None else objects

    origin = scalar(find_value(info_objects, ["OriginTime", "originTime", "EarthquakeTime"]))
    epicenter = find_value(info_objects, ["Epicenter", "epicenter"])
    epicenter_objects = list(walk(epicenter)) if epicenter is not None else info_objects

    latitude = numeric(find_value(epicenter_objects, [
        "EpicenterLatitude", "epicenterLatitude", "Latitude", "latitude", "lat"
    ]))
    longitude = numeric(find_value(epicenter_objects, [
        "EpicenterLongitude", "epicenterLongitude", "Longitude", "longitude", "lon", "lng"
    ]))

    if not origin or latitude is None or longitude is None:
        return None

    magnitude_obj = find_value(info_objects, ["EarthquakeMagnitude", "earthquakeMagnitude", "Magnitude"])
    magnitude_objects = list(walk(magnitude_obj)) if magnitude_obj is not None else info_objects
    magnitude = numeric(find_value(magnitude_objects, [
        "MagnitudeValue", "magnitudeValue", "Magnitude", "magnitude"
    ]))
    depth = numeric(find_value(info_objects, ["FocalDepth", "focalDepth", "Depth", "depth"]))
    location = scalar(find_value(epicenter_objects, ["Location", "location", "EpicenterLocation"]))

    event_no = scalar(find_value(objects, [
        "EarthquakeNo", "earthquakeNo", "ReportNo", "reportNo", "Identifier", "identifier"
    ]))
    report_url = scalar(find_value(objects, [
        "ReportImageURI", "reportImageURI", "Web", "web", "ReportURL", "reportUrl"
    ]))

    intensity_root = find_value(objects, ["Intensity", "intensity"])
    intensity_objects = list(walk(intensity_root)) if intensity_root is not None else []
    shaking_areas = find_value(intensity_objects, ["ShakingArea", "shakingArea"])
    if isinstance(shaking_areas, dict):
        shaking_areas = [shaking_areas]
    if not isinstance(shaking_areas, list):
        shaking_areas = []

    intensities = []
    for area in shaking_areas:
        if not isinstance(area, dict):
            continue
        area_objects = list(walk(area))
        county = scalar(find_value(area_objects, ["CountyName", "countyName", "AreaDesc", "areaDesc"]))
        intensity = scalar(find_value(area_objects, ["AreaIntensity", "areaIntensity", "Intensity", "intensity"]))
        if county is not None and intensity is not None:
            intensities.append({"area": str(county), "intensity": str(intensity)})

    return {
        "id": str(event_no or f"{dataset_key}-{origin}-{latitude}-{longitude}"),
        "eventType": dataset_name,
        "eventTypeKey": dataset_key,
        "originTime": str(origin),
        "latitude": latitude,
        "longitude": longitude,
        "location": str(location) if location else None,
        "depth": depth,
        "magnitude": magnitude,
        "intensities": intensities,
        "reportUrl": str(report_url) if report_url else "https://www.cwa.gov.tw/V8/C/E/index.html",
        "raw": record,
    }


def main():
    session = requests.Session()
    events = {}
    dataset_status = []

    for dataset_id, dataset_name, dataset_key in DATASETS:
        print(f"Fetching {dataset_id} ({dataset_name})")
        try:
            payload = fetch_dataset(session, dataset_id)
            records = extract_records(payload)
            normalized = 0
            for record in records:
                event = normalize_record(record, dataset_name, dataset_key)
                if event:
                    events[event["id"]] = event
                    normalized += 1
            dataset_status.append({
                "id": dataset_id,
                "name": dataset_name,
                "success": True,
                "sourceRecords": len(records),
                "normalizedRecords": normalized,
            })
            print(f"{dataset_id}: {normalized}/{len(records)} records normalized")
        except Exception as exc:
            dataset_status.append({
                "id": dataset_id,
                "name": dataset_name,
                "success": False,
                "error": str(exc),
            })
            print(f"WARNING {dataset_id}: {exc}")

    ordered = sorted(events.values(), key=lambda item: item["originTime"], reverse=True)
    if not ordered:
        raise RuntimeError(
            "No earthquake records could be normalized. Existing data file was not overwritten. "
            "Check CWA_API_KEY and inspect the current API response sample."
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "success": True,
        "source": "中央氣象署開放資料平台",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "eventCount": len(ordered),
        "datasetStatus": dataset_status,
        "earthquakes": ordered,
        "notes": [
            "資料來源為中央氣象署顯著有感與小區域有感地震報告。",
            "地震報告經人工確認產製，非即時地震預警。",
            "官方未提供的欄位保持 null，不以示範值補齊。",
        ],
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Saved {len(ordered)} earthquake records to {OUTPUT}")


if __name__ == "__main__":
    main()
