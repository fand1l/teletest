import asyncio
import logging
from src.config import settings
from src.collector.client import start_collector, client as telethon_client
from src.pipeline.orchestrator import pipeline_worker
from src.bot.main import start_bot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def main():
    logger.info("Starting Telegram Intelligence Aggregator...")
    
    # TODO: Initialize Database Setup (if needed)
    
    # Start the AI Pipeline Worker
    pipeline_task = asyncio.create_task(pipeline_worker())
    
    # Start the Aiogram Bot
    bot_task = asyncio.create_task(start_bot())
    
    # Start the Telethon Collector
    await start_collector()
    
    logger.info("Services initialized. Waiting for events...")
    
    try:
        # Keep the main loop running and wait for the Telethon client to disconnect
        await telethon_client.run_until_disconnected()
    finally:
        logger.info("Shutting down cleanly.")
        pipeline_task.cancel()
        bot_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down.")
