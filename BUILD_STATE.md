# Build state: Stremio on the laptop

## Goal
Official Stremio desktop app installed on this Windows laptop, opens, and has its legal add-ons on.
Finish line: Stremio launches from the Start menu; add-on list shows the official ones enabled.

## Stages
1. done: downloaded StremioSetup-v5.0.24_x64.exe from dl.strem.io (stremio.com/downloads) into apks\stremio-windows\
   SHA-256 12DC718E02773D747F85D5AAC765CF73915B63C582EA799CC4F9B880F1242842
2. done: Authenticode signature Valid, "CN=Smart Code OOD" (Sofia, BG), issued by Sectigo. Installed per-user, no admin:
   C:\Users\jasme\AppData\Local\Programs\Stremio\ ("Stremio version 5.0.24" in Apps)
3. done: launched; stremio-shell-ng (window "Stremio") + stremio-runtime running. Start menu + desktop shortcut created.
4. blocked: add-ons are saved to the Stremio account, so they need his sign-in first.
5. done: report

## Now
Waiting on sign-in for stage 4.

## Blocked (needs Jasmehr)
- Sign in to Stremio on the laptop (same account as the TV). Then the official add-ons that come with the account are on
  automatically, on both laptop and TV. More official ones: Add-ons button, top right in Stremio.

## Not doing
- Piracy add-ons (Torrentio and similar). Declined in chat.

## Notes
- The installer is Inno Setup: silent switches are /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER (not /S).
- Uninstall: Settings > Apps > Stremio, or C:\Users\jasme\AppData\Local\Programs\Stremio\unins000.exe
