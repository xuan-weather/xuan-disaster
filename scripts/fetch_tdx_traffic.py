import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

CLIENT_ID = os.environ["TDX_CLIENT_ID"]
CLIENT_SECRET = os.environ["TDX_CLIENT_SECRET"]

TOKEN_URL = (
    "https://tdx.transportdata.tw/auth/realms/"
    "TDXConnect/protocol/openid-connect/token"
)

DATA_URL = (
    "https://tdx.transportdata.tw/api/basic/v2/"
    "Road/Traffic/Live/City/Taichung"
)


def main():
    print("Requesting TDX access token...")

    token_response = requests.post(
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

    token_response.raise_for_status()
    token = token_response.json()["access_token"]

    print("TDX authentication successful.")
    print("Fetching Taichung traffic data...")

    response = requests.get(
        DATA_URL,
        params={
            "$top": "200",
            "$format": "JSON",
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=45,
    )

    response.raise_for_status()
    payload = response.json()
    print("HTTP STATUS:", response.status_code)
print("CONTENT TYPE:", response.headers.get("Content-Type"))
print("RAW RESPONSE:", response.text[:1500])
print("PAYLOAD TYPE:", type(payload).__name__)

if isinstance(payload, dict):
    print("PAYLOAD KEYS:", list(payload.keys()))

    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = payload.get("value", [])
        if not isinstance(records, list):
            records = []
    else:
        records = []

    result = {
        "success": True,
        "source": "TDX",
        "dataset": "City Live Traffic",
        "city": "Taichung",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "records": records,
    }

    output = Path("data/traffic-city-Taichung.json")
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )

    print(f"Success: saved {len(records)} records.")
    print(f"Output: {output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"TDX fetch failed: {error}", file=sys.stderr)
        raise
