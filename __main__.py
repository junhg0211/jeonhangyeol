# __main__.py

import discord
from discord.ext import commands
import os
import sys

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'{bot.user.name}이(가) 준비되었습니다!')
    print('------------------------------------')

async def load_cogs():
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            try:
                await bot.load_extension(f'cogs.{filename[:-3]}')
                print(f'{filename[:-3]} cog가 로드되었습니다.')
            except Exception as e:
                print(f'{filename[:-3]} cog 로드 중 오류 발생: {e}')

import asyncio

async def main():
    await load_cogs()

    # Dry-run: skip network login, just show loaded cogs and commands
    if os.getenv("DRY_RUN") == "1" or "--dry-run" in sys.argv:
        print("[DRY RUN] 봇 로그인 없이 설정 확인만 진행합니다.")
        # List loaded extensions (cogs)
        print("로드된 코그:")
        for name in bot.cogs.keys():
            print(f" - {name}")
        # List slash commands in the tree
        print("등록된 슬래시 명령어:")
        for cmd in bot.tree.get_commands():
            print(f" /{cmd.name} - {cmd.description}")
        return

    with open("bot_token.txt", "r") as file:
        token = file.read().strip()
    await bot.start(token)

if __name__ == '__main__':
    asyncio.run(main())
