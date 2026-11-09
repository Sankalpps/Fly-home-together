import os
import sys
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import (  # noqa: E402
    FESTIVAL_SURGE, next_festival_start, surge_multiplier,
    AirlineGateway, CohortEngine, EngineError, SERVICE_FEE,
    SimulatedAirlineGateway, SimulatedPaymentGateway, market_fare,
)


class NoInventoryGateway(AirlineGateway):
    def request_quotes(self, origin, dest, travel_date, passengers):
        return []


class ExpensiveGateway(AirlineGateway):
    """Quotes that are NOT cheaper than market; the engine must refuse them."""
    def request_quotes(self, origin, dest, travel_date, passengers):
        fare = market_fare(origin, dest, travel_date)
        return [{"airline": "X", "per_seat": fare + 100, "market_fare": fare,
                 "seats_available": passengers + 5, "deposit_pct": 25}]


_ENGINES = []


def tearDownModule():
    for eng in _ENGINES:
        eng.close()


def make_engine(threshold=5, capacity=7, airline=None):
    eng = _build_engine(threshold, capacity, airline)
    _ENGINES.append(eng)
    return eng


def _build_engine(threshold, capacity, airline):
    return CohortEngine(":memory:", airline=airline or SimulatedAirlineGateway(seed=1),
                        payments=SimulatedPaymentGateway(),
                        threshold=threshold, capacity=capacity)


def future(engine, days=10):
    return (engine.now().date() + timedelta(days=days)).isoformat()


def join_n(engine, n, date_s, origin="BLR", dest="DEL", flex=0, prefix="u"):
    out = []
    for i in range(n):
        out.append(engine.join(f"{prefix}{i} Test", f"{prefix}{i}@x.com", origin, dest, date_s, flex))
    return out


class JoinTests(unittest.TestCase):
    def test_creates_cohort_and_places_hold(self):
        e = make_engine()
        m = e.join("Asha K", "asha@x.com", "BLR", "DEL", future(e))
        self.assertEqual(m["status"], "JOINED")
        self.assertGreater(m["hold_amount"], 0)
        self.assertEqual(len(e.list_cohorts()), 1)

    def test_same_route_and_date_share_a_cohort(self):
        e = make_engine(threshold=10, capacity=12)
        join_n(e, 4, future(e))
        self.assertEqual(len(e.list_cohorts()), 1)
        self.assertEqual(e.list_cohorts()[0]["joined"], 4)

    def test_different_routes_do_not_mix(self):
        e = make_engine()
        e.join("A B", "a@x.com", "BLR", "DEL", future(e))
        e.join("C D", "c@x.com", "BLR", "HYD", future(e))
        self.assertEqual(len(e.list_cohorts()), 2)

    def test_flexible_dates_merge_into_fullest_pool(self):
        e = make_engine(threshold=10, capacity=12)
        d = e.now().date() + timedelta(days=10)
        join_n(e, 3, d.isoformat(), prefix="a")                      # pool on day 10
        e.join("Flex One", "flex@x.com", "BLR", "DEL",
               (d + timedelta(days=2)).isoformat(), flex_days=2)    # day 12 +/-2 -> should merge
        cohorts = e.list_cohorts()
        self.assertEqual(len(cohorts), 1)
        self.assertEqual(cohorts[0]["joined"], 4)

    def test_inflexible_far_date_creates_new_pool(self):
        e = make_engine(threshold=10, capacity=12)
        d = e.now().date() + timedelta(days=10)
        e.join("A B", "a@x.com", "BLR", "DEL", d.isoformat())
        e.join("C D", "c@x.com", "BLR", "DEL", (d + timedelta(days=2)).isoformat(), flex_days=1)
        self.assertEqual(len(e.list_cohorts()), 2)

    def test_validation(self):
        e = make_engine()
        d = future(e)
        with self.assertRaises(EngineError):
            e.join("", "a@x.com", "BLR", "DEL", d)
        with self.assertRaises(EngineError):
            e.join("A", "not-an-email", "BLR", "DEL", d)
        with self.assertRaises(EngineError):
            e.join("A", "a@x.com", "BLR", "BLR", d)
        with self.assertRaises(EngineError):
            e.join("A", "a@x.com", "BLR", "ZZZ", d)
        with self.assertRaises(EngineError):
            e.join("A", "a@x.com", "BLR", "DEL", "yesterday")
        with self.assertRaises(EngineError):
            e.join("A", "a@x.com", "BLR", "DEL", e.now().date().isoformat())
        with self.assertRaises(EngineError):
            e.join("A", "a@x.com", "BLR", "DEL", d, flex_days=9)

    def test_duplicate_email_same_route_rejected(self):
        e = make_engine()
        d = future(e)
        e.join("A B", "a@x.com", "BLR", "DEL", d)
        with self.assertRaises(EngineError):
            e.join("A B", "a@x.com", "BLR", "DEL", d)

    def test_idempotent_join_returns_existing_membership(self):
        e = make_engine()
        d = future(e)
        first = e.join("A B", "a@x.com", "BLR", "DEL", d)
        retry = e.join("A B", "a@x.com", "BLR", "DEL", d, idempotent=True)
        self.assertEqual(retry["id"], first["id"])
        self.assertEqual(e.list_cohorts()[0]["joined"], 1)


