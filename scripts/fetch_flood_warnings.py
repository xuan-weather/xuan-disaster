import json
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://fhy.wra.gov.tw/Api/v2/Disaster/Flooding"
OUTPUT = Path("data/flood-warnings.json")
TIMEOUT = 45


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def first(record, keys):
    wanted = {key.lower() for key in keys}
    for key, value in record.items():
        if key.lower() in wanted and value not in (None, ""):
            return value
    return None


def main():
    response = requests.get(
        API_URL,
        headers={"Accept": "application/json", "User-Agent": "Xuan-Disaster/1.0"},
        timeout=TIMEOUT,
    )
    if response.status_code == 401:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        result = {
            "success": False,
            "status": "api_key_required",
            "source": "經濟部水利署防災資訊服務",
            "sourceUrl": API_URL,
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "eventCount": 0,
            "events": [],
            "message": "水利署 FHY 防災 API 需要申請專用 API 金鑰；目前未取得授權，因此無法讀取即時淹水災情。",
        }
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\\n", encoding="utf-8")
        print("FHY API requires an API key (HTTP 401). Wrote an explicit unavailable status; workflow can continue.")
        return
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("FHY response is not a JSON object")

    events = []
    seen = set()
    for item in walk(payload):
        event_id = first(item, ["DisasterFloodingID"])
        if event_id is None:
            continue
        key = str(event_id)
        if key in seen:
            continue
        seen.add(key)
        events.append({
            "id": key,
            "time": first(item, ["Time"]),
            "city": first(item, ["CityName", "CountyName", "CityCode"]),
            "place": first(item, ["Location", "Address", "TownName", "VillageName"]),
            "description": first(item, ["Description", "DisasterDescription", "Content", "Remark"]),
            "source": first(item, ["CategoryCode"]),
            "status": first(item, ["Status", "DisasterStatus"]),
            "raw": item,
        })

    update_time = first(payload, ["UpdataTime", "UpdateTime"])
    if update_time is None:
        for item in walk(payload):
            update_time = first(item, ["UpdataTime", "UpdateTime"])
            if update_time:
                break

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "success": True,
        "source": "經濟部水利署防災資訊服務",
        "sourceUrl": API_URL,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "sourceUpdatedAt": update_time,
        "eventCount": len(events),
        "events": sorted(events, key=lambda x: str(x.get("time") or ""), reverse=True),
        "notes": [
            "本資料為水利署 API 回傳的淹水災情事件。",
            "災情通報不等同淹水預報或官方淹水警戒區域。",
            "欄位以來源實際回傳為準，未提供者保持空值。",
        ],
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Saved {len(events)} flood incident records to {OUTPUT}")


if __name__ == "__main__":
    main()
