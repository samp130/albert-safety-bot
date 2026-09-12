#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        PROJECT ALBERT — main.py                            ║
║                                                                            ║
║  Child-Safety Triage & Counselor Relay Bot for Telegram                    ║
║  Built with python-telegram-bot v20+ (async API)                           ║
║                                                                            ║
║  Target users: Indian students (ages 10–16) facing cyberbullying           ║
║  and online fraud.                                                         ║
║                                                                            ║
║  PRIVACY: All state is in-memory only. This is intentional.                ║
║  Data does not survive a restart — ephemerality IS the privacy feature.    ║
║  In production, swap the in-memory dicts for an encrypted, access-         ║
║  controlled datastore with audit logging and TTL-based expiration.         ║
╚══════════════════════════════════════════════════════════════════════════════╝

STATE MACHINE DIAGRAM
=====================

                          ┌──────────────┐
                 /start   │ STATE_START   │
              ┌──────────>│ (Main Menu)  │<──────────────────────────┐
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
              │       │STATE_AWAITING_     │                        │
              │       │SEVERITY            │                        │
              │       │(LLM classifying)   │                        │
              │       └────────┬───────────┘                        │
              │                │                                    │
              │     ┌──────────┼──────────┬──────────┐              │
              │     ▼          ▼          ▼          ▼              │
              │ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────────┐  │
              │ │  LOW   │ │MEDIUM  │ │ HIGH   │ │   POCSO      │  │
              │ │SELF_   │ │SELF_   │ │QUEUED_ │ │  REDIRECT    │──┘
              │ │HELP    │ │HELP +  │ │FOR_    │ │(Childline    │
              │ │        │ │option  │ │COUNSEL │ │ 1098)        │
              │ └───┬────┘ └───┬────┘ └───┬────┘ └──────────────┘
              │     │          │          │
              │     │   [Talk to Human]   │
              │     └─────────┬───────────┘
              │               ▼
              │     ┌──────────────────┐
              │     │STATE_QUEUED_FOR_ │
              │     │COUNSELOR         │
              │     │(Awaiting claim)  │
              │     └────────┬─────────┘
              │              │ Counselor [Claim]
              │              ▼
              │     ┌──────────────────┐
              │     │STATE_COUNSELOR_  │
              │     │ACTIVE            │
              │     │(Relay is live)   │
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

  From STATE_WIPED: only /start can re-enter the flow.
  From STATE_POCSO_REDIRECT: only /start can re-enter the flow.
"""

# ==============================================================================
# SECTION: IMPORTS & CONFIGURATION
# ==============================================================================

import asyncio
import enum
import hashlib
import json
import logging
import os
import random
import re
import sys
from datetime import datetime, timezone

# Load .env before anything else so BOT_TOKEN etc. are available
from dotenv import load_dotenv

load_dotenv()

# pyrefly: ignore [missing-import]
import httpx
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# LLM client — we support both Groq and Google Generative AI.
# The code tries Groq first; falls back to Gemini if GROQ_API_KEY is absent.
try:
    from groq import AsyncGroq

    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

try:
    import google.generativeai as genai

    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuration — all from environment variables, never hardcoded.
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
COUNSELOR_GROUP_ID: int = int(os.environ.get("COUNSELOR_GROUP_ID", "0"))
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")

if not BOT_TOKEN:
    sys.exit("FATAL: BOT_TOKEN environment variable is not set. Exiting.")
if COUNSELOR_GROUP_ID == 0:
    sys.exit("FATAL: COUNSELOR_GROUP_ID environment variable is not set. Exiting.")

# ---------------------------------------------------------------------------
# Logging — INFO level, never logs raw chat_id or message content.
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("albert")

# ==============================================================================
# SECTION: STATE ENUM
# ==============================================================================


class State(enum.Enum):
    """
    Explicit state machine states. Each chat_id is in exactly one state at a time.
    See the ASCII diagram at the top of this file for transitions.
    """

    STATE_START = "start"
    STATE_TRIAGE = "triage"
    STATE_AWAITING_SEVERITY = "awaiting_severity"
    STATE_SELF_HELP = "self_help"
    STATE_QUEUED_FOR_COUNSELOR = "queued_for_counselor"
    STATE_COUNSELOR_ACTIVE = "counselor_active"
    STATE_POCSO_REDIRECT = "pocso_redirect"
    STATE_WIPED = "wiped"
    STATE_TEACHER_GUIDANCE = "teacher_guidance"


# ==============================================================================
# SECTION: IN-MEMORY STORES
# ==============================================================================
#
# DESIGN NOTE (INTENTIONAL — HACKATHON):
# All state is held in plain Python dicts. Nothing is persisted to disk, DB,
# or any external store. This means:
#   1. A restart wipes everything — this IS a privacy feature.
#   2. No horizontal scaling (single-process only).
#   3. No audit trail survives the process lifetime.
#
# In production, replace these with an encrypted datastore (e.g., PostgreSQL
# with column-level encryption, row-level security, and TTL-based auto-purge)
# that is access-controlled and audit-logged.
# ==============================================================================

# Per-user session: chat_id -> session dict
# Session dict structure:
# {
#     "state": State,
#     "triage_question_index": int,      # 0, 1, or 2 — which question we're on
#     "triage_answers": list[str],        # answers collected so far
#     "case_id": Optional[str],           # if a case is open for this user
#     "last_menu_message_id": Optional[int],  # for edit_message_text
# }
sessions: dict[int, dict] = {}

# Case registry: case_id -> case dict
# Case dict structure:
# {
#     "student_chat_id": int,
#     "counselor_chat_id": Optional[int],  # None until claimed
#     "status": "pending" | "active" | "closed",
#     "severity": str,
#     "summary": str,
#     "created_at": str,  # ISO timestamp
#     "group_message_id": Optional[int],  # the alert msg in the counselor group
# }
cases: dict[str, dict] = {}

# Reverse lookup: student chat_id -> case_id (for routing relay messages)
student_to_case: dict[int, str] = {}

# Reverse lookup: counselor private chat_id -> case_id (for routing relay messages)
counselor_to_case: dict[int, str] = {}

# Monotonically increasing case counter (simple, predictable — fine for hackathon)
_case_counter: int = 0


def _next_case_id() -> str:
    """Generate the next case ID. Uses a random suffix for slight non-guessability."""
    global _case_counter
    _case_counter += 1
    rand_part = random.randint(100, 999)
    return f"Case-{_case_counter}{rand_part}"


def _hash_chat_id(chat_id: int) -> str:
    """
    Hash a chat_id for any display/logging purposes.
    DPDPA 2023 compliance: raw chat_id must never appear in output text.
    """
    return hashlib.sha256(str(chat_id).encode()).hexdigest()[:8]


def _get_session(chat_id: int) -> dict:
    """Get or create a session for a chat_id."""
    if chat_id not in sessions:
        sessions[chat_id] = {
            "state": State.STATE_START,
            "triage_question_index": 0,
            "triage_answers": [],
            "case_id": None,
            "last_menu_message_id": None,
            "message_ids": [],  # Track ALL message IDs for full chat wipe
        }
    # Ensure message_ids exists for sessions created before this update
    if "message_ids" not in sessions[chat_id]:
        sessions[chat_id]["message_ids"] = []
    return sessions[chat_id]


def _set_state(chat_id: int, new_state: State) -> None:
    """Transition a user to a new state, with logging (no PII)."""
    session = _get_session(chat_id)
    old_state = session["state"]
    session["state"] = new_state
    hashed = _hash_chat_id(chat_id)
    logger.info(
        "State transition: user_hash=%s  %s -> %s",
        hashed,
        old_state.value,
        new_state.value,
    )


def _track_msg(chat_id: int, message_id: int) -> None:
    """
    Record a message ID (bot-sent or user-sent) for later deletion during panic wipe.
    This enables full chat wipe — every tracked message gets deleted.
    """
    session = _get_session(chat_id)
    session["message_ids"].append(message_id)


# ==============================================================================
# SECTION: TRIAGE SYSTEM PROMPT & LLM STUB
# ==============================================================================

# The exact system prompt for the triage LLM call.
# This is also Deliverable 3 — reproduced here so it ships with the code.
TRIAGE_SYSTEM_PROMPT = """You are a child-safety triage classifier for "Albert", a support system for Indian students aged 10–16 who face cyberbullying or online fraud.

YOUR ONLY JOB IS CLASSIFICATION. You must NEVER attempt to counsel, diagnose, comfort, or resolve the situation yourself. You are not a therapist. You are a triage sorter.

You will receive the student's answers to exactly 3 short screening questions:
  Q1: "Can you briefly tell me what happened? (one or two sentences is fine)"
  Q2: "How is this making you feel right now? (e.g., scared, angry, sad, confused)"
  Q3: "Is the person who did this someone you know in real life, or a stranger online?"

Based on the 3 answers, classify the situation into EXACTLY ONE of these severity levels:

  LOW — Minor teasing, one-time event, student seems emotionally stable, no ongoing threat.
  MEDIUM — Repeated incidents, moderate emotional distress, possible ongoing contact with the harasser but no immediate danger.
  HIGH — Severe distress, threats of violence or blackmail, doxxing, impersonation, financial fraud with significant loss, or any situation where the student may be in immediate emotional or physical danger.
  POCSO — ANY indication of sexual abuse, sexual exploitation, grooming, sextortion, sexually explicit content involving a minor (CSAM), or an adult soliciting sexual contact/images from the child. THIS CATEGORY IS MANDATORY whenever there is even a HINT of sexual content. Do NOT downgrade. Do NOT classify as HIGH.

