"""启动时验证监听地址与只读令牌。"""

def check_auth_config(b):
    """启动前的鉴权配置检查（失败关闭）。

    默认配置（127.0.0.1）不受影响。一旦绑到非回环地址，必须提供足够长的
    只读令牌，否则拒绝启动——避免用户在不知情的情况下把弹幕流与音频暴露给
    整个局域网。

    即使配了令牌，也要注意：令牌只保护读，写入口已经硬性限定回环。
    """
    if b.BOUND_TO_LOOPBACK:
        return
    if b.ALLOW_UNAUTHENTICATED:
        print("[安全警告] BRIDGE_HOST=%s 已暴露到局域网，且未设置只读令牌。" % b.HOST)
        print("           同网段任何人都能查看弹幕与音频（写入口仍只允许本机）。")
        return
    if not b.BRIDGE_TOKEN:
        raise SystemExit(
            "[配置错误] BRIDGE_HOST=%s 会把服务暴露到局域网，但 BRIDGE_TOKEN 为空。\n"
            "  请二选一：\n"
            "    1) 设置 BRIDGE_TOKEN=<至少 16 位的随机串>，用 ?token= 打开页面\n"
            "    2) 设 BRIDGE_ALLOW_UNAUTHENTICATED=1 明确接受只读暴露\n"
            "  注：写入口 /api/danmaku 无论如何都只允许本机访问，\n"
            "  所以远程无法替你消耗 API 额度。" % b.HOST)
    if len(b.BRIDGE_TOKEN) < b.MIN_TOKEN_LEN:
        raise SystemExit(
            "[配置错误] BRIDGE_TOKEN 太短（%d 位），暴露到局域网时至少需要 %d 位。"
            % (len(b.BRIDGE_TOKEN), b.MIN_TOKEN_LEN))
    if not b.BRIDGE_TOKEN.isascii():
        # http.server 用 latin-1 解码请求头，而 parse_qs 按 UTF-8 解码 query。
        # 非 ASCII 令牌在这两条路径上会得到不同的字节序列，导致「URL 里能用、
        # 请求头里 401」这种诡异现象。强制 ASCII 后两种编码等价，两条路径天然
        # 一致——不要去分路径 encode，那会留下只在一条路径正确的分支。
        raise SystemExit(
            "[配置错误] BRIDGE_TOKEN 只能包含 ASCII 字符（建议用随机串）。\n"
            "  生成：python -c \"import secrets;print(secrets.token_urlsafe(24))\"")
