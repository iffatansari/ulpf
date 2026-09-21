import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Make the repo's top-level packages importable in tests, matching the
# container layout (PYTHONPATH=/app and WORKDIR=/app/orchestrator).
for path in (
    ROOT,
    os.path.join(ROOT, "orchestrator"),
    os.path.join(ROOT, "api"),
):
    if path not in sys.path:
        sys.path.insert(0, path)