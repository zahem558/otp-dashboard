# Personal OTP Dashboard

Apne rented virtual numbers par aane wale OTP SMS ek hi dashboard par dekho.
Twilio / Plivo / Telnyx webhooks supported. OTP code auto-extract hota hai.

## Files
- `app.py` — Flask server: `/webhook/sms` receiver + `/` dashboard + `/api/messages`
- `templates/dashboard.html` — dashboard UI (auto-refresh har 5 sec, number filter, copy OTP, delete)
- `otp.db` — SQLite database (auto-banta hai, pehli run par)
- `free_inbox.py` — free public inboxes ka poller (har 90s, best-effort parser)
- `free_numbers.json` — tumhare added free inboxes (auto-banta hai)
- `otp-dashboard.service` — systemd unit (optional, auto-start ke liye)

## 1) Server chalao (is machine par)
```bash
cd ~/workspace/otp-dashboard
OTP_TOKEN='koi-mazboot-password' PORT=5057 ./venv/bin/python app.py
```
Dashboard khulega: `http://127.0.0.1:5057/?token=koi-mazboot-password`

> `OTP_TOKEN` zaroor set karo — ye tumhare dashboard ka password hai.

## 2) Public HTTPS URL banao (Twilio ko chahiye)
Twilio sirf public HTTPS webhook par SMS bhejta hai. Free tareeqa — Cloudflare Tunnel:
```bash
cloudflared tunnel --url http://127.0.0.1:5057
```
Jo `https://....trycloudflare.com` URL mile, wahi webhook URL hoga.

## 3) Twilio se number rent karo
1. twilio.com → trial ya paid account → **Phone Numbers → Buy a number** (US number ≈ $1.15/month, SMS receive ≈ $0.0075/msg)
2. Number kholo → **Messaging → A MESSAGE COMES IN → Webhook**:
   `https://TUMHARA-URL/webhook/sms` (HTTP POST)
3. Save. Ab is number par jo bhi SMS aayega, dashboard par OTP ke saath show hoga.

Multiple numbers: jitne chaho rent karo, sab ka webhook same URL par point karo —
dashboard har number ko alag filter karke dikhata hai.

## 4) Plivo / Telnyx
- **Plivo:** number ki Message URL = `https://TUMHARA-URL/webhook/sms` (form POST auto-handle hota hai)
- **Telnyx:** webhook JSON bhejta hai — `/webhook/sms` generic JSON bhi samajhta hai

## 5) Auto-start (optional, systemd)
```bash
# pehle service file mein apna token likho:
nano ~/workspace/otp-dashboard/otp-dashboard.service   # __APNA_TOKEN_YAHAN_LIKHO__ replace karo
sudo cp ~/workspace/otp-dashboard/otp-dashboard.service /etc/systemd/system/
sudo systemctl enable --now otp-dashboard
```

## 6) Free numbers bhi add karo (public inboxes)
Dashboard mein **📱 Free Numbers** section hai:
- **One-tap buttons**: receive-smss.com, quackr.io, sms24.me, PVAPins free — site kholo, koi number choose karo
- **Inbox add karo**: us number ke inbox page ka URL copy karke dashboard mein paste kar do (Label + URL → Add). Dashboard har ~90 second mein naye SMS auto-fetch karega, FREE badge ke saath.
- **↻ Abhi check karo**: foran refresh ke liye.

**Honest limit**: free sites scraping ko rokti hain (Cloudflare, JS apps, unlock walls). Jo site auto-fetch block kare, us ke messages dashboard mein nahi aayenge — wahan har entry par **"Site kholo ↗"** button hai, seedha site par dekh lo. Ye unki taraf se hai, dashboard ka qusoor nahi. Config `free_numbers.json` mein save hoti hai.

## Notes / limits (honest)
- **Numbers free nahi hote** — carrier (Twilio waghera) se rent karne padte hain.
- Kuch services (banks, WhatsApp kabhi-kabhi) virtual/VoIP numbers block kar deti hain — ye unki policy hai, is system ka qusoor nahi.
- Dashboard sirf token walon ke liye khulta hai; webhook URL public hota hai (Twilio ko chahiye) lekin us par sirf SMS receive hota hai, koi data leak nahi.
- Purane messages delete karne ke liye dashboard mein har card par Delete button hai.
