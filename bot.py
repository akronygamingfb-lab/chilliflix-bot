import discord
from discord.ext import commands
from discord.ui import Button, View
import yt_dlp
import asyncio
import lyricsgenius
import re

# --- Configuration ---
# এখানে আপনার Genius API টোকেনটি পেস্ট করুন
GENIUS_API_TOKEN = 'YOUR_GENIUS_API_TOKEN' 

# বট এবং কমান্ড প্রিফিক্স সেট করা
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# Genius API ক্লায়েন্ট শুরু করা
genius = lyricsgenius.Genius(GENIUS_API_TOKEN)

# ইউটিউব-ডিএল এর অপশন সেট করা
YDL_OPTIONS = {'format': 'bestaudio', 'noplaylist': False, 'quiet': True} # noplaylist is now False
FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}

# বিভিন্ন সার্ভারের জন্য ডেটা ম্যানেজ করার ডিকশনারি
song_queue = {}
active_filters = {}

# --- Interactive Player Controls (Buttons) ---
class PlayerControls(View):
    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx

    @discord.ui.button(label="Pause/Resume", style=discord.ButtonStyle.primary, emoji="⏯️")
    async def pause_resume(self, interaction: discord.Interaction, button: Button):
        voice_client = self.ctx.voice_client
        if not voice_client:
            await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)
            return
        if voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message("Resumed!", ephemeral=True)
        elif voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message("Paused!", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing right now.", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip(self, interaction: discord.Interaction, button: Button):
        voice_client = self.ctx.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.stop()
            await interaction.response.send_message("Skipped!", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing to skip.", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop(self, interaction: discord.Interaction, button: Button):
        voice_client = self.ctx.voice_client
        server_id = self.ctx.guild.id
        if server_id in song_queue:
            song_queue[server_id].clear()
        if voice_client and voice_client.is_playing():
            voice_client.stop()
            await interaction.response.send_message("Player stopped and queue cleared.", ephemeral=True)
        else:
            await interaction.response.send_message("Player is not active.", ephemeral=True)

# --- Main Bot Logic ---
current_song_info = {}

async def play_next(ctx):
    server_id = ctx.guild.id
    voice_client = ctx.voice_client
    
    if not voice_client:
        return

    if server_id in song_queue and len(song_queue[server_id]) > 0:
        next_song = song_queue[server_id].pop(0)
        current_song_info[server_id] = next_song
        
        ffmpeg_options = FFMPEG_OPTIONS.copy()
        if server_id in active_filters and active_filters[server_id]:
             ffmpeg_options['options'] += f" -af {active_filters[server_id]}"

        source = await discord.FFmpegOpusAudio.from_probe(next_song['source_url'], **ffmpeg_options)
        voice_client.play(source, after=lambda _: bot.loop.create_task(play_next(ctx)))
        
        embed = discord.Embed(
            title="🎶 Now Playing",
            description=f"**[{next_song['title']}]({next_song['webpage_url']})**",
            color=discord.Color.from_rgb(244, 63, 94)
        )
        embed.set_thumbnail(url=next_song['thumbnail'])
        embed.add_field(name="Uploader", value=next_song['uploader'], inline=True)
        embed.add_field(name="Duration", value=f"{int(next_song['duration'] // 60)}:{int(next_song['duration'] % 60):02d}", inline=True)
        
        await ctx.send(embed=embed, view=PlayerControls(ctx))
    else:
        current_song_info.pop(server_id, None)
        await asyncio.sleep(180) # Wait 3 minutes before leaving
        if voice_client and not voice_client.is_playing() and not voice_client.is_paused():
             await voice_client.disconnect()


@bot.event
async def on_ready():
    print(f'{bot.user.name} এখন অনলাইনে আছে!')
    print('-------------------')

# --- Commands ---

@bot.command(aliases=['p'])
async def play(ctx, *, search: str):
    if ctx.author.voice is None:
        await ctx.send("You need to be in a voice channel to use this command.")
        return
        
    voice_channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await voice_channel.connect()
    else:
        await ctx.voice_client.move_to(voice_channel)

    server_id = ctx.guild.id
    voice_client = ctx.voice_client

    search_query = ""
    # Check if the input is a URL
    if re.match(r'https?://(?:www\.)?.+', search):
        search_query = search
        await ctx.send(f"🔗 Processing link...")
    else:
        search_query = f"ytsearch:{search}"
        await ctx.send(f"🔎 Searching for `{search}`...")

    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        try:
            info = ydl.extract_info(search_query, download=False)
        except Exception as e:
            await ctx.send(f"Could not process the request. Please try again.\nError: `{e}`")
            return
    
    entries = []
    if 'entries' in info: # It's a playlist
        entries = info['entries']
    else: # It's a single video
        entries = [info]

    if not entries:
        await ctx.send("Could not find any songs from the provided input.")
        return

    if server_id not in song_queue:
        song_queue[server_id] = []
        
    for entry in entries:
        if 'url' not in entry: continue # Skip if entry is not a valid song
        song = {
            'source_url': entry['url'],
            'title': entry.get('title', 'Unknown Title'),
            'duration': entry.get('duration', 0),
            'thumbnail': entry.get('thumbnail'),
            'uploader': entry.get('uploader', 'Unknown Artist'),
            'webpage_url': entry.get('webpage_url')
        }
        song_queue[server_id].append(song)

    if len(entries) > 1:
        embed = discord.Embed(
            title=f"✅ Added Playlist to Queue",
            description=f"Added **{len(entries)} songs** from `{info.get('title', 'this playlist')}`.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            title="✅ Added to Queue",
            description=f"**{entries[0].get('title', 'Unknown Title')}**",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=entries[0].get('thumbnail'))
        await ctx.send(embed=embed)
    
    if not voice_client.is_playing():
        await play_next(ctx)

@bot.command(aliases=['q'])
async def queue(ctx):
    server_id = ctx.guild.id
    if server_id in song_queue and len(song_queue[server_id]) > 0:
        embed = discord.Embed(
            title="📜 Song Queue",
            color=discord.Color.from_rgb(159, 88, 228)
        )
        queue_list = ""
        for i, song in enumerate(song_queue[server_id][:10]): # Show first 10 songs
            queue_list += f"`{i+1}.` {song['title']}\n"
        if len(song_queue[server_id]) > 10:
            queue_list += f"\n...and {len(song_queue[server_id]) - 10} more."
        embed.description = queue_list
        await ctx.send(embed=embed)
    else:
        await ctx.send("The queue is empty.")

@bot.command(aliases=['np'])
async def nowplaying(ctx):
    server_id = ctx.guild.id
    if server_id in current_song_info:
        song = current_song_info[server_id]
        embed = discord.Embed(
            title="🎶 Now Playing",
            description=f"**[{song['title']}]({song['webpage_url']})**",
            color=discord.Color.from_rgb(244, 63, 94)
        )
        embed.set_thumbnail(url=song['thumbnail'])
        embed.add_field(name="Uploader", value=song['uploader'], inline=True)
        embed.add_field(name="Duration", value=f"{int(song['duration'] // 60)}:{int(song['duration'] % 60):02d}", inline=True)
        await ctx.send(embed=embed, view=PlayerControls(ctx))
    else:
        await ctx.send("Nothing is currently playing.")

@bot.command()
async def lyrics(ctx, *, song_search: str = None):
    song_title = ""
    if song_search:
        song_title = song_search
    elif ctx.guild.id in current_song_info:
        song_title = current_song_info[ctx.guild.id]['title']
    else:
        await ctx.send("No song is playing. Please provide a song name, e.g., `!lyrics Never Gonna Give You Up`")
        return

    await ctx.send(f"Searching lyrics for `{song_title}`...")
    try:
        song = genius.search_song(song_title)
        if song:
            lyrics_text = song.lyrics
            # Truncate lyrics if too long
            if len(lyrics_text) > 4000:
                lyrics_text = lyrics_text[:4000] + "\n\n[Lyrics truncated]"
            
            embed = discord.Embed(
                title=f"Lyrics for {song_title}",
                description=lyrics_text,
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send("Lyrics not found.")
    except Exception as e:
        await ctx.send(f"An error occurred while fetching lyrics: {e}")

# --- Audio Effects ---

@bot.command()
async def bassboost(ctx):
    server_id = ctx.guild.id
    active_filters[server_id] = "bass=g=10"
    await ctx.send("Bass boost filter enabled! The effect will apply to the next song.")

@bot.command()
async def nightcore(ctx):
    server_id = ctx.guild.id
    active_filters[server_id] = "atempo=1.25,asetrate=44100*1.25"
    await ctx.send("Nightcore filter enabled! The effect will apply to the next song.")

@bot.command()
async def slowed(ctx):
    server_id = ctx.guild.id
    active_filters[server_id] = "atempo=0.8"
    await ctx.send("Slowed filter enabled! The effect will apply to the next song.")

@bot.command()
async def resetfilters(ctx):
    server_id = ctx.guild.id
    active_filters[server_id] = None
    await ctx.send("All audio filters have been reset.")
    
# --- Other Commands ---

@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Left the voice channel.")
    else:
        await ctx.send("I'm not in a voice channel.")

# এখানে আপনার বটের টোকেনটি পেস্ট করুন
# 'YOUR_BOT_TOKEN' লেখাটি সরিয়ে আপনার আসল টোকেনটি দিন
bot.run('YOUR_BOT_TOKEN')

