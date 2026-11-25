local W = ...

test("a roll is recorded and listed", function()
  W.fire("CHAT_MSG_SYSTEM", "Moonbone rolls 42 (1-100)")
  W.slash("/rolls", "")
  W.expectSaid("Rolls:")
  W.expectSaid("1. Moonbone 42")
end)

test("no rolls yet", function()
  W.slash("/rolls", "")
  W.expectSaid("No rolls yet.")
end)
