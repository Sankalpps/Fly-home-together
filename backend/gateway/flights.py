"""Flight data gateway — Indian domestic flights only.

Implementations:
  SimulatedFlightGateway  — deterministic fake data, no config needed (default)
  AeroDataBoxGateway      — real data via RapidAPI AeroDataBox
                            (free dev tier; set AERODATABOX_API_KEY env var)
"""
from __future__ import annotations

import hashlib
import json
import random
import urllib.request
from datetime import date
from typing import Optional

# ---------------------------------------------------------------------------
# Full DGCA-registered Indian domestic airports
# ---------------------------------------------------------------------------
INDIA_AIRPORTS: dict[str, str] = {
    "DEL": "Indira Gandhi Intl, Delhi",
    "BOM": "Chhatrapati Shivaji Maharaj Intl, Mumbai",
    "BLR": "Kempegowda Intl, Bengaluru",
    "MAA": "Chennai Intl",
    "HYD": "Rajiv Gandhi Intl, Hyderabad",
    "CCU": "Netaji Subhas Chandra Bose Intl, Kolkata",
    "COK": "Cochin Intl",
    "PNQ": "Pune Airport",
    "AMD": "Sardar Vallabhbhai Patel Intl, Ahmedabad",
    "GOI": "Goa Intl (Dabolim)",
    "GAU": "Lokpriya Gopinath Bordoloi Intl, Guwahati",
    "IXC": "Chandigarh Airport",
    "JAI": "Jaipur Intl",
    "LKO": "Chaudhary Charan Singh Intl, Lucknow",
    "PAT": "Jay Prakash Narayan Intl, Patna",
    "BBI": "Biju Patnaik Intl, Bhubaneswar",
    "VTZ": "Visakhapatnam Airport",
    "IXR": "Birsa Munda Airport, Ranchi",
    "NAG": "Dr. Babasaheb Ambedkar Intl, Nagpur",
    "SXR": "Sheikh Ul-Alam Intl, Srinagar",
    "IXL": "Kushok Bakula Rimpochee, Leh",
    "DED": "Jolly Grant Airport, Dehradun",
    "BDQ": "Vadodara Airport",
    "IDR": "Devi Ahilyabai Holkar, Indore",
    "BHO": "Raja Bhoj Airport, Bhopal",
    "VNS": "Lal Bahadur Shastri Intl, Varanasi",
    "ATQ": "Sri Guru Ram Dass Jee Intl, Amritsar",
    "TRV": "Trivandrum Intl",
    "IXE": "Mangaluru Intl",
    "PBD": "Porbandar Airport",
    "JDH": "Jodhpur Airport",
    "JGA": "Jamnagar Airport",
    "BHU": "Bhavnagar Airport",
    "DIU": "Diu Airport",
    "AGR": "Agra Airport",
    "GWL": "Gwalior Airport",
    "JLR": "Jabalpur Airport",
    "RPR": "Swami Vivekananda Airport, Raipur",
    "KTU": "Kota Airport",
    "UDR": "Maharana Pratap Airport, Udaipur",
    "JSA": "Jaisalmer Airport",
    "BKB": "Bikaner Airport",
    "JRH": "Jorhat Airport",
    "DIB": "Dibrugarh Airport",
    "IMF": "Imphal Airport",
    "AGX": "Agatti Airport",
    "IXM": "Madurai Airport",
    "CJB": "Coimbatore Intl",
    "TRZ": "Tiruchirappalli Intl",
    "IXZ": "Port Blair Airport",
    "MYQ": "Mysuru Airport",
    "HBX": "Hubli Airport",
    "BEP": "Ballari Airport",
    "RJA": "Rajahmundry Airport",
    "GOP": "Gorakhpur Airport",
    "AJL": "Lengpui Airport, Aizawl",
    "TEZ": "Tezpur Airport",
    "TCR": "Tuticorin Airport",
    "PNY": "Pondicherry Airport",
    "VGA": "Vijayawada Airport",
    "SAG": "Shirdi Airport",
    "IXS": "Silchar Airport",
    "ZER": "Zero Airport, Arunachal",
    "SSA": "Silchar Airport (Kumbhirgram)",
    "KNU": "Kanpur Airport",
    "GAY": "Gaya Airport",
    "IXG": "Belgaum Airport",
    "IXU": "Aurangabad Airport",
    "NMB": "Daman Airport",
    "PYB": "Jeypore Airport",
}

DOMESTIC_AIRLINES = [
    "IndiGo", "Air India", "Akasa Air", "SpiceJet",
    "Air India Express", "Star Air",
]

FLIGHT_STATUSES = ["On Time", "Delayed", "Boarding", "Departed", "Arrived", "Cancelled"]

_AIRLINE_PREFIXES = {
    "IndiGo": "6E", "Air India": "AI", "Akasa Air": "QP",
    "SpiceJet": "SG", "Air India Express": "IX", "Star Air": "OG",
}


# ---------------------------------------------------------------------------
# Abstract gateway
# ---------------------------------------------------------------------------

class FlightDataGateway:
    """Interface — swap implementations via build_flight_gateway()."""

    def get_airport_departures(self, iata: str, date_str: str) -> list[dict]:
        raise NotImplementedError

    def get_flight_status(self, flight_number: str, date_str: str) -> Optional[dict]:
        raise NotImplementedError

    def get_routes(self, origin: str) -> list[str]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Simulated gateway (no config needed)
# ---------------------------------------------------------------------------

