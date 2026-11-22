import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CLIENT_ID = os.environ["TDX_CLIENT_ID"]
CLIENT_SECRET = os.environ["TDX_CLIENT_SECRET"]
BASE_URL = "https://tdx.transportdata.tw/api/basic/v2"
TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
GEOMETRY_URL = f"{BASE_URL}/Road/Link/Shape/Geometry/WKT"

CITIES = [
    ("Taipei", "臺北市"), ("NewTaipei", "新北市"), ("Keelung", "基隆市"),
    ("Taoyuan", "桃園市"), ("Hsinchu", "新竹市"), ("HsinchuCounty", "新竹縣"),
    ("MiaoliCounty", "苗栗縣"), ("Taichung", "臺中市"), ("ChanghuaCounty", "彰化縣"),
    ("NantouCounty", "南投縣"), ("YunlinCounty", "雲林縣"), ("Chiayi", "嘉義市"),
    ("ChiayiCounty", "嘉義縣"), ("Tainan", "臺南市"), ("Kaohsiung", "高雄市"),
    ("PingtungCounty", "屏東縣"), ("YilanCounty", "宜蘭縣"), ("HualienCounty", "花蓮縣"),
    ("TaitungCounty", "臺東縣"), ("PenghuCounty", "澎湖縣"),
    ("KinmenCounty", "金門縣"), ("LienchiangCounty", "連江縣"),
]
TRAFFIC_FILE = Path("data/traffic-taiwan.json")
GEOMETRY_FILE = Path("data/traffic-taiwan-geometry.json")
PAGE_SIZE = 1000
BATCH_SIZE = 20
MAX_GEOMETRY_RETRIES = 6



def get_token(session):
    r = session.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError("TDX access token missing")
    return token


def fetch_city(session, headers, code, name):
    url = f"{BASE_URL}/Road/Traffic/Live/City/{code}"
    records, update_time, interval, skip = [], None, None, 0
    while True:
        r = session.get(
            url,
            params={"$top": str(PAGE_SIZE), "$skip": str(skip), "$format": "JSON"},
            headers=headers,
            timeout=60,
        )
        r.raise_for_status()
        payload = r.json()
        if isinstance(payload, dict):
            page = payload.get("LiveTraffics", [])
            update_time = payload.get("UpdateTime", update_time)
            interval = payload.get("UpdateInterval", interval)
        elif isinstance(payload, list):
            page = payload
        else:
            raise RuntimeError(f"{name}: unexpected response type")
        if not isinstance(page, list):
            raise RuntimeError(f"{name}: LiveTraffics is not an array")
        records.extend(page)
        if len(page) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
        time.sleep(0.15)
    return {
        "city": code, "cityName": name, "officialUpdateTime": update_time,
        "updateInterval": interval, "count": len(records), "records": records,
    }


def collect_link_ids(cities):
    ids = []
    for city in cities:
        for record in city["records"]:
            for item in record.get("LinkIDs", []) or []:
                if isinstance(item, dict) and item.get("LinkID"):
                    ids.append(str(item["LinkID"]))
    return list(dict.fromkeys(ids))


def load_existing_geometry():
    if not GEOMETRY_FILE.exists():
        return {
            "success": True, "source": "TDX", "dataset": "Taiwan Road Link Geometry WKT",
            "fetchedAt": None, "requestedLinkCount": 0, "responseCount": 0,
            "returnedGeometryCount": 0, "responses": [],
        }
    try:
        data = json.loads(GEOMETRY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("responses"), list):
            return data
    except Exception:
        pass
    return {
        "success": True, "source": "TDX", "dataset": "Taiwan Road Link Geometry WKT",
        "fetchedAt": None, "requestedLinkCount": 0, "responseCount": 0,
        "returnedGeometryCount": 0, "responses": [],
    }


