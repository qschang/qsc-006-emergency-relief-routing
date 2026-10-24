import itertools
import copy
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

APP = os.environ.get("APP", "/app/solve.py")


def run(data, raw=None, sentinel=None):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        input_path, output_path = root / "input.json", root / "output.json"
        if raw is None:
            data = copy.deepcopy(data)
            data.setdefault("wave_energy_budget", {"early": 100_000, "late": 100_000})
            for lane in data.get("lanes", []):
                lane.setdefault("early_capacity", lane.get("capacity", 1))
                lane.setdefault("late_capacity", lane.get("capacity", 1))
                lane.setdefault("transit_minutes", 1)
                lane.setdefault("exposure_minutes", 0)
                lane.setdefault("cooling_units_per_pallet", 0)
                lane.pop("capacity", None)
            for order in data.get("orders", []):
                order.setdefault("sla_minutes", 10_080)
                order.setdefault("max_exposure_minutes", 10_080)
                order.setdefault("allowed_waves", ["early", "late"])
        input_path.write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")
        if sentinel is not None:
            output_path.write_bytes(sentinel)
        process = subprocess.run(
            [sys.executable, APP, str(input_path), str(output_path)], capture_output=True, timeout=30
        )
        leftovers = sorted(path.name for path in root.iterdir() if path not in (input_path, output_path))
        output = output_path.read_bytes() if output_path.exists() else None
        return process, output, output_path.exists(), leftovers


def all_paths(start, end, lanes, sla_minutes=10_080, max_exposure_minutes=10_080):
    paths = []

    def walk(node, seen, chosen, elapsed=0, exposure=0):
        if node == end:
            paths.append(tuple(chosen))
            return
        for lane in lanes:
            next_elapsed = elapsed + lane.get("transit_minutes", 1)
            next_exposure = exposure + lane.get("exposure_minutes", 0)
            if lane["from"] == node and lane["to"] not in seen and next_elapsed <= sla_minutes and next_exposure <= max_exposure_minutes:
                walk(lane["to"], seen | {lane["to"]}, chosen + [lane["lane_id"]], next_elapsed, next_exposure)

    walk(start, {start}, [])
    return paths


def exhaustive_plan(data):
    orders = data["orders"]
    lanes = {lane["lane_id"]: lane for lane in data["lanes"]}
    choices = [
        [None] + [
            (wave, path)
            for wave in order.get("allowed_waves", ["early", "late"])
            for path in all_paths(order["origin"], order["destination"], data["lanes"], sla_minutes=order.get("sla_minutes", 10_080), max_exposure_minutes=order.get("max_exposure_minutes", 10_080))
        ]
        for order in orders
    ]
    best_key = None
    best_choice = None
    for selected in itertools.product(*choices):
        loads = {wave: {lane_id: 0 for lane_id in lanes} for wave in ("early", "late")}
        counts = [0, 0, 0]
        cost = 0
        energy = {"early": 0, "late": 0}
        feasible = True
        for order, choice in zip(orders, selected):
            if choice is None:
                continue
            wave, path = choice
            counts[order["priority"]] += 1
            for lane_id in path:
                lane = lanes[lane_id]
                loads[wave][lane_id] += order["pallets"]
                if loads[wave][lane_id] > lane.get(f"{wave}_capacity", lane.get("capacity", 1)):
                    feasible = False
                cost += order["pallets"] * lane["unit_cost"]
                energy[wave] += order["pallets"] * lane.get("cooling_units_per_pallet", 0)
        budget = data.get("wave_energy_budget", {"early": 100_000, "late": 100_000})
        if any(energy[wave] > budget[wave] for wave in ("early", "late")):
            feasible = False
        if not feasible:
            continue
        opened = {lane_id for wave in loads.values() for lane_id, load in wave.items() if load}
        cost += sum(lanes[lane_id]["open_fee"] for lane_id in opened)
        by_id = sorted(range(len(orders)), key=lambda index: orders[index]["order_id"])
        descriptor = tuple(() if selected[index] is None else selected[index] for index in by_id)
        key = (-counts[0], -counts[1], -counts[2], cost, descriptor)
        if best_key is None or key < best_key:
            best_key, best_choice = key, selected
    return [-best_key[0], -best_key[1], -best_key[2]], best_key[3], best_choice