class SimulatedFlightGateway(FlightDataGateway):
    """Deterministic fake data for development and tests."""

    def get_airport_departures(self, iata: str, date_str: str) -> list[dict]:
        iata = iata.upper()
        seed_int = int(hashlib.sha256(f"{iata}{date_str}".encode()).hexdigest(), 16) % (2 ** 32)
        rng = random.Random(seed_int)
        destinations = [k for k in INDIA_AIRPORTS if k != iata]
        rng.shuffle(destinations)
        flights = []
        for i, dest in enumerate(destinations[:12]):
            airline = DOMESTIC_AIRLINES[i % len(DOMESTIC_AIRLINES)]
            prefix = _AIRLINE_PREFIXES.get(airline, "ZZ")
            flight_no = f"{prefix}-{rng.randint(100, 999)}"
            hour = 6 + (i * 90) // 60
            minute = (i * 90) % 60
            status = rng.choice(["On Time", "On Time", "On Time", "Delayed", "Boarding"])
            delay = rng.randint(10, 60) if status == "Delayed" else 0
            flights.append({
                "flight_number": flight_no,
                "airline": airline,
                "origin": iata,
                "destination": dest,
                "destination_name": INDIA_AIRPORTS.get(dest, dest),
                "scheduled_departure": f"{date_str}T{hour:02d}:{minute:02d}:00",
                "estimated_departure": f"{date_str}T{hour:02d}:{(minute + delay) % 60:02d}:00",
                "terminal": rng.choice(["T1", "T2", "T3"]),
                "gate": f"{rng.choice('ABCDE')}{rng.randint(1, 20)}",
                "status": status,
                "delay_minutes": delay,
                "source": "simulated",
            })
        return flights

    def get_flight_status(self, flight_number: str, date_str: str) -> Optional[dict]:
        seed_int = int(hashlib.sha256(f"{flight_number}{date_str}".encode()).hexdigest(), 16) % (2 ** 32)
        rng = random.Random(seed_int)
        return {
            "flight_number": flight_number,
            "status": rng.choice(FLIGHT_STATUSES),
            "delay_minutes": rng.randint(0, 60),
            "terminal": rng.choice(["T1", "T2", "T3"]),
            "gate": f"{rng.choice('ABCD')}{rng.randint(1, 15)}",
            "source": "simulated",
        }

    def get_routes(self, origin: str) -> list[str]:
        return [k for k in INDIA_AIRPORTS if k != origin.upper()]


# ---------------------------------------------------------------------------
# AeroDataBox gateway (real data, free dev tier)
# ---------------------------------------------------------------------------

class AeroDataBoxGateway(FlightDataGateway):
    """Real Indian domestic flight data via RapidAPI AeroDataBox.

    Free developer tier available at:
    https://rapidapi.com/aedbx-aedbx-default/api/aerodatabox
    Set env var AERODATABOX_API_KEY to activate.
    """

    _BASE = "https://aerodatabox.p.rapidapi.com"

    def __init__(self, api_key: str):
        self._headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com",
        }

    def _get(self, path: str):
        req = urllib.request.Request(
            self._BASE + path, headers=self._headers
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print(f"[AeroDataBox] Error: {e}")
            return {}

    def get_airport_departures(self, iata: str, date_str: str) -> list[dict]:
        result = []
        for start_time, end_time in (("T00:00", "T11:59"), ("T12:00", "T23:59")):
            path = (
                f"/flights/airports/iata/{iata}/{date_str}{start_time}/{date_str}{end_time}"
                "?withLeg=true&direction=Departure&withCancelled=true"
                "&withCodeshared=false&withCargo=false&withPrivate=false"
            )
            data = self._get(path)
            departures = data.get("departures", []) if isinstance(data, dict) else []
            for f in departures:
                arrival = f.get("arrival", {})
                departure = f.get("departure", {})
                dest_iata = arrival.get("airport", {}).get("iata", "")
                if dest_iata not in INDIA_AIRPORTS:
                    continue  # domestic only
                result.append({
                    "flight_number": f.get("number", ""),
                    "airline": f.get("airline", {}).get("name", ""),
                    "origin": iata,
                    "destination": dest_iata,
                    "destination_name": INDIA_AIRPORTS.get(dest_iata, dest_iata),
                    "scheduled_departure": departure.get("scheduledTime", {}).get("local", ""),
                    "estimated_departure": departure.get("revisedTime", {}).get(
                        "local", departure.get("scheduledTime", {}).get("local", "")),
                    "terminal": departure.get("terminal", ""),
                    "gate": departure.get("gate", ""),
                    "status": f.get("status", ""),
                    "delay_minutes": departure.get("delay", 0) or 0,
                    "source": "aerodatabox",
                })
        return result

    def get_flight_status(self, flight_number: str, date_str: str) -> Optional[dict]:
        clean = flight_number.replace("-", "").replace(" ", "")
        data = self._get(f"/flights/number/{clean}/{date_str}")
        if not data:
            return None
        f = data[0] if isinstance(data, list) and data else data
        m = f.get("movement", {})
        return {
            "flight_number": flight_number,
            "status": f.get("status", ""),
            "delay_minutes": m.get("delay", 0) or 0,
            "terminal": m.get("terminal", ""),
            "gate": m.get("gate", ""),
            "source": "aerodatabox",
        }

    def get_routes(self, origin: str) -> list[str]:
        return [k for k in INDIA_AIRPORTS if k != origin.upper()]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_flight_gateway(settings) -> FlightDataGateway:
    if settings.flight_api_enabled:
        print("[FlightGateway] AeroDataBox (real Indian domestic flight data)")
        return AeroDataBoxGateway(settings.AERODATABOX_API_KEY)
    print("[FlightGateway] SimulatedFlightGateway (set AERODATABOX_API_KEY for real data)")
    return SimulatedFlightGateway()
