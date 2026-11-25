package com.example.wakebutton;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;

/** Builds and sends Wake-on-LAN magic packets. Plain Java, so it can be tested on the PC. */
public final class WakeOnLan {
    public static final int PORT = 9;

    private WakeOnLan() {
    }

    /** "D8:5E:D3:5B:01:71" -> its 6 bytes. */
    public static byte[] parseMac(String text) {
        String[] parts = text.split(":");
        if (parts.length != 6) {
            throw new IllegalArgumentException("Not a MAC address: " + text);
        }
        byte[] mac = new byte[6];
        for (int i = 0; i < 6; i++) {
            mac[i] = (byte) Integer.parseInt(parts[i], 16);
        }
        return mac;
    }

    /** The magic packet: 6 bytes of 0xFF, then the MAC address 16 times. */
    public static byte[] magicPacket(byte[] mac) {
        byte[] packet = new byte[6 + 16 * mac.length];
        for (int i = 0; i < 6; i++) {
            packet[i] = (byte) 0xFF;
        }
        for (int i = 6; i < packet.length; i += mac.length) {
            System.arraycopy(mac, 0, packet, i, mac.length);
        }
        return packet;
    }

    public static void send(byte[] mac, String address) throws IOException {
        byte[] packet = magicPacket(mac);
        try (DatagramSocket socket = new DatagramSocket()) {
            socket.setBroadcast(true);
            socket.send(new DatagramPacket(packet, packet.length, InetAddress.getByName(address), PORT));
        }
    }
}
