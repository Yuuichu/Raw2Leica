# RAW2LEICA darktable 插件 v0.1.0

在 darktable 中编辑照片，导出时自动写入 Leica 身份 EXIF，方便将成片导入 Leica FOTOS。**HEIF + Rec.709 已获用户实测兼容确认。**

## 好处

- **操作都在 darktable 内完成**：沿用原生导出目录、命名、尺寸和压缩设置。
- **不再次压缩**：支持 JPEG / HEIF，保留导出的色彩信息，原照片保持不变。
- **规则可选、结果可查**：九种 Leica 身份，默认 M11-P；可选择保留日期、曝光、GPS 和镜头信息，每张成片附带校验记录。

## 安装（macOS）

下载 **`RAW2LEICA-darktable-0.1.0-macos.zip`** 并解压。需要支持 Lua 的 darktable、Python 3.11+ 和 ExifTool，无需安装 RAW2LEICA 桌面应用。

退出 darktable，在终端进入解压目录后运行：

```sh
# 已有依赖可跳过第一行
brew install python@3.13 exiftool
"$(brew --prefix python@3.13)/bin/python3.13" scripts/install_darktable.py
```

已有 Python 3.11+ 也可直接运行 `python3 scripts/install_darktable.py`。安装器自动配置轻量后端，保留已有 darktable 配置。

## 使用

1. 重启 darktable，展开 **Leica EXIF**，选择机型和保留规则。
2. 勾选 **启用 Leica EXIF 后处理**。
3. 导出目标选择 **磁盘上的文件**，设置 JPEG / HEIF、目录与质量，点击导出。
4. 查看 Leica 面板的成功计数，再将成片导入 FOTOS。

建议选择“创建不重复的文件名”。更新时退出 darktable，重新运行安装命令；卸载使用 `python3 scripts/install_darktable.py --uninstall`。

[完整使用说明](https://github.com/Yuuichu/Raw2Leica/blob/main/docs/darktable-plugin.md) · [验证记录](https://github.com/Yuuichu/Raw2Leica/blob/main/docs/darktable-validation.md) · [反馈问题](https://github.com/Yuuichu/Raw2Leica/issues)