def fetch_missing_geometry(session, headers, link_ids, geometry):
    known = set()
    for batch in geometry.get("responses", []):
        data = batch.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("LinkID"):
                    known.add(str(item["LinkID"]))

    missing = [x for x in link_ids if x not in known]
    print(f"Existing geometries: {len(known)}; new LinkIDs: {len(missing)}")

    batches = [missing[i:i + BATCH_SIZE] for i in range(0, len(missing), BATCH_SIZE)]
    failed_batches = []

    for n, batch in enumerate(batches, 1):
        succeeded = False

        for attempt in range(1, MAX_GEOMETRY_RETRIES + 1):
            try:
                response = session.post(
                    GEOMETRY_URL,
                    json=batch,
                    headers=headers,
                    timeout=90,
                )

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait_seconds = float(retry_after) if retry_after else min(15 * attempt, 90)
                    except (TypeError, ValueError):
                        wait_seconds = min(15 * attempt, 90)

                    print(
                        f"Geometry rate limited (429), batch {n}/{len(batches)}, "
                        f"attempt {attempt}/{MAX_GEOMETRY_RETRIES}; "
                        f"waiting {wait_seconds:.0f}s"
                    )
                    time.sleep(wait_seconds)
                    continue

                response.raise_for_status()
                payload = response.json()

                if not isinstance(payload, list):
                    raise RuntimeError(f"Geometry batch {n} returned non-array JSON")

                geometry["responses"].append({
                    "batch": len(geometry["responses"]) + 1,
                    "data": payload,
                })
                succeeded = True
                print(f"Geometry batch {n}/{len(batches)}: {len(payload)} returned")
                break

            except requests.exceptions.HTTPError as error:
                status = getattr(error.response, "status_code", None)
                if status == 429:
                    # Defensive fallback in case raise_for_status is reached.
                    wait_seconds = min(15 * attempt, 90)
                    print(f"HTTP 429; waiting {wait_seconds}s before retry")
                    time.sleep(wait_seconds)
                    continue

                print(f"Geometry batch {n} failed with HTTP error: {error}", file=sys.stderr)
                break

            except Exception as error:
                wait_seconds = min(5 * attempt, 30)
                print(
                    f"Geometry batch {n} attempt {attempt} failed: {error}; "
                    f"waiting {wait_seconds}s",
                    file=sys.stderr,
                )
                if attempt < MAX_GEOMETRY_RETRIES:
                    time.sleep(wait_seconds)

        if not succeeded:
            failed_batches.append({
                "requestedLinkIDs": batch,
                "error": "Rate limited or request failed after retries",
            })
            print(
                f"WARNING: geometry batch {n}/{len(batches)} deferred; "
                "it will be retried on the next scheduled run.",
                file=sys.stderr,
            )

        # Keep a pause between requests to reduce pressure on TDX.
        time.sleep(3)

    geometry["success"] = True
    geometry["fetchedAt"] = datetime.now(timezone.utc).isoformat()
    geometry["requestedLinkCount"] = len(set(known).union(link_ids))
    geometry["returnedGeometryCount"] = sum(
        len(x.get("data", []))
        for x in geometry["responses"]
        if isinstance(x.get("data"), list)
    )
    geometry["pendingLinkCount"] = max(
        0, len(set(link_ids) - known) -
        sum(
            len(x.get("data", []))
            for x in geometry["responses"][-len(batches):]
            if isinstance(x.get("data"), list)
        )
    ) if batches else 0
    geometry["deferredBatchCount"] = len(failed_batches)
    geometry["deferredBatches"] = failed_batches[-20:]
    return geometry


def main():
    session = requests.Session()
    token = get_token(session)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    cities, failures = [], []

    for code, name in CITIES:
        try:
            print(f"Fetching {name}...")
            result = fetch_city(session, headers, code, name)
            cities.append(result)
            print(f"{name}: {result['count']} records")
        except Exception as error:
            print(f"WARNING {name}: {error}", file=sys.stderr)
            failures.append({"city": code, "cityName": name, "error": str(error)})

    if not cities:
        raise RuntimeError("All city traffic requests failed")

    link_ids = collect_link_ids(cities)
    traffic = {
        "success": True, "source": "TDX", "dataset": "Taiwan City Live Traffic",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "cityCount": len(cities), "totalCityCount": len(CITIES),
        "totalCount": sum(c["count"] for c in cities),
        "uniqueLinkCount": len(link_ids), "failedCities": failures, "cities": cities,
    }
    TRAFFIC_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRAFFIC_FILE.write_text(json.dumps(traffic, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    geometry = load_existing_geometry()
    try:
        geometry = fetch_missing_geometry(session, headers, link_ids, geometry)
    except Exception as error:
        print(f"WARNING: geometry update incomplete; keeping existing geometry: {error}", file=sys.stderr)
        geometry["success"] = True
        geometry["lastError"] = str(error)
        geometry["fetchedAt"] = datetime.now(timezone.utc).isoformat()

    GEOMETRY_FILE.write_text(
        json.dumps(geometry, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    print("========== TAIWAN TRAFFIC RESULT ==========")
    print(f"Cities successful: {len(cities)}/{len(CITIES)}")
    print(f"Failed cities: {len(failures)}")
    print(f"Traffic records: {traffic['totalCount']}")
    print(f"Unique LinkIDs: {len(link_ids)}")
    print(f"Road geometries cached: {geometry['returnedGeometryCount']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"TDX fetch failed: {exc}", file=sys.stderr)
        raise
