from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_runner.models import Run, RunStatus
from local_runner.models import ProgramCandidate, RunCreate, RunItemInput, RunMode
from local_runner.instance_lock import InstanceBusyError, acquire_instance_lock
from local_runner.__main__ import main as runner_main
from local_runner.server import create_app
from local_runner.managed import CentralError, CentralUnavailable


class RecordingExecutor:
    def __init__(self) -> None:
        self.direct: list[tuple[list[dict], dict]] = []
        self.batches: list[tuple[list[dict], dict]] = []

    def start_direct(self, run: dict, program: dict) -> None:
        self.direct.append(([run], program))

    def start_batch(self, runs: list[dict], program: dict) -> None:
        self.batches.append((runs, program))


class FakeCentral:
    """Protocol-level central fake: it never sees a browser connection code."""
    def __init__(self) -> None:
        self.tokens = {"pair-a": ("user-a", "Alice"), "pair-b": ("user-b", "Bob")}
        self.bindings: dict[str, dict] = {}
        self.execute_calls = 0
        self.offline_events = False
        self.events: list[dict] = []
        self.event_calls = 0
        self.events_denied = False
        self.events_limit: int | None = None
        self.event_payload_sizes: list[int] = []
        self.denied = False

    def post(self, path: str, bearer: str, payload: dict) -> dict:
        if path == "/device/pair-preview":
            if bearer not in self.tokens: raise CentralError(401)
            user_id, display_name = self.tokens[bearer]; return {"user_id": user_id, "display_name": display_name}
        if path == "/device/pair":
            if bearer not in self.tokens: raise CentralError(401)
            user_id, _ = self.tokens.pop(bearer); binding_id = f"binding-{user_id}"
            self.bindings[binding_id] = {"user_id": user_id, "secret": payload["device_secret"]}
            return {"id": binding_id, "user_id": user_id}
        binding = self.bindings.get(payload.get("binding_id"))
        if not binding or bearer != binding["secret"]: raise CentralError(401)
        if path == "/device/authorize":
            if self.denied: raise CentralError(403)
            if not payload["session_token"].startswith(payload["binding_id"] + "."): raise CentralError(401)
            context = dict(payload.get("context") or {})
            if payload["action"] in {"execute", "retry"}:
                self.execute_calls += 1
                context["actor"] = binding["user_id"]
                context["task_name"] = "authoritative task"
                return {"user_id": binding["user_id"], "display_name": binding["user_id"], "context": context, "grant_id": f"grant-{self.execute_calls}"}
            return {"user_id": binding["user_id"], "display_name": binding["user_id"], "context": context}
        if path == "/device/events":
            self.event_calls += 1
            size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            self.event_payload_sizes.append(size)
            if self.events_limit is not None and size > self.events_limit: raise CentralError(413)
            if self.events_denied: raise CentralError(403)
            if self.offline_events: raise CentralUnavailable("offline")
            self.events.extend(payload["events"])
            return {"accepted": [{"run_id": event["run"]["id"], "sequence": event["sequence"]} for event in payload["events"]]}
        raise AssertionError(path)


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "data"
        self.executor = RecordingExecutor()
        self.app = create_app(
            data_dir=self.data_dir,
            token="test-token",
            allowed_origins=["http://127.0.0.1:15173"],
            host_id="test-host",
            executor=self.executor,  # type: ignore[arg-type]
        )
        self.client = TestClient(
            self.app,
            base_url="http://127.0.0.1:8766",
            headers={"Authorization": "Bearer test-token", "Origin": "http://127.0.0.1:15173"},
        )
        self.work = Path(self.temp.name) / "work"
        self.work.mkdir()
        self.input_file = self.work / "case.inp"
        self.input_file.write_text("fixture")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def register(self, **extra: object) -> dict:
        payload = {
            "name": "HyperMesh",
            "version": "2025.1",
            "keywords": ["hypermesh", "mesh", "메시 수정"],
            "executable_path": sys.executable,
            "arguments": ["-c", "import sys; sys.exit(0)"],
        }
        payload.update(extra)
        response = self.client.post("/v1/programs", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_loopback_token_origin_and_query_guards(self) -> None:
        self.assertEqual(self.client.get("/v1/health").status_code, 200)
        self.assertEqual(self.client.get("/v1/health", headers={"Authorization": "Bearer wrong", "Origin": "http://127.0.0.1:15173"}).status_code, 401)
        no_origin = TestClient(self.app, base_url="http://127.0.0.1:8766", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(no_origin.get("/v1/health").status_code, 200)
        self.assertEqual(self.client.get("/v1/health", headers={"Authorization": "Bearer test-token", "Origin": "null"}).status_code, 403)
        self.assertEqual(self.client.get("/v1/health", headers={"Authorization": "Bearer test-token", "Origin": "http://evil.example"}).status_code, 403)
        off_host = TestClient(self.app, base_url="http://example.test", headers={"Authorization": "Bearer test-token", "Origin": "http://127.0.0.1:15173"})
        self.assertEqual(off_host.get("/v1/health").status_code, 421)
        self.assertEqual(self.client.get("/v1/programs?unknown=x").status_code, 422)
        options = off_host.options("/v1/programs", headers={"Origin": "http://127.0.0.1:15173", "Access-Control-Request-Method": "POST"})
        self.assertEqual(options.status_code, 421)
        malformed = self.client.post("/v1/programs", content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "not-a-number"})
        self.assertEqual(malformed.status_code, 400)
        too_large = self.client.post("/v1/programs", content=b"x" * 65_537, headers={"Content-Type": "application/json"})
        self.assertEqual(too_large.status_code, 413)
        update_too_large = self.client.put("/v1/programs/any", content=b"x" * 65_537, headers={"Content-Type": "application/json"})
        self.assertEqual(update_too_large.status_code, 413)

    def test_registration_search_and_snapshot_survive_metadata_update(self) -> None:
        program = self.register()
        self.assertEqual([item["id"] for item in self.client.get("/v1/programs?q=메시+수정").json()], [program["id"]])
        created = self.client.post("/v1/runs", json={
            "program_id": program["id"], "mode": "DIRECT", "items": [{"input_path": "", "working_directory": ""}],
            "context": {"request_id": "req-1", "work_item_id": "item-1", "task_name": "fixture", "actor": "tester"}, "idempotency_key": "direct-1",
        })
        self.assertEqual(created.status_code, 200, created.text)
        run = created.json()[0]
        self.assertEqual(run["program_snapshot"]["version"], "2025.1")
        changed = self.client.put(f"/v1/programs/{program['id']}", json={
            "name": "HyperMesh", "version": "2026.1", "keywords": ["mesh"], "executable_path": sys.executable, "arguments": []
        })
        self.assertEqual(changed.status_code, 200, changed.text)
        historical = self.client.get("/v1/runs?request_id=req-1").json()[0]
        self.assertEqual(historical["program_snapshot"]["version"], "2025.1")

    def test_batch_idempotency_does_not_start_again_and_direct_completion_is_explicit(self) -> None:
        program = self.register(arguments=["-c", "import sys; sys.exit(0)", "{input}"])
        batch = {
            "program_id": program["id"], "mode": "BATCH",
            "items": [{"input_path": str(self.input_file), "working_directory": str(self.work)}],
            "context": {}, "idempotency_key": "batch-one",
        }
        first = self.client.post("/v1/runs", json=batch)
        second = self.client.post("/v1/runs", json=batch)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()[0]["id"], second.json()[0]["id"])
        self.assertEqual(len(self.executor.batches), 1)
        conflicting = dict(batch, idempotency_key="batch-one", context={"request_id": "other"})
        self.assertEqual(self.client.post("/v1/runs", json=conflicting).status_code, 409)

        self.client.put(f"/v1/programs/{program['id']}", json={
            "name": "HyperMesh", "version": "2025.1", "keywords": ["mesh"],
            "executable_path": sys.executable, "arguments": ["-c", "pass"]
        })
        direct = self.client.post("/v1/runs", json={
            "program_id": program["id"], "mode": "DIRECT", "items": [{"input_path": "", "working_directory": ""}], "context": {}, "idempotency_key": "direct-two"
        }).json()[0]
        self.assertEqual(self.client.post(f"/v1/runs/{direct['id']}/complete", json={"note": "done"}).status_code, 409)
        self.app.state.storage.mark_started(direct["id"])
        self.app.state.storage.mark_finished(direct["id"], status=RunStatus.AWAITING_COMPLETION, exit_code=0)
        complete = self.client.post(f"/v1/runs/{direct['id']}/complete", json={"note": "confirmed"})
        self.assertEqual(complete.status_code, 200, complete.text)
        self.assertEqual(complete.json()["status"], "COMPLETED")

    def test_replay_precedes_changed_program_validation_and_direct_duplicates_are_refused(self) -> None:
        program = self.register()
        payload = {
            "program_id": program["id"], "mode": "DIRECT", "items": [{"input_path": "", "working_directory": ""}], "context": {}, "idempotency_key": "replay-after-change"
        }
        created = self.client.post("/v1/runs", json=payload)
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(self.client.delete(f"/v1/programs/{program['id']}").status_code, 200)
        replayed = self.client.post("/v1/runs", json=payload)
        self.assertEqual(replayed.status_code, 200, replayed.text)
        self.assertEqual(replayed.json()[0]["id"], created.json()[0]["id"])

        program = self.register()
        first = dict(payload, program_id=program["id"], idempotency_key="duplicate-first")
        second = dict(payload, program_id=program["id"], idempotency_key="duplicate-second")
        self.assertEqual(self.client.post("/v1/runs", json=first).status_code, 200)
        self.assertEqual(self.client.post("/v1/runs", json=second).status_code, 409)

    def test_batch_accepts_default_working_directory(self) -> None:
        program = self.register(arguments=["-c", "import sys; sys.exit(0)", "{input}"])
        response = self.client.post("/v1/runs", json={
            "program_id": program["id"], "mode": "BATCH", "items": [{"input_path": str(self.input_file), "working_directory": ""}], "context": {}, "idempotency_key": "batch-default-workdir"
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.executor.batches), 1)

    def test_batch_rejects_unused_inputs_before_creating_or_starting_runs(self) -> None:
        program = self.register()
        response = self.client.post("/v1/runs", json={
            "program_id": program["id"], "mode": "BATCH",
            "items": [{"input_path": str(self.input_file), "working_directory": ""}],
            "context": {}, "idempotency_key": "missing-input-mapping"
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("{input}", response.json()["detail"])
        self.assertEqual(self.executor.batches, [])
        self.assertEqual(self.app.state.storage.list_runs(), [])

    def test_restart_marks_running_run_interrupted(self) -> None:
        program = self.register()
        response = self.client.post("/v1/runs", json={
            "program_id": program["id"], "mode": "DIRECT", "items": [{"input_path": "", "working_directory": ""}], "context": {}, "idempotency_key": "restart"
        })
        run_id = response.json()[0]["id"]
        self.app.state.storage.mark_started(run_id)
        restarted = create_app(data_dir=self.data_dir, token="test-token", allowed_origins=["http://127.0.0.1:15173"], host_id="test-host", executor=RecordingExecutor())
        value = restarted.state.storage.get_run(run_id)
        self.assertIsNotNone(value)
        self.assertEqual(value.status, RunStatus.INTERRUPTED)

    def test_retry_uses_immutable_snapshot_after_registration_changes(self) -> None:
        program = self.register()
        source = self.client.post("/v1/runs", json={
            "program_id": program["id"], "mode": "DIRECT", "items": [{"input_path": "", "working_directory": ""}], "context": {"request_id": "retry-context"}, "idempotency_key": "retry-source"
        }).json()[0]
        self.app.state.storage.mark_started(source["id"])
        self.app.state.storage.mark_finished(source["id"], status=RunStatus.FAILED, exit_code=7)
        self.assertEqual(self.client.delete(f"/v1/programs/{program['id']}").status_code, 200)
        retry = self.client.post(f"/v1/runs/{source['id']}/retry", json={"idempotency_key": "retry-once"})
        self.assertEqual(retry.status_code, 200, retry.text)
        fresh = retry.json()[0]
        self.assertEqual(fresh["source_run_id"], source["id"])
        self.assertEqual(fresh["program_snapshot"]["executable_path"], source["program_snapshot"]["executable_path"])
        again = self.client.post(f"/v1/runs/{source['id']}/retry", json={"idempotency_key": "retry-once"})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()[0]["id"], fresh["id"])
        self.assertEqual(len(self.executor.direct), 2)

    def test_retry_refuses_active_duplicate_and_different_host_snapshot(self) -> None:
        program = self.register()
        source = self.client.post("/v1/runs", json={
            "program_id": program["id"], "mode": "DIRECT", "items": [{"input_path": "", "working_directory": ""}], "context": {}, "idempotency_key": "retry-duplicate-source"
        }).json()[0]
        self.app.state.storage.mark_started(source["id"])
        self.app.state.storage.mark_finished(source["id"], status=RunStatus.FAILED, exit_code=7)
        first = self.client.post(f"/v1/runs/{source['id']}/retry", json={"idempotency_key": "retry-duplicate-one"})
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(f"/v1/runs/{source['id']}/retry", json={"idempotency_key": "retry-duplicate-two"})
        self.assertEqual(second.status_code, 409)
        self.app.state.storage.transition_run(first.json()[0]["id"], RunStatus.FAILED, exit_code=1)
        with self.app.state.storage._connection() as connection:
            connection.execute("UPDATE runs SET program_snapshot_json=? WHERE id=?", ('{"id":"foreign","host_id":"other-host","executable_path":"x","arguments":[]}', source["id"]))
        self.assertEqual(self.client.post(f"/v1/runs/{source['id']}/retry", json={"idempotency_key": "retry-foreign"}).status_code, 409)

    def test_second_process_lock_does_not_trigger_recovery(self) -> None:
        lock = acquire_instance_lock(self.data_dir)
        try:
            running_app = create_app(data_dir=self.data_dir, token="test-token", allowed_origins=["http://127.0.0.1:15173"], host_id="test-host", executor=RecordingExecutor())
            program = running_app.state.storage.register_program(ProgramCandidate(
                name="HyperMesh", version="2025.1", keywords=["mesh"], executable_path=sys.executable, arguments=[]
            ))
            runs, _ = running_app.state.storage.create_runs(RunCreate(
                program_id=program.id, mode=RunMode.DIRECT, items=[RunItemInput()], idempotency_key="lock-test"
            ))
            running_app.state.storage.mark_started(runs[0].id)
            with patch("local_runner.__main__.uvicorn.run") as run_server:
                with self.assertRaises(SystemExit) as exit_result:
                    runner_main(["--standalone", "--data-dir", str(self.data_dir), "--port", "18766"])
            self.assertEqual(exit_result.exception.code, 1)
            run_server.assert_not_called()
            self.assertEqual(running_app.state.storage.get_run(runs[0].id).status, RunStatus.RUNNING)
        finally:
            lock.release()


class ManagedServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "managed-data"
        self.central = FakeCentral()
        self.executor = RecordingExecutor()
        self.app = create_app(data_dir=self.data_dir, server_url="http://127.0.0.1:8000", allowed_origins=["http://127.0.0.1:15173"], host_id="managed-host", executor=self.executor, central_client=self.central, confirm_pairing=lambda _: True)
        self.origin = {"Origin": "http://127.0.0.1:15173"}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def client(self, user_id: str) -> TestClient:
        return TestClient(self.app, base_url="http://127.0.0.1:8766", headers={**self.origin, "Authorization": f"Bearer binding-{user_id}.session"})

    def pair(self, token: str) -> dict:
        client = TestClient(self.app, base_url="http://127.0.0.1:8766", headers=self.origin)
        response = client.post("/v1/pair", json={"pairing_token": token})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def register(self, client: TestClient) -> dict:
        response = client.post("/v1/programs", json={"name": "Python", "version": "test", "keywords": [], "executable_path": sys.executable, "arguments": ["-c", "pass"]})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_accounts_are_isolated_and_actor_is_authoritative(self) -> None:
        self.assertEqual(self.client("user-a").get("/v1/identity").json()["managed"], True)
        self.pair("pair-a"); self.pair("pair-b")
        a, b = self.client("user-a"), self.client("user-b")
        program = self.register(a)
        self.assertEqual(b.get("/v1/programs").json(), [])
        created = a.post("/v1/runs", json={"program_id": program["id"], "mode": "DIRECT", "items": [{"input_path": "", "working_directory": ""}], "context": {"request_id": "r", "work_item_id": "w", "task_name": "forged", "actor": "mallory"}, "idempotency_key": "once"})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()[0]["context"]["actor"], "user-a")
        self.assertEqual(created.json()[0]["context"]["task_name"], "authoritative task")
        replay = a.post("/v1/runs", json={"program_id": program["id"], "mode": "DIRECT", "items": [{"input_path": "", "working_directory": ""}], "context": {"request_id": "r", "work_item_id": "w", "task_name": "forged", "actor": "mallory"}, "idempotency_key": "once"})
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(self.central.execute_calls, 1, "idempotent replay must not consume a second grant")
        self.assertEqual(a.get("/v1/health").json()["user_id"], "user-a")
        self.assertEqual(b.get("/v1/health").json()["user_id"], "user-b")

    def test_revoked_or_offline_authorization_fails_closed_and_events_recover(self) -> None:
        self.pair("pair-a"); client = self.client("user-a")
        program = self.register(client)
        self.central.offline_events = True
        created = client.post("/v1/runs", json={"program_id": program["id"], "mode": "DIRECT", "items": [{"input_path": "", "working_directory": ""}], "context": {}, "idempotency_key": "offline"})
        self.assertEqual(created.status_code, 200, created.text)
        runtime = self.app.state.runtimes["binding-user-a"]
        self.assertGreater(runtime.store.pending_count(), 0)
        # Restart reuses the binding store and reconciles the queued snapshot.
        restarted = create_app(data_dir=self.data_dir, server_url="http://127.0.0.1:8000", allowed_origins=["http://127.0.0.1:15173"], host_id="managed-host", central_client=self.central, confirm_pairing=lambda _: True)
        restarted_runtime = restarted.state.runtimes["binding-user-a"]
        self.assertGreater(restarted_runtime.store.pending_count(), 0)
        self.central.offline_events = False
        with TestClient(restarted, base_url="http://127.0.0.1:8766", headers={**self.origin, "Authorization": "Bearer binding-user-a.session"}):
            time.sleep(2.2)
        self.assertEqual(restarted_runtime.store.pending_count(), 0)
        self.central.denied = True
        denied = client.get("/v1/programs")
        self.assertEqual(denied.status_code, 403)

    def test_initial_grant_outbox_failure_rolls_back_run_and_idempotency(self) -> None:
        self.pair("pair-a"); client = self.client("user-a")
        program = self.register(client)
        runtime = self.app.state.runtimes["binding-user-a"]
        payload = RunCreate(program_id=program["id"], mode=RunMode.DIRECT, items=[RunItemInput()], idempotency_key="atomic-initial")
        with patch.object(runtime.store, "_persist_initial_runs", side_effect=RuntimeError("injected outbox failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                runtime.store.create_runs(payload, grant_id="grant-injected")
        self.assertEqual(runtime.store.list_runs(), [])
        self.assertIsNone(runtime.store.replay_runs(payload))

    def test_permanently_denied_event_sync_is_persistently_disabled(self) -> None:
        self.pair("pair-a"); client = self.client("user-a")
        program = self.register(client)
        self.central.events_denied = True
        created = client.post("/v1/runs", json={"program_id": program["id"], "mode": "DIRECT", "items": [{"input_path": "", "working_directory": ""}], "context": {}, "idempotency_key": "denied-events"})
        self.assertEqual(created.status_code, 200, created.text)
        runtime = self.app.state.runtimes["binding-user-a"]
        self.assertTrue(runtime.sync_disabled)
        calls_before_restart = self.central.event_calls
        restarted = create_app(data_dir=self.data_dir, server_url="http://127.0.0.1:8000", allowed_origins=["http://127.0.0.1:15173"], host_id="managed-host", central_client=self.central, confirm_pairing=lambda _: True)
        restarted_runtime = restarted.state.runtimes["binding-user-a"]
        self.assertTrue(restarted_runtime.sync_disabled)
        with TestClient(restarted, base_url="http://127.0.0.1:8766", headers={**self.origin, "Authorization": "Bearer binding-user-a.session"}):
            time.sleep(2.2)
        self.assertEqual(self.central.event_calls, calls_before_restart)
        self.assertGreater(restarted_runtime.store.pending_count(), 0)

    def test_large_event_queue_chunks_by_utf8_size_and_adapts_after_413(self) -> None:
        self.pair("pair-a")
        runtime = self.app.state.runtimes["binding-user-a"]
        # Force one adaptive 413 below the runner's normal 900 KiB budget.
        self.central.events_limit = 500 * 1024
        with runtime.store._connection() as conn:
            for index in range(100):
                run = Run(id=f"large-{index}", batch_id="bulk", mode=RunMode.BATCH, status=RunStatus.QUEUED, program_name="P", program_version="1", input_path="", working_directory="", created_at="2026-01-01T00:00:00+00:00", started_at=None, completed_at=None, exit_code=None, note=None, error=None, context={"task_name": "x" * 100}, program_snapshot={"blob": "x" * 16_000})
                conn.execute("INSERT INTO run_events(run_id, sequence, grant_id, run_json) VALUES (?, 1, ?, ?)", (run.id, "bulk-grant", json.dumps(run.model_dump(mode="json"))))
        with TestClient(self.app, base_url="http://127.0.0.1:8766", headers={**self.origin, "Authorization": "Bearer binding-user-a.session"}):
            time.sleep(2.5)
        self.assertEqual(runtime.store.pending_count(), 0)
        self.assertGreater(self.central.event_calls, 2, "413 should be followed by smaller chunks")
        self.assertTrue(any(size > self.central.events_limit for size in self.central.event_payload_sizes))
        self.assertEqual(len({event["run"]["id"] for event in self.central.events}), 100)

    def test_managed_device_catalog_and_retry_survive_hostname_change(self) -> None:
        data_dir = Path(self.temp.name) / "renamed-device"
        central = FakeCentral()
        app = create_app(data_dir=data_dir, server_url="http://127.0.0.1:8000", allowed_origins=["http://127.0.0.1:15173"], executor=RecordingExecutor(), central_client=central, confirm_pairing=lambda _: True)
        anonymous = TestClient(app, base_url="http://127.0.0.1:8766", headers=self.origin)
        self.assertEqual(anonymous.post("/v1/pair", json={"pairing_token": "pair-a"}).status_code, 200)
        client = TestClient(app, base_url="http://127.0.0.1:8766", headers={**self.origin, "Authorization": "Bearer binding-user-a.session"})
        program = self.register(client)
        source = client.post("/v1/runs", json={"program_id": program["id"], "mode": "DIRECT", "items": [{"input_path": "", "working_directory": ""}], "context": {}, "idempotency_key": "rename-source"}).json()[0]
        runtime = app.state.runtimes["binding-user-a"]
        runtime.store.mark_started(source["id"])
        runtime.store.mark_finished(source["id"], status=RunStatus.FAILED, exit_code=1)
        with patch("local_runner.server.socket.gethostname", return_value="RENAMED-PC"):
            restarted = create_app(data_dir=data_dir, server_url="http://127.0.0.1:8000", allowed_origins=["http://127.0.0.1:15173"], executor=RecordingExecutor(), central_client=central, confirm_pairing=lambda _: True)
        restarted_client = TestClient(restarted, base_url="http://127.0.0.1:8766", headers={**self.origin, "Authorization": "Bearer binding-user-a.session"})
        self.assertEqual(len(restarted_client.get("/v1/programs").json()), 1)
        self.assertEqual(restarted_client.post(f"/v1/runs/{source['id']}/retry", json={"idempotency_key": "rename-retry"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
