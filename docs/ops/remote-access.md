# Remote access to the trading console (private, via Tailscale)

The PAPER servers bind to loopback only (`127.0.0.1`) and have no login of their own. Anyone who can
reach one can start or stop automation, change the kill switch, spend the AI budget, and enter
provider keys in Settings. So a server is **never** exposed to the public internet: no
`0.0.0.0` bind, no port forwarding on the router, no ngrok, no `tailscale funnel`.

Use your own Tailscale network (tailnet) instead:

- `tailscale serve` runs on this Mac and proxies HTTPS to the loopback socket.
- Only devices signed in to *your* tailnet can reach it, for example your phone with the
  Tailscale app.
- Traffic is end-to-end encrypted, with a real HTTPS certificate on a `*.ts.net` name.

## One-time setup

You do these steps yourself; they involve your account.

1. Open **Tailscale** on the Mac and sign in (menu bar icon → *Log in*).
2. In the Tailscale admin console, enable **MagicDNS** and **HTTPS certificates**
   (*DNS* page).
3. Install Tailscale on your phone and sign in to the **same** account.

## Publish the consoles on the tailnet

Run these once. The settings persist in Tailscale. `serve` is tailnet-only; never use `funnel`.

```bash
TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale   # or `tailscale` from Homebrew
$TS serve --bg --https=443 http://127.0.0.1:8771     # Spot Co-Trader (the main app)
$TS serve status
# optional, the futures lab:
# $TS serve --bg --https=8443 http://127.0.0.1:8765  # EXP-001
# $TS serve --bg --https=8444 http://127.0.0.1:8768  # EXP-002
```

Current setup (2026-09-30): only the Co-Trader is published, at
`https://macbook-air--myh.tail554a7c.ts.net/` → `127.0.0.1:8771`, with
`PAPER_TRUSTED_ORIGINS=https://macbook-air--myh.tail554a7c.ts.net` in
`~/Library/LaunchAgents/com.cryptoskills.paper-cotrader.plist`. Verified: a remote GET returns 200,
a POST from that origin returns 200, and a POST with any other `Origin` is refused (400).

Then tell each server which proxy origin to trust. Without this, the page loads but every button
is refused as a cross-origin request.

1. Find the Mac's name with `$TS status --json | python3 -c "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))"`.
2. Add the following to both launchd plists, inside the top `<dict>`:
   ```xml
   <key>EnvironmentVariables</key>
   <dict><key>PAPER_TRUSTED_ORIGINS</key><string>https://NAME,https://NAME:8443</string></dict>
   ```
3. Restart both agents:
   ```bash
   launchctl bootout gui/$(id -u)/com.cryptoskills.paper-campaign; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cryptoskills.paper-campaign.plist
   launchctl bootout gui/$(id -u)/com.cryptoskills.paper-exp002; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cryptoskills.paper-exp002.plist
   ```

From the phone, with Tailscale connected, open:

- `https://NAME/` for EXP-001;
- `https://NAME:8443/` for EXP-002.

## How it stays safe

- The servers still refuse to bind anything but loopback (`serve()` raises for other hosts).
- `PAPER_TRUSTED_ORIGINS` accepts only exact `https://` origins. Wildcards and `http://` entries
  are ignored.
- A request's `Origin` must equal the listed origin *and* match the `Host` it was forwarded
  with. Cross-site requests are still refused.
- The allowlist is empty by default, so only same-origin loopback requests pass.
- To turn remote access off, run `$TS serve reset`, or disconnect Tailscale.
- Anyone signed in to your tailnet can operate the consoles. Keep the tailnet to your own
  devices, and don't share the Mac node with others. Tagged devices (servers) in the tailnet can
  reach it too unless a Tailscale ACL restricts them; restrict port 443 on this Mac to your own
  user if the tailnet contains machines you don't control.
- Everything is still PAPER. Real-money execution stays disabled regardless of how the console is
  reached.
