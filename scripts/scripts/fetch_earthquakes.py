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
    ("E-A0073-001", "本年度地震目錄", "catalog"),
]
OUT = Path("data/earthquakes.json")
TIMEOUT = 45


def first_value(obj, keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    return None


def walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def as_float(value):
    try:
        if value is None:
            return None
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        return float(match.group()) if match else None
    except (TypeError, ValueError):
        return None


def extract_intensities(root):
    rows = []
    seen = set()
    for item in walk_dicts(root):
        area = first_value(item, ["AreaName", "AreaDesc", "TownName", "CountyName", "Location"])
        intensity = first_value(item, ["AreaIntensity", "StationIntensity", "Intensity", "震度"])
        station = first_value(item, ["StationName", "StationID", "Station"])
        if area is not None and intensity is not None:
            key = (str(area), str(intensity), str(station or ""))
            if key not in seen:
                seen.add(key)
                rows.append({"area": str(area), "intensity": str(intensity), "station": str(station or "")})
    return rows


def normalize_event(root, dataset_name, event_key):
    # The CWA earthquake payload nests event information differently by product/version.
    dictionaries = list(walk_dicts(root))
    info = next((x for x in dictionaries if any(k in x for k in (
        "Epicenter", "OriginTime", "EarthquakeMagnitude", "FocalDepth", "EarthquakeNo"
    ))), root if isinstance(root, dict) else {})

    epicenter = first_value(info, ["Epicenter", "EpicenterInfo"]) or {}
    if not isinstance(epicenter, dict):
        epicenter = {}

    magnitude_obj = first_value(info, ["EarthquakeMagnitude", "Magnitude"]) or {}
    if not isinstance(magnitude_obj, dict):
        magnitude_obj = {"MagnitudeValue": magnitude_obj}

    origin = first_value(info, ["OriginTime", "EarthquakeTime", "OriginTimeUTC"])
    latitude = first_value(epicenter, ["EpicenterLatitude", "Latitude"]) or first_value(info, ["EpicenterLatitude", "Latitude"]) or first_value(root, ["EpicenterLatitude", "Latitude"])
    longitude = first_value(epicenter, ["EpicenterLongitude", "Longitude"]) or first_value(info, ["EpicenterLongitude", "Longitude"]) or first_value(root, ["EpicenterLongitude", "Longitude"])
    location = first_value(info, ["EpicenterLocation", "Location", "ReportContent", "EarthquakeLocation"]) or first_value(epicenter, ["地點", "Location", "EpicenterLocation"])
    depth = first_value(info, ["FocalDepth", "Depth", "FocalDepthValue"]) or first_value(root, ["FocalDepth", "Depth", "FocalDepthValue"])
    magnitude = first_value(magnitude_obj, ["MagnitudeValue", "Magnitude", "Value"]) or first_value(info, ["MagnitudeValue", "Magnitude"]) or first_value(root, ["MagnitudeValue", "Magnitude"])
    event_no = first_value(info, ["EarthquakeNo", "ReportNo", "Identifier", "EventID"])
    event_id = str(event_no or f"{event_key}-{origin}-{latitude}-{longitude}")

    # If a product stores location or parameters deeper in its structure, use a conservative fallback.
    if location is None:
        location = first_value(root, ["Location", "ReportContent", "EarthquakeLocation"])
    report_url = first_value(root, ["Web", "ReportURL", "ReportUrl", "報告網址"])

    return {
        "id": event_id,
        "eventType": dataset_name,
        "eventTypeKey": event_key,
        "originTime": str(origin) if origin else None,
        "latitude": as_float(latitude),
        "longitude": as_float(longitude),
        "location": str(location) if location else None,
        "depth": as_float(depth),
        "magnitude": as_float(magnitude),
        "intensities": extract_intensities(root),
        "reportUrl": str(report_url) if report_url else "https://www.cwa.gov.tw/V8/C/E/index.html",
        "raw": root,
    }


def get_dataset(session, dataset_id):
    url = f"{BASE}/{dataset_id}"
    params = {"Authorization": API_KEY, "format": "JSON"}
    last_error = None
    for attempt in range(1, 5):
        try:
            response = session.get(url, params=params, timeout=TIMEOUT)
            if response.status_code == 429:
                wait = min(5 * attempt, 30)
                print(f"{dataset_id}: rate limited; wait {wait}s")
                time.sleep(wait)
                continue
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and payload.get("success") is False:
                raise RuntimeError(f"{dataset_id}: {payload}")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(min(3 * attempt, 12))
    raise RuntimeError(f"{dataset_id} failed: {last_error}")


def extract_records(payload):
    records = payload.get("records", {}) if isinstance(payload, dict) else {}
    found = []
    # Prefer the common CWA collection keys; otherwise find record-like objects conservatively.
    for key in ("Earthquake", "earthquake", "Earthquakes", "EarthquakeInfo", "Record"):
        value = records.get(key) if isinstance(records, dict) else None
        if isinstance(value, list):
            found.extend(value)
        elif isinstance(value, dict):
            found.append(value)
    if not found and isinstance(records, dict):
        for value in records.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                found.extend(value)
    if not found and isinstance(records, list):
        found = records
    return found


def main():
    session = requests.Session()
    all_events = {}
    dataset_status = []

    for dataset_id, title, key in DATASETS:
        print(f"Fetching {dataset_id} {title}...")
        try:
            payload = get_dataset(session, dataset_id)
            records = extract_records(payload)
            count = 0
            for record in records:
                event = normalize_event(record, title, key)
                # Ignore entries without an event time and coordinates; do not create fake map pins.
                if not event["originTime"]:
                    continue
                all_events[event["id"]] = event
                count += 1
            dataset_status.append({
                "id": dataset_id, "name": title, "success": True,
                "recordCount": count,
            })
            print(f"{dataset_id}: normalized {count} records")
        except Exception as exc:
            dataset_status.append({
                "id": dataset_id, "name": title, "success": False,
                "error": str(exc),
            })
            print(f"WARNING {dataset_id}: {exc}")

    events = list(all_events.values())
    events.sort(key=lambda x: x.get("originTime") or "", reverse=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "success": bool(events),
        "source": "中央氣象署開放資料平台",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "eventCount": len(events),
        "datasetStatus": dataset_status,
        "earthquakes": events,
        "notes": [
            "此資料檔為排程擷取快照。",
            "歷史資料範圍依各官方資料集實際回傳內容，不代表完整地震目錄。",
            "官方資料未提供的欄位保持 null，不以示範值補齊。",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Saved {len(events)} unique events to {OUT}")
    if not events:
        raise RuntimeError("No earthquake events were normalized. Check CWA API key and current dataset schema.")


if __name__ == "__main__":
    main()
