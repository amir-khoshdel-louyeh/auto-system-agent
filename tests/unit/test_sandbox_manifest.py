"""Phase 8 item [75]: plugin manifest validation.

The schema is closed (unknown keys are errors), names/versions are checked,
declared inputs/outputs use a frozen type vocabulary, and a manifest can
never ask for network access the sandbox does not offer.
"""

import json
import tempfile
import unittest
from pathlib import Path

from sandbox.manifest import (
    CAPABILITIES,
    FIELD_TYPES,
    MANIFEST_VERSION,
    ManifestError,
    PluginManifest,
    load_manifest,
    load_manifest_file,
)

RAW = {
    "name": "hello-world",
    "version": "1.0.0",
    "entrypoint": "hello.py",
    "description": "say hello",
    "inputs": {"name": "string"},
    "outputs": {"greeting": "string"},
    "capabilities": ["fs:workspace"],
}


class ManifestSchemaTests(unittest.TestCase):
    def test_vocabulary_is_frozen(self):
        self.assertEqual(MANIFEST_VERSION, "plugin-v1")
        self.assertEqual(FIELD_TYPES, ("string", "int", "float", "bool", "path", "json"))
        self.assertEqual(CAPABILITIES, ("fs:workspace",))

    def test_valid_manifest_parses(self):
        manifest = load_manifest(RAW)
        self.assertEqual(manifest.name, "hello-world")
        self.assertEqual(manifest.inputs, {"name": "string"})
        self.assertEqual(manifest.capabilities, ("fs:workspace",))
        self.assertFalse(manifest.network)
        self.assertEqual(manifest.manifest_version, MANIFEST_VERSION)

    def test_unknown_keys_are_refused(self):
        with self.assertRaises(ManifestError) as caught:
            load_manifest({**RAW, "network_access": True})
        self.assertIn("unknown manifest keys", str(caught.exception))

    def test_missing_required_keys_are_refused(self):
        for key in ("name", "version", "entrypoint"):
            with self.subTest(key=key):
                raw = {k: v for k, v in RAW.items() if k != key}
                with self.assertRaises(ManifestError):
                    load_manifest(raw)

    def test_name_and_version_syntax(self):
        for name in ("Bad Name", "ab", "1plugin", "plugin!"):
            with self.subTest(name=name):
                with self.assertRaises(ManifestError):
                    load_manifest({**RAW, "name": name})
        for version in ("1.0", "v1.0.0", "1.0.0-beta"):
            with self.subTest(version=version):
                with self.assertRaises(ManifestError):
                    load_manifest({**RAW, "version": version})

    def test_entrypoint_must_not_escape(self):
        for entrypoint in ("/abs.py", "../escape.py", "sub/../x.py", "plugin.txt"):
            with self.subTest(entrypoint=entrypoint):
                with self.assertRaises(ManifestError):
                    load_manifest({**RAW, "entrypoint": entrypoint})

    def test_field_types_are_a_frozen_vocabulary(self):
        with self.assertRaises(ManifestError):
            load_manifest({**RAW, "inputs": {"count": "integer"}})
        with self.assertRaises(ManifestError):
            load_manifest({**RAW, "outputs": {"Bad-Name": "string"}})

    def test_network_cannot_be_declared(self):
        with self.assertRaises(ManifestError) as caught:
            load_manifest({**RAW, "network": True})
        self.assertIn("no network", str(caught.exception))

    def test_capabilities_are_limited_to_what_the_sandbox_provides(self):
        with self.assertRaises(ManifestError) as caught:
            load_manifest({**RAW, "capabilities": ["fs:workspace", "net:internet"]})
        self.assertIn("net:internet", str(caught.exception))

    def test_timeout_bounds(self):
        with self.assertRaises(ManifestError):
            load_manifest({**RAW, "timeout_s": 120})

    def test_manifest_round_trips(self):
        manifest = load_manifest(RAW)
        self.assertEqual(load_manifest(manifest.as_dict()), manifest)


class DeclaredInputTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(RAW)

    def test_matching_inputs_pass(self):
        self.assertEqual(self.manifest.input_problems({"name": "buffy"}), ())

    def test_missing_and_mistyped_inputs_are_reported(self):
        problems = self.manifest.input_problems({"name": 3, "extra": True})
        self.assertTrue(any("must be string" in problem for problem in problems))
        self.assertTrue(any("undeclared input 'extra'" in problem for problem in problems))

    def test_missing_input_is_named(self):
        problems = self.manifest.input_problems({})
        self.assertEqual(problems, ("missing input 'name'",))

    def test_path_inputs_must_be_relative(self):
        entry = PluginManifest(
            name="path-plugin", version="1.0.0", entrypoint="p.py", inputs={"file": "path"}
        )
        self.assertEqual(entry.input_problems({"file": "data/a.txt"}), ())
        self.assertTrue(entry.input_problems({"file": "/etc/passwd"}))
        self.assertTrue(entry.input_problems({"file": "../outside"}))

    def test_manifest_file_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plugin.json"
            path.write_text(json.dumps(RAW), encoding="utf-8")
            self.assertEqual(load_manifest_file(path).name, "hello-world")
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest_file(path)


if __name__ == "__main__":
    unittest.main()
