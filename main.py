import os
import discord
from discord.ext import commands
from flask import Flask
from threading import Thread

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)  # Prefix needed for compatibility, but slash commands used

# Load cogs dynamically
COGS = [
    "cogs.setup",
    "cogs.moderation",
    "cogs.automod",
    "cogs.logging",
]

@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Error syncing commands: {e}")

def run_flask():
    app = Flask('')

    @app.route('/')
    def home():
        return "Bot is running!"

    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

if __name__ == "__main__":
    keep_alive()
    for cog in COGS:
        bot.load_extension(cog)
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN environment variable not set!")
        exit(1)
    bot.run(DISCORD_TOKEN)
