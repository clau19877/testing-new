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


if __name__ == "__main__":
    unittest.main()