ESCALATION BIAS (MANDATORY):
  This is a child-safety system. False negatives (under-classifying danger) are FAR more harmful than false positives (over-classifying). When in doubt between two severity levels, ALWAYS choose the HIGHER one.
  If there is ANY ambiguity about whether something is POCSO, classify it as POCSO.

OUTPUT FORMAT — STRICT JSON ONLY:
  Return ONLY a JSON object. No prose. No markdown fences. No explanation. No extra keys.
  Schema:
  {
    "severity": "LOW" | "MEDIUM" | "HIGH" | "POCSO",
    "summary": "<one-line summary for the counselor, max 120 chars, no PII, no real names>",
    "recommended_action": "<one-line recommendation, e.g. 'self-help resources' or 'immediate counselor escalation'>"
  }

If any of the answers are empty, nonsensical, or you cannot make a determination, default to:
  {"severity": "MEDIUM", "summary": "Unable to fully assess — routing to human counselor for safety.", "recommended_action": "immediate counselor review"}

NEVER output anything other than the JSON object. NEVER wrap it in markdown code fences."""

# The three triage questions, asked sequentially.
TRIAGE_QUESTIONS = [
    "Hey, I'm really glad you're here. 💙 Can you tell me a little bit about what happened? Even just a sentence or two is perfectly fine — there's no rush at all. 💬",
    "Thank you for sharing that with me — that took courage. 💛 How are you feeling about all of this right now? Scared, angry, sad, confused… whatever you're feeling is completely okay. 💭",
    "You're doing really well. 🌟 One last thing — is the person who did this someone you know in real life (like from school or your neighbourhood), or is it someone you only know online? 🤔",
]


async def run_triage_llm(answers: list[str]) -> dict:
    """
    Call the LLM (Groq or Gemini) to classify the triage answers.

    Returns a dict with keys: severity, summary, recommended_action.

    On ANY failure (API error, timeout, malformed response), falls back to
    MEDIUM severity + route to human. This is deliberate: in a child-safety
    system, silent failure into no-action is unacceptable.
    """
    # Build the user message from the 3 answers
    user_message = (
        f"Student's answers to triage questions:\n"
        f"Q1 (What happened): {answers[0] if len(answers) > 0 else '[no answer]'}\n"
        f"Q2 (How they feel): {answers[1] if len(answers) > 1 else '[no answer]'}\n"
        f"Q3 (Known person or stranger): {answers[2] if len(answers) > 2 else '[no answer]'}"
    )

    safe_fallback = {
        "severity": "MEDIUM",
        "summary": "LLM classification unavailable — routing to human counselor for safety.",
        "recommended_action": "immediate counselor review",
    }

    # --- Try Groq first ---
    if GROQ_AVAILABLE and GROQ_API_KEY:
        try:
            async with httpx.AsyncClient() as http_client:
                client = AsyncGroq(api_key=GROQ_API_KEY, http_client=http_client)
                chat_completion = await client.chat.completions.create(
                    model="openai/gpt-oss-20b",
                    messages=[
                        {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.1,  # Low temp for deterministic classification
                    max_tokens=300,
                    timeout=15.0,
                )
            raw = chat_completion.choices[0].message.content.strip()
            # Strip markdown fences if the LLM disobeys (common failure mode)
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                raw = raw.removesuffix("```")
                raw = raw.strip()
            result = json.loads(raw)
            # Validate required keys
            if result.get("severity") in ("LOW", "MEDIUM", "HIGH", "POCSO"):
                logger.info("LLM triage completed: severity=%s", result["severity"])
                return result
            else:
                logger.warning(
                    "LLM returned invalid severity: %s — using fallback",
                    result.get("severity"),
                )
                return safe_fallback
        except Exception as e:
            logger.error(
                "Groq LLM call failed: %s — using safe fallback", type(e).__name__
            )
            # Fall through to Gemini or fallback

    # --- Try Gemini ---
    if GEMINI_AVAILABLE and GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(
                "gemini-2.0-flash",
                system_instruction=TRIAGE_SYSTEM_PROMPT,
            )
            response = await model.generate_content_async(
                user_message,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=300,
                ),
            )
            raw = response.text.strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                raw = raw.removesuffix("```")
                raw = raw.strip()
            result = json.loads(raw)
            if result.get("severity") in ("LOW", "MEDIUM", "HIGH", "POCSO"):
                logger.info(
                    "LLM triage completed (Gemini): severity=%s", result["severity"]
                )
                return result
            else:
                logger.warning(
                    "Gemini returned invalid severity: %s — using fallback",
                    result.get("severity"),
                )
                return safe_fallback
        except Exception as e:
            logger.error(
                "Gemini LLM call failed: %s — using safe fallback", type(e).__name__
            )

    # --- No LLM available at all ---
    logger.warning(
        "No LLM API key configured or both calls failed — using safe fallback (MEDIUM)"
    )
    return safe_fallback


# ==============================================================================
# SECTION: LLM VALIDATION
# ==============================================================================

VALIDATION_SYSTEM_PROMPT = """You are a validation assistant for "Albert", a child-safety support bot.

Your job: determine if a user's message is an attempt to answer the question being asked, OR if they are completely ignoring it (e.g. asking trivia questions like 'what colour is the sky', asking for jokes, changing the subject, chatting randomly).

IMPORTANT GUIDELINES:
1. BE LENIENT WITH NERVOUS CHILDREN:
   - The users are children aged 10-16 who may be scared, nervous, or confused.
   - Short, vague, or purely emotional answers ARE VALID. Examples: "idk", "something bad", "im scared", "yes", "no", "a boy from school", "it was online", "someone hurt me" — all VALID.
   - Typos, slang, broken English are totally VALID.

2. REJECT OFF-TOPIC / DODGING MESSAGES:
   - If the user asks general knowledge questions or trivia: "what colour is the sky", "what is 2+2", "who is the president" → INVALID.
   - If the user asks for jokes, entertainment, or games: "tell me a joke", "do you play minecraft", "sing a song" → INVALID.
   - If the user is just saying hi or chatting with no context: "hi", "hello", "what is your name" → INVALID.
   - If the user is asking you to do their homework: "can you help me with math" → INVALID.

Return ONLY a JSON object with exactly these two keys:
{"is_valid": true, "reason": ""}
{"is_valid": false, "reason": "your gentle, warm 1-sentence re-prompt here"}

Examples:
- Question: "Can you briefly tell me what happened?", Answer: "someone was mean to me" → {"is_valid": true, "reason": ""}
- Question: "Can you briefly tell me what happened?", Answer: "i dont want to say exactly but it was online" → {"is_valid": true, "reason": ""}
- Question: "How is this making you feel right now?", Answer: "bad" → {"is_valid": true, "reason": ""}
- Question: "Can you briefly tell me what happened?", Answer: "What colour is the sky" → {"is_valid": false, "reason": "That's a fair question, but right now I really want to focus on keeping you safe and helping with what happened. 💙 Could you tell me a little bit about what's going on?"}
- Question: "Can you briefly tell me what happened?", Answer: "tell me a joke" → {"is_valid": false, "reason": "I'd love to share jokes another time! Right now, my main focus is being here for you and helping you feel safe. 💙 Could you tell me what happened?"}
- Question: "How is this making you feel right now?", Answer: "can you help me with homework?" → {"is_valid": false, "reason": "I wish I could help with homework, but right now I really care about how you're feeling. 💛 Even just one word like 'scared' or 'okay' helps me help you."}

