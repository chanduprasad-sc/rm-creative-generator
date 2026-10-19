# HDFC Festive Bonanza — Streamlit

This repository contains the deployable Streamlit Community Cloud version of the RM Creative Generator.

## Current contest

HDFC — Festive Bonanza. The app selects the purple template for **0–700 runs inclusive** and the brown template for **701 runs and above**. The original 2884 × 3345 PNG resolution is preserved.

The purple layout includes a circular RM portrait, RM name, supplied team/branch details, Total Runs and optional Total Fees. The brown layout includes a larger portrait, RM name, supplied team/branch details, Total Runs, optional Total Fees and optional Reward Points. The portrait background is removed with rembg and placed over the supplied template background. Missing optional fields have no labels; SH/RH separators appear only between supplied values. Supplied Reward Points on the purple layout are reported as unsupported, since that design has no reward-points area. No reward amounts or fees are inferred from runs.

Template PNGs are in `assets/hdfc-festive-0-700.png` and `assets/hdfc-festive-above-700.png`. Positions and styles are in `references/template-layouts.json`. The previous contest assets are unused.

## Deploy

1. Create a private GitHub repository.
2. Upload everything in this folder, preserving the folder structure.
3. In Streamlit Community Cloud, create an app from the repository.
4. Set the entrypoint to `streamlit_app.py` and choose Python 3.11.
5. In Advanced settings → Secrets, add:

   ```toml
   ANTHROPIC_API_KEY = "your-real-key"
   CLAUDE_MODEL = "claude-haiku-4-5-20251001"
   ```

6. Deploy and generate one test creative.

For an existing app, upload the updated `streamlit_app.py`, `renderer/generate_creative.py`, `references/template-layouts.json`, and the two new PNGs to the same paths in GitHub. Keep the existing Streamlit secrets. Commit the changes and allow Streamlit to redeploy; reboot it from the app management menu if it does not refresh.

Each completed generation displays input, output and total tokens plus estimated API cost in USD and INR. Haiku 5.5 uses $0.10 input / $0.50 output per million tokens for prompts up to 100,000 input tokens, and $0.50 / $2.50 above that threshold. Sonnet 5 / 5.5 uses $2 / $10. These are standard uncached API estimates, not billing statements. The current extraction request does not enable prompt caching.

The INR conversion defaults to an approximate **₹95.00 per USD**, checked on 19 October 2026 against https://upstox.com/currency-converter/usd-to-inr/. It is not fetched live. Optionally add `USD_TO_INR = "95.00"` in Streamlit Secrets to set your own conversion rate. Taxes, card fees and hosting are excluded. Rates: https://www.anthropic.com/claude-haiku-5-5.

Do not commit `.streamlit/secrets.toml`, photographs, generated creatives, or API keys.

The first creative after a fresh deployment can take longer because the background-removal model must be downloaded and initialized.

`packages.txt` installs the condensed display font used for the approved name and metric hierarchy on Streamlit's Debian environment.
