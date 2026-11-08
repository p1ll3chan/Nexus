"""Lightweight async HTTP + SSE server providing the PhoneBridge Map UI."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from storage import LocationStore, total_distance_meters

if TYPE_CHECKING:
    from config import Config
    from server import BridgeServer

log = logging.getLogger("phone-bridge.web")


MAP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>PhoneBridge — Live Phone Tracker</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="" />
<style>
  :root {
    --bg: #0b0f17;
    --panel: #111827;
    --panel-alt: #1a2333;
    --border: #263248;
    --text: #f3f6fc;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --accent-strong: #0284c7;
    --good: #22c55e;
    --warn: #f59e0b;
    --bad: #ef4444;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    padding: 0;
    height: 100%;
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
  }
  .layout {
    display: grid;
    grid-template-columns: 380px 1fr;
    height: 100vh;
    width: 100vw;
    overflow: hidden;
  }
  @media (max-width: 900px) {
    .layout {
      grid-template-columns: 1fr;
      grid-template-rows: 46vh 54vh;
    }
  }
  .sidebar {
    background: var(--panel);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .header {
    padding: 18px 20px 14px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .brand-dot {
    width: 11px;
    height: 11px;
    border-radius: 50%;
    background: var(--good);
    box-shadow: 0 0 12px var(--good);
  }
  .brand-dot.offline {
    background: var(--warn);
    box-shadow: 0 0 10px var(--warn);
  }
  .brand h1 {
    font-size: 1.05rem;
    font-weight: 700;
    margin: 0;
    letter-spacing: -0.01em;
  }
  .brand p {
    margin: 2px 0 0;
    font-size: 0.76rem;
    color: var(--muted);
  }
  .pill {
    font-size: 0.72rem;
    font-weight: 600;
    padding: 4px 9px;
    border-radius: 999px;
    background: rgba(56, 189, 248, 0.14);
    color: var(--accent);
    border: 1px solid rgba(56, 189, 248, 0.3);
    white-space: nowrap;
  }
  .controls {
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .control-row {
    display: flex;
    gap: 8px;
  }
  select, button, a.btn {
    font: inherit;
    font-size: 0.82rem;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--panel-alt);
    color: var(--text);
    padding: 8px 11px;
    cursor: pointer;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    transition: border-color 0.15s, background 0.15s;
  }
  select { flex: 1; }
  button:hover, a.btn:hover, select:hover {
    border-color: var(--accent);
  }
  button.primary {
    background: linear-gradient(135deg, var(--accent-strong), #0369a1);
    border-color: #38bdf8;
    font-weight: 600;
    flex: 1;
  }
  button.primary:hover {
    filter: brightness(1.1);
  }
  .cards {
    padding: 14px 20px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    border-bottom: 1px solid var(--border);
  }
  .card {
    background: var(--panel-alt);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 12px;
  }
  .card.wide {
    grid-column: span 2;
  }
  .card-label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--muted);
  }
  .card-value {
    font-size: 0.96rem;
    font-weight: 600;
    margin-top: 4px;
    font-variant-numeric: tabular-nums;
  }
  .card-sub {
    font-size: 0.74rem;
    color: var(--muted);
    margin-top: 2px;
  }
  .section-title {
    padding: 12px 20px 6px;
    font-size: 0.74rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .history-list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .history-item {
    background: var(--panel-alt);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 9px 11px;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.8rem;
  }
  .history-item:hover {
    border-color: var(--accent);
  }
  .history-coords {
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .history-meta {
    font-size: 0.72rem;
    color: var(--muted);
    margin-top: 2px;
  }
  .badge {
    font-size: 0.72rem;
    padding: 2px 7px;
    border-radius: 6px;
    background: rgba(34, 197, 94, 0.14);
    color: #4ade80;
    font-variant-numeric: tabular-nums;
  }
  .map-wrap {
    position: relative;
    height: 100%;
    width: 100%;
  }
  #map {
    height: 100%;
    width: 100%;
    background: #0d131f;
  }
  .map-overlay {
    position: absolute;
    top: 14px;
    right: 14px;
    z-index: 1000;
    background: rgba(17, 24, 39, 0.9);
    backdrop-filter: blur(8px);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 0.78rem;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .empty-banner {
    padding: 20px;
    text-align: center;
    color: var(--muted);
    font-size: 0.83rem;
  }
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="header">
      <div class="brand">
        <span id="connDot" class="brand-dot offline"></span>
        <div>
          <h1>PhoneBridge Tracker</h1>
          <p id="connText">Waiting for phone…</p>
        </div>
      </div>
      <span id="filterBadge" class="pill" title="Only social media, messaging & email notifications are forwarded">Social &amp; Comm Filter ON</span>
    </div>

    <div class="controls">
      <div class="control-row">
        <select id="deviceSelect" aria-label="Device">
          <option value="">All devices</option>
        </select>
        <select id="windowSelect" aria-label="Time window">
          <option value="1">Last 1h</option>
          <option value="6">Last 6h</option>
          <option value="24" selected>Last 24h</option>
          <option value="168">Last 7d</option>
          <option value="0">All history</option>
        </select>
      </div>
      <div class="control-row">
        <button id="locateBtn" class="primary" type="button">Locate Phone Now</button>
        <button id="followBtn" type="button">Follow: ON</button>
      </div>
      <div class="control-row">
        <a id="gpxLink" class="btn" href="/api/export.gpx" download="phone-track.gpx" style="flex:1">Export GPX</a>
        <a id="geojsonLink" class="btn" href="/api/export.geojson" download="phone-track.geojson" style="flex:1">Export GeoJSON</a>
      </div>
    </div>

    <div class="cards">
      <div class="card wide">
        <div class="card-label">Current Coordinates</div>
        <div id="coordValue" class="card-value">—</div>
        <div id="timeValue" class="card-sub">No GPS fix recorded yet</div>
      </div>
      <div class="card">
        <div class="card-label">Accuracy / Provider</div>
        <div id="accValue" class="card-value">—</div>
        <div id="provValue" class="card-sub">—</div>
      </div>
      <div class="card">
        <div class="card-label">Phone Battery</div>
        <div id="battValue" class="card-value">—</div>
        <div id="chargeValue" class="card-sub">—</div>
      </div>
      <div class="card">
        <div class="card-label">Speed / Altitude</div>
        <div id="speedValue" class="card-value">—</div>
        <div id="altValue" class="card-sub">—</div>
      </div>
      <div class="card">
        <div class="card-label">Track Distance</div>
        <div id="distValue" class="card-value">0 m</div>
        <div id="countValue" class="card-sub">0 fixes</div>
      </div>
    </div>

    <div class="section-title">
      <span>Recent GPS Fixes</span>
      <span id="liveStatus">Live SSE</span>
    </div>
    <div id="historyList" class="history-list">
      <div class="empty-banner">Connect your phone or run <code>phone-bridge simulate</code> to stream GPS points.</div>
    </div>
  </aside>

  <main class="map-wrap">
    <div id="map"></div>
    <div class="map-overlay">
      <span id="overlayText">Tailscale WebSocket → SQLite → Map UI</span>
    </div>
  </main>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
(() => {
  const map = L.map('map', { zoomControl: true }).setView([37.7749, -122.4194], 13);
  const darkTiles = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 20,
    attribution: '&copy; OpenStreetMap &copy; CARTO'
  });
  const osmTiles = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  });
  darkTiles.addTo(map);
  L.control.layers({ 'Dark Map': darkTiles, 'Street Map': osmTiles }).addTo(map);

  let followMode = true;
  let latestMarker = null;
  let accuracyCircle = null;
  let trailLine = null;
  let pointLayer = L.layerGroup().addTo(map);

  const deviceSelect = document.getElementById('deviceSelect');
  const windowSelect = document.getElementById('windowSelect');
  const locateBtn = document.getElementById('locateBtn');
  const followBtn = document.getElementById('followBtn');
  const gpxLink = document.getElementById('gpxLink');
  const geojsonLink = document.getElementById('geojsonLink');

  function fmtTime(ts) {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    const ago = Math.max(0, Math.round(Date.now() / 1000 - ts));
    const rel = ago < 60 ? `${ago}s ago` : ago < 3600 ? `${Math.round(ago / 60)}m ago` : `${Math.round(ago / 3600)}h ago`;
    return `${d.toLocaleTimeString()} (${rel})`;
  }

  function fmtDistance(m) {
    if (!m || m < 1) return '0 m';
    return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
  }

  function updateExportLinks() {
    const dev = encodeURIComponent(deviceSelect.value || '');
    const hrs = encodeURIComponent(windowSelect.value || '0');
    gpxLink.href = `/api/export.gpx?device=${dev}&hours=${hrs}`;
    geojsonLink.href = `/api/export.geojson?device=${dev}&hours=${hrs}`;
  }

  function renderTelemetry(pt, count, distMeters) {
    document.getElementById('distValue').textContent = fmtDistance(distMeters);
    document.getElementById('countValue').textContent = `${count} fixes in window`;
    if (!pt) return;
    document.getElementById('coordValue').textContent = `${pt.latitude.toFixed(6)}, ${pt.longitude.toFixed(6)}`;
    document.getElementById('timeValue').textContent = `${pt.device} • ${fmtTime(pt.timestamp)}`;
    document.getElementById('accValue').textContent = `±${(pt.accuracy || 0).toFixed(1)} m`;
    document.getElementById('provValue').textContent = `Provider: ${(pt.provider || 'gps').toUpperCase()}`;
    document.getElementById('battValue').textContent = pt.battery != null ? `${pt.battery}%` : '—';
    document.getElementById('chargeValue').textContent = pt.charging ? 'Charging ⚡' : 'On battery';
    const spd = pt.speed != null ? `${(pt.speed * 3.6).toFixed(1)} km/h` : '0.0 km/h';
    document.getElementById('speedValue').textContent = spd;
    document.getElementById('altValue').textContent = pt.altitude != null ? `Alt ${pt.altitude.toFixed(1)} m` : 'Altitude n/a';
  }

  function renderMapPoints(history, latest) {
    pointLayer.clearLayers();
    if (trailLine) {
      map.removeLayer(trailLine);
      trailLine = null;
    }
    if (history.length >= 2) {
      const latlngs = history.map(p => [p.latitude, p.longitude]);
      trailLine = L.polyline(latlngs, {
        color: '#38bdf8',
        weight: 3.5,
        opacity: 0.85
      }).addTo(map);
    }
    history.forEach((pt, idx) => {
      if (idx === history.length - 1) return;
      L.circleMarker([pt.latitude, pt.longitude], {
        radius: 4,
        color: '#0284c7',
        fillColor: '#38bdf8',
        fillOpacity: 0.7,
        weight: 1
      }).bindPopup(
        `<b>${pt.device}</b><br/>${pt.latitude.toFixed(6)}, ${pt.longitude.toFixed(6)}<br/>` +
        `Accuracy: ±${(pt.accuracy || 0).toFixed(1)}m<br/>Battery: ${pt.battery != null ? pt.battery + '%' : 'n/a'}<br/>` +
        `${new Date(pt.timestamp * 1000).toLocaleString()}`
      ).addTo(pointLayer);
    });

    const focus = latest || history[history.length - 1];
    if (!focus) return;
    const pos = [focus.latitude, focus.longitude];
    if (!latestMarker) {
      latestMarker = L.circleMarker(pos, {
        radius: 8,
        color: '#ffffff',
        weight: 2.5,
        fillColor: '#22c55e',
        fillOpacity: 1
      }).addTo(map);
    } else {
      latestMarker.setLatLng(pos);
    }
    latestMarker.bindPopup(
      `<b>${focus.device} (Latest)</b><br/>${focus.latitude.toFixed(6)}, ${focus.longitude.toFixed(6)}<br/>` +
      `Accuracy: ±${(focus.accuracy || 0).toFixed(1)}m • Battery: ${focus.battery != null ? focus.battery + '%' : 'n/a'}`
    );

    const radius = Math.max(focus.accuracy || 0, 4);
    if (!accuracyCircle) {
      accuracyCircle = L.circle(pos, {
        radius,
        color: '#22c55e',
        weight: 1,
        fillColor: '#22c55e',
        fillOpacity: 0.12
      }).addTo(map);
    } else {
      accuracyCircle.setLatLng(pos);
      accuracyCircle.setRadius(radius);
    }

    if (followMode) {
      map.panTo(pos, { animate: true });
      if (map.getZoom() < 15) map.setZoom(16);
    }
  }

  function renderHistoryList(history) {
    const container = document.getElementById('historyList');
    if (!history.length) {
      container.innerHTML = '<div class="empty-banner">No GPS fixes in selected window.</div>';
      return;
    }
    const recent = history.slice(-40).reverse();
    container.innerHTML = '';
    recent.forEach(pt => {
      const row = document.createElement('div');
      row.className = 'history-item';
      const batt = pt.battery != null ? `${pt.battery}%` : `±${(pt.accuracy || 0).toFixed(0)}m`;
      row.innerHTML = `
        <div>
          <div class="history-coords">${pt.latitude.toFixed(5)}, ${pt.longitude.toFixed(5)}</div>
          <div class="history-meta">${pt.device} • ±${(pt.accuracy || 0).toFixed(1)}m • ${fmtTime(pt.timestamp)}</div>
        </div>
        <span class="badge">${batt}</span>
      `;
      row.addEventListener('click', () => {
        followMode = false;
        followBtn.textContent = 'Follow: OFF';
        map.setView([pt.latitude, pt.longitude], 17, { animate: true });
      });
      container.appendChild(row);
    });
  }

  async function refreshStatus() {
    const res = await fetch('/api/status');
    const data = await res.json();
    const dot = document.getElementById('connDot');
    const connText = document.getElementById('connText');
    const clients = data.connected_clients || [];
    if (clients.length > 0) {
      dot.classList.remove('offline');
      connText.textContent = `Connected: ${clients.map(c => c.device).join(', ')}`;
    } else {
      dot.classList.add('offline');
      connText.textContent = `Phone offline (${data.total_points || 0} stored fixes)`;
    }
    const badge = document.getElementById('filterBadge');
    badge.textContent = data.only_allowed_apps ? 'Social & Comm Filter ON' : 'All Notifications ON';

    const current = deviceSelect.value;
    const known = (data.devices || []).map(d => d.device);
    deviceSelect.innerHTML = '<option value="">All devices</option>';
    known.forEach(dev => {
      const opt = document.createElement('option');
      opt.value = dev;
      opt.textContent = dev;
      if (dev === current) opt.selected = true;
      deviceSelect.appendChild(opt);
    });
  }

  async function refreshLocations() {
    updateExportLinks();
    const dev = encodeURIComponent(deviceSelect.value || '');
    const hrs = encodeURIComponent(windowSelect.value || '24');
    const res = await fetch(`/api/locations?device=${dev}&hours=${hrs}&limit=500`);
    const data = await res.json();
    const history = data.history || [];
    renderTelemetry(data.latest, history.length, data.distance_meters || 0);
    renderMapPoints(history, data.latest);
    renderHistoryList(history);
  }

  deviceSelect.addEventListener('change', refreshLocations);
  windowSelect.addEventListener('change', refreshLocations);

  followBtn.addEventListener('click', () => {
    followMode = !followMode;
    followBtn.textContent = followMode ? 'Follow: ON' : 'Follow: OFF';
  });

  locateBtn.addEventListener('click', async () => {
    locateBtn.disabled = true;
    const dev = encodeURIComponent(deviceSelect.value || '');
    try {
      const res = await fetch(`/api/locate?device=${dev}`, { method: 'POST' });
      const info = await res.json();
      locateBtn.textContent = info.requested > 0 ? 'Requested GPS Fix ✓' : 'No Phone Connected';
    } catch (_) {
      locateBtn.textContent = 'Request Failed';
    }
    setTimeout(() => {
      locateBtn.disabled = false;
      locateBtn.textContent = 'Locate Phone Now';
    }, 2000);
  });

  const es = new EventSource('/api/events');
  es.onopen = () => {
    document.getElementById('liveStatus').textContent = 'Live SSE ●';
  };
  es.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.event === 'location' || msg.event === 'session') {
        refreshStatus();
        refreshLocations();
      }
    } catch (_) {}
  };
  es.onerror = () => {
    document.getElementById('liveStatus').textContent = 'Reconnecting…';
  };

  refreshStatus().then(refreshLocations);
})();
</script>
</body>
</html>
"""


