# darktable Leica EXIF 导出插件

在独立的 **Leica EXIF** 面板选择机型与规则、启用后处理，再使用 darktable 原生的 **磁盘上的文件 / file on disk** 目标和导出按钮。目录、变量命名模板与重名策略全部使用原生设置。darktable 负责调色、镜头矫正、裁剪和 JPEG/HEIF 编码；插件仅处理导出副本的元数据，不再次压缩。

## 安装与卸载（macOS）

需要启用 Lua 的 darktable、Python 3.11+ 和 ExifTool。先退出 darktable，在项目目录运行：

```sh
.venv/bin/python scripts/install_darktable.py
```

也可用符合版本要求的 `python3`。安装器创建包含 Pillow 与 pillow-heif 的独立环境，并复制元数据后端及机型配置，不需要启动 RAW2LEICA 桌面应用。ExifTool 优先使用系统安装，也支持项目 `.tools/exiftool` 副本（包含库与许可证一起复制）。指定其他安装位置：

```sh
python3 scripts/install_darktable.py --config-dir /path/to/darktable-config --exiftool /path/to/exiftool
```

重启 darktable 后展开“Leica EXIF”和“导出”。导出目标选择“磁盘上的文件 / file on disk”，格式可选择 JPEG 或 HEIF，配置文件可选择 sRGB、Rec.709 等 RGB 色彩空间。首次安装默认关闭 Leica 后处理；设置规则后勾选“启用 Leica EXIF 后处理”。安装器只增补 `luarc` 中带 RAW2LEICA 标记的区块；首次修改已有配置会保留 `luarc.raw2leica-backup`。更新插件时重新运行安装器。升级时若当前目标是旧版 `raw2leica`，自动迁移为 `disk`，备份为 `darktablerc.raw2leica-backup`；保留其他配置和原生路径。后端复制到配置目录，移动仓库不会影响插件，但独立环境仍依赖创建它的 Python 安装。

卸载前退出 darktable，运行：

```sh
python3 scripts/install_darktable.py --uninstall
```

自定义配置目录需在卸载时传入相同 `--config-dir`。卸载移除管理区块、两个 Lua 文件和插件独立环境，保留用户其他配置、已导出照片及日志。

## 导出选项

- 机型：项目现有九种 Leica 身份，默认 M11-P。
- 日期、曝光与焦距、GPS：默认均保留；来源为原照片，不采用 darktable 后续修改的地理位置或元数据。
- 镜头：默认兼容模式，仅写 Leica LensMake；“保留真实镜头”额外复制 LensModel/LensInfo；“移除镜头信息”不写镜头品牌和型号。焦距和光圈仍由曝光开关控制。
- 输出路径：使用原生路径输入框、目录选择器及变量。例如 `$(FILE_FOLDER)/darktable_exported/$(FILE_NAME)_Leica`；无需填写图像扩展名。目录可由 darktable 创建。建议重名策略选择“创建不重复的文件名”；选择覆盖时遵循原生覆盖规则。
- 规则自动持久化，在启用时固定并锁定控件。修改机型/保留选项时，等待当前任务结束，关闭开关、调整、重新启用。原生接口没有批次开始/结束事件，计数按本次启用累计。关闭时普通导出不处理 EXIF；其他存储目标始终不处理。
- **允许 JPEG 和 HEIF/HEIC，以及 Rec.709 等 RGB 色彩空间**。尺寸、质量、位深、色彩处理及编辑历史由 darktable 设置。插件不会转换色彩空间；原 ICC 或 HEIF NCLX 信息保持。JPEG 仍需有效 RGB ICC，HEIF 需 ICC 或 NCLX；损坏色彩信息和未完成方向变换仍拒绝发布。sRGB 写 EXIF ColorSpace=1；其他空间写 65535（Uncalibrated），以 ICC/NCLX 表达真实空间。

