import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    load_dotenv()

    from backend.bybit_client.rest import BybitRESTClient

    client = BybitRESTClient()
    data = client.get_wallet_balance(account_type="UNIFIED")
    accounts = data.get("list", [])
    for acc in accounts:
        for coin in acc.get("coin", []):
            wallet_balance = float(coin.get("walletBalance", 0))
            if wallet_balance > 0:
                print(
                    f"  {coin['coin']}: walletBalance={wallet_balance}, "
                    f"available={coin.get('availableToWithdraw')}",
                )


if __name__ == "__main__":
    main()
