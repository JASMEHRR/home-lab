You are working on Jasmehr's home internet from his Windows 11 laptop. You can run PowerShell and use a browser on this laptop.

GOAL
His internet plan is 60 Mbps, but the laptop measured about 10 Mbps, and video (Stremio, Fire TV streaming) buffers. Find out why and make it faster. Change router settings only where a change can actually help, and prove each result with a before and after speed test.

WHAT IS ALREADY KNOWN (measured 2026-11-20, do not redo the discovery)
- Router: Huawei HG8121H fibre box from the internet provider (its login page says ProductName "Internet Box 1000" / "HG8121H"). Admin page: http://192.168.100.1/ . Firmware is from around 2016.
- Wi-Fi: network "Mehr", 2.4 GHz only (this box has no 5 GHz), 802.11n, channel 11, WPA2-Personal, router Wi-Fi BSSID c8:8d:83:e0:22:a4.
- Neighbouring networks: "Jas" channel 6 (45% signal), "Massey_5G" channel 6 (5%), "DLINK5G" 5 GHz channel 44 (42%). Channel 11 is already the least crowded choice.
- Laptop has two Wi-Fi adapters, both joined to "Mehr":
  - "Wi-Fi": MediaTek Wi-Fi 6 MT7921, 192.168.100.18, link 144.4 Mbps, signal 79%. This one carries the traffic (lower metric).
  - "Wi-Fi 2": TP-Link Wireless Nano USB Adapter, 192.168.100.19, link 43.3 Mbps, signal 64%. Not carrying traffic. Do not disable it without asking; it may be used for another project.
- Other device on the network: Amazon Fire TV at 192.168.100.156 (leave it alone, and do not wake it: HDMI-CEC would turn the real TV on).
- Speed test (Cloudflare, 50 MB download): 9.7 Mbps.
- Ping to router when idle: 0 to 5 ms (one spike of 20). Ping to router during the download: average 82 ms, max 322 ms, meaning the line was saturated. Ping to 1.1.1.1: 18 ms.
- Conclusion so far: Wi-Fi between laptop and router is fine (144 Mbps link). The internet line itself delivered about 10 Mbps out of 60. Only one full test was done, so it is not yet confirmed.

TASK, IN ORDER
1. Re-test speed 3 times, a few minutes apart, using:
   Invoke-WebRequest -UseBasicParsing -OutFile <temp file> "https://speed.cloudflare.com/__down?bytes=25000000"
   timed with a Stopwatch, then Mbps = bytes*8/1e6/seconds. Delete the temp file after. Also ask him whether anyone else is streaming or downloading at the time.
2. If the laptop has an Ethernet port and he has a cable, ask him to plug into the Huawei box's LAN port and test once wired. Wired near 60 means Wi-Fi is the issue; wired near 10 means the line or provider is the issue.
3. Open http://192.168.100.1/ in the browser and ask HIM to type the username and password and log in. Never type the password yourself and never ask him to paste it in chat. (The default login is usually printed on the sticker under the box.)
4. Once he is logged in, read (do not change) and note down:
   - Status > WAN / Internet info: connection status, uptime.
   - Status > Optical info: RX optical power in dBm. Normal is roughly -8 to -27 dBm. Worse than -27 (for example -29) points to a dirty or bent fibre, which only the provider can fix.
   - The connected devices / DHCP list: how many devices, anything unexpected.
   - WLAN basic and advanced settings: mode, channel, channel width (20 or 40 MHz), max clients.
   Before any change, write down the current value of every setting you plan to touch so it can be put back.
5. Changes you may make, one at a time, each after telling him what and why in one line and getting his yes, with a speed test after each:
   - Restart the router from its menu (fixes a lot on these boxes).
   - Wi-Fi channel width 20 MHz to 20/40 auto (can help Wi-Fi speed; may be worse because neighbours use channel 6).
   - Wi-Fi mode to 802.11n only if it is b/g/n mixed and nothing old needs b/g.
   Revert any change that does not improve the speed.
6. Do NOT touch: Wi-Fi name or password, WAN/PON/GPON settings, VLANs, provider or TR-069 settings, firmware, DNS for the whole network, or factory reset. Changing those can cut the internet entirely, and the provider manages them.

HOW TO WORK
- Tools: PowerShell plus the browser. netsh wlan show interfaces shows the link; Test-Connection 192.168.100.1 tests the router.
- Use plain language: he is not a networking person.

HIS STANDING RULES
- No em dashes or en dashes in any text.
- Real, measured numbers only. Do not invent speeds or settings.
- Free fixes only; if the only fix costs money (new router, plan upgrade, provider visit), say so plainly and let him decide.
- Times in IST, 12-hour format.

WHAT DONE LOOKS LIKE
- 3 speed results before and after any change, with times.
- The optical power reading from the router.
- A clear verdict: "the provider's line is the problem, call them and quote these numbers" or "Wi-Fi setting X fixed it, now Y Mbps".
- A list of every router setting changed, with old and new value.

REPORT BACK (short)
What the speed is now, what was changed (old and new values), the optical power, and whether he needs to call the provider, with the exact numbers to quote to them.
