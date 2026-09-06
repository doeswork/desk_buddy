"""Source-build entry point for the authorized helper; no GUI initialization."""
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from studio.services.network.broker.setup_helper import main
    if len(sys.argv) != 4 or sys.argv[1] != "--broker-setup-helper":
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[2], sys.argv[3]))
