import heapq

# Graph
graph = {
    'A': ['B', 'C', 'D'],
    'B': ['E', 'F'],
    'C': ['G', 'H'],
    'D': ['I', 'J'],
    'E': ['T'],
    'F': ['R'],
    'G': ['N'],
    'H': ['O', 'P'],
    'I': ['Q'],
    'J': ['R'],
    'O': [],
    'P': [],
    'Q': [],
    'R': [],
    'T': [],
    'N': []
}

# Heuristic values
h = {
    'A': 5,
    'B': 4,
    'C': 4,
    'D': 6,
    'E': 5,
    'F': 5,
    'G': 4,
    'H': 3,
    'I': 3,
    'J': 4,
    'O': 2,
    'P': 3,
    'Q': 2,
    'R': 4,
    'T': 5,
    'N': 0
}

def best_first_search(start, goal):
    open_list = []
    closed_list = []
    parent = {}

    # (heuristic, node)
    heapq.heappush(open_list, (h[start], start))

    while open_list:

        # Display OPEN
        print("OPEN =", [node for _, node in sorted(open_list)])
        print("CLOSED =", closed_list)

        _, current = heapq.heappop(open_list)

        if current in closed_list:
            continue

        closed_list.append(current)

        # Goal found
        if current == goal:
            break

        # Add children
        for child in graph[current]:
            if child not in closed_list:
                parent[child] = current
                heapq.heappush(open_list, (h[child], child))

    # Find solution path
    path = []
    current = goal

    while current in parent or current == start:
        path.append(current)

        if current == start:
            break

        current = parent[current]

    path.reverse()

    print("\nSolution Path:")
    print(" - ".join(path))

    print("\nFinal CLOSED =", closed_list)


# Run Best-First Search
best_first_search('A', 'P')