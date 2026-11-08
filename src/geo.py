"""Shared geometry helpers (assumptions items 12-13).

All metric work happens in one local azimuthal-equidistant projection centred on
the centroid of the zone polygon (computed in lon/lat degrees), WGS84.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from shapely.geometry import shape

LOCAL_OFFSET_S = 9 * 3600     # R5: reports are cut at 00:00 UTC+9


def load_geom(path):
    with open(Path(path), encoding="utf-8") as fh:
        return shape(json.load(fh)["features"][0]["geometry"])


class LocalFrame:
    def __init__(self, zone_ll):
        c = zone_ll.centroid
        crs = f"+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m +no_defs"
        self._fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    def xy(self, lon, lat):
        x, y = self._fwd.transform(np.asarray(lon, float), np.asarray(lat, float))
        return np.asarray(x), np.asarray(y)

    def geom(self, g):
        return shapely.transform(g, lambda c: np.column_stack(self._fwd.transform(c[:, 0], c[:, 1])))


def local_day(ts: pd.Series) -> pd.Series:
    """Local (UTC+9) calendar day; a day is [00:00, 24:00) local."""
    return (ts + pd.Timedelta(seconds=LOCAL_OFFSET_S)).dt.strftime("%Y-%m-%d")



def distance_to(geom, x, y):
    return shapely.distance(geom, shapely.points(x, y))
