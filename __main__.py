# __main__.py

import discord
from discord.ext import commands
import os

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
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
    with open("bot_token.txt", "r") as file:
        token = file.read().strip()
    await bot.start(token)

if __name__ == '__main__':
    asyncio.run(main())
