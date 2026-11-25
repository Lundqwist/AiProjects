-- The lua pack's build check for a WoW addon, run with LuaJIT (Lua 5.1, like the game):
--
--   luajit check.lua <path to the addon's .toc>
--
-- For every .lua file the .toc loads (or every .lua file in the folder when it lists none):
--   1. syntax, as Lua 5.1 parses it (LuaJIT refuses //, &, |, ~, <<, >>)
--   2. Lua 5.2+ features WoW doesn't have and LuaJIT does accept: goto, ::labels::, and library calls such as
--      table.unpack, table.pack, utf8.*, math.type, string.pack
--   3. accidental globals: a global set by the addon that isn't one of its SavedVariables, a SLASH_ or BINDING_
--      name, a name that starts with the addon's own name, or one it lists in a "-- globals: A, B" comment
--
-- Prints "file:line: message" per problem and exits 1 when there is any, so the Runner reports them.
-- Needs nothing but LuaJIT (jit.util reads the compiled bytecode for global reads and writes).

local toc = arg[1]
if not toc then
  io.stderr:write("usage: luajit check.lua <addon .toc>\n")
  os.exit(2)
end

local jutil = require("jit.util")
local vmdef = require("jit.vmdef")
local bit = require("bit")

local dir = toc:match("^(.*)[/\\][^/\\]+$") or "."
local addon = toc:match("([^/\\]+)%.toc$") or ""
local problems = {}

local function problem(file, line, msg)
  problems[#problems + 1] = string.format("%s:%s: %s", file, line or "?", msg)
end

local function read(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local s = f:read("*a")
  f:close()
  return s
end

-- the .toc: files in load order, SavedVariables
local tocText = read(toc)
if not tocText then
  io.stderr:write("can't read " .. toc .. "\n")
  os.exit(2)
end
local files, allowed = {}, {}
for line in (tocText .. "\n"):gmatch("([^\r\n]*)\r?\n") do
  local key, value = line:match("^##%s*([%w%-]+)%s*:%s*(.-)%s*$")
  if key and key:match("^SavedVariables") then
    for name in value:gmatch("[%w_]+") do allowed[name] = true end
  elseif not line:match("^%s*#") and line:match("%S") then
    local f = line:gsub("^%s+", ""):gsub("%s+$", ""):gsub("\\", "/")
    if f:lower():match("%.lua$") then files[#files + 1] = f end
  end
end
if #files == 0 then  -- no list: every .lua file in the folder (dir /b on Windows, ls elsewhere)
  local p = io.popen(package.config:sub(1, 1) == "\\" and ('dir /b /a-d "' .. dir .. '\\*.lua" 2>nul')
                     or ('ls "' .. dir .. '"/*.lua 2>/dev/null'))
  if p then
    for name in p:lines() do files[#files + 1] = name:match("([^/\\]+)$") end
    p:close()
  end
end

-- source without comments and strings (same length, so positions keep their lines)
local function blank(s)
  return (s:gsub("[^\n]", " "))
end

local function code_only(src)
  local out, i, n = {}, 1, #src
  while i <= n do
    local c = src:sub(i, i)
    if src:sub(i, i + 1) == "--" then
      local eq = src:match("^%-%-%[(=*)%[", i)
      local stop
      if eq then
        local _, e = src:find("]" .. eq .. "]", i, true)
        stop = e or n
      else
        stop = (src:find("\n", i, true) or (n + 1)) - 1
      end
      out[#out + 1] = blank(src:sub(i, stop))
      i = stop + 1
    elseif c == '"' or c == "'" then
      local j = i + 1
      while j <= n do
        local d = src:sub(j, j)
        if d == "\\" then j = j + 2
        elseif d == c or d == "\n" then break
        else j = j + 1 end
      end
      out[#out + 1] = c .. blank(src:sub(i + 1, j - 1)) .. c
      i = j + 1
    elseif c == "[" and src:match("^%[=*%[", i) then
      local eq = src:match("^%[(=*)%[", i)
      local _, e = src:find("]" .. eq .. "]", i, true)
      e = e or n
      out[#out + 1] = blank(src:sub(i, e))
      i = e + 1
    else
      out[#out + 1] = c
      i = i + 1
    end
  end
  return table.concat(out)
end

local function line_at(s, pos)
  local _, count = s:sub(1, pos):gsub("\n", "")
  return count + 1
end

local NEWER = {
  { "%f[%w_]goto%f[^%w_]", "goto doesn't exist in WoW's Lua 5.1: use if/else or a nested function instead" },
  { "::[%a_][%w_]*::", "labels (::name::) don't exist in WoW's Lua 5.1" },
  { "table%s*%.%s*unpack%f[^%w_]", "table.unpack is Lua 5.2+: WoW has unpack()" },
  { "table%s*%.%s*pack%f[^%w_]", "table.pack is Lua 5.2+: use {...} and select('#', ...)" },
  { "%f[%w_]utf8%s*%.", "utf8 is Lua 5.3+: WoW has no utf8 library (use strlenutf8 or string patterns)" },
  { "math%s*%.%s*type%f[^%w_]", "math.type is Lua 5.3+" },
  { "math%s*%.%s*tointeger%f[^%w_]", "math.tointeger is Lua 5.3+: use math.floor" },
  { "string%s*%.%s*pack%f[^%w_]", "string.pack is Lua 5.3+" },
  { "%f[%w_]bit32%s*%.", "bit32 is Lua 5.2: WoW has the bit library (bit.band, bit.bor)" },
  { "%f[%w_]setfenv%f[^%w_]", nil },  -- fine in 5.1: listed so it isn't mistaken for a typo
}

local function globals_comment(src)
  local names = {}
  for list in src:gmatch("%-%-%s*globals%s*:%s*([^\n]+)") do
    for name in list:gmatch("[%a_][%w_]*") do names[name] = true end
  end
  return names
end

local function walk(fn, visit)
  local info = jutil.funcinfo(fn)
  local pc = 0
  while true do
    local ins = jutil.funcbc(fn, pc)
    if not ins then break end
    local op = vmdef.bcnames:sub(bit.band(ins, 0xff) * 6 + 1, bit.band(ins, 0xff) * 6 + 6):gsub("%s+$", "")
    if op == "GSET" or op == "GGET" then
      local d = bit.rshift(ins, 16)
      local name = jutil.funck(fn, -d - 1)
      visit(op, name, jutil.funcinfo(fn, pc).currentline)
    end
    pc = pc + 1
  end
  for i = 0, (info.gcconsts or 0) - 1 do
    local k = jutil.funck(fn, -i - 1)
    if type(k) == "proto" then walk(k, visit) end
  end
end

local function allowed_global(name, extra)
  return allowed[name] or extra[name] or name:match("^SLASH_") or name:match("^BINDING_")
    or name == "SlashCmdList" or (addon ~= "" and name:lower():sub(1, #addon) == addon:lower())
end

for _, rel in ipairs(files) do
  local path = dir .. "/" .. rel
  local src = read(path)
  if not src then
    problem(rel, 1, "the .toc lists this file, but it doesn't exist")
  else
    local chunk, err = loadstring(src, "@" .. rel)
    if not chunk then
      local line, msg = err:match(":(%d+):%s*(.*)$")
      problem(rel, line, "syntax error: " .. (msg or err))
    else
      local code = code_only(src)
      for _, rule in ipairs(NEWER) do
        if rule[2] then
          local s = 1
          while true do
            local a, b = code:find(rule[1], s)
            if not a then break end
            problem(rel, line_at(code, a), rule[2])
            s = b + 1
          end
        end
      end
      -- a frame has one script per handler: a second SetScript("OnEvent", ...) replaces the first, so the events
      -- the first one handled are silently dropped (Day 5: the Builder did this in 3 of 3 runs)
      local handlers = {}
      local s = 1
      while true do
        local a, b, frameName, quote = code:find("([%w_%.]+)%s*:%s*SetScript%s*%(%s*([\"'])", s)
        if not a then break end
        local script = src:sub(b + 1):match("^([%w_]+)" .. quote)
        local lineStart = code:sub(1, a):match(".*\n()") or 1
        local topLevel = lineStart == a                  -- not inside a function (no indentation)
        local clears = src:sub(b + 1):match("^[%w_]+" .. quote .. "%s*,%s*nil%s*%)")
        -- only OnEvent at the top of the file: turning OnUpdate on and off, or two local frames that happen to
        -- share a name inside different functions, is fine (the client's own addons do both)
        if script == "OnEvent" and topLevel and not clears then
          local key = frameName .. ":" .. script
          if handlers[key] then
            problem(rel, line_at(code, a), string.format("%s:SetScript(\"%s\", ...) is set again (first on line %d): "
              .. "the second replaces the first, so its events are no longer handled. Use one %s function for all "
              .. "of the frame's events", frameName, script, handlers[key], script))
          else
            handlers[key] = line_at(code, a)
          end
        end
        s = b + 1
      end
      local extra = globals_comment(src)
      local seen = {}
      walk(chunk, function(op, name, line)
        if op == "GSET" and type(name) == "string" and not allowed_global(name, extra) and not seen[name] then
          seen[name] = true
          problem(rel, line, string.format("sets the global '%s': make it local, or (if the game must see it) "
            .. "name it after the addon or list it in the .toc's SavedVariables", name))
        end
      end)
    end
  end
end

if #problems > 0 then
  for _, p in ipairs(problems) do print("error: " .. p) end
  print(string.format("%d problem(s) in %s", #problems, addon))
  os.exit(1)
end
print(string.format("ok: %d file(s) of %s checked as Lua 5.1 (syntax, 5.2+ features, globals)", #files, addon))