class RfqAndFinalizeTests(unittest.TestCase):
    def test_rfq_fires_at_threshold(self):
        e = make_engine(threshold=5, capacity=7)
        join_n(e, 4, future(e))
        self.assertEqual(e.list_cohorts()[0]["status"], "OPEN")
        e.join("Fifth", "fifth@x.com", "BLR", "DEL", future(e))
        c = e.list_cohorts()[0]
        self.assertEqual(c["status"], "QUOTED")
        self.assertLess(c["quote"]["per_seat"], c["market_fare"])
        self.assertGreater(c["quote"]["saving_per_seat"], 0)

    def test_full_happy_path_ticketing_and_capture(self):
        e = make_engine(threshold=5, capacity=7)
        members = join_n(e, 5, future(e))
        for m in members:
            e.accept(m["id"])
        c = e.list_cohorts()[0]
        self.assertEqual(c["status"], "TICKETED")
        self.assertEqual(c["ticketed"], 5)
        mv = e.member_view(members[0]["id"])
        self.assertEqual(mv["status"], "TICKETED")
        self.assertTrue(mv["pnr"].startswith("FH"))
        captured = [a for a in e.payments.auths.values() if a["state"] == "captured"]
        self.assertEqual(len(captured), 5)
        self.assertEqual(captured[0]["captured"], c["quote"]["per_seat"] + SERVICE_FEE)

    def test_decline_below_threshold_cancels_and_releases_all_holds(self):
        e = make_engine(threshold=5, capacity=5)
        members = join_n(e, 5, future(e))
        e.decline(members[0]["id"])      # 4 left, below threshold, no waitlist
        for m in members[1:]:
            e.accept(m["id"])
        c = e.list_cohorts()[0]
        self.assertEqual(c["status"], "CANCELLED")
        states = {a["state"] for a in e.payments.auths.values()}
        self.assertEqual(states, {"released"})




    def test_expiry_releases_non_responders_and_cancels(self):
        e = make_engine(threshold=5, capacity=5)
        members = join_n(e, 5, future(e))
        e.accept(members[0]["id"])
        e.advance_clock(25)
        e.tick()
        c = e.list_cohorts()[0]
        self.assertEqual(c["status"], "CANCELLED")
        self.assertEqual(e.member_view(members[1]["id"])["status"], "EXPIRED")
        self.assertEqual(e.member_view(members[0]["id"])["status"], "REFUNDED")
        self.assertEqual({a["state"] for a in e.payments.auths.values()}, {"released"})


    def test_no_airline_inventory_cancels(self):
        e = make_engine(threshold=3, capacity=4, airline=NoInventoryGateway())
        join_n(e, 3, future(e))
        self.assertEqual(e.list_cohorts()[0]["status"], "CANCELLED")
        self.assertEqual({a["state"] for a in e.payments.auths.values()}, {"released"})

    def test_quote_not_cheaper_than_market_is_rejected(self):
        e = make_engine(threshold=3, capacity=4, airline=ExpensiveGateway())
        join_n(e, 3, future(e))
        self.assertEqual(e.list_cohorts()[0]["status"], "CANCELLED")

    def test_accept_over_price_cap_rejected(self):
        e = make_engine(threshold=3, capacity=4)
        d = future(e)
        a = e.join("A", "a@x.com", "BLR", "DEL", d, price_cap=800)   # absurdly low cap
        e.join("B", "b@x.com", "BLR", "DEL", d)
        e.join("C", "c@x.com", "BLR", "DEL", d)
        self.assertEqual(e.list_cohorts()[0]["status"], "QUOTED")
        with self.assertRaises(EngineError):
            e.accept(a["id"])

    def test_cannot_accept_twice_or_before_quote(self):
        e = make_engine(threshold=3, capacity=4)
        d = future(e)
        first = e.join("A", "a@x.com", "BLR", "DEL", d)
        with self.assertRaises(EngineError):
            e.accept(first["id"])           # still OPEN
        e.join("B", "b@x.com", "BLR", "DEL", d)
        e.join("C", "c@x.com", "BLR", "DEL", d)
        e.accept(first["id"])
        with self.assertRaises(EngineError):
            e.accept(first["id"])

    def test_savings_summary(self):
        e = make_engine(threshold=3, capacity=4)
        for m in join_n(e, 3, future(e)):
            e.accept(m["id"])
        s = e.savings_summary()
        self.assertEqual(s["passengers"], 3)
        self.assertGreater(s["total_saved"], 0)
        self.assertEqual(s["platform_revenue"], 3 * SERVICE_FEE)


