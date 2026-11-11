"""
agents.py  -  WareBot intelligent agents (Lab 2)

SimpleReflexAgent : condition-action rules (stop on obstacle, go charge when battery low)
GoalBasedAgent    : goal = deliver orders; picks next order by priority, plans with a search algorithm
LearningAgent     : goal-based + remembers busy routes and raises their cost (A* avoids them next time)

An agent CONTROLS one robot. No UI code here.
Percept  = dict from Agent.perceive()  ->  Action = dict {"action": ..., "reason": ...}
"""
from abc import ABC, abstractmethod
from collections import Counter

try:                                            # imported as a package (Flask app)
    from .search import get_algorithm
    from .models import PickerRobot, PENDING, ASSIGNED, IN_TRANSIT
except ImportError:                             # run directly: python logic/agents.py
    from search import get_algorithm
    from models import PickerRobot, PENDING, ASSIGNED, IN_TRANSIT


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


# ---- Agent (abstract) -----------------------------------------------------
class Agent(ABC):
    MAX_REPLANS = 3                     # how many times a blocked robot re-plans

    def __init__(self, robot, warehouse, algorithm="A*"):
        self.robot = robot
        self.warehouse = warehouse
        self.algorithm = algorithm      # 'BFS' | 'DFS' | 'A*'
        if robot not in warehouse.robots:
            warehouse.add_robot(robot)

    # --- sensing
    def perceive(self, blocked_cell=None):
        r = self.robot
        return {"position": r.get_position(), "battery": r.get_battery(),
                "low_battery": r.needs_charging(), "blocked_cell": blocked_cell,
                "pending_orders": self.warehouse.pending_orders()}

    @abstractmethod
    def act(self, percept):
        """Decide what to do. Returns {"action": str, "reason": str, ...}."""

    # --- shared tools (all agents use the search algorithms)
    def plan(self, goal, start=None):
        start = start or self.robot.get_position()
        return get_algorithm(self.algorithm).find_path(self.warehouse, start, goal)

    def go_charge(self):
        """Reflex action: walk to the charging station and recharge."""
        res = self.plan(self.warehouse.charging_station)
        if not res["found"]:
            return {"ok": False, "reason": "no_path_to_charger"}
        out = self.robot.move(res["path"])
        if not out["completed"]:
            self.robot.status = "Stranded"
            return {"ok": False, "reason": out["reason"]}
        self.robot.recharge()
        return {"ok": True, "reason": None, "steps": out["steps"]}

    def to_dict(self):
        return {"type": self.__class__.__name__, "robot_id": self.robot.id, "algorithm": self.algorithm}


# ---- Simple reflex agent --------------------------------------------------
class SimpleReflexAgent(Agent):
    """No memory, no planning: current percept -> action (rules in priority order)."""

    def act(self, percept):
        if percept["blocked_cell"] is not None:                     # rule 1
            return {"action": "stop", "reason": "obstacle", "blocked_cell": percept["blocked_cell"]}
        if percept["low_battery"]:                                  # rule 2
            return {"action": "go_charge", "reason": "low_battery"}
        return {"action": "continue", "reason": None}               # rule 3


