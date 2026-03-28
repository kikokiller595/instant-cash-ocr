# discord_notifier.py  — watches states.json AND latest.json and posts new items
import os, json, asyncio
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import discord

load_dotenv()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
# Single fallback channel (used if specific ones not provided)
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "0") or "0")

# Optional per-file channels (fallback to CHANNEL_ID if unset)
CHANNEL_ID_STATES = int(os.environ.get("CHANNEL_ID_STATES", str(CHANNEL_ID) or "0") or "0")
CHANNEL_ID_LATEST = int(os.environ.get("CHANNEL_ID_LATEST", str(CHANNEL_ID) or "0") or "0")

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "15"))

BASE = Path(__file__).resolve().parent
STATES_JSON = BASE / "states.json"
LATEST_JSON = BASE / "latest.json"

if not DISCORD_TOKEN:
    raise SystemExit("Set DISCORD_TOKEN in .env")
if CHANNEL_ID_STATES == 0 and CHANNEL_ID_LATEST == 0:
    raise SystemExit("Set CHANNEL_ID or specific CHANNEL_ID_STATES / CHANNEL_ID_LATEST in .env")

def load_json(path: Path):
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

def pretty_digits(arr):
    if not arr: return "—"
    return " ".join(str(x) for x in arr)

def build_msg_states(item: dict) -> str:
    typ = item.get("type", "today")
    state = item.get("state", "Unknown")
    p3 = pretty_digits(item.get("pick3", []))
    p4 = pretty_digits(item.get("pick4", []))
    ts = item.get("timestamp", "")
    try:
        ts_local = datetime.fromisoformat(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        ts_local = ts
    return (
        f"🎰 **{state}** — *{typ}*\n"
        f"• Pick 3: `{p3}`\n"
        f"• Pick 4: `{p4}`\n"
        f"• Captured: {ts_local}"
    )

def build_msg_latest(item: dict) -> str:
    # latest.json schema from your frontend loader:
    # { draw_id, status, captured_at, pick2, pick3, pick4, pick5, ... }
    draw_id = item.get("draw_id", "unknown-draw")
    status = item.get("status", "—")
    captured_at = item.get("captured_at", "")
    try:
        ts_local = datetime.fromisoformat(captured_at).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        ts_local = captured_at

    # They are strings like "5792". Keep them as compact strings in Discord.
    def nz(s):  # show only if has a non-zero digit
        return isinstance(s, str) and any(ch != "0" for ch in s)

    lines = [f"🕒 **Live Draw Update**", f"• Draw: `{draw_id}`", f"• Status: `{status}`"]
    if captured_at: lines.append(f"• Captured: {ts_local}")
    if nz(item.get("pick2", "")): lines.append(f"• Pick 2: `{item['pick2']}`")
    if nz(item.get("pick3", "")): lines.append(f"• Pick 3: `{item['pick3']}`")
    if nz(item.get("pick4", "")): lines.append(f"• Pick 4: `{item['pick4']}`")
    if nz(item.get("pick5", "")): lines.append(f"• Pick 5: `{item['pick5']}`")
    return "\n".join(lines)

class Notifier(discord.Client):
    def __init__(self, ch_states: int, ch_latest: int, poll_interval: int, **kwargs):
        super().__init__(**kwargs)
        self.channel_states_id = ch_states
        self.channel_latest_id = ch_latest
        self.poll_interval = poll_interval
        self.seen_states_ids = set()  # from states.json items['id']
        self.seen_latest_ids = set()  # from latest.json items['draw_id']
        self.ch_states = None
        self.ch_latest = None

    async def setup_hook(self):
        # background watcher
        asyncio.create_task(self.watcher_loop())

    async def on_ready(self):
        print(f"Discord notifier logged in as {self.user}")
        # Resolve channels once on ready (try cache, then fetch)
        self.ch_states = await self._resolve_channel(self.channel_states_id, name="states")
        self.ch_latest = await self._resolve_channel(self.channel_latest_id, name="latest")

        # Test messages so you see it’s alive
        if self.ch_states:
            await self.ch_states.send("✅ Notifier connected (states.json watcher ready).")
        if self.ch_latest and (self.channel_latest_id != self.channel_states_id or not self.ch_states):
            await self.ch_latest.send("✅ Notifier connected (latest.json watcher ready).")

    async def _resolve_channel(self, cid: int, name: str):
        if cid == 0:
            return None
        ch = self.get_channel(cid)
        if ch is None:
            try:
                ch = await self.fetch_channel(cid)
            except Exception as e:
                print(f"ERROR: cannot fetch {name} channel {cid}: {e}")
                return None
        # Optional: basic permission check
        try:
            guild = ch.guild if hasattr(ch, "guild") else None
            me = guild.me if guild else None
            perms = ch.permissions_for(me) if guild and me else None
            if perms and not (perms.view_channel and perms.send_messages):
                print(f"ERROR: bot lacks View/Send in channel {cid} ({name}).")
                return None
        except Exception:
            pass
        return ch

    async def watcher_loop(self):
        await self.wait_until_ready()

        # Seed existing ids to avoid spamming on startup
        for it in load_json(STATES_JSON):
            if isinstance(it, dict) and it.get("id"):
                self.seen_states_ids.add(it["id"])
        for it in load_json(LATEST_JSON):
            if isinstance(it, dict) and it.get("draw_id"):
                self.seen_latest_ids.add(it["draw_id"])

        print(f"[notifier] ready; polling every {self.poll_interval}s")

        while not self.is_closed():
            try:
                # STATES.JSON — post new by 'id' (oldest→newest order)
                if self.ch_states:
                    arr = load_json(STATES_JSON)
                    for it in reversed(arr):
                        if not isinstance(it, dict): continue
                        iid = it.get("id")
                        if not iid or iid in self.seen_states_ids: continue
                        await self.ch_states.send(build_msg_states(it))
                        print(f"[states] posted {iid}")
                        self.seen_states_ids.add(iid)

                # LATEST.JSON — post new by 'draw_id' (oldest→newest order)
                if self.ch_latest:
                    arr2 = load_json(LATEST_JSON)
                    for it in reversed(arr2):
                        if not isinstance(it, dict): continue
                        did = it.get("draw_id")
                        if not did or did in self.seen_latest_ids: continue
                        # Optional: you can filter to today's draws only if you want
                        await self.ch_latest.send(build_msg_latest(it))
                        print(f"[latest] posted {did}")
                        self.seen_latest_ids.add(did)

            except Exception as e:
                print("[notifier] loop error:", e)

            await asyncio.sleep(self.poll_interval)

# minimal intents are fine for sending messages
intents = discord.Intents.default()
client = Notifier(
    ch_states=CHANNEL_ID_STATES,
    ch_latest=CHANNEL_ID_LATEST,
    poll_interval=POLL_INTERVAL,
    intents=intents,
)

if __name__ == "__main__":
    discord.utils.setup_logging()
    client.run(DISCORD_TOKEN)