最终目录、文件名、扩展名及重名编号由 darktable 决定，插件保留其实际输出路径。每张校验通过的照片旁生成同名 `.jpg.json` / `.heic.json` 等记录（大写扩展名也保留），记录来源、机型、尺寸、规则、元数据及 编码图像数据摘要。开启 GPS 时记录也包含 GPS。

原生存储先写出副本，再调用阻塞的 Lua 后处理事件。插件在私有临时目录清理元数据（保留原 ICC/NCLX 色彩信息）、按白名单复制拍摄参数并写入 Leica 身份，校验通过后替换该导出副本并生成记录。源文件不修改，JPEG/HEIF 图像数据不重新编码。失败删除本次未通过的导出副本及插件记录，保留此前其他成功文件，继续后续照片。

由于原生接口不能把后处理失败传回原生存储的结果，darktable 自身的“已导出”提示不能作为 Leica 校验成功依据；以 Leica 面板成功/失败计数、错误提示及 JSON 的 `metadata_verified` 为准。原生已生成但尚在处理的副本会短暂存在，请在任务完成后取用。使用独立输出目录，避免原生覆盖策略在后处理前覆盖源照片或已有成片。

取消使用 darktable 原有任务取消按钮：已经开始的单张后端调用会完成，后续取消由 darktable 控制。单张调用期间不要关闭并重新启用开关。
日志：`~/Library/Logs/RAW2LEICA/exif-export.log`（自动轮换）。缺少依赖时重新安装或通过 `--exiftool` 修正路径；目录不可写时选择其他目录；HEIF 解码依赖缺失时重新运行安装器。

## 独立后端接口

安装后的命令位于 `~/.config/darktable/raw2leica-runtime/raw2leica-exif`。开发时可用 `python -m raw2leica.exif_export`；常规完整安装还提供 `raw2leica-exif` 命令。

```sh
raw2leica-exif --profiles
raw2leica-exif --check --output-dir /absolute/output
raw2leica-exif --source /absolute/photo.ARW --rendered /absolute/rendered.jpg \
  --output-dir /absolute/output --profile q3 --no-preserve-gps --lens-mode compatible
```

`--preserve-date/exposure/gps` 及各自 `--no-preserve-*` 控制保留字段；默认开启。未指定输出目录时使用默认子目录。成功退出码为 0，失败为 1；标准输出为 JSON，失败说明同时写 stderr 和日志。插件调用时使用 `--native-export --source ... --rendered ...`，直接校验并替换本次原生生成的副本，不传 `--output-dir`。这个入口失败会删除传入的导出副本，只应由导出事件调用；独立处理既有文件请使用上面的普通入口。

独立入口输出扩展名为 JPEG `.jpg` 或 HEIF `.heic`，原生入口保留 darktable 的实际扩展名和命名。HEIF 校验比较编码图像项目、编解码/色彩/变换属性及容器头信息中的显示画幅尺寸，EXIF 中写显示画幅尺寸，避免把编码瓦片尺寸当作实际裁剪尺寸。后端不要求完整解码 HEIF 像素，以兼容 darktable 的 10-bit 无损输出；这项校验不等同于像素解码检查。

`--profiles` 返回九种身份配置，`--check` 检查 ExifTool 与输出目录写入能力。

## 验证边界

本插件不应用 Leica Looks，不输出 DNG，不自动传送手机，不提供任意 EXIF 字段输入或同图多机型按钮。`metadata_verified` 与 `fotos_verified` 分开记录；后者保持 false，因为本地后端无法自动验证手机结果。用户已确认 HEIF + Rec.709 兼容 Leica FOTOS（2026-10-07）；各机型、位深、手机版本和 Looks 的具体结果尚未逐项记录，详见[验证记录](darktable-validation.md)。

官方接口参考：[原生导出事件](https://docs.darktable.org/lua/stable/lua.api.manual/events/intermediate-export-image/)、[Lua 界面](https://docs.darktable.org/usermanual/development/en/lua/building-ui-elements/)。本机测试版本与结果见 [插件验证记录](darktable-validation.md)。
