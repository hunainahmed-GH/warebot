"""
models.py  -  WareBot domain model (Lab 1: OOP, Encapsulation, Polymorphism)

Classes: Package, Order, Warehouse, Robot (abstract), PickerRobot, TransportRobot
No UI code here. Cells are (row, col) tuples. Grid: 0 = free, 1 = obstacle (shelf).
"""
import random
from abc import ABC, abstractmethod

# ---- order statuses -------------------------------------------------------
PENDING, ASSIGNED, IN_TRANSIT, DELIVERED = "Pending", "Assigned", "In Transit", "Delivered"
VALID_STATUSES = (PENDING, ASSIGNED, IN_TRANSIT, DELIVERED)


# ---- Package --------------------------------------------------------------
class Package:
    def __init__(self, package_id, location, weight=1):
        self.package_id = package_id
        self.location = location      # (row, col)
        self.weight = weight

    def to_dict(self):
        return {"package_id": self.package_id, "location": self.location, "weight": self.weight}


# ---- Order ----------------------------------------------------------------
class Order:
    def __init__(self, order_id, source, destination, priority=1, packages=None):
        self.order_id = order_id
        self.source = source              # where the package is picked
        self.destination = destination    # where it must be delivered
        self.priority = priority          # higher number = more urgent
        self.status = PENDING
        self.packages = packages or [Package(order_id, source)]   # composition (1..*)

    def update_status(self, status):
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        self.status = status

    def to_dict(self):
        return {"order_id": self.order_id, "source": self.source,
                "destination": self.destination, "priority": self.priority,
                "status": self.status, "packages": [p.to_dict() for p in self.packages]}


# ---- Warehouse ------------------------------------------------------------
class Warehouse:
    def __init__(self, rows=15, cols=15, charging_station=(0, 0)):
        self.rows, self.cols = rows, cols
        self.grid = [[0] * cols for _ in range(rows)]
        self.traffic_cost = {}                 # cell -> extra cost (busy aisles)
        self.charging_station = charging_station
        self.robots = []                       # contains 1..*
        self.orders = []                       # queues 0..*

    # --- grid / obstacles
    def in_bounds(self, cell):
        r, c = cell
        return 0 <= r < self.rows and 0 <= c < self.cols

    def is_free(self, cell):
        return self.in_bounds(cell) and self.grid[cell[0]][cell[1]] == 0

    def add_obstacle(self, r, c):
        if (r, c) != self.charging_station and self.in_bounds((r, c)):
            self.grid[r][c] = 1

    def remove_obstacle(self, r, c):
        if self.in_bounds((r, c)):
            self.grid[r][c] = 0

    def clear_obstacles(self):
        self.grid = [[0] * self.cols for _ in range(self.rows)]

    def generate_obstacles(self, density=0.2, seed=None, keep_free=()):
        """Random shelves. density = fraction of cells (0.0 - 0.5). keep_free cells stay empty."""
        rng = random.Random(seed)
        self.clear_obstacles()
        protected = set(keep_free) | {self.charging_station}
        for r in range(self.rows):
            for c in range(self.cols):
                if (r, c) not in protected and rng.random() < density:
                    self.grid[r][c] = 1

    # --- used by search algorithms
    def neighbors(self, cell):
        """4-directional free neighbours."""
        r, c = cell
        out = []
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            n = (r + dr, c + dc)
            if self.is_free(n):
                out.append(n)
        return out

    def cost(self, cell):
        """Cost of entering a cell = 1 + traffic penalty."""
        return 1 + self.traffic_cost.get(cell, 0)

    def add_traffic(self, cell, amount=1):
        self.traffic_cost[cell] = self.traffic_cost.get(cell, 0) + amount

    # --- robots / orders
    def add_robot(self, robot):
        robot.warehouse = self
        self.robots.append(robot)

    def add_order(self, order):
        self.orders.append(order)

    def pending_orders(self):
        return [o for o in self.orders if o.status == PENDING]

    def to_dict(self):
        return {"rows": self.rows, "cols": self.cols, "grid": self.grid,
                "charging_station": self.charging_station,
                "traffic_cost": {f"{r},{c}": v for (r, c), v in self.traffic_cost.items()}}


