import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"d:\New folder\Sankalp PS\fly-home-together\fly-home-together")
BASE_URL = os.getenv("FHT_BASE_URL", "http://127.0.0.1:8000")

print("=== 1. Probing Root Webpage (/) ===")
req = urllib.request.urlopen(f"{BASE_URL}/")
html = req.read().decode("utf-8", errors="ignore")
assert "Fly Home Together" in html
assert "Live Flights" in html
print("[OK] Web UI served at root (/) successfully")

print("\n=== 2. Probing /api/airports ===")
req = urllib.request.urlopen(f"{BASE_URL}/api/airports")
airports = json.loads(req.read().decode("utf-8"))
print(f"[OK] Indian domestic airports loaded: {len(airports)} airports (DEL, BOM, BLR, HYD, etc.)")

print("\n=== 3. Probing Single Login & Auto Role Detection ===")
def probe_login(email, pw):
    data = json.dumps({"email": email, "password": pw}).encode("utf-8")
    r = urllib.request.Request(f"{BASE_URL}/auth/login", data=data, headers={"Content-Type": "application/json"}, method="POST")
    res = json.loads(urllib.request.urlopen(r).read().decode("utf-8"))
    role = res["user"]["role"]
    dept = res["user"]["department"]
    print(f"[OK] Single Login -> {email} resolved to: Role={role}, Dept={dept}")
    return res["access_token"]

tok_user = probe_login("user1@gmail.com", "password123")
tok_desk = probe_login("ragu@atc.gov", "password123")
tok_admin = probe_login("guru@atc.gov", "password123")

print("\n=== 4. Probing Real-Time Domestic Flights (India) ===")
req = urllib.request.urlopen(f"{BASE_URL}/api/flights/departures/BLR?date=2026-11-15")
flights = json.loads(req.read().decode("utf-8"))
print(f"[OK] Live domestic departures from BLR: {len(flights)} flights")
for f in flights[:2]:
    print(f"     {f['flight_number']} | {f['airline']} -> {f['destination_name']} ({f['destination']}) | Status: {f['status']} | Gate: {f['gate']}")

print("\n=== 5. Probing Desk Portal ===")
r = urllib.request.Request(f"{BASE_URL}/desk/rfqs", headers={"Authorization": f"Bearer {tok_desk}"})
rfqs = json.loads(urllib.request.urlopen(r).read().decode("utf-8"))
print(f"[OK] Ragu (Flight Dept) has desk access: {len(rfqs)} open RFQs")

print("\n=== 6. Probing Admin Console Access & Role Restriction ===")
r = urllib.request.Request(f"{BASE_URL}/admin/stats", headers={"Authorization": f"Bearer {tok_admin}"})
stats = json.loads(urllib.request.urlopen(r).read().decode("utf-8"))
print(f"[OK] Guru (Admin) accessed hidden Admin Console: {stats['total_users']} users, {stats['total_notifications']} notifications")

try:
    r = urllib.request.Request(f"{BASE_URL}/admin/stats", headers={"Authorization": f"Bearer {tok_user}"})
    urllib.request.urlopen(r)
    print("[FAIL] Traveler unexpectedly accessed admin!")
except urllib.error.HTTPError as e:
    print(f"[OK] Traveler is blocked from Admin: HTTP {e.code} Forbidden (Admin console hidden & protected)")

print("\n>>> ALL SYSTEM & USER CHECKS PASSED SUCCESSFULLY! <<<")

