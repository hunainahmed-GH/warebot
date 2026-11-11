"""
app.py  -  WareBot Flask backend (Backend layer)

Only exposes the logic layer (models / agents / search) as JSON routes.
No AI logic here, no HTML building. The dashboard (Module 5) calls these routes.

Run:  python app.py      ->  http://127.0.0.1:5000
"""
import os
import random
import threading
from collections import defaultdict
from functools import lru_cache

from flask import Flask, jsonify, request, render_template

from logic.models import (Warehouse, Order, PickerRobot, TransportRobot,
                          PENDING, ASSIGNED, IN_TRANSIT, DELIVERED)
from logic.search import ALGORITHMS, BFS, DFS, get_algorithm, compare, benchmark
from logic.agents import GoalBasedAgent, LearningAgent, SimpleReflexAgent

# Keep the Flask instance as a direct top-level assignment so Vercel's Flask
# runtime can discover it. Vercel serves assets from /public; locally Flask
# serves the same root-level asset URLs from /static.
_on_vercel = bool(os.environ.get("VERCEL"))
app = Flask(__name__, static_folder=None if _on_vercel else "static",
            static_url_path=None if _on_vercel else "")

AGENT_TYPES = {"reflex": SimpleReflexAgent, "goal": GoalBasedAgent, "learning": LearningAgent}
HISTORY_LIMIT = 200
LOG_LIMIT = 60


