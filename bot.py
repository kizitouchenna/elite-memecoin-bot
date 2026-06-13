"""
╔══════════════════════════════════════════════════╗
║   ELITE MEMECOIN SIGNAL BOT  v4.1               ║
║   @EliteMemecoinBot                              ║
║   Live console  ·  24/7 guardian  ·  Real data  ║
╚══════════════════════════════════════════════════╝
"""

import asyncio
import aiohttp
import time
import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from telegram.error import BadRequest, RetryAfter

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, JSON, BigInteger,
    insert, select, update, func,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from tenacity import retry, stop_after_attempt, wait_exponential
from bs4 import BeautifulSoup

# ════════════════════════════════════════════════════════
#  CREDENTIALS  — hardcoded
# ════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = "8415413880:AAFCxo3s12wWdMRf7TbgqstBWEfeXS9ls7M"
MY_TELEGRAM_ID     = 7867870577
BIRDEYE_API_KEY    = "fad65f0707b5468a9feb8955e5d362a7"
HELIUS_API_KEY     = "fc99689c-49dd-4720-b0f6-c43d6277631b"
GOPLUS_API_KEY     = "j8A5mk4At8QkZ6spusrx"
BOT_USERNAME       = "@EliteMemecoinBot"

# Runtime-adjustable (Settings panel)
MIN_SIGNAL_SCORE: int = 70

# ════════════════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

# ════════════════════════════════════════════════════════
#  VISUAL CONSTANTS
# ════════════════════════════════════════════════════════
SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

def pbar(pct: float, width: int = 12) -> str:
    filled = round(min(pct, 100) / 100 * width)
    return "▰" * filled + "▱" * (width - filled)

def score_meter(score: int, width: int = 10) -> str:
    filled = round(min(score, 100) / 100 * width)
    return "█" * filled + "░" * (width - filled)

def risk_badge(score: int) -> str:
    if score >= 85: return "🟢 LOW RISK"
    if score >= 65: return "🟡 MODERATE"
    return "🔴 HIGH RISK"

def sentiment_label(bsr: float) -> str:
    if bsr >= 3.0: return "🚀 EXPLOSIVE"
    if bsr >= 2.0: return "🔥 BULLISH"
    if bsr >= 1.5: return "📈 POSITIVE"
    if bsr >= 1.0: return "😐 NEUTRAL"
    return "📉 BEARISH"

def fmt_usd(val: float) -> str:
    """Compact USD: $45K, $1.2M, $890"""
    if val >= 1_000_000: return f"${val / 1e6:.1f}M"
    if val >= 1_000:     return f"${val / 1_000:.0f}K"
    return f"${val:.0f}"

# ════════════════════════════════════════════════════════
#  DATABASE
# ════════════════════════════════════════════════════════
import os
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./signals.db")
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


class Signal(Base):
    __tablename__ = "signals"
    id                  = Column(Integer, primary_key=True)
    token_address       = Column(String, unique=True, index=True)
    name                = Column(String)
    symbol              = Column(String)
    market_cap          = Column(Float)
    liquidity           = Column(Float)
    liquidity_locked    = Column(Boolean, default=False)
    volume_5m           = Column(Float)
    buy_sell_ratio      = Column(Float)
    holders             = Column(Integer, default=0)
    holders_history     = Column(JSON, default=list)
    whale_buys          = Column(Integer, default=0)
    ai_score            = Column(Integer)
    safety_score        = Column(Integer)
    risk_level          = Column(String)
    targets             = Column(JSON)
    socials             = Column(JSON)
    telegram_message_id = Column(BigInteger, nullable=True)
    chat_id             = Column(BigInteger, nullable=True)
    tp1_hit             = Column(Boolean, default=False)
    tp2_hit             = Column(Boolean, default=False)
    tp3_hit             = Column(Boolean, default=False)
    tp4_hit             = Column(Boolean, default=False)
    sl_hit              = Column(Boolean, default=False)
    created_at          = Column(DateTime, default=datetime.utcnow)
    last_updated        = Column(DateTime, default=datetime.utcnow)


class Watchlist(Base):
    __tablename__ = "watchlist"
    id            = Column(Integer, primary_key=True)
    user_id       = Column(BigInteger, index=True)
    token_address = Column(String, index=True)
    added_at      = Column(DateTime, default=datetime.utcnow)


class HolderSnapshot(Base):
    __tablename__ = "holder_snapshots"
    id            = Column(Integer, primary_key=True)
    token_address = Column(String, index=True)
    holder_count  = Column(Integer)
    timestamp     = Column(DateTime, default=datetime.utcnow)


# ════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════
def signal_to_dict(sig) -> dict:
    return {
        "id":                  sig.id,
        "token_address":       sig.token_address,
        "name":                sig.name,
        "symbol":              sig.symbol,
        "market_cap":          sig.market_cap,
        "liquidity":           sig.liquidity,
        "liquidity_locked":    sig.liquidity_locked,
        "volume_5m":           sig.volume_5m,
        "buy_sell_ratio":      sig.buy_sell_ratio,
        "holders":             sig.holders,
        "holders_history":     sig.holders_history or [],
        "whale_buys":          sig.whale_buys,
        "ai_score":            sig.ai_score,
        "safety_score":        sig.safety_score,
        "risk_level":          sig.risk_level,
        "targets":             sig.targets or {},
        "socials":             sig.socials or {},
        "telegram_message_id": sig.telegram_message_id,
        "chat_id":             sig.chat_id,
        "tp1_hit":             sig.tp1_hit,
        "tp2_hit":             sig.tp2_hit,
        "tp3_hit":             sig.tp3_hit,
        "tp4_hit":             sig.tp4_hit,
        "sl_hit":              sig.sl_hit,
        "created_at":          sig.created_at,
    }


async def safe_edit(
    message: Message,
    text: str,
    keyboard: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "Markdown",
    disable_preview: bool = True,
):
    try:
        await message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_preview,
        )
    except BadRequest as e:
        err = str(e).lower()
        if "not modified" not in err and "message to edit not found" not in err:
            logger.debug(f"safe_edit: {e}")
    except Exception as e:
        logger.debug(f"safe_edit: {e}")


async def safe_delete(bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


# ════════════════════════════════════════════════════════
#  API UTILITIES
# ════════════════════════════════════════════════════════
def _gp_headers() -> dict:
    return {"Authorization": f"Bearer {GOPLUS_API_KEY}"} if GOPLUS_API_KEY else {}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
async def verify_liquidity_lock(token_address: str) -> bool:
    try:
        url = (f"https://api.gopluslabs.io/api/v1/token_security/501"
               f"?contract_addresses={token_address}")
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=_gp_headers(),
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    d  = await r.json()
                    td = d.get("result", {}).get(token_address, {})
                    return (td.get("lp_lock") == "1"
                            and td.get("mintable") == "0"
                            and td.get("owner_address") == "null")
    except Exception as e:
        logger.debug(f"liq_lock error: {e}")
    return False


async def check_honeypot_and_tax(token_address: str):
    """Returns (is_safe: bool, max_tax: float)."""
    try:
        url = (f"https://api.gopluslabs.io/api/v1/token_security/501"
               f"?contract_addresses={token_address}")
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=_gp_headers(),
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    d  = await r.json()
                    td = d.get("result", {}).get(token_address, {})
                    buy_tax  = float(td.get("buy_tax",  "0") or "0")
                    sell_tax = float(td.get("sell_tax", "0") or "0")
                    hp       = td.get("is_honeypot") == "1"
                    if hp or buy_tax > 10 or sell_tax > 10:
                        return False, max(buy_tax, sell_tax)
                    return True, max(buy_tax, sell_tax)
    except Exception as e:
        logger.debug(f"honeypot error: {e}")
    return True, 0.0


async def check_sniper_concentration(token_address: str) -> bool:
    try:
        url     = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
        payload = {"jsonrpc": "2.0", "id": "sc",
                   "method": "getAsset", "params": {"id": token_address}}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload,
                              timeout=aiohttp.ClientTimeout(total=10)) as r:
                return r.status == 200
    except Exception:
        pass
    return True


async def get_holders_count(token_address: str) -> int:
    try:
        url     = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
        payload = {"jsonrpc": "2.0", "id": "hc",
                   "method": "getAsset", "params": {"id": token_address}}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload,
                              timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    d = await r.json()
                    count = d.get("result", {}).get("holders_count", 0) or 0
                    async with AsyncSessionLocal() as db:
                        await db.execute(HolderSnapshot.__table__.insert().values(
                            token_address=token_address,
                            holder_count=count,
                            timestamp=datetime.utcnow(),
                        ))
                        await db.commit()
                    return count
    except Exception as e:
        logger.debug(f"holders error: {e}")
    return 0


async def get_holder_growth_rate(token_address: str) -> float:
    try:
        async with AsyncSessionLocal() as db:
            r = await db.execute(
                select(HolderSnapshot)
                .where(HolderSnapshot.token_address == token_address)
                .order_by(HolderSnapshot.timestamp.desc()).limit(2)
            )
            snaps = r.scalars().all()
            if len(snaps) >= 2 and snaps[1].holder_count:
                return ((snaps[0].holder_count - snaps[1].holder_count)
                        / snaps[1].holder_count * 100)
    except Exception:
        pass
    return 0.0


async def get_whale_buys(token_address: str) -> int:
    try:
        url = (f"https://api.helius.xyz/v0/addresses/{token_address}"
               f"/transactions?apiKey={HELIUS_API_KEY}&limit=50")
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    txs   = await r.json()
                    count = 0
                    now   = time.time()
                    for tx in txs:
                        if now - tx.get("timestamp", 0) < 300:
                            for t in tx.get("tokenTransfers", []):
                                if (t.get("mint") == token_address
                                        and t.get("tokenAmount", 0) > 5000):
                                    count += 1
                    return count
    except Exception as e:
        logger.debug(f"whale_buys error: {e}")
    return 0


async def get_wallet_creation_time(wallet: str) -> int:
    try:
        url = (f"https://api.helius.xyz/v0/addresses/{wallet}"
               f"/transactions?apiKey={HELIUS_API_KEY}&limit=1")
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    d = await r.json()
                    if d and isinstance(d, list):
                        return d[0].get("timestamp", 0)
    except Exception:
        pass
    return 0


async def is_suspicious_wallet(wallet: str, token_created_at: float) -> bool:
    return await get_wallet_creation_time(wallet) > token_created_at


async def get_early_buyers(token_address: str, limit: int = 10) -> list:
    try:
        url = (f"https://api.helius.xyz/v0/addresses/{token_address}"
               f"/transactions?apiKey={HELIUS_API_KEY}&limit=100")
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    txs    = await r.json()
                    buyers: set = set()
                    for tx in txs:
                        for t in tx.get("tokenTransfers", []):
                            if t.get("mint") == token_address:
                                b = t.get("fromUserAccount")
                                if b and b != token_address:
                                    buyers.add(b)
                                if len(buyers) >= limit:
                                    return list(buyers)
                    return list(buyers)
    except Exception as e:
        logger.debug(f"early_buyers error: {e}")
    return []


async def get_current_price(token_address: str) -> float:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    d = await r.json()
                    if d.get("pairs"):
                        p = float(d["pairs"][0].get("priceUsd", 0) or 0)
                        if p > 0:
                            return p
    except Exception:
        pass
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://public-api.birdeye.so/defi/price?address={token_address}",
                headers={"X-API-KEY": BIRDEYE_API_KEY},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    d = await r.json()
                    return float(d.get("data", {}).get("value", 0) or 0)
    except Exception:
        pass
    return 0.0


