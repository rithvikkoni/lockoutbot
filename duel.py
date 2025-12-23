# duel.py
import discord
from discord.ext import commands, tasks
from cflink import get_handle
import random
import time
import asyncio
from cfapi import fetch_submissions, fetch_problemset
import json
import os

BAD_TAGS = {"output-only", "*special problem", "challenge", "expression parsing"}
MAX_ACTIVE_DUELS = 20
# --- Config ---
DEFAULT_POINTS = [100, 200, 300, 400, 500]
RECENT_FILE = "recent_duels.json"
MAX_RECENT = 20
AUTO_CHECK_INTERVAL = 10  # seconds (auto-check loop interval)

# --- In-memory stores ---
duel_sessions = {}
pending_duel_queue = []
recent_duels = []

if os.path.exists(RECENT_FILE):
    try:
        with open(RECENT_FILE, "r") as f:
            recent_duels = json.load(f)
    except:
        recent_duels = []

def save_recent():
    with open(RECENT_FILE, "w") as f:
        json.dump(recent_duels[-MAX_RECENT:], f, indent=2)

# --- Helpers ---
def _session_key(a, b):
    return (min(a, b), max(a, b))

def _format_time_left(seconds_left: float) -> str:
    if seconds_left < 0:
        seconds_left = 0
    mins = int(seconds_left) // 60
    secs = int(seconds_left) % 60
    return f"{mins}m {secs}s"

async def find_problem_for_rating(problems, rating, excluded_pids, submissions1, submissions2):
    candidates = [p for p in problems if p.get("rating") == rating]
    random.shuffle(candidates)
    for p in candidates:
        pid = f"{p['contestId']}-{p['index']}"
        tags = set(p.get("tags", []))
        if tags & BAD_TAGS:
            continue

        if pid in excluded_pids:
            continue
        if pid in submissions1 or pid in submissions2:
            continue

        return p

    return None

async def get_unsolved_problems_for_ratings(handle1, handle2, ratings_list):
    # DIRECT calls only — NO CACHE
    submissions1 = await fetch_submissions(handle1)
    submissions2 = await fetch_submissions(handle2)
    if submissions1 is None or submissions2 is None:
        return None

    problems = await fetch_problemset()
    if not problems:
        return None

    selected = []
    excluded = set()
    for r in ratings_list:
        p = await find_problem_for_rating(problems, r, excluded, submissions1, submissions2)
        if p is None:
            # fallback search by nearby ratings
            found = None
            for offset in [100, -100, 200, -200, 300, -300, 400, -400, 500, -500]:
                target = r + offset
                found = await find_problem_for_rating(problems, target, excluded, submissions1, submissions2)
                if found:
                    break
            if not found:
                return None
            p = found
        pid = f"{p['contestId']}-{p['index']}"
        excluded.add(pid)
        selected.append(p)
    return selected

def _record_recent(session):
    rec = {
        "players": session["players"],
        "handles": session["handles"],
        "ratings": session["ratings"],
        "points": session["points"],
        "scores": session["scores"],
        "per_problem": session["per_problem"],
        "start_time": session["start_time"],
        "end_time": time.time()
    }
    recent_duels.append(rec)
    save_recent()

