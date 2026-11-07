#!/usr/bin/env python3
"""ICLR benchmark entry for pinned HMA/RoutingBlocks author adaptations."""
from pathlib import Path
import sys

BRIDGE = Path(__file__).resolve().parents[5] / "Other_benchmark/benchmark_bridge"
sys.path.insert(0, str(BRIDGE))
from run import main

if __name__ == "__main__":
    main()
