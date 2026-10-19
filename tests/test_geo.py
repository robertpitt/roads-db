import unittest

from roads_rdb.geo import bng_to_wgs84, wgs84_to_bng


class GeoTests(unittest.TestCase):
    def test_round_trips_wgs84_and_bng(self):
        lat = 52.591905
        lon = -1.9786927

        easting, northing = wgs84_to_bng(lat, lon)
        roundtrip_lat, roundtrip_lon = bng_to_wgs84(easting, northing)

        self.assertAlmostEqual(roundtrip_lat, lat, places=5)
        self.assertAlmostEqual(roundtrip_lon, lon, places=5)


if __name__ == "__main__":
    unittest.main()
