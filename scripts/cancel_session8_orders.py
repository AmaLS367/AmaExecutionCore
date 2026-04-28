"""Cancel the two open buy orders from grid session 8."""
import importlib
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    load_dotenv()
    os.environ["BYBIT_TESTNET"] = "false"

    import backend.config as cfg_mod
    from backend.bybit_client.rest import BybitRESTClient

    importlib.reload(cfg_mod)

    client = BybitRESTClient()
    order_ids = ["2202471790406800896", "2202471792050968064"]
    for oid in order_ids:
        try:
            result = client.cancel_order(category="spot", symbol="XRPUSDT", order_id=oid)
            print(f"Cancelled {oid}: {result}")
        except Exception as exc:
            print(f"Failed to cancel {oid}: {exc}")


if __name__ == "__main__":
    main()
