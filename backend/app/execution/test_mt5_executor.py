from backend.app.execution.mt5_executor import MT5Executor


def main():
    executor = MT5Executor(dry_run=True)

    try:
        executor.connect()

        order_request = executor.prepare_market_order(
            symbol="BTCUSD",
            side="buy",
            volume=0.01,
            stop_loss=None,
            take_profit=None,
        )

        result = executor.check_order(order_request)

        print("\nFinal retcode:", result.get("retcode"))
        print("Final comment:", result.get("comment"))

    except Exception as exc:
        print(f"\nTest failed: {exc}")

    finally:
        executor.disconnect()


if __name__ == "__main__":
    main()