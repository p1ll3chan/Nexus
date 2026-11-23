"""Config additions for Nexus Remote and the placeholder-token fix (Day 4 G2)."""

import datetime as dt
import os
import stat
import tempfile
import unittest
from pathlib import Path

from config import Config, ConfigError, load_config, write_default_config


class NexusConfigTests(unittest.TestCase):
    def test_placeholder_token_rejected(self):
        for token in ("change-me", "CHANGE-ME", " changeme ", "your-token-here"):
            with self.subTest(token=token):
                with self.assertRaises(ConfigError):
                    load_config(env={"PHONE_BRIDGE_TOKEN": token})

    def test_example_config_no_longer_loads_with_placeholder(self):
        example = Path(__file__).resolve().parents[1] / "config.example.toml"
        with self.assertRaises(ConfigError):
            load_config(path=example, env={})

    def test_remote_defaults(self):
        config = load_config(env={})
        self.assertTrue(config.remote_enabled)
        self.assertTrue(config.remote_db_path.endswith("nexus.db"))
        self.assertEqual(config.remote_disks, {"root": "/", "home": "~"})
        self.assertTrue(config.legacy_token_allowed())

    def test_legacy_token_expiry(self):
        config = load_config(env={"PHONE_BRIDGE_LEGACY_TOKEN_EXPIRES": "2030-01-31"})
        self.assertTrue(config.legacy_token_allowed(dt.date(2030, 1, 31)))
        self.assertFalse(config.legacy_token_allowed(dt.date(2030, 2, 1)))
        self.assertFalse(Config(legacy_token_auth=False).legacy_token_allowed())
        with self.assertRaises(ConfigError):
            load_config(env={"PHONE_BRIDGE_LEGACY_TOKEN_EXPIRES": "soon"})

    def test_remote_disks_from_env_and_validation(self):
        config = load_config(env={"PHONE_BRIDGE_REMOTE_DISKS": "root=/,data=/mnt/data"})
        self.assertEqual(config.remote_disks, {"root": "/", "data": "/mnt/data"})
        with self.assertRaises(ConfigError):
            load_config(env={"PHONE_BRIDGE_REMOTE_DISKS": "bad label=/"})

    def test_written_config_round_trips_and_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            write_default_config(path, Config(token="abc-token", remote_disks={"root": "/"}))
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            loaded = load_config(path=path, env={})
            self.assertEqual(loaded.remote_disks, {"root": "/"})
            self.assertTrue(loaded.remote_enabled)


if __name__ == "__main__":
    unittest.main()
