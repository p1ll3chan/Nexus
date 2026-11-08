"""SQLite persistence for phone GPS locations and battery telemetry."""

from __future__ import annotations

import datetime as dt
import math
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape as xml_escape

from protocol import Location


def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in meters between two WGS84 coordinates."""

    radius = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * radius * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def total_distance_meters(records: Sequence["LocationRecord"]) -> float:
    """Compute cumulative path length in meters across ordered records."""

    if len(records) < 2:
        return 0.0
    ordered = sorted(records, key=lambda r: (r.timestamp, r.row_id))
    total = 0.0
    for prev, curr in zip(ordered, ordered[1:]):
        total += distance_meters(prev.latitude, prev.longitude, curr.latitude, curr.longitude)
    return total


@dataclass(frozen=True)
class LocationRecord:
    row_id: int
    event_id: str
    device: str
    latitude: float
    longitude: float
    accuracy: float
    altitude: float | None
    speed: float | None
    bearing: float | None
    provider: str
    battery: int | None
    charging: bool
    timestamp: float
    received_at: float

    @property
    def osm_url(self) -> str:
        return (
            f"https://www.openstreetmap.org/?mlat={self.latitude:.6f}"
            f"&mlon={self.longitude:.6f}#map=17/{self.latitude:.6f}/{self.longitude:.6f}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "id": self.event_id,
            "device": self.device,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "accuracy": self.accuracy,
            "altitude": self.altitude,
            "speed": self.speed,
            "bearing": self.bearing,
            "provider": self.provider,
            "battery": self.battery,
            "charging": self.charging,
            "timestamp": self.timestamp,
            "received_at": self.received_at,
            "osm_url": self.osm_url,
        }


class LocationStore:
    """Thread-safe SQLite store for phone location history."""

    def __init__(self, path: str | os.PathLike[str] = ":memory:") -> None:
        raw_path = str(path)
        self.path = raw_path
        if raw_path != ":memory:":
            target = Path(raw_path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            self.path = str(target)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS locations (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                device TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                accuracy REAL NOT NULL DEFAULT 0.0,
                altitude REAL,
                speed REAL,
                bearing REAL,
                provider TEXT NOT NULL DEFAULT 'gps',
                battery INTEGER,
                charging INTEGER NOT NULL DEFAULT 0,
                timestamp REAL NOT NULL,
                received_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_locations_device_ts
                ON locations (device, timestamp DESC, row_id DESC);
            CREATE INDEX IF NOT EXISTS idx_locations_ts
                ON locations (timestamp DESC, row_id DESC);
            """
        )
        self._conn.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> LocationRecord:
        return LocationRecord(
            row_id=int(row["row_id"]),
            event_id=str(row["event_id"]),
            device=str(row["device"]),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            accuracy=float(row["accuracy"] or 0.0),
            altitude=float(row["altitude"]) if row["altitude"] is not None else None,
            speed=float(row["speed"]) if row["speed"] is not None else None,
            bearing=float(row["bearing"]) if row["bearing"] is not None else None,
            provider=str(row["provider"] or "gps"),
            battery=int(row["battery"]) if row["battery"] is not None else None,
            charging=bool(row["charging"]),
            timestamp=float(row["timestamp"]),
            received_at=float(row["received_at"]),
        )

    def record(
        self,
        device: str,
        event: Location,
        *,
        retention_days: int = 0,
    ) -> LocationRecord:
        received_at = time.time()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO locations (
                    event_id, device, latitude, longitude, accuracy,
                    altitude, speed, bearing, provider, battery,
                    charging, timestamp, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    device,
                    event.latitude,
                    event.longitude,
                    event.accuracy,
                    event.altitude,
                    event.speed,
                    event.bearing,
                    event.provider,
                    event.battery,
                    1 if event.charging else 0,
                    event.timestamp,
                    received_at,
                ),
            )
            row_id = int(cur.lastrowid or 0)
            if retention_days > 0:
                cutoff = received_at - retention_days * 86_400.0
                self._conn.execute("DELETE FROM locations WHERE timestamp < ?", (cutoff,))
            self._conn.commit()
        return LocationRecord(
            row_id=row_id,
            event_id=event.id,
            device=device,
            latitude=event.latitude,
            longitude=event.longitude,
            accuracy=event.accuracy,
            altitude=event.altitude,
            speed=event.speed,
            bearing=event.bearing,
            provider=event.provider,
            battery=event.battery,
            charging=event.charging,
            timestamp=event.timestamp,
            received_at=received_at,
        )

    def latest(self, device: str | None = None) -> LocationRecord | None:
        with self._lock:
            if device:
                row = self._conn.execute(
                    """
                    SELECT * FROM locations
                    WHERE lower(device) = lower(?)
                    ORDER BY timestamp DESC, row_id DESC
                    LIMIT 1
                    """,
                    (device.strip(),),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """
                    SELECT * FROM locations
                    ORDER BY timestamp DESC, row_id DESC
                    LIMIT 1
                    """
                ).fetchone()
        return self._from_row(row) if row is not None else None

    def latest_per_device(self) -> list[LocationRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT l.*
                FROM locations l
                INNER JOIN (
                    SELECT device, MAX(row_id) AS max_row_id
                    FROM locations
                    GROUP BY device
                ) grouped ON l.row_id = grouped.max_row_id
                ORDER BY l.timestamp DESC
                """
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def history(
        self,
        device: str | None = None,
        *,
        since: float | None = None,
        limit: int = 500,
    ) -> list[LocationRecord]:
        limit = max(1, min(10_000, int(limit)))
        clauses: list[str] = []
        params: list[Any] = []
        if device:
            clauses.append("lower(device) = lower(?)")
            params.append(device.strip())
        if since is not None and since > 0:
            clauses.append("timestamp >= ?")
            params.append(float(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT * FROM (
                SELECT * FROM locations
                {where}
                ORDER BY timestamp DESC, row_id DESC
                LIMIT ?
            )
            ORDER BY timestamp ASC, row_id ASC
        """
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._from_row(r) for r in rows]

    def devices(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    device,
                    COUNT(*) AS count,
                    MIN(timestamp) AS first_seen,
                    MAX(timestamp) AS last_seen
                FROM locations
                GROUP BY device
                ORDER BY last_seen DESC
                """
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            dev = str(row["device"])
            latest = self.latest(dev)
            result.append(
                {
                    "device": dev,
                    "count": int(row["count"]),
                    "first_seen": float(row["first_seen"]),
                    "last_seen": float(row["last_seen"]),
                    "latest": latest.to_dict() if latest else None,
                }
            )
        return result

    def count(self, device: str | None = None) -> int:
        with self._lock:
            if device:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM locations WHERE lower(device) = lower(?)",
                    (device.strip(),),
                ).fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM locations").fetchone()
        return int(row["n"]) if row is not None else 0

    def prune(self, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86_400.0
        with self._lock:
            cur = self._conn.execute("DELETE FROM locations WHERE timestamp < ?", (cutoff,))
            self._conn.commit()
            return int(cur.rowcount or 0)

    def clear(self, device: str | None = None) -> int:
        with self._lock:
            if device:
                cur = self._conn.execute(
                    "DELETE FROM locations WHERE lower(device) = lower(?)",
                    (device.strip(),),
                )
            else:
                cur = self._conn.execute("DELETE FROM locations")
            self._conn.commit()
            return int(cur.rowcount or 0)

    def export_geojson(
        self,
        device: str | None = None,
        *,
        since: float | None = None,
        limit: int = 2000,
    ) -> dict[str, Any]:
        points = self.history(device, since=since, limit=limit)
        features: list[dict[str, Any]] = []
        if len(points) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[p.longitude, p.latitude] for p in points],
                    },
                    "properties": {
                        "kind": "track",
                        "device": device or (points[-1].device if points else ""),
                        "point_count": len(points),
                        "distance_meters": round(total_distance_meters(points), 2),
                    },
                }
            )
        for pt in points:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [pt.longitude, pt.latitude],
                    },
                    "properties": pt.to_dict(),
                }
            )
        return {"type": "FeatureCollection", "features": features}

    def export_gpx(
        self,
        device: str | None = None,
        *,
        since: float | None = None,
        limit: int = 2000,
    ) -> str:
        points = self.history(device, since=since, limit=limit)
        track_name = xml_escape(device or (points[-1].device if points else "phone-bridge"))
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<gpx version="1.1" creator="PhoneBridge" xmlns="http://www.topografix.com/GPX/1/1">',
            "  <trk>",
            f"    <name>{track_name}</name>",
            "    <trkseg>",
        ]
        for pt in points:
            iso_time = (
                dt.datetime.fromtimestamp(pt.timestamp, tz=dt.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            lines.append(f'      <trkpt lat="{pt.latitude:.6f}" lon="{pt.longitude:.6f}">')
            if pt.altitude is not None:
                lines.append(f"        <ele>{pt.altitude:.2f}</ele>")
            lines.append(f"        <time>{iso_time}</time>")
            lines.append("      </trkpt>")
        lines.extend(
            [
                "    </trkseg>",
                "  </trk>",
                "</gpx>",
            ]
        )
        return "\n".join(lines)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
