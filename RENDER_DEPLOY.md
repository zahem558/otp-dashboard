# Render.com par FREE deploy — 5 minute mein live dashboard

Tumhe milega: `https://otp-dashboard-xxxx.onrender.com` jaisa stable URL.
Yehi URL Twilio mein webhook ke liye use hoga.

## Steps

1. **render.com** kholo → **Sign up** (GitHub ya Google se, free)
2. Dashboard mein **New +** → **Web Service** → **Build and deploy from a Git repository**
   - Agar repo nahi hai: ye `otp-dashboard` folder ka zip banao aur Render ke
     "deploy from public Git repo" ke bajaye **"New Web Service"** mein
     GitHub connect karke repo banao (ya mujh se kaho, main GitHub repo bana dunga).
3. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `BIND_HOST=0.0.0.0 OTP_TOKEN=[apna-password] python app.py`
     (apna-password khud choose karo — yehi dashboard ka login hoga)
   - Plan: **Free**
4. **Create Web Service** dabao → 2-3 min mein live!

## Phir

- Dashboard: `https://TUMHARA-URL/?token=apna-password`
- Twilio number ke webhook mein ye daalo: `https://TUMHARA-URL/webhook/sms`
- Free numbers dashboard ke andar se add hote rahenge (free_numbers.json Render par
  alag save hoga — dobara add karne padenge, ek dafa ka kaam hai)

## Note
Free plan par 15 min idle ke baad service "so" jati hai — pehla khulna 30-50 sec
le sakta hai, uske baad normal. Webhook ke liye ye theek hai (Twilio retry karta hai).
Agar hamesha fast chahiye to $7/month ka Starter plan hai — zaroori nahi.
