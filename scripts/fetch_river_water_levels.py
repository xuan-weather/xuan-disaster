import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

SOURCE_URL = (
    "https://data.wra.gov.tw/Service/OpenData.aspx"
    "?format=json&id=2D09DB8B-6A1B-485E-88B5-923A462F475C"
)
OUTPUT = Path("data/river-water-levels.json")
TIMEOUT = 45


def norm(key):
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def scalar(value):
    if isinstance(value, dict):
        for key in ("value", "Value", "content", "#text"):
            if key in value:
                return scalar(value[key])
    return value


def walk_lists(value):
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            yield value
        for item in value:
            yield from walk_lists(item)
    elif isinstance(value, dict):
        for child in value.values():
            yield from walk_lists(child)


def pick(record, names):
    wanted = {norm(name) for name in names}
    for key, value in record.items():
        if norm(key) in wanted and value not in (None, ""):
            return scalar(value)
    return None


def number(value):
    value = scalar(value)
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def main():
    response = requests.get(
        SOURCE_URL,
        headers={"User-Agent": "Xuan-Disaster/1.0"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()

    candidates = list(walk_lists(payload))
    if isinstance(payload, list) and payload and all(isinstance(x, dict) for x in payload):
        candidates.insert(0, payload)

    records = []
    seen = set()
    for group in candidates:
        for item in group:
            station_id = pick(item, ["stationid", "stationidentifier", "stationno", "stationcode"])
            level = number(pick(item, ["waterlevel", "water_level", "level", "waterlevelvalue"]))
            observed = pick(item, ["datetime", "recordtime", "observedtime", "time", "checktime"])
            if station_id is None or level is None:
                continue
            station_id = str(station_id).strip()
            key = (station_id, str(observed or ""))
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "stationId": station_id,
                "stationName": str(pick(item, ["stationname", "name", "sitename"]) or station_id),
                "location": pick(item, ["countyname", "cityname", "location", "address", "rivername"]),
                "waterLevel": level,
                "observedAt": str(observed) if observed is not None else None,
                "quality": pick(item, ["checkresult", "checkdesc", "flag", "quality"]),
                "raw": item,
            })

    if not records:
        sample = json.dumps(payload, ensure_ascii=False)[:2500]
        raise RuntimeError(
            "WRA API returned no recognizable station/waterlevel records. "
            "Existing output was preserved. Response sample: " + sample
        )

    records.sort(key=lambda item: item["stationId"])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "success": True,
        "source": "經濟部水利署即時水位資料",
        "sourceUrl": SOURCE_URL,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "recordCount": len(records),
        "stations": records,
        "notes": [
            "水位原始觀測資料最快約每10分鐘更新一次。",
            "原始資料未必經過品管；請檢視觀測時間及品質註記。",
            "水位值不等同洪水警戒或淹水預報。",
        ],
    }
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(records)} WRA water-level stations to {OUTPUT}")


if __name__ == "__main__":
    main()
