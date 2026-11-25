The client's Android app Wake Button (Java, Gradle) wakes a PC with a Wake-on-LAN packet. The network code is in
`app\src\main\java\com\example\wakebutton\WakeOnLan.java`; `gradlew testDebugUnitTest` runs its unit tests. Three
changes:

1. **Bug:** `WakeOnLan.parseMac` only understands `D8:5E:D3:5B:01:71`. It must also accept dashes
   (`d8-5e-d3-5b-01-71`) and no separators at all (`D85ED35B0171`), in upper or lower case, with spaces around the
   text ignored. Each of the 6 parts is exactly 2 hex digits, and one text uses one kind of separator. Anything else
   throws `IllegalArgumentException` with the message `Not a MAC address: <the text as given>`.
2. Add `public static String broadcastAddress(String ip, int prefixLength)`: the broadcast address of the IPv4
   network, e.g. `("192.168.50.12", 24)` → `"192.168.50.255"`, `("192.168.50.12", 16)` → `"192.168.255.255"`,
   `("10.6.0.2", 30)` → `"10.6.0.3"`. An address that isn't 4 numbers 0-255, or a prefix length outside 0-32, throws
   `IllegalArgumentException`. It must work offline: no name lookups.
3. `WakeOnLan.magicPacket(mac)` builds a wrong packet when `mac` isn't 6 bytes; it must throw
   `IllegalArgumentException` then.

Keep the existing tests working.
