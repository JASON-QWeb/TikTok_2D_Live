"""有界状态广播：慢客户端只取最新快照，不累计消息占用内存。"""
import threading


class StateEvents:
    def __init__(self):
        self.condition = threading.Condition()
        self.revision = 0

    def publish(self):
        with self.condition:
            self.revision += 1
            self.condition.notify_all()

    def wait(self, previous, timeout=15):
        with self.condition:
            self.condition.wait_for(lambda: self.revision != previous, timeout)
            return self.revision


def snapshot(b, since=0):
    # 只在复制时持锁，JSON 编码与慢客户端网络写入绝不占用队列锁。
    with b.LOCK:
        state = dict(b.LATEST)
        state.update(
            replies=[dict(r) for r in b.REPLIES],
            last_danmaku_at=b.LAST_DANMAKU_TIME, started_at=b.STARTED_AT,
            model=b.CURRENT_MODEL, now_playing=dict(b.NOW_PLAYING),
            usage=b.get_usage(), gift_total=sum(b.GIFT_BOARD.values()),
            gift_board=sorted(b.GIFT_BOARD.items(), key=lambda x: -x[1])[:10],
            gift_log=list(b.GIFT_LOG), queue={"depth": len(b.QUEUE), **b.QUEUE_STATS},
            actions=[a for a in b.ACTION_LOG if a.get("seq", 0) > since],
            action_seq=b.ACTION_SEQ, director=b.DIRECTOR.snapshot(),
            tts_backend=b.TTS_BACKEND,
        )
    return state
