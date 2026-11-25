local W = ...

test("grey items are sold at a merchant, others are kept", function()
  W.item(100, "Broken Fang", 12, 0)
  W.item(200, "Linen Cloth", 25, 1)
  W.bag(0, 2, { [1] = { 100, 0, 3 }, [2] = { 200, 1, 5 } })
  W.fire("ADDON_LOADED", "JunkSeller")
  W.fire("MERCHANT_SHOW")
  W.eq(#W.used, 1, "items sold")
  W.eq(W.used[1][2], 1, "the slot sold")
end)

test("/junk explains what the addon does", function()
  W.slash("/junk", "")
  W.expectSaid("Sells grey items when you open a merchant.")
end)
