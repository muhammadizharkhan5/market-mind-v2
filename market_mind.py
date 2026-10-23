import MetaTrader5 as mt5

MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL = "XAUUSD"


def main():
    print("MARKET MIND V2 — CONNECTION TEST")
    print("-" * 40)

    if not mt5.initialize(path=MT5_PATH, timeout=60000):
        print("Connection failed:", mt5.last_error())
        return

    try:
        account = mt5.account_info()

        if account is None:
            print("No account information available.")
            print("Check your MT5 terminal login.")
            return

        print("Connection: SUCCESS")
        print("Login:", account.login)
        print("Server:", account.server)

        if not mt5.symbol_select(SYMBOL, True):
            print(f"Could not select {SYMBOL}.")
            print("Check the symbol name in Market Watch.")
            return

        tick = mt5.symbol_info_tick(SYMBOL)

        if tick is None:
            print("No tick available:", mt5.last_error())
            return

        print("\nREAL BROKER QUOTE")
        print("Symbol:", SYMBOL)
        print("Bid:", tick.bid)
        print("Ask:", tick.ask)
        print("Tick time (Unix):", tick.time)
        print("Tick time (milliseconds):", tick.time_msc)

        if tick.bid > 0 and tick.ask > 0:
            print("Quote check: PASSED")
        else:
            print("Quote check: INVALID — investigate the quote")

        print("\nConnection test finished.")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()