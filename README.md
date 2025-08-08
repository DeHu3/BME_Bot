# Render BME Telegram Bot (Open Source)

User-friendly Telegram bot that reports Render Network Burn & Mint Equilibrium (BME) stats:
- `/price` — tier prices (EUR/200 OBh and EUR/OBh) from the official pricing page
- `/emissions` — current monthly scheduled emissions + RNP-018 breakdown
- `/burns` — (optional) recent burns via a Solana indexer if configured
- `/next_epoch` — (placeholder) link to the official dashboard for epoch timing

License: MIT. You can donate/hand this repo to the Render Foundation.

## Quick start

1) Create a bot at @BotFather and copy the token.
2) Create `.env` from `.env.example` and fill in your token.
3) Install deps and run:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m bot.main
```

### Optional: /burns via indexer
Add `SOLANA_INDEXER_API_KEY` (e.g., Helius) and keep `SOLANA_INDEXER_BASE` accordingly.
Without it, `/burns` replies with the official burn address link only.

### Data sources (official)
- Emissions schedule (Google Sheet, “Working Copy”) — linked from the Foundation FAQ.
- Pricing — https://rendernetwork.com/pricing
- Burn address — shown on the Foundation Dashboard.
