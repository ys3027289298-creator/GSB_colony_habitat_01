import unittest

from colony.core import (
    Colony,
    MIGRATE_FOOD_COST,
    OXYGEN_PER_PERSON,
    OXYGEN_REGEN,
    SEED,
    TEMP_MAX,
    TEMP_MIN,
    WIN_POPULATION,
)


def make_colony(**overrides):
    colony = Colony(seed=SEED)
    if overrides:
        state = colony.to_dict()
        state.update(overrides)
        colony = Colony.from_dict(state)
    return colony


def set_cabins(colony, pops):
    state = colony.to_dict()
    for cabin, pop in zip(state["cabins"], pops):
        cabin["population"] = pop
    return Colony.from_dict(state)


class SealedCabinOxygenTest(unittest.TestCase):
    def test_sealed_cabin_still_shares_oxygen(self):
        colony = make_colony()
        colony.execute({"cmd": "seal", "cabin": "A"})
        before = colony.to_dict()["resources"]["oxygen"]
        total_pop = sum(c["population"] for c in colony.to_dict()["cabins"])
        colony.execute({"cmd": "tick"})
        after = colony.to_dict()["resources"]["oxygen"]
        # Sealed cabin A still draws from the shared oxygen supply.
        self.assertEqual(after, before + OXYGEN_REGEN - total_pop * OXYGEN_PER_PERSON)


class MigrationFoodTest(unittest.TestCase):
    def test_migration_charges_food_once(self):
        colony = make_colony()
        food_before = colony.to_dict()["resources"]["food"]
        colony.execute({"cmd": "migrate", "from": "A", "to": "B", "count": 2})
        food_after = colony.to_dict()["resources"]["food"]
        self.assertEqual(food_after, food_before - 2 * MIGRATE_FOOD_COST)

    def test_migration_cost_not_charged_again_on_tick(self):
        colony = make_colony()
        colony.execute({"cmd": "migrate", "from": "A", "to": "B", "count": 2})
        food_after_move = colony.to_dict()["resources"]["food"]
        total_pop = sum(c["population"] for c in colony.to_dict()["cabins"])
        colony.execute({"cmd": "tick"})
        food_after_tick = colony.to_dict()["resources"]["food"]
        # Only normal per-person consumption on tick, no repeated move cost.
        self.assertEqual(food_after_tick, food_after_move - total_pop)


class RepairFaultTest(unittest.TestCase):
    def _colony_with_fault(self):
        colony = make_colony()
        state = colony.to_dict()
        state["faults"] = [
            {
                "id": "F1",
                "kind": "leak",
                "repair_needed": 2,
                "repair_progress": 0,
                "repairing": False,
            }
        ]
        return Colony.from_dict(state)

    def test_fault_survives_until_repair_completes(self):
        colony = self._colony_with_fault()
        colony.execute({"cmd": "repair", "fault": "F1"})
        colony.execute({"cmd": "tick"})
        faults = colony.to_dict()["faults"]
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0]["repair_progress"], 1)
        colony.execute({"cmd": "tick"})
        self.assertEqual(colony.to_dict()["faults"], [])

    def test_unrepaired_fault_persists(self):
        colony = self._colony_with_fault()
        colony.execute({"cmd": "tick"})
        self.assertEqual(len(colony.to_dict()["faults"]), 1)


class TemperatureBoundaryTest(unittest.TestCase):
    def test_boundary_temperature_is_safe(self):
        for edge in (TEMP_MAX, TEMP_MIN):
            colony = make_colony(temperature=edge)
            colony.execute({"cmd": "tick"})
            thermal = [f for f in colony.to_dict()["faults"] if f["kind"] == "thermal"]
            self.assertEqual(thermal, [], f"edge {edge} should not raise a fault")

    def test_out_of_range_temperature_raises_fault(self):
        for extreme in (TEMP_MAX + 1, TEMP_MIN - 1):
            colony = make_colony(temperature=extreme)
            colony.execute({"cmd": "tick"})
            thermal = [f for f in colony.to_dict()["faults"] if f["kind"] == "thermal"]
            self.assertEqual(len(thermal), 1, f"extreme {extreme} should raise a fault")