NEVER return anything other than the JSON object. No markdown fences. No explanation."""


def _strip_llm_fences(raw: str) -> str:
    """Strip markdown code fences from LLM output (common disobedience)."""
    raw = raw.strip()
    if raw.startswith("```"):
        # Remove opening fence (with optional language tag like ```json)
        first_newline = raw.find("\n")
        if first_newline != -1:
            raw = raw[first_newline + 1:]
        else:
            raw = raw[3:]
        # Remove closing fence
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    return raw


async def validate_triage_answer(question: str, answer: str) -> dict:
    """
    Validate if the user's answer is responsive to the question.
    Lenient for scared kids, but strictly prevents dodging via trivia or unrelated questions.
    """
    answer_raw = answer.strip()
    answer_lower = answer_raw.lower()
    clean_words = set(re.findall(r"\b\w+\b", answer_lower))

    # Emotional and safety keywords that prove an attempt to answer
    safety_keywords = {
        "scared", "angry", "sad", "confused", "hurt", "afraid", "help",
        "bully", "bullied", "bullying", "mean", "threat", "threatened", "threatening",
        "hit", "hate", "cry", "crying", "pain", "sorry", "harass", "harassed",
        "please", "someone", "friend", "teacher", "parent", "school", "class", "classmate",
        "online", "message", "messages", "dm", "dms", "photo", "photos", "video", "videos",
        "money", "block", "blocked", "report", "reported", "account", "hacked", "stole", "stolen",
        "stranger", "know", "yes", "no", "idk", "bad", "okay", "ok",
        "fine", "terrible", "awful", "upset", "worried", "anxious",
        "depressed", "lonely", "embarrassed", "ashamed", "blackmail", "blackmailed", "leak", "leaked",
    }
    multi_word_safety = ("real life", "not okay", "not ok", "dont know", "don't know")

    has_safety_concept = (
        bool(clean_words & safety_keywords)
        or any(mws in answer_lower for mws in multi_word_safety)
    )

    # Fast-path 1: Pure greetings with no context
    greetings = {"hi", "hello", "hey", "hola", "yo", "sup", "good morning", "good evening", "good afternoon"}
    if answer_lower in greetings or (clean_words and clean_words.issubset(greetings)):
        logger.info("Fast-path validation: greeting detected: '%s'", answer_raw[:50])
        return {
            "is_valid": False,
            "reason": "Hey there! 💙 I'm right here with you. To help you out, could you tell me a little bit about what happened? Even just a few words is totally okay.",
        }

    # Fast-path 2: Trivia, games, entertainment, jokes
    trivia_words = {
        "sky", "weather", "joke", "riddle", "sing", "dance", "game",
        "minecraft", "roblox", "fortnite", "homework", "math", "pizza", "burger",
        "capital", "president", "prime minister", "song", "lyrics", "colour", "color",
    }
    has_trivia_word = bool(clean_words & trivia_words)

    # Fast-path 3: Questions or question starters
    question_starters = (
        "what", "why", "how", "who", "when", "where", "which",
        "can you", "could you", "tell me", "is the", "are you", "do you",
        "will you", "would you", "should i", "do i",
    )
    is_question = (
        answer_lower.endswith("?")
        or any(answer_lower.startswith(qs + " ") or answer_lower == qs for qs in question_starters)
    )

    # If it's a question or trivia and lacks emotional/safety grounding -> reject immediately
    if (is_question or has_trivia_word) and not has_safety_concept:
        logger.info("Fast-path validation: off-topic question/trivia detected: '%s'", answer_raw[:50])
        return {
            "is_valid": False,
            "reason": "That's a fair question, but right now I really want to focus on keeping you safe and helping with what happened. 💙 Could you try answering what I asked? Even just a few words is totally fine.",
        }

    # If answer is clearly responsive or emotional, we can pass it without burning an LLM call
    if has_safety_concept and not has_trivia_word and len(clean_words) >= 1:
        logger.info("Fast-path validation: safety/emotional response accepted: '%s'", answer_raw[:50])
        return {"is_valid": True, "reason": ""}

    user_message = f"Question: {question}\nUser's Answer: {answer_raw}"

    if GROQ_AVAILABLE and GROQ_API_KEY:
        try:
            async with httpx.AsyncClient() as http_client:
                client = AsyncGroq(api_key=GROQ_API_KEY, http_client=http_client)
                chat_completion = await client.chat.completions.create(
                    model="openai/gpt-oss-20b",
                    messages=[
                        {"role": "system", "content": VALIDATION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.1,
                    max_tokens=150,
                    timeout=8.0,
                )
            raw = chat_completion.choices[0].message.content.strip()
            logger.info("Groq validation raw response: %s", raw[:200])
            raw = _strip_llm_fences(raw)
            result = json.loads(raw)
            if "is_valid" in result:
                logger.info(
                    "Validation result: is_valid=%s for answer='%s'",
                    result["is_valid"],
                    answer_raw[:50],
                )
                return result
        except Exception as e:
            logger.error("Groq validation failed: %s — %s", type(e).__name__, str(e)[:100])

    if GEMINI_AVAILABLE and GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(
                "gemini-2.0-flash",
                system_instruction=VALIDATION_SYSTEM_PROMPT,
            )
            response = await model.generate_content_async(
                user_message,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=150,
                ),
            )
            raw = response.text.strip()
            logger.info("Gemini validation raw response: %s", raw[:200])
            raw = _strip_llm_fences(raw)
            result = json.loads(raw)
            if "is_valid" in result:
                logger.info(
                    "Validation result (Gemini): is_valid=%s for answer='%s'",
                    result["is_valid"],
                    answer_raw[:50],
                )
                return result
        except Exception as e:
            logger.error("Gemini validation failed: %s — %s", type(e).__name__, str(e)[:100])

    # Fallback if all LLMs are unreachable:
    # If the user asked a question or sent obvious trivia, do NOT fail open!
    if is_question or has_trivia_word:
        logger.warning("LLMs unreachable; heuristic rejected off-topic answer: '%s'", answer_raw[:50])
        return {
            "is_valid": False,
            "reason": "Could you try telling me a little bit about what happened? Even just a few words is totally okay 💙",
        }

    logger.warning("Validation LLM calls unavailable — accepting answer as fallback")
    return {"is_valid": True, "reason": ""}


# ==============================================================================
# SECTION: LLM PERSONALIZATION
# ==============================================================================

# System prompt for generating personalized, empathetic responses.
# This is SEPARATE from the triage classifier prompt.
PERSONALIZATION_SYSTEM_PROMPT = """You are "Albert", a warm, caring, and emotionally present support bot for Indian students aged 10–16 facing cyberbullying or online fraud.

You speak like a kind, protective older friend — NOT a helpdesk bot, NOT a formal counselor. You use contractions ("I'm", "you're", "that's"), casual but respectful language, and you genuinely FEEL for the child.

YOUR VOICE:
- Warm, gentle, and reassuring — like a trusted older sibling or favourite teacher.
- You mirror their emotions: if they're scared, acknowledge the fear. If they're angry, validate the anger.
- You use encouraging phrases: "I'm really proud of you for telling me", "That took a lot of courage", "You're not alone in this".
- You sprinkle in emojis naturally (💙, 🌟, 💛, 🤗, 🛡️) but don't overdo it.
- You NEVER sound clinical, robotic, or formulaic. Every message should feel personally written for THIS child.

CRITICAL SAFETY & BOUNDARY RULES:
- Under NO circumstances should you answer trivia, general knowledge, riddles, math problems, joke requests, or factual questions (such as 'what colour is the sky', 'tell me a joke', 'what is 2+2'). You are strictly a child safety and emotional support bot.
- If a message seems confusing or off-topic, NEVER answer the trivia — instead, remind the student that they are in a safe space and invite them to share how they are feeling.
- Keep responses under 200 words.
- Use simple English that a 10-year-old can understand.
- Be warm, validating, and reassuring. NEVER blame the child. NEVER say "just ignore it".
- ALWAYS include the Childline number: 1098 (free, 24/7, confidential).
- Use markdown formatting (*bold* for emphasis).
- NEVER ask the child for personal information (name, school, address, phone).
- NEVER attempt therapy or diagnosis — you are providing practical safety tips with a warm heart.
- Reference their SPECIFIC situation from the triage answers provided.
- Include 3-5 actionable, concrete tips relevant to their situation.
- End with something genuinely encouraging that makes them feel brave and not alone.

You will receive the student's triage answers and a response_type. Generate ONLY the message text (with markdown). No JSON, no metadata."""


