import json
import os
import aiohttp
import discord
from discord.ext import commands

# Load or initialize handle mapping
if os.path.exists("handles.json"):
    with open("handles.json", "r") as f:
        handles = json.load(f)
else:
    handles = {}

def save_handles():
    with open("handles.json", "w") as f:
        json.dump(handles, f, indent=4)

def setup(bot: commands.Bot):

    @bot.command()
    async def linkhandle(ctx, handle: str):
        user_id = str(ctx.author.id)

        # Prevent multiple users from linking same handle
        for uid, linked_handle in handles.items():
            if linked_handle.lower() == handle.lower() and uid != user_id:
                await ctx.send("❌ This Codeforces handle is already linked to another user.")
                return

        # Validate handle via Codeforces API
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://codeforces.com/api/user.info?handles={handle}") as resp:
                data = await resp.json()

        if data["status"] != "OK":
            await ctx.send("❌ Invalid Codeforces handle.")
            return

        handles[user_id] = handle
        save_handles()
        await ctx.send(f"✅ Linked your handle to `{handle}`!")

    @bot.command()
    async def myhandle(ctx):
        user_id = str(ctx.author.id)
        handle = handles.get(user_id)

        if handle:
            await ctx.send(f"🔗 Your linked handle is `{handle}`")
        else:
            await ctx.send("❌ You haven't linked a Codeforces handle yet. Use `!linkhandle <handle>`")

    @bot.command()
    async def unlinkhandle(ctx):
        user_id = str(ctx.author.id)
        if user_id in handles:
            removed = handles.pop(user_id)
            save_handles()
            await ctx.send(f"❌ Unlinked handle `{removed}` successfully.")
        else:
            await ctx.send("⚠️ You haven't linked any handle yet.")

def get_handle(discord_user_id: int) -> str | None:
    return handles.get(str(discord_user_id))