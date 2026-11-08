import asyncio
import json
import unittest
import urllib.request

from client import BridgeClient
from config import Config
from notifications import NotificationService, is_app_allowed
from protocol import Location
from server import BridgeServer
from storage import LocationStore, distance_meters, total_distance_meters
from web import MapWebServer


class NotificationAllowlistTests(unittest.TestCase):
    def test_social_and_communication_apps_allowed(self):
        allowed = [
            ("WhatsApp", "com.whatsapp"),
            ("WhatsApp Business", "com.whatsapp.w4b"),
            ("Telegram", "org.telegram.messenger"),
            ("Signal", "org.thoughtcrime.securesms"),
            ("Discord", "com.discord"),
            ("Slack", "com.Slack"),
            ("Messages", "com.google.android.apps.messaging"),
            ("Messenger", "com.facebook.orca"),
            ("Gmail", "com.google.android.gm"),
            ("Outlook", "com.microsoft.office.outlook"),
            ("Proton Mail", "ch.protonmail.android"),
            ("Instagram", "com.instagram.android"),
            ("Reddit", "com.reddit.frontpage"),
            ("LinkedIn", "com.linkedin.android"),
            ("X", "com.twitter.android"),
            ("PhoneBridge", "com.phonebridge.app"),
        ]
        for app, pkg in allowed:
            with self.subTest(app=app, pkg=pkg):
                self.assertTrue(is_app_allowed(app, pkg))

    def test_unwanted_apps_rejected_by_default(self):
        unwanted = [
            ("ShoppingApp", "com.example.shopping"),
            ("System UI", "com.android.systemui"),
            ("Google Play Store", "com.android.vending"),
            ("Clock", "com.google.android.deskclock"),
            ("Weather", "com.weather.forecast"),
            ("Firefox", "org.mozilla.firefox"),
            ("Netflix", "com.netflix.mediaclient"),
            ("Candy Crush", "com.king.candycrushsaga"),
        ]
        service = NotificationService("none")
        for app, pkg in unwanted:
            with self.subTest(app=app, pkg=pkg):
                self.assertFalse(is_app_allowed(app, pkg))
                self.assertTrue(
                    service.should_ignore(
                        app=app,
                        package=pkg,
                        ongoing=False,
                        low_priority=False,
                    )
                )

    def test_only_allowed_apps_can_be_disabled(self):
        service = NotificationService("none", only_allowed_apps=False)
        self.assertFalse(
            service.should_ignore(
                app="CustomApp",
                package="com.example.custom",
                ongoing=False,
                low_priority=False,
            )
        )


class LocationStoreTests(unittest.TestCase):
    def test_record_query_and_export(self):
        store = LocationStore(":memory:")
        try:
            r1 = store.record(
                "pixel-9",
                Location(
                    latitude=37.7749,
                    longitude=-122.4194,
                    accuracy=6.0,
                    battery=92,
                    charging=False,
                    timestamp=1700000000.0,
                ),
            )
            r2 = store.record(
                "pixel-9",
                Location(
                    latitude=37.7759,
                    longitude=-122.4184,
                    accuracy=5.0,
                    battery=91,
                    charging=True,
                    altitude=22.5,
                    timestamp=1700000060.0,
                ),
            )
            self.assertEqual(store.count(), 2)
            latest = store.latest("pixel-9")
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.row_id, r2.row_id)
            self.assertEqual(latest.battery, 91)
            self.assertTrue(latest.charging)
            self.assertIn("openstreetmap.org", latest.osm_url)

            history = store.history("pixel-9", limit=10)
            self.assertEqual([h.row_id for h in history], [r1.row_id, r2.row_id])
            self.assertGreater(distance_meters(r1.latitude, r1.longitude, r2.latitude, r2.longitude), 100.0)
            self.assertGreater(total_distance_meters(history), 100.0)

            geojson = store.export_geojson("pixel-9")
            self.assertEqual(geojson["type"], "FeatureCollection")
            self.assertEqual(len(geojson["features"]), 3)  # 1 LineString + 2 Points

            gpx = store.export_gpx("pixel-9")
            self.assertIn("<gpx", gpx)
            self.assertIn("<trkpt lat=\"37.775900\" lon=\"-122.418400\">", gpx)
        finally:
            store.close()


class ServerAndWebIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_end_to_end_location_and_filtered_notifications(self):
        store = LocationStore(":memory:")
        config = Config(
            host="127.0.0.1",
            port=18765,
            token="test-token-123",
            notify_backend="none",
            map_host="127.0.0.1",
            map_port=0,
        )
        server = BridgeServer(config, store=store)
        web = MapWebServer(config, store, server)
        await server.start()
        await web.start()
        try:
            async with BridgeClient(config, device="my-phone") as client:
                await client.notify("WhatsApp", "Alice", "Hello!", package="com.whatsapp")
                await client.notify("RandomGame", "Gems", "Buy now!", package="com.game.spam")
                await client.location(
                    37.7749,
                    -122.4194,
                    accuracy=5.5,
                    battery=87,
                    charging=False,
                )
                await asyncio.sleep(0.15)

            self.assertEqual(server.notifier.sent, 1)
            self.assertEqual(store.count("my-phone"), 1)

            status_url = f"{web.url}/api/status"
            loc_url = f"{web.url}/api/locations?device=my-phone"
            html_url = f"{web.url}/"

            status_data = await asyncio.to_thread(
                lambda: json.loads(urllib.request.urlopen(status_url, timeout=5).read().decode())
            )
            self.assertEqual(status_data["total_points"], 1)
            self.assertTrue(status_data["only_allowed_apps"])

            loc_data = await asyncio.to_thread(
                lambda: json.loads(urllib.request.urlopen(loc_url, timeout=5).read().decode())
            )
            self.assertEqual(loc_data["count"], 1)
            self.assertAlmostEqual(loc_data["latest"]["latitude"], 37.7749)
            self.assertEqual(loc_data["latest"]["battery"], 87)

            html_body = await asyncio.to_thread(
                lambda: urllib.request.urlopen(html_url, timeout=5).read().decode()
            )
            self.assertIn("PhoneBridge Tracker", html_body)
        finally:
            await web.stop()
            await server.stop()
            store.close()


if __name__ == "__main__":
    unittest.main()
