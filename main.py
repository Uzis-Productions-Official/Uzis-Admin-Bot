# main.py — Part 1: Imports, setup, Database class, helper functions

import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import datetime
import json
import re
import pytz
from typing import Optional

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
intents.guilds = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

class Database:
    def __init__(self, db_path="database.db"):
        import sqlite3
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._init_tables()

    def _init_tables(self):
        c = self._conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS guild_configs (
            guild_id INTEGER PRIMARY KEY,
            config_json TEXT NOT NULL
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            guild_id INTEGER,
            log_type TEXT,
            channel_id INTEGER,
            PRIMARY KEY(guild_id, log_type)
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS reaction_roles (
            guild_id INTEGER,
            message_id INTEGER,
            emoji TEXT,
            role_id INTEGER,
            PRIMARY KEY (guild_id, message_id, emoji)
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            remind_time INTEGER,
            message TEXT
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS custom_commands (
            guild_id INTEGER,
            command_name TEXT,
            response TEXT,
            PRIMARY KEY(guild_id, command_name)
        )""")
        self._conn.commit()

    async def get_guild_config(self, guild_id: int) -> dict:
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

    async def get_log_channel(self, guild_id: int, log_type: str) -> Optional[int]:
        async with self._lock:
            c = self._conn.cursor()
            c.execute("SELECT channel_id FROM logs WHERE guild_id = ? AND log_type = ?", (guild_id, log_type))
            row = c.fetchone()
            return row["channel_id"] if row else None

    async def set_log_channel(self, guild_id: int, log_type: str, channel_id: int):
        async with self._lock:
            c = self._conn.cursor()
            c.execute("""
                INSERT INTO logs (guild_id, log_type, channel_id)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, log_type) DO UPDATE SET channel_id=excluded.channel_id
            """, (guild_id, log_type, channel_id))
            self._conn.commit()

db = Database()

async def send_log(guild: discord.Guild, log_type: str, embed: discord.Embed):
    channel_id = await db.get_log_channel(guild.id, log_type)
    if channel_id:
        channel = guild.get_channel(channel_id)
        if channel:
            try:
                await channel.send(embed=embed)
            except Exception:
                pass

# main.py — Part 2: Basic moderation commands (kick, ban, unban, mute, unmute)

@tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(member="Member to kick", reason="Reason for kick")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided"):
    if not interaction.user.guild_permissions.kick_members:
        await interaction.response.send_message("You need Kick Members permission.", ephemeral=True)
        return
    if member == interaction.user:
        await interaction.response.send_message("You cannot kick yourself.", ephemeral=True)
        return
    try:
        await member.kick(reason=reason)
        await interaction.response.send_message(f"{member} was kicked. Reason: {reason}")
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
        await interaction.response.send_message("You need Ban Members permission.", ephemeral=True)
        return
    if member == interaction.user:
        await interaction.response.send_message("You cannot ban yourself.", ephemeral=True)
        return
    try:
        await member.ban(reason=reason)
        await interaction.response.send_message(f"{member} was banned. Reason: {reason}")
        embed = discord.Embed(title="Member Banned", color=discord.Color.red(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Member", value=str(member), inline=True)
        embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        await send_log(interaction.guild, "bans", embed)
    except Exception as e:
        await interaction.response.send_message(f"Failed to ban member: {e}", ephemeral=True)

@tree.command(name="unban", description="Unban a user by ID")
@app_commands.describe(user_id="ID of the user to unban")
async def unban(interaction: discord.Interaction, user_id: int):
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("You need Ban Members permission.", ephemeral=True)
        return
    try:
        user = await bot.fetch_user(user_id)
        await interaction.guild.unban(user)
        await interaction.response.send_message(f"Unbanned {user}.")
        embed = discord.Embed(title="Member Unbanned", color=discord.Color.green(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Member", value=str(user), inline=True)
        embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
        await send_log(interaction.guild, "bans", embed)
    except Exception as e:
        await interaction.response.send_message(f"Failed to unban: {e}", ephemeral=True)

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
        muted_role = await guild.create_role(name="Muted", reason="Create Muted role for muting members")
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
        embed = discord.Embed(title="Member Unmuted", color=discord.Color.green(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Member", value=str(member), inline=True)
        embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
        await send_log(guild, "mutes", embed)
    except Exception as e:
        await interaction.response.send_message(f"Failed to unmute member: {e}", ephemeral=True)

# main.py — Part 3: Logging configuration commands and reaction roles

@tree.command(name="log", description="Set log channel for a log type")
@app_commands.describe(log_type="Type of log (e.g. bans, kicks, mutes)", channel="Channel to send logs")
async def log(interaction: discord.Interaction, log_type: str, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Administrator permission required.", ephemeral=True)
        return
    log_type = log_type.lower()
    valid_log_types = {"bans", "kicks", "mutes", "modactions", "joins", "leaves", "message_delete", "message_edit"}
    if log_type not in valid_log_types:
        await interaction.response.send_message(f"Invalid log type. Valid types: {', '.join(valid_log_types)}", ephemeral=True)
        return
    await db.set_log_channel(interaction.guild.id, log_type, channel.id)
    await interaction.response.send_message(f"Log channel for `{log_type}` set to {channel.mention}")

# Reaction roles commands

@tree.command(name="rr_add", description="Add a reaction role")
@app_commands.describe(message_id="ID of the message", emoji="Emoji to react with", role="Role to assign")
async def rr_add(interaction: discord.Interaction, message_id: int, emoji: str, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("You need Manage Roles permission.", ephemeral=True)
        return
    async with db._lock:
        c = db._conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO reaction_roles (guild_id, message_id, emoji, role_id)
            VALUES (?, ?, ?, ?)
        """, (interaction.guild.id, message_id, emoji, role.id))
        db._conn.commit()
    try:
        msg = await interaction.channel.fetch_message(message_id)
        await msg.add_reaction(emoji)
    except Exception:
        pass
    await interaction.response.send_message(f"Reaction role set: React with {emoji} to get {role.name}.")

