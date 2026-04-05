# 🚀 OptiScaler Installer

<img width="1920" height="1128" alt="스크린샷 2026-04-03 213812" src="https://github.com/user-attachments/assets/5ace04fd-3cd4-4234-a792-63575fee5610" />

<p align="center">
  <b>Simple and automated installer for OptiScaler.</b><br>
  <b>Install and Play game! No need to set up OptiScaler option (Like Plug and Play)</b><br>
</p>

> [!NOTE]
> This installer is not officially provided by OptiScaler.

&nbsp;

## 📋 Requirements
---
* **Internet Connection:** Required to load the latest game DB from Google Sheets.
* **Windows 11** Required.
* **OptiScaler Version:** The latest OptiScaler will be downloaded automatically.

&nbsp;

## 💻 GPU Support and Applied Settings

> [!NOTE]
> After installation, optimized **OptiScaler** settings based on your GPU will be automatically applied.  
> The upscaling and frame generation options shown below refer to **OptiScaler configuration**, not the game's native in-game graphics settings.

| Vendor | Supported GPUs | Upscaling | Frame Generation |
|---|---|---|---|
| **Intel** | Arc Series | XeSS | **XeMFG** (default: 3x) |
| **AMD** | 780/890M, 8060S, RX 60/70 Series | FSR4 INT8 | XeFG (2x) |
| **NVIDIA** | RTX 20/30 Series | DLSS | XeFG (2x) |

**Notes**
- **AMD RX 9000, NVIDIA RTX 40/50 Series:** devices are supported only in games that do **not** support in-game FSR Frame Generation.  
  **Examples:** *Kingdom Come: Deliverance II*, *Death Stranding Director’s Cut*

&nbsp;

## ✨ Key Features
---
<p>✅ <b>Live Game DB Update:</b> Supported game list and pre-configured INI & options are loaded live from online DB.</p>

<p>🔍 <b>Auto Game Scan:</b> Automatically detects Steam library folders.</p>

<p>🛠️ <b>One-Click Install:</b> Installs OptiScaler files and <b>pre-configured & tested game specific <code>OptiScaler.ini</code> settings</b> automatically.</p>

<p>🚀 <b>Advanced Patches:</b> Automatic installation of additional modules (OptiPatcher, Unreal5 G/I bug fix) for selected games.</p>

<p>🚀 <b>Advanced INI Edit:</b> Automatically edit Unreal Engine.ini & game INI for proper OptiScaler working for selected games.</p>

<p>🔔 <b>Smart Guidance:</b> User notifications and RTSS option change guides to ensure the best performance.</p>

&nbsp;

## 🎮 How to Install
---
1.  **Launch the App:** It will scan your Steam libraries. (Manual folder selection is also available).
2.  **Check Notice:** View the supported game list and latest update info.
3.  **Select Game:** Choose the game you want to install optimized OptiScaler files and settings.
4.  **Install:** Click the **Install** button.
5.  **Run Game:** Follow the in-game instructions to enable upscaling/frame generation.
    > Press `Insert` to verify with the OptiScaler overlay.

&nbsp;

**All credit goes to OptiScaler Team for their hard work.**
   > nitec, TheRazerMD, Fakemichau, Keu, By-U, Atsy, san9, Cryio

&nbsp;

### **References**
| Component | Repository Link |
| :--- | :--- |
| **OptiScaler** | [GitHub](https://github.com/optiscaler/OptiScaler) |
| **OptiPatcher** | [GitHub](https://github.com/optiscaler/OptiPatcher) |
| **Unreal 5 G/I Fix** | [GitHub](https://github.com/alxn1/d3d12-proxy) |
| **REFramework** | [GitHub](https://github.com/praydog/reframework-nightly/releases) |

&nbsp;


![GitHub All Releases](https://img.shields.io/github/downloads/onehoon/OptiScalerInstaller/total?style=for-the-badge&color=orange)
![GitHub Downloads (latest Release)](https://img.shields.io/github/downloads/onehoon/OptiScalerInstaller/latest/total?style=for-the-badge&label=latest%20download&color=brightgreen)
