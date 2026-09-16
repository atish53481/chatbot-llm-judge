"""Test-session defaults shared by every test module."""
import os

# A local tool should not send DeepEval usage telemetry from test runs.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
