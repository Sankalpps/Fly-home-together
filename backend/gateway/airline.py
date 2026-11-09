"""Airline gateway — re-exports from engine + adds desk-submitted quote support."""
from __future__ import annotations
# The ABC and SimulatedAirlineGateway live in engine.py (preserved as-is).
# We import and re-export them here so the backend package doesn't depend on
# the engine's internal structure changing.
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from engine import AirlineGateway, SimulatedAirlineGateway  # noqa: F401

__all__ = ["AirlineGateway", "SimulatedAirlineGateway"]
