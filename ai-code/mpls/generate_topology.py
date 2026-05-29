from datetime import datetime, timezone
import networkx as nx
from skyfield.api import EarthSatellite, load, wgs84
from geopy.distance import geodesic

class SatelliteTopologyGenerator:
    def __init__(self, tle_data, ground_stations):
        """
        tle_data: Dict of { "SatName": ("TLE_Line1", "TLE_Line2") }
        ground_stations: Dict of { "GSName": (lat, lon, elevation_m) }
        """
        self.ts = load.timescale()
        self.earth = load('de421.bsp')['earth']
        
        # Initialize satellites from TLE
        self.satellites = {}
        for name, lines in tle_data.items():
            self.satellites[name] = EarthSatellite(lines[0], lines[1], name, self.ts)
            
        self.ground_stations = ground_stations

    def _has_line_of_sight(self, pos1, pos2):
        """
        Checks if the line connecting two points in space intersects the Earth.
        pos1 and pos2 are Skyfield Position objects (Geocentric vectors).
        """
        p1 = pos1.position.km
        p2 = pos2.position.km
        
        # Earth radius approximation (km)
        R = 6371.0 
        
        # Vector math to find the closest approach of the line segment to Earth's center
        import numpy as np
        d = p2 - p1
        t = -np.dot(p1, d) / np.dot(d, d)
        t = np.clip(t, 0.0, 1.0)
        closest_point = p1 + t * d
        distance_to_center = np.linalg.norm(closest_point)
        
        # If the closest point of the ray is less than Earth's radius, the Earth blocks it
        return distance_to_center > R

    def generate_topology_at_time(self, dt, max_isl_dist_km=5000, min_elevation_deg=10):
        """
        Generates a NetworkX graph for a specific datetime.
        """
        # Convert datetime to Skyfield time object
        t = self.ts.from_datetime(dt.replace(tzinfo=timezone.utc))
        graph = nx.Graph()

        # 1. Add all nodes to the graph
        for sat_name in self.satellites:
            graph.add_node(sat_name, type="satellite")
        for gs_name in self.ground_stations:
            graph.add_node(gs_name, type="ground_station")

        # Compute positions of all satellites at time 't'
        sat_positions = {name: sat.at(t) for name, sat in self.satellites.items()}

        # 2. Establish Inter-Satellite Links (ISLs)
        sat_names = list(self.satellites.keys())
        for i in range(len(sat_names)):
            for j in range(i + 1, len(sat_names)):
                sat1, sat2 = sat_names[i], sat_names[j]
                pos1, pos2 = sat_positions[sat1], sat_positions[sat2]
                
                # Calculate distance in km
                distance = (pos1 - pos2).distance().km
                
                # Link condition: within range and not blocked by Earth
                if distance <= max_isl_dist_km:
                    if self._has_line_of_sight(pos1, pos2):
                        # Calculate delay (speed of light: ~300,000 km/s -> 300 km/ms)
                        delay_ms = distance / 300.0
                        graph.add_edge(sat1, sat2, weight=delay_ms)

        # 3. Establish Ground-to-Satellite Links (GSLs)
        for gs_name, (lat, lon, elev) in self.ground_stations.items():
            gs_loc = wgs84.latlon(lat, lon, elevation_m=elev)
            
            for sat_name, sat_pos in sat_positions.items():
                # Check elevation angle from ground station to satellite
                difference = sat_pos - gs_loc.at(t)
                alt, az, distance = difference.altaz()
                
                # Link condition: Satellite is above the local horizon constraint
                if alt.degrees >= min_elevation_deg:
                    delay_ms = distance.km / 300.0
                    graph.add_edge(gs_name, sat_name, weight=delay_ms)

        return graph

# ==========================================
# Integration & Execution Context
# ==========================================
if __name__ == "__main__":
    # Sample real TLE data (Example: ISS and a NOAA weather satellite acting as our constellation)
    tle_input = {
        "SatA-ISS": (
            "1 25544U 98067A   24032.54166667  .00016717  00000-0  30000-3 0  9997",
            "2 25544  51.6428 163.5123 0006123  32.4152 327.6891 15.49812345432106"
        ),
        "SatB-NOAA": (
            "1 43013U 17073A   24032.25000000  .00000023  00000-0  25012-4 0  9994",
            "2 43013  98.1234 245.5432 0012345  78.1234 282.4567 14.12345678321045"
        )
    }

    # Ground Stations: { Name: (Lat, Lon, Elevation_m) }
    ground_stations_input = {
        "GS_NewYork": (40.7128, -74.0060, 10),
        "GS_London": (51.5074, -0.1278, 15)
    }

    # Initialize Generator
    topo_gen = SatelliteTopologyGenerator(tle_input, ground_stations_input)

    # Generate a snapshot of the topology right now
    snapshot_time = datetime.now(timezone.utc)
    current_topology_graph = topo_gen.generate_topology_at_time(snapshot_time)

    print(f"Topology successfully generated for time: {snapshot_time}")
    print(f"Total Nodes: {current_topology_graph.number_of_nodes()}")
    print(f"Active Live Links: {current_topology_graph.number_of_edges()}")
    
    # Show active topology connections with delay weights
    for edge in current_topology_graph.edges(data=True):
        print(f"  Link: {edge[0]} <---> {edge[1]} | Delay: {edge[2]['weight']:.2f} ms")

    # =======================================================
    # FEED DIRECTLY INTO YOUR PREVIOUS MPLS MODULE
    # =======================================================
    # Reimporting the conceptual structure of your MPLS Controller
    from mpls_controller import CentralizedSatelliteController # Assuming saved as mpls_controller.py
    
    controller = CentralizedSatelliteController()
    controller.graph = current_topology_graph # Pass the computed topology graph straight in
    
    print("\nCalculating MPLS paths based on live orbital TLE positions...")
    live_mpls_tables = controller.calculate_mpls_tables()
    controller.print_mpls_tables(live_mpls_tables)