@tree.command(name="rr_remove", description="Remove a reaction role")
@app_commands.describe(message_id="ID of the message", emoji="Emoji to remove")
async def rr_remove(interaction: discord.Interaction, message_id: int, emoji: str):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("You need Manage Roles permission.", ephemeral=True)
        return
    async with db._lock:
        c = db._conn.cursor()
        c.execute("DELETE FROM reaction_roles WHERE guild_id = ? AND message_id = ? AND emoji = ?", (interaction.guild.id, message_id, emoji))
        db._conn.commit()
    await interaction.response.send_message(f"Removed reaction role for emoji {emoji} on message {message_id}.")

# Reaction roles event handling

@bot.event
async def on_raw_reaction_add(payload):
    if payload.guild_id is None or payload.user_id == bot.user.id:
        return
    async with db._lock:
        c = db._conn.cursor()
        c.execute("""
            SELECT role_id FROM reaction_roles
            WHERE guild_id = ? AND message_id = ? AND emoji = ?
        """, (payload.guild_id, payload.message_id, str(payload.emoji)))
        row = c.fetchone()
    if row:
        guild = bot.get_guild(payload.guild_id)
        if guild:
            role = guild.get_role(row["role_id"])
            member = guild.get_member(payload.user_id)
            if role and member:
                try:
                    await member.add_roles(role, reason="Reaction role assigned")
                except Exception:
                    pass

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.guild_id is None or payload.user_id == bot.user.id:
        return
    async with db._lock:
        c = db._conn.cursor()
        c.execute("""
            SELECT role_id FROM reaction_roles
            WHERE guild_id = ? AND message_id = ? AND emoji = ?
        """, (payload.guild_id, payload.message_id, str(payload.emoji)))
        row = c.fetchone()
    if row:
        guild = bot.get_guild(payload.guild_id)
        if guild:
            role = guild.get_role(row["role_id"])
            member = guild.get_member(payload.user_id)
            if role and member:
                try:
                    await member.remove_roles(role, reason="Reaction role removed")
                except Exception:
                    pass

