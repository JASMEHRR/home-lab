"""
Kept so the taskbar icon you pinned earlier ("Posture Tracker") still works: it opens the Posture Coach
app (app.py). The old command-line tracker became engine.py; test it with: py engine.py --seconds 20
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv[0] = os.path.join(HERE, "app.py")
runpy.run_path(sys.argv[0], run_name="__main__")
