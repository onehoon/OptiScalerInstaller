# 🚀 OptiScaler Installer

<img width="1920" height="1128" alt="OptiScaler Installer Screenshot" src="https://github.com/user-attachments/assets/5ace04fd-3cd4-4234-a792-63575fee5610" />

<p align="center">
  <b>Automatically installs and configures OptiScaler per game and GPU.</b><br>
  <b>No manual setup — optimized settings applied instantly.</b><br>
  <sub>Unlike conventional installers, this tool eliminates the hassle and guesswork of manual OptiScaler setup.</sub>
</p>

> [!NOTE]
> This installer is not officially provided by OptiScaler.

> [!IMPORTANT]
> **This installer is designed for users who are not familiar with manual OptiScaler installation and configuration.**  
> It automatically applies optimized settings based on the selected game and GPU.
>
> **If you are already comfortable with manual OptiScaler setup or regularly use mods, this installer is not recommended.**

---

## Overview

This application automates both the **installation and configuration** of OptiScaler.

Unlike conventional installers, this tool eliminates the hassle and guesswork of manual OptiScaler setup by automatically applying optimized settings for each game and GPU.

As a result, users can install and start playing immediately without additional setup.

---

## 🌏 Language / Documentation

- [한국어 상세 가이드](https://github.com/onehoon/OptiScalerInstaller/wiki/%ED%95%9C%EA%B5%AD%EC%96%B4-%EC%84%B8%EB%B6%80-%EA%B8%B0%EB%8A%A5-%EB%B0%8F-%EC%82%AC%EC%9A%A9-%EA%B0%80%EC%9D%B4%EB%93%9C)
- This page (English)

---

## 📋 Requirements

- **Internet Connection:** Required to load the latest game database  
- **Windows 11:** Required  
- **OptiScaler:** Latest version is downloaded automatically  

---

## 💻 GPU Support and Rendering Configuration

> [!NOTE]
> After installation, **optimized OptiScaler settings** are applied automatically.  
> The options below refer to **OptiScaler configuration**, not in-game graphics settings.

### Supported GPU Models

| Vendor | Supported GPUs | Upscaling | Frame Generation | Frame Multiplier |
|:---:|:---:|:---:|:---:|:---:|
| **Intel** | Arc Series | XeSS | Intel XeFG | 3x |
| **AMD** | 780/890M, 8060S, RX 6000/7000 Series | FSR | Intel XeFG | 2x |
| **NVIDIA** | RTX 2000/3000 Series | DLSS | Intel XeFG | 2x |

> XeMFG is supported up to 4x only on **Intel Arc**.

> [!NOTE]
> Support for **AMD RX 9000 Series** and **NVIDIA RTX 40/50 Series** is limited to games that do not provide native in-game Frame Generation.  
>  
> **Examples include:** *Kingdom Come: Deliverance II*, *Death Stranding Director’s Cut*, and *Lies of P*.

---

## [Supported Game List](https://github.com/onehoon/OptiScalerInstaller/wiki/Supported-Game-List)

> [!TIP]
> New supported games are continuously being added.  
> Support for additional games may be provided upon request after compatibility review and testing.

---

## ✨ Key Features

### 🧠 Game & GPU-Aware Automation
- **Fully automated configuration:** Installs and configures OptiScaler with optimized settings per game and GPU
- Eliminates the need for manual OptiScaler tuning
- Automatically configures OptiScaler upscaling and frame generation settings

### ⚙️ Installation & Automation
- Automatically scans Steam games (manual folder scan supported)
- Automatically downloads and installs the latest version of **OptiScaler**
- Automatically installs additional game-specific modules and fixes when required:
  - **OptiPatcher**
  - **REFramework**
  - **Unreal Engine 5 global illumination fix** for UMPC

### 🔧 Configuration Automation
- Automatically configures required INI files (OptiScaler.ini, Unreal Engine.ini, etc.)
- Automatically applies the required components and configuration for **FSR4 INT8** where applicable (except AMD RX90 series)

### 🧩 Compatibility Handling
- Automatically adjusts [**RTSS settings**](https://github.com/optiscaler/OptiScaler/wiki/Frame-Generation-Options#xefg-requirements) for Intel XeFG compatibility
- Handles DLL conflicts (ReShade / other mods) by selecting appropriate OptiScaler DLL names
- Provides game-specific instructions via popup and information panel

### 🎯 User Experience
- One-click installation process
- No manual OptiScaler setup required
- Smart guidance for optimal configuration

### 💻 System Support
- Supports dual GPU environments

> [!NOTE]
> A GPU selection popup appears on first launch.  
> The selected GPU is used as the installation target, and running the game later on a different GPU may cause unexpected behavior.

### 🔄 Updates
- Automatically checks for updates on launch
- Automatically downloads and launches the latest version when available
- Automatically opens the release notes page when updating

---

## 🎮 How to Install

> [!CAUTION]
> Intel XeFG requires **Borderless Windowed / Borderless Fullscreen / Fullscreen Windowed mode**.
>
> Ensure the game is set to one of these modes before installation.
>
> XeFG limitations:
> - **Vulkan is not supported**
> - **HDR16 (FP16 HDR)** and **scRGB** are not supported
> - **HDR10 only** is supported
>
> Failure to meet these requirements may result in crashes, black screens, or XeFG not functioning properly.

1. **Launch the App**  
   Steam libraries will be scanned automatically  
   (Manual folder selection is also available)

   > Automatic scanning currently supports **Steam** only.  
   > **Epic Games Store**, **GOG**, and **Xbox Game Pass** titles are not supported at this time.

2. **Review Notices**  
   Check supported games and update information

3. **Select Game**  
   Choose the target game

4. **Read Instructions (Important)**

   > Always review popup messages and instruction panels before installation.  
   > Some games require pre-configuration before installation.

5. **Install**  
   Click the **Install** button

6. **Run Game & Apply Settings**  
   Follow in-game instructions (DLSS / Frame Generation, etc.)

7. **Restart Flow (Recommended)**  
   Load into a save → exit the game → launch again

8. **Verify Installation**  
   Press `Insert` to check OptiScaler overlay

---

## ⚠️ Mod Compatibility

> [!WARNING]
> This installer is intended for games in their original, unmodded state.
>
> Mods such as ReShade, Special K, or RenoDX may conflict with OptiScaler.
>
> Compatibility is not guaranteed.

---

## 🧹 Uninstallation

- Navigate to the game folder
- Locate one of the following files:
  - `dxgi.dll`
  - `winmm.dll`
  - `version.dll`
  - `OptiScaler.asi`
- Check file properties
- Delete the file identified as **OptiScaler**

---

## 🙏 Credits

All credit goes to the OptiScaler team for their work:

nitec, TheRazerMD, Fakemichau, Keu, By-U, Atsy, san9, Cryio

---

## 🔗 References

| Component | Repository |
| :--- | :--- |
| **OptiScaler** | https://github.com/optiscaler/OptiScaler |
| **OptiPatcher** | https://github.com/optiscaler/OptiPatcher |
| **Unreal 5 G/I Fix** | https://github.com/alxn1/d3d12-proxy |
| **REFramework** | https://github.com/praydog/reframework-nightly/releases |

---

![GitHub All Releases](https://img.shields.io/github/downloads/onehoon/OptiScalerInstaller/total?style=for-the-badge&color=orange)
![GitHub Downloads (latest Release)](https://img.shields.io/github/downloads/onehoon/OptiScalerInstaller/latest/total?style=for-the-badge&label=latest%20download&color=brightgreen)
