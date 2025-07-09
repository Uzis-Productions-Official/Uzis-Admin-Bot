import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
from flask import Flask
from threading import Thread
import asyncio
import sqlite3
import json
import datetime
import pytz
from typing import Optional, List
import re

# ---- DATABASE SETUP ----
DB_PATH = "database.db"

class Database:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._create_tables()

    def _create_tables(self):
        c = self._conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS guild_configs (
            guild_id INTEGER PRIMARY KEY,
            config_json TEXT NOT NULL
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS infractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            mod_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            timestamp INTEGER NOT NULL
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS reaction_roles (
            guild_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            emoji TEXT NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, message_id, emoji)
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS custom_commands (
            guild_id INTEGER NOT NULL,
            command_name TEXT NOT NULL,
            response TEXT NOT NULL,
            PRIMARY KEY (guild_id, command_name)
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            mod_id INTEGER NOT NULL,
            reason TEXT,
            timestamp INTEGER NOT NULL
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            remind_time INTEGER NOT NULL,
            message TEXT NOT NULL
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            guild_id INTEGER NOT NULL,
            log_type TEXT NOT NULL,
            channel_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, log_type)
        )""")
        self._conn.commit()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
db = Database()

app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    thread = Thread(target=run)
    thread.start()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    try:
        synced = await tree.sync()
        print(f'Synced {len(synced)} slash commands.')
    except Exception as e:
        print(f'Failed to sync commands: {e}')

# Helper: log to configured channel
async def send_log(guild: discord.Guild, log_type: str, embed: discord.Embed):
    async with db._lock:
        cur = db._conn.cursor()
        cur.execute("SELECT channel_id FROM logs WHERE guild_id = ? AND log_type = ?", (guild.id, log_type))
        row = cur.fetchone()
        if row:
            channel = guild.get_channel(row["channel_id"])
            if channel:
                try:
                    await channel.send(embed=embed)
                except Exception:
                    pass

# Helper: timestamp
def now():
    return int(datetime.datetime.now(tz=pytz.utc).timestamp())

@tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(member="Member to kick", reason="Reason for kick")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided"):
    if not interaction.user.guild_permissions.kick_members:
        await interaction.response.send_message("You do not have permission to kick members.", ephemeral=True)
        return
    if member == interaction.user:
        await interaction.response.send_message("You cannot kick yourself.", ephemeral=True)
        return
    try:
        await member.kick(reason=reason)
        await interaction.response.send_message(f"{member} has been kicked. Reason: {reason}")
        # Log it
        embed = discord.Embed(title="Member Kicked", color=discord.Color.orange(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Member", value=str(member), inline=True)
        embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        await send_log(interaction.guild, "kicks", embed)
    except Exception as e:
        await interaction.response.send_message(f"Failed to kick member: {e}", ephemeral=True)

@tree.command(name="ban", description="Ban a member from the server")
@app_commands.describe(member="Member to ban", reason="Reason for ban")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided"):
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("You do not have permission to ban members.", ephemeral=True)
        return
    if member == interaction.user:
        await interaction.response.send_message("You cannot ban yourself.", ephemeral=True)
        return
    try:
        await member.ban(reason=reason)
        await interaction.response.send_message(f"{member} has been banned. Reason: {reason}")
        # Log it
        embed = discord.Embed(title="Member Banned", color=discord.Color.red(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Member", value=str(member), inline=True)
        embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        await send_log(interaction.guild, "bans", embed)
    except Exception as e:
        await interaction.response.send_message(f"Failed to ban member: {e}", ephemeral=True)

@tree.command(name="unban", description="Unban a member from the server")
@app_commands.describe(user="User ID to unban")
async def unban(interaction: discord.Interaction, user: discord.User):
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("You do not have permission to unban members.", ephemeral=True)
        return
    banned_users = await interaction.guild.bans()
    user_entry = None
    for entry in banned_users:
        if entry.user.id == user.id:
            user_entry = entry
            break
    if user_entry is None:
        await interaction.response.send_message(f"{user} is not banned.", ephemeral=True)
        return
    try:
        await interaction.guild.unban(user)
        await interaction.response.send_message(f"{user} has been unbanned.")
        # Log it
        embed = discord.Embed(title="Member Unbanned", color=discord.Color.green(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="User", value=str(user), inline=True)
        embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
        await send_log(interaction.guild, "bans", embed)
    except Exception as e:
        await interaction.response.send_message(f"Failed to unban member: {e}", ephemeral=True)

@tree.command(name="warn", description="Warn a member")
@app_commands.describe(member="Member to warn", reason="Reason for warning")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided"):
    if not interaction.user.guild_permissions.kick_members:
        await interaction.response.send_message("You do not have permission to warn members.", ephemeral=True)
        return
    timestamp = now()
    async with db._lock:
        c = db._conn.cursor()
        c.execute("INSERT INTO warnings (guild_id, user_id, mod_id, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
                  (interaction.guild.id, member.id, interaction.user.id, reason, timestamp))
        db._conn.commit()
    await interaction.response.send_message(f"{member} has been warned. Reason: {reason}")
    # Log it
    embed = discord.Embed(title="Member Warned", color=discord.Color.orange(), timestamp=datetime.datetime.utcnow())
    embed.add_field(name="Member", value=str(member), inline=True)
    embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await send_log(interaction.guild, "warns", embed)

@tree.command(name="warnings", description="List warnings for a member")
@app_commands.describe(member="Member to check warnings for")
async def warnings(interaction: discord.Interaction, member: discord.Member):
    async with db._lock:
        c = db._conn.cursor()
        c.execute("SELECT mod_id, reason, timestamp FROM warnings WHERE guild_id = ? AND user_id = ?", (interaction.guild.id, member.id))
        rows = c.fetchall()
    if not rows:
        await interaction.response.send_message(f"{member} has no warnings.", ephemeral=True)
        return
    embed = discord.Embed(title=f"Warnings for {member}", color=discord.Color.yellow())
    for i, row in enumerate(rows, 1):
        mod = interaction.guild.get_member(row["mod_id"])
        mod_name = mod.display_name if mod else "Unknown Moderator"
        reason = row["reason"]
        timestamp = datetime.datetime.fromtimestamp(row["timestamp"], tz=pytz.UTC).strftime("%Y-%m-%d %H:%M UTC")
        embed.add_field(name=f"Warning {i}", value=f"By: {mod_name}\nReason: {reason}\nDate: {timestamp}", inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="clear", description="Delete messages from a channel")
@app_commands.describe(amount="Number of messages to delete")
async def clear(interaction: discord.Interaction, amount: int):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("You do not have permission to manage messages.", ephemeral=True)
        return
    if amount < 1 or amount > 100:
        await interaction.response.send_message("Amount must be between 1 and 100.", ephemeral=True)
        return
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.response.send_message(f"Deleted {len(deleted)} messages.", ephemeral=True)
    # Log it
    embed = discord.Embed(title="Messages Purged", color=discord.Color.dark_grey(), timestamp=datetime.datetime.utcnow())
    embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
    embed.add_field(name="Channel", value=str(interaction.channel), inline=True)
    embed.add_field(name="Amount", value=str(len(deleted)), inline=True)
    await send_log(interaction.guild, "messages", embed)

@tree.command(name="nick", description="Change a member's nickname")
@app_commands.describe(member="Member to change nickname", nickname="New nickname")
async def nick(interaction: discord.Interaction, member: discord.Member, nickname: str):
    if not interaction.user.guild_permissions.manage_nicknames:
        await interaction.response.send_message("You do not have permission to manage nicknames.", ephemeral=True)
        return
    try:
        await member.edit(nick=nickname)
        await interaction.response.send_message(f"Changed nickname of {member} to {nickname}.")
        # Log it
        embed = discord.Embed(title="Nickname Changed", color=discord.Color.blue(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Member", value=str(member), inline=True)
        embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
        embed.add_field(name="New Nickname", value=nickname, inline=False)
        await send_log(interaction.guild, "roles", embed)
    except Exception as e:
        await interaction.response.send_message(f"Failed to change nickname: {e}", ephemeral=True)

# Additional moderation commands like /mute, /unmute can be added similarly

# Reaction Roles - simplified example storing mapping in DB

@tree.command(name="reactionrole_create", description="Create a reaction role message")
@app_commands.describe(channel="Channel to send message", message="Message content", emoji_role_pairs="Emoji and role pairs, comma separated, e.g. 😀 Member, 🔥 VIP")
async def reactionrole_create(interaction: discord.Interaction, channel: discord.TextChannel, message: str, emoji_role_pairs: str):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("You need Manage Roles permission.", ephemeral=True)
        return

    pairs = [p.strip() for p in emoji_role_pairs.split(",")]
    mapping = {}
    for pair in pairs:
        if ' ' not in pair:
            await interaction.response.send_message(f"Invalid pair: {pair}. Format is 'emoji roleName'", ephemeral=True)
            return
        emoji, role_name = pair.split(' ', 1)
        role = discord.utils.get(interaction.guild.roles, name=role_name.strip())
        if not role:
            await interaction.response.send_message(f"Role `{role_name.strip()}` not found.", ephemeral=True)
            return
        mapping[emoji] = role.id

    sent_msg = await channel.send(message)
    for emoji in mapping:
        try:
            await sent_msg.add_reaction(emoji)
        except Exception:
            await interaction.followup.send(f"Invalid emoji: {emoji}", ephemeral=True)
            return

    # Save to DB
    async with db._lock:
        c = db._conn.cursor()
        for emoji, role_id in mapping.items():
            c.execute("INSERT OR REPLACE INTO reaction_roles (guild_id, message_id, emoji, role_id) VALUES (?, ?, ?, ?)",
                      (interaction.guild.id, sent_msg.id, emoji, role_id))
        db._conn.commit()

    await interaction.response.send_message(f"Reaction role message created in {channel.mention}.", ephemeral=True)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.member is None or payload.member.bot:
        return
    async with db._lock:
        c = db._conn.cursor()
        c.execute("SELECT role_id FROM reaction_roles WHERE guild_id = ? AND message_id = ? AND emoji = ?",
                  (payload.guild_id, payload.message_id, str(payload.emoji)))
        row = c.fetchone()
        if not row:
            return
        role_id = row["role_id"]
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    role = guild.get_role(role_id)
    if not role:
        return
    try:
        await payload.member.add_roles(role)
    except Exception:
        pass

@bot.event
async def on_raw_reaction_remove(payload):
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return
    async with db._lock:
        c = db._conn.cursor()
        c.execute("SELECT role_id FROM reaction_roles WHERE guild_id = ? AND message_id = ? AND emoji = ?",
                  (payload.guild_id, payload.message_id, str(payload.emoji)))
        row = c.fetchone()
        if not row:
            return
        role_id = row["role_id"]
    role = guild.get_role(role_id)
    if not role:
        return
    try:
        await member.remove_roles(role)
    except Exception:
        pass

# Automod toggle command example

@tree.command(name="automod_set", description="Enable or disable an automod filter")
@app_commands.describe(filter_name="Filter name (badwords, invites, links, spam)", state="On or off")
async def automod_set(interaction: discord.Interaction, filter_name: str, state: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need Administrator permission.", ephemeral=True)
        return
    filter_name = filter_name.lower()
    if filter_name not in {"badwords", "invites", "links", "spam"}:
        await interaction.response.send_message("Invalid filter name.", ephemeral=True)
        return
    state = state.lower()
    if state not in {"on", "off"}:
        await interaction.response.send_message("State must be 'on' or 'off'.", ephemeral=True)
        return
    config = await db.get_guild_config(interaction.guild.id) or {}
    automod = config.get("automod", {})
    automod[filter_name] = (state == "on")
    config["automod"] = automod
    await db.set_guild_config(interaction.guild.id, config)
    await interaction.response.send_message(f"Automod filter `{filter_name}` set to `{state}`.")

# Logging setup command

@tree.command(name="log_set", description="Set a log channel for a specific log type")
@app_commands.describe(log_type="Type of log (messages, joins, leaves, bans, kicks, roles, channels)", channel="Channel to send logs")
async def log_set(interaction: discord.Interaction, log_type: str, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need Administrator permission.", ephemeral=True)
        return
    log_type = log_type.lower()
    valid_types = {"messages", "joins", "leaves", "bans", "kicks", "roles", "channels", "warns"}
    if log_type not in valid_types:
        await interaction.response.send_message(f"Invalid log type. Valid types: {', '.join(valid_types)}", ephemeral=True)
        return
    async with db._lock:
        c = db._conn.cursor()
        c.execute("""
            INSERT INTO logs (guild_id, log_type, channel_id) VALUES (?, ?, ?)
            ON CONFLICT(guild_id, log_type) DO UPDATE SET channel_id=excluded.channel_id
        """, (interaction.guild.id, log_type, channel.id))
        db._conn.commit()
    await interaction.response.send_message(f"Log channel for `{log_type}` set to {channel.mention}.")

# Welcome & Leave messages

@tree.command(name="welcome_set", description="Set welcome channel or message")
@app_commands.describe(channel="Channel to send welcome messages (optional)", message="Custom welcome message (optional)")
async def welcome_set(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None, message: Optional[str] = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Administrator permission required.", ephemeral=True)
        return
    config = await db.get_guild_config(interaction.guild.id) or {}
    welcome = config.get("welcome", {})
    if channel:
        welcome["channel_id"] = channel.id
    if message:
        welcome["message"] = message
    config["welcome"] = welcome
    await db.set_guild_config(interaction.guild.id, config)
    await interaction.response.send_message("Welcome settings updated.", ephemeral=True)

@tree.command(name="leave_set", description="Set leave channel or message")
@app_commands.describe(channel="Channel to send leave messages (optional)", message="Custom leave message (optional)")
async def leave_set(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None, message: Optional[str] = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Administrator permission required.", ephemeral=True)
        return
    config = await db.get_guild_config(interaction.guild.id) or {}
    leave = config.get("leave", {})
    if channel:
        leave["channel_id"] = channel.id
    if message:
        leave["message"] = message
    config["leave"] = leave
    await db.set_guild_config(interaction.guild.id, config)
    await interaction.response.send_message("Leave settings updated.", ephemeral=True)

@bot.event
async def on_member_join(member):
    config = await db.get_guild_config(member.guild.id) or {}
    welcome = config.get("welcome", {})
    channel_id = welcome.get("channel_id")
    message = welcome.get("message", f"Welcome to the server, {member.mention}!")
    if channel_id:
        channel = member.guild.get_channel(channel_id)
        if channel:
            await channel.send(message.replace("{user}", member.mention))

@bot.event
async def on_member_remove(member):
    config = await db.get_guild_config(member.guild.id) or {}
    leave = config.get("leave", {})
    channel_id = leave.get("channel_id")
    message = leave.get("message", f"{member} has left the server.")
    if channel_id:
        channel = member.guild.get_channel(channel_id)
        if channel:
            await channel.send(message.replace("{user}", str(member)))

# Starboard system

@tree.command(name="starboard_set", description="Set the starboard channel")
@app_commands.describe(channel="Channel to post starboard messages")
async def starboard_set(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Administrator permission required.", ephemeral=True)
        return
    config = await db.get_guild_config(interaction.guild.id) or {}
    config["starboard_channel_id"] = channel.id
    await db.set_guild_config(interaction.guild.id, config)
    await interaction.response.send_message(f"Starboard channel set to {channel.mention}")

STAR_THRESHOLD = 3  # Number of stars needed

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    if str(reaction.emoji) != "⭐":
        return
    message = reaction.message
    config = await db.get_guild_config(message.guild.id) or {}
    starboard_channel_id = config.get("starboard_channel_id")
    if not starboard_channel_id:
        return
    if reaction.count >= STAR_THRESHOLD:
        channel = message.guild.get_channel(starboard_channel_id)
        if not channel:
            return
        embed = discord.Embed(description=message.content, color=discord.Color.gold(), timestamp=message.created_at)
        embed.set_author(name=message.author.display_name, icon_url=message.author.avatar.url if message.author.avatar else None)
        embed.add_field(name="Jump to message", value=f"[Click Here]({message.jump_url})")
        await channel.send(embed=embed)

# Reminder commands

@tree.command(name="remindme", description="Set a reminder")
@app_commands.describe(time="Time in minutes", message="Reminder message")
async def remindme(interaction: discord.Interaction, time: int, message: str):
    remind_time = datetime.datetime.utcnow() + datetime.timedelta(minutes=time)
    timestamp = int(remind_time.timestamp())
    async with db._lock:
        c = db._conn.cursor()
        c.execute("INSERT INTO reminders (user_id, remind_time, message) VALUES (?, ?, ?)",
                  (interaction.user.id, timestamp, message))
        db._conn.commit()
    await interaction.response.send_message(f"Reminder set for {time} minutes from now.")

@tasks.loop(seconds=60)
async def check_reminders():
    now_ts = int(datetime.datetime.utcnow().timestamp())
    async with db._lock:
        c = db._conn.cursor()
        c.execute("SELECT id, user_id, message FROM reminders WHERE remind_time <= ?", (now_ts,))
        rows = c.fetchall()
        for row in rows:
            user = bot.get_user(row["user_id"])
            if user:
                try:
                    await user.send(f"⏰ Reminder: {row['message']}")
                except Exception:
                    pass
            c.execute("DELETE FROM reminders WHERE id = ?", (row["id"],))
        db._conn.commit()

check_reminders.start()

# Custom commands (basic add/list/remove)

@tree.command(name="mute", description="Mute a member in the server")
@app_commands.describe(member="Member to mute", reason="Reason for muting")
async def mute(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided"):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("You do not have permission to mute members.", ephemeral=True)
        return
    if member == interaction.user:
        await interaction.response.send_message("You cannot mute yourself.", ephemeral=True)
        return

    guild = interaction.guild
    muted_role = discord.utils.get(guild.roles, name="Muted")
    if not muted_role:
        # Create Muted role
        muted_role = await guild.create_role(name="Muted", reason="Create Muted role for muting members")
        # Deny send messages and speak permissions in all channels
        for channel in guild.channels:
            try:
                await channel.set_permissions(muted_role,
                                              send_messages=False,
                                              speak=False,
                                              add_reactions=False)
            except Exception:
                pass

    if muted_role in member.roles:
        await interaction.response.send_message(f"{member} is already muted.", ephemeral=True)
        return

    try:
        await member.add_roles(muted_role, reason=reason)
        await interaction.response.send_message(f"{member} has been muted. Reason: {reason}")
        # Log it
        embed = discord.Embed(title="Member Muted", color=discord.Color.dark_gray(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Member", value=str(member), inline=True)
        embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        await send_log(guild, "mutes", embed)
    except Exception as e:
        await interaction.response.send_message(f"Failed to mute member: {e}", ephemeral=True)

@tree.command(name="unmute", description="Unmute a member in the server")
@app_commands.describe(member="Member to unmute")
async def unmute(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("You do not have permission to unmute members.", ephemeral=True)
        return

    guild = interaction.guild
    muted_role = discord.utils.get(guild.roles, name="Muted")
    if not muted_role or muted_role not in member.roles:
        await interaction.response.send_message(f"{member} is not muted.", ephemeral=True)
        return

    try:
        await member.remove_roles(muted_role)
        await interaction.response.send_message(f"{member} has been unmuted.")
        # Log it
        embed = discord.Embed(title="Member Unmuted", color=discord.Color.green(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Member", value=str(member), inline=True)
        embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
        await send_log(guild, "mutes", embed)
    except Exception as e:
        await interaction.response.send_message(f"Failed to unmute member: {e}", ephemeral=True)


@tree.command(name="customcmd_add", description="Add a custom command")
@app_commands.describe(name="Command name", response="Command response")
async def customcmd_add(interaction: discord.Interaction, name: str, response: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Administrator permission required.", ephemeral=True)
        return
    async with db._lock:
        c = db._conn.cursor()
        c.execute("INSERT OR REPLACE INTO custom_commands (guild_id, command_name, response) VALUES (?, ?, ?)",
                  (interaction.guild.id, name.lower(), response))
        db._conn.commit()
    await interaction.response.send_message(f"Custom command `{name}` added.")

@tree.command(name="customcmd_list", description="List custom commands")
async def customcmd_list(interaction: discord.Interaction):
    async with db._lock:
        c = db._conn.cursor()
        c.execute("SELECT command_name FROM custom_commands WHERE guild_id = ?", (interaction.guild.id,))
        rows = c.fetchall()
    if not rows:
        await interaction.response.send_message("No custom commands found.", ephemeral=True)
        return
    cmds = ", ".join(row["command_name"] for row in rows)
    await interaction.response.send_message(f"Custom commands: {cmds}", ephemeral=True)

@tree.command(name="customcmd_remove", description="Remove a custom command")
@app_commands.describe(name="Command name to remove")
async def customcmd_remove(interaction: discord.Interaction, name: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Administrator permission required.", ephemeral=True)
        return
    async with db._lock:
        c = db._conn.cursor()
        c.execute("DELETE FROM custom_commands WHERE guild_id = ? AND command_name = ?", (interaction.guild.id, name.lower()))
        db._conn.commit()
    await interaction.response.send_message(f"Custom command `{name}` removed.")

# Detect and run custom commands

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    async with db._lock:
        c = db._conn.cursor()
        c.execute("SELECT response FROM custom_commands WHERE guild_id = ? AND command_name = ?", (message.guild.id, message.content.lower()))
        row = c.fetchone()
    if row:
        await message.channel.send(row["response"])
    await bot.process_commands(message)

# Automod filters - basic message content filtering

@bot.event
async def on_message_edit(before, after):
    await check_automod(after)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await check_automod(message)
    # Custom commands handled in Part 5 on_message event, so only call process_commands once
    await bot.process_commands(message)

async def check_automod(message):
    config = await db.get_guild_config(message.guild.id) or {}
    automod = config.get("automod", {})
    content = message.content.lower()

    # Bad words filter (example list)
    badwords = {"badword1", "badword2"}  # Add your bad words here
    if automod.get("badwords", False):
        if any(word in content for word in badwords):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention}, your message was removed due to bad language.")
            except Exception:
                pass

    # Invite link filter
    if automod.get("invites", False):
        if re.search(r"(discord\.gg|discordapp\.com/invite)", content):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention}, invites are not allowed.")
            except Exception:
                pass

    # Link filter
    if automod.get("links", False):
        if re.search(r"https?://", content):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention}, links are not allowed.")
            except Exception:
                pass

    # Spam filter - simplified: block repeated messages (implement as needed)

# Slowmode command

@tree.command(name="slowmode", description="Set slowmode delay for a channel")
@app_commands.describe(channel="Channel to set slowmode", seconds="Delay in seconds (0 to disable)")
async def slowmode(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None, seconds: int = 0):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You do not have permission to manage channels.", ephemeral=True)
        return
    target_channel = channel or interaction.channel
    if seconds < 0 or seconds > 21600:
        await interaction.response.send_message("Seconds must be between 0 and 21600 (6 hours).", ephemeral=True)
        return
    try:
        await target_channel.edit(slowmode_delay=seconds)
        await interaction.response.send_message(f"Set slowmode delay for {target_channel.mention} to {seconds} seconds.")
    except Exception as e:
        await interaction.response.send_message(f"Failed to set slowmode: {e}", ephemeral=True)

# Lock / Unlock commands

@tree.command(name="lock", description="Lock a channel (deny send messages)")
@app_commands.describe(channel="Channel to lock (optional)")
async def lock(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You do not have permission to manage channels.", ephemeral=True)
        return
    target_channel = channel or interaction.channel
    overwrite = target_channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    try:
        await target_channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(f"Locked {target_channel.mention}.")
    except Exception as e:
        await interaction.response.send_message(f"Failed to lock channel: {e}", ephemeral=True)

@tree.command(name="unlock", description="Unlock a channel (allow send messages)")
@app_commands.describe(channel="Channel to unlock (optional)")
async def unlock(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You do not have permission to manage channels.", ephemeral=True)
        return
    target_channel = channel or interaction.channel
    overwrite = target_channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    try:
        await target_channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(f"Unlocked {target_channel.mention}.")
    except Exception as e:
        await interaction.response.send_message(f"Failed to unlock channel: {e}", ephemeral=True)

# Helper methods for guild config get/set (added to Database class earlier)

async def get_guild_config(self, guild_id: int) -> Optional[dict]:
    async with self._lock:
        c = self._conn.cursor()
        c.execute("SELECT config_json FROM guild_configs WHERE guild_id = ?", (guild_id,))
        row = c.fetchone()
        if row:
            return json.loads(row["config_json"])
        return {}

async def set_guild_config(self, guild_id: int, config: dict):
    async with self._lock:
        c = self._conn.cursor()
        config_json = json.dumps(config)
        c.execute("""
            INSERT INTO guild_configs (guild_id, config_json)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET config_json=excluded.config_json
        """, (guild_id, config_json))
        self._conn.commit()

# Bind the methods to the Database class
Database.get_guild_config = get_guild_config
Database.set_guild_config = set_guild_config

# Run keep_alive server and bot

if __name__ == "__main__":
    keep_alive()
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
        exit(1)
    bot.run(TOKEN)





