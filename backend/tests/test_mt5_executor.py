from unittest.mock import MagicMock

import pytest

from backend.app.execution import mt5_executor


def test_get_open_positions_recovers_from_ipc_failure(monkeypatch):
    executor = mt5_executor.MT5Executor(dry_run=True)
    executor.connected = True

    positions = [
        MagicMock(
            ticket=123,
            symbol="EURUSD",
            type=1,
            volume=0.10,
            price_open=1.1000,
            sl=1.0950,
            tp=1.1100,
            profit=5.0,
            time=None,
        )
    ]

    mock_mt5 = MagicMock()
    mock_mt5.POSITION_TYPE_BUY = 1
    mock_mt5.POSITION_TYPE_SELL = 2
    mock_mt5.positions_get.side_effect = [None, positions]
    mock_mt5.last_error.side_effect = [
        (-10001, "IPC send failed"),
        (1, "Success"),
    ]
    mock_mt5.initialize.return_value = True
    mock_mt5.terminal_info.return_value = MagicMock()
    mock_mt5.account_info.return_value = MagicMock(
        login=123456,
        server="MetaQuotes-Demo",
    )

    monkeypatch.setattr(mt5_executor, "mt5", mock_mt5)

    result = executor.get_open_positions()

    assert len(result) == 1
    assert result[0]["ticket"] == 123
    assert result[0]["symbol"] == "EURUSD"

    mock_mt5.shutdown.assert_called_once()
    mock_mt5.initialize.assert_called_once()
    assert mock_mt5.positions_get.call_count == 2
    assert executor.connected is True


def test_get_open_positions_fails_closed_when_ipc_recovery_fails(monkeypatch):
    executor = mt5_executor.MT5Executor(dry_run=True)
    executor.connected = True

    mock_mt5 = MagicMock()
    mock_mt5.positions_get.return_value = None
    mock_mt5.last_error.return_value = (-10001, "IPC send failed")
    mock_mt5.initialize.return_value = False

    monkeypatch.setattr(mt5_executor, "mt5", mock_mt5)

    with pytest.raises(RuntimeError, match="MT5 positions_get failed"):
        executor.get_open_positions()

    mock_mt5.shutdown.assert_called_once()
    mock_mt5.initialize.assert_called_once()
    assert executor.connected is False