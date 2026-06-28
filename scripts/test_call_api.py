import urllib.request
import json

url = "http://localhost:8787/api/telephony/live/test-call"
payload = {
    "phone_number": "+15513326220",
    "operator": "Jimmy",
    "confirmation": "LIVE CALL"
}

req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req) as resp:
        print("Response:", resp.read().decode())
except urllib.error.HTTPError as e:
    print("HTTP Error:", e.code)
    print("Response payload:", e.read().decode())
except Exception as e:
    print("Error:", e)