async def generate_personalized_response(
    answers: list[str],
    response_type: str,
    severity: str = "MEDIUM",
    fallback: str = "",
) -> str:
    """
    Generate a personalized, empathetic message using the LLM.

    Args:
        answers: The student's 3 triage answers.
        response_type: One of 'self_help_low', 'self_help_medium', 'escalation_high',
                       'triage_ack_1', 'triage_ack_2', 'triage_ack_3'.
        severity: The triage severity level.
        fallback: Static fallback text to use if LLM fails.

    Returns:
        The personalized message string, or the fallback if LLM fails.
    """
    user_prompt = (
        f"Student's situation:\n"
        f"Q1 (What happened): {answers[0] if len(answers) > 0 else '[not yet answered]'}\n"
        f"Q2 (How they feel): {answers[1] if len(answers) > 1 else '[not yet answered]'}\n"
        f"Q3 (Known/stranger): {answers[2] if len(answers) > 2 else '[not yet answered]'}\n\n"
        f"Severity: {severity}\n"
        f"Response type: {response_type}\n\n"
    )

    if response_type.startswith("triage_ack"):
        q_num = response_type[-1]
        user_prompt += (
            f"Generate a brief (1-2 sentence) empathetic acknowledgement of what the student "
            f"just shared in answer {q_num}. Be warm and validating. Do NOT ask the next question — "
            f"just acknowledge. Start with an emoji."
        )
    elif response_type == "self_help_low":
        user_prompt += (
            "Generate personalized self-help advice for a LOW severity situation. "
            "Include 3-5 specific, actionable tips RELEVANT to what they described. "
            "Mention blocking, reporting on the platform, talking to a trusted adult, "
            "and saving screenshots — but tailor it to their specific situation. "
            "Include Childline 1098. End with encouragement."
        )
    elif response_type == "self_help_medium":
        user_prompt += (
            "Generate personalized self-help advice for a MEDIUM severity situation. "
            "This is more serious — emphasize telling a trusted adult NOW, saving evidence, "
            "blocking and reporting. If money/fraud is mentioned, include cybercrime.gov.in and 1930. "
            "Include Childline 1098. Mention they can talk to a human volunteer. End with encouragement."
        )
    elif response_type == "escalation_high":
        user_prompt += (
            "Generate a brief reassuring message telling the student this sounds serious "
            "and you're connecting them with a trained human volunteer RIGHT NOW. "
            "Emphasize they did the right thing by speaking up. Keep it short (3-4 sentences)."
        )

    # Try Groq first, then Gemini, then fallback
    if GROQ_AVAILABLE and GROQ_API_KEY:
        try:
            async with httpx.AsyncClient() as http_client:
                client = AsyncGroq(api_key=GROQ_API_KEY, http_client=http_client)
                chat_completion = await client.chat.completions.create(
                    model="openai/gpt-oss-20b",
                    messages=[
                        {"role": "system", "content": PERSONALIZATION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.7,
                    max_tokens=500,
                    timeout=10.0,
                )
            result = chat_completion.choices[0].message.content.strip()
            if result:
                logger.info(
                    "Personalized response generated via Groq for type=%s",
                    response_type,
                )
                return result
        except Exception as e:
            logger.error("Groq personalization failed: %s", type(e).__name__)

    if GEMINI_AVAILABLE and GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(
                "gemini-2.0-flash",
                system_instruction=PERSONALIZATION_SYSTEM_PROMPT,
            )
            response = await model.generate_content_async(
                user_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.7,
                    max_output_tokens=500,
                ),
            )
            result = response.text.strip()
            if result:
                logger.info(
                    "Personalized response generated via Gemini for type=%s",
                    response_type,
                )
                return result
        except Exception as e:
            logger.error("Gemini personalization failed: %s", type(e).__name__)

    logger.warning(
        "Personalization unavailable — using static fallback for type=%s", response_type
    )
    return fallback


# ==============================================================================
# SECTION: TEACHER / ADULT HELPER GUIDANCE
# ==============================================================================

TEACHER_SYSTEM_PROMPT = """You are "Albert", providing guidance to a TEACHER or ADULT who is trying to help a student (aged 10–16) facing cyberbullying or online fraud.

You are NOT talking to the child. You are talking to the adult who wants to support them.

YOUR VOICE:
- Professional but warm and encouraging. This adult cares enough to seek help — honour that.
- Practical and actionable. Give them specific things to DO and SAY.
- Gently educate them on what NOT to do (don't shout, don't dismiss, don't blame the child).

YOUR GUIDANCE SHOULD INCLUDE:
1. **How to approach the child**: Create a safe space. Use open-ended questions. Don't interrogate.
2. **What NOT to do**: Don't shout. Don't take away their phone as punishment. Don't say "just ignore it" or "why did you do that?" Don't blame them. Don't share their story with others without consent.
3. **What TO say**: "I believe you." "This is not your fault." "I'm here for you." "We'll figure this out together."
4. **Practical steps**: Help the child save evidence (screenshots), report on the platform, block the person. If it's serious, involve parents and school counselor.
5. **When to escalate**: If there's any hint of sexual content, threats, blackmail, or financial fraud — involve police/Childline 1098 immediately. Do NOT try to handle POCSO situations alone.
6. **Resources**: Childline 1098 (free, 24/7), cybercrime.gov.in, Cyber Crime Helpline 1930.

RULES:
- Keep it under 350 words.
- Use markdown formatting (*bold* for key phrases).
- Use numbered lists for actionable steps.
- End with encouragement for the teacher — they're making a real difference.
- Use emojis sparingly (💙, 🛡️, 🌟).

Generate ONLY the guidance message. No JSON, no metadata."""


async def generate_teacher_guidance(situation: str) -> str:
    """
    Generate actionable guidance for a teacher/adult helping a student.
    """
    user_prompt = (
        f"A teacher/adult is trying to help a student who is experiencing the following:\n\n"
        f"\"{situation}\"\n\n"
        f"Generate practical, empathetic guidance for this teacher on how to support the child."
    )

    fallback = (
        "👩‍🏫 *Here's how you can best support your student:* 💙\n\n"
        "*1. Create a safe space* — Find a quiet moment to talk. Let them know they're not "
        "in trouble and you believe them.\n\n"
        "*2. Listen without judging* — Use open questions like \"Can you tell me what happened?\" "
        "Don't interrogate or react with anger.\n\n"
        "*3. Don't blame them* — Avoid saying \"Why did you do that?\" or \"Just ignore it.\" "
        "These responses shut children down.\n\n"
        "*4. Help them take action* — Help the child screenshot the evidence, block the person, "
        "and report on the platform.\n\n"
        "*5. Involve the right people* — Talk to the child's parents and your school counselor. "
        "If the situation involves threats, blackmail, or anything sexual, contact *Childline 1098* immediately.\n\n"
        "📞 *Key resources:*\n"
        "• *Childline 1098* — Free, 24/7, confidential\n"
        "• *cybercrime.gov.in* — For reporting online fraud\n"
        "• *1930* — Cyber Crime Helpline\n\n"
        "*Thank you for caring. Teachers like you change lives. 🌟*"
    )

    if GROQ_AVAILABLE and GROQ_API_KEY:
        try:
            async with httpx.AsyncClient() as http_client:
                client = AsyncGroq(api_key=GROQ_API_KEY, http_client=http_client)
                chat_completion = await client.chat.completions.create(
                    model="openai/gpt-oss-20b",
                    messages=[
                        {"role": "system", "content": TEACHER_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.7,
                    max_tokens=600,
                    timeout=15.0,
                )
            result = chat_completion.choices[0].message.content.strip()
            if result:
                logger.info("Teacher guidance generated via Groq")
                return result
        except Exception as e:
            logger.error("Groq teacher guidance failed: %s", type(e).__name__)

    if GEMINI_AVAILABLE and GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(
                "gemini-2.0-flash",
                system_instruction=TEACHER_SYSTEM_PROMPT,
            )
            response = await model.generate_content_async(
                user_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.7,
                    max_output_tokens=600,
                ),
            )
            result = response.text.strip()
            if result:
                logger.info("Teacher guidance generated via Gemini")
                return result
        except Exception as e:
            logger.error("Gemini teacher guidance failed: %s", type(e).__name__)

    logger.warning("Teacher guidance LLM unavailable — using static fallback")
    return fallback


async def _handle_teacher_guidance(
    chat_id: int, text: str, bot: Bot, session: dict
) -> None:
    """
    Process the teacher's description of the student's situation and generate guidance.
    """
    await bot.send_message(
        chat_id=chat_id,
        text="🔄 *Let me put together some guidance for you…* Please wait a moment.",
        parse_mode=ParseMode.MARKDOWN,
    )

    guidance = await generate_teacher_guidance(text)

    msg = await bot.send_message(
        chat_id=chat_id,
        text=guidance,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_teacher_keyboard(),
    )
    _track_msg(chat_id, msg.message_id)

    # Move to self-help state (teacher can still talk to a counselor from here)
    _set_state(chat_id, State.STATE_SELF_HELP)


# ==============================================================================
# SECTION: KEYBOARD BUILDERS
# ==============================================================================
# All keyboards are InlineKeyboardMarkup. We NEVER use ReplyKeyboardMarkup.


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Main menu after /start."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🆘 I Need Help", callback_data="report_issue")],
            [InlineKeyboardButton("👩‍🏫 I'm a Teacher / Adult Helping a Student", callback_data="teacher_help")],
            [InlineKeyboardButton("ℹ️ What is Albert?", callback_data="about")],
            [InlineKeyboardButton("🚨 Panic Wipe", callback_data="panic")],
        ]
    )


def build_panic_keyboard() -> InlineKeyboardMarkup:
    """The panic button, available to append to most messages."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚨 Panic Wipe", callback_data="panic")],
        ]
    )


def build_triage_panic_keyboard() -> InlineKeyboardMarkup:
    """During triage: just the panic button (answers are free-text)."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚨 Panic Wipe", callback_data="panic")],
        ]
    )


def build_self_help_keyboard(severity: str) -> InlineKeyboardMarkup:
    """After LOW/MEDIUM severity — self-help resources + option to talk to human."""
    buttons = [
        [InlineKeyboardButton("💬 Talk to a Human", callback_data="talk_human")],
        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="main_menu")],
        [InlineKeyboardButton("🚨 Panic Wipe", callback_data="panic")],
    ]
    return InlineKeyboardMarkup(buttons)


def build_queued_keyboard() -> InlineKeyboardMarkup:
    """Shown to student while waiting for a counselor to claim."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚨 Panic Wipe", callback_data="panic")],
        ]
    )


def build_counselor_claim_keyboard(case_id: str) -> InlineKeyboardMarkup:
    """Posted in the Counselor Group — the claim button."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"✋ Claim {case_id}", callback_data=f"claim:{case_id}"
                )
            ],
        ]
    )


def build_counselor_active_keyboard(case_id: str) -> InlineKeyboardMarkup:
    """Replaces the claim button after a counselor claims."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"✅ Close {case_id}", callback_data=f"close:{case_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🚨 Panic Wipe {case_id}", callback_data=f"cpanic:{case_id}"
                )
            ],
        ]
    )


def build_student_relay_keyboard() -> InlineKeyboardMarkup:
    """Shown to the student during active relay."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔚 End Conversation", callback_data="end_relay")],
            [InlineKeyboardButton("🚨 Panic Wipe", callback_data="panic")],
        ]
    )


def build_pocso_keyboard() -> InlineKeyboardMarkup:
    """Terminal POCSO redirect — only option is to start over."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏠 Start Over", callback_data="main_menu")],
        ]
    )


def build_teacher_keyboard() -> InlineKeyboardMarkup:
    """After teacher guidance — option to talk to counselor or return to menu."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💬 Talk to a Counselor Yourself", callback_data="talk_human")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="main_menu")],
        ]
    )


