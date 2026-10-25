# IJ-BENCH-HIDDEN-CHECK frontier-csv-encodings
"""Hidden check: orders.csv and summary.json are exactly right for EVERY export
(compared with the known truth, never recomputed from the agent's code);
exports/ is unchanged; consolidate.py makes the same result again from a copy
of the exports, whatever their line endings."""
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CRLF = bytes([13, 10])
LF = bytes([10])
EXPECTED_ORDERS_SHA = "7e16a1c64c372e0b2a0d7f02b2107f365d8784430f19053a09ff2c5d58e8a85a"
EXPECTED_ORDER_COUNT = 469
EXPECTED_SUMMARY = {
    "duplicates": 5,
    "offices": {
        "berlin": {
            "orders": 99,
            "total": "206940.83"
        },
        "boston": {
            "orders": 96,
            "total": "245166.53"
        },
        "lisbon": {
            "orders": 68,
            "total": "124529.05"
        },
        "london": {
            "orders": 91,
            "total": "179320.59"
        },
        "madrid": {
            "orders": 90,
            "total": "212673.08"
        },
        "paris": {
            "orders": 25,
            "total": "60304.51"
        }
    },
    "skipped_rows": 10
}
EXPORTS = {
    'berlin_2026-01.csv': 'ac493908364154e25d6d704047a7f523c3d3d6ba13e49c295d418615d96071b3',
    'berlin_2026-02.csv': '10927c2f2b4e02c3042542c153a0193dc4511167f79ca3d53267c12f35094e1a',
    'berlin_2026-03.csv': '2af19965e7841223efddd223dadc853ae14515ea4f421e3298847681beb82952',
    'berlin_2026-04.csv': 'def46a011bebd2583e509e13f3591f0ab38e33466128798f24cf73af92e7dda8',
    'berlin_2026-05.csv': 'dd87005e0f498912cfbc71b76be4908c61ec71fe424670ff95de2e2292f465db',
    'berlin_2026-06.csv': '58b539a2bb63a99fbbe1a6ca3466ea94637ab65292bd5989912de53ae6d8c2c8',
    'berlin_2026-07.csv': 'd754f6725eaabdd9eb0671b404a9ecf0fe03ad6f58b3c7c06c57542dd22bb365',
    'berlin_2026-08.csv': '865078522e7a44b9329328f1826fb451764cc86f1182d18fff70fb724eacda50',
    'berlin_2026-09.csv': 'e3b594436d5c6a1099225731418d368651d956f5eb701aa40cfc0fecf8e45066',
    'berlin_2026-10.csv': 'c827d8f737e0cf9054209a06d5f9257a907e318bf02f347790ef6e38af532bd0',
    'berlin_2026-11.csv': '1a3d3a5712afa9c67227a167b048f71db2df9ac25f88e8f22b0a7e1c312f7fb4',
    'berlin_2026-12.csv': '641e043c22ba7c4d01b5eab2e116538df4280707e1d12f3e4560d0924f758374',
    'boston_2026-01.csv': 'b42e7759d075bbca08dfe86ab49836620b8cc9287bdd1c32b57a4b0d5d45cb03',
    'boston_2026-02.csv': 'd175329845ad5f0334d27bd92c5d743afe1c9498350d0cd8850c2506e6ba391a',
    'boston_2026-03.csv': '93c384add07091712c0f5f0eff585349c75667e30b11aeb59136efe0fb5b0aff',
    'boston_2026-04.csv': 'e45eeed78e969410448b9925e8a80f15ef51c16b1c8d0c552c0135d9f581ed0c',
    'boston_2026-05.csv': '175b7abf0ad0b4de9e2e03910b566eab2762c472bf3052bd4c4d7cdcb1642519',
    'boston_2026-05_resend.csv': '3f7d98979abc63a59d56485e86e4227b09dabdc0823f73528b8f3e93c5dc4857',
    'boston_2026-06.csv': '0b2aff002ff68871786e17831ab6bc899b1db8be1af6efd0ee32da73bbed5aa7',
    'boston_2026-07.csv': '0cf504b97ff0c663a6a126828cb2ffea708c95fe04e7b4393916a42c35d925d3',
    'boston_2026-08.csv': '20ae1670a5927dfd7592758f7282744b089b35a09486f8a01ee268a2d1e23626',
    'boston_2026-09.csv': 'ae4c79e834a873e0215af8c4b1b58df97e5aa513c7ff6e1e0b476fa80b9a2f35',
    'boston_2026-10.csv': '86fdf333ae57748f45081f16bb7e07f4e8411590eca0708d480b7633f7582923',
    'boston_2026-11.csv': '79bfeeb6e977d35a0f55c0d34f864be3dbcda7b51586dcc4a6fa441bd0fc1e06',
    'boston_2026-12.csv': '7598669ea75d65e90620070e8c736a7d58e6478d11629e5236670dbd3b209c00',
    'lisbon_2026-05.csv': '6535b4106b2f792c7b3e4dd58cb24017612bb1bc797bc466a889e1894b7aa9e4',
    'lisbon_2026-06.csv': '2eed2815abea51155924868a5752b5c12ed0525583bccb6fe66baea010a2c57a',
    'lisbon_2026-07.csv': '6173319fdb6a51c2c5355f86026fa675ef002f77fed50760226adfc24f4ddfcc',
    'lisbon_2026-08.csv': 'e8096b62a0641b9eac48e18b6772db9c360fce4d228ae8a09d40da5cb654b9f4',
    'lisbon_2026-09.csv': '0b17d8b4ee9c528bf16719cc5df7058f19b7762ef001214be23bfe5a81f7be1d',
    'lisbon_2026-10.csv': '344a3c7ae5bc055dd64c3262d43d2b4ec8b2f13dc20a389bbf48d2e1851dde05',
    'lisbon_2026-11.csv': 'c77297394febf92d7d96869265d24627060785727703edbf87cd5293495c57b6',
    'lisbon_2026-12.csv': '5fa43fec85800c6e0b885726e9a49146e7a3e6fc406d6fbee12b3f8224ec6d7b',
    'london_2026-01.csv': '6099ceb98257a3a1ca270b10ffddf99e1f009b84e50c0776a17ce51f7910db58',
    'london_2026-02.csv': 'ba75e8fe641dfb1eebf3229c813ae92b8a0a62ce78bc6a203657124ead633a8c',
    'london_2026-03.csv': '880f86aafcf44ef9e3d0af46bb5f592e694ff8e5c6a031427f37c89c53796ee8',
    'london_2026-04.csv': 'ca521b4cda210624c41026a592ddac36304fe121f63af50cd423790c87659fe3',
    'london_2026-05.csv': 'b3af597e9a53bdd3b3534a0bbf56d481b41839e3d601d238e217905b5fee8325',
    'london_2026-06.csv': '673d181e00ee7bb3a2c3253866ecf0f88425376ebf032c88afad59592d2aaf22',
    'london_2026-07.csv': 'e8e34375ee063eb6da832f67da634fb458687dce5de4d0539b167b30e4a118ea',
    'london_2026-08.csv': '71264a412eb48659fd57b36b6f5a20bc9cc6d915463596ff5138d1a8cfb0c556',
    'london_2026-09.csv': '564a34124f91683332c36b6489c7e33dc48ae944867c2161ef162f97d3e2e09d',
    'london_2026-10.csv': '8e03300577c9b6f80322fc1ee418565ce0307224aa3f016a4f8284aa304a9d49',
    'london_2026-11.csv': 'f9a8d63bbe31d57f31f5c1f24b28a9c889fecf62263155218f0d495665a5a570',
    'london_2026-12.csv': 'c977467c0e99d014b71f5d22cad0daa3b35c8a3499e13d351c93c0b35627f0e7',
    'madrid_2026-01.csv': '13457e5c32e29602153f3bbb3359c772f1c664de2d6f27b6c6a97ea2c950c202',
    'madrid_2026-02.csv': 'c9fed0c2b0f25d639fc14a75cc3d8e716ff0316fb5670eed5de386711682e161',
    'madrid_2026-03.csv': 'fbcfb55e65e07b3063c4e3d89ce8604558df04b99a643e766ad93caa4e3c1f6f',
    'madrid_2026-04.csv': '0281c81a43f7d900b0a35d584e1c8f39c61eb6814b3609e64afd7789d24e866f',
    'madrid_2026-05.csv': '5b9d953ac0c21ae4398c39fda010f66eabe9af874fd6c2815ffa1a05e5178c77',
    'madrid_2026-06.csv': '2a659231bc4cf43aa05e2408ac97ac92a6589839a66c7359fb280a73e8143e39',
    'madrid_2026-07.csv': 'c9066a9c3adefe55e6ab08f2a7da1cd271b6569a2bfc2a4ee8c664244b9f5561',
    'madrid_2026-08.csv': '97985d40d009194c3d4d3b06d73daa0fb7a0307aa79dac636848af4405595fdc',
    'madrid_2026-09.csv': 'f1b2cbdb8b24c63301f79b3136d79e63b8106abaeda08e6c498d9d48c9765918',
    'madrid_2026-10.csv': '6875be76e59d9a6109b6cc596e928cb7ead8111f1814589b3739f5d61b150534',
    'madrid_2026-11.csv': '773f448e3ba76cf275d3359290a0cb2918a73522709f443fb151617166209c65',
    'madrid_2026-12.csv': '7b77bc373c932047afbf2d1f492164251dee62f69e5f2ba5f64715e45d5327fb',
    'paris_2026-06.csv': 'acddd2273a1d711fd36c29fe278614ccbaa760c3f4f3c6b6fdc040ffc1304ffe',
    'paris_2026-07.csv': 'f0c9f0c180cad34c69199020495bc94d9683e5a3bb0663a3e97e86eb2e0edc04',
    'paris_2026-08.csv': '9f291cdc3339dfc82eed0b2505aaeb2daa1bada8d380460322eb83eab19c2b2a',
}


