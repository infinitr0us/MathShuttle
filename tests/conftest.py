"""Shared test setup: the recall memory (what Copy for Word/PowerPoint copied) goes to a file of the test
session's own, never to the user's (%APPDATA%\\MathShuttle\\copied.json)."""
import os
import tempfile

_folder = tempfile.mkdtemp(prefix="mathshuttle-tests-")
os.environ["MATHSHUTTLE_RECALL_PATH"] = os.path.join(_folder, "copied.json")
