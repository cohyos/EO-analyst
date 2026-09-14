import os
import subprocess
import sys


def test_redirected_cp1252_streams_support_hebrew_after_logging_setup():
    env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
    code = r"""
import sys
import structlog
from eoa.orchestrator.main import configure_logging
configure_logging()
assert sys.stdout.encoding == "utf-8"
assert sys.stderr.encoding == "utf-8"
message = "\u05d3\u05d5\u05d7 \u05e2\u05d1\u05e8\u05d9\u05ea"
structlog.get_logger().warning("report_diagnostic", detail=message)
print(message, file=sys.stderr)
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    import json
    assert json.loads(result.stdout.decode("utf-8"))["detail"] == result.stderr.decode("utf-8").strip()
