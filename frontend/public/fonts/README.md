# Fonts

This app self-hosts two licensed typefaces from **Fontshare** (free, no login)
instead of using Google's default rotation. Download both and drop the `.woff2`
files here:

1. **Gambarino** (display serif — title & verdict)
   https://www.fontshare.com/fonts/gambarino
   → save the Regular weight as `Gambarino-Regular.woff2`

2. **Satoshi** (grotesque — body, labels, data)
   https://www.fontshare.com/fonts/satoshi
   → save the Variable file as `Satoshi-Variable.woff2`

On each Fontshare page: click **Download Family**, unzip, and copy the `.woff2`
files here with the exact names above.

If these files are missing the page still works — the CSS falls back to a
high-contrast system serif (Hoefler Text / Georgia) for display and a clean
system sans for body. It just won't be the intended type.
