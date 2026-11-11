"""
search.py  -  Path finding on the warehouse grid
Lab 3: BFS | Lab 4: DFS | Lab 5: A* (Manhattan heuristic, traffic-aware)

Every algorithm implements find_path(warehouse, start, goal) and returns a dict:
  found, path, path_length (steps), cost (sum of cell costs), nodes_explored,
  explored_order (cells in visit order, for the dashboard animation), time_ms
No UI code here.
"""
import heapq
import random
import time
from abc import ABC, abstractmethod
from collections import deque


class SearchAlgorithm(ABC):
    name = "Base"

    @abstractmethod
    def find_path(self, warehouse, start, goal):
        """Return a result dict (see module docstring)."""

    # ---- helpers shared by all algorithms
    @staticmethod
    def _rebuild(parent, goal):
        path, cell = [], goal
        while cell is not None:
            path.append(cell)
            cell = parent[cell]
        return path[::-1]

    def _result(self, warehouse, path, explored, t0):
        found = path is not None
        cost = sum(warehouse.cost(c) for c in path[1:]) if found else None
        return {"algorithm": self.name, "found": found, "path": path or [],
                "path_length": len(path) - 1 if found else 0, "cost": cost,
                "nodes_explored": len(explored), "explored_order": explored,
                "time_ms": round((time.perf_counter() - t0) * 1000, 4)}


# ---- BFS ------------------------------------------------------------------
class BFS(SearchAlgorithm):
    name = "BFS"

    def find_path(self, warehouse, start, goal):
        t0 = time.perf_counter()
        if not warehouse.is_free(start) or not warehouse.is_free(goal):
            return self._result(warehouse, None, [], t0)
        parent, queue, explored = {start: None}, deque([start]), []
        while queue:
            cell = queue.popleft()
            explored.append(cell)
            if cell == goal:
                return self._result(warehouse, self._rebuild(parent, goal), explored, t0)
            for n in warehouse.neighbors(cell):
                if n not in parent:
                    parent[n] = cell
                    queue.append(n)
        return self._result(warehouse, None, explored, t0)

    def nearest(self, warehouse, start, goals):
        """Nearest-goal search: first cell in `goals` reached by BFS (None if unreachable)."""
        goals, parent, queue = set(goals), {start: None}, deque([start])
        while queue:
            cell = queue.popleft()
            if cell in goals:
                return cell, self._rebuild(parent, cell)
            for n in warehouse.neighbors(cell):
                if n not in parent:
                    parent[n] = cell
                    queue.append(n)
        return None, None


# ---- DFS ------------------------------------------------------------------
class DFS(SearchAlgorithm):
    name = "DFS"

    def find_path(self, warehouse, start, goal):
        t0 = time.perf_counter()
        if not warehouse.is_free(start) or not warehouse.is_free(goal):
            return self._result(warehouse, None, [], t0)
        parent, stack, explored, seen = {start: None}, [start], [], set()
        while stack:
            cell = stack.pop()
            if cell in seen:
                continue
            seen.add(cell)
            explored.append(cell)
            if cell == goal:
                return self._result(warehouse, self._rebuild(parent, goal), explored, t0)
            for n in warehouse.neighbors(cell):
                if n not in seen:
                    parent[n] = cell        # last parent wins, still a valid path
                    stack.append(n)
        return self._result(warehouse, None, explored, t0)

    def scan(self, warehouse, start):
        """Warehouse scan: visit every cell reachable from start. Returns visit order."""
        stack, seen, order = [start], set(), []
        while stack:
            cell = stack.pop()
            if cell in seen:
                continue
            seen.add(cell)
            order.append(cell)
            stack.extend(n for n in warehouse.neighbors(cell) if n not in seen)
        return order


# ---- A* -------------------------------------------------------------------
class AStar(SearchAlgorithm):
    name = "A*"

    @staticmethod
    def manhattan(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def find_path(self, warehouse, start, goal):
        t0 = time.perf_counter()
        if not warehouse.is_free(start) or not warehouse.is_free(goal):
            return self._result(warehouse, None, [], t0)
        parent, g = {start: None}, {start: 0}
        heap, counter = [(self.manhattan(start, goal), 0, start)], 0
        closed, explored = set(), []
        while heap:
            _, _, cell = heapq.heappop(heap)
            if cell in closed:
                continue
            closed.add(cell)
            explored.append(cell)
            if cell == goal:
                return self._result(warehouse, self._rebuild(parent, goal), explored, t0)
            for n in warehouse.neighbors(cell):
                new_g = g[cell] + warehouse.cost(n)          # traffic-aware cost
                if n not in g or new_g < g[n]:
                    g[n], parent[n] = new_g, cell
                    counter += 1
                    heapq.heappush(heap, (new_g + self.manhattan(n, goal), counter, n))
        return self._result(warehouse, None, explored, t0)


# ---- registry + comparison helpers ----------------------------------------
ALGORITHMS = {"BFS": BFS, "DFS": DFS, "A*": AStar}


def get_algorithm(name):
    return ALGORITHMS[name]()


def compare(warehouse, start, goal):
    """Run all three algorithms on the same grid; returns {name: result}."""
    return {name: cls().find_path(warehouse, start, goal) for name, cls in ALGORITHMS.items()}


def benchmark(rows=15, cols=15, densities=(0.1, 0.2, 0.3), trials=20, seed=42):
    """
    Average nodes explored / path length / time per algorithm for each obstacle density.
    Used by the Analytics tab and the lab report. Only solvable random grids are counted.
    """
    from logic.models import Warehouse          # local import keeps search.py standalone
    rng = random.Random(seed)
    start, goal = (0, 0), (rows - 1, cols - 1)
    report = {}
    for d in densities:
        sums = {n: {"nodes": 0, "length": 0, "time_ms": 0.0} for n in ALGORITHMS}
        done = 0
        while done < trials:
            wh = Warehouse(rows, cols)
            wh.generate_obstacles(d, seed=rng.random(), keep_free=(start, goal))
            res = compare(wh, start, goal)
            if not all(r["found"] for r in res.values()):
                continue
            for n, r in res.items():
                sums[n]["nodes"] += r["nodes_explored"]
                sums[n]["length"] += r["path_length"]
                sums[n]["time_ms"] += r["time_ms"]
            done += 1
        report[d] = {n: {"avg_nodes": round(s["nodes"] / trials, 1),
                         "avg_length": round(s["length"] / trials, 1),
                         "avg_time_ms": round(s["time_ms"] / trials, 4)} for n, s in sums.items()}
    return report


# ---- quick demo (python logic/search.py) ----------------------------------
if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from logic.models import Warehouse

    wh = Warehouse(10, 10)
    wh.generate_obstacles(0.2, seed=7, keep_free=[(0, 0), (9, 9)])
    print("Single run (0,0) -> (9,9):")
    for name, r in compare(wh, (0, 0), (9, 9)).items():
        print(f"  {name:3} found={r['found']} length={r['path_length']:3} cost={r['cost']} "
              f"nodes={r['nodes_explored']:3} time={r['time_ms']}ms")

    print("\nBenchmark (15x15, 20 trials each):")
    for d, row in benchmark().items():
        print(f"  density {d}:")
        for n, m in row.items():
            print(f"    {n:3} nodes={m['avg_nodes']:6} length={m['avg_length']:5} time={m['avg_time_ms']}ms")
