"""原有点歌功能，独立于 2D / 3D 渲染。"""
from verify_pipeline import env


def try_music_command(content):
    """识别「点歌/来一首/放一首/播放」命令，返回歌名关键词或 None。"""
    import re
    m = re.search(r"^(?:点歌|来一首|放一首|放首歌|播放)\s*[:：]?\s*(.+)$", content)
    if m:
        kw = m.group(1).strip()
        if kw and len(kw) <= 40:
            return kw
    return None


_NETEASE_LOGGED = False


def netease_login():
    """用 MUSIC_U cookie 登录网易云（VIP 解锁完整版/高音质）。幂等。"""
    global _NETEASE_LOGGED
    if _NETEASE_LOGGED:
        return
    mu = env("NETEASE_MUSIC_U", "").strip()
    if not mu:
        return
    try:
        from pyncm import apis
        apis.login.LoginViaCookie(MUSIC_U=mu)
        _NETEASE_LOGGED = True
    except Exception:
        pass


def parse_lrc(lrc_text):
    """解析 LRC 文本为 [{t:秒, text:文本}]，跳过元信息行(作词/作曲等)。"""
    import re
    META = re.compile(r"^(作词|作曲|编曲|制作人|录音|混音|母带|监制|和声|吉他|贝斯|鼓|键盘|钢琴|弦乐|小提琴|大提琴|箫|二胡|演唱|出品|发行|OP|SP|版权|企划|统筹|配唱|人声|音乐总监|艺人)\s*[:：]")
    lines = []
    for line in (lrc_text or "").splitlines():
        for m in re.finditer(r"\[(\d+):(\d+(?:\.\d+)?)\]([^\[]*)", line):
            mm = int(m.group(1))
            ss = float(m.group(2))
            txt = m.group(3).strip()
            if txt and not META.match(txt) and ":" not in txt and "：" not in txt:
                lines.append({"t": mm * 60 + ss, "text": txt})
    lines.sort(key=lambda x: x["t"])
    return lines


def netease_search(query):
    """搜网易云歌曲，返回 {'id','name','artist','url','pic','lyric'} 或 None。"""
    netease_login()
    try:
        from pyncm import apis
        r = apis.cloudsearch.GetSearchResult(query, limit=5)
        songs = (r.get("result") or {}).get("songs") or []
        for s in songs:
            sid = s.get("id")
            name = s.get("name")
            artist = "/".join(a.get("name", "") for a in (s.get("ar") or []) if a.get("name"))
            al = s.get("al") or {}
            pic = (al.get("picUrl") or "").replace("http://", "https://")
            url = None
            try:
                u = apis.track.GetTrackAudio([sid], bitrate=320000, encodeType="aac")
                url = ((u.get("data") or [{}])[0] or {}).get("url")
            except Exception:
                pass
            lyric = []
            if url:
                try:
                    lr = apis.track.GetTrackLyrics(str(sid))
                    lrc_text = ((lr or {}).get("lrc") or {}).get("lyric") or ""
                    lyric = parse_lrc(lrc_text)
                except Exception:
                    pass
            if url:
                return {"id": sid, "name": name, "artist": artist, "url": url, "pic": pic, "lyric": lyric}
        return None
    except Exception:
        return None
