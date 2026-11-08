import unittest

import protocol
from protocol import Call, CallState, Locate, Location, Notification, ProtocolError


class ProtocolTests(unittest.TestCase):
    def test_notification_round_trip(self):
        event = Notification(app="WhatsApp", title="John", text="Hi", id="abc")
        parsed = protocol.parse_event(protocol.loads(protocol.dumps(event.to_dict())))
        self.assertIsInstance(parsed, Notification)
        self.assertEqual(parsed.app, "WhatsApp")
        self.assertEqual(parsed.text, "Hi")
        self.assertEqual(parsed.id, "abc")

    def test_location_round_trip_and_validation(self):
        loc = Location(
            latitude=37.7749,
            longitude=-122.4194,
            accuracy=6.5,
            battery=84,
            charging=True,
            altitude=15.0,
            speed=1.4,
            bearing=90.0,
            provider="gps",
            id="loc1",
        )
        parsed = protocol.parse_event(protocol.loads(protocol.dumps(loc.to_dict())))
        self.assertIsInstance(parsed, Location)
        self.assertAlmostEqual(parsed.latitude, 37.7749)
        self.assertAlmostEqual(parsed.longitude, -122.4194)
        self.assertAlmostEqual(parsed.accuracy, 6.5)
        self.assertEqual(parsed.battery, 84)
        self.assertTrue(parsed.charging)
        self.assertEqual(parsed.provider, "gps")
        self.assertEqual(parsed.id, "loc1")

    def test_location_rejects_out_of_range_coordinates(self):
        with self.assertRaises(ProtocolError):
            protocol.parse_event({"type": "location", "latitude": 95.0, "longitude": 0.0})
        with self.assertRaises(ProtocolError):
            protocol.parse_event({"type": "location", "latitude": 0.0, "longitude": -190.0})

    def test_locate_round_trip(self):
        frame = protocol.locate_request("req123")
        parsed = protocol.parse_event(protocol.loads(protocol.dumps(frame)))
        self.assertIsInstance(parsed, Locate)
        self.assertEqual(parsed.id, "req123")

    def test_call_state_mapping(self):
        self.assertIs(CallState.from_android(1), CallState.RINGING)
        self.assertIs(CallState.from_android(2), CallState.OFFHOOK)
        self.assertIs(CallState.from_android(0), CallState.IDLE)
        self.assertIs(CallState.from_android("incoming"), CallState.RINGING)
        self.assertIs(CallState.from_android("answered"), CallState.OFFHOOK)
        with self.assertRaises(ProtocolError):
            CallState.from_android("bogus")

    def test_call_accepts_legacy_event_field(self):
        parsed = protocol.parse_event({"type": "call", "event": "ringing", "name": "John"})
        self.assertIsInstance(parsed, Call)
        self.assertIs(parsed.state, CallState.RINGING)
        self.assertEqual(parsed.name, "John")

    def test_missing_type_rejected(self):
        with self.assertRaises(ProtocolError):
            protocol.loads('{"title": "no type"}')

    def test_invalid_json_rejected(self):
        with self.assertRaises(ProtocolError):
            protocol.loads("not json")

    def test_unknown_type_rejected(self):
        with self.assertRaises(ProtocolError):
            protocol.message_type({"type": "nope"})

    def test_fields_are_truncated(self):
        parsed = protocol.parse_event(
            {
                "type": "notification",
                "app": "A",
                "title": "x" * 9000,
                "text": "y" * 9000,
            }
        )
        self.assertLessEqual(len(parsed.title), protocol.MAX_TITLE_LEN)
        self.assertLessEqual(len(parsed.text), protocol.MAX_TEXT_LEN)


if __name__ == "__main__":
    unittest.main()
