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
from typing import Optional


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
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS infractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            mod_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            timestamp INTEGER NOT NULL
        )
        """)
        self._conn.commit()

    async def get_guild_config(self, guild_id: int):
        async with self._lock:
            c = self._conn.cursor()
            c.execute("SELECT config_json FROM guild_configs WHERE guild_id = ?", (guild_id,))
            row = c.fetchone()
            if row:
                return json.loads(row["config_json"])
            return None

    async def set_guild_config(self, guild_id: int, config: dict):
        async with self._lock:
            c = self._conn.cursor()
            config_json = json.dumps(config)
            c.execute("""
            INSERT INTO guild_configs (guild_id, config_json) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET config_json=excluded.config_json
            """, (guild_id, config_json))
            self._conn.commit()

    async def add_infraction(self, guild_id, user_id, mod_id, action, reason, timestamp):
        async with self._lock:
            c = self._conn.cursor()
            c.execute("""
            INSERT INTO infractions (guild_id, user_id, mod_id, action, reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (guild_id, user_id, mod_id, action, reason, timestamp))
            self._conn.commit()

    async def get_infractions(self, guild_id, user_id):
        async with self._lock:
            c = self._conn.cursor()
            c.execute("""
            SELECT * FROM infractions WHERE guild_id = ? AND user_id = ? ORDER BY timestamp DESC
            """, (guild_id, user_id))
            rows = c.fetchall()
            return [dict(row) for row in rows]

db = Database()

# ---- FLASK KEEP ALIVE ----
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# ---- BOT SETUP ----
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)  # prefix needed for compatibility but slash only recommended

# ---- UTILS ----

def get_time_channel_name(timezone: str) -> str:
    tz = pytz.timezone(timezone)
    now = datetime.datetime.now(tz)
    time_str = now.strftime("%H:%M")
    return f"🕒 {time_str} ({timezone.split('/')[-1]})"

class YesNoView(discord.ui.View):
    def __init__(self, timeout=60):
        super().__init__(timeout=timeout)
        self.value = None

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()
        await interaction.response.defer()

# ---- COGS ----

class SetupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Run the interactive server setup wizard")
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return

        current_config = await db.get_guild_config(guild.id) or {}

        if current_config.get("setup_complete"):
            view = YesNoView()
            await interaction.followup.send(
                "Setup has already been run on this server. Run again to update?",
                view=view, ephemeral=True)
            await view.wait()
            if not view.value:
                await interaction.followup.send("Setup cancelled.", ephemeral=True)
                return

        config = current_config.copy()

        # 1. Prefix setup
        await interaction.followup.send(
            "Enter the custom command prefix you want (e.g., `!` or `/`). Type `default` to use `/` commands only:",
            ephemeral=True)
        try:
            msg = await self.bot.wait_for(
                "message", check=lambda m: m.author == interaction.user and m.channel == interaction.channel, timeout=60
            )
            prefix = msg.content.strip()
            if prefix.lower() == "default":
                prefix = "/"
            config["prefix"] = prefix
            await interaction.followup.send(f"Prefix set to `{prefix}`", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("Timeout: no prefix set. Using `/` commands by default.", ephemeral=True)
            config["prefix"] = "/"

        # 2. Join/Leave messages
        view = YesNoView()
        await interaction.followup.send("Enable custom join and leave messages?", view=view, ephemeral=True)
        await view.wait()
        config["join_leave_enabled"] = view.value
        if view.value:
            await interaction.followup.send("Enter your join message. Use `{user}` to mention the new member:", ephemeral=True)
            try:
                join_msg = await self.bot.wait_for(
                    "message", check=lambda m: m.author == interaction.user and m.channel == interaction.channel, timeout=120
                )
                config["join_message"] = join_msg.content
            except asyncio.TimeoutError:
                config["join_message"] = "Welcome {user}!"
            await interaction.followup.send("Enter your leave message. Use `{user}` to mention the leaving member:", ephemeral=True)
            try:
                leave_msg = await self.bot.wait_for(
                    "message", check=lambda m: m.author == interaction.user and m.channel == interaction.channel, timeout=120
                )
                config["leave_message"] = leave_msg.content
            except asyncio.TimeoutError:
                config["leave_message"] = "{user} has left the server."
        else:
            config["join_message"] = None
            config["leave_message"] = None

        # 3. Welcome image placeholder
        view = YesNoView()
        await interaction.followup.send("Enable welcome image/banner for new members? (placeholder)", view=view, ephemeral=True)
        await view.wait()
        config["welcome_image_enabled"] = view.value

        # 4. Rules embed + accept reaction
        view = YesNoView()
        await interaction.followup.send("Set up a rules channel with embed and reaction accept?", view=view, ephemeral=True)
        await view.wait()
        if view.value:
            rules_channel = discord.utils.get(guild.text_channels, name="📜rules")
            if not rules_channel:
                rules_channel = await guild.create_text_channel("📜rules")
            rules_embed = discord.Embed(
                title="Server Rules",
                description=(
                    "1. Be respectful\n"
                    "2. No spamming\n"
                    "3. No NSFW content\n"
                    "4. Follow Discord Terms of Service\n"
                    "React with ✅ to accept the rules and get access."
                ),
                color=discord.Color.blue()
            )
            rules_message = await rules_channel.send(embed=rules_embed)
            await rules_message.add_reaction("✅")
            try:
                await rules_message.pin()
            except discord.Forbidden:
                pass  # no perms to pin

            config["rules_channel_id"] = rules_channel.id
            config["rules_message_id"] = rules_message.id
        else:
            config["rules_channel_id"] = None
            config["rules_message_id"] = None

        # 5. Color roles
        view = YesNoView()
        await interaction.followup.send("Create color roles (e.g., 🔴 Red, 🔵 Blue)?", view=view, ephemeral=True)
        await view.wait()
        if view.value:
            colors = {
                "🔴 Red": 0xFF0000,
                "🟠 Orange": 0xFF7F00,
                "🟡 Yellow": 0xFFFF00,
                "🟢 Green": 0x00FF00,
                "🔵 Blue": 0x0000FF,
                "🟣 Purple": 0x8B00FF,
                "⚫ Black": 0x000000,
                "⚪ White": 0xFFFFFF,
            }
            created_roles = []
            for name, color in colors.items():
                role = discord.utils.get(guild.roles, name=name)
                if not role:
                    role = await guild.create_role(name=name, color=discord.Color(color))
                created_roles.append(role.id)
            config["color_roles"] = created_roles
        else:
            config["color_roles"] = []

        # 6. Timezone clock channel
        view = YesNoView()
        await interaction.followup.send("Create a timezone clock voice channel showing local time?", view=view, ephemeral=True)
        await view.wait()
        if view.value:
            await interaction.followup.send("Enter your timezone (e.g., America/New_York):", ephemeral=True)
            try:
                tz_msg = await self.bot.wait_for(
                    "message", check=lambda m: m.author == interaction.user and m.channel == interaction.channel, timeout=60
                )
                timezone = tz_msg.content.strip()
                if timezone not in pytz.all_timezones:
                    await interaction.followup.send("Invalid timezone. Skipping clock channel.", ephemeral=True)
                    timezone = None
            except asyncio.TimeoutError:
                await interaction.followup.send("Timeout. Skipping clock channel.", ephemeral=True)
                timezone = None

            if timezone:
                channel_name = get_time_channel_name(timezone)
                category = discord.utils.get(guild.categories, name="Information")
                if not category:
                    category = await guild.create_category("Information")
                voice_channel = discord.utils.get(guild.voice_channels, name=channel_name)
                if not voice_channel:
                    voice_channel = await guild.create_voice_channel(channel_name, category=category)
                config["clock_channel_id"] = voice_channel.id
                config["clock_timezone"] = timezone
            else:
                config["clock_channel_id"] = None
                config["clock_timezone"] = None
        else:
            config["clock_channel_id"] = None
            config["clock_timezone"] = None

        # Mark setup complete
        config["setup_complete"] = True

        # Save config
        await db.set_guild_config(guild.id, config)

        await interaction.followup.send("Setup complete! Configuration saved.", ephemeral=True)

# Moderation commands

class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.describe(member="Member to kick", reason="Reason for kick")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided"):
        try:
            await member.kick(reason=reason)
            await interaction.response.send_message(f"Kicked {member} | Reason: {reason}")
            await db.add_infraction(interaction.guild.id, member.id, interaction.user.id, "kick", reason, int(datetime.datetime.utcnow().timestamp()))
        except discord.Forbidden:
            await interaction.response.send_message("I do not have permission to kick this member.", ephemeral=True)

    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.describe(member="Member to ban", reason="Reason for ban")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided"):
        try:
            await member.ban(reason=reason)
            await interaction.response.send_message(f"Banned {member} | Reason: {reason}")
            await db.add_infraction(interaction.guild.id, member.id, interaction.user.id, "ban", reason, int(datetime.datetime.utcnow().timestamp()))
        except discord.Forbidden:
            await interaction.response.send_message("I do not have permission to ban this member.", ephemeral=True)

    @app_commands.command(name="unban", description="Unban a user by name#discrim")
    @app_commands.describe(user="Name#discriminator of the user to unban")
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user: str):
        banned_users = await interaction.guild.bans()
        name, discrim = user.split("#")
        for ban_entry in banned_users:
            if (ban_entry.user.name, ban_entry.user.discriminator) == (name, discrim):
                await interaction.guild.unban(ban_entry.user)
                await interaction.response.send_message(f"Unbanned {user}")
                return
        await interaction.response.send_message("User not found in ban list.", ephemeral=True)

    @app_commands.command(name="clear", description="Delete messages")
    @app_commands.describe(amount="Number of messages to delete")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction, amount: int = 5):
        deleted = await interaction.channel.purge(limit=amount + 1)
        await interaction.response.send_message(f"Deleted {len(deleted)-1} messages.", ephemeral=True)

    @app_commands.command(name="mute", description="Mute a member")
    @app_commands.describe(member="Member to mute")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def mute(self, interaction: discord.Interaction, member: discord.Member):
        guild = interaction.guild
        muted_role = discord.utils.get(guild.roles, name="Muted")
        if not muted_role:
            muted_role = await guild.create_role(name="Muted")
            for channel in guild.channels:
                await channel.set_permissions(muted_role, send_messages=False, speak=False)
        await member.add_roles(muted_role)
        await interaction.response.send_message(f"{member} has been muted.")

    @app_commands.command(name="unmute", description="Unmute a member")
    @app_commands.describe(member="Member to unmute")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):
        muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
        if muted_role in member.roles:
            await member.remove_roles(muted_role)
            await interaction.response.send_message(f"{member} has been unmuted.")
        else:
            await interaction.response.send_message("User is not muted.", ephemeral=True)

