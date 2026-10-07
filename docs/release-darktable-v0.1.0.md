# RAW2LEICA darktable 插件 v0.1.0

在 darktable 内完成编辑和导出，再自动写入 Leica 身份 EXIF。插件使用原生“磁盘上的文件”导出目标，目录、命名模板、重名规则、裁剪、尺寸和压缩由 darktable 控制；后处理只改元数据，不重新编码图像。

## 下载什么

- **`RAW2LEICA-darktable-0.1.0-macos.zip`**：插件安装包，包含 Lua 插件、轻量 Python 后端、九种机型配置、安装器和文档。无需安装 RAW2LEICA 桌面应用。
- **`SHA256SUMS.txt`**：安装包的 SHA-256 校验值。
- GitHub 自动生成的 Source code：完整项目源码，包含桌面应用，通常不必下载。

这是 macOS 首版插件发布，独立于桌面应用 v0.3.0。安装包不内置 Python、darktable 或 ExifTool，也不是 `.app`；首次安装会联网下载 Pillow 和 pillow-heif。

## 安装与使用

需要支持 Lua 的 darktable、Python 3.11+ 和 ExifTool。已在 darktable `5.7.0+1183~gabc10290a7` / Lua API `9.8.0` 验证；其他版本尚未实机验证。若使用 Homebrew，可先运行：

```sh
brew install python@3.13 exiftool
```

退出 darktable，解压安装包，在终端进入解压目录后运行：

```sh
python3 scripts/install_darktable.py
```

若 `python3` 版本低于 3.11，可改用 Homebrew Python：

```sh
"$(brew --prefix python@3.13)/bin/python3.13" scripts/install_darktable.py
```

重启 darktable → 展开 **Leica EXIF** → 选择机型和保留规则 → 勾选 **启用 Leica EXIF 后处理** → 原生导出目标选择 **磁盘上的文件** → 导出。

支持 JPEG / HEIF、10-bit HEIF 和 Rec.709 等 RGB 配置文件；压缩质量及色彩空间使用 darktable 原有设置。照片旁的 JSON 中 `metadata_verified: true` 表示元数据校验通过。用户已实测确认 **HEIF + Rec.709 兼容 Leica FOTOS**；未将其推定为全部机型、位深和 FOTOS 版本均兼容。

安装器保留已有 `luarc` 配置并创建首次备份。升级时退出 darktable，重新执行安装命令。卸载同样先退出 darktable：

```sh
python3 scripts/install_darktable.py --uninstall
```

默认配置目录为 `~/.config/darktable`；自定义位置用 `--config-dir`，安装和卸载须使用同一路径。ExifTool 可通过 `--exiftool` 指定绝对路径。

## 本版功能与验证

- 九种 Leica 身份，默认 M11-P；日期、曝光/焦距、GPS 开关；兼容、保留真实镜头、移除镜头三种模式。
- 保存选项，启用期间固定规则；逐张校验和成功/失败计数，原照片保持不变。
- 清除原相机身份、非白名单元数据、MakerNote 和 C2PA，保留实际 ICC / HEIF NCLX 色彩信息。
- 97 项 Python 测试通过；真实 darktable Lua 运行时、JPEG 原生 GUI 导出、RAW/JPEG 的 CLI 渲染与后端处理已验证。HEIF GUI 已加载，但本次自动点击未确认新 HEIF 成片完成。
- HEIF 比较编码图像项目与编解码、色彩和变换属性，并检查容器中的显示画幅尺寸；不要求完整像素解码，以兼容 10-bit 无损输出。
- 发布 ZIP 经解压后的独立安装、后端检查、JPEG 转换及卸载验证。

## 使用边界

插件不直接应用 Leica Looks，不输出 DNG，也不传送照片到手机。自动报告中的 `fotos_verified` 保持 false，它不代表否定用户实测结果。

建议使用独立输出目录和“创建不重复的文件名”。后处理失败会删除本次未通过的导出副本；darktable 原生“已导出”提示不能表示 Leica 校验成功，以 Leica 面板和 JSON 记录为准。已开始的单张处理会在取消后完成。启用 GPS 时，JSON 记录也包含 GPS。

完整说明：[安装与使用](https://github.com/Yuuichu/Raw2Leica/blob/darktable-v0.1.0/docs/darktable-plugin.md) · [验证记录](https://github.com/Yuuichu/Raw2Leica/blob/darktable-v0.1.0/docs/darktable-validation.md) · [反馈问题](https://github.com/Yuuichu/Raw2Leica/issues)
