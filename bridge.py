#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数字人桥：弹幕 → 流式 LLM → 本地 TTS → 3D / Live2D。

运行：npm ci && npm run build；python bridge.py
POST /api/danmaku 收弹幕，GET /api/events 推送状态，GET /avatar2d 保留 2D。
"""

import json
import os
import re
import socket
import sys
import threading
import time
import traceback
import socketserver
from collections import deque
from http.server import ThreadingHTTPServer
from contextlib import closing

# 跨平台 TTS：piper / macOS say / Windows sapi，按 TTS_BACKEND 或平台自动选择
from tts_backend import (
    BASE_DIR,
    configure_console,
    native_backend,
    pick_local_voice,
    resolve_backend,
    synthesize as tts_synthesize,
    warmup as tts_warmup,
)

# 复用验证脚本里的链路函数
from verify_pipeline import (
    download,
    deepseek_reply,
    deepseek_stream,
    env,
    get_usage,
    pick_voice,
    vidu_lipsync_submit,
    vidu_lipsync_wait,
    vidu_tts,
)
from director import GiftDirector
from bridge_events import StateEvents

EVENTS = StateEvents()
WORK_READY = threading.Event()

HOST = env("BRIDGE_HOST", "127.0.0.1")
PORT = int(env("BRIDGE_PORT", "8000") or 8000)
MIN_INTERVAL = float(env("BRIDGE_MIN_INTERVAL", "0.3") or 0.3)   # 两条回复最小间隔(秒)，在 worker 里控制
MAX_QUEUE = int(env("BRIDGE_MAX_QUEUE", "20") or 20)
MAX_BODY = int(env("BRIDGE_MAX_BODY", "8192") or 8192)       # 请求体上限(字节)

# BRIDGE_TOKEN 是「只读视图令牌」：只保护 GET（页面 / 状态 / 音频 / 视频）。
# 写入口 /api/danmaku 只允许回环访问，因此令牌永远不会成为可写凭据——
# 即使它随 ?token= 出现在 URL 里、被分享出去，最坏后果也只是「别人看到你的
# 弹幕」，而不是「别人替你烧 API 额度」。
BRIDGE_TOKEN = env("BRIDGE_TOKEN", "")
TOKEN_B = BRIDGE_TOKEN.encode("utf-8") if BRIDGE_TOKEN else b""
MIN_TOKEN_LEN = 16
ALLOW_UNAUTHENTICATED = env("BRIDGE_ALLOW_UNAUTHENTICATED", "").lower() in (
    "1", "true", "yes", "on")

# ---- 绑定与访问控制 ----
# 注意：Host 白名单只防 DNS rebinding（攻击者域名解析到 127.0.0.1 后借浏览器
# 打本地服务），**它不构成鉴权**——Host 头是客户端自填的，攻击者直接写
# `Host: 127.0.0.1:8000` 就能过。真正的屏障是「写只允许回环」。
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
# 空串绝不能当成回环：ThreadingHTTPServer 绑 "" 等于监听所有网卡（等价 0.0.0.0）。
# 之前写成 `or HOST == ""` 会让 `BRIDGE_HOST=` 直接绕过 fail-closed，
# 局域网带伪造 Host 就能读到 /api/state。
BOUND_TO_LOOPBACK = HOST in LOOPBACK_HOSTS
ALLOWED_HOSTS = set(LOOPBACK_HOSTS)
if HOST and HOST not in ("0.0.0.0", "::"):
    ALLOWED_HOSTS.add(HOST)
# 只有显式暴露到局域网时才接受本机网卡 IP，避免「已防护」的错觉。
# 在启动时（鉴权检查之后）由 discover_local_ips() 填充，不在 import 阶段做：
# gethostbyname_ex 没有超时，主机名解析不了的机器（如 GitHub macOS runner）
# 会卡十几秒，连「拒绝启动」的检查都被拖住。
LOCAL_IPS = set()


def discover_local_ips(wait=2.0):
    """探测本机网卡 IP 写入 LOCAL_IPS，最多阻塞 wait 秒。"""
    # 主网卡 IP：UDP connect 只选路由、不发包、不查 DNS，瞬间返回
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            LOCAL_IPS.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass

    def _by_hostname():   # 多网卡时补全其它 IP；可能很慢，放后台
        try:
            LOCAL_IPS.update(socket.gethostbyname_ex(socket.gethostname())[2])
        except Exception:  # noqa: BLE001  离线或解析失败不阻断启动
            pass

    t = threading.Thread(target=_by_hostname, daemon=True)
    t.start()
    t.join(wait)
CHAT_TTL = float(env("BRIDGE_CHAT_TTL", "30") or 30)         # 普通弹幕排队超过 N 秒就不回了
GIFT_TTL = float(env("BRIDGE_GIFT_TTL", "120") or 120)       # 礼物感谢保留更久
STREAM_REPLY = env("DEEPSEEK_STREAM", "1").lower() not in ("0", "false", "no", "off")

LOCK = threading.Lock()
QUEUE = deque()
LATEST = {
    "status": "idle",       # idle | queued | processing | done | error
    "user": "", "content": "", "reply": "", "voice": "",
    "audio_url": "", "task_id": "", "video_url": "", "local_path": "",
    "timings": {}, "error": "", "updated_at": time.strftime("%H:%M:%S"),
}
_last_run = 0.0
LATEST_AUDIO = ""          # 本地 TTS 产出的最新 wav 路径
TTS_BACKEND_ERROR = ""
try:
    # piper(本地) / say(macOS) / sapi(Windows) / vidu(云端对口型) / auto(按平台探测)
    TTS_BACKEND = resolve_backend()
except (ValueError, RuntimeError) as _e:
    # 不能在 import 阶段就退出：CI 会在 ubuntu 上 import bridge 跑单测，
    # 而 Linux 既没有 say 也没有 sapi。这里记下原因，等到启动或首次合成时报错。
    TTS_BACKEND = "auto"
    TTS_BACKEND_ERROR = str(_e)
HISTORY = deque(maxlen=int(env("HISTORY_TURNS", "8") or 8))   # 记忆最近 N 轮对话，让回复能接上下文
USER_MEMORY = {}           # 昵称 -> deque([(弹幕, 回复), ...]) 按人记忆，提升用户亲和性
REPLIES = deque(maxlen=30) # 已完成的回复队列（供前端排队播放，避免下一条覆盖上一条）
REPLY_SEQ = 0
REPLY_AUDIO = {}           # seq -> 音频文件路径
NOW_PLAYING = {"seq": 0, "name": "", "artist": "", "url": "", "pic": "", "lyric": []}  # 点歌：当前播放歌曲
STARTED_AT = time.time()   # 桥启动时间(epoch)
LAST_DANMAKU_TIME = None   # 上次收到弹幕的时间戳(epoch)，用于前端"是否在线"提示
MODELS = ["Hiyori", "Haru", "Mao", "Natori", "Ren", "Rice", "Mark", "Wanko"]
CURRENT_MODEL = "Hiyori"   # 当前角色，弹幕可切换
GIFT_LOG = deque(maxlen=10) # 最近礼物事件（前端礼物榜/调试）
DIRECTOR = GiftDirector()  # 礼物规则导演：事件 -> ActionPlan
ACTION_SEQ = 0             # 动作序号，前端据此只播放新动作
ACTION_LOG = deque(maxlen=20)  # 最近动作，供前端补播/排查


def _gift_board_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "gift_board.json")


def _load_gift_board():
    try:
        with open(_gift_board_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_GIFT_BOARD_DIRTY = False


def _save_gift_board():
    """原子写礼物榜：先写临时文件再 os.replace，写到一半崩溃也不会把榜单写坏。"""
    global _GIFT_BOARD_DIRTY
    with LOCK:
        if not _GIFT_BOARD_DIRTY:
            return
        snapshot = dict(GIFT_BOARD)
        _GIFT_BOARD_DIRTY = False
    path = _gift_board_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        with LOCK:
            _GIFT_BOARD_DIRTY = True   # 下次再试
        traceback.print_exc()


def add_gift_value(user, value):
    """累计礼物榜（只改内存并标脏，由 gift_board_saver 每隔几秒落盘一次）。"""
    global _GIFT_BOARD_DIRTY
    with LOCK:
        GIFT_BOARD[user] = GIFT_BOARD.get(user, 0) + value
        _GIFT_BOARD_DIRTY = True


def gift_board_saver(interval=5.0):
    while True:
        time.sleep(interval)
        _save_gift_board()


GIFT_BOARD = _load_gift_board()   # 送礼榜单：从文件恢复，跨重启累计

MAX_AUDIO_KEEP = int(env("MAX_AUDIO_KEEP", "50") or 50)   # 最多保留最近 N 条音频文件


def _cleanup_audio_dir():
    """启动时清空 audio/ 目录：清掉跨重启遗留的旧音频（REPLY_AUDIO 是内存态，重启后旧文件不再被引用）。"""
    try:
        d = os.path.join(BASE_DIR, "audio")
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.endswith(".wav"):
                    try:
                        os.remove(os.path.join(d, fn))
                    except Exception:
                        pass
    except Exception:
        pass


def _cleanup_audio():
    """删除超出保留范围的旧音频文件，防止磁盘无限累积。"""
    if REPLY_SEQ <= MAX_AUDIO_KEEP:
        return
    cutoff = REPLY_SEQ - MAX_AUDIO_KEEP
    for seq in list(REPLY_AUDIO):
        if seq <= cutoff:
            p = REPLY_AUDIO.pop(seq, None)
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def publish_action(plan):
    """给导演产出的 ActionPlan 编序号，并放进最近动作队列。"""
    global ACTION_SEQ
    if not plan:
        return None
    with LOCK:
        ACTION_SEQ += 1
        action = dict(plan)
        action["seq"] = ACTION_SEQ
        ACTION_LOG.append(action)
    EVENTS.publish()
    return action


_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF"      # 主要 emoji 区
    "\U00002600-\U000027BF"       # 杂项符号 + 装饰符号
    "\U0001F1E6-\U0001F1FF"       # 旗帜
    "\U00002B00-\U00002BFF"       # 部分符号
    "\uFE0F\u200D"                # 变体选择符 + 零宽连接符
    "]+"
)


def strip_emoji(text):
    """去掉 emoji（气泡里仍显示，但 TTS 念 emoji 很奇怪，这里只影响朗读文本）。"""
    return _EMOJI_RE.sub("", text).strip()


BLOCK_WORDS = [w.strip() for w in env("BLOCK_WORDS", "").split(",") if w.strip()]


def filter_reply(text):
    """本地敏感词兜底过滤（DeepSeek 自审之外的保险）。"""
    for w in BLOCK_WORDS:
        if w:
            text = text.replace(w, "*" * len(w))
    return text


def user_context(nick):
    """按昵称取该观众最近几轮对话，让数字人记得「上次和这个人聊过什么」。"""
    mem = USER_MEMORY.get(nick)
    if not mem:
        return ""
    parts = []
    for c, r in mem:
        parts.append(f"他之前说「{c}」，你之前回「{r}」")
    return "；".join(parts)


def try_switch_model(content):
    """检测换角色命令：命中返回新模型名，否则 None。"""
    global CURRENT_MODEL
    text = (content or "").strip()
    if not text:
        return None
    # 明确"换/切"命令 → 轮换下一个
    if text in ("换角色", "切换角色", "换人", "换皮肤", "换形象", "换模型",
                "换个角色", "换个形象", "下一个", "下一位"):
        i = MODELS.index(CURRENT_MODEL)
        CURRENT_MODEL = MODELS[(i + 1) % len(MODELS)]
        return CURRENT_MODEL
    # 含"换/切"关键词 + 角色名 → 切到指定
    if ("换" in text or "切" in text):
        for model in MODELS:
            if model.lower() in text.lower():
                if model != CURRENT_MODEL:
                    CURRENT_MODEL = model
                    return model
    return None


def local_tts(text, say_voice=None):
    """本地 TTS：统一委派给 tts_backend（piper / macOS say / Windows sapi）。

    两点与上游原实现等价：
      - Piper 运行期失败时回落到平台原生音色，不让直播直接哑掉；
      - vidu 模式下进场播报/礼物感谢/换角色提示这类短句仍走本地合成——
        它们不经过 process_one 的 vidu 分支，而 tts_backend 没有实现 vidu
        （云端对口型在 process_one 里单独处理），不兜底就会全部报错。
    """
    if TTS_BACKEND_ERROR:
        raise RuntimeError(f"TTS 后端不可用：{TTS_BACKEND_ERROR}")
    backend = resolve_backend("auto") if TTS_BACKEND == "vidu" else TTS_BACKEND
    try:
        return tts_synthesize(text, say_voice or None, backend=backend)
    except Exception:  # noqa: BLE001
        if backend != "piper":
            raise
        traceback.print_exc()
        return tts_synthesize(text, say_voice or None, backend=native_backend())


def _push_reply(entry, wav_path):
    """把一条（或一段）已合成的回复放进前端播放队列。"""
    global REPLY_SEQ, LATEST_AUDIO
    with LOCK:
        REPLY_SEQ += 1
        entry["seq"] = REPLY_SEQ
        entry["has_audio"] = bool(wav_path)
        entry["created_at"] = time.time()
        REPLIES.append(entry)
        if wav_path:
            REPLY_AUDIO[REPLY_SEQ] = wav_path
            LATEST_AUDIO = wav_path
        _cleanup_audio()
    EVENTS.publish()
    return entry


def _new_reply_id():
    return f"r{int(time.time() * 1000)}"


def finish_reply(reply, voice_label, user, content, timings, say_voice=None, emotion=""):
    """合成整段语音 + 更新状态 + 入回复队列（换角色/直接念/非流式回复共用）。"""
    reply = filter_reply(_ANY_TAG_RE.sub("", reply).strip())   # 本地敏感词兜底
    text = strip_emoji(reply)
    t0 = time.time()
    wav_path = local_tts(text, say_voice) if text else ""
    timings["tts"] = round(time.time() - t0, 3)
    timings["first_audio"] = round(timings.get("deepseek", 0) + timings["tts"], 3)
    with LOCK:
        LATEST.update(status="done", reply=reply, voice=voice_label,
                      audio_url="", task_id="", video_url="",
                      local_path=wav_path, timings=timings,
                      error="", updated_at=time.strftime("%H:%M:%S"))
    _push_reply({"reply_id": _new_reply_id(), "part": 0, "final": True,
                 "user": user, "content": content, "reply": reply, "voice": voice_label,
                 "emotion": emotion}, wav_path)


# ---------------------------------------------------------------- 情绪标签
# LLM 按 prompt 在回复开头输出「[开心]」之类的标签（见 verify_pipeline.EMOTION_INSTRUCTION）。
# 这里把标签剥掉（不念、不显示），换成英文代号随回复下发，前端据此切换 Live2D 表情。

from speech_stream import (
    EMOTION_MAP, EMOTION_LABEL, _ANY_TAG_RE, _STRONG_PUNCT, _WEAK_PUNCT,
    parse_emotion, strip_emotion_stream, iter_sentences, prefetch, measure_stream,
)


def stream_reply(content, user, voice_label, say_voice, timings):
    """DeepSeek 流式生成 + 逐句本地 TTS：第一句生成完就开口。返回 (完整回复, 情绪代号)。"""
    t0 = time.time()
    reply_id = _new_reply_id()
    full = ""          # 气泡里显示的累计文本（含 emoji）
    carry = ""         # 纯 emoji/标点的片段，并到下一句一起显示
    part = 0
    tts_total = 0.0
    last_entry = None
    meta = {}
    deltas = strip_emotion_stream(
        deepseek_stream(content, nick=user, history=list(HISTORY),
                        user_context=user_context(user)), meta)
    with closing(prefetch(iter_sentences(measure_stream(deltas, timings)))) as segments:
        for seg in segments:
            seg = filter_reply(_ANY_TAG_RE.sub("", carry + seg))
            carry = ""
            speak_text = strip_emoji(seg).strip()
            if not speak_text or all(c in _STRONG_PUNCT or c in _WEAK_PUNCT for c in speak_text):
                carry = seg
                continue
            if part == 0:
                timings["deepseek_first"] = round(time.time() - t0, 2)
            full += seg
            t1 = time.time()
            wav_path = local_tts(speak_text, say_voice)
            tts_total += time.time() - t1
            if part == 0:
                timings["first_audio"] = round(time.time() - t0, 2)
            last_entry = _push_reply({"reply_id": reply_id, "part": part, "final": False,
                                      "user": user, "content": content, "reply": full,
                                      "say": speak_text, "voice": voice_label,
                                      "emotion": meta.get("emotion", "")}, wav_path)
            with LOCK:
                LATEST.update(reply=full, voice=voice_label, local_path=wav_path)
            part += 1
    if carry:
        full += carry
    with LOCK:
        if last_entry is not None:
            # 前端每次轮询拿到的是 REPLIES 的新快照，改这里即可通知「这句话说完了」
            last_entry["final"] = True
            last_entry["reply"] = full
    timings["pipeline"] = round(time.time() - t0, 2)
    timings["tts"] = round(tts_total, 2)
    if part == 0:   # 一句可念的都没有（空回复或只有 emoji）
        raise RuntimeError("DeepSeek 返回了空回复")
    with LOCK:
        LATEST.update(status="done", reply=full, voice=voice_label,
                      audio_url="", task_id="", video_url="", timings=timings,
                      error="", updated_at=time.strftime("%H:%M:%S"))
    EVENTS.publish()
    return full, meta.get("emotion", "")


from bridge_music import try_music_command, netease_search


def process_cloud_reply(content, user, timings):
    t0 = time.time()
    emotion, reply = parse_emotion(
        deepseek_reply(content, nick=user, history=list(HISTORY),
                       user_context=user_context(user)))
    timings["deepseek"] = round(time.time() - t0, 2)
    _remember(user, content, reply, emotion)

    voice_id, voice_label = pick_voice(content)
    t0 = time.time()
    audio_url = vidu_tts(reply, voice_id)
    timings["tts"] = round(time.time() - t0, 2)

    avatar_video = env("VIDU_AVATAR_VIDEO_URL")
    if not avatar_video:
        raise RuntimeError("缺少 VIDU_AVATAR_VIDEO_URL")

    t0 = time.time()
    task_id = vidu_lipsync_submit(avatar_video, audio_url)
    timings["submit"] = round(time.time() - t0, 2)

    t0 = time.time()
    video_url = vidu_lipsync_wait(task_id)
    timings["render_wait"] = round(time.time() - t0, 2)

    t0 = time.time()
    local_path = download(video_url, os.path.join(BASE_DIR, "output"))
    timings["download"] = round(time.time() - t0, 2)

    with LOCK:
        LATEST.update(status="done", reply=reply, voice=voice_label,
                      audio_url=audio_url, task_id=task_id, video_url=video_url,
                      local_path=local_path, timings=timings,
                      error="", updated_at=time.strftime("%H:%M:%S"))
    _push_reply({"reply_id": _new_reply_id(), "part": 0, "final": True,
                 "user": user, "content": content, "reply": reply,
                 "voice": voice_label, "emotion": emotion}, "")


def process_one(item):
    """在后台线程执行完整链路。item 含 kind: chat(弹幕)/gift(礼物)/speak(直接念)。"""
    kind = item.get("kind", "chat")
    user = item.get("user", "")
    content = item.get("content") or item.get("text") or ""
    with LOCK:
        LATEST.update(status="processing", user=user, content=content,
                      reply="", voice="", video_url="", local_path="", error="", timings={},
                      updated_at=time.strftime("%H:%M:%S"))
    EVENTS.publish()
    timings = {"waited": round(time.time() - item.get("_ts", time.time()), 2)}

    try:
        # 换角色命令（仅 chat）
        if kind == "chat":
            new_model = try_switch_model(content)
            if new_model:
                finish_reply(f"好嘞，换到「{new_model}」啦～", "默认", user, content, timings,
                             emotion="happy")
                return

        # 点歌命令（仅 chat）
        if kind == "chat":
            kw = try_music_command(content)
            if kw:
                song = netease_search(kw)
                with LOCK:
                    NOW_PLAYING["seq"] += 1
                    NOW_PLAYING["name"] = song["name"] if song else ""
                    NOW_PLAYING["artist"] = song["artist"] if song else ""
                    NOW_PLAYING["url"] = song["url"] if song else ""
                    NOW_PLAYING["pic"] = song["pic"] if song else ""
                    NOW_PLAYING["lyric"] = song["lyric"] if song else []
                if song:
                    artist_txt = f"·{song['artist']}" if song.get("artist") else ""
                    finish_reply(f"好嘞，给「{user}」点一首《{song['name']}》{artist_txt}～",
                                 "默认", user, content, timings, emotion="happy")
                else:
                    finish_reply(f"「{kw}」我没搜到诶，换个歌名再试试？",
                                 "默认", user, content, timings, emotion="thinking")
                return

        # 直接念（进场/关注/点赞/守护/醒目留言等）
        if kind == "speak":
            finish_reply(item.get("text", ""), "默认", user, content, timings)
            return

        # 礼物：累计榜单 + 记录最近礼物
        if kind == "gift":
            add_gift_value(user, item.get("gift_value", 0))
            with LOCK:
                GIFT_LOG.append({"nick": user, "item": item.get("gift_item"),
                                 "num": item.get("gift_num"), "value": item.get("gift_value")})

        if TTS_BACKEND != "vidu":
            say_voice, voice_label = pick_local_voice(content)
            if STREAM_REPLY:
                reply, emotion = stream_reply(content, user, voice_label, say_voice, timings)
            else:
                t0 = time.time()
                emotion, reply = parse_emotion(
                    deepseek_reply(content, nick=user, history=list(HISTORY),
                                   user_context=user_context(user)))
                timings["deepseek"] = round(time.time() - t0, 2)
                finish_reply(reply, voice_label, user, content, timings,
                             say_voice=say_voice, emotion=emotion)
            _remember(user, content, reply, emotion)
        else:
            process_cloud_reply(content, user, timings)
    except BaseException as e:  # noqa: BLE001  require() 抛 SystemExit，也要接住，否则 worker 线程会死
        with LOCK:
            LATEST.update(status="error", error=str(e) or type(e).__name__, timings=timings,
                          updated_at=time.strftime("%H:%M:%S"))
        traceback.print_exc()
        if isinstance(e, KeyboardInterrupt):
            raise
    finally:
        EVENTS.publish()


def _remember(user, content, reply, emotion=""):
    HISTORY.append({"role": "user", "content": f"弹幕：{content}"})
    # 历史里保留情绪标签：LLM 看到自己一直这样输出，后续回复才会稳定带标签
    tag = f"[{EMOTION_LABEL[emotion]}]" if emotion in EMOTION_LABEL else ""
    HISTORY.append({"role": "assistant", "content": tag + reply})
    if user:
        USER_MEMORY.setdefault(user, deque(maxlen=3)).append((content, reply))


# ---------------------------------------------------------------- 队列调度
#
# 入口只负责入队（礼物永不因限流被丢），worker 负责节奏与挑选：
#   - 优先级：礼物 > VIP > 提问/点名 > 普通弹幕/播报；同级先来先回
#   - 过期：普通弹幕排队超过 CHAT_TTL 秒就丢——回复一分钟前的弹幕，观众早走了
#   - 去重：event_id（抖音会重推）+ 短时间内相同弹幕文本（刷屏）
#   - 队列满：新来的优先级不低于最差那条时，挤掉最差里最旧的；礼物永远能进

PRIO_GIFT, PRIO_VIP, PRIO_QUESTION, PRIO_NORMAL = 0, 1, 2, 3
_QUESTION_RE = re.compile(r"[?？]|吗|怎么|为什么|什么|小雅|主播|@")
TEXT_DEDUPE_WINDOW = 10.0
EVENT_DEDUPE_WINDOW = 120.0
_SEEN_EVENTS = {}          # event_id -> 首次入队时间
_RECENT_TEXT = {}          # 弹幕文本 -> 最近入队时间
QUEUE_STATS = {"expired": 0, "evicted": 0, "deduped": 0, "rejected": 0}


def _classify(item, now):
    kind = item.get("kind")
    vip = item.get("priority") == "vip"
    if kind == "gift":
        prio, ttl = PRIO_GIFT, GIFT_TTL
    elif vip:
        prio, ttl = PRIO_VIP, max(CHAT_TTL, 60.0)
    elif kind == "chat" and _QUESTION_RE.search(item.get("content", "")):
        prio, ttl = PRIO_QUESTION, CHAT_TTL
    else:
        prio, ttl = PRIO_NORMAL, CHAT_TTL
    item["_prio"] = prio
    item["_ts"] = now
    item["_expires"] = now + ttl


def _prune_seen(now):
    for table, window in ((_SEEN_EVENTS, EVENT_DEDUPE_WINDOW), (_RECENT_TEXT, TEXT_DEDUPE_WINDOW)):
        if len(table) > 500:
            for k in [k for k, t in table.items() if now - t > window]:
                del table[k]


def _drop_expired(now):
    expired = [i for i in QUEUE if i["_expires"] <= now]
    for i in expired:
        QUEUE.remove(i)
    QUEUE_STATS["expired"] += len(expired)


def enqueue(item, now=None):
    """入队；返回是否真正入队。调用方无需关心节奏，限流在 worker 里做。"""
    global LAST_DANMAKU_TIME
    now = now if now is not None else time.time()
    _classify(item, now)
    eid = str(item.get("event_id") or "")
    text = (item.get("content") or "").strip() if item.get("kind") == "chat" else ""
    with LOCK:
        LAST_DANMAKU_TIME = now
        _prune_seen(now)
        if eid:
            seen = _SEEN_EVENTS.get(eid)
            if seen is not None and now - seen < EVENT_DEDUPE_WINDOW:
                QUEUE_STATS["deduped"] += 1
                return False
            _SEEN_EVENTS[eid] = now
        if text:
            seen = _RECENT_TEXT.get(text)
            if seen is not None and now - seen < TEXT_DEDUPE_WINDOW:
                QUEUE_STATS["deduped"] += 1
                return False
            _RECENT_TEXT[text] = now

        _drop_expired(now)
        if len(QUEUE) >= MAX_QUEUE:
            worst = max(QUEUE, key=lambda i: (i["_prio"], -i["_ts"]))
            if item["_prio"] == PRIO_GIFT and worst["_prio"] == PRIO_GIFT:
                pass                       # 全是礼物：超额也收，礼物会在出队时合并
            elif item["_prio"] <= worst["_prio"]:
                QUEUE.remove(worst)
                QUEUE_STATS["evicted"] += 1
            else:
                QUEUE_STATS["rejected"] += 1
                return False
        QUEUE.append(item)
        if LATEST.get("status") not in ("processing",):
            LATEST.update(status="queued", user=item.get("user", ""),
                          content=item.get("content") or item.get("text") or "",
                          updated_at=time.strftime("%H:%M:%S"))
    WORK_READY.set()
    EVENTS.publish()
    return True


def _merge_gifts_locked(item):
    """把队列里同一观众的其它礼物合并进 item，避免连击时逐个感谢。调用方持有 LOCK。"""
    nick = item.get("user", "")
    same = [g for g in QUEUE if g.get("kind") == "gift" and g.get("user") == nick]
    if not same:
        return item
    counts = {item.get("gift_item", "礼物"): item.get("gift_num", 1)}   # 同名礼物合计
    total_num = item.get("gift_num", 1)
    total_value = item.get("gift_value", 0)
    for g in same:
        QUEUE.remove(g)
        name = g.get("gift_item", "礼物")
        counts[name] = counts.get(name, 0) + g.get("gift_num", 1)
        total_num += g.get("gift_num", 1)
        total_value += g.get("gift_value", 0)
    item["gift_num"] = total_num
    item["gift_value"] = total_value
    summary = "、".join(f"{n}个「{i}」" for i, n in counts.items())
    item["content"] = f"观众「{nick or '神秘人'}」连送了 {summary}"
    return item


def pick_next(now=None):
    """挑下一条要处理的：先丢过期，再按 (优先级, 入队时间) 取最小。"""
    now = now if now is not None else time.time()
    with LOCK:
        _drop_expired(now)
        if not QUEUE:
            return None
        best = min(QUEUE, key=lambda i: (i["_prio"], i["_ts"]))
        QUEUE.remove(best)
        if best.get("kind") == "gift":
            best = _merge_gifts_locked(best)
        return best


def _gift_waiting():
    with LOCK:
        return any(i.get("kind") == "gift" for i in QUEUE)


def worker():
    global _last_run
    while True:
        # 节奏控制：两条回复之间至少隔 MIN_INTERVAL 秒（省 API 额度、给观众反应时间）。
        # 礼物不等。等待期间新来的高优先级弹幕会在出队时被优先挑中。
        WORK_READY.clear()
        wait = _last_run + MIN_INTERVAL - time.time()
        if wait > 0 and not _gift_waiting():
            WORK_READY.wait(wait)
            continue
        item = pick_next()
        if item is None:
            WORK_READY.wait(1)
            continue
        process_one(item)
        _last_run = time.time()


def director_tick_loop():
    """后台推进礼物队列：当前演出结束后自动播下一个。"""
    while True:
        try:
            publish_action(DIRECTOR.tick())
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(0.2)


with open(os.path.join(BASE_DIR, "avatar.html"), encoding="utf-8") as _f:
    AVATAR_HTML = _f.read()
LIVE2D_DIR = os.path.join(BASE_DIR, "live2d")
STATIC_DIR = os.path.join(BASE_DIR, "static")


from bridge_http import make_handler
Handler = make_handler(sys.modules[__name__])


def check_auth_config():
    from bridge_auth import check_auth_config as check
    check(sys.modules[__name__])


class BridgeServer(ThreadingHTTPServer):
    """跳过 HTTPServer.server_bind 里的 socket.getfqdn()。

    getfqdn 会做反向 DNS 解析且没有超时；主机名/反解不通的机器（如 GitHub
    macOS runner）上 bind 会卡几十秒才开始监听。server_name 只用于 CGI 环境变量，
    本服务用不到，直接用绑定地址即可。
    """

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


if __name__ == "__main__":
    configure_console()   # Windows 下输出重定向时不因 GBK 编码崩溃
    check_auth_config()
    _cleanup_audio_dir()   # 清掉跨重启遗留的旧音频
    if not BOUND_TO_LOOPBACK:
        discover_local_ips()
    if TTS_BACKEND_ERROR:
        # 支持平台上没有可用 TTS 就是配置错误，直接退出比跑起来再哑掉好。
        # Linux 只用于 CI 跑接口/单测（既无 say 也无 sapi），放行但明确告警，
        # 否则 CI 里所有需要启动服务的用例都会失败。
        _msg = f"TTS 后端不可用：{TTS_BACKEND_ERROR}"
        if os.name == "nt" or sys.platform == "darwin":
            raise SystemExit(f"[配置错误] {_msg}")
        print(f"[警告] {_msg}")
        print("       当前平台无原生 TTS，服务仍会启动，但合成语音会失败；仅适合接口调试。")
    # Piper 预热（阻塞几秒，避免与 worker 线程竞争加载模型）。
    # auto 模式下预热失败会回落到平台原生后端，这里接住实际生效的后端，
    # 免得拖到第一条回复才报错。
    TTS_BACKEND = tts_warmup(TTS_BACKEND)
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=director_tick_loop, daemon=True).start()
    threading.Thread(target=gift_board_saver, daemon=True).start()
    srv = BridgeServer((HOST, PORT), Handler)
    print(f"数字人桥已启动：http://{HOST}:{PORT}")
    print(f"  - 虚拟人页面： http://{HOST}:{PORT}/")
    print(f"  - 收弹幕：     POST http://{HOST}:{PORT}/api/danmaku")
    print(f"  - 查状态：     GET  http://{HOST}:{PORT}/api/state")
    print(f"  - 本地音频：   GET  http://{HOST}:{PORT}/audio")
    print(f"  TTS 后端 = {TTS_BACKEND}"
          f"（piper=本地模型 / say=macOS / sapi=Windows / vidu=云端对口型）")
    print(f"  工作目录 = {BASE_DIR}")
    print(f"  - 礼物规则：   {DIRECTOR.snapshot().get('rules', 0)} 条（config/gift_actions.json）")
    print(f"  回复模式 = {'流式按句合成' if STREAM_REPLY else '整段合成'}（DEEPSEEK_STREAM）"
          f"；弹幕过期 {CHAT_TTL:g}s / 礼物过期 {GIFT_TTL:g}s")
    print("提示：开播前请确认 .env 已填 DeepSeek Key。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        _save_gift_board()   # 退出前把未落盘的礼物榜写掉
