<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue?logo=python&logoColor=white" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram&logoColor=white" alt="Telegram Bot API"/>
  <img src="https://img.shields.io/badge/LLM-Groq%20%7C%20Gemini-8B5CF6?logo=openai&logoColor=white" alt="LLM Powered"/>
  <img src="https://img.shields.io/badge/License-MIT-green" alt="MIT License"/>
</p>

# 🛡️ Project Albert

**An AI-powered, privacy-first child safety bot for Telegram** — built for Indian students (ages 10–16) facing cyberbullying and online fraud.

Albert acts as a safe, anonymous bridge between distressed children and trained volunteer counselors, using AI-driven triage to assess severity and route cases appropriately — while keeping the child's identity completely hidden.

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| **🤖 AI-Powered Triage** | 3-question screening classified by LLM into LOW / MEDIUM / HIGH / POCSO severity |
| **🔒 Anonymous Relay** | Students and counselors communicate through the bot — neither party sees the other's Telegram identity |
| **🚨 Panic Wipe** | One tap replaces the entire chat with fake NCERT Math content — all session data is permanently destroyed |
| **👩‍🏫 Teacher/Adult Mode** | Dedicated guidance flow for teachers and adults helping students, with actionable advice on emotional de-escalation |
| **🎙️ Voice Notes** | Accepts voice notes from distressed children, transcribes them using Groq Whisper, and routes the text automatically |
| **📋 Incident Dossiers** | Counselors can generate and export anonymized Markdown reports (`/export`) of cases for school administration or legal use |
| **📞 POCSO Auto-Redirect** | Cases involving sexual abuse are immediately routed to Childline 1098 — never to volunteer counselors |
| **💙 Emotionally Warm AI** | Albert speaks like a caring older friend, not a clinical form — designed to make scared children feel safe enough to open up |
| **🧠 Smart Validation** | Multi-layer answer validation prevents off-topic responses (trivia, jokes, greetings) from derailing the triage flow |
| **🕵️ Zero Persistence** | All data lives in-memory only — a restart wipes everything. Ephemerality IS the privacy feature |

---

## 🏗️ Architecture

```
                          ┌──────────────┐
                 /start   │ STATE_START  │
              ┌──────────>│ (Main Menu)  │<─────────────────────────┐
              │           └──────┬───────┘                          │
              │                  │ [Report Issue]                   │ /start
              │                  ▼                                  │ (reset)
              │           ┌──────────────┐                          │
              │           │ STATE_TRIAGE │──── POCSO detected ─────>│
              │           │ (3 Questions)│     at any question      │
              │           └──────┬───────┘                          │
              │                  │ All 3 answered                   │
              │                  ▼                                  │
              │       ┌────────────────────┐                        │
              │       │ LLM CLASSIFICATION │                        │
              │       └────────┬───────────┘                        │
              │                │                                    │
              │     ┌──────────┼──────────┬──────────┐              │
              │     ▼          ▼          ▼          ▼              │
              │ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────────┐   │
              │ │  LOW   │ │MEDIUM  │ │ HIGH   │ │   POCSO      │   │
              │ │Self-   │ │Self-   │ │Queued  │ │  Redirect    │───┘
              │ │Help    │ │Help +  │ │for     │ │(Childline    │
              │ │        │ │option  │ │Counsel │ │ 1098)        │
              │ └───┬────┘ └───┬────┘ └───┬────┘ └──────────────┘
              │     │          │          │
              │     │   [Talk to Human]   │
              │     └─────────┬───────────┘
              │               ▼
              │     ┌──────────────────┐
              │     │ COUNSELOR RELAY  │
              │     │ (Anonymous DMs)  │
              │     └────────┬─────────┘
              │              │ [Close Case] or /start
              │              ▼
              │        Back to START
              │
              │  ── /wipe or [Panic Wipe] at ANY state ──
              │              │
              │              ▼
              │     ┌──────────────────┐
              └─────│ STATE_WIPED      │
                    │ (Decoy shown)    │
                    └──────────────────┘
```

---

## 📋 Prerequisites