# ---- Goal-based agent -----------------------------------------------------
class GoalBasedAgent(Agent):
    """Goal: deliver orders. Chooses next order by priority, plans a route, handles problems."""

    def __init__(self, robot, warehouse, algorithm="A*"):
        super().__init__(robot, warehouse, algorithm)
        self.reflex = SimpleReflexAgent(robot, warehouse, algorithm)   # safety rules reused
        self.delivered = 0

    def _serves(self):
        """Picker works on Pending orders; Transport works on orders already picked (In Transit)."""
        return PENDING if isinstance(self.robot, PickerRobot) else IN_TRANSIT

    def choose_next_order(self, orders=None):
        """Highest priority first; tie -> nearest source; tie -> lowest id."""
        orders = orders if orders is not None else \
            [o for o in self.warehouse.orders if o.status == self._serves()]
        if not orders:
            return None
        pos = self.robot.get_position()
        return min(orders, key=lambda o: (-o.priority, manhattan(pos, o.source), o.order_id))

    def act(self, percept):
        reflex = self.reflex.act(percept)
        if reflex["action"] == "go_charge":
            return reflex
        order = self.choose_next_order()
        if order is None:
            return {"action": "idle", "reason": "no_orders"}
        return {"action": "process_order", "reason": "goal", "order": order}

    def step(self):
        """One full cycle: perceive -> act -> execute. Used by the backend."""
        action = self.act(self.perceive())
        if action["action"] == "go_charge":
            return {"action": "go_charge", **self.go_charge()}
        if action["action"] == "process_order":
            return {"action": "process_order", **self.process_order(action["order"])}
        return action

    # --- movement with re-planning (activity diagram: obstacle -> stop -> recompute)
    def _travel(self, goal, order=None):
        """Plan + move to `goal`. If `order` is given, the robot's work() runs (pick / deliver)."""
        r = self.robot
        info = {"ok": False, "reason": None, "path": [r.get_position()],
                "steps": 0, "nodes": 0, "replans": 0, "charged": False}
        for attempt in range(self.MAX_REPLANS + 1):
            res = self.plan(goal)
            info["nodes"] += res["nodes_explored"]
            if not res["found"]:
                info["reason"] = "no_path"
                return info
            path = res["path"]
            if r.get_battery() < res["path_length"] * r.DRAIN_PER_STEP:      # battery sufficient?
                if info["charged"] or not self.go_charge()["ok"]:
                    info["reason"] = "battery_insufficient"
                    return info
                info["charged"] = True
                continue                                                      # re-plan from charger
            out = r.work(order, path) if order else r.move(path)
            info["steps"] += out["steps"]
            info["path"] += path[:path.index(r.get_position()) + 1][1:]      # cells actually walked
            if out["completed"]:
                info["ok"] = True
                return info
            if out["reason"] == "obstacle":                                   # reflex: stop, re-plan
                self.reflex.act(self.perceive(out["blocked_cell"]))
                info["replans"] += 1
                continue
            info["reason"] = out["reason"]                                    # low_battery / overload
            return info
        info["reason"] = "too_many_replans"
        return info

    def process_order(self, order):
        """Full job for this robot's type. Returns a result dict (order status updated)."""
        prev = order.status
        if prev == PENDING:
            order.update_status(ASSIGNED)
        total = {"path": [], "steps": 0, "nodes": 0, "replans": 0, "charged": False}
        legs = []
        if isinstance(self.robot, PickerRobot):
            legs = [(order.source, order)]                          # go to shelf + pick
        else:
            if prev != IN_TRANSIT:
                legs.append((order.source, None))                   # not picked yet: collect first
            legs.append((order.destination, order))                 # carry + deliver
        reason = None
        for goal, job in legs:
            leg = self._travel(goal, job)
            total["path"] += leg["path"] if not total["path"] else leg["path"][1:]
            for k in ("steps", "nodes", "replans"):
                total[k] += leg[k]
            total["charged"] |= leg["charged"]
            if not leg["ok"]:
                reason = leg["reason"]
                break
        if reason:
            order.status = prev                                     # give the order back
            return {"order_id": order.order_id, "success": False, "reason": reason,
                    "status": order.status, "algorithm": self.algorithm, **total}
        self.delivered += order.status == "Delivered"
        return {"order_id": order.order_id, "success": True, "reason": None,
                "status": order.status, "algorithm": self.algorithm, **total}

    def to_dict(self):
        return {**super().to_dict(), "delivered": self.delivered}


