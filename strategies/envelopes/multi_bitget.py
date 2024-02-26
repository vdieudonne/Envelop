import datetime
import sys

import asyncio
import ta

#sys.path.append("./Live-Tools-V2")
from secret import ACCOUNTS
from bitget_perp import PerpBitget

import coin_params
import importlib

importlib.reload(coin_params)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def main():
    account = ACCOUNTS["bitget1"]

    margin_mode = "isolated"  # isolated or crossed
    exchange_leverage = 1

    tf = "1h"
    size_leverage = 1
    sl = 0.3
    extra_gain = 0.011 # 1.1% au dessus de la MA

    use_long = True
    use_short = False

    params = coin_params.balanced_15m

    exchange = PerpBitget(
        public_api=account["public_api"],
        secret_api=account["secret_api"],
        password=account["password"],
    )
    invert_side = {"long": "sell", "short": "buy"}
    print(f"--- Execution started at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    try:
        await exchange.load_markets()

        for pair in params.copy():
            info = exchange.get_pair_info(pair)
            if info is None:
                print(f"Pair {pair} not found, removing from params...")
                del params[pair]

        pairs = list(params.keys())

        # Set margin mode and leverge on each perpetual pairs
        try:
            print(f"Setting {margin_mode} x {exchange_leverage} on {len(pairs)} pairs...")
            tasks = [
                exchange.set_margin_mode_and_leverage(
                    pair, margin_mode, exchange_leverage
                )
                for pair in pairs
            ]
            await asyncio.gather(*tasks)  # set leverage and margin mode for all pairs
        except Exception as e:
            print(e)

        print(f"Getting data and indicators on {len(pairs)} pairs...")
        tasks = [exchange.get_last_ohlcv(pair, tf, 50) for pair in pairs]
        dfs = await asyncio.gather(*tasks)
        df_list = dict(zip(pairs, dfs))

        for pair in df_list:
            current_params = params[pair]
            df = df_list[pair]
            if current_params["src"] == "close":
                src = df["close"]
            elif current_params["src"] == "ohlc4":
                src = (df["close"] + df["high"] + df["low"] + df["open"]) / 4

            df["ma_base"] = ta.trend.sma_indicator(
                close=src, window=current_params["ma_base_window"]
            )
            high_envelopes = [
                round(1 / (1 - e) - 1, 3) for e in current_params["envelopes"]
            ]
            for i in range(1, len(current_params["envelopes"]) + 1):
                if(use_short):
                    df[f"ma_high_{i}"] = df["ma_base"] * (1 + high_envelopes[i - 1])
                if(use_long):
                    df[f"ma_low_{i}"] = df["ma_base"] * (
                        1 - current_params["envelopes"][i - 1]
                    )

            df_list[pair] = df

        
        # Get portfolio
        usdt_balance = await exchange.get_balance()
        usdt_balance = 100 #usdt_balance.free
        print(f"Balance: {round(usdt_balance, 2)} USDT")

        tasks = [exchange.get_open_trigger_orders(pair) for pair in pairs]
        print(f"Getting open trigger orders...")
        trigger_orders = await asyncio.gather(*tasks)
        trigger_order_list = dict(
            zip(pairs, trigger_orders)
        )  # Get all open trigger orders by pair

        tasks = []
        for pair in df_list:
            params[pair]["canceled_orders_buy"] = len(
                [
                    order
                    for order in trigger_order_list[pair]
                    if (order.side == "buy" and order.reduce is False)
                ]
            )
            params[pair]["canceled_orders_sell"] = len(
                [
                    order
                    for order in trigger_order_list[pair]
                    if (order.side == "sell" and order.reduce is False)
                ]
            )
            tasks.append(
                exchange.cancel_trigger_orders(
                    pair, [order.id for order in trigger_order_list[pair]]
                )
            )
        print(f"Canceling trigger orders...")
        await asyncio.gather(*tasks)  # Cancel all trigger orders

        tasks = [exchange.get_open_orders(pair) for pair in pairs]
        print(f"Getting open orders...")
        orders = await asyncio.gather(*tasks)
        order_list = dict(zip(pairs, orders))  # Get all open orders by pair

        tasks = []
        for pair in df_list:
            params[pair]["canceled_orders_buy"] = params[pair][
                "canceled_orders_buy"
            ] + len(
                [
                    order
                    for order in order_list[pair]
                    if (order.side == "buy" and order.reduce is False)
                ]
            )
            params[pair]["canceled_orders_sell"] = params[pair][
                "canceled_orders_sell"
            ] + len(
                [
                    order
                    for order in order_list[pair]
                    if (order.side == "sell" and order.reduce is False)
                ]
            )
            tasks.append(
                exchange.cancel_orders(pair, [order.id for order in order_list[pair]])
            )

        print(f"Canceling limit orders...")
        await asyncio.gather(*tasks)  # Cancel all orders

        print(f"Getting live positions...")
        positions = await exchange.get_open_positions(pairs)
        tasks_close = []
        tasks_open = []
        for position in positions:
            print(
                f"Current position on {position.pair} {position.side} - {position.size} ~ {position.usd_size} $"
            )
            row = df_list[position.pair].iloc[-2]
            order_price = row["ma_base"]

            if position.side == "long":
                pair_margin =  params[position.pair]["margin"]
                order_price = row["ma_base"] * (1 + pair_margin)
            
            tasks_close.append(
                exchange.place_order(
                    pair=position.pair,
                    side=invert_side[position.side],
                    price=order_price,
                    size=position.size,
                    type="limit",
                    reduce=True,
                    margin_mode=margin_mode,
                )
            )
            if position.side == "long":
                sl_side = "sell"
                sl_price = exchange.price_to_precision(position.pair, position.entry_price * (1 - sl))
            elif position.side == "short":
                sl_side = "buy"
                sl_price = exchange.price_to_precision(position.pair, position.entry_price * (1 + sl))
            tasks_close.append(
                exchange.place_trigger_order(
                    pair=position.pair,
                    side=sl_side,
                    trigger_price=sl_price,
                    price=None,
                    size=position.size,
                    type="market",
                    reduce=True,
                    margin_mode=margin_mode,
                    error=False,
                )
            )
            if(use_long):
                for i in range(
                    len(params[position.pair]["envelopes"])
                    - params[position.pair]["canceled_orders_buy"],
                    len(params[position.pair]["envelopes"]),
                ):
                    tasks_open.append(
                        exchange.place_trigger_order(
                            pair=position.pair,
                            side="buy",
                            price=exchange.price_to_precision(
                                position.pair, row[f"ma_low_{i+1}"]
                            ),
                            trigger_price=exchange.price_to_precision(
                                position.pair, row[f"ma_low_{i+1}"] * 1.005
                            ),
                            size=(
                                (params[position.pair]["size"] * usdt_balance)
                                / len(params[position.pair]["envelopes"])
                                * size_leverage
                            )
                            / row[f"ma_low_{i+1}"],
                            type="limit",
                            reduce=False,
                            margin_mode=margin_mode,
                            error=False,
                        )
                    )
            if(use_short):
                for i in range(
                    len(params[position.pair]["envelopes"])
                    - params[position.pair]["canceled_orders_sell"],
                    len(params[position.pair]["envelopes"]),
                ):
                    tasks_open.append(
                        exchange.place_trigger_order(
                            pair=position.pair,
                            side="sell",
                            trigger_price=exchange.price_to_precision(
                                position.pair, row[f"ma_high_{i+1}"] * 0.995
                            ),
                            price=exchange.price_to_precision(
                                position.pair, row[f"ma_high_{i+1}"]
                            ),
                            size=(
                                (params[position.pair]["size"] * usdt_balance)
                                / len(params[position.pair]["envelopes"])
                                * size_leverage
                            )
                            / row[f"ma_high_{i+1}"],
                            type="limit",
                            reduce=False,
                            margin_mode=margin_mode,
                            error=False,
                        )
                    )
            
        print(f"Placing {len(tasks_close)} close SL / limit order...")
        await asyncio.gather(*tasks_close)  # Limit orders when in positions

        # Find pairs that are not in position and create an entry
        pairs_not_in_position = [
            pair
            for pair in pairs
            if pair not in [position.pair for position in positions]
        ]
        for pair in pairs_not_in_position:
            row = df_list[pair].iloc[-2]
            for i in range(len(params[pair]["envelopes"])):
                if(use_long):
                    tasks_open.append(
                        exchange.place_trigger_order(
                            pair=pair,
                            side="buy",
                            price=exchange.price_to_precision(pair, row[f"ma_low_{i+1}"]),
                            trigger_price=exchange.price_to_precision(pair, row[f"ma_low_{i+1}"] * 1.005),
                            size=((params[pair]["size"] * usdt_balance)
                                / len(params[pair]["envelopes"])
                                * size_leverage
                            )
                            / row[f"ma_low_{i+1}"],
                            type="limit",
                            reduce=False,
                            margin_mode=margin_mode,
                            error=False,
                        )
                    )
                if(use_short):
                    tasks_open.append(
                        exchange.place_trigger_order(
                            pair=pair,
                            side="sell",
                            trigger_price=exchange.price_to_precision(
                                pair, row[f"ma_high_{i+1}"] * 0.995
                            ),
                            price=exchange.price_to_precision(pair, row[f"ma_high_{i+1}"]),
                            size=(
                                (params[pair]["size"] * usdt_balance)
                                / len(params[pair]["envelopes"])
                                * size_leverage
                            )
                            / row[f"ma_high_{i+1}"],
                            type="limit",
                            reduce=False,
                            margin_mode=margin_mode,
                            error=False,
                        )
                    )

        print(f"Placing {len(tasks_open)} open limit order...")
        await asyncio.gather(*tasks_open)  # Limit orders when not in positions

        await exchange.close()
        print(f"--- Execution finished at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    except Exception as e:
        await exchange.close()
        raise e


if __name__ == "__main__":
    asyncio.run(main())
