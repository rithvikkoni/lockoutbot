import discord
from discord.ext import commands
from config import BOT_TOKEN
import cflink
import duel
import teams

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")

# Register all modular command sets
cflink.setup(bot)
duel.setup(bot)
teams.setup(bot)

# Start the bot
bot.run(BOT_TOKEN)