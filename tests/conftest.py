"""Adds each Lambda's flat deployment package directory to sys.path, mirroring
how it's laid out at runtime (Lambda imports `dynamodb_processor` etc. as a
top-level module, not a package)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _pkg_dir in ("dynamodb_handler", "sqs_processor"):
    path = os.path.join(_ROOT, _pkg_dir)
    if path not in sys.path:
        sys.path.insert(0, path)