class BenchAttritionTests(unittest.TestCase):
    """capacity - threshold = backup bench that absorbs drop-outs after a quote."""

    def quoted_pool(self, threshold=5, capacity=7):
        e = make_engine(threshold=threshold, capacity=capacity)
        d = future(e)
        members = join_n(e, threshold, d, prefix="m")
        self.assertEqual(e.list_cohorts()[0]["status"], "QUOTED")
        return e, d, members

    def test_late_joiner_takes_backup_seat_without_hold(self):
        e, d, _ = self.quoted_pool()
        late = e.join("Late One", "late1@x.com", "BLR", "DEL", d)
        self.assertEqual(late["status"], "WAITLIST")
        self.assertEqual(late["hold_amount"], 0)
        self.assertEqual(late["waitlist_position"], 1)
        self.assertEqual(len(e.list_cohorts()), 1)

    def test_bench_full_starts_a_fresh_pool(self):
        e, d, _ = self.quoted_pool(threshold=5, capacity=7)      # bench of 2
        e.join("L1", "l1@x.com", "BLR", "DEL", d)
        e.join("L2", "l2@x.com", "BLR", "DEL", d)
        third = e.join("L3", "l3@x.com", "BLR", "DEL", d)
        self.assertEqual(third["cohort"]["status"], "OPEN")
        self.assertEqual(len(e.list_cohorts()), 2)

    def test_dropout_is_replaced_from_bench_and_pool_still_ticketed(self):
        e, d, members = self.quoted_pool()
        backup = e.join("Backup", "backup@x.com", "BLR", "DEL", d)
        e.decline(members[0]["id"])
        promoted = e.member_view(backup["id"])
        self.assertEqual(promoted["status"], "JOINED")
        self.assertGreater(promoted["hold_amount"], 0)
        for mid in [m["id"] for m in members[1:]] + [backup["id"]]:
            e.accept(mid)
        c = e.list_cohorts()[0]
        self.assertEqual(c["status"], "TICKETED")
        self.assertEqual(c["ticketed"], 5)
        self.assertEqual(e.member_view(backup["id"])["status"], "TICKETED")

    def test_without_bench_a_dropout_cancels_the_pool(self):
        e, d, members = self.quoted_pool(threshold=5, capacity=5)   # no bench
        e.decline(members[0]["id"])
        for m in members[1:]:
            e.accept(m["id"])
        self.assertEqual(e.list_cohorts()[0]["status"], "CANCELLED")

    def test_unused_bench_member_is_released_when_pool_ticketed(self):
        e, d, members = self.quoted_pool()
        backup = e.join("Backup", "backup@x.com", "BLR", "DEL", d)
        for m in members:
            e.accept(m["id"])
        self.assertEqual(e.list_cohorts()[0]["status"], "TICKETED")
        self.assertEqual(e.member_view(backup["id"])["status"], "REFUNDED")

    def test_promotion_never_exceeds_quoted_seat_count(self):
        e, d, members = self.quoted_pool(threshold=5, capacity=8)   # bench of 3
        for i in range(3):
            e.join(f"B{i}", f"b{i}@x.com", "BLR", "DEL", d)
        e.decline(members[0]["id"])
        c = e.cohort_view(e.list_cohorts()[0]["id"])
        self.assertEqual(c["joined"], 5)        # exactly the quoted 5, not 7
        self.assertEqual(c["waitlist"], 2)

    def test_open_pool_decline_frees_a_seat(self):
        e = make_engine(threshold=6, capacity=8)
        members = join_n(e, 4, future(e))
        e.decline(members[0]["id"])
        c = e.list_cohorts()[0]
        self.assertEqual((c["status"], c["joined"]), ("OPEN", 3))
        self.assertEqual(e.member_view(members[0]["id"])["hold_amount"], 0)


class FestivalTests(unittest.TestCase):
    def test_seed_dates_land_inside_a_festival_window(self):
        from datetime import date
        for today in (date(2026, 11, 9), date(2026, 12, 25), date(2026, 10, 1)):
            start = next_festival_start(today)
            for offset in (1, 2, 3):
                mult, name = surge_multiplier(start + timedelta(days=offset))
                self.assertGreater(mult, 1.0, f"{today}: day +{offset} fell outside window")

    def test_falls_back_when_no_window_is_left(self):
        from datetime import date
        last_end = max(end for _, end, _, _ in FESTIVAL_SURGE)
        today = last_end
        self.assertEqual(next_festival_start(today), today + timedelta(days=10))


if __name__ == "__main__":
    unittest.main()