def assert_plan_consistent(data, result):
    lane_by_id = {lane["lane_id"]: lane for lane in data["lanes"]}
    order_by_id = {order["order_id"]: order for order in data["orders"]}
    loads = {wave: {lane_id: 0 for lane_id in lane_by_id} for wave in ("early", "late")}
    energy = {"early": 0, "late": 0}
    opened = set()
    counts = [0, 0, 0]
    cost = 0
    assert len(result["orders"]) == len(data["orders"])
    for row in result["orders"]:
        order = order_by_id[row["order_id"]]
        path = row["path"]
        assert row["accepted"] is (len(path) > 0)
        if not row["accepted"]:
            assert path == [] and row["wave"] is None
            continue
        wave = row["wave"]
        assert wave in order.get("allowed_waves", ["early", "late"])
        counts[order["priority"]] += 1
        current = order["origin"]
        visited = {current}
        exposure = 0
        for lane_id in path:
            lane = lane_by_id[lane_id]
            assert lane["from"] == current
            current = lane["to"]
            assert current not in visited
            visited.add(current)
            loads[wave][lane_id] += order["pallets"]
            exposure += lane.get("exposure_minutes", 0)
            energy[wave] += order["pallets"] * lane.get("cooling_units_per_pallet", 0)
            opened.add(lane_id)
            cost += order["pallets"] * lane["unit_cost"]
        assert sum(lane_by_id[lane_id].get("transit_minutes", 1) for lane_id in path) <= order.get("sla_minutes", 10_080)
        assert exposure <= order.get("max_exposure_minutes", 10_080)
        assert current == order["destination"]
    cost += sum(lane_by_id[lane_id]["open_fee"] for lane_id in opened)
    assert result["served_counts"] == {str(i): counts[i] for i in range(3)}
    assert result["total_cost"] == cost
    assert result["wave_energy_loads"] == energy
    budget = data.get("wave_energy_budget", {"early": 100_000, "late": 100_000})
    assert energy["early"] <= budget["early"]
    assert energy["late"] <= budget["late"]
    assert len(result["lane_loads"]) == len(data["lanes"])
    for row, lane in zip(result["lane_loads"], data["lanes"]):
        lane_id = lane["lane_id"]
        assert row == {
            "lane_id": lane_id,
            "early_load": loads["early"][lane_id],
            "late_load": loads["late"][lane_id],
            "opened": lane_id in opened,
        }
        assert 0 <= row["early_load"] <= lane.get("early_capacity", lane.get("capacity", 1))
        assert 0 <= row["late_load"] <= lane.get("late_capacity", lane.get("capacity", 1))


