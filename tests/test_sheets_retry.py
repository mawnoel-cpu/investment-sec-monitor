import unittest
from unittest.mock import Mock, patch
from gspread.exceptions import APIError
from ft_sheets_retry import FTSheetsHTTPClient, HTTPClient


def error(code):
    response=Mock();response.json.return_value={"error":{"code":code,"message":"test"}}
    return APIError(response)


class SheetsRetryTests(unittest.TestCase):
    def setUp(self):
        self.pacing=patch.object(FTSheetsHTTPClient,"_pace")
        self.pacing.start();self.addCleanup(self.pacing.stop)

    def test_quota_retry_recovers_without_changing_request(self):
        client=object.__new__(FTSheetsHTTPClient)
        with patch.object(HTTPClient,"request",side_effect=[error(429),"saved"]) as request,patch("ft_sheets_retry.time.sleep") as sleep:
            self.assertEqual(client.request("put","range",json={"values":[[1]]}),"saved")
        self.assertEqual(request.call_args_list[0],request.call_args_list[1])
        sleep.assert_called_once_with(15)

    def test_permanent_quota_exhaustion_is_bounded_and_visible(self):
        client=object.__new__(FTSheetsHTTPClient)
        with patch.object(HTTPClient,"request",side_effect=error(429)) as request,patch("ft_sheets_retry.time.sleep") as sleep:
            with self.assertRaises(APIError):client.request("get","range")
        self.assertEqual(request.call_count,4)
        self.assertEqual([x.args[0] for x in sleep.call_args_list],[15,30,45])

    def test_access_denied_is_not_retried(self):
        client=object.__new__(FTSheetsHTTPClient)
        with patch.object(HTTPClient,"request",side_effect=error(403)) as request,patch("ft_sheets_retry.time.sleep") as sleep:
            with self.assertRaises(APIError):client.request("get","range")
        self.assertEqual(request.call_count,1);sleep.assert_not_called()
