# 5 Mins Momo Trades — Scanner

EMA 20 + MACD 12/26/9 momentum scanner for NIFTY50, NIFTYBANK & SENSEX.

## Files
```
momo_scanner/
├── app.py              # Flask backend + Fyers API + signal logic
├── templates/
│   ├── scanner.html    # Main scanner page
│   └── history.html    # History + analytics page
├── requirements.txt
├── Procfile            # Render deployment
└── README.md
```

## Deploy to Render

1. Push this folder to a GitHub repository

2. Go to https://render.com → New → Web Service → connect your repo

3. Set these environment variables in Render dashboard:
   ```
   FYERS_APP_ID       = EMRCD1JW93-100
   FYERS_SECRET_ID    = your_secret_here
   FYERS_ACCESS_TOKEN = your_daily_token_here
   ```

4. Build command:  `pip install -r requirements.txt`
   Start command:  `gunicorn app:app --workers 2 --bind 0.0.0.0:$PORT --timeout 120`

## Daily Token Refresh

Fyers access tokens expire every 24 hours. Each trading day:
1. Run the auth flow (Cells 3+4 from your Colab notebook)
2. Copy the new ACCESS_TOKEN
3. Update `FYERS_ACCESS_TOKEN` in Render → Environment → Save
4. Render auto-restarts with the new token (takes ~30 seconds)

## Strategy Parameters (from backtest — 15min best, 5min used here)

| Parameter     | Value |
|---------------|-------|
| EMA Period    | 20    |
| MACD Fast     | 12    |
| MACD Slow     | 26    |
| MACD Signal   | 9     |
| Entry Offset  | 15 pts|
| Stop Buffer   | 20 pts|
| MACD Bars     | 5     |
| R:R Target    | 1:2.5 |

## Rescan Logic

After market close, click **Rescan** on the History page.
For each pending/expired trade it fetches today's 5min bars and:
- **Expired**: Entry price never touched during session
- **Pending**: Entry hit, but neither TP nor SL triggered yet
- **Target Hit**: T1 or T2 reached after entry
- **Stop Hit**: SL triggered after entry