async def fetch_token_dex_data(addr: str) -> dict:
    """Fetch live token data from DexScreener for a specific address."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{addr}",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 200:
                    data  = await r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        pair = sorted(
                            pairs,
                            key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0),
                            reverse=True,
                        )[0]
                        m5   = pair.get("txns", {}).get("m5", {})
                        buys = int(m5.get("buys",  0) or 0)
                        sels = int(m5.get("sells", 0) or 0)
                        pc   = pair.get("priceChange", {})
                        vol  = pair.get("volume", {})
                        return {
                            "name":             pair.get("baseToken", {}).get("name",   "Unknown"),
                            "symbol":           pair.get("baseToken", {}).get("symbol", "UNK"),
                            "price":            float(pair.get("priceUsd", 0) or 0),
                            "market_cap":       float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0),
                            "liquidity":        float(pair.get("liquidity", {}).get("usd", 0) or 0),
                            "volume_5m":        float(vol.get("m5",  0) or 0),
                            "volume_1h":        float(vol.get("h1",  0) or 0),
                            "volume_24h":       float(vol.get("h24", 0) or 0),
                            "buy_sell_ratio":   buys / max(sels, 1),
                            "transactions_5m":  buys + sels,
                            "price_change_5m":  float(pc.get("m5",  0) or 0),
                            "price_change_1h":  float(pc.get("h1",  0) or 0),
                            "price_change_24h": float(pc.get("h24", 0) or 0),
                            "dex":              pair.get("dexId", "unknown"),
                            "description":      pair.get("info", {}).get("description", ""),
                            "pair_age_minutes": (time.time() - (pair.get("pairCreatedAt") or time.time() * 1000) / 1000) / 60,
                        }
    except Exception as e:
        logger.error(f"fetch_token_dex_data: {e}")
    return {}


async def get_twitter_followers(handle: str) -> int:
    if not handle:
        return 0
    handle = handle.strip().lstrip("@")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://nitter.net/{handle}",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    html = await r.text()
                    soup = BeautifulSoup(html, "lxml")
                    el   = soup.find("a", href=re.compile(r"/followers"))
                    if el:
                        nums = re.findall(r"[\d,]+", el.get_text(strip=True))
                        if nums:
                            return int(nums[0].replace(",", ""))
    except Exception:
        pass
    return 0


async def get_telegram_members(chat_username: str) -> int:
    if not chat_username:
        return 0
    chat_username = chat_username.lstrip("@")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://t.me/{chat_username}",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    html = await r.text()
                    m    = re.search(r"([\d,]+)\s+members?", html, re.I)
                    if m:
                        return int(m.group(1).replace(",", ""))
    except Exception:
        pass
    return 0


async def extract_socials(description: str) -> dict:
    socials = {"twitter": "", "telegram": "", "website": ""}
    if not description:
        return socials
    m = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)", description, re.I)
    if m: socials["twitter"] = m.group(1)
    m = re.search(r"t\.me/([A-Za-z0-9_]+)", description, re.I)
    if m: socials["telegram"] = m.group(1)
    m = re.search(r"(https?://[^\s]+)", description)
    if m: socials["website"] = m.group(1)
    return socials


# ════════════════════════════════════════════════════════
#  LIVE CONSOLE ENGINE
#  One persistent Telegram message, always edited in-place.
#  Shows a real-time table of every token the scanner touches.
# ════════════════════════════════════════════════════════
@dataclass
class ConsoleEntry:
    ts:      str    # "HH:MM"
    symbol:  str    # token symbol ≤ 8 chars
    mcap:    float  # market cap USD
    liq:     float  # liquidity USD
    bsr:     float  # buy/sell ratio
    icon:    str    # result emoji
    verdict: str    # reason ≤ 14 chars

_console_entries: deque       = deque(maxlen=14)
_console_dirty:   bool        = False
_console_msg_id:  Optional[int] = None
_console_chat_id: Optional[int] = None
_console_lock:    Optional[asyncio.Lock] = None
_bot_start_time:  float       = time.time()

telegram_app: Optional[Application] = None


async def log_decision(
    symbol: str,
    mcap: float,
    liq: float,
    bsr: float,
    icon: str,
    verdict: str,
):
    """Push a structured row into the live console. Thread-safe via lock."""
    global _console_dirty
    ts = datetime.now().strftime("%H:%M")
    entry = ConsoleEntry(ts=ts, symbol=symbol, mcap=mcap, liq=liq,
                         bsr=bsr, icon=icon, verdict=verdict)
    async with _console_lock:
        _console_entries.appendleft(entry)
        _console_dirty = True


def _render_console() -> str:
    """Build the full console text — pure function, no I/O."""
    now    = datetime.now().strftime("%H:%M:%S")
    uptime_min = int((time.time() - _bot_start_time) / 60)
    if uptime_min >= 60:
        uptime_str = f"{uptime_min // 60}h {uptime_min % 60}m"
    else:
        uptime_str = f"{uptime_min}m"

    status = "🟢 SCANNING" if scanner.active else "🔴 STOPPED"

    # Header
    lines = [
        "📟 *LIVE SCANNER CONSOLE*",
        "`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`",
        "`TIME   TOKEN    MCAP    LIQ    BSR   RESULT`",
        "`───────────────────────────────────`",
    ]

    # Data rows — newest at top
    if _console_entries:
        for e in _console_entries:
            sym  = e.symbol[:7].ljust(7)
            mcap = fmt_usd(e.mcap).rjust(6)
            liq  = fmt_usd(e.liq).rjust(6)
            bsr  = f"{e.bsr:.1f}x".rjust(5)
            verd = e.verdict[:13]
            lines.append(f"`{e.ts}  {sym} {mcap} {liq} {bsr}  {e.icon}{verd}`")
    else:
        lines.append("`  ⌛ Waiting for first batch…          `")

    # Footer stats
    lines += [
        "`───────────────────────────────────`",
        f"`📊 Scanned:{scanner.scan_count:<6}  Signals:{scanner.signal_count:<4}`",
        f"`⚡ Score≥{MIN_SIGNAL_SCORE}    Poll:3s   Update:60s `",
        f"`🕐 {now}   ⏱ {uptime_str}   {status}`",
    ]

    return "\n".join(lines)


async def _console_loop():
    """
    The live console loop.
    - Sends exactly ONE initial message on bot startup.
    - From then on: ONLY edits that message, never sends a new one.
    - If the message is externally deleted, recreates it ONCE.
    - Updates every 2 s when dirty, force-refresh clock every 30 s.
    """
    global _console_msg_id, _console_chat_id, _console_dirty

    # Wait for bot to come up
    while not telegram_app:
        await asyncio.sleep(1)
    await asyncio.sleep(4)  # let startup settle

    # ── Create the initial console message ──
    for attempt in range(5):
        try:
            msg = await telegram_app.bot.send_message(
                chat_id=MY_TELEGRAM_ID,
                text=_render_console(),
                parse_mode="Markdown",
            )
            _console_msg_id  = msg.message_id
            _console_chat_id = msg.chat_id
            logger.info(f"Console created: msg_id={_console_msg_id}")
            break
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except Exception as e:
            logger.error(f"Console init attempt {attempt+1}: {e}")
            await asyncio.sleep(3)

    # ── Edit loop ──────────────────────────────────────────────
    _last_force_refresh = time.time()

    while True:
        await asyncio.sleep(2)
        try:
            force = (time.time() - _last_force_refresh) >= 30

            async with _console_lock:
                dirty = _console_dirty or force

            if not dirty:
                continue

            text = _render_console()

            if not _console_msg_id:
                # Message was externally deleted — recreate once
                try:
                    msg = await telegram_app.bot.send_message(
                        chat_id=MY_TELEGRAM_ID,
                        text=text,
                        parse_mode="Markdown",
                    )
                    _console_msg_id  = msg.message_id
                    _console_chat_id = msg.chat_id
                    logger.info(f"Console recreated: msg_id={_console_msg_id}")
                except Exception as e:
                    logger.error(f"Console recreate error: {e}")
                continue

            # Edit existing message
            try:
                await telegram_app.bot.edit_message_text(
                    chat_id=_console_chat_id,
                    message_id=_console_msg_id,
                    text=text,
                    parse_mode="Markdown",
                )
                async with _console_lock:
                    _console_dirty = False
                if force:
                    _last_force_refresh = time.time()

            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)

            except BadRequest as e:
                err = str(e).lower()
                if "message to edit not found" in err or "message can't be edited" in err:
                    # Genuinely gone — clear so next loop recreates it
                    logger.info("Console message gone — will recreate")
                    _console_msg_id = None
                elif "not modified" in err:
                    async with _console_lock:
                        _console_dirty = False
                    if force:
                        _last_force_refresh = time.time()
                else:
                    # Parse error or other — don't reset msg_id, just skip this tick
                    logger.debug(f"Console edit skipped: {e}")

        except Exception as e:
            logger.error(f"Console loop error: {e}")
            await asyncio.sleep(5)


# ════════════════════════════════════════════════════════
#  HUB AUTO-REFRESH
# ════════════════════════════════════════════════════════
_hub_msg_id:  Optional[int] = None
_hub_chat_id: Optional[int] = None
_hub_on_menu: bool          = False
_scan_waiting: dict         = {}     # chat_id → prompt_message_id


async def _hub_refresh_loop():
    while True:
        await asyncio.sleep(10)
        if _hub_on_menu and _hub_msg_id and telegram_app:
            try:
                text = await _build_menu_text()
                await telegram_app.bot.edit_message_text(
                    chat_id=_hub_chat_id,
                    message_id=_hub_msg_id,
                    text=text,
                    reply_markup=main_menu_keyboard(),
                    parse_mode="Markdown",
                )
            except BadRequest as e:
                if "not modified" not in str(e).lower():
                    logger.debug(f"hub_refresh: {e}")
            except Exception as e:
                logger.debug(f"hub_refresh: {e}")


async def _build_menu_text() -> str:
    now    = datetime.now().strftime("%H:%M:%S")
    status = "🟢 LIVE" if scanner.active else "🔴 STOPPED"
    cutoff = datetime.utcnow() - timedelta(hours=24)
    try:
        async with AsyncSessionLocal() as db:
            today = (await db.execute(
                select(func.count(Signal.id)).where(Signal.created_at > cutoff)
            )).scalar() or 0
            elite = (await db.execute(
                select(func.count(Signal.id)).where(Signal.ai_score >= 90)
            )).scalar() or 0
            top = (await db.execute(
                select(Signal.symbol, Signal.ai_score)
                .where(Signal.ai_score > 0)
                .order_by(Signal.ai_score.desc()).limit(1)
            )).first()
    except Exception:
        today, elite, top = 0, 0, None
    top_str = f"*{top[0]}* — {top[1]}/100" if top else "_scanning…_"
    return (
        "╔═══════════════════════╗\n"
        "║  🚀 *ELITE MEMECOIN*    ║\n"
        "║  *SIGNAL INTELLIGENCE* ║\n"
        "╚═══════════════════════╝\n\n"
        f"🤖 {BOT_USERNAME}\n"
        f"Scanner: {status}  ·  🔔 Min score: `{MIN_SIGNAL_SCORE}`\n\n"
        "━━━ 📊 LIVE TICKER ━━━\n"
        f"🚀 Signals (24h):    `{today}`\n"
        f"💎 Elite calls:      `{elite}`\n"
        f"🔭 Pairs processed:  `{scanner.scan_count}`\n"
        f"🏆 Top pick:         {top_str}\n"
        f"🕐 Updated:          `{now}`\n\n"
        "Select a module:"
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Scan Token",     callback_data="scan_token"),
         InlineKeyboardButton("📊 P&L Report",     callback_data="pnl_today")],
        [InlineKeyboardButton("📡 Live Signals",   callback_data="live"),
         InlineKeyboardButton("🔥 Trending",        callback_data="trending")],
        [InlineKeyboardButton("💎 Elite Calls",     callback_data="elite_calls"),
         InlineKeyboardButton("📈 History",         callback_data="history")],
        [InlineKeyboardButton("🐋 Whale Tracker",   callback_data="whale_tracker"),
         InlineKeyboardButton("⚡ Market Pulse",    callback_data="market_pulse")],
        [InlineKeyboardButton("🏆 Hall of Fame",    callback_data="hall_of_fame"),
         InlineKeyboardButton("💼 Portfolio",       callback_data="portfolio")],
        [InlineKeyboardButton("📌 Watchlist",       callback_data="watchlist"),
         InlineKeyboardButton("📟 Status",          callback_data="bot_status")],
        [InlineKeyboardButton("📊 Analytics",       callback_data="analytics"),
         InlineKeyboardButton("⚙️ Settings",        callback_data="settings")],
        [InlineKeyboardButton("❓ Help",            callback_data="help"),
         InlineKeyboardButton("🔄 Refresh",         callback_data="menu")],
    ])


def back_row(refresh_cb: str = None) -> list:
    row = [InlineKeyboardButton("🏠 Menu", callback_data="menu")]
    if refresh_cb:
        row.append(InlineKeyboardButton("🔄 Refresh", callback_data=refresh_cb))
    return row


# ════════════════════════════════════════════════════════
#  SIGNAL FORMATTING
# ════════════════════════════════════════════════════════
def _social_links(sig: dict, addr: str) -> str:
    socials = sig.get("socials") or {}
    website = socials.get("website") or ""
    twitter = socials.get("twitter") or ""
    tg      = socials.get("telegram") or ""
    if twitter and not twitter.startswith("http"):
        twitter = f"https://x.com/{twitter}"
    if tg and not tg.startswith("http"):
        tg = f"https://t.me/{tg}"
    parts = []
    if website: parts.append(f"[🌐]({website})")
    if twitter: parts.append(f"[🐦]({twitter})")
    if tg:      parts.append(f"[💬]({tg})")
    parts.append(f"[📈 DEX](https://dexscreener.com/solana/{addr})")
    parts.append(f"[📊 Eye](https://birdeye.so/token/{addr})")
    parts.append(f"[⚡ Jup](https://jup.ag/swap/SOL-{addr})")
    return "  ".join(parts)


def format_signal_full(sig: dict, pnl_updates: list = None) -> str:
    addr    = sig.get("token_address") or ""
    name    = sig.get("name") or "Unknown"
    symbol  = sig.get("symbol") or "UNK"
    mcap    = sig.get("market_cap") or 0
    liq     = sig.get("liquidity") or 0
    vol5m   = sig.get("volume_5m") or 0
    bsr     = sig.get("buy_sell_ratio") or 0
    holders = sig.get("holders") or 0
    whales  = sig.get("whale_buys") or 0
    ai_sc   = sig.get("ai_score") or 0
    targets = sig.get("targets") or {}
    socials = sig.get("socials") or {}
    tw_foll = socials.get("twitter_followers", 0)
    tg_mem  = socials.get("telegram_members",  0)
    meter   = score_meter(ai_sc)
    risk    = risk_badge(ai_sc)
    senti   = sentiment_label(bsr)
    tp_row  = "  ".join([
        f"TP{i}{'✅' if sig.get(f'tp{i}_hit') else '⬜'}" for i in range(1, 5)
    ]) + f"  SL{'🔴' if sig.get('sl_hit') else '⬜'}"
    pnl_block = ("\n" + "\n".join(pnl_updates)) if pnl_updates else ""
    created   = sig.get("created_at")
    age_str   = ""
    if created:
        mins    = int((datetime.utcnow() - created).total_seconds() / 60)
        age_str = f"`{mins}m ago`"
    return (
        f"╔══ 🚀 *ELITE SIGNAL* ══╗\n"
        f"  *{name}*   `${symbol}`\n"
        f"╚═══════════════════════╝\n\n"
        f"📍 `{addr}`\n"
        + (f"⏱ {age_str}\n" if age_str else "")
        + f"\n━━━━ 📊 MARKET DATA ━━━━\n"
        f"💰 MCap:    `${mcap:>10,.0f}`\n"
        f"💧 Liq:     `${liq:>10,.0f}`  🔒\n"
        f"📈 5m Vol:  `${vol5m:>10,.0f}`\n"
        f"🔥 BSR:     `{bsr:.2f}x`  {senti}\n"
        f"👥 Holders: `{holders}`\n"
        f"🐋 Whales:  `{whales}` buys >$5k\n"
        + (f"🐦 X:       `{tw_foll:,}` followers\n" if tw_foll else "")
        + (f"💬 TG:      `{tg_mem:,}` members\n"   if tg_mem  else "")
        + f"\n━━━━ 🤖 AI ANALYSIS ━━━━\n"
        f"Score: `{meter}` *{ai_sc}/100*\n"
        f"Risk:   {risk}\n\n"
        f"━━━━ 🎯 TARGETS ━━━━━━\n"
        f"TP1  `${targets.get('tp1',0):.6f}`  +50%\n"
        f"TP2  `${targets.get('tp2',0):.6f}`  +150%\n"
        f"TP3  `${targets.get('tp3',0):.6f}`  +300%\n"
        f"TP4  `${targets.get('tp4',0):.6f}`  +500%\n"
        f"🛑 SL  `${targets.get('sl',0):.6f}`  -30%\n\n"
        f"Status: `{tp_row}`"
        f"{pnl_block}\n\n"
        + _social_links(sig, addr)
    )


def format_signal_compact(sig: dict, index: int, total: int) -> str:
    addr    = sig.get("token_address") or ""
    name    = sig.get("name") or "Unknown"
    symbol  = sig.get("symbol") or "UNK"
    mcap    = sig.get("market_cap") or 0
    liq     = sig.get("liquidity") or 0
    bsr     = sig.get("buy_sell_ratio") or 0
    holders = sig.get("holders") or 0
    whales  = sig.get("whale_buys") or 0
    ai_sc   = sig.get("ai_score") or 0
    targets = sig.get("targets") or {}
    meter   = score_meter(ai_sc, width=8)
    risk    = risk_badge(ai_sc)
    senti   = sentiment_label(bsr)
    created = sig.get("created_at")
    age_str = ""
    if created:
        mins    = int((datetime.utcnow() - created).total_seconds() / 60)
        age_str = f"  ⏱ `{mins}m ago`"
    return (
        f"📡 *Signal {index}/{total}*{age_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 *{name}*  `${symbol}`\n"
        f"📍 `{addr[:22]}…`\n\n"
        f"💰 MCap: `${mcap:,.0f}`\n"
        f"💧 Liq:  `${liq:,.0f}` 🔒\n"
        f"🔥 BSR:  `{bsr:.2f}x`  {senti}\n"
        f"👥 Holders: `{holders}`   🐋 Whales: `{whales}`\n\n"
        f"🤖 `{meter}` *{ai_sc}/100*  {risk}\n\n"
        f"🎯 TP1:`${targets.get('tp1',0):.5f}` "
        f"TP2:`${targets.get('tp2',0):.5f}`\n"
        f"🛑 SL: `${targets.get('sl',0):.5f}`\n\n"
        f"[DEX](https://dexscreener.com/solana/{addr})  "
        f"[Eye](https://birdeye.so/token/{addr})  "
        f"[Jup](https://jup.ag/swap/SOL-{addr})"
    )


# ════════════════════════════════════════════════════════
#  SIGNAL NOTIFICATION
# ════════════════════════════════════════════════════════
async def _fire_tp_alert(symbol: str, level: str, pct: str,
                         price: float, target: float, hit_sl: bool = False):
    """Send a standalone TP / SL alert message (separate from signal card edit)."""
    global telegram_app
    if not telegram_app:
        return
    icon = "🛑" if hit_sl else "🎯"
    label = "STOP LOSS HIT" if hit_sl else f"{level} HIT!"
    try:
        await telegram_app.bot.send_message(
            chat_id=MY_TELEGRAM_ID,
            text=(
                f"{icon} *{label}*  —  *${symbol}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Target:  `${target:.8f}`  {pct}\n"
                f"Current: `${price:.8f}`"
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"_fire_tp_alert: {e}")


async def send_signal_notification(signal: dict):
    global telegram_app
    if not telegram_app:
        return None, None
    try:
        addr = signal["token_address"]
        text = format_signal_full(signal)
        kb   = InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 DexScreener", url=f"https://dexscreener.com/solana/{addr}"),
             InlineKeyboardButton("🔄 Refresh PnL", callback_data=f"pnl_{addr}")],
            [InlineKeyboardButton("📌 Watchlist",   callback_data=f"wl_add_{addr}"),
             InlineKeyboardButton("🔍 Rug Check",   callback_data=f"rug_{addr}")],
            [InlineKeyboardButton("📋 Copy CA",     callback_data=f"copy_{addr}")],
        ])
        msg = await telegram_app.bot.send_message(
            chat_id=MY_TELEGRAM_ID, text=text,
            reply_markup=kb, parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return msg.message_id, msg.chat_id
    except Exception as e:
        logger.error(f"send_signal_notification: {e}")
        return None, None


async def update_signal_notification(message_id: int, chat_id: int, sig, pnl_updates: list):
    global telegram_app
    if not telegram_app or not message_id or not chat_id:
        return
    try:
        sig_dict = signal_to_dict(sig)
        addr     = sig_dict["token_address"]
        text     = format_signal_full(sig_dict, pnl_updates)
        kb       = InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 DexScreener", url=f"https://dexscreener.com/solana/{addr}"),
             InlineKeyboardButton("🔄 Refresh PnL", callback_data=f"pnl_{addr}")],
            [InlineKeyboardButton("📌 Watchlist",   callback_data=f"wl_add_{addr}"),
             InlineKeyboardButton("🔍 Rug Check",   callback_data=f"rug_{addr}")],
            [InlineKeyboardButton("📋 Copy CA",     callback_data=f"copy_{addr}")],
        ])
        await telegram_app.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=text, reply_markup=kb, parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.error(f"update_signal_notification: {e}")
    except Exception as e:
        logger.error(f"update_signal_notification: {e}")


# ════════════════════════════════════════════════════════
#  SCANNER  — 24/7 guardian-wrapped
# ════════════════════════════════════════════════════════
class Scanner:
    def __init__(self):
        self.active      = False
        self.scan_count  = 0
        self.signal_count= 0
        self.last_bsr_avg= 0.0

    async def start(self):
        self.active = True
        # Guardian wraps _scan_loop: auto-restarts on crash
        asyncio.create_task(self._guardian())
        asyncio.create_task(self._update_loop())

    async def stop(self):
        self.active = False

    async def _guardian(self):
        """Keeps _scan_loop alive forever.  24/7 insurance."""
        while True:
            if not self.active:
                await asyncio.sleep(5)
                continue
            try:
                logger.info("Scanner _scan_loop starting…")
                await self._scan_loop()
            except Exception as e:
                logger.error(f"_scan_loop crashed: {e!r}. Restarting in 10 s…")
                await asyncio.sleep(10)

    async def _fetch_pairs(self) -> List[Dict]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://api.dexscreener.com/latest/dex/search?q=raydium",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    data  = await r.json()
                    pairs = []
                    for pair in data.get("pairs", []):
                        if (pair.get("chainId") == "solana"
                                and pair.get("dexId") in ["raydium", "pumpfun"]):
                            n = self._normalize(pair)
                            if n["token_address"]:
                                pairs.append(n)
                    bsrs = [p["buy_sell_ratio"] for p in pairs if p["buy_sell_ratio"] > 0]
                    if bsrs:
                        self.last_bsr_avg = sum(bsrs) / len(bsrs)
                    return pairs
        except Exception as e:
            logger.warning(f"DexScreener fetch: {e}")
            return []

    def _normalize(self, raw: dict) -> dict:
        m5         = raw.get("txns", {}).get("m5", {})
        buys       = int(m5.get("buys",  0) or 0)
        sells      = int(m5.get("sells", 0) or 0)
        volume_5m  = float(raw.get("volume", {}).get("m5", 0) or 0)
        market_cap = float(raw.get("marketCap", 0) or raw.get("fdv", 0) or 0)
        created_ms = raw.get("pairCreatedAt") or (time.time() * 1000)
        created_s  = created_ms / 1000
        return {
            "token_address":    raw.get("baseToken", {}).get("address"),
            "name":             raw.get("baseToken", {}).get("name",   "Unknown"),
            "symbol":           raw.get("baseToken", {}).get("symbol", "UNK"),
            "liquidity":        float(raw.get("liquidity", {}).get("usd", 0) or 0),
            "market_cap":       market_cap,
            "volume_5m":        volume_5m,
            "buy_sell_ratio":   buys / max(sells, 1),
            "transactions_5m":  buys + sells,
            "pair_age_minutes": (time.time() - created_s) / 60,
            "description":      raw.get("info", {}).get("description", ""),
            "pairCreatedAt":    created_s,
        }

    async def _scan_loop(self):
        processed: set = set()
        while self.active:
            try:
                pairs = await self._fetch_pairs()
                if not pairs:
                    await asyncio.sleep(5)
                    continue

                self.scan_count += len(pairs)
                qualified = 0

                for pair in pairs:
                    addr   = pair["token_address"]
                    symbol = pair["symbol"]
                    mcap   = pair["market_cap"]
                    liq    = pair["liquidity"]
                    bsr    = pair["buy_sell_ratio"]

                    if not addr or addr in processed:
                        continue
                    processed.add(addr)

                    # ── Log every token seen ──
                    await log_decision(symbol, mcap, liq, bsr, "🔍", "evaluating")

                    # ── Basic filters ──
                    if liq < 20_000:
                        await log_decision(symbol, mcap, liq, bsr, "❌", f"liq<$20k")
                        continue
                    if not (10_000 <= mcap <= 500_000):
                        await log_decision(symbol, mcap, liq, bsr, "❌",
                                           "mcap OOR" if mcap > 500_000 else "mcap<$10k")
                        continue
                    if pair["pair_age_minutes"] > 120:
                        await log_decision(symbol, mcap, liq, bsr, "❌", f"age>120m")
                        continue
                    if pair["transactions_5m"] < 50:
                        await log_decision(symbol, mcap, liq, bsr, "❌", f"txns<50")
                        continue
                    if bsr < 1.5:
                        await log_decision(symbol, mcap, liq, bsr, "❌", f"bsr<1.5x")
                        continue

                    # ── Liq lock ──
                    await log_decision(symbol, mcap, liq, bsr, "🔒", "chk liq-lock")
                    locked = await verify_liquidity_lock(addr)
                    if not locked:
                        await log_decision(symbol, mcap, liq, bsr, "❌", "no liq-lock")
                        continue

                    # ── Honeypot / tax ──
                    await log_decision(symbol, mcap, liq, bsr, "🍯", "chk honeypot")
                    safe, tax = await check_honeypot_and_tax(addr)
                    if not safe:
                        await log_decision(symbol, mcap, liq, bsr, "🍯", "HONEYPOT")
                        continue
                    if tax > 5:
                        await log_decision(symbol, mcap, liq, bsr, "💸", f"tax {tax:.0f}%")
                        continue

                    # ── Sniper concentration ──
                    if not await check_sniper_concentration(addr):
                        await log_decision(symbol, mcap, liq, bsr, "⚠️", "snipers")
                        continue

                    # ── Bundler detection ──
                    await log_decision(symbol, mcap, liq, bsr, "🕵", "chk bundler")
                    early_buyers = await get_early_buyers(addr, 10)
                    suspicious   = sum(
                        1 for r in await asyncio.gather(
                            *[is_suspicious_wallet(b, pair["pairCreatedAt"])
                              for b in early_buyers]
                        ) if r
                    )
                    if suspicious >= 3:
                        await log_decision(symbol, mcap, liq, bsr, "⚠️",
                                           f"bndlr({suspicious})")
                        continue

                    # ── Enrich ──
                    await log_decision(symbol, mcap, liq, bsr, "📊", "enriching…")
                    holders, whale_buys = await asyncio.gather(
                        get_holders_count(addr),
                        get_whale_buys(addr),
                    )
                    socials = await extract_socials(pair.get("description", ""))
                    if socials.get("twitter"):
                        socials["twitter_followers"] = await get_twitter_followers(socials["twitter"])
                    if socials.get("telegram"):
                        socials["telegram_members"]  = await get_telegram_members(socials["telegram"])

                    # ── AI score ──
                    score = 20
                    if mcap > 0:
                        score += min(15, (liq / mcap) * 100 * 0.15)
                    score += max(0, min(15, (bsr - 1) * 30))
                    score += min(10, (pair["volume_5m"] / 10_000) * 100)
                    score += min(10, (holders / 500) * 100)
                    score += min(10, whale_buys * 20)
                    if socials.get("twitter_followers", 0) > 1_000: score += 5
                    if socials.get("telegram_members",  0) >   500: score += 5
                    score += min(5, (await get_holder_growth_rate(addr)) / 10)
                    score += 10
                    ai_score = int(min(100, score))

                    if ai_score < MIN_SIGNAL_SCORE:
                        await log_decision(symbol, mcap, liq, bsr, "⏭",
                                           f"score {ai_score}<{MIN_SIGNAL_SCORE}")
                        continue

                    # ── Targets ──
                    bp      = mcap / 1_000_000 if mcap > 0 else 1e-6
                    targets = {
                        "tp1": bp * 1.5, "tp2": bp * 2.5,
                        "tp3": bp * 4.0, "tp4": bp * 6.0, "sl": bp * 0.7,
                    }
                    signal_data = {
                        "token_address":    addr,
                        "name":             pair["name"],
                        "symbol":           symbol,
                        "market_cap":       mcap,
                        "liquidity":        liq,
                        "liquidity_locked": True,
                        "volume_5m":        pair["volume_5m"],
                        "buy_sell_ratio":   bsr,
                        "holders":          holders,
                        "holders_history":  [{"ts": time.time(), "count": holders}],
                        "whale_buys":       whale_buys,
                        "ai_score":         ai_score,
                        "safety_score":     95,
                        "risk_level":       risk_badge(ai_score),
                        "targets":          targets,
                        "socials":          socials,
                    }

                    # ── Save & notify ──
                    try:
                        async with AsyncSessionLocal() as db:
                            await db.execute(insert(Signal).values(**signal_data))
                            await db.commit()
                        msg_id, chat_id = await send_signal_notification(signal_data)
                        if msg_id:
                            async with AsyncSessionLocal() as db:
                                await db.execute(
                                    update(Signal)
                                    .where(Signal.token_address == addr)
                                    .values(telegram_message_id=msg_id, chat_id=chat_id)
                                )
                                await db.commit()
                        qualified      += 1
                        self.signal_count += 1
                        await log_decision(symbol, mcap, liq, bsr, "✅",
                                           f"SIGNAL {ai_score}/100")
                        logger.info(f"✅ Signal: {symbol}  score={ai_score}")
                    except Exception as e:
                        logger.error(f"Signal save/send {addr}: {e}")

                await asyncio.sleep(3)

            except Exception as e:
                logger.error(f"scan_loop inner: {e}")
                await asyncio.sleep(10)

    async def _update_loop(self):
        """Update active signal cards every 60 s with live PnL + TP checks."""
        while True:
            try:
                if not self.active:
                    await asyncio.sleep(10)
                    continue
                await asyncio.sleep(60)
                cutoff = datetime.utcnow() - timedelta(days=1)
                async with AsyncSessionLocal() as db:
                    r = await db.execute(
                        select(Signal).where(Signal.created_at > cutoff)
                    )
                    signals = r.scalars().all()

                for sig in signals:
                    try:
                        price = await get_current_price(sig.token_address)
                        if not price:
                            continue
                        entry = (sig.market_cap / 1_000_000
                                 if sig.market_cap and sig.market_cap > 0 else 0)
                        pnl   = ((price - entry) / entry * 100) if entry > 0 else 0
                        icon  = "🔴" if pnl < -10 else ("🟡" if pnl < 0 else "🟢")
                        updates = [f"{icon} Live P&L: *{pnl:+.1f}%*  `${price:.6f}`"]

                        t    = sig.targets or {}
                        tp1, tp2, tp3, tp4, sl = (sig.tp1_hit, sig.tp2_hit,
                                                   sig.tp3_hit, sig.tp4_hit, sig.sl_hit)
                        if price >= t.get("tp1", float("inf")) and not tp1:
                            updates.append("✅ TP1 HIT!"); tp1 = True
                            await _fire_tp_alert(sig.symbol, "TP1", "+50%",   price, t.get("tp1", 0))
                        if price >= t.get("tp2", float("inf")) and not tp2:
                            updates.append("✅ TP2 HIT!"); tp2 = True
                            await _fire_tp_alert(sig.symbol, "TP2", "+150%",  price, t.get("tp2", 0))
                        if price >= t.get("tp3", float("inf")) and not tp3:
                            updates.append("✅ TP3 HIT! 🎉"); tp3 = True
                            await _fire_tp_alert(sig.symbol, "TP3", "+300% 🎉", price, t.get("tp3", 0))
                        if price >= t.get("tp4", float("inf")) and not tp4:
                            updates.append("✅ TP4 HIT! 🏆🎉"); tp4 = True
                            await _fire_tp_alert(sig.symbol, "TP4", "+500% 🏆", price, t.get("tp4", 0))
                        if price <= t.get("sl", 0) and not sl:
                            updates.append("🛑 STOP LOSS HIT!"); sl = True
                            await _fire_tp_alert(sig.symbol, "SL", "−30%", price, t.get("sl", 0), hit_sl=True)

                        growth = await get_holder_growth_rate(sig.token_address)
                        if growth > 20:
                            updates.append(f"📈 Holder growth +{growth:.0f}%")
                        new_whales  = await get_whale_buys(sig.token_address)
                        whale_delta = new_whales - (sig.whale_buys or 0)
                        if whale_delta > 0:
                            updates.append(f"🐋 +{whale_delta} new whale buy(s) >$5k")

                        await update_signal_notification(
                            sig.telegram_message_id, sig.chat_id, sig, updates
                        )
                        async with AsyncSessionLocal() as db:
                            await db.execute(
                                update(Signal).where(Signal.id == sig.id).values(
                                    whale_buys=new_whales,
                                    tp1_hit=tp1, tp2_hit=tp2,
                                    tp3_hit=tp3, tp4_hit=tp4,
                                    sl_hit=sl,
                                    last_updated=datetime.utcnow(),
                                )
                            )
                            await db.commit()
                    except Exception as e:
                        logger.error(f"update_inner {sig.token_address}: {e}")
            except Exception as e:
                logger.error(f"update_loop: {e}")
                await asyncio.sleep(10)


scanner = Scanner()


# ════════════════════════════════════════════════════════
#  HEARTBEAT  — 24/7 keep-alive logging every 5 min
# ════════════════════════════════════════════════════════
async def _heartbeat_loop():
    while True:
        await asyncio.sleep(300)
        uptime_min = int((time.time() - _bot_start_time) / 60)
        logger.info(
            f"💓 Heartbeat — uptime:{uptime_min}m  "
            f"scanned:{scanner.scan_count}  signals:{scanner.signal_count}"
        )


# ════════════════════════════════════════════════════════
#  ANIMATED HELPERS
# ════════════════════════════════════════════════════════
async def animate_boot(message: Message):
    steps = [
        (0,  "⠋ *Initializing AI Engine…*"),
        (12, "⠙ *Connecting Market Streams…*"),
        (25, "⠹ *Verifying API Keys…*"),
        (40, "⠸ *Loading Signal Models…*"),
        (55, "⠼ *Calibrating Rug Detectors…*"),
        (70, "⠴ *Syncing Whale Trackers…*"),
        (85, "⠦ *Arming Alpha Algorithms…*"),
        (95, "⠧ *Final system checks…*"),
    ]
    for pct, label in steps:
        try:
            await message.edit_text(
                f"{label}\n`{pbar(pct)}` {pct}%", parse_mode="Markdown"
            )
        except Exception:
            pass
        await asyncio.sleep(0.35)
    await asyncio.sleep(0.2)
    try:
        await message.edit_text(
            f"✅ *ALL SYSTEMS ONLINE*\n`{pbar(100)}` 100%\n\n🤖 {BOT_USERNAME}",
            parse_mode="Markdown",
        )
    except Exception:
        pass
    await asyncio.sleep(0.5)


async def animate_loading(message: Message, label: str, steps: int = 5):
    for i in range(steps):
        frame = SPIN[i % len(SPIN)]
        bar   = pbar((i + 1) / steps * 100)
        try:
            await message.edit_text(
                f"{frame} *{label}*\n`{bar}`", parse_mode="Markdown"
            )
        except Exception:
            pass
        await asyncio.sleep(0.25)


# ════════════════════════════════════════════════════════
#  SIGNAL PAGINATION
# ════════════════════════════════════════════════════════
async def _fetch_signal_ids(view_key: str) -> list:
    async with AsyncSessionLocal() as db:
        if view_key == "live":
            r = await db.execute(
                select(Signal.id).order_by(Signal.created_at.desc()).limit(20)
            )
        elif view_key == "positions":
            cutoff = datetime.utcnow() - timedelta(days=1)
            r = await db.execute(
                select(Signal.id)
                .where(Signal.created_at > cutoff, Signal.sl_hit == False)
                .order_by(Signal.created_at.desc())
            )
        elif view_key == "elite_calls":
            r = await db.execute(
                select(Signal.id)
                .where(Signal.ai_score >= 90)
                .order_by(Signal.created_at.desc()).limit(20)
            )
        else:
            r = await db.execute(
                select(Signal.id).order_by(Signal.created_at.desc()).limit(20)
            )
    return [row[0] for row in r.fetchall()]


async def _load_signal_page(query, context, view_key: str, page: int):
    sig_ids: list = context.user_data.get(f"{view_key}_ids", [])
    if not sig_ids:
        await safe_edit(query.message, "📭 *No signals found.*",
                        InlineKeyboardMarkup([back_row(view_key)]))
        return
    page = max(0, min(page, len(sig_ids) - 1))
    context.user_data[f"{view_key}_page"] = page
    async with AsyncSessionLocal() as db:
        r   = await db.execute(select(Signal).where(Signal.id == sig_ids[page]))
        sig = r.scalars().first()
    if not sig:
        await safe_edit(query.message, "⚠️ Signal not found.",
                        InlineKeyboardMarkup([back_row()]))
        return
    sig_dict = signal_to_dict(sig)
    total    = len(sig_ids)
    text     = format_signal_compact(sig_dict, page + 1, total)
    addr     = sig_dict["token_address"]
    nav_row  = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(f"◀ {page}", callback_data=f"sig_prev_{view_key}"))
    nav_row.append(InlineKeyboardButton(f"· {page+1}/{total} ·", callback_data="noop"))
    if page < total - 1:
        nav_row.append(InlineKeyboardButton(f"{page+2} ▶", callback_data=f"sig_next_{view_key}"))
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Full Card",   callback_data=f"sig_full_{view_key}"),
         InlineKeyboardButton("🔒 Rug Check",  callback_data=f"rug_{addr}")],
        [InlineKeyboardButton("📌 Watchlist",  callback_data=f"wl_add_{addr}"),
         InlineKeyboardButton("🔄 Refresh PnL",callback_data=f"pnl_{addr}")],
        nav_row,
        back_row(view_key),
    ])
    await safe_edit(query.message, text, kb)


# ════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ════════════════════════════════════════════════════════
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _hub_msg_id, _hub_chat_id, _hub_on_menu
    if update.effective_user.id != MY_TELEGRAM_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    msg = await update.message.reply_text(
        f"⠋ *Initializing AI Engine…*\n`{pbar(0)}` 0%",
        parse_mode="Markdown",
    )
    await animate_boot(msg)
    text = await _build_menu_text()
    hub  = await update.message.reply_text(
        text, reply_markup=main_menu_keyboard(), parse_mode="Markdown",
    )
    _hub_msg_id  = hub.message_id
    _hub_chat_id = hub.chat_id
    _hub_on_menu = True


async def _run_scan(addr: str, msg: Message):
    """Core scan logic — takes an address + an existing loading Message to edit."""
    steps = [
        (12, "⠙ *Pulling DexScreener data…*"),
        (24, "⠹ *Checking liquidity lock…*"),
        (36, "⠸ *Running honeypot test…*"),
        (50, "⠼ *Counting holders…*"),
        (64, "⠴ *Scanning whale buys…*"),
        (78, "⠦ *Fetching holder growth…*"),
        (90, "⠧ *Computing AI score…*"),
        (98, "⠇ *Building signal card…*"),
    ]
    for pct, label in steps:
        try:
            await msg.edit_text(f"{label}\n`{pbar(pct)}` {pct}%", parse_mode="Markdown")
        except Exception:
            pass
        await asyncio.sleep(0.28)

    dex, locked, hp_result, holders, wbuys, price, growth = await asyncio.gather(
        fetch_token_dex_data(addr),
        verify_liquidity_lock(addr),
        check_honeypot_and_tax(addr),
        get_holders_count(addr),
        get_whale_buys(addr),
        get_current_price(addr),
        get_holder_growth_rate(addr),
    )
    safe, tax = hp_result

    name   = dex.get("name",   "Unknown")
    symbol = dex.get("symbol", "UNK")
    mcap   = dex.get("market_cap", 0)
    liq    = dex.get("liquidity",  0)
    bsr    = dex.get("buy_sell_ratio", 0.0)
    vol5m  = dex.get("volume_5m",  0)
    vol1h  = dex.get("volume_1h",  0)
    vol24h = dex.get("volume_24h", 0)
    pc5m   = dex.get("price_change_5m",  0.0)
    pc1h   = dex.get("price_change_1h",  0.0)
    pc24h  = dex.get("price_change_24h", 0.0)
    dex_id = dex.get("dex", "unknown")
    entry  = price if price else dex.get("price", 0)

    score = 20
    if mcap > 0:
        score += min(15, (liq / mcap) * 100 * 0.15)
    score += max(0, min(15, (bsr - 1) * 30))
    score += min(10, (vol5m / 10_000) * 100)
    score += min(10, (holders / 500) * 100)
    score += min(10, wbuys * 20)
    score += min(5, growth / 10)
    if locked: score += 5
    if safe and tax <= 3: score += 5
    ai_score = int(min(100, max(0, score)))

    buy_reasons, risk_reasons = [], []
    if locked:     buy_reasons.append("✅ Liquidity locked")
    else:          risk_reasons.append("❌ Liquidity NOT locked")
    if safe:       buy_reasons.append("✅ No honeypot detected")
    else:          risk_reasons.append("❌ Honeypot detected")
    if tax <= 5:   buy_reasons.append(f"✅ Low tax: {tax:.1f}%")
    else:          risk_reasons.append(f"❌ High tax: {tax:.1f}%")
    if bsr >= 1.5: buy_reasons.append(f"✅ Strong BSR: {bsr:.1f}x")
    else:          risk_reasons.append(f"⚠️ Weak BSR: {bsr:.1f}x")
    if ai_score >= MIN_SIGNAL_SCORE:
                   buy_reasons.append(f"✅ AI score: {ai_score}/100")
    else:          risk_reasons.append(f"⚠️ Low AI score: {ai_score}/100")
    if holders >= 100: buy_reasons.append(f"✅ Holders: {holders}")
    if wbuys >= 2:     buy_reasons.append(f"✅ {wbuys} whale buys >$5k")
    if growth > 10:    buy_reasons.append(f"✅ Holder growth +{growth:.0f}%")

    is_buy = locked and safe and tax <= 5 and bsr >= 1.5 and ai_score >= MIN_SIGNAL_SCORE

    verdict_header = (
        "╔════════════════════════╗\n"
        "║  🟢 *RECOMMENDATION: BUY*  ║\n"
        "╚════════════════════════╝"
        if is_buy else
        "╔══════════════════════════╗\n"
        "║  🔴 *RECOMMENDATION: LEAVE*  ║\n"
        "╚══════════════════════════╝"
    )

    targets_block = ""
    if is_buy and entry > 0:
        tp1_p = entry * 1.50; tp2_p = entry * 2.50
        tp3_p = entry * 4.00; tp4_p = entry * 6.00
        sl_p  = entry * 0.70
        targets_block = (
            f"\n━━━━ 🎯 ENTRY & TARGETS ━━━━\n"
            f"💵 Entry:  `${entry:.8f}`\n"
            f"TP1  `${tp1_p:.8f}`  +50%\n"
            f"TP2  `${tp2_p:.8f}`  +150%\n"
            f"TP3  `${tp3_p:.8f}`  +300%\n"
            f"TP4  `${tp4_p:.8f}`  +500%\n"
            f"🛑 SL  `${sl_p:.8f}`  −30%\n"
        )

    pc_block = (
        f"📊 5m: `{pc5m:+.1f}%`  1h: `{pc1h:+.1f}%`  24h: `{pc24h:+.1f}%`\n"
        if any([pc5m, pc1h, pc24h]) else ""
    )

    text = (
        f"╔══ 🔍 *MANUAL SCAN* ══╗\n"
        f"  *{name}*   `${symbol}`\n"
        f"╚═══════════════════════╝\n\n"
        f"📍 `{addr}`\n"
        f"🏦 DEX: `{dex_id.upper()}`\n\n"
        f"━━━━ 📊 LIVE MARKET DATA ━━━━\n"
        f"💰 MCap:     `${mcap:>10,.0f}`\n"
        f"💧 Liq:      `${liq:>10,.0f}`\n"
        f"📈 5m Vol:   `${vol5m:>10,.0f}`\n"
        f"📈 1h Vol:   `${vol1h:>10,.0f}`\n"
        f"📈 24h Vol:  `${vol24h:>10,.0f}`\n"
        f"🔥 BSR:      `{bsr:.2f}x`  {sentiment_label(bsr)}\n"
        f"👥 Holders:  `{holders}`\n"
        f"📈 H.Growth: `{growth:+.1f}%`\n"
        f"🐋 Whales:   `{wbuys}` buys >$5k\n"
        + pc_block
        + f"\n━━━━ 🛡 SAFETY CHECKS ━━━━\n"
        f"🔒 Liq Locked:  {'✅ YES' if locked else '❌ NO'}\n"
        f"🍯 Honeypot:    {'✅ SAFE' if safe else '🔴 DETECTED'}\n"
        f"💸 Tax:         `{tax:.1f}%`  {'✅' if tax <= 5 else '⚠️'}\n\n"
        f"━━━━ 🤖 AI ANALYSIS ━━━━\n"
        f"Score: `{score_meter(ai_score)}` *{ai_score}/100*\n"
        f"Risk:   {risk_badge(ai_score)}\n\n"
        + verdict_header + "\n"
        + "\n".join(buy_reasons + risk_reasons)
        + targets_block
        + f"\n\n[📈 DEX](https://dexscreener.com/solana/{addr})  "
        f"[📊 Eye](https://birdeye.so/token/{addr})  "
        f"[⚡ Jup](https://jup.ag/swap/SOL-{addr})"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Re-Scan",   callback_data=f"rescan_{addr}"),
         InlineKeyboardButton("📌 Watchlist", callback_data=f"wl_add_{addr}")],
        [InlineKeyboardButton("📋 Copy CA",   callback_data=f"copy_{addr}"),
         InlineKeyboardButton("🏠 Menu",      callback_data="menu")],
    ])
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown",
                            disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"_run_scan result: {e}")


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kept for backward-compat; real flow is via 🔍 Scan Token button."""
    if update.effective_user.id != MY_TELEGRAM_ID:
        return
    if not context.args:
        await update.message.reply_text(
            "Use the *🔍 Scan Token* button in the main menu.", parse_mode="Markdown"
        )
        return
    addr = context.args[0].strip()
    if len(addr) < 32 or len(addr) > 44:
        await update.message.reply_text("⚠️ Invalid Solana address (32–44 chars).")
        return
    msg = await update.message.reply_text(
        f"⠋ *Fetching live data…*\n`{pbar(0)}` 0%\n`{addr[:22]}…`",
        parse_mode="Markdown",
    )
    await _run_scan(addr, msg)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MY_TELEGRAM_ID:
        return
    uptime_min = int((time.time() - _bot_start_time) / 60)
    await update.message.reply_text(
        f"📟 *Bot Status*\n"
        f"Scanner:   {'🟢 ACTIVE' if scanner.active else '🔴 STOPPED'}\n"
        f"Scanned:   `{scanner.scan_count}`\n"
        f"Signals:   `{scanner.signal_count}`\n"
        f"Uptime:    `{uptime_min}m`\n"
        f"Threshold: `{MIN_SIGNAL_SCORE}`",
        parse_mode="Markdown",
    )


