#!/usr/bin/env python3
import csv
import tempfile
import unittest
from pathlib import Path

import csv_queue


class CsvQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.task = self.base / "task.csv"
        self.success = self.base / "success.csv"
        self.failed = self.base / "failed.csv"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_task(self, rows: list[dict[str, str]]) -> None:
        with self.task.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=csv_queue.TASK_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in csv_queue.TASK_FIELDS})

    def test_next_and_success_removes_row(self) -> None:
        self.write_task(
            [
                {"email": "a@icloud.com", "password": "PassA!234"},
                {"email": "b@icloud.com", "password": "PassB!234"},
            ]
        )
        rc = csv_queue.main(["--dir", str(self.base), "next"])
        self.assertEqual(rc, 0)

        rc = csv_queue.main(["--dir", str(self.base), "success", "--email", "a@icloud.com"])
        self.assertEqual(rc, 0)

        _, task_rows = csv_queue.read_rows(self.task)
        self.assertEqual(len(task_rows), 1)
        self.assertEqual(task_rows[0]["email"], "b@icloud.com")

        _, success_rows = csv_queue.read_rows(self.success)
        self.assertEqual(len(success_rows), 1)
        self.assertEqual(success_rows[0]["email"], "a@icloud.com")
        self.assertTrue(success_rows[0].get("created_at"))

    def test_failed_removes_and_records_reason(self) -> None:
        self.write_task([{"email": "x@icloud.com", "password": "PassX!234"}])
        rc = csv_queue.main(
            [
                "--dir",
                str(self.base),
                "failed",
                "--email",
                "x@icloud.com",
                "--reason",
                "timeout waiting for code",
            ]
        )
        self.assertEqual(rc, 0)
        _, task_rows = csv_queue.read_rows(self.task)
        self.assertEqual(task_rows, [])
        _, failed_rows = csv_queue.read_rows(self.failed)
        self.assertEqual(failed_rows[0]["reason"], "timeout waiting for code")

    def test_next_resolves_random_fields_and_persists_them(self) -> None:
        self.write_task(
            [
                {
                    "email": "r@icloud.com",
                    "password": "random",
                    "first_name": "random",
                    "last_name": "random",
                    "gender": "random",
                    "month": "random",
                    "day": "random",
                    "year": "random",
                }
            ]
        )
        rc = csv_queue.main(["--dir", str(self.base), "next"])
        self.assertEqual(rc, 0)

        _, task_rows = csv_queue.read_rows(self.task)
        row = task_rows[0]
        self.assertNotEqual(row["password"].lower(), "random")
        self.assertNotEqual(row["first_name"].lower(), "random")
        self.assertNotEqual(row["gender"].lower(), "random")
        self.assertIn(row["gender"], ["Male", "Female", "NotApplicable", "NotSelected"])
        self.assertTrue(1 <= int(row["month"]) <= 12)
        self.assertTrue(row["day"].isdigit())
        self.assertTrue(row["year"].isdigit())

        # A second success/failed round-trip should record the resolved
        # values, not the literal word "random".
        rc = csv_queue.main(["--dir", str(self.base), "success", "--email", "r@icloud.com"])
        self.assertEqual(rc, 0)
        _, success_rows = csv_queue.read_rows(self.success)
        self.assertNotEqual(success_rows[0]["first_name"].lower(), "random")

    def test_next_email_random_without_template_errors(self) -> None:
        self.write_task([{"email": "random", "password": "random"}])
        rc = csv_queue.main(["--dir", str(self.base), "next"])
        self.assertEqual(rc, 2)

    def test_next_email_random_with_template_resolves(self) -> None:
        (self.base / "config.json").write_text(
            '{"random_data": {"email_template": "tester+{token}@example.com"}}',
            encoding="utf-8",
        )
        self.write_task([{"email": "random", "password": "random"}])
        rc = csv_queue.main(["--dir", str(self.base), "next"])
        self.assertEqual(rc, 0)
        _, task_rows = csv_queue.read_rows(self.task)
        email = task_rows[0]["email"]
        self.assertTrue(email.startswith("tester+"))
        self.assertTrue(email.endswith("@example.com"))


if __name__ == "__main__":
    unittest.main()