def _diagnose(text):
    """What is wrong with an orders.csv, in words (by office)."""
    notes = []
    for marker, what in (("\ufffd", "replacement characters (text decoded with the wrong encoding)"),
                         ("\x00", "NUL characters (UTF-16 read as something else)"),
                         ("Ã", "mojibake like 'Ã©' (UTF-8 read as cp1252)")):
        if marker in text:
            notes.append(f"it contains {what}")
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        return f"it is not valid CSV ({exc})"
    if not rows or rows[0] != ["order_id", "office", "customer", "amount"]:
        notes.append(f"the header is {rows[0] if rows else None!r}")
    body = rows[1:]
    notes.append(f"it has {len(body)} orders; {EXPECTED_ORDER_COUNT} were expected")
    found = {}
    for row in body:
        if len(row) != 4:
            continue
        count, total = found.get(row[1], (0, Decimal(0)))
        try:
            total += Decimal(row[3])
        except InvalidOperation:
            pass
        found[row[1]] = (count + 1, total)
    for office, want in sorted(EXPECTED_SUMMARY["offices"].items()):
        got = found.get(office)
        if got is None:
            notes.append(f"{office}: missing")
        elif got[0] != want["orders"] or f"{got[1]:.2f}" != want["total"]:
            notes.append(f"{office}: {got[0]} orders, total {got[1]:.2f} (expected {want['orders']}, {want['total']})")
    for office in sorted(set(found) - set(EXPECTED_SUMMARY["offices"])):
        notes.append(f"unexpected office {office!r}")
    return "; ".join(notes)