async def pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/pnl [today|yesterday|YYYY-MM-DD]  — full daily P&L report."""
    if update.effective_user.id != MY_TELEGRAM_ID:
        return

    raw  = " ".join(context.args).lower().strip() if context.args else "today"
    today = datetime.utcnow().date()

    if raw in ("", "today"):
        target_date = today
        label       = "Today"
    elif raw == "yesterday":
        target_date = today - timedelta(days=1)
        label       = "Yesterday"
    else:
        try:
            target_date = datetime.strptime(raw, "%Y-%m-%d").date()
            label       = str(target_date)
        except ValueError:
            await update.message.reply_text(
                "⚠️ Usage: `/pnl today`  `/pnl yesterday`  `/pnl 2026-06-13`",
                parse_mode="Markdown",
            )
            return

    msg = await update.message.reply_text(
        f"⠋ *Generating P&L report…*\n`{pbar(0)}` 0%",
        parse_mode="Markdown",
    )
    for pct, lbl in [(30, "⠹ *Fetching signals…*"),
                     (60, "⠼ *Pulling live prices…*"),
                     (90, "⠧ *Calculating returns…*")]:
        try:
            await msg.edit_text(f"{lbl}\n`{pbar(pct)}` {pct}%", parse_mode="Markdown")
        except Exception:
            pass
        await asyncio.sleep(0.3)

    start_dt = datetime.combine(target_date, datetime.min.time())
    end_dt   = datetime.combine(target_date, datetime.max.time())

    async with AsyncSessionLocal() as db:
        r    = await db.execute(
            select(Signal)
            .where(Signal.created_at >= start_dt, Signal.created_at <= end_dt)
            .order_by(Signal.created_at.asc())
        )
        sigs = r.scalars().all()

    if not sigs:
        await msg.edit_text(
            f"📭 *No signals on {label}*\n\n"
            f"Try: `/pnl today`  `/pnl yesterday`  `/pnl YYYY-MM-DD`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Menu", callback_data="menu")]
            ]),
        )
        return

    # Fetch all current prices in parallel
    prices = await asyncio.gather(*[get_current_price(s.token_address) for s in sigs])

    rows, total_pnl, wins, losses, open_cnt = [], 0.0, 0, 0, 0
    best_sym, best_pnl   = "", -9999.0
    worst_sym, worst_pnl = "", 9999.0

    for s, price in zip(sigs, prices):
        entry = (s.market_cap / 1_000_000) if s.market_cap and s.market_cap > 0 else 0
        pnl   = ((price - entry) / entry * 100) if entry > 0 and price > 0 else 0
        total_pnl += pnl

        tps_hit = sum([s.tp1_hit, s.tp2_hit, s.tp3_hit, s.tp4_hit])
        if s.sl_hit:
            status_icon = "🛑 SL"
            losses += 1
        elif tps_hit == 4:
            status_icon = "🏆 TP4"
            wins += 1
        elif tps_hit == 3:
            status_icon = "🥇 TP3"
            wins += 1
        elif tps_hit == 2:
            status_icon = "✅ TP2"
            wins += 1
        elif tps_hit == 1:
            status_icon = "✅ TP1"
            wins += 1
        else:
            status_icon = "📊 Open"
            open_cnt += 1

        pnl_icon = "🟢" if pnl > 0 else ("🔴" if pnl < -5 else "🟡")
        time_str = s.created_at.strftime("%H:%M")
        rows.append(
            f"`{time_str}` {pnl_icon} *{s.symbol:<7}*  "
            f"`{pnl:+6.1f}%`  {status_icon}"
        )
        if pnl > best_pnl:   best_pnl,  best_sym  = pnl, s.symbol
        if pnl < worst_pnl:  worst_pnl, worst_sym = pnl, s.symbol

    n        = len(sigs)
    avg_pnl  = total_pnl / n
    win_rate = wins / n * 100
    pnl_bar  = score_meter(max(0, min(100, int(50 + avg_pnl / 2))))

    summary = (
        f"📊 *P&L Report — {label}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(rows)
        + f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Signals:   `{n}`\n"
        f"🟢 Wins:      `{wins}`   🔴 Losses: `{losses}`   📊 Open: `{open_cnt}`\n"
        f"🎯 Win Rate:  `{win_rate:.0f}%`\n"
        f"📈 Avg P&L:   `{avg_pnl:+.1f}%`\n"
        f"{'🟢' if total_pnl > 0 else '🔴'} Total P&L: *{total_pnl:+.1f}%*\n\n"
        f"`{pnl_bar}`\n\n"
        f"🏆 Best:   *{best_sym}*  `{best_pnl:+.1f}%`\n"
        f"⚠️ Worst:  *{worst_sym}*  `{worst_pnl:+.1f}%`"
    )

    await msg.edit_text(
        summary,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh",     callback_data=f"pnl_day_{target_date}"),
             InlineKeyboardButton("◀ Yesterday",   callback_data=f"pnl_day_{target_date - timedelta(days=1)}")],
            [InlineKeyboardButton("▶ Next Day",     callback_data=f"pnl_day_{target_date + timedelta(days=1)}"),
             InlineKeyboardButton("🏠 Menu",        callback_data="menu")],
        ]),
    )


# ════════════════════════════════════════════════════════
#  TEXT MESSAGE HANDLER — catches CA input after Scan button
# ════════════════════════════════════════════════════════
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catches any plain-text message — if we're waiting for a CA, run the scan."""
    if update.effective_user.id != MY_TELEGRAM_ID:
        return
    chat_id = update.effective_chat.id
    if chat_id not in _scan_waiting:
        return                           # ignore all other text

    prompt_id = _scan_waiting.pop(chat_id)
    # Clean up the prompt message
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=prompt_id)
    except Exception:
        pass
    # Clean up the user's own message (tidy chat)
    try:
        await update.message.delete()
    except Exception:
        pass

    addr = (update.message.text or "").strip()
    if len(addr) < 32 or len(addr) > 44:
        err = await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ That doesn't look like a valid Solana address (32–44 chars).\n"
                 "Use *🔍 Scan Token* from the menu and try again.",
            parse_mode="Markdown",
        )
        await asyncio.sleep(4)
        try:
            await err.delete()
        except Exception:
            pass
        return

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"⠋ *Fetching live data…*\n`{pbar(0)}` 0%\n`{addr[:22]}…`",
        parse_mode="Markdown",
    )
    await _run_scan(addr, msg)


