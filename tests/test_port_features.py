import unittest

from app.engine import parse_list
from app.registry import REGISTRY
from app.router_client import RouterClient, normalize_port_expression


class PortExpressionTests(unittest.TestCase):
    def test_single_port_and_range(self):
        self.assertEqual(normalize_port_expression(8080), "8080")
        self.assertEqual(normalize_port_expression(" 8000 – 8010 "), "8000-8010")

    def test_port_triggering_list(self):
        self.assertEqual(
            normalize_port_expression("80, 443, 6970-6999", allow_list=True),
            "80,443,6970-6999",
        )

    def test_invalid_expressions(self):
        invalid = ("", "0", "65536", "9000-8000", "80,,443", "abc")
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_port_expression(value, allow_list=True)
        with self.assertRaises(ValueError):
            normalize_port_expression("1000-1001", allow_range=False)


class VirtualServerPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_metadata_ranges_and_global_ids_are_parsed(self):
        html = """
        <script>var virServerListPara = new Array(
          8000, 8010, 9000, 9010, "192.168.0.10", 2, 1, 0, 0
        );</script>
        <script>var virServerPara = new Array(2, 1, 1, 7, 8, 0, 0);</script>
        """
        client = RouterClient.__new__(RouterClient)
        calls = []

        async def fake_get(path, params=None):
            calls.append((path, params))
            return html

        client._get = fake_get
        result = await client.get_virtual_servers(2)

        self.assertEqual(calls, [("/userRpm/VirtualServerRpm.htm", {"Page": 2})])
        self.assertEqual(result["page"], 2)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["items"][0]["id"], 8)
        self.assertEqual(result["items"][0]["service_port"], "8000-8010")
        self.assertEqual(result["items"][0]["internal_port"], "9000-9010")

    async def test_range_is_sent_with_current_page(self):
        client = RouterClient.__new__(RouterClient)
        calls = []

        async def fake_get(path, params=None):
            calls.append((path, params))
            return ""

        client._get = fake_get
        await client.vs_add("8000-8010", "9000-9010", "192.168.0.10", page=2)

        path, params = calls[0]
        self.assertEqual(path, "/userRpm/VirtualServerRpm.htm")
        self.assertEqual(params["ExPort"], "8000-8010")
        self.assertEqual(params["InPort"], "9000-9010")
        self.assertEqual(params["Page"], 2)


class PortTriggeringPaginationTests(unittest.TestCase):
    def test_second_page_metadata_and_ids(self):
        html = """
        <script>var specAppList = new Array(
          3389, 1, "6970-6999", 2, 1, 0, 0
        );</script>
        <script>var specAppPara = new Array(2, 0, 1, 5, 8, 0, 0);</script>
        """
        result = parse_list(REGISTRY["SpecialAppRpm"], html, page=2)

        self.assertEqual(result["page"], 2)
        self.assertFalse(result["has_more"])
        self.assertEqual(result["rows"][0]["id"], 8)
        self.assertEqual(result["rows"][0]["cells"][2]["value"], "6970-6999")


if __name__ == "__main__":
    unittest.main()
