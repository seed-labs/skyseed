import networkx as nx
import h3

class SatelliteController:
    def __init__(self, resolution=2):
        self.resolution = resolution
        self.graph = nx.Graph()
        
        # Maps Zone ID -> Satellite Name currently covering it
        self.zone_coverage = {} 
        # Reverse map for tracking: Satellite Name -> List of covered Zone IDs
        self.sat_to_zones = {}

    def update_topology_and_coverage(self, physical_topology, satellite_positions, ground_stations):
        """
        1. Copies the base satellite physical topology (ISLs).
        2. Injects Ground Stations as physical nodes and connects them to overhead satellites.
        3. Injects H3 Zones as logical nodes and connects them to overhead satellites.
        """
        self.graph = physical_topology.copy()
        self.zone_coverage.clear()
        self.sat_to_zones = {node: [] for node in self.graph.nodes() if self.graph.nodes[node].get('type') == 'satellite'}

        # --- STEP 1: Inject Ground Stations ---
        for gs_name, gs_data in ground_stations.items():
            self.graph.add_node(gs_name, type="ground_station")
            
            # Connect Ground Station to its designated serving satellite (passed from your visibility module)
            serving_sat = gs_data["connected_satellite"]
            if serving_sat in self.graph:
                # Add Ground-to-Satellite Link (GSL) with its specific delay
                self.graph.add_edge(serving_sat, gs_name, weight=gs_data["delay_ms"])

        # --- STEP 2: Inject H3 Geographic Zones ---
        for sat_name, (lat, lon) in satellite_positions.items():
            center_zone = h3.latlng_to_cell(lat, lon, self.resolution)
            
            # Map nearby zones within the satellite antenna footprint
            covered_zones = h3.grid_disk(center_zone, k=1) 
            
            for zone in covered_zones:
                self.zone_coverage[zone] = sat_name
                self.sat_to_zones[sat_name].append(zone)
                
                if not self.graph.has_node(zone):
                    self.graph.add_node(zone, type="zone")
                
                # Connect zone to the satellite with a typical radio beam propagation delay (e.g., 2.5 ms)
                self.graph.add_edge(sat_name, zone, weight=2.5)

    def calculate_unified_mpls_tables(self):
        """
        Calculates MPLS tables for all satellites, running Dijkstra for every 
        single H3 Zone and every Ground Station on Earth.
        """
        # Initialize tables only for the routing nodes (Satellites)
        mpls_tables = {node: {} for node in self.graph.nodes() if self.graph.nodes[node].get('type') == 'satellite'}
        label_counter = 2000 # Starting label block

        # Find all valid network exit destinations (Both Zones and Ground Stations)
        all_destinations = [node for node in self.graph.nodes() 
                            if self.graph.nodes[node].get('type') in ['zone', 'ground_station']]

        for dest in all_destinations:
            dest_type = self.graph.nodes[dest]['type']
            
            # Run Dijkstra to find the shortest path tree to this specific exit point
            paths = nx.single_target_shortest_path(self.graph, dest)
            tree_labels = {dest: 0} # Egress pop label
            
            # Order nodes downstream-to-upstream
            sorted_sources = sorted(paths.keys(), key=lambda k: len(paths[k]))
            
            for src in sorted_sources:
                # Only populate forwarding tables for the routing satellites
                if src == dest or self.graph.nodes[src].get('type') != 'satellite':
                    continue
                    
                path = paths[src]
                next_hop = path[1]
                
                # Dynamically generate upstream labels
                if next_hop not in tree_labels:
                    tree_labels[next_hop] = label_counter
                    label_counter += 1
                if src not in tree_labels:
                    tree_labels[src] = label_counter
                    label_counter += 1
                    
                in_label = tree_labels[src]
                out_label = tree_labels[next_hop]
                
                # Determine explicit action based on what the next hop node actually is
                next_hop_type = self.graph.nodes[next_hop].get('type')
                if next_hop_type == 'zone':
                    action = "Downlink Beam to User Terminal"
                elif next_hop_type == 'ground_station':
                    action = f"Gateway Drop to Terrestrial Fiber ({next_hop})"
                else:
                    action = f"Forward via ISL to {next_hop}"

                # Append entry to the satellite's local MPLS table
                mpls_tables[src][in_label] = {
                    "Destination": dest,
                    "Dest-Type": dest_type.upper(),
                    "Out-Label": out_label if out_label != 0 else "Pop/Egress",
                    "Next-Hop-Node": next_hop,
                    "Action": action
                }
                
        return mpls_tables

    def print_mpls_tables(self, tables):
        for sat, table in tables.items():
            print(f"\n=== Unified MPLS Table for Satellite: {sat} ===")
            print(f"{'In-Label':<10} | {'Out-Label':<10} | {'Next-Hop':<12} | {'Destination Exit':<18} | {'Type':<15} | {'Action':<35}")
            print("-" * 110)
            for in_label, data in table.items():
                print(f"{in_label:<10} | {str(data['Out-Label']):<10} | {data['Next-Hop-Node']:<12} | {data['Destination']:<18} | {data['Dest-Type']:<15} | {data['Action']:<35}")

# ==========================================
# Emulation Verification Run
# ==========================================
if __name__ == "__main__":
    # Create the physical inter-satellite links (ISLs)
    physical_isl_graph = nx.Graph()
    physical_isl_graph.add_node("Sat_Alpha", type="satellite")
    physical_isl_graph.add_node("Sat_Beta", type="satellite")
    physical_isl_graph.add_edge("Sat_Alpha", "Sat_Beta", weight=15.0)

    # Dynamic positions of satellites at timestamp T
    satellite_gps_snapshot = {
        "Sat_Alpha": (40.7128, -74.0060), # East Coast US
        "Sat_Beta": (34.0522, -118.2437)  # West Coast US
    }

    # Ground Stations (Connected to specific overhead satellites based on line-of-sight visibility)
    ground_stations_snapshot = {
        "GS_NewYork_Teleport": {"connected_satellite": "Sat_Alpha", "delay_ms": 1.8},
        "GS_LA_Teleport": {"connected_satellite": "Sat_Beta", "delay_ms": 2.1}
    }

    # Initialize Controller
    controller = SatelliteController(resolution=1) # Low res for small clean tables
    
    # 1. Rebuild the master graph mapping the zones and the physical fiber gateways
    controller.update_topology_and_coverage(physical_isl_graph, satellite_gps_snapshot, ground_stations_snapshot)
    
    # 2. Compute the complete unified forwarding tables
    unified_mpls_tables = controller.calculate_unified_mpls_tables()
    
    # 3. Output results
    controller.print_mpls_tables(unified_mpls_tables)