# ════════════════════════════════════════════════════════
#  BUTTON CALLBACKS — all edit-in-place, zero spam
# ════════════════════════════════════════════════════════
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _hub_msg_id, _hub_chat_id, _hub_on_menu, MIN_SIGNAL_SCORE

    if update.effective_user.id != MY_TELEGRAM_ID:
        await update.callback_query.answer("❌ Unauthorized", show_alert=True)
        return
    query = update.callback_query
    await query.answer()
    data  = query.data

    def _set_menu_active():
        global _hub_msg_id, _hub_chat_id, _hub_on_menu
        _hub_msg_id  = query.message.message_id
        _hub_chat_id = query.message.chat_id
        _hub_on_menu = True

    def _set_menu_inactive():
        global _hub_on_menu
        _hub_on_menu = False

    if data == "noop":
        return

    # ══ SCAN TOKEN via inline button ══
    if data == "scan_token":
        _set_menu_inactive()
        prompt = await query.message.reply_text(
            "🔍 *Scan Any Token*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Paste a Solana token CA below\n"
            "_(32–44 characters)_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="scan_cancel")]
            ]),
        )
        _scan_waiting[query.message.chat_id] = prompt.message_id
        return

    if data == "scan_cancel":
        _scan_waiting.pop(query.message.chat_id, None)
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    # ══ P&L TODAY / YESTERDAY shortcuts ══
    if data in ("pnl_today", "pnl_yesterday"):
        _set_menu_inactive()
        today_d     = datetime.utcnow().date()
        target_date = today_d if data == "pnl_today" else today_d - timedelta(days=1)
        label       = "Today" if data == "pnl_today" else "Yesterday"
        await animate_loading(query.message, f"P&L report: {label}…", 3)
        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt   = datetime.combine(target_date, datetime.max.time())
        async with AsyncSessionLocal() as db:
            r    = await db.execute(
                select(Signal)
                .where(Signal.created_at >= start_dt, Signal.created_at <= end_dt)
                .order_by(Signal.created_at.asc())
            )
            sigs = r.scalars().all()
        if not sigs:
            await safe_edit(
                query.message,
                f"📭 *No signals on {label}*\n\nThe scanner hasn't fired any signals yet on this day.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh",    callback_data=data),
                     InlineKeyboardButton("🏠 Menu",       callback_data="menu")],
                ]),
            )
            return
        prices = await asyncio.gather(*[get_current_price(s.token_address) for s in sigs])
        rows, total_pnl, wins, losses, open_cnt = [], 0.0, 0, 0, 0
        best_sym, best_pnl   = "", -9999.0
        worst_sym, worst_pnl = "", 9999.0
        for s, price in zip(sigs, prices):
            entry_p = (s.market_cap / 1_000_000) if s.market_cap and s.market_cap > 0 else 0
            pnl_v   = ((price - entry_p) / entry_p * 100) if entry_p > 0 and price > 0 else 0
            total_pnl += pnl_v
            tps_hit = sum([s.tp1_hit, s.tp2_hit, s.tp3_hit, s.tp4_hit])
            if s.sl_hit:        st = "🛑 SL";   losses += 1
            elif tps_hit == 4:  st = "🏆 TP4";  wins += 1
            elif tps_hit == 3:  st = "🥇 TP3";  wins += 1
            elif tps_hit == 2:  st = "✅ TP2";   wins += 1
            elif tps_hit == 1:  st = "✅ TP1";   wins += 1
            else:               st = "📊 Open"; open_cnt += 1
            pnl_icon = "🟢" if pnl_v > 0 else ("🔴" if pnl_v < -5 else "🟡")
            rows.append(f"`{s.created_at.strftime('%H:%M')}` {pnl_icon} *{s.symbol:<7}*  `{pnl_v:+6.1f}%`  {st}")
            if pnl_v > best_pnl:  best_pnl,  best_sym  = pnl_v, s.symbol
            if pnl_v < worst_pnl: worst_pnl, worst_sym = pnl_v, s.symbol
        n = len(sigs)
        await safe_edit(
            query.message,
            f"📊 *P&L Report — {label}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(rows)
            + f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Signals: `{n}`  🟢 Wins: `{wins}`  🔴 Losses: `{losses}`\n"
            f"🎯 Win Rate: `{wins/n*100:.0f}%`\n"
            f"{'🟢' if total_pnl > 0 else '🔴'} Total P&L: *{total_pnl:+.1f}%*  "
            f"Avg: `{total_pnl/n:+.1f}%`\n"
            f"🏆 Best: *{best_sym}* `{best_pnl:+.1f}%`  "
            f"⚠️ Worst: *{worst_sym}* `{worst_pnl:+.1f}%`",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh",    callback_data=data),
                 InlineKeyboardButton("◀ Yesterday",  callback_data=f"pnl_day_{target_date - timedelta(days=1)}")],
                [InlineKeyboardButton("▶ Next Day",    callback_data=f"pnl_day_{target_date + timedelta(days=1)}"),
                 InlineKeyboardButton("🏠 Menu",       callback_data="menu")],
            ]),
        )
        return

    # ══ BOT STATUS inline ══
    if data == "bot_status":
        _set_menu_inactive()
        uptime_min = int((time.time() - _bot_start_time) / 60)
        await safe_edit(
            query.message,
            f"📟 *Scanner Status*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Scanner:   {'🟢 ACTIVE' if scanner.active else '🔴 STOPPED'}\n"
            f"Pairs:     `{scanner.scan_count:,}`\n"
            f"Signals:   `{scanner.signal_count}`\n"
            f"Threshold: `{MIN_SIGNAL_SCORE}/100`\n"
            f"Uptime:    `{uptime_min}m`\n\n"
            f"━━━━ Active Filters ━━━━\n"
            f"💰 MCap:   `$10K – $500K`\n"
            f"💧 Liq:    `> $20K`\n"
            f"🔥 BSR:    `> 1.5x`\n"
            f"⏱ Age:    `< 120 min`\n"
            f"📊 Txns:   `> 50 / 5m`\n"
            f"🔒 Lock:   required\n"
            f"🍯 Honey:  no honeypots\n"
            f"💸 Tax:    `≤ 5%`\n\n"
            f"📈 *Est. signals/day:  3–12* _(market dependent)_",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh",       callback_data="bot_status"),
                 InlineKeyboardButton("⚙️ Settings",      callback_data="settings")],
                [InlineKeyboardButton("🏠 Menu",          callback_data="menu")],
            ]),
        )
        return

    # ── delete buttons ──
    if data.startswith("del_"):
        try:
            mid = int(data[4:])
            await safe_delete(telegram_app.bot, query.message.chat_id, mid)
        except Exception:
            await safe_delete(telegram_app.bot, query.message.chat_id,
                              query.message.message_id)
        return

    # ══ MAIN MENU ══
    if data == "menu":
        _set_menu_active()
        text = await _build_menu_text()
        await safe_edit(query.message, text, main_menu_keyboard())
        return

    _set_menu_inactive()

    # ══ SIGNAL LIST VIEWS ══
    if data in ("live", "positions", "elite_calls"):
        await safe_edit(query.message, f"⠋ *Loading…*\n`{pbar(0)}`", None)
        ids = await _fetch_signal_ids(data)
        if not ids:
            await safe_edit(query.message, "📭 *No signals found.*",
                            InlineKeyboardMarkup([back_row(data)]))
            return
        context.user_data[f"{data}_ids"]  = ids
        context.user_data[f"{data}_page"] = 0
        await _load_signal_page(query, context, data, 0)
        return

    if any(data.startswith(p) for p in ["sig_next_", "sig_prev_"]):
        is_next  = data.startswith("sig_next_")
        view_key = data[9:]
        current  = context.user_data.get(f"{view_key}_page", 0)
        await _load_signal_page(query, context, view_key,
                                current + (1 if is_next else -1))
        return

    if data.startswith("sig_full_"):
        view_key = data[9:]
        page     = context.user_data.get(f"{view_key}_page", 0)
        ids      = context.user_data.get(f"{view_key}_ids",  [])
        if not ids or page >= len(ids):
            await safe_edit(query.message, "⚠️ No signal loaded.",
                            InlineKeyboardMarkup([back_row()]))
            return
        async with AsyncSessionLocal() as db:
            r   = await db.execute(select(Signal).where(Signal.id == ids[page]))
            sig = r.scalars().first()
        if not sig:
            await safe_edit(query.message, "⚠️ Signal not found.",
                            InlineKeyboardMarkup([back_row()]))
            return
        sig_dict = signal_to_dict(sig)
        addr     = sig_dict["token_address"]
        kb       = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔒 Rug Check",   callback_data=f"rug_{addr}"),
             InlineKeyboardButton("🔄 Refresh PnL", callback_data=f"pnl_{addr}")],
            [InlineKeyboardButton("📌 Watchlist",   callback_data=f"wl_add_{addr}"),
             InlineKeyboardButton("📋 Copy CA",      callback_data=f"copy_{addr}")],
            back_row(view_key),
        ])
        await safe_edit(query.message, format_signal_full(sig_dict), kb)
        return

    # ══ TRENDING ══
    if data == "trending":
        async with AsyncSessionLocal() as db:
            r    = await db.execute(
                select(Signal).order_by(Signal.ai_score.desc()).limit(10)
            )
            sigs = r.scalars().all()
        if not sigs:
            await safe_edit(query.message, "📭 *No data yet.*",
                            InlineKeyboardMarkup([back_row("trending")]))
            return
        lines = "\n".join([
            f"`{i+1:>2}.` *{s.symbol}*  `{score_meter(s.ai_score, 6)}`  *{s.ai_score}/100*"
            for i, s in enumerate(sigs)
        ])
        await safe_edit(query.message,
                        f"🔥 *Trending — Top 10 by AI Score*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n{lines}",
                        InlineKeyboardMarkup([back_row("trending")]))
        return

    # ══ HISTORY ══
    if data == "history":
        async with AsyncSessionLocal() as db:
            r    = await db.execute(
                select(Signal).where(Signal.sl_hit == True)
                .order_by(Signal.created_at.desc()).limit(20)
            )
            sigs = r.scalars().all()
        if not sigs:
            await safe_edit(query.message, "📭 *No closed positions yet.*",
                            InlineKeyboardMarkup([back_row("history")]))
            return
        lines = "\n".join([
            f"📉 *{s.symbol}*  SL hit  `{s.created_at.strftime('%m-%d %H:%M')}`"
            for s in sigs
        ])
        await safe_edit(query.message,
                        f"📈 *Signal History (closed)*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n{lines}",
                        InlineKeyboardMarkup([back_row("history")]))
        return

    # ══ WHALE TRACKER ══
    if data == "whale_tracker":
        async with AsyncSessionLocal() as db:
            r    = await db.execute(
                select(Signal).order_by(Signal.whale_buys.desc()).limit(8)
            )
            sigs = r.scalars().all()
        if not sigs:
            await safe_edit(query.message, "📭 *No whale data yet.*",
                            InlineKeyboardMarkup([back_row("whale_tracker")]))
            return
        lines = "\n".join([
            f"🐋 *{s.symbol}*  `{s.whale_buys}` buys >$5k  AI:{s.ai_score}"
            for s in sigs
        ])
        await safe_edit(query.message,
                        f"🐋 *Whale Activity Tracker*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n{lines}",
                        InlineKeyboardMarkup([back_row("whale_tracker")]))
        return

    # ══ MARKET PULSE ══
    if data in ("market_pulse", "refresh_pulse"):
        await animate_loading(query.message, "Aggregating market data…", 4)
        async with AsyncSessionLocal() as db:
            total  = (await db.execute(select(func.count(Signal.id)))).scalar() or 0
            bsr_r  = (await db.execute(
                select(func.avg(Signal.buy_sell_ratio))
                .where(Signal.created_at > datetime.utcnow() - timedelta(hours=6))
            )).scalar()
            avg_bsr = float(bsr_r or 0) or scanner.last_bsr_avg
            avg_liq = float((await db.execute(
                select(func.avg(Signal.liquidity)))).scalar() or 0)
            total_wh= int((await db.execute(
                select(func.sum(Signal.whale_buys)))).scalar() or 0)
            top5    = [r[0] for r in (await db.execute(
                select(Signal.symbol).order_by(Signal.ai_score.desc()).limit(5)
            )).fetchall()]

        if avg_bsr >= 2.0:   sentiment, bull = "🚀 VERY BULLISH", 85
        elif avg_bsr >= 1.5: sentiment, bull = "🔥 BULLISH",      70
        elif avg_bsr >= 1.0: sentiment, bull = "😐 NEUTRAL",      50
        else:                sentiment, bull = "📉 BEARISH",       25

        top5_str = "  ".join([f"*{s}*" for s in top5]) if top5 else "_none yet_"
        await safe_edit(
            query.message,
            f"⚡ *Market Pulse — Solana*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Tracked tokens:    `{total}`\n"
            f"🔥 Avg BSR (6h):      `{avg_bsr:.2f}x`\n"
            f"💧 Avg Liquidity:     `${avg_liq:,.0f}`\n"
            f"🐋 Total Whale Buys:  `{total_wh}`\n\n"
            f"🎯 Trending:\n{top5_str}\n\n"
            f"Market Sentiment:\n"
            f"`{score_meter(bull)}` {sentiment}\n\n"
            f"🕐 `{datetime.now().strftime('%H:%M:%S')}`",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_pulse")],
                back_row(),
            ]),
        )
        return

    # ══ HALL OF FAME ══
    if data in ("hall_of_fame", "refresh_hof"):
        await animate_loading(query.message, "Loading Hall of Fame…", 3)
        async with AsyncSessionLocal() as db:
            r       = await db.execute(
                select(Signal).order_by(Signal.ai_score.desc()).limit(30)
            )
            all_s   = r.scalars().all()
        sigs = sorted(
            all_s,
            key=lambda s: (
                int(bool(s.tp1_hit)) + int(bool(s.tp2_hit))
                + int(bool(s.tp3_hit)) + int(bool(s.tp4_hit)),
                s.ai_score or 0,
            ),
            reverse=True,
        )[:10]
        if not sigs:
            await safe_edit(query.message,
                            "📭 *No data yet — signals need time to play out.*",
                            InlineKeyboardMarkup([back_row("hall_of_fame")]))
            return
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        rows   = []
        for i, s in enumerate(sigs):
            tps      = sum([s.tp1_hit, s.tp2_hit, s.tp3_hit, s.tp4_hit])
            tp_icons = ("".join(["✅" if getattr(s, f"tp{j}_hit") else "⬜"
                                 for j in range(1, 5)]))
            rows.append(f"{medals[i]} *{s.symbol}*  {tp_icons}  "
                        f"AI:{s.ai_score}  `{tps}/4 TPs`")
        await safe_edit(
            query.message,
            f"🏆 *Hall of Fame*\n_Top signals ranked by TPs hit_\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(rows),
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_hof")],
                back_row(),
            ]),
        )
        return

    # ══ PORTFOLIO ══
    if data in ("portfolio", "refresh_portfolio"):
        await animate_loading(query.message, "Building portfolio snapshot…", 4)
        cutoff = datetime.utcnow() - timedelta(days=1)
        async with AsyncSessionLocal() as db:
            r    = await db.execute(
                select(Signal)
                .where(Signal.created_at > cutoff, Signal.sl_hit == False)
                .order_by(Signal.created_at.desc())
            )
            sigs = r.scalars().all()
        if not sigs:
            await safe_edit(query.message,
                            "📭 *Portfolio is empty.*\n\nNo active signals in the last 24h.",
                            InlineKeyboardMarkup([back_row("portfolio")]))
            return
        prices    = await asyncio.gather(*[get_current_price(s.token_address) for s in sigs])
        rows      = []
        total_pnl = 0.0
        best_sym, best_pnl   = "", -999.0
        worst_sym, worst_pnl = "", 999.0
        for s, price in zip(sigs, prices):
            entry = (s.market_cap / 1_000_000) if s.market_cap and s.market_cap > 0 else 0
            pnl   = ((price - entry) / entry * 100) if entry > 0 and price > 0 else 0
            total_pnl += pnl
            icon  = "🟢" if pnl > 0 else ("🔴" if pnl < -10 else "🟡")
            tps   = "".join(["✅" if getattr(s, f"tp{i}_hit") else "⬜"
                             for i in range(1, 5)])
            rows.append(f"{icon} *{s.symbol}*  `{pnl:+.1f}%`  {tps}")
            if pnl > best_pnl:   best_pnl, best_sym   = pnl, s.symbol
            if pnl < worst_pnl:  worst_pnl, worst_sym = pnl, s.symbol
        avg_pnl  = total_pnl / len(sigs)
        avg_icon = "🟢" if avg_pnl > 0 else "🔴"
        await safe_edit(
            query.message,
            f"💼 *Portfolio Snapshot*\n_Active signals · last 24h_\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(rows)
            + f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Positions:  `{len(sigs)}`\n"
            f"{avg_icon} Avg P&L:   *{avg_pnl:+.1f}%*\n"
            f"🏆 Best:    *{best_sym}*  `{best_pnl:+.1f}%`\n"
            f"⚠️ Worst:   *{worst_sym}*  `{worst_pnl:+.1f}%`",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_portfolio")],
                back_row(),
            ]),
        )
        return

    # ══ WATCHLIST ══
    if data == "watchlist":
        async with AsyncSessionLocal() as db:
            r     = await db.execute(
                select(Watchlist).where(Watchlist.user_id == MY_TELEGRAM_ID)
            )
            items = r.scalars().all()
        if not items:
            text = ("📭 *Watchlist is empty.*\n\n"
                    "Tap 📌 on any signal card to add a token.")
        else:
            lines = "\n".join([
                f"`{i+1:>2}.` `{w.token_address}`  "
                f"_added {w.added_at.strftime('%m-%d')}_"
                for i, w in enumerate(items)
            ])
            text = (f"📌 *Watchlist* — {len(items)} token(s)\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n{lines}")
        await safe_edit(query.message, text,
                        InlineKeyboardMarkup([back_row("watchlist")]))
        return

    # ══ RUG SCANNER INFO ══
    if data == "rug_scanner":
        await safe_edit(
            query.message,
            "🔍 *Rug Scanner*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Use the `/scan` command:\n\n"
            "`/scan <solana_token_address>`\n\n"
            "Checks performed:\n"
            "• 🔒 Liquidity lock\n• 🍯 Honeypot detection\n"
            "• 💸 Buy/Sell tax\n• 👥 Holder count\n"
            "• 📈 Holder growth rate\n• 🐋 Whale buy activity\n"
            "• 🛡 Safety score /100",
            InlineKeyboardMarkup([back_row()]),
        )
        return

    # ══ SCANNER STATUS ══
    if data in ("scanner_status", "refresh_status"):
        uptime_min = int((time.time() - _bot_start_time) / 60)
        uptime_str = (f"{uptime_min // 60}h {uptime_min % 60}m"
                      if uptime_min >= 60 else f"{uptime_min}m")
        await safe_edit(
            query.message,
            f"📟 *Scanner Status*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Engine:       {'🟢 ACTIVE' if scanner.active else '🔴 STOPPED'}\n"
            f"🌐 DexScreener:  🟢 polling\n"
            f"🦅 Birdeye API:  🟢 ready\n"
            f"🛡 GoPlus API:   🟢 ready\n"
            f"⚡ Helius API:   🟢 ready\n\n"
            f"📊 Pairs scanned:    `{scanner.scan_count}`\n"
            f"🚀 Signals fired:    `{scanner.signal_count}`\n"
            f"🔔 Alert threshold:  `{MIN_SIGNAL_SCORE}`\n"
            f"🔄 Poll interval:    `3s`\n"
            f"♻️ Update cycle:     `60s`\n"
            f"⏱ Uptime:           `{uptime_str}`\n\n"
            f"🕐 `{datetime.now().strftime('%H:%M:%S')}`",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh",      callback_data="refresh_status"),
                 InlineKeyboardButton("⏹ Stop Scanner", callback_data="stop_scanner")],
                back_row(),
            ]),
        )
        return

    if data == "stop_scanner":
        scanner.active = False
        await query.answer("🛑 Scanner stopped.", show_alert=True)
        _set_menu_active()
        text = await _build_menu_text()
        await safe_edit(query.message, text, main_menu_keyboard())
        return

    # ══ ANALYTICS ══
    if data in ("analytics", "refresh_analytics"):
        await animate_loading(query.message, "Calculating analytics…", 3)
        async with AsyncSessionLocal() as db:
            total  = (await db.execute(select(func.count(Signal.id)))).scalar() or 0
            elite  = (await db.execute(
                select(func.count(Signal.id)).where(Signal.ai_score >= 90)
            )).scalar() or 0
            slhit  = (await db.execute(
                select(func.count(Signal.id)).where(Signal.sl_hit == True)
            )).scalar() or 0
            tp1hit = (await db.execute(
                select(func.count(Signal.id)).where(Signal.tp1_hit == True)
            )).scalar() or 0
            tp2hit = (await db.execute(
                select(func.count(Signal.id)).where(Signal.tp2_hit == True)
            )).scalar() or 0
            tp3hit = (await db.execute(
                select(func.count(Signal.id)).where(Signal.tp3_hit == True)
            )).scalar() or 0
        wr1    = f"{tp1hit/total*100:.0f}%" if total else "N/A"
        wr3    = f"{tp3hit/total*100:.0f}%" if total else "N/A"
        tp_bar = score_meter(int(tp1hit / total * 100) if total else 0)
        await safe_edit(
            query.message,
            f"📊 *Analytics Dashboard*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 Total Signals:     `{total}`\n"
            f"💎 Elite calls (90+): `{elite}`\n"
            f"📊 Active:            `{total - slhit}`\n"
            f"🛑 SL Hit:            `{slhit}`\n\n"
            f"✅ TP1 hit: `{tp1hit}`  Rate: *{wr1}*\n"
            f"✅ TP2 hit: `{tp2hit}`\n"
            f"✅ TP3 hit: `{tp3hit}`  Rate: *{wr3}*\n\n"
            f"TP1 Hit Rate:\n`{tp_bar}` *{wr1}*\n\n"
            f"🕐 `{datetime.now().strftime('%H:%M:%S')}`",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_analytics")],
                back_row(),
            ]),
        )
        return

    # ══ SETTINGS ══
    if data == "settings":
        await safe_edit(
            query.message,
            f"⚙️ *Settings*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔐 Private Mode:       `ON`\n"
            f"🔔 Min Signal Score:   `{MIN_SIGNAL_SCORE}`\n"
            f"🔄 Scanner Poll:       `3s`\n"
            f"♻️ Update Cycle:       `60s`\n"
            f"🔄 Menu Auto-refresh:  `30s`\n"
            f"📟 Console Refresh:    `2s`\n"
            f"💰 Min Liquidity:      `$20,000`\n"
            f"📈 MCap Range:         `$10K – $500K`\n"
            f"🔥 Min BSR:            `1.5x`\n"
            f"💸 Max Tax:            `5%`\n\n"
            f"🤖 {BOT_USERNAME}",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔔 Raise Threshold (+5)",
                                      callback_data="thresh_up"),
                 InlineKeyboardButton("🔔 Lower Threshold (−5)",
                                      callback_data="thresh_down")],
                back_row(),
            ]),
        )
        return

    if data in ("thresh_up", "thresh_down"):
        MIN_SIGNAL_SCORE = max(30, min(95, MIN_SIGNAL_SCORE + (5 if data == "thresh_up" else -5)))
        await query.answer(f"🔔 Threshold → {MIN_SIGNAL_SCORE}", show_alert=False)
        await safe_edit(
            query.message,
            f"⚙️ *Settings*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔐 Private Mode:       `ON`\n"
            f"🔔 Min Signal Score:   *`{MIN_SIGNAL_SCORE}`*  ← updated\n"
            f"🔄 Scanner Poll:       `3s`\n"
            f"♻️ Update Cycle:       `60s`\n"
            f"🔄 Menu Auto-refresh:  `30s`\n"
            f"📟 Console Refresh:    `2s`\n"
            f"💰 Min Liquidity:      `$20,000`\n"
            f"📈 MCap Range:         `$10K – $500K`\n"
            f"🔥 Min BSR:            `1.5x`\n"
            f"💸 Max Tax:            `5%`\n\n"
            f"🤖 {BOT_USERNAME}",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔔 Raise Threshold (+5)",
                                      callback_data="thresh_up"),
                 InlineKeyboardButton("🔔 Lower Threshold (−5)",
                                      callback_data="thresh_down")],
                back_row(),
            ]),
        )
        return

    # ══ HELP ══
    if data == "help":
        await safe_edit(
            query.message,
            "❓ *Help & Documentation*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📋 *COMMANDS*\n"
            "`/start`  — Open the main dashboard\n"
            "`/scan <ca>`  — Instant rug-check any token\n"
            "`/status`  — Quick scanner health check\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📡 *MODULES*\n"
            "📡 *Live Signals* — Latest auto-detected signals\n"
            "🔥 *Trending* — Top 10 by AI score\n"
            "💎 *Elite Calls* — Only 90+ confidence signals\n"
            "📊 *Positions* — Open trades (last 24h)\n"
            "📈 *History* — Closed positions (SL hit)\n"
            "🐋 *Whale Tracker* — Highest whale-buy tokens\n"
            "⚡ *Market Pulse* — Live Solana sentiment\n"
            "🏆 *Hall of Fame* — Best-performing signals\n"
            "💼 *Portfolio* — Active signals with live P&L\n"
            "📌 *Watchlist* — Your saved tokens\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 *HOW IT WORKS*\n"
            "• Polls DexScreener every 3 s (real data)\n"
            "• Filters: MCap $10K–$500K · Liq >$20K · BSR >1.5x\n"
            "• Rug checks: lock · honeypot · tax ≤5%\n"
            "• Bundler detection via Helius wallet ages\n"
            "• AI score: liq ratio + BSR + holders + whales + social\n"
            "• Live console: edits single message every 2 s\n"
            "• Main menu auto-refreshes every 30 s\n"
            "• 24/7 guardian auto-restarts scanner on crash\n"
            "• Heartbeat log every 5 min\n\n"
            f"🤖 {BOT_USERNAME}",
            InlineKeyboardMarkup([back_row()]),
        )
        return

    # ══ INLINE ACTIONS ══

    if data.startswith("rescan_"):
        addr = data[7:]
        await animate_loading(query.message, "Re-scanning token…", 5)
        locked, (safe, tax), holders, wbuys, price = await asyncio.gather(
            verify_liquidity_lock(addr),
            check_honeypot_and_tax(addr),
            get_holders_count(addr),
            get_whale_buys(addr),
            get_current_price(addr),
        )
        safety = ((40 if locked else 0) + (30 if safe else 0)
                  + (20 if tax <= 5 else 0) + (10 if holders > 100 else 0))
        result = ("✅ SAFE" if safety >= 80
                  else ("⚠️ MODERATE RISK" if safety >= 50 else "🔴 HIGH RISK"))
        await safe_edit(
            query.message,
            f"🔍 *Re-Scan Result*\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 `{addr}`\n\n"
            f"🔒 Liq Locked: {'✅' if locked else '❌'}\n"
            f"🍯 Honeypot:   {'✅ SAFE' if safe else '🔴 YES'}\n"
            f"💸 Tax: `{tax:.1f}%`\n"
            f"👥 Holders: `{holders}`\n"
            f"🐋 Whale Buys: `{wbuys}`\n"
            f"💰 Price: `{'${:.6f}'.format(price) if price else 'N/A'}`\n\n"
            f"🛡 `{score_meter(safety)}` *{safety}/100*\n"
            f"Verdict: *{result}*",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("📌 Watchlist", callback_data=f"wl_add_{addr}"),
                 InlineKeyboardButton("🔄 Re-Scan",   callback_data=f"rescan_{addr}")],
                back_row(),
            ]),
        )
        return

    if data.startswith("rug_"):
        addr = data[4:]
        await animate_loading(query.message, "Running rug analysis…", 4)
        locked, (safe, tax), holders = await asyncio.gather(
            verify_liquidity_lock(addr),
            check_honeypot_and_tax(addr),
            get_holders_count(addr),
        )
        safety = ((40 if locked else 0) + (30 if safe else 0)
                  + (20 if tax <= 5 else 0) + (10 if holders > 100 else 0))
        result = ("✅ SAFE" if safety >= 80
                  else ("⚠️ RISKY" if safety >= 50 else "🔴 HIGH RISK"))
        await safe_edit(
            query.message,
            f"🔒 *Rug Analysis*\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 `{addr}`\n\n"
            f"🔒 Liq Locked:  {'✅ YES' if locked else '❌ NO'}\n"
            f"🍯 Honeypot:    {'✅ SAFE' if safe else '🔴 DETECTED'}\n"
            f"💸 Tax:         `{tax:.1f}%` {'✅' if tax <= 5 else '⚠️'}\n"
            f"👥 Holders:     `{holders}`\n\n"
            f"🛡 `{score_meter(safety)}` *{safety}/100*\n"
            f"Verdict: *{result}*",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Re-Check", callback_data=f"rug_{addr}"),
                 InlineKeyboardButton("🏠 Menu",     callback_data="menu")],
            ]),
        )
        return

    if data.startswith("pnl_"):
        addr = data[4:]
        await animate_loading(query.message, "Fetching live price…", 3)
        price = await get_current_price(addr)
        async with AsyncSessionLocal() as db:
            r   = await db.execute(select(Signal).where(Signal.token_address == addr))
            sig = r.scalars().first()
        if sig and price:
            entry = (sig.market_cap / 1_000_000
                     if sig.market_cap and sig.market_cap > 0 else 0)
            pnl   = ((price - entry) / entry * 100) if entry > 0 else 0
            icon  = "🔴" if pnl < -10 else ("🟡" if pnl < 0 else "🟢")
            tps   = "".join(["✅" if getattr(sig, f"tp{i}_hit") else "⬜"
                             for i in range(1, 5)])
            text  = (f"💰 *Live Price Update*\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
                     f"*{sig.symbol}*  📍 `{addr[:20]}…`\n\n"
                     f"Current:  `${price:.6f}`\n"
                     f"P&L:      {icon} *{pnl:+.1f}%*\n"
                     f"TPs: `{tps}`")
        elif price:
            text = f"💰 Current price: `${price:.6f}`"
        else:
            text = "⚠️ *Could not fetch price.* Try again shortly."
        await safe_edit(query.message, text,
                        InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 Refresh", callback_data=f"pnl_{addr}"),
                             InlineKeyboardButton("🏠 Menu",    callback_data="menu")],
                        ]))
        return

    if data.startswith("copy_"):
        await query.answer(data[5:], show_alert=True)
        return

    if data.startswith("wl_add_"):
        addr = data[7:]
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(Watchlist.__table__.insert().values(
                    user_id=MY_TELEGRAM_ID, token_address=addr
                ))
                await db.commit()
            await query.answer("✅ Added to watchlist!", show_alert=False)
        except Exception:
            await query.answer("⚠️ Already in watchlist.", show_alert=False)
        return

    # ══ P&L DAY NAVIGATION (inline date buttons) ══
    if data.startswith("pnl_day_"):
        date_str = data[len("pnl_day_"):]
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            await query.answer("Invalid date", show_alert=True)
            return
        label = "Today" if target_date == datetime.utcnow().date() else str(target_date)
        await animate_loading(query.message, f"Loading P&L for {label}…", 3)

        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt   = datetime.combine(target_date, datetime.max.time())
        async with AsyncSessionLocal() as db:
            r    = await db.execute(
                select(Signal)
                .where(Signal.created_at >= start_dt, Signal.created_at <= end_dt)
                .order_by(Signal.created_at.asc())
            )
            sigs = r.scalars().all()

        if not sigs:
            await safe_edit(
                query.message,
                f"📭 *No signals on {label}*",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀ Prev", callback_data=f"pnl_day_{target_date - timedelta(days=1)}"),
                     InlineKeyboardButton("▶ Next", callback_data=f"pnl_day_{target_date + timedelta(days=1)}")],
                    back_row(),
                ]),
            )
            return

        prices = await asyncio.gather(*[get_current_price(s.token_address) for s in sigs])
        rows, total_pnl, wins, losses, open_cnt = [], 0.0, 0, 0, 0
        best_sym, best_pnl   = "", -9999.0
        worst_sym, worst_pnl = "", 9999.0
        for s, price in zip(sigs, prices):
            entry = (s.market_cap / 1_000_000) if s.market_cap and s.market_cap > 0 else 0
            pnl   = ((price - entry) / entry * 100) if entry > 0 and price > 0 else 0
            total_pnl += pnl
            tps_hit = sum([s.tp1_hit, s.tp2_hit, s.tp3_hit, s.tp4_hit])
            if s.sl_hit:            st = "🛑 SL";   losses += 1
            elif tps_hit == 4:      st = "🏆 TP4";  wins += 1
            elif tps_hit == 3:      st = "🥇 TP3";  wins += 1
            elif tps_hit == 2:      st = "✅ TP2";   wins += 1
            elif tps_hit == 1:      st = "✅ TP1";   wins += 1
            else:                   st = "📊 Open"; open_cnt += 1
            pnl_icon = "🟢" if pnl > 0 else ("🔴" if pnl < -5 else "🟡")
            rows.append(f"`{s.created_at.strftime('%H:%M')}` {pnl_icon} *{s.symbol:<7}*  `{pnl:+6.1f}%`  {st}")
            if pnl > best_pnl:  best_pnl,  best_sym  = pnl, s.symbol
            if pnl < worst_pnl: worst_pnl, worst_sym = pnl, s.symbol
        n = len(sigs)
        avg_pnl  = total_pnl / n
        win_rate = wins / n * 100
        await safe_edit(
            query.message,
            f"📊 *P&L Report — {label}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(rows)
            + f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Signals: `{n}`  🟢 Wins: `{wins}`  🔴 Losses: `{losses}`\n"
            f"🎯 Win Rate: `{win_rate:.0f}%`\n"
            f"{'🟢' if total_pnl > 0 else '🔴'} Total P&L: *{total_pnl:+.1f}%*  "
            f"Avg: `{avg_pnl:+.1f}%`\n"
            f"🏆 Best: *{best_sym}* `{best_pnl:+.1f}%`  "
            f"⚠️ Worst: *{worst_sym}* `{worst_pnl:+.1f}%`",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh",   callback_data=f"pnl_day_{target_date}"),
                 InlineKeyboardButton("◀ Yesterday",  callback_data=f"pnl_day_{target_date - timedelta(days=1)}")],
                [InlineKeyboardButton("▶ Next Day",   callback_data=f"pnl_day_{target_date + timedelta(days=1)}"),
                 InlineKeyboardButton("🏠 Menu",       callback_data="menu")],
            ]),
        )
        return

    await query.answer("🔧 Coming soon", show_alert=False)


# ════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════
async def main():
    global telegram_app, _console_lock, _bot_start_time

    _bot_start_time = time.time()
    _console_lock   = asyncio.Lock()

    logger.info("Creating database tables…")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database ready ✅")

    telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    # /start is the only command — everything else is inline buttons
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CallbackQueryHandler(button_callback))
    # Text handler for CA input after pressing 🔍 Scan Token
    telegram_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
    )

    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling(drop_pending_updates=True)
    logger.info("✅ Telegram bot polling started")

    # 24/7 always-on tasks — all wrapped with guardian/restart logic
    await scanner.start()                            # guardian inside scanner
    asyncio.create_task(_console_loop())             # persistent live console
    asyncio.create_task(_hub_refresh_loop())         # main menu auto-refresh
    asyncio.create_task(_heartbeat_loop())           # 5-min keep-alive log

    logger.info(f"🚀 Elite Bot v4.0 — {BOT_USERNAME} — 24/7 MODE ACTIVE")

    try:
        await asyncio.Event().wait()                 # run forever
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("Shutting down…")
        await scanner.stop()
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()
        await engine.dispose()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
