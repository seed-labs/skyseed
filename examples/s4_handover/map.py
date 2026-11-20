"""Build the S4 adaptation of SEED InternetMap2 once, after simu.py."""
from pathlib import Path

from experiment.internet_map.build import build


if __name__ == "__main__":
    build(Path(__file__).parent / "output")
    print("InternetMap2 ready; python run.py starts the network and map together.")
