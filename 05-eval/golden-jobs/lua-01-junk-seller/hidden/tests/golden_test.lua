local W = ...

local function merchant()
  W.fire("ADDON_LOADED", "JunkSeller")
  W.fire("MERCHANT_SHOW")
end

test("golden: empty slots are skipped and the rest is still sold", function()
  W.item(100, "Broken Fang", 12, 0)
  W.bag(0, 4, { [2] = { 100, 0, 1 }, [4] = { 100, 0, 2 } })
  W.bag(1, 3, {})
  merchant()
  W.eq(#W.used, 2, "stacks sold")
end)

test("golden: whites, greys without value and kept items are not sold", function()
  W.item(100, "Broken Fang", 12, 0)
  W.item(101, "Lucky Charm", 30, 0)
  W.item(200, "Linen Cloth", 25, 1)
  W.item(300, "Quest Junk", 0, 0)
  W.fire("ADDON_LOADED", "JunkSeller")
  W.slash("/junk", "keep 101")
  W.bag(0, 4, { [1] = { 100, 0, 1 }, [2] = { 101, 0, 1 }, [3] = { 200, 1, 1 }, [4] = { 300, 0, 1, true } })
  W.fire("MERCHANT_SHOW")
  W.eq(#W.used, 1, "stacks sold")
  W.eq(W.used[1][2], 1, "the slot sold")
end)

test("golden: keep toggles, with its messages", function()
  W.fire("ADDON_LOADED", "JunkSeller")
  W.slash("/junk", "keep 4242")
  W.expectSaid("Keeping 4242")
  W.eq(JunkSellerDB.keep[4242] ~= nil and JunkSellerDB.keep[4242] ~= false, true, "4242 on the list")
  W.slash("/junk", "keep 4242")
  W.expectSaid("No longer keeping 4242")
  W.eq(not JunkSellerDB.keep[4242], true, "4242 off the list")
end)

test("golden: keep with spaces around the number", function()
  W.fire("ADDON_LOADED", "JunkSeller")
  W.slash("/junk", "  keep   77  ")
  W.expectSaid("Keeping 77")
end)

test("golden: keep without a number shows the usage", function()
  W.fire("ADDON_LOADED", "JunkSeller")
  W.slash("/junk", "keep")
  W.expectSaid("Usage: /junk keep <itemID>")
  W.printed = {}
  W.slash("/junk", "keep fang")
  W.expectSaid("Usage: /junk keep <itemID>")
  W.eq(next(JunkSellerDB.keep or {}), nil, "nothing kept")
end)

test("golden: the saved list is created on ADDON_LOADED and kept when it is there", function()
  W.fire("ADDON_LOADED", "SomeOtherAddon")
  W.fire("ADDON_LOADED", "JunkSeller")
  W.eq(type(JunkSellerDB), "table", "JunkSellerDB")
  W.eq(type(JunkSellerDB.keep), "table", "JunkSellerDB.keep")
end)

test("golden: a saved keep list from last time is used", function()
  JunkSellerDB = { keep = { [100] = true } }
  W.item(100, "Broken Fang", 12, 0)
  W.bag(0, 1, { [1] = { 100, 0, 1 } })
  merchant()
  W.eq(#W.used, 0, "stacks sold")
  W.eq(JunkSellerDB.keep[100], true, "the old list is kept")
end)

test("golden: summary with gold, silver and copper", function()
  W.item(100, "Broken Fang", 10520, 0)   -- 1g 5s 20c
  W.bag(0, 1, { [1] = { 100, 0, 1 } })
  merchant()
  W.expectSaid("Sold 1 items for 1g 5s 20c")
end)

test("golden: summary counts stacks and multiplies by the stack size, zero parts left out", function()
  W.item(100, "Broken Fang", 250, 0)
  W.item(101, "Torn Hide", 125, 0)
  W.bag(0, 2, { [1] = { 100, 0, 2 }, [2] = { 101, 0, 4 } })   -- 2*250 + 4*125 = 1000 = 10s
  merchant()
  W.expectSaid("Sold 2 items for 10s")
end)

test("golden: gold and copper without silver", function()
  W.item(100, "Rusty Blade", 20003, 0)   -- 2g 0s 3c
  W.bag(0, 1, { [1] = { 100, 0, 1 } })
  merchant()
  W.expectSaid("Sold 1 items for 2g 3c")
end)

test("golden: nothing sold, nothing printed", function()
  W.item(200, "Linen Cloth", 25, 1)
  W.bag(0, 1, { [1] = { 200, 1, 1 } })
  merchant()
  for _, m in ipairs(W.printed) do
    if m:match("^Sold") then error("printed '" .. m .. "' with nothing sold") end
  end
end)
