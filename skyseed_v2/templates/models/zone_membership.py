"""Rectangle membership, with the first supplied match authoritative.

Inject zone_membership_model=FirstMatchMembershipModel() alongside a Zone layout.
"""


class FirstMatchMembershipModel:
    def find_zones(self, zones, latitude_deg, longitude_deg):
        return tuple(zone for zone in zones if zone.contains(latitude_deg, longitude_deg))

    def resolve_zone(self, matches, latitude_deg, longitude_deg):
        return matches[0]  # ZoneLayout calls this only with nonempty owned matches.
