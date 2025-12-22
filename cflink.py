# cflink.py
import json
import os
import discord
from discord.ext import commands
from cfapi import fetch_submissions

HANDLES_FILE = "handles.json"

if os.path.exists(HANDLES_FILE):
    with open(HANDLES_FILE, "r") as f:
        try:
            handles = json.load(f)
        except:
            handles = {}
else:
    handles = {}

def save_handles():
    with open(HANDLES_FILE, "w") as f:
        json.dump(handles, f, indent=4)

def setup(bot: commands.Bot):

    @bot.command()
    @commands.has_permissions(manage_guild=True)
    async def register(ctx, member: discord.Member, handle: str):
        """Admin only: register @user handle"""
        user_id = str(member.id)
        # prevent duplicate handle mapping
        for uid, linked in handles.items():
            if linked.lower() == handle.lower() and uid != user_id:
                await ctx.send(embed=discord.Embed(description="❌ This Codeforces handle is already linked to another user.", color=discord.Color.red()))
                return

        # validate handle via CF API (uses cfapi rate-limiter)
        subs = await fetch_submissions(handle)
        if subs is None:
            await ctx.send(embed=discord.Embed(description="❌ Invalid Codeforces handle or API error.", color=discord.Color.red()))
            return

        handles[user_id] = handle
        save_handles()
        await ctx.send(embed=discord.Embed(description=f"✅ Registered `{handle}` for {member.mention}.", color=discord.Color.green()))

    @register.error
    async def register_error(ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=discord.Embed(description="❌ You need Manage Server permission to use this command.", color=discord.Color.red()))
        else:
            raise error

    @bot.command()
    @commands.has_permissions(manage_guild=True)
    async def unregister(ctx, member: discord.Member):
        """Admin only: remove registered handle for a user"""
        user_id = str(member.id)
        if user_id in handles:
            removed = handles.pop(user_id)
            save_handles()
            await ctx.send(embed=discord.Embed(description=f"✅ Unregistered `{removed}` for {member.mention}.", color=discord.Color.green()))
        else:
            await ctx.send(embed=discord.Embed(description="⚠️ That user has no registered handle.", color=discord.Color.orange()))

def get_handle(discord_user_id: int) -> str | None:
    return handles.get(str(discord_user_id))
