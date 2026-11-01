import heapq

def a_star(graph, start, goal, heuristic):
    open_list = []
    heapq.heappush(open_list, (0, start))

    g_cost = {start: 0}
    parent = {start: None}

    while open_list:
        _, current = heapq.heappop(open_list)

        if current == goal:
            path = []

            while current is not None:
                path.append(current)
                current = parent[current]

            return path[::-1], g_cost[goal]

        for neighbor, cost in graph[current]:
            new_g = g_cost[current] + cost

            if neighbor not in g_cost or new_g < g_cost[neighbor]:
                g_cost[neighbor] = new_g

                f_cost = new_g + heuristic[neighbor]

                heapq.heappush(open_list, (f_cost, neighbor))
                parent[neighbor] = current

    return None, float('inf')


# Graph
graph = {
    'A': [('B', 1), ('C', 3)],
    'B': [('D', 3), ('E', 1)],
    'C': [('F', 5)],
    'D': [('G', 2)],
    'E': [('G', 4)],
    'F': [('G', 1)],
    'G': []
}

# Heuristic values
heuristic = {
    'A': 6,
    'B': 4,
    'C': 4,
    'D': 2,
    'E': 2,
    'F': 1,
    'G': 0
}

# A* Search
path, cost = a_star(graph, 'A', 'G', heuristic)

print("Shortest Path:", path)
print("Total Cost:", cost)