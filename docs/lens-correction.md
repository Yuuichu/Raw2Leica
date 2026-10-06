# 镜头校正如何接入

核查日期：2026-10-06（Asia/Shanghai）。本版尚未实现镜头像素校正；界面“镜头 EXIF”下拉框仅控制输出元数据。

## 当前样本的实际情况

用户此前提供的 `YUC00574.ARW` 使用 `FE 20-70mm F4 G`。
ExifTool 从原始文件中读到了：

- `DistortionCorrParams`（畸变参数）
- `ChromaticAberrationCorrParams`（横向色差参数）
- `VignettingCorrParams`（暗角参数）

这证明该文件内存在相应参数，但不证明现有 rawpy 后处理已经应用它们，也不证明任何特定外部软件版本均能处理这台机身。当前项目的 `develop()` 只调用 rawpy 后处理，未解析或应用这些厂商参数。

## 现在可用的路径

先用支持这份 RAW 的开发软件执行镜头校正，输出 sRGB JPEG，再导入 RAW2LEICA 做尺寸、裁剪和 Leica EXIF。

例如 darktable 的 lens correction 模块提供：

- embedded metadata：源 RAW 存在且受支持的内嵌参数时可用。
- Lensfun database：使用外部镜头校准数据库。
- corrections done：确认本次实际应用了畸变、色差、暗角中的哪些项。

应检查实际支持状态；找不到配置时不要把未校正误报成校正完成。
[darktable 官方说明](https://docs.darktable.org/usermanual/development/en/module-reference/processing-modules/lens-correction/)

## 推荐的产品接入顺序

1. 优先接入可处理 Sony 内嵌校正参数的成熟 RAW 开发引擎，验证 FE 20–70mm 样本。该文件已有厂商参数，值得先验证这条路径。
2. 加入 Lensfun 作为可选校准数据库，以真实源相机、镜头、焦距、光圈、对焦距离匹配配置。多个候选时让用户选择；匹配失败时明确提示。
3. 按照片显示识别到的镜头、参数来源、实际执行的校正项，并在导出报告记录。JPEG 输入默认不再自动校正，避免已校正成片重复处理。

建议的完整顺序：

```text
读取真实相机 / 镜头 / 焦距 / 光圈 / 距离 / 内嵌参数
→ RAW 开发与早期镜头校正
→ 色彩转换与色调处理
→ 方向与用户裁剪
→ 缩小、编码 sRGB JPEG
→ 改写 Leica 身份 EXIF
→ 文件与元数据校验
```

**匹配的是原始 Sony 镜头，而非导出时的 M11-P 身份。**
畸变校正可能改变边缘范围，所以应在用户裁剪前完成，并使用同一引擎产生裁剪预览。

Lensfun 官方要求色差与暗角等校正在足够早的线性 RGB 阶段执行；直接在当前 8-bit sRGB JPEG 上补做暗角校正，可能造成角落过亮。因此不能只把 Lensfun 作为当前 `develop()` 后的普通滤镜调用。
[Lensfun 官方架构说明](https://lensfun.github.io/manual/latest/basearch.html)

另：本次查阅 Lensfun 上游 `data/db/mil-sony.xml` 没有找到 `20-70`；这仅是该数据库文件的检查结果，不能据此断言所有软件或版本均无此镜头配置。
[核查的数据库文件](https://github.com/lensfun/lensfun/blob/master/data/db/mil-sony.xml)