class DoorPowerTest(unittest.TestCase):
    def test_auto_door_holds_position_without_power(self):
        colony = make_colony()
        state = colony.to_dict()
        state["resources"]["power"] = 0
        colony = Colony.from_dict(state)
        before = [d["open"] for d in colony.to_dict()["doors"]]
        colony.execute({"cmd": "tick"})
        after = [d["open"] for d in colony.to_dict()["doors"]]
        self.assertEqual(before, after)

    def test_auto_door_toggles_with_power(self):
        colony = make_colony()
        state = colony.to_dict()
        state["resources"]["power"] = 5
        colony = Colony.from_dict(state)
        before = [d["open"] for d in colony.to_dict()["doors"] if d["auto"]]
        colony.execute({"cmd": "tick"})
        after = [d["open"] for d in colony.to_dict()["doors"] if d["auto"]]
        self.assertEqual([not b for b in before], after)


class CancelBuildTest(unittest.TestCase):
    def test_cancel_build_releases_materials(self):
        colony = make_colony()
        materials_before = colony.to_dict()["resources"]["materials"]
        colony.execute({"cmd": "build", "item": "filter"})
        self.assertLess(colony.to_dict()["resources"]["materials"], materials_before)
        colony.execute({"cmd": "cancel_build"})
        state = colony.to_dict()
        self.assertEqual(state["resources"]["materials"], materials_before)
        self.assertIsNone(state["construction"])


class EventLogReplayTest(unittest.TestCase):
    SCRIPT = [
        {"cmd": "migrate", "from": "A", "to": "B", "count": 1},
        {"cmd": "seal", "cabin": "B"},
        {"cmd": "tick"},
        {"cmd": "build", "item": "filter"},
        {"cmd": "tick"},
        {"cmd": "tick"},
        {"cmd": "door", "door": "D2", "state": "open"},
        {"cmd": "tick"},
    ]

    def _run(self):
        colony = Colony(seed=SEED)
        for command in self.SCRIPT:
            colony.execute(command)
        return colony

    def test_replay_reproduces_exact_state(self):
        live = self._run()
        replayed = Colony.replay(live.to_dict()["event_log"], seed=SEED)
        self.assertEqual(replayed.to_dict(), live.to_dict())

    def test_replay_does_not_change_resources_twice(self):
        live = self._run()
        log = live.to_dict()["event_log"]
        first = Colony.replay(log, seed=SEED).to_dict()
        second = Colony.replay(log, seed=SEED).to_dict()
        self.assertEqual(first, second)
        self.assertEqual(first["resources"], live.to_dict()["resources"])

    def test_replay_leaves_original_untouched(self):
        live = self._run()
        before = live.to_dict()
        Colony.replay(before["event_log"], seed=SEED)
        self.assertEqual(live.to_dict(), before)


class WinLossTest(unittest.TestCase):
    def test_reserve_cabin_counts_toward_win(self):
        colony = make_colony()
        # 4 + 3 + 1(reserve) = 8 = WIN_POPULATION; without the reserve
        # cabin the total would only be 7.
        colony = set_cabins(colony, [4, 3, 1])
        colony.execute({"cmd": "tick"})
        self.assertEqual(colony.to_dict()["status"], "won")

    def test_reserve_cabin_counts_toward_survival(self):
        colony = make_colony()
        colony = set_cabins(colony, [0, 0, 1])
        colony.execute({"cmd": "tick"})
        self.assertEqual(colony.to_dict()["status"], "running")

    def test_total_loss_when_no_population_anywhere(self):
        colony = make_colony()
        colony = set_cabins(colony, [0, 0, 0])
        colony.execute({"cmd": "tick"})
        self.assertEqual(colony.to_dict()["status"], "lost")


class IllegalCommandTest(unittest.TestCase):
    def test_unknown_command_rejected(self):
        colony = make_colony()
        with self.assertRaises(ValueError):
            colony.execute({"cmd": "explode"})

    def test_missing_cmd_key_rejected(self):
        colony = make_colony()
        with self.assertRaises(ValueError):
            colony.execute({})

    def test_non_dict_command_rejected(self):
        colony = make_colony()
        with self.assertRaises(ValueError):
            colony.execute("tick")

    def test_unknown_cabin_rejected(self):
        colony = make_colony()
        with self.assertRaises(ValueError):
            colony.execute({"cmd": "seal", "cabin": "ZZ"})

    def test_cancel_without_construction_rejected(self):
        colony = make_colony()
        with self.assertRaises(ValueError):
            colony.execute({"cmd": "cancel_build"})