# ---- Learning agent -------------------------------------------------------
class LearningAgent(GoalBasedAgent):
    """
    Goal-based + learning. After each job it records the route; cells used often are
    'busy', so their traffic cost is raised. A* (traffic-aware) then prefers other aisles.
    Note: BFS/DFS ignore cost, so learning only changes behaviour with A*.
    """
    BUSY_THRESHOLD = 3                  # visits before a cell counts as busy
    MAX_PENALTY = 5                     # cap on extra cost per cell

    def __init__(self, robot, warehouse, algorithm="A*"):
        super().__init__(robot, warehouse, algorithm)
        self.route_history = {}         # (start, goal) -> {"trips", "total_time", "best_time"}
        self.cell_usage = Counter()     # cell -> times walked

    def learn(self, route, time):
        """Record a finished route (time = steps taken). Returns cells whose cost was raised."""
        if len(route) < 2:
            return []
        h = self.route_history.setdefault((route[0], route[-1]),
                                          {"trips": 0, "total_time": 0, "best_time": None})
        h["trips"] += 1
        h["total_time"] += time
        h["best_time"] = time if h["best_time"] is None else min(h["best_time"], time)
        raised = []
        for cell in route[1:-1]:                                     # endpoints are unavoidable
            self.cell_usage[cell] += 1
            busy = self.cell_usage[cell] >= self.BUSY_THRESHOLD
            if busy and cell != self.warehouse.charging_station \
                    and self.warehouse.traffic_cost.get(cell, 0) < self.MAX_PENALTY:
                self.warehouse.add_traffic(cell, 1)
                raised.append(cell)
        return raised

    def process_order(self, order):
        result = super().process_order(order)
        result["learned"] = []
        if result["success"]:
            result["learned"] = self.learn(result["path"], result["steps"])
        return result

    def busy_cells(self, top=10):
        return [{"cell": c, "visits": n} for c, n in self.cell_usage.most_common(top)
                if n >= self.BUSY_THRESHOLD]

    def to_dict(self):
        return {**super().to_dict(),
                "routes_learned": len(self.route_history),
                "busy_cells": self.busy_cells()}


# ---- quick demo (python logic/agents.py) ----------------------------------
if __name__ == "__main__":
    try:
        from .models import Warehouse, Order, TransportRobot
    except ImportError:
        from models import Warehouse, Order, TransportRobot

    wh = Warehouse(10, 10)
    for c in range(1, 8):
        wh.add_obstacle(4, c)                                   # a shelf row

    print("== 1. Simple reflex agent")
    rx = SimpleReflexAgent(PickerRobot(1), wh)
    print(rx.act(rx.perceive()))
    print(rx.act(rx.perceive(blocked_cell=(4, 3))))
    rx.robot.set_position((0, 3)); rx.robot.set_battery(10)
    print(rx.act(rx.perceive()))
    print("charging:", rx.go_charge(), "battery:", rx.robot.get_battery())

    print("== 2. Goal-based: priority choice + pick & deliver")
    picker, trans = GoalBasedAgent(PickerRobot(2), wh), GoalBasedAgent(TransportRobot(3), wh)
    for oid, src, dst, pr in [(1, (2, 2), (8, 8), 1), (2, (1, 5), (9, 1), 3), (3, (6, 6), (0, 9), 3)]:
        wh.add_order(Order(oid, src, dst, pr))
    print("next order:", picker.choose_next_order().order_id, "(highest priority, nearest)")
    o = wh.orders[1]
    print("picker :", {k: v for k, v in picker.process_order(o).items() if k != "path"}, o.status)
    print("trans  :", {k: v for k, v in trans.process_order(o).items() if k != "path"}, o.status)

    print("== 3. Obstacle appears mid-route -> stop + re-plan")
    t2 = GoalBasedAgent(TransportRobot(4), wh, "BFS")
    o4 = Order(4, (0, 0), (9, 0)); wh.add_order(o4)
    orig = t2.robot.move
    def sabotage(path, _d={"done": False}):                     # shelf drops onto the path once
        if not _d["done"] and len(path) > 6:
            _d["done"] = True; wh.add_obstacle(*path[5])
        return orig(path)
    t2.robot.move = sabotage
    r = t2.process_order(o4)
    print({k: v for k, v in r.items() if k != "path"})

    print("== 4. Learning agent: busy routes get costlier")
    wh2 = Warehouse(8, 8)
    la = LearningAgent(PickerRobot(5), wh2)
    for i in range(5):
        o = Order(10 + i, (0, 7), (7, 7)); wh2.add_order(o)
        r = la.process_order(o)
        la.robot.set_battery(100); la.robot.set_position((0, 0))
        print(f"trip {i+1}: steps={r['steps']} raised={len(r['learned'])} nodes={r['nodes']}")
    print("traffic cells:", len(wh2.traffic_cost), "| busy:", la.busy_cells(3))
