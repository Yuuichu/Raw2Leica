-- Native-export hook contract, run inside darktable's actual Lua runtime.
local real_dt, real_config = package.loaded.darktable, package.loaded.raw2leica_config
local saved, widgets, commands, messages, codes, events = {}, {}, {}, {}, {}, {}
local mock = {
  preferences = {
    register=function(_, key, _, _, _, value) if saved[key] == nil then saved[key] = value end end,
    read=function(_, key) return saved[key] end,
    write=function(_, key, _, value) saved[key] = value end,
  },
  new_widget=function(kind) return function(values)
    if kind == "combobox" then values.selected = -1 end
    widgets[#widgets+1] = values
    return values
  end end,
  control={execute=function(command)
    commands[#commands+1] = command
    return table.remove(codes, 1) or 0
  end},
  print=function(message) messages[#messages+1] = message end,
  print_error=function(message) messages[#messages+1] = message end,
  gui={views={lighttable={}, darkroom={}}, libs={}, current_view=function() return {id="lighttable"} end},
  register_event=function(_, event, callback) events[event] = callback end,
  destroy_event=function(_, event) events[event] = nil end,
  register_lib=function(_, _, _, _, _, widget) mock_widget = widget end,
}
mock.gui.libs.raw2leica = {visible=true}
package.loaded.darktable = mock
package.loaded.raw2leica_config = {
  backend="/tmp/backend '特殊", profiles={{id="m11p", label="M11-P"},{id="q3", label="Q3"}},
}
local plugin = dofile("darktable/raw2leica.lua")
local image = {path="/tmp/目录 空格", filename="photo '$(touch HACKED).ARW"}
local process = events['intermediate-export-image']
local model, status, enabled = widgets[1], mock_widget[8], mock_widget[1]
process(nil, image, "/tmp/native 'name.jpg", nil, {plugin_name="disk"})
assert(#commands == 0) -- disabled by default
assert(model.sensitive and not saved.native_enabled)
enabled.value = true; enabled.clicked_callback(enabled)
assert(saved.native_enabled and not model.sensitive)
local checked = #commands
process(nil, image, "/tmp/native.jpg", nil, {plugin_name="email"})
process(nil, image, "/tmp/native.jpg", nil, nil)
assert(#commands == checked) -- other storage and direct format writes untouched
model.selected = 2 -- mimic a programmatic change after arming
codes = {1, 0}
process(nil, image, "/tmp/native 'name.jpg", nil, {plugin_name="disk"})
process(nil, image, "/tmp/native 'name_01.jpg", nil, {plugin_name="disk"})
assert(status.label == "本次启用：成功 1，失败 1")
assert(commands[#commands]:find("'\\''", 1, true))
assert(commands[#commands]:find("'m11p'", 1, true)) -- frozen enabled-session rules
assert(commands[#commands]:find("'--native-export'", 1, true))
assert(not commands[#commands]:find("--output-dir", 1, true)) -- native path is passed unchanged
plugin.destroy()
assert(not events['intermediate-export-image'] and not mock.gui.libs.raw2leica.visible)
plugin.restart()
assert(events['intermediate-export-image'] and mock.gui.libs.raw2leica.visible)
enabled.value = false; enabled.clicked_callback(enabled)
assert(model.sensitive)
codes = {1}
enabled.value = true; enabled.clicked_callback(enabled)
assert(not enabled.value and not saved.native_enabled) -- dependency check fails closed
plugin.destroy()
mock.gui.current_view = function() return {id="darkroom"} end
mock_widget = nil
local deferred_plugin = dofile("darktable/raw2leica.lua")
assert(events['view-changed'] and not mock_widget)
deferred_plugin.destroy()
assert(not events['view-changed']) -- hidden script cannot install a delayed panel
deferred_plugin.restart()
assert(events['view-changed'])
events['view-changed'](nil, {id="darkroom"}, {id="lighttable"})
assert(mock_widget and not events['view-changed'])
deferred_plugin.destroy()
package.loaded.darktable, package.loaded.raw2leica_config = real_dt, real_config
print("RAW2LEICA native export contract passed")
