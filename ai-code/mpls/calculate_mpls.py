import networkx as nx

class CentralizedSatelliteController:
    def __init__(self):
        # Initialize the global topology graph
        self.graph = nx.Graph()
        
    def add_link(self, node1, node2, delay_ms):
        """Adds a directional/bidirectional link with a weight (latency)."""
        self.graph.add_edge(node1, node2, weight=delay_ms)

    def calculate_mpls_tables(self):
        """
        Computes MPLS tables for all nodes using Dijkstra's Algorithm
        and a centralized downstream label assignment strategy.
        """
        # Global structure to hold MPLS tables for each node
        # Format: { node: { in_label: (out_label, next_hop, destination) } }
        mpls_tables = {node: {} for node in self.graph.nodes()}
        
        # Label counter to assign unique labels globally for simplicity
        label_counter = 100
        
        # We calculate paths to every possible destination root
        for destination in self.graph.nodes():
            # Run Dijkstra to get the shortest path tree rooted at 'destination'
            # paths is a dict: { source: [list of nodes in path] }
            paths = nx.single_target_shortest_path(self.graph, destination)
            
            # Map to keep track of allocated labels for this specific destination tree
            # Format: { node: label_to_reach_destination }
            tree_labels = {}
            
            # 1. Downstream Label Assignment (Egress node allocates implicit null / 0)
            tree_labels[destination] = 0 
            
            # 2. Assign labels upstream along the shortest path tree
            # Sort paths by length (shortest first) so we assign labels from destination outwards
            sorted_sources = sorted(paths.keys(), key=lambda k: len(paths[k]))
            
            for src in sorted_sources:
                if src == destination:
                    continue
                    
                path = paths[src]
                # Next hop is the second node in the path from src to destination
                next_hop = path[1] 
                
                # If the next hop doesn't have an assigned label yet, generate one
                if next_hop not in tree_labels:
                    tree_labels[next_hop] = label_counter
                    label_counter += 1
                
                # Current node needs an In-Label for this destination
                if src not in tree_labels:
                    tree_labels[src] = label_counter
                    label_counter += 1
                
                # Fetch the labels
                in_label = tree_labels[src]
                out_label = tree_labels[next_hop]
                
                # Populate the local MPLS table for the 'src' satellite
                mpls_tables[src][in_label] = {
                    "Out-Label": out_label if out_label != 0 else "Pop/Egress",
                    "Next-Hop": next_hop,
                    "Destination": destination
                }
                
        return mpls_tables

    def print_mpls_tables(self, tables):
        """Helper function to print tables cleanly."""
        for node, table in tables.items():
            print(f"\n=== MPLS Table for Satellite: {node} ===")
            print(f"{'In-Label':<10} | {'Out-Label':<12} | {'Next-Hop':<10} | {'Final Destination':<15}")
            print("-" * 55)
            for in_label, action in table.items():
                print(f"{in_label:<10} | {str(action['Out-Label']):<12} | {action['Next-Hop']:<10} | {action['Destination']:<15}")

# ==========================================
# Execution / Emulation Setup
# ==========================================
if __name__ == "__main__":
    controller = CentralizedSatelliteController()

    # Simulating a simple satellite constellation slice + 1 Ground Station
    # Nodes: SatA, SatB, SatC (in orbit), GS1 (Ground Station)
    # Weights represent propagation delay (latency) in milliseconds
    controller.add_link("SatA", "SatB", delay_ms=10)
    controller.add_link("SatB", "SatC", delay_ms=12)
    controller.add_link("SatA", "SatC", delay_ms=40) # Higher latency link
    controller.add_link("SatC", "GS1", delay_ms=5)   # Satellite to Ground

    print("Calculating centralized MPLS paths via Dijkstra...")
    global_mpls_tables = controller.calculate_mpls_tables()
    
    # Print out the calculated tables for the network
    controller.print_mpls_tables(global_mpls_tables)