import discord
from discord.ext import commands

teams = {}  # team_name -> list of user IDs
user_to_team = {}  # user_id -> team_name

def setup(bot: commands.Bot):
    @bot.command()
    async def createteam(ctx, team_name: str):
        if ctx.author.id in user_to_team:
            await ctx.send("❌ You are already in a team. Leave it first using `!leaveteam`.")
            return

        if team_name in teams:
            await ctx.send("❌ A team with this name already exists.")
            return

        teams[team_name] = [ctx.author.id]
        user_to_team[ctx.author.id] = team_name
        await ctx.send(f"✅ Team `{team_name}` created and you have joined it.")

    @bot.command()
    async def jointeam(ctx, team_name: str):
        if ctx.author.id in user_to_team:
            await ctx.send("❌ You are already in a team. Leave it first using `!leaveteam`.")
            return

        if team_name not in teams:
            await ctx.send("❌ No such team exists. Create one with `!createteam`.")
            return

        teams[team_name].append(ctx.author.id)
        user_to_team[ctx.author.id] = team_name
        await ctx.send(f"✅ You joined team `{team_name}`.")

    @bot.command()
    async def leaveteam(ctx):
        user_id = ctx.author.id
        if user_id not in user_to_team:
            await ctx.send("❌ You are not in any team.")
            return

        team_name = user_to_team[user_id]
        teams[team_name].remove(user_id)
        del user_to_team[user_id]

        # delete team if empty
        if len(teams[team_name]) == 0:
            del teams[team_name]
            await ctx.send(f"👋 You left team `{team_name}`. The team has been disbanded.")
        else:
            await ctx.send(f"👋 You left team `{team_name}`.")

    @bot.command()
    async def myteam(ctx):
        if ctx.author.id not in user_to_team:
            await ctx.send("ℹ️ You are not in any team.")
        else:
            team = user_to_team[ctx.author.id]
            members = teams[team]
            names = []
            for uid in members:
                user = await bot.fetch_user(uid)
                names.append(user.display_name)
            await ctx.send(f"👥 Your team: `{team}`\nMembers: {', '.join(names)}")

    @bot.command()
    async def listteams(ctx):
        if not teams:
            await ctx.send("📭 No teams have been created yet.")
            return

        msg = "**📝 Active Teams:**\n"
        for name, members in teams.items():
            msg += f"- `{name}` ({len(members)} members)\n"
        await ctx.send(msg)