class ApiError(Exception):
    """Bad request from the dashboard -> JSON error with status 400."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


@app.errorhandler(ApiError)
def handle_api_error(e):
    return jsonify({"error": e.message}), e.status


@app.errorhandler(404)
def handle_404(_):
    return jsonify({"error": "not found"}), 404


# ---------------------------------------------------------------------------
# Simulation state (one warehouse per server process)
# ---------------------------------------------------------------------------
class Simulation:
    """Holds the warehouse, agents, results history and event log."""

    def __init__(self, rows=15, cols=15, density=0.15, seed=7):
        self.reset(rows, cols, density, seed)

    # ---- setup
    def reset(self, rows=15, cols=15, density=0.15, seed=7, with_orders=True):
        self.rng = random.Random(seed)
        self.seed = seed
        self.next_order_id = 101
        self.history = []                 # one entry per process_order() call
        self.log = []                     # human-readable events for the dashboard
        self.wh = Warehouse(rows, cols)
        self.wh.generate_obstacles(density, seed=seed)
        self.agents = {}
        for rid, cls, agent_cls in [(1, PickerRobot, GoalBasedAgent), (2, PickerRobot, LearningAgent),
                                    (3, TransportRobot, GoalBasedAgent), (4, TransportRobot, LearningAgent)]:
            self.agents[rid] = agent_cls(cls(rid), self.wh, "A*")
        if with_orders:
            for _ in range(6):
                self.new_random_order()
        self.add_log(f"Warehouse reset ({rows}x{cols}, density {density}, seed {seed})")

    # ---- helpers
    def add_log(self, text):
        self.log.append(text)
        del self.log[:-LOG_LIMIT]

    def reachable_cells(self):
        """Free cells reachable from the charging station (flood fill)."""
        start, seen, stack = self.wh.charging_station, set(), [self.wh.charging_station]
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            stack.extend(n for n in self.wh.neighbors(c) if n not in seen)
        return seen

    def new_random_order(self, priority=None):
        cells = sorted(self.reachable_cells() - {self.wh.charging_station})
        if len(cells) < 2:
            raise ApiError("not enough free cells for an order")
        src, dst = self.rng.sample(cells, 2)
        return self.create_order(src, dst, priority or self.rng.randint(1, 3))

    def create_order(self, source, destination, priority=1):
        for name, c in (("source", source), ("destination", destination)):
            if not self.wh.is_free(c):
                raise ApiError(f"{name} {list(c)} is not a free cell")
        if source == destination:
            raise ApiError("source and destination must differ")
        order = Order(self.next_order_id, source, destination, priority)
        self.next_order_id += 1
        self.wh.add_order(order)
        self.add_log(f"Order #{order.order_id} created {list(source)} -> {list(destination)} (priority {priority})")
        return order

    def order_cells(self):
        """Cells that open orders still need (cannot become shelves)."""
        cells = set()
        for o in self.wh.orders:
            if o.status != DELIVERED:
                cells |= {o.source, o.destination}
        return cells

    def robot_cells(self):
        return {a.robot.get_position() for a in self.agents.values()}

    # ---- running agents
    def record(self, agent, result):
        entry = {"robot_id": agent.robot.id, "agent": agent.__class__.__name__,
                 "algorithm": result["algorithm"], "order_id": result["order_id"],
                 "success": result["success"], "steps": result["steps"],
                 "nodes": result["nodes"], "replans": result["replans"]}
        self.history.append(entry)
        del self.history[:-HISTORY_LIMIT]
        if result["success"]:
            self.add_log(f"Robot {agent.robot.id} ({agent.algorithm}): order #{result['order_id']} "
                         f"-> {result['status']} in {result['steps']} steps, {result['nodes']} nodes")
        else:
            self.add_log(f"Robot {agent.robot.id}: order #{result['order_id']} failed ({result['reason']})")

    def step_agent(self, agent):
        """One agent cycle. Returns (outcome dict, made_progress)."""
        if isinstance(agent, SimpleReflexAgent):            # reflex agents only react, no planning
            action = agent.act(agent.perceive())
            if action["action"] == "go_charge":
                out = {**action, **agent.go_charge()}
                self.add_log(f"Robot {agent.robot.id} (reflex): low battery -> charging")
                return out, out["ok"]
            return action, False
        out = agent.step()
        if out["action"] == "process_order":
            self.record(agent, out)
            return out, out["success"]
        if out["action"] == "go_charge":
            self.add_log(f"Robot {agent.robot.id}: low battery -> charging")
            return out, out["ok"]
        return out, False

    def run_all(self, max_passes=50):
        """Run every non-reflex agent until no more progress (pickers then transporters)."""
        results = []
        agents = sorted(self.agents.values(), key=lambda a: isinstance(a.robot, TransportRobot))
        for _ in range(max_passes):
            progress = False
            for agent in agents:
                if isinstance(agent, SimpleReflexAgent):
                    continue
                out, ok = self.step_agent(agent)
                if out["action"] == "process_order":
                    results.append({"robot_id": agent.robot.id, **out})
                progress |= ok
            if not progress:
                break
        return results

    # ---- views
    def kpis(self):
        orders = self.wh.orders
        done = [h for h in self.history if h["success"]]
        return {"total_orders": len(orders),
                "delivered": sum(o.status == DELIVERED for o in orders),
                "pending": sum(o.status == PENDING for o in orders),
                "in_transit": sum(o.status == IN_TRANSIT for o in orders),
                "robots_total": len(self.agents),
                "robots_active": sum(a.robot.status not in ("Idle", "Stranded") for a in self.agents.values()),
                "avg_path_length": round(sum(h["steps"] for h in done) / len(done), 1) if done else 0}

    def live_analytics(self):
        """Average results per algorithm from real runs of the agents."""
        groups = defaultdict(list)
        for h in self.history:
            if h["success"]:
                groups[h["algorithm"]].append(h)
        return {name: {"runs": len(rows),
                       "avg_steps": round(sum(r["steps"] for r in rows) / len(rows), 1),
                       "avg_nodes": round(sum(r["nodes"] for r in rows) / len(rows), 1)}
                for name, rows in groups.items()}

    def state(self):
        return {"warehouse": self.wh.to_dict(),
                "robots": [{**a.robot.to_dict(), "agent": a.to_dict()} for a in self.agents.values()],
                "orders": [o.to_dict() for o in self.wh.orders],
                "kpis": self.kpis(), "log": self.log[-20:]}


sim = Simulation()
lock = threading.RLock()                # one request touches the simulation at a time


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------
def body():
    return request.get_json(silent=True) or {}


def parse_cell(value, name="cell"):
    try:
        r, c = int(value[0]), int(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        raise ApiError(f"{name} must be [row, col]")
    if not sim.wh.in_bounds((r, c)):
        raise ApiError(f"{name} {[r, c]} is outside the grid")
    return (r, c)


def parse_algorithm(name):
    if name not in ALGORITHMS:
        raise ApiError(f"algorithm must be one of {list(ALGORITHMS)}")
    return name


def parse_number(value, name, lo, hi, cast=float):
    try:
        v = cast(value)
    except (TypeError, ValueError):
        raise ApiError(f"{name} must be a number")
    if not lo <= v <= hi:
        raise ApiError(f"{name} must be between {lo} and {hi}")
    return v


def get_agent(robot_id):
    agent = sim.agents.get(robot_id)
    if agent is None:
        raise ApiError(f"robot {robot_id} not found", 404)
    return agent


def get_order(order_id):
    for o in sim.wh.orders:
        if o.order_id == order_id:
            return o
    raise ApiError(f"order {order_id} not found", 404)


def clean(result):
    """JSON-safe copy of a search result (cells as lists happen automatically)."""
    return {**result, "path": [list(c) for c in result["path"]],
            "explored_order": [list(c) for c in result["explored_order"]]}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    if os.path.exists(os.path.join(app.root_path, "templates", "index.html")):
        return render_template("index.html")
    return jsonify({"app": "WareBot API", "note": "dashboard (Module 5) not added yet",
                    "routes": sorted(str(r) for r in app.url_map.iter_rules() if str(r).startswith("/api"))})


# ---------------------------------------------------------------------------
# State / setup
# ---------------------------------------------------------------------------
@app.get("/api/state")
def api_state():
    with lock:
        return jsonify(sim.state())


@app.post("/api/reset")
def api_reset():
    d = body()
    with lock:
        sim.reset(rows=parse_number(d.get("rows", 15), "rows", 5, 30, int),
                  cols=parse_number(d.get("cols", 15), "cols", 5, 30, int),
                  density=parse_number(d.get("density", 0.15), "density", 0, 0.5),
                  seed=parse_number(d.get("seed", 7), "seed", 0, 10**9, int),
                  with_orders=bool(d.get("with_orders", True)))
        return jsonify(sim.state())


@app.post("/api/obstacles/generate")
def api_generate_obstacles():
    """New random shelves. Robot cells and open-order cells stay free."""
    d = body()
    with lock:
        density = parse_number(d.get("density", 0.2), "density", 0, 0.5)
        seed = d.get("seed")
        keep = sim.robot_cells() | sim.order_cells()
        sim.wh.generate_obstacles(density, seed=None if seed is None else int(seed), keep_free=keep)
        sim.add_log(f"Shelves regenerated (density {density})")
        return jsonify(sim.state())


@app.post("/api/obstacles/toggle")
def api_toggle_obstacle():
    """Click on a cell in the grid: add / remove a shelf."""
    with lock:
        cell = parse_cell(body().get("cell"))
        if cell == sim.wh.charging_station or cell in sim.robot_cells() or cell in sim.order_cells():
            raise ApiError("cell is used by a robot, order or the charging station")
        if sim.wh.is_free(cell):
            sim.wh.add_obstacle(*cell)
        else:
            sim.wh.remove_obstacle(*cell)
        return jsonify({"cell": list(cell), "obstacle": not sim.wh.is_free(cell),
                        "warehouse": sim.wh.to_dict()})


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
@app.get("/api/orders")
def api_orders():
    with lock:
        return jsonify([o.to_dict() for o in sim.wh.orders])


@app.post("/api/orders")
def api_create_order():
    d = body()
    with lock:
        if d.get("random"):
            order = sim.new_random_order(d.get("priority"))
        else:
            priority = parse_number(d.get("priority", 1), "priority", 1, 5, int)
            order = sim.create_order(parse_cell(d.get("source"), "source"),
                                     parse_cell(d.get("destination"), "destination"), priority)
        return jsonify(order.to_dict()), 201


# ---------------------------------------------------------------------------
# Robots / agents
# ---------------------------------------------------------------------------
@app.get("/api/robots")
def api_robots():
    with lock:
        return jsonify([{**a.robot.to_dict(), "agent": a.to_dict()} for a in sim.agents.values()])


@app.post("/api/robots/<int:robot_id>/config")
def api_robot_config(robot_id):
    """Change the agent type (reflex / goal / learning) and/or the algorithm of one robot."""
    d = body()
    with lock:
        agent = get_agent(robot_id)
        if "agent" in d:
            if d["agent"] not in AGENT_TYPES:
                raise ApiError(f"agent must be one of {list(AGENT_TYPES)}")
            if type(agent) is not AGENT_TYPES[d["agent"]]:
                agent = AGENT_TYPES[d["agent"]](agent.robot, sim.wh, agent.algorithm)
                sim.agents[robot_id] = agent
        if "algorithm" in d:
            agent.algorithm = parse_algorithm(d["algorithm"])
        sim.add_log(f"Robot {robot_id}: {agent.__class__.__name__} using {agent.algorithm}")
        return jsonify({**agent.robot.to_dict(), "agent": agent.to_dict()})


@app.post("/api/robots/<int:robot_id>/charge")
def api_robot_charge(robot_id):
    with lock:
        agent = get_agent(robot_id)
        if body().get("rescue"):                   # stranded robot: carried to the charger by staff
            agent.robot.set_position(sim.wh.charging_station)
            agent.robot.recharge()
            out = {"ok": True, "reason": None, "rescued": True}
            sim.add_log(f"Robot {robot_id}: rescued to charging station")
        else:
            out = agent.go_charge()
            sim.add_log(f"Robot {robot_id}: manual charge -> {'ok' if out['ok'] else out['reason']}")
        return jsonify({**out, "robot": agent.robot.to_dict()})


@app.post("/api/robots/<int:robot_id>/step")
def api_robot_step(robot_id):
    """One agent cycle (perceive -> act -> execute). Returns the path for animation."""
    with lock:
        agent = get_agent(robot_id)
        out, _ = sim.step_agent(agent)
        out.pop("order", None)                     # Order object is not JSON, state has it
        if "path" in out:
            out["path"] = [list(c) for c in out["path"]]
        return jsonify({"result": out, "state": sim.state()})


@app.post("/api/algorithm")
def api_set_algorithm():
    """Dashboard dropdown: all robots use this algorithm."""
    with lock:
        name = parse_algorithm(body().get("algorithm"))
        for a in sim.agents.values():
            a.algorithm = name
        sim.add_log(f"All robots now use {name}")
        return jsonify({"algorithm": name})


@app.post("/api/run")
def api_run():
    """Run every robot until all orders are done (or nothing more is possible)."""
    with lock:
        results = sim.run_all()
        for r in results:
            r["path"] = [list(c) for c in r["path"]]
        return jsonify({"results": results, "state": sim.state()})


# ---------------------------------------------------------------------------
# Search (Lab 3 / 4 / 5) - used by the Warehouse tab animation and Analytics
# ---------------------------------------------------------------------------
@app.post("/api/path")
def api_path():
    """Find a path with one algorithm. Result includes explored_order for animation."""
    d = body()
    with lock:
        res = get_algorithm(parse_algorithm(d.get("algorithm", "A*"))).find_path(
            sim.wh, parse_cell(d.get("start"), "start"), parse_cell(d.get("goal"), "goal"))
        return jsonify(clean(res))


@app.post("/api/compare")
def api_compare():
    """BFS vs DFS vs A* on the same start/goal of the current grid."""
    d = body()
    with lock:
        res = compare(sim.wh, parse_cell(d.get("start"), "start"), parse_cell(d.get("goal"), "goal"))
        return jsonify({name: clean(r) for name, r in res.items()})


@app.post("/api/scan")
def api_scan():
    """DFS warehouse scan: visit order of every reachable cell."""
    with lock:
        start = parse_cell(body().get("start", list(sim.wh.charging_station)), "start")
        order = DFS().scan(sim.wh, start)
        return jsonify({"start": list(start), "cells_visited": len(order),
                        "explored_order": [list(c) for c in order]})


@app.post("/api/nearest")
def api_nearest():
    """BFS nearest-goal search: closest pending order source from a start cell."""
    with lock:
        start = parse_cell(body().get("start", list(sim.wh.charging_station)), "start")
        sources = {o.source: o.order_id for o in sim.wh.pending_orders()}
        cell, path = BFS().nearest(sim.wh, start, sources.keys())
        if cell is None:
            return jsonify({"found": False, "order_id": None, "cell": None, "path": []})
        return jsonify({"found": True, "order_id": sources[cell], "cell": list(cell),
                        "path": [list(c) for c in path]})


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
@lru_cache(maxsize=8)
def _benchmark(rows, cols, trials):
    return benchmark(rows, cols, trials=trials)


@app.get("/api/analytics")
def api_analytics():
    """benchmark = sparse vs dense random grids (real runs); live = agents' own runs so far."""
    rows = parse_number(request.args.get("rows", 15), "rows", 5, 30, int)
    cols = parse_number(request.args.get("cols", 15), "cols", 5, 30, int)
    trials = parse_number(request.args.get("trials", 20), "trials", 1, 100, int)
    with lock:
        bench = _benchmark(rows, cols, trials)
        return jsonify({"benchmark": {str(d): v for d, v in bench.items()},
                        "live": sim.live_analytics(), "kpis": sim.kpis()})


if __name__ == "__main__":
    app.run(debug=True)
