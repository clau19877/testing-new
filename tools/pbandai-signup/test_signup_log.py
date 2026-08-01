#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

import signup_log


class SignupLogTests(unittest.TestCase):
    def test_error_writes_jsonl_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            signup_log.log_error(
                "boom",
                log_dir=log_dir,
                source="unit",
                step="email_submit",
                email="a@icloud.com",
                extra={"code": 1},
            )
            errors = (log_dir / "errors.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(errors), 1)
            payload = json.loads(errors[0])
            self.assertEqual(payload["level"], "ERROR")
            self.assertEqual(payload["step"], "email_submit")
            self.assertIn("boom", (log_dir / "signup.log").read_text(encoding="utf-8"))

    def test_info_does_not_enter_errors_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            signup_log.log_info("hello", log_dir=log_dir, step="start")
            self.assertFalse((log_dir / "errors.jsonl").exists())
            self.assertTrue((log_dir / "signup.log").exists())

    def test_debug_goes_to_trace_log_not_signup_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            signup_log.log_debug("quiet detail", log_dir=log_dir, step="trace_step")
            self.assertIn("quiet detail", (log_dir / "trace.log").read_text(encoding="utf-8"))
            self.assertFalse((log_dir / "signup.log").exists())

    def test_everything_ends_up_in_trace_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            signup_log.log_debug("d1", log_dir=log_dir)
            signup_log.log_info("i1", log_dir=log_dir)
            signup_log.log_error("e1", log_dir=log_dir)
            trace = (log_dir / "trace.log").read_text(encoding="utf-8")
            self.assertIn("d1", trace)
            self.assertIn("i1", trace)
            self.assertIn("e1", trace)

    def test_redact_masks_sensitive_flags(self) -> None:
        cmd = "python3 csv_queue.py failed --email a@b.com --password sekret123 --reason boom"
        redacted = signup_log.redact(cmd)
        self.assertNotIn("sekret123", redacted)
        self.assertIn("--password [REDACTED]", redacted)
        self.assertIn("a@b.com", redacted)

    def test_redact_masks_api_key(self) -> None:
        cmd = "grizzly_sms.py --api-key abc123def --action rent"
        redacted = signup_log.redact(cmd)
        self.assertNotIn("abc123def", redacted)

    def test_truncate_long_text(self) -> None:
        long_text = "x" * 5000
        out = signup_log.truncate(long_text, limit=100)
        self.assertLessEqual(len(out), 150)
        self.assertIn("truncated", out)

    def test_truncate_short_text_unchanged(self) -> None:
        self.assertEqual(signup_log.truncate("short", limit=100), "short")

    def test_log_io_redacts_and_truncates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            signup_log.log_io(
                "OUT",
                "cmd --password topsecret",
                redact_message=True,
                log_dir=log_dir,
                step="shell_call",
            )
            trace = (log_dir / "trace.log").read_text(encoding="utf-8")
            self.assertNotIn("topsecret", trace)
            self.assertIn("[REDACTED]", trace)


if __name__ == "__main__":
    unittest.main()
