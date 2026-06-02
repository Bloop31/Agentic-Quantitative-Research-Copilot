import asyncio
import ssl
import certifi
from dotenv import load_dotenv
import os

load_dotenv()

async def ping():
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.getenv("MONGO_URI")

    print("Python SSL version :", ssl.OPENSSL_VERSION)
    print("Certifi path       :", certifi.where())
    print("URI loaded         :", "YES" if uri else "NO")
    print("Connecting...")

    client = AsyncIOMotorClient(
        uri,
        serverSelectionTimeoutMS=10000,
        tlsCAFile=certifi.where(),   # explicitly point to certifi certs
    )

    result = await client.admin.command("ping")
    print("✅ Atlas alive:", result)
    client.close()

asyncio.run(ping())