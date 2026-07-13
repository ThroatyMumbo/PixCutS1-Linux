"""Make the repo-root modules (make_job, generate_cut) importable from the tests."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
