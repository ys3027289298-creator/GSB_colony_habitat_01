import unittest

from colony import core


def fresh():
    return core.new_game(core.SEED)


class OxygenTests(unittest.TestCase):
    def test_sealed_room_does_not_share_oxygen(self):
        state = fresh()
        core.run_command(state, "seal beta")
        sealed_before = state["rooms"]["beta"]["oxygen"]
        core.run_command(state, "tick")
        beta = state["rooms"]["beta"]
        alpha = state["rooms"]["alpha"]
        self.assertEqual(
            beta["oxygen"],
            sealed_before - beta["population"] * core.OXYGEN_PER_PERSON)
        self.assertNotEqual(beta["oxygen"], alpha["oxygen"])

    def test_unsealed_rooms_share_oxygen(self):
        state = fresh()
        core.run_command(state, "tick")
        self.assertEqual(state["rooms"]["alpha"]["oxygen"],
                         state["rooms"]["gamma"]["oxygen"])


class MigrationTests(unittest.TestCase):
    def test_migration_charges_food_once(self):
        state = fresh()
        before = state["rooms"]["alpha"]["food"]
        core.run_command(state, "migrate alpha beta 2")
        self.assertEqual(state["rooms"]["alpha"]["food"], before - 2)
        self.assertEqual(state["rooms"]["alpha"]["population"], 2)
        self.assertEqual(state["rooms"]["beta"]["population"], 4)

    def test_duplicate_commands_apply_exactly_once_each(self):
        state = fresh()
        before = state["rooms"]["alpha"]["food"]
        core.run_command(state, "migrate alpha beta 1")
        core.run_command(state, "migrate alpha beta 1")
        self.assertEqual(state["rooms"]["alpha"]["food"], before - 2)


class RepairTests(unittest.TestCase):
    def _inject_fault(self, state, room="alpha", fid="fault-x", needed=3):
        state["rooms"][room]["faults"][fid] = {
            "repair_needed": needed, "progress": 0, "repairing": False}
        return fid

    def test_fault_cleared_only_after_repair_completes(self):
        state = fresh()
        fid = self._inject_fault(state)
        core.run_command(state, "repair alpha %s" % fid)
        core.run_command(state, "tick")
        self.assertIn(fid, state["rooms"]["alpha"]["faults"])
        core.run_command(state, "tick")
        self.assertIn(fid, state["rooms"]["alpha"]["faults"])
        core.run_command(state, "tick")
        self.assertNotIn(fid, state["rooms"]["alpha"]["faults"])

    def test_duplicate_repair_keeps_progress(self):
        state = fresh()
        fid = self._inject_fault(state)
        core.run_command(state, "repair alpha %s" % fid)
        core.run_command(state, "tick")
        core.run_command(state, "repair alpha %s" % fid)
        self.assertEqual(
            state["rooms"]["alpha"]["faults"][fid]["progress"], 1)

    def test_repair_unknown_fault_rejected(self):
        state = fresh()
        self.assertRaises(core.GameError, core.run_command,
                          state, "repair alpha nope")


class TemperatureTests(unittest.TestCase):
    def test_temperature_boundary_comparison(self):
        state = fresh()
        core.run_command(state, "temperature alpha 30")
        core.run_command(state, "tick")
        self.assertIsNone(state["rooms"]["alpha"]["alarm"])
        core.run_command(state, "temperature alpha 31")
        core.run_command(state, "tick")
        self.assertEqual(state["rooms"]["alpha"]["alarm"], "overheat")
        core.run_command(state, "temperature alpha 10")
        core.run_command(state, "tick")
        self.assertIsNone(state["rooms"]["alpha"]["alarm"])
        core.run_command(state, "temperature alpha 9")
        core.run_command(state, "tick")
        self.assertEqual(state["rooms"]["alpha"]["alarm"], "freeze")


class DoorTests(unittest.TestCase):
    def test_auto_door_halts_without_power(self):
        state = fresh()
        core.run_command(state, "power beta off")
        before = state["rooms"]["beta"]["door_open"]
        core.run_command(state, "tick")
        self.assertEqual(state["rooms"]["beta"]["door_open"], before)
        self.assertNotEqual(state["rooms"]["alpha"]["door_open"], before)

    def test_power_restore_resumes_door_cycle(self):
        state = fresh()
        core.run_command(state, "power beta off")
        core.run_command(state, "tick")
        core.run_command(state, "power beta on")
        before = state["rooms"]["beta"]["door_open"]
        core.run_command(state, "tick")
        self.assertNotEqual(state["rooms"]["beta"]["door_open"], before)


class ConstructionTests(unittest.TestCase):
    def test_cancel_construction_refunds_materials(self):
        state = fresh()
        before = state["materials"]
        core.run_command(state, "build delta")
        self.assertEqual(state["materials"], before - core.BUILD_COST)
        core.run_command(state, "cancel")
        self.assertEqual(state["materials"], before)
        self.assertIsNone(state["construction"])

    def test_completed_construction_adds_room(self):
        state = fresh()
        core.run_command(state, "build delta")
        core.run_command(state, "tick 2")
        self.assertIn("delta", state["rooms"])
        self.assertIsNone(state["construction"])

    def test_double_build_rejected(self):
        state = fresh()
        core.run_command(state, "build delta")
        self.assertRaises(core.GameError, core.run_command,
                          state, "build epsilon")


