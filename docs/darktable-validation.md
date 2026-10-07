# darktable 插件验证记录

日期：2026-10-07。平台：本机 macOS。darktable：`5.7.0+1183~gabc10290a7`；Lua API：`9.8.0`。这是开发版本，本记录不代表其他 darktable 版本均已验证。

## 自动验证

完整 Python 测试：`75 passed`，包含原有桌面应用测试及 41 项新增后端测试。覆盖九种机型、三种镜头模式、日期/曝光/GPS 开关、源文件与临时 JPEG 不变、ICC 字节一致、JPEG 编码数据及解码像素一致、实际尺寸与方向、中文/空格/引号/命令字符路径、重名与并发、缺少 ExifTool、无效/只读输出目录、校验/发布失败与取消后清理、JSON CLI 和轻量依赖隔离。

本机 JPEG 批次暴露的手机 EXIF 数值格式化问题已修复：使用 ExifTool 原始数值复制，避免将 f/1.78 格式化为 f/1.8；对复制的浮点拍摄字段允许仅相对 `1e-6` / 绝对 `1e-10` 的舍入误差。身份、尺寸、方向和整数标志仍精确比对。回归测试同时确认真实字段偏差会被拒绝。

新增 `tests/lua/storage_contract.lua` 在本机 darktable 的真实 Lua 运行时通过；以事件与界面替身验证原生 disk 过滤、关闭开关时不处理、启用时固定规则、单张失败后继续、成功/失败累计、依赖检查、脚本停用/重启及延迟面板注册。Python 新增 8 项原生后处理测试覆盖实际文件名保持、源文件/硬链接/符号链接保护、失败清理、非插件报告保护和 CLI。

安装器额外在含中文、空格、单引号与 `$` 的独立配置目录完成安装、调用及卸载；确认原 `luarc` 文本恢复、备份保留。源代码发布包已验证包含插件 Lua 文件和安装脚本。

## 原生导出接入实测（当前版本）

- 安装升级并正常重启，旧 `raw2leica` 存储目标迁移为 `disk`；原生路径、JPEG、质量 100、4:4:4、sRGB 与 Leica 规则保留。
- 独立 Leica EXIF 面板可见，启用后机型/保留控件锁定，关闭后恢复可编辑。
- 路径 `/…/outputs/darktable-native/中文 空格/$(FILE_NAME)_native_$(SEQUENCE)` 通过原生控件设置，真实 RAW `YUC00714.ARW` 导出为 `YUC00714_native_0001.jpg`，实际尺寸 10008×6672，身份 LEICA M11-P、记录校验通过。
- 再次导出同一照片，原生重名策略生成 `YUC00714_native_0001_01.jpg` 及对应记录；没有重命名为后端独立入口的 `_Leica` 模式。
- 关闭开关后导出 `_02.jpg`，保持 Sony ILCE-7RM6 身份，不产生插件报告。
- 两张 RAW 原生批次完成 `YUC00714_native_0001_03.jpg`、`YUC00720_native_0002.jpg`，均通过校验，面板成功计数为 2。
- 在下一输出路径旁放入非插件 JSON，导出失败计数变为 1；该 JSON 保留，未通过 JPEG 删除，此前成功文件不变。日志提供修改命名模板的操作建议。
- 插件在原生阻塞导出事件中处理文件；原生任务的完成提示不能报告后处理失败。以 Leica 面板、错误提示及报告为准。取消沿用原生任务，已开始的一张处理会完成；本次优化没有重新进行 GUI 点击取消测试。
- 迁移函数以独立临时配置验证，仅改旧存储目标行，其他设置、中文路径和首次备份保持。
- 测试结束恢复原生 `$(FILE_FOLDER)/darktable_exported/$(FILE_NAME)` 路径；重启后确认 disk、JPEG/sRGB、Leica 启用状态及规则持久化，界面可直接使用。

## 初版独立存储的历史验证

初版在同一 darktable 版本完成单张/两张 RAW、八张手机 JPEG GUI 导出、控件持久化和取消测试；这些记录证明共享 EXIF 后端可用于真实渲染照片，不代表当前原生事件的批次接口测试。darktable-cli 也分别渲染真实 Sony RAW 与 JPEG，生成 Q3 / M11-P 成片；实际 sRGB ICC 被接受且保留。

本机样片集中有一张 darktable 无法解码的损坏 DNG，没有进入 EXIF 后端。未使用带真实 C2PA 签名的专门样片做人工验证；自动测试确认处理结果无 MakerNote/C2PA 残留。

## FOTOS 结果

用户反馈“实测是可以的”。记录为用户确认总体可用；尚未提供分别对应 Q3、M11-P 的手机/FOTOS 版本及 Looks 导入、选择和保存细节，不作逐项推断。本地报告的 `fotos_verified` 仍为 false，后端仅能自动验证 EXIF。

已有样片位于忽略于 Git 的 `outputs/fotos-test`：RAW / M11-P `m11p/YUC00713_Leica.jpg`、RAW / Q3 `q3/YUC00713_Leica.jpg`、JPEG / Q3 `q3/sony-preview_Leica.jpg`，均通过 EXIF 校验。

## HEIF / Rec.709 限制放开

同日更新后，插件允许 JPEG、HEIF/HEIC 和 Rec.709 等 RGB 色彩空间，安装器添加 `pillow-heif`。本机插件及独立后端已升级；界面显示“JPEG / HEIF；保留导出色彩空间”，保留用户当前 HEIF 10-bit、无损、Rec709 RGB 和原生输出路径设置。

完整 Python 回归测试：`97 passed`；HEIF / Rec.709 专项：`23 passed`。新增测试覆盖九种身份的 HEIF ICC/NCLX、Rec.709 JPEG、HEIF 原生扩展名及记录、10-bit、镜头数值舍入和无需像素解码的校验。比较 HEIF 编码图像项目及完整图像属性，确认元数据操作没有改变编码数据、ICC/NCLX、裁剪与变换属性。HEIF 后端仅验证容器头中的显示尺寸，不要求完整像素解码；可解码样片另比较解码像素。Rec.709 ICC 测试夹具由本机 darktable 导出 JPEG 后提取。

实际 darktable-cli 分别渲染 JPEG / Rec.709、HEIF / Rec.709 和真实 Sony RAW 的 10-bit 无损 HEIF / Rec.709，再经原生后处理入口处理，均通过元数据校验并保留编码图像。样片及记录在 `outputs/heif-test`。更新后的 GUI 插件已加载，但本次自动点击未确认新的 HEIF 导出完成，不将其计为 GUI 成片验收。

用户随后明确确认“HEIF + 709 是兼容 Leica FOTOS 的”，记录为用户实测确认该组合兼容（2026-10-07）。未提供手机、FOTOS 版本、位深及 Looks 选择和保存的逐项结果，不扩大为所有配置均已验证。自动报告的 `fotos_verified` 仍为 false，因为它表示后端自动验证状态。