class MapWebServer:
    """Async HTTP server exposing the Leaflet Map UI and JSON/GeoJSON/GPX/SSE APIs."""

    def __init__(
        self,
        config: "Config",
        store: LocationStore,
        bridge: "BridgeServer | None" = None,
    ) -> None:
        self.config = config
        self.store = store
        self.bridge = bridge
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        if self._server and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self.config.map_port

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.config.map_host in {"0.0.0.0", "::", ""} else self.config.map_host
        return f"http://{host}:{self.port}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            self.config.map_host,
            self.config.map_port,
        )
        log.info("map UI listening on %s", self.url)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not request_line:
                return
            parts = request_line.decode("utf-8", errors="replace").strip().split()
            if len(parts) < 2:
                return
            method, target = parts[0].upper(), parts[1]

            # Read headers
            content_length = 0
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10.0)
                if not line or line in (b"\r\n", b"\n"):
                    break
                header = line.decode("utf-8", errors="replace")
                if header.lower().startswith("content-length:"):
                    with contextlib.suppress(ValueError):
                        content_length = int(header.split(":", 1)[1].strip())
            if content_length > 0:
                await asyncio.wait_for(reader.read(min(content_length, 65536)), timeout=10.0)

            parsed = urlparse(target)
            path = parsed.path
            query = parse_qs(parsed.query)

            if path == "/" and method == "GET":
                await self._send_response(
                    writer,
                    200,
                    MAP_HTML.encode("utf-8"),
                    "text/html; charset=utf-8",
                )
            elif path == "/api/status" and method == "GET":
                payload = self._build_status()
                await self._send_json(writer, 200, payload)
            elif path == "/api/locations" and method == "GET":
                payload = self._build_locations(query)
                await self._send_json(writer, 200, payload)
            elif path == "/api/export.geojson" and method == "GET":
                device, since, limit = self._parse_filter(query, default_limit=2000)
                geo = self.store.export_geojson(device, since=since, limit=limit)
                body = json.dumps(geo, indent=2).encode("utf-8")
                await self._send_response(
                    writer,
                    200,
                    body,
                    "application/geo+json; charset=utf-8",
                    extra_headers={"Content-Disposition": 'attachment; filename="phone-track.geojson"'},
                )
            elif path == "/api/export.gpx" and method == "GET":
                device, since, limit = self._parse_filter(query, default_limit=2000)
                gpx = self.store.export_gpx(device, since=since, limit=limit)
                await self._send_response(
                    writer,
                    200,
                    gpx.encode("utf-8"),
                    "application/gpx+xml; charset=utf-8",
                    extra_headers={"Content-Disposition": 'attachment; filename="phone-track.gpx"'},
                )
            elif path == "/api/locate" and method in {"POST", "GET"}:
                device = (query.get("device") or [""])[0].strip() or None
                requested = 0
                if self.bridge is not None:
                    requested = await self.bridge.request_location(device)
                await self._send_json(writer, 200, {"ok": True, "requested": requested})
            elif path == "/api/events" and method == "GET":
                await self._stream_events(writer)
            else:
                await self._send_json(writer, 404, {"error": "not_found", "path": path})
        except (asyncio.TimeoutError, ConnectionError):
            pass
        except Exception:
            log.exception("error handling web request")
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    def _parse_filter(
        self,
        query: dict[str, list[str]],
        *,
        default_limit: int = 500,
    ) -> tuple[str | None, float | None, int]:
        device = (query.get("device") or [""])[0].strip() or None
        since: float | None = None
        if "since" in query:
            with contextlib.suppress(ValueError):
                since = float(query["since"][0])
        elif "hours" in query:
            with contextlib.suppress(ValueError):
                hours = float(query["hours"][0])
                if hours > 0:
                    since = time.time() - hours * 3600.0
        limit = default_limit
        if "limit" in query:
            with contextlib.suppress(ValueError):
                limit = max(1, min(10_000, int(query["limit"][0])))
        return device, since, limit

    def _build_status(self) -> dict[str, Any]:
        latest = self.store.latest()
        clients = self.bridge.active_sessions() if self.bridge else []
        return {
            "server": self.config.device_name,
            "connected_clients": clients,
            "devices": self.store.devices(),
            "latest": latest.to_dict() if latest else None,
            "total_points": self.store.count(),
            "only_allowed_apps": self.config.only_allowed_apps,
            "allowed_apps": list(self.config.allowed_apps),
        }

    def _build_locations(self, query: dict[str, list[str]]) -> dict[str, Any]:
        device, since, limit = self._parse_filter(query, default_limit=500)
        records = self.store.history(device, since=since, limit=limit)
        latest = self.store.latest(device)
        return {
            "device": device or "",
            "latest": latest.to_dict() if latest else None,
            "history": [r.to_dict() for r in records],
            "count": len(records),
            "distance_meters": round(total_distance_meters(records), 2),
        }

    async def _stream_events(self, writer: asyncio.StreamWriter) -> None:
        headers = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream; charset=utf-8\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "Access-Control-Allow-Origin: *\r\n\r\n"
        )
        writer.write(headers.encode("utf-8"))
        await writer.drain()
        if self.bridge is None:
            return
        queue = self.bridge.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    data = json.dumps(event, separators=(",", ":"))
                    writer.write(f"data: {data}\n\n".encode("utf-8"))
                except asyncio.TimeoutError:
                    writer.write(b": keepalive\n\n")
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            return
        finally:
            self.bridge.unsubscribe(queue)

    async def _send_json(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        await self._send_response(writer, status, body, "application/json; charset=utf-8")

    async def _send_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes,
        content_type: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        reasons = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error"}
        reason = reasons.get(status, "OK")
        lines = [
            f"HTTP/1.1 {status} {reason}",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(body)}",
            "Cache-Control: no-store",
            "Connection: close",
        ]
        if extra_headers:
            for k, v in extra_headers.items():
                lines.append(f"{k}: {v}")
        lines.append("")
        lines.append("")
        writer.write("\r\n".join(lines).encode("utf-8") + body)
        await writer.drain()