class ReplayTests(unittest.TestCase):
    SCRIPT = ("tick", "migrate alpha beta 1", "seal gamma",
              "tick 2", "build delta", "tick", "cancel", "tick")

    def test_event_log_replay_preserves_resources(self):
        state = fresh()
        for command in self.SCRIPT:
            core.run_command(state, command)
        replayed = core.replay(state["seed"], state["event_log"])
        self.assertEqual(core.snapshot(replayed), core.snapshot(state))

    def test_replay_does_not_mutate_inputs(self):
        state = fresh()
        for command in self.SCRIPT:
            core.run_command(state, command)
        log_copy = list(state["event_log"])
        snap_before = core.snapshot(state)
        core.replay(state["seed"], state["event_log"])
        self.assertEqual(state["event_log"], log_copy)
        self.assertEqual(core.snapshot(state), snap_before)

    def test_replay_empty_log_matches_fresh_game(self):
        self.assertEqual(core.snapshot(core.replay(core.SEED, [])),
                         core.snapshot(fresh()))


class EndConditionTests(unittest.TestCase):
    def test_backup_room_population_prevents_defeat(self):
        state = fresh()
        for room in state["rooms"].values():
            room["population"] = 0
        state["rooms"]["gamma"]["population"] = 3
        core.run_command(state, "tick")
        self.assertEqual(state["status"], "running")

    def test_defeat_when_all_rooms_empty(self):
        state = fresh()
        for room in state["rooms"].values():
            room["population"] = 0
        core.run_command(state, "tick")
        self.assertEqual(state["status"], "lost")

    def test_victory_counts_backup_room(self):
        state = fresh()
        for room in state["rooms"].values():
            room["population"] = 0
        state["rooms"]["gamma"]["population"] = state["goal_population"]
        core.run_command(state, "tick")
        self.assertEqual(state["status"], "won")


class CommandValidationTests(unittest.TestCase):
    def test_unknown_command_rejected(self):
        state = fresh()
        self.assertRaises(core.GameError, core.run_command, state, "dance")

    def test_empty_command_rejected(self):
        state = fresh()
        for bad in ("", "   ", None):
            self.assertRaises(core.GameError, core.run_command, state, bad)

    def test_bad_arguments_rejected(self):
        state = fresh()
        for bad in ("migrate alpha beta", "migrate alpha beta x",
                    "seal", "tick 1 2", "tick 0", "power alpha maybe",
                    "temperature alpha hot"):
            self.assertRaises(core.GameError, core.run_command, state, bad)

    def test_unknown_room_rejected(self):
        state = fresh()
        for bad in ("seal nowhere", "power nowhere off",
                    "migrate nowhere alpha 1", "migrate alpha nowhere 1"):
            self.assertRaises(core.GameError, core.run_command, state, bad)

    def test_failed_command_not_logged(self):
        state = fresh()
        self.assertRaises(core.GameError, core.run_command, state, "dance")
        self.assertEqual(state["event_log"], [])


class CapacityTests(unittest.TestCase):
    def test_capacity_boundary(self):
        state = fresh()
        core.run_command(state, "migrate alpha beta 4")
        self.assertEqual(state["rooms"]["beta"]["population"], 6)
        state["rooms"]["alpha"]["population"] = 1
        state["rooms"]["alpha"]["food"] = 5
        self.assertRaises(core.GameError, core.run_command,
                          state, "migrate alpha beta 1")
        self.assertEqual(state["rooms"]["beta"]["population"], 6)
        self.assertEqual(state["rooms"]["alpha"]["population"], 1)

    def test_overpopulation_source_rejected(self):
        state = fresh()
        self.assertRaises(core.GameError, core.run_command,
                          state, "migrate alpha beta 5")


class DuplicateInputTests(unittest.TestCase):
    def test_duplicate_seal_is_idempotent(self):
        state = fresh()
        core.run_command(state, "seal beta")
        core.run_command(state, "seal beta")
        self.assertTrue(state["rooms"]["beta"]["sealed"])
        core.run_command(state, "unseal beta")
        self.assertFalse(state["rooms"]["beta"]["sealed"])

    def test_duplicate_cancel_rejected(self):
        state = fresh()
        core.run_command(state, "build delta")
        core.run_command(state, "cancel")
        self.assertRaises(core.GameError, core.run_command, state, "cancel")


class SnapshotTests(unittest.TestCase):
    def test_snapshot_round_trip_restores_state(self):
        state = fresh()
        for command in ("tick", "migrate alpha beta 1", "tick 2"):
            core.run_command(state, command)
        clone = core.restore(core.snapshot(state))
        self.assertEqual(clone, state)
        core.run_command(state, "tick")
        core.run_command(clone, "tick")
        self.assertEqual(core.snapshot(clone), core.snapshot(state))

    def test_restore_rejects_empty_data(self):
        for bad in ("", None, "{}", "[]", "not json", '"text"'):
            self.assertRaises(core.GameError, core.restore, bad)

    def test_restore_rejects_missing_keys(self):
        state = fresh()
        del state["rooms"]["alpha"]["oxygen"]
        self.assertRaises(core.GameError, core.restore,
                          core.snapshot(state))


class DemoTests(unittest.TestCase):
    def test_fixed_seed_demo_is_deterministic(self):
        first = core.demo()
        second = core.demo()
        self.assertEqual(first, second)
        state = core.restore(first)
        self.assertEqual(state["status"], "running")
        self.assertGreater(state["tick"], 0)


if __name__ == "__main__":
    unittest.main()
