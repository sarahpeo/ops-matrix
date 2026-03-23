# Windows Home to Pro Upgrade Guide

## Can I Do This Myself?

**Yes.** It takes about 15 minutes, no command prompts needed.

## Steps

1. Open **Settings** > **System** > **Activation**
2. Click **"Upgrade your edition of Windows"** or **"Change product key"**
3. Enter a valid **Windows 11 Pro product key**
4. Windows downloads the Pro features and upgrades in place — no reinstall required
5. Reboot when prompted

## Where to Get a Windows Pro Key

- **Microsoft Store** (built into Windows or at microsoft.com) — ~$99–199 USD
- Authorized retailers often sell legitimate OEM keys for less

## What Your IT Company Is Probably Doing

They may be using these commands:

```cmd
slmgr /ipk XXXXX-XXXXX-XXXXX-XXXXX-XXXXX
slmgr /ato
```

This does the **exact same thing** as the Settings UI above — just typed into a command prompt instead of clicking through menus. There is no technical reason to pay someone to run these two commands for you.

## Important Notes

- This is an **in-place upgrade** — your files, apps, and settings are preserved
- No USB drive or reinstall media is needed
- The upgrade is instant after the download completes (usually a few minutes)
- You will need to reboot once

## Pro Features You'll Gain

- BitLocker drive encryption
- Remote Desktop (host)
- Hyper-V virtualization
- Group Policy Editor (gpedit.msc)
- Windows Sandbox
- Domain join capability
