"""Core simulation for the colony habitat game.

The game advances in ticks. Each tick processes, in order:
oxygen, food, doors, faults, construction, temperature and the
win/lose condition. State is a plain JSON-serializable dict so
snapshots can be saved and restored losslessly.
"""

import json

SEED = 74
OXYGEN_PER_PERSON = 1
FOOD_PER_PERSON = 1
MIGRATION_FOOD_COST = 1
BUILD_COST = 4
BUILD_TICKS = 2
REPAIR_TICKS = 3
FAULT_CHANCE = 20

STATE_KEYS = (
    "seed", "rng", "tick", "materials", "goal_population", "status",
    "fault_seq", "construction", "event_log", "rooms",
)
ROOM_KEYS = (
    "population", "capacity", "oxygen", "food", "sealed", "door_open",
    "auto_door", "powered", "temperature", "temp_min", "temp_max",
    "alarm", "backup", "faults",
)


class GameError(Exception):
    """Raised for illegal commands or invalid game data."""


def _room(population=0, oxygen=100, food=0, backup=False):
    return {
        "population": population,
        "capacity": 6,
        "oxygen": oxygen,
        "food": food,
        "sealed": False,
        "door_open": True,
        "auto_door": True,
        "powered": True,
        "temperature": 20,
        "temp_min": 10,
        "temp_max": 30,
        "alarm": None,
        "backup": backup,
        "faults": {},
    }


def new_game(seed=SEED):
    return {
        "seed": seed,
        "rng": seed % 2147483647 or 1,
        "tick": 0,
        "materials": 8,
        "goal_population": 12,
        "status": "running",
        "fault_seq": 0,
        "construction": None,
        "event_log": [],
        "rooms": {
            "alpha": _room(population=4, oxygen=100, food=12),
            "beta": _room(population=2, oxygen=60, food=6),
            "gamma": _room(population=0, oxygen=40, food=0, backup=True),
        },
    }


def _rand(state, modulus):
    state["rng"] = (1103515245 * state["rng"] + 12345) % 2147483648
    return state["rng"] % modulus


def tick(state, ticks=1):
    for _ in range(ticks):
        if state["status"] != "running":
            break
        state["tick"] += 1
        _process_oxygen(state)
        _process_food(state)
        _process_doors(state)
        _process_faults(state)
        _process_construction(state)
        _process_temperature(state)
        _check_end(state)
    return state


def _process_oxygen(state):
    rooms = state["rooms"]
    for room in rooms.values():
        room["oxygen"] = max(
            0, room["oxygen"] - room["population"] * OXYGEN_PER_PERSON)
    # Only unsealed rooms with an open door share oxygen. A sealed
    # room must keep its own atmosphere.
    open_rooms = [r for r in rooms.values()
                  if not r["sealed"] and r["door_open"]]
    if len(open_rooms) > 1:
        avg = sum(r["oxygen"] for r in open_rooms) // len(open_rooms)
        for room in open_rooms:
            room["oxygen"] = avg


def _process_food(state):
    for room in state["rooms"].values():
        room["food"] = max(
            0, room["food"] - room["population"] * FOOD_PER_PERSON)


def _process_doors(state):
    for room in state["rooms"].values():
        # Automatic doors need power; without it they hold position.
        if room["auto_door"] and room["powered"]:
            room["door_open"] = not room["door_open"]


def _process_faults(state):
    if state["rooms"] and _rand(state, 100) < FAULT_CHANCE:
        names = sorted(state["rooms"])
        name = names[_rand(state, len(names))]
        state["fault_seq"] += 1
        fid = "fault-%d" % state["fault_seq"]
        state["rooms"][name]["faults"][fid] = {
            "repair_needed": REPAIR_TICKS, "progress": 0, "repairing": False,
        }
    for room in state["rooms"].values():
        done = []
        for fid, fault in room["faults"].items():
            if fault["repairing"]:
                fault["progress"] += 1
                # The fault is cleared only once the repair completes.
                if fault["progress"] >= fault["repair_needed"]:
                    done.append(fid)
        for fid in done:
            del room["faults"][fid]


def _process_construction(state):
    construction = state["construction"]
    if construction is None:
        return
    construction["remaining"] -= 1
    if construction["remaining"] <= 0:
        state["rooms"][construction["room"]] = _room()
        state["construction"] = None


def _process_temperature(state):
    for room in state["rooms"].values():
        if room["temperature"] > room["temp_max"]:
            room["alarm"] = "overheat"
        elif room["temperature"] < room["temp_min"]:
            room["alarm"] = "freeze"
        else:
            room["alarm"] = None


def _check_end(state):
    # Every room counts, including backup rooms.
    total = sum(r["population"] for r in state["rooms"].values())
    if total >= state["goal_population"]:
        state["status"] = "won"
    elif total <= 0:
        state["status"] = "lost"


def _require_room(state, name):
    try:
        return state["rooms"][name]
    except KeyError:
        raise GameError("unknown room: %s" % name)


def _parse_int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        raise GameError("not an integer: %r" % (text,))


def _expect(args, count, verb):
    if len(args) != count:
        raise GameError("%s expects %d argument(s)" % (verb, count))


