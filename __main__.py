# __main__.py

import discord
from discord.ext import commands
import os
import sys
import asyncio
import signal

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    # Global sync (may take time to propagate)
    try:
        await bot.tree.sync()
        print('[commands] Global sync complete')
    except Exception as e:
        print(f'[commands] Global sync error: {e}')

    # Per-guild fast sync so new commands appear immediately
    for g in bot.guilds:
        try:
            await bot.tree.sync(guild=discord.Object(id=g.id))
            print(f"[commands] Guild sync: {g.name} ({g.id})")
        except Exception as e:
            print(f"[commands] Guild sync failed for {g.id}: {e}")

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
    try:
        # Optional: set signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _signal_handler(signame):
            print(f"\n[{signame}] 종료 신호 수신 — 안전하게 종료합니다…")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler, sig.name)
            except NotImplementedError:
                # Windows 등에서 add_signal_handler 미지원 시 기본 handler 등록
                signal.signal(sig, lambda s, f: _signal_handler(getattr(s, 'name', str(s))))

        # Run bot and wait for stop_event
        bot_task = loop.create_task(bot.start(token))
        stopper = loop.create_task(stop_event.wait())
        done, pending = await asyncio.wait({bot_task, stopper}, return_when=asyncio.FIRST_COMPLETED)

        # If stop requested, close bot gracefully
        if stopper in done and not bot_task.done():
            await bot.close()
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass
    except KeyboardInterrupt:
        print("\n[CTRL+C] 종료 요청 — 안전하게 종료합니다…")
    except asyncio.CancelledError:
        print("작업이 취소되어 종료합니다…")
    finally:
        try:
            await bot.close()
        except Exception:
            pass

if __name__ == '__main__':
    asyncio.run(main())
