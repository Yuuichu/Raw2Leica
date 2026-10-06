# 曝光调整设计（v0.3）

资料核对与实现日期：2026-10-06。

## 参考与取舍

| 参考 | 查到的行为 | 本项目采用 |
|---|---|---|
| Capture One 官方 Exposure 文档 | ±4 EV；方向键 0.1 EV；Shift + 方向键 1 EV | 相同范围与键盘步长 |
| Capture One 官方 Speed Edit 文档 | 按住工具快捷键，拖动、滚轮或方向键操作；快捷键 + 空格归零 | 点击预览后按住 Q 拖动/滚动，Q + 空格归零 |
| RawTherapee `rtgui/tools/tonecurve.cc` | Exposure compensation 的 Adjuster 范围 −5 到 +12，步长 0.05；以 0 为中心设置 logScale | 参考数值框与滑块组合，不照搬范围或非线性刻度 |
| darktable `src/iop/exposure.c` | 通过曝光到白点的指数关系计算线性增益 | 独立实现 `gain = 2 ** EV`，先施加增益，再编码 sRGB |

本项目额外提供 0.01 EV 数值输入、Alt 精调（键盘 0.01 EV，鼠标拖动灵敏度为通常的十分之一）、双击滑块归零、空格暂时查看 0 EV、批量相对增减。**0.01 EV 是本项目的控制精度，并非宣称 Capture One 的默认键盘步长。**数值框输入在 Enter、移出焦点或点击“应用曝光”时提交，防止输入负号或小数过程中反复渲染。

参考的是公开文档和数学方法；Qt 控件与处理路径为独立实现，没有复制 GPL 项目源码，没有集成 Capture One 的处理引擎。

## 处理路径与精度

RAW：LibRaw 相机白平衡 / AHD 去马赛克 → 16 位线性 RGB（sRGB 原色）→ 固定中性基准增益 → `2 ** EV` → sRGB 编码 → 用户裁剪 → 长边缩小 → JPEG / Leica EXIF。

中性基准从整张开发图像的 RGB 通道第 99 百分位估算，取最大通道白点并限制增益。它在用户裁剪和曝光调整前计算一次，不会因拖动滑块而重新自动测光。v0.2 使用 LibRaw 默认自动亮度和默认 gamma；v0.3 使用此固定参考与标准 sRGB 转移函数，**即使 0 EV，RAW 亮度与色调也可能与旧版不同**。它不是 Capture One 的自动曝光或相机厂商色彩配置文件。

JPEG：读取 / 应用方向和 ICC → sRGB 反转移到 16 位线性 RGB → 曝光增益 → sRGB 编码。JPEG 已丢失的高光或暗部信息不能恢复。无曝光调整、无 ICC 转换的 JPEG，在重新压缩前可保持原来的 RGB 像素值。

增益与转移函数以浮点计算生成 65,536 项 LUT，索引 16 位缓存后才量化为最终 8 位 JPEG。0.01 EV 是参数分辨率，最终像素仍受输入精度与 JPEG 量化约束。LibRaw 开发阶段的白平衡 / 色彩转换可能已剪裁通道；本版没有加入高光重建、HDR 高光/阴影滑块或厂商镜头矫正。

## 预览与工效

- 首次完整解码一次 RAW，再生成长边 1500 的线性缓存；拖动滑块不重复解码 RAW。
- 45 ms 合并快速输入；后台最多一个渲染任务；跳过过期结果，显示最新值。首次加载时可继续操作主事件循环。
- 预览与导出共享曝光公式、基准和方向；预览先缩小线性缓存，最终导出则先编码再缩小，所以边缘、细节和剪裁百分比可能有轻微差异。
- RGB 直方图和高光/阴影警告基于当前裁剪后的预览显示值，不是传感器饱和度测量。任一通道 ≥254 为高光警告，全部通道 ≤1 为阴影警告。
- “相对增减”使用首张选中照片的改变量。例如首张 +1.00 改为 +1.30，其余 −0.50 改为 −0.20；超出 ±4 EV 的照片限制到边界并提示。
- 每张照片的曝光值保存在当前队列；退出程序后不保存队列。改值后重新进入等待，可生成新的编号成片，不覆盖旧文件。
- 调整值记录在输出 JSON 的 `exposure_ev`，不把后期调整伪装成拍摄时的 `ExposureBiasValue`；原拍摄补偿按元数据保留开关复制。

## 资料

- [Capture One — Adjusting exposure](https://support.captureone.com/hc/en-us/articles/360002602077-Adjusting-exposure-in-the-Exposure-tool)
- [Capture One — Speed Edit](https://support.captureone.com/hc/en-us/articles/360015629178-Speed-Edit)
- [darktable — exposure.c](https://github.com/darktable-org/darktable/blob/master/src/iop/exposure.c)
- [darktable — Exposure manual](https://docs.darktable.org/usermanual/development/en/module-reference/processing-modules/exposure/)
- [RawTherapee — tonecurve.cc](https://github.com/RawTherapee/RawTherapee/blob/dev/rtgui/tools/tonecurve.cc)
- [rawpy — Params](https://letmaik.github.io/rawpy/api/rawpy.Params.html)（`exp_shift` 范围 0.25–8，即 −2 到 +3 EV，无法单独覆盖 ±4 EV，因此采用共享线性缓存增益）
