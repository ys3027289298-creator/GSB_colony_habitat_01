"""Colony tick simulation core.

Processes oxygen, food, doors, population, faults, construction and
win/loss conditions once per tick. State is plain JSON-compatible data
so snapshots can be saved and restored, and the event log can be
replayed deterministically from a fixed seed.
"""

from __future__ import annotations

import copy
import json
import random

SEED = 74

OXYGEN_MAX = 100
OXYGEN_REGEN = 6
OXYGEN_PER_PERSON = 1

FOOD_PER_PERSON = 1
MIGRATE_FOOD_COST = 2

POWER_MAX = 20
POWER_REGEN = 4
DOOR_POWER_COST = 1

TEMP_MIN = -20
TEMP_MAX = 38

WIN_POPULATION = 8
DEFAULT_REPAIR_NEEDED = 2

BUILDABLES = {
    "filter": {"materials": 5, "work": 2, "oxygen_bonus": 2},
}

COMMAND_NAMES = (
    "tick",
    "seal",
    "unseal",
    "migrate",
    "repair",
    "door",
    "build",
    "cancel_build",
)


def default_state() -> dict:
    return {
        "tick": 0,
        "seed": SEED,
        "status": "running",
        "temperature": 20,
        "oxygen_bonus": 0,
        "resources": {
            "oxygen": 60,
            "food": 30,
            "power": 10,
            "materials": 10,
        },
        "cabins": [
            {"id": "A", "capacity": 4, "population": 2, "sealed": False, "reserved": False},
            {"id": "B", "capacity": 4, "population": 2, "sealed": False, "reserved": False},
            {"id": "R", "capacity": 2, "population": 1, "sealed": False, "reserved": True},
        ],
        "doors": [
            {"id": "D1", "open": True, "auto": True},
            {"id": "D2", "open": False, "auto": False},
        ],
        "faults": [],
        "construction": None,
        "event_log": [],
    }


