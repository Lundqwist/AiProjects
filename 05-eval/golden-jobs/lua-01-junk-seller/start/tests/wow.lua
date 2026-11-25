-- Stand-ins for the parts of the WoW API this addon uses, so it can be loaded and tested outside the game.
-- Tests drive it through W: put items in bags, fire events, type slash commands, read what was printed.

local W = {}

local function fresh()
  W.printed = {}   -- every message, colour codes and the addon's prefix removed
  W.raw = {}       -- every message as the addon wrote it
  W.used = {}      -- {bag, slot} for every C_Container.UseContainerItem call (selling, at a merchant)
  W.bags = {}      -- W.bags[bag] = {size = n, [slot] = item}
  W.items = {}     -- W.items[itemID] = {name = ..., sellPrice = copper}
  W.frames = {}
end

local function strip(msg)
  msg = tostring(msg)
  msg = msg:gsub("^|c%x%x%x%x%x%x%x%x[^|]*|r:%s*", "")   -- the addon's coloured "Name: " prefix
  msg = msg:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")
  return msg
end

local function say(msg)
  W.raw[#W.raw + 1] = tostring(msg)
  W.printed[#W.printed + 1] = strip(msg)
end

function W.reset()
  fresh()
  CreateFrame = function()
    local f = { events = {}, scripts = {} }
    function f:RegisterEvent(e) self.events[e] = true end
    function f:UnregisterEvent(e) self.events[e] = nil end
    function f:SetScript(k, fn) self.scripts[k] = fn end
    function f:GetScript(k) return self.scripts[k] end
    W.frames[#W.frames + 1] = f
    return f
  end
  DEFAULT_CHAT_FRAME = { AddMessage = function(_, msg) say(msg) end }
  print = function(...)
    local parts = {}
    for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
    say(table.concat(parts, " "))
  end
  SlashCmdList = {}
  for k in pairs(_G) do
    if type(k) == "string" and k:match("^SLASH_") then _G[k] = nil end
  end
  C_Container = {
    GetContainerNumSlots = function(bag)
      return W.bags[bag] and W.bags[bag].size or 0
    end,
    GetContainerItemInfo = function(bag, slot)
      local it = W.bags[bag] and W.bags[bag][slot]
      if not it then return nil end
      return { itemID = it.id, quality = it.quality, stackCount = it.count or 1, hasNoValue = it.noValue or false,
               hyperlink = "|cff9d9d9d|Hitem:" .. it.id .. "|h[" .. (W.items[it.id] and W.items[it.id].name or "?")
                 .. "]|h|r", isLocked = false }
    end,
    UseContainerItem = function(bag, slot)
      W.used[#W.used + 1] = { bag, slot }
      if W.bags[bag] then W.bags[bag][slot] = nil end
    end,
  }
  GetItemInfo = function(id)
    local it = W.items[tonumber(id) or id]
    if not it then return nil end
    return it.name, "item:" .. id, it.quality or 0, 1, 1, "Miscellaneous", "Junk", 20, "", 134400, it.sellPrice or 0
  end
end

-- bag(0, 16, {slot = {id, quality, count}, ...}); item(id, name, sellPrice)
function W.bag(bag, size, contents)
  W.bags[bag] = { size = size }
  for slot, it in pairs(contents or {}) do
    W.bags[bag][slot] = { id = it[1], quality = it[2], count = it[3], noValue = it[4] }
  end
end

function W.item(id, name, sellPrice, quality)
  W.items[id] = { name = name, sellPrice = sellPrice, quality = quality }
end

function W.fire(event, ...)
  for _, f in ipairs(W.frames) do
    local fn = f.scripts.OnEvent
    if f.events[event] and fn then fn(f, event, ...) end
  end
end

function W.slash(command, msg)
  for key, fn in pairs(SlashCmdList) do
    for i = 1, 9 do
      local c = _G["SLASH_" .. key .. i]
      if c and c:lower() == command:lower() then
        return fn(msg or "")
      end
    end
  end
  error("no slash command " .. command)
end

-- load the addon the way the game does: the .toc's files in order, each given (addon name, shared table)
function W.load(dir, addon)
  local toc = assert(io.open(dir .. "/" .. addon .. ".toc", "r"), "no " .. addon .. ".toc")
  local ns = {}
  for line in toc:lines() do
    line = line:gsub("%s+$", "")
    if line ~= "" and not line:match("^#") then
      local chunk = assert(loadfile(dir .. "/" .. line))
      chunk(addon, ns)
    end
  end
  toc:close()
  return ns
end

-- assertions
function W.eq(actual, expected, what)
  if actual ~= expected then
    error((what or "value") .. ": expected " .. tostring(expected) .. ", got " .. tostring(actual), 2)
  end
end

function W.said(text)
  for _, m in ipairs(W.printed) do
    if m == text then return true end
  end
  return false
end

function W.expectSaid(text)
  if not W.said(text) then
    error("expected the message '" .. text .. "', got: " .. (#W.printed > 0 and table.concat(W.printed, " | ")
      or "(nothing)"), 2)
  end
end

fresh()
return W
