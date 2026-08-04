"""Phase 8 item [76]: registered plugins run sandbox-first.

The registry refuses unknown, duplicate and unregistered plugins; a
registered plugin's source is seeded into a disposable workspace and only its
declared inputs reach it. The `custom` bus worker publishes the sandbox
verdict and turns an escape attempt into `step.failed`.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from bus.backend import MemoryBackend
from bus.events import new_event
from bus.workers.core import RESULTS_STREAM, steps_stream
from bus.workers.custom import make_custom_worker, plugin_call
from sandbox.plugins import PluginError, PluginRegistry

MANIFEST = {
    "name": "hello-world",
    "version": "1.0.0",
    "entrypoint": "hello.py",
    "inputs": {"name": "string"},
    "outputs": {"greeting": "string"},
}
SOURCE = (
    "def run(ctx):\n"
    "    open('greeting.txt', 'w').write('hi ' + ctx['name'])\n"
    "    return {'greeting': 'hi ' + ctx['name']}\n"
)


def registry_with(source: str = SOURCE, manifest: dict | None = None) -> PluginRegistry:
    registry = PluginRegistry()
    registry.register(manifest or MANIFEST, source)
    return registry


class RegistryTests(unittest.TestCase):
    def test_register_and_describe(self):
        registry = registry_with()
        self.assertEqual(registry.names(), ("hello-world",))
        described = registry.describe()[0]
        self.assertEqual(described["entrypoint"], "hello.py")
        self.assertEqual(described["inputs"], {"name": "string"})

    def test_duplicate_registration_is_refused(self):
        registry = registry_with()
        with self.assertRaises(PluginError):
            registry.register(MANIFEST, SOURCE)
        registry.register(MANIFEST, SOURCE, replace=True)

    def test_unregistered_plugin_is_refused(self):
        with self.assertRaises(PluginError) as caught:
            registry_with().request("nope", {})
        self.assertIn("not registered", str(caught.exception))

    def test_declared_inputs_are_enforced_before_running(self):
        registry = registry_with()
        with self.assertRaises(PluginError) as caught:
            registry.run("hello-world", {"wrong": "x"})
        self.assertIn("missing input 'name'", str(caught.exception))
        with self.assertRaises(PluginError):
            registry.run("hello-world", {"name": 3})

    def test_sandbox_runs_the_plugin_and_reports_files(self):
        result = registry_with().run("hello-world", {"name": "buffy"})
        self.assertTrue(result.ok, result.output_summary)
        self.assertEqual(result.result, {"greeting": "hi buffy"})
        self.assertEqual([change.path for change in result.files], ["greeting.txt"])

    def test_discovery_reads_manifest_source_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "hello-world.json").write_text(
                __import__("json").dumps(MANIFEST), encoding="utf-8"
            )
            (folder / "hello-world.py").write_text(SOURCE, encoding="utf-8")
            registry = PluginRegistry()
            registered = registry.discover(folder)
        self.assertEqual([manifest.name for manifest in registered], ["hello-world"])

    def test_discovery_rejects_a_manifest_without_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "hello-world.json").write_text(
                __import__("json").dumps(MANIFEST), encoding="utf-8"
            )
            registry = PluginRegistry()
            with self.assertRaises(PluginError):
                registry.discover(folder)

    def test_plugin_call_splits_func_from_inputs(self):
        node = {"type": "custom", "params": {"func": "hello-world", "name": "x"}}
        self.assertEqual(plugin_call(node), ("hello-world", {"name": "x"}))
        self.assertEqual(plugin_call({"params": {}}), ("", {}))


class CustomWorkerTests(unittest.TestCase):
    def _execute(self, registry: PluginRegistry, params: dict):
        async def scenario():
            backend = MemoryBackend()
            worker = make_custom_worker(backend, "g", registry)
            await backend.publish(
                steps_stream("custom"),
                new_event(
                    "step.dispatched",
                    {"node_id": "n1", "node": {"type": "custom", "params": params}},
                    correlation_id="r1",
                    idempotency_key="r1:n1",
                ),
            )
            await worker.drain_once()
            return (await backend.read_range(RESULTS_STREAM))[-1][1]

        return asyncio.run(scenario())

    def test_custom_node_finishes_through_the_sandbox(self):
        event = self._execute(registry_with(), {"func": "hello-world", "name": "buffy"})
        self.assertEqual(event.type, "step.finished")
        output = event.data["output"]
        self.assertEqual(output["plugin"], "hello-world")
        self.assertTrue(output["sandbox"]["ok"])
        self.assertIn(output["sandbox"]["isolation"], ("bwrap", "audit"))
        self.assertEqual(output["files"], ["greeting.txt"])

    def test_escape_attempt_fails_the_step(self):
        source = "def run(ctx):\n    open('../outside/evil.txt','w').write('x')\n    return {}\n"
        event = self._execute(registry_with(source), {"func": "hello-world", "name": "x"})
        self.assertEqual(event.type, "step.failed")
        self.assertIn("sandbox", event.data["error"])
        self.assertIn("denied", event.data["error"])

    def test_unknown_plugin_fails_the_step(self):
        event = self._execute(registry_with(), {"func": "missing"})
        self.assertEqual(event.type, "step.failed")
        self.assertIn("not registered", event.data["error"])

    def test_node_without_func_fails_the_step(self):
        event = self._execute(registry_with(), {})
        self.assertEqual(event.type, "step.failed")
        self.assertIn("no plugin", event.data["error"])


if __name__ == "__main__":
    unittest.main()
