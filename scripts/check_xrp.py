import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    load_dotenv()

    from backend.bybit_client.rest import BybitRESTClient

    client = BybitRESTClient()

    # Check all account types.
    for account_type in ["UNIFIED", "SPOT"]:
        try:
            data = client.get_wallet_balance(account_type=account_type)
            accounts = data.get("list", [])
            for account in accounts:
                for coin in account.get("coin", []):
                    if coin["coin"] == "XRP":
                        print(f"{account_type} XRP: {json.dumps(coin, indent=2)}")
        except Exception as exc:
            print(f"{account_type} error: {exc}")

    print("\n--- Open XRPUSDT orders ---")
    try:
        orders = client.get_open_orders(category="spot", symbol="XRPUSDT")
        print(f"Open orders: {len(orders)}")
        for order in orders:
            print(
                f"  {order.get('orderId')} side={order.get('side')} "
                f"qty={order.get('qty')} price={order.get('price')} "
                f"status={order.get('orderStatus')}",
            )
    except Exception as exc:
        print(f"Error: {exc}")

    print("\n--- Recent XRPUSDT executions ---")
    try:
        executions = client.get_executions(
            category="spot",
            symbol="XRPUSDT",
            limit=5,
        )
        for execution in executions:
            print(
                f"  side={execution.get('side')} qty={execution.get('execQty')} "
                f"price={execution.get('execPrice')} time={execution.get('execTime')}",
            )
    except Exception as exc:
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()
