"""One-shot script: sell all available XRP at market price using mainnet."""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

# Force mainnet for this script
os.environ["BYBIT_TESTNET"] = "false"

def main() -> None:
    import backend.config as cfg_mod
    from backend.bybit_client.rest import BybitRESTClient

    importlib.reload(cfg_mod)

    client = BybitRESTClient()

    # Verify we're on mainnet
    from backend.config import settings

    print(f"Testnet: {settings.bybit_testnet}")
    print(f"API key: {settings.active_api_key[:6]}...")

    # Check current XRP balance
    balance_data = client.get_wallet_balance(account_type="UNIFIED")
    coins = balance_data.get("list", [{}])[0].get("coin", [])
    xrp_info = next((c for c in coins if c.get("coin") == "XRP"), None)

    if xrp_info is None:
        print("No XRP found in wallet.")
        all_coins = [
            (coin["coin"], coin.get("walletBalance"))
            for coin in coins
            if float(coin.get("walletBalance", 0)) > 0
        ]
        print(f"Wallet contents: {all_coins}")
        return

    available_qty = float(
        xrp_info.get("availableToWithdraw") or xrp_info.get("walletBalance") or 0,
    )
    print(f"XRP wallet balance: {xrp_info.get('walletBalance')}")
    print(f"XRP available: {available_qty}")

    if available_qty < 1:
        print("Less than 1 XRP available — nothing to sell.")
        return

    # Round down to 2 decimal places (Bybit XRPUSDT min qty step = 0.01)
    qty_str = f"{int(available_qty * 100) / 100:.2f}"
    print(f"Placing market SELL for {qty_str} XRPUSDT...")

    result = client.place_order(
        category="spot",
        symbol="XRPUSDT",
        side="Sell",
        order_type="Market",
        qty=qty_str,
        market_unit="baseCoin",
    )
    print(f"Order placed successfully: orderId={result.get('orderId')}")


if __name__ == "__main__":
    main()
