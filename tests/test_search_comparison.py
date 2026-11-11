"""Regression tests for WareBot's BFS, DFS, and A* comparison."""
import unittest

from logic.models import Warehouse
from logic.search import AStar, BFS, DFS, compare


class SearchComparisonTests(unittest.TestCase):
    def setUp(self):
        self.warehouse = Warehouse(7, 7)
        # A short wall with a gap forces all algorithms around the same layout.
        for row in range(1, 6):
            if row != 3:
                self.warehouse.add_obstacle(row, 3)
        self.start = (0, 0)
        self.goal = (6, 6)

    def assert_valid_path(self, result, start, goal):
        self.assertTrue(result["found"])
        path = result["path"]
        self.assertEqual(path[0], start)
        self.assertEqual(path[-1], goal)
        self.assertEqual(result["path_length"], len(path) - 1)
        self.assertEqual(result["nodes_explored"], len(result["explored_order"]))
        self.assertGreater(result["nodes_explored"], 0)
        for cell in path:
            self.assertTrue(self.warehouse.is_free(cell), f"blocked cell in path: {cell}")
        for left, right in zip(path, path[1:]):
            self.assertEqual(abs(left[0] - right[0]) + abs(left[1] - right[1]), 1)

    def test_all_algorithms_find_valid_paths_and_comparison_has_three_entries(self):
        results = compare(self.warehouse, self.start, self.goal)
        self.assertEqual(set(results), {"BFS", "DFS", "A*"})
        for result in results.values():
            self.assert_valid_path(result, self.start, self.goal)

    def test_bfs_and_astar_match_shortest_unweighted_path(self):
        bfs = BFS().find_path(self.warehouse, self.start, self.goal)
        astar = AStar().find_path(self.warehouse, self.start, self.goal)
        self.assertEqual(astar["path_length"], bfs["path_length"])

    def test_dfs_path_is_valid_but_may_be_longer(self):
        bfs = BFS().find_path(self.warehouse, self.start, self.goal)
        dfs = DFS().find_path(self.warehouse, self.start, self.goal)
        self.assert_valid_path(dfs, self.start, self.goal)
        self.assertGreaterEqual(dfs["path_length"], bfs["path_length"])

    def test_astar_prefers_lower_traffic_cost_even_when_path_is_longer(self):
        wh = Warehouse(5, 5)
        # Make the direct top row expensive; A* should take a longer, cheaper detour.
        for col in range(1, 5):
            wh.add_traffic((0, col), 10)
        start, goal = (0, 0), (0, 4)
        bfs = BFS().find_path(wh, start, goal)
        astar = AStar().find_path(wh, start, goal)
        self.assertEqual(bfs["path_length"], 4)
        self.assertGreater(astar["path_length"], bfs["path_length"])
        self.assertLess(astar["cost"], bfs["cost"])

    def test_blocked_endpoints_return_not_found_for_each_algorithm(self):
        self.warehouse.add_obstacle(*self.goal)
        for result in compare(self.warehouse, self.start, self.goal).values():
            self.assertFalse(result["found"])
            self.assertEqual(result["path"], [])
            self.assertIsNone(result["cost"])

    def test_disconnected_goal_returns_not_found(self):
        wh = Warehouse(3, 3)
        for cell in ((0, 1), (1, 0), (1, 2), (2, 1)):
            wh.add_obstacle(*cell)
        for result in compare(wh, (0, 0), (2, 2)).values():
            self.assertFalse(result["found"])


if __name__ == "__main__":
    unittest.main()