# Auto moderation (simple bad word filter example)

class AutomodCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bad_words = {"badword1", "badword2", "badword3"}  # replace with your list

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        config = await db.get_guild_config(message.guild.id)
        if not config:
            return
        if any(bw in message.content.lower() for bw in self.bad_words):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention}, your message contained a banned word.", delete_after=5)
            except discord.Forbidden:
                pass

# Logging Cog (logs deletions & edits)

class LoggingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None:
            return
        config = await db.get_guild_config(message.guild.id)
        if not config:
            return
        mod_log_id = config.get("mod_log_channel_id")
        if not mod_log_id:
            return
        channel = message.guild.get_channel(mod_log_id)
        if not channel:
            return
        embed = discord.Embed(title="Message Deleted", color=discord.Color.red())
        embed.add_field(name="Author", value=str(message.author), inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(name="Content", value=message.content or "[No Text]", inline=False)
        embed.timestamp = datetime.datetime.utcnow()
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.guild is None:
            return
        if before.content == after.content:
            return
        config = await db.get_guild_config(before.guild.id)
        if not config:
            return
        mod_log_id = config.get("mod_log_channel_id")
        if not mod_log_id:
            return
        channel = before.guild.get_channel(mod_log_id)
        if not channel:
            return
        embed = discord.Embed(title="Message Edited", color=discord.Color.orange())
        embed.add_field(name="Author", value=str(before.author), inline=True)
        embed.add_field(name="Channel", value=before.channel.mention, inline=True)
        embed.add_field(name="Before", value=before.content or "[No Text]", inline=False)
        embed.add_field(name="After", value=after.content or "[No Text]", inline=False)
        embed.timestamp = datetime.datetime.utcnow()
        await channel.send(embed=embed)

# Background task to update clock channel names every minute

@tasks.loop(minutes=1)
async def update_clock_channels():
    for guild in bot.guilds:
        config = await db.get_guild_config(guild.id)
        if not config:
            continue
        clock_channel_id = config.get("clock_channel_id")
        timezone = config.get("clock_timezone")
        if clock_channel_id and timezone:
            channel = guild.get_channel(clock_channel_id)
            if channel:
                new_name = get_time_channel_name(timezone)
                if channel.name != new_name:
                    try:
                        await channel.edit(name=new_name)
                    except discord.Forbidden:
                        pass

# On ready event

@bot.event
async def on_ready():
    print(f"Bot online as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    update_clock_channels.start()

# Register cogs

async def setup_cogs():
    await bot.add_cog(SetupCog(bot))
    await bot.add_cog(ModerationCog(bot))
    await bot.add_cog(AutomodCog(bot))
    await bot.add_cog(LoggingCog(bot))

async def main():
    keep_alive()
    await setup_cogs()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("ERROR: DISCORD_TOKEN environment variable not set!")
        return
    await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
