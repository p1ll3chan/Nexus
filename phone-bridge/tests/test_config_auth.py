import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from authentication import AuthError, Authenticator
from config import Config, ConfigError, generate_token, load_config, write_default_config


class ConfigTests(unittest.TestCase):
    def test_token_is_generated_when_missing(self):
        config = load_config(env={})
        self.assertTrue(config.token)

    def test_env_and_overrides_precedence(self):
        config = load_config(
            overrides={"host": "10.0.0.1", "port": 1234},
            env={"PHONE_BRIDGE_TOKEN": "secret", "PHONE_BRIDGE_PORT": "9999"},
        )
        self.assertEqual(config.host, "10.0.0.1")
        self.assertEqual(config.port, 1234)
        self.assertEqual(config.token, "secret")

    def test_invalid_port_rejected(self):
        with self.assertRaises(ConfigError):
            load_config(overrides={"port": 70000}, env={})

    def test_invalid_backend_rejected(self):
        with self.assertRaises(ConfigError):
            load_config(overrides={"notify_backend": "carrier-pigeon"}, env={})

    def test_web_url_and_device_allow_list(self):
        config = Config(token="t", enabled_devices=("my-phone",))
        self.assertTrue(config.is_device_allowed("MY-PHONE"))
        self.assertFalse(config.is_device_allowed("other"))

    def test_toml_file_round_trip(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            write_default_config(path, Config(token="abc", port=8765))
            loaded = load_config(path=path, env={})
            self.assertEqual(loaded.token, "abc")
            self.assertEqual(loaded.port, 8765)


class AuthTests(unittest.TestCase):
    def test_valid_and_invalid_token(self):
        auth = Authenticator("correct-horse")
        self.assertTrue(auth.verify("correct-horse", source="1.2.3.4"))
        with self.assertRaises(AuthError):
            auth.verify("wrong", source="5.6.7.8")

    def test_rate_limiting(self):
        auth = Authenticator("secret", max_attempts=3)
        for _ in range(3):
            with self.assertRaises(AuthError):
                auth.verify("nope", source="9.9.9.9")
        with self.assertRaises(AuthError):
            auth.verify("secret", source="9.9.9.9")

    def test_generate_token_is_random(self):
        self.assertNotEqual(generate_token(), generate_token())


if __name__ == "__main__":
    unittest.main()
