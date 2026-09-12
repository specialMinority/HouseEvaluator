import json
import threading
import time
import unittest
from unittest.mock import patch

from backend.v2.personal_jobs import SearchBusy, SearchJobs


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.005)
    raise AssertionError("background search did not reach the expected state")


class PersonalJobsTest(unittest.TestCase):
    def jobs(self, function, **kwargs):
        jobs = SearchJobs(function, **kwargs)
        self.addCleanup(jobs.close)
        return jobs

    def gate(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        return gate

    def complete(self, jobs, job_id):
        return wait_until(lambda: (value if (value := jobs.get(job_id)) and value["status"] == "complete" else None))

    def test_start_returns_immediately_and_completion_preserves_result(self):
        gate, entered = self.gate(), threading.Event()
        def search(subject):
            entered.set()
            gate.wait(3)
            return {"listings": [{"rent_yen": subject["rent_yen"]}]}
        jobs = self.jobs(search)
        start = time.monotonic()
        created = jobs.start({"rent_yen": 85000})
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(created["status"], "pending")
        self.assertRegex(created["job_id"], r"^[A-Za-z0-9_-]{32}$")
        self.assertTrue(entered.wait(1))
        self.assertEqual(jobs.get(created["job_id"])["status"], "running")
        gate.set()
        self.assertEqual(self.complete(jobs, created["job_id"])["result"]["listings"][0]["rent_yen"], 85000)

    def test_input_and_each_returned_result_are_independent_copies(self):
        gate, entered = self.gate(), threading.Event()
        received = []
        def search(subject):
            received.append(subject)
            entered.set()
            gate.wait(3)
            subject["nested"]["value"] += 1
            return subject
        jobs = self.jobs(search)
        subject = {"nested": {"value": 8}}
        job_id = jobs.start(subject)["job_id"]
        self.assertTrue(entered.wait(1))
        subject["nested"]["value"] = 100
        gate.set()
        result = self.complete(jobs, job_id)
        self.assertEqual(result["result"]["nested"]["value"], 9)
        received[0]["nested"]["value"] = 200
        result["result"]["nested"]["value"] = 300
        self.assertEqual(jobs.get(job_id)["result"]["nested"]["value"], 9)
        self.assertEqual(subject["nested"]["value"], 100)

    def test_workers_are_bounded_with_no_hidden_queue(self):
        gate = self.gate()
        state_lock = threading.Lock()
        active, maximum, calls = [0], [0], []
        def search(subject):
            with state_lock:
                active[0] += 1
                maximum[0] = max(maximum[0], active[0])
                calls.append(subject["id"])
            gate.wait(3)
            with state_lock:
                active[0] -= 1
            return {"id": subject["id"]}
        jobs = self.jobs(search, max_workers=2)
        barrier = threading.Barrier(9)
        accepted, rejected = [], []
        def caller(number):
            barrier.wait()
            try:
                accepted.append(jobs.start({"id": number}))
            except SearchBusy:
                rejected.append(number)
        threads = [threading.Thread(target=caller, args=(i,), daemon=True) for i in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(1)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(accepted), 2)
        self.assertEqual(len(rejected), 6)
        wait_until(lambda: len(calls) == 2)
        self.assertEqual(maximum[0], 2)
        gate.set()
        for item in accepted:
            self.complete(jobs, item["job_id"])
        self.assertEqual(len(calls), 2)

    def test_cancellation_does_not_free_an_inflight_worker_or_publish_late_result(self):
        gate, entered, returned = self.gate(), threading.Event(), threading.Event()
        def search(subject):
            entered.set()
            gate.wait(3)
            returned.set()
            return {"secret": "should never be delivered"}
        jobs = self.jobs(search, max_workers=1)
        job_id = jobs.start({})["job_id"]
        self.assertTrue(entered.wait(1))
        self.assertTrue(jobs.cancel(job_id))
        self.assertFalse(jobs.cancel(job_id))
        self.assertEqual(jobs.get(job_id), {"job_id": job_id, "status": "cancelled"})
        with self.assertRaises(SearchBusy):
            jobs.start({})
        gate.set()
        self.assertTrue(returned.wait(1))
        wait_until(lambda: jobs._active == 0)
        self.assertNotIn("result", jobs.get(job_id))
        self.assertEqual(jobs.get(job_id)["status"], "cancelled")
        another = jobs.start({})["job_id"]
        self.complete(jobs, another)

    def test_expiration_keeps_active_slot_and_cannot_resurrect_a_result(self):
        tick, gate, entered = [10.0], self.gate(), threading.Event()
        def search(subject):
            entered.set()
            gate.wait(3)
            return {"value": 1}
        jobs = self.jobs(search, max_workers=1, ttl_seconds=10, clock=lambda: tick[0])
        job_id = jobs.start({})["job_id"]
        self.assertTrue(entered.wait(1))
        tick[0] = 20
        self.assertIsNone(jobs.get(job_id))
        self.assertFalse(jobs.cancel(job_id))
        with self.assertRaises(SearchBusy):
            jobs.start({})
        gate.set()
        wait_until(lambda: jobs._active == 0)
        self.assertIsNone(jobs.get(job_id))
        self.assertEqual(len(jobs._jobs), 0)

    def test_completed_jobs_expire_even_without_further_api_requests(self):
        jobs = self.jobs(lambda _: {"value": "temporary"}, ttl_seconds=0.08)
        job_id = jobs.start({})["job_id"]
        self.complete(jobs, job_id)
        wait_until(lambda: not jobs._jobs)
        wait_until(lambda: jobs._reaper is None)
        self.assertIsNone(jobs.get(job_id))
        new_id = jobs.start({})["job_id"]
        self.assertNotEqual(new_id, job_id)
        self.complete(jobs, new_id)

    def test_full_result_capacity_evicts_oldest_terminal_without_waiting_for_ttl(self):
        tick = [0.0]
        jobs = self.jobs(lambda _: {"value": 1}, max_workers=1, max_jobs=2,
                         ttl_seconds=10, clock=lambda: tick[0])
        first = jobs.start({})["job_id"]
        self.complete(jobs, first)
        wait_until(lambda: jobs._active == 0)
        old_record = jobs._jobs[first]
        tick[0] = 1
        second = jobs.start({})["job_id"]
        self.complete(jobs, second)
        wait_until(lambda: jobs._active == 0)
        tick[0] = 2
        self.assertIsNotNone(jobs.get(first))  # Reading does not renew retention.
        third = jobs.start({})["job_id"]
        self.complete(jobs, third)
        self.assertEqual(len(jobs._jobs), 2)
        self.assertIsNone(jobs.get(first))
        self.assertIsNone(old_record.result)
        self.assertIsNotNone(jobs.get(second))
        tick[0] = 12
        self.assertIsNone(jobs.get(second))
        self.assertIsNone(jobs.get(third))

    def test_default_capacity_accepts_more_than_32_completed_searches_in_one_ttl(self):
        jobs = self.jobs(lambda _: {"source_reports": [{"status": "unavailable"}]}, clock=lambda: 100.0)
        ids = []
        for _ in range(33):
            identity = jobs.start({})["job_id"]
            ids.append(identity)
            self.complete(jobs, identity)
            wait_until(lambda: jobs._active == 0)
        self.assertEqual(len(jobs._jobs), 32)
        self.assertIsNone(jobs.get(ids[0]))
        self.assertEqual(jobs.get(ids[-1])["status"], "complete")

    def test_failed_terminal_is_evicted_while_an_older_running_job_is_preserved(self):
        gate = self.gate()
        tick = [0.0]
        def search(payload):
            if payload.get("wait"):
                gate.wait(3)
            if payload.get("fail"):
                raise RuntimeError("private failure")
            return {"ok": True}
        jobs = self.jobs(search, max_workers=3, max_jobs=2, clock=lambda: tick[0])
        running = jobs.start({"wait": True})["job_id"]
        wait_until(lambda: jobs.get(running)["status"] == "running")
        tick[0] = 1
        failed = jobs.start({"fail": True})["job_id"]
        wait_until(lambda: jobs.get(failed)["status"] == "failed" and jobs._active == 1)
        tick[0] = 2
        replacement = jobs.start({})["job_id"]
        self.complete(jobs, replacement)
        self.assertIsNone(jobs.get(failed))
        self.assertEqual(jobs.get(running)["status"], "running")
        self.assertEqual(len(jobs._jobs), 2)
        gate.set()
        self.complete(jobs, running)

    def test_capacity_never_evicts_active_jobs_even_if_another_worker_is_available(self):
        gate = self.gate()
        jobs = self.jobs(lambda _: gate.wait(3), max_workers=3, max_jobs=2)
        ids = [jobs.start({})["job_id"] for _ in range(2)]
        with self.assertRaises(SearchBusy):
            jobs.start({})
        self.assertTrue(all(jobs.get(identity)["status"] in ("pending", "running") for identity in ids))
        self.assertEqual(jobs._active, 2)
        gate.set()

    def test_evicting_cancelled_record_does_not_release_its_inflight_worker(self):
        first_gate, second_gate = self.gate(), self.gate()
        def search(payload):
            (first_gate if payload["id"] == 1 else second_gate).wait(3)
            return {"id": payload["id"]}
        jobs = self.jobs(search, max_workers=2, max_jobs=1)
        first = jobs.start({"id": 1})["job_id"]
        wait_until(lambda: jobs.get(first)["status"] == "running")
        self.assertTrue(jobs.cancel(first))
        second = jobs.start({"id": 2})["job_id"]
        self.assertIsNone(jobs.get(first))
        self.assertEqual(jobs._active, 2)
        with self.assertRaises(SearchBusy):
            jobs.start({"id": 3})
        first_gate.set()
        wait_until(lambda: jobs._active == 1)
        self.assertIsNone(jobs.get(first))
        second_gate.set()
        self.assertEqual(self.complete(jobs, second)["result"], {"id": 2})

    def test_errors_are_generic_and_do_not_expose_transport_details(self):
        def fail(_):
            raise RuntimeError("https://secret.invalid/?api_key=hidden local-file-private-content")
        jobs = self.jobs(fail)
        job_id = jobs.start({"input_secret": "hidden"})["job_id"]
        failed = wait_until(lambda: (value if (value := jobs.get(job_id))["status"] == "failed" else None))
        self.assertEqual(failed["error"]["code"], "search_failed")
        self.assertEqual(failed["message"], failed["error"]["message"])
        output = json.dumps(failed)
        for marker in ("secret.invalid", "hidden", "private-content", "input_secret"):
            self.assertNotIn(marker, output)
        self.assertFalse(jobs.cancel(job_id))

    def test_unserializable_result_copy_failure_becomes_generic_failure(self):
        class BadCopy:
            def __deepcopy__(self, memo):
                raise RuntimeError("private object")
        jobs = self.jobs(lambda _: BadCopy())
        job_id = jobs.start({})["job_id"]
        failed = wait_until(lambda: (value if (value := jobs.get(job_id))["status"] == "failed" else None))
        self.assertNotIn("private object", json.dumps(failed))

    def test_close_is_nonblocking_clears_records_and_rejects_new_jobs(self):
        gate, entered = self.gate(), threading.Event()
        def search(_):
            entered.set()
            gate.wait(3)
            return {"discard": True}
        jobs = self.jobs(search)
        job_id = jobs.start({})["job_id"]
        self.assertTrue(entered.wait(1))
        start = time.monotonic()
        jobs.close()
        self.assertLess(time.monotonic() - start, 0.2)
        self.assertIsNone(jobs.get(job_id))
        self.assertFalse(jobs.cancel(job_id))
        with self.assertRaises(SearchBusy):
            jobs.start({})
        jobs.close()
        self.assertEqual(jobs._active, 1)
        gate.set()
        wait_until(lambda: jobs._active == 0)
        self.assertEqual(jobs._jobs, {})

    def test_unknown_or_malformed_ids_have_no_result(self):
        jobs = self.jobs(lambda _: {})
        for value in (None, 1, {}, [], "", "not-a-job", "../../private"):
            with self.subTest(value=value):
                self.assertIsNone(jobs.get(value))
                self.assertFalse(jobs.cancel(value))

    def test_daemon_workers_and_start_failure_do_not_leak_capacity(self):
        jobs = self.jobs(lambda _: {"daemon": threading.current_thread().daemon}, max_workers=1)
        original_start = threading.Thread.start
        def fail_search_worker(thread):
            if thread.name == "personal-search":
                raise RuntimeError("private startup details")
            return original_start(thread)
        with patch("backend.v2.personal_jobs.threading.Thread.start", fail_search_worker):
            with self.assertRaises(SearchBusy) as raised:
                jobs.start({})
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(jobs._active, 0)
        self.assertEqual(jobs._jobs, {})
        job_id = jobs.start({})["job_id"]
        self.assertTrue(self.complete(jobs, job_id)["result"]["daemon"])

    def test_settings_and_input_validation(self):
        for field, values in {
            "max_workers": (0, -1, True, 1.5, 17),
            "max_jobs": (0, -1, True, 2.5, 1025),
            "ttl_seconds": (0, -1, True, "10", float("inf"), float("nan"), 86401),
            "clock": (None,),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    SearchJobs(lambda _: {}, **{field: value})
        with self.assertRaises(ValueError):
            SearchJobs(None)
        jobs = self.jobs(lambda _: {})
        for value in (None, [], "hello", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                jobs.start(value)
        self.assertTrue(issubclass(SearchBusy, RuntimeError))
        self.assertFalse(issubclass(SearchBusy, ValueError))


if __name__ == "__main__":
    unittest.main()
