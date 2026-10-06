# RAW2LEICA v0.3.0

发布日期：2026-10-07（Asia/Shanghai）。本版是用于 Leica FOTOS 兼容性实验的桌面工具，尚处于早期测试阶段。

## 下载与安装

- `RAW2LEICA-0.3.0-macos-arm64.zip`：macOS 26.0+ / Apple Silicon 应用，解压后打开 `RAW2LEICA.app`。已包含 Python、PySide6 / Qt、rawpy / LibRaw、Pillow、NumPy 和 ExifTool；ExifTool 仍依赖 macOS 系统 Perl。旧版 macOS 请使用源码安装方式，应用包的最低版本受本次 Python 构建影响。
- `raw2leica-0.3.0-py3-none-any.whl`：Python 3.11+ 安装包，需自行安装 ExifTool；执行 `pip install <wheel文件>` 后运行 `raw2leica`。
- `raw2leica-0.3.0.tar.gz`：Python 源码发行包。完整仓库、构建脚本与文档也可通过 GitHub 的 Source code 下载。
- `SHA256SUMS.txt`：上述三个文件的 SHA-256 校验值。

macOS 应用为本地 ad-hoc 签名，未使用 Developer ID 签名、未经过 Apple 公证。Gatekeeper 可能拦截下载的应用；确认下载来源后，在系统设置 → 隐私与安全性中允许打开。本次仅在构建机器验证，不宣称覆盖所有 macOS 版本，也不提供 Intel 或 Windows 应用包。

## 已实现

- RAW / JPEG → sRGB JPEG → Leica 身份 EXIF，默认 M11-P，另有 M11、M11-D、M11 Monochrom、Q3、Q3 43、SL3、SL3-S、M EV1。
- 八项基础调整：曝光、对比度、亮度、饱和度、高光、阴影、白色、黑色；缓存预览、RGB 直方图、剪裁警告和原图对比。
- 逐张或批量裁剪、常用比例、长边限制、每张独立参数、批量相对增减、重新导出。
- 文件 / 文件夹导入、去重、后台批量转换、错误隔离、重试和安全取消；原文件保持不变，成片重名自动编号。
- EXIF 白名单与隐私开关，输出 JPEG 完整性、EXIF / ICC 回读验证，每张成片附带 JSON 报告。
- 同图六机型测试：一次开发生成像素一致、身份不同的 JPEG。

## 本次验证

- 34 项自动测试通过，覆盖核心转换、基础调整、裁剪、批量参数及真实 Qt 界面任务流程。
- `pip check` 通过；Python wheel、源码包和 macOS 应用构建完成。
- 独立应用冒烟检查使用包内 ExifTool，完成临时 JPEG 转换及 Leica 身份、尺寸和 JSON 回读检查，并加载 Qt、rawpy 和全部九个机型配置。
- 构建和冒烟检查环境：macOS 27.0 arm64、Python 3.13.15、PySide6 6.11.2、rawpy 0.27.1、Pillow 12.3.0、NumPy 2.5.3、PyInstaller 6.22.3；最低 macOS 26.0 来自内置 Python 二进制的部署目标，未在 macOS 26 实机运行验证。
- 历史 Sony ARW 样本及曝光 / 裁剪验证记录见 [validation.md](validation.md)。本次打包验证不等于重新完成真实 RAW 主观画质评估。

## 当前限制与待验证事项

- 本工具不直接应用 Leica Looks。EXIF 校验成功不代表当前 Leica FOTOS 一定识别或启用 Looks；此前 Q3 成功来自用户实测，其他身份仍需在 App 中测试。
- 未执行畸变、暗角或色差的镜头像素校正；只处理镜头元数据。需要校正时，请先在 RAW 开发软件完成校正后导入 JPEG。
- DNG 可以作为输入，尚未实现 DNG 输出；不模拟 Leica MakerNote、序列号、传感器数据或 C2PA 签名。Monochrom 身份不会自动转为黑白。
- 新的 RAW 中性亮度与基础调整算法仍需更多真实场景主观评估；不是 Capture One 或相机厂商专有色彩引擎，已经丢失的细节不能保证恢复。
- Canon、Nikon、Fujifilm 等更多真实 RAW 样本、500 张压力测试、Windows 和 Intel Mac 尚未验证。扩展名可导入不保证具体相机能被当前 LibRaw 解码。
- 队列、每张调整及裁剪在退出后不保留。取消需等待当前 LibRaw / ExifTool 步骤结束，已经完成的文件保留。

GPS 等保留字段也会写入输出旁的 JSON，分享成片和报告前请根据需要关闭保留选项。
