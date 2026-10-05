# Social preview

`social-preview-v1.png` is an original Minos Health design rendered from
`preview.html` at 1200 x 630 pixels. It uses the existing brand mark and
self-hosted Atkinson Hyperlegible Next fonts under the SIL OFL 1.1; see
`../fonts/OFL.txt`. No external artwork, source data or third-party runtime
request is involved. The HTML source is not served publicly.

To regenerate with an installed Chromium, run from the repository root:

```sh
~/.local/bin/agent-scratch --root /tmp/opencode -- bash -euo pipefail -c '
  chromium --headless=new --no-sandbox --disable-gpu \
    --disable-background-networking --no-first-run \
    --user-data-dir="$AGENT_SCRATCH_DIR/profile" \
    --allow-file-access-from-files --hide-scrollbars \
    --force-device-scale-factor=1 --window-size=1200,630 \
    --virtual-time-budget=2000 \
    --screenshot="$PWD/apps/web/social/social-preview-v1.png" \
    "file://$PWD/apps/web/social/preview.html"
'
uv run python scripts/build_site.py
```

Inspect the raster image before release. Rendering bytes can vary with browser
versions; the committed PNG, not an on-demand render, is the release artifact.
The validation edge serves only its exact versioned path. Its shared metadata
includes dimensions, type and descriptive alt text. The API-free maintenance
mode uses text-only cards, with no missing image reference or active-search
claim. No preload is added to normal pages.
