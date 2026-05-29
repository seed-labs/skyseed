import h3

class EarthZoneManager:
    def __init__(self, resolution):
        """
        Resolution ranges from 0 (largest, ~1100km edge length) 
        to 15 (smallest, ~50cm edge length).
        For satellite networks, resolutions 1 to 4 are typically ideal.
        """
        self.resolution = resolution

    def get_zone_id(self, lat, lon):
        """Returns the unique H3 Hexagon ID for a given coordinate."""
        return h3.latlng_to_cell(lat, lon, self.resolution)

    def get_zone_boundary(self, zone_id):
        """Returns the lat/lon coordinates of the hexagon's vertices."""
        return h3.cell_to_boundary(zone_id)

    def get_all_global_zones(self):
        """Returns every single Zone ID on Earth for this resolution."""
        return h3.get_res0_cells() if self.resolution == 0 else h3.cell_to_children(h3.get_res0_cells(), self.resolution)

    def get_neighboring_zones(self, zone_id):
        """Returns the IDs of the immediately adjacent zones (useful for handovers)."""
        return h3.grid_disk(zone_id, k=1)

# ==========================================
# Execution / Emulation Setup
# ==========================================
if __name__ == "__main__":
    # Resolution 2 creates 5,812 zones globally (avg area ~86,000 km², great for mega-constellations)
    zone_manager = EarthZoneManager(resolution=2)

    # 1. Find the zone of a specific Ground Station (e.g., New York City)
    ny_lat, ny_lon = 40.7128, -74.0060
    ny_zone = zone_manager.get_zone_id(ny_lat, ny_lon)
    print(f"Ground Station (NY) belongs to Zone ID: {ny_zone}")

    # 2. Get the physical geographic boundary of that zone
    boundary = zone_manager.get_zone_boundary(ny_zone)
    print(f"\nZone {ny_zone} Vertices (Lat, Lon):")
    for vertex in boundary:
        print(f"  {vertex}")

    # 3. Find adjacent zones (for routing to adjacent satellite footprints)
    neighbors = zone_manager.get_neighboring_zones(ny_zone)
    print(f"\nAdjacent Zones for handover routing: {neighbors}")

    # 4. Count total zones your simulation controller needs to track
    # WARNING: High resolutions (like 5+) will generate millions of cells.
    all_zones = h3.get_child_res(h3.get_res0_cells(), zone_manager.resolution)
    print(f"\nTotal geographic zones generated globally at resolution {zone_manager.resolution}: {len(all_zones)}")