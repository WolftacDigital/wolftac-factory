"""Portability smoke must exercise real entry points, with fixed outcomes."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from linux_host_smoke import smoke


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 required; dedicated CI smoke fails if absent")
class LinuxHostTests(unittest.TestCase):
    def test_entry_points_match_cross_platform_contract(self):
        with tempfile.TemporaryDirectory(prefix="wd634-", dir=os.getenv("TMPDIR")) as temp:
            observed = smoke(Path(temp))
        expected = json.loads((Path(__file__).parent / "fixtures/linux-host-expected.json").read_text())
        self.assertEqual(observed, expected)


if __name__ == "__main__":
    unittest.main()
