import pytest

from webapp.backend import geo


def test_distances_are_great_circle_miles():
    nyc, la = (40.7128, -74.0060), (34.0522, -118.2437)
    assert geo.haversine_miles(nyc, la) == pytest.approx(2445, abs=5)
    assert geo.haversine_miles(nyc, nyc) == 0


@pytest.mark.parametrize("value, expected", [
    ("02139", "02139"), ("2139", "02139"), (2139, "02139"), ("02139-4307", "02139"), (" 43004 ", "43004"),
    ("", None), (None, None), ("ABCDE", None), ("12", None), ("123456", None),
])
def test_zip_codes_are_normalized_to_five_digits(value, expected):
    assert geo.normalize_zip(value) == expected


def test_zip_points_come_from_the_census_file():
    lat, lon = geo.zip_point("2139")  # Cambridge, MA, with its leading zero lost
    assert 42.3 < lat < 42.4 and -71.2 < lon < -71.0
    assert geo.zip_point("00000") is None and geo.zip_point(None) is None
    assert len(geo.centroids()) > 33_000


@pytest.mark.parametrize("value, expected", [("OH", "Ohio"), ("oh", "Ohio"), ("Ohio", "Ohio"), ("dc", "District of Columbia"),
                                             ("PR", "Puerto Rico"), ("Ontario", "Ontario"), ("", None), (None, None)])
def test_state_codes_become_names(value, expected):
    assert geo.state_name(value) == expected