# ---- Robot (abstract) -----------------------------------------------------
class Robot(ABC):
    MAX_BATTERY = 100
    DRAIN_PER_STEP = 1
    LOW_BATTERY = 20

    def __init__(self, robot_id, position=(0, 0), battery=100):
        self.id = robot_id
        self.__battery = battery        # encapsulated (private)
        self.__position = position      # encapsulated (private)
        self.status = "Idle"
        self.warehouse = None

    # getters / setters
    def get_battery(self):
        return self.__battery

    def set_battery(self, value):
        self.__battery = max(0, min(self.MAX_BATTERY, value))

    def get_position(self):
        return self.__position

    def set_position(self, cell):
        self.__position = cell

    def needs_charging(self):
        return self.__battery <= self.LOW_BATTERY

    def recharge(self):
        self.set_battery(self.MAX_BATTERY)
        self.status = "Idle"

    def move(self, path):
        """
        Walk along `path` (list of cells, may start with current position).
        Returns {"completed", "steps", "reason", "blocked_cell"}.
        Stops at an obstacle or when battery is too low (reflex behaviour).
        """
        steps = 0
        for cell in path:
            if cell == self.__position:
                continue
            if self.warehouse and not self.warehouse.is_free(cell):
                return {"completed": False, "steps": steps, "reason": "obstacle", "blocked_cell": cell}
            if self.__battery < self.DRAIN_PER_STEP:
                return {"completed": False, "steps": steps, "reason": "low_battery", "blocked_cell": None}
            self.__position = cell
            self.__battery -= self.DRAIN_PER_STEP
            steps += 1
        return {"completed": True, "steps": steps, "reason": None, "blocked_cell": None}

    @abstractmethod
    def work(self, order, path):
        """Each robot type does its job on an order in its own way (polymorphism)."""

    def to_dict(self):
        return {"id": self.id, "type": self.__class__.__name__, "battery": self.__battery,
                "position": self.__position, "status": self.status}


# ---- PickerRobot ----------------------------------------------------------
class PickerRobot(Robot):
    DRAIN_PER_STEP = 1                  # light, fast robot

    def __init__(self, robot_id, position=(0, 0), battery=100):
        super().__init__(robot_id, position, battery)
        self.carrying = None            # Package

    def move(self, path):
        self.status = "Moving"
        return super().move(path)

    def pick_package(self, order):
        self.carrying = order.packages[0] if order.packages else None
        self.status = "Picked"
        return self.carrying

    def work(self, order, path):
        """Go to the shelf (path ends at order.source) and pick the package."""
        order.update_status(IN_TRANSIT)
        result = self.move(path)
        if result["completed"]:
            self.pick_package(order)
        else:
            self.status = "Blocked" if result["reason"] == "obstacle" else "Low battery"
        return result


# ---- TransportRobot -------------------------------------------------------
class TransportRobot(Robot):
    DRAIN_PER_STEP = 2                  # heavier robot uses more battery

    def __init__(self, robot_id, position=(0, 0), battery=100, load_capacity=10):
        super().__init__(robot_id, position, battery)
        self.load_capacity = load_capacity

    def move(self, path):
        self.status = "Transporting"
        return super().move(path)

    def deliver_package(self, order):
        order.update_status(DELIVERED)
        self.status = "Delivered"

    def work(self, order, path):
        """Carry the package along path (ends at order.destination) and deliver it."""
        if sum(p.weight for p in order.packages) > self.load_capacity:
            self.status = "Overloaded"
            return {"completed": False, "steps": 0, "reason": "overload", "blocked_cell": None}
        order.update_status(IN_TRANSIT)
        result = self.move(path)
        if result["completed"]:
            self.deliver_package(order)
        else:
            self.status = "Blocked" if result["reason"] == "obstacle" else "Low battery"
        return result


# ---- quick demo (python logic/models.py) ----------------------------------
if __name__ == "__main__":
    wh = Warehouse(5, 5)
    wh.add_obstacle(1, 1)
    picker, trans = PickerRobot(1), TransportRobot(2)
    wh.add_robot(picker); wh.add_robot(trans)
    o = Order(101, (0, 3), (4, 4), priority=2)
    wh.add_order(o)
    print("neighbors of (0,0):", wh.neighbors((0, 0)))
    print("picker :", picker.work(o, [(0, 0), (0, 1), (0, 2), (0, 3)]), picker.to_dict())
    print("trans  :", trans.work(o, [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1)]), o.status)
    print("blocked:", trans.work(Order(102, (0, 0), (2, 1)), [(0, 0), (1, 1)]))
    try:
        picker.__battery
    except AttributeError:
        print("encapsulation OK: __battery is private")
