-- JunkSeller
-- Sells grey (poor quality) items when you open a merchant, so your bags don't fill up with junk.

local ADDON_NAME, ns = ...

local frame = CreateFrame("Frame")

local function Print(msg)
  DEFAULT_CHAT_FRAME:AddMessage("|cff9d9d9dJunkSeller|r: " .. msg)
end

local function SellJunk()
  for bag = 0, 4 do
    for slot = 1, C_Container.GetContainerNumSlots(bag) do
      local info = C_Container.GetContainerItemInfo(bag, slot)
      if info.quality == 0 and not info.hasNoValue then
        C_Container.UseContainerItem(bag, slot)
      end
    end
  end
end

frame:RegisterEvent("MERCHANT_SHOW")
frame:SetScript("OnEvent", function(self, event, ...)
  if event == "MERCHANT_SHOW" then
    SellJunk()
  end
end)

SLASH_JUNKSELLER1 = "/junk"
SlashCmdList.JUNKSELLER = function(msg)
  Print("Sells grey items when you open a merchant.")
end
