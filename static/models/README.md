# 自定义模型

将自包含的 `.vrm` / `.glb` 放在本目录，然后修改 `config/avatar3d.json`：

```json
{"url": "/static/models/your-avatar.glb"}
```

只改原配置中的 `url`，保留 `morphs` 与 `bones`。刷新页面与 OBS 浏览器源即可。

模型默认不进入 Git。此目录与其他 static 资源一样可被访问，不要放密钥或私密资料。
模型绑定、形态键和导出要求见 [3D 使用说明](../../docs/3D使用说明.md)。
