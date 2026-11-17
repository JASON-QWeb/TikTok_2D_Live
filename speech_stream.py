"""情绪解析、分句和有界流式预取。"""
import re
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor


_RECEIVER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-receiver")


def measure_stream(source, timings):
    start = time.monotonic()
    try:
        yield from source
    finally:
        timings["deepseek"] = round(time.monotonic() - start, 3)


def prefetch(source, capacity=4):
    """TTS 合成时继续接收 LLM；有界缓冲提供背压，失败后停止生产者。

    只并行接收文本，不并行使用 Piper 音色，确保声音顺序与模型线程安全。
    消费者异常时取消等待；在途 HTTP 读取由连接自身的读超时收尾。
    """
    pending = queue.Queue(maxsize=capacity)
    cancelled = threading.Event()

    def put(value):
        while not cancelled.is_set():
            try:
                pending.put(value, timeout=0.1)
                return
            except queue.Full:
                pass

    def produce():
        try:
            for value in source:
                if cancelled.is_set():
                    break
                put((value, None, False))
        except Exception as exc:
            put((None, exc, False))
        finally:
            close = getattr(source, "close", None)
            if close:
                close()
            put((None, None, True))

    _RECEIVER.submit(produce)
    try:
        while True:
            value, error, done = pending.get()
            if error:
                raise error
            if done:
                return
            yield value
    finally:
        cancelled.set()

EMOTION_MAP = {"开心": "happy", "害羞": "shy", "惊讶": "surprised", "难过": "sad",
               "生气": "angry", "思考": "thinking", "平静": "neutral"}
EMOTION_LABEL = {v: k for k, v in EMOTION_MAP.items()}
_TAG_ALT = "|".join(EMOTION_MAP)
_LEAD_TAG_RE = re.compile(r"^\s*[\[【](" + _TAG_ALT + r")[\]】]\s*")
_ANY_TAG_RE = re.compile(r"[\[【](?:" + _TAG_ALT + r")[\]】]")


def parse_emotion(text):
    """整段文本：返回 (情绪代号或空串, 去掉标签后的文本)。"""
    m = _LEAD_TAG_RE.match(text or "")
    emotion = EMOTION_MAP[m.group(1)] if m else ""
    rest = text[m.end():] if m else (text or "")
    return emotion, _ANY_TAG_RE.sub("", rest).strip()


def strip_emotion_stream(deltas, out):
    """流式文本：识别开头的情绪标签写入 out["emotion"]，其余增量原样往下传。

    只在开头几个字符内等待标签；不是合法标签就原样放行，不会吞字。
    """
    buf = ""
    decided = False
    for d in deltas:
        if decided:
            yield d
            continue
        buf += d
        head = buf.lstrip()
        if not head:
            continue
        if head[0] not in "[【":
            decided = True
            yield buf
            continue
        m = _LEAD_TAG_RE.match(buf)
        if m:
            out["emotion"] = EMOTION_MAP[m.group(1)]
            decided = True
            if buf[m.end():]:
                yield buf[m.end():]
        elif len(head) > 6 or "]" in head or "】" in head:
            decided = True        # 方括号开头但不是情绪标签：照常输出
            yield buf
    if not decided and buf:
        emotion, rest = parse_emotion(buf)
        if emotion:
            out["emotion"] = emotion
        if rest:
            yield rest


# ---------------------------------------------------------------- 流式按句合成

_STRONG_PUNCT = set("。！？!?；;…~～\n")
_WEAK_PUNCT = set("，,、：:")
_CLOSERS = set("」』”’）)】》 ")


def _find_cut(buf, first):
    """在 buf 里找切句位置；返回切点下标（不含），找不到返回 None。

    只有标点后面已经出现了下一个正文字符才切——否则「哈哈！」之后紧跟的「！」
    会被单独切成一段，念出来很怪。
    """
    n = len(buf)
    i = 0
    while i < n:
        ch = buf[i]
        strong = ch in _STRONG_PUNCT
        # 第一句允许在逗号处提前切：尽快开口比断句完美更重要
        weak_ok = ch in _WEAK_PUNCT and (first and i + 1 >= 8 or i + 1 >= 40)
        if (strong and i + 1 >= 4) or weak_ok:
            j = i + 1
            while j < n and (buf[j] in _STRONG_PUNCT or buf[j] in _WEAK_PUNCT
                             or buf[j] in _CLOSERS):
                j += 1
            if j < n:
                return j
            return None
        i += 1
    return None


def iter_sentences(deltas):
    """把流式文本增量切成适合 TTS 的句子（生成器）。"""
    buf = ""
    first = True
    for delta in deltas:
        buf += delta
        while True:
            cut = _find_cut(buf, first)
            if cut is None and len(buf) >= 48:
                cut = 48  # 长句一直不带标点时也能及时开口，并限制单次 TTS 的开销。
            if cut is None:
                break
            seg, buf = buf[:cut], buf[cut:]
            if seg.strip():
                yield seg.strip()
                first = False
    if buf.strip():
        yield buf.strip()
