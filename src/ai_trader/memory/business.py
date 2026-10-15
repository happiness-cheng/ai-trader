"""只从服务端确认的 Paper 成交构造事实，不记录模型目标或推理内容。"""

import json
from collections.abc import Callable

from ai_trader.domain.trading import Order, OrderStatus
from ai_trader.memory.lifecycle import ConfirmedMemoryEvent, ControlledMemory


def make_paper_memory_sink(
    memory: ControlledMemory, *, user_id: str,
) -> Callable[[str, Order], None]:
    if not user_id.strip():
        raise ValueError('explicit memory user required')

    def confirmed(run_id: str, order: Order) -> None:
        if order.status is not OrderStatus.FILLED:
            raise ValueError('only confirmed Paper fills can be recorded')
        memory.record_confirmed(ConfirmedMemoryEvent(
            event_id=f'paper-fill:{order.order_id}', user_id=user_id, source_run=run_id,
            confirmed_at=order.created_at, outcome='paper_filled',
            conflict_key=f'paper-order:{order.order_id}', confirmed=True,
            content=json.dumps({
                'execution_mode': 'paper', 'order_id': order.order_id,
                'symbol': order.symbol, 'side': order.side.value,
                'quantity': order.quantity, 'price': str(order.price),
                'status': order.status.value, 'source_run': run_id,
            }, ensure_ascii=False, sort_keys=True),
        ))

    return confirmed
