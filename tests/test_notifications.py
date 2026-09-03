import unittest
from types import SimpleNamespace
from unittest.mock import patch

import _stubs  # noqa: F401
import notifications


class NotificationTests(unittest.TestCase):
    def test_default_personal_mode_sends_once(self):
        response = SimpleNamespace(status_code=200, text="ok")
        with patch.object(notifications.requests, "post", return_value=response, create=True) as post:
            result = notifications.send_line_notifications(
                "token", "user-id", "group-id", None, "message"
            )
        self.assertEqual(result, {"personal": True})
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["to"], "user-id")

    def test_group_mode_sends_only_group(self):
        response = SimpleNamespace(status_code=200, text="ok")
        with patch.object(notifications.requests, "post", return_value=response, create=True) as post:
            result = notifications.send_line_notifications(
                "token", "user-id", "group-id", "group", "message"
            )
        self.assertEqual(result, {"group": True})
        self.assertEqual(post.call_args.kwargs["json"]["to"], "group-id")

    def test_both_continues_after_personal_failure(self):
        response = SimpleNamespace(status_code=200, text="ok")
        with patch.object(
            notifications.requests,
            "post",
            side_effect=[RuntimeError("personal failed"), response],
            create=True,
        ) as post:
            result = notifications.send_line_notifications(
                "token", "user-id", "group-id", "both", "message"
            )
        self.assertEqual(result, {"personal": False, "group": True})
        self.assertEqual(post.call_count, 2)

    def test_invalid_mode_falls_back_to_personal(self):
        response = SimpleNamespace(status_code=200, text="ok")
        with patch.object(notifications.requests, "post", return_value=response, create=True) as post:
            notifications.send_line_notifications(
                "token", "user-id", "group-id", "invalid", "message"
            )
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["to"], "user-id")


if __name__ == "__main__":
    unittest.main()
