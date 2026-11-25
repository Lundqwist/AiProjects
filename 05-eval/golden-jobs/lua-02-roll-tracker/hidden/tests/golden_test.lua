local W = ...

local function roll(name, n, low, high)
  W.fire("CHAT_MSG_SYSTEM", string.format("%s rolls %d (%d-%d)", name, n, low or 1, high or 100))
end

local function lines()
  local out = {}
  for _, m in ipairs(W.printed) do
    if m:match("^%d+%. ") then out[#out + 1] = m end
  end
  return table.concat(out, " | ")
end

test("golden: accented names and realms are recorded as written", function()
  roll("Lûnamal", 30)
  roll("Lunamal-Stormscale", 20)
  roll("Zoë-ArgentDawn", 10)
  W.slash("/rolls", "")
  W.eq(lines(), "1. Lûnamal 30 | 2. Lunamal-Stormscale 20 | 3. Zoë-ArgentDawn 10", "listed")
end)

test("golden: only 1-100 counts", function()
  roll("Alpha", 99, 1, 50)
  roll("Bravo", 500, 1, 1000)
  roll("Charlie", 7, 1, 100)
  W.slash("/rolls", "")
  W.eq(lines(), "1. Charlie 7", "listed")
end)

test("golden: a player's first roll counts", function()
  roll("Alpha", 12)
  roll("Alpha", 99)
  roll("Bravo", 50)
  W.slash("/rolls", "")
  W.eq(lines(), "1. Bravo 50 | 2. Alpha 12", "listed")
end)

test("golden: a roll outside 1-100 doesn't use up the player's first roll", function()
  roll("Alpha", 40, 1, 50)
  roll("Alpha", 77)
  W.slash("/rolls", "")
  W.eq(lines(), "1. Alpha 77", "listed")
end)

test("golden: highest first, ties by name ignoring case", function()
  roll("delta", 50)
  roll("Bravo", 80)
  roll("alpha", 50)
  roll("Charlie", 50)
  roll("Echo", 3)
  W.slash("/rolls", "")
  W.expectSaid("Rolls:")
  W.eq(lines(), "1. Bravo 80 | 2. alpha 50 | 3. Charlie 50 | 4. delta 50 | 5. Echo 3", "listed")
end)

test("golden: other system messages are ignored", function()
  W.fire("CHAT_MSG_SYSTEM", "Alpha has joined the raid group.")
  W.fire("CHAT_MSG_SYSTEM", "You receive loot: [Linen Cloth].")
  W.slash("/rolls", "")
  W.expectSaid("No rolls yet.")
end)

test("golden: clear", function()
  roll("Alpha", 12)
  W.slash("/rolls", "clear")
  W.expectSaid("Rolls cleared.")
  W.printed = {}
  W.slash("/rolls", "")
  W.expectSaid("No rolls yet.")
  roll("Alpha", 88)
  W.printed = {}
  W.slash("/rolls", "")
  W.eq(lines(), "1. Alpha 88", "a new first roll after clear")
end)

test("golden: commands ignore case and spaces", function()
  roll("Alpha", 12)
  W.slash("/rolls", "  CLEAR ")
  W.expectSaid("Rolls cleared.")
end)

test("golden: winner", function()
  roll("Alpha", 12)
  roll("Bravo", 97)
  roll("Charlie", 45)
  W.slash("/rolls", "winner")
  W.expectSaid("Winner: Bravo (97)")
end)

test("golden: a tie for the highest roll", function()
  roll("delta", 90)
  roll("Bravo", 90)
  roll("Charlie", 12)
  roll("alpha", 90)
  W.slash("/rolls", "Winner")
  W.expectSaid("Tie: alpha, Bravo, delta (90)")
end)

test("golden: winner with no rolls", function()
  W.slash("/rolls", "winner")
  W.expectSaid("No rolls yet.")
end)