# main.py — Part 4: Welcome/Leave, Starboard, Reminders, Custom Commands

@bot.event
async def on_member_join(member):
    config = await db.get_guild_config(member.guild.id)
    welcome_channel_id = config.get("welcome_channel")
    if welcome_channel_id:
        channel = member.guild.get_channel(welcome_channel_id)
        if channel:
            await channel.send(f"Welcome to the server, {member.mention}!")

@bot.event
async def on_member_remove(member):
    config = await db.get_guild_config(member.guild.id)
    leave_channel_id = config.get("leave_channel")
    if leave_channel_id:
        channel = member.guild.get_channel(leave_channel_id)
        if channel:
            await channel.send(f"{member} has left the server.")

@tree.command(name="set_welcome", description="Set the welcome channel")
@app_commands.describe(channel="Channel to send welcome messages")
async def set_welcome(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admin permission required.", ephemeral=True)
        return
    config = await db.get_guild_config(interaction.guild.id)
    config["welcome_channel"] = channel.id
    await db.set_guild_config(interaction.guild.id, config)
    await interaction.response.send_message(f"Welcome channel set to {channel.mention}")

@tree.command(name="set_leave", description="Set the leave channel")
@app_commands.describe(channel="Channel to send leave messages")
async def set_leave(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admin permission required.", ephemeral=True)
        return
    config = await db.get_guild_config(interaction.guild.id)
    config["leave_channel"] = channel.id
    await db.set_guild_config(interaction.guild.id, config)
    await interaction.response.send_message(f"Leave channel set to {channel.mention}")

# Starboard

STARBOARD_EMOJI = "⭐"
STARBOARD_THRESHOLD = 3

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    if str(reaction.emoji) == STARBOARD_EMOJI:
        message = reaction.message
        star_count = 0
        for react in message.reactions:
            if str(react.emoji) == STARBOARD_EMOJI:
                star_count = react.count
                break
        if star_count >= STARBOARD_THRESHOLD:
            config = await db.get_guild_config(message.guild.id)
            starboard_channel_id = config.get("starboard_channel")
            if starboard_channel_id:
                starboard_channel = message.guild.get_channel(starboard_channel_id)
                if starboard_channel:
                    embed = discord.Embed(description=message.content, color=discord.Color.gold(), timestamp=message.created_at)
                    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
                    embed.add_field(name="Jump to message", value=f"[Click Here]({message.jump_url})")
                    embed.set_footer(text=f"{star_count} {STARBOARD_EMOJI}")
                    await starboard_channel.send(embed=embed)

@tree.command(name="set_starboard", description="Set starboard channel")
@app_commands.describe(channel="Channel for starboard messages")
async def set_starboard(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admin permission required.", ephemeral=True)
        return
    config = await db.get_guild_config(interaction.guild.id)
    config["starboard_channel"] = channel.id
    await db.set_guild_config(interaction.guild.id, config)
    await interaction.response.send_message(f"Starboard channel set to {channel.mention}")

# Reminders

@tree.command(name="remindme", description="Set a reminder")
@app_commands.describe(time="Time in minutes from now", message="Reminder message")
async def remindme(interaction: discord.Interaction, time: int, message: str):
    if time <= 0:
        await interaction.response.send_message("Time must be positive.", ephemeral=True)
        return
    remind_time = int((datetime.datetime.utcnow() + datetime.timedelta(minutes=time)).timestamp())
    async with db._lock:
        c = db._conn.cursor()
        c.execute("INSERT INTO reminders (user_id, remind_time, message) VALUES (?, ?, ?)",
                  (interaction.user.id, remind_time, message))
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

# Custom commands

@tree.command(name="custom_add", description="Add a custom command")
@app_commands.describe(name="Command name", response="Response text")
async def custom_add(interaction: discord.Interaction, name: str, response: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admin permission required.", ephemeral=True)
        return
    name = name.lower()
    async with db._lock:
        c = db._conn.cursor()
        c.execute("INSERT OR REPLACE INTO custom_commands (guild_id, command_name, response) VALUES (?, ?, ?)",
                  (interaction.guild.id, name, response))
        db._conn.commit()
    await interaction.response.send_message(f"Custom command `{name}` added.")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.guild:
        # Check custom commands
        async with db._lock:
            c = db._conn.cursor()
            c.execute("SELECT response FROM custom_commands WHERE guild_id = ? AND command_name = ?", (message.guild.id, message.content.lower()))
            row = c.fetchone()
        if row:
            await message.channel.send(row["response"])
    await bot.process_commands(message)

# main.py — Part 5: Automod filters, slowmode, lock/unlock, on_ready and startup fixes

@tree.command(name="automod", description="Toggle automod features")
@app_commands.describe(feature="Feature to toggle (badwords, invites, links)", enabled="Enable or disable")
async def automod(interaction: discord.Interaction, feature: str, enabled: bool):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admin permission required.", ephemeral=True)
        return
    feature = feature.lower()
    valid_features = {"badwords", "invites", "links"}
    if feature not in valid_features:
        await interaction.response.send_message(f"Invalid feature. Valid: {', '.join(valid_features)}", ephemeral=True)
        return
    config = await db.get_guild_config(interaction.guild.id)
    automod = config.get("automod", {})
    automod[feature] = enabled
    config["automod"] = automod
    await db.set_guild_config(interaction.guild.id, config)
    await interaction.response.send_message(f"Automod feature `{feature}` set to {enabled}")

async def check_automod(message):
    config = await db.get_guild_config(message.guild.id)
    automod = config.get("automod", {})
    content = message.content.lower()

    badwords = {"badword1", "badword2"}  # Customize your bad words list

    if automod.get("badwords", False):
        if any(word in content for word in badwords):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention}, your message was removed for bad language.", delete_after=5)
            except Exception:
                pass
    if automod.get("invites", False):
        if re.search(r"(discord\.gg|discordapp\.com/invite)", content):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention}, invites are not allowed.", delete_after=5)
            except Exception:
                pass
    if automod.get("links", False):
        if re.search(r"https?://", content):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention}, links are not allowed.", delete_after=5)
            except Exception:
                pass

@bot.event
async def on_message_edit(before, after):
    if after.author.bot or not after.guild:
        return
    await check_automod(after)
    await bot.process_commands(after)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    await check_automod(message)
    # Custom commands already handled in Part 4 on_message event
    await bot.process_commands(message)

@tree.command(name="slowmode", description="Set slowmode delay in a channel")
@app_commands.describe(channel="Channel to set slowmode (optional)", seconds="Delay in seconds (0 to disable)")
async def slowmode(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None, seconds: int = 0):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You need Manage Channels permission.", ephemeral=True)
        return
    if seconds < 0 or seconds > 21600:
        await interaction.response.send_message("Seconds must be between 0 and 21600.", ephemeral=True)
        return
    target = channel or interaction.channel
    try:
        await target.edit(slowmode_delay=seconds)
        await interaction.response.send_message(f"Slowmode set to {seconds} seconds for {target.mention}.")
    except Exception as e:
        await interaction.response.send_message(f"Failed to set slowmode: {e}", ephemeral=True)

@tree.command(name="lock", description="Lock a channel")
@app_commands.describe(channel="Channel to lock (optional)")
async def lock(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You need Manage Channels permission.", ephemeral=True)
        return
    target = channel or interaction.channel
    overwrite = target.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    try:
        await target.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(f"Locked {target.mention}.")
    except Exception as e:
        await interaction.response.send_message(f"Failed to lock: {e}", ephemeral=True)

@tree.command(name="unlock", description="Unlock a channel")
@app_commands.describe(channel="Channel to unlock (optional)")
async def unlock(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You need Manage Channels permission.", ephemeral=True)
        return
    target = channel or interaction.channel
    overwrite = target.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    try:
        await target.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(f"Unlocked {target.mention}.")
    except Exception as e:
        await interaction.response.send_message(f"Failed to unlock: {e}", ephemeral=True)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    if not check_reminders.is_running():
        check_reminders.start()

if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
        exit(1)
    bot.run(TOKEN)