# ==============================================================================
# SECTION: DECOY / PANIC WIPE TEXT
# ==============================================================================

DECOY_TEXT = (
    "📘 *NCERT Class 9 Mathematics — Chapter 3*\n\n"
    "*Coordinate Geometry*\n\n"
    "In earlier classes, you have studied number lines. On a number line, "
    "distances from a fixed point are marked in equal units positively in "
    "one direction and negatively in the other.\n\n"
    "The concepts of coordinate geometry were first introduced by the French "
    "mathematician René Descartes (1596–1650). He proposed that two perpendicular "
    "lines could be used to describe the position of a point in a plane.\n\n"
    "A coordinate plane consists of two number lines perpendicular to each other. "
    "The horizontal number line is called the *x-axis* and the vertical number "
    "line is called the *y-axis*. The point of intersection is called the *origin*, "
    "denoted by O.\n\n"
    "*Exercise 3.1*\n"
    "1. How will you describe the position of a table lamp on your study table "
    "to another person?\n"
    "2. Street Plan: A city has two main roads that cross each other at the "
    "centre of the city…\n\n"
    "_Source: NCERT Textbook for Class IX_"
)


# ==============================================================================
# SECTION: SELF-HELP RESOURCES TEXT
# ==============================================================================

SELF_HELP_LOW = (
    "💙 *Hey, I hear you. What happened isn't okay, and I'm glad you told me.*\n\n"
    "Here are some things that might help right now:\n\n"
    "• *Block them* — On most apps, you can block or mute someone who's "
    "bothering you. Once you do, they can't reach you anymore. You deserve that peace. 🛡️\n\n"
    "• *Don't reply to them* — I know it's tempting, but bullies usually want a reaction. "
    "Not giving them one takes away their power.\n\n"
    "• *Take screenshots* — Before you block them, save evidence of what happened. "
    "It could be really helpful later if you decide to report it.\n\n"
    "• *Tell someone you trust* — A parent, teacher, older sibling, or even a friend. "
    "You don't have to carry this alone, okay? 🤗\n\n"
    "• *Report it on the app* — Instagram, WhatsApp, Snapchat — they all have a "
    "'Report' button. Use it. That's what it's there for.\n\n"
    "📞 *If you ever feel unsafe:* Call *Childline 1098* — it's completely free, "
    "available 24/7, and everything you say stays confidential.\n\n"
    "Remember: *This is NOT your fault.* You're braver than you think, "
    "and you deserve to feel safe. I'm really proud of you for speaking up. 🌟"
)

SELF_HELP_MEDIUM = (
    "💛 *I can tell this has been really hard on you, and I want you to know — you don't deserve any of this.*\n\n"
    "Here are some really important things you can do right now:\n\n"
    "• *Save everything* — Take screenshots of messages, profiles, and anything "
    "hurtful or threatening. Do this before blocking, in case you need it later. 📱\n\n"
    "• *Block and report* — On whatever app this is happening, block them AND "
    "use the Report button. You shouldn't have to see any more of this.\n\n"
    "• *Tell a trusted adult NOW* — A parent, your favourite teacher, a school counselor — "
    "someone who cares about you needs to know. You shouldn't handle this alone. 🤗\n\n"
    "• *If money is involved* — Tell your parents right away. For online fraud, "
    "you can also report at *cybercrime.gov.in* or call *1930* (Cyber Crime Helpline).\n\n"
    "• *Your school can help* — Schools are required to take bullying seriously. "
    "A teacher you trust can step in and make things better.\n\n"
    "📞 *Childline 1098* — Free, 24/7, completely confidential. They've helped lakhs of kids just like you.\n\n"
    "💬 If you'd like to talk to one of our trained volunteers (a real person who will listen), "
    "tap the button below.\n\n"
    "*You're so brave for speaking up. That takes real strength. 🌟*"
)


# ==============================================================================
# SECTION: POCSO REDIRECT TEXT
# ==============================================================================

POCSO_REDIRECT_TEXT = (
    "🛑 *Hey… I need you to listen to me for a moment.*\n\n"
    "What you've told me sounds really, really serious. And I want you to know — "
    "*none of this is your fault*. Not even a little bit.\n\n"
    "You need to talk to someone who is specially trained to help with exactly "
    "this kind of situation — someone much better than an app like me.\n\n"
    "📞 *Please call Childline right now:*\n\n"
    "☎️ *1098*\n\n"
    "• It's *completely FREE* — works from any phone\n"
    "• Available *24 hours a day, 7 days a week* — even right now\n"
    "• Everything you say is *100% confidential* — nobody else will know\n"
    "• The people there are kind, patient, and trained to help\n"
    "• Even if you're not sure, just call — they will listen without judging\n\n"
    "You can also reach them at: *www.childlineindia.org*\n\n"
    "*You are so brave. You deserve to be safe, happy, and protected. 💙 "
    "Please make that call — they're waiting to help you.*"
)


