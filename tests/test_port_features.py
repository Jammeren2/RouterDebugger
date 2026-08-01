import unittest

from app.engine import parse_list
from app.registry import REGISTRY
from app.router_client import (
    RouterClient,
    RouterError,
    expand_port_expression,
    normalize_port_expression,
)


class PortExpressionTests(unittest.TestCase):
    def test_single_port_and_range(self):
        self.assertEqual(normalize_port_expression(8080), "8080")
        self.assertEqual(normalize_port_expression(" 8000 – 8010 "), "8000-8010")

    def test_port_triggering_list(self):
        self.assertEqual(
            normalize_port_expression("80, 443, 6970-6999", allow_list=True),
            "80,443,6970-6999",
        )
        self.assertEqual(
            expand_port_expression("80,443,6970-6972", allow_list=True),
            [80, 443, 6970, 6971, 6972],
        )

    def test_invalid_expressions(self):
        invalid = ("", "0", "65536", "9000-8000", "80,,443", "abc")
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_port_expression(value, allow_list=True)
        with self.assertRaises(ValueError):
            normalize_port_expression("1000-1001", allow_range=False)
        with self.assertRaises(ValueError):
            normalize_port_expression("1,2,3", allow_list=True, max_length=4)


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

    async def test_range_is_sent_as_individual_rules(self):
        client = RouterClient.__new__(RouterClient)
        calls = []

        async def fake_get(path, params=None):
            calls.append((path, params))
            return ""

        client._get = fake_get
        await client.vs_add("8000-8010", "9000-9010", "192.168.0.10", page=2)

        self.assertEqual(len(calls), 11)
        for offset, (path, params) in enumerate(calls):
            self.assertEqual(path, "/userRpm/VirtualServerRpm.htm")
            self.assertEqual(params["ExPort"], 8000 + offset)
            self.assertEqual(params["InPort"], 9000 + offset)
            self.assertEqual(params["Page"], 2)

    async def test_mismatched_ranges_are_rejected_before_router_request(self):
        client = RouterClient.__new__(RouterClient)
        calls = []

        async def fake_get(path, params=None):
            calls.append((path, params))
            return ""

        client._get = fake_get
        with self.assertRaises(ValueError):
            await client.vs_add("8000-8002", "9000-9001", "192.168.0.10")

        self.assertEqual(calls, [])


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


class PortTriggeringSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_add_state_is_loaded_before_exact_save_request(self):
        client = RouterClient.__new__(RouterClient)
        calls = []
        add_html = """
        <script>var specappEditInf = new Array(0,1,"",1,1,0,0,2,0,0);</script>
        """

        async def fake_get(path, params=None):
            calls.append((path, params))
            return add_html if len(calls) == 1 else ""

        client._get = fake_get
        await client.port_trigger_add(
            554, "6970", tr_protocol=2, in_protocol=3, state=1, page=2,
        )

        self.assertEqual(calls[0], (
            "/userRpm/SpecialAppRpm.htm", {"Add": "Add", "Page": 2},
        ))
        self.assertEqual(calls[1], (
            "/userRpm/SpecialAppRpm.htm",
            {
                "trPort": 554,
                "trProtocol": 2,
                "inPort": 6970,
                "inProtocol": 3,
                "State": 1,
                "Commonapp": 0,
                "Changed": 0,
                "SelIndex": 0,
                "Page": 2,
                "Save": "Save",
            },
        ))

    async def test_incoming_range_is_sent_as_individual_rules(self):
        client = RouterClient.__new__(RouterClient)
        calls = []
        add_html = """
        <script>var specappEditInf = new Array(0,1,"",1,1,0,0,1,0,0);</script>
        """

        async def fake_get(path, params=None):
            calls.append((path, params))
            return add_html if len(calls) % 2 == 1 else ""

        client._get = fake_get
        added = await client.port_trigger_add(554, "6970-6972")

        self.assertEqual(added, 3)
        self.assertEqual(len(calls), 6)
        self.assertEqual([calls[i][1]["inPort"] for i in (1, 3, 5)], [6970, 6971, 6972])

    async def test_save_is_not_sent_without_native_form_state(self):
        client = RouterClient.__new__(RouterClient)
        calls = []

        async def fake_get(path, params=None):
            calls.append((path, params))
            return "<html>unexpected response</html>"

        client._get = fake_get
        with self.assertRaises(RouterError):
            await client.port_trigger_add(554, "6970-6999")

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
