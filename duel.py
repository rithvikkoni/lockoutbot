import discord
from discord.ext import commands, tasks
from cflink import get_handle
import random
import aiohttp
import time
import asyncio
from cfapi import fetch_submissions, fetch_problemset

duel_sessions = {}

async def get_unsolved_problems(handle1, handle2, min_rating, max_rating, count):
    print("🌀 Fetching submissions")
    submissions1 = await fetch_submissions(handle1)
    submissions2 = await fetch_submissions(handle2)

    if submissions1 is None or submissions2 is None:
        print("❌ Submission fetch failed")
        return []

    problems = await fetch_problemset()

    filtered = []
    for p in problems:
        if "rating" not in p:
            continue
        if not (min_rating <= p["rating"] <= max_rating):
            continue
        pid = f"{p['contestId']}-{p['index']}"
        if pid in submissions1 or pid in submissions2:
            continue
        filtered.append(p)

    random.shuffle(filtered)
    return filtered[:count]

def setup(bot: commands.Bot):
    @bot.command()
    async def duel(ctx, opponent: discord.Member, min_rating: int = 800, max_rating: int = 2400, num: int = 5, time_min: int = 30):
        user1 = ctx.author
        user2 = opponent

        h1 = get_handle(user1.id)
        h2 = get_handle(user2.id)

        if not h1 or not h2:
            msg = "❌ Duel cannot start because:\n"
            if not h1:
                msg += f"- `{user1.display_name}` has not linked their Codeforces handle.\n"
            if not h2:
                msg += f"- `{user2.display_name}` has not linked their Codeforces handle.\n"
            msg += "Please use `!linkhandle <your_handle>` to proceed."
            await ctx.send(msg)
            return

        await ctx.send(f"🤝 Starting duel between `{user1.display_name}` and `{user2.display_name}`...")
        await ctx.send(f"🔍 Fetching {num} problems in range {min_rating}–{max_rating}...")

        problems = await get_unsolved_problems(h1, h2, min_rating, max_rating, num)

        if not problems:
            await ctx.send("❌ Duel failed. One or both users may have an invalid handle or no unsolved problems in that range.")
            return

        duel_sessions[(ctx.author.id, opponent.id)] = {
            "handles": (h1, h2),
            "problems": problems,
            "scores": {h1: 0, h2: 0},
            "solved": set(),
            "last_submission_time": {h1: 0, h2: 0},
            "start_time": time.time(),
            "time_limit": time_min * 60,
            "ended": False,
            "channel": ctx.channel
        }

        for i, p in enumerate(problems):
            name = p["name"]
            link = f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"
            await ctx.send(f"**Q{i+1}:** [{name}]({link})")

    @bot.command()
    async def checkscore(ctx):
        session_key = next((k for k in duel_sessions if ctx.author.id in k), None)
        if not session_key:
            await ctx.send("❌ You're not in an active duel.")
            return

        session = duel_sessions[session_key]
        if session["ended"]:
            await ctx.send("❗ This duel has already ended.")
            return

        h1, h2 = session["handles"]
        problems = session["problems"]
        solved = session["solved"]
        last_time = session["last_submission_time"]
        scores = session["scores"]
        time_limit = session["time_limit"]
        start = session["start_time"]

        submissions1 = await fetch_submissions(h1)
        submissions2 = await fetch_submissions(h2)

        if submissions1 is None or submissions2 is None:
            await ctx.send("⚠️ Couldn't fetch submissions at the moment. Try again.")
            return

        now = time.time()

        for p in problems:
            pid = f"{p['contestId']}-{p['index']}"
            if pid in solved:
                continue
            s1 = pid in submissions1
            s2 = pid in submissions2
            if s1 and not s2:
                scores[h1] += 1
                last_time[h1] = now
                solved.add(pid)
            elif s2 and not s1:
                scores[h2] += 1
                last_time[h2] = now
                solved.add(pid)
            elif s1 and s2:
                scores[h1] += 1
                scores[h2] += 1
                last_time[h1] = now
                last_time[h2] = now
                solved.add(pid)

        total = len(problems)
        if scores[h1] == total and scores[h2] < total:
            session["ended"] = True
            await ctx.send(f"🏆 `{h1}` wins by solving all problems first!")
        elif scores[h2] == total and scores[h1] < total:
            session["ended"] = True
            await ctx.send(f"🏆 `{h2}` wins by solving all problems first!")
        elif now - start > time_limit:
            session["ended"] = True
            if scores[h1] > scores[h2]:
                await ctx.send(f"⏰ Time up! `{h1}` wins with more problems solved!")
            elif scores[h2] > scores[h1]:
                await ctx.send(f"⏰ Time up! `{h2}` wins with more problems solved!")
            else:
                if last_time[h1] < last_time[h2]:
                    await ctx.send(f"🤏 Tie on problems! `{h1}` wins by faster final submission!")
                elif last_time[h2] < last_time[h1]:
                    await ctx.send(f"🤏 Tie on problems! `{h2}` wins by faster final submission!")
                else:
                    await ctx.send("🤝 It's a complete tie!")
        else:
            await ctx.send(
                f"📊 Current Score:\n"
                f"**{h1}**: {scores[h1]} pts\n"
                f"**{h2}**: {scores[h2]} pts\n"
                f"⏱ Time left: {int(time_limit - (now - start))} sec"
            )
    @bot.command()
    async def endduel(ctx):
        session_key = next((k for k in duel_sessions if ctx.author.id in k), None)
        if not session_key:
            await ctx.send("❌ You're not in an active duel.")
            return

        session = duel_sessions[session_key]
        if session["ended"]:
            await ctx.send("⚠️ This duel has already ended.")
            return

        session["ended"] = True
        h1, h2 = session["handles"]
        scores = session["scores"]

        await ctx.send(
            f"🛑 Duel ended manually by `{ctx.author.display_name}`.\n"
            f"📊 Final Score:\n"
            f"**{h1}**: {scores[h1]} pts\n"
            f"**{h2}**: {scores[h2]} pts"
        )
        
    @tasks.loop(seconds=10)
    async def auto_check_duels():
        for key, session in list(duel_sessions.items()):
            if session["ended"]:
                continue

            h1, h2 = session["handles"]
            problems = session["problems"]
            solved = session["solved"]
            last_time = session["last_submission_time"]
            scores = session["scores"]
            time_limit = session["time_limit"]
            start = session["start_time"]
            channel = session.get("channel")

            submissions1 = await fetch_submissions(h1)
            submissions2 = await fetch_submissions(h2)

            if submissions1 is None or submissions2 is None:
                print(f"❌ Submission fetch failed for {h1} or {h2}, skipping.")
                continue

            now = time.time()

            for p in problems:
                pid = f"{p['contestId']}-{p['index']}"
                if pid in solved:
                    continue
                s1 = pid in submissions1
                s2 = pid in submissions2
                if s1 and not s2:
                    scores[h1] += 1
                    last_time[h1] = now
                    solved.add(pid)
                elif s2 and not s1:
                    scores[h2] += 1
                    last_time[h2] = now
                    solved.add(pid)
                elif s1 and s2:
                    scores[h1] += 1
                    scores[h2] += 1
                    last_time[h1] = now
                    last_time[h2] = now
                    solved.add(pid)

            total = len(problems)
            if scores[h1] == total and scores[h2] < total:
                session["ended"] = True
                await channel.send(f"🏆 `{h1}` wins by solving all problems first!")
            elif scores[h2] == total and scores[h1] < total:
                session["ended"] = True
                await channel.send(f"🏆 `{h2}` wins by solving all problems first!")
            elif now - start > time_limit:
                session["ended"] = True
                if scores[h1] > scores[h2]:
                    await channel.send(f"⏰ Time up! `{h1}` wins with more problems solved!")
                elif scores[h2] > scores[h1]:
                    await channel.send(f"⏰ Time up! `{h2}` wins with more problems solved!")
                else:
                    if last_time[h1] < last_time[h2]:
                        await channel.send(f"🤏 Tie on problems! `{h1}` wins by faster final submission!")
                    elif last_time[h2] < last_time[h1]:
                        await channel.send(f"🤏 Tie on problems! `{h2}` wins by faster final submission!")
                    else:
                        await channel.send("🤝 It's a complete tie!")

    @bot.event
    async def on_ready():
        print(f"✅ Bot is online as {bot.user}")
        auto_check_duels.start()