---
description: Keep CRM Mobile update metadata and the running Windows supervisor synchronized.
applyTo: 'crmhay-mobile/**,downloads/mobile-release.json,scripts/publish-mobile-apk.ps1,scripts/run-vps-supervisor.ps1'
---

# CRM Mobile release procedure

- Every mobile release must increment both `versionName` and `versionCode` in `crmhay-mobile/android/app/build.gradle`, and update the `VERSION` constant in `crmhay-mobile/src/main.js`.
- Build and publish the APK to the fixed backend path `downloads/crmhay-mobile.apk`; update `downloads/mobile-release.json` with the same version and version code.
- The running CRM process reads `CRM_MOBILE_VERSION` and `CRM_MOBILE_VERSION_CODE` at startup. After publishing, restart the single `run-vps-supervisor.ps1`/Waitress instance; otherwise the public `/api/mobile/version` endpoint may continue reporting an older release even when the manifest is newer.
- Verify both `http://127.0.0.1:5000/api/mobile/version` and `https://crmhay.cloud/api/mobile/version` before declaring an update complete. The response must report the newly published version and `update_available: true` for an older client.
- Avoid running duplicate supervisors or Waitress processes. Duplicate workers can bind different configurations and make update checks appear inconsistent.