def _migrate(state, src, dst, count):
    if count < 1:
        raise GameError("migration count must be positive")
    if src == dst:
        raise GameError("source and destination must differ")
    source = _require_room(state, src)
    target = _require_room(state, dst)
    if source["population"] < count:
        raise GameError("not enough population in %s" % src)
    if target["population"] + count > target["capacity"]:
        raise GameError("capacity exceeded in %s" % dst)
    cost = count * MIGRATION_FOOD_COST
    if source["food"] < cost:
        raise GameError("not enough food in %s" % src)
    source["population"] -= count
    target["population"] += count
    # Travel rations are charged exactly once.
    source["food"] -= cost


def _repair(state, room_name, fid):
    room = _require_room(state, room_name)
    fault = room["faults"].get(fid)
    if fault is None:
        raise GameError("no such fault: %s" % fid)
    # Repeating the repair order keeps the existing progress.
    fault["repairing"] = True


def _build(state, name):
    if state["construction"] is not None:
        raise GameError("construction already in progress")
    if name in state["rooms"]:
        raise GameError("room already exists: %s" % name)
    if state["materials"] < BUILD_COST:
        raise GameError("not enough materials")
    state["materials"] -= BUILD_COST
    state["construction"] = {
        "room": name, "cost": BUILD_COST, "remaining": BUILD_TICKS,
    }


def _cancel(state):
    construction = state["construction"]
    if construction is None:
        raise GameError("no construction in progress")
    # Cancelling releases the reserved materials.
    state["materials"] += construction["cost"]
    state["construction"] = None


def run_command(state, command):
    if not isinstance(command, str) or not command.strip():
        raise GameError("empty command")
    parts = command.split()
    verb, args = parts[0], parts[1:]

    if verb == "snapshot":
        _expect(args, 0, verb)
        return snapshot(state)
    if verb == "status":
        _expect(args, 0, verb)
        return {"tick": state["tick"], "status": state["status"],
                "materials": state["materials"]}

    if verb == "tick":
        if len(args) > 1:
            raise GameError("usage: tick [n]")
        count = 1
        if args:
            count = _parse_int(args[0])
            if count < 1:
                raise GameError("tick count must be positive")
        tick(state, count)
        result = {"tick": state["tick"], "status": state["status"]}
    elif verb in ("seal", "unseal"):
        _expect(args, 1, verb)
        room = _require_room(state, args[0])
        room["sealed"] = (verb == "seal")
        result = {"room": args[0], "sealed": room["sealed"]}
    elif verb == "migrate":
        _expect(args, 3, verb)
        _migrate(state, args[0], args[1], _parse_int(args[2]))
        result = {"from": args[0], "to": args[1]}
    elif verb == "repair":
        _expect(args, 2, verb)
        _repair(state, args[0], args[1])
        result = {"room": args[0], "fault": args[1], "repairing": True}
    elif verb == "build":
        _expect(args, 1, verb)
        _build(state, args[0])
        result = {"building": args[0]}
    elif verb == "cancel":
        _expect(args, 0, verb)
        _cancel(state)
        result = {"cancelled": True}
    elif verb == "power":
        _expect(args, 2, verb)
        room = _require_room(state, args[0])
        if args[1] not in ("on", "off"):
            raise GameError("power expects on|off")
        room["powered"] = (args[1] == "on")
        result = {"room": args[0], "powered": room["powered"]}
    elif verb == "temperature":
        _expect(args, 2, verb)
        room = _require_room(state, args[0])
        room["temperature"] = _parse_int(args[1])
        result = {"room": args[0], "temperature": room["temperature"]}
    else:
        raise GameError("unknown command: %s" % verb)

    state["event_log"].append(command)
    return result


def snapshot(state):
    return json.dumps(state, sort_keys=True)


def restore(data):
    if not data:
        raise GameError("empty snapshot")
    try:
        state = json.loads(data)
    except (TypeError, ValueError):
        raise GameError("invalid snapshot data")
    _validate(state)
    return state


def _validate(state):
    if not isinstance(state, dict):
        raise GameError("snapshot is not a state object")
    for key in STATE_KEYS:
        if key not in state:
            raise GameError("snapshot missing key: %s" % key)
    if not isinstance(state["rooms"], dict):
        raise GameError("snapshot rooms must be an object")
    for name, room in state["rooms"].items():
        if not isinstance(room, dict):
            raise GameError("room %s is not an object" % name)
        for key in ROOM_KEYS:
            if key not in room:
                raise GameError("room %s missing key: %s" % (name, key))


def replay(seed, events):
    """Rebuild a state from the seed and the recorded event log.

    Replaying must reproduce the exact same resources as the live
    run and must not mutate the log it is given.
    """
    state = new_game(seed)
    for event in list(events):
        run_command(state, event)
    return state


DEMO_SCRIPT = (
    "tick",
    "migrate alpha beta 1",
    "seal gamma",
    "build delta",
    "tick",
    "cancel",
    "power beta off",
    "temperature alpha 31",
    "tick",
    "unseal gamma",
    "tick",
)


def demo(seed=SEED):
    """Run the fixed-seed demonstration and return the snapshot."""
    state = new_game(seed)
    for command in DEMO_SCRIPT:
        run_command(state, command)
    clone = replay(seed, state["event_log"])
    assert snapshot(clone) == snapshot(state), "replay diverged"
    return snapshot(state)


if __name__ == "__main__":
    print(demo())
