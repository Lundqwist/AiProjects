package com.example.wakebutton;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class WakeOnLanTest {
    @Test
    public void parsesAMacWithColons() {
        assertArrayEquals(new byte[] {(byte) 0xD8, 0x5E, (byte) 0xD3, 0x5B, 0x01, 0x71},
                WakeOnLan.parseMac("D8:5E:D3:5B:01:71"));
    }

    @Test
    public void magicPacketIs102Bytes() {
        assertEquals(102, WakeOnLan.magicPacket(new byte[6]).length);
    }
}