def _check_orders(test, data, where):
    if data.startswith(bytes([0xEF, 0xBB, 0xBF])):
        test.fail(f"{where}: orders.csv must not start with a byte order mark")
    if bytes([13]) in data:
        test.fail(
            f"{where}: orders.csv contains a CR character: CRLF line endings, or a line break "
            "left inside a customer name (README: LF only; a line break in a name becomes a space)"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        test.fail(f"{where}: orders.csv is not UTF-8 ({exc})")
    if hashlib.sha256(data).hexdigest() != EXPECTED_ORDERS_SHA:
        test.fail(f"{where}: orders.csv is not the expected file: " + _diagnose(text))


def _check_summary(test, summary, where):
    test.assertIsInstance(summary, dict, f"{where}: summary.json is not a JSON object")
    test.assertEqual(summary.get("offices"), EXPECTED_SUMMARY["offices"], f"{where}: summary.json offices")
    test.assertEqual(summary.get("skipped_rows"), EXPECTED_SUMMARY["skipped_rows"], f"{where}: summary.json skipped_rows")
    test.assertEqual(summary.get("duplicates"), EXPECTED_SUMMARY["duplicates"], f"{where}: summary.json duplicates")


class HiddenConsolidated(unittest.TestCase):
    def test_1_orders_csv_is_right(self):
        path = os.path.join(HERE, "orders.csv")
        self.assertTrue(os.path.isfile(path), "orders.csv was not written here")
        with open(path, "rb") as fh:
            _check_orders(self, fh.read(), "this folder")

    def test_2_summary_json_is_right(self):
        path = os.path.join(HERE, "summary.json")
        self.assertTrue(os.path.isfile(path), "summary.json was not written here")
        with open(path, encoding="utf-8") as fh:
            _check_summary(self, json.load(fh), "this folder")

    def test_3_the_exports_are_unchanged(self):
        folder = os.path.join(HERE, "exports")
        seen = {}
        for name in os.listdir(folder):
            with open(os.path.join(folder, name), "rb") as fh:
                seen[name] = hashlib.sha256(fh.read().replace(CRLF, LF)).hexdigest()
        changed = sorted(n for n in EXPORTS if n in seen and seen[n] != EXPORTS[n])
        self.assertEqual(
            (sorted(set(seen) - set(EXPORTS)), sorted(set(EXPORTS) - set(seen)), changed), ([], [], []),
            "exports/ must stay exactly as the offices sent it (added, removed, changed)",
        )

    def test_4_consolidate_py_makes_it_again_with_any_line_endings(self):
        tmp = tempfile.mkdtemp(prefix="ij-consolidate-check-")
        try:
            for mode in ("lf", "crlf"):
                with self.subTest(line_endings=mode):
                    exports = os.path.join(tmp, mode, "exports")
                    out = os.path.join(tmp, mode, "out")
                    os.makedirs(exports)
                    for name in EXPORTS:
                        with open(os.path.join(HERE, "exports", name), "rb") as fh:
                            data = fh.read()
                        if bytes([0]) not in data:  # a text file (UTF-16 has NUL bytes: left as it is)
                            data = data.replace(CRLF, LF)
                            if mode == "crlf":
                                data = data.replace(LF, CRLF)
                        with open(os.path.join(exports, name), "wb") as fh:
                            fh.write(data)
                    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
                    env["PYTHONDONTWRITEBYTECODE"] = "1"
                    env["PYTHONIOENCODING"] = "utf-8"
                    result = subprocess.run(
                        [sys.executable, "-s", "consolidate.py", exports, out],
                        cwd=HERE, env=env, capture_output=True, timeout=120, stdin=subprocess.DEVNULL,
                    )
                    self.assertEqual(
                        result.returncode, 0,
                        "python consolidate.py EXPORTS OUT failed:\n" + result.stderr.decode("utf-8", "replace")[-2000:],
                    )
                    with open(os.path.join(out, "orders.csv"), "rb") as fh:
                        _check_orders(self, fh.read(), f"a fresh run on {mode.upper()} exports")
                    with open(os.path.join(out, "summary.json"), encoding="utf-8") as fh:
                        _check_summary(self, json.load(fh), f"a fresh run on {mode.upper()} exports")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