- **Python 3.10+** ([python.org](https://www.python.org/downloads/))
- **A Telegram account** (for the student)
- **A second Telegram account** (for the counselor) — or a friend/team member
- **An LLM API key** — either:
  - [Groq](https://console.groq.com) (recommended — free tier, fast inference)
  - [Google Gemini](https://aistudio.google.com/apikey) (alternative)

---

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/samp130/albert-safety-bot.git
cd albert-safety-bot
```

### 2. Create the Telegram Bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts.
3. Choose a name (e.g., `Albert Safety Bot`) and a username (must end in `bot`, e.g., `albert_safety_bot`).
4. **Copy the bot token** — it looks like `7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
5. *(Optional)* Send `/setdescription` to BotFather:
   > "I'm Albert — a safe, anonymous support system for students facing cyberbullying or online fraud."

### 3. Create the Counselor Group

1. In Telegram, create a **new group** (☰ → New Group).
2. Name it something like `Albert Counselor Hub`.
3. **Add your bot** to the group (search by its username).
4. **Promote the bot to admin** (Group Settings → Administrators → Add the bot). It needs permission to send messages.
5. **Get the group's Chat ID** using one of these methods:

   **Method A (easiest):** Send any message in the group, then open this URL in a browser:
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
   Look for `"chat":{"id":-100xxxxxxxxxx,...}` — that negative number is your `COUNSELOR_GROUP_ID`.

   **Method B:** Run the included helper script:
   ```bash
   python get_group_id.py
   ```
   Then send a message in the counselor group — the script will print the ID.

   **Method C:** Temporarily add `@RawDataBot` to the group. It will reply with the chat ID. Remove it after.

6. **Important:** Each counselor must also **start a private DM** with the bot by sending `/start` in a direct message. Telegram bots can't initiate DMs — the counselor must message first.

### 4. Configure Environment Variables

```bash
# Copy the example file
cp .env.example .env     # macOS/Linux
copy .env.example .env   # Windows
```

Edit `.env` with your actual values:

```env
BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
COUNSELOR_GROUP_ID=-1001234567890
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxx
# GEMINI_API_KEY=your-gemini-key-here  (alternative to Groq)
```

| Variable | Where to Get It |
|----------|----------------|
| `BOT_TOKEN` | From @BotFather (Step 2) |
| `COUNSELOR_GROUP_ID` | From the group (Step 3) — must be a negative number |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) → API Keys → Create |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (if not using Groq) |

### 5. Install Dependencies & Run

```bash
# Create a virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the bot
python main.py
```

You should see:
```
INFO albert: Starting Project Albert...
INFO albert: Bot configured. Handlers registered. Starting polling...
INFO albert: Albert is live. Press Ctrl+C to stop.
```

🎉 **Albert is now live on Telegram!**

---

## 🎬 Demo Walkthrough (3 Minutes)

### Setup
- **Phone A** (Student): Open a DM with the bot
- **Phone B** (Counselor): In the Counselor Group AND has a private DM open with the bot

### Flow

| Time | Role | Action |
|------|------|--------|
| 0:00 | 👧 Student | Send `/start` → Tap **🆘 I Need Help** |
| 0:15 | 👧 Student | Answer Q1: *"Someone from school is sharing my photos in a group chat and making fun of me"* |
| 0:25 | 👧 Student | Answer Q2: *"I feel scared and embarrassed, I don't want to go to school"* |
| 0:35 | 👧 Student | Answer Q3: *"It's someone from my class, I know them"* |
| 0:40 | 🤖 Albert | Analyzes → Routes to counselor (HIGH severity) |
| 0:50 | 👨‍⚕️ Counselor | Sees 🚨 alert in group → Taps **✋ Claim** |
| 1:05 | 👧 Student | Sees "A counselor has connected!" |
| 1:10 | 👧↔️👨‍⚕️ | **Anonymous relay** — messages flow through the bot, no identities visible |
| 2:00 | 👧 Student | Taps **🚨 Panic Wipe** |
| 2:05 | 🤖 Albert | Chat replaced with fake NCERT Math content — all data destroyed |

### POCSO Test (Optional)
| Time | Role | Action |
|------|------|--------|
| 2:35 | 👧 Student | `/start` → **🆘 I Need Help** |
| 2:40 | 👧 Student | Q1: *"An older man online asked me to send photos of myself"* |
| 2:55 | 🤖 Albert | Immediately shows **Childline 1098** redirect — does NOT route to counselor |

### Teacher Mode Test
| Time | Role | Action |
|------|------|--------|
| 3:00 | 👩‍🏫 Teacher | `/start` → **👩‍🏫 I'm a Teacher / Adult Helper** |
| 3:05 | 👩‍🏫 Teacher | Describes student's situation |
| 3:10 | 🤖 Albert | Provides guidance on emotional de-escalation, practical steps, and when to escalate |

---

## 🔒 Privacy & Safety Design

| Principle | Implementation |
|-----------|---------------|
| **Zero storage** | All state lives in Python dicts — nothing on disk, no database. A restart = full wipe. |
| **Anonymous relay** | Messages are re-sent by the bot. Neither student nor counselor ever sees the other's Telegram profile, name, or ID. |
| **Panic Wipe** | Deletes all bot messages from the chat and replaces with fake NCERT content. Available at every stage. |
| **No PII collection** | Albert never asks for names, school names, phone numbers, or addresses. |
| **POCSO firewall** | Sexual abuse cases are immediately redirected to Childline 1098 — never handled by volunteer counselors. |
| **Hashed logging** | Internal logs use SHA-256 hashed chat IDs — never raw identifiers. |

---

## 📁 Project Structure

```
albert-safety-bot/
├── main.py              # Core bot — state machine, triage, relay, all logic
├── get_group_id.py      # Helper script to find your counselor group's Chat ID
├── requirements.txt     # Python dependencies (pinned versions)
├── .env.example         # Template for environment variables
├── .gitignore           # Keeps secrets and build artifacts out of git
├── SETUP_GUIDE.md       # Detailed setup + 3-minute demo script
├── TRIAGE_PROMPT.md     # LLM triage system prompt documentation
└── README.md            # This file
```

---

## 🛠️ Troubleshooting

| Problem | Solution |
|---------|----------|
| Bot doesn't respond | Check `BOT_TOKEN` is correct. Make sure `python main.py` is running. |
| No alert in counselor group | Verify `COUNSELOR_GROUP_ID` (must be negative, e.g., `-1001234567890`). Ensure bot is an admin in the group. |
| "Could not DM counselor" | The counselor must send `/start` to the bot in a **private DM** first. |
| LLM classification fails | Check your `GROQ_API_KEY` or `GEMINI_API_KEY`. Bot falls back to MEDIUM severity (safe default). |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` inside your activated virtual environment. |
| Callback query errors | Ensure `python-telegram-bot` v21.6 (v20+ async API). v13 is incompatible. |

---

## ⚠️ Known Limitations

> These are intentional trade-offs for a hackathon MVP.

1. **In-memory only** — A restart wipes all data. Production needs an encrypted DB with TTL-based auto-purge.
2. **Single-process** — No horizontal scaling. One instance handles everything.
3. **No persistent audit trail** — No case history survives a restart.
4. **Media Support** — Images, stickers, and files are not relayed. Voice Notes are supported (transcribed and relayed as text).
5. **No counselor authentication** — Anyone in the counselor group can claim cases.
6. **One case per user** — Each student/counselor can only handle one active case at a time.
7. **No rate limiting** — No protection against spam or triage abuse.
8. **Polling (not webhooks)** — Fine for development; production should use webhooks.

---

## 🤝 Built With

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v21.6 — Async Telegram Bot API wrapper
- [Groq](https://console.groq.com) — Fast LLM inference (primary)
- [Google Gemini](https://aistudio.google.com) — LLM fallback
- [python-dotenv](https://github.com/theskumar/python-dotenv) — Environment variable management

---

## 📜 License

This project is open-source under the [MIT License](LICENSE).

---

<p align="center">
  <b>Built with 💙 for the safety of every child online.</b>
</p>