class Colony:
    def __init__(self, seed: int = SEED, state: dict | None = None):
        self.state = default_state()
        self.state["seed"] = seed
        if state:
            if not isinstance(state, dict):
                raise ValueError("state must be a dict")
            for key, value in state.items():
                self.state[key] = copy.deepcopy(value)

    @classmethod
    def from_dict(cls, data: dict) -> "Colony":
        if not isinstance(data, dict):
            raise ValueError("state must be a dict")
        return cls(state=data)

    def to_dict(self) -> dict:
        return copy.deepcopy(self.state)

    def snapshot(self) -> dict:
        return self.to_dict()

    def restore(self, snap: dict) -> None:
        self.state = Colony.from_dict(snap).state

    @classmethod
    def replay(cls, event_log: list, seed: int = SEED) -> "Colony":
        colony = cls(seed=seed)
        for entry in event_log:
            colony.execute(copy.deepcopy(entry["command"]))
        return colony

    # -- lookups ------------------------------------------------------

    def _cabin(self, cabin_id) -> dict:
        for cabin in self.state["cabins"]:
            if cabin["id"] == cabin_id:
                return cabin
        raise ValueError(f"unknown cabin: {cabin_id!r}")

    def _door(self, door_id) -> dict:
        for door in self.state["doors"]:
            if door["id"] == door_id:
                return door
        raise ValueError(f"unknown door: {door_id!r}")

    def _fault(self, fault_id) -> dict:
        for fault in self.state["faults"]:
            if fault["id"] == fault_id:
                return fault
        raise ValueError(f"unknown fault: {fault_id!r}")

    # -- commands -----------------------------------------------------

    def execute(self, command: dict) -> dict:
        if not isinstance(command, dict) or "cmd" not in command:
            raise ValueError("illegal command: expected a dict with a 'cmd' key")
        name = command["cmd"]
        if name not in COMMAND_NAMES:
            raise ValueError(f"unknown command: {name!r}")
        handler = getattr(self, f"_cmd_{name}")
        handler(command)
        self.state["event_log"].append(
            {"tick": self.state["tick"], "command": copy.deepcopy(command)}
        )
        return {"ok": True}

    def _cmd_tick(self, command: dict) -> None:
        self._tick()

    def _cmd_seal(self, command: dict) -> None:
        self._cabin(command.get("cabin"))["sealed"] = True

    def _cmd_unseal(self, command: dict) -> None:
        self._cabin(command.get("cabin"))["sealed"] = False

    def _cmd_migrate(self, command: dict) -> None:
        source = self._cabin(command.get("from"))
        target = self._cabin(command.get("to"))
        count = command.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError("migrate count must be a positive integer")
        if source is target:
            raise ValueError("source and target cabins must differ")
        if source["population"] < count:
            raise ValueError("not enough people in source cabin")
        if target["population"] + count > target["capacity"]:
            raise ValueError("target cabin capacity exceeded")
        cost = count * MIGRATE_FOOD_COST
        if self.state["resources"]["food"] < cost:
            raise ValueError("not enough food to migrate")
        source["population"] -= count
        target["population"] += count
        # Food is charged exactly once per migrant, here at move time.
        self.state["resources"]["food"] -= cost

    def _cmd_repair(self, command: dict) -> None:
        fault = self._fault(command.get("fault"))
        fault["repairing"] = True

    def _cmd_door(self, command: dict) -> None:
        door = self._door(command.get("door"))
        state = command.get("state")
        if state not in ("open", "close"):
            raise ValueError("door state must be 'open' or 'close'")
        door["open"] = state == "open"

    def _cmd_build(self, command: dict) -> None:
        item = command.get("item")
        if item not in BUILDABLES:
            raise ValueError(f"unknown build item: {item!r}")
        if self.state["construction"] is not None:
            raise ValueError("construction already in progress")
        cost = BUILDABLES[item]["materials"]
        if self.state["resources"]["materials"] < cost:
            raise ValueError("not enough materials")
        self.state["resources"]["materials"] -= cost
        self.state["construction"] = {
            "item": item,
            "materials_reserved": cost,
            "progress": 0,
            "work_needed": BUILDABLES[item]["work"],
        }

    def _cmd_cancel_build(self, command: dict) -> None:
        construction = self.state["construction"]
        if construction is None:
            raise ValueError("no construction in progress")
        # Reserved materials go back to the stockpile on cancel.
        self.state["resources"]["materials"] += construction["materials_reserved"]
        self.state["construction"] = None

    # -- tick ---------------------------------------------------------

    def _total_population(self) -> int:
        # Every cabin counts, including sealed and reserve cabins.
        return sum(cabin["population"] for cabin in self.state["cabins"])

    def _lose_one_person(self) -> None:
        for cabin in self.state["cabins"]:
            if cabin["population"] > 0:
                cabin["population"] -= 1
                return

    def _tick(self) -> None:
        state = self.state
        res = state["resources"]
        state["tick"] += 1

        # Thermal stress only outside the (inclusive) safe band.
        if state["temperature"] > TEMP_MAX or state["temperature"] < TEMP_MIN:
            if not any(f["kind"] == "thermal" for f in state["faults"]):
                state["faults"].append(
                    {
                        "id": f"thermal-{state['tick']}",
                        "kind": "thermal",
                        "repair_needed": DEFAULT_REPAIR_NEEDED,
                        "repair_progress": 0,
                        "repairing": False,
                    }
                )

        # Automatic doors need power; during an outage they hold position.
        for door in state["doors"]:
            if door["auto"] and res["power"] >= DOOR_POWER_COST:
                res["power"] -= DOOR_POWER_COST
                door["open"] = not door["open"]
        res["power"] = min(POWER_MAX, res["power"] + POWER_REGEN)

        # Oxygen is colony-wide: sealed cabins still share the supply.
        total_pop = self._total_population()
        res["oxygen"] += OXYGEN_REGEN + state["oxygen_bonus"] - total_pop * OXYGEN_PER_PERSON
        res["oxygen"] = max(0, min(OXYGEN_MAX, res["oxygen"]))
        res["food"] = max(0, res["food"] - total_pop * FOOD_PER_PERSON)

        # Deterministic drift derived from (seed, tick) keeps snapshots
        # and replays reproducible without storing RNG state.
        rng = random.Random(f"{state['seed']}:{state['tick']}")
        state["temperature"] += rng.choice([-1, 0, 1])

        # A fault is cleared only once its repair has fully completed.
        remaining = []
        for fault in state["faults"]:
            if fault["repairing"]:
                fault["repair_progress"] += 1
            if fault["repair_progress"] < fault["repair_needed"]:
                remaining.append(fault)
        state["faults"] = remaining

        construction = state["construction"]
        if construction is not None:
            construction["progress"] += 1
            if construction["progress"] >= construction["work_needed"]:
                state["oxygen_bonus"] += BUILDABLES[construction["item"]]["oxygen_bonus"]
                state["construction"] = None

        if res["oxygen"] <= 0 or res["food"] <= 0:
            self._lose_one_person()

        total_pop = self._total_population()
        if total_pop <= 0:
            state["status"] = "lost"
        elif total_pop >= WIN_POPULATION:
            state["status"] = "won"


def demo(seed: int = SEED) -> dict:
    """Fixed-seed scripted run used as a reproducible demo."""
    colony = Colony(seed=seed)
    script = [
        {"cmd": "migrate", "from": "A", "to": "B", "count": 1},
        {"cmd": "seal", "cabin": "B"},
        {"cmd": "tick"},
        {"cmd": "build", "item": "filter"},
        {"cmd": "tick"},
        {"cmd": "cancel_build"},
        {"cmd": "build", "item": "filter"},
        {"cmd": "tick"},
        {"cmd": "tick"},
        {"cmd": "door", "door": "D2", "state": "open"},
        {"cmd": "tick"},
    ]
    for command in script:
        colony.execute(command)
    result = colony.to_dict()
    replayed = Colony.replay(result["event_log"], seed=seed)
    assert replayed.to_dict() == result, "replay diverged from live run"
    return result


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2, sort_keys=True))