# --- Main setup ---
def setup(bot: commands.Bot):

    @bot.command()
    async def duel(ctx, *args):
        """
        Third-person (or single-mention) duel start supported:
        - !duel @p1 @p2 base_rating time_min
        - !duel @p1 @p2 min max num time_min
        - !duel @p2 base_rating time_min  (you vs @p2)
        """
        mentions = ctx.message.mentions
        if not mentions:
            await ctx.send(embed=discord.Embed(description="❌ Provide at least one mention (opponent).", color=discord.Color.red()))
            return

        # Determine players
        if len(mentions) == 1:
            p1 = ctx.author
            p2 = mentions[0]
            tail_start = 1
        else:
            p1 = mentions[0]
            p2 = mentions[1]
            tail_start = 2

        # parse numeric args from remaining tokens (simple approach)
        tokens = ctx.message.content.split()
        tail = tokens[1 + tail_start:] if len(tokens) > 1 + tail_start else []

        try:
            if len(tail) == 2:
                base_rating = int(tail[0])
                time_min = int(tail[1])
                num = 5
                ratings_list = [base_rating + i * 100 for i in range(num)]
            elif len(tail) == 4:
                min_rating = int(tail[0]); max_rating = int(tail[1]); num = int(tail[2]); time_min = int(tail[3])
                if num == 1:
                    ratings_list = [min_rating]
                else:
                    step = (max_rating - min_rating) // (num - 1)
                    ratings_list = [min_rating + i * step for i in range(num)]
            elif len(tail) == 0:
                min_rating = 800; max_rating = 2400; num = 5; time_min = 30
                step = (max_rating - min_rating) // (num - 1)
                ratings_list = [min_rating + i * step for i in range(num)]
            else:
                await ctx.send(embed=discord.Embed(description="❌ Invalid numeric arguments. Use base/time or min max num time.", color=discord.Color.red()))
                return
        except ValueError:
            await ctx.send(embed=discord.Embed(description="❌ Invalid numeric args.", color=discord.Color.red()))
            return

        h1 = get_handle(p1.id); h2 = get_handle(p2.id)
        if not h1 or not h2:
            msg = "❌ Duel cannot start because:\n"
            if not h1:
                msg += f"- `{p1.display_name}` has no registered handle.\n"
            if not h2:
                msg += f"- `{p2.display_name}` has no registered handle.\n"
            msg += "Admins can register handles with `!register @user handle`."
            await ctx.send(embed=discord.Embed(description=msg, color=discord.Color.orange()))
            return
        # Enforce max active duels
        if len(duel_sessions) >= MAX_ACTIVE_DUELS:
            await ctx.send(embed=discord.Embed(
                title="⏳ Duel Limit Reached",
                description=(
                    f"Maximum **{MAX_ACTIVE_DUELS} active duels** are currently running.\n"
                    "Please wait for a duel to finish and try again."
                ),
                color=discord.Color.orange()
            ))
            return

        key = _session_key(p1.id, p2.id)
        if key in duel_sessions:
            await ctx.send(embed=discord.Embed(description="❌ A duel between these players is already active.", color=discord.Color.red()))
            return

        # prepare duel: fetch problems (direct calls only)
        await ctx.send(embed=discord.Embed(description=f"🔍 Fetching problems for {p1.display_name} vs {p2.display_name} ...", color=discord.Color.blue()))
        problems = await get_unsolved_problems_for_ratings(h1, h2, ratings_list)
        if not problems:
            await ctx.send(embed=discord.Embed(description="❌ Could not find enough unsolved problems for these players.", color=discord.Color.red()))
            return

        points = DEFAULT_POINTS.copy() if len(problems) == 5 else [100*(i+1) for i in range(len(problems))]
        pids = [f"{p['contestId']}-{p['index']}" for p in problems]
        session = {
            "players": (p1.id, p2.id),
            "handles": (h1, h2),
            "problems": problems,
            "problems_pids": pids,
            "ratings": ratings_list,
            "points": points,
            "scores": {h1: 0, h2: 0},
            "score_times": {h1: None, h2: None},
            "per_problem": {pid: {"solved_by": None, "first_time": None} for pid in pids},
            "start_time": time.time(),
            "time_limit": time_min * 60,
            "ended": False,
            "channel_id": ctx.channel.id
        }
        duel_sessions[key] = session

        # announce
        embed = discord.Embed(title="🤝 Duel Started", color=discord.Color.green())
        embed.description = f"{p1.mention}  vs  {p2.mention}"
        for i, p in enumerate(problems):
            link = f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"
            embed.add_field(name=f"Q{i+1} [{ratings_list[i]}] — {points[i]} pts",
                            value=f"[{p['name']}]({link})\n`{pids[i]}`", inline=False)
        embed.set_footer(text=f"Time limit: {time_min} minutes. Players report solves with `!update`.")
        await ctx.send(embed=embed)

    @bot.command(name="update")
    async def update_cmd(ctx):
        """
        Check both players' Codeforces submissions and update any newly accepted unsolved duel problems.
        Announces only when there are new awards (embed) and tags both players.
        """
        session_key = next((k for k in duel_sessions if ctx.author.id in k), None)
        if not session_key:
            await ctx.send(embed=discord.Embed(description="❌ You're not in an active duel.", color=discord.Color.red()))
            return
        session = duel_sessions[session_key]
        if session["ended"]:
            await ctx.send(embed=discord.Embed(description="❗ This duel has already ended.", color=discord.Color.orange()))
            return

        # direct fetch (no cache)
        h1, h2 = session["handles"]
        subs1 = await fetch_submissions(h1)
        subs2 = await fetch_submissions(h2)
        if subs1 is None or subs2 is None:
            await ctx.send(embed=discord.Embed(description="⚠️ Could not fetch submissions from Codeforces now. Try again later.", color=discord.Color.orange()))
            return

        newly_awarded = []
        now = time.time()
        for idx, pid in enumerate(session["problems_pids"]):
            if session["per_problem"][pid]["solved_by"] is not None:
                continue
            t1 = subs1.get(pid)
            t2 = subs2.get(pid)
            if not t1 and not t2:
                continue
            if t1 and t2:
                if t1 < t2:
                    award = h1; ft = t1
                elif t2 < t1:
                    award = h2; ft = t2
                else:
                    award = "tie"; ft = t1
            elif t1:
                award = h1; ft = t1
            else:
                award = h2; ft = t2

            if award == "tie":
                session["per_problem"][pid]["solved_by"] = "tie"
                session["per_problem"][pid]["first_time"] = ft
                newly_awarded.append((idx, pid, "tie", 0))
            else:
                pts = session["points"][idx] if idx < len(session["points"]) else 100*(idx+1)
                session["per_problem"][pid]["solved_by"] = award
                session["per_problem"][pid]["first_time"] = ft
                session["scores"][award] = session["scores"].get(award, 0) + pts
                session["score_times"].setdefault(award, now)
                newly_awarded.append((idx, pid, award, pts))

        if not newly_awarded:
            await ctx.send(embed=discord.Embed(description="ℹ️ No new accepted submissions found for either player among unsolved duel problems.", color=discord.Color.blue()))
            return

        # announce
        p0, p1_ids = session["players"][0], session["players"][1]
        embed = discord.Embed(title="✅ Duel Update — New Solves", color=discord.Color.green())
        embed.description = f"<@{p0}>  vs  <@{p1_ids}>"
        for idx, pid, award_handle, pts in newly_awarded:
            p = session["problems"][idx]
            # if solved -> show name as plain text + LOCKED (no link)
            if award_handle == "tie":
                embed.add_field(
                    name=f"Q{idx+1} [{session['ratings'][idx]}] — tie",
                    value=f"{p['name']}\n`{pid}`\nNo points awarded (same second). 🔒 LOCKED",
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"Q{idx+1} [{session['ratings'][idx]}] — awarded {pts} pts",
                    value=f"{p['name']}\n`{pid}`\nSolved by `{award_handle}`. 🔒 LOCKED",
                    inline=False
                )

        embed.add_field(name="Points", value=f"**{session['handles'][0]}**: {session['scores'].get(session['handles'][0],0)} pts\n**{session['handles'][1]}**: {session['scores'].get(session['handles'][1],0)} pts", inline=False)
        time_left = session["time_limit"] - (time.time() - session["start_time"])
        embed.set_footer(text=f"Time left: {_format_time_left(time_left)}")
        ch = bot.get_channel(session["channel_id"])
        await ch.send(embed=embed)

        await _maybe_finalize(session_key, session, bot)

    @bot.command()
    async def duel_status(ctx):
        """
        Show current duel status AS-IS (no automatic update).
        """
        session_key = next((k for k in duel_sessions if ctx.author.id in k), None)
        if not session_key:
            await ctx.send(embed=discord.Embed(description="❌ You're not in an active duel.", color=discord.Color.red()))
            return
        session = duel_sessions[session_key]

        # Status embed WITHOUT fetching/updating submissions
        embed = discord.Embed(title="📊 Duel Status", color=discord.Color.blue())
        embed.description = f"<@{session['players'][0]}>  vs  <@{session['players'][1]}>"
        for i, pid in enumerate(session["problems_pids"]):
            p = session["problems"][i]
            info = session["per_problem"].get(pid, {})
            solved_by = info.get("solved_by")
            if solved_by == "tie":
                status = "Tie — no points"
                value = f"{p['name']}\n`{pid}`\n{status} 🔒 LOCKED"
            elif solved_by:
                status = f"Solved by `{solved_by}`"
                value = f"{p['name']}\n`{pid}`\n{status} 🔒 LOCKED"
            else:
                # unsolved: show link
                link = f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"
                status = "Unsolved"
                value = f"[{p['name']}]({link})\n`{pid}`\n{status}"
            embed.add_field(name=f"Q{i+1} [{session['ratings'][i]}] — {session['points'][i]} pts", value=value, inline=False)

        embed.add_field(name="Points", value=f"**{session['handles'][0]}**: {session['scores'].get(session['handles'][0],0)} pts\n**{session['handles'][1]}**: {session['scores'].get(session['handles'][1],0)} pts", inline=False)
        embed.set_footer(text=f"Time left: {_format_time_left(session['time_limit'] - (time.time() - session['start_time']))}")
        ch = bot.get_channel(session["channel_id"])
        await ch.send(embed=embed)

    @bot.command()
    async def problems(ctx):
        session_key = next((k for k in duel_sessions if ctx.author.id in k), None)
        if not session_key:
            await ctx.send(embed=discord.Embed(description="❌ You're not in an active duel.", color=discord.Color.red()))
            return
        session = duel_sessions[session_key]
        embed = discord.Embed(title="🧾 Duel Problems", color=discord.Color.green())
        for i, p in enumerate(session["problems"]):
            pid = session["problems_pids"][i]
            info = session["per_problem"].get(pid, {})
            solved_by = info.get("solved_by")
            if solved_by:
                # locked, no link
                embed.add_field(name=f"Q{i+1} [{session['ratings'][i]}] — {session['points'][i]} pts",
                                value=f"{p['name']}\n`{pid}`\nSolved — 🔒 LOCKED", inline=False)
            else:
                link = f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"
                embed.add_field(name=f"Q{i+1} [{session['ratings'][i]}] — {session['points'][i]} pts",
                                value=f"[{p['name']}]({link}) — `{pid}`", inline=False)
        await ctx.send(embed=embed)

    @bot.command()
    async def endduel(ctx):
        session_key = next((k for k in duel_sessions if ctx.author.id in k), None)
        if not session_key:
            await ctx.send(embed=discord.Embed(description="❌ You're not in an active duel.", color=discord.Color.red()))
            return
        session = duel_sessions[session_key]
        if session["ended"]:
            await ctx.send(embed=discord.Embed(description="⚠️ This duel has already ended.", color=discord.Color.orange()))
            return
        session["ended"] = True
        await _finalize_and_announce(session)

    @bot.command()
    async def commands(ctx):
        """Show brief command guide."""
        embed = discord.Embed(title="📚 Bot Commands", color=discord.Color.teal())
        embed.add_field(name="Linking (admin)", value="`!register @user handle` — register CF handle\n`!unregister @user` — remove registration", inline=False)
        embed.add_field(name="Duel (start)", value="`!duel @p1 @p2 base_rating time_min` — start duel", inline=False)
        embed.add_field(name="Report / Status", value="`!update` — update solves; `!duel_status` — show status (no update); `!problems` — list problems; `!endduel` — end duel", inline=False)
        embed.add_field(name="History", value="`!recent` — show recent duels", inline=False)
        await ctx.send(embed=embed)

    async def _maybe_finalize(session_key, session, bot_ref):
        now = time.time()
        if all(session["per_problem"][pid]["solved_by"] is not None for pid in session["problems_pids"]) or (now - session["start_time"] > session["time_limit"]):
            session["ended"] = True
            await _finalize_and_announce(session)

    async def _finalize_and_announce(session):
        h1, h2 = session["handles"]
        pids = session["problems_pids"]
        problems = session["problems"]
        scores = session["scores"]
        channel = session.get("channel") or bot.get_channel(session["channel_id"])

        embed = discord.Embed(title="🏁 Duel Finished — Final Results", color=discord.Color.green())
        players = session.get("players", None)
        if players:
            u1, u2 = players
            embed.description = f"{'<@' + str(u1) + '>'} vs {'<@' + str(u2) + '>'}"

        for i, pid in enumerate(pids):
            p = problems[i]
            info = session["per_problem"].get(pid, {})
            solved_by = info.get("solved_by")
            if solved_by == "tie":
                value = f"{p['name']}\n`{pid}`\nTie — no points 🔒 LOCKED"
            elif solved_by:
                value = f"{p['name']}\n`{pid}`\nSolved by `{solved_by}` 🔒 LOCKED"
            else:
                link = f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"
                value = f"[{p['name']}]({link}) — Unsolved"
            embed.add_field(name=f"Q{i+1} [{session['ratings'][i]}] — {session['points'][i]} pts", value=value, inline=False)

        embed.add_field(name="Final Points", value=f"**{h1}**: {scores[h1]} pts\n**{h2}**: {scores[h2]} pts\n", inline=False)

        if scores[h1] > scores[h2]:
            embed.add_field(name="Winner", value=f"`{h1}`", inline=False)
        elif scores[h2] > scores[h1]:
            embed.add_field(name="Winner", value=f"`{h2}`", inline=False)
        else:
            lt1 = session["score_times"].get(h1) or float("inf")
            lt2 = session["score_times"].get(h2) or float("inf")
            if lt1 < lt2:
                embed.add_field(name="Tie-break Winner", value=f"`{h1}` (earlier to reach final score)", inline=False)
            elif lt2 < lt1:
                embed.add_field(name="Tie-break Winner", value=f"`{h2}` (earlier to reach final score)", inline=False)
            else:
                embed.add_field(name="Result", value="Tie", inline=False)

        if channel:
            await channel.send(embed=embed)

        _record_recent(session)
        # cleanup
        key_to_remove = next((k for k, v in duel_sessions.items() if v is session), None)
        if key_to_remove:
            del duel_sessions[key_to_remove]

    async def _update_scores(session):
        """
        Silent update: fetch submissions and update session scores & solved set.
        Returns a list of newly solved info (pid, idx, s1, s2) and ended flag.
        """
        h1, h2 = session["handles"]
        pids = session["problems_pids"]
        scores = session["scores"]
        score_times = session["score_times"]

        submissions1 = await fetch_submissions(h1)
        submissions2 = await fetch_submissions(h2)
        if submissions1 is None or submissions2 is None:
            return [], False

        now = time.time()
        new_solved = []

        for idx, pid in enumerate(pids):
            if session["per_problem"][pid]["solved_by"] is not None:
                continue
            s1 = pid in submissions1
            s2 = pid in submissions2
            if s1 and not s2:
                pts = session["points"][idx] if idx < len(session["points"]) else 100*(idx+1)
                session["per_problem"][pid]["solved_by"] = h1
                session["per_problem"][pid]["first_time"] = submissions1.get(pid)
                scores[h1] += pts
                score_times.setdefault(h1, now)
                new_solved.append((pid, idx, True, False))
            elif s2 and not s1:
                pts = session["points"][idx] if idx < len(session["points"]) else 100*(idx+1)
                session["per_problem"][pid]["solved_by"] = h2
                session["per_problem"][pid]["first_time"] = submissions2.get(pid)
                scores[h2] += pts
                score_times.setdefault(h2, now)
                new_solved.append((pid, idx, False, True))
            elif s1 and s2:
                # both have ACs: decide via timestamps; award earlier; if equal, mark tie (no points)
                t1 = submissions1.get(pid)
                t2 = submissions2.get(pid)
                if t1 and t2:
                    if t1 < t2:
                        pts = session["points"][idx] if idx < len(session["points"]) else 100*(idx+1)
                        session["per_problem"][pid]["solved_by"] = h1
                        session["per_problem"][pid]["first_time"] = t1
                        scores[h1] += pts
                        score_times.setdefault(h1, now)
                        new_solved.append((pid, idx, True, False))
                    elif t2 < t1:
                        pts = session["points"][idx] if idx < len(session["points"]) else 100*(idx+1)
                        session["per_problem"][pid]["solved_by"] = h2
                        session["per_problem"][pid]["first_time"] = t2
                        scores[h2] += pts
                        score_times.setdefault(h2, now)
                        new_solved.append((pid, idx, False, True))
                    else:
                        session["per_problem"][pid]["solved_by"] = "tie"
                        session["per_problem"][pid]["first_time"] = t1
                        new_solved.append((pid, idx, True, True))
                else:
                    # fallback: award both (rare)
                    pts = session["points"][idx] if idx < len(session["points"]) else 100*(idx+1)
                    session["per_problem"][pid]["solved_by"] = h1 + "," + h2
                    session["per_problem"][pid]["first_time"] = now
                    scores[h1] += pts
                    scores[h2] += pts
                    score_times.setdefault(h1, now)
                    score_times.setdefault(h2, now)
                    new_solved.append((pid, idx, True, True))

        # check end
        ended = False
        if all(session["per_problem"][pid]["solved_by"] is not None for pid in pids):
            ended = True
        elif time.time() - session["start_time"] > session["time_limit"]:
            ended = True

        return new_solved, ended

    @tasks.loop(seconds=AUTO_CHECK_INTERVAL)
    async def auto_check_duels():
        for key, session in list(duel_sessions.items()):
            if session["ended"]:
                continue
            try:
                new_solved_info, ended_flag = await _update_scores(session)
                if new_solved_info:
                    channel = session.get("channel") or bot.get_channel(session["channel_id"])
                    await _send_status_embed(session, channel, mention_players=True, new_solved_info=new_solved_info)
                if ended_flag:
                    session["ended"] = True
                    await _finalize_and_announce(session)
            except Exception as e:
                print("❌ Error during auto-check:", e)

    @bot.event
    async def on_ready():
        print(f"✅ Bot is online as {bot.user}")

        if not duel_timer_watcher.is_running():
            duel_timer_watcher.start()
        # Auto-check disabled to make updates manual-only.
        # If you want auto-check back, uncomment the next two lines.
        # if not auto_check_duels.is_running():
        #     auto_check_duels.start()

    @tasks.loop(seconds=5)
    async def duel_timer_watcher():
        now = time.time()
        for key, session in list(duel_sessions.items()):
            if session["ended"]:
                continue

            if now - session["start_time"] >= session["time_limit"]:
                session["ended"] = True
                await _finalize_and_announce(session)

    @bot.command()
    async def recent(ctx):
        try:
            with open("recent_duels.json", "r") as f:
                duels = json.load(f)
        except FileNotFoundError:
            await ctx.send("❌ No duel history found.")
            return

        if not duels:
            await ctx.send("📭 No completed duels yet.")
            return

        embed = discord.Embed(
            title="🕒 Recent Duels",
            color=discord.Color.blurple()
        )

        for d in duels[-20:][::-1]:  # last 20, newest first
            h1, h2 = d["handles"]
            s1 = d["scores"].get(h1, 0)
            s2 = d["scores"].get(h2, 0)

            # Winner logic
            if s1 > s2:
                winner = h1
            elif s2 > s1:
                winner = h2
            else:
                winner = "Draw"

            duration = int(d["end_time"] - d["start_time"])

            embed.add_field(
                name=f" {h1} vs {h2} ",
                value=(
                    f"  **Winner:** {winner}\n"
                    f"  **Score:** {s1} – {s2}\n"
                    f"  **Duration:** {duration // 60}m {duration % 60}s"
                ),
                inline=False
            )

        await ctx.send(embed=embed)



