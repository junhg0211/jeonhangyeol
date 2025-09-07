# __main__.py

import discord
from discord.ext import commands
import os

# 봇의 접두사와 인텐트를 설정합니다.
# 인텐트는 봇이 어떤 이벤트에 반응할지를 정하는 권한과 같습니다.
# all()을 사용하면 대부분의 이벤트에 반응할 수 있습니다.
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# 봇이 준비되었을 때 실행되는 이벤트입니다.
@bot.event
async def on_ready():
    print(f'{bot.user.name}이(가) 준비되었습니다!')
    print('------------------------------------')

# cogs 폴더에 있는 모든 .py 파일을 찾아 불러옵니다.
async def load_cogs():
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            try:
                # 'cogs.economy'와 같은 형태로 모듈을 불러옵니다.
                await bot.load_extension(f'cogs.{filename[:-3]}')
                print(f'{filename[:-3]} cog가 로드되었습니다.')
            except Exception as e:
                print(f'{filename[:-3]} cog 로드 중 오류 발생: {e}')

# 비동기 함수를 실행하기 위한 부분입니다.
import asyncio

async def main():
    await load_cogs()
    # 여기에 봇 토큰을 입력하세요.
    # 중요: 봇 토큰은 절대 외부에 노출되어서는 안 됩니다.
    await bot.start('YOUR_BOT_TOKEN')

if __name__ == '__main__':
    asyncio.run(main())
