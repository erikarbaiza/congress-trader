"""
Creates a Windows Scheduled Task that runs main.py every weekday at 15:35
(Spain/CEST time = 09:35 ET = 5 minutes after US market open).

Run once as Administrator:
    python setup_scheduler.py
"""

import sys
import os
import subprocess
from pathlib import Path

TASK_NAME = "CongressTrader_DailyOpen"
# 15:35 Spain local time  =  09:35 ET  =  5 min after market open
RUN_TIME = "15:35"
DAYS = "MON,TUE,WED,THU,FRI"


def main() -> None:
    python_exe = sys.executable
    script = str(Path(__file__).parent / "main.py")
    working_dir = str(Path(__file__).parent)

    cmd = [
        "schtasks", "/create",
        "/tn", TASK_NAME,
        "/tr", f'"{python_exe}" "{script}"',
        "/sc", "weekly",
        "/d", DAYS,
        "/st", RUN_TIME,
        "/sd", "01/01/2026",
        "/ru", os.environ.get("USERNAME", ""),
        "/f",   # overwrite if exists
    ]

    print(f"Creating task '{TASK_NAME}'…")
    print(f"  Python: {python_exe}")
    print(f"  Script: {script}")
    print(f"  Schedule: {DAYS} at {RUN_TIME} (Spain local time)")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print("\nTask created successfully.")
        print("Verify with:  schtasks /query /tn", TASK_NAME)
        print("Run now:      schtasks /run /tn", TASK_NAME)
    else:
        print("\nFailed to create task:")
        print(result.stdout)
        print(result.stderr)
        print("\nTry running this script as Administrator.")


if __name__ == "__main__":
    main()