class EmptyDataTest(unittest.TestCase):
    def test_empty_dict_uses_defaults(self):
        self.assertEqual(Colony.from_dict({}).to_dict(), Colony(seed=SEED).to_dict())

    def test_partial_data_merges_with_defaults(self):
        colony = Colony.from_dict({"resources": {"oxygen": 1, "food": 2, "power": 3, "materials": 4}})
        state = colony.to_dict()
        self.assertEqual(state["resources"]["food"], 2)
        self.assertEqual(state["tick"], 0)
        self.assertEqual(len(state["cabins"]), 3)

    def test_invalid_state_type_rejected(self):
        with self.assertRaises(ValueError):
            Colony.from_dict([1, 2, 3])


class CapacityBoundaryTest(unittest.TestCase):
    def test_fill_exactly_to_capacity_allowed(self):
        colony = make_colony()
        colony.execute({"cmd": "migrate", "from": "A", "to": "B", "count": 2})
        cabins = {c["id"]: c for c in colony.to_dict()["cabins"]}
        self.assertEqual(cabins["B"]["population"], cabins["B"]["capacity"])

    def test_one_past_capacity_rejected(self):
        colony = make_colony()
        colony.execute({"cmd": "migrate", "from": "A", "to": "B", "count": 2})
        colony.execute({"cmd": "migrate", "from": "R", "to": "A", "count": 1})
        with self.assertRaises(ValueError):
            colony.execute({"cmd": "migrate", "from": "A", "to": "B", "count": 1})

    def test_overfull_migration_rejected(self):
        colony = make_colony()
        with self.assertRaises(ValueError):
            colony.execute({"cmd": "migrate", "from": "A", "to": "R", "count": 2})


class DuplicateInputTest(unittest.TestCase):
    def test_duplicate_migrate_charges_once_per_execution(self):
        colony = make_colony()
        command = {"cmd": "migrate", "from": "A", "to": "B", "count": 1}
        food_before = colony.to_dict()["resources"]["food"]
        colony.execute(dict(command))
        colony.execute(dict(command))
        state = colony.to_dict()
        cabins = {c["id"]: c for c in state["cabins"]}
        self.assertEqual(cabins["A"]["population"], 0)
        self.assertEqual(cabins["B"]["population"], 4)
        self.assertEqual(state["resources"]["food"], food_before - 2 * MIGRATE_FOOD_COST)

    def test_duplicate_repair_is_idempotent(self):
        colony = make_colony()
        state = colony.to_dict()
        state["faults"] = [
            {
                "id": "F1",
                "kind": "leak",
                "repair_needed": 2,
                "repair_progress": 0,
                "repairing": False,
            }
        ]
        colony = Colony.from_dict(state)
        colony.execute({"cmd": "repair", "fault": "F1"})
        colony.execute({"cmd": "repair", "fault": "F1"})
        colony.execute({"cmd": "tick"})
        self.assertEqual(colony.to_dict()["faults"][0]["repair_progress"], 1)

    def test_duplicate_commands_replay_identically(self):
        colony = Colony(seed=SEED)
        for _ in range(2):
            colony.execute({"cmd": "migrate", "from": "A", "to": "B", "count": 1})
            colony.execute({"cmd": "tick"})
        replayed = Colony.replay(colony.to_dict()["event_log"], seed=SEED)
        self.assertEqual(replayed.to_dict(), colony.to_dict())


class SnapshotTest(unittest.TestCase):
    def test_restore_continues_deterministically(self):
        colony = Colony(seed=SEED)
        colony.execute({"cmd": "seal", "cabin": "A"})
        colony.execute({"cmd": "tick"})
        snap = colony.snapshot()

        tail = [{"cmd": "migrate", "from": "A", "to": "B", "count": 1}, {"cmd": "tick"}]
        for command in tail:
            colony.execute(command)

        restored = Colony(seed=SEED)
        restored.restore(snap)
        for command in tail:
            restored.execute(command)

        self.assertEqual(restored.to_dict(), colony.to_dict())

    def test_fixed_seed_runs_are_identical(self):
        script = [{"cmd": "tick"}, {"cmd": "build", "item": "filter"}, {"cmd": "tick"}]
        runs = []
        for _ in range(2):
            colony = Colony(seed=SEED)
            for command in script:
                colony.execute(command)
            runs.append(colony.to_dict())
        self.assertEqual(runs[0], runs[1])


if __name__ == "__main__":
    unittest.main()
