"""Tests for the Indian domestic flight data gateway."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from backend.gateway.flights import (
    DOMESTIC_AIRLINES, INDIA_AIRPORTS, SimulatedFlightGateway,
)


@pytest.fixture
def gateway():
    return SimulatedFlightGateway()


class TestIndiaAirports:
    def test_has_at_least_50_airports(self):
        assert len(INDIA_AIRPORTS) >= 50

    def test_all_metro_hubs_present(self):
        for iata in ["DEL", "BOM", "BLR", "MAA", "HYD", "CCU", "COK", "AMD"]:
            assert iata in INDIA_AIRPORTS, f"Missing metro: {iata}"

    def test_tier_2_cities_present(self):
        for iata in ["PNQ", "NAG", "LKO", "JAI", "PAT", "BBI"]:
            assert iata in INDIA_AIRPORTS, f"Missing tier-2: {iata}"


class TestSimulatedGateway:
    def test_departures_returns_non_empty_list(self, gateway):
        flights = gateway.get_airport_departures("BLR", "2026-11-15")
        assert len(flights) > 0

    def test_all_returned_destinations_are_domestic(self, gateway):
        flights = gateway.get_airport_departures("DEL", "2026-11-15")
        for f in flights:
            assert f["destination"] in INDIA_AIRPORTS, \
                f"Non-domestic destination: {f['destination']}"

    def test_origin_not_in_destinations(self, gateway):
        flights = gateway.get_airport_departures("BOM", "2026-11-15")
        for f in flights:
            assert f["destination"] != "BOM"

    def test_required_fields_present(self, gateway):
        required = {
            "flight_number", "airline", "origin", "destination",
            "destination_name", "scheduled_departure", "terminal",
            "gate", "status", "delay_minutes", "source",
        }
        for f in gateway.get_airport_departures("HYD", "2026-11-15"):
            missing = required - f.keys()
            assert not missing, f"Missing fields: {missing}"

    def test_all_airlines_are_domestic_carriers(self, gateway):
        flights = gateway.get_airport_departures("BLR", "2026-11-15")
        for f in flights:
            assert f["airline"] in DOMESTIC_AIRLINES, \
                f"Unknown airline: {f['airline']}"

    def test_departures_are_deterministic(self, gateway):
        a = gateway.get_airport_departures("HYD", "2026-11-20")
        b = gateway.get_airport_departures("HYD", "2026-11-20")
        assert a == b

    def test_different_dates_give_different_results(self, gateway):
        a = gateway.get_airport_departures("BLR", "2026-11-15")
        b = gateway.get_airport_departures("BLR", "2026-11-20")
        # Flight numbers should differ across dates (deterministic hash changes)
        flights_a = {f["flight_number"] for f in a}
        flights_b = {f["flight_number"] for f in b}
        assert flights_a != flights_b

    def test_flight_status_lookup(self, gateway):
        result = gateway.get_flight_status("6E-204", "2026-11-15")
        assert result is not None
        assert result["source"] == "simulated"
        assert result["status"] in [
            "On Time", "Delayed", "Boarding", "Departed", "Arrived", "Cancelled"
        ]

    def test_routes_excludes_origin(self, gateway):
        routes = gateway.get_routes("BLR")
        assert "BLR" not in routes

    def test_routes_returns_domestic_only(self, gateway):
        for iata in gateway.get_routes("DEL"):
            assert iata in INDIA_AIRPORTS
