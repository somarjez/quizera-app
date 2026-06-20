import os
import sys

# Make the project root importable so `import quota_guard` etc. work under pytest.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
