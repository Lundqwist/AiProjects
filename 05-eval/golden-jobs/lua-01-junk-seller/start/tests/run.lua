-- Runs the addon's tests outside the game:   luajit tests/run.lua   (from the addon's folder)
-- Every tests/*_test.lua file is run; each test gets a fresh copy of the addon and of the game stand-ins.

package.path = "tests/?.lua;" .. package.path
local W = require("wow")
local ADDON = "JunkSeller"

local tests = {}
function test(name, fn)
  tests[#tests + 1] = { name = name, fn = fn }
end

local files = {}
local listing = io.popen(package.config:sub(1, 1) == "\\" and "dir /b tests\\*_test.lua 2>nul"
                         or "ls tests/*_test.lua 2>/dev/null")
for name in listing:lines() do
  files[#files + 1] = "tests/" .. name:match("([^/\\]+)$")
end
listing:close()
table.sort(files)

for _, f in ipairs(files) do
  local chunk = assert(loadfile(f))
  chunk(W)
end

local failed = 0
for _, t in ipairs(tests) do
  W.reset()
  JunkSellerDB = nil
  local ok, err = pcall(function()
    W.load(".", ADDON)
    t.fn(W)
  end)
  if ok then
    io.write("ok - ", t.name, "\n")
  else
    failed = failed + 1
    io.write("not ok - ", t.name, "\n    ", tostring(err), "\n")
  end
end
io.write(string.format("%d tests, %d passed, %d failed\n", #tests, #tests - failed, failed))
os.exit(failed == 0 and 0 or 1)
