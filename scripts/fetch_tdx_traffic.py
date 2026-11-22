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

TOKEN_URL = (
    "https://tdx.transportdata.tw/auth/realms/"
    "TDXConnect/protocol/openid-connect/token"
)

TRAFFIC_URL = (
    f"{BASE_URL}/Road/Traffic/Live/City/Taichung"
)

GEOMETRY_URL = (
    f"{BASE_URL}/Road/Link/Shape/Geometry/WKT"
)

TRAFFIC_FILE = Path("data/traffic-city-Taichung.json")
GEOMETRY_FILE = Path("data/traffic-city-Taichung-geometry.json")


def get_token(session):
    response = session.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        timeout=30,
    )

    response.raise_for_status()

    token = response.json().get("access_token")

    if not token:
        raise RuntimeError("TDX access token missing")

    return token


def fetch_traffic(session, headers):
    response = session.get(
        TRAFFIC_URL,
        params={
            "$top": "200",
            "$format": "JSON",
        },
        headers=headers,
        timeout=45,
    )

    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected traffic response")

    records = payload.get("LiveTraffics", [])

    if not isinstance(records, list):
        raise RuntimeError("LiveTraffics is not an array")

    result = {
        "success": True,
        "source": "TDX",
        "dataset": "City Live Traffic",
        "city": "Taichung",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "officialUpdateTime": payload.get("UpdateTime"),
        "updateInterval": payload.get("UpdateInterval"),
        "count": len(records),
        "records": records,
    }

    TRAFFIC_FILE.parent.mkdir(parents=True, exist_ok=True)

    TRAFFIC_FILE.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    print("Traffic records:", len(records))

    return records


def extract_link_ids(records):
    link_ids = []

    for record in records:
        for item in record.get("LinkIDs", []):
            if isinstance(item, dict):
                link_id = item.get("LinkID")
                if link_id:
                    link_ids.append(str(link_id))

    # 去除重複 LinkID，保留原順序
    return list(dict.fromkeys(link_ids))


def fetch_geometry(session, headers, link_ids):
    result = {
        "success": False,
        "source": "TDX",
        "dataset": "Road Link Geometry WKT",
        "city": "Taichung",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "requestedLinkCount": len(link_ids),
        "responseCount": 0,
        "responses": [],
    }

    if not link_ids:
        result["error"] = "No LinkIDs found in traffic records"
        return result

    # 分批查詢，避免單次請求過大
    batches = [
        link_ids[i:i + 100]
        for i in range(0, len(link_ids), 100)
    ]

    print("Unique LinkIDs:", len(link_ids))
    print("Geometry batches:", len(batches))

    for index, batch in enumerate(batches, start=1):
        print(
            f"Fetching geometry batch {index}/{len(batches)} "
            f"({len(batch)} LinkIDs)"
        )

        try:
            response = session.post(
                GEOMETRY_URL,
                json=batch,
                headers=headers,
                timeout=60,
            )

            print("Geometry HTTP:", response.status_code)
            response.raise_for_status()

            try:
                payload = response.json()
            except ValueError:
                payload = {
                    "rawText": response.text[:5000]
                }

            result["responses"].append({
                "batch": index,
                "requestedLinkIDs": batch,
                "httpStatus": response.status_code,
                "data": payload,
            })

            result["responseCount"] += 1

        except Exception as error:
            print(
                f"Geometry batch {index} failed: {error}",
                file=sys.stderr,
            )

            result["responses"].append({
                "batch": index,
                "requestedLinkIDs": batch,
                "error": str(error),
            })

        if index < len(batches):
            time.sleep(1)

    successful = all(
        "data" in item
        for item in result["responses"]
    )

    result["success"] = successful

    if not successful:
        result["error"] = "One or more geometry batches failed"

    return result


def main():
    session = requests.Session()

    print("Requesting TDX token...")
    token = get_token(session)

    print("TDX authentication successful.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    print("Fetching Taichung live traffic...")
    records = fetch_traffic(session, headers)

    print("Extracting road LinkIDs...")
    link_ids = extract_link_ids(records)

    print("Fetching real road geometry...")
    geometry = fetch_geometry(
        session,
        headers,
        link_ids,
    )

    GEOMETRY_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    GEOMETRY_FILE.write_text(
        json.dumps(
            geometry,
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )

    print("========== FINAL RESULT ==========")
    print("Traffic records:", len(records))
    print("Unique LinkIDs:", len(link_ids))
    print("Geometry success:", geometry["success"])
    print("Geometry responses:", geometry["responseCount"])
    print("Geometry output:", GEOMETRY_FILE)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("TDX fetch failed:", error, file=sys.stderr)
        raise
