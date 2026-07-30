"""Phase 4 item [37]: frozen event JSON schemas (FR-09).

The cross-service contract is a closed set. Every event carries the frozen
envelope — id, type, source, time, correlation_id, idempotency_key, data —
and an unknown type or a missing idempotency key is rejected in code.
"""

import json
import unittest

from bus.events import (
    FROZEN_TYPES,
    KNOWN_TYPES,
    WORKER_STEP_TYPES,
    Event,
    new_event,
)

REQUIRED_KEYS = {"id", "type", "source", "time", "correlation_id", "idempotency_key", "data"}


class FrozenSchemaTests(unittest.TestCase):
    def test_cross_service_contract_is_exactly_five_types(self):
        self.assertEqual(
            FROZEN_TYPES,
            (
                "plan.created",
                "approval.requested",
                "step.failed",
                "file.arrived",
                "schedule.tick",
            ),
        )

    def test_worker_step_types_are_engine_internal(self):
        self.assertEqual(WORKER_STEP_TYPES, ("step.dispatched", "step.finished"))

    def test_unknown_type_is_rejected(self):
        with self.assertRaises(ValueError):
            new_event("made.up")

    def test_idempotency_key_is_required(self):
        with self.assertRaises(ValueError):
            Event(id="1", type="plan.created", source="engine", time="t", idempotency_key="")


class EnvelopeTests(unittest.TestCase):
    def test_event_serialises_with_the_frozen_envelope(self):
        event = new_event("plan.created", {"run_id": "r1"}, correlation_id="r1")
        payload = json.loads(event.to_json())
        self.assertEqual(set(payload), REQUIRED_KEYS)
        self.assertEqual(payload["type"], "plan.created")
        self.assertEqual(payload["correlation_id"], "r1")
        self.assertTrue(payload["idempotency_key"])

    def test_round_trip_preserves_every_field(self):
        event = new_event(
            "step.dispatched",
            {"node_id": "a"},
            source="dispatcher",
            correlation_id="r1",
            idempotency_key="r1:a:1",
        )
        rebuilt = Event.from_dict(json.loads(event.to_json()))
        self.assertEqual(rebuilt, event)

    def test_json_is_canonical(self):
        event = new_event("plan.created", {"b": 1, "a": 2})
        self.assertEqual(event.to_json(), json.dumps(event.to_dict(), sort_keys=True, default=str))

    def test_new_event_defaults_a_unique_idempotency_key(self):
        first = new_event("plan.created")
        second = new_event("plan.created")
        self.assertNotEqual(first.idempotency_key, second.idempotency_key)

    def test_all_known_types_serialise(self):
        for event_type in sorted(KNOWN_TYPES):
            with self.subTest(event_type=event_type):
                event = new_event(event_type)
                self.assertEqual(Event.from_dict(event.to_dict()).type, event_type)


if __name__ == "__main__":
    unittest.main()