# ==============================================================================
# SECTION: COMMAND HANDLERS
# ==============================================================================


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start — Entry point. Shows the main menu. Resets session state.
    This is the only way back from WIPED or POCSO_REDIRECT states.
    """
    chat = update.effective_chat
    chat_id = chat.id

    # If /start is typed in the Counselor Group or any group, guide user to private chat
    if chat.type != "private":
        bot_info = await context.bot.get_me()
        await update.message.reply_text(
            "👋 *Albert Counselor Hub*\n\n"
            "This group is for counselors to receive and claim alerts.\n\n"
            "🔒 *Need help or want to report an issue?*\n"
            "To keep your conversation completely private, anonymous, and safe, "
            "please chat with Albert in a direct message:\n\n"
            f"👉 [Tap here to chat with @{bot_info.username}](https://t.me/{bot_info.username})",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "💬 Open Private Chat with Albert",
                            url=f"https://t.me/{bot_info.username}",
                        )
                    ]
                ]
            ),
        )
        return

    session = _get_session(chat_id)
    _track_msg(chat_id, update.message.message_id)

    # Reset session to clean state
    session["state"] = State.STATE_START
    session["triage_question_index"] = 0
    session["triage_answers"] = []
    # Don't clear case_id here — if they have an active case, closing /start
    # shouldn't silently orphan it. The case stays in the registry.

    _set_state(chat_id, State.STATE_START)

    welcome_text = (
        "👋 *Hey there! I'm Albert.* 💙\n\n"
        "I'm a safe space for you. If someone is being mean to you online, "
        "or if something happened on the internet that made you feel "
        "scared, confused, or uncomfortable — I'm here to listen.\n\n"
        "Everything you share with me stays *completely private* — "
        "nobody will ever see your name or know who you are. 🔒\n\n"
        "You're not in trouble, and you're not alone. 🤗\n\n"
        "What would you like to do?"
    )

    msg = await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_main_menu_keyboard(),
    )
    _track_msg(chat_id, msg.message_id)
    session["last_menu_message_id"] = msg.message_id


async def cmd_wipe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /wipe — Panic wipe via command. Identical to the [Panic Wipe] inline button.
    This must be instant and must NOT depend on any external API call.
    """
    chat_id = update.effective_chat.id
    _track_msg(chat_id, update.message.message_id)

    await _execute_panic_wipe(chat_id, context.bot, message_id=None)

    # Note: _execute_panic_wipe already sends the decoy message as the final step.
    # No need to send another one here.


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — Show help text."""
    help_text = (
        "🤖 *Albert — Help*\n\n"
        "• /start — Begin or return to the main menu\n"
        "• /wipe — Emergency panic wipe (deletes all your data instantly)\n"
        "• /help — Show this help message\n\n"
        "If you're in a conversation with a counselor, just type your message "
        "and it will be relayed anonymously.\n\n"
        "📞 In an emergency, call *Childline 1098* (free, 24/7, confidential)."
    )
    chat_id = update.effective_chat.id
    _track_msg(chat_id, update.message.message_id)
    msg = await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    _track_msg(chat_id, msg.message_id)


# ==============================================================================
# SECTION: PANIC WIPE LOGIC
# ==============================================================================


async def _execute_panic_wipe(
    chat_id: int, bot: Bot, message_id: int | None = None
) -> None:
    """
    Core panic wipe logic. Called by both /wipe command and [Panic Wipe] button.

    This function:
    1. Deletes ALL tracked messages (bot-sent AND user-sent) from the chat.
    2. Cleans up both sides of any active relay.
    3. Sends a single NCERT decoy message so the chat looks like homework.

    This must NEVER call an external API (no LLM) — it must be instant.
    Uses only Telegram's deleteMessage API which is fast and local.
    """
    hashed = _hash_chat_id(chat_id)
    logger.info("PANIC WIPE initiated for user_hash=%s", hashed)

    # Grab the session BEFORE we start cleaning up
    session = _get_session(chat_id)
    tracked_messages = list(session.get("message_ids", []))

    # --- DELETE ALL TRACKED MESSAGES from this chat ---
    # This removes every bot message AND every user message we recorded.
    # In private chats, bots can delete any message sent within 48 hours.
    # We do this FIRST so the chat is cleared before any notifications.
    deleted_count = 0
    for msg_id in tracked_messages:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted_count += 1
        except Exception:
            pass  # Message may already be deleted, too old, or in a different chat

    # Also try to delete the triggering message if not already tracked
    if message_id and message_id not in tracked_messages:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            deleted_count += 1
        except Exception:
            pass

    logger.info(
        "PANIC WIPE: deleted %d messages for user_hash=%s", deleted_count, hashed
    )

    # --- Clean up any case associated with this chat_id ---
    # Check as student
    case_id = student_to_case.pop(chat_id, None)
    if case_id and case_id in cases:
        case = cases[case_id]
        c_id = case.get("counselor_chat_id")
        if c_id:
            counselor_to_case.pop(c_id, None)
            try:
                await bot.send_message(
                    chat_id=c_id,
                    text=f"⚠️ *{case_id}* — Case has been wiped. All data deleted.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
        del cases[case_id]

    # Check as counselor
    case_id_c = counselor_to_case.pop(chat_id, None)
    if case_id_c and case_id_c in cases:
        case = cases[case_id_c]
        s_id = case.get("student_chat_id")
        if s_id:
            student_to_case.pop(s_id, None)
            # Wipe student's chat too (recursive)
            await _execute_panic_wipe(s_id, bot)
        del cases[case_id_c]

    # --- Reset session state ---
    session["state"] = State.STATE_WIPED
    session["triage_answers"] = []
    session["triage_question_index"] = 0
    session["case_id"] = None
    session["message_ids"] = []  # Clear the tracking list

    # --- Send the decoy as the ONLY remaining message ---
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=DECOY_TEXT,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass  # If we can't even send the decoy, at least the data is wiped

    logger.info("PANIC WIPE completed for user_hash=%s — chat cleared", hashed)


# ==============================================================================
# SECTION: CASE CREATION & COUNSELOR ALERT
# ==============================================================================


async def _create_case_and_alert(
    chat_id: int,
    bot: Bot,
    severity: str,
    summary: str,
    message_id: int | None = None,
) -> str:
    """
    Create a new case and post the alert to the Counselor Group.

    Returns the case_id.

    The alert message NEVER contains the student's chat_id, username, or any PII.
    Only the case_id, severity, and a short summary are shown.
    """
    case_id = _next_case_id()
    now = datetime.now(timezone.utc).isoformat()

    cases[case_id] = {
        "student_chat_id": chat_id,
        "counselor_chat_id": None,
        "status": "pending",
        "severity": severity,
        "summary": summary,
        "created_at": now,
        "group_message_id": None,
    }
    student_to_case[chat_id] = case_id

    session = _get_session(chat_id)
    session["case_id"] = case_id
    _set_state(chat_id, State.STATE_QUEUED_FOR_COUNSELOR)

    logger.info("Case created: case_id=%s severity=%s", case_id, severity)

    # Post alert to Counselor Group
    # DPDPA: No PII, no chat_id, no username — only case_id, severity, summary.
    alert_text = (
        f"🚨 *New Case Alert*\n\n"
        f"*Case ID:* `{case_id}`\n"
        f"*Severity:* {_severity_emoji(severity)} *{severity}*\n"
        f"*Summary:* {summary}\n"
        f"*Time:* {now[:19]} UTC\n\n"
        f"Tap below to claim this case. You must have started a private chat "
        f"with this bot first (send /start to @{(await bot.get_me()).username} in DM)."
    )

    try:
        group_msg = await bot.send_message(
            chat_id=COUNSELOR_GROUP_ID,
            text=alert_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_counselor_claim_keyboard(case_id),
        )
        cases[case_id]["group_message_id"] = group_msg.message_id
    except Exception as e:
        logger.error("Failed to send alert to Counselor Group: %s", type(e).__name__)

    # Notify student
    student_text = (
        "✅ *Your case has been created.*\n\n"
        "A trained volunteer will be with you shortly. "
        "You don't need to do anything right now — just wait here.\n\n"
        "When they connect, you can type messages here and they'll "
        "be relayed anonymously. The volunteer will *never* see your "
        "name, username, or profile.\n\n"
        "📞 If you need help right now: *Childline 1098* (free, 24/7)"
    )

    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=student_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=build_queued_keyboard(),
            )
            # Editing an existing tracked message, so no new tracking needed
        except Exception:
            msg = await bot.send_message(
                chat_id=chat_id,
                text=student_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=build_queued_keyboard(),
            )
            _track_msg(chat_id, msg.message_id)
    else:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=student_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_queued_keyboard(),
        )
        _track_msg(chat_id, msg.message_id)

    return case_id


def _severity_emoji(severity: str) -> str:
    """Return an emoji for the severity level."""
    return {
        "LOW": "🟢",
        "MEDIUM": "🟡",
        "HIGH": "🔴",
        "POCSO": "⛔",
    }.get(severity, "❓")


# ==============================================================================
# SECTION: CALLBACK QUERY HANDLER (with sub-routing)
# ==============================================================================


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Master callback handler for all InlineKeyboardButton presses.
    Routes by callback_data prefix.
    """
    query = update.callback_query
    # ALWAYS answer the callback query immediately to dismiss the loading spinner.
    await query.answer()

    data = query.data
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    # The user who pressed the button (may differ from chat_id in groups)
    user_id = query.from_user.id

    # Prevent student actions from running in public counselor groups
    if data in ["report_issue", "about", "main_menu", "talk_human", "teacher_help"]:
        if query.message.chat.type != "private":
            bot_info = await context.bot.get_me()
            await query.answer(
                f"⚠️ Please open a private DM with @{bot_info.username} to report an issue confidentially.",
                show_alert=True,
            )
            return

    # --- PANIC WIPE (available in ALL states) ---
    if data == "panic":
        await _execute_panic_wipe(chat_id, context.bot, message_id=message_id)
        return

    # --- MAIN MENU ---
    if data == "main_menu":
        session = _get_session(chat_id)
        session["state"] = State.STATE_START
        session["triage_question_index"] = 0
        session["triage_answers"] = []
        _set_state(chat_id, State.STATE_START)
        welcome_text = (
            "👋 *Hey there! I'm Albert.* 💙\n\n"
            "I'm a safe space for you. If someone is being mean to you online, "
            "or if something happened that made you feel "
            "scared or uncomfortable — I'm here to listen.\n\n"
            "Everything you share with me stays *completely private*. 🔒\n\n"
            "You're not in trouble, and you're not alone. 🤗\n\n"
            "What would you like to do?"
        )
        await query.edit_message_text(
            text=welcome_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_main_menu_keyboard(),
        )
        session["last_menu_message_id"] = message_id
        return

    # --- ABOUT ---
    if data == "about":
        about_text = (
            "ℹ️ *About Albert*\n\n"
            "Albert is a safe, anonymous support system for Indian students "
            "(ages 10–16) who face cyberbullying or online fraud.\n\n"
            "• 🔒 *Private* — Your name and identity are never shared\n"
            "• 🤖 *AI Triage* — Quickly assesses your situation\n"
            "• 👩‍⚕️ *Human Support* — Connects you to a trained volunteer\n"
            "• 🚨 *Panic Wipe* — Instantly deletes all your data\n\n"
            "Albert was built for the safety of children. "
            "All data is temporary and never saved to disk.\n\n"
            "📞 Emergency: *Childline 1098* (free, 24/7, confidential)"
        )
        await query.edit_message_text(
            text=about_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🏠 Back to Main Menu", callback_data="main_menu"
                        )
                    ],
                ]
            ),
        )
        return

    # --- REPORT ISSUE (start triage) ---
    if data == "report_issue":
        session = _get_session(chat_id)
        session["triage_question_index"] = 0
        session["triage_answers"] = []
        _set_state(chat_id, State.STATE_TRIAGE)

        triage_intro = (
            "💛 *Thank you for trusting me. That takes real courage.*\n\n"
            "I'm going to ask you just 3 simple questions so I can "
            "understand what's going on and figure out the best way to help you.\n\n"
            "There are *no wrong answers* — just say whatever feels right. "
            "Take your time, I'm not going anywhere. 🤗\n\n"
            "If at any point you feel unsafe, you can tap 🚨 *Panic Wipe* "
            "to instantly clear everything.\n\n"
            f"*Question 1 of 3:*\n{TRIAGE_QUESTIONS[0]}"
        )
        await query.edit_message_text(
            text=triage_intro,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_triage_panic_keyboard(),
        )
        session["last_menu_message_id"] = message_id
        return

    # --- TEACHER / ADULT HELPER ---
    if data == "teacher_help":
        session = _get_session(chat_id)
        session["triage_question_index"] = 0
        session["triage_answers"] = []
        _set_state(chat_id, State.STATE_TEACHER_GUIDANCE)

        teacher_intro = (
            "👩‍🏫 *Thank you for being here for your student.* 💙\n\n"
            "The fact that you're looking for help shows how much you care, "
            "and that makes a real difference in a child's life.\n\n"
            "I'll do my best to give you practical, actionable guidance "
            "on how to support them through what they're going through.\n\n"
            "*Can you tell me briefly what the student is experiencing?*\n\n"
            "For example: Are they being bullied online? Did someone send them "
            "something inappropriate? Are they being threatened or blackmailed? "
            "Any details you can share will help me give better advice. 📝"
        )
        await query.edit_message_text(
            text=teacher_intro,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_panic_keyboard(),
        )
        session["last_menu_message_id"] = message_id
        return

    # --- TALK TO HUMAN (from self-help screen) ---
    if data == "talk_human":
        session = _get_session(chat_id)
        severity = "MEDIUM"  # Default; the actual severity was already assessed
        summary = "Student requested to talk to a human counselor."

        # Check if we have a previous triage result in the session
        # If so, use that severity; otherwise use MEDIUM
        await _create_case_and_alert(
            chat_id, context.bot, severity, summary, message_id=message_id
        )
        return

    # --- END RELAY (student ends conversation) ---
    if data == "end_relay":
        session = _get_session(chat_id)
        case_id = session.get("case_id")
        if case_id and case_id in cases:
            case = cases[case_id]
            c_id = case.get("counselor_chat_id")
            if c_id:
                counselor_to_case.pop(c_id, None)
                try:
                    await context.bot.send_message(
                        chat_id=c_id,
                        text=f"ℹ️ *{case_id}* — The student has ended the conversation.",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass
            case["status"] = "closed"
            student_to_case.pop(chat_id, None)
            session["case_id"] = None
            logger.info("Case closed by student: case_id=%s", case_id)

        _set_state(chat_id, State.STATE_START)
        await query.edit_message_text(
            text=(
                "✅ *Conversation ended.*\n\n"
                "Thank you for reaching out. Remember, you can always "
                "come back by sending /start.\n\n"
                "📞 *Childline 1098* is always available (free, 24/7)."
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
                ]
            ),
        )
        return

    # --- COUNSELOR CLAIM (from Counselor Group) ---
    if data.startswith("claim:"):
        case_id = data.split(":", 1)[1]
        # Validate case exists and is still pending
        if case_id not in cases:
            await query.edit_message_text(
                text=f"❌ *{case_id}* — Case not found or has been wiped.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        case = cases[case_id]
        if case["status"] != "pending":
            await query.edit_message_text(
                text=f"ℹ️ *{case_id}* — Case has already been claimed.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # The counselor's private chat_id is their user_id (they must have
        # started a DM with the bot). We store user_id as counselor_chat_id.
        counselor_private_chat_id = user_id
        case["counselor_chat_id"] = counselor_private_chat_id
        case["status"] = "active"
        counselor_to_case[counselor_private_chat_id] = case_id

        logger.info(
            "Case claimed: case_id=%s counselor_hash=%s",
            case_id,
            _hash_chat_id(counselor_private_chat_id),
        )

        # Update the group message to show "Claimed"
        await query.edit_message_text(
            text=(
                f"✅ *{case_id}* — Claimed by a counselor\n"
                f"Status: *ACTIVE*\n\n"
                f"Counselor: please open your *private DM* with this bot to "
                f"relay messages. All messages you send in the DM will be "
                f"forwarded to the student anonymously."
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_counselor_active_keyboard(case_id),
        )

        # Transition student to COUNSELOR_ACTIVE
        student_chat_id = case["student_chat_id"]
        _set_state(student_chat_id, State.STATE_COUNSELOR_ACTIVE)

        # Notify student
        msg = await context.bot.send_message(
            chat_id=student_chat_id,
            text=(
                "🟢 *A counselor has connected!*\n\n"
                "You can now type your messages here. They will be sent "
                "anonymously to the counselor — they can't see your name "
                "or profile.\n\n"
                "The counselor is a trained volunteer who wants to help."
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_student_relay_keyboard(),
        )
        _track_msg(student_chat_id, msg.message_id)

        # Notify counselor in their private DM
        try:
            await context.bot.send_message(
                chat_id=counselor_private_chat_id,
                text=(
                    f"🟢 *You've claimed {case_id}.*\n\n"
                    f"*Severity:* {_severity_emoji(case['severity'])} {case['severity']}\n"
                    f"*Summary:* {case['summary']}\n\n"
                    f"Any message you type here will be relayed to the student. "
                    f"The student cannot see your name or profile.\n\n"
                    f"To close the case, use the button in the counselor group."
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error(
                "Could not DM counselor for case %s — they may not have "
                "started a private chat with the bot. Error: %s",
                case_id,
                type(e).__name__,
            )
            # Try to inform via the group
            await context.bot.send_message(
                chat_id=COUNSELOR_GROUP_ID,
                text=(
                    f"⚠️ *{case_id}* — Could not send DM to the claiming counselor. "
                    f"Please make sure you've started a private chat with this bot "
                    f"by sending /start to the bot in a direct message."
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # --- COUNSELOR CLOSE CASE ---
    if data.startswith("close:"):
        case_id = data.split(":", 1)[1]
        if case_id not in cases:
            return
        case = cases[case_id]

        # Validate the counselor owns this case
        if case.get("counselor_chat_id") != user_id:
            return

        # Close the case
        case["status"] = "closed"
        student_chat_id = case["student_chat_id"]
        counselor_chat_id = case["counselor_chat_id"]

        # Clean up mappings
        student_to_case.pop(student_chat_id, None)
        counselor_to_case.pop(counselor_chat_id, None)

        # Reset student session
        student_session = _get_session(student_chat_id)
        student_session["case_id"] = None
        _set_state(student_chat_id, State.STATE_START)

        logger.info("Case closed by counselor: case_id=%s", case_id)

        # Update group message
        await query.edit_message_text(
            text=f"📁 *{case_id}* — Case closed by counselor.",
            parse_mode=ParseMode.MARKDOWN,
        )

        # Notify student
        try:
            msg = await context.bot.send_message(
                chat_id=student_chat_id,
                text=(
                    "📁 *The counselor has ended the conversation.*\n\n"
                    "If you need to talk again, just send /start.\n\n"
                    "📞 *Childline 1098* is always available (free, 24/7)."
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🏠 Main Menu", callback_data="main_menu"
                            )
                        ],
                    ]
                ),
            )
            _track_msg(student_chat_id, msg.message_id)
        except Exception:
            pass

        # Notify counselor
        try:
            await context.bot.send_message(
                chat_id=counselor_chat_id,
                text=f"📁 *{case_id}* — Case closed. Thank you for volunteering. 💙",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        return

    # --- COUNSELOR PANIC WIPE (from group) ---
    if data.startswith("cpanic:"):
        case_id = data.split(":", 1)[1]
        if case_id not in cases:
            return
        case = cases[case_id]

        # Wipe both sides
        student_chat_id = case["student_chat_id"]
        counselor_chat_id = case.get("counselor_chat_id")

        # Wipe student
        await _execute_panic_wipe(student_chat_id, context.bot, message_id=None)
        # Send decoy to student
        try:
            await context.bot.send_message(
                chat_id=student_chat_id,
                text=DECOY_TEXT,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

        # Clean up counselor
        if counselor_chat_id:
            counselor_to_case.pop(counselor_chat_id, None)

        # Clean up case
        student_to_case.pop(student_chat_id, None)
        cases.pop(case_id, None)

        # Update group message
        await query.edit_message_text(
            text=f"🚨 *{case_id}* — PANIC WIPE executed. All data deleted.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # If we get here, the callback_data was unrecognized
    logger.warning("Unrecognized callback_data: %s", data)


# ==============================================================================
# SECTION: MESSAGE HANDLER (with sub-routing by state)
# ==============================================================================


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles all plain-text messages (non-command).
    Routes based on the sender's current state.
    """
    # Ignore messages from groups/channels that aren't part of the relay
    if update.effective_chat.type != "private":
        return

    chat_id = update.effective_chat.id
    text = update.message.text
    if not text:
        return

    # Track user's message for panic wipe
    _track_msg(chat_id, update.message.message_id)

    session = _get_session(chat_id)
    state = session["state"]
    logger.info(
        "Received message from user_hash=%s in state=%s",
        _hash_chat_id(chat_id),
        state.value,
    )

    # --- STATE_TRIAGE: Collecting triage answers ---
    if state == State.STATE_TRIAGE:
        await _handle_triage_answer(chat_id, text, context.bot, session)
        return

    # --- STATE_TEACHER_GUIDANCE: Teacher describing student's situation ---
    if state == State.STATE_TEACHER_GUIDANCE:
        await _handle_teacher_guidance(chat_id, text, context.bot, session)
        return

    # --- STATE_COUNSELOR_ACTIVE: Relay message (student -> counselor) ---
    if state == State.STATE_COUNSELOR_ACTIVE:
        case_id = session.get("case_id") or student_to_case.get(chat_id)
        if case_id and case_id in cases:
            case = cases[case_id]
            counselor_chat_id = case.get("counselor_chat_id")
            if counselor_chat_id:
                # Relay as a bot-authored message — NEVER use Telegram's
                # native forward (which leaks username/profile metadata).
                try:
                    await context.bot.send_message(
                        chat_id=counselor_chat_id,
                        text=f"💬 *[{case_id}] Student:*\n{text}",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception as e:
                    logger.error(
                        "Relay student->counselor failed for %s: %s",
                        case_id,
                        type(e).__name__,
                    )
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="⚠️ Your message couldn't be delivered. The counselor may have disconnected. Please try again or send /start.",
                    )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⏳ No counselor has claimed your case yet. Please wait...",
                    reply_markup=build_queued_keyboard(),
                )
        return

    # --- Check if this chat_id is a counselor with an active relay ---
    case_id_c = counselor_to_case.get(chat_id)
    if case_id_c and case_id_c in cases:
        case = cases[case_id_c]
        # Validate this counselor owns the case
        if case.get("counselor_chat_id") == chat_id and case["status"] == "active":
            student_chat_id = case["student_chat_id"]
            # Relay to student — again, as a bot-authored message, no forwarding.
            try:
                msg = await context.bot.send_message(
                    chat_id=student_chat_id,
                    text=f"👩‍⚕️ *Counselor:*\n{text}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=build_student_relay_keyboard(),
                )
                _track_msg(student_chat_id, msg.message_id)
            except Exception as e:
                logger.error(
                    "Relay counselor->student failed for %s: %s",
                    case_id_c,
                    type(e).__name__,
                )
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Could not deliver message to student for *{case_id_c}*. "
                    f"The student may have wiped their session.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            return

    # --- STATE_WIPED / STATE_POCSO_REDIRECT / STATE_START / other ---
    # In these states, free-text is not expected. Gently redirect.
    if state == State.STATE_WIPED:
        # Don't respond at all in wiped state to maintain the illusion
        return

    if state == State.STATE_POCSO_REDIRECT:
        await context.bot.send_message(
            chat_id=chat_id,
            text="📞 Please call *Childline 1098* for help. Send /start to return to the main menu.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Default: prompt to use the menu
    await context.bot.send_message(
        chat_id=chat_id,
        text="Please use the menu buttons to navigate. Send /start if you don't see a menu.",
        reply_markup=build_main_menu_keyboard(),
    )


async def _handle_triage_answer(
    chat_id: int, text: str, bot: Bot, session: dict
) -> None:
    """
    Process one triage answer. After all 3, trigger LLM classification.
    """
    q_index = session["triage_question_index"]
    current_q = TRIAGE_QUESTIONS[q_index]

    # Validate the answer first
    validation = await validate_triage_answer(current_q, text)
    if not validation.get("is_valid", True):
        # Answer was evasive or unrelated
        reason = validation.get("reason", "Could you try answering the question?").strip()
        if not any(reason.endswith(e) for e in ("💙", "💛", "🤗", "🌟", "🛡️")):
            reason = f"{reason} 💙"
        prompt = f"{reason}\n\n*Question {q_index + 1} of 3:*\n{current_q}"
        msg = await bot.send_message(
            chat_id=chat_id,
            text=prompt,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_triage_panic_keyboard(),
        )
        _track_msg(chat_id, msg.message_id)
        return

    session["triage_answers"].append(text)
    session["triage_question_index"] = q_index + 1

    next_q = q_index + 1  # 0-indexed, so after first answer next_q = 1

    if next_q < 3:
        # Ask the next question, with personalized acknowledgement of the previous answer
        ack_text = await generate_personalized_response(
            answers=session["triage_answers"],
            response_type=f"triage_ack_{next_q}",
            severity="PENDING",
            fallback="✅ Got it. Thank you.",
        )
        prompt = (
            f"{ack_text}\n\n*Question {next_q + 1} of 3:*\n{TRIAGE_QUESTIONS[next_q]}"
        )
        msg = await bot.send_message(
            chat_id=chat_id,
            text=prompt,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_triage_panic_keyboard(),
        )
        _track_msg(chat_id, msg.message_id)
    else:
        # All 3 answers collected — classify with LLM
        _set_state(chat_id, State.STATE_AWAITING_SEVERITY)

        await bot.send_message(
            chat_id=chat_id,
            text="🔄 *Analyzing your situation…* Please wait a moment.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_panic_keyboard(),
        )

        # Call LLM
        result = await run_triage_llm(session["triage_answers"])
        severity = result.get("severity", "MEDIUM")
        summary = result.get("summary", "No summary available.")

        logger.info("Triage result: severity=%s", severity)

        # --- Route based on severity ---
        if severity == "POCSO":
            await _handle_pocso(chat_id, bot)
        elif severity == "HIGH":
            await _handle_high_severity(chat_id, bot, summary)
        elif severity == "MEDIUM":
            await _handle_medium_severity(chat_id, bot, summary)
        else:  # LOW
            await _handle_low_severity(chat_id, bot, summary)


async def _handle_pocso(chat_id: int, bot: Bot) -> None:
    """
    POCSO detected — immediately redirect to Childline 1098.
    Do NOT route to counselor relay. Terminate AI triage flow.
    """
    _set_state(chat_id, State.STATE_POCSO_REDIRECT)
    logger.info("POCSO redirect for user_hash=%s", _hash_chat_id(chat_id))

    msg = await bot.send_message(
        chat_id=chat_id,
        text=POCSO_REDIRECT_TEXT,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_pocso_keyboard(),
    )
    _track_msg(chat_id, msg.message_id)


async def _handle_high_severity(chat_id: int, bot: Bot, summary: str) -> None:
    """
    HIGH severity — automatically open a case and alert counselor group.
    """
    fallback_text = (
        "💙 *Hey, I hear you. What you're going through sounds really tough, "
        "and I want to make sure you get the help you deserve.*\n\n"
        "I'm connecting you with a trained volunteer right now — "
        "a real person who genuinely cares and is ready to listen. "
        "They won't be able to see your name or who you are, so you're completely safe. 🔒\n\n"
        "You did the right thing by speaking up. That took real courage. 🌟\n\n"
        "Just wait here — they'll be with you very soon."
    )
    session = _get_session(chat_id)
    custom_text = await generate_personalized_response(
        answers=session.get("triage_answers", []),
        response_type="escalation_high",
        severity="HIGH",
        fallback=fallback_text,
    )

    msg = await bot.send_message(
        chat_id=chat_id,
        text=custom_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_panic_keyboard(),
    )
    _track_msg(chat_id, msg.message_id)
    await _create_case_and_alert(chat_id, bot, "HIGH", summary)


async def _handle_medium_severity(chat_id: int, bot: Bot, summary: str) -> None:
    """
    MEDIUM severity — show self-help resources + offer to talk to human.
    """
    _set_state(chat_id, State.STATE_SELF_HELP)
    session = _get_session(chat_id)

    custom_text = await generate_personalized_response(
        answers=session.get("triage_answers", []),
        response_type="self_help_medium",
        severity="MEDIUM",
        fallback=SELF_HELP_MEDIUM,
    )

    msg = await bot.send_message(
        chat_id=chat_id,
        text=custom_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_self_help_keyboard("MEDIUM"),
    )
    _track_msg(chat_id, msg.message_id)


async def _handle_low_severity(chat_id: int, bot: Bot, summary: str) -> None:
    """
    LOW severity — show self-help resources + offer to talk to human.
    """
    _set_state(chat_id, State.STATE_SELF_HELP)
    session = _get_session(chat_id)

    custom_text = await generate_personalized_response(
        answers=session.get("triage_answers", []),
        response_type="self_help_low",
        severity="LOW",
        fallback=SELF_HELP_LOW,
    )

    msg = await bot.send_message(
        chat_id=chat_id,
        text=custom_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_self_help_keyboard("LOW"),
    )
    _track_msg(chat_id, msg.message_id)


# ==============================================================================
# SECTION: ERROR HANDLER
# ==============================================================================


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Global error handler. Logs the exception without crashing the bot.
    Never exposes stack traces to end users.
    """
    logger.error(
        "Exception while handling an update: %s",
        context.error,
        exc_info=context.error,
    )

    # Try to notify the user that something went wrong, without details
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "⚠️ Something went wrong on my end. Please try again.\n\n"
                    "If the issue persists, send /start to reset.\n"
                    "📞 In an emergency: *Childline 1098*"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass  # If we can't even send this, just log and move on


# ==============================================================================
# SECTION: MAIN ENTRYPOINT
# ==============================================================================


async def run_bot() -> None:
    """
    Async entrypoint for the bot.

    Python 3.12+ (and especially 3.14) no longer auto-creates an event loop
    and deprecated set_event_loop_policy. PTB's run_polling() calls
    asyncio.get_event_loop() internally which raises RuntimeError when no
    loop exists in the main thread.

    The fix: drive the Application manually inside an async function so that
    asyncio.run() below creates and owns the event loop cleanly.
    This is the officially recommended PTB pattern for Python 3.12+.
    """
    logger.info("Starting Project Albert...")

    # Build the Application (v20+ API — NOT the deprecated v13 Updater)
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # --- Register handlers ---
    # Order matters: more specific handlers first.

    # Command handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("wipe", cmd_wipe))
    application.add_handler(CommandHandler("help", cmd_help))

    # Callback query handler (all inline button presses)
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Message handler (plain text only, no commands)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
    )

    # Global error handler
    application.add_error_handler(error_handler)

    logger.info(
        "Bot configured. Handlers registered. Starting polling... (Ctrl+C to stop)"
    )

    # Manually drive the Application lifecycle so asyncio.run() owns the loop.
    # initialize() sets up the bot connection, start() begins background tasks,
    # updater.start_polling() begins receiving updates, idle() blocks until
    # Ctrl+C, then stop() and shutdown() clean up gracefully.
    async with application:
        await application.initialize()
        await application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        await application.start()
        logger.info("Albert is live. Press Ctrl+C to stop.")
        # Block here until the user presses Ctrl+C
        await asyncio.Event().wait()


def main() -> None:
    """Synchronous entrypoint — creates a fresh event loop via asyncio.run()."""
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Shutdown requested — Albert stopped.")


if __name__ == "__main__":
    main()
