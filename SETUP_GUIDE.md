# 🚀 Project Albert — 5-Step Live Demo Setup Guide

## Prerequisites
- Python 3.10+ installed
- A Telegram account
- A second Telegram account (or a friend) to play the "counselor" role
- An LLM API key (Groq recommended for speed — free tier at [console.groq.com](https://console.groq.com))

---

## Step 1: Create the Bot via @BotFather

1. Open Telegram, search for **@BotFather**, and start a chat.
2. Send `/newbot`.
3. Choose a name (e.g., `Albert Safety Bot`) and a username (e.g., `albert_safety_bot` — must end in `bot`).
4. **Copy the bot token** — it looks like `7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
5. (Optional but recommended) Send `/setdescription` to BotFather and set:
   > "I'm Albert — a safe, anonymous support system for students facing cyberbullying or online fraud."
6. (Optional) Send `/setuserpic` and upload a friendly bot avatar.

---

## Step 2: Create the Counselor Group & Get Its Chat ID

1. In Telegram, create a **new group** (tap ☰ → New Group).
2. Name it something like `Albert Counselor Hub`.
3. **Add the bot** you just created (search by its username) to the group.
4. **Promote the bot to admin** (Group Settings → Administrators → Add Administrator → select the bot). It needs permission to send messages.
5. **Get the group's Chat ID** — this is a negative number. Use one of these methods:

   **Method A (easiest):** Send any message in the group, then open this URL in a browser:
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
   Look for `"chat":{"id":-100xxxxxxxxxx,...}` in the JSON response. The negative number is your `COUNSELOR_GROUP_ID`.

   **Method B:** Add `@RawDataBot` to the group temporarily. It will reply with the chat ID. Remove it after.

   **Method C:** Use the bot's getUpdates API after sending a message:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates" | python -m json.tool | grep -A2 '"chat"'
   ```

6. **Important:** Each counselor who will claim cases must also **start a private DM with the bot** by searching for the bot and sending `/start` in a direct message. This is required because the relay works through DMs — the bot can't DM someone who hasn't initiated contact.

---

## Step 3: Set Environment Variables

1. In the project folder (`TeamLC/`), copy the example env file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and fill in:
   ```env
   BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   COUNSELOR_GROUP_ID=-1001234567890
   GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxx
   ```

   - **GROQ_API_KEY**: Get a free key at [console.groq.com](https://console.groq.com). Sign up → API Keys → Create.
   - Alternatively, use `GEMINI_API_KEY` from [aistudio.google.com](https://aistudio.google.com/apikey).

---

## Step 4: Install Dependencies & Run

```bash
# Create a virtual environment (recommended)
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the bot
python main.py
```

You should see:
```
INFO albert: Starting Project Albert...
INFO albert: Bot configured. Handlers registered. Starting polling...
```

The bot is now live! 🎉

---

## Step 5: The 3-Minute Live Demo Script

### Setup (30 seconds)
- **Phone A** (or Telegram Desktop): Student's account — DM the bot
- **Phone B** (or second Telegram client): Counselor's account — in the Counselor Group AND has a private DM open with the bot

### Demo Flow

| Time | Who | Action |
|------|-----|--------|
| 0:00 | **Student** | Send `/start` to the bot in DM |
| 0:10 | **Student** | Tap **🆘 Report an Issue** |
| 0:15 | **Student** | Answer Q1: *"Someone from school is sharing my photos in a group chat and making fun of me"* |
| 0:25 | **Student** | Answer Q2: *"I feel scared and embarrassed, I don't want to go to school"* |
| 0:35 | **Student** | Answer Q3: *"It's someone from my class, I know them"* |
| 0:40 | **Bot** | Shows "Analyzing your situation…" then routes based on LLM classification |
| 0:50 | **Counselor** | Check the **Counselor Group** — a 🚨 alert should appear with a **[Claim]** button |
| 1:00 | **Counselor** | Tap **✋ Claim Case-XXXX** |
| 1:05 | **Student** | See "🟢 A counselor has connected!" message |
| 1:10 | **Student** | Type: *"I'm really scared, please help"* |
| 1:15 | **Counselor** | See the relayed message in their **private DM** with the bot (no student name/profile visible) |
| 1:20 | **Counselor** | Reply: *"I hear you. You're not alone. Let's talk about what we can do."* |
| 1:25 | **Student** | See the counselor's reply (no counselor name visible) |
| 1:30 | **Narrator** | Point out: "Notice neither party can see the other's identity — fully anonymous relay" |
| 2:00 | **Student** | Tap **🚨 Panic Wipe** |
| 2:05 | **Both** | Chat is replaced with fake NCERT Math content — all session data destroyed |
| 2:15 | **Narrator** | "If a parent or bully grabs the phone, they see only a homework reference. All data is gone — nothing stored on disk, nothing to recover." |
| 2:30 | **Student** | Send `/start` — bot works fresh, no history |

### Optional POCSO Demo (if time permits)
| Time | Who | Action |
|------|-----|--------|
| 2:35 | **Student** | Tap **🆘 Report an Issue** |
| 2:40 | **Student** | Answer Q1: *"An older man online asked me to send photos of myself"* |
| 2:45 | **Student** | Answer Q2: *"I feel very scared and confused"* |
| 2:50 | **Student** | Answer Q3: *"A stranger online"* |
| 2:55 | **Bot** | Immediately shows Childline 1098 redirect — does NOT route to counselor |
| 3:00 | **Narrator** | "POCSO-flagged cases are immediately routed to Childline — they're out of scope for volunteer counselors." |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Bot doesn't respond | Check `BOT_TOKEN` is correct. Make sure `python main.py` is running without errors. |
| No alert in counselor group | Check `COUNSELOR_GROUP_ID` is correct (must be negative, e.g., `-1001234567890`). Make sure the bot is an admin in the group. |
| "Could not DM counselor" | The counselor must send `/start` to the bot in a **private DM** first. Telegram bots can't initiate DMs. |
| LLM classification fails | Check your `GROQ_API_KEY` or `GEMINI_API_KEY`. The bot falls back to MEDIUM severity (safe default). |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` inside your activated virtual environment. |
| Callback query errors | Make sure you're using `python-telegram-bot` v21.6 (v20+ async API). v13 is incompatible. |

---

## Known Limitations

These are intentional trade-offs for a hackathon MVP:

1. **In-memory only** — All data lives in Python dicts. A restart wipes everything. This is a feature (privacy), but production would need an encrypted DB with TTL-based auto-purge.
2. **Single-process** — No horizontal scaling. One bot instance handles everything.
3. **No persistent audit trail** — No case history survives a restart. Production needs encrypted, access-controlled logging for legal compliance.
4. **No media relay** — Only plain text is relayed between student and counselor. Images, voice, stickers, etc. are not supported.
5. **No counselor authentication** — Anyone in the counselor group can claim cases. Production needs role-based access control.
6. **One active case per user** — A student can only have one open case at a time. A counselor can only handle one case at a time.
7. **No rate limiting** — No protection against spam or abuse of the triage flow.
8. **Polling, not webhooks** — Fine for development, but production should use webhooks behind a reverse proxy for lower latency and better reliability.
