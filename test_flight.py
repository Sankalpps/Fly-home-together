import json, sys, os
# Adjust import path
project_root = os.path.abspath(os.path.dirname(__file__))
# Add root (project folder) to sys.path
sys.path.append(project_root)
# Import settings and gateway builder
from backend.config import get_settings
from backend.gateway.flights import build_flight_gateway

settings = get_settings()
gateway = build_flight_gateway(settings)
flights = gateway.get_airport_departures('DEL', '2026-11-10')
print('Number of flights:', len(flights))
print(json.dumps(flights[:2], indent=2))
