package com.example.wakebutton;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

import org.junit.Test;

public class GoldenTest {
    private static final byte[] MAC = {(byte) 0xD8, 0x5E, (byte) 0xD3, 0x5B, 0x01, 0x71};

    private static void rejects(String text) {
        try {
            WakeOnLan.parseMac(text);
            fail("accepted: " + text);
        } catch (IllegalArgumentException e) {
            assertEquals("Not a MAC address: " + text, e.getMessage());
        }
    }

    private static void rejectsBroadcast(String ip, int prefix) {
        try {
            WakeOnLan.broadcastAddress(ip, prefix);
            fail("accepted: " + ip + "/" + prefix);
        } catch (IllegalArgumentException e) {
            // expected
        }
    }

    @Test
    public void goldenColonsDashesAndNone() {
        assertArrayEquals(MAC, WakeOnLan.parseMac("D8:5E:D3:5B:01:71"));
        assertArrayEquals(MAC, WakeOnLan.parseMac("d8-5e-d3-5b-01-71"));
        assertArrayEquals(MAC, WakeOnLan.parseMac("D85ED35B0171"));
        assertArrayEquals(MAC, WakeOnLan.parseMac("d8:5e:D3:5b:01:71"));
    }

    @Test
    public void goldenSpacesAround() {
        assertArrayEquals(MAC, WakeOnLan.parseMac("  D8:5E:D3:5B:01:71 "));
    }

    @Test
    public void goldenRejectsBadMacs() {
        rejects("D8:5E:D3:5B:01");
        rejects("D8:5E:D3:5B:01:71:00");
        rejects("D8:5E:D3:5B:01:7");
        rejects("D8:5E:D3:5B:01:7G");
        rejects("D8:5E-D3:5B:01:71");
        rejects("D85ED35B017");
        rejects("D8:5E:D3:5B:01:+1");
        rejects("");
    }

    @Test
    public void goldenBroadcast() {
        assertEquals("192.168.50.255", WakeOnLan.broadcastAddress("192.168.50.12", 24));
        assertEquals("192.168.255.255", WakeOnLan.broadcastAddress("192.168.50.12", 16));
        assertEquals("10.6.0.3", WakeOnLan.broadcastAddress("10.6.0.2", 30));
        assertEquals("192.168.50.12", WakeOnLan.broadcastAddress("192.168.50.12", 32));
        assertEquals("255.255.255.255", WakeOnLan.broadcastAddress("192.168.50.12", 0));
        assertEquals("172.31.255.255", WakeOnLan.broadcastAddress("172.16.4.9", 12));
    }

    @Test
    public void goldenRejectsBadBroadcasts() {
        rejectsBroadcast("192.168.50", 24);
        rejectsBroadcast("192.168.50.256", 24);
        rejectsBroadcast("192.168.50.-1", 24);
        rejectsBroadcast("pc.local", 24);
        rejectsBroadcast("192.168.50.12", 33);
        rejectsBroadcast("192.168.50.12", -1);
    }

    @Test
    public void goldenMagicPacketNeedsSixBytes() {
        byte[] p = WakeOnLan.magicPacket(MAC);
        assertEquals(102, p.length);
        assertEquals((byte) 0xFF, p[5]);
        assertEquals((byte) 0xD8, p[6]);
        assertEquals((byte) 0x71, p[101]);
        try {
            WakeOnLan.magicPacket(new byte[5]);
            fail("accepted 5 bytes");
        } catch (IllegalArgumentException e) {
            // expected
        }
    }
}
