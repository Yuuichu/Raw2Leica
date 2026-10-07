-- Leica EXIF processing for darktable's native file-on-disk storage.
local dt = require "darktable"
local config = require "raw2leica_config"
local name = "raw2leica"
local prefs = dt.preferences
local modes = {"compatible", "original", "remove"}
local function quote(value)
  return "'" .. tostring(value):gsub("'", "'\\''") .. "'"
end
local function preference(key, kind, label, default)
  prefs.register(name, key, kind, label, label, default)
  return prefs.read(name, key, kind)
end
local function save(key, kind, value) prefs.write(name, key, kind, value) end
local function run(arguments)
  local capture = os.tmpname()
  local command = quote(config.backend)
  for _, arg in ipairs(arguments) do command = command .. " " .. quote(arg) end
  local ok, status = pcall(dt.control.execute, command .. " > " .. quote(capture) .. " 2>&1")
  local handle = io.open(capture, "r")
  local message = handle and handle:read("*a") or "无法读取后端结果"
  if handle then handle:close() end
  os.remove(capture)
  return ok and status == 0, message
end
local model_id = preference("profile", "string", "Leica 机型", "m11p")
local model = dt.new_widget("combobox"){label="Leica 机型"}
for i, p in ipairs(config.profiles) do
  model[i] = p.label
  if p.id == model_id then model.selected = i end
end
if model.selected < 1 then model.selected = 1 end
model.changed_callback = function(w) save("profile", "string", config.profiles[w.selected].id) end
local lens_id = preference("lens_mode", "string", "镜头信息", "compatible")
local lens = dt.new_widget("combobox"){label="镜头信息", "兼容模式", "保留真实镜头", "移除镜头信息"}
for i, value in ipairs(modes) do if value == lens_id then lens.selected = i end end
if lens.selected < 1 then lens.selected = 1 end
lens.changed_callback = function(w) save("lens_mode", "string", modes[w.selected]) end
local function checkbox(key, label)
  local value = preference(key, "bool", label, true)
  return dt.new_widget("check_button"){label=label, value=value,
    clicked_callback=function(w) save(key, "bool", w.value) end}
end
local date = checkbox("date", "保留拍摄日期")
local exposure = checkbox("exposure", "保留曝光与焦距")
local gps = checkbox("gps", "保留 GPS")
local enabled_value = preference("native_enabled", "bool", "启用 Leica EXIF 后处理", false)
local enabled = dt.new_widget("check_button"){label="启用 Leica EXIF 后处理", value=enabled_value}
local status = dt.new_widget("label"){label="已关闭；使用原生 file on disk 导出"}
local active, success, failed = nil, 0, 0
local controls = {model, date, exposure, gps, lens}
local function arm()
  if enabled.value then
    local ok, message = run({"--check"})
    if not ok then
      enabled.value = false
      dt.print_error("Leica EXIF：" .. message)
    end
  end
  if enabled.value then
    active = {profile=config.profiles[model.selected].id, lens=modes[lens.selected],
      date=date.value, exposure=exposure.value, gps=gps.value}
    success, failed = 0, 0
    status.label = "已启用：JPEG / HEIF；保留导出色彩空间"
  else
    active = nil
    status.label = "已关闭；使用原生 file on disk 导出"
  end
  -- Native Lua events have no batch-start callback. Freeze the rules while armed
  -- instead of letting changes halfway through an export affect later images.
  for _, control in ipairs(controls) do control.sensitive = not enabled.value end
  save("native_enabled", "bool", enabled.value)
end
enabled.clicked_callback = arm
local widget = dt.new_widget("box"){orientation="vertical", enabled, model, date, exposure, gps, lens,
  dt.new_widget("label"){label="目录、文件名和重名规则：在原生导出面板设置"}, status}
local function process(event, image, filename, format, storage)
  local options = active
  if not options or not storage or storage.plugin_name ~= "disk" then return end
  local arguments = {"--native-export", "--source", image.path .. "/" .. image.filename,
    "--rendered", filename, "--profile", options.profile, "--lens-mode", options.lens}
  for _, key in ipairs({"date", "exposure", "gps"}) do
    arguments[#arguments+1] = (options[key] and "--preserve-" or "--no-preserve-") .. key
  end
  -- The backend also rejects unsupported formats or invalid colour metadata, removes failed native
  -- copies, and guards against processing the original image or its hard links.
  local ok, message = run(arguments)
  if ok then
    success = success + 1
    dt.print("Leica EXIF 已校验：" .. filename)
  else
    failed = failed + 1
    dt.print_error("Leica EXIF 失败：" .. image.filename .. "\n" .. message)
  end
  status.label = string.format("本次启用：成功 %d，失败 %d", success, failed)
end
local registered, installed, deferred = false, false, false
local function register_event()
  if not registered then
    dt.register_event(name, "intermediate-export-image", process)
    registered = true
  end
end
local function install_panel()
  if installed then return end
  dt.register_lib(name, "Leica EXIF", true, false,
    {[dt.gui.views.lighttable]={"DT_UI_CONTAINER_PANEL_RIGHT_CENTER", 99},
     [dt.gui.views.darkroom]={"DT_UI_CONTAINER_PANEL_RIGHT_CENTER", 99}}, widget, nil, nil)
  installed = true
end
local function ensure_panel()
if installed then dt.gui.libs[name].visible = true; return end
if dt.gui.current_view().id == "lighttable" then install_panel()
elseif not deferred then
  deferred = true
  dt.register_event(name, "view-changed", function(event, old_view, new_view)
    if new_view.id == "lighttable" then
      install_panel()
      dt.destroy_event(name, "view-changed")
      deferred = false
    end
  end)
end
end
ensure_panel()
register_event()
arm()
local function destroy()
  active = nil
  if registered then dt.destroy_event(name, "intermediate-export-image"); registered = false end
  if deferred then dt.destroy_event(name, "view-changed"); deferred = false end
  if installed then dt.gui.libs[name].visible = false end
end
local function restart()
  ensure_panel()
  register_event()
  arm()
end
return {metadata={name="Leica EXIF", purpose="Leica metadata with native disk export", author="RAW2LEICA"},
  destroy=destroy, restart=restart, show=restart, destroy_method="hide"}
