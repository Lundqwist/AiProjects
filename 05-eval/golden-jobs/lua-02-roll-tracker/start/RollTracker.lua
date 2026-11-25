-- RollTracker
-- Keeps track of /roll results in the group, so the raid leader can see who rolled what for loot.

local ADDON_NAME, ns = ...

local rolls = {}
local frame = CreateFrame("Frame")

local function Print(msg)
  DEFAULT_CHAT_FRAME:AddMessage("|cffffd100Rolls|r: " .. msg)
end

-- The game's system message for a roll: "Name rolls 57 (1-100)"
local function OnSystemMessage(msg)
  local name, roll, low, high = msg:match("^(%a+) rolls (%d+) %((%d+)%-(%d+)%)$")
  if name then
    rolls[#rolls + 1] = { name = name, roll = tonumber(roll) }
  end
end

frame:RegisterEvent("CHAT_MSG_SYSTEM")
frame:SetScript("OnEvent", function(self, event, msg)
  if event == "CHAT_MSG_SYSTEM" then
    OnSystemMessage(msg)
  end
end)

SLASH_ROLLTRACKER1 = "/rolls"
SlashCmdList.ROLLTRACKER = function(msg)
  if #rolls == 0 then
    Print("No rolls yet.")
    return
  end
  Print("Rolls:")
  for i, r in ipairs(rolls) do
    Print(i .. ". " .. r.name .. " " .. r.roll)
  end
end
