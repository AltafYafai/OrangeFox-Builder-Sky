# Leaked OrangeFox Recovery Builder

This repository contains a GitHub Actions workflow to build OrangeFox Recovery using a custom "leaked" recovery source.

## Customization
- The workflow uses `https://github.com/wojtekojtek/leaked_bootable_recovery` with branch `fox_12.1_dev_svg` for the `bootable/recovery` directory.
- Default device is set to `sky` (Redmi 12 5G).

## Usage
1. Push this repository to GitHub.
2. Go to **Actions** -> **OrangeFox Build (Leaked)**.
3. Click **Run workflow**.