def test_hand_computed_dispatch_respects_priority_and_open_fee_gate():
    '''接口："程序从第一个命令行参数读取 UTF-8 JSON，并将结果写入第二个参数指定的文件"；PRI-01："计划须先按优先级 0、1、2 的顺序，词典序最大化各级接收订单张数；"'''
    data = {
        "nodes": ["S", "A", "T"],
        "lanes": [
            {"lane_id": "SA", "from": "S", "to": "A", "capacity": 2, "open_fee": 7, "unit_cost": 1},
            {"lane_id": "AT", "from": "A", "to": "T", "capacity": 2, "open_fee": 0, "unit_cost": 1},
        ],
        "orders": [
            {"order_id": "P0", "origin": "S", "destination": "T", "priority": 0, "pallets": 2, "allowed_waves": ["early"]},
            {"order_id": "P1", "origin": "S", "destination": "T", "priority": 1, "pallets": 1, "allowed_waves": ["early"]},
        ],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert result["served_counts"] == {"0": 1, "1": 0, "2": 0}
    assert result["total_cost"] == 11 and [row["accepted"] for row in result["orders"]] == [True, False]
    assert_plan_consistent(data, result)


def test_exhaustive_small_ledgers_match_independent_joint_enumeration():
    '''CAP-02: "所有订单共用同一班次的线路容量。"'''
    rng = random.Random(287014)
    nodes = ["S", "A", "B", "T"]
    possible = [("S", "A"), ("S", "B"), ("A", "B"), ("A", "T"), ("B", "T"), ("S", "T")]
    for case in range(16):
        selected_edges = possible[:]
        rng.shuffle(selected_edges)
        lanes = [
            {"lane_id": f"L{i:02}", "from": edge[0], "to": edge[1], "capacity": rng.randint(1, 5), "open_fee": rng.randint(0, 8), "unit_cost": rng.randint(0, 5)}
            for i, edge in enumerate(selected_edges[: rng.randint(3, 6)])
        ]
        orders = [
            {"order_id": f"O{i}", "origin": "S", "destination": "T", "priority": rng.randrange(3), "pallets": rng.randint(1, 3)}
            for i in range(3)
        ]
        data = {"nodes": nodes, "lanes": lanes, "orders": orders}
        expected_counts, expected_cost, expected_paths = exhaustive_plan(data)
        process, output, exists, leftovers = run(data)
        result = json.loads(output)
        assert process.returncode == 0 and exists and leftovers == [], f"case={case}"
        assert [result["served_counts"][str(i)] for i in range(3)] == expected_counts, f"case={case}"
        assert result["total_cost"] == expected_cost, f"case={case}"
        assert [None if not row["accepted"] else (row["wave"], tuple(row["path"])) for row in result["orders"]] == list(expected_paths), f"case={case}"
        assert_plan_consistent(data, result)


def test_dense_route_batch_uses_shared_capacity_and_global_tie_break():
    '''CAP-02/FIX-03/TIE-04: "所有订单共用同一班次的线路容量。"'''
    orders = [
        {"order_id": f"O{i:02d}", "origin": "S", "destination": "T", "priority": i // 2, "pallets": 4, "allowed_waves": ["early"]}
        for i in range(6)
    ]
    lane_specs = [
        ("S", "A", 32), ("S", "B", 32),
        ("A", "C", 8), ("A", "D", 8), ("A", "E", 8),
        ("B", "C", 8), ("B", "D", 8), ("B", "E", 8),
        ("C", "F", 8), ("C", "G", 8), ("C", "H", 8),
        ("D", "F", 8), ("D", "G", 8), ("D", "H", 8),
        ("E", "F", 8), ("E", "G", 8), ("E", "H", 8),
        ("F", "T", 32), ("G", "T", 32), ("H", "T", 32),
    ]
    data = {
        "nodes": ["S", "A", "B", "C", "D", "E", "F", "G", "H", "T"],
        "lanes": [
            {"lane_id": f"L{i:02d}", "from": start, "to": end, "capacity": capacity, "open_fee": 0, "unit_cost": 0}
            for i, (start, end, capacity) in enumerate(lane_specs)
        ],
        "orders": list(reversed(orders)),
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assignment = {row["order_id"]: row["path"] for row in result["orders"]}
    assert process.returncode == 0 and exists and leftovers == []
    assert result["served_counts"] == {"0": 2, "1": 2, "2": 2}
    assert result["total_cost"] == 0
    route_a = ["L00", "L02", "L08", "L17"]
    route_b = ["L01", "L05", "L09", "L18"]
    expected = {f"O{i:02d}": route_a for i in range(2)}
    expected.update({f"O{i:02d}": ["L00", "L03", "L11", "L17"] for i in range(2, 4)})
    expected.update({f"O{i:02d}": ["L00", "L04", "L14", "L17"] for i in range(4, 6)})
    assert assignment == expected
    assert [row["early_load"] + row["late_load"] for row in result["lane_loads"]] == [24, 0, 8, 8, 8, 0, 0, 0, 8, 0, 0, 8, 0, 0, 8, 0, 0, 24, 0, 0]
    assert_plan_consistent(data, result)


def test_sla_boundary_selects_a_feasible_route_and_rejects_late_only_service():
    '''SLA-07: "线路的 `transit_minutes` 之和不得超过该订单的 `sla_minutes`"'''
    data = {
        "nodes": ["S", "A", "T"],
        "lanes": [
            {"lane_id": "Z-DIRECT", "from": "S", "to": "T", "capacity": 1, "open_fee": 10, "unit_cost": 0, "transit_minutes": 3},
            {"lane_id": "A-1", "from": "S", "to": "A", "capacity": 1, "open_fee": 0, "unit_cost": 0, "transit_minutes": 2},
            {"lane_id": "A-2", "from": "A", "to": "T", "capacity": 1, "open_fee": 0, "unit_cost": 0, "transit_minutes": 2},
        ],
        "orders": [
            {"order_id": "SLA4", "origin": "S", "destination": "T", "priority": 0, "pallets": 1, "sla_minutes": 4},
            {"order_id": "SLA3", "origin": "S", "destination": "T", "priority": 0, "pallets": 1, "sla_minutes": 3},
            {"order_id": "SLA2", "origin": "S", "destination": "T", "priority": 1, "pallets": 1, "sla_minutes": 2},
        ],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert result["served_counts"] == {"0": 2, "1": 0, "2": 0}
    assert {row["order_id"]: row["path"] for row in result["orders"]} == {
        "SLA4": ["A-1", "A-2"], "SLA3": ["Z-DIRECT"], "SLA2": []
    }
    assert result["total_cost"] == 10
    assert {row["order_id"]: row["wave"] for row in result["orders"]} == {"SLA4": "early", "SLA3": "early", "SLA2": None}
    assert_plan_consistent(data, result)


def test_dispatch_waves_have_separate_capacity_and_one_shift_opening_fee():
    '''WAVE-08: "早、晚波次的容量分别核算。线路 `open_fee` 按整个夜班只收取一次。"'''
    data = {
        "nodes": ["S", "T"],
        "lanes": [
            {"lane_id": "A", "from": "S", "to": "T", "early_capacity": 1, "late_capacity": 1, "open_fee": 5, "unit_cost": 0, "transit_minutes": 1},
            {"lane_id": "B", "from": "S", "to": "T", "early_capacity": 1, "late_capacity": 1, "open_fee": 7, "unit_cost": 0, "transit_minutes": 1},
        ],
        "orders": [
            {"order_id": "E0", "origin": "S", "destination": "T", "priority": 0, "pallets": 1, "sla_minutes": 10, "allowed_waves": ["early"]},
            {"order_id": "E1", "origin": "S", "destination": "T", "priority": 0, "pallets": 1, "sla_minutes": 10, "allowed_waves": ["early"]},
            {"order_id": "L0", "origin": "S", "destination": "T", "priority": 0, "pallets": 1, "sla_minutes": 10, "allowed_waves": ["late"]},
        ],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert result["served_counts"] == {"0": 3, "1": 0, "2": 0}
    assert result["total_cost"] == 12
    assert [(row["wave"], row["path"]) for row in result["orders"]] == [
        ("early", ["A"]), ("early", ["B"]), ("late", ["A"])
    ]
    assert result["lane_loads"] == [
        {"lane_id": "A", "early_load": 1, "late_load": 1, "opened": True},
        {"lane_id": "B", "early_load": 1, "late_load": 0, "opened": True},
    ]
    assert_plan_consistent(data, result)


def test_shared_lane_fee_is_paid_once_for_multiple_orders():
    '''FIX-03: "其 `open_fee` 只收取一次"'''
    data = {
        "nodes": ["S", "A", "T"],
        "lanes": [
            {"lane_id": "SHARE", "from": "S", "to": "A", "capacity": 2, "open_fee": 9, "unit_cost": 0},
            {"lane_id": "END", "from": "A", "to": "T", "capacity": 2, "open_fee": 0, "unit_cost": 0},
        ],
        "orders": [
            {"order_id": "A", "origin": "S", "destination": "T", "priority": 1, "pallets": 1},
            {"order_id": "B", "origin": "S", "destination": "T", "priority": 1, "pallets": 1},
        ],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert result["served_counts"] == {"0": 0, "1": 2, "2": 0} and result["total_cost"] == 9
    assert_plan_consistent(data, result)


def test_opening_fee_can_overrule_individually_cheapest_lanes():
    '''FIX-03: "所有启用线路开线费与订单变动费之和。"'''
    data = {
        "nodes": ["S", "A", "T"],
        "lanes": [
            {"lane_id": "DIRECT", "from": "S", "to": "T", "capacity": 2, "open_fee": 12, "unit_cost": 0},
            {"lane_id": "SA", "from": "S", "to": "A", "capacity": 2, "open_fee": 3, "unit_cost": 0},
            {"lane_id": "AT", "from": "A", "to": "T", "capacity": 2, "open_fee": 0, "unit_cost": 0},
        ],
        "orders": [
            {"order_id": "A", "origin": "S", "destination": "T", "priority": 0, "pallets": 1},
            {"order_id": "B", "origin": "S", "destination": "T", "priority": 0, "pallets": 1},
        ],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert result["total_cost"] == 3 and [row["path"] for row in result["orders"]] == [["SA", "AT"], ["SA", "AT"]]
    assert_plan_consistent(data, result)


def test_whole_order_cannot_be_split_across_individually_insufficient_paths():
    '''CAP-02: "每张订单只能整体接收或整体拒收。"'''
    data = {
        "nodes": ["S", "A", "B", "T"],
        "lanes": [
            {"lane_id": "SA", "from": "S", "to": "A", "capacity": 2, "open_fee": 0, "unit_cost": 0},
            {"lane_id": "AT", "from": "A", "to": "T", "capacity": 2, "open_fee": 0, "unit_cost": 0},
            {"lane_id": "SB", "from": "S", "to": "B", "capacity": 2, "open_fee": 0, "unit_cost": 0},
            {"lane_id": "BT", "from": "B", "to": "T", "capacity": 2, "open_fee": 0, "unit_cost": 0},
        ],
        "orders": [{"order_id": "WHOLE", "origin": "S", "destination": "T", "priority": 0, "pallets": 3, "allowed_waves": ["early"]}],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert result["served_counts"] == {"0": 0, "1": 0, "2": 0} and result["orders"][0] == {"order_id": "WHOLE", "accepted": False, "wave": None, "path": []}
    assert_plan_consistent(data, result)


def test_partial_capacity_assignment_prefers_feasible_order_subset():
    '''CAP-02: "所有订单共用同一班次的线路容量。"'''
    data = {
        "nodes": ["S", "T"],
        "lanes": [{"lane_id": "ONLY", "from": "S", "to": "T", "capacity": 3, "open_fee": 1, "unit_cost": 2}],
        "orders": [
            {"order_id": "BIG", "origin": "S", "destination": "T", "priority": 1, "pallets": 3, "allowed_waves": ["early"]},
            {"order_id": "SMALL", "origin": "S", "destination": "T", "priority": 1, "pallets": 2, "allowed_waves": ["early"]},
        ],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert result["served_counts"] == {"0": 0, "1": 1, "2": 0}
    assert result["total_cost"] == 5 and result["lane_loads"][0]["early_load"] + result["lane_loads"][0]["late_load"] == 2
    assert [row["order_id"] for row in result["orders"] if row["accepted"]] == ["SMALL"]
    assert_plan_consistent(data, result)


def test_tie_break_uses_lane_ids_not_input_lane_order():
    '''TIE-04: "逐单比较路径的 `lane_id` 序列（ASCII 字典序）；"'''
    data = {
        "nodes": ["S", "T"],
        "lanes": [
            {"lane_id": "Z", "from": "S", "to": "T", "capacity": 1, "open_fee": 0, "unit_cost": 0},
            {"lane_id": "A", "from": "S", "to": "T", "capacity": 1, "open_fee": 0, "unit_cost": 0},
        ],
        "orders": [{"order_id": "O", "origin": "S", "destination": "T", "priority": 2, "pallets": 1}],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert result["orders"][0]["path"] == ["A"]
    assert [row["opened"] for row in result["lane_loads"]] == [False, True]


def test_tie_break_is_stable_under_order_input_permutation():
    '''TIE-04: "将订单按 `order_id` 升序排列"'''
    lanes = [
        {"lane_id": "A", "from": "S", "to": "T", "capacity": 1, "open_fee": 0, "unit_cost": 0},
        {"lane_id": "B", "from": "S", "to": "T", "capacity": 1, "open_fee": 0, "unit_cost": 0},
    ]
    orders = [
        {"order_id": "X", "origin": "S", "destination": "T", "priority": 0, "pallets": 1, "allowed_waves": ["early"]},
        {"order_id": "Y", "origin": "S", "destination": "T", "priority": 0, "pallets": 1, "allowed_waves": ["early"]},
    ]
    first, out_first, exists_first, _ = run({"nodes": ["S", "T"], "lanes": lanes, "orders": orders})
    second, out_second, exists_second, _ = run({"nodes": ["S", "T"], "lanes": lanes, "orders": list(reversed(orders))})
    by_id_first = {row["order_id"]: row["path"] for row in json.loads(out_first)["orders"]}
    by_id_second = {row["order_id"]: row["path"] for row in json.loads(out_second)["orders"]}
    assert first.returncode == second.returncode == 0 and exists_first and exists_second
    assert by_id_first == by_id_second == {"X": ["A"], "Y": ["B"]}


def test_unreachable_order_is_rejected_without_changing_other_service():
    '''PRI-01: "输入合法但无订单可承运时，应成功输出全拒收的零费用计划。"'''
    data = {
        "nodes": ["S", "T", "X"],
        "lanes": [{"lane_id": "ST", "from": "S", "to": "T", "capacity": 1, "open_fee": 4, "unit_cost": 2}],
        "orders": [
            {"order_id": "OK", "origin": "S", "destination": "T", "priority": 2, "pallets": 1},
            {"order_id": "MISS", "origin": "X", "destination": "T", "priority": 0, "pallets": 1},
        ],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert result["served_counts"] == {"0": 0, "1": 0, "2": 1}
    assert [row["accepted"] for row in result["orders"]] == [True, False]


def test_exposure_boundary_selects_a_feasible_route():
    '''THERM-09: "exposure_minutes 之和不得超过该订单的 `max_exposure_minutes`。"'''
    data = {
        "nodes": ["S", "A", "T"],
        "wave_energy_budget": {"early": 20, "late": 20},
        "lanes": [
            {"lane_id": "DIRECT", "from": "S", "to": "T", "early_capacity": 1, "late_capacity": 1, "open_fee": 0, "unit_cost": 0, "transit_minutes": 1, "exposure_minutes": 6, "cooling_units_per_pallet": 0},
            {"lane_id": "SA", "from": "S", "to": "A", "early_capacity": 1, "late_capacity": 1, "open_fee": 2, "unit_cost": 1, "transit_minutes": 2, "exposure_minutes": 2, "cooling_units_per_pallet": 0},
            {"lane_id": "AT", "from": "A", "to": "T", "early_capacity": 1, "late_capacity": 1, "open_fee": 3, "unit_cost": 1, "transit_minutes": 2, "exposure_minutes": 3, "cooling_units_per_pallet": 0},
        ],
        "orders": [{"order_id": "O", "origin": "S", "destination": "T", "priority": 0, "pallets": 1, "sla_minutes": 4, "max_exposure_minutes": 5, "allowed_waves": ["early"]}],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert result["orders"][0]["accepted"] is True
    assert result["orders"][0]["path"] == ["SA", "AT"]
    assert result["served_counts"] == {"0": 1, "1": 0, "2": 0}
    assert result["total_cost"] == 7
    assert result["wave_energy_loads"] == {"early": 0, "late": 0}


def test_wave_energy_is_bounded_per_wave_and_shared_by_orders():
    '''COOL-10: "每个波次的冷却能源用量，等于该波次所有接收订单逐单计算的 `pallets × 路径各线路 cooling_units_per_pallet 之和`，不得超过对应的 `wave_energy_budget`。"'''
    data = {
        "nodes": ["S", "T"],
        "wave_energy_budget": {"early": 2, "late": 2},
        "lanes": [{"lane_id": "L", "from": "S", "to": "T", "early_capacity": 3, "late_capacity": 3, "open_fee": 7, "unit_cost": 5, "transit_minutes": 1, "exposure_minutes": 0, "cooling_units_per_pallet": 2}],
        "orders": [
            {"order_id": "P0-E", "origin": "S", "destination": "T", "priority": 0, "pallets": 1, "sla_minutes": 1, "max_exposure_minutes": 0, "allowed_waves": ["early"]},
            {"order_id": "P1-E", "origin": "S", "destination": "T", "priority": 1, "pallets": 1, "sla_minutes": 1, "max_exposure_minutes": 0, "allowed_waves": ["early"]},
            {"order_id": "P1-L", "origin": "S", "destination": "T", "priority": 1, "pallets": 1, "sla_minutes": 1, "max_exposure_minutes": 0, "allowed_waves": ["late"]},
        ],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert result["served_counts"] == {"0": 1, "1": 1, "2": 0}
    assert [row["accepted"] for row in result["orders"]] == [True, False, True]
    assert [row["wave"] for row in result["orders"]] == ["early", None, "late"]
    assert result["wave_energy_loads"] == {"early": 2, "late": 2}
    assert result["lane_loads"] == [{"lane_id": "L", "early_load": 1, "late_load": 1, "opened": True}]
    assert result["total_cost"] == 17


def test_output_shape_and_input_order_are_preserved():
    '''VALID-05: "输出只能包含 `served_counts`、`total_cost`、`orders`、`lane_loads`、`wave_energy_loads`。"'''
    data = {
        "nodes": ["S", "T"],
        "lanes": [
            {"lane_id": "Z", "from": "S", "to": "T", "capacity": 2, "open_fee": 0, "unit_cost": 1},
            {"lane_id": "A", "from": "S", "to": "T", "capacity": 2, "open_fee": 0, "unit_cost": 2},
        ],
        "orders": [
            {"order_id": "Z-ORDER", "origin": "S", "destination": "T", "priority": 1, "pallets": 1},
            {"order_id": "A-ORDER", "origin": "S", "destination": "T", "priority": 1, "pallets": 1},
        ],
    }
    process, output, exists, leftovers = run(data)
    result = json.loads(output)
    assert process.returncode == 0 and exists and leftovers == []
    assert set(result) == {"served_counts", "total_cost", "orders", "lane_loads", "wave_energy_loads"}
    assert [row["order_id"] for row in result["orders"]] == ["Z-ORDER", "A-ORDER"]
    assert [row["lane_id"] for row in result["lane_loads"]] == ["Z", "A"]


def test_identical_input_produces_identical_plan():
    '''VALID-05: "相同输入必须产生相同计划。"'''
    data = {
        "nodes": ["S", "T"],
        "lanes": [{"lane_id": "L", "from": "S", "to": "T", "capacity": 1, "open_fee": 3, "unit_cost": 4}],
        "orders": [{"order_id": "O", "origin": "S", "destination": "T", "priority": 0, "pallets": 1}],
    }
    first, bytes_first, exists_first, left_first = run(data)
    second, bytes_second, exists_second, left_second = run(data)
    assert first.returncode == second.returncode == 0
    assert exists_first and exists_second and left_first == left_second == []
    assert json.loads(bytes_first) == json.loads(bytes_second)


def test_rejects_duplicate_json_key_without_creating_output():
    '''VALID-05: "任何 JSON 对象出现重复键、缺键、多余键、错误类型、布尔数值或越界值时，输入无效。"'''
    raw = '{"nodes":["S","T"],"nodes":["S","X"],"lanes":[],"orders":[]}'
    process, output, exists, leftovers = run({}, raw=raw)
    assert process.returncode != 0 and output is None and not exists and leftovers == []


def test_rejects_boolean_capacity_and_preserves_sentinel():
    '''VALID-05: "容量为 1 至 32 的整数"'''
    data = {
        "nodes": ["S", "T"],
        "lanes": [{"lane_id": "L", "from": "S", "to": "T", "capacity": True, "open_fee": 0, "unit_cost": 0}],
        "orders": [{"order_id": "O", "origin": "S", "destination": "T", "priority": 0, "pallets": 1}],
    }
    sentinel = b"prior-dispatch\x00"
    process, output, exists, leftovers = run(data, sentinel=sentinel)
    assert process.returncode != 0 and output == sentinel and exists and leftovers == []


def test_rejects_fractional_integer_field():
    '''VALID-05: "两个波次容量均为 1 至 32 的整数"'''
    raw = '{"nodes":["S","T"],"wave_energy_budget":{"early":10,"late":10},"lanes":[{"lane_id":"L","from":"S","to":"T","early_capacity":1.0,"late_capacity":1,"open_fee":0,"unit_cost":0,"transit_minutes":1,"exposure_minutes":0,"cooling_units_per_pallet":0}],"orders":[{"order_id":"O","origin":"S","destination":"T","priority":0,"pallets":1,"sla_minutes":1,"max_exposure_minutes":1,"allowed_waves":["early"]}]}'
    process, output, exists, leftovers = run({}, raw=raw)
    assert process.returncode != 0 and output is None and not exists and leftovers == []
    raw = '{"nodes":["S","T"],"wave_energy_budget":{"early":10,"late":10},"lanes":[{"lane_id":"L","from":"S","to":"T","early_capacity":1,"late_capacity":1,"open_fee":0,"unit_cost":0,"transit_minutes":1.5,"exposure_minutes":0,"cooling_units_per_pallet":0}],"orders":[{"order_id":"O","origin":"S","destination":"T","priority":0,"pallets":1,"sla_minutes":1,"max_exposure_minutes":1,"allowed_waves":["early"]}]}'
    process, output, exists, leftovers = run({}, raw=raw)
    assert process.returncode != 0 and output is None and not exists and leftovers == []
    raw = '{"nodes":["S","T"],"wave_energy_budget":{"early":10,"late":10},"lanes":[{"lane_id":"L","from":"S","to":"T","early_capacity":1,"late_capacity":1,"open_fee":0,"unit_cost":0,"transit_minutes":1,"exposure_minutes":0,"cooling_units_per_pallet":0}],"orders":[{"order_id":"O","origin":"S","destination":"T","priority":0,"pallets":1,"sla_minutes":1,"max_exposure_minutes":1,"allowed_waves":[]} ]}'
    process, output, exists, leftovers = run({}, raw=raw)
    assert process.returncode != 0 and output is None and not exists and leftovers == []


def test_rejects_duplicate_identifier_and_late_invalid_order_atomically():
    '''VALID-05: "`order_id` 唯一且符合 `[A-Z0-9-]{1,16}`"'''
    data = {
        "nodes": ["S", "T"],
        "lanes": [{"lane_id": "L", "from": "S", "to": "T", "capacity": 8, "open_fee": 0, "unit_cost": 0}],
        "orders": [
            {"order_id": "O", "origin": "S", "destination": "T", "priority": 0, "pallets": 1},
            {"order_id": "O", "origin": "S", "destination": "T", "priority": 1, "pallets": 1},
        ],
    }
    sentinel = b"old-version"
    process, output, exists, leftovers = run(data, sentinel=sentinel)
    assert process.returncode != 0 and output == sentinel and exists and leftovers == []
    data["orders"][1]["order_id"] = "LATE"
    data["orders"][1]["priority"] = 8
    process, output, exists, leftovers = run(data)
    assert process.returncode != 0 and output is None and not exists and leftovers == []


def test_atomic_replace_failure_preserves_old_report(monkeypatch):
    '''VALID-05: "拒收或发布失败不得创建或改动目标文件，也不得留下临时产物。"'''
    import importlib.util

    data = {
        "nodes": ["S", "T"],
        "wave_energy_budget": {"early": 10, "late": 10},
        "lanes": [{"lane_id": "L", "from": "S", "to": "T", "early_capacity": 1, "late_capacity": 1, "open_fee": 0, "unit_cost": 0, "transit_minutes": 1, "exposure_minutes": 0, "cooling_units_per_pallet": 0}],
        "orders": [{"order_id": "O", "origin": "S", "destination": "T", "priority": 0, "pallets": 1, "sla_minutes": 1, "max_exposure_minutes": 1, "allowed_waves": ["early"]}],
    }
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        input_path, output_path = root / "input.json", root / "output.json"
        input_path.write_text(json.dumps(data), encoding="utf-8")
        sentinel = b"previous-report"
        output_path.write_bytes(sentinel)
        monkeypatch.setattr(os, "replace", lambda _src, _dst: (_ for _ in ()).throw(OSError("injected")))
        spec = importlib.util.spec_from_file_location("candidate_solver", APP)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setattr(sys, "argv", [APP, str(input_path), str(output_path)])
        status = module.main()
        assert status != 0 and output_path.read_bytes() == sentinel
        assert sorted(path.name for path in root.iterdir()) == ["input.json", "output.json"]
