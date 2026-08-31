"""Lets the tests import the project's modules without any installation step."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
