# WareBot - Progress

## Folder structure
warebot/
  logic/__init__.py
  logic/models.py     (DONE)
  logic/search.py     (DONE) BFS, DFS, A*
  logic/agents.py     (DONE) SimpleReflex, GoalBased, Learning
  app.py              (DONE) Flask backend (JSON API)
  templates/index.html, static/style.css, static/app.js   (DONE) dashboard
  tests/test_search_comparison.py (DONE) BFS / DFS / A* comparison regressions
  screenshots/        (TODO) dashboard evidence for report / viva

## Done
- Module 1: logic/models.py - Package, Order, Warehouse, Robot(abstract), PickerRobot, TransportRobot
- Module 2: logic/search.py - SearchAlgorithm(abstract), BFS (+nearest), DFS (+scan), AStar, compare(), benchmark(), get_algorithm(name)
- Module 4: app.py - Simulation class + JSON routes (below)
- Module 5: dashboard - templates/index.html, static/style.css, static/app.js (Overview, Warehouse, Orders, Robots, Analytics tabs; canvas map + search animation; SVG charts, no external libraries)
- Module 3: logic/agents.py - Agent(abstract), SimpleReflexAgent, GoalBasedAgent, LearningAgent(GoalBasedAgent)
- Module 6: tests/test_search_comparison.py - compares path validity and BFS/A* optimality, DFS behavior, traffic-aware A*, and unreachable cases

## Agents API (for app.py)
- Agent(robot, warehouse, algorithm='A*'); agent.perceive(blocked_cell=None) -> percept; agent.act(percept) -> {"action","reason"}
- GoalBasedAgent.step() = perceive+act+execute; process_order(order) -> {order_id, success, reason, status, algorithm, path, steps, nodes, replans, charged}
- Picker agent serves Pending orders (ends In Transit); Transport agent serves In Transit orders (ends Delivered). Failed order returns to previous status.
- LearningAgent.process_order adds result["learned"]; to_dict() has routes_learned, busy_cells; learning only affects A* (BFS/DFS ignore cost)
- Import in Flask: from logic.agents import GoalBasedAgent, LearningAgent, SimpleReflexAgent

## Flask API (for dashboard JS) - all JSON, cells are [row, col]
GET  /api/state                 warehouse, robots(+agent), orders, kpis, log
POST /api/reset                 {rows, cols, density, seed, with_orders}
POST /api/obstacles/toggle      {cell}      | /api/obstacles/generate {density, seed}
GET/POST /api/orders            POST {source, destination, priority} or {random:true}
GET  /api/robots                POST /api/robots/<id>/config {agent: reflex|goal|learning, algorithm}
POST /api/robots/<id>/step      one agent cycle -> {result(path), state}
POST /api/robots/<id>/charge    {rescue:true} teleports a stranded robot to the charger
POST /api/algorithm             {algorithm} for all robots
POST /api/run                   run all robots until orders done -> {results(path), state}
POST /api/path                  {algorithm, start, goal} -> result incl. explored_order (animation)
POST /api/compare               {start, goal} -> {BFS, DFS, A*}
POST /api/scan | /api/nearest   DFS warehouse scan | BFS nearest pending order
GET  /api/analytics             {benchmark{density:{algo:{avg_nodes,avg_length,avg_time_ms}}}, live, kpis}
Errors: {"error": msg} with 400/404. templates/index.html is served at "/" once it exists.

## Conventions
- Cells are (row, col) tuples; grid 0 = free, 1 = obstacle; default grid 15x15, charging station (0,0)
- Order statuses: Pending, Assigned, In Transit, Delivered
- robot.move(path) returns {"completed","steps","reason","blocked_cell"}
- Warehouse.neighbors(cell) and Warehouse.cost(cell) are what search.py must use

- search result dict: algorithm, found, path, path_length, cost, nodes_explored, explored_order, time_ms
- agents.py should call get_algorithm('A*').find_path(warehouse, start, goal); algorithm names: 'BFS','DFS','A*'

## Run
python logic/models.py      (from the warebot folder)
python logic/search.py      (demo + benchmark)
python logic/agents.py      (agents demo)
pip install -r requirements.txt && python app.py   (http://127.0.0.1:5000)

## Dashboard notes (Module 5)
- Warehouse tab: 'Find path' mode = click start then goal (runs selected algorithm, animates explored cells then path); 'Edit shelves' mode = click toggles shelf
- Buttons: Compare all three (/api/compare), Scan (DFS), Nearest order (BFS), New shelves, New warehouse
- Robots tab: change agent type / algorithm, Step, Charge, Rescue; Rail button 'Run all robots' = /api/run
- Analytics tab: benchmark chart (nodes / length / time) + live table from agents' real runs
- Module 6: screenshot capture remains TODO. Local preview could not start because Flask is not installed; the dependency download did not complete in this environment.


how to run:
.venv\Scripts\Activate.ps1
python app.py