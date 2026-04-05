# 🚀 OptiScaler Installer

<img width="1920" height="1128" alt="OptiScaler Installer Screenshot" src="https://github.com/user-attachments/assets/5ace04fd-3cd4-4234-a792-63575fee5610" />

<p align="center">
  <b>Simple, automated installer for OptiScaler.</b><br>
  <b>Install and play — no manual OptiScaler setup required.</b><br>
</p>

> [!NOTE]
> This installer is not officially provided by OptiScaler.

> [!IMPORTANT]
> **This installer is designed for users who are not familiar with manual OptiScaler installation and configuration.**  
> It automatically applies optimized settings based on the selected game and GPU.
>
> **If you are already comfortable with manual OptiScaler setup or regularly use mods, this installer is not recommended.**

&nbsp;

## 📋 Requirements
---
* **Internet Connection:** Required to load the latest game database.
* **Windows 11:** Required
* **OptiScaler:** The latest version is downloaded automatically

&nbsp;

## 💻 GPU Support and Applied Settings

> [!NOTE]
> After installation, **optimized OptiScaler settings** based on your GPU are applied automatically.  
> The upscaling and frame generation options shown below refer to **OptiScaler configuration**, not the game's native in-game graphics settings.

| Vendor | Supported GPUs | Upscaling | Frame Generation |
|:---:|:---:|:---:|:---:|
| **Intel** | Arc Series | XeSS | **XeMFG** (3x by default, up to 4x) |
| **AMD** | 780/890M, 8060S, RX 6000/7000 Series | FSR | XeFG (2x only) |
| **NVIDIA** | RTX 2000/3000 Series | DLSS | XeFG (2x only) |

**Notes**
- **AMD RX 9000 and NVIDIA RTX 40/50 Series** are supported only in games that do not provide native in-game Frame Generation.  
  **Examples:** *Kingdom Come: Deliverance II*, *Death Stranding Director’s Cut*
- **XeMFG** is Intel’s multi-frame generation feature and is currently available only on **Intel Arc** GPUs.

**Frame Generation Notes**
- NVIDIA RTX 20/30 Series GPUs do not support native DLSS Frame Generation. This installer enables frame generation through OptiScaler using Intel XeFG instead.
- On AMD GPUs, this installer uses Intel XeFG through OptiScaler instead of FSR3 Frame Generation.

&nbsp;
## ✨ Key Features
---
<p>✅ <b>Live Game DB Update:</b> Supported game list and pre-configured INI & options are loaded dynamically from an online database.</p>

<p>🔍 <b>Auto Game Scan:</b> Automatically detects Steam library folders.</p>

<p>🛠️ <b>One-Click Install:</b> Installs OptiScaler files and <b>pre-configured and tested game-specific <code>OptiScaler.ini</code> settings</b> automatically.</p>

<p>🚀 <b>Automatic Patches:</b> Automatic installation of additional modules (OptiPatcher, Unreal5 G/I bug fix) for selected games.</p>

<p>🚀 <b>Automatic INI Configuration:</b> Automatically edits Unreal Engine.ini and game INI for proper OptiScaler operation for selected games.</p>

<p>🔔 <b>Smart Guidance:</b> User notifications and RTSS option change guides to ensure the best performance.</p>

&nbsp;

## 🎮 How to Install
---
1.  **Launch the App:** It will scan your Steam libraries. (Manual folder selection is also available).
2.  **Review Notices:** Check the supported game list and latest update information.
3.  **Select Game:** Choose the game for which you want to install optimized OptiScaler files and settings.
4.  **Install:** Click the **Install** button.
5.  **Run Game:** Follow the in-game instructions to enable upscaling/frame generation.
    > Press `Insert` to verify that OptiScaler is working through the overlay.

&nbsp;

**All credit goes to the OptiScaler team for their hard work.**  
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
